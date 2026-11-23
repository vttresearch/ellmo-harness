#!/usr/bin/env python3
"""Diagnostics for the SINAMICS docs retrieval tool.

Usage examples:
  python tools/query_sinamics_docs/diagnose.py
  python tools/query_sinamics_docs/diagnose.py --question "chapter 4" --top-k 8
  python tools/query_sinamics_docs/diagnose.py --force-reindex
  python tools/query_sinamics_docs/diagnose.py --conversation conversations/<id>.json
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _preview(text: str, limit: int = 220) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _print_status(name: str, value: Any) -> None:
    print(f"{name}: {value}")


def _summarize_conversation(path: Path) -> None:
    print("\n=== Conversation Tool Trace ===")
    if not path.exists():
        print(f"conversation_file_not_found: {path}")
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"conversation_load_error: {exc}")
        return

    messages = data.get("messages")
    if not isinstance(messages, list):
        print("conversation_messages_invalid: expected list")
        return

    tool_msg_count = 0
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue
        tool_msg_count += 1
        tool_name = msg.get("tool_name", "<unknown>")
        content = str(msg.get("content", ""))
        print(f"{idx:04d} TOOL={tool_name}")
        print(f"     RESULT={_preview(content)}")

    if tool_msg_count == 0:
        print("no_tool_messages_found")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose query_sinamics_docs tool behavior.")
    parser.add_argument(
        "--question",
        default="mounting a flat plate variant",
        help="Question to run through query_sinamics_docs.",
    )
    parser.add_argument("--top-k", type=int, default=5, help="top_k for query.")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=300,
        help="max_chars_per_snippet for query.",
    )
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Force rebuild index (requires Docling).",
    )
    parser.add_argument(
        "--conversation",
        type=str,
        default="",
        help="Optional path to a saved conversation JSON to summarize tool results.",
    )
    args = parser.parse_args()

    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # Local import after sys.path adjustment.
    from tools import query_sinamics_docs as qsd

    print("=== Environment ===")
    _print_status("python", sys.executable)
    _print_status("python_version", platform.python_version())
    _print_status("platform", platform.platform())
    _print_status("project_root", root)

    print("\n=== Files ===")
    pdf_path = qsd._DEFAULT_DOC_PATH
    cache_path = qsd._INDEX_PATH
    _print_status("pdf_path", pdf_path)
    _print_status("pdf_exists", pdf_path.exists())
    if pdf_path.exists():
        _print_status("pdf_size_bytes", pdf_path.stat().st_size)
        _print_status("pdf_mtime", int(pdf_path.stat().st_mtime))

    _print_status("cache_path", cache_path)
    _print_status("cache_exists", cache_path.exists())
    if cache_path.exists():
        _print_status("cache_size_bytes", cache_path.stat().st_size)
        _print_status("cache_mtime", int(cache_path.stat().st_mtime))

    print("\n=== Docling ===")
    try:
        import docling  # type: ignore

        _print_status("docling_import", "ok")
        _print_status("docling_version", getattr(docling, "__version__", "unknown"))
    except Exception as exc:
        _print_status("docling_import", f"failed: {exc}")

    print("\n=== Cache Compatibility ===")
    cache_compatible = False
    if cache_path.exists() and pdf_path.exists():
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            source_mtime = int(pdf_path.stat().st_mtime)
            cache_compatible = bool(qsd._is_cache_compatible(raw, pdf_path.resolve(), source_mtime))
            _print_status("cache_compatible", cache_compatible)
        except Exception as exc:
            _print_status("cache_compatible", f"error: {exc}")
    else:
        _print_status("cache_compatible", "n/a")

    print("\n=== Query Run ===")
    _print_status("question", args.question)
    _print_status("top_k", args.top_k)
    _print_status("max_chars_per_snippet", args.max_chars)
    _print_status("force_reindex", args.force_reindex)
    try:
        result = qsd.query_sinamics_docs(
            question=args.question,
            top_k=args.top_k,
            max_chars_per_snippet=args.max_chars,
            force_reindex=args.force_reindex,
        )
    except Exception as exc:
        print(f"query_exception: {exc}")
        return 1

    print("---")
    print(result)

    if args.conversation:
        _summarize_conversation(Path(args.conversation))

    # Soft health summary
    print("\n=== Quick Health Summary ===")
    if pdf_path.exists():
        print("- PDF file: OK")
    else:
        print("- PDF file: MISSING")

    if cache_compatible:
        print("- Cached index: USABLE")
    else:
        print("- Cached index: NOT USABLE")

    if "Docling is not installed" in result:
        print("- Docling runtime: MISSING (needed for index rebuild)")
    elif "Docling failed to parse PDF" in result:
        print("- Docling runtime: PRESENT, but PDF parse failed")
    elif "Retrieval results for" in result:
        print("- Retrieval query: SUCCESS")
    else:
        print("- Retrieval query: CHECK OUTPUT (unexpected status)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
