import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import config
from model_provider import build_tool_schemas
from tool_catalog import ToolCatalog
from tools import query_local_document as documents
from tools.change_gameobject_color import change_gameobject_color


class PublicToolCatalogTests(unittest.TestCase):
    def test_public_examples_resolve_every_tool_without_private_imports(self):
        backend = Path(__file__).resolve().parents[1]
        for name in ["config.example.json", "config.tk.example.json"]:
            with self.subTest(name=name):
                catalog = ToolCatalog(backend / name)
                rows = catalog.list_tools()
                self.assertTrue(all(not row["load_error"] for row in rows), rows)
                self.assertTrue(all(".internal." not in row["module"] for row in rows))
                names = {row["name"] for row in rows}
                self.assertTrue({"get_mqtt_telemetry", "query_local_document", "change_gameobject_color"} <= names)
                if name == "config.tk.example.json":
                    self.assertFalse(catalog.get_enabled_tool_names())
                settings = json.loads((backend / name).read_text())
                self.assertFalse(settings["mqtt"]["enabled"])
                self.assertTrue(all(not any(key in spec for key in ["minimum", "maximum", "max_change"]) for spec in settings["telemetry"]["fields"].values()))

    def test_color_tool_dispatches_new_name_without_claiming_acknowledgement(self):
        send = Mock()
        with patch.dict(sys.modules, {"api_ws": SimpleNamespace(send_to_unity_threadsafe=send)}):
            result = change_gameobject_color(10, 20, 30)
        send.assert_called_once_with({"type": "unity_tool", "data": {
            "tool_name": "change_gameobject_color", "color": {"r": 10, "g": 20, "b": 30},
        }})
        self.assertIn("not been confirmed", result)
        schemas = build_tool_schemas({"change_gameobject_color": change_gameobject_color})
        self.assertIn("change_gameobject_color", schemas)

    def test_operator_document_selection_and_cache_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.md"
            second = root / "second.md"
            first.write_text("The violet indicator marks calibration mode.")
            second.write_text("The amber indicator marks maintenance mode.")
            documents._document_search.cache_clear()
            self.addCleanup(documents._document_search.cache_clear)
            with patch.object(documents, "_SUPPORT_DIR", root / "support"), patch.object(
                config, "load_config", return_value={"local_document": {"path": "first.md"}}
            ), patch.object(config, "_config_path", return_value=root / "config.json"), patch.dict(os.environ, {"ELLMO_DOCUMENT_PATH": ""}):
                result = documents.query_local_document("indicator")
                self.assertIn("violet", result)
                with patch.dict(os.environ, {"ELLMO_DOCUMENT_PATH": str(second)}):
                    result = documents.query_local_document("indicator")
                    self.assertIn("amber", result)
                    self.assertNotIn("violet", result)
                self.assertIn("violet", documents.query_local_document("indicator"))
                self.assertEqual(len(list((root / "support" / "cache").glob("*/local_document_text_index.json"))), 2)


if __name__ == "__main__":
    unittest.main()
