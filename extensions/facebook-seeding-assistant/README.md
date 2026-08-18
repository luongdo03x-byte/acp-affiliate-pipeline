# ACP Facebook Seeding Assistant

Chrome Manifest V3 extension for the ACP `/seeding` workspace.

## Safety model

- Processes only Facebook target URLs that were explicitly imported into ACP.
- Does not discover groups/posts, rotate accounts, store Facebook cookies, bypass checkpoints, solve CAPTCHA, spoof fingerprints, or rotate proxies.
- `AUTO_READY` comes from ACP. The extension still downgrades to review if the DOM is ambiguous.
- Global pause is re-checked immediately before a submit action.
- A submit control is clicked at most once. If the comment cannot be verified afterward, the target is recorded as `UNKNOWN` and automatic execution stops for that target.

## Local setup

1. Start ACP with the normal operator command:

   ```bash
   cd ~/Downloads/ACP
   ./manage.sh start
   ```

2. Put a random value in `shared/.env.local`:

   ```text
   ACP_SEEDING_EXTENSION_TOKEN=<your-random-local-token>
   ```

   Restart ACP after changing the environment file. Never commit the real token.

3. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select:

   ```text
   extensions/facebook-seeding-assistant/
   ```

4. Open Facebook. The ACP panel appears in the bottom-right. Enter:

   - ACP URL: `http://127.0.0.1:5000`
   - the same `ACP_SEEDING_EXTENSION_TOKEN`

5. In ACP `/seeding`:

   - create a campaign;
   - add approved claims/templates;
   - import explicit Facebook target URLs;
   - keep **auto-submit OFF** for the first selector/review check;
   - start a shift.

6. Validate at least one non-production/test target in review mode. Only enable campaign auto-submit after confirming the page structure, campaign content, and authorization are correct.

## Development tests

Pure extension tests require only Node:

```bash
node --test extensions/facebook-seeding-assistant/tests/*.test.cjs
```

These tests do not open Facebook or publish anything.

## Stop conditions

Use **STOP NOW · Global pause** in ACP or stop the shift if Facebook displays a checkpoint, identity verification, rate restriction, wrong target, ambiguous composer, or unexpected behavior. The extension intentionally does not try to bypass those states.
