# Task 2 Report: deterministic Threads account routing and slot ranking

## Status

DONE.

## Scope delivered

- Added `core/auto_scheduler.py` with deterministic routing helpers.
- `candidate_channels(conn, product, now_utc)` only considers Threads channels that are:
  - `ACTIVE`;
  - enabled;
  - Auto scheduling enabled;
  - configured with at least one valid niche;
  - matched by the product;
  - still have an eligible slot inside the rolling horizon.
- `niches=[]` is explicitly excluded from Auto routing.
- `route_product(conn, product, now_utc)` excludes products already queued/recently published and returns a stable skip reason when no route exists.
- Routing prefers:
  1. more matched niches;
  2. stronger same-account/hour historical score;
  3. lower current local-day occupancy;
  4. deterministic `channel_code` tie-break.
- `rank_slots(conn, channel_id, local_date, slots)` uses same-account historical samples only. Slots with sufficient samples are ranked by median performance; insufficient-history slots keep configured order.
- Slot/quota checks use the channel posting timezone and rolling 48-hour availability rather than assuming only the current local day.

## Regression coverage already in `tests/test_auto_scheduler.py`

- exact niche match;
- no fallback to `niches=[]`;
- inactive/disabled/Auto-OFF exclusion;
- full-quota exclusion while retaining channels with a later valid slot;
- duplicate/active/recent product exclusion;
- deterministic specific-match and channel-code tie-break;
- matched-niche reporting;
- same-account/hour metric ranking with configured-order fallback;
- selected-slot local-day quota behavior.

Task 3 and Task 4 follow-up verification repeatedly ran `AutoSchedulerRoutingTests` green while integrating fill and publish preflight behavior.

## Additional safety follow-up

During final branch review on 2026-08-21, a state-transition race was identified around slot validation: an Auto-OFF channel could be switched ON after artifact preparation while the current iteration still had no routed slot. `live_slot_occupied()` now fails closed for missing or malformed slot values so automated approval cannot proceed without a concrete parseable schedule slot. Dedicated regression coverage lives in `tests/test_auto_scheduler_safety.py`.

## Concerns

- The branch is currently behind `main` by the Shopee Affiliate CSV import merge. Those changes are largely non-overlapping with this feature, but final integration and release verification should still be run on the developer machine before merge.
