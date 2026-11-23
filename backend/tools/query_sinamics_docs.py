import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from document_retrieval import (
    _CHUNK_OVERLAP_CHARS,
    _CHUNK_SIZE_CHARS,
    _INDEX_VERSION,
    _build_index,
    _cache_incompatibility_reasons,
    _is_broad_query,
    _is_cache_compatible,
    _is_cache_structurally_usable,
    _page_citation,
    _page_labels_for,
    _search_index,
    _truncate_text,
)

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parent
_SUPPORT_DIR = _TOOLS_DIR / "query_sinamics_docs"
_CACHE_DIR = _SUPPORT_DIR / "cache"
_DEFAULT_DOC_NAME = "Siemens SINAMICS V20_op_instr_0522_en-US.pdf"
_DEFAULT_DOC_PATH = _SUPPORT_DIR / _DEFAULT_DOC_NAME
_INDEX_PATH = _CACHE_DIR / "sinamics_docling_text_index.json"


_MAX_DUPLICATE_QUERY_CACHE = 64
_DUPLICATE_QUERY_CACHE_LOCAL = threading.local()


def _duplicate_query_cache() -> "OrderedDict[str, str]":
    cache = getattr(_DUPLICATE_QUERY_CACHE_LOCAL, "cache", None)
    if cache is None:
        cache = OrderedDict()
        _DUPLICATE_QUERY_CACHE_LOCAL.cache = cache
    return cache


def reset_runtime_state() -> None:
    """Clear per-thread runtime state between eval trials."""
    _duplicate_query_cache().clear()


def query_sinamics_docs(
    question: str,
    top_k: int = 5,
    max_chars_per_snippet: int = 700,
) -> str:
    """Query the Siemens SINAMICS V20 PDF with Docling-backed text retrieval.

    Args:
        question: Natural-language query to search in the manual.
        top_k: Number of snippets to return.
        max_chars_per_snippet: Maximum snippet size for each match.

    Returns:
        A plain-text retrieval report with snippets and page citations.
    """
    question = (question or "").strip()
    if not question:
        return "Invalid request: 'question' cannot be empty."

    if not isinstance(top_k, int):
        return "Invalid request: 'top_k' must be an integer."
    if top_k < 1:
        return "Invalid request: 'top_k' must be >= 1."
    requested_top_k = top_k
    top_k = min(top_k, 20)

    if not isinstance(max_chars_per_snippet, int):
        return "Invalid request: 'max_chars_per_snippet' must be an integer."
    if max_chars_per_snippet < 200:
        return "Invalid request: 'max_chars_per_snippet' must be >= 200."
    requested_max_chars_per_snippet = max_chars_per_snippet
    max_chars_per_snippet = min(max_chars_per_snippet, 2500)

    if not _DEFAULT_DOC_PATH.exists():
        return (
            "SINAMICS manual not found at expected location: "
            f"{_DEFAULT_DOC_PATH}. Place the PDF there or update this tool path."
        )

    try:
        page_labels = _page_labels_for(_DEFAULT_DOC_PATH)
    except Exception as exc:
        return f"Could not read PDF page labels: {exc}"

    override_notes: List[str] = []
    if _is_broad_query(question):
        if top_k < 12:
            top_k = 12
            override_notes.append("broad-query override applied: top_k=12")
        if max_chars_per_snippet < 1200:
            max_chars_per_snippet = 1200
            override_notes.append("broad-query override applied: max_chars_per_snippet=1200")

    source_mtime = int(_DEFAULT_DOC_PATH.stat().st_mtime)
    duplicate_cache_key = _build_duplicate_cache_key(
        question=question,
        top_k=top_k,
        max_chars_per_snippet=max_chars_per_snippet,
        source_mtime=source_mtime,
    )
    cached_report = _get_duplicate_cached_result(duplicate_cache_key)
    if cached_report is not None:
        return (
            "Duplicate query detected: this exact request already ran in this runtime. "
            "Returning the cached retrieval report; this evidence is likely already in prior tool results.\n"
            "---\n"
            f"{cached_report}"
        )

    index_data, index_status = _load_or_build_index(_DEFAULT_DOC_PATH)
    if isinstance(index_data, str):
        return index_data

    results = _search_index(index_data, question, top_k)
    if not results:
        report = (
            f"No relevant snippets found for question: {question}\n"
            "Try a more specific query."
        )
        _store_duplicate_cached_result(duplicate_cache_key, report)
        return report

    lines: List[str] = [
        f'Retrieval results for "{question}" from {_DEFAULT_DOC_NAME}:',
        f"Index status: {index_status}",
        (
            "Retrieval params: "
            f"requested_top_k={requested_top_k}, applied_top_k={top_k}, "
            f"requested_max_chars_per_snippet={requested_max_chars_per_snippet}, "
            f"applied_max_chars_per_snippet={max_chars_per_snippet}"
        ),
    ]
    if index_status.startswith("reused stale cache"):
        lines.append(
            "Warning: stale index cache was reused because reindexing failed. "
            "Current indexing-code changes are not fully active until a successful rebuild."
        )
    if override_notes:
        lines.append("Retrieval overrides: " + "; ".join(override_notes))
    lines.extend(
        [
        "Cite page= labels (printed page numbers), not pdf_page= positions. "
        "Pass the page label unchanged to change_page; do not calculate an offset.",
        "---",
        ]
    )

    for rank, item in enumerate(results, start=1):
        page = item.get("page")
        score = item.get("score", 0.0)
        snippet = _truncate_text(item.get("text", ""), max_chars_per_snippet)
        lines.append(
            f"[{rank}] {_page_citation(page, page_labels)} score={score:.3f} "
            f"chunk_id={item.get('id', 'n/a')}"
        )
        lines.append(snippet)
        lines.append("---")

    report = "\n".join(lines).rstrip("-\n")
    _store_duplicate_cached_result(duplicate_cache_key, report)
    return report


def _load_or_build_index(
    pdf_path: Path,
) -> Tuple[Dict[str, Any] | str, str]:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_path.resolve()
    source_mtime = int(pdf_path.stat().st_mtime)
    cached_any: Optional[Dict[str, Any]] = None
    cache_mismatch_reason = ""

    if _INDEX_PATH.exists():
        try:
            with _INDEX_PATH.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            if _is_cache_structurally_usable(cached):
                cached_any = cached
            mismatch_reasons = _cache_incompatibility_reasons(cached, pdf_path, source_mtime)
            if not mismatch_reasons:
                return cached, "reused cached index"
            cache_mismatch_reason = "; ".join(mismatch_reasons)
        except Exception:
            # Fallback to rebuilding the index
            pass

    built = _build_index(pdf_path, source_mtime)
    if isinstance(built, str):
        if cached_any is not None:
            reason = " ".join(str(built).split())
            status = f"reused stale cache (rebuild failed: {reason})"
            if cache_mismatch_reason:
                status += f"; cache mismatch: {cache_mismatch_reason}"
            return cached_any, status
        return built, "index build failed"

    try:
        with _INDEX_PATH.open("w", encoding="utf-8") as f:
            json.dump(built, f, ensure_ascii=False)
    except Exception as e:
        return f"Index built but failed to save cache at {_INDEX_PATH}: {e}", "cache save failed"

    status = "rebuilt index"
    if cache_mismatch_reason:
        status += f" (cache mismatch: {cache_mismatch_reason})"
    return built, status


def _build_duplicate_cache_key(
    question: str,
    top_k: int,
    max_chars_per_snippet: int,
    source_mtime: int,
) -> str:
    normalized_question = " ".join(question.lower().split())
    return (
        f"v{_INDEX_VERSION}|mtime={source_mtime}|"
        f"top_k={top_k}|max_chars={max_chars_per_snippet}|q={normalized_question}"
    )


def _get_duplicate_cached_result(cache_key: str) -> Optional[str]:
    duplicate_query_cache = _duplicate_query_cache()
    value = duplicate_query_cache.get(cache_key)
    if value is None:
        return None
    duplicate_query_cache.move_to_end(cache_key)
    return value


def _store_duplicate_cached_result(cache_key: str, report: str) -> None:
    duplicate_query_cache = _duplicate_query_cache()
    duplicate_query_cache[cache_key] = report
    duplicate_query_cache.move_to_end(cache_key)
    while len(duplicate_query_cache) > _MAX_DUPLICATE_QUERY_CACHE:
        duplicate_query_cache.popitem(last=False)
