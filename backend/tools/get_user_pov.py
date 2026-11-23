import base64
import binascii
import os
from threading import RLock
from typing import Any, Dict

from vision_analyzer import OllamaVisionAnalyzer, VisionAnalyzer


_UNITY_RESPONSE_TIMEOUT_SECONDS = 30.0
_MAX_BASE64_CHARACTERS = 16 * 1024 * 1024
_OLLAMA_VISION_HOST = os.getenv("ELLMO_VISION_HOST", "http://127.0.0.1:11434")
_OLLAMA_VISION_MODEL = os.getenv("ELLMO_VISION_MODEL", "qwen3.8:27b-mlx")
_OLLAMA_VISION_TIMEOUT_SECONDS = 120.0
_VISION_CONVERSATIONS: Dict[str, VisionAnalyzer] = {}
_VISION_CONVERSATIONS_LOCK = RLock()


def _build_vision_analyzer() -> VisionAnalyzer:
    return OllamaVisionAnalyzer(
        host=_OLLAMA_VISION_HOST,
        model=_OLLAMA_VISION_MODEL,
        think=False,
        timeout_seconds=_OLLAMA_VISION_TIMEOUT_SECONDS,
    )


def clear_user_pov_conversation(conversation_id: str) -> None:
    """Discard the in-memory VLM conversation for a Unity session."""
    session_id = str(conversation_id or "").strip()
    if not session_id:
        return
    with _VISION_CONVERSATIONS_LOCK:
        _VISION_CONVERSATIONS.pop(session_id, None)


def _get_vision_conversation(conversation_id: str) -> VisionAnalyzer | None:
    with _VISION_CONVERSATIONS_LOCK:
        return _VISION_CONVERSATIONS.get(conversation_id)


def _store_vision_conversation(
    conversation_id: str,
    analyzer: VisionAnalyzer,
) -> None:
    with _VISION_CONVERSATIONS_LOCK:
        _VISION_CONVERSATIONS[conversation_id] = analyzer


def get_user_pov(question: str = "", continue_conversation: bool = False) -> str:
    """Start or continue a conversation about the user's camera view.

    Call this tool only when the answer needs visual evidence that is not already
    available in a previous tool result. Do not call it for general knowledge or
    when the previous visual answer already contains everything needed to reply.

    Use continue_conversation=False whenever the user asks about the current or a
    potentially changed view. Cues including "now", "right now", "currently",
    "again", "look again", "this", "over here", movement, or a viewpoint change
    require a new capture. Never describe an old capture as the current view.

    Use continue_conversation=True only when the user clearly asks for additional
    analysis of the exact same captured image, such as identifying another detail
    in it or clarifying the VLM's prior answer. A message being a conversational
    follow-up does not by itself mean that the old image should be reused. If it is
    unclear whether the scene changed, capture a new image. The image never includes
    Unity UI or virtual overlays.

    Args:
        question: The visual question for the VLM. Make this self-contained when
            starting a new conversation because the previous VLM history is erased.
        continue_conversation: False for fresh visual evidence, especially any
            request about what is visible now; true only for more analysis of the
            exact same frozen image. This controls image reuse, not whether the user
            message is grammatically a follow-up.
    """
    from api_ws import get_active_conversation_id, request_unity_tool_threadsafe

    conversation_id = str(get_active_conversation_id() or "").strip()
    if not conversation_id:
        return "Could not use the user's camera view: no active Unity session is available."

    visual_question = str(question or "").strip()
    if continue_conversation:
        if not visual_question:
            return (
                "Could not continue the visual conversation: a follow-up question is required."
            )

        analyzer = _get_vision_conversation(conversation_id)
        if analyzer is None:
            return (
                "No visual conversation is available to continue in this session. "
                "Call get_user_pov with continue_conversation=false to capture a new image."
            )

        try:
            return analyzer.continue_conversation(question=visual_question)
        except Exception as exc:
            return f"Could not continue the visual conversation: {exc}"

    # Reset immediately: a failed replacement must not make the old image available again.
    clear_user_pov_conversation(conversation_id)

    try:
        response = request_unity_tool_threadsafe(
            "get_user_pov",
            timeout_seconds=_UNITY_RESPONSE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return f"Could not capture the user's camera view: {exc}"

    succeeded = response.get("ok")
    if succeeded is not True:
        error = response.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "camera_error")
            message = str(error.get("message") or "Unity could not capture an image.")
            return f"Could not capture the user's camera view ({code}): {message}"
        if succeeded is not False:
            return "Could not capture the user's camera view: Unity returned an invalid success flag."
        return "Could not capture the user's camera view: Unity returned an unspecified error."

    image = response.get("image")
    if not isinstance(image, dict):
        return "Could not analyze the user's camera view: Unity returned no image object."

    encoded = image.get("base64")
    if not isinstance(encoded, str) or not encoded:
        return "Could not analyze the user's camera view: Unity returned no base64 image data."
    if len(encoded) > _MAX_BASE64_CHARACTERS:
        return "Could not analyze the user's camera view: the encoded image is too large."

    mime_type = str(image.get("mime_type") or "").strip().lower()
    if not mime_type.startswith("image/"):
        return "Could not analyze the user's camera view: Unity returned an invalid image MIME type."

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return "Could not analyze the user's camera view: Unity returned invalid base64 image data."
    if not image_bytes:
        return "Could not analyze the user's camera view: Unity returned an empty image."

    metadata: Dict[str, Any] = {
        key: value
        for key, value in image.items()
        if key != "base64"
    }
    try:
        analyzer = _build_vision_analyzer()
        answer = analyzer.analyze(
            image_bytes=image_bytes,
            mime_type=mime_type,
            metadata=metadata,
            question=visual_question,
        )
    except Exception as exc:
        return f"Could not analyze the user's camera view: {exc}"

    # Avoid retaining a conversation after its owning WebSocket disconnected mid-analysis.
    if str(get_active_conversation_id() or "").strip() == conversation_id:
        _store_vision_conversation(conversation_id, analyzer)
    return answer
