import asyncio
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import WebSocketDisconnect

import api_ws
import session as session_module
from conversation_store import ConversationManager
from model_provider import ProviderChatResponse, build_tool_schemas
from tools.change_page import change_page


class _FakeWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.outgoing: asyncio.Queue = asyncio.Queue()
        self.accepted = False
        self.accepted_event = asyncio.Event()
        self.closed = None

    async def accept(self) -> None:
        self.accepted = True
        self.accepted_event.set()

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive_json(self):
        message = await self.incoming.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def send_json(self, message) -> None:
        await self.outgoing.put(message)


class _PovRequestSession:
    def __init__(self) -> None:
        self.results = queue.Queue()
        self.current_conversation_id = None

    def step(self, text, on_event, *, stop_requested=None):
        result = api_ws.request_unity_tool_threadsafe(
            "get_user_pov",
            timeout_seconds=2.0,
        )
        self.results.put(result)
        return {"assistant_message": "done"}

    def save_snapshot(self, transcript, log):
        pass


class _RecordingSession(session_module.ConversationSession):
    def __init__(self, store, functions=None):
        with patch.object(session_module, "ConversationManager", return_value=store):
            super().__init__(
                {"test": {"provider": "ollama", "models": ["stub"]}},
                functions or {},
            )
        self.configure("test", "stub")
        self.saved = threading.Event()

    def save_snapshot(self, transcript, log):
        super().save_snapshot(transcript, log)
        self.saved.set()


def _response(content="done", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return ProviderChatResponse(message=message, history_message=message)


def _tool_call(name="marker"):
    return {"id": "call-1", "function": {"name": name, "arguments": {}}}


class ApiWsUnityToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_client_is_rejected_without_replacing_the_first(self):
        first, second, third = (_FakeWebSocket() for _ in range(3))
        session = _PovRequestSession()
        with patch.object(api_ws, "_get_or_create_session", return_value=session):
            endpoint = asyncio.create_task(api_ws.websocket_chat(first, "first"))
            await first.accepted_event.wait()
            try:
                # Even reusing the same logical id must not take over the socket.
                await api_ws.websocket_chat(second, "first")
                error = await second.outgoing.get()
                self.assertEqual(error["data"]["code"], "CLIENT_ALREADY_CONNECTED")
                self.assertEqual(second.closed[0], 1008)
                self.assertIs(api_ws._ACTIVE_WS, first)
                await api_ws.send_to_unity({"type": "routing-probe"})
                self.assertEqual((await first.outgoing.get())["type"], "routing-probe")
            finally:
                await first.incoming.put(WebSocketDisconnect())
                await asyncio.wait_for(endpoint, 2)
            await third.incoming.put(WebSocketDisconnect())
            await api_ws.websocket_chat(third, "third")
            self.assertTrue(third.accepted)
            self.assertIsNone(third.closed)
            self.assertIsNone(api_ws._ACTIVE_WS)

    async def test_failed_handshake_releases_the_slot(self):
        socket = _FakeWebSocket()
        async def reject():
            raise RuntimeError("handshake failed")
        socket.accept = reject
        await api_ws.websocket_chat(socket, "failed")
        self.assertIsNone(api_ws._ACTIVE_WS)

    async def test_completed_turns_save_messages_tool_evidence_and_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationManager(Path(tmp))
            session = _RecordingSession(store, {"marker": lambda: "tool evidence"})
            client = Mock()
            client.chat.side_effect = [
                _response("", [_tool_call()]), _response("first answer"),
                _response("second answer"),
            ]
            client.make_tool_result_message.side_effect = lambda **kw: {
                "role": "tool", "tool_call_id": "call-1", "content": kw["tool_output"],
            }
            websocket = _FakeWebSocket()
            with patch.object(api_ws, "_get_or_create_session", return_value=session), patch.object(
                session_module, "build_model_provider_client", return_value=client
            ):
                endpoint = asyncio.create_task(api_ws.websocket_chat(websocket, "history"))
                await websocket.accepted_event.wait()
                try:
                    for text in ("first question", "second question"):
                        session.saved.clear()
                        await websocket.incoming.put({"type": "user_message", "text": text})
                        self.assertTrue(await asyncio.to_thread(session.saved.wait, 2))
                        # The worker finished; let its awaiting task observe completion.
                        await asyncio.sleep(0.01)
                finally:
                    await websocket.incoming.put(WebSocketDisconnect())
                    await asyncio.wait_for(endpoint, 2)
            # Reopen from disk, using a fresh persistence object.
            record = ConversationManager(Path(tmp)).load_conversation(session.current_conversation_id)
            self.assertEqual([m["role"] for m in record["messages"]],
                             ["user", "assistant", "tool", "assistant", "user", "assistant"])
            self.assertEqual(record["messages"][2]["content"], "tool evidence")
            self.assertEqual(record["messages"][-1]["content"], "second answer")
            self.assertIn("User: first question", record["chat_transcript"])
            self.assertIn("Assistant: second answer", record["chat_transcript"])
            events = [json.loads(line) for line in record["log_text"].splitlines()]
            tool_result = next(e for e in events if e["data"]["kind"] == "tool_result")
            self.assertEqual(tool_result["data"]["tool_call_id"], "call-1")
            self.assertEqual(tool_result["data"]["tool_output"], "tool evidence")
            restored = _RecordingSession(store)
            restored.restore_conversation(session.current_conversation_id)
            self.assertEqual(restored.messages, record["messages"])

    async def test_model_error_still_saves_the_user_message_and_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationManager(Path(tmp))
            session = _RecordingSession(store)
            client = Mock()
            client.chat.side_effect = RuntimeError("mock provider failed")
            websocket = _FakeWebSocket()
            with patch.object(api_ws, "_get_or_create_session", return_value=session), patch.object(
                session_module, "build_model_provider_client", return_value=client
            ):
                endpoint = asyncio.create_task(api_ws.websocket_chat(websocket, "error"))
                await websocket.incoming.put({"type": "user_message", "text": "save me"})
                try:
                    self.assertTrue(await asyncio.to_thread(session.saved.wait, 2))
                finally:
                    await websocket.incoming.put(WebSocketDisconnect())
                    await asyncio.wait_for(endpoint, 2)
            record = store.load_conversation(session.current_conversation_id)
            self.assertEqual(record["messages"][0]["content"], "save me")
            self.assertIn("mock provider failed", record["log_text"])

    async def test_disconnect_stops_further_calls_and_holds_slot_until_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationManager(Path(tmp))
            marker = Mock(return_value="should not execute")
            session = _RecordingSession(store, {"marker": marker})
            entered, release, disconnected = (threading.Event() for _ in range(3))
            def chat(**kwargs):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test timed out")
                return _response("", [_tool_call()])
            client = Mock()
            client.chat.side_effect = chat
            client.make_tool_result_message.side_effect = lambda **kw: {
                "role": "tool", "content": kw["tool_output"],
            }
            first, second = _FakeWebSocket(), _FakeWebSocket()
            original_fail = api_ws.UNITY_TOOL_BRIDGE.fail_conversation
            def fail(*args):
                disconnected.set()
                return original_fail(*args)
            with patch.object(api_ws, "_get_or_create_session", return_value=session), patch.object(
                session_module, "build_model_provider_client", return_value=client
            ), patch.object(api_ws.UNITY_TOOL_BRIDGE, "fail_conversation", side_effect=fail):
                endpoint = asyncio.create_task(api_ws.websocket_chat(first, "disconnect"))
                await first.incoming.put({"type": "user_message", "text": "stop me"})
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    await first.incoming.put(WebSocketDisconnect())
                    self.assertTrue(await asyncio.to_thread(disconnected.wait, 2))
                    await api_ws.websocket_chat(second, "new-client")
                    self.assertEqual(second.closed[0], 1008)
                    self.assertIs(api_ws._ACTIVE_WS, first)
                finally:
                    release.set()
                    await asyncio.wait_for(endpoint, 2)
            marker.assert_not_called()
            self.assertEqual(client.chat.call_count, 1)
            self.assertTrue(session.saved.is_set())
            self.assertIsNone(api_ws._ACTIVE_WS)
            record = store.load_conversation(session.current_conversation_id)
            self.assertIn("Tool not executed", record["messages"][-1]["content"])
            self.assertIn("TURN_CANCELLED", record["log_text"])

    async def test_socket_routes_tool_result_while_turn_is_running(self) -> None:
        websocket = _FakeWebSocket()
        session = _PovRequestSession()

        with patch.object(api_ws, "_get_or_create_session", return_value=session):
            with patch.object(
                api_ws, "clear_user_pov_conversation"
            ) as clear_user_pov_conversation:
                endpoint = asyncio.create_task(api_ws.websocket_chat(websocket, "unity-test"))
                try:
                    await websocket.incoming.put(
                        {"type": "user_message", "text": "What am I looking at?"}
                    )
                    request = await asyncio.wait_for(websocket.outgoing.get(), timeout=1.0)
                    self.assertEqual(request["type"], "unity_tool")
                    request_id = request["data"]["request_id"]

                    # A second turn is rejected, but the in-flight Unity result remains routable.
                    await websocket.incoming.put(
                        {"type": "user_message", "text": "A second message"}
                    )
                    busy = await asyncio.wait_for(websocket.outgoing.get(), timeout=1.0)
                    self.assertEqual(busy["data"]["code"], "CONVERSATION_BUSY")

                    await websocket.incoming.put(
                        {
                            "type": "unity_tool_result",
                            "data": {
                                "tool_name": "get_user_pov",
                                "request_id": request_id,
                                "ok": True,
                                "image": {
                                    "mime_type": "image/jpeg",
                                    "base64": "AA==",
                                },
                            },
                        }
                    )
                    result = await asyncio.wait_for(
                        asyncio.to_thread(session.results.get, True, 1.0),
                        timeout=2.0,
                    )
                    self.assertTrue(result["ok"])
                finally:
                    await websocket.incoming.put(WebSocketDisconnect())
                    await asyncio.wait_for(endpoint, timeout=1.0)

                clear_user_pov_conversation.assert_called_once_with("unity-test")

        self.assertTrue(websocket.accepted)
        self.assertEqual(api_ws.UNITY_TOOL_BRIDGE.pending_count, 0)


class ChangePageContractTests(unittest.TestCase):
    def test_pdf_page_is_sent_unchanged(self):
        with patch.object(api_ws, "request_unity_tool_threadsafe") as send:
            for page in (1, 2, 411, "iii"):
                send.return_value = {"ok": True, "page_label": str(page), "pdf_page": 413, "page_count": 440}
                result = change_page("goto", page)
                self.assertEqual(send.call_args.kwargs["arguments"]["page_number"], str(page))
                self.assertIn(f"Displayed page {page}", result)
                self.assertIn("PDF position 413", result)
        schema = build_tool_schemas({"change_page": change_page})
        self.assertIn("one-based", json.dumps(schema))
        self.assertNotIn("0-based", json.dumps(schema))

    def test_invalid_pages_never_reach_unity(self):
        with patch.object(api_ws, "request_unity_tool_threadsafe") as send:
            for page in (None, 0, -1, True, 1.5, "", " ", "0", "-2"):
                self.assertIn("Invalid request", change_page("goto", page))
            self.assertIn("Invalid request", change_page("next", 2))
            self.assertIn("Invalid request", change_page("invalid"))
            send.assert_not_called()

    def test_next_and_previous_keep_their_direction(self):
        with patch.object(api_ws, "request_unity_tool_threadsafe") as send:
            send.return_value = {"ok": True, "page_label": "411", "pdf_page": 413, "page_count": 440}
            for action in ("next", "previous"):
                change_page(action)
                self.assertEqual(send.call_args.kwargs["arguments"]["direction"], action)
                self.assertNotIn("page_number", send.call_args.kwargs["arguments"])

    def test_missing_pdf_and_timeout_are_not_reported_as_success(self):
        with patch.object(api_ws, "request_unity_tool_threadsafe") as send:
            send.return_value = {"ok": False, "error": "PDF unavailable"}
            self.assertEqual(change_page("goto", 411), "Page was not changed: PDF unavailable")
            send.side_effect = RuntimeError("Unity disconnected")
            self.assertIn("Failed to change page", change_page("goto", 411))


if __name__ == "__main__":
    unittest.main()
