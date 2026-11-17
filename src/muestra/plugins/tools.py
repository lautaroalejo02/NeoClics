"""Machine inventory and bounded native-plugin probes, owned by WP3."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from ..errors import Issue, MuestraError
from ..project.core import open_project
from ..schemas._base import ContractModel
from ..schemas.song import Instrument
from ..toolbase import ProjectScoped, ToolOutput
from .core import instrument_spec
from .discovery import default_search_dirs, discover, resolve_plugin
from .host import PluginHost
from .presets import CURATED, instrument_root, surge_presets, vst_presets
from .sfz import parse_sfz


class PluginsListParams(ProjectScoped):
    pass


class PluginEntry(ContractModel):
    path: str
    name: str
    status: str = "unprobed"
    is_instrument: bool | None = None
    is_effect: bool | None = None
    latency_samples: int | None = None
    parameters: list[str] = Field(default_factory=list)
    parameter_info: dict = Field(default_factory=dict)
    errors: list[Issue] = Field(default_factory=list)


class PresetEntry(ContractModel):
    name: str
    path: str
    plugin: str
    supported: bool | None = None
    errors: list[Issue] = Field(default_factory=list)


class AliasEntry(ContractModel):
    alias: str
    plugin: str
    preset: str
    available: bool
    reason: str
    errors: list[Issue] = Field(default_factory=list)


class PluginsListResult(ToolOutput):
    plugins: list[PluginEntry]
    presets: list[PresetEntry]
    aliases: list[AliasEntry]
    search_dirs: list[str]
    hint: str = "Run plugins.scan to probe native plugins before use."


class PluginsScanParams(ProjectScoped):
    names: list[str] | None = Field(
        default=None, description="Plugin names/aliases/paths; null scans all."
    )
    timeout_s: float = Field(default=120, gt=0, le=600)
    parameters: list[str] = Field(
        default_factory=list, description="Native params to inspect in detail (can be expensive)."
    )


class PluginsScanResult(ToolOutput):
    plugins: list[PluginEntry]


def plugins_list(params: PluginsListParams) -> PluginsListResult:
    """List installed plugins, factory/sample presets and usable future-funk aliases."""
    project = open_project(params.project)
    cached = {}
    cache = project.cache_dir / "plugins/scan.json"
    if cache.is_file():
        try:
            cached = {
                p["path"]: p for p in json.loads(cache.read_text(encoding="utf-8"))["plugins"]
            }
        except (ValueError, KeyError, TypeError):
            pass
    paths = discover(project)
    entries = [
        PluginEntry.model_validate(cached[str(p)])
        if str(p) in cached
        else PluginEntry(path=str(p), name=p.stem)
        for p in paths
    ]
    presets = [
        PresetEntry(name=f"{p.parent.name}/{p.stem}", path=str(p), plugin="surge-xt")
        for p in surge_presets()
    ]
    presets += [PresetEntry(name=p.stem, path=str(p), plugin=p.parent.name) for p in vst_presets()]
    sample_dirs = [instrument_root(), *project.library_dirs()]
    for root in sample_dirs:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.sfz")):
            errors = []
            try:
                parse_sfz(p)
            except MuestraError as e:
                errors = e.issues
            presets.append(
                PresetEntry(
                    name=p.stem, path=str(p), plugin="sfz", supported=not errors, errors=errors
                )
            )
    aliases = []
    for alias, (plugin, preset, reason) in CURATED.items():
        errors = []
        try:
            instrument_spec(project, Instrument(plugin=alias))
        except MuestraError as e:
            errors = e.issues
        aliases.append(
            AliasEntry(
                alias=alias,
                plugin=plugin,
                preset=preset,
                available=not errors,
                reason=reason,
                errors=errors,
            )
        )
    return PluginsListResult(
        plugins=entries,
        presets=presets,
        aliases=aliases,
        search_dirs=project.config.plugins.search_dirs + [str(p) for p in default_search_dirs()],
    )


def plugins_scan(params: PluginsScanParams) -> PluginsScanResult:
    """Probe plugins in disposable subprocesses; report crashes/timeouts with fix hints."""
    project = open_project(params.project)
    entries = []
    names = params.names if params.names is not None else [str(p) for p in discover(project)]
    for name in names:
        path = Path(name)
        try:
            path = resolve_plugin(project, name)
            if path.suffix.lower() == ".sfz":
                parse_sfz(path)
                entry = PluginEntry(
                    path=str(path), name=path.stem, status="ready", is_instrument=True
                )
            else:
                with PluginHost(path, timeout=params.timeout_s) as host:
                    info = host.info
                    details = (
                        host.request(op="parameter_info", names=params.parameters)
                        if params.parameters
                        else {}
                    )
                    entry = PluginEntry(
                        path=str(path), status="ready", parameter_info=details, **info
                    )
        except MuestraError as e:
            entry = PluginEntry(path=str(path), name=path.stem, status="failed", errors=e.issues)
        entries.append(entry)
    result = PluginsScanResult(plugins=entries, files=["cache/plugins/scan.json"])
    cache = project.cache_dir / result.files[0].removeprefix("cache/")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(result.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    return result


TOOLS = [
    (
        "plugins.list",
        plugins_list,
        PluginsListParams,
        PluginsListResult,
        {"readonly": True, "cli": ("plugins", "list")},
    ),
    (
        "plugins.scan",
        plugins_scan,
        PluginsScanParams,
        PluginsScanResult,
        {"long_running": True, "cli": ("plugins", "scan")},
    ),
]
