from __future__ import annotations

from threading import RLock
from typing import Any, Mapping, Protocol


class VisionAnalyzer(Protocol):
    """Provider-neutral interface for a conversation about one captured image."""

    def analyze(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        metadata: Mapping[str, Any],
        question: str,
    ) -> str:
        """Start a new conversation and return its first answer."""

    def continue_conversation(self, *, question: str) -> str:
        """Answer a follow-up using the current image and conversation history."""


class StubVisionAnalyzer:
    """Confirm image transport without claiming to inspect image contents."""

    def __init__(self) -> None:
        self._has_image = False

    def analyze(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        metadata: Mapping[str, Any],
        question: str,
    ) -> str:
        self._has_image = True
        width = metadata.get("width")
        height = metadata.get("height")
        source = str(metadata.get("source") or "unknown camera")
        dimensions = (
            f"{width}x{height}"
            if isinstance(width, int) and isinstance(height, int)
            else "unknown dimensions"
        )
        return (
            "Vision analyzer stub received a valid "
            f"{mime_type} image ({dimensions}, source: {source}, "
            f"{len(image_bytes)} decoded bytes). No VLM is configured yet, so the "
            "image contents were not analyzed and no visual claims can be made."
        )

    def continue_conversation(self, *, question: str) -> str:
        if not self._has_image:
            raise RuntimeError("No visual conversation has been started.")
        return (
            "The vision analyzer stub still has the previously captured image, but no VLM "
            "is configured, so it cannot answer visual follow-up questions."
        )


class OllamaVisionAnalyzer:
    """Hold one in-memory multimodal conversation served by Ollama."""

    def __init__(
        self,
        *,
        host: str,
        model: str,
        think: bool = False,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.host = str(host or "").rstrip("/")
        self.model = str(model or "").strip()
        self.think = bool(think)
        self.timeout_seconds = float(timeout_seconds)
        self._messages: list[dict[str, Any]] = []
        self._lock = RLock()
        if not self.host:
            raise ValueError("Ollama vision host must not be empty.")
        if not self.model:
            raise ValueError("Ollama vision model must not be empty.")
        if self.timeout_seconds <= 0:
            raise ValueError("Ollama vision timeout must be greater than zero.")

    def analyze(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        metadata: Mapping[str, Any],
        question: str,
    ) -> str:
        visual_question = str(question or "").strip()
        if not visual_question:
            visual_question = "Describe the relevant objects and details visible in this image."

        prompt = (
            "Act as a visual analysis tool for another assistant. Inspect the attached "
            "camera image and answer the question directly using only visible evidence. "
            "Be concise but include the details needed to answer accurately. If something "
            "is unreadable, outside the frame, or uncertain, say so rather than guessing.\n\n"
            f"Question: {visual_question}"
        )
        user_message = {
            "role": "user",
            "content": prompt,
            "images": [image_bytes],
        }

        with self._lock:
            # Calling analyze explicitly starts over, even if the provider request fails.
            self._messages = []
            answer = self._chat([user_message])
            self._messages = [
                user_message,
                {"role": "assistant", "content": answer},
            ]
        return answer

    def continue_conversation(self, *, question: str) -> str:
        visual_question = str(question or "").strip()
        if not visual_question:
            raise ValueError("A follow-up question is required.")

        follow_up = {
            "role": "user",
            "content": f"Follow-up question about the same image: {visual_question}",
        }
        with self._lock:
            if not self._messages:
                raise RuntimeError("No visual conversation has been started.")

            messages = [*self._messages, follow_up]
            answer = self._chat(messages)
            self._messages = [
                *messages,
                {"role": "assistant", "content": answer},
            ]
        return answer

    def _chat(self, messages: list[dict[str, Any]]) -> str:
        try:
            import ollama
        except Exception as exc:
            raise RuntimeError(f"The Ollama Python client is unavailable: {exc}") from exc

        client = ollama.Client(host=self.host, timeout=self.timeout_seconds)
        response = client.chat(
            model=self.model,
            messages=messages,
            think=self.think,
            stream=False,
        )

        message = getattr(response, "message", None)
        if message is None and isinstance(response, dict):
            message = response.get("message")

        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)

        answer = str(content or "").strip()
        if not answer:
            raise RuntimeError(
                f"Ollama model '{self.model}' returned an empty visual analysis."
            )
        return answer
