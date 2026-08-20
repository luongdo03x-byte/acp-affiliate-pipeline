# ACP — Shopee Affiliate Full Release Checklist

Date: 2026-08-17
Scope: Phase 1 → Phase 4 stacked rollout.

## Branch / PR order

```text
main
 └─ feat/shopee-bulk-affiliate          # Phase 1
      └─ feat/shopee-metadata-helper     # Phase 2
           └─ feat/shopee-product-intel  # Phase 3
                └─ feat/shopee-affiliate-polish  # Phase 4
```

Merge only in that order after each gate is green. Do not force-push `main`.

## Safety boundary

Release verification must not:

- publish a real Threads/Facebook/Instagram post;
- auto-approve a post;
- bypass Shopee CAPTCHA/identity verification;
- automate Shopee login with Selenium/Playwright/headless browser;
- read/reuse Shopee cookies/session/localStorage auth/browser credentials;
- reverse-engineer private Shopee APIs;
- wrap Shopee Direct links through ACCESSTRADE;
- print/commit `.env.local`, production DB, tokens, secrets or `var/` runtime data.

Use mock adapters unless a human explicitly requests a controlled live integration step.

## Ubuntu automated gate

From the directory containing the `acp/` package:

```bash
export ACP_ADAPTER=mock
export ACP_SOURCE=mock

python -m acp.tests.test_shopee_bulk_affiliate
python -m acp.tests.test_shopee_helper -v
python -m acp.tests.test_shopee_helper_ui -v
python -m acp.tests.test_shopee_product_intel -v
python -m acp.tests.test_shopee_product_upsert -v
python -m acp.tests.test_shopee_product_intel_web -v
python -m acp.tests.test_shopee_product_intel_ui -v
python -m acp.tests.test_shopee_observability -v
python -m acp.tests.test_shopee_instrumentation -v
python -m acp.tests.test_shopee_preview -v
python -m acp.tests.test_shopee_polish_ui -v
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot

cd acp
python tests/test_manage.py
./manage.sh test
git diff --check
```

Also parse/compile touched source and templates where the local toolchain supports it. Do not call an environment/dependency failure a pass; record the exact blocker.

## Secret/runtime scan

Before every merge/release inspect tracked changes and ensure none of these are present:

```text
.env.local
*.db
*.db-wal
*.db-shm
__pycache__/
*.pyc
var/
production media
Threads/Meta access tokens
ACCESSTRADE keys
ACP_MASTER_KEY
Shopee browser/session data
```

Never print secret values just to verify they exist.

## Browser pilot — stop before approval/publish

### 1. Phase 1 bulk direct link

1. Open `/sanpham/shopee-bulk`.
2. Paste a small batch containing valid Shopee product URLs plus one deliberately invalid URL.
3. Confirm valid rows get links while invalid row remains an item-level error.
4. Repeat the same batch and verify duplicate/idempotent behavior.
5. Do not publish anything.

### 2. Manual Shopee resolve

1. Open `/sanpham` → **Nhập link affiliate**.
2. Resolve one Shopee affiliate URL.
3. Confirm canonical product URL contains no `credential_token`.
4. Confirm the original affiliate URL is still preserved for the post flow.

### 3. Chrome Helper

1. On incomplete metadata, press **Mở Shopee & lấy thông tin**.
2. Click helper on the correct product → expected success.
3. Start a new pairing, click helper on a different Shopee product → expected rejection.
4. Switch back to the paired product within 300 seconds and retry → expected success without new token.
5. Confirm helper never requests cookies/session credentials.

### 4. Cache / product identity / price refresh

1. Confirm metadata provenance and timestamp are visible.
2. When cache is used, UI must say it is not realtime.
3. Click **Làm mới giá** once.
4. If server cannot refresh, the UI may direct to Chrome Helper/manual entry, but must not auto-click the helper.
5. Re-confirm the same item in a test DB and verify it reuses one Product row.
6. Unchanged price must not add another Shopee confirmation history row.
7. Changed price must add one sourced observation.

### 5. Phase 4 preview

1. On Shopee confirmation screen select at least one active channel.
2. Press **Xem trước bài**.
3. Verify preview includes image, product name/price, preliminary caption, disclosure, exact affiliate link, canonical product link, selected channels and metadata source.
4. Verify preview is clearly labelled **sơ bộ** and creates no Product/Post/job by itself.
5. Verify Product/Affiliate copy/open controls work.

### 6. Create draft and inspect `/duyet`

1. If desired, press the existing **Tạo bài nháp** action once.
2. Open `/duyet`.
3. Verify Shopee Direct/source badges, product link, affiliate link/copy actions, live character counter and responsive review card.
4. Verify caption edits still use the existing form and existing approve/reject routes.
5. **STOP HERE. Do not press Duyệt & lên lịch as part of release verification.**

## Observability checks

For a test product inspect `audit_log` without exposing secrets. Expected event vocabulary may include:

```text
resolve_success
canonicalized
html_metadata_success
html_captcha
json_api_403
helper_metadata_success
cache_hit
cache_stale
manual_fallback
price_refresh_success
price_refresh_failed
```

Rules:

- `entity_id` is only canonical `shop_id:item_id`;
- detail contains only allowlisted diagnostic fields;
- no full affiliate tracking URL/token/cookie/raw response;
- `html_captcha` and `json_api_403` are emitted only when explicit transport/content evidence exists.

## GitHub Actions blocker

At the time this checklist was written, GitHub Actions jobs for this account were not starting because GitHub reported:

```text
The job was not started because your account is locked due to a billing issue.
```

A job with zero runner/zero steps is an infrastructure blocker, not a test result. Use the ACP Ubuntu environment for the real gate until Actions is available again.

## Ready-to-merge checklist

```text
[ ] Phase 1 focused tests green
[ ] Phase 2 focused/helper tests green
[ ] Phase 3 cache/upsert/refresh tests green
[ ] Phase 4 observability/preview/UI tests green
[ ] pipeline tests green
[ ] pilot tests green
[ ] manager tests green
[ ] ./manage.sh test green
[ ] git diff --check clean
[ ] template/JS/JSON syntax checks clean
[ ] secret/runtime scan clean
[ ] browser pilot accepted by operator
[ ] no automated publish performed
[ ] PRs merge sequentially Phase 1 → 2 → 3 → 4
```

Do not mark the roadmap production-ready until every applicable gate above has evidence from the ACP Ubuntu environment.
