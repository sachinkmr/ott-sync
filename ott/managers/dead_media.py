"""Dead-media manager — detect torrents needing human attention.

Phase B: detect, Telegram-notify with inline action buttons, persist state
in SQLite, and provide action handlers the callback router can call when
the user taps a button.

Three detectors fire per cron tick (all conditions AND-ed):

1. **stuck_metadata** — state ∈ {metaDL, forcedMetaDL}, num_seeds == 0,
   age (since added_on) ≥ `metadata_timeout_hours` (default 4h).
   Meaning: torrent never even fetched its .torrent metadata from peers.

2. **dead_download** — state == stalledDL, num_seeds == 0, progress < 1%,
   age ≥ 24h.
   Meaning: torrent got metadata but never started downloading.

3. **orphan_risk** — state ∈ {pausedUP, stoppedUP}, any file nlink == 1,
   completion_on age ≥ `import_fallback_hours` (default 2h).
   Meaning: torrent finished downloading hours ago but *arr never imported.
   This is the orphan-prevention escalation path — the housekeeping manager
   skips these (waiting for nlink to flip), this manager surfaces them
   once they've been stuck too long.

For each issue found we look up the corresponding Sonarr/Radarr queue entry
(by `downloadId` == torrent hash) to enrich with series_id / movie_id /
episode info, then group by (category, item_id) and send one Telegram alert
per series/movie listing the affected episodes.

In-memory dedup: don't re-notify the same (category, item_id) until
`dedup_ttl_hours` (default 24h) have passed. Phase B will persist this to
SQLite so dedup survives restarts.
"""

import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ..clients.qbittorrent import QBittorrentClient
from ..clients.arr_client import ArrClient
from ..clients.telegram import TelegramNotifier
from ..db.repositories.dead_media import DeadMediaRepository

logger = logging.getLogger("ott-hooks")

# Callback-data prefix for Telegram inline buttons.
# Format: "dead-media:<action>:<notification_id>"
CALLBACK_PREFIX = "dead-media:"

# Supported action verbs (also the values stored in DeadMediaNotificationModel.action_taken).
ACTION_SEARCH = "search"
ACTION_UNMONITOR_EPS = "unmonitor_eps"
ACTION_UNMONITOR_SERIES = "unmonitor_series"
ACTION_DROP = "drop"
ACTION_AUTO_UNMONITOR = "auto_unmonitor"  # fired by manager itself, not user
ACTION_AUTO_DROP = "auto_drop"            # fired by manager itself, not user

VALID_USER_ACTIONS = {ACTION_SEARCH, ACTION_UNMONITOR_EPS, ACTION_UNMONITOR_SERIES, ACTION_DROP}

# Button text labels. Kept short to fit nicely on Telegram phone screens.
_BUTTON_LABELS = {
    ACTION_SEARCH: "🔍 Search again",
    ACTION_UNMONITOR_EPS: "⏸ Unmonitor eps",
    ACTION_UNMONITOR_SERIES: "📺 Unmonitor series",
    ACTION_DROP: "❌ Drop",
}

# Human-friendly verdict text the callback handler edits into the message
# after an action is recorded — replaces the inline button row.
ACTION_VERDICTS = {
    ACTION_SEARCH: "🔍 Searched again — Sonarr/Radarr will retry",
    ACTION_UNMONITOR_EPS: "⏸ Episodes unmonitored — no more grabs",
    ACTION_UNMONITOR_SERIES: "📺 Series unmonitored — no more grabs",
    ACTION_DROP: "❌ Dropped — removed from qBittorrent, blocklisted",
    ACTION_AUTO_UNMONITOR: "🤖 Auto-unmonitored (blocklist threshold reached)",
    ACTION_AUTO_DROP: "🤖 Auto-dropped (release classified as malicious)",
}


# qBittorrent state classifications used by the detectors.
_METADATA_STATES = frozenset({"metaDL", "forcedMetaDL"})
_STALLED_STATES = frozenset({"stalledDL"})
_COMPLETED_STATES = frozenset({"pausedUP", "stoppedUP"})

# Detector names used as bucketing keys and in log lines.
DET_STUCK_METADATA = "stuck_metadata"
DET_DEAD_DOWNLOAD = "dead_download"
DET_ORPHAN_RISK = "orphan_risk"

# How long a dead_download must persist before alerting. Fixed (not config)
# because it's so far past normal that a knob isn't valuable yet — adjust
# here if real usage shows the threshold is wrong.
DEAD_DOWNLOAD_AGE_HOURS = 24

# Category → which *arr owns this torrent. Empty / unknown categories are
# logged but not associated with any *arr, so they show up in alerts as
# "unknown series" without queue enrichment.
_CATEGORY_TO_ARR = {
    "tv-sonarr": "sonarr",
    "radarr": "radarr",
}


# ----- Release classifier (Layer 1: heuristic-only) -----
# Severity tiers — ordered by user-action urgency.
SEVERITY_MALICIOUS = "malicious"        # bad ext / malware markers — auto-drop candidate
SEVERITY_WRONG_QUALITY = "wrong_quality"  # CAM/TS/TC — never want these
SEVERITY_SUSPICIOUS = "suspicious"      # 0 seeds + age, generic naming — probably dead
SEVERITY_NORMAL = "normal"              # no automated flags — human review

# Executable / script extensions — these are NEVER legitimate video, full
# stop. A torrent file ending here is malware-shaped and gets the strongest
# verdict (auto-droppable when the opt-in flag is on).
_EXECUTABLE_EXTENSIONS = (
    ".scr", ".exe", ".msi", ".bat", ".com", ".lnk",
)

# Archive extensions — Sonarr cannot ingest these directly even when the
# contents *are* legitimate episodes (old fansub batches occasionally wrap
# a season in a single .zip/.rar/.7z). So we flag them as suspicious +
# recommend "search for another release" rather than auto-dropping. If the
# user actually wants those episodes, they extract manually and Sonarr can
# pick up the resulting .mkv via DownloadedEpisodesScan.
_ARCHIVE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".cab", ".iso", ".tar", ".gz",
)

# Cinema-rip source markers — word-boundary regex so we don't false-match
# e.g. "Camera" or "Tslate" inside titles. Case-insensitive.
_CAM_SOURCE_RE = re.compile(
    r"\b(?:CAM|HDCAM|TS|HDTS|TELESYNC|TELECINE|TC|HDTC)\b",
    re.IGNORECASE,
)

# Suspicious-bundle filename tokens (when the torrent name itself contains
# these, it's almost always a malware archive in TV-show clothing).
_BUNDLE_MARKERS = ("setup.exe", "installer", "password.txt", "readme.url")


@dataclass(frozen=True)
class Recommendation:
    """Heuristic-derived advice for a dead-media issue."""
    severity: str          # one of SEVERITY_*
    action: str            # one of ACTION_* (or "none")
    reason: str            # short human-readable explanation


def classify_release(issue: "TorrentIssue") -> Recommendation:
    """Classify a TorrentIssue using deterministic heuristics only.

    Rule priority (first match wins):
      1. Executable extension          → malicious / drop      (.exe, .scr, etc.)
      2. Bundled-malware filename hint → malicious / drop      (setup.exe, password.txt)
      3. Archive extension             → suspicious / search   (.zip, .rar, .iso — Sonarr can't ingest)
      4. Cinema-rip source marker      → wrong_quality / drop  (CAM, TS, HDTC, ...)
      5. 0 seeds + age > 4h            → suspicious / search
      6. (Fallback)                    → normal / none

    Returns a Recommendation. Callers decide whether to act, surface in
    Telegram, or both. Only the *malicious* tier is ever auto-acted; the
    rest always surface to the user.
    """
    name = (issue.name or "").strip()
    name_lower = name.lower()

    # 1. Executable / script extension — the strongest negative signal.
    # These are never legitimate video, and the file is potentially actively
    # harmful if Plex/Kodi/the OS opens it. Auto-droppable.
    for ext in _EXECUTABLE_EXTENSIONS:
        if name_lower.endswith(ext):
            return Recommendation(
                severity=SEVERITY_MALICIOUS,
                action=ACTION_DROP,
                reason=f"Extension {ext} is an executable file, not a video/media file.",
            )

    # 2. Bundled-malware token inside the name (rare but unambiguous).
    for marker in _BUNDLE_MARKERS:
        if marker in name_lower:
            return Recommendation(
                severity=SEVERITY_MALICIOUS,
                action=ACTION_DROP,
                reason=f"Name contains malware-bundle marker '{marker}'.",
            )

    # 3. Archive extension. The archive *might* legitimately contain episodes
    # (old fansub batch packages do this), but Sonarr can't extract them — so
    # functionally this release is unusable as-is. Recommend search-again
    # rather than drop, and never auto-act; the user might want to extract
    # manually.
    for ext in _ARCHIVE_EXTENSIONS:
        if name_lower.endswith(ext):
            return Recommendation(
                severity=SEVERITY_SUSPICIOUS,
                action=ACTION_SEARCH,
                reason=(
                    f"Archive format {ext} — Sonarr cannot import directly. "
                    "Extract manually or search for a non-archive release."
                ),
            )

    # 4. Cinema-rip source markers — bad quality, never wanted.
    m = _CAM_SOURCE_RE.search(name)
    if m:
        return Recommendation(
            severity=SEVERITY_WRONG_QUALITY,
            action=ACTION_DROP,
            reason=f"Cinema source marker '{m.group(0)}' — poor video quality, avoid.",
        )

    # 5. 0 seeds + meaningful age — abandoned release, search for a different
    # one. Threshold excludes fresh grabs that haven't seeded yet.
    if issue.num_seeds == 0 and issue.age_hours > 4:
        return Recommendation(
            severity=SEVERITY_SUSPICIOUS,
            action=ACTION_SEARCH,
            reason=f"0 seeders after {issue.age_hours:.1f}h — abandoned release.",
        )

    # 6. Nothing flagged — human review.
    return Recommendation(
        severity=SEVERITY_NORMAL,
        action="none",
        reason="No automated flags — manual review",
    )


# Pretty severity labels (emoji + word) for the Telegram message.
_SEVERITY_LABELS = {
    SEVERITY_MALICIOUS: "🚨 Malicious",
    SEVERITY_WRONG_QUALITY: "⚠️ Wrong quality",
    SEVERITY_SUSPICIOUS: "❓ Suspicious",
    SEVERITY_NORMAL: "ℹ️ Normal",
}


@dataclass
class TorrentIssue:
    """One torrent that tripped a detector.

    Enriched fields (series_id, movie_id, episodes) populated by queue
    lookup; absent for torrents not currently in any *arr queue.
    """

    hash: str
    name: str
    category: str  # qBittorrent category — drives *arr routing
    state: str
    detector: str
    age_hours: float
    num_seeds: int

    # Enrichment from *arr queue lookup
    arr: Optional[str] = None  # "sonarr" | "radarr" | None (unknown)
    series_id: Optional[int] = None
    series_title: Optional[str] = None
    movie_id: Optional[int] = None
    movie_title: Optional[str] = None
    # Episode descriptions like "S01E03" for grouping/display.
    episode_labels: list[str] = field(default_factory=list)
    # Episode titles fetched from *arr (parallel to episode_labels).
    # Empty when enrichment skipped them. Shown in the rich message format.
    episode_titles: list[str] = field(default_factory=list)
    # Sonarr episode IDs (parallel to episode_labels) — used by the
    # Unmonitor-eps action to PUT monitored=false. Empty for radarr or
    # when queue enrichment didn't find a match.
    episode_ids: list[int] = field(default_factory=list)
    # Rich-format fields populated by the *arr enrichment step. Used by
    # _format_message and the send_photo path. All optional; the message
    # gracefully falls back when missing.
    year: Optional[int] = None
    rating_value: Optional[float] = None  # 0-10 scale (TVDB/TMDB style)
    rating_votes: Optional[int] = None
    genres: list[str] = field(default_factory=list)
    status: Optional[str] = None  # 'continuing' | 'ended' | 'released' | etc.
    poster_url: Optional[str] = None  # remote URL, ready for Telegram sendPhoto
    overview: Optional[str] = None  # plot summary
    # Blocklist count for the affected episodes (Sonarr only) — used by the
    # auto-unmonitor threshold logic. Lazily populated only when the
    # threshold is configured non-zero.
    max_blocklist_count: int = 0


class DeadMediaManager:
    """Detect-and-notify manager for torrents needing human attention.

    Owns no destructive operations — phase A is read-only against qBittorrent
    and the *arr APIs. Removal / unmonitor / re-search happen via Telegram
    callback buttons in phase B.
    """

    def __init__(
        self,
        qbt: QBittorrentClient,
        sonarr_client: Optional[ArrClient],
        radarr_client: Optional[ArrClient],
        telegram: TelegramNotifier,
        chat_id: str,
        thread_id: int = 0,
        metadata_timeout_hours: int = 4,
        import_fallback_hours: int = 2,
        dedup_ttl_hours: int = 24,
        auto_unmonitor_after_blocklists: int = 0,
        auto_drop_malicious: bool = False,
    ):
        self.qbt = qbt
        self.sonarr = sonarr_client
        self.radarr = radarr_client
        self.telegram = telegram
        self.chat_id = chat_id
        # 0 = main chat (no forum topic); positive int routes into a topic.
        self.thread_id = thread_id
        self.metadata_timeout_hours = metadata_timeout_hours
        self.import_fallback_hours = import_fallback_hours
        self.dedup_ttl_seconds = dedup_ttl_hours * 3600
        # 0 = never auto-unmonitor (humans always decide via buttons).
        # N>0 = after Sonarr has blocklisted N+ different releases for an
        # episode, fire ACTION_AUTO_UNMONITOR on it without sending a button
        # prompt — useful for known-dead series that keep generating grabs.
        self.auto_unmonitor_after_blocklists = auto_unmonitor_after_blocklists
        # False = surface the recommendation in Telegram only, never act
        # without the user. True = automatically drop releases the classifier
        # flags as SEVERITY_MALICIOUS (bad extension, malware bundle markers).
        # Other severities (wrong_quality, suspicious) always defer to the user
        # regardless of this flag — auto-action only fires on the unambiguous
        # malicious cases.
        self.auto_drop_malicious = auto_drop_malicious

    # ---- entry point ----

    def run(self) -> dict[str, int]:
        """One cron tick. Returns counts dict for logging.

        Pulls full qbt inventory, runs detectors, enriches via *arr queues,
        applies auto-actions (if configured), then groups and notifies on
        whatever remains.
        """
        torrents = self.qbt.list_torrents()
        if not torrents:
            return {"detected": 0, "notified": 0, "deduped": 0, "auto_actioned": 0}

        # Run all detectors; collect at most one issue per torrent (first
        # detector to fire wins — they don't overlap in practice anyway).
        issues: list[TorrentIssue] = []
        for t in torrents:
            issue = self._detect_one(t)
            if issue:
                issues.append(issue)

        logger.info(f"[dead-media] Scanned {len(torrents)} torrent(s), found {len(issues)} issue(s)")

        if not issues:
            return {"detected": 0, "notified": 0, "deduped": 0, "auto_actioned": 0}

        # Enrich with *arr queue info so we can group meaningfully.
        self._enrich_with_arr(issues)

        # Apply auto-actions (e.g. auto-unmonitor after N blocklists) before
        # notification. Any issue handled by auto-action is removed from the
        # list so we don't double-notify.
        remaining, auto_actioned = self._apply_auto_actions(issues)

        # Group + notify on the rest.
        notified, deduped = self._notify_grouped(remaining)
        return {
            "detected": len(issues),
            "notified": notified,
            "deduped": deduped,
            "auto_actioned": auto_actioned,
        }

    # ---- detectors ----

    def _detect_one(self, t: dict[str, Any]) -> Optional[TorrentIssue]:
        """Run all detectors against a single torrent; return first hit."""
        now = int(time.time())
        state = t.get("state", "")
        num_seeds = int(t.get("num_seeds", 0) or 0)

        # Detector 1: stuck metadata
        if state in _METADATA_STATES and num_seeds == 0:
            added_on = int(t.get("added_on", 0) or 0)
            age_h = (now - added_on) / 3600 if added_on > 0 else 0
            if age_h >= self.metadata_timeout_hours:
                return self._make_issue(t, DET_STUCK_METADATA, age_h, num_seeds)

        # Detector 2: dead download (has metadata but no peers, no progress)
        if state in _STALLED_STATES and num_seeds == 0:
            added_on = int(t.get("added_on", 0) or 0)
            age_h = (now - added_on) / 3600 if added_on > 0 else 0
            progress = float(t.get("progress", 0) or 0)
            if age_h >= DEAD_DOWNLOAD_AGE_HOURS and progress < 0.01:
                return self._make_issue(t, DET_DEAD_DOWNLOAD, age_h, num_seeds)

        # Detector 3: orphan risk (completed-but-not-imported)
        if state in _COMPLETED_STATES:
            completion_on = int(t.get("completion_on", 0) or 0)
            if completion_on > 0:
                age_h = (now - completion_on) / 3600
                if age_h >= self.import_fallback_hours and self._has_unimported_files(t):
                    return self._make_issue(t, DET_ORPHAN_RISK, age_h, num_seeds)

        return None

    def _has_unimported_files(self, t: dict[str, Any]) -> bool:
        """True if any file in the torrent has link count == 1 on disk.

        Same check as torrent_housekeeping uses — keeps the two managers in
        agreement about what "imported" means. Missing files are treated as
        "not imported" so they surface in the alert (they need attention too).
        """
        save_path = t.get("save_path") or ""
        try:
            files = self.qbt.get_files(t["hash"])
        except Exception as e:
            logger.error(f"[dead-media] get_files({t['name'][:60]}) failed: {e}")
            return False
        if not files:
            return False
        for f in files:
            full = os.path.join(save_path, f.get("name") or "")
            try:
                if os.stat(full).st_nlink < 2:
                    return True
            except FileNotFoundError:
                return True  # treat missing as "needs attention"
            except OSError:
                # Permission / stale fs error — be conservative, alert.
                return True
        return False

    def _make_issue(
        self, t: dict[str, Any], detector: str, age_hours: float, num_seeds: int,
    ) -> TorrentIssue:
        return TorrentIssue(
            hash=t["hash"],
            name=t.get("name") or t["hash"],
            category=t.get("category") or "",
            state=t.get("state") or "",
            detector=detector,
            age_hours=age_hours,
            num_seeds=num_seeds,
        )

    # ---- *arr enrichment ----

    def _enrich_with_arr(self, issues: list[TorrentIssue]) -> None:
        """Look up each issue's torrent hash in the appropriate *arr queue.

        Mutates issues in place. If the hash isn't found, leaves the *arr
        fields blank — the issue still gets reported (under an "unknown
        series" bucket).
        """
        sonarr_hashes = {i.hash.lower() for i in issues
                          if _CATEGORY_TO_ARR.get(i.category) == "sonarr"}
        radarr_hashes = {i.hash.lower() for i in issues
                          if _CATEGORY_TO_ARR.get(i.category) == "radarr"}

        sonarr_queue = self._fetch_arr_queue(self.sonarr) if sonarr_hashes else {}
        radarr_queue = self._fetch_arr_queue(self.radarr) if radarr_hashes else {}

        for issue in issues:
            arr_kind = _CATEGORY_TO_ARR.get(issue.category)
            if arr_kind == "sonarr":
                issue.arr = "sonarr"
                self._enrich_sonarr(issue, sonarr_queue)
            elif arr_kind == "radarr":
                issue.arr = "radarr"
                self._enrich_radarr(issue, radarr_queue)
            # else: unknown — leave arr/series_id/movie_id as None.

    def _fetch_arr_queue(self, client: Optional[ArrClient]) -> dict[str, list[dict]]:
        """Fetch and index *arr queue entries by lowercased downloadId.

        Returns {hash_lower: [queue_record, ...]} — list because a single
        torrent can correspond to multiple episodes (season pack).
        """
        if client is None:
            return {}
        try:
            # includeUnknownSeriesItems lets us see entries even when the
            # series was deleted between grab and queue scan.
            res = client.get(
                "queue",
                params={
                    "pageSize": 1000,
                    "includeUnknownSeriesItems": "true",
                    "includeUnknownMovieItems": "true",
                },
            )
            if not res:
                return {}
            data = res.json()
            records = data.get("records", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.error(f"[dead-media] *arr queue fetch failed: {e}")
            return {}

        indexed: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            did = (r.get("downloadId") or "").lower()
            if did:
                indexed[did].append(r)
        return indexed

    def _enrich_sonarr(self, issue: TorrentIssue, queue: dict[str, list[dict]]) -> None:
        rows = queue.get(issue.hash.lower(), [])
        if not rows:
            return
        # All rows for the same downloadId map to the same series (multi-
        # episode packs explode in queue). Pull series + episode labels.
        first = rows[0]
        issue.series_id = first.get("seriesId")
        # The queue row's top-level `title` is the *release* title (e.g.
        # "Show.S01E03.1080p.WEB.x264-GROUP") — NOT the series name. Get
        # the series name from the nested `series` object when present.
        series_obj = first.get("series") or {}
        if isinstance(series_obj, dict) and series_obj.get("title"):
            issue.series_title = series_obj["title"]
        for r in rows:
            ep = r.get("episode") or {}
            season = ep.get("seasonNumber")
            epnum = ep.get("episodeNumber")
            ep_id = ep.get("id") or r.get("episodeId")
            ep_title = ep.get("title")
            if season is not None and epnum is not None:
                issue.episode_labels.append(f"S{int(season):02d}E{int(epnum):02d}")
            if ep_id is not None:
                issue.episode_ids.append(int(ep_id))
            issue.episode_titles.append(ep_title or "")

        # Fetch the full series object for poster/ratings/genres. Best-effort;
        # missing fields are fine — _format_message handles None gracefully.
        if issue.series_id and self.sonarr:
            self._populate_arr_details(issue, "series", issue.series_id, self.sonarr)

    def _enrich_radarr(self, issue: TorrentIssue, queue: dict[str, list[dict]]) -> None:
        rows = queue.get(issue.hash.lower(), [])
        if not rows:
            return
        first = rows[0]
        issue.movie_id = first.get("movieId")
        # As with Sonarr, queue row's `title` is the release title — get the
        # actual movie name from the nested `movie` object.
        movie_obj = first.get("movie") or {}
        if isinstance(movie_obj, dict) and movie_obj.get("title"):
            issue.movie_title = movie_obj["title"]
        if issue.movie_id and self.radarr:
            self._populate_arr_details(issue, "movie", issue.movie_id, self.radarr)

    def _populate_arr_details(
        self, issue: TorrentIssue, kind: str, item_id: int, client: ArrClient,
    ) -> None:
        """Fetch the full series/movie object and populate rich-display fields.

        kind: 'series' (Sonarr) | 'movie' (Radarr) — only affects the endpoint.
        Both *arr APIs expose the same field shapes (year, ratings, genres,
        status, images, overview) so we share extraction code.

        Failures here are silent — the message renderer treats every rich
        field as optional and falls back to a plain text format.
        """
        try:
            res = client.get(f"{kind}/{item_id}")
            if not res:
                return
            obj = res.json()
        except Exception as e:
            logger.debug(f"[dead-media] _populate_arr_details({kind}/{item_id}) failed: {e}")
            return

        if not isinstance(obj, dict):
            return

        # Title fallback if queue enrichment didn't have it.
        if kind == "series" and not issue.series_title:
            issue.series_title = obj.get("title")
        elif kind == "movie" and not issue.movie_title:
            issue.movie_title = obj.get("title")

        # Year — both shapes have it as a top-level int.
        year = obj.get("year")
        if isinstance(year, int) and year > 0:
            issue.year = year

        # Ratings — Sonarr/Radarr keep TVDB/TMDB-style {value, votes} dict.
        ratings = obj.get("ratings") or {}
        if isinstance(ratings, dict):
            val = ratings.get("value")
            votes = ratings.get("votes")
            if isinstance(val, (int, float)) and val > 0:
                issue.rating_value = float(val)
            if isinstance(votes, int) and votes > 0:
                issue.rating_votes = votes

        # Genres + status — used as compact metadata pill in the message.
        genres = obj.get("genres") or []
        if isinstance(genres, list):
            issue.genres = [g for g in genres if isinstance(g, str)][:3]
        status = obj.get("status")
        if isinstance(status, str) and status:
            issue.status = status

        # Poster image. Sonarr/Radarr return an `images` array with several
        # cover types; the "poster" type is what fits the Telegram sendPhoto
        # surface best. Prefer the `remoteUrl` (CDN) over the local `/MediaCover/`
        # path because Telegram needs a publicly-reachable URL.
        for img in obj.get("images") or []:
            if not isinstance(img, dict):
                continue
            if img.get("coverType") == "poster":
                remote = img.get("remoteUrl")
                if isinstance(remote, str) and remote.startswith(("http://", "https://")):
                    issue.poster_url = remote
                    break

        # Plot summary — short version for context. Trim to keep Telegram
        # captions under the 1024-char limit when combined with episode list.
        overview = obj.get("overview")
        if isinstance(overview, str) and overview:
            issue.overview = overview[:300].rstrip()
            if len(overview) > 300:
                issue.overview += "…"

    # ---- grouping + notification ----

    def _notify_grouped(self, issues: list[TorrentIssue]) -> tuple[int, int]:
        """Group issues by (arr, item_id, detector), dedup via DB, send with
        inline action buttons.

        Returns (notified_count, deduped_count).
        """
        groups: dict[tuple, list[TorrentIssue]] = defaultdict(list)
        for issue in issues:
            key = self._group_key(issue)
            groups[key].append(issue)

        notified = 0
        deduped = 0

        for key, grp in groups.items():
            group_key_str = self._group_key_str(key)

            # DB-backed dedup: don't re-notify if a recent (unactioned-or-
            # actioned) row already exists for this group within the TTL.
            existing = DeadMediaRepository.get_recent_by_group_key(
                group_key_str, self.dedup_ttl_seconds
            )
            if existing:
                deduped += len(grp)
                age_h = (time.time() - existing["sent_at"].timestamp()) / 3600
                logger.debug(
                    f"[dead-media] Dedup skip: {group_key_str} "
                    f"(notified {age_h:.1f}h ago, action={existing.get('action_taken')})"
                )
                continue

            # Persist BEFORE sending so the row's id is available for the
            # button callback_data. If the send fails we'd have an orphaned
            # row, but that's fine — cleanup_old will sweep it eventually
            # and dedup logic only cares about recent rows.
            arr, item_id, _detector = key
            title = self._best_title(grp)
            hashes = [i.hash for i in grp]
            ep_labels = list({e for i in grp for e in i.episode_labels})
            ep_ids = list({eid for i in grp for eid in i.episode_ids})
            notif_id = DeadMediaRepository.create_notification(
                group_key=group_key_str,
                arr=arr,
                item_id=item_id,
                detector=grp[0].detector,
                title=title,
                affected_hashes=hashes,
                affected_episodes=ep_labels,
                affected_episode_ids=ep_ids,
                telegram_chat_id=str(self.chat_id) if self.chat_id else None,
            )
            if not notif_id:
                logger.error(f"[dead-media] Failed to persist notification for {group_key_str}")
                continue

            message = self._format_message(grp)
            buttons = self._build_buttons(notif_id, grp)
            ok = self._send_alert(grp[0].poster_url, message, buttons)
            if ok:
                notified += 1
                logger.info(
                    f"[dead-media] Notified: {self._describe_group(grp)} "
                    f"(notif_id={notif_id})"
                )
            else:
                logger.warning(f"[dead-media] Telegram send failed for {group_key_str}")
                # Leave the DB row in place — cleanup_old will reap it.

        return notified, deduped

    def _send_alert(
        self, poster_url: Optional[str], caption: str, buttons: Optional[list[list[dict]]],
    ) -> bool:
        """Send via sendPhoto when we have a poster URL, fall back to text.

        Telegram has a 1024-char limit on photo captions; the formatter
        already keeps the message inside it. Plain-text messages have a
        4096-char limit so the fallback is generous.
        """
        if poster_url:
            ok = self.telegram.send_photo(
                photo_url=poster_url,
                caption=caption,
                buttons=buttons,
                chat_id=self.chat_id,
                thread_id=self.thread_id,
            )
            if ok:
                return True
            # Photo URL might be unreachable from Telegram's side (rate
            # limit, CDN hiccup, etc.). Don't lose the alert — retry as
            # plain text.
            logger.info("[dead-media] send_photo failed; retrying as plain text")
        return self.telegram.send(
            message=caption,
            chat_id=self.chat_id,
            thread_id=self.thread_id,
            buttons=buttons,
        )

    def _group_key_str(self, key: tuple) -> str:
        """String form of (arr, item_id, detector) for DB storage and
        comparison. Stable across processes."""
        arr, item_id, detector = key
        return f"{arr}:{item_id}:{detector}"

    def _best_title(self, grp: list[TorrentIssue]) -> Optional[str]:
        """Pick a human title from the group, falling back through enriched
        fields. Used for the message header and DB cache."""
        for issue in grp:
            if issue.series_title:
                return issue.series_title
            if issue.movie_title:
                return issue.movie_title
        # Fall back to the torrent name itself (better than "Unknown").
        return grp[0].name if grp else None

    def _build_buttons(
        self,
        notification_id: int,
        grp: list[TorrentIssue],
    ) -> list[list[dict]]:
        """Build the inline-keyboard layout for an alert.

        Layout (2 rows x 2 buttons):
            [🔍 Search again]   [⏸ Unmonitor eps]
            [📺 Unmonitor series] [❌ Drop]

        Buttons that don't make sense for the group are omitted:
        - Unmonitor eps: needs at least one known episode_id
        - Unmonitor series: needs a series_id (sonarr only; movies skip)
        """
        first = grp[0]
        has_episodes = any(i.episode_ids for i in grp)
        has_series = first.arr == "sonarr" and first.series_id is not None
        # All actions are keyed by notification_id; the handler reads the
        # affected_* arrays back out of the DB row.
        def cb(action: str) -> dict:
            return {
                "text": _BUTTON_LABELS[action],
                "callback_data": f"{CALLBACK_PREFIX}{action}:{notification_id}",
            }

        row1 = [cb(ACTION_SEARCH)]
        if has_episodes:
            row1.append(cb(ACTION_UNMONITOR_EPS))

        row2 = []
        if has_series:
            row2.append(cb(ACTION_UNMONITOR_SERIES))
        row2.append(cb(ACTION_DROP))

        # Telegram inline_keyboard is list of rows; empty rows are invalid.
        rows = [r for r in (row1, row2) if r]
        return rows

    def _group_key(self, issue: TorrentIssue) -> tuple:
        """Group by (arr, item_id, detector). Unknown items group by detector
        only so all 'unknown series' alerts for a given detector batch
        together rather than spamming one per torrent."""
        if issue.series_id:
            return ("sonarr", issue.series_id, issue.detector)
        if issue.movie_id:
            return ("radarr", issue.movie_id, issue.detector)
        return (issue.arr or "unknown", 0, issue.detector)

    def _describe_group(self, grp: list[TorrentIssue]) -> str:
        """Short string for logs."""
        first = grp[0]
        title = first.series_title or first.movie_title or "unknown"
        return f"{first.detector} | {title} | {len(grp)} item(s)"

    def _format_message(self, grp: list[TorrentIssue]) -> str:
        """Markdown message body for one Telegram alert.

        Layout intentionally mirrors the manual-mode OTT approval message in
        `ott.models.build_caption` — one fact per line with an emoji label,
        bold field names, bottom-aligned "Choose an action:" prompt.

        Captions for `send_photo` have a 1024-char limit, so we keep section
        sizes bounded. When no poster is available the same string is sent
        as a plain text message (4096-char limit).
        """
        first = grp[0]
        raw_title = first.series_title or first.movie_title or "Unknown title"
        year_suffix = f" ({first.year})" if first.year else ""
        detector_label = {
            DET_STUCK_METADATA: "🪤 Stuck metadata — no peers found",
            DET_DEAD_DOWNLOAD: "💀 Dead download — no progress, no seeds",
            DET_ORPHAN_RISK: "🩹 Orphan risk — completed, Sonarr/Radarr didn't import",
        }.get(first.detector, first.detector)
        item_type = "Movie" if first.arr == "radarr" else "Series"
        item_id = first.movie_id if first.arr == "radarr" else first.series_id
        ts = datetime.now().strftime("%d %b %Y, %H:%M")

        lines = [
            f"🎬 *{raw_title}*{year_suffix}",
            "",
            f"⚠️ *Issue:* {detector_label}",
        ]

        if first.rating_value:
            r = f"⭐ *Rating:* {first.rating_value:.1f}/10"
            if first.rating_votes:
                r += f" ({first.rating_votes:,} votes)"
            lines.append(r)
        lines.append(f"📂 *Type:* {item_type}")
        if item_id:
            lines.append(f"🆔 *ID:* {item_id}")
        if first.genres:
            lines.append("🎭 *Genre:* " + ", ".join(first.genres))
        if first.status:
            lines.append(f"🟢 *Status:* {first.status.title()}")
        lines.append(f"⏱ *Detected at:* {ts}")

        # Plot summary, if we got one.
        if first.overview:
            lines.append("")
            lines.append(f"📝 _{first.overview}_")

        # Episode list — prefer labels + titles when we have them, fall back
        # to just labels. Cap at 8 to leave room under the caption limit.
        ep_pairs: list[tuple[str, str]] = []
        seen_labels = set()
        for issue in grp:
            for label, ep_title in zip(
                issue.episode_labels,
                issue.episode_titles or [""] * len(issue.episode_labels),
            ):
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                ep_pairs.append((label, ep_title or ""))

        if ep_pairs:
            lines.append("")
            lines.append(f"📼 *{len(ep_pairs)} episode(s) affected:*")
            for label, ep_title in ep_pairs[:8]:
                if ep_title:
                    lines.append(f"  • {label} — _{ep_title[:50]}_")
                else:
                    lines.append(f"  • {label}")
            if len(ep_pairs) > 8:
                lines.append(f"  • _…and {len(ep_pairs) - 8} more_")

        # Torrent-level context. Each torrent on one line: name -> age + seeds.
        # No monospace — Telegram's mobile monospace font overflows on long
        # release names.
        lines.append("")
        lines.append(f"🧲 *{len(grp)} torrent(s) affected:*")
        for issue in grp[:3]:
            age = f"{issue.age_hours:.1f}h" if issue.age_hours > 0 else "?h"
            short_name = issue.name if len(issue.name) <= 60 else issue.name[:57] + "…"
            lines.append(f"- {short_name} -> _age {age} · seeds {issue.num_seeds}_")
        if len(grp) > 3:
            lines.append(f"- _…and {len(grp) - 3} more_")

        # Blocklist hint when auto-unmonitor is configured.
        if first.max_blocklist_count > 0:
            lines.append("")
            lines.append(
                f"_Sonarr has blocklisted {first.max_blocklist_count} "
                f"release(s) for the affected episode(s)._"
            )

        # Heuristic recommendation — surfaces obvious cases (bad extension,
        # CAM rip, 0-seed dead torrents) so the user can act with one tap
        # instead of investigating. Only shows when the classifier has
        # something to say (severity != normal).
        rec = classify_release(first)
        if rec.severity != SEVERITY_NORMAL:
            sev_label = _SEVERITY_LABELS.get(rec.severity, rec.severity)
            action_label = _BUTTON_LABELS.get(rec.action, rec.action)
            lines.append("")
            lines.append(f"🤖 *Verdict:* {sev_label}")
            lines.append(f"📌 *Recommended Action:* {action_label}")
            lines.append(f"⚠️ _{rec.reason}_")

        lines.append("")
        lines.append("👇 Choose an action:")

        return "\n".join(lines)

    # ---- auto-actions (run between detect+enrich and notify) ----

    def _apply_auto_actions(
        self, issues: list[TorrentIssue],
    ) -> tuple[list[TorrentIssue], int]:
        """Apply automated actions before notifying. Returns (remaining, count).

        Two auto-action paths, each gated by its own opt-in config flag:
          A) auto_drop_malicious — drop releases the heuristic classifier
             flags as SEVERITY_MALICIOUS (bad extension, malware markers).
             Runs first because malicious-by-name is unambiguous and never
             needs the user.
          B) auto_unmonitor_after_blocklists — for episodes Sonarr has
             blocklisted N+ times, unmonitor without prompting. Sonarr-only.

        Auto-actioned issues are removed from the returned list so the
        normal notification path doesn't fire on them.
        """
        auto_actioned = 0
        after_drop: list[TorrentIssue] = []

        # Pass A: auto-drop malicious. Cheap (no API call to enumerate, just
        # the per-issue classifier) so we do it inline.
        if self.auto_drop_malicious:
            for issue in issues:
                rec = classify_release(issue)
                if rec.severity != SEVERITY_MALICIOUS:
                    after_drop.append(issue)
                    continue
                try:
                    self._fire_auto_drop(issue, rec)
                    auto_actioned += 1
                except Exception as e:
                    logger.error(
                        f"[dead-media] auto_drop failed for "
                        f"{issue.series_title or issue.hash[:10]}: {e}",
                        exc_info=True,
                    )
                    after_drop.append(issue)
        else:
            after_drop = list(issues)

        # Pass B: auto-unmonitor on blocklist threshold (Sonarr-only).
        if self.auto_unmonitor_after_blocklists <= 0 or not self.sonarr:
            return after_drop, auto_actioned

        blocklist_counts = self._sonarr_blocklist_counts_by_episode()
        if not blocklist_counts:
            return after_drop, auto_actioned

        remaining: list[TorrentIssue] = []
        for issue in after_drop:
            if issue.arr != "sonarr" or not issue.episode_ids:
                remaining.append(issue)
                continue
            max_count = max(
                (blocklist_counts.get(eid, 0) for eid in issue.episode_ids),
                default=0,
            )
            issue.max_blocklist_count = max_count
            if max_count < self.auto_unmonitor_after_blocklists:
                remaining.append(issue)
                continue
            try:
                self._fire_auto_unmonitor(issue, max_count)
                auto_actioned += 1
            except Exception as e:
                logger.error(
                    f"[dead-media] auto_unmonitor failed for "
                    f"{issue.series_title or issue.hash[:10]}: {e}",
                    exc_info=True,
                )
                remaining.append(issue)
        return remaining, auto_actioned

    def _sonarr_blocklist_counts_by_episode(self) -> dict[int, int]:
        """Fetch Sonarr's blocklist and return {episode_id: count}.

        Sonarr's blocklist API paginates. We pull a reasonably large page
        ordered by most recent — for auto-unmonitor we mostly care about
        recent activity (a series with 10+ blocklisted releases recently
        is the canonical dead-media case). Older entries are noise.

        Returns empty dict on any failure so callers degrade gracefully
        rather than crashing the cron tick.
        """
        if not self.sonarr:
            return {}
        try:
            res = self.sonarr.get(
                "blocklist",
                params={"page": 1, "pageSize": 1000, "sortKey": "date", "sortDirection": "descending"},
            )
            if not res:
                return {}
            data = res.json()
            records = data.get("records", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.error(f"[dead-media] blocklist fetch failed: {e}")
            return {}

        counts: dict[int, int] = defaultdict(int)
        for r in records:
            # A blocklist row can list multiple episodeIds for a season pack.
            # Each id contributes once to its own count.
            for eid in r.get("episodeIds", []) or []:
                if isinstance(eid, int):
                    counts[eid] += 1
        return dict(counts)

    def _fire_auto_drop(self, issue: TorrentIssue, rec: Recommendation) -> None:
        """Execute auto-drop + record + notify-only message.

        Same shape as _fire_auto_unmonitor but for malicious releases: persist
        notification row, claim ACTION_AUTO_DROP, call the *arr queue DELETE
        with blocklist=true & skipRedownload=true, send a no-buttons message
        explaining the verdict.
        """
        arr = issue.arr or "unknown"
        item_id = issue.series_id if arr == "sonarr" else issue.movie_id
        group_key_str = f"{arr}:{item_id or 0}:{issue.detector}"
        title = issue.series_title or issue.movie_title

        notif_id = DeadMediaRepository.create_notification(
            group_key=group_key_str,
            arr=arr,
            item_id=item_id or 0,
            detector=issue.detector,
            title=title,
            affected_hashes=[issue.hash],
            affected_episodes=issue.episode_labels,
            affected_episode_ids=issue.episode_ids,
            telegram_chat_id=str(self.chat_id) if self.chat_id else None,
        )
        if notif_id is None:
            logger.error(
                f"[dead-media] auto-drop: failed to persist notification for {group_key_str}"
            )
            return

        claimed = DeadMediaRepository.claim_action(notif_id, ACTION_AUTO_DROP)
        if not claimed:
            logger.warning(f"[dead-media] auto-drop: race on claim_action for notif {notif_id}")

        # Side effect: drop from the *arr queue with blocklist. This is the
        # same path the user "Drop" button takes — we just call it directly.
        arr_client = self._arr_for(arr)
        if arr_client:
            queue_id = self._find_queue_id_by_hash(arr_client, issue.hash)
            if queue_id:
                arr_client.delete(
                    f"queue/{queue_id}",
                    params={
                        "removeFromClient": "true",
                        "blocklist": "true",
                        "skipRedownload": "true",
                        "changeCategory": "false",
                    },
                )
            else:
                logger.warning(
                    f"[dead-media] auto-drop: no {arr} queue entry for {issue.hash[:10]} — "
                    "drop notification recorded but client-side removal skipped"
                )

        # User-facing notice. Re-uses the full rich format and tacks on the
        # auto-action verdict so the message is self-explanatory.
        rich_msg = self._format_message([issue])
        verdict = ACTION_VERDICTS[ACTION_AUTO_DROP]
        full_msg = (
            f"{rich_msg}\n\n"
            f"*Action taken automatically:* {verdict}\n"
            f"_(classifier verdict: {rec.severity} — {rec.reason})_"
        )
        self._send_alert(issue.poster_url, full_msg, buttons=None)
        logger.info(
            f"[dead-media] Auto-dropped {issue.name[:60]} "
            f"({title or issue.hash[:10]}, reason={rec.reason})"
        )

    def _fire_auto_unmonitor(self, issue: TorrentIssue, blocklist_count: int) -> None:
        """Execute auto-unmonitor + record + notify-only message.

        Three things in order:
          1. Persist the notification row so we have a stable id (for log
             traceability and so dedup recognizes the action was taken).
          2. Call Sonarr's PUT /episode/monitor to flip monitored=false.
          3. Send a notification (no buttons) so the user sees what
             happened without being asked to act.
        """
        group_key_str = f"sonarr:{issue.series_id or 0}:{issue.detector}"
        notif_id = DeadMediaRepository.create_notification(
            group_key=group_key_str,
            arr="sonarr",
            item_id=issue.series_id or 0,
            detector=issue.detector,
            title=issue.series_title,
            affected_hashes=[issue.hash],
            affected_episodes=issue.episode_labels,
            affected_episode_ids=issue.episode_ids,
            telegram_chat_id=str(self.chat_id) if self.chat_id else None,
        )
        if notif_id is None:
            logger.error(f"[dead-media] auto-unmonitor: failed to persist notification for {group_key_str}")
            return

        # Claim immediately as auto-actioned. If somehow racing (shouldn't be
        # possible — we just created the row), the second claim would no-op.
        claimed = DeadMediaRepository.claim_action(notif_id, ACTION_AUTO_UNMONITOR)
        if not claimed:
            logger.warning(f"[dead-media] auto-unmonitor: race on claim_action for notif {notif_id}")

        # Side effect: unmonitor.
        self.sonarr.put(
            "episode/monitor",
            json={"episodeIds": issue.episode_ids, "monitored": False},
        )

        # User-facing notice — single message, no buttons, verdict baked in.
        # Use the same rich format so the user gets context.
        rich_msg = self._format_message([issue])
        verdict = ACTION_VERDICTS[ACTION_AUTO_UNMONITOR]
        full_msg = (
            f"{rich_msg}\n\n"
            f"*Action taken automatically:* {verdict}\n"
            f"_(blocklist count {blocklist_count} ≥ threshold "
            f"{self.auto_unmonitor_after_blocklists})_"
        )
        self._send_alert(issue.poster_url, full_msg, buttons=None)
        logger.info(
            f"[dead-media] Auto-unmonitored {len(issue.episode_ids)} ep(s) of "
            f"{issue.series_title or issue.series_id} "
            f"(blocklist count {blocklist_count})"
        )

    # ---- action handlers (called by Telegram callback router) ----

    def handle_user_action(
        self, notification_id: int, action: str,
    ) -> tuple[bool, str]:
        """Execute a user-chosen action on a notification.

        Idempotent: a second call (same id, same or different action)
        returns the same outcome without firing side-effects, because
        `claim_action` is conditional on action_taken IS NULL.

        Returns (success, human_message). The caller (callback handler)
        uses the message both for ack popups and for editMessageText.
        """
        if action not in VALID_USER_ACTIONS:
            return False, f"❌ Unknown action `{action}`"

        notif = DeadMediaRepository.get(notification_id)
        if not notif:
            # Row is gone — either expired past TTL cleanup, dropped during
            # maintenance, or this is a very old Telegram message whose
            # underlying record has been reaped. Either way the side-effect
            # window has closed: the torrent it referenced is almost
            # certainly already handled (imported, dropped, or removed by
            # qBit cleanup). Tell the user instead of just "not found".
            return False, (
                "ℹ️ This alert is stale — the underlying record has been "
                "cleaned up. The torrent it referenced was likely already "
                "imported, dropped, or removed. Check Sonarr/qBit to "
                "confirm; no action taken."
            )

        # Already-acted? Don't re-fire side effects, but tell the user what
        # already happened.
        if notif.get("action_taken"):
            prev = notif["action_taken"]
            verdict = ACTION_VERDICTS.get(prev, prev)
            return False, f"ℹ️ Already actioned: {verdict}"

        # Claim the action atomically (single UPDATE with WHERE
        # action_taken IS NULL). Wins exactly once across concurrent
        # callbacks.
        if not DeadMediaRepository.claim_action(notification_id, action):
            # Another caller won the race.
            notif = DeadMediaRepository.get(notification_id) or {}
            prev = notif.get("action_taken", "unknown")
            verdict = ACTION_VERDICTS.get(prev, prev)
            return False, f"ℹ️ Already actioned: {verdict}"

        # Side effects per action.
        try:
            if action == ACTION_SEARCH:
                self._do_search_again(notif)
            elif action == ACTION_DROP:
                self._do_drop(notif)
            elif action == ACTION_UNMONITOR_EPS:
                self._do_unmonitor_eps(notif)
            elif action == ACTION_UNMONITOR_SERIES:
                self._do_unmonitor_series(notif)
        except Exception as e:
            logger.error(
                f"[dead-media] Action {action} failed for notif {notification_id}: {e}",
                exc_info=True,
            )
            # Leave action_taken set so we don't retry the side effects on
            # next click (idempotency over completeness).
            return False, f"⚠️ {ACTION_VERDICTS[action]}\nbut an error occurred — check logs."

        return True, ACTION_VERDICTS[action]

    # Each action helper takes the notification dict (already detached from
    # the DB row) and only talks to qBittorrent / *arr clients. None of
    # them write back to the dead_media table — claim_action did that.

    def _do_search_again(self, notif: dict[str, Any]) -> None:
        """Remove from qbt, blocklist, trigger a new search.

        Implementation: delete the *arr queue entry with removeFromClient=
        true, blocklist=true, skipRedownload=false. *arr handles the qbt
        removal + new search atomically.
        """
        arr_client = self._arr_for(notif["arr"])
        if not arr_client:
            return
        for h in notif.get("affected_hashes", []):
            queue_id = self._find_queue_id_by_hash(arr_client, h)
            if not queue_id:
                continue
            arr_client.delete(
                f"queue/{queue_id}",
                params={
                    "removeFromClient": "true",
                    "blocklist": "true",
                    "skipRedownload": "false",
                    "changeCategory": "false",
                },
            )

    def _do_drop(self, notif: dict[str, Any]) -> None:
        """Same as search_again but skipRedownload=true: blocklist + remove,
        no new search. Use this to break runaway loops on dead series."""
        arr_client = self._arr_for(notif["arr"])
        if not arr_client:
            return
        for h in notif.get("affected_hashes", []):
            queue_id = self._find_queue_id_by_hash(arr_client, h)
            if not queue_id:
                continue
            arr_client.delete(
                f"queue/{queue_id}",
                params={
                    "removeFromClient": "true",
                    "blocklist": "true",
                    "skipRedownload": "true",
                    "changeCategory": "false",
                },
            )

    def _do_unmonitor_eps(self, notif: dict[str, Any]) -> None:
        """Unmonitor specific Sonarr episodes by id.

        Sonarr v3+ exposes POST /episode/monitor {episodeIds: [...],
        monitored: false}. (Older versions used PUT /episode/{id}.) This
        path is sonarr-only; movies don't have episodes.
        """
        if notif["arr"] != "sonarr" or not self.sonarr:
            return
        ep_ids = notif.get("affected_episode_ids", [])
        if not ep_ids:
            return
        self.sonarr.put(
            "episode/monitor",
            json={"episodeIds": ep_ids, "monitored": False},
        )

    def _do_unmonitor_series(self, notif: dict[str, Any]) -> None:
        """Flip a Sonarr series' monitored=false. Stops all future grabs."""
        if notif["arr"] != "sonarr" or not self.sonarr or not notif.get("item_id"):
            return
        sid = notif["item_id"]
        res = self.sonarr.get(f"series/{sid}")
        if not res:
            return
        series = res.json()
        series["monitored"] = False
        self.sonarr.put(f"series/{sid}", json=series)

    # ---- arr utilities ----

    def _arr_for(self, arr: str) -> Optional[ArrClient]:
        if arr == "sonarr":
            return self.sonarr
        if arr == "radarr":
            return self.radarr
        return None

    def _find_queue_id_by_hash(self, client: ArrClient, torrent_hash: str) -> Optional[int]:
        """Look up a queue record id by its downloadId. Returns the first
        match; queue items for season packs may explode into multiple rows
        but they share an id reference Sonarr uses for delete cascading."""
        try:
            res = client.get(
                "queue",
                params={
                    "pageSize": 1000,
                    "includeUnknownSeriesItems": "true",
                    "includeUnknownMovieItems": "true",
                },
            )
            if not res:
                return None
            data = res.json()
            for r in data.get("records", []) if isinstance(data, dict) else []:
                if (r.get("downloadId") or "").lower() == torrent_hash.lower():
                    return r.get("id")
        except Exception as e:
            logger.error(f"[dead-media] queue lookup failed: {e}")
        return None
