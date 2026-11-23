import base64
import sys
import types
import unittest
from unittest.mock import Mock, patch

import tools.get_user_pov as pov_tool
from model_provider import build_tool_schemas


class GetUserPovToolTests(unittest.TestCase):
    def setUp(self) -> None:
        for conversation_id in ("pov-test", "other-session"):
            pov_tool.clear_user_pov_conversation(conversation_id)

    def tearDown(self) -> None:
        for conversation_id in ("pov-test", "other-session"):
            pov_tool.clear_user_pov_conversation(conversation_id)

    @staticmethod
    def _fake_api_ws(request: Mock, conversation_id: str = "pov-test") -> types.ModuleType:
        fake_api_ws = types.ModuleType("api_ws")
        fake_api_ws.get_active_conversation_id = Mock(return_value=conversation_id)
        fake_api_ws.request_unity_tool_threadsafe = request
        return fake_api_ws

    def test_decodes_image_and_passes_metadata_to_analyzer(self) -> None:
        image_bytes = b"jpeg-test-payload"
        request = Mock(
            return_value={
                "ok": True,
                "image": {
                    "mime_type": "image/jpeg",
                    "base64": base64.b64encode(image_bytes).decode("ascii"),
                    "width": 1024,
                    "height": 768,
                    "source": "meta_quest_left_passthrough_camera",
                },
            }
        )
        analyzer = Mock()
        analyzer.analyze.return_value = "stub result"
        fake_api_ws = self._fake_api_ws(request)

        with patch.dict(sys.modules, {"api_ws": fake_api_ws}):
            with patch.object(pov_tool, "_build_vision_analyzer", return_value=analyzer):
                result = pov_tool.get_user_pov("What is in front of me?")

        self.assertEqual(result, "stub result")
        request.assert_called_once_with("get_user_pov", timeout_seconds=30.0)
        analyzer.analyze.assert_called_once_with(
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            metadata={
                "mime_type": "image/jpeg",
                "width": 1024,
                "height": 768,
                "source": "meta_quest_left_passthrough_camera",
            },
            question="What is in front of me?",
        )

    def test_continues_existing_conversation_without_capturing_another_image(self) -> None:
        image_bytes = b"jpeg-test-payload"
        request = Mock(
            return_value={
                "ok": True,
                "image": {
                    "mime_type": "image/jpeg",
                    "base64": base64.b64encode(image_bytes).decode("ascii"),
                },
            }
        )
        analyzer = Mock()
        analyzer.analyze.return_value = "There are two warning lights."
        analyzer.continue_conversation.return_value = "The left one is amber."
        fake_api_ws = self._fake_api_ws(request)

        with patch.dict(sys.modules, {"api_ws": fake_api_ws}):
            with patch.object(pov_tool, "_build_vision_analyzer", return_value=analyzer):
                first = pov_tool.get_user_pov("How many warning lights are on?")
                follow_up = pov_tool.get_user_pov(
                    "What color is the left one?",
                    continue_conversation=True,
                )

        self.assertEqual(first, "There are two warning lights.")
        self.assertEqual(follow_up, "The left one is amber.")
        request.assert_called_once_with("get_user_pov", timeout_seconds=30.0)
        analyzer.continue_conversation.assert_called_once_with(
            question="What color is the left one?"
        )

    def test_new_capture_permanently_replaces_previous_conversation(self) -> None:
        request = Mock(
            return_value={
                "ok": True,
                "image": {
                    "mime_type": "image/jpeg",
                    "base64": base64.b64encode(b"image").decode("ascii"),
                },
            }
        )
        first_analyzer = Mock()
        first_analyzer.analyze.return_value = "first"
        second_analyzer = Mock()
        second_analyzer.analyze.return_value = "second"
        second_analyzer.continue_conversation.return_value = "second follow-up"
        fake_api_ws = self._fake_api_ws(request)

        with patch.dict(sys.modules, {"api_ws": fake_api_ws}):
            with patch.object(
                pov_tool,
                "_build_vision_analyzer",
                side_effect=[first_analyzer, second_analyzer],
            ):
                pov_tool.get_user_pov("First image")
                pov_tool.get_user_pov("Replacement image")
                result = pov_tool.get_user_pov("Follow up", continue_conversation=True)

        self.assertEqual(result, "second follow-up")
        self.assertEqual(request.call_count, 2)
        first_analyzer.continue_conversation.assert_not_called()
        second_analyzer.continue_conversation.assert_called_once_with(question="Follow up")

    def test_failed_reset_does_not_restore_previous_conversation(self) -> None:
        request = Mock(
            side_effect=[
                {
                    "ok": True,
                    "image": {
                        "mime_type": "image/jpeg",
                        "base64": base64.b64encode(b"image").decode("ascii"),
                    },
                },
                {
                    "ok": False,
                    "error": {
                        "code": "capture_timeout",
                        "message": "No frame arrived.",
                    },
                },
            ]
        )
        analyzer = Mock()
        analyzer.analyze.return_value = "first"
        fake_api_ws = self._fake_api_ws(request)

        with patch.dict(sys.modules, {"api_ws": fake_api_ws}):
            with patch.object(pov_tool, "_build_vision_analyzer", return_value=analyzer):
                pov_tool.get_user_pov("First image")
                failed_reset = pov_tool.get_user_pov("Replacement image")
                follow_up = pov_tool.get_user_pov(
                    "Return to the first image",
                    continue_conversation=True,
                )

        self.assertIn("capture_timeout", failed_reset)
        self.assertIn("No visual conversation is available", follow_up)
        analyzer.continue_conversation.assert_not_called()

    def test_cannot_continue_another_or_missing_session(self) -> None:
        request = Mock()
        analyzer = Mock()
        fake_api_ws = self._fake_api_ws(request, conversation_id="other-session")

        with patch.dict(sys.modules, {"api_ws": fake_api_ws}):
            with patch.object(pov_tool, "_build_vision_analyzer", return_value=analyzer):
                result = pov_tool.get_user_pov("What color was it?", continue_conversation=True)

        self.assertIn("No visual conversation is available", result)
        request.assert_not_called()
        analyzer.continue_conversation.assert_not_called()

    def test_returns_correlated_unity_error_without_calling_analyzer(self) -> None:
        request = Mock(
            return_value={
                "ok": False,
                "error": {
                    "code": "permission_denied",
                    "message": "Headset camera permission was denied.",
                },
            }
        )
        analyzer = Mock()
        fake_api_ws = self._fake_api_ws(request)

        with patch.dict(sys.modules, {"api_ws": fake_api_ws}):
            with patch.object(pov_tool, "_build_vision_analyzer", return_value=analyzer):
                result = pov_tool.get_user_pov()

        self.assertIn("permission_denied", result)
        analyzer.analyze.assert_not_called()

    def test_rejects_invalid_base64(self) -> None:
        request = Mock(
            return_value={
                "ok": True,
                "image": {
                    "mime_type": "image/jpeg",
                    "base64": "not base64!",
                },
            }
        )
        fake_api_ws = self._fake_api_ws(request)

        with patch.dict(sys.modules, {"api_ws": fake_api_ws}):
            result = pov_tool.get_user_pov()

        self.assertIn("invalid base64", result)

    def test_tool_schema_exposes_boolean_continuation_control(self) -> None:
        function_schema = build_tool_schemas({"get_user_pov": pov_tool.get_user_pov})[
            "get_user_pov"
        ]["function"]
        schema = function_schema["parameters"]

        self.assertEqual(
            schema["properties"]["continue_conversation"]["type"],
            "boolean",
        )
        self.assertNotIn("continue_conversation", schema.get("required", []))
        self.assertIn("right now", function_schema["description"])
        self.assertIn("exact same captured image", function_schema["description"])
        self.assertIn(
            "exact same frozen image",
            schema["properties"]["continue_conversation"]["description"],
        )


if __name__ == "__main__":
    unittest.main()
