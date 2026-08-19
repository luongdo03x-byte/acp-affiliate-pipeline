# ACP Facebook Seeding Assistant

Chrome Manifest V3 extension for ACP manual task intake + Facebook Profile routing.

## Current execution model

Each logged-in Facebook account uses a separate Chrome Profile. The extension stores a stable `extensionInstanceId` in that profile and pairs it to ACP with an operator label such as `FB01`.

ACP can then map:

```text
Task A2GR-64
Account slot 1 → FB01
Account slot 2 → FB02
Account slot 3 → FB03
```

Each profile only receives work for its mapped slot.

## Safety model

- Only processes Facebook target URLs explicitly supplied by the operator.
- Does not discover targets, create/rotate accounts, store Facebook password/cookies/session in ACP, bypass checkpoint/CAPTCHA, spoof fingerprints, or rotate proxies.
- The multi-profile flow does **not** click Like or Submit.
- Main comments are filled only when the target composer is unambiguous.
- For replies, the operator clicks **Reply** under the intended Facebook comment first; the extension remembers that Facebook composer and fills only that selected composer.
- Operator manually presses Facebook **Post/Đăng**.
- ACP records `DONE` only after the filled composer clears and the final text is observed in the target Facebook article.
- Final edited text is revalidated server-side for forbidden words and exact/near duplicates.
- Facebook checkpoint/rate restriction remains a hard stop.

## Local setup

1. Start ACP:

   ```bash
   cd ~/Downloads/ACP
   ./manage.sh start
   ```

2. Configure `shared/.env.local`:

   ```text
   ACP_SEEDING_EXTENSION_TOKEN=<random-local-token>
   ACP_CAPTION_LLM=gemini
   ACP_GEMINI_API_KEY=<gemini-key>
   ```

3. Open `chrome://extensions`, enable Developer mode and **Load unpacked**:

   ```text
   extensions/facebook-seeding-assistant/
   ```

4. In each Chrome Profile, open Facebook and fill the ACP panel:

   ```text
   Account label: FB01
   ACP URL:       http://127.0.0.1:5000
   Token:         ACP_SEEDING_EXTENSION_TOKEN
   ```

5. Repeat for FB02/FB03 as needed.

6. Create the task at `/seeding`, then map the connected accounts at `/seeding/accounts`.

The extension checks for newly assigned work again while IDLE, so an already-open profile can pick up a task after mapping without Facebook credentials being sent to ACP.

## Operator flow

```text
pair Chrome Profiles
→ create task
→ map accounts
→ profile opens target
→ operator Like + confirm if required
→ ACP reads post + generates distinct plan
→ profile gets its own MAIN/REPLY slot
→ extension fills composer only
→ operator presses Facebook Post
→ ACP verifies + records final text
→ when all mapped accounts finish: report B/C/D / optional Google Sheet
```

Google Sheets setup: `docs/SEEDING_SHEET_SETUP.md`.

## Development tests

```bash
node --test extensions/facebook-seeding-assistant/tests/*.test.cjs
```

These tests do not open Facebook or publish anything.
