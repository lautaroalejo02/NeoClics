from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from muestra.api.server import create_app
from muestra.schemas.api import WsEvent


def test_websocket_events_from_external_file_writes(tmp_project):
    root = tmp_project.root
    with TestClient(create_app(root, watch_interval=0.01, debounce=0.03)) as client:
        with client.websocket_connect("/events") as ws:

            def receive(kind):
                event = WsEvent.model_validate(ws.receive_json())
                assert event.type == kind
                return event.data

            # A half-written meta must not produce a version announcement.
            target = root / "renders/v002/meta.json"
            target.parent.mkdir()
            target.write_text('{"version":', encoding="utf-8")
            time.sleep(0.08)
            with (root / "song.yaml").open("a", encoding="utf-8") as file:
                file.write("\n# edited by another MCP process\n")
            receive("song.changed")
            meta = json.loads((root / "renders/v001/meta.json").read_text("utf-8"))
            meta["version"] = "v002"
            target.write_text(json.dumps(meta), "utf-8")
            assert receive("version.created")["version"] == "v002"
            candidate = root / "cache/chops/src1/cs001.json"
            content = json.loads(candidate.read_text("utf-8"))
            content["query"]["n"] = 2
            candidate.write_text(json.dumps(content), "utf-8")
            assert receive("candidates.created") == {"set_id": "cs001", "source_id": "src1"}
            # Partial JSONL is held until its newline; the full event is emitted once.
            line = json.dumps(
                {
                    "type": "comment",
                    "version": "v002",
                    "at_s": 0.5,
                    "text": "human feedback",
                    "ts": "2026-11-17T23:00:00Z",
                }
            )
            with (root / "feedback.jsonl").open("ab") as file:
                file.write(line[:20].encode())
            time.sleep(0.08)
            with (root / "song.yaml").open("a", encoding="utf-8") as file:
                file.write("\n# another edit\n")
            receive("song.changed")
            with (root / "feedback.jsonl").open("ab") as file:
                file.write(line[20:].encode() + b"\n")
            assert receive("feedback.appended")["text"] == "human feedback"
        assert len(client.app.state.bus.subscribers) == 0


def test_metrics_landing_reannounces_the_version_with_metrics(tmp_project):
    """WP11: metrics.json written after meta.json refreshes the UI's Oídos panel."""
    root = tmp_project.root
    metrics = root / "renders/v001/metrics.json"
    saved = metrics.read_bytes()
    metrics.unlink()
    with TestClient(create_app(root, watch_interval=0.01, debounce=0.03)) as client:
        with client.websocket_connect("/events") as ws:
            metrics.write_bytes(saved)
            event = WsEvent.model_validate(ws.receive_json())
            assert event.type == "version.created"
            assert event.data["version"] == "v001"
            assert event.data["metrics"]["version"] == "v001"
