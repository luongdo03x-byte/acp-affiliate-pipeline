# Shopee Metadata Helper Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hoàn thiện và harden ACP Shopee Helper để metadata từ tab Shopee do operator chủ động mở được ghép đúng canonical product, truyền về ACP qua localhost bằng one-time pairing, tự điền confirmation form và luôn có manual fallback.

**Architecture:** Tái sử dụng `core/helper_pairing.py`, các route helper hiện có trong `web/server.py`, UI polling trong `web/templates/products.html` và Manifest V3 extension tại `tools/chrome_helper/`; không viết lại flow. Bổ sung một lớp validation thuần Python cho helper payload/product identity, bắt extension gửi URL quan sát thực tế từ tab Shopee, canonicalize + so khớp server-side trước khi consume token, rồi tăng test coverage cho pairing/replay/loopback/payload/UI/extension contract.

**Tech Stack:** Python 3, Flask 3, SQLite, Jinja2, JavaScript Manifest V3, Chrome/Chromium Extension APIs; stdlib + dependencies hiện có, không thêm frontend framework hoặc browser automation dependency.

## Global Constraints

- Base branch: `feat/shopee-bulk-affiliate`; implementation branch: `feat/shopee-metadata-helper`.
- Không hardcode hoặc đọc Shopee cookie, session token, password, `credential_token`, localStorage/sessionStorage auth data hay browser profile secret.
- Không CAPTCHA bypass, headless anti-bot bypass, Selenium/Playwright login/scrape, private API reverse engineering hoặc proxy credential injection.
- Helper là user-assisted: operator tự mở tab Shopee và tự bấm extension.
- `POST /api/helper/shopee-product` chỉ nhận loopback/localhost và one-time token TTL 300 giây.
- Pairing phải gắn đúng canonical Shopee product identity; metadata từ tab sản phẩm khác phải bị reject trước khi consume token.
- Payload helper chỉ cho phép `name`, `current_price`, `original_price`, `image_url`, `shop` và URL quan sát để xác minh identity; giới hạn kích thước/độ dài rõ ràng.
- Manual fallback luôn hoạt động.
- Flow helper không tạo draft/post/publish; việc tạo draft vẫn là action riêng của operator và dừng ở `PENDING_REVIEW`/`DRAFT` theo pipeline hiện có.
- Shopee Direct không gọi ACCESSTRADE và không đổi attribution semantics.
- Automated tests dùng mock/network-free; không publish Threads thật.
- Không sửa `.env.local`, DB live, runtime `var/`, token hoặc secret.

---

## File map

### Create

- `core/shopee_helper.py` — pure validation/canonical identity boundary cho pairing submit: canonicalize expected/observed URLs, validate allowlisted metadata fields/length/type/price/image URL, return sanitized payload.
- `tests/test_shopee_helper.py` — focused `unittest` suite cho validator, pairing lifecycle và Flask helper routes; deterministic, network-free.

### Modify

- `core/helper_pairing.py` — đổi pairing entry từ raw URL matching sang canonical identity đã validate; expose testable expiry/replay semantics nhưng giữ TTL 300 giây.
- `web/server.py` — helper issue/status/submit dùng validator, loopback guard rõ ràng, payload-size guard, observed-product verification và safe error behavior.
- `web/templates/products.html` — helper status states rõ hơn, truyền pairing cho extension như hiện tại, manual fallback không bị khóa, timeout/retry ổn định.
- `tools/chrome_helper/background.js` — extractor trả `observed_url = location.href`; submit đúng observed URL; không gửi nếu identity/tab không phù hợp; không đọc storage/cookies.
- `tools/chrome_helper/content_acp.js` — giữ relay tối thiểu `{token, productUrl, origin}`; validate ACP origin shape trước khi gửi message.
- `tools/chrome_helper/manifest.json` — giữ MV3, `activeTab`, `scripting`; host permissions chỉ Shopee + ACP localhost cần thiết.
- `tools/chrome_helper/README.md` — hướng dẫn cài unpacked extension, flow pairing, badge/status, security/non-goals, troubleshooting.
- `tests/test_pilot.py` — giữ regression contract hiện có và bổ sung static assertions để Phase 2 không làm hỏng Shopee/manual/security flow.
- `docs/ACP_RUNBOOK.md` — operator runbook cho Chrome Helper và manual fallback.

---

### Task 1: Canonical helper payload and product-identity validator

**Files:**
- Create: `core/shopee_helper.py`
- Create/Test: `tests/test_shopee_helper.py`
- Reuse: `adapters/shopee_affiliate.py`

**Interfaces:**
- Consumes: `canonical_product_url(url: str) -> str`, `external_product_id(url: str) -> str` from `adapters/shopee_affiliate.py`.
- Produces:
  - `ShopeeHelperError(ValueError)`
  - `HelperSubmission` dataclass with `expected_product_url`, `observed_product_url`, `product_id`, `metadata`.
  - `canonical_helper_product(url: str) -> tuple[str, str]`
  - `sanitize_helper_metadata(value: object) -> dict`
  - `validate_helper_submission(expected_url: str, observed_url: str, metadata: object) -> HelperSubmission`

- [ ] **Step 1: Write failing focused tests for canonical identity and allowlisted metadata**

Add tests equivalent to:

```python
class ShopeeHelperValidationTests(unittest.TestCase):
    def test_same_product_different_shopee_url_shapes_match(self):
        got = validate_helper_submission(
            "https://shopee.vn/product/123/456",
            "https://shopee.vn/Ten-san-pham-i.123.456?sp_atk=x",
            {"name": "Tai nghe", "current_price": 199000, "image_url": "https://down-vn.img.susercontent.com/file/abc"},
        )
        self.assertEqual(got.product_id, "456")
        self.assertEqual(got.observed_product_url, "https://shopee.vn/product/123/456")

    def test_different_product_is_rejected(self):
        with self.assertRaises(ShopeeHelperError):
            validate_helper_submission(
                "https://shopee.vn/product/123/456",
                "https://shopee.vn/product/123/999",
                {"name": "Sai sản phẩm"},
            )

    def test_unknown_fields_are_dropped_and_secret_like_fields_never_survive(self):
        meta = sanitize_helper_metadata({
            "name": "X",
            "current_price": "199000",
            "cookie": "forbidden",
            "token": "forbidden",
            "localStorage": "forbidden",
        })
        self.assertEqual(meta, {"name": "X", "current_price": 199000,
                                "original_price": None, "image_url": None, "shop": None})
```

Also cover: non-HTTPS/non-Shopee URL rejection, short affiliate URL rejection, OPA/slug canonicalization, negative/boolean/unreasonably large prices rejection, text length caps, image URL must be HTTPS HTTP(S) and contain no credentials/control chars.

- [ ] **Step 2: Run focused test to verify RED**

Run from directory containing package `acp/`:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'acp.core.shopee_helper'`.

- [ ] **Step 3: Implement minimal validator**

Implement constants and behavior explicitly:

```python
ALLOWED_METADATA_FIELDS = ("name", "current_price", "original_price", "image_url", "shop")
MAX_NAME_LEN = 500
MAX_SHOP_LEN = 200
MAX_IMAGE_URL_LEN = 2048
MAX_PRICE_VND = 10_000_000_000
```

`canonical_helper_product()` must accept only direct `https://shopee.vn`/`www.shopee.vn` product URLs with concrete numeric item IDs after canonicalization; reject `s.shopee.vn`, `shope.ee`, non-Shopee hosts, credentials and non-443 explicit ports.

`validate_helper_submission()` must compare canonical expected and observed URLs (or their `(shop_id,item_id)` identity), then sanitize metadata. It must never trust the expected URL supplied by the extension as proof of the active tab.

- [ ] **Step 4: Re-run focused validation tests GREEN**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

Expected: validator test cases PASS.

- [ ] **Step 5: Commit validator slice**

```bash
git add core/shopee_helper.py tests/test_shopee_helper.py
git commit -m "feat: validate Shopee helper submissions"
```

---

### Task 2: Harden one-time pairing lifecycle around canonical identity

**Files:**
- Modify: `core/helper_pairing.py`
- Test: `tests/test_shopee_helper.py`

**Interfaces:**
- Consumes: `canonical_helper_product()` from Task 1.
- Produces/keeps:
  - `issue(product_url: str) -> dict`
  - `submit(token: str, observed_product_url: str, metadata: dict) -> bool`
  - `poll(token: str) -> dict | None`
  - `reset() -> None`
  - TTL remains exactly `300` seconds.

- [ ] **Step 1: Add failing tests for canonical pairing, replay and expiry**

Add cases:

```python
def test_pairing_accepts_same_product_slug_and_canonical_shapes(self):
    issued = helper_pairing.issue("https://shopee.vn/product/123/456")
    self.assertTrue(helper_pairing.submit(
        issued["token"],
        "https://shopee.vn/Tai-nghe-i.123.456?tracking=x",
        {"name": "Tai nghe", "current_price": 199000},
    ))


def test_product_mismatch_does_not_consume_token(self):
    issued = helper_pairing.issue("https://shopee.vn/product/123/456")
    self.assertFalse(helper_pairing.submit(
        issued["token"], "https://shopee.vn/product/123/999", {"name": "Sai"}))
    self.assertTrue(helper_pairing.submit(
        issued["token"], "https://shopee.vn/product/123/456", {"name": "Đúng"}))
```

Keep existing assertions: token is one-time, second valid submit fails, expired token disappears, ready metadata remains pollable until TTL cleanup.

- [ ] **Step 2: Run test and verify expected RED on raw URL equality**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

Expected: same product with different URL shape fails before implementation.

- [ ] **Step 3: Implement canonical identity storage and submit validation**

At `issue()`, canonicalize once and store canonical URL/product identity. At `submit()`, validate observed URL + metadata before setting `consumed=True`. A mismatch/invalid payload must return `False` without consuming the token.

Do not persist token or metadata to DB in Phase 2; keep current in-memory, short-TTL semantics.

- [ ] **Step 4: Re-run focused tests GREEN**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

- [ ] **Step 5: Commit pairing hardening**

```bash
git add core/helper_pairing.py tests/test_shopee_helper.py
git commit -m "fix: bind Shopee helper pairing to canonical product"
```

---

### Task 3: Harden Flask helper routes and loopback boundary

**Files:**
- Modify: `web/server.py`
- Test: `tests/test_shopee_helper.py`
- Regression: `tests/test_pilot.py`

**Interfaces:**
- Existing routes remain:
  - `POST /sanpham/affiliate/helper/token` — authenticated dashboard + CSRF, returns `{token, expires_in}`.
  - `GET /sanpham/affiliate/helper/status?token=...` — authenticated dashboard polling.
  - `POST /api/helper/shopee-product` — extension submit; no login cookie; loopback + one-time token protected.
- Submit body becomes:

```json
{
  "token": "<one-time token>",
  "product_url": "https://shopee.vn/product/123/456",
  "observed_url": "https://shopee.vn/Ten-san-pham-i.123.456?...",
  "metadata": {
    "name": "...",
    "current_price": 199000,
    "original_price": 299000,
    "image_url": "https://...",
    "shop": "..."
  }
}
```

`product_url` remains for compatibility/debug contract but server trusts the token-bound expected identity plus `observed_url`, not extension-provided expected identity.

- [ ] **Step 1: Add failing route tests**

Cover exactly:

```python
# dashboard endpoints require login/CSRF
self.assertEqual(client.post("/sanpham/affiliate/helper/token", data={"product_url": valid}).status_code, 302)

# helper submit must reject non-loopback
with app.test_request_context("/api/helper/shopee-product", method="POST",
                              environ_base={"REMOTE_ADDR": "203.0.113.9"},
                              json=payload):
    response = app.full_dispatch_request()
    self.assertEqual(response.status_code, 403)

# wrong observed product -> 410/4xx and token remains usable for correct observed product
# unknown metadata fields never appear in polled result
# oversized/malformed JSON -> 400/413
# replay of same token -> 410
```

Use the existing login flow to obtain CSRF for authenticated token/status tests. Never hit real Shopee/network.

- [ ] **Step 2: Run route tests RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

Expected: observed-product and payload-size cases fail against current route contract.

- [ ] **Step 3: Implement route hardening**

Add a small explicit helper for loopback check using `ipaddress.ip_address(request.remote_addr).is_loopback`; reject missing/invalid remote addresses. Enforce JSON object request and a conservative content length cap (for example 16 KiB) before parsing/accepting metadata. Pass `observed_url` to pairing validation. Do not log the token, full affiliate URL, cookies or raw payload.

Keep `/api/helper/` unauthenticated by dashboard session only because extension lacks the session cookie; protection remains loopback + one-time token + product identity.

- [ ] **Step 4: Run focused and pilot helper regressions**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pilot
```

Expected: focused helper tests PASS; existing helper/web security checks in pilot remain PASS.

- [ ] **Step 5: Commit route hardening**

```bash
git add web/server.py tests/test_shopee_helper.py tests/test_pilot.py
git commit -m "fix: harden Shopee helper localhost endpoint"
```

---

### Task 4: Make extension prove the observed Shopee product

**Files:**
- Modify: `tools/chrome_helper/background.js`
- Modify: `tools/chrome_helper/content_acp.js`
- Modify: `tools/chrome_helper/manifest.json`
- Test: `tests/test_shopee_helper.py` (static extension contract checks)

**Interfaces:**
- `extractShopeeMetadata()` returns metadata plus `observed_url: location.href` to the service worker.
- Background POST sends both token-bound `product_url` and DOM-observed `observed_url`.
- Content script relays only `{token, productUrl, origin}` from ACP page.

- [ ] **Step 1: Add failing static contract tests**

Read extension files as text and assert:

```python
self.assertIn("observed_url", background_js)
self.assertIn("location.href", background_js)
self.assertNotIn("chrome.cookies", background_js + content_js)
self.assertNotIn("localStorage", executable_lines_without_comments)
self.assertNotIn("sessionStorage", executable_lines_without_comments)
```

Parse `manifest.json` and assert `manifest_version == 3`, permissions are exactly/minimally `activeTab` + `scripting`, and no `cookies`, `webRequest`, `debugger`, `storage` or `<all_urls>` permission exists.

- [ ] **Step 2: Run static test RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

Expected: missing `observed_url` assertion fails.

- [ ] **Step 3: Update background/content scripts minimally**

`extractShopeeMetadata()` should return:

```javascript
return {
  observed_url: location.href,
  metadata: {
    name: ...,
    current_price: ...,
    original_price: ...,
    image_url: ...,
    shop: ...,
  }
};
```

Before POST, require an HTTPS `shopee.vn` tab URL. POST:

```javascript
body: JSON.stringify({
  token: pairing.token,
  product_url: pairing.productUrl,
  observed_url: extracted.observed_url,
  metadata: extracted.metadata,
})
```

Clear pairing in `finally` after one submit attempt, as current code does. Do not introduce cookies/storage/session access or navigation automation.

In `content_acp.js`, only accept `location.origin` matching the manifest-allowed localhost origins before relaying pairing.

- [ ] **Step 4: Re-run focused static contract GREEN**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

- [ ] **Step 5: Commit extension identity proof**

```bash
git add tools/chrome_helper/background.js tools/chrome_helper/content_acp.js tools/chrome_helper/manifest.json tests/test_shopee_helper.py
git commit -m "fix: verify active Shopee product in Chrome helper"
```

---

### Task 5: Complete metadata states and helper/manual UI behavior

**Files:**
- Modify: `web/templates/products.html`
- Modify only if required: `adapters/shopee_affiliate.py`
- Test: `tests/test_shopee_helper.py`
- Regression: `tests/test_pilot.py`

**Interfaces:**
- Metadata states remain `AUTO_COMPLETE`, `AUTO_PARTIAL`, `BROWSER_HELPER_REQUIRED`; add/use `MANUAL_REQUIRED` only as a UI state for helper unavailable/expired/repeated failure without changing required product fields.
- Required confirmation fields remain name, current price > 0, image URL; shop/original price optional.

- [ ] **Step 1: Add failing template/state contract tests**

Assert confirmation HTML contains all four state labels/paths, helper button is rendered for `AUTO_PARTIAL` and `BROWSER_HELPER_REQUIRED`, and manual inputs remain enabled regardless of helper outcome.

Add a state helper test such as:

```python
self.assertEqual(metadata_state(ProductMetadata(name="x", current_price=1, image_url="https://img/x")), AUTO_COMPLETE)
self.assertEqual(metadata_state(ProductMetadata(name="x", current_price=1)), AUTO_PARTIAL)
self.assertEqual(metadata_state(ProductMetadata()), BROWSER_HELPER_REQUIRED)
```

For `MANUAL_REQUIRED`, keep it presentation-driven after helper timeout/failure; do not make server metadata resolver invent a fourth automatic state without evidence.

- [ ] **Step 2: Run tests and capture RED for missing MANUAL UI path**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

- [ ] **Step 3: Implement minimal UI behavior**

Refactor inline helper JS only enough to expose named functions/state transitions:

```text
idle → waiting → received
             ↘ expired/manual
             ↘ failed/manual
```

On timeout/error, show `Không thể lấy thông tin tự động. Vui lòng nhập các trường bắt buộc.` and re-enable helper retry button; never disable manual form inputs.

On ready, fill only non-empty allowlisted fields, update image preview safely, show `✓ Đã nhận thông tin từ Chrome`, and leave operator confirmation required.

- [ ] **Step 4: Run focused + pilot tests GREEN**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pilot
```

- [ ] **Step 5: Commit UI state slice**

```bash
git add web/templates/products.html adapters/shopee_affiliate.py tests/test_shopee_helper.py tests/test_pilot.py
git commit -m "feat: complete Shopee helper metadata states"
```

---

### Task 6: Operator docs and deterministic browser-helper pilot checklist

**Files:**
- Modify: `tools/chrome_helper/README.md`
- Modify: `docs/ACP_RUNBOOK.md`
- Modify: `README.md` only if the top-level operator entry point lacks Chrome Helper reference.
- Test: `tests/test_shopee_helper.py` for presence of required manifest/assets only; no live browser automation.

**Interfaces:** None.

- [ ] **Step 1: Document install and pairing flow exactly**

Document Chrome/Chromium steps:

```text
chrome://extensions
→ Developer mode ON
→ Load unpacked
→ chọn tools/chrome_helper
```

Then ACP flow:

```text
/sanpham?mode=affiliate
→ Phân tích link
→ Mở Shopee & lấy thông tin
→ tab Shopee mở
→ chờ trang render bình thường
→ bấm icon ACP Shopee Helper
→ quay lại ACP
→ thấy ✓ Đã nhận thông tin từ Chrome
→ kiểm tra/chỉnh metadata
→ Tạo bài nháp
→ /duyet
```

Document badges `✓`, `×`, `?`, `!`, TTL 5 phút, retry/manual fallback, and security boundary (no cookie/session/password/localStorage auth, no CAPTCHA bypass, no auto publish).

- [ ] **Step 2: Add manual controlled pilot checklist**

Checklist must verify one real Shopee product only:

```text
[ ] ACP is local on an allowed localhost origin
[ ] extension loaded unpacked
[ ] helper token issued only after authenticated + CSRF-protected click
[ ] correct Shopee tab receives metadata
[ ] switching to a different product before clicking helper is rejected
[ ] replaying the same pairing cannot submit twice
[ ] expired pairing requires retry
[ ] manual entry still works when helper fails
[ ] no post/publish is created by helper submission itself
```

No automated real Shopee request/publish in this task.

- [ ] **Step 3: Run focused tests**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

- [ ] **Step 4: Commit docs**

```bash
git add tools/chrome_helper/README.md docs/ACP_RUNBOOK.md README.md tests/test_shopee_helper.py
git commit -m "docs: add Shopee helper operator workflow"
```

---

### Task 7: Phase 2 full verification and stacked PR handoff

**Files:**
- No production changes unless a failing test exposes a Phase 2 regression.
- Update this plan only with factual verification notes if desired.

**Interfaces:** None.

- [ ] **Step 1: Run focused helper suite**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
```

Expected: all Phase 2 tests PASS.

- [ ] **Step 2: Run Phase 1 Shopee regression**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_bulk_affiliate
```

Expected: Phase 1 bulk behavior unchanged.

- [ ] **Step 3: Run pipeline and pilot regression**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pipeline
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pilot
```

Expected: PASS; no live adapter/network/publish.

- [ ] **Step 4: Run manager/release verification**

From package root:

```bash
python tests/test_manage.py
git diff --check
./manage.sh test
```

Expected: PASS. If GitHub Actions is still blocked by account billing, this Ubuntu output is the merge gate; report the exact blocker rather than claiming CI pass.

- [ ] **Step 5: Inspect tracked files and secret/runtime safety**

```bash
git status --short
git diff feat/shopee-bulk-affiliate...HEAD --stat
git diff feat/shopee-bulk-affiliate...HEAD --check
git ls-files | grep -E '(^|/)(\.env\.local|.*\.db(-wal|-shm)?|__pycache__|.*\.pyc)$' && exit 1 || true
```

Expected: only Phase 2 source/tests/docs; no secret/runtime artifacts.

- [ ] **Step 6: Perform the controlled browser pilot**

Use one product and the checklist from Task 6. Do not click approve/publish as part of helper verification. Record pass/fail manually.

- [ ] **Step 7: Create stacked PR**

Target:

```text
base: feat/shopee-bulk-affiliate
head: feat/shopee-metadata-helper
```

PR body must include exact test outputs, browser pilot status, known environment blockers, security/non-goals, and no claim of release readiness until all required Ubuntu gates are green.

---

## Phase 2 Acceptance Criteria

- [ ] Existing helper implementation is reused rather than duplicated.
- [ ] Pairing token expires after 300 seconds and can submit successfully only once.
- [ ] Token is bound to canonical Shopee product identity.
- [ ] Extension supplies `observed_url` from `location.href`; server rejects a different product even if extension also sends the expected `product_url`.
- [ ] Product mismatch/invalid payload does not consume a still-valid token.
- [ ] Helper submit endpoint accepts loopback only and enforces bounded JSON payload.
- [ ] Only five metadata fields survive sanitization; secret-like/unknown fields are dropped.
- [ ] Extension permissions remain minimal and include no cookie/storage/debugger/all-urls access.
- [ ] `AUTO_COMPLETE`, `AUTO_PARTIAL`, `BROWSER_HELPER_REQUIRED`, and manual fallback behavior are clear in UI.
- [ ] Helper timeout/failure never blocks manual confirmation.
- [ ] Helper submission alone creates no Product/Post/Publish job.
- [ ] Existing exact/prebuilt affiliate semantics and Phase 1 bulk link behavior remain unchanged.
- [ ] Focused Shopee helper, Phase 1 Shopee, pipeline, pilot, manager and release tests pass in ACP Ubuntu environment.
- [ ] `git diff --check` is clean and no secrets/runtime artifacts are tracked.
- [ ] Controlled browser pilot verifies correct-product accept + wrong-product reject + replay/expiry/manual fallback.
