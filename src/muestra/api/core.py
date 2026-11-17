"""Registry adapters shared by CLI, HTTP and MCP; no transport dependencies."""

from __future__ import annotations

import inspect
import logging
import math
import os
import threading
from collections.abc import Callable
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..errors import Issue, MuestraError, ToolResult, issues_from_validation_error
from ..project.core import is_project_dir
from ..registry import REGISTRY, ToolSpec

LOG_FILE = "cache/logs/muestra.log"
Progress = Callable[[float, str | None], None]


def find_project_dir(start: str | Path | None = None) -> Path | None:
    cur = Path(start or Path.cwd()).expanduser().resolve()
    if cur.is_file():
        cur = cur.parent
    return next((p for p in (cur, *cur.parents) if is_project_dir(p)), None)


def resolve_project(explicit: str | Path | None = None) -> Path | None:
    selected = explicit or os.environ.get("MUESTRA_PROJECT")
    return Path(selected).expanduser().resolve() if selected else find_project_dir()


@lru_cache(maxsize=32)
def project_logger(root: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(f"muestra.runtime.{root}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        if root is None:
            logger.addHandler(logging.NullHandler())
        else:
            path = root / LOG_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8", delay=True)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
    return logger


def failure(name: str, code: str, message: str, hint: str) -> ToolResult:
    return ToolResult(
        tool=name, ok=False, errors=[Issue(code=code, message=message, hint=hint)], hint=hint
    )


def spec_by_mcp_name(name: str) -> ToolSpec:
    spec = next((s for s in REGISTRY.values() if s.mcp_name == name), None)
    if spec is None:
        raise MuestraError.one(
            "unknown_tool", f"Unknown tool {name!r}.", hint="Use muestra tools to list tools."
        )
    return spec


def normalized_progress(value: float) -> float:
    value = float(value)
    return max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0


def invoke_spec(
    spec: ToolSpec,
    params: dict[str, Any] | None = None,
    *,
    progress: Progress | None = None,
    logger: logging.Logger | None = None,
) -> ToolResult:
    """Registry envelope plus progress(fraction_0_to_1, message=None) and crash logging."""
    try:
        parsed = spec.params.model_validate(params or {})
        kwargs = {}
        parameter = inspect.signature(spec.func).parameters.get("progress")
        if parameter is not None and parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            kwargs["progress"] = progress or (lambda fraction, message=None: None)
        result = spec.func(parsed, **kwargs)
        if spec.result is not None:
            result = spec.result.model_validate(result)
        data = result.model_dump(mode="json", by_alias=True) if result is not None else None
        return ToolResult(
            tool=spec.name, ok=True, data=data, files=list(getattr(result, "files", []) or [])
        )
    except ValidationError as exc:
        return ToolResult(
            tool=spec.name,
            ok=False,
            errors=issues_from_validation_error(exc),
            hint="Fix the listed fields and retry.",
        )
    except MuestraError as exc:
        return ToolResult(tool=spec.name, ok=False, errors=exc.issues, hint=exc.hint)
    except NotImplementedError:
        return failure(
            spec.name,
            "not_implemented",
            f"{spec.name} is not implemented yet.",
            "Install an integrated Muestra build containing this tool's work package.",
        )
    except Exception:
        if logger:
            logger.exception("Tool %s failed", spec.name)
            # Release Windows handles so project directories remain movable/deletable.
            for handler in logger.handlers:
                handler.close()
        return failure(
            spec.name,
            "internal_error",
            "The tool could not complete.",
            f"Inspect the project's {LOG_FILE} and retry.",
        )


def tool_description(spec: ToolSpec) -> str:
    doc = inspect.getdoc(spec.func) or spec.description
    return (
        f"{doc}\n"
        "Uses the current project unless project is supplied. Song bars/beats and source "
        "downbeat indices are 1-based; range_bars is [start,end), end exclusive; "
        "clip preview indices are 0-based. Times ending _s are seconds, _ms milliseconds, "
        "_hz Hertz, _db decibels. Paths returned in files are project-relative. "
        "On failure fix errors using their hints. Read feedback before editing song.yaml, "
        "then song_validate, preview, render and inspect metrics/feedback. "
        + (
            "May take minutes; wait for the result and use progress notifications."
            if spec.long_running
            else ""
        )
    )


def input_schema(spec: ToolSpec, *, project_default: bool = False) -> dict[str, Any]:
    schema = spec.params.model_json_schema()
    if project_default and "project" in schema.get("properties", {}):
        schema["required"] = [x for x in schema.get("required", []) if x != "project"]
        schema["properties"]["project"]["description"] += " Omit to use the current project."
    return schema


class Runtime:
    """One selected project per adapter instance. HTTP pins it; MCP may switch it."""

    def __init__(self, project: str | Path | None = None, *, fixed: bool = False):
        self.project = resolve_project(project)
        self.fixed = fixed
        self._lock = threading.RLock()

    def prepare(self, spec: ToolSpec, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params)
        if self.fixed:
            if spec.name == "project.create":
                raise MuestraError.one(
                    "project_locked",
                    "HTTP serves one existing project.",
                    hint="Create projects through MCP or CLI; start another server for it.",
                )
            supplied = params.get("project")
            if spec.name == "project.open":
                supplied = params.get("path", str(self.project))
                params["path"] = supplied
            if isinstance(supplied, str) and Path(supplied).expanduser().resolve() != self.project:
                raise MuestraError.one(
                    "project_locked",
                    "This HTTP process is bound to another project.",
                    hint="Omit project or start muestra serve --project <path>.",
                )
        if "project" in spec.params.model_fields and "project" not in params and self.project:
            params["project"] = str(self.project)
        return params

    def call(
        self,
        spec: ToolSpec,
        params: dict[str, Any] | None = None,
        *,
        progress: Progress | None = None,
    ) -> ToolResult:
        with self._lock if not self.fixed else nullcontext():
            try:
                prepared = self.prepare(spec, params or {})
                spec.params.model_validate(prepared)
            except MuestraError as exc:
                return ToolResult(tool=spec.name, ok=False, errors=exc.issues, hint=exc.hint)
            except ValidationError as exc:
                return ToolResult(
                    tool=spec.name,
                    ok=False,
                    errors=issues_from_validation_error(exc),
                    hint="Fix the listed parameters and retry.",
                )
            root = (
                Path(prepared["project"]).expanduser().resolve()
                if prepared.get("project")
                else self.project
            )
            if spec.name == "project.open" and prepared.get("path"):
                root = Path(prepared["path"]).expanduser().resolve()
            if spec.name == "project.create" and prepared.get("name"):
                root = Path(prepared.get("dir", ".")).expanduser().resolve() / prepared["name"]
            # All transports cooperate on the same OS-backed lock, including readers.
            lock = nullcontext()
            if root and spec.name.startswith("feedback.") and is_project_dir(root):
                import portalocker

                path = root / "cache/locks/feedback.lock"
                path.parent.mkdir(parents=True, exist_ok=True)
                lock = portalocker.Lock(path, timeout=30)
            try:
                with lock:
                    result = invoke_spec(
                        spec,
                        prepared,
                        progress=progress,
                        logger=project_logger(root if root and is_project_dir(root) else None),
                    )
            except Exception:
                result = failure(
                    spec.name,
                    "internal_error",
                    "Project storage is unavailable.",
                    "Check the project path, permissions and available disk space.",
                )
            if result.ok and spec.name in ("project.open", "project.create") and not self.fixed:
                self.project = Path(result.data["project_path"]).resolve()
            return result


def compact_result(result: ToolResult, project: Path | None = None) -> str:
    import json

    location = str(project) if project else "No project selected"
    return f"Project: {location}\n" + json.dumps(
        result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
