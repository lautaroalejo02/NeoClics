"""Base types for the section-8 tool interface.

Every tool is a typed Python function ``fn(params: XxxParams) ->
XxxResult`` registered in ``muestra.registry.REGISTRY``. WP7 generates
the MCP tools, CLI commands and API routes mechanically from that
registry, so this file defines the two conventions everything shares:

* params models for project-scoped tools inherit :class:`ProjectScoped`
  (a ``project`` path field);
* result models inherit :class:`ToolOutput` (a ``files`` field listing
  project-relative paths of generated/updated files, which the envelope
  ``ToolResult.files`` mirrors).
"""

from __future__ import annotations

from pydantic import Field

from .schemas._base import ContractModel

__all__ = ["ProjectScoped", "ToolOutput"]


class ProjectScoped(ContractModel):
    """Params base for tools that operate on an open project directory."""

    project: str = Field(min_length=1, description="Path to the project directory.")


class ToolOutput(ContractModel):
    """Result base for tools: ``files`` = generated/updated files,
    project-relative POSIX paths."""

    files: list[str] = Field(default_factory=list)
