# ott-sync

Bridges Radarr/Sonarr with JustWatch — auto-skips downloads for media already available on configured OTT providers (Netflix, Prime, etc.).

## Architecture
- `main.py` — entry point; wires managers, starts FastAPI, registers CLI + cron.
- `ott/api/` — FastAPI routes: webhooks (radarr/sonarr/telegram), health, cache, metrics.
- `ott/managers/` — `base.py` (shared logic), `radarr.py`, `sonarr.py`, `telegram.py`.
- `ott/clients/` — HTTP clients: `arr_client.py`, `justwatch.py`, `tmdb.py`, `anilist.py`.
- `ott/db/` — SQLAlchemy + SQLite (WAL mode); `client.py`, `schema.py`, `repositories/`.
- `ott/utils/` — `cache.py`, `reload.py` (hot config reload), `rate_limiter.py`, `anime_detector.py`.
- `ott/cli/commands.py` — CLI entry points registered under `main.py`.
- Config: `config.json` (gitignored) — see `config.json.example` for shape.

## Decision flow (webhook handler, summarized)

Controlled by `auto_download` config flag (default: `false`).

1. If item has `ott-override` tag → skip all checks (user escape hatch).
1.5. **Import complete** (`Download` event, when `unmonitor_on_download.enabled`): unmonitor the imported item once quality ≥ `min_resolution` (default 720p), skipping `ott-override` items. Sonarr rolls up episode → season (fully downloaded) → series (fully downloaded **and** `status == "ended"`; specials/season 0 excluded). Sub-threshold imports stay monitored so a later upgrade can land. Dispatched before the OTT/manual-add logic, so import events never run a JustWatch lookup.
2. **Automatic mode** (`auto_download: true`):
   - JustWatch lookup → on a configured provider: tag `ott-<provider>`, unmonitor, cancel any queued downloads (queue-record id DELETE, `blocklist=false`), send Telegram notification with "Download anyway" override button.
   - Not on OTT → proceed normally, download.
3. **Manual mode** (`auto_download: false`):
   - Every new item is gated; OTT is informational only.
   - Unmonitor, cancel queue, fetch TMDB/AniList ratings, send Telegram notification with "Approve Download" button, tag `ott-processed`, wait forever for user decision.
   - `Grab` event for already-notified item → cancel queue only (no re-notify).
4. Telegram callbacks:
   - `override:*` (automatic mode) — re-monitor + trigger search, add `ott-override` tag.
   - `approve:*` (manual mode) — re-monitor + trigger search (no tag change).
   - Both callbacks acquire the per-item lock to serialize against webhook flow.
5. Future: 3-tier rating threshold gate (Plan.md Phase 7.1) layers on top of `auto_download=true` as "auto-approve above X%".

## Tag vocabulary (applied to *arr items)
- `ott-<provider>` — found on that OTT provider (e.g. `ott-netflix`).
- `ott-override` — user escape hatch; ott-sync never touches these items after this tag is added.
- `ott-processed` / `ott-skipped` — terminal states.
- `anime-checked` / `anime-detected` / `anime-maybe` — anime detection states.
- Import-list tags (e.g. `list-trakt`) — configured per-list in *arr; Plan.md Phase 7.2 plans to use them for inverse-detection of manual adds.

## Known issues
See [Plan.md](Plan.md) — 36 catalogued issues across phases. Phase 1 (critical correctness, 8 items) and Phase 6 (manual-mode polish, 7 items) both complete as of 2026-04-05. Phases 2 (reliability), 3 (hardening), 4 (tests), 5 (docs), 7 (optional enhancements) pending.

## Running
- `python main.py` — starts FastAPI server + cron scheduler.
- `python main.py cron` — one-shot cron sweep.
- `python main.py migrate-ott-tags {radarr|sonarr}` — backfill provider tags.
- `pytest tests/` — run tests (coverage is ~40%, critical paths in `ott/db/` + `ott/utils/` untested).

## Gotchas
- Config hot-reload swaps manager instances live — webhook handlers can see half-swapped state during reload (no lock yet; see Plan.md §3.2).
- `datetime.utcnow()` vs `datetime.now()` are mixed across cache layers causing TTL drift (Plan.md §2.9).
- Webhook endpoints have no signature verification (Plan.md §2.4).
- Deployment config lives at `/ssd/tools/docker/arrs/ott-sync/config.json` (referenced by `test_config.py::test_config_load_missing_file`, which fails because of the fallback path — Plan.md §2.8).
- `config.telegram` is a dict now (post Plan.md §1.2); access fields with `config.telegram["key"]`, not attribute style.
- `unmonitor_on_download` only fires if the Radarr/Sonarr webhook connections have the **"On Import" / "On File Import"** event enabled (internally `eventType: "Download"`). Without it, imports never reach ott-sync and nothing is unmonitored.
