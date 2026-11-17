from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from muestra.cli.main import build_parser
from muestra.cli.params import collect_params
from muestra.registry import REGISTRY, ToolSpec


class NewParams(BaseModel):
    help: str
    spec: int
    mode: Literal[1, 2]
    enabled: bool = True


def test_new_fields_cannot_overwrite_cli_namespace(monkeypatch):
    spec = ToolSpec(
        "future.new",
        "future",
        lambda p: p,
        NewParams,
        NewParams,
        "New tool",
        "future_new",
        cli=("future", "new"),
    )
    monkeypatch.setitem(REGISTRY, spec.name, spec)
    args = build_parser().parse_args(
        [
            "future",
            "new",
            "--param-help",
            "text",
            "--spec",
            "42",
            "--mode",
            "2",
            "--no-enabled",
            "--json",
        ]
    )
    assert args.spec is spec and args.json is True
    assert NewParams.model_validate(collect_params(spec, args)) == NewParams(
        help="text", spec=42, mode=2, enabled=False
    )
