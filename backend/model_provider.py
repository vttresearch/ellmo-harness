from __future__ import annotations

import inspect
import json
import os
import re
from types import UnionType
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Union, get_args, get_origin, get_type_hints


class ModelProviderError(RuntimeError):
    """Base class for model-provider errors."""


class ModelProviderConfigError(ModelProviderError):
    """Raised when a model provider configuration is invalid."""


class ModelProviderDependencyError(ModelProviderError):
    """Raised when a required provider SDK is missing."""


@dataclass
class ProviderChatResponse:
    """Provider-agnostic chat response payload."""

    message: Dict[str, Any]
    history_message: Any
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ModelProviderClient(Protocol):
    provider: str
    model: str

    def chat(
        self,
        *,
        messages: List[Any],
        tools: Dict[str, Any],
        context_window_size: int,
        seed: Optional[int],
        options: Dict[str, Any],
    ) -> ProviderChatResponse:
        """Execute one chat request and return normalized output."""

    def make_tool_result_message(
        self,
        *,
        tool_call: Dict[str, Any],
        tool_output: str,
    ) -> Any:
        """Build a provider-compatible tool-result message for conversation history."""


_DOCSTRING_ARG_LINE_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*:\s*(.*)$"
)


def _parse_tool_arguments(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {"raw": value}
        if isinstance(parsed, dict):
            return parsed
        return {"raw": parsed}
    if value is None:
        return {}
    return {"raw": value}


def _normalize_ollama_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
    if not raw_tool_calls:
        return []

    normalized: List[Dict[str, Any]] = []
    for raw_call in raw_tool_calls:
        call_id = getattr(raw_call, "id", None)
        if call_id is None and isinstance(raw_call, dict):
            call_id = raw_call.get("id")

        function_obj = getattr(raw_call, "function", None)
        if function_obj is None and isinstance(raw_call, dict):
            function_obj = raw_call.get("function")

        function_name: Optional[str] = None
        function_args: Dict[str, Any] = {}

        if function_obj is not None:
            if isinstance(function_obj, dict):
                function_name = function_obj.get("name")
                function_args = _parse_tool_arguments(function_obj.get("arguments"))
            else:
                function_name = getattr(function_obj, "name", None)
                function_args = _parse_tool_arguments(getattr(function_obj, "arguments", {}))

        if not function_name:
            continue

        normalized_call = {
            "function": {
                "name": str(function_name),
                "arguments": function_args,
            }
        }
        if call_id:
            normalized_call["id"] = str(call_id)
        normalized.append(normalized_call)

    return normalized


def _normalize_ollama_message(raw_message: Any) -> Dict[str, Any]:
    content = ""
    if isinstance(raw_message, dict):
        content = str(raw_message.get("content") or "")
        raw_tool_calls = raw_message.get("tool_calls")
    else:
        content = str(getattr(raw_message, "content", "") or "")
        raw_tool_calls = getattr(raw_message, "tool_calls", None)

    return {
        "role": "assistant",
        "content": content,
        "tool_calls": _normalize_ollama_tool_calls(raw_tool_calls),
    }


class OllamaProviderClient:
    provider = "ollama"

    def __init__(
        self,
        *,
        host: str,
        model: str,
        request_timeout_seconds: Optional[float],
    ) -> None:
        if not host:
            raise ModelProviderConfigError("Ollama provider requires a non-empty 'host'.")
        if not model:
            raise ModelProviderConfigError("Model configuration requires a non-empty 'model'.")

        try:
            import ollama  # type: ignore
        except Exception as e:
            raise ModelProviderDependencyError(
                "The 'ollama' Python package is required for provider 'ollama': "
                f"{e}"
            ) from e

        self.model = model
        self._client = ollama.Client(host=host, timeout=request_timeout_seconds)

    def chat(
        self,
        *,
        messages: List[Any],
        tools: Dict[str, Any],
        context_window_size: int,
        seed: Optional[int],
        options: Dict[str, Any],
    ) -> ProviderChatResponse:
        merged_options = dict(options or {})
        if context_window_size > 0:
            merged_options["num_ctx"] = int(context_window_size)
        if seed is not None:
            merged_options["seed"] = int(seed)

        response = self._client.chat(
            model=self.model,
            messages=messages,
            # The SDK's callable converter drops Literal enums. Send the same
            # explicit schemas used by the other providers, including aliases.
            tools=[
                build_tool_schemas({name: tool})[name] if callable(tool) else tool
                for name, tool in tools.items()
            ],
            options=merged_options,
        )

        raw_message = getattr(response, "message", None)
        if raw_message is None and isinstance(response, dict):
            raw_message = response.get("message")

        if raw_message is None:
            raise RuntimeError("Model response did not include a message object.")

        return ProviderChatResponse(
            message=_normalize_ollama_message(raw_message),
            history_message=raw_message,
            prompt_tokens=int(getattr(response, "prompt_eval_count", 0) or 0),
            completion_tokens=int(getattr(response, "eval_count", 0) or 0),
        )

    def make_tool_result_message(
        self,
        *,
        tool_call: Dict[str, Any],
        tool_output: str,
    ) -> Any:
        tool_name = str(tool_call.get("tool_name", "")).strip()
        return {
            "role": "tool",
            "tool_name": tool_name,
            "content": str(tool_output),
        }


def _parse_docstring_descriptions(func: Any) -> tuple[str, Dict[str, str]]:
    doc = inspect.getdoc(func) or ""
    if not doc:
        return "", {}

    description_lines: List[str] = []
    arg_descriptions: Dict[str, str] = {}
    in_args = False
    current_arg: Optional[str] = None

    for raw_line in doc.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        lowered = stripped.lower()

        if lowered in {"args:", "arguments:", "parameters:"}:
            in_args = True
            current_arg = None
            continue
        if lowered.startswith(("returns:", "yields:", "raises:")):
            in_args = False
            current_arg = None
            continue

        if not in_args:
            description_lines.append(line)
            continue

        if not stripped:
            current_arg = None
            continue

        match = _DOCSTRING_ARG_LINE_RE.match(stripped)
        if match:
            current_arg = match.group(1)
            arg_descriptions[current_arg] = match.group(3).strip()
            continue

        if current_arg:
            existing = arg_descriptions.get(current_arg, "")
            arg_descriptions[current_arg] = (
                f"{existing} {stripped}".strip() if existing else stripped
            )

    return "\n".join(description_lines).strip(), arg_descriptions


def _annotation_to_json_schema(annotation: Any) -> Dict[str, Any]:
    if annotation is inspect._empty:
        return {"type": "string"}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (Union, UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_to_json_schema(non_none[0])
        return {"type": "string"}

    if origin is not None and str(origin) in {"typing.Literal", "typing_extensions.Literal"}:
        values = list(args)
        if not values:
            return {"type": "string"}
        enum_types = {type(v) for v in values}
        if enum_types == {int}:
            value_type = "integer"
        elif enum_types <= {int, float}:
            value_type = "number"
        elif enum_types == {bool}:
            value_type = "boolean"
        else:
            value_type = "string"
        return {"type": value_type, "enum": values}

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation in {dict, Dict}:
        return {"type": "object"}
    if annotation in {list, List, tuple, set}:
        return {"type": "array"}

    if origin in {dict, Dict}:
        return {"type": "object"}
    if origin in {list, List}:
        return {
            "type": "array",
            "items": _annotation_to_json_schema(args[0]) if args else {},
        }
    if origin in {tuple, set}:
        return {"type": "array"}

    return {"type": "string"}


def build_tool_schemas(tools: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    schemas: Dict[str, Dict[str, Any]] = {}

    for tool_name, func in tools.items():
        signature = inspect.signature(func)
        try:
            annotations = get_type_hints(func)
        except (NameError, TypeError):
            annotations = {}
        description, arg_descriptions = _parse_docstring_descriptions(func)
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param_name, param in signature.parameters.items():
            if param.kind not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                continue

            property_schema = _annotation_to_json_schema(annotations.get(param_name, param.annotation))
            arg_description = arg_descriptions.get(param_name, "").strip()
            if arg_description:
                property_schema["description"] = arg_description
            properties[param_name] = property_schema
            if param.default is inspect._empty:
                required.append(param_name)

        parameters_schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            parameters_schema["required"] = required

        schemas[str(tool_name)] = {
            "type": "function",
            "function": {
                "name": str(tool_name),
                "description": description,
                "parameters": parameters_schema,
            },
        }

    return schemas


def _normalize_openai_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
    if not raw_tool_calls:
        return []

    normalized: List[Dict[str, Any]] = []
    for index, raw_call in enumerate(raw_tool_calls, start=1):
        call_id = getattr(raw_call, "id", None)
        if call_id is None and isinstance(raw_call, dict):
            call_id = raw_call.get("id")
        if not call_id:
            call_id = f"call_{index}"

        function_obj = getattr(raw_call, "function", None)
        if function_obj is None and isinstance(raw_call, dict):
            function_obj = raw_call.get("function")
        if function_obj is None:
            continue

        if isinstance(function_obj, dict):
            function_name = function_obj.get("name")
            arguments_raw = function_obj.get("arguments")
        else:
            function_name = getattr(function_obj, "name", None)
            arguments_raw = getattr(function_obj, "arguments", None)

        if not function_name:
            continue

        normalized_call = {
            "id": str(call_id),
            "function": {
                "name": str(function_name),
                "arguments": _parse_tool_arguments(arguments_raw),
            },
        }
        normalized.append(normalized_call)

    return normalized


def _stringify_tool_arguments(value: Dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


def _coerce_openai_content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p).strip()
    return str(value)


def _merge_default_headers(*header_maps: Any) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for header_map in header_maps:
        if not isinstance(header_map, dict):
            continue
        for raw_key, raw_value in header_map.items():
            key = str(raw_key).strip()
            value = str(raw_value).strip()
            if key and value:
                merged[key] = value
    return merged


def _get_response_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)

    value = getattr(obj, key, None)
    if value is not None:
        return value

    model_extra = getattr(obj, "model_extra", None)
    if isinstance(model_extra, dict):
        return model_extra.get(key, default)

    return default


def _coerce_int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _extract_usage_counts(response: Any) -> tuple[int, int]:
    usage = _get_response_value(response, "usage")
    prompt_tokens = _coerce_int_value(_get_response_value(usage, "prompt_tokens"))
    completion_tokens = _coerce_int_value(_get_response_value(usage, "completion_tokens"))
    if prompt_tokens or completion_tokens:
        return prompt_tokens, completion_tokens

    # llama-server can include llama.cpp-specific timing counters instead of
    # standard OpenAI usage fields.
    timings = _get_response_value(response, "timings")
    prompt_tokens = _coerce_int_value(_get_response_value(timings, "prompt_n"))
    completion_tokens = _coerce_int_value(_get_response_value(timings, "predicted_n"))
    return prompt_tokens, completion_tokens


class OpenAICompatibleProviderClient:
    provider = "openai"
    display_name = "OpenAI"
    default_api_key_env = "OPENAI_API_KEY"
    default_base_url = ""

    def _build_default_headers(self, model_config: Dict[str, Any]) -> Dict[str, str]:
        return _merge_default_headers(model_config.get("default_headers"))

    def __init__(
        self,
        *,
        model: str,
        model_config: Dict[str, Any],
        request_timeout_seconds: Optional[float],
    ) -> None:
        if not model:
            raise ModelProviderConfigError("Model configuration requires a non-empty 'model'.")

        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:
            raise ModelProviderDependencyError(
                f"The 'openai' Python package is required for provider '{self.provider}': "
                f"{e}"
            ) from e

        api_key = str(model_config.get("api_key", "")).strip()
        api_key_env = str(model_config.get("api_key_env", self.default_api_key_env)).strip()
        if not api_key:
            api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            raise ModelProviderConfigError(
                f"Missing {self.display_name} API key. Set {api_key_env} or provide model.api_key."
            )

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        base_url = str(model_config.get("base_url", self.default_base_url)).strip()
        organization = str(model_config.get("organization", "")).strip()
        project = str(model_config.get("project", "")).strip()
        default_headers = self._build_default_headers(model_config)
        if base_url:
            client_kwargs["base_url"] = base_url
        if organization:
            client_kwargs["organization"] = organization
        if project:
            client_kwargs["project"] = project
        if default_headers:
            client_kwargs["default_headers"] = default_headers

        self.model = model
        self._client = OpenAI(**client_kwargs)
        self._request_timeout_seconds = request_timeout_seconds

    def chat(
        self,
        *,
        messages: List[Any],
        tools: Dict[str, Any],
        context_window_size: int,
        seed: Optional[int],
        options: Dict[str, Any],
    ) -> ProviderChatResponse:
        request_kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if self._request_timeout_seconds is not None:
            request_kwargs["timeout"] = float(self._request_timeout_seconds)

        if tools:
            request_kwargs["tools"] = list(tools.values())

        if seed is not None:
            request_kwargs["seed"] = int(seed)

        request_kwargs.update(dict(options or {}))

        response = self._client.chat.completions.create(**request_kwargs)
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise RuntimeError("Model response did not include any choices.")

        first_choice = choices[0]
        raw_message = getattr(first_choice, "message", None)
        if raw_message is None:
            raise RuntimeError("Model response did not include a message object.")

        raw_tool_calls = getattr(raw_message, "tool_calls", None)
        normalized_tool_calls = _normalize_openai_tool_calls(raw_tool_calls)

        raw_content = getattr(raw_message, "content", None)
        content = _coerce_openai_content_text(raw_content)

        history_message: Dict[str, Any] = {"role": "assistant", "content": content or None}
        if normalized_tool_calls:
            history_tool_calls: List[Dict[str, Any]] = []
            for call in normalized_tool_calls:
                tool_call_id = str(call.get("id", "")).strip()
                function_obj = call.get("function", {}) if isinstance(call, dict) else {}
                function_name = str(function_obj.get("name", "")).strip()
                function_args = function_obj.get("arguments", {})
                if not tool_call_id or not function_name:
                    continue
                history_tool_calls.append(
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": function_name,
                            "arguments": _stringify_tool_arguments(
                                function_args if isinstance(function_args, dict) else {}
                            ),
                        },
                    }
                )
            if history_tool_calls:
                history_message["tool_calls"] = history_tool_calls

        prompt_tokens, completion_tokens = _extract_usage_counts(response)

        normalized_message = {
            "role": "assistant",
            "content": content,
            "tool_calls": normalized_tool_calls,
        }

        return ProviderChatResponse(
            message=normalized_message,
            history_message=history_message,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def make_tool_result_message(
        self,
        *,
        tool_call: Dict[str, Any],
        tool_output: str,
    ) -> Any:
        tool_call_id = str(tool_call.get("tool_call_id", "")).strip()
        if not tool_call_id:
            tool_call_id = str(tool_call.get("id", "")).strip()
        if not tool_call_id:
            raise ModelProviderConfigError(
                "OpenAI tool result requires tool_call_id from the assistant tool call."
            )

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": str(tool_output),
        }


class OpenAIProviderClient(OpenAICompatibleProviderClient):
    provider = "openai"
    display_name = "OpenAI"
    default_api_key_env = "OPENAI_API_KEY"
    default_base_url = ""


class OpenRouterProviderClient(OpenAICompatibleProviderClient):
    provider = "openrouter"
    display_name = "OpenRouter"
    default_api_key_env = "OPENROUTER_API_KEY"
    default_base_url = "https://openrouter.ai/api/v1"

    def _build_default_headers(self, model_config: Dict[str, Any]) -> Dict[str, str]:
        referer = str(model_config.get("http_referer", "")).strip()
        title = str(model_config.get("x_title", "")).strip()
        openrouter_headers: Dict[str, str] = {}
        if referer:
            openrouter_headers["HTTP-Referer"] = referer
        if title:
            openrouter_headers["X-Title"] = title
        return _merge_default_headers(openrouter_headers, model_config.get("default_headers"))


class LlamaServerProviderClient(OpenAICompatibleProviderClient):
    provider = "llama-server"
    display_name = "llama-server"
    default_api_key_env = "LLAMA_SERVER_API_KEY"
    default_base_url = "http://127.0.0.1:8080/v1"
    default_api_key = "sk-no-key-required"

    @staticmethod
    def _normalize_base_url(raw_value: str) -> str:
        base_url = raw_value.strip().rstrip("/")
        if not base_url:
            base_url = LlamaServerProviderClient.default_base_url
        if base_url.endswith("/v1"):
            return base_url
        return f"{base_url}/v1"

    def __init__(
        self,
        *,
        model: str,
        model_config: Dict[str, Any],
        request_timeout_seconds: Optional[float],
    ) -> None:
        resolved_config = dict(model_config)
        configured_base_url = str(resolved_config.get("base_url", "")).strip()
        configured_host = str(resolved_config.get("host", "")).strip()
        resolved_config["base_url"] = self._normalize_base_url(
            configured_base_url or configured_host or self.default_base_url
        )

        api_key_env = str(
            resolved_config.get("api_key_env", self.default_api_key_env)
        ).strip()
        api_key = str(resolved_config.get("api_key", "")).strip()
        if not api_key and api_key_env:
            api_key = os.getenv(api_key_env, "").strip()
        resolved_config["api_key"] = api_key or self.default_api_key

        super().__init__(
            model=model,
            model_config=resolved_config,
            request_timeout_seconds=request_timeout_seconds,
        )


def build_model_provider_client(
    *,
    model_config: Dict[str, Any],
    request_timeout_seconds: Optional[float],
) -> ModelProviderClient:
    provider = str(model_config.get("provider", "")).strip().lower()
    if not provider:
        raise ModelProviderConfigError("Model configuration requires a non-empty 'provider'.")
    model = str(model_config.get("model", "")).strip()

    if provider == "ollama":
        host = str(model_config.get("host", "")).strip()
        return OllamaProviderClient(
            host=host,
            model=model,
            request_timeout_seconds=request_timeout_seconds,
        )
    if provider == "openai":
        return OpenAIProviderClient(
            model=model,
            model_config=model_config,
            request_timeout_seconds=request_timeout_seconds,
        )
    if provider == "openrouter":
        return OpenRouterProviderClient(
            model=model,
            model_config=model_config,
            request_timeout_seconds=request_timeout_seconds,
        )
    if provider == "llama-server":
        return LlamaServerProviderClient(
            model=model,
            model_config=model_config,
            request_timeout_seconds=request_timeout_seconds,
        )

    raise ModelProviderConfigError(f"Unsupported provider '{provider}'.")
