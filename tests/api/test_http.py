from __future__ import annotations

import threading
import time
from urllib.parse import quote

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from muestra.api.server import bind_socket, create_app
from muestra.registry import REGISTRY
from muestra.schemas.api import Job, WsEvent

from .fakes import specs


@pytest.fixture
def client(tmp_project, monkeypatch):
    for spec in specs():
        monkeypatch.setitem(REGISTRY, spec.name, spec)
    with TestClient(create_app(tmp_project.root, watch_interval=0.01, debounce=0.03)) as client:
        yield client


def wait_job(client, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{identity}").json()
        Job.model_validate(job)
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.01)
    pytest.fail("Job did not finish")


def test_generation_real_tools_and_project_lock(client, tmp_project, tmp_path):
    assert client.get("/tools/song_get").json()["data"]["song"]["meta"]["bpm"] == 120
    assert client.post("/tools/song_validate", json={"check_files": False}).json()["data"]["valid"]
    assert client.get("/tools/song_validate?check_files=false").json()["data"]["valid"]
    assert client.get("/tools/test_echo?value=13").json()["data"]["value"] == 13
    assert client.post("/tools/test_echo", json={"value": 14}).json()["data"]["value"] == 14
    assert client.get("/tools/test_crash").status_code in (404, 405)
    invalid = client.post("/tools/test_echo", json={"value": "wrong", "unknown": True})
    assert invalid.status_code == 422
    assert len(invalid.json()["errors"]) == 2
    assert client.post("/tools/test_echo", content="{").json()["errors"][0]["code"] == "parse_error"
    assert client.post("/tools/test_echo", json=[]).status_code == 422
    assert client.post("/tools/test_echo", json={"project": str(tmp_path)}).status_code == 409
    assert client.post("/tools/project_open", json={"path": str(tmp_path)}).status_code == 409
    assert client.get("/tools/project_open").json()["data"]["project_path"] == str(tmp_project.root)
    assert client.post("/tools/project_create", json={"name": "blocked"}).status_code == 409
    response = client.post(
        "/tools/feedback_append",
        json={"event": {"type": "comment", "version": "v001", "text": "More bass", "at_s": 1.2}},
    )
    assert response.status_code == 200
    assert response.headers["x-muestra-project"] == str(tmp_project.root)
    assert client.get("/tools/feedback_read").json()["data"]["count"] > 0
    assert client.post("/tools/feedback_append", json={"event": {"type": "bad"}}).status_code == 422
    stub = client.post("/tools/test_stub", json={})
    assert stub.status_code == 501
    assert stub.json()["errors"][0]["code"] == "not_implemented"
    crash = client.post("/tools/test_crash", json={})
    assert crash.status_code == 500
    assert "PRIVATE_TOKEN" not in crash.text
    assert "PRIVATE_TOKEN" in (tmp_project.root / "cache/logs/muestra.log").read_text("utf-8")


def test_openapi_generated_parameter_models(client):
    schema = client.get("/openapi.json").json()
    for spec in REGISTRY.values():
        route = schema["paths"][f"/tools/{spec.mcp_name}"]
        assert "post" in route
        assert ("get" in route) == spec.readonly
        body = route["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert "project" not in body.get("properties", {})
        assert set(body.get("properties", {})) == set(spec.params.model_fields) - {"project"}
    assert "202" in schema["paths"]["/tools/render"]["post"]["responses"]

    # All references in the published OpenAPI document resolve from its root.
    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                node = schema
                for key in value["$ref"][2:].split("/"):
                    node = node[key]
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)

    refs(schema)


def test_jobs_are_serial_progress_and_cancellation(client, monkeypatch):
    spec = REGISTRY["fake.slow"]
    entered, release = threading.Event(), threading.Event()
    running, maximum = 0, 0

    def work(params, *, progress):
        nonlocal running, maximum
        from .fakes import FakeResult

        running += 1
        maximum = max(maximum, running)
        progress(0.5, "Waiting")
        entered.set()
        assert release.wait(5)
        running -= 1
        return FakeResult(value=params.value)

    object.__setattr__(spec, "func", work)
    with client.websocket_connect("/events") as ws:
        first = client.post("/tools/test_slow", json={"value": 3})
        assert first.status_code == 202
        identity = first.json()["job_id"]
        assert entered.wait(2)
        # A render never prevents the UI reading the previous song/versions.
        assert client.get("/project").status_code == 200
        second = client.post("/tools/test_slow", json={"value": 4}).json()["job_id"]
        third = client.post("/tools/test_slow", json={"value": 5}).json()["job_id"]
        assert client.delete(f"/jobs/{identity}").status_code == 409
        assert client.delete(f"/jobs/{second}").json()["status"] == "failed"
        release.set()
        assert wait_job(client, identity)["result"]["data"]["value"] == 3
        assert wait_job(client, third)["result"]["data"]["value"] == 5
        assert maximum == 1
        saw_progress = False
        while True:
            event = WsEvent.model_validate(ws.receive_json())
            if event.type == "job.updated":
                saw_progress |= event.data.get("progress") == 0.5
                if event.data["id"] == third and event.data["status"] == "done":
                    break
        assert saw_progress
    assert client.get("/jobs/absent").status_code == 404
    assert client.delete("/jobs/absent").status_code == 404


def test_queue_capacity_and_bad_long_parameters(tmp_project, monkeypatch):
    from .fakes import FakeResult

    entered, release = threading.Event(), threading.Event()

    def work(params, *, progress):
        entered.set()
        assert release.wait(5)
        return FakeResult(value=1)

    spec = next(s for s in specs() if s.long_running)
    object.__setattr__(spec, "func", work)
    monkeypatch.setitem(REGISTRY, spec.name, spec)
    with TestClient(create_app(tmp_project.root, job_capacity=1)) as client:
        try:
            assert client.post("/tools/test_slow", json={"value": "bad"}).status_code == 422
            first = client.post("/tools/test_slow", json={}).json()["job_id"]
            assert entered.wait(2)
            assert client.post("/tools/test_slow", json={}).status_code == 202
            assert client.post("/tools/test_slow", json={}).status_code == 503
        finally:
            release.set()
        assert wait_job(client, first)["status"] == "done"


def test_summary_audio_ranges_waveform_and_ui(client, tmp_project):
    summary = client.get("/project").json()
    assert summary["song"]["meta"]["bpm"] == 120
    assert summary["versions"][0]["version"] == "v001"
    assert summary["versions"][0]["metrics"]["loudness"]
    assert summary["candidate_sets"][0]["set_id"] == "cs001"
    assert "feedback" in summary["versions"][0]
    path = "renders/v001/mix.wav"
    raw = (tmp_project.root / path).read_bytes()
    assert client.get(f"/files/{path}").content == raw
    head = client.head(f"/files/{path}")
    assert head.headers["content-type"].startswith("audio/wav")
    assert head.headers["accept-ranges"] == "bytes"
    assert head.content == b""
    response = client.get(f"/files/{path}", headers={"Range": "bytes=12-31"})
    assert response.status_code == 206 and response.content == raw[12:32]
    assert response.headers["content-range"] == f"bytes 12-31/{len(raw)}"
    suffix = client.get(f"/files/{path}", headers={"Range": "bytes=-9"})
    assert suffix.content == raw[-9:]
    assert client.get(f"/files/{path}", headers={"Range": f"bytes={len(raw)}-"}).status_code == 416
    assert client.get("/files/renders/v001/absent.wav").status_code == 404
    # Known extrema verify all frames are covered, including final uneven buckets.
    data = np.array([[0.2, -0.2], [-0.5, 0.5], [0.8, -0.8], [0, 0], [-0.9, 0.9]], dtype=np.float32)
    sf.write(tmp_project.root / "renders/test.wav", data, 8000, subtype="FLOAT")
    peaks = client.get("/waveform", params={"path": "renders/test.wav", "points": 2}).json()
    assert peaks["frames"] == 5 and peaks["points"] == 2
    assert peaks["channels"][0]["min"] == pytest.approx([-0.5, -0.9])
    assert peaks["channels"][0]["max"] == pytest.approx([0.2, 0.8])
    assert peaks["channels"][1]["min"] == pytest.approx([-0.2, -0.8])
    assert client.get("/waveform", params={"path": path, "points": 8193}).status_code == 422
    # Placeholder page when ui/dist is absent, the built UI when it exists.
    home = client.get("/").text
    assert "Your project API is ready" in home or '<div id="root">' in home
    assert client.get("/tools/unknown").status_code == 404
    assert client.get("/docs").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "../song.yaml",
        "renders/../../song.yaml",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "C:relative.wav",
        "renders\\v001\\mix.wav",
        "renders/../sources/private.wav",
        "renders/nul.wav",
        "renders/CON",
        "renders/com1.wav",
        "renders/LPT9.wav",
        "renders/COM¹.wav",
        "renders/dir./a.wav",
        "renders/dir /a.wav",
        "renders//a.wav",
        "renders/./a.wav",
        "renders/a.wav:secret",
        "//server/share.wav",
        "renders/a\x00.wav",
        "sources/original.wav",
        "song.yaml",
        "renders/v001/meta.json",
        "cache/stems/src1/other.wav",
        "renders/a?.wav",
    ],
)
def test_traversal_and_portable_path_rejection(client, path):
    assert client.get("/waveform", params={"path": path}).status_code == 403
    response = client.get("/files/" + quote(path, safe="/"))
    assert response.status_code in (403, 404)


def test_audio_symlink_escape(client, tmp_project, tmp_path):
    outside = tmp_path / "private.wav"
    outside.write_bytes(b"private audio")
    link = tmp_project.root / "renders/escape.wav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks.")
    assert client.get("/files/renders/escape.wav").status_code == 403


def test_built_ui_and_cors(tmp_project, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Listening room</h1>", "utf-8")
    (dist / "asset.js").write_text("console.log('studio')", "utf-8")
    with TestClient(create_app(tmp_project.root, ui_dist=dist)) as client:
        assert "Listening room" in client.get("/").text
        assert "Listening room" in client.get("/compare/v001").text
        assert "studio" in client.get("/asset.js").text
        assert client.get("/missing.js").status_code == 404
        allowed = client.get("/project", headers={"Origin": "http://localhost:5173"})
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
        blocked = client.post(
            "/tools/feedback_append", json={}, headers={"Origin": "https://evil.test"}
        )
        assert blocked.status_code == 403
        assert "access-control-allow-origin" not in blocked.headers


def test_free_port_fallback():
    with bind_socket("127.0.0.1", 0) as first:
        port = first.getsockname()[1]
        with bind_socket("127.0.0.1", port) as second:
            assert second.getsockname()[1] != port
