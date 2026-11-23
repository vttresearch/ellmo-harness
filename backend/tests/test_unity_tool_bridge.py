import queue
import threading
import unittest

from unity_tool_bridge import (
    UnityToolBridge,
    UnityToolProtocolError,
    UnityToolTimeoutError,
)


class UnityToolBridgeTests(unittest.TestCase):
    def test_correlated_result_unblocks_waiting_tool(self) -> None:
        bridge = UnityToolBridge()
        sent = queue.Queue()
        completed = queue.Queue()

        def run_request() -> None:
            completed.put(
                bridge.request(
                    conversation_id="unity-test",
                    tool_name="get_user_pov",
                    send=sent.put,
                    timeout_seconds=1.0,
                )
            )

        worker = threading.Thread(target=run_request)
        worker.start()
        request = sent.get(timeout=1.0)
        request_id = request["data"]["request_id"]

        resolved = bridge.resolve(
            "unity-test",
            {
                "type": "unity_tool_result",
                "data": {
                    "tool_name": "get_user_pov",
                    "request_id": request_id,
                    "ok": True,
                    "image": {"mime_type": "image/jpeg", "base64": "AA=="},
                },
            },
        )

        worker.join(timeout=1.0)
        self.assertTrue(resolved)
        self.assertFalse(worker.is_alive())
        self.assertTrue(completed.get_nowait()["ok"])
        self.assertEqual(bridge.pending_count, 0)

    def test_timeout_cleans_up_pending_request(self) -> None:
        bridge = UnityToolBridge()

        with self.assertRaises(UnityToolTimeoutError):
            bridge.request(
                conversation_id="unity-test",
                tool_name="get_user_pov",
                send=lambda _: None,
                timeout_seconds=0.01,
            )

        self.assertEqual(bridge.pending_count, 0)

    def test_mismatched_tool_name_fails_waiting_request(self) -> None:
        bridge = UnityToolBridge()
        sent = queue.Queue()
        failed = queue.Queue()

        def run_request() -> None:
            try:
                bridge.request(
                    conversation_id="unity-test",
                    tool_name="get_user_pov",
                    send=sent.put,
                    timeout_seconds=1.0,
                )
            except Exception as exc:
                failed.put(exc)

        worker = threading.Thread(target=run_request)
        worker.start()
        request_id = sent.get(timeout=1.0)["data"]["request_id"]

        self.assertTrue(
            bridge.resolve(
                "unity-test",
                {
                    "data": {
                        "tool_name": "change_gameobject_color",
                        "request_id": request_id,
                        "ok": True,
                    }
                },
            )
        )

        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(failed.get_nowait(), UnityToolProtocolError)


if __name__ == "__main__":
    unittest.main()
