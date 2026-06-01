"""Torrent housekeeping manager.

Periodic, low-risk maintenance of the qBittorrent state. Two policies fire
on each tick:

1. **Resume stalled** — torrents in `pausedDL`, `stalledDL`, `queuedDL`, or
   `stoppedDL` get a resume kick. Idempotent; harmless if they were already
   resuming.

2. **Cleanup completed-and-imported** — torrents in `pausedUP`/`stoppedUP`
   that finished long enough ago AND whose files are already hardlinked into
   the media library are removed from qBittorrent (the data on disk stays —
   `delete_files=False`).

The hardlink check is the orphan-prevention safety net: if a file's link
count is 1, the *arr stack hasn't imported it yet (or import failed), so
we skip the deletion this cycle. The dead-media manager handles the case
where this persists across many cycles.

This module deliberately does **not** talk to Sonarr/Radarr. Coordination
with *arr (queue removal, blocklisting, search) belongs to dead_media.py.
This manager just keeps qBittorrent tidy.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from ..clients.qbittorrent import QBittorrentClient

logger = logging.getLogger("ott-hooks")

# qBittorrent states that should be kicked back to active downloading.
DEFAULT_RESUME_STATES = frozenset({"pausedDL", "stalledDL", "queuedDL", "stoppedDL"})

# qBittorrent states that mean "finished downloading, stopped seeding".
# These are the ones qBittorrent's GlobalMaxRatio guard transitions a
# completed torrent into.
DEFAULT_DELETE_STATES = frozenset({"pausedUP", "stoppedUP"})


@dataclass
class HousekeepingResult:
    """Summary of one housekeeping tick."""

    resumed: int = 0
    deleted: int = 0
    skipped_not_imported: int = 0  # files have nlink==1, *arr hasn't imported
    skipped_too_recent: int = 0  # completion_on too recent
    skipped_missing_files: int = 0  # file path doesn't exist on disk
    errors: int = 0
    # Hashes the orphan-risk path saw — useful for dead-media manager to pick up later.
    not_imported_hashes: list[str] = field(default_factory=list)


class TorrentHousekeepingManager:
    """Resume stalled + delete completed (only if imported via hardlink).

    Config:
        qbt: QBittorrentClient instance (already constructed).
        min_age_minutes: Minimum age since `completion_on` before a stopped/paused
            torrent is eligible for cleanup. Acts as a time-based fallback in
            addition to the hardlink check.
        resume_states: Override the default set of states that trigger resume.
        delete_states: Override the default set of states that trigger cleanup.
    """

    def __init__(
        self,
        qbt: QBittorrentClient,
        min_age_minutes: int = 10,
        resume_states: frozenset[str] = DEFAULT_RESUME_STATES,
        delete_states: frozenset[str] = DEFAULT_DELETE_STATES,
    ):
        self.qbt = qbt
        self.min_age_seconds = min_age_minutes * 60
        self.min_age_minutes = min_age_minutes
        self.resume_states = resume_states
        self.delete_states = delete_states

    # ---- entry point ----

    def run(self) -> HousekeepingResult:
        """Single housekeeping tick. Safe to call from a cron."""
        result = HousekeepingResult()
        torrents = self.qbt.list_torrents()
        if not torrents:
            logger.info("[housekeep] No torrents in qBittorrent")
            return result

        logger.info(f"[housekeep] Inspecting {len(torrents)} torrent(s)")
        self._resume_stalled(torrents, result)
        self._cleanup_completed(torrents, result)

        logger.info(
            f"[housekeep] Done: resumed={result.resumed} deleted={result.deleted} "
            f"skipped_not_imported={result.skipped_not_imported} "
            f"skipped_too_recent={result.skipped_too_recent} "
            f"errors={result.errors}"
        )
        return result

    # ---- policies ----

    def _resume_stalled(self, torrents: list[dict[str, Any]], result: HousekeepingResult) -> None:
        """Bulk-resume any torrent sitting in a stalled/paused download state."""
        hashes = [t["hash"] for t in torrents if t.get("state") in self.resume_states]
        if not hashes:
            return
        if self.qbt.resume(hashes):
            result.resumed = len(hashes)
            logger.info(f"[housekeep] Resumed {len(hashes)} torrent(s)")
        else:
            result.errors += 1
            logger.warning(f"[housekeep] Resume of {len(hashes)} torrent(s) failed")

    def _cleanup_completed(self, torrents: list[dict[str, Any]], result: HousekeepingResult) -> None:
        """Delete completed torrents whose files are confirmed imported (hardlinked)."""
        now = int(time.time())
        candidates = [t for t in torrents if t.get("state") in self.delete_states]
        if not candidates:
            return

        # Wait at least one resume cycle for newly-resumed torrents to settle
        # before sweeping for completed ones (matches the legacy scheduler's
        # 10s sleep). We don't sleep here; the cron interval already gives more
        # than enough headroom between ticks.

        ripe = []
        for t in candidates:
            completion_on = t.get("completion_on", 0)
            if completion_on <= 0:
                # Unknown completion time — treat as not yet ripe.
                result.skipped_too_recent += 1
                continue
            if (now - completion_on) < self.min_age_seconds:
                result.skipped_too_recent += 1
                continue
            ripe.append(t)

        if result.skipped_too_recent:
            logger.info(
                f"[housekeep] {result.skipped_too_recent} completed torrent(s) "
                f"skipped (age < {self.min_age_minutes}min)"
            )

        # Hardlink check per torrent — keep only those where every file is
        # already imported into the media library (nlink >= 2).
        to_delete: list[str] = []
        for t in ripe:
            status = self._import_status(t)
            if status == "imported":
                to_delete.append(t["hash"])
            elif status == "not_imported":
                result.skipped_not_imported += 1
                result.not_imported_hashes.append(t["hash"])
                logger.info(
                    f"[housekeep] Skipping {t['name'][:80]} "
                    f"— files exist but not hardlinked into library"
                )
            elif status == "missing":
                result.skipped_missing_files += 1
                logger.warning(
                    f"[housekeep] Skipping {t['name'][:80]} "
                    f"— files missing on disk (manual investigation needed)"
                )
            else:
                result.errors += 1

        if to_delete:
            if self.qbt.delete(to_delete, delete_files=False):
                result.deleted = len(to_delete)
                logger.info(f"[housekeep] Deleted {len(to_delete)} completed torrent(s) (data preserved)")
            else:
                result.errors += 1
                logger.warning(f"[housekeep] Bulk delete of {len(to_delete)} failed")

    # ---- helpers ----

    def _import_status(self, torrent: dict[str, Any]) -> str:
        """Decide whether the torrent's files have been imported by *arr.

        Returns:
            "imported"     — every file has nlink >= 2 (safe to delete)
            "not_imported" — at least one file has nlink == 1 (skip; *arr hasn't imported)
            "missing"      — at least one file's path doesn't exist on disk
            "error"        — exception while statting
        """
        save_path = torrent.get("save_path") or ""
        try:
            files = self.qbt.get_files(torrent["hash"])
        except Exception as e:
            logger.error(f"[housekeep] get_files failed for {torrent['name'][:60]}: {e}")
            return "error"

        if not files:
            # No files reported — nothing to verify, treat as imported so the
            # entry gets cleaned up (qBittorrent has nothing on disk to lose).
            return "imported"

        for f in files:
            rel = f.get("name") or ""
            full = os.path.join(save_path, rel)
            try:
                st = os.stat(full)
            except FileNotFoundError:
                return "missing"
            except OSError as e:
                logger.error(f"[housekeep] stat({full}) failed: {e}")
                return "error"
            if st.st_nlink < 2:
                return "not_imported"

        return "imported"
