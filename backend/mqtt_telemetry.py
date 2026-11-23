from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from telemetry_store import (
    TELEMETRY_STORE,
    MQTT_TELEMETRY_STREAM,
    TelemetryStore,
    validate_fields,
)


_LOGGER = logging.getLogger(__name__)


class MqttTelemetrySubscriber:
    """Own the backend MQTT connection and feed validated samples to telemetry storage."""

    def __init__(
        self,
        settings: Mapping[str, Any],
        store: TelemetryStore = TELEMETRY_STORE,
    ) -> None:
        self.settings = dict(settings)
        self.store = store
        self.fields = validate_fields(self.settings.get("fields", {}))
        self._client: Optional[Any] = None
        self._lock = threading.RLock()
        enabled = bool(self.settings.get("enabled", False))
        self._status: Dict[str, Any] = {
            "enabled": enabled,
            "state": "stopped" if enabled else "disabled",
            "connected": False,
            "host": self.settings.get("host", ""),
            "port": self.settings.get("port", 0),
            "topic": self.settings.get("topic", ""),
            "qos": self.settings.get("qos", 0),
            "messages_received": 0,
            "invalid_messages": 0,
            "last_message_at": None,
            "last_error": None,
        }

    def start(self) -> None:
        if not self.settings.get("enabled", False):
            return

        with self._lock:
            if self._client is not None:
                return
            self._status.update(
                state="starting", connected=False, last_error=None
            )

        try:
            import paho.mqtt.client as mqtt
        except Exception as exc:
            self._set_error(f"paho-mqtt is unavailable: {exc}", state="dependency_error")
            _LOGGER.error("MQTT telemetry subscriber could not start: %s", exc)
            return

        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=str(self.settings.get("client_id", "")),
                protocol=mqtt.MQTTv311,
            )
            client.on_connect = self._on_connect
            client.on_connect_fail = self._on_connect_fail
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message

            username = str(self.settings.get("username", "") or "")
            password = str(self.settings.get("password", "") or "")
            if username:
                client.username_pw_set(username, password or None)

            client.reconnect_delay_set(
                min_delay=int(self.settings.get("reconnect_min_delay", 1)),
                max_delay=int(self.settings.get("reconnect_max_delay", 30)),
            )
            self.store.clear(MQTT_TELEMETRY_STREAM)
            with self._lock:
                self._client = client
                self._status["state"] = "connecting"
            client.connect_async(
                str(self.settings["host"]),
                int(self.settings["port"]),
                int(self.settings["keepalive"]),
            )
            client.loop_start()
        except Exception as exc:
            with self._lock:
                self._client = None
            self._set_error(str(exc), state="start_error")
            _LOGGER.exception("MQTT telemetry subscriber failed to start.")
            return

        _LOGGER.info(
            "MQTT telemetry subscriber connecting to %s:%s topic=%s",
            self.settings["host"],
            self.settings["port"],
            self.settings["topic"],
        )

    def stop(self) -> None:
        with self._lock:
            client = self._client
            self._client = None

        if client is not None:
            try:
                client.disconnect()
            except Exception:
                _LOGGER.exception("MQTT telemetry disconnect failed.")
            try:
                client.loop_stop()
            except Exception:
                _LOGGER.exception("MQTT telemetry network loop failed to stop.")

        with self._lock:
            self._status.update(state="stopped", connected=False)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        connect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code != 0:
            self._set_error(
                f"Broker rejected connection: {reason_code}", state="connection_rejected"
            )
            return

        result, _message_id = client.subscribe(
            str(self.settings["topic"]), int(self.settings["qos"])
        )
        if result != 0:
            self._set_error(f"Subscription failed with result code {result}", state="subscribe_error")
            return

        with self._lock:
            self._status.update(state="connected", connected=True, last_error=None)
        _LOGGER.info("MQTT telemetry subscriber connected and subscribed.")

    def _on_connect_fail(self, client: Any, userdata: Any) -> None:
        self._set_error("TCP connection to MQTT broker failed.", state="reconnecting")

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        with self._lock:
            stopping = self._client is None
            self._status.update(
                state="stopped" if stopping else "reconnecting",
                connected=False,
            )
            if not stopping and reason_code != 0:
                self._status["last_error"] = f"Disconnected: {reason_code}"

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = message.payload.decode("utf-8")
            sample = json.loads(payload)
            if not isinstance(sample, dict):
                raise ValueError("MQTT telemetry payload must be a JSON object.")
            self.store.add_sample(MQTT_TELEMETRY_STREAM, sample, self.fields)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            with self._lock:
                self._status["invalid_messages"] += 1
                self._status["last_error"] = str(exc)
            _LOGGER.warning("Rejected MQTT telemetry message: %s", exc)
            return

        with self._lock:
            self._status["messages_received"] += 1
            self._status["last_message_at"] = datetime.now(timezone.utc).isoformat()
            self._status["last_error"] = None

    def _set_error(self, message: str, state: str) -> None:
        with self._lock:
            self._status.update(
                state=state,
                connected=False,
                last_error=message,
            )
