# Shopee Affiliate Phase 4 Polish & Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hoàn thiện preview/review UX, audit Shopee source có bằng chứng nhưng không lộ secret/full tracking URL, và đóng gói release verification cho toàn bộ Shopee Affiliate roadmap.

**Architecture:** Giữ nguyên legacy routes/state machine. Thêm `core/shopee_observability.py` cho audit sanitization, một web composition module `web/shopee_polish.py` cho preview/context/instrumentation và các static assets riêng để polish confirmation/review mà không thêm frontend framework. Preview là non-persisted preliminary preview; final draft vẫn do pipeline hiện có tạo/validate và `/duyet` vẫn là approval gate duy nhất.

**Tech Stack:** Python 3, Flask 3, SQLite, Jinja2, vanilla JS/CSS, requests/Pillow hiện có; không thêm dependency.

## Global Constraints

- Base: `feat/shopee-product-intel`; branch: `feat/shopee-affiliate-polish`.
- Preview không tạo Product/Post/job và phải ghi rõ caption preview là sơ bộ nếu không dùng chính pipeline variant cuối.
- Không thay approve/reject/publish state machine.
- Không log full affiliate tracking URL, pairing token, cookie/session/browser secret hoặc raw provider response.
- Audit chỉ phát `html_captcha`/`json_api_403` khi response/exception thực sự chứng minh category đó; không đoán.
- Shopee Direct không gọi/wrap ACCESSTRADE.
- Không CAPTCHA bypass/headless/private API reverse engineering.
- Automated verification mock-first; không publish Threads.

---

### Task 1: Safe Shopee audit events

**Files:** create `core/shopee_observability.py`; create `tests/test_shopee_observability.py`.

**Interfaces:** `record_shopee_event(conn, product_url, action, *, detail=None, actor="system")`; allow actions `resolve_success`, `canonicalized`, `html_metadata_success`, `html_captcha`, `json_api_403`, `helper_metadata_success`, `cache_hit`, `cache_stale`, `manual_fallback`, `price_refresh_success`, `price_refresh_failed`. Entity id is only `<shop_id>:<item_id>`. Detail allowlist: `source`, `state`, `error_category`, `http_status`, `metadata_fields`, `price_changed`.

- [ ] Write failing tests proving URL/token/cookie/link keys are discarded and audit contains only canonical identity + allowlisted detail.
- [ ] Implement minimal recorder using existing `db.audit()` and `identity_from_url()`.
- [ ] Re-run focused tests and commit `feat: add sanitized Shopee observability`.

### Task 2: Evidence-based HTTP/metadata instrumentation

**Files:** create/modify `web/shopee_polish.py`; test `tests/test_shopee_observability.py`.

- [ ] Test observing HTTP wrapper: API `SafeHttpError("Upstream HTTP 403")` => `json_api_403`; HTML containing explicit captcha marker => `html_captcha`; ordinary network error must not emit either.
- [ ] Instrument the underlying `ManualShopeeSource` inside current cache-aware source factory without changing permissions/cookies/proxies. Wrap `_html_metadata` to emit `html_metadata_success` only when parser returns usable metadata.
- [ ] Add after-request audit for helper success, cache hit/stale, manual fallback, refresh success/fail and resolve/canonicalized when canonical product URL is actually available.
- [ ] Commit `feat: instrument Shopee metadata flow`.

### Task 3: Non-persisted confirmation preview

**Files:** modify `web/shopee_polish.py`; create `tests/test_shopee_preview.py`.

**Route:** `POST /sanpham/affiliate/preview`, authenticated + CSRF. Inputs are existing confirmation fields plus selected `channel_codes`. Output: validated image URL, name, prices, preliminary safe caption, disclosure, exact affiliate link, canonical product link, selected channel display names, metadata source/warnings. No DB writes.

- [ ] Test preview response and assert Product/Post/job counts remain unchanged.
- [ ] Test invalid URLs/price/image/channel return safe 4xx.
- [ ] Implement a deterministic factual preliminary caption (no claimed personal experience), <=500 chars, explicitly label response `preliminary=true`.
- [ ] Commit `feat: add Shopee confirmation preview`.

### Task 4: Confirmation/review polish

**Files:** create `web/static/shopee_polish.js`, `web/static/shopee_polish.css`; modify `web/templates/base.html`; test `tests/test_shopee_polish_ui.py`.

- [ ] Confirmation JS adds `Xem trước bài` action and renders image/name/price/caption/disclosure/links/channels/source/warnings from preview endpoint; never submits create automatically.
- [ ] Add authenticated `GET /api/review/shopee-context?post_id=...` returning safe product/affiliate context for review cards; no secret/full audit logging.
- [ ] Review JS derives post id from existing approve form, adds affiliate/source badges, safe product/affiliate links + copy actions, live caption counter, and Threads-like preview styling.
- [ ] CSS improves image sizing/mobile/focus/primary action without changing forms/routes.
- [ ] Commit `feat: polish Shopee confirmation and review UX`.

### Task 5: Release hardening/docs

**Files:** create `docs/SHOPEE_AFFILIATE_FULL_RELEASE_CHECKLIST.md`; regression tests as needed.

- [ ] Run focused Phase 1–4 suites, pipeline, pilot, manager, `./manage.sh test`, Jinja parse, JS/JSON syntax, `git diff --check`, secret/runtime scan.
- [ ] Browser pilot: bulk link → resolve → helper/cache/manual → preview → create draft → inspect `/duyet`; stop before approval/publish unless explicitly approved separately.
- [ ] Record exact pass/fail/blockers; GitHub billing lock/environment failures are blockers, never passes.
- [ ] Open Draft stacked PR base `feat/shopee-product-intel`.

## Acceptance

- Preview contains requested fields and persists nothing.
- `/duyet` gains live count, affiliate/source badges, product/copy links and responsive polish without changing state machine.
- Audit events contain canonical identity only; no full affiliate URL/secrets.
- CAPTCHA/403 categories emitted only from explicit evidence.
- Phase 1–4 regression gates and browser pilot are truthfully recorded before merge/release.
