# Task 1 Report

Status: DONE

Scope completed:
- Added additive `channel` automation columns and fresh-schema defaults in `core/db.py`.
- Added `validate_channel_automation_config()` plus `/kenh` Threads-only save handling and audit persistence in `web/server.py`.
- Added Threads-only automation controls to `web/templates/channels.html`.
- Added focused regression coverage in `tests/test_auto_scheduler.py`.

Red -> green evidence:
- Red: `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler -v`
  - Failed on missing `channel.auto_schedule_enabled` / `daily_post_target` / `posting_timezone` / `posting_slots`
  - Failed on missing `validate_channel_automation_config`
  - Failed because `/kenh` lacked the new Threads-only automation controls
- Green: `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler -v`
  - Result: `Ran 8 tests ... OK`

Verification commands:
- `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler -v` -> PASS
- `git diff --check` -> PASS
- `git status --short` -> reviewed before commit

Follow-up fixes after review:
- Updated all new Threads channel creation paths touched by Task 1 to default `daily_post_cap=3` without rewriting legacy rows:
  - `core/account_factory.py` OAuth channel insert now uses `3`
  - `core/factory_v2/channel_schema.py` minimal factory schema now defaults to `3`
- Made `updated_automation` audit conditional on real automation-field changes; niche-only `/kenh` saves now keep only the existing `set_niches` audit.
- Added regressions in:
  - `tests/test_account_factory.py`
  - `tests/test_auto_scheduler.py`

Follow-up red -> green evidence:
- Red: `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_account_factory tests.test_auto_scheduler -v`
  - Failed because `core/account_factory.py` still inserted `daily_post_cap=12`
  - Failed because `core/factory_v2/channel_schema.py` still defaulted `daily_post_cap=12`
  - Failed because niche-only `/kenh` saves still emitted `updated_automation`
- Green: `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_account_factory tests.test_auto_scheduler -v`
  - Result: `Ran 15 tests ... OK`

Updated verification commands:
- `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler -v` -> PASS
- `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_account_factory tests.test_auto_scheduler -v` -> PASS
- `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler.ChannelAutomationWebTests -v` -> PASS
- `./manage.sh test` -> PASS (`TEST_OK`, mock mode)
- `git diff --check` -> PASS
- `git status --short` -> reviewed before follow-up commit

Legacy browser-cap follow-up:
- Fixed `/kenh` browser-side `daily_post_cap` input max for legacy Threads channels so unchanged legacy caps like `12` can be submitted without the browser blocking before POST.
- Kept the server-side Auto clamp semantics unchanged:
  - new Auto cap values above `3` are still rejected
  - unchanged legacy caps above `3` are still allowed through validation
  - niche-only saves still work and do not emit `updated_automation`
- Implemented dynamic rendering via per-channel `daily_post_cap_input_max` in `web/server.py` and `web/templates/channels.html`.

Legacy browser-cap red -> green evidence:
- Red: `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler.ChannelAutomationWebTests -v`
  - Failed because `/kenh` still rendered legacy Threads `daily_post_cap` with `max="3"` even when the existing stored cap was `12`
- Green: `/home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler.ChannelAutomationWebTests -v`
  - Result: `Ran 5 tests ... OK`

Concerns:
- None within Task 1 scope.
