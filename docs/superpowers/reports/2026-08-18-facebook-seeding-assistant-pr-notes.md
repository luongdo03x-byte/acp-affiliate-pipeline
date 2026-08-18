# Draft PR Notes — Facebook Seeding Assistant

## What changed

- Additive SQLite seeding schema and global kill switch.
- Seeding domain queue, template-first generation, deterministic risk gates, duplicate guard, shift/KPI reporting.
- Flask/Jinja2 Seeding dashboard and token-protected extension API.
- Chrome Manifest V3 extension with current-target context extraction, review panel, hybrid auto-submit, single-submit verification, and fail-closed pause behavior.
- Release gate integration for seeding Python/web tests.
- Operator and extension runbooks.

## Validation performed in sandbox

- 9/9 domain tests pass.
- 2/2 schema/settings tests pass.
- 13/13 Node extension contract tests pass.
- KPI/report scenario passes.
- Shift-pause-before-submit regression red/green verified.
- Manager integration harness: 5/5 pass.

## Remaining gates

Sandbox cannot run the exact full Ubuntu checkout/release verification because it has no Flask installed and cannot clone GitHub through outbound DNS. Before enabling auto-submit on the operator machine, run the commands in `docs/superpowers/reports/2026-08-18-facebook-seeding-assistant-verification.md`.

No live Facebook post was executed while implementing this branch.
