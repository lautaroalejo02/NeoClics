"""feedback.jsonl append/read with 'since'."""

from __future__ import annotations

import pytest

from muestra.errors import MuestraError
from muestra.feedback import append_event, read_events
from muestra.schemas.feedback import CommentEvent, RatingEvent


def test_read_fixture_events(example_project):
    events = read_events(example_project)
    assert len(events) == 4
    assert events[0].type == "rating"
    assert events[-1].type == "chop_choice"


def test_append_and_since(tmp_project):
    base = read_events(tmp_project)
    assert len(base) == 4
    append_event(
        tmp_project,
        RatingEvent.model_validate(
            {"ts": "2026-11-17T21:00:00Z", "version": "v001", "type": "rating", "value": "dislike"}
        ),
    )
    append_event(
        tmp_project,
        CommentEvent.model_validate(
            {
                "ts": "2026-11-17T21:01:00Z",
                "version": "v001",
                "type": "comment",
                "text": "mas filtro",
            }
        ),
    )
    all_events = read_events(tmp_project)
    assert len(all_events) == 6
    new = read_events(tmp_project, since="2026-11-17T20:30:00Z")
    assert len(new) == 2
    assert all(e.ts.isoformat() > "2026-11-17T20:30" for e in new)
    assert read_events(tmp_project, since="2026-11-17T22:00:00Z") == []


def test_since_naive_and_aware(tmp_project):
    # naive 'since' must still compare correctly against tz-aware events
    events = read_events(tmp_project, since="2026-11-17T20:13:30")
    assert [e.type for e in events] == ["chop_choice"]


def test_malformed_line_reports_line_number(tmp_project):
    with tmp_project.feedback_path.open("a", encoding="utf-8") as f:
        f.write('{"ts": "not-a-date", "type": "rating"}\n')
        f.write("{not json}\n")
    with pytest.raises(MuestraError) as exc:
        read_events(tmp_project)
    paths = {i.path for i in exc.value.issues}
    assert any("line 5" in p for p in paths)
    assert any("line 6" in p for p in paths)


def test_append_serializes_alias_and_type(tmp_project):
    append_event(
        tmp_project,
        CommentEvent.model_validate(
            {"ts": "2026-11-17T21:05:00Z", "type": "comment", "version": "v001", "text": "hola"}
        ),
    )
    last = tmp_project.feedback_path.read_text().strip().splitlines()[-1]
    import json

    assert json.loads(last)["type"] == "comment"
