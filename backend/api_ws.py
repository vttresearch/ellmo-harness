import asyncio
import json
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any

from fastapi import WebSocket, WebSocketDisconnect

from api_rest import app, TOOL_CATALOG  # reuse same app + shared tool catalog
import config
from session import ConversationSession
from mqtt_telemetry import MqttTelemetrySubscriber
from tools.get_user_pov import clear_user_pov_conversation
from unity_tool_bridge import UnityToolBridge, UnityToolProtocolError

_LOGGER = logging.getLogger(__name__)

MODEL_HOSTS: Dict[str, Dict[str, Any]] = config.get_model_hosts()

CONTEXT_WINDOW_SIZE = config.get_context_window_size()
SYSTEM_MESSAGE = config.get_system_message()
MQTT_SUBSCRIBER = MqttTelemetrySubscriber(config.get_mqtt_config())

if SYSTEM_MESSAGE:
    _LOGGER.info(
        "Loaded system_message at startup for api_ws (length=%d).",
        len(SYSTEM_MESSAGE),
    )
else:
    _LOGGER.info("No system_message loaded at startup for api_ws.")

# In-memory map of logical websocket conversation ids -> ConversationSession.
# Snapshots are saved after each turn; live sessions belong to this one WS worker.
SESSIONS: Dict[str, ConversationSession] = {}


# ---- Connector globals ----
_ACTIVE_WS: Optional[WebSocket] = None
_ACTIVE_WS_LOOP: Optional[asyncio.AbstractEventLoop] = None
_ACTIVE_WS_CONVERSATION_ID: Optional[str] = None
_ACTIVE_WS_SEND_LOCK: Optional[asyncio.Lock] = None
UNITY_TOOL_BRIDGE = UnityToolBridge()

# Protects swapping/clearing the active websocket refs across threads.
_ACTIVE_WS_LOCK = threading.Lock()


def _get_or_create_session(conversation_id: str) -> ConversationSession:
    session = SESSIONS.get(conversation_id)
    if session is None:
        session = ConversationSession(
            model_hosts=MODEL_HOSTS,
            available_functions=TOOL_CATALOG.get_enabled_tool_functions(),
            context_window_size=CONTEXT_WINDOW_SIZE,
            tool_provider=TOOL_CATALOG.get_enabled_tool_functions,
            system_message=SYSTEM_MESSAGE,
        )
        SESSIONS[conversation_id] = session
    return session


async def _send_json_locked(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    content: Any,
) -> None:
    async with send_lock:
        await websocket.send_json(content)


async def send_to_unity(content: Any) -> None:
    """Async send. Raises if there is no active websocket connection."""
    with _ACTIVE_WS_LOCK:
        ws = _ACTIVE_WS
        send_lock = _ACTIVE_WS_SEND_LOCK
    if ws is None or send_lock is None:
        raise RuntimeError("No active Unity websocket connection is open.")
    await _send_json_locked(ws, send_lock, content)


def send_to_unity_threadsafe(content: Any) -> None:
    """Sync-friendly send. Raises if there is no active websocket connection.

    Useful when called from non-async code (other scripts / worker threads).
    """
    with _ACTIVE_WS_LOCK:
        ws = _ACTIVE_WS
        loop = _ACTIVE_WS_LOOP
        send_lock = _ACTIVE_WS_SEND_LOCK

    if ws is None or loop is None or send_lock is None:
        raise RuntimeError("No active Unity websocket connection is open.")

    fut = asyncio.run_coroutine_threadsafe(
        _send_json_locked(ws, send_lock, content),
        loop,
    )
    fut.result()


def request_unity_tool_threadsafe(
    tool_name: str,
    *,
    arguments: Optional[Dict[str, Any]] = None,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Send a correlated Unity tool request and block the worker until its reply."""
    conversation_id = get_active_conversation_id()
    if not conversation_id:
        raise RuntimeError("No active Unity websocket connection is open.")

    return UNITY_TOOL_BRIDGE.request(
        conversation_id=conversation_id,
        tool_name=tool_name,
        arguments=arguments,
        timeout_seconds=timeout_seconds,
        send=send_to_unity_threadsafe,
    )


def get_active_conversation_id() -> Optional[str]:
    """Return the logical id for the active Unity websocket connection."""
    with _ACTIVE_WS_LOCK:
        return _ACTIVE_WS_CONVERSATION_ID


@app.on_event("startup")
def start_mqtt_telemetry() -> None:
    MQTT_SUBSCRIBER.start()


@app.on_event("shutdown")
def stop_mqtt_telemetry() -> None:
    MQTT_SUBSCRIBER.stop()


@app.get("/mqtt/status")
async def mqtt_status() -> Dict[str, Any]:
    """Return backend MQTT connection and ingestion status without credentials."""
    return {
        "v": 1,
        "type": "mqtt_status",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": MQTT_SUBSCRIBER.status(),
    }


@app.websocket("/ws/chat/{conversation_id}")
async def websocket_chat(websocket: WebSocket, conversation_id: str):
    """WebSocket endpoint for live chat with tool notifications.

    Incoming messages:
      - {"type": "configure", "host": "...", "model": "..."}
      - {"type": "user_message", "text": "Hello model!"}
      - {"type": "unity_tool_result", "data": {"request_id": "...", ...}}

    Outgoing messages (server -> client):
      - {"type": "log", "text": "..."}
      - {"type": "stats", "stats": {...}}
      - {"type": "tool_call", ...}
      - {"type": "tool_result", ...}
      - {"type": "unity_tool", "data": {"request_id": "...", ...}}
      - {"type": "assistant_message", "content": "..."}
    """
    global _ACTIVE_WS, _ACTIVE_WS_LOOP, _ACTIVE_WS_CONVERSATION_ID, _ACTIVE_WS_SEND_LOCK

    loop = asyncio.get_running_loop()
    send_lock = asyncio.Lock()
    connection_open = threading.Event()
    connection_open.set()
    stop_requested = threading.Event()
    transcript: list[str] = []
    event_log: list[Dict[str, Any]] = []
    session_task: Optional[asyncio.Task[Any]] = None

    async def send_json(content: Any) -> None:
        await _send_json_locked(websocket, send_lock, content)

    def error_message(message: str, code: str) -> Dict[str, Any]:
        return {
            "v": 1,
            "type": "error",
            "ts": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id,
            "data": {
                "message": message,
                "code": code,
            },
        }

    def on_event(event: Dict[str, Any]) -> None:
        event_log.append(event)
        if not connection_open.is_set():
            return
        future = asyncio.run_coroutine_threadsafe(send_json(event), loop)

        def report_send_failure(completed: Any) -> None:
            try:
                completed.result()
            except Exception:
                if connection_open.is_set():
                    _LOGGER.debug("Failed to stream a WebSocket event.", exc_info=True)

        future.add_done_callback(report_send_failure)

    def run_turn(text: str) -> None:
        if session.current_conversation_id is None:
            transcript.clear()
            event_log.clear()
        transcript.append(f"User: {text}")
        try:
            result = session.step(text, on_event, stop_requested=stop_requested.is_set)
            if result.get("assistant_message"):
                transcript.append(f"Assistant: {result['assistant_message']}")
        finally:
            # Run in the same worker as step: the snapshot cannot race a tool result.
            session.save_snapshot(
                "\n\n".join(transcript),
                "\n".join(json.dumps(event, ensure_ascii=False) for event in event_log),
            )

    async def run_session_step(text: str) -> None:
        try:
            await asyncio.to_thread(run_turn, text)
        except Exception as exc:
            if connection_open.is_set():
                try:
                    await send_json(error_message(str(exc), "SESSION_STEP_FAILED"))
                except Exception:
                    _LOGGER.debug("Failed to report session step error.", exc_info=True)

    # Reserve before the first await so simultaneous handshakes cannot both win.
    # TODO: replace this single-client slot with per-connection tool routing.
    with _ACTIVE_WS_LOCK:
        busy = _ACTIVE_WS is not None
        if not busy:
            _ACTIVE_WS = websocket
            _ACTIVE_WS_LOOP = loop
            _ACTIVE_WS_CONVERSATION_ID = conversation_id
            _ACTIVE_WS_SEND_LOCK = send_lock
    if busy:
        await websocket.accept()
        await send_json(error_message(
            "Only one client can connect at a time. Disconnect the other client "
            "and wait for its current request to finish before reconnecting.",
            "CLIENT_ALREADY_CONNECTED",
        ))
        await websocket.close(code=1008, reason="Only one client is supported.")
        return

    try:
        await websocket.accept()
        session = _get_or_create_session(conversation_id)
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "configure":
                if session_task is not None and not session_task.done():
                    await send_json(
                        error_message(
                            "The conversation is busy processing the current user message.",
                            "CONVERSATION_BUSY",
                        )
                    )
                    continue

                host = message.get("host")
                model = message.get("model")
                system_message = message.get("system_message")
                enabled_tools = message.get("enabled_tools")
                session.configure(host, model)
                if system_message is not None:
                    session.set_system_message(system_message)
                if enabled_tools is not None:
                    if not isinstance(enabled_tools, list):
                        raise ValueError("configure.enabled_tools must be a list of tool names.")
                    session.set_enabled_tools(enabled_tools)
                # Acknowledge configuration
                await send_json(
                    {
                        "v": 1,
                        "type": "configured",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "conversation_id": conversation_id,
                        "data": {
                            "host": host,
                            "model": model,
                            "system_message_configured": system_message is not None,
                            "enabled_tools": enabled_tools,
                        },
                    }
                )

            elif msg_type == "user_message":
                text = message.get("text") or ""
                if not text:
                    continue

                if session_task is not None and not session_task.done():
                    await send_json(
                        error_message(
                            "The conversation is busy processing the current user message.",
                            "CONVERSATION_BUSY",
                        )
                    )
                    continue

                # Keep the socket reader alive while the synchronous agent step runs
                # in a worker. This lets Unity return correlated tool results without
                # allowing two conversation turns to mutate the session concurrently.
                session_task = asyncio.create_task(run_session_step(text))

            elif msg_type == "unity_tool_result":
                try:
                    resolved = UNITY_TOOL_BRIDGE.resolve(conversation_id, message)
                except UnityToolProtocolError as exc:
                    await send_json(error_message(str(exc), "INVALID_UNITY_TOOL_RESULT"))
                    continue

                if not resolved:
                    await send_json(
                        error_message(
                            "No pending Unity tool request matches this result.",
                            "UNKNOWN_UNITY_TOOL_REQUEST",
                        )
                    )

            else:
                # Unknown message types can be ignored or logged on the client.
                await send_json(
                    error_message(
                        f"Unknown message type: {msg_type}",
                        "UNKNOWN_MESSAGE_TYPE",
                    )
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        # Best-effort error reporting back to the client
        try:
            await send_json(error_message(str(exc), "WS_EXCEPTION"))
        except Exception:
            pass
    finally:
        connection_open.clear()
        stop_requested.set()
        UNITY_TOOL_BRIDGE.fail_conversation(
            conversation_id,
            "The Unity websocket connection closed before the tool result arrived.",
        )
        try:
            # Cancelling to_thread would leave its worker running. Keep the slot
            # reserved until it stops and saves, so it cannot reach a new client.
            if session_task is not None:
                await asyncio.shield(session_task)
        finally:
            clear_user_pov_conversation(conversation_id)
            SESSIONS.pop(conversation_id, None)
            with _ACTIVE_WS_LOCK:
                if _ACTIVE_WS is websocket:
                    _ACTIVE_WS = None
                    _ACTIVE_WS_LOOP = None
                    _ACTIVE_WS_CONVERSATION_ID = None
                    _ACTIVE_WS_SEND_LOCK = None
