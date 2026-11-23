from __future__ import annotations

import importlib
import config
import inspect
import json
import logging
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

_LOGGER = logging.getLogger(__name__)


class ToolCatalog:
    """Config-backed tool catalog with runtime callable resolution."""

    def __init__(self, config_path: Optional[Path] = None, tools_key: str = "tools") -> None:
        self.config_path = config_path or config._config_path()
        self.tools_key = tools_key
        self._lock = RLock()
        self._callable_cache: Dict[Tuple[str, str, str], Any] = {}
        self._load_errors: Dict[Tuple[str, str, str], str] = {}

    def list_tools(self) -> List[Dict[str, Any]]:
        tools = self._load_tools()
        rows: List[Dict[str, Any]] = []
        for item in tools:
            name = item["name"]
            module = item["module"]
            function = item["function"]
            description = item["description"]
            enabled = item["enabled"]
            key = self._cache_key(name, module, function)

            signature = ""
            with self._lock:
                load_error = self._load_errors.get(key, "")

            try:
                func = self._load_callable(name, module, function)
                signature = str(inspect.signature(func))
                if not description:
                    doc = inspect.getdoc(func) or ""
                    description = doc.splitlines()[0].strip() if doc else ""
            except Exception as e:
                if not load_error:
                    load_error = str(e)

            rows.append(
                {
                    "name": name,
                    "module": module,
                    "function": function,
                    "enabled": enabled,
                    "description": description,
                    "signature": signature,
                    "load_error": load_error,
                }
            )
        return rows

    def get_enabled_tool_names(self) -> List[str]:
        names: List[str] = []
        seen = set()
        for item in self._load_tools():
            if not item["enabled"]:
                continue
            name = item["name"]
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    def get_enabled_tool_functions(self) -> Dict[str, Any]:
        functions: Dict[str, Any] = {}
        for item in self._load_tools():
            if not item["enabled"]:
                continue

            name = item["name"]
            module = item["module"]
            function = item["function"]
            key = self._cache_key(name, module, function)

            with self._lock:
                had_error_before = key in self._load_errors

            try:
                functions[name] = self._load_callable(name, module, function)
            except Exception:
                if not had_error_before:
                    _LOGGER.exception("Failed to load tool '%s' from %s.%s", name, module, function)

        return functions

    def set_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(tools, list):
            raise ValueError("'tools' must be a list.")

        normalized: List[Dict[str, Any]] = []
        seen_names = set()
        for idx, item in enumerate(tools):
            if not isinstance(item, dict):
                raise ValueError(f"Tool entry at index {idx} must be an object.")

            name = str(item.get("name") or "").strip()
            module = str(item.get("module") or "").strip()
            function = str(item.get("function") or "").strip()
            description = str(item.get("description") or "").strip()
            enabled = bool(item.get("enabled", True))

            if not name:
                raise ValueError(f"Tool entry at index {idx} has missing 'name'.")
            if name in seen_names:
                raise ValueError(f"Duplicate tool name in payload: {name}")
            if not module:
                raise ValueError(f"Tool '{name}' has missing 'module'.")
            if not function:
                raise ValueError(f"Tool '{name}' has missing 'function'.")

            seen_names.add(name)
            normalized.append(
                {
                    "name": name,
                    "module": module,
                    "function": function,
                    "enabled": enabled,
                    "description": description,
                }
            )

        config_data = self._read_config_data()
        config_data[self.tools_key] = normalized
        self._write_config_data(config_data)
        return normalized

    def set_enabled_tools(self, enabled_tools: List[str]) -> List[str]:
        if not isinstance(enabled_tools, list):
            raise ValueError("'enabled_tools' must be a list.")

        normalized: List[str] = []
        seen = set()
        for raw_name in enabled_tools:
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ValueError("Each enabled tool must be a non-empty string.")
            name = raw_name.strip()
            if name in seen:
                continue
            seen.add(name)
            normalized.append(name)

        config_data = self._read_config_data()
        tools_raw = config_data.get(self.tools_key)
        if not isinstance(tools_raw, list):
            raise ValueError(f"Invalid config: '{self.tools_key}' must be a list.")

        known_names: List[str] = []
        for item in tools_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            known_names.append(name)

        unknown = [name for name in normalized if name not in known_names]
        if unknown:
            raise ValueError(f"Unknown tool(s): {', '.join(sorted(unknown))}")

        enabled_set = set(normalized)
        for item in tools_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            item["enabled"] = name in enabled_set

        config_data[self.tools_key] = tools_raw
        self._write_config_data(config_data)
        return [name for name in known_names if name in enabled_set]

    def _read_config_data(self) -> Dict[str, Any]:
        path = self.config_path
        if not path.exists():
            message = f"Config file not found at {path}."
            _LOGGER.error(message)
            return {}

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            _LOGGER.exception("Failed to read config file at %s", path)
            return {}

        if not isinstance(data, dict):
            _LOGGER.warning("Config file at %s is not a JSON object.", path)
            return {}
        return data

    def _write_config_data(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("Config payload must be a JSON object.")

        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")

        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

        temp_path.replace(path)

    def _load_tools(self) -> List[Dict[str, Any]]:
        config_data = self._read_config_data()
        tools_raw = config_data.get(self.tools_key, [])
        if not isinstance(tools_raw, list):
            _LOGGER.warning("Config key '%s' is missing or invalid.", self.tools_key)
            return []

        tools: List[Dict[str, Any]] = []
        seen_names = set()
        for idx, item in enumerate(tools_raw):
            if not isinstance(item, dict):
                _LOGGER.warning("Ignoring non-object tool entry at index %d.", idx)
                continue

            name = str(item.get("name") or "").strip()
            module = str(item.get("module") or "").strip()
            function = str(item.get("function") or "").strip()
            description = str(item.get("description") or "").strip()
            enabled = bool(item.get("enabled", True))

            if not name:
                _LOGGER.warning("Ignoring tool entry at index %d with missing name.", idx)
                continue
            if name in seen_names:
                _LOGGER.warning("Ignoring duplicate tool name '%s' in config.", name)
                continue
            if not module or not function:
                _LOGGER.warning("Ignoring tool '%s': module/function is missing.", name)
                continue

            seen_names.add(name)
            tools.append(
                {
                    "name": name,
                    "module": module,
                    "function": function,
                    "description": description,
                    "enabled": enabled,
                }
            )
        return tools

    @staticmethod
    def _cache_key(name: str, module: str, function: str) -> Tuple[str, str, str]:
        return (name, module, function)

    def _load_callable(self, name: str, module: str, function: str) -> Any:
        key = self._cache_key(name, module, function)
        with self._lock:
            if key in self._callable_cache:
                return self._callable_cache[key]
            if key in self._load_errors:
                raise RuntimeError(self._load_errors[key])

        try:
            module_obj = importlib.import_module(module)
            callable_obj = getattr(module_obj, function)
            if not callable(callable_obj):
                raise TypeError(f"Configured tool target is not callable: {module}.{function}")
        except Exception as e:
            error_text = f"{type(e).__name__}: {e}"
            with self._lock:
                self._load_errors[key] = error_text
            raise

        with self._lock:
            self._callable_cache[key] = callable_obj
            self._load_errors.pop(key, None)
        return callable_obj
