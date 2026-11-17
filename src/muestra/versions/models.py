"""Result models of the versions tools (re-exported by ``versions.tools``)."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_serializer

from ..schemas._base import ContractModel, VersionStr
from ..schemas.metrics import Metrics, MetricsDelta
from ..schemas.render_meta import DiffEntry, RenderMeta
from ..toolbase import ToolOutput

__all__ = [
    "DiffItem",
    "DiskUsage",
    "PruneEntry",
    "VersionsPruneResult",
    "RenderMetaOut",
    "VersionFeedback",
    "VersionInfo",
    "VersionsDiffResult",
    "VersionsGetResult",
    "VersionsListResult",
    "VersionsRestoreResult",
]


class DiffItem(DiffEntry):
    """A :class:`DiffEntry` that serialises its ``from_`` field as ``from``
    (the contract's JSON name) even without ``by_alias=True``, which is how the
    tool envelope dumps results."""

    @model_serializer(mode="wrap")
    def _from_alias(self, handler):
        data = handler(self)
        if "from_" in data:
            data["from"] = data.pop("from_")
        return data


class RenderMetaOut(RenderMeta):
    """meta.json as returned by versions.get (``diff`` entries use ``from``)."""

    diff: list[DiffItem] = Field(default_factory=list)


class VersionFeedback(ContractModel):
    """What the listener said about a version (from feedback.jsonl)."""

    rating: Literal["like", "dislike"] | None = Field(
        default=None, description="Latest like/dislike for this version."
    )
    rated_at: AwareDatetime | None = None
    comments: int = Field(default=0, ge=0, description="Comments left on this version.")
    ab_wins: int = Field(default=0, ge=0, description="A/B comparisons this version won.")
    ab_losses: int = Field(default=0, ge=0, description="A/B comparisons this version lost.")


class DiskUsage(ContractModel):
    """Disk used by derived data (WP13)."""

    renders_bytes: int = 0
    cache_render_bytes: int = Field(default=0, description="cache/render (disposable).")
    previews_bytes: int = Field(default=0, description="cache/previews + cache/drafts.")
    stems_bytes: int = Field(default=0, description="cache/stems (needed to re-render).")
    total_bytes: int = 0
    hint: str | None = None


class PruneEntry(ContractModel):
    version: VersionStr
    bytes: int = 0
    reasons: list[str] = Field(default_factory=list, description="Why it is kept.")


class VersionsPruneResult(ToolOutput):
    dry_run: bool
    remove: list[PruneEntry] = Field(
        default_factory=list, description="renders/ folders removed (or that would be)."
    )
    keep: list[PruneEntry] = Field(default_factory=list, description="Kept, with reasons.")
    freed_bytes: int = Field(default=0, description="Version folders (actual or would-be).")
    cache_files: int = Field(default=0, description="Cache/preview files removed (or would be).")
    cache_freed_bytes: int = 0
    disk: DiskUsage | None = None
    hint: str | None = None


class VersionInfo(ContractModel):
    """One coherent record per version: git + renders/ + feedback merged."""

    version: VersionStr
    parent: VersionStr | None = Field(
        default=None, description="Version this one descends from (history is a tree)."
    )
    children: list[VersionStr] = Field(
        default_factory=list, description="Versions whose parent is this one (oldest first)."
    )
    created_at: AwareDatetime | None = Field(
        default=None, description="Render time (meta.json) else the commit time."
    )
    request: str | None = Field(default=None, description="The natural-language request.")
    summary: str | None = None
    agent: str | None = None
    git_commit: str | None = Field(default=None, description="Version commit sha.")
    liked: bool | None = Field(default=None, description="Latest rating for this version, if any.")
    feedback: VersionFeedback = Field(default_factory=VersionFeedback)
    rendered: bool = Field(
        default=False, description="renders/<v>/mix.wav exists (audio can be played)."
    )
    rerenderable: bool = Field(
        default=False,
        description="No audio on disk (renders/ deleted) but the song is in git: "
        "'render' with version=<v> regenerates it.",
    )
    has_meta: bool = False
    has_metrics: bool = False
    has_stems: bool = False
    is_base: bool = Field(
        default=False,
        description="The version the working copy descends from: the next render's parent.",
    )
    size_bytes: int = Field(default=0, ge=0, description="Disk used by renders/<v>/ (WP13).")
    warnings: list[str] = Field(default_factory=list)


class VersionsListResult(ToolOutput):
    versions: list[VersionInfo] = Field(
        default_factory=list, description="Newest first (by version number)."
    )
    roots: list[VersionStr] = Field(
        default_factory=list,
        description="Versions without a (known) parent: the tree's roots, oldest first. "
        "Draw the tree from roots + each version's 'children'.",
    )
    base: VersionStr | None = Field(
        default=None, description="Parent the next render will record (see VersionInfo.is_base)."
    )
    disk: DiskUsage | None = Field(
        default=None, description="Bytes used by renders/ and caches (WP13); hint suggests prune."
    )


class VersionsGetResult(ToolOutput):
    version: VersionStr
    meta: RenderMetaOut | None = Field(
        default=None,
        description="renders/<version>/meta.json when it exists (derived data — git-ignored).",
    )
    song_yaml: str | None = Field(
        default=None,
        description="The version's song.yaml text (detail=true only): renders/<version>/"
        "song.yaml byte copy when present, else 'git show vNNN:song.yaml'.",
    )
    song_source: Literal["renders", "git"] = Field(
        default="renders", description="Where song_yaml was read from."
    )
    info: VersionInfo | None = None
    metrics: Metrics | None = Field(
        default=None, description="Full ears report (detail=true only; see metrics_path)."
    )
    metrics_summary: str | None = Field(
        default=None, description="Compact Spanish ears summary (headline + worst alarms)."
    )
    metrics_path: str | None = Field(
        default=None, description="renders/<version>/metrics.json when it exists."
    )
    mix: str | None = Field(
        default=None, description="Project-relative path of mix.wav if present."
    )
    stems: list[str] = Field(
        default_factory=list, description="Project-relative paths of the stems present on disk."
    )


class VersionsDiffResult(ToolOutput):
    a: VersionStr
    b: VersionStr
    diff: list[DiffItem] = Field(
        default_factory=list,
        description="Changed fields; ID-based song.yaml paths "
        "(chops.c1.warp.pitch_semitones, tracks.drums.pattern.lanes.kick.steps, "
        "tracks.<id>.clips[<i>], fx[<i>] for id-less fx).",
    )
    musical_summary: str | None = Field(
        default=None, description="Human-readable musical diff in Spanish (WP5)."
    )
    changes: list[str] = Field(
        default_factory=list,
        description="The musical diff as separate Spanish sentences (one bullet each).",
    )
    metrics_delta: list[str] = Field(
        default_factory=list,
        description="Spanish phrases on how the metrics moved; empty unless both versions "
        "have a metrics.json.",
    )
    metrics_compare: MetricsDelta | None = Field(
        default=None,
        description="Structured ears comparison (loudness, brightness, low end, per-track "
        "levels, alarms fixed/introduced/kept); null unless both versions have metrics.",
    )
    identical: bool = Field(
        default=False, description="True when the two songs have no structural difference."
    )


class VersionsRestoreResult(ToolOutput):
    version: VersionStr = Field(description="The version now in the working copy.")
    restored_from: VersionStr = Field(description="The version whose content was restored.")
    changed: bool = Field(
        default=True, description="False when song.yaml/midi already matched that version."
    )
    snapshot_source: Literal["renders", "git"] = "renders"
    previous_commit: str | None = Field(
        default=None, description="HEAD before the restore: your history is untouched."
    )
    snapshot_commit: str | None = Field(
        default=None,
        description="Commit holding your uncommitted song.yaml/midi edits, if there were any.",
    )
    restore_commit: str | None = None
    undo: str | None = Field(default=None, description="Command that undoes this restore.")
    hint: str | None = Field(default=None, description="What happened and how to undo / continue.")
    warnings: list[str] = Field(default_factory=list)
