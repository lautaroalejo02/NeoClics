"""Generate argparse options from a tool's pydantic params model.

Rules (kept deliberately simple and predictable for agents):

* every model field becomes ``--<field-name>`` (underscores -> dashes);
* scalar types map to native argparse types; ``Literal`` becomes
  ``choices``; ``bool`` becomes a ``--x`` / ``--no-x`` pair;
* everything else (nested models, dicts, lists, tuples, datetimes)
  takes a string which is parsed as JSON when possible and passed
  raw otherwise (datetimes, ids, paths keep working);
* nothing is marked ``required`` at the argparse level: missing or
  invalid params come back as structured ``Issue``s from the tool,
  the same contract agents see over MCP/HTTP;
* ``--params-json`` accepts a JSON object (``-`` reads stdin) that is
  merged UNDER the individual flags (flags win).
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from typing import Any, Literal, Union, get_args, get_origin

from ..api.core import ToolSpec

__all__ = ["add_tool_arguments", "collect_params", "ParamsJsonError"]

_RESERVED = {"project", "params_json", "json", "pretty", "help"}


class ParamsJsonError(Exception):
    pass


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """(base, optional): strip ``X | None`` / ``Optional[X]``."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _is_literal(annotation: Any) -> bool:
    return get_origin(annotation) is Literal


def _is_str(annotation: Any) -> bool:
    """Field accepts a plain string value (str or a Literal of strings)."""
    if annotation is str:
        return True
    if _is_literal(annotation):
        return all(isinstance(a, str) for a in get_args(annotation))
    return False


def _flag_name(field_name: str) -> str:
    name = field_name if field_name not in _RESERVED else f"param_{field_name}"
    return "--" + name.replace("_", "-")


def add_tool_arguments(parser: argparse.ArgumentParser, spec: ToolSpec) -> None:
    """Add one option per params-model field (minus ``project``)."""
    for fname, finfo in spec.params.model_fields.items():
        if fname == "project":
            continue
        dest = f"param__{fname}"
        opt = _flag_name(fname)
        help_text = (finfo.description or "").replace("%", "%%")
        base, _optional = _unwrap_optional(finfo.annotation)
        if base is bool:
            group = parser.add_mutually_exclusive_group()
            group.add_argument(
                opt,
                dest=dest,
                action="store_true",
                default=None,
                help=help_text,
            )
            group.add_argument(
                "--no-" + opt[2:],
                dest=dest,
                action="store_false",
                default=None,
                help=f"Negate {opt}.",
            )
        elif _is_literal(base):
            parser.add_argument(
                opt,
                dest=dest,
                choices=[a if isinstance(a, str) else json.dumps(a) for a in get_args(base)],
                default=None,
                metavar="VALUE",
                help=help_text,
            )
        elif base is int:
            parser.add_argument(opt, dest=dest, type=int, default=None, metavar="N", help=help_text)
        elif base is float:
            parser.add_argument(
                opt, dest=dest, type=float, default=None, metavar="F", help=help_text
            )
        elif _is_str(base):
            parser.add_argument(opt, dest=dest, default=None, metavar="VALUE", help=help_text)
        else:
            parser.add_argument(
                opt,
                dest=dest,
                default=None,
                metavar="JSON",
                help=(help_text + " " if help_text else "")
                + "(JSON value; non-JSON text is passed through)",
            )


def collect_params(spec: ToolSpec, ns: argparse.Namespace) -> dict[str, Any]:
    """Turn a parsed namespace into the tool's params dict.

    ``project`` is handled by the caller (``--project``/env/discovery);
    it is merged here when present on the namespace.
    """
    params: dict[str, Any] = {}
    for fname in spec.params.model_fields:
        if fname == "project":
            continue
        value = getattr(ns, f"param__{fname}", None)
        if value is None:
            continue
        base, _optional = _unwrap_optional(spec.params.model_fields[fname].annotation)
        if isinstance(value, str) and not _is_str(base):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                pass
        params[fname] = value
    project = getattr(ns, "project", None)
    if project and "project" in spec.params.model_fields:
        params["project"] = project
    return params


def read_params_json(raw: str, stdin=None) -> dict[str, Any]:
    """Parse a ``--params-json`` argument (``-`` reads stdin)."""
    if raw == "-":
        raw = (stdin or sys.stdin).read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ParamsJsonError(f"--params-json is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ParamsJsonError("--params-json must be a JSON object.")
    return data
