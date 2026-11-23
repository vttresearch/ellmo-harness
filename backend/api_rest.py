from datetime import datetime, timezone
from typing import Any, Dict, List

from pydantic import BaseModel

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

from conversation_store import ConversationManager
from session import probe_model_hosts
import config
from tool_catalog import ToolCatalog

app = FastAPI(title="Model Chat REST API")

MODEL_HOSTS: Dict[str, Dict[str, Any]] = config.get_model_hosts()
TOOL_CATALOG = ToolCatalog()

conversation_manager = ConversationManager()


class ToolDefinitionPayload(BaseModel):
    name: str
    module: str
    function: str
    enabled: bool = True
    description: str = ""


class SetToolsRequest(BaseModel):
    tools: List[ToolDefinitionPayload]


class SetEnabledToolsRequest(BaseModel):
    enabled_tools: List[str]


@app.get("/hosts")
async def get_hosts() -> dict[str, int | str | dict[str, dict[str, dict[str, Any]]]]:
    """Return all configured runtime hosts and their models (with/without tools)."""
    # Host probes use synchronous HTTP clients and may take several seconds for
    # offline machines. Keep that work off the ASGI event loop so lightweight
    # endpoints such as /health remain responsive while discovery is running.
    hosts_info = await run_in_threadpool(probe_model_hosts, MODEL_HOSTS)
    return {
        "v": 1,
        "type": "hosts",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {"hosts": hosts_info},
    }


@app.get("/conversations")
async def list_conversations() -> dict[str, int | str | dict[str, list[dict[str, Any]]]]:
    """List stored conversations (metadata only)."""
    try:
        conversations = conversation_manager.list_conversations()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "v": 1,
        "type": "conversations",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {"conversations": conversations},
    }


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Return a stored conversation snapshot by id."""
    try:
        data = conversation_manager.load_conversation(conv_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "v": 1,
        "type": "conversation",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


@app.get("/health")
async def health_check():
    """Report REST API liveness without waiting for model-host probes.

    Runtime host availability and model discovery belong to ``GET /hosts``.
    Keeping this endpoint local and inexpensive lets short-timeout clients
    distinguish an unreachable REST API from slow or offline model hosts.
    """
    return {
        "v": 1,
        "type": "health",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {"status": "ok"},
    }


@app.get("/tools")
async def get_tools() -> dict[str, int | str | dict[str, Any]]:
    """Return tool catalog and enabled tool names."""
    tools = TOOL_CATALOG.list_tools()
    enabled_tools = TOOL_CATALOG.get_enabled_tool_names()
    return {
        "v": 1,
        "type": "tools",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {
            "tools": tools,
            "enabled_tools": enabled_tools,
        },
    }


@app.put("/tools")
async def set_tools(payload: SetToolsRequest) -> dict[str, int | str | dict[str, Any]]:
    """Replace the full tools catalog in config.json."""
    try:
        tools_payload = []
        for item in payload.tools:
            if hasattr(item, "model_dump"):
                tools_payload.append(item.model_dump())
            else:
                tools_payload.append(item.dict())
        tools = TOOL_CATALOG.set_tools(tools_payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    enabled_tools = TOOL_CATALOG.get_enabled_tool_names()
    return {
        "v": 1,
        "type": "tools_replaced",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {
            "tools": tools,
            "enabled_tools": enabled_tools,
        },
    }


@app.put("/tools/enabled")
async def set_enabled_tools(payload: SetEnabledToolsRequest) -> dict[str, int | str | dict[str, Any]]:
    """Persist enabled tool list into config.json."""
    try:
        enabled = TOOL_CATALOG.set_enabled_tools(payload.enabled_tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    tools = TOOL_CATALOG.list_tools()
    return {
        "v": 1,
        "type": "tools_updated",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {
            "enabled_tools": enabled,
            "tools": tools,
        },
    }
