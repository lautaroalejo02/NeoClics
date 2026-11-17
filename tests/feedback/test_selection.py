"""WP16: a request anchored to an editor selection round-trips through
feedback.append/feedback.read and reads clearly for an agent."""

from __future__ import annotations

from muestra.api.core import compact_result
from muestra.registry import run_tool

REQUEST = {
    "type": "comment",
    "text": "sacá esto",
    "song_revision": "0123456789abcdef",
    "selection": {
        "label": "bombo · compás 7, tiempo 3",
        "track": "drums",
        "lane": "kick",
        "start_beat": 27,
        "end_beat": 28,
        "hits": [{"lane": "kick", "bar": 7, "step": 8, "beat": 27}],
        "paths": ["tracks.drums.pattern.lanes.kick.overrides.7"],
        "excerpt": {"steps": "x...x...x...x..."},
    },
}


def test_selection_request_round_trips_with_a_digest(tmp_path):
    root = run_tool("project.create", {"name": "s", "dir": str(tmp_path)}).data["project_path"]
    assert run_tool("feedback.append", {"project": root, "event": REQUEST}).ok
    r = run_tool("feedback.read", {"project": root})
    assert r.ok
    event = r.data["events"][0]
    assert event["selection"]["hits"][0] == {
        "lane": "kick",
        "bar": 7,
        "step": 8,
        "beat": 27.0,
        "index": None,
    }
    line = r.data["digest"][0]
    assert "PEDIDO SOBRE UNA SELECCIÓN" in line and "bombo · compás 7, tiempo 3" in line
    assert '{"op":"hit","track":"drums","lane":"kick","bar":7,"step":8,"velocity":null}' in line
    text = compact_result(r)
    assert text.splitlines()[1].startswith("feedback: 1 events")
    assert "sacá esto" in text


def test_selection_is_validated(tmp_path):
    root = run_tool("project.create", {"name": "s", "dir": str(tmp_path)}).data["project_path"]
    bad = {
        **REQUEST,
        "selection": {**REQUEST["selection"], "hits": [{"lane": "kick", "bar": 0, "beat": 1}]},
    }
    r = run_tool("feedback.append", {"project": root, "event": bad})
    assert not r.ok
