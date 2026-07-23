"""Loopback HTTP bridge used by the Chromium sentence-capture extension."""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .config import (
    DEFAULT_BROWSER_CAPTURE_HOST,
    DEFAULT_BROWSER_CAPTURE_PORT,
    normalize_browser_capture_host,
    normalize_browser_capture_port,
)


logger = logging.getLogger(__name__)

CAPTURE_HOST = DEFAULT_BROWSER_CAPTURE_HOST
CAPTURE_PORT = DEFAULT_BROWSER_CAPTURE_PORT
CAPTURE_PATH = "/capture"
MAX_CAPTURE_BYTES = 64 * 1024
MAX_CAPTURE_CHARACTERS = 10_000


class _CaptureHTTPServer(ThreadingHTTPServer):
    """Threaded loopback server carrying the UI-dispatch callback."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, capture_callback: Callable[[str], None]):
        super().__init__(server_address, _CaptureRequestHandler)
        self.capture_callback = capture_callback


class _CaptureRequestHandler(BaseHTTPRequestHandler):
    """Accept one small JSON capture payload without enabling web-page CORS."""

    server: _CaptureHTTPServer

    def do_POST(self) -> None:  # noqa: N802 - required BaseHTTPRequestHandler API
        if self.path != CAPTURE_PATH:
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        if self.headers.get_content_type().casefold() != "application/json":
            self._write_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "Content-Type must be application/json"},
            )
            return

        content_length = self._content_length()
        if content_length is None:
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Request body must be valid JSON"},
            )
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Request body must contain text"},
            )
            return

        sentence = payload["text"].strip()
        if not sentence:
            self._write_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"ok": False, "error": "Captured text must not be blank"},
            )
            return
        if len(sentence) > MAX_CAPTURE_CHARACTERS:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "Captured text is too long"},
            )
            return

        try:
            self.server.capture_callback(sentence)
        except Exception:
            logger.exception("Could not dispatch browser capture to the application")
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Could not deliver captured text"},
            )
            return

        self._write_json(HTTPStatus.ACCEPTED, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802 - required BaseHTTPRequestHandler API
        self._write_json(
            HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "POST only"}
        )

    def do_OPTIONS(self) -> None:  # noqa: N802 - deliberately do not grant CORS
        self._write_json(
            HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "POST only"}
        )

    def _content_length(self) -> int | None:
        raw_content_length = self.headers.get("Content-Length")
        if raw_content_length is None:
            self._write_json(
                HTTPStatus.LENGTH_REQUIRED,
                {"ok": False, "error": "Content-Length is required"},
            )
            return None

        try:
            content_length = int(raw_content_length)
        except ValueError:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Content-Length must be an integer"},
            )
            return None

        if content_length < 0:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Content-Length must not be negative"},
            )
            return None
        if content_length > MAX_CAPTURE_BYTES:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "Request body is too large"},
            )
            return None
        return content_length

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args) -> None:  # noqa: A002
        """Keep routine local requests out of stderr while retaining diagnostics."""
        logger.debug("Browser capture request: " + format, *args)


class BrowserCaptureServer:
    """Own the local daemon that accepts sentence captures from the extension."""

    def __init__(
        self,
        on_capture: Callable[[str], None],
        *,
        host: str = CAPTURE_HOST,
        port: int = CAPTURE_PORT,
    ):
        if not callable(on_capture):
            raise TypeError("on_capture must be callable")

        self._on_capture = on_capture
        self._host = normalize_browser_capture_host(host)
        # Port zero is reserved for isolated test listeners only.
        self._requested_port = (
            0
            if type(port) is int and port == 0
            else normalize_browser_capture_port(port)
        )
        self._server: _CaptureHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        """The only interface exposed by the daemon: IPv4 loopback."""
        return self._host

    @property
    def port(self) -> int:
        """Return the bound port, including an ephemeral test port after start."""
        if self._server is None:
            return self._requested_port
        return int(self._server.server_address[1])

    @property
    def is_running(self) -> bool:
        """Whether a serving thread currently belongs to this instance."""
        return self._server is not None and self._thread is not None

    def start(self) -> None:
        """Bind loopback and begin accepting requests on a daemon thread."""
        if self.is_running:
            return

        server = _CaptureHTTPServer(
            (self._host, self._requested_port), self._on_capture
        )
        thread = threading.Thread(
            target=server.serve_forever,
            name="KGB browser capture daemon",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        try:
            thread.start()
        except Exception:
            self._server = None
            self._thread = None
            server.server_close()
            raise

    def stop(self) -> None:
        """Stop accepting captures and release the loopback port."""
        server = self._server
        thread = self._thread
        if server is None:
            return

        self._server = None
        self._thread = None
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
