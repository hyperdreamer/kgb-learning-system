"""TTS (Text-to-Speech) worker thread using Edge TTS."""

import os
import asyncio
import tempfile
import uuid

import edge_tts

from PyQt6.QtCore import QThread, pyqtSignal


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
        unique_name = f"barsky_tts_{uuid.uuid4().hex[:8]}.mp3"
        temp_file = os.path.join(tempfile.gettempdir(), unique_name)

        communicate = edge_tts.Communicate(self.text, self.voice)
        await communicate.save(temp_file)
        try:
            os.chmod(temp_file, 0o600)
        except OSError:
            pass
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
