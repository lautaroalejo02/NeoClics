"""Starter drum kits (WP4): named lane -> sample maps an agent can drop
straight into a ``song.yaml`` drum pattern.

The manifest ``starter-kits.json`` lives inside the library root
(:data:`~muestra.schemas.library.STARTER_KITS_FILE`). A template with the
kits curated for the owner's library ships inside this package
(``muestra/library/data/starter-kits.json``) and is used as a fallback so
``library.kits`` works before anyone copies it next to the samples; kits
whose files are missing from the library are reported unavailable, never
silently dropped.

Nobody has listened to the curated kits: members were chosen from
measured features and source reputation (see docs/library.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..errors import MuestraError
from ..schemas._base import LIBRARY_SCHEME
from ..schemas.library import STARTER_KITS_FILE, StarterKit, StarterKits
from .core import loaded_index, root_label
from .search import describe

if TYPE_CHECKING:
    from ..project.core import Project

__all__ = [
    "BUNDLED_KITS_FILE",
    "DEFAULT_STEPS",
    "KitLaneInfo",
    "KitReport",
    "kit_pattern",
    "pattern_names",
    "kit_reports",
    "load_manifest",
]

#: the template shipped with the package.
BUNDLED_KITS_FILE = Path(__file__).parent / "data" / STARTER_KITS_FILE

#: fallback 'basic' steps per lane name, for a kit without named patterns:
#: four-on-the-floor, backbeat, off-beat open hat, light closed hats. Lanes
#: not listed (crash, toms, percussion) are left out: a crash on every bar or
#: a cowbell all the time is not a default (WP12).
DEFAULT_STEPS: dict[str, str] = {
    "kick": "x...x...x...x...",
    "clap": "....x.......x...",
    "snare": "....x.......x...",
    "rim": "....x.......x...",
    "chat": "o...o...o...o...",
    "hat": "o...o...o...o...",
    "ohat": "..x...x...x...x.",
}

DEFAULT_PATTERN = "basic"

#: lane name -> index ``type`` values that make sense there.
_LANE_TYPES: dict[str, set[str]] = {
    "kick": {"kick"},
    "clap": {"clap"},
    "snare": {"snare", "rim"},
    "rim": {"rim", "snare"},
    "chat": {"chat", "hat"},
    "ohat": {"ohat", "hat"},
    "hat": {"hat", "chat", "ohat"},
    "tom": {"tom"},
    "crash": {"cym"},
}


@dataclass
class KitLaneInfo:
    """One lane of a kit, with what the index knows about its sample."""

    lane: str
    uri: str
    present: bool
    summary: str | None = None
    type: str | None = None


@dataclass
class KitReport:
    """A kit resolved against a library root."""

    name: str
    kit: StarterKit
    source: str
    root: str
    lanes: list[KitLaneInfo]
    warnings: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return all(lane.present for lane in self.lanes)


def _read_manifest(path: Path) -> StarterKits:
    try:
        return StarterKits.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        raise MuestraError.one(
            "parse_error",
            f"Cannot read starter kits from {path}: {e}",
            hint="Fix or delete the manifest; the bundled template is used when it is absent.",
        ) from e


def load_manifest(root: Path) -> tuple[StarterKits, str] | None:
    """The kits for a library root: its own manifest, else the bundled template.

    Returns ``(kits, source)`` with source ``'<root>/starter-kits.json'`` or
    ``'bundled template'``; None when neither exists.
    """
    own = root / STARTER_KITS_FILE
    if own.is_file():
        return _read_manifest(own), str(own)
    if BUNDLED_KITS_FILE.is_file():
        return _read_manifest(BUNDLED_KITS_FILE), "bundled template"
    return None


def _regrid(steps: str, steps_per_bar: int) -> str:
    """16-steps-per-bar string -> ``steps_per_bar`` keeping each hit's musical time."""
    steps = steps.replace(" ", "")
    if steps_per_bar == 16:
        return steps
    if steps_per_bar % 16 == 0:
        pad = "." * (steps_per_bar // 16 - 1)
        return "".join(c + pad for c in steps)
    if 16 % steps_per_bar == 0:
        k = 16 // steps_per_bar
        return "".join(steps[i] for i in range(0, len(steps), k))  # off-grid hits dropped
    raise MuestraError.one(
        "invalid_value",
        f"steps_per_bar {steps_per_bar} cannot express the 16-step starter patterns.",
        path="steps_per_bar",
        hint="Use 16 (or 8, 32, 48, 64).",
    )


def pattern_names(kit: StarterKit) -> list[str]:
    return list(kit.patterns) or [DEFAULT_PATTERN]


def kit_pattern(kit: StarterKit, *, steps_per_bar: int = 16, name: str = DEFAULT_PATTERN) -> dict:
    """A ``pattern:`` mapping ready to paste under a drum track in song.yaml.

    ``name`` picks one of the kit's named patterns ('basic', 'busy', 'fill',
    'phrase'...); a kit without patterns falls back to :data:`DEFAULT_STEPS`.
    """
    if kit.patterns:
        if name not in kit.patterns:
            raise MuestraError.one(
                "ref_not_found",
                f"The kit has no pattern {name!r}.",
                path="pattern",
                value=name,
                hint=f"Use one of: {', '.join(kit.patterns)}.",
            )
        spec = kit.patterns[name]
        chosen = {lane: steps for lane, steps in spec.steps.items()}
        bars = spec.bars
    elif name != DEFAULT_PATTERN:
        raise MuestraError.one(
            "ref_not_found",
            f"The kit has no pattern {name!r}.",
            path="pattern",
            value=name,
            hint=f"This kit only has {DEFAULT_PATTERN!r}.",
        )
    else:
        chosen = {lane: DEFAULT_STEPS[lane] for lane in kit.lanes if lane in DEFAULT_STEPS}
        bars = 1
    lanes = {
        lane: {
            "sample": f"{LIBRARY_SCHEME}{kit.lanes[lane]}",
            "steps": _regrid(steps, steps_per_bar),
        }
        for lane, steps in chosen.items()
    }
    pattern: dict = {"steps_per_bar": steps_per_bar}
    if bars != 1:
        pattern["bars"] = bars
    pattern["lanes"] = lanes
    return pattern


def _resolve_kit(
    project: Project, name: str, kit: StarterKit, source: str, root: Path
) -> KitReport:
    """One kit resolved against one library root and its index."""
    loaded = loaded_index(root)
    label = root_label(project, root)
    lanes: list[KitLaneInfo] = []
    warnings: list[str] = []
    restrictions: set[str] = set()
    for lane, path in kit.lanes.items():
        present = (root / path).is_file()
        entry = loaded.by_path.get(path) if loaded is not None else None
        summary = describe(entry) if entry is not None else None
        if not present:
            warnings.append(f"lane {lane}: {path} is missing from {label}")
        elif loaded is None:
            warnings.append(f"{label} is not indexed: run library.index")
        elif entry is None:
            warnings.append(f"lane {lane}: {path} is not in the index; run library.index")
        if entry is not None:
            allowed = _LANE_TYPES.get(lane)
            if allowed and entry.type not in allowed:
                warnings.append(f"lane {lane}: sample is typed {entry.type!r}")
            for f in entry.flags:
                if f.startswith(("type-mismatch", "clipping")):
                    warnings.append(f"lane {lane}: {f}")
            lic = loaded.index.kits.get(entry.kit or "") if loaded is not None else None
            if lic is not None:
                restrictions.update(lic.restrictions)
        lanes.append(
            KitLaneInfo(
                lane,
                f"{LIBRARY_SCHEME}{path}",
                present,
                summary,
                entry.type if entry is not None else None,
            )
        )
    return KitReport(name, kit, source, label, lanes, warnings, sorted(restrictions))


def kit_reports(project: Project, name: str | None = None) -> list[KitReport]:
    """Every starter kit, resolved against the library roots and their indexes.

    A root's own ``starter-kits.json`` wins (earlier roots first). Kits from
    the bundled template are resolved against the first root that has all
    their files (else the root that has most of them), so an empty project
    ``library/`` in front of the real library does not hide the kits.
    """
    roots = project.library_dirs()
    reports: list[KitReport] = []
    seen: set[str] = set()
    for root in roots:
        own = root / STARTER_KITS_FILE
        if not own.is_file():
            continue
        for kit_name, kit in _read_manifest(own).kits.items():
            if (name is None or kit_name == name) and kit_name not in seen:
                seen.add(kit_name)
                reports.append(_resolve_kit(project, kit_name, kit, str(own), root))
    if BUNDLED_KITS_FILE.is_file() and roots:
        for kit_name, kit in _read_manifest(BUNDLED_KITS_FILE).kits.items():
            if (name is not None and kit_name != name) or kit_name in seen:
                continue
            seen.add(kit_name)
            candidates = [
                _resolve_kit(project, kit_name, kit, "bundled template", root) for root in roots
            ]
            best = max(candidates, key=lambda r: (r.available, sum(ln.present for ln in r.lanes)))
            reports.append(best)
    if name is not None and not reports:
        raise MuestraError.one(
            "ref_not_found",
            f"No starter kit named {name!r}.",
            value=name,
            hint="Call library.kits without a name to list the available kits.",
        )
    return reports
