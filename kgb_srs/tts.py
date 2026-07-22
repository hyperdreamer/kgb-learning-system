"""TTS (Text-to-Speech) worker thread using Edge TTS."""

import os
import asyncio
import tempfile

import edge_tts

from PyQt6.QtCore import QThread, pyqtSignal


_TTS_TERMINAL_PUNCTUATION = frozenset(".!?…。！？؟۔।॥")
_TTS_TRAILING_CLOSERS = "\"'”’»›）)]}】〕〉》」』"


def _ends_with_tts_terminal_punctuation(segment):
    """Return whether terminal punctuation precedes closing delimiters."""
    content = segment.rstrip(_TTS_TRAILING_CLOSERS)
    return bool(content) and content[-1] in _TTS_TERMINAL_PUNCTUATION


def prepare_tts_text(text):
    """End each non-empty speech segment so Edge TTS pauses after it."""
    segments = (line.strip() for line in text.splitlines())
    return "\n".join(
        segment
        if _ends_with_tts_terminal_punctuation(segment)
        else f"{segment}."
        for segment in segments
        if segment
    )


def unlink_tts_temp(path):
    """Best-effort delete of a TTS temp MP3. Returns None."""
    if not path:
        return None
    try:
        os.unlink(path)
    except OSError:
        pass
    return None


class TTSWorker(QThread):
    """Worker thread that generates TTS audio without blocking the GUI."""

    audio_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text, voice):
        super().__init__()
        self.text = text
        self.voice = voice

    async def generate_audio(self):
        fd, temp_file = tempfile.mkstemp(prefix="barsky_tts_", suffix=".mp3")
        os.close(fd)

        communicate = edge_tts.Communicate(prepare_tts_text(self.text), self.voice)
        try:
            await communicate.save(temp_file)
        except Exception:
            unlink_tts_temp(temp_file)
            raise
        return temp_file

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            file_path = loop.run_until_complete(self.generate_audio())
            self.audio_ready.emit(file_path)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()


class VoiceListWorker(QThread):
    """Background worker that fetches the list of available Edge TTS voices."""

    voices_ready = pyqtSignal(
        list
    )  # emits list of (ShortName, Locale, Gender, FriendlyName)
    error = pyqtSignal(str)

    async def _fetch(self):
        voices = await edge_tts.list_voices()
        result = []
        for v in voices:
            result.append(
                (
                    v["ShortName"],
                    v["Locale"],
                    v.get("Gender", ""),
                    v.get("FriendlyName", v["ShortName"]),
                )
            )
        return result

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            voices = loop.run_until_complete(self._fetch())
            self.voices_ready.emit(voices)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()
