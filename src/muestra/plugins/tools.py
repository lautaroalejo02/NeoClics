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
from .discovery import default_search_dirs, discover, resolve_plugin
from .presets import CURATED, instrument_root, surge_presets, vst_presets

#: hard cap on presets returned by one plugins.list call (LLM context budget).
MAX_PRESETS = 100


class PluginsListParams(ProjectScoped):
    query: str | None = Field(
        default=None,
        description="Words that must all appear in the preset name/category, e.g. 'slap bass'.",
    )
    plugin: str | None = Field(
        default=None, description="Only presets of this plugin: 'surge-xt', 'sfz' or a VST name."
    )
    category: str | None = Field(
        default=None, description="Only this preset category (Surge folder), e.g. 'Basses'."
    )
    limit: int = Field(default=30, ge=1, le=MAX_PRESETS, description="Presets per page.")
    offset: int = Field(default=0, ge=0, description="Skip this many matching presets.")
    detail: bool = Field(
        default=False,
        description="Include plugins' parameter names and every preset's file path.",
    )


class PluginEntry(ContractModel):
    path: str
    name: str
    status: str = "unprobed"
    is_instrument: bool | None = None
    is_effect: bool | None = None
    latency_samples: int | None = None
    n_parameters: int | None = Field(
        default=None, description="Parameter count (names only with detail=true)."
    )
    parameters: list[str] = Field(default_factory=list)
    parameter_info: dict = Field(default_factory=dict)
    errors: list[Issue] = Field(default_factory=list)


class PresetEntry(ContractModel):
    name: str = Field(description="What to write in instrument.preset (Surge: 'Category/Name').")
    path: str | None = Field(default=None, description="File path (detail=true only).")
    plugin: str
    category: str | None = None
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
    aliases: list[AliasEntry] = Field(
        description="Curated instruments ready for future funk: use these first."
    )
    plugins: list[PluginEntry]
    preset_counts: dict[str, int] = Field(
        default_factory=dict, description="Presets per plugin (all of them, unfiltered)."
    )
    categories: dict[str, dict[str, int]] = Field(
        default_factory=dict, description="plugin -> category -> preset count."
    )
    presets: list[PresetEntry] = Field(
        default_factory=list,
        description="One page of matching presets; empty unless query/plugin/category is set.",
    )
    matched: int = Field(default=0, description="Presets matching the filters (all pages).")
    next_offset: int | None = Field(default=None, description="offset of the next page, if any.")
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
    """Curated future-funk instrument aliases, installed plugins and preset COUNTS per
    plugin/category. Presets themselves are listed only when browsing with query,
    plugin or category (one page of at most `limit`, hard cap 100; `next_offset`
    continues)."""
    from .core import instrument_spec
    from .sfz import parse_sfz

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
    entries = []
    for path in paths:
        entry = (
            PluginEntry.model_validate(cached[str(path)])
            if str(path) in cached
            else PluginEntry(path=str(path), name=path.stem)
        )
        entry.n_parameters = len(entry.parameters) if entry.parameters else entry.n_parameters
        if not params.detail:
            entry.parameters, entry.parameter_info = [], {}
        entries.append(entry)
    presets = [
        PresetEntry(
            name=f"{p.parent.name}/{p.stem}", path=str(p), plugin="surge-xt", category=p.parent.name
        )
        for p in surge_presets()
    ]
    presets += [
        PresetEntry(name=p.stem, path=str(p), plugin=p.parent.name, category=p.parent.name)
        for p in vst_presets()
    ]
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
                    name=p.stem,
                    path=str(p),
                    plugin="sfz",
                    category=p.parent.name,
                    supported=not errors,
                    errors=errors,
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
    counts: dict[str, int] = {}
    categories: dict[str, dict[str, int]] = {}
    for preset in presets:
        counts[preset.plugin] = counts.get(preset.plugin, 0) + 1
        cats = categories.setdefault(preset.plugin, {})
        cats[preset.category or ""] = cats.get(preset.category or "", 0) + 1
    filtered = bool(params.query or params.plugin or params.category)
    matching = [p for p in presets if _matches(p, params)] if filtered else []
    page = matching[params.offset : params.offset + params.limit]
    if not params.detail:
        page = [p.model_copy(update={"path": None}) for p in page]
    nxt = params.offset + len(page)
    if not filtered:
        hint = (
            f"{len(presets)} presets not listed: use an alias, or browse with "
            "plugin='surge-xt' and category='Basses' (see categories), or query='slap bass'."
        )
    elif not matching:
        hint = "No preset matches; loosen query or check plugin/category names in categories."
    elif nxt < len(matching):
        hint = f"Showing {len(page)} of {len(matching)}; pass offset={nxt} for more, or narrow."
    else:
        hint = "Use a preset as instrument: {plugin: surge-xt, preset: '<name>'}."
    return PluginsListResult(
        plugins=entries,
        aliases=aliases,
        preset_counts=counts,
        categories=categories,
        presets=page,
        matched=len(matching),
        next_offset=nxt if filtered and nxt < len(matching) else None,
        search_dirs=project.config.plugins.search_dirs + [str(p) for p in default_search_dirs()],
        hint=hint,
    )


def _matches(preset: PresetEntry, params: PluginsListParams) -> bool:
    if params.plugin and preset.plugin.casefold() != params.plugin.casefold():
        return False
    if params.category and (preset.category or "").casefold() != params.category.casefold():
        return False
    if params.query:
        hay = f"{preset.name} {preset.category or ''}".casefold().replace("_", " ")
        return all(word in hay for word in params.query.casefold().split())
    return True


def plugins_scan(params: PluginsScanParams) -> PluginsScanResult:
    """Probe plugins in disposable subprocesses; report crashes/timeouts with fix hints."""
    from .host import PluginHost
    from .sfz import parse_sfz

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
