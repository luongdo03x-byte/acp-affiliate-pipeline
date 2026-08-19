function jsonResponse(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
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
    if (!Array.isArray(body.rows) || body.rows.length === 0 || body.rows.length > 100) {
      return jsonResponse({ok: false, error: 'rows must contain 1..100 rows'});
    }
    var rows = body.rows.map(function (row) {
      if (!Array.isArray(row) || row.length !== 3) {
        throw new Error('each report row must contain exactly B/C/D values');
      }
      return row.map(function (cell) { return String(cell == null ? '' : cell); });
    });

    var spreadsheet = SpreadsheetApp.openById(spreadsheetId);
    var sheet = spreadsheet.getSheetByName(sheetName);
    if (!sheet) {
      return jsonResponse({ok: false, error: 'sheet not found: ' + sheetName});
    }
    var startRow = Math.max(sheet.getLastRow() + 1, 1);
    sheet.getRange(startRow, 2, rows.length, 3).setValues(rows);
    var endRow = startRow + rows.length - 1;
    return jsonResponse({
      ok: true,
      sheet_ref: sheetName + '!B' + startRow + ':D' + endRow,
      task_name: String(body.task_name || ''),
      campaign_id: String(body.campaign_id || '')
    });
  } catch (error) {
    return jsonResponse({ok: false, error: String(error && error.message ? error.message : error)});
  }
}
