# OTT-Sync Complete Execution Flow Documentation

This document provides **detailed path flows** for every scenario in the OTT-sync system.

---

## 📋 **Table of Contents**

1. [Webhook Flows](#webhook-flows)
   - [Scenario 1A: New Item Added - OTT Found](#scenario-1a-new-item-added---ott-found)
   - [Scenario 1B: New Item Added - OTT Not Found](#scenario-1b-new-item-added---ott-not-found)
   - [Scenario 1C: New Item with Override Tag](#scenario-1c-new-item-with-override-tag)
   - [Scenario 1D: Previously Blocked Item Re-Added](#scenario-1d-previously-blocked-item-re-added)
   - [Scenario 1E: Grab Event - OTT Check](#scenario-1e-grab-event---ott-check)
   - [Scenario 1F: JustWatch Lookup Fails](#scenario-1f-justwatch-lookup-fails)

2. [Telegram Callback Flows](#telegram-callback-flows)
   - [Scenario 2A: User Approves Download](#scenario-2a-user-approves-download)
   - [Scenario 2B: User Approves During Verification Window](#scenario-2b-user-approves-during-verification-window)

3. [Delayed Verification Flows](#delayed-verification-flows)
   - [Scenario 3A: Verification - Item Correctly Blocked](#scenario-3a-verification---item-correctly-blocked)
   - [Scenario 3B: Verification - Race Condition Caught (In Queue)](#scenario-3b-verification---race-condition-caught-in-queue)
   - [Scenario 3C: Verification - Race Condition Caught (Has Files)](#scenario-3c-verification---race-condition-caught-has-files)
   - [Scenario 3D: Verification - Override Applied Before Check](#scenario-3d-verification---override-applied-before-check)

4. [Cron Cleanup Flows](#cron-cleanup-flows)
   - [Scenario 4A: Cron - Unprocessed Item OTT Found](#scenario-4a-cron---unprocessed-item-ott-found)
   - [Scenario 4B: Cron - Unprocessed Item OTT Not Found](#scenario-4b-cron---unprocessed-item-ott-not-found)
   - [Scenario 4C: Cron - Already Processed Item (Skip)](#scenario-4c-cron---already-processed-item-skip)
   - [Scenario 4D: Cron - 30-Day Re-check (OTT Now Available)](#scenario-4d-cron---30-day-re-check-ott-now-available)
   - [Scenario 4E: Cron - Manual Unmonitor Bypass Detected](#scenario-4e-cron---manual-unmonitor-bypass-detected)
   - [Scenario 4F: Cron - Override Tag Present (Skip)](#scenario-4f-cron---override-tag-present-skip)
   - [Scenario 4G: Cron - Unmonitored Item (Skip)](#scenario-4g-cron---unmonitored-item-skip)

5. [Race Condition Protection](#race-condition-protection)
   - [Scenario 5A: Concurrent Webhooks (Same Item)](#scenario-5a-concurrent-webhooks-same-item)
   - [Scenario 5B: Webhook + Cron Collision](#scenario-5b-webhook--cron-collision)
   - [Scenario 5C: Fast Auto-Grab During JustWatch Lookup](#scenario-5c-fast-auto-grab-during-justwatch-lookup)
   - [Scenario 5D: Duplicate Verification Timers](#scenario-5d-duplicate-verification-timers)

6. [Edge Cases](#edge-cases)
   - [Scenario 6A: Telegram Notification Failure](#scenario-6a-telegram-notification-failure)
   - [Scenario 6B: API Failures During Enforcement](#scenario-6b-api-failures-during-enforcement)
   - [Scenario 6C: Rate Limit Timeout](#scenario-6c-rate-limit-timeout)

---

## 📊 **Webhook Flows**

### **Scenario 1A: New Item Added - OTT Found**

**Trigger:** Radarr/Sonarr webhook → `MovieAdded` or `SeriesAdded` event

**Execution Path:**
```
1. Webhook received at /radarr or /sonarr endpoint
   └─ POST payload contains eventType + item data

2. added_hook(payload) called
   └─ Extract: event, item, title, year, item_id, tags
   └─ Log: "[WEBHOOK] MovieAdded → Title (2024) id=123"

3. Event validation
   └─ Check: event.startswith(("MovieAdd", "SeriesAdd", "Grab"))
   └─ ✅ Valid event type

4. Pre-lock override check
   └─ Check: override_tag in tags?
   └─ ❌ No override tag → Continue

5. Acquire item-level lock
   └─ _get_item_lock(item_id)
   └─ 🔐 Lock acquired for item 123

6. _process_webhook_locked() called
   └─ Re-fetch current item state (may have changed)
   └─ GET /api/v3/movie/123

7. Re-check override tag
   └─ Check: override_tag in current_tags?
   └─ ❌ No override → Continue

8. Pre-emptive monitoring disable
   └─ Check: was_monitored AND processed_tag NOT in tags?
   └─ ✅ Yes → Disable monitoring immediately
   └─ PUT /api/v3/movie/123 → monitored=false
   └─ Log: "[RACE-PROTECTION] Pre-emptively disabling monitoring"

9. OTT lookup decision
   └─ Check: processed_tag in current_tags?
   └─ ❌ Not processed yet

10. JustWatch API call
    └─ Extract: tmdb_id, imdb_id from item
    └─ justwatch.get_providers(title, year, ott_providers, tmdb_id, imdb_id)
    └─ Rate limiter enforced (10 calls/60s)
    └─ Search results filtered by ID match (best) or year match (fallback)
    └─ Filter: only "flatrate" monetization (subscriptions)
    └─ ✅ Found: ["Netflix"]

11. Extract metadata
    └─ Poster URL from images array
    └─ Plex users from pulsarr tags (if any)
    └─ Build caption with Telegram formatting

12. Telegram notification
    └─ Send photo + caption + "Download anyway" button
    └─ Button callback_data: "override:movie:123"
    └─ ✅ Notification sent successfully

13. Enforce block
    └─ POST /api/v3/command → CancelPendingDownloads
    └─ DELETE /api/v3/queue?movieId=123&removeFromClient=true
    └─ GET /api/v3/movie/123 (fetch current state)
    └─ Update: monitored=false, tags=[ott-skipped, ott-processed]
    └─ PUT /api/v3/movie/123
    └─ Log: "[ACTION] Item unmonitored + tagged"

14. Delete files
    └─ Check: delete_files=true (new block)
    └─ Extract: movieFile from item data
    └─ DELETE /api/v3/moviefile/{file_id}
    └─ Log: "[ACTION] 1 file(s) deleted from disk"

15. Update timestamp cache
    └─ timestamp_cache.update_check("movie", 123)
    └─ Save to ott_timestamps.json

16. Schedule delayed verification
    └─ Check: verification_delay_seconds > 0 AND not was_previously_blocked
    └─ ✅ Schedule verification in 60 seconds
    └─ _schedule_verification(123, "Title")
    └─ Timer created → _verify_and_cleanup(123, "Title") at T+60s

17. Mark processed
    └─ GET /api/v3/movie/123
    └─ Add processed_tag to tags array
    └─ PUT /api/v3/movie/123

18. Release lock
    └─ 🔓 Lock released for item 123

19. Return response
    └─ {"ok": true}
```

**Final State:**
- Item: `monitored=false`, `tags=[ott-skipped, ott-processed]`
- Files: Deleted
- Queue: Cleared
- Timestamp: Recorded
- Verification: Scheduled for T+60s

---

### **Scenario 1B: New Item Added - OTT Not Found**

**Execution Path:**
```
Steps 1-9: Same as Scenario 1A

10. JustWatch API call
    └─ justwatch.get_providers(...)
    └─ Search results returned
    └─ Filter by allowed providers
    └─ ❌ Not found: []

11. Decision: Not on OTT
    └─ Log: "[DECISION] Not on OTT → marking processed and restoring monitoring"

12. Update timestamp cache
    └─ timestamp_cache.update_check("movie", 123)

13. Mark processed
    └─ Check: processed_tag not in tags?
    └─ ✅ Yes → add processed_tag
    └─ GET /api/v3/movie/123
    └─ Add processed_tag to tags
    └─ Restore monitoring: monitored=true (was pre-emptively disabled)
    └─ PUT /api/v3/movie/123
    └─ Log: "[RACE-PROTECTION] Restoring monitoring"

14. Release lock
    └─ 🔓 Lock released

15. Return response
    └─ {"ok": true}
```

**Final State:**
- Item: `monitored=true`, `tags=[ott-processed]`
- Files: Untouched
- Download: Proceeds normally

---

### **Scenario 1C: New Item with Override Tag**

**Execution Path:**
```
Steps 1-3: Same as Scenario 1A

4. Pre-lock override check
   └─ Check: override_tag in tags?
   └─ ✅ Override present!
   └─ Log: "[DECISION] ott-override present → skip ALL OTT enforcement"
   └─ Return immediately

5. Return response
   └─ {"ok": true}
```

**Final State:**
- Item: Completely untouched
- No JustWatch lookup performed
- No lock acquired (optimization)

---

### **Scenario 1D: Previously Blocked Item Re-Added**

**Trigger:** Item with `ott-skipped` tag triggered again (manual search attempt)

**Execution Path:**
```
Steps 1-9: Same as Scenario 1A

10. Check processed status
    └─ Check: processed_tag in current_tags?
    └─ ✅ Yes, already processed

11. Check if previously blocked
    └─ Check: skipped_tag in tags?
    └─ ✅ Yes, was previously blocked
    └─ Set: was_previously_blocked = true
    └─ Log: "[DECISION] ott-skipped present → item was previously blocked"
    └─ Set: providers = ["OTT"] (placeholder for notification)

12. Telegram notification
    └─ Send: "🚨 Re-block detected" message
    └─ Simpler message (no poster)
    └─ Button: "Download anyway"

13. Enforce block
    └─ Same as Scenario 1A
    └─ BUT: delete_files=false (don't re-delete)
    └─ Log: "[ACTION] Item unmonitored + tagged"

14. Update timestamp
    └─ timestamp_cache.update_check("movie", 123)

15. NO verification scheduled
    └─ Check: not was_previously_blocked?
    └─ ❌ Was previously blocked → skip verification
    └─ Prevents duplicate timers

16. Already has processed_tag
    └─ Skip marking processed (already has it)

17. Release lock & return
```

**Final State:**
- Item: `monitored=false`, `tags=[ott-skipped, ott-processed]`
- Files: Not deleted (already gone)
- Verification: Not scheduled (avoid duplicates)

---

### **Scenario 1E: Grab Event - OTT Check**

**Trigger:** `Grab` webhook (download about to start)

**Execution Path:**
```
Steps 1-7: Same as Scenario 1A

8. Pre-emptive monitoring disable
   └─ Check: was_monitored AND processed_tag NOT in tags?
   └─ ✅ Yes → Disable monitoring
   └─ Log: "[RACE-PROTECTION] Pre-emptively disabling monitoring"

9. Grab event detection
   └─ Check: event == "Grab"?
   └─ ✅ Yes
   └─ Log: "[DECISION] Grab event → checking OTT as fallback"

10-18: Same as Scenario 1A or 1B depending on OTT result
```

**Purpose:** Last-chance check before download starts

---

### **Scenario 1F: JustWatch Lookup Fails**

**Trigger:** JustWatch API error, timeout, or infrastructure issue

**Execution Path:**
```
Steps 1-9: Same as Scenario 1A

10. JustWatch API call
    └─ justwatch.get_providers(...)
    └─ Exception raised OR rate limit timeout
    └─ Log: "[JustWatch] Search failed: {error}"
    └─ Return: None (infrastructure failure)

11. Fail-open decision
    └─ Check: providers is None?
    └─ ✅ Yes, infrastructure error
    └─ Log: "[DECISION] OTT lookup failed → defer decision"
    └─ Return immediately (no action taken)

12. Release lock & return
```

**Final State:**
- Item: Unchanged (monitoring still disabled from step 8)
- Decision: Deferred to next cron run or webhook retry
- Fail-open: Allows download to prevent false blocks

---

## 📱 **Telegram Callback Flows**

### **Scenario 2A: User Approves Download**

**Trigger:** User clicks "⬇️ Download anyway" button in Telegram

**Execution Path:**
```
1. Telegram webhook received
   └─ POST /telegram/callback
   └─ Payload contains callback_query data

2. Parse callback data
   └─ callback_data: "override:movie:123"
   └─ Extract: item_type="movie", item_id=123
   └─ Extract: user="John" (from callback_query.from.first_name)

3. Validate format
   └─ Check: callback_data.startswith("override:")?
   └─ ✅ Valid override request

4. Select manager
   └─ mgr = get_radarr_mgr() (for movies)

5. Fetch current item state
   └─ GET /api/v3/movie/123
   └─ Store: data = response.json()
   └─ Extract: tags = set(data["tags"])

6. Apply override
   └─ Remove: skipped_tag from tags
   └─ Add: override_tag to tags
   └─ Set: data["monitored"] = true
   └─ Update: data["tags"] = list(tags)

7. Update item in Radarr
   └─ PUT /api/v3/movie/123 (json=data)
   └─ ✅ Update successful

8. Cancel pending verification
   └─ mgr._cancel_verification(123)
   └─ Check: item_id in _verification_timers?
   └─ ✅ Timer found → cancel it
   └─ Log: "[VERIFY] Canceled verification for id=123"
   └─ Delete from tracking dict

9. Trigger search command
   └─ POST /api/v3/command
   └─ json: {"name": "MoviesSearch", "movieIds": [123]}
   └─ ✅ Command accepted

10. Send confirmation to Telegram
    └─ telegram.send("⬇️ Download confirmed\n\nOverride applied by John")

11. Return response
    └─ {"ok": true}
```

**Final State:**
- Item: `monitored=true`, `tags=[ott-override, ott-processed]`
- Verification: Canceled
- Search: Triggered automatically
- User: Notified of success

---

### **Scenario 2B: User Approves During Verification Window**

**Timeline:**
```
T+0s:   Item blocked, verification scheduled for T+60s
T+30s:  User clicks "Download anyway"
T+60s:  Verification timer fires (but timer was canceled)
```

**Execution Path:**
```
Steps 1-8: Same as Scenario 2A

8. Cancel pending verification
   └─ _cancel_verification(123) called
   └─ Timer found and still alive
   └─ timer.cancel() executed
   └─ Timer removed from _verification_timers dict

... T+60s arrives ...

9. Verification attempts to fire
   └─ Timer was canceled → nothing happens
   └─ No verification executed

10. Download proceeds normally with override
```

**Result:** User override takes precedence, verification never runs

---

## ⏱️ **Delayed Verification Flows**

### **Scenario 3A: Verification - Item Correctly Blocked**

**Trigger:** 60 seconds after item blocked, timer fires

**Execution Path:**
```
1. Timer fires at T+60s
   └─ _verify_and_cleanup(item_id=123, title="Title") called

2. Try-finally wrapper
   └─ try: _verify_not_grabbed(123, "Title")

3. Acquire item lock
   └─ 🔐 Lock acquired

4. Fetch current item state
   └─ GET /api/v3/movie/123
   └─ ✅ Success

5. Check override tag
   └─ Check: override_tag in tags?
   └─ ❌ No override → Continue

6. Check if blocked
   └─ Check: skipped_tag in tags?
   └─ ✅ Yes, item was blocked

7. Check for files
   └─ Check: hasFile field in item data
   └─ ❌ hasFile = false

8. Check download queue
   └─ GET /api/v3/queue
   └─ Check: any item in queue with movieId=123?
   └─ ❌ Not in queue

9. Verification successful
   └─ Log: "[VERIFY] ✓ Item 123 correctly blocked (not in queue/files)"

10. Release lock
    └─ 🔓 Lock released

11. Finally block executes
    └─ Remove from _verification_timers dict
    └─ Cleanup complete
```

**Result:** Item correctly blocked, no action needed

---

### **Scenario 3B: Verification - Race Condition Caught (In Queue)**

**Trigger:** Fast RSS sync grabbed item during JustWatch lookup

**Execution Path:**
```
Steps 1-7: Same as Scenario 3A

8. Check for files
   └─ Check: hasFile field
   └─ ❌ hasFile = false (download not complete yet)

9. Check download queue
   └─ GET /api/v3/queue
   └─ Parse: records = response.json()["records"]
   └─ Check: any record with movieId=123?
   └─ ✅ FOUND IN QUEUE!

10. Race condition detected
    └─ Log: "[VERIFY] ⚠️ Race condition detected! id=123 slipped through"
    └─ Log: "has_files=false, in_queue=true"

11. Re-enforce block
    └─ enforce_block(123, delete_files=true)
    └─ Cancel downloads
    └─ Remove from queue (removeFromClient=true)
    └─ Unmonitor item
    └─ Update tags

12. Telegram alert
    └─ Send: "⚠️ Race condition caught"
    └─ Message: "Item slipped through but caught before completion"
    └─ Status: "In queue: true, Has files: false"

13. Log success
    └─ Log: "[VERIFY] ✓ Re-enforced block for 123"

14. Release lock & cleanup
```

**Result:** Download canceled before completion, data saved!

---

### **Scenario 3C: Verification - Race Condition Caught (Has Files)**

**Execution Path:**
```
Steps 1-7: Same as Scenario 3A

8. Check for files
   └─ Check: hasFile field
   └─ ✅ hasFile = true (download completed fast!)

9. Race condition detected
   └─ Log: "[VERIFY] ⚠️ Race condition detected!"
   └─ Log: "has_files=true, in_queue=false"

10. Re-enforce block
    └─ enforce_block(123, delete_files=true)
    └─ DELETE /api/v3/moviefile/{file_id}
    └─ Files deleted from disk

11. Telegram alert
    └─ Send: "⚠️ Race condition caught"
    └─ Status: "In queue: false, Has files: true"

12. Release lock & cleanup
```

**Result:** Files deleted, but some data was consumed (caught as early as possible)

---

### **Scenario 3D: Verification - Override Applied Before Check**

**Execution Path:**
```
Steps 1-4: Same as Scenario 3A

5. Check override tag
   └─ Check: override_tag in tags?
   └─ ✅ Override present!
   └─ Log: "[VERIFY] ott-override present → skip verification (user approved download)"
   └─ Return immediately

6. Release lock & cleanup
```

**Result:** Verification respects user decision

---

## 🔄 **Cron Cleanup Flows**

### **Scenario 4A: Cron - Unprocessed Item OTT Found**

**Trigger:** Scheduled cron job (every 24 hours by default)

**Execution Path:**
```
1. Cron job starts
   └─ cron_cleanup() called
   └─ Log: "[CRON] Starting cleanup for movie"

2. Fetch all items
   └─ fetch_items() → GET /api/v3/movie
   └─ Returns: list of all movies in Radarr

3. Iterate through items
   └─ for item in items:

4. Acquire item lock
   └─ 🔐 _get_item_lock(item_id)

5. _process_cron_item_locked() called
   └─ Extract: tags, item_id from item

6. Check override tag
   └─ Check: override_tag in tags?
   └─ ❌ No override → Continue

7. Check if monitored
   └─ Check: item["monitored"]?
   └─ ✅ Yes, monitored → Continue

8. Check if processed
   └─ Check: processed_tag in tags AND skipped_tag NOT in tags?
   └─ ❌ Not processed yet → Continue

9. Check for manual bypass
   └─ Check: monitored AND skipped_tag in tags?
   └─ ❌ Not a bypass → Continue

10. Increment checked metric
    └─ metrics.checked += 1

11. JustWatch lookup
    └─ Extract: tmdb_id, imdb_id
    └─ justwatch.get_providers(title, year, ott_providers, tmdb_id, imdb_id)
    └─ ✅ Found: ["Netflix"]

12. Increment cleaned metric
    └─ metrics.cleaned += 1

13. Enforce block
    └─ enforce_block(item_id, delete_files=true)
    └─ Full cleanup: cancel + delete + untag + monitor

14. Update timestamp
    └─ timestamp_cache.update_check("movie", item_id)

15. Release lock
    └─ 🔓 Lock released

16. Continue to next item
```

**Final State:**
- Metrics: `{checked: 1, cleaned: 1, ...}`
- Item: Blocked and cleaned

---

### **Scenario 4B: Cron - Unprocessed Item OTT Not Found**

**Execution Path:**
```
Steps 1-11: Same as Scenario 4A

11. JustWatch lookup
    └─ justwatch.get_providers(...)
    └─ ❌ Not found: []

12. Increment marked_processed metric
    └─ metrics.marked_processed += 1

13. Mark as processed
    └─ GET /api/v3/movie/{item_id}
    └─ Add processed_tag to tags
    └─ PUT /api/v3/movie/{item_id}

14. Update timestamp
    └─ timestamp_cache.update_check("movie", item_id)

15. Release lock & continue
```

**Final State:**
- Item: `tags=[ott-processed]`, still monitored
- Download can proceed

---

### **Scenario 4C: Cron - Already Processed Item (Skip)**

**Execution Path:**
```
Steps 1-8: Same as Scenario 4A

8. Check if processed
   └─ Check: processed_tag in tags AND skipped_tag NOT in tags?
   └─ ✅ Already processed

9. Check re-check period
   └─ timestamp_cache.should_recheck("movie", item_id, recheck_days=30)
   └─ Check: last_check < now - 30 days?
   └─ ❌ Last checked 5 days ago → skip

10. Increment already_processed metric
    └─ metrics.already_processed += 1

11. Release lock & return
    └─ Early exit from _process_cron_item_locked()
```

**Result:** Item skipped to avoid redundant JustWatch calls

---

### **Scenario 4D: Cron - 30-Day Re-check (OTT Now Available)**

**Execution Path:**
```
Steps 1-8: Same as Scenario 4A

8. Check if processed
   └─ Check: processed_tag in tags AND skipped_tag NOT in tags?
   └─ ✅ Already processed

9. Check re-check period
   └─ timestamp_cache.should_recheck("movie", item_id, recheck_days=30)
   └─ Check: last_check < now - 30 days?
   └─ ✅ Last checked 35 days ago → RE-CHECK!
   └─ Log: "[CRON] Re-checking 123 (30+ days since last check)"

10. Continue with JustWatch lookup
    └─ justwatch.get_providers(...)
    └─ ✅ Found: ["Disney Plus"] (newly added to OTT!)

11. Enforce block
    └─ enforce_block(item_id, delete_files=true)

12. Update timestamp
    └─ timestamp_cache.update_check("movie", item_id)
```

**Result:** OTT availability changes caught automatically

---

### **Scenario 4E: Cron - Manual Unmonitor Bypass Detected**

**Trigger:** User manually re-enabled monitoring on blocked item in Radarr UI

**Execution Path:**
```
Steps 1-8: Same as Scenario 4A

9. Check for manual bypass
   └─ Check: monitored=true AND skipped_tag in tags?
   └─ ✅ BYPASS DETECTED!
   └─ Log: "[CRON] Manual unmonitor bypass detected for {item_id}"

10. Increment cleaned metric
    └─ metrics.cleaned += 1

11. Re-enforce block
    └─ enforce_block(item_id, delete_files=false)
    └─ Set monitored=false again
    └─ Don't delete files (already deleted)

12. Update timestamp
    └─ timestamp_cache.update_check("movie", item_id)

13. Release lock & return
```

**Result:** Manual bypass reverted, block re-enforced

---

### **Scenario 4F: Cron - Override Tag Present (Skip)**

**Execution Path:**
```
Steps 1-6: Same as Scenario 4A

6. Check override tag
   └─ Check: override_tag in tags?
   └─ ✅ Override present!

7. Increment skipped_override metric
   └─ metrics.skipped_override += 1

8. Release lock & return
```

**Result:** User intent always respected

---

### **Scenario 4G: Cron - Unmonitored Item (Skip)**

**Execution Path:**
```
Steps 1-7: Same as Scenario 4A

7. Check if monitored
   └─ Check: item["monitored"]?
   └─ ❌ Not monitored → Return

8. Release lock (no metrics updated)
```

**Result:** Only monitored items processed in cron

---

## 🔐 **Race Condition Protection**

### **Scenario 5A: Concurrent Webhooks (Same Item)**

**Trigger:** MovieAdded and Grab webhooks arrive simultaneously for same item

**Timeline:**
```
T+0ms:   MovieAdded webhook arrives (Thread 1)
T+5ms:   Grab webhook arrives (Thread 2)
```

**Execution Path:**
```
Thread 1:
1. MovieAdded webhook starts processing
2. Pre-lock override check → Pass
3. Acquire item lock (item_id=123)
   └─ 🔐 LOCK ACQUIRED (Thread 1 holds lock)
4. Start JustWatch lookup...

Thread 2 (5ms later):
1. Grab webhook starts processing
2. Pre-lock override check → Pass
3. Attempt to acquire item lock (item_id=123)
   └─ 🔒 BLOCKED! Thread 1 holds lock
   └─ Thread 2 waits...

Thread 1 (continues):
5. JustWatch returns results
6. Enforce block
7. Schedule verification
8. Release lock
   └─ 🔓 LOCK RELEASED

Thread 2 (now unblocked):
4. Lock acquired (Thread 1 released it)
   └─ 🔐 Lock acquired
5. Re-fetch current item state
   └─ Item already has: processed_tag, skipped_tag
6. Check processed_tag
   └─ ✅ Already processed
7. Check skipped_tag
   └─ ✅ Was previously blocked
   └─ Re-blocking scenario
8. Schedule verification
   └─ Check: not was_previously_blocked?
   └─ ❌ Was previously blocked
   └─ SKIP scheduling (prevent duplicate timer)
9. Release lock
```

**Result:** No duplicate processing, no duplicate timers

---

### **Scenario 5B: Webhook + Cron Collision**

**Timeline:**
```
T+0s:     Cron starts, begins processing item 123
T+0.5s:   Webhook arrives for item 123
```

**Execution Path:**
```
Cron (Thread 1):
1. Cron starts iteration
2. Acquire lock for item 123
   └─ 🔐 LOCK ACQUIRED
3. Start JustWatch lookup (takes 2s)

Webhook (Thread 2):
1. Webhook received
2. Pre-lock override check → Pass
3. Attempt to acquire lock for item 123
   └─ 🔒 BLOCKED! Cron holds lock
   └─ Thread 2 WAITS...

Cron (continues):
4. JustWatch returns
5. Enforce block
6. Update tags
7. Release lock
   └─ 🔓 LOCK RELEASED

Webhook (unblocked):
4. Lock acquired
5. Re-fetch item state
   └─ Now has processed_tag (added by cron)
6. Check processed_tag
   └─ ✅ Already processed → skip JustWatch
7. Release lock
```

**Result:** No redundant JustWatch calls, no conflicts

---

### **Scenario 5C: Fast Auto-Grab During JustWatch Lookup**

**Timeline:**
```
T+0s:     Webhook received
T+0.1s:   Monitoring disabled (pre-emptive)
T+0.5s:   JustWatch lookup starts
T+1s:     RSS sync finds release
T+1.1s:   Auto-grab ATTEMPT → FAILS (monitoring=false)
T+2s:     JustWatch returns → enforce block
T+62s:    Verification runs → no items in queue ✓
```

**Execution Path:**
```
1. Webhook processing starts
2. Pre-emptive monitoring disable
   └─ PUT /api/v3/movie/123 → monitored=false
   └─ Log: "[RACE-PROTECTION] Pre-emptively disabling monitoring"

3. JustWatch lookup (takes ~2 seconds)

4. Meanwhile: Radarr RSS sync runs
   └─ Finds: new release for item 123
   └─ Checks: item.monitored?
   └─ ❌ monitored=false → AUTO-GRAB PREVENTED

5. JustWatch returns → OTT found
6. Enforce block (monitoring already disabled)
7. Schedule verification for T+60s

8. T+60s: Verification runs
   └─ Check queue → empty ✓
   └─ Check files → none ✓
   └─ Log: "[VERIFY] ✓ Item correctly blocked"
```

**Result:** Pre-emptive disable prevented auto-grab window

---

### **Scenario 5D: Duplicate Verification Timers**

**Trigger:** MovieAdded + Grab webhooks for new item

**Timeline:**
```
T+0s:     MovieAdded webhook → blocks item, schedules verification
T+2s:     Grab webhook → re-blocks item (shouldn't schedule again)
```

**Execution Path:**
```
MovieAdded (Thread 1):
1. Process webhook
2. Block item (newly blocked)
3. Check: not was_previously_blocked?
   └─ ✅ First block
4. Schedule verification
   └─ _schedule_verification(123, "Title")
   └─ Timer created and stored in _verification_timers[123]
   └─ Log: "[VERIFY] Scheduling verification in 60s"

Grab (Thread 2):
1. Process webhook
2. Check: already has skipped_tag
   └─ ✅ was_previously_blocked = true
3. Re-block item
4. Check: not was_previously_blocked?
   └─ ❌ Was previously blocked
   └─ SKIP scheduling verification
   └─ No duplicate timer created!
```

**Result:** Only one verification timer per item

---

## ⚠️ **Edge Cases**

### **Scenario 6A: Telegram Notification Failure**

**Execution Path:**
```
Steps 1-11: Same as Scenario 1A (Webhook - OTT Found)

12. Telegram notification
    └─ telegram.send_photo(poster, caption, buttons)
    └─ ❌ REQUEST FAILED (timeout, network error, bot token invalid)
    └─ Returns: false

13. Log notification failure
    └─ Log: "[DECISION] Telegram notification FAILED for id=123"
    └─ Log: "Blocking anyway - user won't receive unblock button"
    └─ Log: "Manual intervention required via Radarr UI if this persists"

14. Continue with enforcement
    └─ enforce_block() called anyway
    └─ Item blocked regardless of Telegram status

15. Schedule verification
    └─ Verification still scheduled

16. Complete processing
```

**Result:** Block enforced, but user has no easy override button (must use Radarr UI)

---

### **Scenario 6B: API Failures During Enforcement**

**Execution Path:**
```
1. enforce_block(item_id=123) called

2. Cancel pending downloads
   └─ POST /api/v3/command → CancelPendingDownloads
   └─ ❌ REQUEST FAILED (Radarr down, network error)
   └─ Log: "[HTTP] POST command failed → 500"
   └─ Continue anyway (best effort)

3. Remove from queue
   └─ DELETE /api/v3/queue?movieId=123
   └─ ❌ REQUEST FAILED
   └─ Log: "[HTTP] DELETE queue failed"
   └─ Continue anyway

4. Fetch current item
   └─ GET /api/v3/movie/123
   └─ ❌ REQUEST FAILED
   └─ Return None → enforce_block exits early

5. Tags/monitoring NOT updated
   └─ Item state unchanged

6. Verification still scheduled
   └─ Will catch item if download proceeded
```

**Result:** Partial failure, verification layer catches it

---

### **Scenario 6C: Rate Limit Timeout**

**Execution Path:**
```
1. JustWatch lookup requested
2. Rate limiter check
   └─ Check: len(calls) < max_calls?
   └─ ❌ 60 calls already made in last 60 seconds
   └─ Wait for slot...

3. Timeout check
   └─ Wait time: 30 seconds (default timeout)
   └─ After 30s: still no slot available
   └─ Log: "[RateLimiter] Timeout after 30s"
   └─ Return: False

4. JustWatch client receives None
   └─ Log: "[JustWatch] Rate limit timeout or execution failed"
   └─ Return: None

5. Fail-open decision
   └─ providers = None → infrastructure failure
   └─ Log: "[DECISION] OTT lookup failed → defer decision"
   └─ No enforcement action taken
```

**Result:** Fail-open, deferred to next cron run

---

## 📈 **Performance Characteristics**

### **Locks Held Duration:**
- Webhook: ~2-4 seconds (JustWatch lookup time)
- Cron: ~2-4 seconds per item
- Verification: ~500ms (queue check + file check)

### **API Calls Per Webhook:**
- Pre-emptive disable: 2 calls (GET + PUT)
- JustWatch lookup: 1 call (rate limited)
- Enforcement: 5-8 calls (cancel + queue + get + put + delete files)
- Mark processed: 2 calls (GET + PUT)
- **Total: 10-13 calls per blocked item**

### **Concurrent Processing:**
- Different items: Parallel (no blocking)
- Same item: Sequential (lock enforced)
- Max JustWatch rate: 10 calls/60 seconds (configurable)

---

## 🎯 **Decision Matrix**

| Condition | Action | Verification | Notes |
|-----------|--------|--------------|-------|
| Override tag present | Skip all checks | None | User intent absolute |
| OTT found + new | Block + delete | Schedule 60s | Full cleanup |
| OTT found + re-block | Block no-delete | Skip | Already cleaned |
| OTT not found | Mark processed | None | Allow download |
| Already processed | Skip | None | Unless 30+ days |
| 30+ days old | Re-check | If blocked | OTT changes |
| JustWatch fails | Defer | None | Fail-open |
| Monitored + skipped | Re-block | None | Bypass prevention |
| Unmonitored | Skip | None | Cron only |

---

**Last Updated:** January 6, 2026  
**Version:** 1.0.0 with race condition protection
