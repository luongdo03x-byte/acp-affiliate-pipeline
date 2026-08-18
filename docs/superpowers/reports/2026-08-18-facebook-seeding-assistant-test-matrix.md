# Facebook Seeding Assistant — Test Matrix

| Area | Sandbox status | Ubuntu gate |
|---|---|---|
| SQLite schema/settings | PASS | rerun via test_seeding |
| Domain queue/risk/result logic | PASS | rerun via test_seeding |
| Shift pause immediately before submit | PASS red/green | covered by Node tests |
| Extension pure helpers/contracts | PASS | rerun Node suite |
| KPI/report aggregation | PASS | rerun via test_seeding/report scenario |
| Flask functional routes | BLOCKED: Flask unavailable | run test_seeding_web |
| Exact repo manager integration | PARTIAL: harness 5/5 | run tests/test_manage.py |
| Full release gate | BLOCKED: deployment layout unavailable | run ./manage.sh test |
| Live Facebook DOM | NOT RUN | review-mode authorized target first |
