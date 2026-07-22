"""Regression tests for text-to-speech temporary-file cleanup."""

import asyncio
import os
import stat

import pytest

from .qt_helpers import qt_app as _qt_app


class TestTtsRegressions:
    """TTS payload formatting and temporary-file lifecycle regressions."""

    def test_prepare_tts_text_adds_pauses_to_unpunctuated_segments(self):
        from kgb_srs.tts import prepare_tts_text

        assert prepare_tts_text(
            "A sentence without punctuation\nword\nmulti-word phrase"
        ) == "A sentence without punctuation.\nword.\nmulti-word phrase."

    def test_prepare_tts_text_keeps_punctuation_inside_closing_quotes(self):
        from kgb_srs.tts import prepare_tts_text

        assert prepare_tts_text('"Finished."\n(What?)\n「終わり。」') == (
            '"Finished."\n(What?)\n「終わり。」'
        )

    def test_prepare_tts_text_keeps_common_non_latin_terminal_punctuation(self):
        from kgb_srs.tts import prepare_tts_text

        assert prepare_tts_text("هل انتهيت؟\nयह समाप्त है।") == (
            "هل انتهيت؟\nयह समाप्त है।"
        )

    def test_card_speech_text_keeps_card_fields_as_tts_segments(self):
        _qt_app()
        from kgb_srs.review_controller import _card_speech_text

        assert _card_speech_text(
            "A sentence without punctuation", "word or phrase"
        ) == "A sentence without punctuation\nword or phrase"

    def test_sentence_card_tts_pauses_between_sentence_and_each_expression(self):
        _qt_app()
        import sqlite3

        from kgb_srs.review_controller import _sentence_card_speech_text
        from kgb_srs.schema import init_db, insert_sentence_card
        from kgb_srs.tts import prepare_tts_text

        conn = sqlite3.connect(":memory:")
        try:
            init_db(conn)
            sentence = "A river flows through a valley"
            card_id = insert_sentence_card(
                conn,
                sentence,
                [
                    ("river", "a natural stream"),
                    ("valley", "a low area"),
                ],
            )

            assert prepare_tts_text(
                _sentence_card_speech_text(conn, card_id, sentence)
            ) == (
                "A river flows through a valley.\n"
                "river: a natural stream.\n"
                "valley: a low area."
            )
        finally:
            conn.close()

    def test_word_phrase_card_tts_pauses_between_senses_and_examples(self):
        _qt_app()
        from types import SimpleNamespace

        from kgb_srs.catalog import DatabaseType
        from kgb_srs.main_window import BarskyApp
        from kgb_srs.senses import Sense, build_word_phrase_back_from_senses
        from kgb_srs.tts import prepare_tts_text

        class CapturingCard:
            def set_text(self, display_text, is_flipped, speech_text):
                self.display_text = display_text
                self.is_flipped = is_flipped
                self.speech_text = speech_text

        senses = [
            Sense(
                1,
                "bank",
                "a financial institution",
                "bank",
                "a financial institution",
            ),
            Sense(
                2,
                "bank",
                "the side of a river",
                "bank",
                "the side of a river",
            ),
        ]
        back = build_word_phrase_back_from_senses(
            senses,
            {
                1: ["I deposited money at the bank."],
                2: ["We sat by the bank of the river."],
            },
        )
        card = CapturingCard()
        window = SimpleNamespace(
            current_card=(1, "bank", back, 1),
            _db_type=DatabaseType.LANGUAGE_WORD_PHRASE,
            card_ui=card,
        )
        window._build_word_phrase_card_display = (
            BarskyApp._build_word_phrase_card_display.__get__(window)
        )

        BarskyApp.flip_card(window)

        assert prepare_tts_text(card.speech_text) == (
            "bank.\n"
            "a financial institution.\n"
            "I deposited money at the bank.\n"
            "the side of a river.\n"
            "We sat by the bank of the river."
        )

    def test_redrawn_word_phrase_card_keeps_tts_pauses(self, monkeypatch):
        _qt_app()
        from types import SimpleNamespace

        import kgb_srs.review_controller as review_controller
        from kgb_srs.catalog import DatabaseType
        from kgb_srs.main_window import BarskyApp
        from kgb_srs.tts import prepare_tts_text

        class CapturingCard:
            def __init__(self, *_args):
                self.speech_text = ""

            def set_text(self, _display_text, _is_flipped, speech_text):
                self.speech_text = speech_text

        class FakeScene:
            def width(self):
                return 900

            def height(self):
                return 700

            def addItem(self, _item):
                return None

        back = (
            "1. a financial institution\n\n"
            "    > *I deposited money at the **bank**.*\n\n"
            "> \n\n"
            "2. the side of a river\n\n"
            "    > *We sat by the **bank** of the river.*"
        )
        window = SimpleNamespace(
            current_card=(1, "bank", back, 1),
            is_current_flipped=True,
            _db_type=DatabaseType.LANGUAGE_WORD_PHRASE,
            scene=FakeScene(),
            _zone_y=600,
            card_ui=None,
            _update_button_visibility=lambda: None,
        )
        window._build_word_phrase_card_display = (
            BarskyApp._build_word_phrase_card_display.__get__(window)
        )
        monkeypatch.setattr(review_controller, "FlashCardItem", CapturingCard)

        BarskyApp.draw_card_ui(window)

        assert prepare_tts_text(window.card_ui.speech_text) == (
            "bank.\n"
            "a financial institution.\n"
            "I deposited money at the bank.\n"
            "the side of a river.\n"
            "We sat by the bank of the river."
        )

    def test_generate_audio_sends_paused_segments_to_edge_tts(
        self, tmp_path, monkeypatch
    ):
        _qt_app()
        import kgb_srs.tts as tts

        original_mkstemp = tts.tempfile.mkstemp

        def mkstemp_in_test_dir(*args, **kwargs):
            kwargs["dir"] = str(tmp_path)
            return original_mkstemp(*args, **kwargs)

        class FakeCommunicate:
            text = None

            def __init__(self, text, _voice):
                FakeCommunicate.text = text

            async def save(self, path):
                with open(path, "wb") as audio:
                    audio.write(b"fake audio")

        monkeypatch.setattr(tts.tempfile, "mkstemp", mkstemp_in_test_dir)
        monkeypatch.setattr(tts.edge_tts, "Communicate", FakeCommunicate)

        worker = tts.TTSWorker(
            "A sentence without punctuation\nword\nmulti-word phrase", "voice"
        )
        path = asyncio.run(worker.generate_audio())

        assert FakeCommunicate.text == (
            "A sentence without punctuation.\nword.\nmulti-word phrase."
        )
        tts.unlink_tts_temp(path)

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

    def test_immediate_close_discards_queued_tts_audio_from_completed_worker(
        self, tmp_path, monkeypatch
    ):
        """An idle worker's queued payload cannot play after terminal close."""
        _qt_app()
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from PyQt6.QtCore import QObject, pyqtSignal

        import kgb_srs.main_window as mw

        late_audio = tmp_path / "barsky_tts_late_after_close.mp3"
        late_audio.write_bytes(b"audio")

        class FakeWorker(QObject):
            audio_ready = pyqtSignal(str)
            error = pyqtSignal(str)
            finished = pyqtSignal()
            instance = None

            def __init__(self, *_args):
                super().__init__()
                FakeWorker.instance = self

            def start(self):
                return None

            def deleteLater(self):
                return None

            def isRunning(self):
                return False

        class Event:
            accepted = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                raise AssertionError("a completed TTS worker must not defer close")

        monkeypatch.setattr(mw, "TTSWorker", FakeWorker)
        player = MagicMock()
        card = object()
        card_ui = object()
        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=None,
            settings={"tts_voice": "en-US-AvaMultilingualNeural"},
            player=player,
            current_card=card,
            card_ui=card_ui,
            width=lambda: 800,
            height=lambda: 600,
            current_db_path=None,
            _save_settings=lambda: None,
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

        mw.BarskyApp.speak_text(window, "hello", button)
        event = Event()
        mw.BarskyApp.closeEvent(window, event)
        FakeWorker.instance.audio_ready.emit(str(late_audio))

        assert event.accepted
        assert window._terminal_closing
        assert not late_audio.exists()
        player.setSource.assert_not_called()
        player.play.assert_not_called()

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

    def test_late_tts_audio_for_redrawn_same_card_plays_without_old_button_update(
        self, tmp_path, monkeypatch
    ):
        _qt_app()
        from types import SimpleNamespace
        from PyQt6.QtCore import QObject, pyqtSignal
        import kgb_srs.main_window as mw

        audio = tmp_path / "barsky_tts_redrawn_same_card.mp3"
        audio.write_bytes(b"audio")

        class FakeWorker(QObject):
            audio_ready = pyqtSignal(str)
            error = pyqtSignal(str)
            finished = pyqtSignal()
            instance = None

            def __init__(self, *_args):
                super().__init__()
                FakeWorker.instance = self

            def start(self):
                return None

            def deleteLater(self):
                return None

            def isRunning(self):
                return False

        class FakePlayer:
            source_calls = 0
            play_calls = 0

            def setSource(self, *_args):
                self.source_calls += 1

            def play(self):
                self.play_calls += 1

        monkeypatch.setattr(mw, "TTSWorker", FakeWorker)
        card = object()
        player = FakePlayer()
        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=None,
            settings={"tts_voice": "en-US-AvaMultilingualNeural"},
            player=player,
            current_card=card,
            card_ui=object(),
        )
        window._cleanup_tts_temp = mw.BarskyApp._cleanup_tts_temp.__get__(
            window, type(window)
        )
        button = SimpleNamespace(updates=[])
        button.setEnabled = lambda value: button.updates.append(("enabled", value))
        button.setText = lambda value: button.updates.append(("text", value))

        mw.BarskyApp.speak_text(window, "card A", button)
        button.updates.clear()
        window.card_ui = object()  # resize redraw
        FakeWorker.instance.audio_ready.emit(str(audio))

        assert window._tts_temp_path == str(audio)
        assert audio.exists()
        assert player.source_calls == 1
        assert player.play_calls == 1
        assert button.updates == []

    def test_late_tts_error_for_redrawn_same_card_warns_without_old_button_update(
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

            def __init__(self, *_args):
                super().__init__()
                FakeWorker.instance = self

            def start(self):
                return None

            def deleteLater(self):
                return None

            def isRunning(self):
                return False

        warnings = []
        monkeypatch.setattr(mw, "TTSWorker", FakeWorker)
        monkeypatch.setattr(
            mw.QMessageBox, "warning", lambda *args: warnings.append(args)
        )
        card = object()
        window = SimpleNamespace(
            tts_worker=None,
            _tts_temp_path=None,
            settings={"tts_voice": "en-US-AvaMultilingualNeural"},
            current_card=card,
            card_ui=object(),
        )
        window._cleanup_tts_temp = mw.BarskyApp._cleanup_tts_temp.__get__(
            window, type(window)
        )
        button = SimpleNamespace(updates=[])
        button.setEnabled = lambda value: button.updates.append(("enabled", value))
        button.setText = lambda value: button.updates.append(("text", value))

        mw.BarskyApp.speak_text(window, "card A", button)
        button.updates.clear()
        window.card_ui = object()  # resize redraw
        FakeWorker.instance.error.emit("generation failed")

        assert len(warnings) == 1
        assert button.updates == []
