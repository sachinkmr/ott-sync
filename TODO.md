# OTT Hooks & ARR Governance — Detailed Implementation Roadmap

This TODO.md is a **living implementation plan** for evolving the OTT-aware ARR stack safely.
Each phase is designed to be **independent, reversible, and low-risk**.

> Philosophy: *ARR controls quality. OTT hooks control intent. Tags are contracts.*

---

## Phase 0 — Baseline (Current State) ✅

**Status:** Complete and stable

### What exists today

* Radarr + Sonarr + Prowlarr + Bazarr + qBittorrent
* Single quality profile: `HD-720p`
* Recyclarr manages all Custom Formats
* Hardlinks enabled (same filesystem)
* OTT detection using JustWatch (India)
* Execution modes:

  * Webhooks (MovieAdded / SeriesAdded / Grabbed)
  * Scheduled cron sweep

### Tags in use

* `ott_skipped` — item blocked due to OTT availability
* `ott_processed` — item already checked
* `ott_override` — user intent override

### Behavior

* OTT found → unmonitor + delete files
* Non-OTT → allow + mark processed

### Why this phase exists

* Establish correctness
* Eliminate bad downloads early
* Keep library clean while stack matures

---

## Phase 1 — Observation & Baseline Metrics 🧪

**Goal:** Collect real-world signals before changing logic

### Tasks

* [ ] Run cron daily for 1–2 weeks
* [ ] Review logs for:

  * False OTT positives
  * Missing OTT matches
  * Frequent overrides
* [ ] Capture baseline metrics:

  * checked
  * cleaned
  * marked_processed
  * skipped_override
  * errors

### Outputs to note

* Titles frequently overridden
* OTT providers causing noise
* Content types affected (Indian / Western / Anime)

### Exit Criteria

* OTT accuracy acceptable
* Confident that strict mode is not over-aggressive

---

## Phase 2 — OTT Mode Switch (Strict ↔ Penalty) ⚖️

**Goal:** Introduce soft governance without breaking existing behavior

### Config changes

Add to `config.json`:

```json
{
  "ott_mode": "strict" | "penalty"
}
```

Default remains `strict`.

### Code changes

* [ ] Add `ott_mode` check in decision path
* [ ] Extract cleanup logic into reusable method
* [ ] Introduce new tag: `ott_penalty`

### New behavior (Penalty Mode)

* OTT found → apply `ott_penalty` tag
* Do NOT:

  * unmonitor
  * delete files

### Strict Mode (unchanged)

* OTT found → unmonitor + delete

### Safety

* `ott_override` always bypasses both modes
* Existing strict users unaffected

---

## Phase 3 — ARR Scoring Integration 🎯

**Goal:** Let ARR deprioritize OTT content instead of deleting it

### Recyclarr

* [ ] Create Custom Format: `OTT_PENALTY`
* [ ] Assign negative score (recommended: −50)
* [ ] Ensure no minimum score rejection

### Expected Behavior

* OTT content loses against non-OTT releases
* ARR still upgrades if user wants better copy
* No download blocks

### Validation

* [ ] Confirm score appears in release decision UI
* [ ] Verify override removes penalty impact

---

## Phase 4 — Delayed Cleanup (Grace Period) ⏳

**Goal:** Avoid instant deletions while preventing library bloat

### Config changes

```json
{
  "ott_penalty_grace_days": 14
}
```

### Data tracking

* [ ] Store timestamp when `ott_penalty` is applied

  * Option A: tag metadata (if supported later)
  * Option B: lightweight JSON / SQLite store

### Cron logic

* [ ] If item has `ott_penalty`
* [ ] If grace period expired
* [ ] If no `ott_override`
* [ ] Then cleanup:

  * unmonitor
  * delete files
  * tag as `ott_skipped`

### Benefits

* Time buffer for human review
* No accidental removals

---

## Phase 5 — Provider Confidence Weighting 🧠 (Optional)

**Goal:** Smarter OTT decisions based on provider quality

### Provider tiers (example)

* Tier 1: Netflix, Prime Video, Disney+
* Tier 2: Others

### Implementation

* [ ] Map provider → confidence score
* [ ] Apply penalty based on tier

  * Tier 1: −50
  * Tier 2: −20

### Notes

* Only implement if false positives persist
* Avoid overfitting

---

## Phase 6 — Subtitle & Language Governance Alignment 📝

**Goal:** Ensure ARR, Bazarr, and OTT logic never conflict

### Rules (current intent)

* Hindi audio → NO subtitles
* Non-Hindi audio → English subtitles

### Tasks

* [ ] Verify Bazarr language config
* [ ] Audit existing subtitle files
* [ ] Optional cleanup job for unwanted subs

---

## Phase 7 — Metrics Persistence & Observability 📊 (Optional)

**Goal:** Long-term visibility

### Metrics to persist

* OTT checks over time
* Cleanup actions
* Overrides usage
* Penalty → cleanup conversions

### Storage options

* JSONL (simplest)
* SQLite (preferred)

### Optional

* Grafana / Prometheus export

---

## Phase 8 — Policy Review & Maintenance 🔁

**Goal:** Keep automation aligned with real usage

### Periodic tasks

* [ ] Review OTT provider list
* [ ] Adjust penalty values
* [ ] Remove stale tags
* [ ] Re-evaluate grace period

---

## Implementation Order (Recommended)

1. Phase 1 — Observe
2. Phase 2 — Mode switch
3. Phase 3 — ARR scoring
4. Phase 4 — Delayed cleanup
5. Phase 6 — Subtitle audit
6. Optional phases as needed

---

## Design Principles (Do Not Violate)

* Never block user intent
* Tags must be human-readable
* Defaults must be safe
* Strict mode always available
* Automation must be reversible

---

*Last updated: fill date when implementing changes*
