const CAPTURE_MENU_ID = "kgb-send-selected-sentence";
const DEFAULT_CAPTURE_HOST = "127.0.0.1";
const DEFAULT_CAPTURE_PORT = 8010;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: CAPTURE_MENU_ID,
    title: "Send selected sentence to KGB",
    contexts: ["selection"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== CAPTURE_MENU_ID || !info.selectionText?.trim()) {
    return;
  }

  try {
    const response = await fetch(await getCaptureEndpoint(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: info.selectionText }),
    });
    if (!response.ok) {
      throw new Error(`KGB returned HTTP ${response.status}`);
    }
    showCaptureStatus("✓", "Sentence sent to KGB");
  } catch (error) {
    console.error("Could not send selected sentence to KGB", error);
    showCaptureStatus("!", "Could not reach KGB. Start the desktop app first.");
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
