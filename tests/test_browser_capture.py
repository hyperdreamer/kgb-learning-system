"""Tests for the local browser-to-app sentence capture bridge."""

import json
import socket
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _post_capture(port, payload, *, content_type="application/json", path="/capture"):
    return _request(
        port,
        json.dumps(payload).encode("utf-8"),
        content_type=content_type,
        path=path,
    )


def _request(
    port, body=None, *, content_type="application/json", path="/capture", method="POST"
):
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method=method,
    )
    return urlopen(request, timeout=2)


def _raw_http_request(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
        connection.sendall(request)
        return connection.recv(4096)


def test_capture_daemon_delivers_trimmed_sentence_to_callback():
    from kgb_srs.browser_capture import BrowserCaptureServer

    received = []
    server = BrowserCaptureServer(received.append, port=0)
    server.start()
    try:
        with _post_capture(
            server.port, {"text": "  A captured sentence.  "}
        ) as response:
            assert response.status == 202
            assert json.loads(response.read()) == {"ok": True}

        assert server.host == "127.0.0.1"
        assert received == ["A captured sentence."]
    finally:
        server.stop()


def test_capture_daemon_rejects_blank_selection_without_dispatching():
    from kgb_srs.browser_capture import BrowserCaptureServer

    received = []
    server = BrowserCaptureServer(received.append, port=0)
    server.start()
    try:
        with pytest.raises(HTTPError) as error:
            _post_capture(server.port, {"text": " \n\t "})

        assert error.value.code == 422
        assert received == []
    finally:
        server.stop()


def test_capture_daemon_requires_json_content_type_without_dispatching():
    from kgb_srs.browser_capture import BrowserCaptureServer

    received = []
    server = BrowserCaptureServer(received.append, port=0)
    server.start()
    try:
        with pytest.raises(HTTPError) as error:
            _post_capture(
                server.port, {"text": "A sentence."}, content_type="text/plain"
            )

        assert error.value.code == 415
        assert received == []
    finally:
        server.stop()


def test_capture_daemon_rejects_malformed_payloads_without_dispatching():
    from kgb_srs.browser_capture import BrowserCaptureServer

    received = []
    server = BrowserCaptureServer(received.append, port=0)
    server.start()
    try:
        for body, expected_status in (
            (b"{", 400),
            (b'{"message": "missing text"}', 400),
        ):
            with pytest.raises(HTTPError) as error:
                _request(server.port, body)
            assert error.value.code == expected_status

        with pytest.raises(HTTPError) as error:
            _post_capture(server.port, {"text": "A sentence."}, path="/other")
        assert error.value.code == 404
        assert received == []
    finally:
        server.stop()


def test_capture_daemon_rejects_invalid_http_framing_and_oversized_text():
    from kgb_srs.browser_capture import BrowserCaptureServer, MAX_CAPTURE_CHARACTERS

    server = BrowserCaptureServer(lambda _sentence: None, port=0)
    server.start()
    try:
        requests_and_statuses = [
            (
                b"GET /capture HTTP/1.1\r\nHost: localhost\r\n\r\n",
                b" 405 ",
            ),
            (
                b"OPTIONS /capture HTTP/1.1\r\nHost: localhost\r\n\r\n",
                b" 405 ",
            ),
            (
                b"POST /capture HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\n\r\n",
                b" 411 ",
            ),
            (
                b"POST /capture HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: nope\r\n\r\n",
                b" 400 ",
            ),
            (
                b"POST /capture HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: -1\r\n\r\n",
                b" 400 ",
            ),
            (
                b"POST /capture HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: 65537\r\n\r\n",
                b" 413 ",
            ),
        ]
        for request, expected_status in requests_and_statuses:
            assert expected_status in _raw_http_request(server.port, request)

        with pytest.raises(HTTPError) as error:
            _post_capture(server.port, {"text": "a" * (MAX_CAPTURE_CHARACTERS + 1)})
        assert error.value.code == 413
    finally:
        server.stop()


def test_capture_daemon_reports_callback_failure_without_accepting_text():
    from kgb_srs.browser_capture import BrowserCaptureServer

    def raise_delivery_error(_sentence):
        raise RuntimeError("UI unavailable")

    server = BrowserCaptureServer(raise_delivery_error, port=0)
    server.start()
    try:
        with pytest.raises(HTTPError) as error:
            _post_capture(server.port, {"text": "A sentence."})

        assert error.value.code == 500
    finally:
        server.stop()


def test_capture_daemon_stop_releases_its_serving_state():
    from kgb_srs.browser_capture import BrowserCaptureServer

    server = BrowserCaptureServer(lambda _sentence: None, port=0)
    server.start()
    assert server.is_running

    server.stop()

    assert not server.is_running
    server.stop()


def test_captured_sentence_opens_prefilled_sentence_add_dialog(monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from .qt_helpers import qt_app

    qt_app()
    from kgb_srs.catalog import DatabaseType
    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    created_dialogs = []

    class CaptureDialog:
        def __init__(self, parent, title, sentence="", **_kwargs):
            created_dialogs.append((parent, title, sentence))

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(main_window, "SentenceCardDialog", CaptureDialog)
    window = BarskyApp()
    window.conn = object()
    window._db_type = DatabaseType.LANGUAGE_SENTENCE
    monkeypatch.setattr(window, "showNormal", lambda: None)
    monkeypatch.setattr(window, "raise_", lambda: None)
    monkeypatch.setattr(window, "activateWindow", lambda: None)

    try:
        window._handle_browser_capture("A sentence from a webpage.")

        assert created_dialogs == [
            (window, "Add Sentence Card", "A sentence from a webpage.")
        ]
    finally:
        window.close()


def test_captured_sentence_prefills_a_knowledge_card_front(monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from .qt_helpers import qt_app

    qt_app()
    from kgb_srs.catalog import DatabaseType
    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    dialog_arguments = []

    class CaptureDialog:
        def __init__(self, _parent, title, label, initial_text=""):
            dialog_arguments.append((title, label, initial_text))
            self.text_value = None

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(main_window, "DynamicInputDialog", CaptureDialog)
    window = BarskyApp()
    window.conn = object()
    window._db_type = DatabaseType.KNOWLEDGE
    monkeypatch.setattr(window, "showNormal", lambda: None)
    monkeypatch.setattr(window, "raise_", lambda: None)
    monkeypatch.setattr(window, "activateWindow", lambda: None)

    try:
        window._handle_browser_capture("A knowledge card front.")

        assert dialog_arguments == [
            (
                "Add New Knowledge Card",
                "Enter the front content. Markdown and MathJax are supported:",
                "A knowledge card front.",
            )
        ]
    finally:
        window.close()


def test_daemon_queues_capture_into_the_main_window(monkeypatch):
    from PyQt6.QtWidgets import QApplication, QDialog

    from .qt_helpers import qt_app

    qt_app()
    from kgb_srs.browser_capture import BrowserCaptureServer
    from kgb_srs.catalog import DatabaseType
    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    captured_sentences = []

    class CaptureDialog:
        def __init__(self, _parent, _title, sentence="", **_kwargs):
            captured_sentences.append(sentence)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(main_window, "SentenceCardDialog", CaptureDialog)
    window = BarskyApp()
    window.conn = object()
    window._db_type = DatabaseType.LANGUAGE_SENTENCE
    monkeypatch.setattr(window, "showNormal", lambda: None)
    monkeypatch.setattr(window, "raise_", lambda: None)
    monkeypatch.setattr(window, "activateWindow", lambda: None)
    server = BrowserCaptureServer(window.browser_capture_received.emit, port=0)
    server.start()

    try:
        with _post_capture(server.port, {"text": "Queued from the daemon."}):
            pass
        QApplication.processEvents()

        assert captured_sentences == ["Queued from the daemon."]
    finally:
        server.stop()
        window.close()


def test_capture_daemon_accepts_a_configured_loopback_address():
    from kgb_srs.browser_capture import BrowserCaptureServer

    server = BrowserCaptureServer(lambda _sentence: None, host="127.0.0.2", port=0)

    assert server.host == "127.0.0.2"


def test_capture_daemon_rejects_non_loopback_listen_addresses():
    from kgb_srs.browser_capture import BrowserCaptureServer

    with pytest.raises(ValueError, match="loopback"):
        BrowserCaptureServer(lambda _sentence: None, host="192.168.1.20")


def test_capture_daemon_rejects_boolean_port_values():
    from kgb_srs.browser_capture import BrowserCaptureServer

    with pytest.raises(ValueError, match="port"):
        BrowserCaptureServer(lambda _sentence: None, port=False)


def test_main_window_starts_capture_daemon_at_the_configured_endpoint(monkeypatch):
    from types import SimpleNamespace

    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    created = []

    class CaptureServer:
        def __init__(self, callback, *, host, port):
            created.append((callback, host, port))

        def start(self):
            pass

    signal = SimpleNamespace(emit=lambda _sentence: None)
    window = SimpleNamespace(
        _browser_capture_server=None,
        browser_capture_received=signal,
        settings={"browser_capture_host": "127.0.0.2", "browser_capture_port": 9123},
    )
    monkeypatch.setattr(main_window, "BrowserCaptureServer", CaptureServer)

    assert BarskyApp.start_browser_capture_server(window) is True
    assert created == [(signal.emit, "127.0.0.2", 9123)]


def test_main_window_restarts_capture_daemon_at_a_new_configured_endpoint(monkeypatch):
    from types import SimpleNamespace

    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    class ExistingServer:
        host = "127.0.0.1"
        port = 8010

        def __init__(self):
            self.stop_calls = 0

        def stop(self):
            self.stop_calls += 1

    created = []

    class ReplacementServer:
        def __init__(self, _callback, *, host, port):
            self.host = host
            self.port = port
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

    existing = ExistingServer()
    window = SimpleNamespace(
        _browser_capture_server=existing,
        browser_capture_received=SimpleNamespace(emit=lambda _sentence: None),
        settings={"browser_capture_host": "127.0.0.2", "browser_capture_port": 9123},
    )
    monkeypatch.setattr(main_window, "BrowserCaptureServer", ReplacementServer)

    assert BarskyApp.restart_browser_capture_server(window) is True
    assert existing.stop_calls == 1
    assert created[0].started
    assert window._browser_capture_server is created[0]


def test_main_window_restores_prior_daemon_if_listener_reconfiguration_fails(
    monkeypatch,
):
    from types import SimpleNamespace

    from kgb_srs.main_window import BarskyApp
    import kgb_srs.main_window as main_window

    class ExistingServer:
        host = "127.0.0.1"
        port = 8010

        def __init__(self):
            self.stop_calls = 0
            self.start_calls = 0

        def stop(self):
            self.stop_calls += 1

        def start(self):
            self.start_calls += 1

    class FailingServer:
        def __init__(self, _callback, *, host, port):
            self.host = host
            self.port = port

        def start(self):
            raise OSError("port is occupied")

    existing = ExistingServer()
    window = SimpleNamespace(
        _browser_capture_server=existing,
        browser_capture_received=SimpleNamespace(emit=lambda _sentence: None),
        settings={"browser_capture_host": "127.0.0.2", "browser_capture_port": 9123},
    )
    monkeypatch.setattr(main_window, "BrowserCaptureServer", FailingServer)

    assert BarskyApp.restart_browser_capture_server(window) is False
    assert existing.stop_calls == 1
    assert existing.start_calls == 1
    assert window._browser_capture_server is existing


def test_capture_daemon_uses_the_documented_default_port():
    from kgb_srs.browser_capture import CAPTURE_PORT

    assert CAPTURE_PORT == 8010


def test_extension_manifest_declares_a_manifest_v3_context_menu_capture():
    extension_directory = PROJECT_ROOT / "browser_extension"
    manifest = json.loads((extension_directory / "manifest.json").read_text("utf-8"))
    worker = (extension_directory / manifest["background"]["service_worker"]).read_text(
        "utf-8"
    )

    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {
        "contextMenus", "storage", "activeTab", "scripting"
    }
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
    assert manifest["optional_host_permissions"] == ["http://*/*"]
    assert manifest["options_ui"]["page"] == "options.html"
    assert "commands" in manifest
    assert "send-selected-text" in manifest["commands"]
    assert "info.selectionText" in worker
    assert "chrome.storage.local" in worker
    assert "chrome.commands.onCommand" in worker
    assert "chrome.scripting.executeScript" in worker
    assert "options.html" == (extension_directory / "options.html").name
    options_page = extension_directory / "options.html"
    options_script = extension_directory / "options.js"
    assert options_page.is_file()
    assert options_script.is_file()
    options_source = options_script.read_text("utf-8")
    assert "chrome.permissions.request" in options_source
    assert "return `http://${host}/*`;" in options_source
    assert "${host}:${port}/*" not in options_source
    assert "isLoopbackIPv4" in options_source
