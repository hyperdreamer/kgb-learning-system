"""Regression tests for text-to-speech temporary-file cleanup."""

import asyncio
import os
import stat

import pytest

from .qt_helpers import qt_app as _qt_app


class TestTtsTempCleanup:
    """R2-2: temp barsky_tts_*.mp3 files must not linger forever."""

    def test_generate_audio_precreates_private_temp_file(self, tmp_path, monkeypatch):
        _qt_app()
        import kgb_srs.tts as tts

        original_mkstemp = tts.tempfile.mkstemp

        def mkstemp_in_test_dir(*args, **kwargs):
            kwargs["dir"] = str(tmp_path)
            return original_mkstemp(*args, **kwargs)

        class FakeCommunicate:
            saved_path = None

            def __init__(self, *_args):
                pass

            async def save(self, path):
                FakeCommunicate.saved_path = path
                assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
                with open(path, "wb") as audio:
                    audio.write(b"fake audio")

        monkeypatch.setattr(tts.tempfile, "mkstemp", mkstemp_in_test_dir)
        monkeypatch.setattr(tts.edge_tts, "Communicate", FakeCommunicate)

        path = asyncio.run(tts.TTSWorker("hello", "voice").generate_audio())

        assert path == FakeCommunicate.saved_path
        assert os.path.basename(path).startswith("barsky_tts_")
        assert path.endswith(".mp3")
        assert os.path.exists(path)
        tts.unlink_tts_temp(path)

    def test_generate_audio_removes_temp_file_when_save_fails(
        self, tmp_path, monkeypatch
    ):
        _qt_app()
        import kgb_srs.tts as tts

        original_mkstemp = tts.tempfile.mkstemp

        def mkstemp_in_test_dir(*args, **kwargs):
            kwargs["dir"] = str(tmp_path)
            return original_mkstemp(*args, **kwargs)

        class FailingCommunicate:
            saved_path = None

            def __init__(self, *_args):
                pass

            async def save(self, path):
                FailingCommunicate.saved_path = path
                assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
                raise RuntimeError("save failed")

        monkeypatch.setattr(tts.tempfile, "mkstemp", mkstemp_in_test_dir)
        monkeypatch.setattr(tts.edge_tts, "Communicate", FailingCommunicate)

        with pytest.raises(RuntimeError, match="save failed"):
            asyncio.run(tts.TTSWorker("hello", "voice").generate_audio())
        assert not os.path.exists(FailingCommunicate.saved_path)

    def test_unlink_tts_temp_removes_file(self, tmp_path):
        from kgb_srs.tts import unlink_tts_temp

        p = tmp_path / "barsky_tts_deadbeef.mp3"
        p.write_bytes(b"fake")
        assert p.exists()
        assert unlink_tts_temp(str(p)) is None
        assert not p.exists()
        # Missing path is a no-op.
        assert unlink_tts_temp(str(p)) is None
        assert unlink_tts_temp(None) is None

    def test_speak_text_replaces_previous_temp_file(self, tmp_path, monkeypatch):
        _qt_app()
        from types import SimpleNamespace
        from PyQt6.QtCore import QObject, pyqtSignal
        import kgb_srs.main_window as mw

        old = tmp_path / "barsky_tts_old.mp3"
        new = tmp_path / "barsky_tts_new.mp3"
        old.write_bytes(b"old")
        new.write_bytes(b"new")

        class FakeWorker(QObject):
            audio_ready = pyqtSignal(str)
            error = pyqtSignal(str)
            finished = pyqtSignal()

            def __init__(self, text, voice):
                super().__init__()
                self.text = text
                self.voice = voice

            def start(self):
                self.audio_ready.emit(str(new))
                self.finished.emit()

            def deleteLater(self):
                return None

            def isRunning(self):
                return False

        monkeypatch.setattr(mw, "TTSWorker", FakeWorker)

        class FakePlayer:
            def setSource(self, *_a, **_k):
                return None

            def play(self):
                return None

        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=str(old),
            settings={"tts_voice": "en-US-AvaMultilingualNeural"},
            player=FakePlayer(),
        )
        # Bind real helpers onto the lightweight stand-in.
        window._cleanup_tts_temp = mw.BarskyApp._cleanup_tts_temp.__get__(
            window, type(window)
        )
        btn = SimpleNamespace(enabled=True, text="🔊 Listen")
        btn.setEnabled = lambda v: setattr(btn, "enabled", v)
        btn.setText = lambda t: setattr(btn, "text", t)

        mw.BarskyApp.speak_text(window, "hello", btn)

        assert not old.exists()
        assert window._tts_temp_path == str(new)
        assert new.exists()

    def test_second_speak_stops_playback_before_cleaning_previous_audio(
        self, monkeypatch
    ):
        _qt_app()
        from types import SimpleNamespace
        from PyQt6.QtCore import QObject, pyqtSignal
        import kgb_srs.main_window as mw
        import kgb_srs.tts as tts

        first_audio = "/tmp/barsky_tts_first.mp3"
        second_audio = "/tmp/barsky_tts_second.mp3"
        events = []

        class FakeWorker(QObject):
            audio_ready = pyqtSignal(str)
            error = pyqtSignal(str)
            finished = pyqtSignal()
            paths = iter((first_audio, second_audio))

            def __init__(self, *_args):
                super().__init__()

            def start(self):
                self.audio_ready.emit(next(self.paths))
                self.finished.emit()

            def deleteLater(self):
                return None

            def isRunning(self):
                return False

        class FakePlayer:
            def setSource(self, *_args):
                return None

            def play(self):
                return None

            def stop(self):
                events.append(("stop", None))

        monkeypatch.setattr(mw, "TTSWorker", FakeWorker)
        monkeypatch.setattr(
            tts,
            "unlink_tts_temp",
            lambda path: events.append(("cleanup", path)) or None,
        )

        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=None,
            settings={"tts_voice": "en-US-AvaMultilingualNeural"},
            player=FakePlayer(),
        )
        window._stop_tts_playback = mw.BarskyApp._stop_tts_playback.__get__(
            window, type(window)
        )
        window._cleanup_tts_temp = mw.BarskyApp._cleanup_tts_temp.__get__(
            window, type(window)
        )
        button = SimpleNamespace()
        button.setEnabled = lambda _value: None
        button.setText = lambda _text: None

        mw.BarskyApp.speak_text(window, "first", button)
        events.clear()
        mw.BarskyApp.speak_text(window, "second", button)

        assert events[:2] == [("stop", None), ("cleanup", first_audio)]

    def test_immediate_close_stops_playback_before_cleaning_audio(self, monkeypatch):
        _qt_app()
        from types import SimpleNamespace
        import kgb_srs.main_window as mw
        import kgb_srs.tts as tts

        audio_path = "/tmp/barsky_tts_playing.mp3"
        events = []

        class FakePlayer:
            def stop(self):
                events.append(("stop", None))

        class Event:
            accepted = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                raise AssertionError("an idle TTS close must not be deferred")

        monkeypatch.setattr(
            tts,
            "unlink_tts_temp",
            lambda path: events.append(("cleanup", path)) or None,
        )
        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=audio_path,
            settings={},
            current_db_path=None,
            player=FakePlayer(),
            width=lambda: 800,
            height=lambda: 600,
            _save_settings=lambda: None,
        )
        window._stop_tts_playback = mw.BarskyApp._stop_tts_playback.__get__(
            window, type(window)
        )
        window._cleanup_tts_temp = mw.BarskyApp._cleanup_tts_temp.__get__(
            window, type(window)
        )
        event = Event()

        mw.BarskyApp.closeEvent(window, event)

        assert event.accepted
        assert events == [("stop", None), ("cleanup", audio_path)]

    def test_late_tts_audio_for_replaced_card_is_unlinked_without_playback(
        self, tmp_path, monkeypatch
    ):
        _qt_app()
        from types import SimpleNamespace
        from PyQt6.QtCore import QObject, pyqtSignal
        import kgb_srs.main_window as mw
        import kgb_srs.tts as tts

        late_audio = tmp_path / "barsky_tts_late.mp3"
        late_audio.write_bytes(b"late")

        class FakeWorker(QObject):
            audio_ready = pyqtSignal(str)
            error = pyqtSignal(str)
            finished = pyqtSignal()
            instance = None

            def __init__(self, text, voice):
                super().__init__()
                FakeWorker.instance = self

            def start(self):
                return None

            def deleteLater(self):
                return None

            def isRunning(self):
                return False

        monkeypatch.setattr(mw, "TTSWorker", FakeWorker)
        unlinked = []

        def unlink(path):
            unlinked.append(path)
            if path:
                late_audio.unlink(missing_ok=True)
            return None

        monkeypatch.setattr(tts, "unlink_tts_temp", unlink)

        class FakePlayer:
            source_calls = 0
            play_calls = 0

            def setSource(self, *_args):
                self.source_calls += 1

            def play(self):
                self.play_calls += 1

        original_card = object()
        original_ui = object()
        player = FakePlayer()
        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=None,
            settings={"tts_voice": "en-US-AvaMultilingualNeural"},
            player=player,
            current_card=original_card,
            card_ui=original_ui,
        )
        window._cleanup_tts_temp = mw.BarskyApp._cleanup_tts_temp.__get__(
            window, type(window)
        )
        button = SimpleNamespace(enabled=True, text="🔊 Listen", updates=[])
        button.setEnabled = lambda value: button.updates.append(("enabled", value))
        button.setText = lambda value: button.updates.append(("text", value))

        mw.BarskyApp.speak_text(window, "card A", button)
        button.updates.clear()
        unlinked.clear()
        window.current_card = object()
        window.card_ui = object()
        FakeWorker.instance.audio_ready.emit(str(late_audio))

        assert unlinked == [str(late_audio)]
        assert not late_audio.exists()
        assert player.source_calls == 0
        assert player.play_calls == 0
        assert button.updates == []

    def test_late_tts_error_for_replaced_card_does_not_touch_old_button(
        self, monkeypatch
    ):
        _qt_app()
        from types import SimpleNamespace
        from PyQt6.QtCore import QObject, pyqtSignal
        import kgb_srs.main_window as mw

        class FakeWorker(QObject):
            audio_ready = pyqtSignal(str)
            error = pyqtSignal(str)
            finished = pyqtSignal()
            instance = None

            def __init__(self, text, voice):
                super().__init__()
                FakeWorker.instance = self

            def start(self):
                return None

            def deleteLater(self):
                return None

            def isRunning(self):
                return False

        monkeypatch.setattr(mw, "TTSWorker", FakeWorker)
        warnings = []
        monkeypatch.setattr(
            mw.QMessageBox,
            "warning",
            lambda *args: warnings.append(args),
        )

        original_card = object()
        original_ui = object()
        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=None,
            settings={"tts_voice": "en-US-AvaMultilingualNeural"},
            current_card=original_card,
            card_ui=original_ui,
        )
        window._cleanup_tts_temp = mw.BarskyApp._cleanup_tts_temp.__get__(
            window, type(window)
        )
        button = SimpleNamespace(updates=[])
        button.setEnabled = lambda value: button.updates.append(("enabled", value))
        button.setText = lambda value: button.updates.append(("text", value))

        mw.BarskyApp.speak_text(window, "card A", button)
        button.updates.clear()
        window.current_card = object()
        window.card_ui = object()
        FakeWorker.instance.error.emit("late failure")

        assert warnings == []
        assert button.updates == []
