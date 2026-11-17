"""feedback.jsonl read/append (spec 7.5).

One JSON object per line, validated against the FeedbackEvent union.
Append is used by the UI/API (WP7/WP8); read (with ``since``) is used by
the agent before every iteration.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ..errors import Issue, MuestraError, issues_from_validation_error
from ..project.core import FEEDBACK_FILE, Project
from ..schemas.feedback import FeedbackEvent

__all__ = ["EVENT_ADAPTER", "append_event", "read_events", "parse_since"]

EVENT_ADAPTER = TypeAdapter(FeedbackEvent)


def parse_since(since: str | datetime | None) -> datetime | None:
    """Parse a ``since`` filter: ISO-8601 string or datetime."""
    if since is None or isinstance(since, datetime):
        return since
    try:
        return datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as e:
        raise MuestraError.one(
            "invalid_value",
            f"Invalid 'since' timestamp {since!r}.",
            value=since,
            hint="Use an ISO-8601 timestamp, e.g. '2026-11-18T21:06:00Z'.",
        ) from e


def append_event(project: Project, event: FeedbackEvent) -> Path:
    """Validate and append one event line to feedback.jsonl (creates the file)."""
    line = json.dumps(EVENT_ADAPTER.dump_python(event, mode="json"), ensure_ascii=False)
    path = project.feedback_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return path


def read_events(project: Project, since: str | datetime | None = None) -> list[FeedbackEvent]:
    """Read all events newer than ``since`` (ts > since; None = all).

    A malformed line is a hard error naming the line — the file is the
    UI->agent channel and silent skipping would lose feedback.
    """
    cutoff = parse_since(since)
    path = project.feedback_path
    if not path.is_file():
        return []
    events: list[FeedbackEvent] = []
    issues: list[Issue] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        loc = f"{FEEDBACK_FILE} line {lineno}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            issues.append(
                Issue(
                    path=loc,
                    code="parse_error",
                    message=f"Invalid JSON: {e}",
                    hint="Fix or remove the line; each line must be one JSON object.",
                    value=line[:80],
                )
            )
            continue
        try:
            event = EVENT_ADAPTER.validate_python(raw)
        except ValidationError as e:
            for issue in issues_from_validation_error(e, path_prefix=loc):
                issues.append(issue)
            continue
        ts = getattr(event, "ts", None)
        if cutoff is not None and ts is not None:
            if (cutoff.tzinfo is None) != (ts.tzinfo is None):
                # compare naive with naive, aware with aware
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=ts.tzinfo)
                else:
                    ts = ts.replace(tzinfo=None)
            if ts <= cutoff:
                continue
        events.append(event)
    if issues:
        raise MuestraError(issues)
    return events
