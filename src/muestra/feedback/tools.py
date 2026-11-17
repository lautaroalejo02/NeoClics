"""Tool owned by WP0: feedback.read."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ..project.core import open_project
from ..schemas.feedback import FeedbackEvent
from ..toolbase import ProjectScoped, ToolOutput
from .store import read_events

__all__ = ["FeedbackReadParams", "FeedbackReadResult", "feedback_read"]


class FeedbackReadParams(ProjectScoped):
    since: str | None = Field(
        default=None,
        description="ISO-8601 timestamp; only events with ts > since are "
        "returned. Omit to read everything.",
    )


class FeedbackReadResult(ToolOutput):
    events: list[FeedbackEvent] = Field(default_factory=list)
    count: int = 0
    latest_ts: datetime | None = Field(
        default=None, description="ts of the newest returned event (feed it back as 'since')."
    )


def feedback_read(p: FeedbackReadParams) -> FeedbackReadResult:
    """Read feedback.jsonl events newer than ``since`` (spec 7.5)."""
    project = open_project(p.project)
    events = read_events(project, since=p.since)
    latest = max((e.ts for e in events), default=None)
    return FeedbackReadResult(events=events, count=len(events), latest_ts=latest)
