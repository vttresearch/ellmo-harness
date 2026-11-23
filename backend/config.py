import json
import logging
import os
from pathlib import Path
from typing import Dict, Any

_LOGGER = logging.getLogger(__name__)


def _config_path() -> Path:
    override = os.environ.get("ELLMO_CONFIG")
    return Path(override).expanduser().resolve() if override else Path(__file__).resolve().parent / "config.json"


def load_config() -> Dict[str, Any]:
    path = _config_path()
    if not path.exists():
        message = f"Config file not found at {path}. Please add a config.json file."
        _LOGGER.error(message)
        raise FileNotFoundError(message)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        _LOGGER.exception("Failed to read config file at %s; no defaults are defined in code.", path)
        return {}
    if not isinstance(data, dict):
        _LOGGER.warning("Config file at %s is not a JSON object; no defaults are defined in code.", path)
        return {}
    return data


def save_config(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("Config payload must be a dictionary.")

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    temp_path.replace(path)


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_host_entry(host_name: str, raw_config: Dict[str, Any]) -> Dict[str, Any]:
    config_entry = dict(raw_config)
    provider = str(config_entry.get("provider", "") or "").strip().lower()
    if not provider:
        raise ValueError(f"Model host '{host_name}' is missing 'provider'.")

    config_entry["provider"] = provider

    for key in ("models", "tool_models", "no_tool_models"):
        if key in config_entry:
            config_entry[key] = _normalize_string_list(config_entry.get(key))

    default_model = str(config_entry.get("default_model", "") or "").strip()
    if default_model:
        config_entry["default_model"] = default_model
    else:
        config_entry.pop("default_model", None)

    return config_entry


def get_model_hosts() -> Dict[str, Dict[str, Any]]:
    """Return provider-backed model hosts for the UI/API runtime.

    Preferred config shape:

        "model_hosts": {
          "Ollama - localhost": {
            "provider": "ollama",
            "host": "http://127.0.0.1:11434"
          },
          "OpenRouter": {
            "provider": "openrouter",
            "models": ["openai/gpt-4o-mini", "anthropic/claude-3.7-sonnet"]
          }
        }

    `model_hosts` is the only supported config key for runtime host definitions.
    """
    data = load_config()

    raw_hosts = data.get("model_hosts")
    if not isinstance(raw_hosts, dict):
        _LOGGER.warning("Config 'model_hosts' is missing or invalid; no defaults are defined in code.")
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for raw_name, raw_config in raw_hosts.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_config, dict):
            continue
        try:
            normalized[name] = _normalize_host_entry(name, raw_config)
        except ValueError as e:
            _LOGGER.warning("%s", e)
    return normalized


def get_context_window_size() -> int:
    data = load_config()
    value = data.get("context_window_size")
    try:
        return int(value)
    except Exception:
        _LOGGER.warning("Config 'context_window_size' is missing or invalid; no defaults are defined in code.")
        return 0


def get_available_functions() -> Dict[str, Any]:
    from tool_catalog import ToolCatalog

    catalog = ToolCatalog()
    return catalog.get_enabled_tool_functions()


def get_system_message() -> str:
    data = load_config()
    value = data.get("system_message")
    if isinstance(value, str):
        return value.strip()
    _LOGGER.info("Config 'system_message' is missing or invalid; defaulting to empty.")
    return ""


def get_mqtt_config() -> Dict[str, Any]:
    """Return validated MQTT subscriber settings with optional environment overrides."""
    data = load_config()
    raw = data.get("mqtt")
    if not isinstance(raw, dict):
        raise ValueError("Config 'mqtt' is missing or invalid.")

    enabled = bool(raw.get("enabled", False))
    host = str(os.getenv("MQTT_HOST") or raw.get("host") or "").strip()
    topic = str(os.getenv("MQTT_TOPIC") or raw.get("topic") or "").strip()

    try:
        port = int(os.getenv("MQTT_PORT") or raw.get("port"))
        qos = int(raw.get("qos", 0))
        keepalive = int(raw.get("keepalive", 60))
        reconnect_min_delay = int(raw.get("reconnect_min_delay", 1))
        reconnect_max_delay = int(raw.get("reconnect_max_delay", 30))
    except (TypeError, ValueError) as exc:
        raise ValueError("MQTT port, QoS, keepalive, and reconnect delays must be integers.") from exc

    if enabled and not host:
        raise ValueError("MQTT is enabled but no broker host is configured.")
    if enabled and not topic:
        raise ValueError("MQTT is enabled but no topic is configured.")
    if not 1 <= port <= 65535:
        raise ValueError("MQTT port must be between 1 and 65535.")
    if qos not in (0, 1, 2):
        raise ValueError("MQTT QoS must be 0, 1, or 2.")
    if keepalive < 1:
        raise ValueError("MQTT keepalive must be at least 1 second.")
    if reconnect_min_delay < 1 or reconnect_max_delay < reconnect_min_delay:
        raise ValueError("MQTT reconnect delays are invalid.")

    username_env = str(raw.get("username_env") or "MQTT_USERNAME").strip()
    password_env = str(raw.get("password_env") or "MQTT_PASSWORD").strip()
    return {
        "fields": get_telemetry_config(data)["fields"],
        "enabled": enabled,
        "host": host,
        "port": port,
        "topic": topic,
        "qos": qos,
        "keepalive": keepalive,
        "client_id": str(raw.get("client_id") or "ellmo-harness-backend").strip(),
        "reconnect_min_delay": reconnect_min_delay,
        "reconnect_max_delay": reconnect_max_delay,
        "username": os.getenv(username_env, "") if username_env else "",
        "password": os.getenv(password_env, "") if password_env else "",
    }


def get_telemetry_config(data=None) -> Dict[str, Any]:
    """Return optional numeric sensor fields and thresholds; none are assumed."""
    from telemetry_store import validate_fields

    data = load_config() if data is None else data
    profile = data.get("telemetry", {})
    if not isinstance(profile, dict):
        raise ValueError("Config 'telemetry' must be an object.")
    return {"fields": validate_fields(profile.get("fields", {}))}
