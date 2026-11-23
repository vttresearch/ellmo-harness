# Backend

The backend exposes model/tool metadata over REST and streams conversations and
correlated Unity tool calls over WebSocket.

Only one client can be connected at a time. Use one WebSocket worker and no
replicas; additional connections receive `CLIENT_ALREADY_CONNECTED` and close
code 1008. TODO: support multiple clients with isolated sessions and tool routing.

Turns save their messages, tool results, transcript and event log to the ignored
`backend/conversations/` directory. A reconnect starts a new chat; saved snapshots
remain available through the REST conversation endpoints.

From the repository root:

```bash
cp backend/config.example.json backend/config.json
python run.py
```

Useful endpoints include `GET /health`, `GET /hosts`, `GET /tools`, and
`/ws/chat/{conversation_id}`. Configuration details and privacy notes are in
[`../docs/setup.md`](../docs/setup.md).
