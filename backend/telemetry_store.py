from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from statistics import fmean
from typing import Any, Callable, Dict, List, Mapping, Optional

MQTT_TELEMETRY_STREAM = "backend-mqtt"
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_THRESHOLD_KEYS = ("minimum", "maximum", "max_change")


def validate_fields(fields: Any) -> Dict[str, Dict[str, Any]]:
    """Validate an optional field allowlist with aliases, units and thresholds."""
    if not isinstance(fields, Mapping):
        raise ValueError("Telemetry fields must be an object keyed by field name.")
    result: Dict[str, Dict[str, Any]] = {}
    aliases: Dict[str, str] = {}
    for name, raw in fields.items():
        if not isinstance(name, str) or not _FIELD_NAME.fullmatch(name) or name == "receivedAt":
            raise ValueError(f"Invalid telemetry field name: {name!r}.")
        if not isinstance(raw, Mapping):
            raise ValueError(f"Settings for telemetry field '{name}' must be an object.")
        spec = dict(raw)
        unknown = set(spec) - {"aliases", "unit", *_THRESHOLD_KEYS}
        if unknown:
            raise ValueError(f"Unknown settings for telemetry field '{name}': {', '.join(sorted(unknown))}.")
        names = spec.get("aliases", [])
        if not isinstance(names, list) or any(not isinstance(a, str) or not _FIELD_NAME.fullmatch(a) for a in names):
            raise ValueError(f"Aliases for telemetry field '{name}' must be field-name strings.")
        if "unit" in spec and not isinstance(spec["unit"], str):
            raise ValueError(f"Unit for telemetry field '{name}' must be a string.")
        for alias in [name, *names]:
            key = alias.casefold()
            if key in aliases and aliases[key] != name:
                raise ValueError(f"Ambiguous telemetry alias: {alias}.")
            aliases[key] = name
        for key in _THRESHOLD_KEYS:
            if key not in spec:
                continue
            value = spec[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Telemetry threshold '{name}.{key}' must be a finite number.")
            if key == "max_change" and value < 0:
                raise ValueError(f"Telemetry threshold '{name}.max_change' must be nonnegative.")
        if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
            raise ValueError(f"Telemetry minimum exceeds maximum for '{name}'.")
        result[name] = spec
    return result


class TelemetryStore:
    """Thread-safe rolling buffers of flat numeric samples, keyed by source."""

    def __init__(self, max_samples: int = 500) -> None:
        self.max_samples = max(1, int(max_samples))
        self._samples: Dict[str, deque] = {}
        self._lock = threading.RLock()

    def clear(self, source_id: str) -> None:
        with self._lock:
            self._samples.pop(source_id, None)

    def add_sample(
        self, source_id: str, sample: Mapping[str, Any], fields: Optional[Mapping] = None
    ) -> Dict[str, float]:
        if not source_id:
            raise ValueError("A source id is required for telemetry.")
        if not isinstance(sample, Mapping):
            raise ValueError("Telemetry data must be a JSON object.")
        normalized = {"receivedAt": time.time()}
        # An explicit allowlist ignores unrelated metadata in the MQTT payload.
        for field in (fields or sample):
            if field == "receivedAt" or field not in sample:
                continue
            if not isinstance(field, str) or not _FIELD_NAME.fullmatch(field):
                raise ValueError(f"Invalid telemetry field name: {field!r}.")
            value = sample[field]
            try:
                if isinstance(value, bool):
                    raise ValueError()
                value = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Telemetry field '{field}' must be numeric.") from exc
            if not math.isfinite(value):
                raise ValueError(f"Telemetry field '{field}' must be finite.")
            normalized[field] = value
        if len(normalized) == 1:
            raise ValueError("Telemetry did not contain any recognized numeric fields.")
        with self._lock:
            self._samples.setdefault(source_id, deque(maxlen=self.max_samples)).append(normalized)
        return dict(normalized)

    def summarize(
        self,
        source_id: str,
        parameters: str = "ALL",
        sample_count: int = 50,
        *,
        fields: Optional[Mapping] = None,
        diagnostic_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        field_specs = validate_fields(fields or {})
        if isinstance(sample_count, bool):
            raise ValueError("sample_count must be an integer.")
        count = max(1, min(int(sample_count), self.max_samples))
        with self._lock:
            samples = list(self._samples.get(source_id, ()))[-count:]
        available = list(field_specs) or list(dict.fromkeys(
            key for sample in samples for key in sample if key != "receivedAt"
        ))
        requested = self._parse_fields(parameters, available, field_specs)
        base = {"requested_fields": requested, "sample_count": len(samples)}
        if not samples:
            return {"status": "no_data", "message": "No telemetry has been received from this source.", **base}
        series = {field: [sample[field] for sample in samples if field in sample] for field in requested}
        series = {field: values for field, values in series.items() if values}
        if not series:
            return {"status": "no_data", "message": "The requested telemetry fields have not been received.", **base}
        statistics = {
            field: {"minimum": min(values), "maximum": max(values), "average": fmean(values),
                    "latest": values[-1], "change": values[-1] - values[0]}
            for field, values in series.items()
        }
        configured = diagnostic_fn is not None or any(
            key in field_specs.get(field, {}) for field in series for key in _THRESHOLD_KEYS
        )
        findings = diagnostic_fn(series) if diagnostic_fn else self._diagnostics(series, field_specs)
        return {
            "status": "ok", **base,
            "latest": {field: values[-1] for field, values in series.items()},
            "statistics": statistics,
            "units": {field: field_specs[field]["unit"] for field in series if field_specs.get(field, {}).get("unit")},
            "diagnostics_status": "evaluated" if configured else "not_configured",
            "diagnostics": findings,
        }

    @staticmethod
    def _parse_fields(parameters: str, available: List[str], specs: Mapping) -> List[str]:
        raw = str(parameters or "ALL").strip()
        if not raw or raw.upper() == "ALL":
            return available
        aliases = {name.casefold(): name for name in available}
        for name, spec in specs.items():
            aliases.update({alias.casefold(): name for alias in spec.get("aliases", [])})
        requested = []
        for token in re.split(r"[\s,;]+", raw):
            if not token:
                continue
            name = aliases.get(token.casefold())
            if name is None:
                if not available and _FIELD_NAME.fullmatch(token):
                    name = token
                else:
                    raise ValueError(f"Unknown telemetry parameter: {token}. Available fields: {', '.join(available)}.")
            if name not in requested:
                requested.append(name)
        return requested or available

    @staticmethod
    def _diagnostics(series: Mapping[str, List[float]], specs: Mapping) -> List[Dict[str, str]]:
        findings = []
        for field, values in series.items():
            spec = specs.get(field, {})
            checks = {
                "minimum": min(values),
                "maximum": max(values),
                "max_change": max((abs(b - a) for a, b in zip(values, values[1:])), default=0),
            }
            unit = f" {spec['unit']}" if spec.get("unit") else ""
            for key, value in checks.items():
                limit = spec.get(key)
                if limit is None or not (value < limit if key == "minimum" else value > limit):
                    continue
                findings.append({
                    "code": f"{field}_{key}", "severity": "warning",
                    "message": f"{field}: {key} threshold {limit:g}{unit} exceeded (observed {value:g}{unit}).",
                })
        return findings


TELEMETRY_STORE = TelemetryStore()
