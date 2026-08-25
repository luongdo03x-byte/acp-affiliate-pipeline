# Auto Posting Calendar Fairness Design

## Goal

Make Auto Posting fill the remainder of each channel's local calendar day plus the next local calendar day, distribute scarce eligible products fairly across Auto-enabled Threads channels, and scope product cooldown/active-use checks to each channel instead of globally.

## Current problems

1. The scheduler uses a rolling `now -> now + 48h` window, so late-day runs can spill into a third local calendar date while failing to represent the full current day cleanly.
2. `available_slots()` slices configured slots before removing elapsed/occupied slots. If target=2 and slots are `09:30, 12:30, 20:30`, a 15:00 run can lose the still-valid `20:30` slot because only the first two slots are considered.
3. `fill_auto_schedule()` fills channels sequentially, while `route_product()` independently re-ranks all channels. This can starve later channels when the eligible product pool is small.
4. Product active/cooldown checks are global by `product_id`, so one account consuming a product prevents other accounts from using it even when their content and schedule are independent.
5. The Auto Posting page describes a rolling 48-hour control center instead of the intended calendar-day semantics.

## Approved behavior

### Calendar window

For each channel, use its `posting_timezone` and manage exactly two local calendar dates: today and tomorrow.

- Planning lower bound: current instant (`now_utc`) so the scheduler never creates a post in the past.
- Planning upper bound: local midnight immediately after tomorrow, converted to UTC for that channel.
- Display lower bound: local midnight at the start of today, so posts from earlier today remain visible.
- Display upper bound: local midnight immediately after tomorrow.

This means a run at 16:00 still considers remaining configured slots later today and all valid configured slots tomorrow.

### Daily slot selection

For each local date:

1. Determine the daily target and cap using the existing channel configuration.
2. Count existing live targets for that local date.
3. Rank all configured posting slots.
4. Remove occupied and elapsed slots.
5. Return only as many slots as are still needed to reach the day's effective target/cap.

Do not slice `posting_slots` before elapsed/occupied filtering.

### Fair allocation

Fill plans round-robin across Auto-enabled channels instead of exhausting one channel before the next.

Each pass gives at most one new plan to each channel that still has an open slot. Continue passes until no channel can be filled further or no eligible products remain for any channel.

Expected scarcity behavior: if six channels each need multiple posts and only ten assignments are possible, allocation should be close to `2/2/2/2/1/1`, not `4/2/4/0/0/0`.

### Product reuse and cooldown

A product may be used by different channels/accounts. A product may not be duplicated on the same channel while it is queued/live or during the configured cooldown after publication.

All relevant checks must therefore accept/use `channel_id`:

- active/queued product check;
- recent published cooldown check;
- candidate scoring exclusion used by Auto scheduling;
- route/preflight eligibility checks.

Do not relax blocked categories, quality thresholds, category/day caps, inventory/link/image freshness, or publish safety checks.

### Routing

The fair scheduler owns assignment to a channel slot. Product selection for one channel must not re-route that candidate to another channel and then discard it. Channel-specific candidate eligibility remains authoritative.

### Auto Posting UI

Replace rolling-48h wording with calendar wording:

- `48-hour control center` -> `Hôm nay + ngày mai`
- `Tạo lịch 48h ngay` -> `Lấp lịch hôm nay + ngày mai`
- `Plan 48h` -> `Plan hôm nay + ngày mai`

The page should show all non-cancelled plans whose scheduled local date is today or tomorrow for their channel, including plans earlier today.

## Files

- `core/auto_scheduler.py`: calendar boundaries, available-slot logic, channel-scoped duplicate/cooldown helpers.
- `core/pipeline.py`: fair round-robin fill and channel-specific candidate eligibility.
- `core/shopee_auto_runtime.py`: propagate channel-scoped duplicate/cooldown semantics for Shopee candidates/preflight.
- `core/auto_post_plans.py`: calendar-day list window for UI.
- `web/auto_posting.py`: request/display semantics and operator messages.
- `web/templates/auto_posting.html`: calendar wording.
- Tests: scheduler, Auto Posting control center, Shopee regression as needed.

## Regression requirements

Tests must prove:

1. A 15:00 local run with slots `09:30, 12:30, 20:30` can still schedule `20:30` today.
2. No slot on the local day after tomorrow is created.
3. The Auto Posting list includes an earlier-today plan even when the page is opened later that day.
4. The same product may be scheduled on two different channels.
5. The same product remains blocked from duplicate/recent reuse on the same channel.
6. Multiple channels sharing a limited eligible pool are filled round-robin so later channels are not starved.
7. Existing quality, category cap, link/image freshness, publish worker and Auto ON/OFF safeguards remain green.