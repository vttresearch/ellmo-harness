import io
from queue import SimpleQueue
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch
import wave

from tk_voice import VoiceTurn
from UI import SimpleChatUI


class VoiceTurnTests(TestCase):
    def test_recording_posts_unity_wav_and_returns_transcript(self):
        samples = b"\x12\x34" * 160
        stream = MagicMock()

        def open_stream(**kwargs):
            self.assertEqual(kwargs["samplerate"], 16000)
            self.assertEqual(kwargs["channels"], 1)
            self.assertEqual(kwargs["dtype"], "int16")
            kwargs["callback"](samples, 160, None, None)
            return stream

        microphone = SimpleNamespace(
            RawInputStream=open_stream, PortAudioError=RuntimeError
        )
        response = Mock(status_code=200, content=b"  Show me the demo.\n")

        with patch.dict(sys.modules, {"sounddevice": microphone}), patch(
            "tk_voice.requests.post", return_value=response
        ) as post:
            turn = VoiceTurn("http://127.0.0.1:5050/")
            turn.stopped.set()
            self.assertEqual(turn.result.get(timeout=2), ("Show me the demo.", None))

        stream.__exit__.assert_called_once()
        self.assertEqual(post.call_args.args, ("http://127.0.0.1:5050/generate-text",))
        name, audio, mime = post.call_args.kwargs["files"]["audio"]
        self.assertEqual((name, mime), ("recording.wav", "audio/wav"))
        with wave.open(io.BytesIO(audio), "rb") as recording:
            self.assertEqual(recording.getframerate(), 16000)
            self.assertEqual(recording.getnchannels(), 1)
            self.assertEqual(recording.getsampwidth(), 2)
            self.assertEqual(recording.readframes(160), samples)

    def test_transcript_uses_normal_send_callback_and_ignores_stale_results(self):
        result = SimpleQueue()
        result.put(("Show me the demo.", None))
        turn = SimpleNamespace(result=result)
        view = SimpleNamespace(
            _voice_turn=None, _voice_button=Mock(),
            show_user_message=Mock(), on_send=Mock(),
        )
        view._submit_message = SimpleChatUI._submit_message.__get__(view)

        SimpleChatUI._poll_voice(view, turn)
        view.on_send.assert_not_called()

        view._voice_turn = turn
        SimpleChatUI._poll_voice(view, turn)
        view.show_user_message.assert_called_once_with("Show me the demo.")
        view.on_send.assert_called_once_with("Show me the demo.")
