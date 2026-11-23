import json
import unittest
from typing import Literal, Optional
from unittest.mock import patch

import httpx
import ollama

from model_provider import OllamaProviderClient


class OllamaToolSchemaTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply = {
            "model": "test-model", "done": True,
            "message": {"role": "assistant", "content": ""},
            "prompt_eval_count": 12, "eval_count": 3,
        }

        def respond(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json=self.reply)

        client = ollama.Client(host="http://ollama.test", transport=httpx.MockTransport(respond))
        self.addCleanup(client._client.close)
        with patch.object(ollama, "Client", return_value=client):
            self.provider = OllamaProviderClient(
                host="http://ollama.test", model="test-model", request_timeout_seconds=2,
            )

    def chat(self, tools):
        return self.provider.chat(
            messages=[{"role": "user", "content": "Choose a mode."}], tools=tools,
            context_window_size=4096, seed=7, options={"temperature": 0},
        )

    def test_sdk_serialization_preserves_constraints_aliases_and_response(self):
        def example(mode: 'Literal["first", "second"]', count: int,
                    values: Optional[list[float]] = None):
            """Choose a mode.

            Args:
                mode: Which mode to use.
            """
            self.fail("Schema construction must not execute tools")

        call = {"function": {"name": "configured_alias", "arguments": {"mode": "first", "count": 2}}}
        self.reply["message"]["tool_calls"] = [call]
        response = self.chat({"configured_alias": example})

        request, = self.requests
        schema, = request["tools"]
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "configured_alias")
        parameters = schema["function"]["parameters"]
        self.assertEqual(parameters["required"], ["mode", "count"])
        self.assertEqual(parameters["properties"], {
            "mode": {"type": "string", "enum": ["first", "second"], "description": "Which mode to use."},
            "count": {"type": "integer"},
            "values": {"type": "array", "items": {"type": "number"}},
        })
        self.assertEqual(request["options"], {"num_ctx": 4096, "seed": 7, "temperature": 0})
        self.assertEqual(response.message["tool_calls"], [call])
        self.assertEqual((response.prompt_tokens, response.completion_tokens), (12, 3))

    def test_chat_without_tools_serializes_an_empty_list(self):
        self.chat({})
        self.assertEqual(self.requests[0]["tools"], [])

    def test_prebuilt_schemas_remain_supported(self):
        schema = {
            "type": "function",
            "function": {
                "name": "choose", "description": "Choose a mode.",
                "parameters": {
                    "type": "object", "required": ["mode"],
                    "properties": {"mode": {"type": "string", "enum": ["first", "second"]}},
                },
            },
        }
        self.chat({"choose": schema})
        self.assertEqual(self.requests[0]["tools"], [schema])


if __name__ == "__main__":
    unittest.main()
