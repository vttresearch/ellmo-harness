import os
from functools import lru_cache
from pathlib import Path

import config
from local_document import LocalDocumentSearch

_SUPPORT_DIR = Path(__file__).resolve().parent / "query_local_document"


@lru_cache(maxsize=8)
def _document_search(document_path: str) -> LocalDocumentSearch:
    return LocalDocumentSearch(_SUPPORT_DIR, Path(document_path) if document_path else None)


def _configured_path() -> str:
    override = os.getenv("ELLMO_DOCUMENT_PATH")
    if override:
        return str(Path(override).expanduser().resolve())
    settings = config.load_config().get("local_document", {})
    if not isinstance(settings, dict) or not isinstance(settings.get("path", ""), str):
        raise ValueError("local_document.path must be a local file path string.")
    value = settings.get("path", "").strip()
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config._config_path().parent / path
    return str(path.resolve())


def reset_runtime_state() -> None:
    _document_search(_configured_path()).reset_runtime_state()


def query_local_document(question: str, top_k: int = 5, max_chars_per_snippet: int = 700) -> str:
    """Search one locally configured PDF, Markdown or text document with citations.

    Args:
        question: Natural-language query to search in the document.
        top_k: Number of matching snippets to return, from 1 to 20.
        max_chars_per_snippet: Maximum characters per snippet, from 200 to 2500.
    """
    return _document_search(_configured_path()).query(
        question, top_k, max_chars_per_snippet
    )
