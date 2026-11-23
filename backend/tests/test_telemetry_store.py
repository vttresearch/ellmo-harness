import json
import unittest
from unittest.mock import patch

import config
from telemetry_store import MQTT_TELEMETRY_STREAM, TELEMETRY_STORE, TelemetryStore, validate_fields
from tools.get_mqtt_telemetry import get_mqtt_telemetry


class TelemetryStoreTests(unittest.TestCase):
    def test_arbitrary_fields_aliases_units_and_configured_findings(self):
        store = TelemetryStore()
        fields = {"humidity": {"aliases": ["RH"], "unit": "%", "maximum": 60, "max_change": 10},
                  "battery": {"unit": "V", "minimum": 3}}
        store.add_sample("sensor", {"humidity": 40, "battery": 3.3})
        store.add_sample("sensor", {"humidity": 70, "battery": 2.9})
        result = store.summarize("sensor", "rh BATTERY", fields=fields)
        self.assertEqual(result["latest"], {"humidity": 70, "battery": 2.9})
        self.assertEqual(result["statistics"]["humidity"]["average"], 55)
        self.assertEqual(result["units"], {"humidity": "%", "battery": "V"})
        self.assertEqual(result["diagnostics_status"], "evaluated")
        self.assertEqual({x["code"] for x in result["diagnostics"]},
                         {"humidity_maximum", "humidity_max_change", "battery_minimum"})

    def test_no_thresholds_are_assumed(self):
        store = TelemetryStore()
        store.add_sample("sensor", {"temperature": 1000})
        result = store.summarize("sensor")
        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(result["diagnostics_status"], "not_configured")

    def test_limits_can_be_changed_or_removed(self):
        store = TelemetryStore()
        store.add_sample("sensor", {"temperature": 10})
        for spec, expected in [({"maximum": 5}, 1), ({"maximum": 15}, 0), ({}, 0)]:
            with self.subTest(spec=spec):
                result = store.summarize("sensor", fields={"temperature": spec})
                self.assertEqual(len(result["diagnostics"]), expected)

    def test_buffers_and_sources_are_isolated(self):
        store = TelemetryStore(max_samples=3)
        for value in range(4):
            store.add_sample("one", {"value": value})
        store.add_sample("two", {"value": 99})
        result = store.summarize("one", sample_count=2)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["statistics"]["value"]["minimum"], 2)
        self.assertEqual(store.summarize("two")["latest"]["value"], 99)

    def test_no_data_and_unknown_fields(self):
        store = TelemetryStore()
        self.assertEqual(store.summarize("sensor", "TEMP", fields={"temperature": {"aliases": ["TEMP"]}})["status"], "no_data")
        with self.assertRaisesRegex(ValueError, "Unknown telemetry parameter"):
            store.summarize("sensor", "OIL", fields={"temperature": {}})

    def test_invalid_samples_do_not_pollute_buffer(self):
        for value in [True, "fast", float("nan"), float("inf"), {}, []]:
            with self.subTest(value=value):
                store = TelemetryStore()
                with self.assertRaises(ValueError):
                    store.add_sample("sensor", {"value": value})
                self.assertEqual(store.summarize("sensor")["status"], "no_data")

    def test_allowlist_ignores_unrelated_payload_metadata(self):
        store = TelemetryStore()
        store.add_sample("sensor", {"rpm": 2000, "metadata": {"id": "x"}}, {"rpm": {}})
        self.assertEqual(store.summarize("sensor")["latest"], {"rpm": 2000})

    def test_profile_validation_rejects_ambiguous_aliases_and_bad_thresholds(self):
        for fields in [[], {"x": {"maximum": float("nan")}}, {"x": {"maximum": True}},
                       {"x": {"minimun": 3}}, {"x": {"max_change": -1}},
                       {"x": {"minimum": 5, "maximum": 1}},
                       {"x": {"aliases": ["y"]}, "y": {}}, {"receivedAt": {}}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_fields(fields)

    def test_public_tool_uses_configured_profile_and_mqtt_buffer(self):
        TELEMETRY_STORE.clear(MQTT_TELEMETRY_STREAM)
        self.addCleanup(TELEMETRY_STORE.clear, MQTT_TELEMETRY_STREAM)
        with patch.object(config, "load_config", return_value={"telemetry": {"fields": {"humidity": {"aliases": ["RH"]}}}}):
            result = json.loads(get_mqtt_telemetry("RH"))
            self.assertEqual(result["status"], "no_data")
            TELEMETRY_STORE.add_sample(MQTT_TELEMETRY_STREAM, {"humidity": 42})
            result = json.loads(get_mqtt_telemetry("RH"))
            self.assertEqual(result["source"], "backend_mqtt")
            self.assertEqual(result["latest"], {"humidity": 42})
            self.assertEqual(json.loads(get_mqtt_telemetry("unknown"))["status"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
