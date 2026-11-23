import unittest
from unittest.mock import Mock, patch

try:
    import UI
except ModuleNotFoundError as exc:
    if exc.name not in {"tkinter", "_tkinter"}:
        raise
    UI = None


@unittest.skipIf(UI is None, "Optional Tk runtime is not installed")
class TkRendererTests(unittest.TestCase):
    def test_native_renderer_failure_preserves_existing_widgets_and_falls_back(self):
        for error in (OSError("Native renderer unavailable"), UI.tk.TclError("Cannot load Tkhtml")):
            with self.subTest(error=type(error).__name__):
                existing, partial = Mock(), Mock()
                parent = Mock()
                parent.winfo_children.side_effect = [[existing], [existing, partial]]
                with patch.object(UI, "HtmlFrame", side_effect=error), self.assertLogs("UI", level="WARNING"):
                    self.assertIsNone(UI._create_html_frame(parent))
                partial.destroy.assert_called_once_with()
                existing.destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
