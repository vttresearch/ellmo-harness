import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from conversation_store import ConversationManager
from model_provider import build_model_provider_client, build_tool_schemas

_LOGGER = logging.getLogger(__name__)
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0


def _normalize_provider_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _provider_message_family(provider: Any) -> str:
    normalized = _normalize_provider_name(provider)
    if normalized in {"openai", "openrouter", "llama-server"}:
        return "openai-compatible"
    return normalized


def _normalize_models_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _resolve_host_model_lists(host_config: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    tool_models = _normalize_models_list(host_config.get("tool_models"))
    no_tool_models = _normalize_models_list(host_config.get("no_tool_models"))
    models = _normalize_models_list(host_config.get("models"))

    if tool_models or no_tool_models:
        return tool_models, no_tool_models
    return models, []


def _resolve_api_key_error(provider: str, host_config: Dict[str, Any]) -> Optional[str]:
    if provider == "openai":
        api_key_env = str(host_config.get("api_key_env", "OPENAI_API_KEY")).strip()
    elif provider == "openrouter":
        api_key_env = str(host_config.get("api_key_env", "OPENROUTER_API_KEY")).strip()
    else:
        return None

    api_key = str(host_config.get("api_key", "")).strip()
    if not api_key:
        api_key = os.getenv(api_key_env, "").strip()
    if api_key:
        return None
    return f"Missing API key. Set {api_key_env} or provide api_key in config."


def _dedupe_model_names(values: List[str]) -> List[str]:
    models: List[str] = []
    seen = set()
    for raw_value in values:
        model = str(raw_value or "").strip()
        if not model or model in seen:
            continue
        seen.add(model)
        models.append(model)
    return models


def _resolve_all_configured_models(host_config: Dict[str, Any]) -> List[str]:
    configured: List[str] = []
    for key in ("tool_models", "no_tool_models", "models"):
        configured.extend(_normalize_models_list(host_config.get(key)))

    default_model = str(host_config.get("default_model", "") or "").strip()
    if default_model:
        configured.append(default_model)

    return _dedupe_model_names(configured)


def _llama_server_base_url(host_config: Dict[str, Any]) -> str:
    raw_base_url = str(host_config.get("base_url", "") or "").strip()
    raw_host = str(host_config.get("host", "") or "").strip()
    base_url = (raw_base_url or raw_host).rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _llama_server_root_url(host_config: Dict[str, Any]) -> str:
    base_url = _llama_server_base_url(host_config)
    if base_url.endswith("/v1"):
        return base_url[:-3].rstrip("/")
    return base_url.rstrip("/")


def _llama_server_headers(host_config: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    default_headers = host_config.get("default_headers")
    if isinstance(default_headers, dict):
        for raw_key, raw_value in default_headers.items():
            key = str(raw_key).strip()
            value = str(raw_value).strip()
            if key and value:
                headers[key] = value

    api_key = str(host_config.get("api_key", "") or "").strip()
    api_key_env = str(host_config.get("api_key_env", "LLAMA_SERVER_API_KEY") or "").strip()
    if not api_key and api_key_env:
        api_key = os.getenv(api_key_env, "").strip()
    if api_key and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {api_key}"

    return headers


def _parse_openai_models_response(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return []

    models: List[str] = []
    raw_models = data.get("data", [])
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict):
                model_id = str(item.get("id", "") or "").strip()
                if model_id:
                    models.append(model_id)

    return _dedupe_model_names(models)


def _parse_llama_server_models_response(data: Any) -> Tuple[List[str], Dict[str, str]]:
    if not isinstance(data, dict):
        return [], {}

    models: List[str] = []
    statuses: Dict[str, str] = {}
    raw_models = data.get("data", [])
    if isinstance(raw_models, list):
        for item in raw_models:
            if not isinstance(item, dict):
                continue

            model_id = str(item.get("id", "") or item.get("name", "") or "").strip()
            if not model_id:
                continue

            models.append(model_id)
            status_value = ""
            raw_status = item.get("status")
            if isinstance(raw_status, dict):
                status_value = str(raw_status.get("value", "") or "").strip()
            elif raw_status is not None:
                status_value = str(raw_status).strip()
            if status_value:
                statuses[model_id] = status_value

    return _dedupe_model_names(models), statuses


def _probe_ollama_host(host_config: Dict[str, Any]) -> Dict[str, Any]:
    host = str(host_config.get("host", "")).strip()
    if not host:
        return {
            "provider": "ollama",
            "online": False,
            "error": "No host configured",
            "tool_models": [],
            "no_tool_models": [],
        }

    base_url = host.rstrip("/")
    tags_url = f"{base_url}/api/tags"

    try:
        response = requests.get(tags_url, timeout=2.0)
    except Exception as e:
        return {
            "provider": "ollama",
            "online": False,
            "error": str(e) or "Unreachable",
            "tool_models": [],
            "no_tool_models": [],
        }

    if response.status_code != 200:
        return {
            "provider": "ollama",
            "online": False,
            "error": f"HTTP {response.status_code} from /api/tags",
            "tool_models": [],
            "no_tool_models": [],
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "provider": "ollama",
            "online": False,
            "error": "Invalid JSON from /api/tags",
            "tool_models": [],
            "no_tool_models": [],
        }

    models: List[str] = []
    if isinstance(data, dict) and "models" in data:
        for item in data.get("models", []):
            if isinstance(item, dict) and item.get("name"):
                models.append(str(item["name"]).strip())
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("name"):
                models.append(str(item["name"]).strip())

    tool_models: List[str] = []
    no_tool_models: List[str] = []
    show_url = f"{base_url}/api/show"

    for model_name in models:
        has_tools = False
        try:
            show_resp = requests.post(show_url, json={"model": model_name}, timeout=4.0)
            if show_resp.status_code == 200:
                try:
                    show_data = show_resp.json()
                except ValueError:
                    show_data = {}
                capabilities = show_data.get("capabilities", [])
                if isinstance(capabilities, list) and "tools" in capabilities:
                    has_tools = True
        except Exception:
            has_tools = False

        if has_tools:
            tool_models.append(model_name)
        else:
            no_tool_models.append(model_name)

    return {
        "provider": "ollama",
        "online": True,
        "error": None,
        "tool_models": tool_models,
        "no_tool_models": no_tool_models,
    }


def _probe_llama_server_host(host_config: Dict[str, Any]) -> Dict[str, Any]:
    root_url = _llama_server_root_url(host_config)
    base_url = _llama_server_base_url(host_config)
    if not root_url or not base_url:
        return {
            "provider": "llama-server",
            "online": False,
            "error": "No host/base_url configured",
            "tool_models": [],
            "no_tool_models": [],
        }

    configured_models = _resolve_all_configured_models(host_config)
    native_models_url = f"{root_url}/models"
    openai_models_url = f"{base_url}/models"
    headers = _llama_server_headers(host_config)

    probe_errors: List[str] = []

    try:
        native_response = requests.get(native_models_url, headers=headers, timeout=4.0)
    except Exception as e:
        probe_errors.append(f"Could not list /models: {e}")
    else:
        if native_response.status_code in {401, 403}:
            return {
                "provider": "llama-server",
                "online": False,
                "error": f"HTTP {native_response.status_code} from /models",
                "tool_models": [],
                "no_tool_models": [],
            }
        if native_response.status_code == 200:
            try:
                native_data = native_response.json()
            except ValueError:
                probe_errors.append("Invalid JSON from /models")
            else:
                models, model_statuses = _parse_llama_server_models_response(native_data)
                if models:
                    return {
                        "provider": "llama-server",
                        "online": True,
                        "error": None,
                        "tool_models": models,
                        "no_tool_models": [],
                        "model_statuses": model_statuses,
                        "model_listing_endpoint": "/models",
                    }
                probe_errors.append("No models returned from /models")
        else:
            probe_errors.append(f"HTTP {native_response.status_code} from /models")

    try:
        models_response = requests.get(openai_models_url, headers=headers, timeout=4.0)
    except Exception as e:
        probe_errors.append(f"Could not list /v1/models: {e}")
        models = configured_models
        return {
            "provider": "llama-server",
            "online": bool(models),
            "error": "; ".join(probe_errors) or "Unreachable",
            "tool_models": models if models else [],
            "no_tool_models": [],
        }

    if models_response.status_code != 200:
        if models_response.status_code in {401, 403}:
            return {
                "provider": "llama-server",
                "online": False,
                "error": f"HTTP {models_response.status_code} from /v1/models",
                "tool_models": [],
                "no_tool_models": [],
            }
        probe_errors.append(f"HTTP {models_response.status_code} from /v1/models")
        models = configured_models
        return {
            "provider": "llama-server",
            "online": bool(models),
            "error": "; ".join(probe_errors) or f"HTTP {models_response.status_code} from /v1/models",
            "tool_models": models,
            "no_tool_models": [],
        }

    try:
        data = models_response.json()
    except ValueError:
        probe_errors.append("Invalid JSON from /v1/models")
        models = configured_models
        return {
            "provider": "llama-server",
            "online": bool(models),
            "error": "; ".join(probe_errors) or "Invalid JSON from /v1/models",
            "tool_models": models,
            "no_tool_models": [],
        }

    models = _parse_openai_models_response(data) or configured_models
    return {
        "provider": "llama-server",
        "online": bool(models),
        "error": None if models else "; ".join(probe_errors + ["No models returned from /v1/models."]),
        "tool_models": models,
        "no_tool_models": [],
        "model_statuses": {},
        "model_listing_endpoint": "/v1/models",
    }


def _probe_static_host(host_config: Dict[str, Any]) -> Dict[str, Any]:
    provider = _normalize_provider_name(host_config.get("provider"))
    tool_models, no_tool_models = _resolve_host_model_lists(host_config)
    api_key_error = _resolve_api_key_error(provider, host_config)
    if api_key_error:
        return {
            "provider": provider,
            "online": False,
            "error": api_key_error,
            "tool_models": [],
            "no_tool_models": [],
        }

    return {
        "provider": provider,
        "online": True,
        "error": None if (tool_models or no_tool_models) else "No suggested models configured.",
        "tool_models": tool_models,
        "no_tool_models": no_tool_models,
    }


def probe_model_hosts(model_hosts: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Probe configured model hosts and list their selectable models."""
    results: Dict[str, Dict[str, Any]] = {}

    for raw_name, raw_host_config in (model_hosts or {}).items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_host_config, dict):
            continue

        host_config = dict(raw_host_config)
        provider = _normalize_provider_name(host_config.get("provider"))
        if not provider:
            results[name] = {
                "provider": "",
                "online": False,
                "error": "Missing provider in host config.",
                "tool_models": [],
                "no_tool_models": [],
            }
            continue

        if provider == "ollama":
            results[name] = _probe_ollama_host(host_config)
            continue
        if provider == "llama-server":
            results[name] = _probe_llama_server_host(host_config)
            continue

        results[name] = _probe_static_host(host_config)

    return results


class ConversationSession:
    """UI-agnostic conversation/session controller.

    This class encapsulates:
      - provider-backed chat calls
      - tool execution (server-side only)
      - conversation state & persistence

    It does **not** import or depend on any UI toolkit.
    """

    def __init__(
        self,
        model_hosts: Dict[str, Dict[str, Any]],
        available_functions: Dict[str, Any],
        context_window_size: int = 131072,
        tool_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        system_message: Optional[str] = None,
    ) -> None:
        self.model_hosts = model_hosts or {}
        self.available_functions = available_functions or {}
        self.context_window_size = context_window_size
        self.tool_provider = tool_provider
        self.system_message = (system_message or "").strip()
        self.enabled_tool_names: Optional[set[str]] = None
        self._missing_system_message_logged = False

        self.messages: List[Dict[str, Any]] = []
        self.current_conversation_id: Optional[str] = None
        self.host: Optional[str] = None
        self.model: Optional[str] = None

        self.conversation_manager = ConversationManager()

    # === Configuration & lifecycle ===

    def configure(self, host: Optional[str], model: Optional[str]) -> None:
        """Store host/model selection for future calls."""
        next_host = (host or "").strip() or None
        next_model = (model or "").strip() or None

        current_provider = _normalize_provider_name(
            (self.model_hosts.get(self.host or "", {}) or {}).get("provider")
        )
        next_provider = _normalize_provider_name(
            (self.model_hosts.get(next_host or "", {}) or {}).get("provider")
        )
        if (
            self.messages
            and current_provider
            and next_provider
            and _provider_message_family(current_provider) != _provider_message_family(next_provider)
        ):
            self.reset()

        self.host = next_host
        self.model = next_model

    def set_system_message(self, system_message: Optional[str]) -> None:
        """Update the configured system message for upcoming new conversations."""
        self.system_message = (system_message or "").strip()
        self._missing_system_message_logged = False

    def set_enabled_tools(self, enabled_tools: Optional[List[str]]) -> None:
        """Limit this session to a client-selected tool allow-list."""
        if enabled_tools is None:
            self.enabled_tool_names = None
            return
        self.enabled_tool_names = {
            str(name).strip() for name in enabled_tools if str(name).strip()
        }

    def _resolve_available_functions(self) -> Dict[str, Any]:
        functions = self.available_functions
        if self.tool_provider is not None:
            try:
                provided = self.tool_provider() or {}
                if isinstance(provided, dict):
                    functions = provided
            except Exception:
                pass

        if self.enabled_tool_names is None:
            return functions
        return {
            name: function
            for name, function in functions.items()
            if name in self.enabled_tool_names
        }

    def reset(self) -> None:
        """Reset in-memory state for a fresh conversation."""
        self.messages = []
        self.current_conversation_id = None
        self._missing_system_message_logged = False

    def _ensure_system_message(self) -> None:
        """Ensure configured system message exists as the first message."""
        if not self.system_message:
            if not self._missing_system_message_logged:
                _LOGGER.info("No system message configured; skipping system message injection.")
                self._missing_system_message_logged = True
            return

        self._missing_system_message_logged = False

        first_message = self.messages[0] if self.messages else None
        if isinstance(first_message, dict) and first_message.get("role") == "system":
            if first_message.get("content") != self.system_message:
                self.messages[0] = {"role": "system", "content": self.system_message}
            return

        self.messages.insert(0, {"role": "system", "content": self.system_message})

    def _ensure_conversation_id(self, first_user_message: Optional[str]) -> None:
        """Ensure we have a persistent conversation id in the store."""
        if self.current_conversation_id:
            return

        title = (first_user_message or "").strip() or "New conversation"
        host = self.host or ""
        model = self.model or ""

        self.current_conversation_id = self.conversation_manager.start_new_conversation(
            title,
            host,
            model,
        )

    def _resolve_selected_host(self) -> Tuple[Optional[str], Dict[str, Any]]:
        if not self.model_hosts:
            return None, {}

        if not self.host:
            raise RuntimeError("No model host selected.")

        host_config = self.model_hosts.get(self.host)
        if not isinstance(host_config, dict):
            raise RuntimeError(f"Unknown model host '{self.host}'.")

        return self.host, dict(host_config)

    def _default_model_for_host(self, host_config: Dict[str, Any]) -> str:
        explicit_default = str(host_config.get("default_model", "")).strip()
        if explicit_default:
            return explicit_default

        tool_models, no_tool_models = _resolve_host_model_lists(host_config)
        if tool_models:
            return tool_models[0]
        if no_tool_models:
            return no_tool_models[0]
        return ""

    def _coerce_request_timeout_seconds(self, host_config: Dict[str, Any]) -> Optional[float]:
        raw_value = host_config.get("request_timeout_seconds")
        if raw_value is None:
            return _DEFAULT_REQUEST_TIMEOUT_SECONDS
        try:
            value = float(raw_value)
        except Exception:
            return _DEFAULT_REQUEST_TIMEOUT_SECONDS
        if value <= 0:
            return None
        return value

    def _resolve_request_config(self) -> Tuple[str, Dict[str, Any], Optional[float]]:
        host_name, host_config = self._resolve_selected_host()
        if not host_name or not host_config:
            raise RuntimeError(
                "No model host configured. Add 'model_hosts' to config.json."
            )

        provider = _normalize_provider_name(host_config.get("provider"))
        if not provider:
            raise RuntimeError(f"Model host '{host_name}' is missing 'provider'.")

        model_name = (self.model or "").strip()
        if not model_name:
            model_name = self._default_model_for_host(host_config)
            if model_name:
                self.model = model_name

        if not model_name:
            raise RuntimeError(
                f"No model selected for host '{host_name}'. Configure a model in the UI or "
                "add models/default_model in config.json."
            )

        request_timeout_seconds = self._coerce_request_timeout_seconds(host_config)
        model_config = dict(host_config)
        model_config["provider"] = provider
        model_config["model"] = model_name
        return host_name, model_config, request_timeout_seconds

    # === Persistence helpers (UI-agnostic) ===

    def save_snapshot(self, chat_transcript: str, log_text: str) -> None:
        """Save a snapshot of the current conversation state."""
        if not self.current_conversation_id:
            return

        host = self.host or ""
        model = self.model or ""

        try:
            self.conversation_manager.save_snapshot(
                self.current_conversation_id,
                self.messages,
                chat_transcript or "",
                log_text or "",
                host,
                model,
            )
        except Exception:
            _LOGGER.exception("Failed to save conversation snapshot")

    def list_conversations(self) -> List[Dict[str, Any]]:
        """Return a list of stored conversations."""
        return self.conversation_manager.list_conversations()

    def load_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """Load a conversation snapshot from storage."""
        try:
            return self.conversation_manager.load_conversation(conv_id)
        except Exception:
            return None

    def restore_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """Restore internal state from a stored conversation."""
        data = self.load_conversation(conv_id)
        if not data:
            return None

        messages = data.get("messages") or []
        if isinstance(messages, list):
            self.messages = messages
        else:
            self.messages = []

        self.current_conversation_id = data.get("id") or conv_id
        self.host = (data.get("host") or "") or None
        self.model = (data.get("model") or "") or None

        return data

    # === Core chat step ===

    def step(
        self,
        user_message: Optional[str],
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        *,
        stop_requested: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Perform one full assistant turn, including tool calls."""
        events: List[Dict[str, Any]] = []
        stats: Dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "context_fullness": 0.0,
            "context_fullness_with_output": 0.0,
            "tokens_per_second": 0.0,
        }

        def emit(ev: Dict[str, Any]) -> None:
            events.append(ev)
            if on_event is not None:
                try:
                    on_event(ev)
                except Exception:
                    pass

        if user_message:
            if not self.current_conversation_id:
                self._ensure_system_message()
            self._ensure_conversation_id(user_message)
            self.messages.append({"role": "user", "content": user_message})

        try:
            host_name, model_config, request_timeout_seconds = self._resolve_request_config()
            client = build_model_provider_client(
                model_config=model_config,
                request_timeout_seconds=request_timeout_seconds,
            )
        except Exception as e:
            error_text = str(e)
            error_event = {
                "v": 1,
                "type": "agent_message",
                "ts": datetime.now(timezone.utc).isoformat(),
                "conversation_id": self.current_conversation_id,
                "data": {
                    "kind": "error",
                    "content": error_text,
                    "log": error_text,
                    "stats": stats,
                    "tool_calls": [],
                },
            }
            emit(error_event)
            return {
                "conversation_id": self.current_conversation_id,
                "assistant_message": error_text,
                "stats": stats,
                "events": events,
            }

        provider = str(model_config.get("provider", "")).strip().lower()
        model_name = str(model_config.get("model", "")).strip()
        request_options = (
            dict(model_config.get("options"))
            if isinstance(model_config.get("options"), dict)
            else {}
        )

        final_assistant_content = ""
        available_functions = self._resolve_available_functions()
        tool_payloads = (
            available_functions
            if provider == "ollama"
            else build_tool_schemas(available_functions)
        )

        while True:
            if stop_requested is not None and stop_requested():
                final_assistant_content = "Turn stopped because the client disconnected."
                emit({
                    "v": 1,
                    "type": "agent_message",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "conversation_id": self.current_conversation_id,
                    "data": {"kind": "error", "code": "TURN_CANCELLED",
                             "content": final_assistant_content, "tool_calls": []},
                })
                break
            timestamp = time.time()
            try:
                response = client.chat(
                    messages=self.messages,
                    tools=tool_payloads,
                    context_window_size=self.context_window_size,
                    seed=None,
                    options=request_options,
                )
            except Exception as e:
                error_text = f"Error calling model: {e}"
                error_event = {
                    "v": 1,
                    "type": "agent_message",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "conversation_id": self.current_conversation_id,
                    "data": {
                        "kind": "error",
                        "content": error_text,
                        "log": error_text,
                        "stats": stats,
                        "tool_calls": [],
                    },
                }
                emit(error_event)
                final_assistant_content = error_text
                break

            prompt_tokens = int(response.prompt_tokens or 0)
            completion_tokens = int(response.completion_tokens or 0)
            duration = max(time.time() - timestamp, 1e-6)
            tokens_per_second = (
                float(completion_tokens) / float(duration) if completion_tokens > 0 else 0.0
            )

            fullness = (
                float(prompt_tokens) / float(self.context_window_size)
                if self.context_window_size
                else 0.0
            )
            total_fullness = (
                float(prompt_tokens + completion_tokens) / float(self.context_window_size)
                if self.context_window_size
                else 0.0
            )

            stats = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "context_fullness": float(fullness),
                "context_fullness_with_output": float(total_fullness),
                "tokens_per_second": float(tokens_per_second),
            }

            normalized_message = response.message if isinstance(response.message, dict) else {}
            raw_tool_calls = normalized_message.get("tool_calls") or []
            tool_call_summaries: List[Dict[str, Any]] = []
            for tool_call in raw_tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = str(tool_call.get("id", "")).strip()
                function_obj = tool_call.get("function", {})
                if not isinstance(function_obj, dict):
                    continue
                tool_name = str(function_obj.get("name", "")).strip()
                arguments = function_obj.get("arguments", {})
                if not tool_name:
                    continue
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_call_summaries.append(
                    {
                        "id": tool_call_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                    }
                )

            assistant_text = str(normalized_message.get("content") or "")
            runtime_payload = {
                "provider": provider,
                "host_name": host_name,
                "model": model_name,
            }
            host_url = str(model_config.get("host", "")).strip()
            base_url = str(model_config.get("base_url", "")).strip()
            if host_url:
                runtime_payload["host_url"] = host_url
            if base_url:
                runtime_payload["base_url"] = base_url

            log_payload = {
                "context": {
                    "window_size": self.context_window_size,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "fullness": float(fullness),
                    "fullness_with_output": float(total_fullness),
                },
                "performance": {
                    "tokens_per_second": float(tokens_per_second),
                    "duration_seconds": float(duration),
                },
                "runtime": runtime_payload,
                "assistant": {
                    "preview": assistant_text[:30],
                },
                "tool_calls": tool_call_summaries,
            }

            kind = "tool_call" if tool_call_summaries else "assistant_message"
            agent_event = {
                "v": 1,
                "type": "agent_message",
                "ts": datetime.now(timezone.utc).isoformat(),
                "conversation_id": self.current_conversation_id,
                "data": {
                    "kind": kind,
                    "content": assistant_text,
                    "log": log_payload,
                    "stats": stats,
                    "tool_calls": tool_call_summaries,
                },
            }
            emit(agent_event)

            self.messages.append(response.history_message)

            if tool_call_summaries:
                for tool_call in tool_call_summaries:
                    tool_name = str(tool_call.get("tool_name") or "").strip() or "unknown_tool"
                    tool_arguments = tool_call.get("arguments", {})
                    if not isinstance(tool_arguments, dict):
                        tool_arguments = {}

                    func = available_functions.get(tool_name)
                    if stop_requested is not None and stop_requested():
                        result: Any = "Tool not executed: the client disconnected."
                    elif not func:
                        result: Any = f"Unknown tool requested: {tool_name}"
                    else:
                        try:
                            result = func(**tool_arguments)
                        except Exception as e:
                            result = f"Error while running tool {tool_name}: {e}"

                    result_text = str(result)
                    tool_result_event = {
                        "v": 1,
                        "type": "agent_message",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "conversation_id": self.current_conversation_id,
                        "data": {
                            "kind": "tool_result",
                            "content": "",
                            "log": "",
                            "stats": stats,
                            "tool_calls": [],
                            "tool_name": tool_name,
                            "tool_call_id": tool_call.get("tool_call_id", ""),
                            "tool_arguments": tool_arguments,
                            "tool_output": result_text,
                        },
                    }
                    emit(tool_result_event)

                    self.messages.append(
                        client.make_tool_result_message(
                            tool_call=tool_call,
                            tool_output=result_text,
                        )
                    )

                continue

            final_assistant_content = assistant_text
            break

        return {
            "conversation_id": self.current_conversation_id,
            "assistant_message": final_assistant_content,
            "stats": stats,
            "events": events,
        }
