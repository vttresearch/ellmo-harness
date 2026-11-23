
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class ConversationManager:
    """Persistence layer for chat conversations.

    Conversations are stored as JSON files in a `conversations/` folder
    next to this module. Each conversation has the structure:

    {
      "version": 1,
      "id": "...",
      "title": "...",
      "created_at": "...",
      "updated_at": "...",
      "host": "...",
      "model": "...",
      "messages": [...],
      "chat_transcript": "...",
      "log_text": "..."
    }
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent
        self.base_dir = Path(base_dir)
        self.storage_dir = self.base_dir / "conversations"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    # === Public API ===

    def start_new_conversation(
        self,
        first_user_message: Optional[str],
        host: Optional[str],
        model: Optional[str],
    ) -> str:
        """Create a new conversation file and return its id."""
        conv_id = self._generate_id(host)
        now = self._now_iso()
        title = self._make_title(first_user_message)

        record: Dict[str, Any] = {
            "version": 1,
            "id": conv_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "host": host or "",
            "model": model or "",
            "messages": [],
            "chat_transcript": "",
            "log_text": "",
        }

        self._write_json(conv_id, record)
        return conv_id

    def save_snapshot(
        self,
        conv_id: str,
        messages: List[Any],
        chat_transcript: str,
        log_text: str,
        host: Optional[str],
        model: Optional[str],
    ) -> None:
        """Persist a snapshot of the current conversation state."""
        if not conv_id:
            return

        path = self._conversation_path(conv_id)
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    record = json.load(f)
            except Exception:
                record = {}
        else:
            record = {}

        if not isinstance(record, dict):
            record = {}

        # Base metadata
        record.setdefault("version", 1)
        record.setdefault("id", conv_id)
        record.setdefault("created_at", self._now_iso())
        record["title"] = record.get("title") or "Conversation"
        record["updated_at"] = self._now_iso()
        record["host"] = host or ""
        record["model"] = model or ""

        # Payload
        record["messages"] = self._make_json_safe(messages)
        record["chat_transcript"] = chat_transcript or ""
        record["log_text"] = log_text or ""

        self._write_json(conv_id, record)

    def list_conversations(self) -> List[Dict[str, Any]]:
        """Return a list of conversation metadata, newest first."""
        conversations: List[Dict[str, Any]] = []

        for path in self.storage_dir.glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            if not isinstance(data, dict):
                continue

            conv = {
                "id": data.get("id") or path.stem,
                "title": data.get("title") or "Conversation",
                "created_at": data.get("created_at") or "",
                "updated_at": data.get("updated_at") or "",
                "host": data.get("host") or "",
                "model": data.get("model") or "",
            }
            conversations.append(conv)

        def _sort_key(item: Dict[str, Any]) -> str:
            # Prefer updated_at, fall back to created_at, for sorting
            return item.get("updated_at") or item.get("created_at") or ""

        conversations.sort(key=_sort_key, reverse=True)
        return conversations

    def load_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """Load the full conversation payload for the given id."""
        path = self._conversation_path(conv_id)
        if not path.exists():
            return None

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None

        if not isinstance(data, dict):
            return None

        # Ensure required fields exist
        data.setdefault("version", 1)
        data.setdefault("id", conv_id)
        data.setdefault("title", "Conversation")
        data.setdefault("created_at", self._now_iso())
        data.setdefault("updated_at", self._now_iso())
        data.setdefault("host", "")
        data.setdefault("model", "")
        data.setdefault("messages", [])
        data.setdefault("chat_transcript", "")
        data.setdefault("log_text", "")

        return data

    # === Internal helpers ===

    def _conversation_path(self, conv_id: str) -> Path:
        return self.storage_dir / f"{conv_id}.json"

    def _now_iso(self) -> str:
        # Always use UTC with a trailing Z
        return datetime.utcnow().isoformat() + "Z"

    def _slugify(self, value: Optional[str]) -> str:
        if not value:
            return "default"
        value = value.lower().strip()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        value = value.strip("-")
        return value or "conv"

    def _generate_id(self, host: Optional[str]) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        host_slug = self._slugify(host)
        rand = uuid.uuid4().hex[:8]
        return f"{ts}-{host_slug}-{rand}"

    def _make_title(self, first_user_message: Optional[str]) -> str:
        if not first_user_message:
            return "Conversation"
        line = first_user_message.strip().splitlines()[0]
        if len(line) > 80:
            line = line[:80]
        return line or "Conversation"

    def _write_json(self, conv_id: str, record: Dict[str, Any]) -> None:
        """Atomically write the JSON file, ensuring ASCII-safe output."""
        path = self._conversation_path(conv_id)
        tmp_path = path.with_suffix(".tmp")

        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=True, indent=2)
            tmp_path.replace(path)
        except Exception:
            # Persistence errors are non-fatal for the application.
            _LOGGER.exception("Failed to save conversation %s", conv_id)

    # === JSON-safe conversion ===

    def _make_json_safe(self, obj: Any) -> Any:
        """Recursively convert an object to something JSON-serializable.

        - Primitive types are returned as-is.
        - Dicts, lists, tuples, and sets are traversed recursively.
        - Dataclasses are converted via asdict().
        - Objects with __dict__ are converted using their __dict__.
        - Fallback is str(obj).
        """
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj

        if isinstance(obj, dict):
            return {
                str(self._make_json_safe(k)): self._make_json_safe(v)
                for k, v in obj.items()
            }

        if isinstance(obj, (list, tuple, set)):
            return [self._make_json_safe(v) for v in obj]

        if is_dataclass(obj):
            try:
                return self._make_json_safe(asdict(obj))
            except Exception:
                return str(obj)

        if hasattr(obj, "model_dump"):
            # Some libraries (e.g. Pydantic) support model_dump()
            try:
                return self._make_json_safe(obj.model_dump())
            except Exception:
                return str(obj)

        if hasattr(obj, "__dict__"):
            try:
                return self._make_json_safe(vars(obj))
            except Exception:
                return str(obj)

        try:
            return str(obj)
        except Exception:
            return repr(obj)
