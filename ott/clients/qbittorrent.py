"""HTTP client for qBittorrent WebUI API (v2)

Wraps qBittorrent's auth + torrent-info + actions in a small surface that
matches the style of arr_client.ArrClient. Uses a persistent cookie jar so
restarts don't force re-login.

Reference: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)
"""

import logging
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger("ott-hooks")

_RETRYABLE_STATUS = {429, 502, 503, 504}
_RETRY_DELAYS = (1.0, 2.0, 4.0)


class QBittorrentClient:
    """qBittorrent WebUI client.

    Auth model: POST /api/v2/auth/login sets a SID cookie. We persist that
    cookie to disk (cookie jar) so restarts don't force a fresh login.
    A 403 anywhere triggers a re-login + single retry.
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        cookie_jar_path: str | Path = "/config/qbt_cookie.jar",
        timeout: int = 30,
    ):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.cookie_jar_path = Path(cookie_jar_path)
        self.timeout = timeout

        self._session = requests.Session()
        self._session.cookies = MozillaCookieJar(str(self.cookie_jar_path))
        if self.cookie_jar_path.exists():
            try:
                self._session.cookies.load(ignore_discard=True, ignore_expires=True)
                logger.info(f"[qbt] Loaded cookie jar from {self.cookie_jar_path}")
            except Exception as e:
                logger.warning(f"[qbt] Cookie jar load failed ({e}); will login fresh")

    # ---- auth ----

    def login(self) -> bool:
        """Log in and persist the SID cookie. Returns True on success."""
        try:
            res = self._session.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
            res.raise_for_status()
            if res.text.strip().lower() != "ok.":
                logger.error(f"[qbt] Login rejected: {res.text!r}")
                return False
            self.cookie_jar_path.parent.mkdir(parents=True, exist_ok=True)
            self._session.cookies.save(ignore_discard=True, ignore_expires=True)
            logger.info(f"[qbt] Logged in; saved cookies to {self.cookie_jar_path}")
            return True
        except Exception as e:
            logger.error(f"[qbt] Login failed: {e}")
            return False

    # ---- queries ----

    def list_torrents(self, filter: Optional[str] = None) -> list[dict[str, Any]]:
        """Return all torrents (or filtered subset).

        See qBittorrent docs for the full field list — useful ones include
        `hash`, `name`, `state`, `category`, `tags`, `save_path`, `added_on`,
        `completion_on`, `last_activity`, `num_seeds`, `num_leechs`, `progress`,
        `size`, `dlspeed`, `upspeed`, `eta`, `availability`.
        """
        params = {"filter": filter} if filter else None
        res = self._request("GET", "torrents/info", params=params)
        if res is None:
            return []
        try:
            return res.json()
        except ValueError as e:
            logger.error(f"[qbt] list_torrents parse failed: {e}")
            return []

    def get_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        """Return file list for a torrent. Each entry has `name`, `size`, etc.

        File paths are relative to the torrent's `save_path`.
        """
        res = self._request("GET", "torrents/files", params={"hash": torrent_hash})
        if res is None:
            return []
        try:
            return res.json()
        except ValueError as e:
            logger.error(f"[qbt] get_files({torrent_hash[:10]}) parse failed: {e}")
            return []

    # ---- actions ----

    def resume(self, hashes: list[str]) -> bool:
        """Resume torrents. qBittorrent 5.x renamed pause/resume to stop/start;
        v2 API endpoint is /torrents/start (or /resume on older builds).
        """
        if not hashes:
            return True
        res = self._request("POST", "torrents/start", data={"hashes": "|".join(hashes)})
        return res is not None

    def delete(self, hashes: list[str], delete_files: bool = False) -> bool:
        """Delete torrents. If delete_files=False, data on disk is preserved."""
        if not hashes:
            return True
        res = self._request(
            "POST",
            "torrents/delete",
            data={
                "hashes": "|".join(hashes),
                "deleteFiles": str(delete_files).lower(),
            },
        )
        return res is not None

    # ---- internals ----

    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> Optional[requests.Response]:
        """HTTP with retry/backoff + automatic re-login on 403.

        Mirrors arr_client._request semantics for retryable status codes and
        connection errors, but adds: if we see a 403, attempt one login and
        replay the request (qbt's session cookies expire silently).
        """
        url = f"{self.url}/api/v2/{endpoint}"
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        relogin_attempted = False

        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                res = self._session.request(method, url, **kwargs)
            except requests.RequestException as e:
                if attempt == len(_RETRY_DELAYS):
                    logger.error(f"[qbt] {method} {endpoint} failed after {attempt} attempts: {e}")
                    return None
                logger.warning(f"[qbt] {method} {endpoint} attempt {attempt} failed ({e}); retrying in {delay}s")
                time.sleep(delay)
                continue

            if res.status_code == 403 and not relogin_attempted:
                logger.info(f"[qbt] 403 on {endpoint}; re-logging in")
                relogin_attempted = True
                if self.login():
                    continue  # retry immediately with fresh cookie
                return None

            if res.ok:
                return res

            if res.status_code in _RETRYABLE_STATUS and attempt < len(_RETRY_DELAYS):
                logger.warning(f"[qbt] {method} {endpoint} got {res.status_code}; retrying in {delay}s")
                time.sleep(delay)
                continue

            logger.error(f"[qbt] {method} {endpoint} failed → {res.status_code} {res.text[:200]}")
            return None

        return None
