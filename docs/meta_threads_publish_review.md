# ACP — Meta Threads App Review / Publish Package

This document is the minimal submission package for publishing ACP with the Threads API. The first review should request only the permissions ACP currently uses:

- `threads_basic`
- `threads_content_publish`

ACP's OAuth client requests exactly these two scopes. Do not add replies, insights, search, mentions, or deletion permissions to the first submission unless ACP starts using those capabilities.

> Meta Dashboard labels change occasionally. If a menu name differs slightly, use the Threads API use case and the equivalent App Review / Permissions screen.

## 1. Required public URLs

ACP exposes two public legal pages from the dedicated Account Factory Flask app:

- Privacy Policy: `<PUBLIC_BASE_URL>/privacy`
- Data Deletion Instructions: `<PUBLIC_BASE_URL>/data-deletion`

The Account Factory OAuth callback is:

- OAuth Redirect URI: `<PUBLIC_BASE_URL>/oauth/account-factory/threads/callback`

`<PUBLIC_BASE_URL>` is the HTTPS value configured in `ACP_PUBLIC_BASE_URL`. A stable ngrok HTTPS domain is acceptable for development/review as long as the tunnel and ACP service remain online while Meta reviews/tests the app.

Before submission, configure a real support email:

```bash
cd ~/Downloads/ACP/worktrees/account-factory-android

# Add or replace this line in ~/Downloads/ACP/shared/.env.local
ACP_SUPPORT_EMAIL=YOUR_REAL_SUPPORT_EMAIL
```

Then restart the Account Factory server and verify the legal pages are public without an API key or login:

```bash
set -a
source ~/Downloads/ACP/shared/.env.local
set +a

curl -fsS "$ACP_PUBLIC_BASE_URL/privacy" | grep -F "ACP Privacy Policy"
curl -fsS "$ACP_PUBLIC_BASE_URL/data-deletion" | grep -F "ACP Data Deletion"
curl -fsS "$ACP_PUBLIC_BASE_URL/privacy" | grep -F "$ACP_SUPPORT_EMAIL"
```

Do not submit while either page contains `Support contact is not configured yet`.

## 2. Meta Dashboard checklist

### App settings / Basic information

Fill or verify:

- App display name: `ACP`
- Contact email: the same monitored support/developer email used for the review
- Privacy Policy URL: `<PUBLIC_BASE_URL>/privacy`
- User Data Deletion / Data Deletion Instructions URL: `<PUBLIC_BASE_URL>/data-deletion`
- App icon: square icon, at least 512×512 recommended
- App domain / website domain: use the public hostname if Meta asks for it

Save the changes.

### Threads API settings

In the Threads API use case, verify the OAuth redirect URI exactly matches:

```text
<PUBLIC_BASE_URL>/oauth/account-factory/threads/callback
```

The scheme, host, path, and trailing slash behavior must match the URI ACP sends during OAuth.

### Permissions / App Review

Request only:

```text
threads_basic
threads_content_publish
```

Do not request additional Threads permissions in the first review.

## 3. Copy/paste app description

Use this as the short app/use-case description:

> ACP is a social media management application that lets an authorized user connect their own Threads account through Meta OAuth and publish content that the user explicitly selects or schedules in ACP. ACP uses the connected account only to identify the authorized Threads profile, maintain the authorized API connection, and perform user-requested publishing actions.

## 4. `threads_basic` review text

### Why ACP needs this permission

Copy/paste:

> ACP uses `threads_basic` to complete Threads OAuth, identify the Threads profile that the user connected, and maintain the authorized account connection. ACP verifies the connected Threads username/profile before associating the authorization with the ACP account. The permission is not used for advertising, profiling, or unrelated data collection.

### Reviewer steps

Copy/paste and adjust only the visible button/page names if the UI changes:

> 1. Open ACP and start the Connect Threads flow for a Threads account.
> 2. ACP opens Meta/Threads OAuth and requests the minimum Threads permissions.
> 3. Sign in to the Threads account and approve the authorization request.
> 4. Meta redirects the browser to ACP's configured OAuth callback.
> 5. ACP exchanges the authorization code for a Threads access token and reads the authorized Threads profile.
> 6. ACP verifies that the returned Threads username matches the account being connected.
> 7. ACP shows the account as connected/active. Access tokens are not displayed to the user.

### What the screencast must show

- Start of the Connect Threads flow
- Threads/Meta authorization screen
- Account authorization
- Redirect back to ACP
- Connected Threads username/account visible in ACP

## 5. `threads_content_publish` review text

### Why ACP needs this permission

Copy/paste:

> ACP uses `threads_content_publish` so an authorized user can publish Threads content from ACP to the Threads account they connected. ACP publishes only content that the user explicitly creates, selects, approves, or schedules through ACP. This permission is required to create a Threads publishing container and publish that container to the authorized Threads profile.

### Reviewer steps

Copy/paste:

> 1. Complete the Connect Threads flow described for `threads_basic`.
> 2. In ACP, create or select a test post for the connected Threads account.
> 3. Enter a simple review-safe text such as `ACP Threads API review test`.
> 4. Choose the connected Threads account as the destination.
> 5. Trigger the publish action in ACP.
> 6. ACP sends the publishing request to the Threads API using the authorized user's access token.
> 7. Open the connected Threads profile and show that the exact test post was published.

### What the screencast must show

- The connected Threads account in ACP
- The exact text/content before publishing
- The publish action
- The resulting post on Threads
- Enough of the Threads profile to show it is the same account that ACP connected

Do not cut the video between the ACP publish action and the resulting Threads post unless necessary. A continuous recording makes the use case easier to verify.

## 6. Recommended 60–90 second review video script

Use one tester account that already has access to the app while it is still in development mode.

```text
00:00  Show ACP and the account to be connected.
00:05  Start Connect Threads.
00:10  Show the Meta/Threads authorization page.
00:20  Approve the requested Threads permissions.
00:30  Show redirect back to ACP and the account becoming connected.
00:40  Create/select a text post: "ACP Threads API review test".
00:50  Select the connected Threads account and publish.
01:00  Open Threads and refresh the profile.
01:10  Show the newly published post.
01:20  End recording.
```

Keep passwords, OTP codes, app secrets, access tokens, API keys, and recovery codes out of the recording.

## 7. Submission notes

Paste this into an optional reviewer-notes field:

> The submitted use case is intentionally limited to connecting a user's own Threads account and publishing user-requested content. ACP requests only `threads_basic` and `threads_content_publish` for this review. The attached screencast shows the complete OAuth flow, account verification, and a real Threads publishing action from ACP to the same authorized profile. The Privacy Policy and Data Deletion Instructions URLs are publicly accessible without authentication.

## 8. Pre-submit verification

Run these checks immediately before submitting:

```bash
cd ~/Downloads/ACP/worktrees/account-factory-android
set -a
source ~/Downloads/ACP/shared/.env.local
set +a

printf 'PUBLIC_BASE=%s\n' "$ACP_PUBLIC_BASE_URL"
printf 'SUPPORT_CONFIGURED=%s\n' "$([ -n "${ACP_SUPPORT_EMAIL:-}" ] && echo yes || echo no)"

curl -fsS -o /dev/null -w 'privacy=%{http_code}\n' \
  "$ACP_PUBLIC_BASE_URL/privacy"
curl -fsS -o /dev/null -w 'deletion=%{http_code}\n' \
  "$ACP_PUBLIC_BASE_URL/data-deletion"
curl -fsS -o /dev/null -w 'callback_route=%{http_code}\n' \
  "$ACP_PUBLIC_BASE_URL/oauth/account-factory/threads/callback"
```

Expected result:

- `privacy=200`
- `deletion=200`
- `callback_route=400` is acceptable when called without OAuth `code`/`state`; it proves the route is public and reachable.
- `SUPPORT_CONFIGURED=yes`

Also verify manually:

- The Privacy Policy page shows the real support email.
- The Data Deletion page shows the same support email.
- OAuth redirect URI in Meta exactly matches ACP.
- The tester account can complete OAuth.
- A real test Threads post can be published from ACP.
- The review video contains no secrets.

## 9. After approval

After both requested permissions are approved and the app is published, accounts that do not have a tester/developer role on the Meta app can authorize ACP through the normal OAuth flow. At that point the Account Factory no longer needs the development-only Threads Tester invitation step for ordinary users.

Keep the Privacy Policy and Data Deletion URLs online for the lifetime of the published app.
