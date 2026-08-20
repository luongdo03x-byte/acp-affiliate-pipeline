function jsonResponse(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function safeSheetCell(value) {
  var text = String(value == null ? '' : value);
  // setValues() interprets leading formula characters. Prefix an apostrophe so
  // user/AI text is stored as literal text while Sheet displays the original
  // visible value without evaluating it as a formula.
  if (/^[=+\-@]/.test(text)) {
    return "'" + text;
  }
  return text;
}

function doPost(e) {
  try {
    var props = PropertiesService.getScriptProperties();
    var expectedSecret = props.getProperty('ACP_SEEDING_SECRET');
    var spreadsheetId = props.getProperty('SPREADSHEET_ID');
    var sheetName = props.getProperty('SHEET_NAME') || 'Sheet1';
    if (!expectedSecret || !spreadsheetId) {
      return jsonResponse({ok: false, error: 'Apps Script properties are not configured'});
    }

    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (!body.secret || body.secret !== expectedSecret) {
      return jsonResponse({ok: false, error: 'unauthorized'});
    }
    if (!body.campaign_id || !Array.isArray(body.rows) || body.rows.length === 0 || body.rows.length > 100) {
      return jsonResponse({ok: false, error: 'campaign_id and 1..100 report rows are required'});
    }
    var rows = body.rows.map(function (row) {
      if (!Array.isArray(row) || row.length !== 3) {
        throw new Error('each report row must contain exactly B/C/D values');
      }
      return row.map(function (cell) { return safeSheetCell(cell); });
    });

    var campaignId = String(body.campaign_id);
    var reportKey = 'ACP_SEEDING_REPORT_' + campaignId;
    var lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try {
      var existingRef = props.getProperty(reportKey);
      if (existingRef) {
        return jsonResponse({
          ok: true,
          duplicate: true,
          sheet_ref: existingRef,
          task_name: String(body.task_name || ''),
          campaign_id: campaignId
        });
      }

      var spreadsheet = SpreadsheetApp.openById(spreadsheetId);
      var sheet = spreadsheet.getSheetByName(sheetName);
      if (!sheet) {
        return jsonResponse({ok: false, error: 'sheet not found: ' + sheetName});
      }
      var startRow = Math.max(sheet.getLastRow() + 1, 1);
      sheet.getRange(startRow, 2, rows.length, 3).setValues(rows);
      var endRow = startRow + rows.length - 1;
      var sheetRef = sheetName + '!B' + startRow + ':D' + endRow;
      props.setProperty(reportKey, sheetRef);
      return jsonResponse({
        ok: true,
        duplicate: false,
        sheet_ref: sheetRef,
        task_name: String(body.task_name || ''),
        campaign_id: campaignId
      });
    } finally {
      lock.releaseLock();
    }
  } catch (error) {
    return jsonResponse({ok: false, error: String(error && error.message ? error.message : error)});
  }
}
