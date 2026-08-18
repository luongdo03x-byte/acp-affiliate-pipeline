# Facebook Seeding Assistant — Review Checklist

Use this checklist for draft PR review.

## Scope

- [ ] Facebook only.
- [ ] Target URLs are operator-supplied only; no discovery/search.
- [ ] Template-first generation; brief fallback does not bypass claim validation.
- [ ] Auto-submit defaults OFF and threshold cannot be below 0.85.

## Execution safety

- [ ] Global pause checked before queue execution.
- [ ] Active shift ID is re-checked immediately before submit.
- [ ] DOM/composer/submit ambiguity downgrades to review.
- [ ] Submit is attempted at most once.
- [ ] Failed post verification records UNKNOWN and never auto-retries.
- [ ] Facebook checkpoint/rate restriction stops execution without bypass.

## Content integrity

- [ ] Unsupported claims trigger review.
- [ ] Complaint/refund/legal/medical/fraud context triggers review.
- [ ] Fabricated first-person experience/testimonial language triggers review.
- [ ] Recent duplicate comments trigger review.

## Secrets/data

- [ ] No `.env.local` or real extension token committed.
- [ ] No Facebook cookie/password/session storage.
- [ ] Manifest has no `cookies`, `debugger`, proxy, or `<all_urls>` permission.

## Verification

- [ ] `python3 tests/test_manage.py` passes on Ubuntu checkout.
- [ ] `python3 -m acp.tests.test_seeding` passes in mock mode.
- [ ] `python3 -m acp.tests.test_seeding_web` passes in mock mode.
- [ ] Node extension tests pass.
- [ ] `./manage.sh test` passes from normal deployment layout.
- [ ] Controlled review-mode Facebook target validated before auto-submit is enabled.
