"""Muestra data contracts (spec section 7 + derived contracts).

All models forbid unknown fields. Load with ``model_validate``; file
loading and filesystem-aware validation live in ``muestra.project``.
"""

from ._base import (
    FileRef,
    IdStr,
    KeyStr,
    LaneName,
    LibraryURI,
    RelPath,
    VersionStr,
    is_library_uri,
)
from .analysis import Analysis, KeyEstimate, Section, Stems
from .chops import (
    AgentPick,
    CandidateScore,
    ChopAdjust,
    ChopCandidate,
    ChopCandidateSet,
    ChopQuery,
    ChosenRecord,
    full_candidate_id,
    parse_candidate_id,
)
from .config import ProjectConfig, SeparationConfig
from .feedback import (
    ABChoiceEvent,
    ChopChoiceEvent,
    CommentEvent,
    FeedbackEvent,
    RatingEvent,
)
from .metrics import Metrics
from .render_meta import DiffEntry, RenderMeta
from .song import (
    Automation,
    Chop,
    Clip,
    Fx,
    Instrument,
    Master,
    Pattern,
    PatternLane,
    Song,
    SongMeta,
    Source,
    StemName,
    Track,
    Warp,
)

__all__ = [
    "ABChoiceEvent",
    "AgentPick",
    "Analysis",
    "Automation",
    "CandidateScore",
    "Chop",
    "ChopAdjust",
    "ChopCandidate",
    "ChopCandidateSet",
    "ChopChoiceEvent",
    "ChopQuery",
    "ChosenRecord",
    "Clip",
    "CommentEvent",
    "DiffEntry",
    "FeedbackEvent",
    "FileRef",
    "Fx",
    "IdStr",
    "Instrument",
    "KeyEstimate",
    "KeyStr",
    "LaneName",
    "LibraryURI",
    "Master",
    "Metrics",
    "Pattern",
    "PatternLane",
    "ProjectConfig",
    "RatingEvent",
    "RelPath",
    "RenderMeta",
    "Section",
    "SeparationConfig",
    "Song",
    "SongMeta",
    "Source",
    "StemName",
    "Stems",
    "Track",
    "VersionStr",
    "Warp",
    "full_candidate_id",
    "is_library_uri",
    "parse_candidate_id",
]
