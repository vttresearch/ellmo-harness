"""Reusable search over one locally configured PDF, Markdown or text document."""
import hashlib
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import document_retrieval as _retrieval

_SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
_IGNORED_FILENAMES = {"readme.md"}
_MAX_DUPLICATE_QUERY_CACHE = 64


class LocalDocumentSearch:
    def __init__(self, support_dir: Path, document_path: Optional[Path] = None):
        self.support_dir = Path(support_dir)
        self.document_path = Path(document_path).expanduser().resolve() if document_path else None
        self.cache_dir = self.support_dir / "cache"
        if self.document_path is not None:
            self.cache_dir /= hashlib.sha256(str(self.document_path).encode()).hexdigest()[:16]
        self.index_path = self.cache_dir / "local_document_text_index.json"
        self._cache_local = threading.local()
        self._index_lock = threading.RLock()

    def _duplicate_query_cache(self) -> "OrderedDict[str, str]":
        cache = getattr(self._cache_local, "cache", None)
        if cache is None:
            cache = OrderedDict()
            self._cache_local.cache = cache
        return cache

    def reset_runtime_state(self) -> None:
        """Clear per-thread duplicate-query state."""
        self._duplicate_query_cache().clear()

    def query(
        self,
        question: str,
        top_k: int = 5,
        max_chars_per_snippet: int = 700,
    ) -> str:
        """Search the selected local document (PDF, Markdown, or text).

        question: Natural-language query to search in the manual.
        top_k: Number of matching snippets to return, from 1 to 20.
        max_chars_per_snippet: Maximum characters returned per snippet, from 200 to 2500.
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
        requested_max_chars = max_chars_per_snippet
        max_chars_per_snippet = min(max_chars_per_snippet, 2500)

        document_path, discovery_error = self._find_manual()
        if discovery_error:
            return discovery_error
        assert document_path is not None
        page_labels: Tuple[str, ...] = ()
        if document_path.suffix.lower() == ".pdf":
            try:
                page_labels = _retrieval._page_labels_for(document_path)
            except Exception as exc:
                return f"Could not read PDF page labels: {exc}"

        override_notes: List[str] = []
        if _retrieval._is_broad_query(question):
            if top_k < 12:
                top_k = 12
                override_notes.append("broad-query override applied: top_k=12")
            if max_chars_per_snippet < 1200:
                max_chars_per_snippet = 1200
                override_notes.append(
                    "broad-query override applied: max_chars_per_snippet=1200"
                )

        source_stat = document_path.stat()
        duplicate_key = json.dumps(
            {
                "source": str(document_path.resolve()),
                "mtime_ns": source_stat.st_mtime_ns,
                "size": source_stat.st_size,
                "question": question,
                "top_k": top_k,
                "max_chars": max_chars_per_snippet,
            },
            sort_keys=True,
        )
        cached_report = self._get_duplicate_cached_result(duplicate_key)
        if cached_report is not None:
            return (
                "Duplicate query detected: returning the cached retrieval report.\n"
                "---\n"
                f"{cached_report}"
            )

        index_data, index_status = self._load_or_build_index(document_path)
        if isinstance(index_data, str):
            return index_data

        results = _retrieval._search_index(index_data, question, top_k)
        if not results:
            report = (
                f"No relevant snippets found for question: {question}\n"
                "Try a more specific query."
            )
            self._store_duplicate_cached_result(duplicate_key, report)
            return report

        source_format = self._source_format(document_path)
        lines = [
            f'Retrieval results for "{question}" from {document_path.name}:',
            f"Source format: {source_format}",
            f"Index status: {index_status}",
            (
                "Retrieval params: "
                f"requested_top_k={requested_top_k}, applied_top_k={top_k}, "
                f"requested_max_chars_per_snippet={requested_max_chars}, "
                f"applied_max_chars_per_snippet={max_chars_per_snippet}"
            ),
        ]
        if override_notes:
            lines.append("Retrieval overrides: " + "; ".join(override_notes))
        if document_path.suffix.lower() == ".pdf":
            lines.append(
                "Cite page= labels (printed page numbers), not pdf_page= positions. "
                "Pass the page label unchanged to change_page; do not calculate an offset."
            )
        else:
            lines.append("Cite the source filename and chunk id when relevant.")
        lines.append("---")

        for rank, item in enumerate(results, start=1):
            page = item.get("page")
            location = (
                _retrieval._page_citation(page, page_labels)
                if isinstance(page, int)
                else f"chunk_id={item.get('id', 'n/a')}"
            )
            score = float(item.get("score", 0.0))
            snippet = _retrieval._truncate_text(
                str(item.get("text", "")), max_chars_per_snippet
            )
            lines.append(f"[{rank}] {location} score={score:.3f}")
            lines.append(snippet)
            lines.append("---")

        report = "\n".join(lines).rstrip("-\n")
        self._store_duplicate_cached_result(duplicate_key, report)
        return report

    def _find_manual(self) -> Tuple[Optional[Path], Optional[str]]:
        if self.document_path is not None:
            if not self.document_path.is_file():
                return None, f"Local document not found: {self.document_path}."
            if self.document_path.suffix.lower() not in _SUPPORTED_SUFFIXES:
                return None, "Unsupported document format; use PDF, Markdown, or text."
            return self.document_path, None
        self.support_dir.mkdir(parents=True, exist_ok=True)
        candidates = sorted(
            path
            for path in self.support_dir.iterdir()
            if path.is_file()
            and path.name.lower() not in _IGNORED_FILENAMES
            and path.suffix.lower() in _SUPPORTED_SUFFIXES
        )
        supported = ", ".join(sorted(_SUPPORTED_SUFFIXES))
        if not candidates:
            return None, (
                "Local document not found. Place exactly one supported document in "
                f"{self.support_dir}. Supported extensions: {supported}."
            )
        if len(candidates) > 1:
            names = ", ".join(path.name for path in candidates)
            return None, (
                "Multiple local documents were found; keep exactly one document in "
                f"{self.support_dir}. Found: {names}."
            )
        return candidates[0], None

    def _load_or_build_index(self, pdf_or_text_path: Path) -> Tuple[Dict[str, Any] | str, str]:
        with self._index_lock:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            source_path = pdf_or_text_path.resolve()
            source_stat = source_path.stat()
            source_mtime_ns = source_stat.st_mtime_ns
            source_size = source_stat.st_size
            cached_any: Optional[Dict[str, Any]] = None
            mismatch_reason = ""

            if self.index_path.exists():
                try:
                    cached = json.loads(self.index_path.read_text(encoding="utf-8"))
                    if _retrieval._is_cache_structurally_usable(cached):
                        cached_any = cached
                    mismatch_reasons = self._cache_incompatibility_reasons(
                        cached, source_path, source_mtime_ns, source_size
                    )
                    if not mismatch_reasons:
                        return cached, "reused cached index"
                    mismatch_reason = "; ".join(mismatch_reasons)
                except Exception:
                    pass

            built = self._build_index(source_path, source_mtime_ns, source_size)
            if isinstance(built, str):
                if cached_any is not None and cached_any.get("source_path") == str(source_path):
                    reason = " ".join(built.split())
                    return cached_any, f"reused stale cache (rebuild failed: {reason})"
                return built, "index build failed"

            temp_path = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
            try:
                temp_path.write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
                temp_path.replace(self.index_path)
            except Exception as exc:
                return (
                    f"Index built but failed to save cache at {self.index_path}: {exc}",
                    "cache save failed",
                )

            status = "rebuilt index"
            if mismatch_reason:
                status += f" (cache mismatch: {mismatch_reason})"
            return built, status

    def _build_index(self,
        source_path: Path, source_mtime_ns: int, source_size: int
    ) -> Dict[str, Any] | str:
        if source_path.suffix.lower() == ".pdf":
            built = _retrieval._build_index(source_path, source_mtime_ns)
            if isinstance(built, str):
                return built
            built["source_size"] = source_size
            built["source_format"] = "pdf"
            return built

        try:
            text = source_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = source_path.read_text(encoding="utf-16")
            except Exception as exc:
                return f"Failed to decode text document '{source_path}': {exc}"
        except Exception as exc:
            return f"Failed to read text document '{source_path}': {exc}"

        normalized = _retrieval._normalize_text_block(text)
        if not normalized:
            return f"Document did not contain usable text: '{source_path}'."

        chunks: List[Dict[str, Any]] = []
        for chunk_index, chunk_text in enumerate(_retrieval._split_into_chunks(normalized)):
            terms = _retrieval._tokenize(chunk_text)
            if not terms:
                continue
            term_freq: Dict[str, int] = {}
            for term in terms:
                term_freq[term] = term_freq.get(term, 0) + 1
            chunks.append(
                {
                    "id": f"c{chunk_index}",
                    "page": None,
                    "text": chunk_text,
                    "term_freq": term_freq,
                    "len": len(terms),
                }
            )

        if not chunks:
            return f"Chunking produced no searchable text for '{source_path}'."

        document_frequency: Dict[str, int] = {}
        total_terms = 0
        for chunk in chunks:
            total_terms += int(chunk["len"])
            for term in chunk["term_freq"]:
                document_frequency[term] = document_frequency.get(term, 0) + 1

        return {
            "version": _retrieval._INDEX_VERSION,
            "created_at": int(time.time()),
            "source_path": str(source_path),
            "source_mtime": source_mtime_ns,
            "source_size": source_size,
            "source_format": source_path.suffix.lower().lstrip("."),
            "chunk_size_chars": _retrieval._CHUNK_SIZE_CHARS,
            "chunk_overlap_chars": _retrieval._CHUNK_OVERLAP_CHARS,
            "avg_chunk_len": total_terms / len(chunks),
            "doc_count": len(chunks),
            "df": document_frequency,
            "chunks": chunks,
        }

    def _cache_incompatibility_reasons(self,
        index_data: Any,
        source_path: Path,
        source_mtime_ns: int,
        source_size: int,
    ) -> List[str]:
        reasons = _retrieval._cache_incompatibility_reasons(
            index_data, source_path, source_mtime_ns
        )
        if not isinstance(index_data, dict):
            return reasons
        try:
            cached_size = int(index_data.get("source_size", -1))
        except Exception:
            cached_size = -1
        if cached_size != source_size:
            reasons.append(f"source_size={cached_size} expected={source_size}")
        return reasons

    def _source_format(self, path: Path) -> str:
        return {
            ".pdf": "PDF",
            ".md": "Markdown",
            ".markdown": "Markdown",
            ".txt": "plain text",
        }.get(path.suffix.lower(), path.suffix.lstrip(".").upper())

    def _get_duplicate_cached_result(self, key: str) -> Optional[str]:
        cache = self._duplicate_query_cache()
        result = cache.get(key)
        if result is not None:
            cache.move_to_end(key)
        return result

    def _store_duplicate_cached_result(self, key: str, result: str) -> None:
        cache = self._duplicate_query_cache()
        cache[key] = result
        cache.move_to_end(key)
        while len(cache) > _MAX_DUPLICATE_QUERY_CACHE:
            cache.popitem(last=False)
