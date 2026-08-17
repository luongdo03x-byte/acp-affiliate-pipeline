# ACP — Shopee Metadata Helper Runbook

Date: 2026-08-17
Scope: Phase 2 browser-assisted Shopee metadata only.

## Safety boundary

This pilot verifies metadata transfer, not publishing. Do not approve or publish a
Threads post as part of the helper pilot.

The helper must never read or transfer Shopee cookies, login/session tokens,
passwords, browser profile secrets, `localStorage`/`sessionStorage` auth data, or
reuse `credential_token`. It does not bypass CAPTCHA and does not automate Shopee.

## Pre-flight

From the active ACP checkout:

```bash
cd ~/Downloads/ACP/acp
export ACP_ADAPTER=mock
export ACP_SOURCE=mock

python -m acp.tests.test_shopee_helper -v
python -m acp.tests.test_shopee_helper_ui -v
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot
python tests/test_manage.py
./manage.sh test
```

Do not continue to browser pilot if a real regression fails. Environment blockers
must be recorded explicitly rather than treated as a pass.

## Install/reload extension

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Load unpacked: `tools/chrome_helper/`.
4. If already installed, press Reload after updating the branch.
5. ACP itself must be opened on `http://127.0.0.1:5000` or
   `http://localhost:5000`; public/ngrok helper submit is intentionally rejected.

## Controlled one-product pilot

Use one normal Shopee product/affiliate link that you are allowed to access.

### A. Correct product acceptance

1. ACP `/sanpham` → **Nhập link affiliate**.
2. Resolve one link and reach confirmation.
3. If metadata is incomplete, press **Mở Shopee & lấy thông tin**.
4. Wait for the Shopee page to render.
5. On that exact product tab, click ACP Shopee Helper.
6. Expected extension badge: `✓`.
7. Return to ACP.
8. Expected: received-from-Chrome status; available metadata fields filled.
9. Verify name/price/image/shop against the visible Shopee page before continuing.

### B. Wrong-product rejection without burning token

1. Start a fresh helper pairing for Product A.
2. Before clicking the extension, switch to a different Shopee Product B.
3. Click the extension.
4. Expected: red `×`; ACP does not accept Product B metadata.
5. Switch back to Product A while still inside the 300-second TTL.
6. Click the extension again.
7. Expected: `✓`; Product A metadata is accepted without issuing a new token.

### C. Replay behavior

After a successful submit, the extension clears its completed in-memory pairing.
Clicking again without a new ACP pairing should show `?` and must not create or
modify a Product/Post. Server-side unit tests additionally assert replay of a
consumed token is rejected.

### D. Expiry/manual fallback

1. Start a helper pairing and do not submit until its 300-second TTL expires, or
   exercise the expiry unit test instead of waiting during routine development.
2. Expected ACP UI: helper timeout transitions to `MANUAL_REQUIRED`.
3. Verify manual name/current price/image inputs are still editable.
4. Fill/adjust data manually if desired.
5. Do not approve/publish during this pilot.

## Expected security behavior

- Non-loopback request → HTTP 403.
- Public request forwarded through ProxyFix/ngrok → HTTP 403.
- Direct remote peer spoofing `X-Forwarded-For: 127.0.0.1` → HTTP 403.
- Malformed JSON → HTTP 400.
- Payload > 16 KiB → HTTP 413.
- Invalid/wrong observed product → 4xx; valid token remains usable until a
  successful matching submit or TTL expiry.
- Consumed/replayed token → HTTP 410.
- Unknown metadata fields are dropped and never returned by polling.

## Publish/data assertions

During helper issue/submit/poll alone:

```text
no Product creation
no Post creation
no PUBLISH_POST job
no publisher call
no ACCESSTRADE tracking-link call
```

Creating a draft remains a separate operator action and follows the existing
`PENDING_REVIEW`/`DRAFT` state machine.

## Troubleshooting

### Badge `?`

ACP pairing was not relayed or the MV3 service worker restarted. Return to ACP,
press **Mở Shopee & lấy thông tin** again, then click the extension.

### Badge `!`

The active tab is not `https://shopee.vn/...`.

### Badge `×`

Check in this order:

1. active tab is the intended Shopee product;
2. pairing is younger than 300 seconds;
3. ACP is reachable at localhost/127.0.0.1 port 5000;
4. extension was reloaded after code update;
5. product page rendered usable metadata.

Do not solve `×` by adding cookies, copying browser sessions, disabling security
checks, or automating CAPTCHA.

## Browser-pilot record

Record factual result only:

```text
Date:
Branch/SHA:
Browser:
Correct product: PASS/FAIL
Wrong product rejected: PASS/FAIL
Retry on correct product: PASS/FAIL
Replay/expiry: PASS/FAIL
Manual fallback: PASS/FAIL
No publish action performed: YES/NO
Notes/blockers:
```
