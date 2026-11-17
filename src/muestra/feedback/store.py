"""feedback.jsonl read/append (spec 7.5).

One JSON object per line, validated against the FeedbackEvent union.
Append is used by the UI/API and ``feedback.append``; read is used by
the agent before every iteration.

Read never crashes on a bad line: the line is skipped and reported in
the result (path + line number + hint). Cursor paging is line-based —
``cursor`` is the number of lines already consumed, ``next_cursor`` the
new count — so ``since`` filtering stays ``>=``-safe: re-querying with
the returned cursor yields exactly the events appended later, with no
timestamp ties.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..project.core import FEEDBACK_FILE, Project
from ..schemas._base import LazyAdapter
from ..schemas.feedback import FeedbackEvent

__all__ = ["EVENT_ADAPTER", "ReadPage", "SkippedLine", "append_event", "read_events"]

EVENT_ADAPTER = LazyAdapter(FeedbackEvent)


@dataclass(frozen=True)
class SkippedLine:
    """A feedback.jsonl line that could not be parsed as an event."""

    path: str
    line: int
    message: str
    hint: str


@dataclass
class ReadPage:
    """One page of feedback events."""

    events: list[FeedbackEvent] = field(default_factory=list)
    next_cursor: int = 0
    skipped: list[SkippedLine] = field(default_factory=list)


def parse_since(since: str | datetime | None) -> datetime | None:
    """Parse a ``since`` filter: ISO-8601 string or datetime.

    The timestamp must be timezone-aware (UTC); a naive value is an
    error with a hint, matching the contract that every timestamp in
    every contract is aware.
    """
    if since is None:
        return None
    if isinstance(since, datetime):
        if since.tzinfo is None:
            raise MuestraError.one(
                "invalid_value",
                "A naive 'since' timestamp is not allowed.",
                hint="Use an aware ISO-8601 timestamp, e.g. '2026-11-18T21:06:00Z'.",
            )
        return since
    try:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as e:
        raise MuestraError.one(
            "invalid_value",
            f"Invalid 'since' timestamp {since!r}.",
            value=since,
            hint="Use an ISO-8601 timestamp, e.g. '2026-11-18T21:06:00Z'.",
        ) from e
    if parsed.tzinfo is None:
        raise MuestraError.one(
            "invalid_value",
            f"The 'since' timestamp {since!r} has no timezone.",
            value=since,
            hint="Use an aware ISO-8601 timestamp, e.g. '2026-11-18T21:06:00Z'.",
        )
    return parsed


def utcnow() -> datetime:
    """Timezone-aware UTC 'now' for events written by muestra itself."""
    return datetime.now(UTC)


#: cross-process append lock (WP13). Separate from the transports'
#: ``cache/locks/feedback.lock`` (held around whole tool calls), so a tool
#: call holding that one can still append in-process.
APPEND_LOCK = "cache/locks/feedback-append.lock"


def append_event(project: Project, event: FeedbackEvent) -> Path:
    """Validate and append one event line to feedback.jsonl (creates the file).

    Atomic across processes on Windows and macOS (WP13): an OS lock
    (portalocker) around one ``os.write`` of the whole line on an
    ``O_APPEND`` descriptor; a file whose last line lacks its newline (a hand
    edit) gets one first, so two events are never glued together."""
    import portalocker

    line = json.dumps(EVENT_ADAPTER.dump_python(event, mode="json"), ensure_ascii=False)
    data = (line + "\n").encode("utf-8")
    path = project.feedback_path
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = project.path(APPEND_LOCK)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(lock, timeout=30):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0))
        try:
            size = os.fstat(fd).st_size
            if size:
                with path.open("rb") as existing:
                    existing.seek(size - 1)
                    if existing.read(1) != b"\n":
                        data = b"\n" + data
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
        finally:
            os.close(fd)
    return path


def read_events(
    project: Project,
    since: str | AwareDatetime | None = None,
    cursor: int | None = None,
) -> ReadPage:
    """Read feedback events, skipping (and reporting) malformed lines.

    * ``since`` — aware ISO-8601 timestamp; events with ``ts >= since``
      are returned.
    * ``cursor`` — skip this many lines before reading; the returned
      ``next_cursor`` (total line count) feeds back for the next page.
      Cursor paging is line-based, so it is exact even when events
      share a timestamp.
    """
    cutoff = parse_since(since)
    if cursor is not None and cursor < 0:
        raise MuestraError.one(
            "invalid_value",
            f"Invalid 'cursor' {cursor}.",
            value=cursor,
            hint="Use the 'next_cursor' value from a previous feedback.read.",
        )
    path = project.feedback_path
    page = ReadPage()
    if not path.is_file():
        return page
    # Only complete lines (ending in "\n") count: a line being written right
    # now by another process is read next time, never skipped (WP13). Split
    # on "\n" only: str.splitlines would also split inside a comment text
    # containing U+2028 and drift the cursor.
    data = path.read_bytes()
    lines = data[: data.rfind(b"\n") + 1].decode("utf-8", errors="replace").split("\n")[:-1]
    if cursor:
        lines = lines[cursor:]
    start_line = (cursor or 0) + 1
    page.next_cursor = (cursor or 0) + len(lines)
    for off, line in enumerate(lines):
        lineno = start_line + off
        line = line.strip()
        if not line:
            continue
        loc = f"{FEEDBACK_FILE} line {lineno}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            page.skipped.append(
                SkippedLine(
                    path=FEEDBACK_FILE,
                    line=lineno,
                    message=f"Invalid JSON: {e}",
                    hint="Fix or remove the line; each line must be one JSON object.",
                )
            )
            continue
        try:
            event = EVENT_ADAPTER.validate_python(raw)
        except ValidationError as e:
            first = issues_from_validation_error(e, path_prefix=loc)
            page.skipped.append(
                SkippedLine(
                    path=FEEDBACK_FILE,
                    line=lineno,
                    message=first[0].message if first else "Invalid event.",
                    hint="Fix or remove the line; it did not match the feedback event contract.",
                )
            )
            continue
        ts = getattr(event, "ts", None)
        if cutoff is not None and ts is not None and ts < cutoff:
            continue
        page.events.append(event)
    return page
