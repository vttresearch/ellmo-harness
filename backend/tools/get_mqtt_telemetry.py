import json

import config
from telemetry_store import MQTT_TELEMETRY_STREAM, TELEMETRY_STORE


def summarize_mqtt_telemetry(parameters="ALL", sample_count=50, *, profile=None, diagnostic_fn=None):
    """Shared report implementation; private adapters may supply a profile/diagnostics."""
    try:
        if profile is None:
            profile = config.get_telemetry_config()
        result = TELEMETRY_STORE.summarize(
            MQTT_TELEMETRY_STREAM, parameters, sample_count,
            fields=profile.get("fields", {}), diagnostic_fn=diagnostic_fn,
        )
        result["source"] = "backend_mqtt"
        return json.dumps(result, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        return json.dumps({"status": "invalid_request", "message": str(exc)})


def get_mqtt_telemetry(parameters: str = "ALL", sample_count: int = 50) -> str:
    """Read recent MQTT sensor values, statistics and explicitly configured thresholds.

    Args:
        parameters: Space- or comma-separated configured field names/aliases, or ALL.
        sample_count: Number of recent samples to inspect, from 1 to 500.
    """
    return summarize_mqtt_telemetry(parameters, sample_count)
