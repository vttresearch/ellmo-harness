import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
from tool_catalog import ToolCatalog


class ConfigOverrideTests(unittest.TestCase):
    def test_config_and_catalog_read_and_write_the_same_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.json'
            path.write_text(json.dumps({'context_window_size': 4096, 'tools': []}))
            with patch.dict(os.environ, {'ELLMO_CONFIG': str(path)}):
                catalog = ToolCatalog()
                self.assertEqual(catalog.config_path, path.resolve())
                self.assertEqual(config.get_context_window_size(), 4096)
                self.assertEqual(catalog.get_enabled_tool_names(), [])
                config.save_config({'context_window_size': 8192, 'tools': []})
                self.assertEqual(json.loads(path.read_text())['context_window_size'], 8192)


if __name__ == '__main__':
    unittest.main()
