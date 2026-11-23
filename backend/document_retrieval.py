"""Shared local document indexing, ranking and printed-page citation helpers."""

import math
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


_SECTION_REF_RE = re.compile(r"\b\d+(?:\.\d+){1,3}\b")


_PARAM_REF_RE = re.compile(r"\b([prPR])\s*([0-9]{3,4})(?:\s*\[[^\]]+\])?\b")


_PARAM_WORD_RE = re.compile(r"^[pr]\d{3,4}$")


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "how", "if", "in", "into", "is", "it", "its", "of",
    "on", "or", "that", "the", "their", "this", "to", "was", "were",
    "what", "when", "where", "which", "who", "why", "with",
}


_INDEX_VERSION = 2


_CHUNK_SIZE_CHARS = 1200


_CHUNK_OVERLAP_CHARS = 200


_BROAD_QUERY_HINTS = (
    "table of contents",
    "contents",
    "chapter",
    "chapters",
    "overview",
    "section list",
    "list of sections",
)


@lru_cache(maxsize=4)
def _pdf_page_labels(path: str, mtime_ns: int, size: int) -> Tuple[str, ...]:
    """Read the document's printed labels without changing the physical-page index."""
    import pypdfium2

    with pypdfium2.PdfDocument(path) as document:
        return tuple(
            document.get_page_label(index) or str(index + 1)
            for index in range(len(document))
        )


def _page_labels_for(pdf_path: Path) -> Tuple[str, ...]:
    stat = pdf_path.stat()
    return _pdf_page_labels(str(pdf_path.resolve()), stat.st_mtime_ns, stat.st_size)


def _page_citation(page: Any, labels: Tuple[str, ...]) -> str:
    if isinstance(page, int) and 1 <= page <= len(labels):
        return f"page={labels[page - 1]} pdf_page={page}"
    return "page=unknown"


def _is_cache_compatible(index_data: Any, pdf_path: Path, source_mtime: int) -> bool:
    return not _cache_incompatibility_reasons(index_data, pdf_path, source_mtime)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _cache_incompatibility_reasons(index_data: Any, pdf_path: Path, source_mtime: int) -> List[str]:
    reasons: List[str] = []
    if not isinstance(index_data, dict):
        return ["cache payload is not a JSON object"]

    version = _safe_int(index_data.get("version", -1), -1)
    if version != _INDEX_VERSION:
        reasons.append(f"version={version} expected={_INDEX_VERSION}")

    chunk_size = _safe_int(index_data.get("chunk_size_chars", -1), -1)
    if chunk_size != _CHUNK_SIZE_CHARS:
        reasons.append(f"chunk_size_chars={chunk_size} expected={_CHUNK_SIZE_CHARS}")

    chunk_overlap = _safe_int(index_data.get("chunk_overlap_chars", -1), -1)
    if chunk_overlap != _CHUNK_OVERLAP_CHARS:
        reasons.append(f"chunk_overlap_chars={chunk_overlap} expected={_CHUNK_OVERLAP_CHARS}")

    cache_source_path = str(index_data.get("source_path", ""))
    if cache_source_path != str(pdf_path):
        reasons.append(f"source_path mismatch: cache={cache_source_path} expected={pdf_path}")

    cache_source_mtime = _safe_int(index_data.get("source_mtime", -1), -1)
    if cache_source_mtime != source_mtime:
        reasons.append(f"source_mtime={cache_source_mtime} expected={source_mtime}")

    chunks = index_data.get("chunks")
    if not isinstance(chunks, list):
        reasons.append("chunks missing or not a list")
    elif not chunks:
        reasons.append("chunks list is empty")

    return reasons


def _is_cache_structurally_usable(index_data: Any) -> bool:
    if not isinstance(index_data, dict):
        return False
    chunks = index_data.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return False
    return True


def _build_index(pdf_path: Path, source_mtime: int) -> Dict[str, Any] | str:
    try:
        from docling.document_converter import DocumentConverter
    except Exception:
        return (
            "Docling is not installed. The user needs to install it with `pip install docling`."
        )

    try:
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        document = result.document
    except Exception as e:
        return f"Docling failed to parse PDF '{pdf_path}': {e}"

    page_texts = _extract_page_texts(document)
    if not page_texts:
        return f"Docling did not extract usable text from '{pdf_path}'."

    chunks: List[Dict[str, Any]] = []
    for page_number, page_text in page_texts:
        cleaned = _normalize_text_block(page_text)
        if not cleaned:
            continue
        for chunk_idx, chunk_text in enumerate(_split_into_chunks(cleaned)):
            terms = _tokenize(chunk_text)
            if not terms:
                continue

            term_freq: Dict[str, int] = {}
            for term in terms:
                term_freq[term] = term_freq.get(term, 0) + 1

            chunk_id = f"p{page_number if page_number is not None else 'na'}_c{chunk_idx}"
            chunks.append(
                {
                    "id": chunk_id,
                    "page": page_number,
                    "text": chunk_text,
                    "term_freq": term_freq,
                    "len": len(terms),
                }
            )

    if not chunks:
        return f"Docling extracted content, but chunking produced no searchable text for '{pdf_path}'."

    df: Dict[str, int] = {}
    total_len = 0
    for chunk in chunks:
        total_len += int(chunk["len"])
        for term in chunk["term_freq"].keys():
            df[term] = df.get(term, 0) + 1

    avg_chunk_len = total_len / len(chunks)
    return {
        "version": _INDEX_VERSION,
        "created_at": int(time.time()),
        "source_path": str(pdf_path),
        "source_mtime": source_mtime,
        "chunk_size_chars": _CHUNK_SIZE_CHARS,
        "chunk_overlap_chars": _CHUNK_OVERLAP_CHARS,
        "avg_chunk_len": avg_chunk_len,
        "doc_count": len(chunks),
        "df": df,
        "chunks": chunks,
    }


def _extract_page_texts(document: Any) -> List[Tuple[Optional[int], str]]:
    num_pages = _resolve_num_pages(document)
    pages = _export_pages(document, num_pages, one_based=True)
    if pages:
        return pages

    pages = _export_pages(document, num_pages, one_based=False)
    if pages:
        # Normalize zero-based page indexes to one-based for citations.
        return [(page + 1 if page is not None else None, text) for page, text in pages]

    # Fallback: full-document markdown if page-level export is unavailable.
    try:
        full_markdown = document.export_to_markdown()
    except Exception:
        full_markdown = ""

    full_markdown = _normalize_text_block(full_markdown)
    if full_markdown:
        return [(None, full_markdown)]

    return []


def _resolve_num_pages(document: Any) -> Optional[int]:
    value = getattr(document, "num_pages", None)
    if callable(value):
        try:
            value = value()
        except Exception:
            value = None
    if isinstance(value, int) and value > 0:
        return value

    pages_attr = getattr(document, "pages", None)
    try:
        pages_len = len(pages_attr)
    except Exception:
        pages_len = 0
    if pages_len > 0:
        return pages_len

    return None


def _export_pages(document: Any, num_pages: Optional[int], one_based: bool) -> List[Tuple[Optional[int], str]]:
    if not isinstance(num_pages, int) or num_pages <= 0:
        return []

    output: List[Tuple[Optional[int], str]] = []
    start = 1 if one_based else 0
    end = num_pages + 1 if one_based else num_pages

    for page_idx in range(start, end):
        try:
            markdown = document.export_to_markdown(page_no=page_idx)
        except Exception:
            markdown = ""
        normalized = _normalize_text_block(markdown)
        if normalized:
            output.append((page_idx, normalized))

    return output


def _split_into_chunks(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= _CHUNK_SIZE_CHARS:
        return [text]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: List[str] = []
    current = ""
    step = max(1, _CHUNK_SIZE_CHARS - _CHUNK_OVERLAP_CHARS)

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= _CHUNK_SIZE_CHARS:
            current = candidate
            continue

        if current:
            chunks.append(current.strip())
            current = ""

        if len(paragraph) <= _CHUNK_SIZE_CHARS:
            current = paragraph
            continue

        for start in range(0, len(paragraph), step):
            piece = paragraph[start:start + _CHUNK_SIZE_CHARS].strip()
            if piece:
                chunks.append(piece)

    if current:
        chunks.append(current.strip())

    return chunks


def _search_index(index_data: Dict[str, Any], question: str, top_k: int) -> List[Dict[str, Any]]:
    chunks = index_data.get("chunks") or []
    df = index_data.get("df") or {}
    if not chunks:
        return []

    query_terms = _query_terms(question)
    if not query_terms:
        query_terms = _tokenize(question)
    if not query_terms:
        return []

    query_terms = _expand_query_terms_for_available_index(query_terms, df)
    unique_terms = list(dict.fromkeys(query_terms))
    avg_chunk_len = float(index_data.get("avg_chunk_len") or 1.0)
    total_docs = max(1, int(index_data.get("doc_count") or len(chunks)))

    k1 = 1.5
    b = 0.75
    lower_question = question.lower()
    section_literals = [m.group(0) for m in _SECTION_REF_RE.finditer(lower_question)]
    param_literals = [f"{m.group(1).lower()}{m.group(2)}" for m in _PARAM_REF_RE.finditer(lower_question)]
    semantic_terms = [
        t for t in unique_terms
        if (not t.startswith("sec_")) and (not t.startswith("param_")) and (not t.isdigit())
    ]
    section_lookup_mode = bool(section_literals) and not semantic_terms
    param_lookup_mode = bool(param_literals) and not semantic_terms

    scored: List[Tuple[float, Dict[str, Any]]] = []
    structured_terms = [
        t for t in unique_terms
        if (t.startswith("sec_") or t.startswith("param_")) and (t in df)
    ]

    for chunk in chunks:
        tf = chunk.get("term_freq") or {}
        chunk_len = max(1, int(chunk.get("len") or 1))

        score = 0.0
        matched_terms = 0
        for term in unique_terms:
            f = float(tf.get(term, 0))
            if f <= 0:
                continue
            matched_terms += 1
            docs_with_term = float(df.get(term, 0))
            idf = math.log(1.0 + ((total_docs - docs_with_term + 0.5) / (docs_with_term + 0.5)))
            denom = f + (k1 * (1.0 - b + b * (chunk_len / avg_chunk_len)))
            score += idf * ((f * (k1 + 1.0)) / denom)

        if matched_terms == 0:
            continue

        # Small boosts for coverage and exact phrase containment.
        score += 0.2 * (matched_terms / len(unique_terms))
        if structured_terms:
            structured_hits = sum(1 for term in structured_terms if tf.get(term, 0) > 0)
            if structured_hits > 0:
                score += 1.2 * structured_hits
            else:
                score *= 0.6
        text = str(chunk.get("text", ""))
        lowered_text = text.lower()
        if section_lookup_mode and not any(lit in lowered_text for lit in section_literals):
            continue
        if param_lookup_mode and not any(lit in lowered_text for lit in param_literals):
            continue
        if section_literals:
            literal_hits = sum(1 for lit in section_literals if lit in lowered_text)
            if literal_hits > 0:
                score += 2.0 * literal_hits
            else:
                score *= 0.7
        if len(lower_question) >= 8 and lower_question in text.lower():
            score += 0.5

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[:top_k]

    results: List[Dict[str, Any]] = []
    for score, chunk in best:
        results.append(
            {
                "id": chunk.get("id"),
                "page": chunk.get("page"),
                "text": chunk.get("text", ""),
                "score": score,
            }
        )
    return results


def _expand_query_terms_for_available_index(query_terms: List[str], df: Dict[str, Any]) -> List[str]:
    """Add simple fallbacks so newer query tokens can still match older cached indexes."""
    expanded = list(query_terms)
    for term in query_terms:
        if term.startswith("sec_"):
            raw = term[len("sec_"):]
            for piece in raw.split("."):
                piece = piece.strip()
                if piece and piece in df:
                    expanded.append(piece)
        elif term.startswith("param_"):
            raw = term[len("param_"):].strip()
            if raw and raw in df:
                expanded.append(raw)
    return expanded


def _query_terms(text: str) -> List[str]:
    terms = []
    lowered = text.lower()
    for token in _tokenize(text):
        if token in _STOPWORDS:
            continue
        if len(token) <= 1:
            continue
        terms.append(token)
    if "chapter" in lowered:
        terms.extend(["table", "contents", "section", "sections"])
    if "toc" in lowered:
        terms.extend(["table", "contents"])
    return terms


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    lowered = text.lower()
    tokens: List[str] = []

    for match in _SECTION_REF_RE.finditer(lowered):
        tokens.append(f"sec_{match.group(0)}")

    for match in _PARAM_REF_RE.finditer(lowered):
        prefix = match.group(1).lower()
        number = match.group(2)
        tokens.append(f"param_{prefix}{number}")

    for tok in _WORD_TOKEN_RE.findall(lowered):
        if tok.isdigit():
            continue
        if len(tok) == 1 and tok.isalpha():
            continue
        if _PARAM_WORD_RE.match(tok):
            continue
        tokens.append(tok)
    return tokens


def _normalize_text_block(text: str) -> str:
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    compact = "\n".join(line for line in lines if line)
    return compact.strip()


def _truncate_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _is_broad_query(question: str) -> bool:
    lowered = question.lower().strip()
    if not lowered:
        return False
    if _SECTION_REF_RE.search(lowered):
        return False
    if _PARAM_REF_RE.search(lowered):
        return False
    if any(hint in lowered for hint in _BROAD_QUERY_HINTS):
        return True
    tokens = [tok for tok in _WORD_TOKEN_RE.findall(lowered) if not tok.isdigit()]
    return len(tokens) <= 3
