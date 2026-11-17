"""Tools owned by WP0: feedback.read / feedback.append."""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, Field

from ..project.core import FEEDBACK_FILE, open_project
from ..schemas._base import ContractModel
from ..schemas.feedback import FeedbackEvent
from ..toolbase import ProjectScoped, ToolOutput
from .store import EVENT_ADAPTER, append_event, read_events, utcnow

__all__ = [
    "FeedbackReadParams",
    "FeedbackReadResult",
    "FeedbackAppendParams",
    "FeedbackAppendResult",
    "SkippedFeedbackLine",
    "feedback_read",
    "feedback_append",
    "TOOLS",
]


class SkippedFeedbackLine(ContractModel):
    """Report of a feedback.jsonl line that could not be parsed."""

    path: str
    line: int
    message: str
    hint: str


class FeedbackReadParams(ProjectScoped):
    since: AwareDatetime | None = Field(
        default=None,
        description="Aware ISO-8601 timestamp; only events with ts >= since "
        "are returned. Omit (and use 'cursor') to page everything.",
    )
    cursor: int | None = Field(
        default=None,
        ge=0,
        description="Line-based cursor: skip this many lines before reading. "
        "Feed back the previous result's 'next_cursor' for exact paging.",
    )


class FeedbackReadResult(ToolOutput):
    events: list[FeedbackEvent] = Field(default_factory=list)
    count: int = 0
    latest_ts: AwareDatetime | None = Field(
        default=None,
        description="ts of the newest returned event.",
    )
    next_cursor: int = Field(
        ge=0,
        description="Line count read so far; feed back as 'cursor' to get "
        "exactly the events appended later (>=-safe, no timestamp ties).",
    )
    skipped: list[SkippedFeedbackLine] = Field(
        default_factory=list,
        description="feedback.jsonl lines that were skipped (path + line + hint).",
    )


def feedback_read(p: FeedbackReadParams) -> FeedbackReadResult:
    """Read feedback.jsonl events (spec 7.5): ``since`` filters by
    timestamp, ``cursor`` pages by line; bad lines are skipped and
    reported, never fatal."""
    project = open_project(p.project)
    page = read_events(project, since=p.since, cursor=p.cursor)
    latest = max((e.ts for e in page.events), default=None)
    return FeedbackReadResult(
        events=page.events,
        count=len(page.events),
        latest_ts=latest,
        next_cursor=page.next_cursor,
        skipped=[
            SkippedFeedbackLine(path=s.path, line=s.line, message=s.message, hint=s.hint)
            for s in page.skipped
        ],
    )


class FeedbackAppendParams(ProjectScoped):
    event: dict[str, Any] = Field(
        description="The event to append (a feedback union object); 'ts' is "
        "filled with aware UTC now when omitted."
    )


class FeedbackAppendResult(ToolOutput):
    appended: int = Field(default=1, ge=0)
    path: str = Field(default=FEEDBACK_FILE)


def feedback_append(p: FeedbackAppendParams) -> FeedbackAppendResult:
    """Append one event to feedback.jsonl (validated against the event
    union; ``ts`` defaults to aware UTC now)."""
    project = open_project(p.project)
    raw = dict(p.event)
    if raw.get("ts") is None:
        raw["ts"] = utcnow().isoformat()
    event = EVENT_ADAPTER.validate_python(raw)
    append_event(project, event)
    return FeedbackAppendResult(appended=1, files=[FEEDBACK_FILE])


#: tools exported by this subpackage (collected by muestra.registry).
TOOLS = [
    (
        "feedback.read",
        feedback_read,
        FeedbackReadParams,
        FeedbackReadResult,
        {"readonly": True, "cli": ("feedback", "read")},
    ),
    (
        "feedback.append",
        feedback_append,
        FeedbackAppendParams,
        FeedbackAppendResult,
        {"cli": ("feedback", "append")},
    ),
]
