# Unmonitor on Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When *arr finishes importing a movie/season/series, unmonitor it so *arr stops searching/upgrading — but only once the obtained resolution meets a threshold (default 720p); sub-threshold imports stay monitored so upgrades can still land.

**Architecture:** A `Download` (import-complete) webhook event is dispatched in `OTTBaseManager.added_hook` — after the `ott-override` guard, before manual-add detection — to a new polymorphic `_handle_download_complete()`. The base (Radarr) implementation unmonitors the movie; `SonarrManager` overrides it for an episode→season→series roll-up. A single `unmonitor_on_download` config block (default enabled, `min_resolution: 720`) gates the feature, wired through `main.py` exactly like the rating-gate config.

**Tech Stack:** Python 3.10, FastAPI (webhook transport, untouched here), `unittest.mock`, pytest (`pytest tests/`). Reuses the existing `ArrClient` PUT/GET interface and the Sonarr `episode/monitor` endpoint already used by `dead_media`.

**Reference spec:** [docs/superpowers/specs/2026-05-31-unmonitor-on-download-design.md](../specs/2026-05-31-unmonitor-on-download-design.md)

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `ott/config.py` | Parse `unmonitor_on_download` → `unmonitor_on_download_enabled`, `unmonitor_on_download_min_resolution` | Modify |
| `ott/managers/base.py` | `__init__` defaults; `added_hook` dispatch; `_file_resolution`; base (movie) `_handle_download_complete` | Modify |
| `ott/managers/sonarr.py` | `_handle_download_complete` override + completeness helpers | Modify |
| `main.py` | Push config values onto both managers at both construction sites | Modify |
| `config.json.example` | Document the new config block | Modify |
| `CLAUDE.md` | Document behavior, flag, and "On Import" webhook prerequisite | Modify |
| `tests/test_config.py` | Config parsing tests | Modify |
| `tests/test_managers.py` | Dispatch, movie, and Sonarr roll-up tests | Modify |

---

## Task 0: Pre-flight — isolate pre-existing WIP

The working tree on branch `2.0.0` has **uncommitted changes in files this feature also touches** (`ott/managers/base.py`, `ott/config.py`, `main.py`) plus untracked files (`ott/managers/dead_media.py`, `ott/managers/torrent_housekeeping.py`, `ott/clients/qbittorrent.py`, `ott/db/repositories/dead_media.py`, `coverage.xml`). If you `git add` those files for a feature commit you will also commit unrelated WIP.

- [ ] **Step 1: Inspect the current state**

Run: `git -C /ssd/Workspace/Projects/ott-sync status --short`
Expected: `M` entries for `config.json.example main.py ott/api/telegram.py ott/cli/commands.py ott/clients/telegram.py ott/config.py ott/db/schema.py ott/managers/base.py ott/models.py` and `??` untracked dead-media/torrent files.

- [ ] **Step 2: Decide how to isolate it (ask the human if unsure)**

Pick ONE, then proceed:
- **Commit the WIP** (preferred if it's a coherent in-progress feature): `git add -A && git commit -m "WIP: dead-media / torrent-housekeeping"` — then this plan's per-task commits are clean.
- **Stash it:** `git stash push -u -m "pre unmonitor-on-download WIP"` — restore later with `git stash pop`. Note: this also stashes the spec/plan docs if uncommitted; commit the docs first (Task 9) or exclude them.

Do not start Task 1 until the tree is clean of unrelated changes for the files in the table above, so each task commits only its own work.

---

## Task 1: Config parsing

**Files:**
- Modify: `ott/config.py` (after the `manual_add_detection` block, ~line 202)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` (uses the existing `sample_config_dict` and `config` fixtures from `tests/conftest.py`):

```python
from ott.config import Config


def test_unmonitor_on_download_defaults(config):
    """Defaults: enabled and 720p threshold when the block is absent."""
    assert config.unmonitor_on_download_enabled is True
    assert config.unmonitor_on_download_min_resolution == 720


def test_unmonitor_on_download_overrides(sample_config_dict):
    """Explicit block overrides both fields."""
    cfg = Config(dict(sample_config_dict, unmonitor_on_download={
        "enabled": False, "min_resolution": 1080,
    }))
    assert cfg.unmonitor_on_download_enabled is False
    assert cfg.unmonitor_on_download_min_resolution == 1080
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_config.py::test_unmonitor_on_download_defaults tests/test_config.py::test_unmonitor_on_download_overrides`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'unmonitor_on_download_enabled'`.

- [ ] **Step 3: Implement config parsing**

In `ott/config.py`, immediately after the `manual_add_detection` block (the line `self.manual_add_tag_recheck_delay_ms: int = mad_cfg.get(... )` and its closing `)`), add:

```python
        # Unmonitor-on-download: when an import (*arr "Download" event) completes,
        # stop monitoring the movie/season/series so *arr no longer searches or
        # upgrades it — but only once the obtained resolution meets
        # min_resolution. Sub-threshold imports stay monitored for upgrade.
        uod_cfg = config_dict.get("unmonitor_on_download", {}) or {}
        self.unmonitor_on_download_enabled: bool = uod_cfg.get("enabled", True)
        self.unmonitor_on_download_min_resolution: int = uod_cfg.get(
            "min_resolution", 720,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_config.py::test_unmonitor_on_download_defaults tests/test_config.py::test_unmonitor_on_download_overrides`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add ott/config.py tests/test_config.py
git commit -m "feat(config): add unmonitor_on_download settings (default on, 720p)"
```

---

## Task 2: Manager defaults + `_file_resolution` helper

**Files:**
- Modify: `ott/managers/base.py` (`__init__`, after the rating-gate defaults ~line 87; new method near `_unmonitor_item`)
- Test: `tests/test_managers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_managers.py` (module level; `RadarrManager` is already imported at the top of the file):

```python
def test_unmonitor_defaults(arr_mock, mock_justwatch, mock_telegram):
    """Managers default to enabled + 720p before main.py overrides them."""
    m = RadarrManager(
        arr_client=arr_mock, justwatch_client=mock_justwatch,
        telegram=mock_telegram, ott_providers={"Netflix"},
    )
    assert m.unmonitor_on_download_enabled is True
    assert m.unmonitor_on_download_min_resolution == 720


def test_file_resolution(arr_mock, mock_justwatch, mock_telegram):
    """_file_resolution reads quality.quality.resolution, 0 when missing."""
    m = RadarrManager(
        arr_client=arr_mock, justwatch_client=mock_justwatch,
        telegram=mock_telegram, ott_providers={"Netflix"},
    )
    assert m._file_resolution({"quality": {"quality": {"resolution": 1080}}}) == 1080
    assert m._file_resolution({"quality": {"quality": {"resolution": 0}}}) == 0
    assert m._file_resolution({"quality": {"quality": {}}}) == 0
    assert m._file_resolution({}) == 0
    assert m._file_resolution(None) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py::test_unmonitor_defaults tests/test_managers.py::test_file_resolution`
Expected: FAIL with `AttributeError` for `unmonitor_on_download_enabled` / `_file_resolution`.

- [ ] **Step 3: Add the defaults**

In `ott/managers/base.py` `__init__`, right after the rating-gate default block (the line `self.rating_gate_max_defer_attempts: int = 0`), add:

```python
        # Unmonitor-on-download (defaults; main.py overrides from config).
        # After an import completes, stop monitoring the item when the obtained
        # resolution meets the threshold so *arr stops searching/upgrading.
        self.unmonitor_on_download_enabled: bool = True
        self.unmonitor_on_download_min_resolution: int = 720
```

- [ ] **Step 4: Add the `_file_resolution` helper**

In `ott/managers/base.py`, add this method directly above `_unmonitor_item` (~line 1394):

```python
    def _file_resolution(self, file_obj: dict | None) -> int:
        """Extract quality.quality.resolution (int) from an *arr file object.

        Returns 0 when the object or any nesting level is missing/unknown,
        so callers treat unknown quality as below any threshold.
        """
        if not file_obj:
            return 0
        try:
            return int(
                (file_obj.get("quality") or {})
                .get("quality", {})
                .get("resolution") or 0
            )
        except (AttributeError, TypeError, ValueError):
            return 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py::test_unmonitor_defaults tests/test_managers.py::test_file_resolution`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add ott/managers/base.py tests/test_managers.py
git commit -m "feat(managers): add unmonitor-on-download defaults + _file_resolution"
```

---

## Task 3: `added_hook` dispatch + movie `_handle_download_complete`

**Files:**
- Modify: `ott/managers/base.py` (`added_hook` ~lines 835-845; new method)
- Test: `tests/test_managers.py`

- [ ] **Step 1: Write the failing dispatch tests**

Add to `tests/test_managers.py` (this introduces the `Mock` import used by later tasks too):

```python
from unittest.mock import Mock


def _movie_download_payload(movie_id=10, tags=None, resolution=1080, event="Download"):
    return {
        "eventType": event,
        "movie": {"id": movie_id, "title": "M", "year": 2020, "tags": tags or []},
        "movieFile": {"quality": {"quality": {"resolution": resolution}}},
    }


def _radarr(client, mock_justwatch, mock_telegram, **kwargs):
    return RadarrManager(
        arr_client=client, justwatch_client=mock_justwatch,
        telegram=mock_telegram, ott_providers={"Netflix"}, **kwargs,
    )


def test_download_event_routes_to_handler(arr_mock, mock_justwatch, mock_telegram):
    m = _radarr(arr_mock, mock_justwatch, mock_telegram)
    m._handle_download_complete = Mock()
    m._process_webhook_locked = Mock()
    m.added_hook(_movie_download_payload())
    m._handle_download_complete.assert_called_once()
    m._process_webhook_locked.assert_not_called()


def test_download_event_disabled_routes_to_normal_flow(arr_mock, mock_justwatch, mock_telegram):
    m = _radarr(arr_mock, mock_justwatch, mock_telegram)
    m.unmonitor_on_download_enabled = False
    m._handle_download_complete = Mock()
    m._process_webhook_locked = Mock()
    m.added_hook(_movie_download_payload())
    m._handle_download_complete.assert_not_called()
    m._process_webhook_locked.assert_called_once()


def test_download_event_override_skipped(arr_mock, mock_justwatch, mock_telegram):
    m = _radarr(arr_mock, mock_justwatch, mock_telegram)
    m._handle_download_complete = Mock()
    m._process_webhook_locked = Mock()
    # tag id 3 == ott-override in sample_tag_response
    m.added_hook(_movie_download_payload(tags=[3]))
    m._handle_download_complete.assert_not_called()
    m._process_webhook_locked.assert_not_called()


def test_download_event_manual_add_still_unmonitored(arr_mock, mock_justwatch, mock_telegram):
    # manual-add detection must NOT skip unmonitor-on-download: the dispatch is
    # placed before _handle_manual_add, so it is bypassed for Download events.
    m = _radarr(
        arr_mock, mock_justwatch, mock_telegram,
        manual_add_detection_enabled=True, import_list_tags=["list-trakt"],
    )
    m._handle_download_complete = Mock()
    m._handle_manual_add = Mock()
    m._process_webhook_locked = Mock()
    m.added_hook(_movie_download_payload())  # tags=[] → would be a "manual add"
    m._handle_download_complete.assert_called_once()
    m._handle_manual_add.assert_not_called()


def test_grab_event_routes_to_normal_flow(arr_mock, mock_justwatch, mock_telegram):
    m = _radarr(arr_mock, mock_justwatch, mock_telegram)
    m._handle_download_complete = Mock()
    m._process_webhook_locked = Mock()
    m.added_hook(_movie_download_payload(event="Grab"))
    m._handle_download_complete.assert_not_called()
    m._process_webhook_locked.assert_called_once()
```

- [ ] **Step 2: Write the failing movie-behavior tests**

These call `_handle_download_complete` directly (so they fail cleanly before the method exists, instead of accidentally passing via the manual-mode fallthrough in `_process_webhook_locked`):

```python
def _puts_to(client, endpoint):
    return [c for c in client.put.call_args_list if c.args and c.args[0] == endpoint]


def test_movie_unmonitored_when_quality_meets_threshold(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    movie = {"id": 10, "title": "M", "year": 2020, "monitored": True, "tags": []}
    client = _make_arr_mock(tag_responses=sample_tag_response, item_by_id={10: movie})
    m = _radarr(client, mock_justwatch, mock_telegram)
    payload = _movie_download_payload(resolution=1080)
    m._handle_download_complete(payload, payload["movie"], 10, "M")
    puts = _puts_to(client, "movie/10")
    assert puts, "expected a PUT to movie/10"
    assert puts[-1].kwargs["json"]["monitored"] is False


def test_movie_kept_monitored_below_threshold(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    movie = {"id": 10, "title": "M", "year": 2020, "monitored": True, "tags": []}
    client = _make_arr_mock(tag_responses=sample_tag_response, item_by_id={10: movie})
    m = _radarr(client, mock_justwatch, mock_telegram)
    payload = _movie_download_payload(resolution=480)
    m._handle_download_complete(payload, payload["movie"], 10, "M")
    assert not _puts_to(client, "movie/10")


def test_movie_already_unmonitored_no_put(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    movie = {"id": 10, "title": "M", "year": 2020, "monitored": False, "tags": []}
    client = _make_arr_mock(tag_responses=sample_tag_response, item_by_id={10: movie})
    m = _radarr(client, mock_justwatch, mock_telegram)
    payload = _movie_download_payload(resolution=1080)
    m._handle_download_complete(payload, payload["movie"], 10, "M")
    assert not _puts_to(client, "movie/10")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py -k "download_event or grab_event or movie_unmonitored or movie_kept or movie_already"`
Expected: the new-behavior tests FAIL — `test_download_event_routes_to_handler` and `test_download_event_manual_add_still_unmonitored` fail (no dispatch yet, so the handler isn't called), and the three movie-behavior tests fail with `AttributeError: ... has no attribute '_handle_download_complete'`. (`test_download_event_disabled_routes_to_normal_flow`, `test_download_event_override_skipped`, and `test_grab_event_routes_to_normal_flow` assert current behavior and may already pass — that's fine.)

- [ ] **Step 4: Add the dispatch in `added_hook`**

In `ott/managers/base.py`, inside `added_hook`, replace this block:

```python
        # 🔒 Absolute override guard (check before acquiring lock)
        if self.override_tag in tags:
            logger.info(
                f"[DECISION] ott-override present → skip ALL OTT enforcement "
                f"for {self.item_type()} id={item_id}"
            )
            return

        # 🏷️ Manual-add detection (inverse tag match)
        if self._handle_manual_add(item_id, tags):
            return
```

with:

```python
        # 🔒 Absolute override guard (check before acquiring lock)
        if self.override_tag in tags:
            logger.info(
                f"[DECISION] ott-override present → skip ALL OTT enforcement "
                f"for {self.item_type()} id={item_id}"
            )
            return

        # ⬇️ Unmonitor-on-download: a completed import means we (maybe) stop
        # monitoring. Placed before manual-add detection so manual adds are also
        # unmonitored; override items already returned above and stay untouched.
        if self.unmonitor_on_download_enabled and event == "Download":
            with self._get_item_lock(item_id):
                logger.debug(f"[LOCK] Acquired lock for download-complete id={item_id}")
                self._handle_download_complete(payload, item, item_id, title)
            return

        # 🏷️ Manual-add detection (inverse tag match)
        if self._handle_manual_add(item_id, tags):
            return
```

- [ ] **Step 5: Add the base (movie) `_handle_download_complete`**

In `ott/managers/base.py`, add this method directly above `_file_resolution` (added in Task 2):

```python
    def _handle_download_complete(
        self, payload: dict, item: dict, item_id: int, title: str,
    ) -> None:
        """Unmonitor an item after a completed import, if quality is good enough.

        Base (movie) behavior: unmonitor the movie when the imported file's
        resolution meets unmonitor_on_download_min_resolution. SonarrManager
        overrides this for the episode→season→series roll-up. Runs under the
        per-item lock (held by added_hook). Idempotent: no PUT when the item is
        already unmonitored.
        """
        threshold = self.unmonitor_on_download_min_resolution
        resolution = self._file_resolution(payload.get("movieFile"))
        if resolution < threshold:
            logger.info(
                f"[UNMONITOR] {self.item_type()} id={item_id} '{title}' imported "
                f"at {resolution}p < {threshold}p — keeping monitored for upgrade"
            )
            return

        res = self.client.get(f"{self.item_type()}/{item_id}")
        if not res:
            logger.error(
                f"[UNMONITOR] Failed to fetch {self.item_type()} id={item_id}"
            )
            return
        data = res.json()
        if not data.get("monitored", False):
            logger.debug(
                f"[UNMONITOR] {self.item_type()} id={item_id} already unmonitored"
            )
            return
        data["monitored"] = False
        if self.client.put(f"{self.item_type()}/{item_id}", json=data):
            logger.info(
                f"[UNMONITOR] Unmonitored {self.item_type()} id={item_id} "
                f"'{title}' ({resolution}p)"
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py -k "download_event or grab_event or movie_unmonitored or movie_kept or movie_already"`
Expected: PASS (8 passed).

- [ ] **Step 7: Commit**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add ott/managers/base.py tests/test_managers.py
git commit -m "feat(managers): unmonitor movie on import when quality >= threshold"
```

---

## Task 4: Sonarr completeness helpers

**Files:**
- Modify: `ott/managers/sonarr.py` (new helper methods on `SonarrManager`)
- Test: `tests/test_managers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_managers.py` (`SonarrManager` is already imported at the top of the file):

```python
def _ep(ep_id, season, has_file, resolution, monitored=True):
    """Build an episode dict as returned by GET /episode?includeEpisodeFile=true."""
    return {
        "id": ep_id,
        "seasonNumber": season,
        "hasFile": has_file,
        "monitored": monitored,
        "episodeFile": (
            {"quality": {"quality": {"resolution": resolution}}} if has_file else None
        ),
    }


def _sonarr(client, mock_justwatch, mock_telegram):
    return SonarrManager(
        arr_client=client, justwatch_client=mock_justwatch,
        telegram=mock_telegram, ott_providers={"Netflix"},
    )


def test_episode_settled(arr_mock, mock_justwatch, mock_telegram):
    m = _sonarr(arr_mock, mock_justwatch, mock_telegram)
    assert m._episode_settled(_ep(1, 1, True, 1080), 720) is True
    assert m._episode_settled(_ep(2, 1, True, 480), 720) is False
    assert m._episode_settled(_ep(3, 1, False, 0), 720) is False


def test_season_complete(arr_mock, mock_justwatch, mock_telegram):
    m = _sonarr(arr_mock, mock_justwatch, mock_telegram)
    full = [_ep(1, 1, True, 1080), _ep(2, 1, True, 720), _ep(3, 1, True, 1080)]
    assert m._season_complete(full, 1, 720) is True
    mixed = [_ep(1, 1, True, 1080), _ep(2, 1, True, 480)]
    assert m._season_complete(mixed, 1, 720) is False
    assert m._season_complete(full, 2, 720) is False  # no episodes in season 2


def test_series_fully_downloaded(arr_mock, mock_justwatch, mock_telegram):
    m = _sonarr(arr_mock, mock_justwatch, mock_telegram)
    full = [_ep(1, 1, True, 1080), _ep(2, 1, True, 1080), _ep(3, 2, True, 720)]
    assert m._series_fully_downloaded(full, 720) is True
    gap = [_ep(1, 1, True, 1080), _ep(2, 1, False, 0)]
    assert m._series_fully_downloaded(gap, 720) is False
    # specials (season 0) are ignored
    with_special = [_ep(1, 1, True, 1080), _ep(99, 0, False, 0)]
    assert m._series_fully_downloaded(with_special, 720) is True
    # only specials → no main episodes → False
    only_special = [_ep(99, 0, True, 1080)]
    assert m._series_fully_downloaded(only_special, 720) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py -k "episode_settled or season_complete or series_fully_downloaded"`
Expected: FAIL with `AttributeError: 'SonarrManager' object has no attribute '_episode_settled'`.

- [ ] **Step 3: Implement the helpers**

In `ott/managers/sonarr.py`, add these methods to `SonarrManager` (e.g. just below `item_type`):

```python
    def _episode_settled(self, episode: dict, threshold: int) -> bool:
        """True when an episode has a file at or above the resolution threshold."""
        return bool(episode.get("hasFile")) and (
            self._file_resolution(episode.get("episodeFile")) >= threshold
        )

    def _season_complete(
        self, episodes: list[dict], season_number: int, threshold: int,
    ) -> bool:
        """True when every episode of the given season is settled (>= threshold)."""
        season_eps = [e for e in episodes if e.get("seasonNumber") == season_number]
        return bool(season_eps) and all(
            self._episode_settled(e, threshold) for e in season_eps
        )

    def _series_fully_downloaded(self, episodes: list[dict], threshold: int) -> bool:
        """True when every non-special (seasonNumber > 0) episode is settled.

        Specials (season 0) are excluded so unobtained specials never block a
        series-level unmonitor.
        """
        main_eps = [e for e in episodes if e.get("seasonNumber", 0) > 0]
        return bool(main_eps) and all(
            self._episode_settled(e, threshold) for e in main_eps
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py -k "episode_settled or season_complete or series_fully_downloaded"`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add ott/managers/sonarr.py tests/test_managers.py
git commit -m "feat(sonarr): add episode/season/series completeness helpers"
```

---

## Task 5: Sonarr `_handle_download_complete` roll-up

**Files:**
- Modify: `ott/managers/sonarr.py` (override `_handle_download_complete`)
- Test: `tests/test_managers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_managers.py`:

```python
def _sonarr_client(series, episodes, tags):
    """Sonarr ArrClient mock: GET tag/episode/series, capture PUTs.

    `series` is the single series dict (returned for GET series/<id>).
    `episodes` is the full episode list (returned for GET episode).
    """
    client = Mock()
    client.url = "http://sonarr:8989"

    def _get(endpoint, **kwargs):
        res = Mock()
        res.status_code = 200
        if endpoint == "tag":
            res.json.return_value = tags
            return res
        if endpoint == "episode":
            res.json.return_value = episodes
            return res
        if endpoint.startswith("series/"):
            res.json.return_value = series
            return res
        return None

    client.get = Mock(side_effect=_get)
    client.put = Mock(return_value=Mock(status_code=200))
    client.post = Mock(return_value=Mock(status_code=200))
    return client


def _series_payload(series_id=5, episodes=None, resolution=1080):
    """Webhook Download payload: imports the LAST episode in `episodes`."""
    episodes = episodes or []
    imported = episodes[-1] if episodes else {"id": 0, "seasonNumber": 1}
    return {
        "eventType": "Download",
        "series": {"id": series_id, "title": "Show", "year": 2020, "tags": []},
        "episodes": [{"id": imported["id"], "seasonNumber": imported["seasonNumber"]}],
        "episodeFile": {"quality": {"quality": {"resolution": resolution}}},
    }


def _ep_monitor_calls(client):
    return [c for c in client.put.call_args_list if c.args and c.args[0] == "episode/monitor"]


def test_sonarr_episode_only_incomplete_season(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    # S1: E1 imported 1080; E2,E3 not yet downloaded → season incomplete
    episodes = [_ep(1, 1, True, 1080), _ep(2, 1, False, 0), _ep(3, 1, False, 0)]
    series = {"id": 5, "status": "continuing", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": True}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=[_ep(1, 1, True, 1080)], resolution=1080)
    m._handle_download_complete(payload, payload["series"], 5, "Show")
    ep_calls = _ep_monitor_calls(client)
    assert ep_calls and ep_calls[-1].kwargs["json"]["episodeIds"] == [1]
    assert ep_calls[-1].kwargs["json"]["monitored"] is False
    assert not _puts_to(client, "series/5")  # season not complete → no series PUT


def test_sonarr_season_complete_continuing_series(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    # S1 fully downloaded; series continuing → unmonitor season, NOT series
    episodes = [_ep(1, 1, True, 1080), _ep(2, 1, True, 1080), _ep(3, 1, True, 1080)]
    series = {"id": 5, "status": "continuing", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": True}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=episodes, resolution=1080)  # imports E3
    m._handle_download_complete(payload, payload["series"], 5, "Show")
    ep_calls = _ep_monitor_calls(client)
    assert ep_calls and ep_calls[-1].kwargs["json"]["episodeIds"] == [1, 2, 3]
    series_puts = _puts_to(client, "series/5")
    assert series_puts, "expected a series PUT to flip the season flag"
    body = series_puts[-1].kwargs["json"]
    assert body["seasons"][0]["monitored"] is False  # season 1 unmonitored
    assert body["monitored"] is True  # continuing series stays monitored


def test_sonarr_ended_series_fully_downloaded(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    episodes = [_ep(1, 1, True, 1080), _ep(2, 1, True, 1080), _ep(3, 2, True, 720)]
    series = {"id": 5, "status": "ended", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": True},
                          {"seasonNumber": 2, "monitored": True}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=episodes, resolution=720)  # imports E3 (S2)
    m._handle_download_complete(payload, payload["series"], 5, "Show")
    ep_calls = _ep_monitor_calls(client)
    assert ep_calls and ep_calls[-1].kwargs["json"]["episodeIds"] == [1, 2, 3]
    body = _puts_to(client, "series/5")[-1].kwargs["json"]
    assert body["monitored"] is False
    assert all(s["monitored"] is False for s in body["seasons"])


def test_sonarr_continuing_series_not_unmonitored_at_series_level(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    # All episodes downloaded but series continuing → only the imported season flips
    episodes = [_ep(1, 1, True, 1080), _ep(2, 1, True, 1080), _ep(3, 2, True, 720)]
    series = {"id": 5, "status": "continuing", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": True},
                          {"seasonNumber": 2, "monitored": True}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=episodes, resolution=720)  # imports E3 (S2)
    m._handle_download_complete(payload, payload["series"], 5, "Show")
    body = _puts_to(client, "series/5")[-1].kwargs["json"]
    assert body["monitored"] is True  # series NOT unmonitored
    s1 = next(s for s in body["seasons"] if s["seasonNumber"] == 1)
    s2 = next(s for s in body["seasons"] if s["seasonNumber"] == 2)
    assert s1["monitored"] is True   # untouched (not the imported season)
    assert s2["monitored"] is False  # imported season, complete → unmonitored


def test_sonarr_mixed_quality_season_stays_monitored(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    # S1: E1 1080, E2 480 (downloaded but sub-threshold), E3 1080 imported
    episodes = [_ep(1, 1, True, 1080), _ep(2, 1, True, 480), _ep(3, 1, True, 1080)]
    series = {"id": 5, "status": "continuing", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": True}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=episodes, resolution=1080)  # imports E3
    m._handle_download_complete(payload, payload["series"], 5, "Show")
    ep_calls = _ep_monitor_calls(client)
    assert ep_calls and ep_calls[-1].kwargs["json"]["episodeIds"] == [3]  # only imported
    assert not _puts_to(client, "series/5")  # 480p E2 keeps season monitored


def test_sonarr_unknown_resolution_import_no_writes(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    # Imported file reports resolution 0 (e.g. SDTV) → keep monitored, no writes
    episodes = [_ep(1, 1, True, 0), _ep(2, 1, False, 0)]
    series = {"id": 5, "status": "continuing", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": True}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=[_ep(1, 1, True, 0)], resolution=0)
    m._handle_download_complete(payload, payload["series"], 5, "Show")
    assert not _ep_monitor_calls(client)
    assert not _puts_to(client, "series/5")


def test_sonarr_idempotent_already_unmonitored(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    # Season already fully unmonitored → re-import issues NO writes
    episodes = [_ep(1, 1, True, 1080, monitored=False),
                _ep(2, 1, True, 1080, monitored=False),
                _ep(3, 1, True, 1080, monitored=False)]
    series = {"id": 5, "status": "continuing", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": False}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=episodes, resolution=1080)  # re-imports E3
    m._handle_download_complete(payload, payload["series"], 5, "Show")
    assert not _ep_monitor_calls(client)
    assert not _puts_to(client, "series/5")


def test_sonarr_override_item_skipped_via_added_hook(
    mock_justwatch, mock_telegram, sample_tag_response,
):
    episodes = [_ep(1, 1, True, 1080)]
    series = {"id": 5, "status": "ended", "monitored": True,
              "seasons": [{"seasonNumber": 1, "monitored": True}]}
    client = _sonarr_client(series, episodes, sample_tag_response)
    m = _sonarr(client, mock_justwatch, mock_telegram)
    payload = _series_payload(episodes=episodes, resolution=1080)
    payload["series"]["tags"] = [3]  # ott-override
    m.added_hook(payload)
    assert not _ep_monitor_calls(client)
    assert not _puts_to(client, "series/5")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py -k "sonarr_episode_only or sonarr_season_complete or sonarr_ended or sonarr_continuing or sonarr_mixed or sonarr_unknown or sonarr_idempotent or sonarr_override"`
Expected: FAIL — `SonarrManager` currently inherits the base (movie) `_handle_download_complete`, which reads `movieFile` (absent) and issues no episode/series PUTs, so every assertion expecting an `episode/monitor` or `series/5` PUT fails. (The `unknown_resolution`, `idempotent`, and `override` tests assert *no* writes and may already pass — that's fine.)

- [ ] **Step 3: Implement the Sonarr override**

In `ott/managers/sonarr.py`, add this method to `SonarrManager` (below the completeness helpers from Task 4):

```python
    def _handle_download_complete(
        self, payload: dict, item: dict, item_id: int, title: str,
    ) -> None:
        """Unmonitor imported episodes, rolling up to season then series.

        - Episode: unmonitor each imported episode whose file is >= threshold.
        - Season: when every episode of an affected season is settled, unmonitor
          that whole season (its episodes + the season's monitored flag).
        - Series: when every non-special episode is settled AND the series has
          ended, unmonitor every non-special episode + season flag + the series.

        Idempotent: only currently-monitored episodes are sent to
        episode/monitor, and the series is PUT only when a season/series flag
        actually changes. At most one episode/monitor PUT and one series PUT.
        """
        threshold = self.unmonitor_on_download_min_resolution
        imported_eps = payload.get("episodes", []) or []
        imported_resolution = self._file_resolution(payload.get("episodeFile"))

        ep_res = self.client.get(
            "episode",
            params={"seriesId": item_id, "includeEpisodeFile": "true"},
        )
        series_res = self.client.get(f"series/{item_id}")
        if not ep_res or not series_res:
            logger.error(f"[UNMONITOR] Failed to fetch state for series id={item_id}")
            return

        episodes = ep_res.json()
        series = series_res.json()
        ep_by_id = {e["id"]: e for e in episodes if e.get("id") is not None}

        # Candidate episode ids; filtered to currently-monitored ones at the end
        # so re-processing an already-unmonitored season is a no-op.
        candidates: set[int] = set()
        if imported_resolution >= threshold:
            candidates |= {e["id"] for e in imported_eps if e.get("id")}
        else:
            logger.info(
                f"[UNMONITOR] series id={item_id} '{title}' imported episode at "
                f"{imported_resolution}p < {threshold}p — keeping monitored for upgrade"
            )

        series_dirty = False
        ended = series.get("status") == "ended"

        if ended and self._series_fully_downloaded(episodes, threshold):
            main_eps = [e for e in episodes if e.get("seasonNumber", 0) > 0]
            candidates |= {e["id"] for e in main_eps if e.get("id")}
            for season in series.get("seasons", []):
                if season.get("seasonNumber", 0) > 0 and season.get("monitored"):
                    season["monitored"] = False
                    series_dirty = True
            if series.get("monitored"):
                series["monitored"] = False
                series_dirty = True
            logger.info(
                f"[UNMONITOR] series id={item_id} '{title}' fully downloaded + "
                f"ended — unmonitoring series"
            )
        else:
            affected = {
                e.get("seasonNumber") for e in imported_eps
                if e.get("seasonNumber") is not None
            }
            for season_number in affected:
                if not self._season_complete(episodes, season_number, threshold):
                    continue
                candidates |= {
                    e["id"] for e in episodes
                    if e.get("seasonNumber") == season_number and e.get("id")
                }
                for season in series.get("seasons", []):
                    if (season.get("seasonNumber") == season_number
                            and season.get("monitored")):
                        season["monitored"] = False
                        series_dirty = True
                logger.info(
                    f"[UNMONITOR] series id={item_id} '{title}' season "
                    f"{season_number} complete — unmonitoring season"
                )

        to_unmonitor = sorted(
            eid for eid in candidates if ep_by_id.get(eid, {}).get("monitored")
        )
        if to_unmonitor:
            self.client.put(
                "episode/monitor",
                json={"episodeIds": to_unmonitor, "monitored": False},
            )
        if series_dirty:
            self.client.put(f"series/{item_id}", json=series)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py -k "sonarr_episode_only or sonarr_season_complete or sonarr_ended or sonarr_continuing or sonarr_mixed or sonarr_unknown or sonarr_idempotent or sonarr_override"`
Expected: PASS (8 passed).

- [ ] **Step 5: Run the whole manager test module**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/test_managers.py`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 6: Commit**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add ott/managers/sonarr.py tests/test_managers.py
git commit -m "feat(sonarr): roll up episode->season->series unmonitor on import"
```

---

## Task 6: Wire config into managers (main.py, both sites)

**Files:**
- Modify: `main.py` (the two `for mgr in (...):` rating-gate loops, ~line 185 and ~line 516)

There is no clean unit test for `main.py`'s wiring (heavy startup). Verify by code inspection + the full suite (Task 8). Add to BOTH rating-gate loops.

- [ ] **Step 1: Add to the hot-reload site (~line 192)**

In `main.py`, find the first rating-gate loop ending with `mgr.rating_gate_max_defer_attempts = config.rating_gate_max_defer_attempts` and append two lines inside that same `for` body, matching the surrounding indentation (12 spaces here):

```python
            mgr.unmonitor_on_download_enabled = config.unmonitor_on_download_enabled
            mgr.unmonitor_on_download_min_resolution = config.unmonitor_on_download_min_resolution
```

- [ ] **Step 2: Add to the startup site (~line 523)**

In `main.py`, find the second rating-gate loop (`for mgr in (_managers['radarr'], _managers['sonarr']):`) ending with `mgr.rating_gate_max_defer_attempts = config.rating_gate_max_defer_attempts` and append, matching that loop's indentation (8 spaces):

```python
        mgr.unmonitor_on_download_enabled = config.unmonitor_on_download_enabled
        mgr.unmonitor_on_download_min_resolution = config.unmonitor_on_download_min_resolution
```

- [ ] **Step 3: Verify both edits landed and Python parses**

Run: `cd /ssd/Workspace/Projects/ott-sync && grep -n "unmonitor_on_download_enabled = config" main.py && python -m py_compile main.py && echo OK`
Expected: two matching lines printed, then `OK`.

- [ ] **Step 4: Commit**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add main.py
git commit -m "feat(main): wire unmonitor_on_download config onto both managers"
```

---

## Task 7: Documentation

**Files:**
- Modify: `config.json.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the config block to `config.json.example`**

In `config.json.example`, replace this:

```json
  "_comment_auto_download": "Download mode: true = automatic (current behavior), false = manual approval required for ALL items",
  "auto_download": false,
```

with:

```json
  "_comment_auto_download": "Download mode: true = automatic (current behavior), false = manual approval required for ALL items",
  "auto_download": false,

  "_comment_unmonitor_on_download": "After *arr finishes importing (the 'On Import' / 'Download' webhook event), unmonitor the movie/season/series so *arr stops searching & upgrading it — but only once the imported resolution is >= min_resolution. Sub-threshold imports stay monitored so a better release can still upgrade them; a later >=min_resolution upgrade then unmonitors. Sonarr rolls up: episode -> season (when fully downloaded) -> series (when fully downloaded AND ended; specials excluded). Items tagged ott-override are never touched. Requires the 'On Import'/'On File Import' event enabled on the Radarr & Sonarr webhook connections.",
  "unmonitor_on_download": {
    "enabled": true,
    "min_resolution": 720
  },
```

- [ ] **Step 2: Document the behavior in `CLAUDE.md`**

In `CLAUDE.md`, in the "Decision flow (webhook handler, summarized)" section, after item 1 (the `ott-override` escape-hatch line), insert a new bullet:

```markdown
1.5. **Import complete** (`Download` event, when `unmonitor_on_download.enabled`): unmonitor the imported item once quality ≥ `min_resolution` (default 720p), skipping `ott-override` items. Sonarr rolls up episode → season (fully downloaded) → series (fully downloaded **and** `status == "ended"`; specials/season 0 excluded). Sub-threshold imports stay monitored so a later upgrade can land. Dispatched before the OTT/manual-add logic, so import events never run a JustWatch lookup.
```

- [ ] **Step 3: Add a gotcha to `CLAUDE.md`**

In `CLAUDE.md`, under the "Gotchas" section, append:

```markdown
- `unmonitor_on_download` only fires if the Radarr/Sonarr webhook connections have the **"On Import" / "On File Import"** event enabled (internally `eventType: "Download"`). Without it, imports never reach ott-sync and nothing is unmonitored.
```

- [ ] **Step 4: Verify the docs and validate the example JSON parses**

Run: `cd /ssd/Workspace/Projects/ott-sync && grep -n "unmonitor_on_download" config.json.example CLAUDE.md && python -c "import json; json.load(open('config.json.example')); print('valid json')"`
Expected: matches in both files, then `valid json`. (If `config.json.example` was already non-strict JSON before this change, that's pre-existing — confirm the only new lines are the block you added and that the surrounding commas are balanced.)

- [ ] **Step 5: Commit**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add config.json.example CLAUDE.md
git commit -m "docs: document unmonitor_on_download config + webhook prerequisite"
```

---

## Task 8: Full suite + plan/spec docs

- [ ] **Step 1: Run the full test suite**

Run: `cd /ssd/Workspace/Projects/ott-sync && python -m pytest tests/`
Expected: PASS (no regressions). If pre-existing unrelated tests were already failing before this work (e.g. `test_config.py::test_config_load_missing_file`, noted in CLAUDE.md gotchas), confirm they are the *same* failures as before, not new ones introduced here.

- [ ] **Step 2: Commit the spec + plan docs (if not already committed)**

```bash
cd /ssd/Workspace/Projects/ott-sync
git add docs/superpowers/specs/2026-05-31-unmonitor-on-download-design.md \
        docs/superpowers/plans/2026-05-31-unmonitor-on-download.md
git commit -m "docs: unmonitor-on-download spec + implementation plan" || echo "nothing to commit"
```

- [ ] **Step 3: Final review**

Run: `cd /ssd/Workspace/Projects/ott-sync && git log --oneline -10`
Expected: the feature commits from Tasks 1–8 in order. Done.

---

## Manual verification (optional, post-merge)

These require a live Radarr/Sonarr and are not part of the automated suite:

1. In Radarr → Settings → Connect → (the ott-sync webhook) → enable **On Import**. Same for Sonarr.
2. Grab a 1080p movie; on import, confirm the movie flips to unmonitored in Radarr.
3. Grab a single 480p movie (or force a low-quality release); confirm it stays monitored.
4. Sonarr: download a full season of an ended series at ≥720p; confirm episodes + season + (if it's the last season) the series unmonitor.
5. Sonarr: download one episode of a continuing show; confirm only that episode unmonitors and the series stays monitored.
