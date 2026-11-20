"""A minimal MCP server over stdio (JSON-RPC 2.0, one message per line) that offers the rca tools of one task.

The claude CLI starts it from the --mcp-config the UI passes; the task comes as JSON in RCA_UI_TASK.
"""

import json
import os
import sys

from rca.ui import toolspec


def main() -> None:
    task = json.loads(os.environ["RCA_UI_TASK"])
    tools = [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
             for t in toolspec.definitions(task)]
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:  # a notification, e.g. notifications/initialized
            continue
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "rca", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": tools}
        elif method == "tools/call":
            params = msg.get("params", {})
            output, is_error = toolspec.call(task, params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": output}], "isError": is_error}
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"no method {method}"}}),
                  flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}), flush=True)


if __name__ == "__main__":
    main()
