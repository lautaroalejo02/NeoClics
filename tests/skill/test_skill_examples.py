"""The agent skill must not rot: every song.yaml example in it validates.

Fenced blocks in the skill's Markdown are tagged by their info string:

* ```` ```yaml song ````     a complete song.yaml;
* ```` ```yaml fragment ```` top-level song keys merged into ``BASE`` (tracks,
  chops, sources and automations replace the base entry with the same id or
  target, otherwise they are appended; ``meta`` is merged; ``master`` replaced);
* ```` ```diff <chain> ```` an iteration diff: every ``-`` line must exist in
  the chain's current text and is replaced by the following ``+`` lines; the
  chain starts from ``examples/<chain>.yaml`` and must validate after each diff.
  A line ``~ old => new`` replaces every occurrence (e.g. all bass notes).

Untagged ``yaml`` blocks are rejected so nothing escapes validation. Every
``examples/*.yaml`` is a complete song. Validation is the product's own
``validate_song(check_files=False)`` (structure, references, ids, automation
targets, clip/pattern lengths). The audio-level check (files, analysis grid,
render) is ``test_skill_render.py``, opt-in.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from muestra.project import open_project, validate_song

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "muestra"
GUIDE = REPO / "src" / "muestra" / "mcp" / "guide.md"
DOCS = [*sorted(SKILL.rglob("*.md")), GUIDE, REPO / "docs" / "skill.md"]
EXAMPLES = sorted((SKILL / "examples").glob("*.yaml"))

KICK = "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav"
CLAP = "library://kits/essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav"
BASE = {
    "schema_version": 0,
    "meta": {"title": "base", "bpm": 124.0, "key": "Am", "length_bars": 16, "seed": 0},
    "sources": [{"id": "src1", "file": "sources/src1.wav"}],
    "chops": [
        {
            "id": "c1",
            "source": "src1",
            "stem": "other",
            "start_downbeat": 1,
            "length_beats": 8,
            "warp": {"mode": "resample", "pitch_semitones": 0},
        }
    ],
    "tracks": [
        {
            "id": "sample",
            "clips": [{"chop": "c1", "at_bar": 1, "repeat": 8}],
            "fx": [
                {"id": "hp", "type": "highpass", "cutoff_hz": 150},
                {"id": "lp", "type": "lowpass", "cutoff_hz": 18000},
                {
                    "id": "sc",
                    "type": "sidechain",
                    "key_track": "drums",
                    "key_lane": "kick",
                    "depth": 0.5,
                },
            ],
        },
        {
            "id": "drums",
            "pattern": {
                "steps_per_bar": 16,
                "lanes": {
                    "kick": {"sample": KICK, "steps": "x...x...x...x..."},
                    "clap": {"sample": CLAP, "steps": "....x.......x..."},
                },
            },
        },
        {
            "id": "bass",
            "instrument": {"plugin": "finger-bass"},
            "notes": [{"beat": 1.5, "pitch": "A1", "length_beats": 0.4}],
        },
    ],
    "automation": [],
    "master": {"fx": [{"type": "limiter", "ceiling_db": -1.0}], "target_lufs": -12},
}

FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.M | re.S)


def blocks(kind_prefix: str):
    out = []
    for doc in DOCS:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        for m in FENCE.finditer(text):
            info = m.group(1).strip()
            if info.split(" ")[0] == kind_prefix:
                line = text[: m.start()].count("\n") + 1
                out.append(pytest.param(info, m.group(2), id=f"{doc.name}:{line}"))
    return out


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("skill") / "proj"
    shutil.copytree(REPO / "fixtures" / "example-project", root)
    return open_project(root)


def check(project, text: str) -> None:
    project.song_path.write_text(text, encoding="utf-8")
    issues = validate_song(project, check_files=False)
    assert not issues, "\n".join(f"{i.path} [{i.code}] {i.message} | {i.hint}" for i in issues)


def merge(fragment: dict) -> dict:
    song = yaml.safe_load(yaml.safe_dump(BASE))
    for key, value in fragment.items():
        if key == "meta":
            song["meta"].update(value)
        elif key == "master":
            song["master"] = value
        elif key in ("sources", "chops", "tracks", "automation"):
            ident = "target" if key == "automation" else "id"
            for item in value:
                song[key] = [x for x in song[key] if x[ident] != item[ident]] + [item]
        else:
            song[key] = value
    return song


def test_docs_exist():
    for name in [
        "SKILL.md",
        "workflow.md",
        "song-yaml.md",
        "recipes/future-funk.md",
        "styles.md",
        "ears.md",
        "feedback.md",
        "troubleshooting.md",
        "paid-tools.md",
    ]:
        assert (SKILL / name).is_file(), name
    assert EXAMPLES, "no examples"


def test_skill_frontmatter():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = yaml.safe_load(text.split("---\n")[1])
    assert head["name"] == "muestra"
    assert re.fullmatch(r"[a-z0-9-]{1,64}", head["name"])
    assert 50 < len(head["description"]) <= 1024


def test_no_untagged_yaml():
    bad = []
    for doc in DOCS:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        for m in FENCE.finditer(text):
            info = m.group(1).strip().split(" ")
            if info[0] == "yaml" and (len(info) < 2 or info[1] not in ("song", "fragment")):
                bad.append(f"{doc.name}: ```{m.group(1)}")
    assert not bad, bad


def test_relative_links_resolve():
    missing = []
    for doc in DOCS:
        if not doc.is_file() or doc == GUIDE:
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", doc.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            if not (doc.parent / target).exists():
                missing.append(f"{doc.relative_to(REPO)} -> {target}")
    assert not missing, missing


def test_checker_rejects_bad_fragments(project):
    """Guard against a vacuous checker: typical agent mistakes must fail."""
    bad = [
        {"tracks": [{"id": "sample", "clips": [{"chop": "c1", "at_bar": 1, "repeat": 9}]}]},
        {"automation": [{"target": "tracks.sample.fx.nope.cutoff_hz", "points": [[1, 1], [2, 2]]}]},
        {
            "chops": [
                {
                    "id": "c2",
                    "source": "src1",
                    "start_downbeat": 1,
                    "length_beats": 4,
                    "warp": {"mode": "vinyl"},
                }
            ]
        },
        {
            "tracks": [
                {
                    "id": "drums",
                    "pattern": {"lanes": {"kick": {"sample": KICK, "steps": "x...x..."}}},
                }
            ]
        },
    ]
    for fragment in bad:
        with pytest.raises(AssertionError):
            check(project, yaml.safe_dump(merge(fragment)))


@pytest.mark.parametrize("info,body", blocks("yaml"))
def test_yaml_blocks_validate(project, info, body):
    kind = info.split(" ")[1]
    if kind == "song":
        check(project, body)
    else:
        check(project, yaml.safe_dump(merge(yaml.safe_load(body)), allow_unicode=True))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_validate(project, path):
    check(project, path.read_text(encoding="utf-8"))


def apply_diff(text: str, diff: str) -> str:
    lines = diff.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("~ "):  # "~ old => new": replace every occurrence
            old, new = lines[i][2:].split(" => ")
            assert old in text, f"not found in the chain's song: {old}"
            text = text.replace(old, new)
            i += 1
            continue
        if not lines[i].startswith("-"):
            assert lines[i].startswith(("+", " ", "#")) or not lines[i].strip(), lines[i]
            if lines[i].startswith("+"):
                raise AssertionError(f"'+' without a preceding '-': {lines[i]}")
            i += 1
            continue
        old = [lines[i][1:]]
        i += 1
        while i < len(lines) and lines[i].startswith("-"):
            old.append(lines[i][1:])
            i += 1
        new = []
        while i < len(lines) and lines[i].startswith("+"):
            new.append(lines[i][1:])
            i += 1
        chunk = "\n".join(old) + "\n"
        assert chunk in text, f"not found in the chain's song:\n{chunk}"
        text = text.replace(chunk, "\n".join(new) + "\n" if new else "", 1)
    return text


def test_iteration_diffs_apply(project):
    chains: dict[str, str] = {}
    found = 0
    for doc in DOCS:
        if not doc.is_file():
            continue
        for m in FENCE.finditer(doc.read_text(encoding="utf-8")):
            info = m.group(1).strip().split(" ")
            if info[0] != "diff" or len(info) < 2:
                continue
            chain = info[1]
            if chain not in chains:
                chains[chain] = (SKILL / "examples" / f"{chain}.yaml").read_text(encoding="utf-8")
            chains[chain] = apply_diff(chains[chain], m.group(2))
            check(project, chains[chain])
            found += 1
    assert found >= 3, "the recipe needs at least three worked iterations"
