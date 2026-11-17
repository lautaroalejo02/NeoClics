from __future__ import annotations

from fastapi.testclient import TestClient

from muestra.mcp.server import create_server
from muestra.registry import REGISTRY


def test_official_sdk_streamable_http_transport(tmp_project):
    app = create_server(tmp_project.root).streamable_http_app(json_response=True)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        headers = {"Accept": "application/json, text/event-stream"}
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "muestra-test", "version": "1"},
                },
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["serverInfo"]["name"] == "Muestra"
        headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
        headers["Mcp-Protocol-Version"] = "2025-11-25"
        initialized = client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert initialized.status_code == 202
        listing = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert {t["name"] for t in listing.json()["result"]["tools"]} >= {
            s.mcp_name for s in REGISTRY.values()
        }
        call = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "song_validate", "arguments": {"check_files": False}},
            },
        )
        assert call.json()["result"]["structuredContent"]["data"]["valid"]
