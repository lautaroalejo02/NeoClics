"""WP13 finding 5: a LAN bind needs a token and only exposes the listening UI."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from muestra.api.server import LOOPBACK_NAMES, _host_name, create_app

TOKEN = "s3cret-token"


@pytest.fixture
def lan(tmp_project):
    app = create_app(tmp_project.root, watch_interval=0.01, debounce=0.03, token=TOKEN)
    with TestClient(app, client=("192.168.1.50", 50000)) as phone:
        yield phone, app


def test_remote_client_needs_the_token_then_gets_a_cookie(lan):
    phone, _ = lan
    denied = phone.get("/tools/song_get")
    assert denied.status_code == 401 and denied.json()["errors"][0]["code"] == "unauthorized"
    assert phone.get("/").status_code == 401  # a helpful page, not the UI
    first = phone.get(f"/project?token={TOKEN}")
    assert first.status_code == 200 and phone.cookies.get("muestra_token") == TOKEN
    assert phone.get("/tools/song_get").status_code == 200  # cookie from now on
    phone.cookies.clear()
    assert phone.get("/tools/song_get", headers={"X-Muestra-Token": TOKEN}).status_code == 200
    assert phone.get("/tools/song_get?token=wrong").status_code == 401
    phone.cookies.clear()
    assert phone.get(f"/tools/song_get?token={TOKEN}").json()["ok"]  # not a tool param


def test_remote_client_can_listen_and_comment_but_not_edit(lan, tmp_project):
    phone, _ = lan
    headers = {"X-Muestra-Token": TOKEN}
    event = {"type": "comment", "ts": "2026-11-17T10:00:00Z", "text": "acá el bombo"}
    ok = phone.post("/tools/feedback_append", json={"event": event}, headers=headers)
    assert ok.status_code == 200 and ok.json()["ok"]
    for route, body in [
        ("library_index", {"path": str(tmp_project.root.parent)}),
        ("source_import", {"path": str(tmp_project.root / "song.yaml")}),
        ("render", {}),
        ("versions_restore", {"version": "v001"}),
        ("project_create", {"name": "x"}),
    ]:
        response = phone.post(f"/tools/{route}", json=body, headers=headers)
        assert response.status_code == 403, route
        assert response.json()["errors"][0]["code"] == "local_only"
    assert phone.delete("/jobs/abc", headers=headers).status_code == 403
    assert not (tmp_project.root.parent / ".muestra-index.json").exists()


def test_loopback_client_is_frictionless_in_lan_mode(tmp_project):
    app = create_app(tmp_project.root, token=TOKEN)
    with TestClient(app, client=("127.0.0.1", 50000)) as laptop:
        assert laptop.get("/tools/song_get").status_code == 200
        response = laptop.post("/tools/song_validate", json={"check_files": False})
        assert response.status_code == 200


def test_websocket_requires_the_token_from_remote_clients(lan):
    phone, _ = lan
    with pytest.raises(WebSocketDisconnect):
        with phone.websocket_connect("/events") as ws:
            ws.receive_json()
    with phone.websocket_connect(f"/events?token={TOKEN}") as ws:
        ws.close()


def test_loopback_bind_rejects_foreign_host_headers(tmp_project):
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends a
    matching Origin and Host; the loopback server only answers loopback names."""
    app = create_app(tmp_project.root, allowed_hosts=LOOPBACK_NAMES)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/tools/song_get").status_code == 200
        evil = client.get("/tools/song_get", headers={"host": "evil.example:8765"})
        assert evil.status_code == 403
    assert _host_name("[::1]:8765") == "::1" and _host_name("localhost:1") == "localhost"
