#!/usr/bin/env python3
"""
ott_hooks.py

OTT-aware governance for Radarr/Sonarr
- Event-driven (webhooks)
- Scheduled reconciliation (cron)
- Human override via Telegram
"""

import json
import sys
import time
import threading
import logging
import requests
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from functools import cached_property
from concurrent.futures import ThreadPoolExecutor, as_completed
from abc import ABC, abstractmethod
from datetime import datetime

import typer
from fastapi import FastAPI, Request
import uvicorn
from simplejustwatchapi.justwatch import search

# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ott-hooks")

# ------------------------------------------------------------------------------
# App / CLI
# ------------------------------------------------------------------------------
app = typer.Typer()
fastapi_app = FastAPI()

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
CONFIG_PATH = Path("/config/config.json")
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path("/ssd/tools/docker/plex_addons/ott-sync/config.json")

if not CONFIG_PATH.exists():
    logger.error("Config file not found")
    sys.exit(1)

with CONFIG_PATH.open("r", encoding="utf-8") as f:
    CONFIG = json.load(f)

logger.info(f"Config loaded from {CONFIG_PATH}")

def build_telegram_caption(
    title: str,
    year: int | None,
    provider: str,
    region: str,
    item_type: str,
    item_id: int,
    requested_by: str | None = None,
):
    ts = datetime.now().strftime("%d %b %Y, %H:%M")
    return (
        f"🎬 *{title}*"
        f"{f' ({year})' if year else ''}\n\n"
        f"📺 *Available on:* {provider} ({region})\n"
        f"📂 *Type:* {item_type.title()}\n"
        f"🆔 *ID:* {item_id}\n"
        f"👤 *Requested by:* {requested_by or 'Unknown'}\n"
        f"⏱ *Detected at:* {ts}\n\n"
        "⚠️ This title is already available on OTT.\n"
        "It will *not* be downloaded unless you approve.\n\n"
        "👇 Choose an action:"
    )

# ------------------------------------------------------------------------------
# Telegram
# ------------------------------------------------------------------------------
class TelegramNotifier:
    def __init__(self, config: dict):
        cfg = config.get("telegram", {})
        self.enabled = cfg.get("enabled", False)
        self.token = cfg.get("bot_token")
        self.chat_id = cfg.get("chat_id")
        self.region = cfg.get("region", "your region")

    def send(self, message: str, buttons: list | None = None):
        if not self.enabled:
            logger.debug("[TG] Telegram disabled")
            return
        if not self.token or not self.chat_id:
            logger.error("[TG] Missing bot_token or chat_id")
            return

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}

        res = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json=payload,
            timeout=15,
        )

        if res.ok:
            logger.info("[TG] Message sent")
        else:
            logger.error(f"[TG] Send failed: {res.text}")
            
    def send_photo(self, photo_url: str, caption: str, buttons: list | None = None):
        if not self.enabled:
            return
        if not self.token or not self.chat_id:
            logger.warning("[TG] Missing bot_token or chat_id")
            return

        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        payload = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "Markdown",
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}

        res = requests.post(url, json=payload, timeout=15)
        if not res.ok:
            logger.error(f"[TG] Photo send failed: {res.text}")
            
            
telegram_notifier = TelegramNotifier(CONFIG)


# ------------------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------------------
@dataclass
class ProcessingMetrics:
    checked: int = 0
    cleaned: int = 0
    marked_processed: int = 0
    skipped_override: int = 0
    already_processed: int = 0
    errors: int = 0

# ------------------------------------------------------------------------------
# Base Manager
# ------------------------------------------------------------------------------
class OTTBaseManager(ABC):
    def __init__(self, url, api_key, ott_providers, config):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.ott_providers = ott_providers
        self.config = config
        self.pool = ThreadPoolExecutor(max_workers=5)
        self._metrics_lock = threading.Lock()

    # -------------------- HTTP --------------------
    def headers(self):
        return {"X-Api-Key": self.api_key}

    def safe(self, method, endpoint, **kwargs):
        url = f"{self.url}/api/v3/{endpoint}"
        res = requests.request(
            method, url, headers=self.headers(), timeout=120, **kwargs
        )
        if not res.ok:
            logger.error(f"[HTTP] {method} {endpoint} failed → {res.text}")
            return None
        return res

    # -------------------- Tags --------------------
    @cached_property
    def skipped_tag(self):
        return self._get_or_create_tag("ott-skipped")

    @cached_property
    def processed_tag(self):
        return self._get_or_create_tag("ott-processed")

    @cached_property
    def override_tag(self):
        return self._get_or_create_tag("ott-override")

    def _get_or_create_tag(self, label):
        tags = self.safe("GET", "tag").json()
        for t in tags:
            if t["label"] == label:
                return t["id"]
        res = self.safe("POST", "tag", json={"label": label})
        return res.json()["id"]

    # -------------------- OTT --------------------
    def get_providers(self, title, year):
        logger.info(f"[OTT] Searching providers for '{title}' ({year})")
        try:
            results = search(title, "IN", "en", 5)
            for item in results:
                if year and item.release_year != year:
                    continue
                found = [
                    o.package.name
                    for o in (item.offers or [])
                    if o.package.name in self.ott_providers
                ]
                if found:
                    logger.info(f"[OTT] FOUND on {found}")
                    return found
        except Exception as e:
            logger.warning(f"[OTT] Lookup failed: {e}")
            return None
        logger.info("[OTT] Not found")
        return []

    # -------------------- Enforcement --------------------
    def enforce_block(self, item_id, delete_files=True):
        logger.warning(f"[ACTION] Blocking {self.item_type()} id={item_id}")

        self.safe("POST", "command", json={
            "name": "CancelPendingDownloads",
            f"{self.item_type()}Ids": [item_id],
        })

        self.safe("DELETE", "queue", params={
            f"{self.item_type()}Id": item_id,
            "removeFromClient": True,
        })

        res = self.safe("GET", f"{self.item_type()}/{item_id}")
        if not res:
            return

        data = res.json()
        data["monitored"] = False
        data["tags"] = list(
            set(data.get("tags", [])) | {self.skipped_tag, self.processed_tag}
        )

        self.safe("PUT", f"{self.item_type()}/{item_id}", json=data)
        logger.info("[ACTION] Item unmonitored + tagged")
        
        # 🗑️ Delete files from disk to save space (OTT available)
        if delete_files:
            delete_res = self.safe(
                "DELETE",
                f"{self.item_type()}/{item_id}",
                params={"deleteFiles": True, "addImportExclusion": False}
            )
            if delete_res:
                logger.info("[ACTION] Files deleted from disk")
            else:
                logger.warning("[ACTION] File deletion failed")

    # -------------------- Webhook --------------------
    def added_hook(self, payload):
        event = payload.get("eventType")
        item = payload.get(self.item_type(), {})
    
        title = item.get("title")
        year = item.get("year")
        item_id = item.get("id")
        tags = set(item.get("tags", []))
    
        logger.info(f"[WEBHOOK] {event} → {title} ({year}) id={item_id}")
    
        if event not in ("MovieAdded", "SeriesAdded", "Grab"):
            return
    
        # 🔒 Absolute override guard
        if self.override_tag in tags:
            logger.info(
                f"[DECISION] ott-override present → skip ALL OTT enforcement "
                f"for {self.item_type()} id={item_id}"
            )
            return
    
        if event == "Grab":
            logger.info("[DECISION] Grab event → checking OTT as fallback")
    
        # ─────────────────────────────────────────────
        # OTT lookup decision (JustWatch only)
        # ─────────────────────────────────────────────
        providers = None
        was_previously_blocked = False
    
        if self.processed_tag in tags:
            logger.info("[DECISION] ott_processed present → skip JustWatch lookup")
            
            # 🚨 Check if item was previously blocked (has ott-skipped tag)
            if self.skipped_tag in tags:
                was_previously_blocked = True
                logger.warning(
                    f"[DECISION] ott-skipped present → item was previously blocked. "
                    f"Re-enforcing block for {self.item_type()} id={item_id}"
                )
                # Re-use previous OTT detection for Telegram notification
                # (we don't know the exact provider, but we know it was on OTT)
                providers = ["OTT"]  # Placeholder to trigger notification flow
        else:
            providers = self.get_providers(title, year)
    
            # 🌐 Infra failure → fail open, retry later
            if providers is None:
                logger.warning("[DECISION] OTT lookup failed → defer decision")
                return
    
        # ─────────────────────────────────────────────
        # Telegram + enforcement decision
        # ─────────────────────────────────────────────
        if providers:
            # 🖼 Extract poster if available
            images = item.get("images", [])
            poster = next(
                (img.get("remoteUrl") for img in images if img.get("coverType") == "poster"),
                None,
            )
    
            # 👤 Best-effort requester info
            requested_by = (
                payload.get("username")
                or payload.get("requestedBy")
                or payload.get("author")
                or "Overseerr"
            )
    
            caption = build_telegram_caption(
                title=title,
                year=year,
                provider=providers[0],
                region=telegram_notifier.region,
                item_type=self.item_type(),
                item_id=item_id,
                requested_by=requested_by,
            )
    
            buttons = [[{
                "text": "⬇️ Download anyway",
                "callback_data": f"override:{self.item_type()}:{item_id}"
            }]]
    
            # 📣 Always notify Telegram if OTT found (or re-blocked)
            if was_previously_blocked:
                # Re-blocking scenario - simpler message
                telegram_notifier.send(
                    f"🚨 *Re-block detected*\n\n"
                    f"🎬 *{title}*{f' ({year})' if year else ''}\n\n"
                    f"This item was previously blocked but a download was attempted.\n"
                    f"Use the button below to approve if this was intentional.",
                    buttons=buttons
                )
            elif poster:
                telegram_notifier.send_photo(poster, caption, buttons=buttons)
            else:
                telegram_notifier.send(caption, buttons=buttons)
    
            logger.warning(
                f"[DECISION] OTT found on {providers[0]} → enforcing block "
                f"{self.item_type()} id={item_id}"
            )
    
            # 🚫 Enforce block (delete files only if NOT previously blocked to avoid redundant deletions)
            self.enforce_block(item_id, delete_files=not was_previously_blocked)
    
            # ✅ Mark processed AFTER a successful decision
            if self.processed_tag not in tags:
                res = self.safe("GET", f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(set(data.get("tags", [])) | {self.processed_tag})
                    self.safe("PUT", f"{self.item_type()}/{item_id}", json=data)
    
        else:
            # ❌ Not on OTT → mark processed to avoid future lookups
            logger.info("[DECISION] Not on OTT → marking processed")
    
            if self.processed_tag not in tags:
                res = self.safe("GET", f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(set(data.get("tags", [])) | {self.processed_tag})
                    self.safe("PUT", f"{self.item_type()}/{item_id}", json=data)

    
    # -------------------- Cron --------------------
    def cron_cleanup(self):
        logger.info(f"[CRON] Starting cleanup for {self.item_type()}")
        metrics = ProcessingMetrics()
        items = self.fetch_items()

        for item in items:
            tags = set(item.get("tags", []))
            item_id = item.get("id")
            
            # 🔒 Respect override - never touch items with ott-override
            if self.override_tag in tags:
                metrics.skipped_override += 1
                continue
                
            # Skip unmonitored items
            if not item.get("monitored"):
                continue
                
            if self.processed_tag in tags:
                metrics.already_processed += 1
                continue

            metrics.checked += 1
            providers = self.get_providers(item.get("title"), item.get("year"))
            if providers is None:
                logger.warning("[DECISION] OTT lookup failed → defer decision")
                continue   # Skip this item, continue with others
            
            if providers:
                metrics.cleaned += 1
                # 🗑️ Cron also deletes files (saves disk space)
                self.enforce_block(item_id, delete_files=True)
            else:
                metrics.marked_processed += 1
                res = self.safe("GET", f"{self.item_type()}/{item_id}")
                if res:
                    data = res.json()
                    data["tags"] = list(
                        set(data.get("tags", [])) | {self.processed_tag}
                    )
                    self.safe("PUT", f"{self.item_type()}/{item_id}", json=data)

        logger.info(f"[CRON] Completed → {metrics}")
        return metrics

    @abstractmethod
    def fetch_items(self): ...
    @abstractmethod
    def item_type(self): ...

# ------------------------------------------------------------------------------
# Radarr / Sonarr
# ------------------------------------------------------------------------------
class RadarrManager(OTTBaseManager):
    def fetch_items(self):
        return self.safe("GET", "movie").json()
    def item_type(self):
        return "movie"

class SonarrManager(OTTBaseManager):
    def fetch_items(self):
        return self.safe("GET", "series").json()
    def item_type(self):
        return "series"


radarr_mgr = RadarrManager(
    CONFIG["radarr_url"],
    CONFIG["radarr_api_key"],
    set(CONFIG["ott_providers"]),
    CONFIG,
)

sonarr_mgr = SonarrManager(
    CONFIG["sonarr_url"],
    CONFIG["sonarr_api_key"],
    set(CONFIG["ott_providers"]),
    CONFIG,
)

# ------------------------------------------------------------------------------
# Telegram Callback (Override)
# ------------------------------------------------------------------------------
@fastapi_app.post("/telegram/callback")
async def telegram_callback(request: Request):
    payload = await request.json()
    cb = payload.get("callback_query", {})
    data = cb.get("data", "")
    user = cb.get("from", {}).get("first_name", "User")

    if not data.startswith("override:"):
        return {"ok": True}

    _, typ, item_id = data.split(":")
    item_id = int(item_id)
    mgr = radarr_mgr if typ == "movie" else sonarr_mgr

    res = mgr.safe("GET", f"{typ}/{item_id}")
    if not res:
        telegram_notifier.send("❌ Failed to apply override")
        return {"ok": True}

    data = res.json()
    tags = set(data.get("tags", []))

    tags.discard(mgr.skipped_tag)
    tags.add(mgr.override_tag)

    data["tags"] = list(tags)
    data["monitored"] = True

    mgr.safe("PUT", f"{typ}/{item_id}", json=data)

    mgr.safe("POST", "command", json={
        "name": "MoviesSearch" if typ == "movie" else "SeriesSearch",
        f"{typ}Ids": [item_id],
    })

    telegram_notifier.send(
        f"⬇️ *Download confirmed*\n\nOverride applied by {user}."
    )

    logger.info("[TG] Override applied and search triggered")
    return {"ok": True}

# ------------------------------------------------------------------------------
# Webhooks
# ------------------------------------------------------------------------------
@fastapi_app.get("/health")
async def health():
    return {"status": "ok"}
    
@fastapi_app.get("/cron")
async def health():
    cron()

@fastapi_app.post("/radarr")
async def radarr_webhook(request: Request):
    payload = await request.json()
    radarr_mgr.added_hook(payload)
    
@fastapi_app.post("/sonarr")
async def sonarr_webhook(request: Request):
    payload = await request.json()
    sonarr_mgr.added_hook(payload)

# ------------------------------------------------------------------------------
# CLI COMMANDS
# ------------------------------------------------------------------------------
@app.command()
def cron():
    logger.info("=" * 60)
    logger.info("Starting scheduled cron cleanup")
    logger.info("=" * 60)

    radarr_metrics = radarr_mgr.cron_cleanup()
    sonarr_metrics = sonarr_mgr.cron_cleanup()

    logger.info(f"Radarr metrics: {radarr_metrics}")
    logger.info(f"Sonarr metrics: {sonarr_metrics}")

    logger.info("Cron cleanup completed for all services")

@app.command()
def server(host: str = "0.0.0.0", port: int = 9123):
    logger.info(f"Starting webhook server on {host}:{port}")
    uvicorn.run("ott_hooks:fastapi_app", host=host, port=port, reload=False)

@app.command()
def run_all():
    logger.info("Starting combined server + cron mode")

    def run_cron_loop():
        initial_delay = CONFIG.get("cron_initial_delay_seconds", 60)
        interval_hours = CONFIG.get("cron_interval_hours", 24)

        logger.info(f"Cron starts in {initial_delay}s, interval {interval_hours}h")
        time.sleep(initial_delay)

        while True:
            cron()
            time.sleep(interval_hours * 3600)

    threading.Thread(target=run_cron_loop, daemon=True).start()
    server()

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    run_all()
