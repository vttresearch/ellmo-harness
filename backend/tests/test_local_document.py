import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_document import LocalDocumentSearch
import document_retrieval as retrieval_core
from tools import query_sinamics_docs


def _write_pdf(path, *, labeled=True):
    """A blank five-page fixture with two Roman pages followed by pages 1–3."""
    labels = " /PageLabels << /Nums [0 << /S /r >> 2 << /S /D >>] >>" if labeled else ""
    objects = [
        f"<< /Type /Catalog /Pages 2 0 R{labels} >>",
        "<< /Type /Pages /Count 5 /Kids [3 0 R 4 0 R 5 0 R 6 0 R 7 0 R] >>",
        *["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>"] * 5,
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n{obj}\nendobj\n".encode())
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)


class LocalDocumentSearchTests(unittest.TestCase):
    def setUp(self):
        self.search = LocalDocumentSearch(Path("unused"))

    def _paths(self, root: Path):
        return patch.multiple(self.search, support_dir=root, cache_dir=root / "cache",
                              index_path=root / "cache" / "local_document_text_index.json")

    def test_queries_markdown_and_reuses_isolated_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            support_dir = Path(temp_dir)
            (support_dir / "maintenance_manual.md").write_text(
                "# Motor maintenance\n\nCalibrate motor torque to 42 Nm before startup.",
                encoding="utf-8",
            )

            with self._paths(support_dir):
                first = self.search.query("motor torque calibration")
                self.search.reset_runtime_state()
                second = self.search.query("motor torque calibration")

                self.assertIn("maintenance_manual.md", first)
                self.assertIn("42 Nm", first)
                self.assertIn("Index status: rebuilt index", first)
                self.assertIn("Index status: reused cached index", second)
                self.assertTrue(self.search.index_path.exists())

    def test_reports_missing_manual_with_supported_formats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self._paths(Path(temp_dir)):
                result = self.search.query("startup")

        self.assertIn("Local document not found", result)
        self.assertIn(".pdf", result)
        self.assertIn(".md", result)
        self.assertIn(".txt", result)

    def test_rejects_ambiguous_manual_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            support_dir = Path(temp_dir)
            (support_dir / "manual.md").write_text("alpha", encoding="utf-8")
            (support_dir / "manual.txt").write_text("beta", encoding="utf-8")

            with self._paths(support_dir):
                result = self.search.query("alpha")

        self.assertIn("Multiple local documents", result)
        self.assertIn("manual.md", result)
        self.assertIn("manual.txt", result)

    def test_pdf_uses_docling_index_builder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            support_dir = Path(temp_dir)
            manual_path = support_dir / "maintenance_manual.pdf"
            _write_pdf(manual_path)

            def fake_pdf_index(path, source_mtime):
                return {
                    "version": retrieval_core._INDEX_VERSION,
                    "created_at": 1,
                    "source_path": str(path),
                    "source_mtime": source_mtime,
                    "chunk_size_chars": retrieval_core._CHUNK_SIZE_CHARS,
                    "chunk_overlap_chars": retrieval_core._CHUNK_OVERLAP_CHARS,
                    "avg_chunk_len": 4,
                    "doc_count": 1,
                    "df": {"motor": 1, "torque": 1},
                    "chunks": [
                        {
                            "id": "p3_c0",
                            "page": 3,
                            "text": "Set motor torque before startup.",
                            "term_freq": {"motor": 1, "torque": 1},
                            "len": 4,
                        }
                    ],
                }

            with self._paths(support_dir), patch.object(
                retrieval_core, "_build_index", side_effect=fake_pdf_index
            ) as build_index:
                result = self.search.query("motor torque")

        build_index.assert_called_once()
        self.assertIn("Source format: PDF", result)
        self.assertIn("page=1", result)
        self.assertIn("pdf_page=3", result)
        self.assertIn("printed page numbers", result)

    def test_pdf_labels_include_roman_front_matter_and_unlabeled_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            labeled, unlabeled = Path(tmp) / "labeled.pdf", Path(tmp) / "unlabeled.pdf"
            _write_pdf(labeled)
            _write_pdf(unlabeled, labeled=False)
            self.assertEqual(retrieval_core._page_labels_for(labeled), ("i", "ii", "1", "2", "3"))
            self.assertEqual(retrieval_core._page_labels_for(unlabeled), ("1", "2", "3", "4", "5"))

    def test_sinamics_report_converts_existing_physical_page_index_to_printed_label(self):
        retrieval = query_sinamics_docs
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "manual.pdf"
            _write_pdf(source)
            retrieval.reset_runtime_state()
            with patch.object(retrieval, "_DEFAULT_DOC_PATH", source), patch.object(
                retrieval, "_load_or_build_index", return_value=({}, "reused cached index")
            ), patch.object(retrieval, "_search_index", return_value=[
                {"id": "p3_c0", "page": 3, "text": "torque", "score": 1.0}
            ]):
                result = retrieval.query_sinamics_docs("torque")
            self.assertIn("page=1 pdf_page=3", result)
            self.assertIn("printed page numbers", result)


if __name__ == "__main__":
    unittest.main()
