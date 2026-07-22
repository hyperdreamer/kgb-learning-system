"""Regression tests for text-to-speech temporary-file cleanup."""

from .qt_helpers import qt_app as _qt_app


class TestTtsTempCleanup:
    """R2-2: temp barsky_tts_*.mp3 files must not linger forever."""

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
