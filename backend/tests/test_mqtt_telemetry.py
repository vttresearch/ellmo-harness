import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mqtt_telemetry import MqttTelemetrySubscriber
from telemetry_store import MQTT_TELEMETRY_STREAM, TelemetryStore
import config


class MqttTelemetrySubscriberTests(unittest.TestCase):
    def setUp(self):
        self.store = TelemetryStore(max_samples=10)
        self.subscriber = MqttTelemetrySubscriber(
            {
                "fields": {"rpm": {}, "afr": {}},
                "enabled": True,
                "host": "127.0.0.1",
                "port": 1883,
                "topic": "ellmo/outputs/stateJSON",
                "qos": 0,
                "keepalive": 60,
            },
            store=self.store,
        )

    @staticmethod
    def _message(payload):
        return SimpleNamespace(payload=payload, topic="ellmo/outputs/stateJSON")

    def test_valid_json_payload_populates_backend_stream(self):
        payload = json.dumps(
            {"rpm": 2250, "rpmNormalized": 0.44444, "afr": 14.4}
        ).encode("utf-8")

        self.subscriber._on_message(None, None, self._message(payload))

        result = self.store.summarize(MQTT_TELEMETRY_STREAM, "RPM AFR")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["latest"]["rpm"], 2250)
        self.assertEqual(self.subscriber.status()["messages_received"], 1)

    def test_unused_normalized_rpm_does_not_enter_backend_contract(self):
        payload = json.dumps(
            {"rpm": 2250, "rpmNormalized": {"unexpected": "shape"}}
        ).encode("utf-8")

        self.subscriber._on_message(None, None, self._message(payload))

        result = self.store.summarize(MQTT_TELEMETRY_STREAM, "ALL")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("rpmNormalized", result["latest"])

    def test_invalid_payload_is_counted_without_polluting_store(self):
        self.subscriber._on_message(None, None, self._message(b"not-json"))

        result = self.store.summarize(MQTT_TELEMETRY_STREAM, "RPM")
        self.assertEqual(result["status"], "no_data")
        self.assertEqual(self.subscriber.status()["invalid_messages"], 1)

    def test_connect_callback_subscribes_to_configured_topic(self):
        client = SimpleNamespace(subscribe=lambda topic, qos: (0, 1))

        self.subscriber._on_connect(client, None, None, 0, None)

        status = self.subscriber.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["state"], "connected")

    def test_start_configures_non_blocking_paho_client(self):
        class FakeClient:
            def __init__(self, callback_version, client_id, protocol):
                self.callback_version = callback_version
                self.client_id = client_id
                self.protocol = protocol
                self.connected_to = None
                self.loop_started = False

            def reconnect_delay_set(self, min_delay, max_delay):
                self.reconnect_delays = (min_delay, max_delay)

            def connect_async(self, host, port, keepalive):
                self.connected_to = (host, port, keepalive)

            def loop_start(self):
                self.loop_started = True

        fake_mqtt = SimpleNamespace(
            Client=FakeClient,
            CallbackAPIVersion=SimpleNamespace(VERSION2=2),
            MQTTv311=4,
        )

        with patch.dict(
            sys.modules,
            {
                "paho": SimpleNamespace(mqtt=SimpleNamespace(client=fake_mqtt)),
                "paho.mqtt": SimpleNamespace(client=fake_mqtt),
                "paho.mqtt.client": fake_mqtt,
            },
        ):
            self.subscriber.start()

        client = self.subscriber._client
        self.assertEqual(client.connected_to, ("127.0.0.1", 1883, 60))
        self.assertTrue(client.loop_started)
        self.assertEqual(self.subscriber.status()["state"], "connecting")

    def test_backend_config_supports_broker_environment_overrides(self):
        raw_config = {
            "mqtt": {
                "enabled": True,
                "host": "config-host",
                "port": 1883,
                "topic": "config/topic",
            }
        }
        overrides = {
            "MQTT_HOST": "broker.internal",
            "MQTT_PORT": "2883",
            "MQTT_TOPIC": "ellmo/test/stateJSON",
        }

        with patch.object(config, "load_config", return_value=raw_config), patch.dict(
            os.environ, overrides, clear=False
        ):
            settings = config.get_mqtt_config()

        self.assertEqual(settings["host"], "broker.internal")
        self.assertEqual(settings["port"], 2883)
        self.assertEqual(settings["topic"], "ellmo/test/stateJSON")


if __name__ == "__main__":
    unittest.main()
