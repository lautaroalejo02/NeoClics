"""feedback.jsonl append/read: aware 'since', cursor paging, bad lines."""

from __future__ import annotations

import json

import pytest

from muestra.errors import MuestraError
from muestra.feedback import append_event, read_events
from muestra.registry import run_tool
from muestra.schemas.feedback import CommentEvent, RatingEvent


def test_read_fixture_events(example_project):
    page = read_events(example_project)
    assert len(page.events) == 4
    assert page.skipped == []
    assert page.events[0].type == "rating"
    assert page.events[-1].type == "chop_choice"


def test_append_and_since(tmp_project):
    page = read_events(tmp_project)
    assert len(page.events) == 4
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
    assert len(all_events.events) == 6
    new = read_events(tmp_project, since="2026-11-17T20:30:00Z")
    assert len(new.events) == 2
    assert read_events(tmp_project, since="2026-11-17T22:00:00Z").events == []


def test_since_must_be_aware(tmp_project):
    # a naive 'since' is a validation error (every timestamp is aware UTC)
    with pytest.raises(MuestraError) as exc:
        read_events(tmp_project, since="2026-11-17T20:13:30")
    assert exc.value.issues[0].code == "invalid_value"


def test_cursor_paging_is_exact(tmp_project):
    page = read_events(tmp_project)
    assert page.next_cursor == 4
    append_event(
        tmp_project,
        RatingEvent.model_validate(
            {"ts": "2026-11-17T20:12:10Z", "version": "v002", "type": "rating", "value": "like"}
        ),
    )
    # same ts as line 1: the cursor still yields exactly the new line
    page2 = read_events(tmp_project, cursor=page.next_cursor)
    assert len(page2.events) == 1
    assert page2.events[0].version == "v002"
    assert page2.next_cursor == 5


def test_malformed_lines_are_skipped_and_reported(tmp_project):
    with tmp_project.feedback_path.open("a", encoding="utf-8") as f:
        f.write('{"ts": "not-a-date", "type": "rating"}\n')
        f.write("{not json}\n")
    page = read_events(tmp_project)
    assert len(page.events) == 4  # good lines still read
    assert {s.line for s in page.skipped} == {5, 6}
    assert all(s.path == "feedback.jsonl" and s.hint for s in page.skipped)


def test_append_serializes_alias_and_type(tmp_project):
    append_event(
        tmp_project,
        CommentEvent.model_validate(
            {"ts": "2026-11-17T21:05:00Z", "type": "comment", "version": "v001", "text": "hola"}
        ),
    )
    last = tmp_project.feedback_path.read_text().strip().splitlines()[-1]
    assert json.loads(last)["type"] == "comment"


def test_feedback_append_tool(tmp_project):
    r = run_tool(
        "feedback.append",
        {
            "project": str(tmp_project.root),
            "event": {"type": "rating", "version": "v001", "value": "like"},
        },
    )
    assert r.ok, r.errors
    # ts was filled with aware UTC now
    page = read_events(tmp_project)
    assert page.events[-1].ts.tzinfo is not None
    r = run_tool(
        "feedback.read",
        {"project": str(tmp_project.root)},
    )
    assert r.ok and r.data["count"] == 5 and r.data["next_cursor"] == 5
