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
1. If item has `ott-override` tag → skip all checks (manual-add escape hatch).
2. JustWatch lookup → if on a configured provider: tag `ott-<provider>`, unmonitor, delete from *arr queue (`blocklist=false`).
3. If NOT on OTT → rating gate (TMDB `vote_average`, min 50 votes):
   - ≥ 80% → auto-download
   - 70–79% OR trending → Telegram approval (wait forever, no timeout)
   - < 70% AND not trending → skip silently (`ott-low-rating`)
   - No rating → defer 7 days, re-check on weekly cron (`ott-pending-rating`)
4. Sonarr is two-stage: series-level at `SeriesAdd`, season-level at per-episode `Grab` webhook.

## Tag vocabulary (applied to *arr items)
- `ott-<provider>` — found on that OTT provider (e.g. `ott-netflix`).
- `ott-override` — user escape hatch; ott-sync never touches these items.
- `ott-processed` / `ott-skipped` — terminal states.
- `ott-low-rating` — skipped because rating below threshold.
- `ott-pending-rating` / `ott-pending-approval` — in rating-gate limbo.
- `anime-checked` / `anime-detected` / `anime-maybe` — anime detection states.
- Import-list tags (e.g. `list-trakt`) — configured per-list in *arr; used to distinguish manual vs list-sourced adds.

## Known issues
See [Plan.md](Plan.md) — 36 catalogued issues across 5 phases (critical correctness → reliability → hardening → tests → docs) plus Phase 6 for the rating-gate feature. Start with Phase 1 before anything else.

## Running
- `python main.py` — starts FastAPI server + cron scheduler.
- `python main.py cron` — one-shot cron sweep.
- `python main.py migrate-ott-tags {radarr|sonarr}` — backfill provider tags.
- `pytest tests/` — run tests (coverage is ~40%, critical paths in `ott/db/` + `ott/utils/` untested).

## Gotchas
- Config hot-reload swaps manager instances live — webhook handlers can see half-swapped state during reload (no lock yet; see Plan.md §3.2).
- `datetime.utcnow()` vs `datetime.now()` are mixed across cache layers causing TTL drift (Plan.md §2.9).
- Webhook endpoints have no signature verification (Plan.md §2.4).
- `self.telegram` is assigned twice in `config.py` with conflicting types — both dict and object access patterns exist in the codebase (Plan.md §1.2).
