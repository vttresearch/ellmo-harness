"""Optional microphone input for the standalone Tk chat client."""

import io
from queue import SimpleQueue
from threading import Event, Thread
from urllib.parse import urlsplit
import wave

import requests


class VoiceTurn:
    """Capture one utterance and transcribe it without calling Tk from a worker."""

    def __init__(self, server_url):
        self.server_url = server_url.rstrip("/")
        self.stopped = Event()
        self.cancelled = Event()
        self.result = SimpleQueue()
        Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancelled.set()
        self.stopped.set()

    def _run(self):
        try:
            self.result.put((self._transcribe(), None))
        except Exception as exc:
            self.result.put(("", str(exc)))

    def _transcribe(self):
        url = urlsplit(self.server_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Set voice_input.server_url to the Unity speech service URL.")

        try:
            import sounddevice
        except (ImportError, OSError) as exc:
            raise RuntimeError("Install sounddevice and PortAudio in the Tk environment.") from exc

        if self.cancelled.is_set():
            return ""

        chunks = []

        def capture(data, _frames, _time, _status):
            chunks.append(bytes(data))

        try:
            with sounddevice.RawInputStream(
                samplerate=16000, channels=1, dtype="int16", callback=capture
            ):
                self.stopped.wait(60)
        except sounddevice.PortAudioError as exc:
            raise RuntimeError(f"Check microphone availability and permissions. {exc}") from exc
        finally:
            self.stopped.set()

        if self.cancelled.is_set() or not chunks:
            return ""

        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"".join(chunks))

        if self.cancelled.is_set():
            return ""
        try:
            response = requests.post(
                f"{self.server_url}/generate-text",
                files={"audio": ("recording.wav", audio.getvalue(), "audio/wav")},
                timeout=(3, 30),
                allow_redirects=False,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                "Transcription failed. Check that the Unity speech service is running "
                "and accepts POST /generate-text."
            ) from exc
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"The speech service returned HTTP {response.status_code}.")
        return response.content.decode("utf-8").strip()
