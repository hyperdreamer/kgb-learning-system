const DEFAULT_CAPTURE_HOST = "127.0.0.1";
const DEFAULT_CAPTURE_PORT = 8010;
const DEFAULT_CAPTURE_ORIGIN = captureOrigin(
  DEFAULT_CAPTURE_HOST,
  DEFAULT_CAPTURE_PORT,
);

const form = document.querySelector("#captureOptions");
const hostInput = document.querySelector("#captureHost");
const portInput = document.querySelector("#capturePort");
const status = document.querySelector("#status");

form.addEventListener("submit", saveOptions);
loadOptions();

async function loadOptions() {
  const { captureHost, capturePort } = await chrome.storage.local.get({
    captureHost: DEFAULT_CAPTURE_HOST,
    capturePort: DEFAULT_CAPTURE_PORT,
  });
  hostInput.value = captureHost;
  portInput.value = capturePort;
}

async function saveOptions(event) {
  event.preventDefault();

  const host = hostInput.value.trim();
  const port = Number(portInput.value);
  if (!isLoopbackIPv4(host) || !Number.isInteger(port) || port < 1 || port > 65535) {
    setStatus("Enter a loopback IPv4 address and a port from 1 through 65535.", true);
    return;
  }

  const origin = captureOrigin(host, port);
  const previous = await chrome.storage.local.get({
    captureHost: DEFAULT_CAPTURE_HOST,
    capturePort: DEFAULT_CAPTURE_PORT,
  });
  const hasPermission = await chrome.permissions.contains({ origins: [origin] });
  if (!hasPermission) {
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) {
      setStatus("Permission is required to contact this local endpoint.", true);
      return;
    }
  }

  await chrome.storage.local.set({ captureHost: host, capturePort: port });
  const previousOrigin = captureOrigin(previous.captureHost, previous.capturePort);
  if (previousOrigin !== origin && previousOrigin !== DEFAULT_CAPTURE_ORIGIN) {
    await chrome.permissions.remove({ origins: [previousOrigin] });
  }
  setStatus("Endpoint saved.");
}

function captureOrigin(host, port) {
  return `http://${host}:${port}/*`;
}

function isLoopbackIPv4(host) {
  const octets = host.split(".");
  return (
    octets.length === 4 &&
    octets.every((octet) => /^\d+$/.test(octet) && Number(octet) <= 255) &&
    Number(octets[0]) === 127
  );
}

function setStatus(message, isError = false) {
  status.textContent = message;
  status.style.color = isError ? "#b42318" : "#1a7f37";
}
