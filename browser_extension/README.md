# KGB Sentence Capture extension

This unpacked Manifest V3 extension sends selected text from Chromium-family
browsers (Chrome, Edge, Brave, and similar) to a running local KGB desktop app.

## Install

1. Start KGB. By default, it starts a loopback capture daemon at
   `127.0.0.1:8010`.
2. Open your browser's extensions page:
   - Chrome: `chrome://extensions`
   - Edge: `edge://extensions`
   - Brave: `brave://extensions`
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select this `browser_extension/` directory.
5. Select a sentence on any webpage, right-click it, and choose
   **Send selected sentence to KGB** — or press **Alt+K**.

KGB restores its window and opens its existing Add Entry workflow with the
selected text prefilled. Nothing is saved automatically: review or edit the
text, then save it normally.

## Keyboard shortcut

The default keyboard shortcut is **Alt+K** (**MacCtrl+K** on macOS).

To customize it:

1. Open **chrome://extensions/shortcuts** in your browser.
2. Find **KGB Sentence Capture**.
3. Click the pencil icon next to "Send selected text to KGB" and enter your
   preferred key combination.

The shortcut must include a modifier key (**Ctrl**, **Alt**, or **Command**)
and a letter or number. **Shift** is an optional extra modifier.

## Configure a custom listener

In KGB, open **Settings → General** and set **Browser Capture IP** and
**Browser Capture Port**. Saving replaces the active listener immediately. The
IP must be an IPv4 loopback address (`127.0.0.0/8`); the daemon deliberately
cannot be exposed to your LAN or the internet.

Then open this extension’s **Options** page from your browser’s extension
manager and enter the identical IP and port. The extension asks the browser for
permission to contact that loopback IP when you save the endpoint. Chromium
permission match patterns are host-scoped, so the selected port directs capture
traffic but cannot narrow the browser permission further.

## Permissions and privacy

The extension requests only:

- `contextMenus`, to add the selection-only right-click command;
- `storage`, to retain the endpoint you explicitly configure;
- `activeTab` and `scripting`, to read the current page's selection when the
  keyboard shortcut is pressed (no content script is injected); and
- access to the default loopback host `http://127.0.0.1/*` (Chrome applies this
  permission across that host's ports).

For a custom endpoint, it asks for access only to the selected loopback IP when
you save it in Options. The stored endpoint still sends capture text only to the
chosen IP and port; the extension does not receive broad host access. The
extension does not inject scripts, read page contents beyond the text the user
selects, or send data to a remote service. The daemon binds to IPv4 loopback
only and intentionally does not enable CORS for webpages.

A writable sentence-based or knowledge-based database must be open for KGB to
present an add-card dialog. Word/Phrase databases are read-only projections.
