import unittest
from typing import List, Literal, Optional

from model_provider import build_tool_schemas


class ToolSchemaTests(unittest.TestCase):
    def test_string_annotations_and_pep604_optional_preserve_types(self):
        def example(mode: 'Literal["first", "second"]', count: 'int',
                    values: 'Optional[List[float]]' = None, limit: int | None = None):
            pass

        parameters = build_tool_schemas({"example": example})["example"]["function"]["parameters"]
        self.assertEqual(parameters["properties"], {
            "mode": {"type": "string", "enum": ["first", "second"]},
            "count": {"type": "integer"},
            "values": {"type": "array", "items": {"type": "number"}},
            "limit": {"type": "integer"},
        })
        self.assertEqual(parameters["required"], ["mode", "count"])

    def test_optional_numeric_list_has_number_items(self):
        def example(values: Optional[List[float]] = None):
            pass

        parameters = build_tool_schemas({"example": example})["example"]["function"]["parameters"]
        self.assertEqual(parameters["properties"]["values"], {
            "type": "array", "items": {"type": "number"},
        })
        self.assertNotIn("required", parameters)

    def test_builtin_nested_lists_preserve_item_types_and_requiredness(self):
        def example(values: list[list[int]]):
            pass

        parameters = build_tool_schemas({"example": example})["example"]["function"]["parameters"]
        self.assertEqual(parameters["properties"]["values"], {
            "type": "array", "items": {"type": "array", "items": {"type": "integer"}},
        })
        self.assertEqual(parameters["required"], ["values"])

    def test_list_items_retain_enum_constraints(self):
        def example(values: List[Literal["first", "second"]]):
            pass

        properties = build_tool_schemas({"example": example})["example"]["function"]["parameters"]["properties"]
        self.assertEqual(properties["values"]["items"], {
            "type": "string", "enum": ["first", "second"],
        })


if __name__ == "__main__":
    unittest.main()
