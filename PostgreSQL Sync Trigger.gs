const WEBHOOK_URL = "https://URL.ngrok-free.app/sync-sheets"; // Replace with your actual URL
const SECRET_KEY = "a_secret_key"; // Must match WEBHOOK_SECRET_KEY in your Python server

/**
 * This function will be linked to an 'On edit' trigger to catch cell edits.
 * @param {GoogleAppsScript.Events.SheetsOnEdit} e The event object.
 */
function onEdit(e) {
  // Your existing onEdit logic for cell changes
  const sheetName = e.range.getSheet().getName();
  if (sheetName === "All Data") {
    console.log(`Edit detected in sheet: ${sheetName}, cell: ${e.range.getA1Notation()}`);
    sendWebhookTrigger(); // Call a helper function
  }
}

/**
 * This function will be linked to an 'On change' installable trigger.
 * It detects structural changes like row deletions/insertions.
 * @param {GoogleAppsScript.Events.SheetsOnChange} e The event object.
 */
function onChange(e) {
  // Check if the change happened in your target sheet
  const activeSheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  if (activeSheet.getName() === "All Data") {
    // Ensure it's a structural change like a row/column deletion or insertion
    if (e.changeType === 'REMOVE_ROW' || e.changeType === 'REMOVE_COLUMN' ||
        e.changeType === 'INSERT_ROW' || e.changeType === 'INSERT_COLUMN') {
      console.log(`Structural change detected: ${e.changeType} in sheet: ${activeSheet.getName()}`);
      sendWebhookTrigger(); // Call the helper function
    }
  }
}


/**
 * Helper function to send the webhook request.
 */
function sendWebhookTrigger() {
  const options = {
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      'X-Secret-Key': SECRET_KEY
    },
    payload: JSON.stringify({
      // Removed e.range/e.value as onChange doesn't always have them
      message: "Data sync requested due to sheet change."
    }),
    muteHttpExceptions: true
  };

  try {
    const response = UrlFetchApp.fetch(WEBHOOK_URL, options);
    const responseCode = response.getResponseCode();
    const responseBody = response.getContentText();

    if (responseCode === 200) {
      console.log(`Sync trigger sent successfully. Server response: ${responseBody}`);
    } else {
      console.error(`Error sending sync trigger. Status: ${responseCode}, Response: ${responseBody}`);
    }
  } catch (error) {
    console.error(`Network error or problem connecting to webhook: ${error.message}`);
  }
}
