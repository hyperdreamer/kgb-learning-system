"""TTS (Text-to-Speech) worker thread using Edge TTS."""

import os
import asyncio
import tempfile
import uuid

import edge_tts

from PyQt6.QtCore import QThread, pyqtSignal


class TTSWorker(QThread):
    """Worker thread that generates TTS audio without blocking the GUI."""

    finished = pyqtSignal(str)
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
        return temp_file

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            file_path = loop.run_until_complete(self.generate_audio())
            self.finished.emit(file_path)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()
