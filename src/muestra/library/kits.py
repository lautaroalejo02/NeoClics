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
    "kit_reports",
    "load_manifest",
]

#: the template shipped with the package.
BUNDLED_KITS_FILE = Path(__file__).parent / "data" / STARTER_KITS_FILE

#: four-on-the-floor friendly default step strings per lane name.
DEFAULT_STEPS: dict[str, str] = {
    "kick": "x...x...x...x...",
    "clap": "....x.......x...",
    "snare": "....x.......x...",
    "rim": "....x.......x...",
    "chat": "x.x.x.x.x.x.x.x.",
    "hat": "x.x.x.x.x.x.x.x.",
    "ohat": "..x...x...x...x.",
    "perc": ".x.x..x..x.x..x.",
    "conga": ".x.x..x..x.x..x.",
    "cowbell": "x..x..x..x..x...",
    "shaker": "xxxxxxxxxxxxxxxx",
    "tom": "................",
    "crash": "x...............",
}

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


def load_manifest(root: Path) -> tuple[StarterKits, str] | None:
    """The kits for a library root: its own manifest, else the bundled template.

    Returns ``(kits, source)`` with source ``'<root>/starter-kits.json'`` or
    ``'bundled template'``; None when neither exists.
    """
    own = root / STARTER_KITS_FILE
    for path, source in ((own, str(own)), (BUNDLED_KITS_FILE, "bundled template")):
        if not path.is_file():
            continue
        try:
            return StarterKits.model_validate(json.loads(path.read_text(encoding="utf-8"))), source
        except (OSError, json.JSONDecodeError, ValidationError) as e:
            raise MuestraError.one(
                "parse_error",
                f"Cannot read starter kits from {path}: {e}",
                hint="Fix or delete the manifest; the bundled template is used when it is absent.",
            ) from e
    return None


def kit_pattern(kit: StarterKit, *, steps_per_bar: int = 16) -> dict:
    """A ``pattern:`` mapping ready to paste under a drum track in song.yaml."""
    lanes = {}
    for lane, path in kit.lanes.items():
        steps = DEFAULT_STEPS.get(lane, "x...............")
        if steps_per_bar != 16:
            steps = (steps * (steps_per_bar // 16 + 1))[:steps_per_bar]
        lanes[lane] = {"sample": f"{LIBRARY_SCHEME}{path}", "steps": steps}
    return {"steps_per_bar": steps_per_bar, "lanes": lanes}


def kit_reports(project: Project, name: str | None = None) -> list[KitReport]:
    """Every starter kit of every library root, resolved against the index."""
    reports: list[KitReport] = []
    seen: set[str] = set()
    for root in project.library_dirs():
        found = load_manifest(root)
        if found is None:
            continue
        manifest, source = found
        loaded = loaded_index(root)
        label = root_label(project, root)
        for kit_name, kit in manifest.kits.items():
            if name is not None and kit_name != name:
                continue
            if kit_name in seen:
                continue
            seen.add(kit_name)
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
            reports.append(
                KitReport(kit_name, kit, source, label, lanes, warnings, sorted(restrictions))
            )
    if name is not None and not reports:
        raise MuestraError.one(
            "ref_not_found",
            f"No starter kit named {name!r}.",
            value=name,
            hint="Call library.kits without a name to list the available kits.",
        )
    return reports
