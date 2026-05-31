# Unmonitor on Download — Design

- **Date:** 2026-05-31
- **Status:** Approved (pending implementation plan)
- **Component:** `ott-sync` webhook pipeline (`OTTBaseManager` / `SonarrManager`)

## Goal

When a movie, season, or series finishes downloading (i.e. *arr completes an
**import**), stop monitoring it so *arr no longer searches/upgrades it — **but
only once the obtained quality is good enough**. Below the quality threshold the
item stays monitored so *arr keeps trying to upgrade it; once a release at or
above the threshold lands, we unmonitor.

## Background — current behavior

- *arr fires its import-complete webhook with `eventType: "Download"` (this is
  *arr's confusing name for "imported", not "snatched" — that's `Grab`).
- `OTTBaseManager.added_hook` ([ott/managers/base.py](../../../ott/managers/base.py))
  already accepts events starting with `Download`, but routes them through the
  full OTT decision flow (`_process_webhook_locked`). For a `Download` event that
  path is at best a no-op and at worst counterproductive — in auto mode the
  rating-gate "auto-download" branch can *re-monitor* an item
  (`base.py` ~L1274). There is no dedicated import-complete handling today.
- Reusable mechanisms already exist:
  - `_unmonitor_item(item_id)` — PUT `monitored=false` for the top-level item.
  - Sonarr episode unmonitor via `PUT episode/monitor {episodeIds, monitored:false}`
    (used by `dead_media._do_unmonitor_eps`).
  - Series unmonitor via PUT `series/{id}` with `monitored=false`
    (`dead_media._do_unmonitor_series`).
- A documented invariant: **ott-sync never touches `ott-override` items.**

## Requirements

### Functional
1. On a `Download` (import-complete) event, unmonitor the imported media when the
   imported file's resolution is **≥ `min_resolution`** (default `720`).
2. **Movie (Radarr):** unmonitor the movie iff imported resolution ≥ threshold.
3. **Series (Sonarr) — episode→season→series roll-up:**
   - **Episode:** unmonitor each imported episode whose file is ≥ threshold. A
     sub-threshold import stays monitored (awaiting upgrade).
   - **Season:** unmonitor a season only when *every* episode in that season has a
     file **and** every file is ≥ threshold. One missing or sub-threshold episode
     keeps the season monitored.
   - **Series:** unmonitor the series only when *all* **non-special**
     (`seasonNumber > 0`) episodes are present at ≥ threshold **and**
     `series.status == "ended"`. Specials (season 0) are excluded from this check
     (commonly unmonitored/absent and would otherwise block it forever). A
     `continuing` series is never unmonitored at the series level, so future
     episodes/seasons keep flowing. When the series is unmonitored, only
     non-special season flags + episodes are flipped; season 0 is left untouched.
4. **Override skip:** items carrying `ott-override` are left fully alone (honors
   the existing invariant). This is the *only* skip condition.
5. **Manual adds are unmonitored** on download (they are not skipped). The import
   branch sits before manual-add detection, so manual-add detection is bypassed
   for `Download` events.
6. **Config-gated:** controlled by `unmonitor_on_download.enabled` (default
   `true`). When disabled, `Download` events are ignored entirely (current
   behavior).
7. **Idempotent:** re-processing a `Download` for an already-unmonitored item, or
   a later episode import of an already-unmonitored season, performs no redundant
   writes — only PUT when a flag actually changes.

### Non-functional
- No new *arr tag (unmonitoring is idempotent via the `monitored` flag; cron
  already skips unmonitored items). Reuses existing mechanisms per project
  preference.
- At most **one** `episode/monitor` PUT (union of episode ids) and **one**
  `series` PUT per import event.

## Config

Nested object (mirrors `rating_gate` / `dead_media` / `manual_add_detection`):

```json
"unmonitor_on_download": {
  "enabled": true,
  "min_resolution": 720
}
```

- `ott/config.py`:
  ```python
  uod_cfg = config_dict.get("unmonitor_on_download", {})
  self.unmonitor_on_download_enabled: bool = uod_cfg.get("enabled", True)
  self.unmonitor_on_download_min_resolution: int = uod_cfg.get("min_resolution", 720)
  ```
- `config.json.example`: add the block with a `_comment_unmonitor_on_download`
  explaining the quality gate and the "On Import" webhook prerequisite.
- `main.py`: thread both values into `RadarrManager` / `SonarrManager` at **both**
  construction sites (initial + hot-reload), following the `auto_download` pattern.
- `OTTBaseManager.__init__`: accept
  `unmonitor_on_download_enabled: bool = True` and
  `unmonitor_on_download_min_resolution: int = 720`; store on `self`.

## Design (chosen approach)

**Approach:** dedicated import branch in `added_hook` + polymorphic
`_handle_download_complete()`. (Alternatives considered: branch inside
`_process_webhook_locked` — rejected because Sonarr's override runs anime
detection before `super()`, wasting work on imports; separate API-layer
`import_hook` — rejected as it pushes event semantics into the transport layer
and bypasses the existing per-item lock/guards.)

### Entry point — `added_hook`

Insert the dispatch **after** the `ott-override` guard and **before** manual-add
detection:

```python
if self.override_tag in tags:        # existing guard — leave override items alone
    return
if self.unmonitor_on_download_enabled and event == "Download":
    with self._get_item_lock(item_id):
        self._handle_download_complete(payload, item, item_id, title)
    return
if self._handle_manual_add(item_id, tags):   # now only reached for Add/Grab
    return
```

- Runs under the per-item lock (serializes against callbacks/cron/other webhooks).
- Short-circuits before `_process_webhook_locked`, so no OTT lookup or anime
  detection runs on imports.

### Quality helper — `OTTBaseManager`

```python
def _file_resolution(self, file_obj: dict | None) -> int:
    """Return quality.quality.resolution (int) or 0 if missing/unknown."""
```
Reads `file_obj["quality"]["quality"]["resolution"]` defensively; returns `0` when
absent. `0` (unknown) is treated as below threshold → keep monitored.

### Movie — base `_handle_download_complete`

Base implementation (used by Radarr):
1. Read imported resolution from `payload["movieFile"]`.
2. If `< min_resolution`: log `[UNMONITOR] keep monitored (res<thr)` and return.
3. Else re-fetch the movie; if currently monitored, PUT `monitored=false`; log
   `[UNMONITOR] Unmonitored movie …`. (Skip PUT if already unmonitored.)

### Series — `SonarrManager._handle_download_complete` (override)

1. **Imported episodes:** from `payload["episodes"]` collect episode ids; read
   imported resolution from `payload["episodeFile"]`. Episodes meeting the
   threshold go into `to_unmonitor` (episode-id set).
2. **Fetch state once:** `GET /episode?seriesId={id}&includeEpisodeFile=true`
   (each episode carries `hasFile` + embedded `episodeFile.quality...resolution`)
   and `GET /series/{id}` (for `seasons[]` and `status`).
3. **Per affected season number:** if every episode in that season has a file and
   every file ≥ threshold → add all that season's episode ids to `to_unmonitor`
   and mark `seasons[].monitored=false`.
4. **Series:** if every **non-special** (`seasonNumber > 0`) episode has a file at
   ≥ threshold **and** `series.status == "ended"` → add all non-special episode
   ids, set every non-special season flag false, set `series.monitored=false`.
   Season 0 (specials) is left untouched.
5. **Apply:** one `PUT episode/monitor {episodeIds:list(to_unmonitor), monitored:false}`
   if non-empty; one `PUT series/{id}` only if any season/series flag changed.

Helper `_episode_settled(ep, thr)` = `ep.hasFile and resolution(ep.episodeFile) >= thr`.

## Edge cases & decisions

- **Unknown/missing resolution** (e.g. `SDTV`, resolution `0`/absent): treated as
  below threshold → **keep monitored**. Conservative — never a premature
  unmonitor. Logged for visibility.
- **Sub-threshold then upgrade:** a 480p import stays monitored; *arr later grabs
  a ≥720p upgrade → that `Download` event (with `isUpgrade=true`) re-runs this
  logic, now passes the threshold, and unmonitors. No special upgrade handling
  needed.
- **Ongoing season / unaired episodes:** unaired episodes have no file → season
  not "complete" → left monitored. Safe for ongoing shows.
- **Continuing series:** never unmonitored at the series level (only `ended` +
  fully present at quality). Future seasons keep flowing.
- **Specials (season 0):** excluded from the series-complete check so unobtained
  specials never block series-level unmonitor; left untouched when the series is
  unmonitored. A season-0 import is still handled by the season-level roll-up like
  any other season.
- **Backfilled/legacy episodes:** when a season/series completes, *all* its
  episode ids are unmonitored (not just the just-imported one), so episodes that
  predate this feature are reconciled.
- **`ott-override` + manual-add-detection interaction:** if
  `manual_add_detection` is enabled *with* `auto_apply_override`, manual adds get
  the `ott-override` tag at add-time and would then be skipped by the override
  guard. With manual-add detection off (default), all manual adds are unmonitored.
  Out of scope to distinguish further unless requested.

## Out of scope
- A finer rule that unmonitors override-tagged-manual-adds while still skipping
  explicit "Download anyway" overrides.
- An audit/tracking tag (`ott-downloaded`) — easy to add later if visibility is
  wanted.
- Quality-profile-aware "cutoff" logic — we use a single resolution threshold, not
  *arr's per-profile cutoff.

## Deployment / ops prerequisite
This feature fires on *arr's **"On Import" / "On File Import"** Connect webhook
event (`eventType: "Download"`). Both the Radarr and Sonarr webhooks pointing at
`/radarr` and `/sonarr` must have that event enabled. Document in `CLAUDE.md`.

## Testing
Unit tests (mock `ArrClient`), covering:
1. Movie ≥720p → unmonitored; movie 480p → stays monitored.
2. Sonarr single-episode ≥720p import in an incomplete season → episode
   unmonitored, season/series untouched.
3. Season completion (all eps present ≥720p) → season + its episodes unmonitored,
   series untouched (when continuing).
4. Ended series fully present ≥720p → episodes + all season flags + series
   unmonitored.
5. Continuing series fully present ≥720p → episodes/seasons unmonitored but series
   **not** unmonitored.
6. Mixed-quality season (one 480p episode) → season stays monitored.
7. Unknown resolution → keep monitored.
8. `ott-override` item → fully skipped (no writes).
9. Manual-add item (detection off) → unmonitored.
10. `enabled: false` → `Download` event is a no-op.
11. Idempotency: re-processing an already-unmonitored season → no redundant PUT.

## Files to change
- `ott/config.py` — parse `unmonitor_on_download`.
- `config.json.example` — document the block.
- `main.py` — thread config into both manager construction sites.
- `ott/managers/base.py` — `__init__` params; `added_hook` dispatch;
  `_file_resolution`; base `_handle_download_complete` (movie).
- `ott/managers/sonarr.py` — `_handle_download_complete` override + helpers.
- `CLAUDE.md` — note the new flag, the unmonitor-on-download behavior, and the
  "On Import" webhook prerequisite.
- `tests/` — the matrix above.
