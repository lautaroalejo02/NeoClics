"""Per-kit licence summaries read from ``<kit>/LICENSE.txt`` (WP4).

The goal is to surface restrictions to the agent, not to give legal
advice: ``restrictions`` are machine-readable flags found by scanning the
licence text for prohibitions, ``summary`` is a condensed paraphrase
(the file's own INTERPRETATION block when present).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..schemas.library import KitLicense

__all__ = ["parse_license"]

_NEGATION = re.compile(
    r"\b(?:not|no|never|don'?t|cannot|can'?t|prohibit\w*|forbid\w*|without)\b", re.IGNORECASE
)

# restriction -> regex of the topic; flagged when a negation is within +-100 chars.
_TOPICS: list[tuple[str, re.Pattern[str]]] = [
    ("no-redistribution", re.compile(r"re-?distribut\w*", re.IGNORECASE)),
    (
        "no-ai-training",
        re.compile(r"machine learning|\bAI\b|artificial intelligence|AI-related", re.IGNORECASE),
    ),
    ("non-commercial", re.compile(r"non-?commercial|personal use only", re.IGNORECASE)),
    ("no-resale", re.compile(r"\bselling\b|\bresale\b|\bsell the samples\b", re.IGNORECASE)),
]


def _restrictions(text: str) -> list[str]:
    found: list[str] = []
    for name, topic in _TOPICS:
        for m in topic.finditer(text):
            window = text[max(0, m.start() - 100) : m.end() + 100]
            if _NEGATION.search(window):
                found.append(name)
                break
    return found


def _summary(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("INTERPRETATION"):
            for follow in lines[i + 1 :]:
                if not follow.strip():
                    break
                out.append(follow.strip().lstrip("- ").strip())
            break
    for line in lines:
        if line.strip().upper().startswith("ADDITIONAL RESTRICTION"):
            out.append(line.strip())
    if not out:
        # fall back to the first non-header lines
        for line in lines:
            s = line.strip().strip('"')
            if s and not re.match(r"^[A-Z ]{3,}:", s):
                out.append(s)
            if sum(len(x) for x in out) > 300:
                break
    return re.sub(r"\s+", " ", " ".join(out)).strip()[:600]


def _source(text: str) -> str | None:
    for key in ("SOURCE URL", "SOURCE PAGE", "SOURCE PACKS", "SOURCE PACK"):
        m = re.search(rf"^{key}\s*:\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def parse_license(kit_dir: Path, kit_rel: str) -> KitLicense | None:
    """Summarise ``<kit_dir>/LICENSE.txt`` (None when absent/unreadable)."""
    path = kit_dir / "LICENSE.txt"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return KitLicense(
        path=kit_rel,
        summary=_summary(text),
        restrictions=_restrictions(text),
        source=_source(text),
    )
