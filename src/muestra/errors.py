"""Shared error type and the tool-result envelope.

Every validation problem in muestra is an :class:`Issue`: a structured,
JSON-serialisable record that tells an agent *which field* is wrong,
*what value* it found, and *how to fix it*. Validation collects all
issues instead of stopping at the first one.

Every section-8 tool returns a :class:`ToolResult` envelope when invoked
through the registry (``muestra.registry.run_tool``). Tool functions
themselves return a typed result model on success and raise
:class:`MuestraError` on failure.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

__all__ = [
    "Issue",
    "MuestraError",
    "ToolResult",
    "issues_from_validation_error",
]


class Issue(BaseModel):
    """One machine-readable problem. Agents consume these to fix files.

    Attributes:
        path: Dotted/bracketed location inside the offending file, e.g.
            ``tracks[1].fx[2].cutoff_hz``. Empty string means the problem
            is about the file (or project) as a whole.
        code: Stable machine-readable code, e.g. ``unknown_field``,
            ``ref_not_found``, ``file_not_found``.
        message: Human-readable description of what is wrong.
        hint: Short actionable fix suggestion, when one is known.
        value: ``repr()`` of the offending value, if there is one.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    code: str
    message: str
    hint: str | None = None
    value: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v: Any) -> Any:
        """Accept any offending value; store its repr (JSON-safe)."""
        if v is None or isinstance(v, str):
            return v
        return _short_repr(v)


class MuestraError(Exception):
    """Raised by tools and validators. Carries a list of issues."""

    def __init__(self, issues: Issue | list[Issue], hint: str | None = None) -> None:
        if isinstance(issues, Issue):
            issues = [issues]
        self.issues: list[Issue] = issues
        self.hint = hint or next((i.hint for i in issues if i.hint), None)
        super().__init__("; ".join(f"{i.path or '<root>'}: {i.message}" for i in issues))

    @classmethod
    def one(
        cls,
        code: str,
        message: str,
        *,
        path: str = "",
        value: Any = None,
        hint: str | None = None,
    ) -> MuestraError:
        """Shortcut for a single-issue error."""
        return cls(
            Issue(
                path=path,
                code=code,
                message=message,
                hint=hint,
                value=None if value is None else _short_repr(value),
            )
        )


class ToolResult(BaseModel):
    """JSON envelope every section-8 tool call produces (spec section 8).

    ``files`` are project-relative POSIX paths of files the tool created
    or updated (copied from the result model's ``files`` field).
    ``hint`` is a short message for the agent when something is off.
    """

    model_config = ConfigDict(extra="forbid")

    tool: str
    ok: bool
    data: dict[str, Any] | None = None
    files: list[str] = []
    errors: list[Issue] = []
    hint: str | None = None


def _short_repr(value: Any, limit: int = 120) -> str:
    r = repr(value)
    return r if len(r) <= limit else r[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# pydantic ValidationError -> list[Issue]
# ---------------------------------------------------------------------------

# Pydantic puts the discriminator *value* in ``loc`` for tagged-union member
# errors (e.g. ('fx', 0, 'lowpass', 'cutoff_hz')). These carry no positional
# information, so they are dropped from the rendered path — but only when
# they directly follow a list index, so dict keys like a lane named "gain"
# are preserved.
_UNION_TAGS = frozenset(
    {
        # fx types
        "highpass",
        "lowpass",
        "sidechain",
        "compressor",
        "limiter",
        "gain",
        "reverb",
        "delay",
        "chorus",
        "phaser",
        "saturation",
        "plugin",
        # feedback event types
        "rating",
        "comment",
        "ab_choice",
        "chop_choice",
    }
)

_TYPE_TO_CODE = {
    "extra_forbidden": "unknown_field",
    "missing": "missing_field",
    "literal_error": "invalid_value",
    "enum": "invalid_value",
    "union": "invalid_value",
    "union_tag_invalid": "invalid_type_tag",
    "union_tag_not_found": "missing_type_tag",
    "greater_than": "too_small",
    "greater_than_equal": "too_small",
    "less_than": "too_large",
    "less_than_equal": "too_large",
    "string_pattern_mismatch": "invalid_format",
    "string_too_short": "too_short",
    "string_too_long": "too_long",
    "too_short": "too_short",
    "too_long": "too_long",
    "model_type": "invalid_type",
    "dataclass_type": "invalid_type",
    "is_instance_of": "invalid_type",
    "is_subclass_of": "invalid_type",
    "callable_type": "invalid_type",
}

_TYPE_HINTS = {
    "extra_forbidden": "Unknown field. Remove it or check the spelling; "
    "unknown fields are errors (spec 7.3).",
    "missing": "Add the required field.",
    "literal_error": "Use one of the allowed values.",
    "union_tag_not_found": "Set 'type' to one of the allowed values.",
    "union_tag_invalid": "Set 'type' to one of the allowed values.",
    "string_pattern_mismatch": "Fix the value so it matches the required format.",
}


def _issue_path(loc: tuple[Any, ...]) -> str:
    parts: list[str] = []
    prev: Any = None
    for part in loc:
        if isinstance(part, str) and part in _UNION_TAGS and isinstance(prev, int):
            prev = part
            continue
        if isinstance(part, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{part}]"
            else:
                parts.append(f"[{part}]")
        else:
            parts.append(str(part))
        prev = part
    return ".".join(parts)


def issues_from_validation_error(exc: ValidationError, *, path_prefix: str = "") -> list[Issue]:
    """Convert a pydantic ValidationError into our Issue list.

    Pydantic already collects every error it can; we reformat each one so
    paths read like ``tracks[1].fx[2].cutoff_hz`` and every issue carries
    the offending value and a fix hint.
    """
    issues: list[Issue] = []
    for err in exc.errors(include_url=False):
        path = _issue_path(err["loc"])
        if path_prefix:
            path = f"{path_prefix}.{path}" if path else path_prefix
        err_type: str = err["type"]
        code = _TYPE_TO_CODE.get(err_type)
        if code is None:
            code = "invalid_type" if err_type.endswith(("_type", "_parsing")) else err_type
        ctx = err.get("ctx") or {}
        hint = ctx.get("hint") if isinstance(ctx.get("hint"), str) else None
        if hint is None:
            hint = _TYPE_HINTS.get(err_type)
        if hint is None:
            if err_type == "literal_error" and ctx.get("expected"):
                hint = f"Use one of: {ctx['expected']}."
            elif err_type == "extra_forbidden":
                hint = (
                    "Unknown field. Remove it or check the spelling; "
                    "unknown fields are errors (spec 7.3)."
                )
            elif err_type in ("greater_than", "greater_than_equal"):
                bound = ctx.get("gt", ctx.get("ge"))
                op = ">" if "gt" in ctx else ">="
                hint = f"Use a value {op} {bound}."
            elif err_type in ("less_than", "less_than_equal"):
                bound = ctx.get("lt", ctx.get("le"))
                op = "<" if "lt" in ctx else "<="
                hint = f"Use a value {op} {bound}."
            elif err_type == "string_pattern_mismatch" and ctx.get("pattern"):
                hint = f"Must match pattern {ctx['pattern']!r}."
            elif err_type.endswith(("_type", "_parsing")):
                hint = "Fix the value's type."
        value = err.get("input")
        issues.append(
            Issue(
                path=path,
                code=code,
                message=err["msg"],
                hint=hint,
                value=None if value is None else _short_repr(value),
            )
        )
    return issues
