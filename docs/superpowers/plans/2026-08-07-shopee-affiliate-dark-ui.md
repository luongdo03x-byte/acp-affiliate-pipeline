# Shopee Affiliate Import + Dark Premium UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm luồng nhập link affiliate Shopee có sẵn vào `/sanpham`, giữ nguyên link, dừng ở `PENDING_REVIEW`, đồng thời redesign toàn bộ dashboard ACP 2.0 theo giao diện Dark Premium đã duyệt.

**Architecture:** Giữ Flask/Jinja2/SQLite hiện tại. Tách outbound HTTP an toàn vào `adapters/safe_http.py`, logic Shopee direct vào `adapters/shopee_affiliate.py`, refactor pipeline để nhận prebuilt affiliate link, sau đó thêm hai POST route server-rendered và một design system CSS dùng chung ở `web/static/acp.css`. Không migration schema ở P0 và không thay state machine publish.

**Tech Stack:** Python 3, Flask 3, SQLite, requests, Pillow, Jinja2, HTML/CSS thuần.

## Global Constraints

- Không đọc/in/commit `.env.local`, DB live, `var/`, token/key/secrets.
- Automated verification giữ adapter mock và không publish Threads thật.
- Shopee direct không gọi ACCESSTRADE, không tự chèn `sub1=post_id`.
- `post.affiliate_link` giữ nguyên chính xác link operator nhập.
- Flow import/create dừng ở `PENDING_REVIEW` hoặc `DRAFT` khi content validator báo lỗi; không enqueue `PUBLISH_POST`.
- URL fetch phải chống SSRF: validate scheme/host/DNS/IP, manual redirects, giới hạn redirects/bytes/content type.
- Không browser automation/headless/CAPTCHA bypass.
- Không thêm React/Vue/Tailwind/chart library/build pipeline.
- Không đổi route hiện có, auth, CSRF, queue/scoring semantics.
- Dark Premium dùng stylesheet chung `web/static/acp.css`, không vá rời rạc từng trang.
- TDD: test phải fail đúng lý do trước production code.

---

### Task 1: Safe HTTP + Shopee URL resolution

**Files:**
- Create: `adapters/safe_http.py`
- Create: `adapters/shopee_affiliate.py`
- Modify/Test: `tests/test_pilot.py`

**Interfaces:**
- Produces `SafeHttpClient`, `SafeHttpError`, `SafeHttpResponse`, `AffiliateUrlResolver`, `AffiliateImportError`, `ResolvedAffiliateUrl`.

- [ ] Add failing tests for allowed Shopee hosts, rejected schemes/hosts/private IPs, manual redirect, redirect outside allowlist, redirect cap, response size/content type.
- [ ] Run `python -m acp.tests.test_pilot` from package parent and verify RED.
- [ ] Implement `SafeHttpClient.validate_url()` and `get()` with `allow_redirects=False`, `Session.trust_env=False`, DNS/IP checks, size/content-type limits.
- [ ] Implement `AffiliateUrlResolver` for `shopee.vn` and `s.shopee.vn`.
- [ ] Re-run pilot suite GREEN.

### Task 2: Shopee metadata parser + manual normalized product + safe image fetch

**Files:**
- Modify: `adapters/base.py`
- Modify: `adapters/shopee_affiliate.py`
- Create: `tests/fixtures/shopee_product_jsonld.html`
- Create: `tests/fixtures/shopee_product_og.html`
- Modify/Test: `tests/test_pilot.py`

**Interfaces:**
- Adds `RawProduct.image_path_local: Optional[str]`.
- Produces `ProductMetadata`, `ConfirmedProductInput`, `ProductMetadataResolver`, `ManualShopeeSource`.

- [ ] Add failing JSON-LD/OpenGraph/fallback ID/price/missing-field tests.
- [ ] Add failing image content-type/private-IP/valid-Pillow tests.
- [ ] Verify RED.
- [ ] Implement HTMLParser + JSON-LD/OpenGraph parser without new dependency.
- [ ] Implement canonical product URL/item ID fallback hash.
- [ ] Implement manual product normalization and safe image materialization under `var/media/source/`.
- [ ] Re-run pilot suite GREEN.

### Task 3: Refactor single-product pipeline for prebuilt affiliate links

**Files:**
- Modify: `core/pipeline.py`
- Modify/Test: `tests/test_pilot.py`

**Interfaces:**
- Produces `_create_post_from_raw_product(...)` and `create_post_from_manual_affiliate_product(...)`.

- [ ] Add failing manual-pipeline test proving exact link preservation, `shopee_direct/prebuilt` JSON attribution, no fake sub1, no publish job/thread_id.
- [ ] Verify RED.
- [ ] Persist `image_path_local` in product upsert SQL.
- [ ] Extract shared post-building helper.
- [ ] Make existing `create_post_for_product()` delegate with unchanged TikTok/mock behavior.
- [ ] Add manual public API that never calls `create_tracking_link()`.
- [ ] Run core + pilot suites GREEN.

### Task 4: `/sanpham` Shopee direct web flow

**Files:**
- Modify: `web/server.py`
- Modify: `web/templates/products.html`
- Modify/Test: `tests/test_pilot.py`

**Interfaces:**
- Produces `POST /sanpham/affiliate/resolve`, `POST /sanpham/affiliate/create`, `mode=search|affiliate`.

- [ ] Add failing web tests for login/CSRF, affiliate tab, confirm screen, required fields, exact-link persistence, no ACCESSTRADE/publish side effects.
- [ ] Verify RED/404.
- [ ] Add `SHOPEE_SOURCE_FACTORY` test seam.
- [ ] Make affiliate GET mode avoid `factory.get_source()` entirely.
- [ ] Implement resolve route: invalid link stops; metadata failure falls back to editable confirmation.
- [ ] Implement create route with server-side revalidation, active channel validation, safe image fetch, storage-only context, redirect `/duyet` on success.
- [ ] Update products template with two tabs and confirmation form; no `Đăng ngay`.
- [ ] Re-run pilot suite GREEN.

### Task 5: Dark Premium shared design system

**Files:**
- Create: `web/static/acp.css`
- Modify: `web/templates/base.html`
- Modify: `web/templates/login.html`
- Modify: `web/templates/dashboard.html`
- Modify: `web/templates/products.html`
- Modify: `web/templates/review.html`
- Modify: `web/templates/channels.html`
- Modify: `web/templates/ops.html`
- Modify: `web/templates/scoring.html`
- Modify/Test: `tests/test_pilot.py`

**Interfaces:**
- Produces reusable CSS classes `.app-shell`, `.sidebar`, `.page-header`, `.card`, `.kpi-grid`, `.data-table`, `.tabs`, `.btn`, `.status-badge`, `.review-card`, responsive breakpoints.

- [ ] Add failing render checks that `/`, `/sanpham`, `/duyet`, `/kenh`, `/vanhanh`, `/chamdiem` contain the new stylesheet/class markers and `/sanpham` contains no `Đăng ngay`.
- [ ] Verify RED.
- [ ] Move shared styling from `base.html` to `web/static/acp.css`; preserve Jinja globals/CSRF/nav routes.
- [ ] Redesign sidebar/header/cards/tables/forms/buttons/status chips with the approved dark palette.
- [ ] Refactor each page from inline presentation to shared classes while preserving form actions/field names.
- [ ] Ensure responsive behavior at 960px/720px and focus-visible/reduced-motion rules.
- [ ] Re-run pilot suite GREEN.

### Task 6: Edge hardening, docs, full regression, artifact output

**Files:**
- Modify/Test as needed: `tests/test_pilot.py`, `adapters/safe_http.py`, `adapters/shopee_affiliate.py`, `core/pipeline.py`, `web/server.py`
- Modify: `README.md`, `docs/ACP_RUNBOOK.md`
- Add: `docs/superpowers/specs/2026-08-07-shopee-affiliate-link-import-design.md`
- Add: `docs/superpowers/specs/2026-08-07-acp-dark-premium-ui-design.md`
- Add: `docs/superpowers/plans/2026-08-07-shopee-affiliate-dark-ui.md`

- [ ] Add edge tests: malformed JSON-LD fallback, empty metadata, duplicate product upsert, attribution JSON separation, redirect/content/image limits.
- [ ] Run syntax compile and `git diff --check` equivalent on generated tree.
- [ ] Run `python -m acp.tests.test_pipeline`.
- [ ] Run `python -m acp.tests.test_pilot`.
- [ ] Run `python tests/test_manage.py`.
- [ ] Update README/runbook with `/sanpham → Nhập link affiliate → Phân tích → Xác nhận → Tạo bài nháp → /duyet` and Dark Premium notes.
- [ ] Produce a patch against the baseline snapshot and a ready-to-copy ZIP; no secrets/runtime files included.

## Acceptance

```text
✓ Existing search/product flow still works
✓ Shopee direct tab resolves safely and always confirms metadata
✓ Metadata missing can be filled manually
✓ Exact affiliate link is preserved
✓ No ACCESSTRADE call in manual flow
✓ No fake sub1/post attribution
✓ Post stops at PENDING_REVIEW/DRAFT and no publish job exists
✓ Dashboard pages share Dark Premium design system
✓ Responsive/accessibility basics are present
✓ No frontend framework added
✓ Core/pilot/manager suites all pass
✓ Patch/ZIP contains no secrets/runtime DB/var/.venv
```
