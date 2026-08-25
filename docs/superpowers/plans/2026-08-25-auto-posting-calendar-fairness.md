# Auto Posting Calendar Fairness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Auto Posting plan today + tomorrow by each channel's local calendar, fill remaining current-day slots, distribute scarce products fairly across channels, and scope product reuse/cooldown to each channel.

**Architecture:** Keep the existing single scheduler and publish-worker path. Move calendar semantics into `auto_scheduler`, keep candidate generation channel-specific, let `pipeline.fill_auto_schedule()` allocate one plan per channel per round, and make Auto Posting listing use channel-local calendar boundaries rather than a rolling 48-hour lower bound.

**Tech Stack:** Python 3.12, SQLite, Flask/Jinja, unittest.

**Spec:** `docs/superpowers/specs/2026-08-25-auto-posting-calendar-fairness-design.md`

## Global Constraints

- Scheduler must never create a target in the past.
- Scheduling window is today + tomorrow in each channel's `posting_timezone`.
- UI includes earlier-today plans.
- Product reuse is allowed across different channels but not within the same channel during active/queued state or cooldown.
- Preserve existing quality, category/day, provider freshness, Auto toggle and publish-worker safeguards.
- Do not create a second scheduler/queue/publisher.

---

### Task 1: Calendar-aware slot computation

**Files:**
- Modify: `core/auto_scheduler.py`
- Test: `tests/test_auto_scheduler.py`

**Interfaces:**
- Produces: helper(s) for local today/tomorrow boundaries and updated `available_slots(conn, channel, now_utc) -> list[dict]`.
- Consumers: `pipeline.fill_auto_schedule`, Auto Posting listing tests.

- [ ] **Step 1: Write failing tests**

Add tests that create a channel in `Asia/Bangkok` with target 2 and slots `09:30, 12:30, 20:30`; at local 15:00 assert `20:30` today remains available. Add a test asserting returned slots never fall on the local day after tomorrow.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
python -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests -v
```

Expected: the current implementation misses `20:30` in the afternoon case because it slices slots before filtering.

- [ ] **Step 3: Implement minimal calendar logic**

In `available_slots`:

```python
local_today = now_utc.astimezone(tzinfo).date()
local_tomorrow = local_today + timedelta(days=1)
for local_date in (local_today, local_tomorrow):
    effective_target = min(_core_daily_target(channel), _core_daily_cap(channel))
    existing_count = _quota_count_for_local_date(conn, channel["id"], tz_name, local_date)
    remaining = max(0, effective_target - existing_count)
    if remaining <= 0:
        continue
    occupied = _occupied_slots_for_local_date(conn, channel["id"], tz_name, local_date)
    candidates = []
    for slot in rank_slots(conn, channel["id"], local_date, slots):
        if slot["slot"] in occupied:
            continue
        slot_dt = _slot_datetime(local_date, slot["slot"], tz_name)
        if slot_dt.astimezone(timezone.utc) < now_utc:
            continue
        candidates.append(...)
    available.extend(candidates[:remaining])
```

Do not use `now_utc + timedelta(hours=48)` to decide the local date range.

- [ ] **Step 4: Run focused tests and confirm pass**

```bash
python -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests -v
```

- [ ] **Step 5: Commit**

```bash
git add core/auto_scheduler.py tests/test_auto_scheduler.py
git commit -m "fix: plan auto slots by local calendar day"
```

### Task 2: Channel-scoped product reuse/cooldown

**Files:**
- Modify: `core/auto_scheduler.py`
- Modify: `core/pipeline.py`
- Modify: `core/shopee_auto_runtime.py`
- Test: `tests/test_auto_scheduler.py`
- Test: `tests/test_shopee_auto_pipeline.py`

**Interfaces:**
- Change private helper to `_queued_or_recently_published_product_exists(conn, product_id, now_utc, *, channel_id=None, exclude_post_id=None) -> bool`.
- Auto scheduling must always pass `channel_id`.

- [ ] **Step 1: Write failing tests**

Add two assertions:

```python
# same product active on channel A does not block channel B
self.assertFalse(_queued_or_recently_published_product_exists(
    conn, product_id, now_utc, channel_id="channel-b"
))

# same product active/recent on channel A still blocks channel A
self.assertTrue(_queued_or_recently_published_product_exists(
    conn, product_id, now_utc, channel_id="channel-a"
))
```

Add a Shopee regression proving a Shopee product eligible for a second channel is not rejected solely because channel one already has it scheduled.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
python -m unittest tests.test_auto_scheduler tests.test_shopee_auto_pipeline -v
```

- [ ] **Step 3: Implement channel scoping**

Update queued and published SQL with optional channel filter:

```sql
AND (? IS NULL OR channel_id = ?)
```

Pass the current channel ID from `current_auto_product_eligibility`, Shopee eligibility, route/preflight paths and Auto candidate filtering. Preserve global behavior for non-Auto callers that omit `channel_id`.

Where Auto candidate scoring currently inherits a global `recent` exclusion, ensure the Auto path performs its final active/cooldown decision with the channel-scoped helper instead of losing cross-channel candidates before eligibility can inspect them.

- [ ] **Step 4: Run focused tests**

```bash
python -m unittest tests.test_auto_scheduler tests.test_shopee_auto_pipeline -v
```

- [ ] **Step 5: Commit**

```bash
git add core/auto_scheduler.py core/pipeline.py core/shopee_auto_runtime.py tests/test_auto_scheduler.py tests/test_shopee_auto_pipeline.py
git commit -m "fix: scope auto product cooldown to channel"
```

### Task 3: Fair round-robin scheduler allocation

**Files:**
- Modify: `core/pipeline.py`
- Test: `tests/test_auto_scheduler.py`

**Interfaces:**
- `fill_auto_schedule(...) -> dict` remains unchanged externally.
- Internally track per-channel open slot queues and candidate cursors; one successful assignment per channel per round.

- [ ] **Step 1: Write failing fairness test**

Create at least four Auto-enabled channels sharing a product niche, each needing multiple slots, and a limited candidate set. Record scheduled targets by channel and assert every channel receives one assignment before any channel receives a second assignment whenever enough assignments exist for one full round.

Example invariant:

```python
counts = Counter(row["channel_id"] for row in targets)
self.assertTrue(all(counts[channel_id] >= 1 for channel_id in channel_ids))
self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
```

- [ ] **Step 2: Run test and confirm current sequential allocator fails**

```bash
python -m unittest tests.test_auto_scheduler -v
```

- [ ] **Step 3: Implement round-robin fill**

Build state only for eligible Auto-enabled Threads channels:

```python
states = [
    {
        "channel": channel,
        "slots": auto_scheduler.available_slots(conn, channel, now_utc),
        "candidates": _candidate_products_for_channel(...),
        "candidate_index": 0,
    }
    for channel in channels
    if channel["auto_schedule_enabled"]
]
```

Loop rounds while progress is made. For each state, attempt candidates until one post is successfully scheduled for the state's next slot or candidates are exhausted. Do not call `route_product()` to reassign a candidate to another channel inside this channel-owned allocation path; validate `current_auto_product_eligibility` for the state channel directly.

Preserve the existing Auto-OFF/manual-review behavior separately if it is still required by current tests; do not let it interfere with fair Auto-enabled allocation.

- [ ] **Step 4: Run scheduler tests**

```bash
python -m unittest tests.test_auto_scheduler -v
```

- [ ] **Step 5: Commit**

```bash
git add core/pipeline.py tests/test_auto_scheduler.py
git commit -m "fix: distribute auto plans fairly across channels"
```

### Task 4: Calendar-aware Auto Posting listing and copy

**Files:**
- Modify: `core/auto_post_plans.py`
- Modify: `web/auto_posting.py`
- Modify: `web/templates/auto_posting.html`
- Test: `tests/test_auto_posting_job_controls.py`

**Interfaces:**
- Add calendar-aware plan listing that includes earlier-today rows for each channel timezone.
- Keep route `/auto-posting` and POST endpoints unchanged.

- [ ] **Step 1: Write failing web/list tests**

Create a target earlier today and another tomorrow in Asia/Bangkok. Open `/auto-posting` later today and assert both are visible. Create a target on the day after tomorrow and assert it is absent.

Assert copy contains `Hôm nay + ngày mai` / `Lấp lịch hôm nay + ngày mai` and no longer labels the control as `48-hour control center`.

- [ ] **Step 2: Run focused tests**

```bash
python -m unittest tests.test_auto_posting_job_controls -v
```

- [ ] **Step 3: Implement listing semantics**

Prefer a query that selects plausible rows and then filters each row using its channel timezone:

```python
local_date = scheduled_dt.astimezone(ZoneInfo(row["posting_timezone"] or "Asia/Bangkok")).date()
local_today = now_utc.astimezone(tz).date()
if local_date in (local_today, local_today + timedelta(days=1)):
    result.append(item)
```

This correctly handles channels with different timezones in one page.

Update `run_scheduler_now()` success/error wording and template labels to calendar language.

- [ ] **Step 4: Run focused tests**

```bash
python -m unittest tests.test_auto_posting_job_controls -v
```

- [ ] **Step 5: Commit**

```bash
git add core/auto_post_plans.py web/auto_posting.py web/templates/auto_posting.html tests/test_auto_posting_job_controls.py
git commit -m "feat: show auto plans for today and tomorrow"
```

### Task 5: Full regression verification

**Files:**
- No new production files expected.
- Adjust tests only if a failing legacy assertion contradicts the approved calendar/fairness semantics.

- [ ] **Step 1: Run focused scheduler + Auto Posting + Shopee gate**

```bash
python -m unittest \
  tests.test_auto_scheduler \
  tests.test_auto_scheduler_safety \
  tests.test_auto_posting_job_controls \
  tests.test_shopee_auto_pipeline -v
```

Expected: all pass.

- [ ] **Step 2: Compile changed surfaces**

```bash
python -m compileall core web tests adapters run.py
```

Expected: exit 0.

- [ ] **Step 3: Check whitespace**

```bash
git diff --check main...HEAD
```

Expected: no output, exit 0.

- [ ] **Step 4: Review diff for unintended behavior**

Confirm no new scheduler/worker path, no relaxed quality/provider checks, and no secret/config changes.

- [ ] **Step 5: Commit any final test-only alignment, push branch, and open PR to `main`**
