# Google Sheets report setup for ACP Seeding

ACP can always download a task report as TSV. To append completed tasks directly to Google Sheets, deploy the included Apps Script webhook.

## 1. Prepare the sheet

Create/open the target Google Sheet and note:

- Spreadsheet ID: the value between `/d/` and `/edit` in the Sheet URL.
- Sheet/tab name, e.g. `Sheet1`.

The webhook appends exactly three values starting at column **B**:

- B: task name on the first row, Facebook URL on the second row.
- C: main comments in account-slot order.
- D: reply comments in account-slot/item order.

## 2. Deploy Apps Script

1. Open Apps Script and create a standalone project.
2. Paste `integrations/google_sheets_seeding_webhook.gs` into the project.
3. In **Project Settings → Script properties**, create:
   - `SPREADSHEET_ID` = target spreadsheet ID.
   - `SHEET_NAME` = target tab name.
   - `ACP_SEEDING_SECRET` = a long random secret.
4. Deploy as **Web app** and copy the `/exec` HTTPS URL.
5. Allow the web app to execute as the owner and accept requests needed by your ACP instance.

## 3. Configure ACP

In `~/Downloads/ACP/shared/.env.local`:

```bash
ACP_SEEDING_SHEET_WEBHOOK_URL=https://script.google.com/macros/s/<deployment-id>/exec
ACP_SEEDING_SHEET_SECRET=<same value as ACP_SEEDING_SECRET>
```

Also enable the LLM used to generate the distinct comment plan:

```bash
ACP_CAPTION_LLM=gemini
ACP_GEMINI_API_KEY=<your key>
```

Restart ACP after changing `.env.local`.

## 4. Behavior

When all mapped accounts have completed every required comment and every required LIKE, ACP attempts to push the report once. A successful push is persisted as `PUSHED`, so page refreshes or repeated result calls do not append the same task again.

If the Sheet webhook fails, the Facebook-side DONE state remains saved. ACP records the report as `FAILED`; it does **not** ask the extension to repeat a completed comment. The operator can retry with **Ghi Google Sheet** on `/seeding/accounts` after fixing the configuration.

The **Tải TSV B/C/D** button remains available independently of webhook configuration.
