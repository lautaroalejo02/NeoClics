"""Small test-only tools, also loaded in subprocess integration tests."""

from __future__ import annotations

import time

from pydantic import Field

from muestra.registry import REGISTRY, ToolSpec
from muestra.toolbase import ProjectScoped, ToolOutput


class FakeParams(ProjectScoped):
    value: int = 1
    delay: float = Field(default=0.02, ge=0, le=2)


class FakeResult(ToolOutput):
    value: int


def echo(params):
    """Return a value to prove new tools appear without transport edits."""
    return FakeResult(value=params.value)


def slow(params, *, progress):
    """Report progress before finishing a fake long-running operation."""
    progress(0.25, "Preparing audio")
    time.sleep(params.delay)
    progress(0.75, "Finishing")
    return FakeResult(value=params.value)


def crash(params):
    raise RuntimeError("PRIVATE_TOKEN_MUST_NOT_LEAK")


def stub(params):
    raise NotImplementedError("PRIVATE_STUB_DETAILS")


def specs():
    return [
        ToolSpec(
            name=f"fake.{action}",
            group="fake",
            func=func,
            params=FakeParams,
            result=FakeResult,
            description=func.__doc__ or action,
            mcp_name=f"test_{action}",
            readonly=action == "echo",
            long_running=action == "slow",
            cli=("future", action),
        )
        for action, func in (("echo", echo), ("slow", slow), ("crash", crash), ("stub", stub))
    ]


def register():
    for spec in specs():
        REGISTRY[spec.name] = spec
