# OTT-Sync Remediation Plan

Audit date: 2026-04-05  ·  Reconciled with working-tree WIP: 2026-04-05
Scope: Full-project review — main.py, ott/{api,cli,clients,db,managers,utils}, config.py, models.py, tests/, docs.
Method: 4 parallel code-review passes (main/config, managers/clients, db/api, cli/utils/tests).

Total actionable issues: **36** (8 critical, 11 high, 12 medium, 5 low) plus significant test-coverage gaps.

**Reality check (2026-04-05):** the working tree already contains WIP that implements a **binary-switch manual-approval mode** (different design from what we originally spec'd in Phase 6). The user chose to keep that design; Phase 6 has been rewritten to describe what's built and what's missing. Plan items §1.4 and most of §6.1 are now done in the WIP. See §WIP-STATUS below.

---

## Intended Workflow (reference) — as implemented in WIP

The WIP uses a **binary `auto_download` switch** rather than the rating-threshold gate originally spec'd. Ratings are surfaced in Telegram notifications for human decision-making, not used as automated gates (yet).

### `auto_download = true` (automatic mode)

When a webhook fires from Radarr/Sonarr on a newly-added item:

**Pre-check — respect manual user intent:**
- If the item carries the `ott-override` tag, **skip all OTT logic**. Let the download proceed.

**OTT check runs:**
- **If available on a configured OTT provider** → unmonitor + tag `ott-<provider>` + `ott-skipped` + `ott-processed` + cancel queue + send Telegram notification (with ratings) + "✅ Approve Download" button.
- **If NOT on OTT** → proceed normally, download as usual. No rating gate, no extra prompt.

### `auto_download = false` (manual mode)

**Every new item is gated** — OTT availability is informational only.

1. Skip if item is already monitored, already processed, or has `ott-override`.
2. Unmonitor + cancel queue + delete from download client.
3. Fetch TMDB + AniList ratings (IMDb placeholder only).
4. Look up OTT provider (informational, shown in Telegram caption if found).
5. Send Telegram notification with poster, title, provider (if any), ratings, "✅ Approve Download" button.
6. Tag `ott-processed`.
7. Wait forever for user decision.
8. If `Grab` fires for an already-notified item → cancel queue again (don't re-notify).

### Callbacks

- `override:<type>:<id>` — existing flow: remove `ott-skipped`, add `ott-override`, re-monitor, trigger search.
- `approve:<type>:<id>` — new manual-mode flow: re-monitor + trigger search (no tag mutation).

### Future enhancement (moved to Phase 7)

A 3-tier rating threshold gate (80% auto / 70-79% ask / <70% skip / trending override / 7-day defer on no rating) was originally designed in Phase 6 but superseded by the binary-switch design. It can layer on top of the binary switch as "auto-approve above X%" in a later iteration — see Phase 7.

---

## Phase 1 — Critical Correctness Fixes

These block or corrupt core functionality. Fix before anything else.

### 1.1 Invalid JSON in example config
- **File:** [config.json.example](config.json.example) lines 120-162
- **Problem:** Duplicate `anime_detection` block placed outside the root object → file is unparseable.
- **Fix:** Delete lines 122-162 (the duplicate block after the closing brace at line 121).
- **Verify:** `python -c "import json; json.load(open('config.json.example'))"` exits 0.

### 1.2 `Config.telegram` type conflict
- **File:** [ott/config.py:102-108, :163](ott/config.py#L102)
- **Problem:** `self.telegram` first built as a dynamic object (`.bot_token` attr access), then overwritten at line 163 with a raw dict. Dual access patterns leak through the codebase.
- **Fix:** Pick one representation. Recommendation: keep the dict form at line 163 (simpler, matches test_config.py expectations) and **delete lines 102-108**. Then audit all `config.telegram.X` call sites → convert to `config.telegram["X"]`.
- **Call sites to update:** grep for `config.telegram.` and `self.telegram.` across `ott/` and `main.py`.

### 1.3 Duplicate config initialization
- **File:** [ott/config.py:114-188](ott/config.py#L114)
- **Problem:** `cron_initial_delay_seconds`, `cron_interval_hours`, `region`, `justwatch_rate_limit_*`, `verification_delay_seconds`, and `anime_detection` all assigned twice.
- **Fix:** Delete the second block (lines 163-191). First initialization is correct.

### 1.4 RadarrManager initial construction missing params ✅ DONE (WIP)
- **File:** [main.py:289-295](main.py#L289)
- **Status:** Fixed in working-tree WIP. `reload_configuration()` now passes `auto_download`, `tmdb_client`, `anilist_client` to both Radarr and Sonarr managers. The initial startup construction mirror fix still needs verification (check that `main.py` lines ~289-295 match `reload_configuration` shape once WIP is committed).

### 1.5 Provider tag cleanup deletes valid tags
- **File:** [ott/managers/base.py:216](ott/managers/base.py#L216)
- **Problem:** Condition uses `or` where semantics require `and`. A tag is removed if it's missing from **either** current-JustWatch-providers OR configured-providers, but should only be removed if missing from **both**. Legit provider tags get wiped on every sweep.
- **Fix:** Change `or` → `and` on line 216.
- **Regression test:** Seed an item with a legitimate `ott-netflix` tag, run cleanup, assert tag survives when Netflix is configured.

### 1.6 TelegramNotifier signature mismatch
- **File:** [ott/managers/sonarr.py:132-134](ott/managers/sonarr.py#L132)
- **Problem:** Calls `telegram.send_photo(..., chat_id=admin_chat_id)` and `telegram.send(..., chat_id=admin_chat_id)`, but neither method accepts a `chat_id` kwarg. Raises `TypeError` every time anime-maybe notifications fire.
- **Fix (option A):** Remove `chat_id=admin_chat_id` kwargs; notifier sends to its configured chat.
- **Fix (option B, preferred):** Add optional `chat_id` parameter to `TelegramNotifier.send()` and `send_photo()` so admin notifications can route to a different chat.

### 1.7 Duplicate FastAPI route registrations
- **File:** [ott/api/health.py:110-241](ott/api/health.py#L110)
- **Problem:** `/wakeup` and `/cron` are each defined twice. FastAPI keeps only the second definition; the first (with full summary/description metadata) is dead code.
- **Fix:** Delete the duplicate endpoint pair at lines 176-241. Keep the better-documented first pair (110-173).

### 1.8 Database session double-commit
- **File:** [ott/db/client.py:112-135](ott/db/client.py#L112)
- **Problem:** Context manager commits automatically after `yield`, while every repository method ALSO commits explicitly. Violates SQLAlchemy single-responsibility for transactions.
- **Fix:** Remove `session.commit()` from the context manager (line inside try block after yield). Rely on explicit commits in repository methods. Update docstring accordingly.

---

## Phase 2 — Reliability, Security, and Data Integrity

Silent failures and security exposures. Fix immediately after Phase 1.

### 2.1 Async webhook endpoints blocking the event loop
- **File:** [ott/api/webhooks.py:20-40](ott/api/webhooks.py#L20)
- **Problem:** `async def` handlers call synchronous `added_hook()` which does network + DB I/O. Blocks the entire event loop under concurrent webhook load.
- **Fix:** Change the handlers to `def` (FastAPI will run them in a threadpool), OR wrap the blocking call in `await asyncio.to_thread(handler, payload)`.
- **Also applies to:** [health.py:152-153, :219-220](ott/api/health.py#L152) (`trigger_cron` doing sync cleanup), [health.py:31-98](ott/api/health.py#L31) (`health_check` doing sync `client.get("tag")`).

### 2.2 Missing exception handling in webhook endpoints
- **File:** [ott/api/webhooks.py:20-40](ott/api/webhooks.py#L20)
- **Problem:** No try/except. JSON parse errors and handler exceptions return bare 500s with no logging.
- **Fix:**
  ```python
  try:
      payload = await request.json()
      get_radarr_mgr().added_hook(payload)
  except Exception as e:
      logger.error(f"[WEBHOOK] Radarr failed: {e}", exc_info=True)
      return {"ok": False, "error": "Processing failed"}
  ```

### 2.3 Webhook idempotency never checked
- **File:** [ott/api/webhooks.py](ott/api/webhooks.py)
- **Problem:** `WebhookRepository.check_duplicate()` exists but is never called. Radarr/Sonarr retries re-run the full pipeline.
- **Fix:** Call `WebhookRepository.check_duplicate(payload_hash)` before dispatching. Persist hash on successful processing.

### 2.4 No webhook signature verification
- **File:** [ott/api/webhooks.py](ott/api/webhooks.py)
- **Problem:** All webhooks accept any caller.
- **Fix:** Add HMAC-SHA256 verification using shared secrets configured per service. Reject requests with invalid/missing signatures.

### 2.5 `request.client` null-dereference in cache routes
- **File:** [ott/api/cache.py:94-96, :140-142](ott/api/cache.py#L94)
- **Problem:** `request.client.host` crashes with `AttributeError` when `request.client is None` (happens behind some proxies and in tests).
- **Fix:** `client_ip = request.client.host if request.client else None` and handle `None` (deny by default).

### 2.6 Telegram callback payload not validated
- **File:** [ott/api/telegram.py:31-34, :82, :162, :229](ott/api/telegram.py#L31)
- **Problem:** Parses untrusted input without Pydantic validation even though `TelegramCallbackPayload` model exists. Also doesn't verify `anime_enabled` before calling `mgr.set_anime_metadata()`.
- **Fix:** Validate against `TelegramCallbackPayload`. Add `if not mgr.anime_enabled: return {...}` guards.

### 2.7 Silent DB/cache init failures cascade to crashes
- **File:** [main.py:203-205, :208-224](main.py#L203)
- **Problem:** Init errors log a warning and continue, but later `get_db()`/`get_cache()` calls raise `RuntimeError`. "Features will be limited" is false — they crash.
- **Fix:** Either fail-fast with `sys.exit(1)` on init failure, or add `None`-guards on every `get_db()`/`get_cache()` call site with graceful degradation paths.

### 2.8 Hardcoded deployment path in config loader
- **File:** [ott/config.py:220-224](ott/config.py#L220)
- **Context:** `/ssd/tools/docker/arrs/ott-sync/config.json` is this user's actual deployment config path, not a dev leak. But hardcoding it in library code is still wrong — ties the library to one host.
- **Problem:** `Config.load()` silently swaps the caller's path for the hardcoded one. When the caller's path doesn't exist, error messages don't show *which* paths were actually tried.
- **Fix:** Move fallback discovery into `main.py` (lines 167-178) as an explicit list of candidate paths read from an environment variable `OTT_SYNC_CONFIG_PATH` or CLI arg. Remove the hardcoded path from `Config.load()`. Error message should list all candidates tried.
- **Keep the path as the default first candidate** in the main.py list for the user's actual deployment.

### 2.9 Timezone inconsistency (cache expiry drifts)
- **File:** [ott/utils/timestamp_cache.py:105-106, :116](ott/utils/timestamp_cache.py#L105) vs [ott/utils/cache.py:197](ott/utils/cache.py#L197) and schema defaults
- **Problem:** `timestamp_cache.py` uses naïve `datetime.now()`; `cache.py` and schema defaults use `datetime.utcnow()`. TTL comparisons drift by local UTC offset.
- **Fix:** Use timezone-aware UTC everywhere: `datetime.now(timezone.utc)`. Also migrates off deprecated `datetime.utcnow` (Python 3.12+).

### 2.10 Anime state tags erased during provider sweep
- **File:** [ott/managers/base.py:212-214](ott/managers/base.py#L212)
- **Problem:** Exclusion list only protects `ott-skipped`, `ott-processed`, `ott-override`. Does not protect `anime-checked`, `anime-detected`, `anime-maybe` — these get swept when the logic fix from §1.5 lands.
- **Fix:** Extend exclusion list to include all `anime-*` tags. Better: change filter to "only touch tags matching `ott-<provider>` pattern".

### 2.11 Unsafe attribute access in JustWatch client
- **File:** [ott/clients/justwatch.py:80, :93](ott/clients/justwatch.py#L80)
- **Problem:** `offer.package.name` and `item.release_year` accessed without `hasattr`. Crashes on unexpected API payload shape.
- **Fix:** Guard with `hasattr` or wrap with `getattr(..., default)`. Existing code at lines 85-87 already demonstrates the correct pattern.

---

## Phase 3 — Hardening

Improves resilience and observability. Not urgent but important.

### 3.1 HTTP retry/backoff for Arr clients
- **File:** [ott/clients/arr_client.py:104-111](ott/clients/arr_client.py#L104)
- **Problem:** Single request failure = operation failure. No retry for 429/502/503/504 or connection timeouts.
- **Fix:** Add exponential backoff retry loop (3 attempts, 1s → 2s → 4s) for retryable status codes and `RequestException`/`Timeout`.

### 3.2 Config reload race conditions
- **File:** [ott/utils/reload.py:71-76](ott/utils/reload.py#L71) and [main.py:155-191](main.py#L155)
- **Problem:** `reload_callback()` swaps manager instances without a lock; an in-flight webhook can read half-updated state.
- **Fix:** Wrap reload with a threading lock that also gates new webhook dispatch. Or introduce a version counter and atomic swap via a single reference.

### 3.3 Comprehensive config validation
- **File:** [ott/config.py:60-77, :245-252](ott/config.py#L60)
- **Problem:** Only anime_detection validated. No checks for URL format, API key emptiness, numeric ranges (`cron_interval_hours > 0`, `cache_max_size_mb > 0`, `max_concurrent_webhooks` bounded, etc.), region ISO codes, OTT provider list non-empty.
- **Fix:** Add a `Config.validate()` that raises `ConfigurationError` (see §3.4) with clear messages. Run it at the end of `__init__`.

### 3.4 Custom exceptions unused
- **File:** [ott/exceptions.py:9-36](ott/exceptions.py#L9)
- **Problem:** All 6 exceptions defined, zero are raised. Generic `ValueError`/`Exception` used throughout.
- **Fix:** Replace generic raises with the appropriate custom exception. At minimum, `Config.validate` → `ConfigurationError`, JustWatch errors → `JustWatchAPIError`, Arr HTTP errors → `ArrAPIError`, DB failures → `DatabaseError`.

### 3.5 Response-shape consistency
- **Files:** [ott/api/health.py](ott/api/health.py), [ott/api/webhooks.py](ott/api/webhooks.py), [ott/api/telegram.py](ott/api/telegram.py), [ott/api/cache.py](ott/api/cache.py), [ott/api/metrics.py](ott/api/metrics.py)
- **Problem:** Endpoints disagree on error shape — some `HTTPException`, some `{"ok": True}` even for errors, some with `error` field.
- **Fix:** Standardize on `{ok: bool, error: str|None, error_code: str|None}` or always raise `HTTPException` for errors. Document the chosen shape in API_DOCUMENTATION.md.

### 3.6 Type-hint corrections
- **Files:** [ott/db/client.py:137](ott/db/client.py#L137) (`dict[str, any]` → `dict[str, Any]`), [ott/api/cache.py:18](ott/api/cache.py#L18) (`callable` → `Callable[[], JustWatchCache]` from `collections.abc`).

### 3.7 Graceful DB shutdown
- **File:** [ott/db/client.py:269-275](ott/db/client.py#L269)
- **Problem:** `close()` disposes the engine without draining in-flight sessions → "connection closed" errors on shutdown under load.
- **Fix:** Set `_initialized = False` first (prevents new sessions), wait for active sessions, then dispose.

### 3.8 Composite index for webhook dedup
- **File:** [ott/db/schema.py:160-173](ott/db/schema.py#L160)
- **Problem:** Dedup query filters on `(payload_hash, timestamp)` but only has separate indexes.
- **Fix:** Add `Index('idx_webhook_dedup', 'payload_hash', 'timestamp')`.

### 3.9 Introduce migrations
- **Folder:** [ott/db/](ott/db/)
- **Problem:** No Alembic / versioned schema. Upgrades require manual DROP/recreate.
- **Fix:** Add Alembic, generate initial baseline migration from current schema. Document upgrade flow in README.

### 3.10 Rate limiter ergonomics
- **File:** [ott/utils/rate_limiter.py:59, :61-77](ott/utils/rate_limiter.py#L59)
- **Problem:** Tight-loop 0.1s sleeps, silent `None` return on timeout, no reset/introspection.
- **Fix:** Add `get_available_calls()`, `reset()`, and an `execute_raising()` variant. Replace polling with `threading.Condition`.

### 3.11 Remove hardcoded personal artifact
- **File:** [ott/api/health.py:120-136, :188-205](ott/api/health.py#L120)
- **Problem:** Hardcoded MAC `d8:5e:d3:89:7e:ae` + `192.168.1.255` broadcast for "Beast PC" in `/wakeup`.
- **Fix:** Move to config (`wakeup.mac_address`, `wakeup.broadcast_address`), or remove the endpoint if not needed in production.

---

## Phase 4 — Test Coverage

Currently **~40% of the codebase has zero test coverage**. Critical paths must be protected before shipping further changes.

### Missing test files (create):
- `tests/test_database.py` — init, schema creation, WAL mode, repository CRUD, cleanup queries
- `tests/test_cache.py` — hit/miss, TTL expiry, eviction, invalidation endpoints
- `tests/test_webhooks.py` — idempotency, signature verification, error paths
- `tests/test_anime_detector.py` — signal scoring, edge cases (conflicting signals, missing data)
- `tests/test_cli_commands.py` — command registration, argument parsing, mocked manager calls
- `tests/test_utils.py` — rate_limiter, reload, timestamp_cache

### Existing test gaps (expand):
- `tests/test_config.py` — invalid JSON, missing required keys, validation errors
- `tests/test_api.py` — async endpoint behavior under concurrent load, error-response shape

Target: **>70% line coverage** on `ott/db/`, `ott/api/`, `ott/managers/`, `ott/utils/`.

---

## Phase 5 — Documentation Drift

### 5.1 Unverified performance claims
- **File:** [CHANGELOG.md:72, :155](CHANGELOG.md#L72)
- **Problem:** Claims "~80% reduction in API calls" and "~80% cache hit rate" with no benchmarks or runtime metrics.
- **Fix:** Either remove the claims, or expose `_cache_hits`/`_cache_misses` ratio via `/metrics` and back the claim with data.

### 5.2 Config example gaps
- **File:** [config.json.example](config.json.example)
- **Problem:** `auto_download` and `telegram.admin_chat_id` are under-documented.
- **Fix:** Add `_comment_*` strings explaining intent. Make sure every field read in `config.py` has a matching example entry.

### 5.3 README feature status
- **File:** [README.md](README.md)
- **Problem:** Some README features depend on manual webhook wiring that isn't called out.
- **Fix:** Add a "Prerequisites / Wiring" subsection to the webhook section explicitly listing Radarr/Sonarr webhook config required.

---

## §WIP-STATUS — Inventory of already-implemented work

The working-tree WIP (staged 2026-04-05, not yet committed) introduces manual-approval mode. Files touched:

| File | Change | Status |
|---|---|---|
| `main.py` | Wires TMDB + AniList clients into both managers; fixes anime-init indentation | ✅ Done |
| `ott/config.py` | Adds `auto_download: bool` config field (default `false`) | ✅ Done |
| `config.json.example` | Documents `auto_download` option | ✅ Done (but file itself is still broken JSON — §1.1) |
| `ott/managers/base.py` | Adds manual-mode branch in `added_hook()`: unmonitor → cancel queue → fetch ratings → send Telegram prompt → tag processed. Also fetches ratings in automatic mode. | ✅ Done (needs review — see §6.2-WIP-GAPS) |
| `ott/clients/tmdb.py` | Adds `get_movie_ratings()` + `get_series_ratings()` | ✅ Done |
| `ott/clients/anilist.py` | Adds `get_ratings(anilist_id)` → normalized 0–10 score | ✅ Done |
| `ott/models.py` | Extends `build_telegram_caption()` with rating fields + `manual_mode` variant | ✅ Done |
| `ott/api/telegram.py` | Adds `approve:*` callback handler alongside existing `override:*` | ✅ Done |
| (removed) `EXECUTION_FLOWS.md`, `IMPLEMENTATION_SUMMARY.md` | Stale architecture docs deleted | ✅ Done |

**Plan.md items that this WIP addresses or partially addresses:**
- §1.4 RadarrManager init params — ✅ DONE
- §6.1 Queue deletion on OTT found — ✅ DONE (via `CancelPendingDownloads` command + `DELETE /queue`)
- §6.2 Rating integration — partial (ratings are fetched + displayed, but no threshold-based gating)

**WIP gaps / issues identified (§6.2-WIP-GAPS):**

1. **No explicit `blocklist=false` on queue delete.** [base.py](ott/managers/base.py) uses `self.client.delete("queue", params={..., "removeFromClient": True})` — `blocklist` defaults to `false` on both Radarr/Sonarr, but the Plan.md §6.1 spec called for explicit `blocklist=False` as a regression guard. Low priority but recommended.
2. **`DELETE /queue` targets the movie/series, not individual queue entries.** Current code does `DELETE /queue?movieId=X&removeFromClient=true`. Radarr/Sonarr queue endpoints expect a queue record id (`/queue/{id}`). Verify this actually works against a real Radarr/Sonarr instance — may be silently ineffective.
3. **`_fetch_ratings()` IMDb path is a placeholder** — returns `None` always. Caption shows "N/A" but leaves confusion about whether ratings failed or IMDb isn't supported.
4. **Race condition in manual-mode tag update.** After sending notification, code does `client.get()` → merge tags → `client.put()`. If user clicks Approve between get and put, the monitored=true from the callback gets overwritten by monitored=false (from the original `current_item` snapshot that had it set false earlier). Needs tighter sequencing or CAS pattern.
5. **Grab-event handling only cancels queue when `processed_tag` already set.** If Grab fires for a manual-mode item that hasn't been through the add path yet (webhook race), the full flow runs and sends a duplicate notification. Low probability but possible.
6. **`admin_chat_id` hardcoded empty** in anime_config_dict ([main.py](main.py) reload path). TODO: thread it through from telegram config.
7. **No Pydantic validation** on the new `approve:*` callback payload (same issue as §2.6).

These gaps should be addressed as part of Phase 6 (WIP-finalization) — see below.

---

## Phase 6 — Finalize manual-approval mode (WIP polish)

Completes the WIP work. Renamed from "Rating-Gated Download" to match what's actually built.

### 6.1 Verify and harden queue-deletion targeting (WIP gap #2)
- **Files:** [ott/managers/base.py](ott/managers/base.py), [ott/clients/arr_client.py](ott/clients/arr_client.py)
- **Problem:** WIP calls `DELETE /queue?movieId=X&removeFromClient=true` but Radarr/Sonarr queue endpoints expect a queue-record id (`DELETE /queue/{queueId}`). The current query-parameter form may be silently ineffective.
- **Fix:**
  1. Add `get_queue_items_for(media_id)` to `arr_client.py`: `GET /api/v3/queue` + filter client-side by `movieId` (Radarr) / `seriesId` (Sonarr). Return list of queue-record ids. For Sonarr, include **all** matching queued episodes of the series.
  2. Add `delete_queue_item(queue_id, remove_from_client=True, blocklist=False)` to `arr_client.py` — explicit `blocklist=false` as a regression guard.
  3. Replace the current `self.client.delete("queue", params={...})` calls with a loop that fetches matching queue ids and deletes each one.
  4. Log `"Cancelled N queue items for <title>"`.
  5. Keep queue deletion **non-fatal**: errors logged, tag/unmonitor work unaffected.
- **Tests:**
  - Mock Radarr queue with one movie item → assert single DELETE `/queue/{id}` with `blocklist=false`.
  - Mock Sonarr queue with 3 episodes of series X + 1 from series Y → exactly 3 DELETEs, series Y untouched.
  - Queue list endpoint returns 500 → tag/unmonitor still succeed, error logged.
  - Regression guard: assert the DELETE URL query string contains `blocklist=false` literally.

### 6.2 Tighten manual-mode state transitions (WIP gaps #4, #5)
- **Files:** [ott/managers/base.py](ott/managers/base.py)
- **Problem #4 (race):** After sending the Telegram notification, the manual-mode path does `client.get()` → merge tags → `client.put()`. If the user clicks "Approve Download" between the `get` and the `put`, the `monitored=False` from the put overwrites the `monitored=True` that the callback just set.
- **Problem #5 (Grab race):** If Sonarr/Radarr fires `Grab` before the `*Added` webhook reaches ott-sync (rare but possible), the manual-mode full flow runs and sends a duplicate notification.
- **Fix for #4:**
  - Use a PATCH-style update that only sets the tag field, not the whole item body. Radarr `PUT /movie/{id}` accepts partial updates; verify same for Sonarr `PUT /series/{id}`.
  - If PATCH is not available, refetch immediately before the PUT and copy forward the `monitored` field from the refetch (preserves any concurrent callback change).
  - Document the chosen approach with an inline comment pointing to this plan item.
- **Fix for #5:**
  - In the Grab short-circuit path (`if event == "Grab" and self.processed_tag in current_tags`), also handle the absence case: if Grab fires and item is NOT yet processed, still cancel the queue entry (so the download stops) but continue into the full manual-mode flow — the notification will then correctly mark it processed.
  - Add a test with Grab-before-Added ordering to lock this behavior in.
- **Tests:**
  - Simulate user clicking Approve between get and put → final state has `monitored=true` AND `ott-processed` tag.
  - Simulate Grab event on a not-yet-processed manual-mode item → queue cancelled, notification sent, `ott-processed` set. No duplicate notification.

### 6.3 Remove IMDb rating placeholder (WIP gap #3)
- **Files:** [ott/managers/base.py](ott/managers/base.py), [ott/models.py](ott/models.py)
- **Problem:** `_fetch_ratings()` always returns `None` for `imdb`. The caption displays "N/A" which suggests a fetch failure rather than "not supported".
- **Fix (option A):** Drop the `imdb` key entirely from `_fetch_ratings()` return + from `build_telegram_caption()` args. Caption shows only TMDB + AniList. Cleanest.
- **Fix (option B):** Wire an IMDb source. TMDB's `/movie/{id}/external_ids` returns the IMDb id, but rating data requires OMDb API (new dependency, new API key). Defer to Phase 7.
- **Recommend option A** for now. Add Phase 7 item for IMDb source if user wants it.
- **Tests:** update `test_models.py` caption tests to not expect `imdb_rating` field.

### 6.4 Thread `admin_chat_id` through from config (WIP gap #6)
- **File:** [main.py](main.py) reload path (anime_config_dict construction), [ott/config.py](ott/config.py).
- **Problem:** `main.py` hardcodes `"admin_chat_id": ""` in `anime_config_dict`. The telegram config actually carries this value.
- **Fix:** Read `config.telegram["admin_chat_id"]` (after §1.2 is resolved — dict access form) and pass through. Ensure empty-string fallback is preserved.
- **Tests:** unit test the reload path builds `anime_config_dict` with correct admin_chat_id.

### 6.5 Pydantic validation on `approve:*` callback (WIP gap #7, overlaps §2.6)
- **File:** [ott/api/telegram.py](ott/api/telegram.py)
- **Problem:** `approve:*` callback parsing uses raw `.split(":")`. Same issue as existing `override:*` callback.
- **Fix:** Combine with §2.6 — define a Pydantic model `TelegramCallbackAction(action, item_type, item_id)` and parse all callback-data strings through it with proper error responses.

### 6.6 Docs for manual mode
- **Files:** [README.md](README.md), [CHANGELOG.md](CHANGELOG.md), [CLAUDE.md](CLAUDE.md), [API_DOCUMENTATION.md](API_DOCUMENTATION.md).
- **Add:**
  - "Manual approval mode" subsection explaining the `auto_download: false` toggle, what gets gated, and how Telegram approvals work.
  - Update CLAUDE.md decision-flow section to reflect binary switch (currently still describes the 3-tier threshold).
  - Bullet in CHANGELOG describing the manual-mode feature.

### 6.7 Verify `ott-override` respects all WIP paths
- **Context:** `ott-override` is already checked in the WIP manual-mode path at [base.py ~line 685](ott/managers/base.py#L685) (`if self.override_tag in current_tags: return`). Confirm the automatic-mode path does the same.
- **Files:** [ott/managers/base.py](ott/managers/base.py)
- **Fix:** Audit both manual and automatic branches of `added_hook()` to ensure `ott-override` short-circuits **before** any OTT lookup, tag mutation, queue cancellation, or unmonitor. Add a unit test that proves an `ott-override`-tagged item passes through untouched in both modes.
- **Tests:**
  - Manual mode + `ott-override` → no unmonitor, no queue delete, no notification.
  - Automatic mode + `ott-override` + item on OTT → no unmonitor, no queue delete, no notification.

---

## Phase 7 — Future Enhancements (deferred; build on Phase 6 foundation)

Originally planned as Phase 6; deferred because the binary-switch manual mode already ships the core value. Each item below is optional — layer on top of the binary switch when the need arises.

### 7.1 Three-tier rating-threshold gate
- **Goal:** Reduce Telegram noise by auto-approving high-rated items and silently-skipping low-rated ones. Replaces "ask for everything" with "ask only for the uncertain band."
- **Behavior matrix** (when `auto_download=true` AND threshold gate enabled):
  | TMDB rating | Trending? | Action |
  |---|---|---|
  | ≥ 80% (≥ 50 votes) | — | Auto-download, no prompt |
  | 70–79% (≥ 50 votes) | — | Telegram approval |
  | < 70% (≥ 50 votes) | Yes | Telegram approval |
  | < 70% (≥ 50 votes) | No | Skip silently (`ott-low-rating`) |
  | No rating / < 50 votes | — | Defer 7 days, re-check on weekly cron (`ott-pending-rating`) |
- **Key components:**
  - New `ott/managers/decision.py` — pure `decide()` function (no I/O), fully unit-testable.
  - TMDB `get_rating()` + `is_trending()` methods with cache TTLs (7d/6h).
  - Config: `rating_gate.{enabled, auto_download_threshold_pct, approval_threshold_pct, min_vote_count, trending_window, defer_days_on_no_rating, max_defer_attempts}`.
  - DB tables: `pending_evaluation` (deferred items) + `pending_approval` (awaiting user tap).
  - Weekly sweeper re-evaluates `pending_evaluation` rows.
  - Two-stage Sonarr flow: `SeriesAdd` uses series rating; per-episode `Grab` webhook uses season rating. Requires wiring Sonarr's Grab webhook; degrades gracefully if not wired.
- **Config example:**
  ```jsonc
  "rating_gate": {
    "enabled": false,
    "auto_download_threshold_pct": 80,
    "approval_threshold_pct": 70,
    "min_vote_count": 50,
    "trending_window": "week",
    "defer_days_on_no_rating": 7,
    "max_defer_attempts": 0
  }
  ```
- **Dependencies:** needs Phase 6 (stable WIP), Phase 1 §1.2 (telegram dict access), Phase 2 §2.6 (payload validation), Phase 3 §3.2 (reload lock).

### 7.2 Auto-apply `ott-override` via inverse import-list tag detection
- **Goal:** Automatically exempt manually-added items (not sourced from an import list) from all OTT/rating logic.
- **Mechanism:** Radarr/Sonarr import lists can apply identifying tags (e.g. `list-trakt`). If a newly-added item carries none of those tags, auto-apply `ott-override`.
- **Config:**
  ```jsonc
  "manual_add_detection": {
    "enabled": false,
    "import_list_tags": ["list-trakt", "list-imdb", "list-plex-watchlist"],
    "auto_apply_override_tag": true,
    "tag_recheck_delay_ms": 1500
  }
  ```
- **Caveat:** works only if each import list is configured in *arr to apply an identifying tag. Webhook-arrival races handled via short re-fetch delay.

### 7.3 IMDb rating source (OMDb integration)
- **Goal:** Show IMDb score alongside TMDB in Telegram captions (currently stubbed as N/A).
- **Option:** Wire OMDb API (new API key required) → fetch `imdbRating` by `imdbId` (already extracted by TMDB `get_movie_ratings()`).
- Low priority — resolve §6.3 (remove placeholder) first; add IMDb back only if user requests.

### 7.4 Per-season rating gate via Sonarr Grab webhook
- Applies only if 7.1 is implemented. See 7.1 for the two-stage flow details.

### 7.5 Rating cache surfacing in `/metrics`
- Expose cache hit-rate (currently tracked in `_cache_hits`/`_cache_misses` but unused) and ratings fetched per source.
- Addresses §5.1 (unverified performance claims in CHANGELOG).

---

## Execution Strategy

Recommended approach: tackle **one phase at a time**, with commits per numbered item. After each phase:

1. Run the existing test suite.
2. Manual smoke test: start service, register a fake webhook, trigger cron.
3. Verify `/health` returns healthy.

Suggested branching:
- `feat/manual-mode-wip` — commit staged WIP (recovers §1.4 + §6.1 queue-cancel + manual approval flow)
- `fix/phase-1-critical` — remaining Phase 1 items (1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8)
- `fix/phase-2-reliability` — items 2.1-2.11
- `fix/phase-6-wip-polish` — items 6.1-6.7 (queue-delete targeting, race fixes, docs)
- `fix/phase-3-hardening` — items 3.1-3.11
- `test/phase-4-coverage` — missing test files
- `docs/phase-5-drift` — doc updates
- `feat/phase-7-*` — optional future enhancements

**Dependency order:**
1. Commit WIP first (unblocks everything else).
2. Phase 1 critical fixes (especially §1.2 which Phase 6.4 depends on).
3. Phase 6 WIP polish (finishes the manual-mode work).
4. Phase 2 reliability.
5. Phase 3 hardening.
6. Phase 4 test coverage (can run in parallel with Phases 2-3).
7. Phase 5 docs (at the end).
8. Phase 7 enhancements (optional, ship if/when wanted).

Each phase should ship independently.

---

## Open Questions for Review

Before starting work, confirm:

1. **Multi-chat Telegram routing** (§1.6) — do we want `admin_chat_id` to be a genuinely separate recipient, or is collapsing to a single chat acceptable?
2. **Webhook signature verification** (§2.4) — is this in scope, or is the deployment network-isolated enough to defer?
3. **Alembic migrations** (§3.9) — acceptable to force a DB recreation for existing users, or must we ship a backward-compatible migration?
4. **Hardcoded wakeup target** (§3.11) — keep the feature behind config, or remove entirely?

### Resolved (recorded for traceability)

| # | Question | Decision |
|---|---|---|
| 5 | Rating source | TMDB only for v1 |
| 6 | Rating threshold | 80% auto-download; 70–79% → Telegram approval |
| 6a | Trending items | Send Telegram approval regardless of rating |
| 7 | Missing-rating default | Defer 7 days, re-check weekly |
| 8 | Approval timeout | None — wait forever |
| 8a | Defer attempts cap | Configurable, default 0 (unlimited) |
| 9 | Queue deletion scope | Remove from download client + ensure unmonitored; **do NOT blocklist** |
| 10 | Series rating granularity | Per-season (`GET /3/tv/{id}/season/{n}`) |
| 10a | Sonarr queue cleanup | Nuke all queued episodes for the matched series |
