import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vision_analyzer import OllamaVisionAnalyzer


class OllamaVisionAnalyzerTests(unittest.TestCase):
    def test_sends_raw_image_with_thinking_disabled(self) -> None:
        client = Mock()
        client.chat.return_value = SimpleNamespace(
            message=SimpleNamespace(content="A red toolbox is visible.")
        )
        fake_ollama = types.ModuleType("ollama")
        fake_ollama.Client = Mock(return_value=client)
        analyzer = OllamaVisionAnalyzer(
            host="http://127.0.0.1:11434/",
            model="qwen3.8:27b-mlx",
            think=False,
            timeout_seconds=120.0,
        )

        with patch.dict(sys.modules, {"ollama": fake_ollama}):
            result = analyzer.analyze(
                image_bytes=b"raw-jpeg-bytes",
                mime_type="image/jpeg",
                metadata={"width": 1024, "height": 768},
                question="What object is in front of me?",
            )

        self.assertEqual(result, "A red toolbox is visible.")
        fake_ollama.Client.assert_called_once_with(
            host="http://127.0.0.1:11434",
            timeout=120.0,
        )
        call = client.chat.call_args
        self.assertEqual(call.kwargs["model"], "qwen3.8:27b-mlx")
        self.assertEqual(call.kwargs["think"], False)
        self.assertEqual(call.kwargs["stream"], False)
        self.assertEqual(
            call.kwargs["messages"][0]["images"],
            [b"raw-jpeg-bytes"],
        )
        self.assertIn(
            "What object is in front of me?",
            call.kwargs["messages"][0]["content"],
        )

    def test_follow_up_replays_the_image_and_prior_answer(self) -> None:
        client = Mock()
        client.chat.side_effect = [
            SimpleNamespace(message=SimpleNamespace(content="Two switches are visible.")),
            SimpleNamespace(message=SimpleNamespace(content="The right switch is red.")),
        ]
        fake_ollama = types.ModuleType("ollama")
        fake_ollama.Client = Mock(return_value=client)
        analyzer = OllamaVisionAnalyzer(host="http://ollama:11434", model="vision")

        with patch.dict(sys.modules, {"ollama": fake_ollama}):
            first = analyzer.analyze(
                image_bytes=b"raw-jpeg-bytes",
                mime_type="image/jpeg",
                metadata={},
                question="How many switches are visible?",
            )
            follow_up = analyzer.continue_conversation(
                question="What color is the right one?"
            )

        self.assertEqual(first, "Two switches are visible.")
        self.assertEqual(follow_up, "The right switch is red.")
        follow_up_messages = client.chat.call_args_list[1].kwargs["messages"]
        self.assertEqual(len(follow_up_messages), 3)
        self.assertEqual(follow_up_messages[0]["images"], [b"raw-jpeg-bytes"])
        self.assertEqual(
            follow_up_messages[1],
            {"role": "assistant", "content": "Two switches are visible."},
        )
        self.assertIn(
            "What color is the right one?",
            follow_up_messages[2]["content"],
        )

    def test_follow_up_requires_a_started_conversation(self) -> None:
        analyzer = OllamaVisionAnalyzer(host="http://ollama:11434", model="vision")

        with self.assertRaisesRegex(RuntimeError, "No visual conversation"):
            analyzer.continue_conversation(question="What color was it?")

    def test_empty_model_response_is_an_error(self) -> None:
        client = Mock()
        client.chat.return_value = {"message": {"content": ""}}
        fake_ollama = types.ModuleType("ollama")
        fake_ollama.Client = Mock(return_value=client)
        analyzer = OllamaVisionAnalyzer(host="http://ollama:11434", model="vision")

        with patch.dict(sys.modules, {"ollama": fake_ollama}):
            with self.assertRaisesRegex(RuntimeError, "empty visual analysis"):
                analyzer.analyze(
                    image_bytes=b"raw-jpeg-bytes",
                    mime_type="image/jpeg",
                    metadata={},
                    question="Describe this image.",
                )


if __name__ == "__main__":
    unittest.main()
