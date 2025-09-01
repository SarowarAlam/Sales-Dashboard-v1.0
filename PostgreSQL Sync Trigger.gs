const WEBHOOK_URL = "https://URL.ngrok-free.app/sync-sheets"; // Replace with your actual URL
const SECRET_KEY = "a_secret_key"; // Must match WEBHOOK_SECRET_KEY in your Python server

/**
 * SIMPLE trigger — runs automatically on user edits.
 * Do NOT click Run on this; test by editing the sheet.
 * If you also add an installable “On edit” trigger, you’ll get the same event here.
 * @param {GoogleAppsScript.Events.SheetsOnEdit} e
 */
function onEdit(e) {
  if (!e || !e.range) return; // Prevent "reading 'range'" when invoked without event
  const sheet = e.range.getSheet();
  if (sheet.getName() !== "All Data") return;

  console.log(`Edit in ${sheet.getName()} @ ${e.range.getA1Notation()}`);
  sendWebhookTrigger({
    cause: "edit",
    a1: e.range.getA1Notation(),
    sheet: sheet.getName(),
  });
}

/**
 * INSTALLABLE trigger — add via Triggers as “From spreadsheet” → “On change”.
 * Fires on structural changes; no e.range is provided.
 * @param {GoogleAppsScript.Events.SheetsOnChange} e
 */
function onChange(e) {
  if (!e) return;
  const change = e.changeType;
  if (!change) return;

  // e.source is the spreadsheet; use active sheet as a reasonable proxy
  const activeSheet = e.source && e.source.getActiveSheet
    ? e.source.getActiveSheet()
    : null;

  if (!activeSheet || activeSheet.getName() !== "All Data") return;

  const structural =
    change === 'REMOVE_ROW'   || change === 'REMOVE_COLUMN' ||
    change === 'INSERT_ROW'   || change === 'INSERT_COLUMN' ||
    change === 'OTHER'; // keep if you want to treat other structural changes

  if (!structural) return;

  console.log(`Change=${change} on sheet ${activeSheet.getName()}`);
  sendWebhookTrigger({
    cause: "change",
    changeType: change,
    sheet: activeSheet.getName(),
  });
}

/**
 * Shared webhook helper — safe to call from either trigger.
 * Accepts a small payload to avoid depending on e.range.
 */
function sendWebhookTrigger(extra) {
  const payload = Object.assign(
    { message: "Data sync requested due to sheet change." },
    extra || {}
  );

  const options = {
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      'X-Secret-Key': SECRET_KEY,
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  };

  try {
    const res = UrlFetchApp.fetch(WEBHOOK_URL, options);
    const code = res.getResponseCode();
    const body = res.getContentText();

    if (code >= 200 && code < 300) {
      console.log(`Sync trigger OK (${code}): ${body}`);
    } else {
      console.error(`Sync trigger FAILED (${code}): ${body}`);
    }
  } catch (err) {
    console.error(`Webhook fetch error: ${err && err.message ? err.message : err}`);
  }
}

/** Optional: safer storage for secrets */
function setSecrets() {
  const props = PropertiesService.getScriptProperties();
  props.setProperty('WEBHOOK_URL', WEBHOOK_URL);
  props.setProperty('SECRET_KEY', SECRET_KEY);
}
