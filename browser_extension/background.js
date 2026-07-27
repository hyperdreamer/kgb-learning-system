const CAPTURE_MENU_ID = "kgb-send-selected-sentence";
const CAPTURE_COMMAND_ID = "send-selected-text";
const DEFAULT_CAPTURE_HOST = "127.0.0.1";
const DEFAULT_CAPTURE_PORT = 8010;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: CAPTURE_MENU_ID,
    title: "Send selected sentence to KGB",
    contexts: ["selection"],
  });
});

/**
 * Send selected text to the local KGB capture daemon.
 * @param {string} text - The selected sentence text to send.
 */
async function sendToKGB(text) {
  if (!text?.trim()) {
    return;
  }
  try {
    const response = await fetch(await getCaptureEndpoint(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    });
    if (!response.ok) {
      throw new Error(`KGB returned HTTP ${response.status}`);
    }
    showCaptureStatus("✓", "Sentence sent to KGB");
    // Auto-clear success badge after a brief visible confirmation.
    setTimeout(() => clearCaptureBadge(), 2000);
  } catch (error) {
    console.error("Could not send selected sentence to KGB", error);
    // Error badge persists so the user sees it even if they miss the brief flash.
    showCaptureStatus("ERR", "Could not reach KGB. Start the desktop app first.");
  }
}

// --- Context menu handler ---

chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== CAPTURE_MENU_ID) {
    return;
  }
  await sendToKGB(info.selectionText);
});

// --- Keyboard shortcut handler ---

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== CAPTURE_COMMAND_ID) {
    return;
  }
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => window.getSelection()?.toString() ?? "",
    });
    const selectedText = results?.[0]?.result ?? "";
    await sendToKGB(selectedText);
  } catch (error) {
    console.error("Could not read selected text", error);
    showCaptureStatus("ERR", "Could not read selection. Try the right-click menu instead.");
  }
});

async function getCaptureEndpoint() {
  const { captureHost, capturePort } = await chrome.storage.local.get({
    captureHost: DEFAULT_CAPTURE_HOST,
    capturePort: DEFAULT_CAPTURE_PORT,
  });
  return `http://${captureHost}:${capturePort}/capture`;
}

function showCaptureStatus(text, title) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color: text === "✓" ? "#2e7d32" : "#c62828" });
  chrome.action.setTitle({ title });
}

function clearCaptureBadge() {
  chrome.action.setBadgeText({ text: "" });
}
