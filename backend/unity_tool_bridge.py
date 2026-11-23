from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Dict, Mapping, Optional
from uuid import uuid4


class UnityToolBridgeError(RuntimeError):
    """Base error raised by correlated Unity tool requests."""


class UnityToolTimeoutError(UnityToolBridgeError):
    """Raised when Unity does not answer a tool request before its deadline."""


class UnityToolProtocolError(UnityToolBridgeError):
    """Raised when a correlated Unity response violates the wire contract."""


@dataclass
class _PendingRequest:
    conversation_id: str
    tool_name: str
    future: Future[Dict[str, Any]]


class UnityToolBridge:
    """Correlate synchronous backend tools with asynchronous Unity responses."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._pending: Dict[str, _PendingRequest] = {}

    def request(
        self,
        *,
        conversation_id: str,
        tool_name: str,
        send: Callable[[Dict[str, Any]], None],
        arguments: Optional[Mapping[str, Any]] = None,
        timeout_seconds: float = 30.0,
    ) -> Dict[str, Any]:
        conversation_id = str(conversation_id or "").strip()
        tool_name = str(tool_name or "").strip()
        if not conversation_id:
            raise UnityToolBridgeError("No active Unity conversation is available.")
        if not tool_name:
            raise ValueError("tool_name must be a non-empty string.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")

        request_id = uuid4().hex
        future: Future[Dict[str, Any]] = Future()
        pending = _PendingRequest(
            conversation_id=conversation_id,
            tool_name=tool_name,
            future=future,
        )

        request_data: Dict[str, Any] = {
            "tool_name": tool_name,
            "request_id": request_id,
        }
        for raw_key, value in dict(arguments or {}).items():
            key = str(raw_key).strip()
            if not key:
                continue
            if key in request_data:
                raise ValueError(f"Unity tool argument '{key}' is reserved.")
            request_data[key] = value

        with self._lock:
            self._pending[request_id] = pending

        try:
            send({"type": "unity_tool", "data": request_data})
            try:
                return future.result(timeout=timeout_seconds)
            except FutureTimeoutError as exc:
                raise UnityToolTimeoutError(
                    f"Unity tool '{tool_name}' timed out after {timeout_seconds:g} seconds."
                ) from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def resolve(self, conversation_id: str, message: Mapping[str, Any]) -> bool:
        """Resolve a pending request. Return False for an unknown request id."""
        data = message.get("data")
        if not isinstance(data, dict):
            raise UnityToolProtocolError("unity_tool_result.data must be an object.")

        request_id = str(data.get("request_id") or "").strip()
        if not request_id:
            raise UnityToolProtocolError("unity_tool_result is missing data.request_id.")

        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.future.done():
                return False

            if pending.conversation_id != str(conversation_id or "").strip():
                return False

            response_tool_name = str(data.get("tool_name") or "").strip()
            if response_tool_name and response_tool_name != pending.tool_name:
                pending.future.set_exception(
                    UnityToolProtocolError(
                        f"Unity response for '{pending.tool_name}' claimed tool "
                        f"'{response_tool_name}'."
                    )
                )
                return True

            pending.future.set_result(dict(data))
            return True

    def fail_conversation(self, conversation_id: str, reason: str) -> int:
        """Fail and remove all pending requests owned by a disconnected client."""
        conversation_id = str(conversation_id or "").strip()
        error = UnityToolBridgeError(str(reason or "Unity connection closed."))

        with self._lock:
            matches = [
                (request_id, pending)
                for request_id, pending in self._pending.items()
                if pending.conversation_id == conversation_id
            ]
            for request_id, _ in matches:
                self._pending.pop(request_id, None)

        for _, pending in matches:
            if not pending.future.done():
                pending.future.set_exception(error)
        return len(matches)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)
