"""Turn a path + audio probe into an index entry's descriptive fields (WP4).

Type comes from file/folder NAMES first (the library's file names are
already normalised ``<type>-<source>-<detail>.wav`` and folders carry
meaning), then the measured audio features VERIFY it: clear
contradictions are flagged (``type-mismatch:...``), never hidden or
silently "corrected". Everything is deterministic; vocabularies and
thresholds are documented in docs/library.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from pydantic import TypeAdapter, ValidationError

from ..schemas._base import LIBRARY_SCHEME, LibraryURI
from ..schemas.library import AudioFeatures
from .audio import AudioProbe

__all__ = [
    "BRIGHTNESS_HZ",
    "DECAY_MS",
    "TYPES",
    "Classification",
    "NameInfo",
    "bpm_relation",
    "classify",
    "descriptor_tags",
    "feature_family",
    "kit_dir",
    "kit_name",
    "parse_name",
]

#: canonical ``type`` vocabulary.
TYPES = (
    "kick",
    "snare",
    "clap",
    "chat",  # closed hat
    "ohat",  # open hat
    "hat",  # hat of unknown openness (pedal, generic)
    "tom",
    "cym",  # cymbals: crash/ride/splash/china/gong
    "rim",  # rimshot / sidestick
    "perc",
    "drums",  # multi-hit drum loop / groove
    "fill",
    "bass",
    "keys",
    "synth",
    "guitar",
    "pad",
    "vox",
    "fx",
    "other",
)

_LOW_BODY = {"kick", "tom", "bass"}

# ---------------------------------------------------------------------------
# name parsing
# ---------------------------------------------------------------------------

#: file-name prefix -> (type, subtype). The library's normalised names use these.
_PREFIX: dict[str, tuple[str, str | None]] = {
    "kick": ("kick", None),
    "snare": ("snare", None),
    "clap": ("clap", None),
    "chat": ("chat", None),
    "ohat": ("ohat", None),
    "hat": ("hat", None),
    "tom": ("tom", None),
    "cym": ("cym", None),
    "crash": ("cym", "crash"),
    "ride": ("cym", "ride"),
    "splash": ("cym", "splash"),
    "china": ("cym", "china"),
    "gong": ("cym", "gong"),
    "rim": ("rim", None),
    "perc": ("perc", None),
    "conga": ("perc", "conga"),
    "cowbell": ("perc", "cowbell"),
    "shaker": ("perc", "shaker"),
    "maracas": ("perc", "maracas"),
    "tamb": ("perc", "tambourine"),
    "bongo": ("perc", "bongo"),
    "block": ("perc", "block"),
    "bell": ("perc", "bell"),
    "guiro": ("perc", "guiro"),
    "claves": ("perc", "claves"),
    "djembe": ("perc", "djembe"),
    "cajon": ("perc", "cajon"),
    "triangle": ("perc", "triangle"),
    "fill": ("fill", None),
}

# Ordered keyword rules applied to the digit-less, '-'-joined name when
# the prefix does not decide the type (``misc-...``) or to refine it
# (open vs closed hat, crash vs ride). First match wins.
# (regex, type, subtype)
_KEYWORDS: list[tuple[re.Pattern[str], str, str | None]] = [
    (
        re.compile(
            r"(?:^|-)(?:ophat|ohat|hho|hhopen|oh|open|openhat|hlfop|halfopen|hfhat|hatopen\w*)(?:-|$)|open-?hat|hat-?open|\w*hat-?open"
        ),
        "ohat",
        None,
    ),
    (
        re.compile(
            r"(?:^|-)(?:clhat|chat|hhc|hhclosed|clsd|closed|closing|closehat|clhh|ch)(?:-|$)|closed-?hat|hat-?closed|clhat|k-close-hat"
        ),
        "chat",
        None,
    ),
    (
        re.compile(r"(?:^|-)(?:pdhat|pedal|pdl|pedalhat)(?:-|$)|hat-foot|hi-hat-foot|foot"),
        "hat",
        "pedal",
    ),
    (
        re.compile(
            r"(?:^|-)(?:hh|hihat|hat|hats|hi-hat|hi)(?:-|$)|hihat|hi-hat|\w+hat(?:-|$)|(?:^|-)hh\w*"
        ),
        "hat",
        None,
    ),
    (re.compile(r"sdst|sidestick|crossstick|side-stick"), "snare", "sidestick"),
    (re.compile(r"brsh|brush"), "snare", "brush"),
    (re.compile(r"sdmod"), "snare", None),
    (re.compile(r"flam"), "snare", "flam"),
    (
        re.compile(
            r"(?:^|-)(?:kick|kik|bd|bdr|basedrum|bassdrum|bass-drum|kickdrum)(?:-|$)|basedrum|bassdrum|bass-drum"
        ),
        "kick",
        None,
    ),
    (re.compile(r"snroff|snareoff|snares-off"), "snare", "snares-off"),
    (re.compile(r"(?:^|-)(?:snare|snr|sd|snareroll|snrbuzz)(?:-|$)|snare"), "snare", None),
    (re.compile(r"(?:^|-)(?:clap|cp|claps|handclap)(?:-|$)|claptrap|clap"), "clap", None),
    (re.compile(r"(?:^|-)(?:rim|rimshot|rs)(?:-|$)|rimshot"), "rim", None),
    (
        re.compile(
            r"(?:^|-)(?:tom|toms|tomhigh|tomlow|tomhi|tomlo|ht|mt|lt)(?:-|$)|(?<!bot)(?<!at)(?<!cus)tom(?!orrow)"
        ),
        "tom",
        None,
    ),
    (re.compile(r"crash|trash"), "cym", "crash"),
    (re.compile(r"(?:^|-)ride(?:-|$)|ride"), "cym", "ride"),
    (re.compile(r"splash"), "cym", "splash"),
    (re.compile(r"china"), "cym", "china"),
    (re.compile(r"gong"), "cym", "gong"),
    (re.compile(r"(?:^|-)(?:cy|cym|cymb|cymbal|cymbals)(?:-|$)|cymbal"), "cym", None),
    (re.compile(r"conga|(?:^|-)cng(?:-|$)"), "perc", "conga"),
    (re.compile(r"cowbell|(?:^|-)(?:cow|cb)(?:-|$)"), "perc", "cowbell"),
    (
        re.compile(r"shaker|(?<!n)shake(?!n)|cabasa|cabassa|shkr|caxixi|rainstick|jingles"),
        "perc",
        "shaker",
    ),
    (re.compile(r"maraca|marcas|(?:^|-)ma(?:-|$)"), "perc", "maracas"),
    (re.compile(r"tamb|pandeiro"), "perc", "tambourine"),
    (re.compile(r"bonga|bongo"), "perc", "bongo"),
    (re.compile(r"timbale|timbal"), "perc", "timbale"),
    (re.compile(r"woodblock|(?:^|-)block(?:-|$)"), "perc", "block"),
    (re.compile(r"guiro"), "perc", "guiro"),
    (re.compile(r"clave|(?:^|-)cl(?:-|$)"), "perc", "claves"),
    (re.compile(r"djembe|djmbe"), "perc", "djembe"),
    (re.compile(r"cajon"), "perc", "cajon"),
    (re.compile(r"triangle"), "perc", "triangle"),
    (re.compile(r"taiko"), "perc", "taiko"),
    (re.compile(r"chime"), "perc", "chimes"),
    (
        re.compile(
            r"tabla|darbuka|dumbek|(?:^|-)udu(?:-|$)|frame-drum|talking-drum|hide-drum|handdrum|stringdrum|beangourd|hand-drum"
        ),
        "perc",
        "hand-drum",
    ),
    (re.compile(r"snap|click"), "perc", "snap"),
    (re.compile(r"stacked-hit|blast|(?:^|-)pop(?:-|$)|slap|smash"), "perc", "hit"),
    (re.compile(r"scratch|scrt|vocoder|glitch|fingers|(?:^|-)lp(?:-|$)"), "fx", None),
    (re.compile(r"(?:^|-)(?:stb|stab|ravestab)(?:-|$)|ravestab"), "synth", "stab"),
    (
        re.compile(
            r"(?:^|-)(?:fx|zap|zapp|zip|riser|impact|sweep|whoosh|noise|laser|robo|spacey|arcade|drop|pwiiiiup|birdzzz|metalic|rachet|ratchet)(?:-|$)|fx|riser|zapp"
        ),
        "fx",
        None,
    ),
    (re.compile(r"(?:^|-)(?:bass|sub|munchbass|acidbass)(?:-|$)|munchbass|bass"), "bass", None),
    (re.compile(r"rhodes|piano|(?:^|-)keys(?:-|$)|organ|wurli|(?:^|-)ep(?:-|$)"), "keys", None),
    (re.compile(r"guitar|(?:^|-)guit(?:-|$)"), "guitar", None),
    (re.compile(r"(?:^|-)pad(?:s)?(?:-|$)|strings?"), "pad", None),
    (re.compile(r"synth|(?:^|-)(?:arp|lead|saw|pluck|stab|chord)(?:-|$)"), "synth", None),
    (re.compile(r"vox|vocal|voice|choir|(?:^|-)vo(?:-|$)"), "vox", None),
    (re.compile(r"perc"), "perc", None),
]

#: folder segment -> (type, subtype), consulted when the name decides nothing.
_FOLDER_TYPE: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"closed-hi-hat"), "chat", None),
    (re.compile(r"open-hi-hat"), "ohat", None),
    (re.compile(r"hi-?hats?|^hats$"), "hat", None),
    (re.compile(r"bass-?drum|basedrums?|^kicks?$|kes-kick|loose-kick"), "kick", None),
    (re.compile(r"snare|gated"), "snare", None),
    (re.compile(r"claps?|claptrap"), "clap", None),
    (re.compile(r"^toms?$|tom-toms"), "tom", None),
    (re.compile(r"cymbal|crashes|^cy"), "cym", None),
    (re.compile(r"rimshot"), "rim", None),
    (re.compile(r"conga"), "perc", "conga"),
    (re.compile(r"djembe"), "perc", "djembe"),
    (re.compile(r"cajon"), "perc", "cajon"),
    (re.compile(r"cowbell"), "perc", "cowbell"),
    (re.compile(r"maracas"), "perc", "maracas"),
    (re.compile(r"claves"), "perc", "claves"),
    (re.compile(r"perc"), "perc", None),
    (re.compile(r"synth"), "synth", None),
]

_LOOP_FOLDER = re.compile(r"loops?$|^beats$|^instruments$|world-perc")
_FILL_FOLDER = re.compile(r"fills?$|^drum-fills$")

#: loop suffix tokens that say what a drum loop contains.
_DRUM_LOOP_FLAVOURS = {
    "kicksnare": "kick+snare",
    "hatclap": "hats+clap",
    "hats": "hats",
    "tops": "tops",
    "nokick": "no-kick",
    "kiksnrtom": "kick+snare+toms",
}

_BPM_TOKEN = re.compile(r"^(\d{2,3})(?:bpm)?$")


@dataclass
class NameInfo:
    """What the path alone says about a file."""

    type: str | None = None
    subtype: str | None = None
    kind: str = "one-shot"  # one-shot | loop | phrase
    type_source: str | None = None  # 'prefix' | 'keyword' | 'folder' | 'loop-context'
    bpms: list[float] = field(default_factory=list)


def kit_dir(rel_path: str) -> str | None:
    """Root-relative directory of the kit (source pack) a file belongs to.

    ``kits/<kit>/...`` (the standard layout) -> ``kits/<kit>``; any other
    top-level directory is itself the kit.
    """
    parts = PurePosixPath(rel_path).parts
    if len(parts) < 2:
        return None
    if parts[0] == "kits" and len(parts) > 2:
        return f"kits/{parts[1]}"
    return parts[0]


def kit_name(rel_path: str) -> str | None:
    """Kit/source-pack name: the directory under ``kits/`` (or the top-level dir)."""
    d = kit_dir(rel_path)
    return PurePosixPath(d).name if d else None


def _tokens(stem: str) -> list[str]:
    return [t for t in re.split(r"[-_\s.]+", stem.lower()) if t]


def _nodigit(tokens: list[str]) -> str:
    """Tokens with digits removed, empties dropped, '-'-joined."""
    out = [re.sub(r"\d+", "", t) for t in tokens]
    return "-".join(t for t in out if t)


def _keyword(text: str) -> tuple[str, str | None] | None:
    for pat, t, st in _KEYWORDS:
        if pat.search(text):
            return t, st
    return None


def _folder_type(folders: list[str]) -> tuple[str, str | None] | None:
    for seg in reversed(folders):
        for pat, t, st in _FOLDER_TYPE:
            if pat.search(seg):
                return t, st
    return None


def _name_bpms(tokens: list[str], folders: list[str]) -> list[float]:
    """BPM values written in the name/folders (60..220 only)."""
    found: list[float] = []
    for tok in tokens + [seg.lower() for seg in folders]:
        m = _BPM_TOKEN.match(tok)
        if m and (tok.endswith("bpm") or len(m.group(1)) >= 2):
            v = float(m.group(1))
            if 60.0 <= v <= 220.0 and v not in found:
                found.append(v)
    return found


def parse_name(rel_path: str) -> NameInfo:
    """Everything the file/folder names say: type, subtype, kind, BPMs."""
    p = PurePosixPath(rel_path)
    stem = p.stem.lower()
    folders = [s.lower() for s in p.parts[:-1]]
    tokens = _tokens(stem)
    flat = _nodigit(tokens)
    prefix = tokens[0] if tokens else ""
    info = NameInfo()

    fill_ctx = any(_FILL_FOLDER.search(f) for f in folders) or prefix == "fill" or "fill" in flat
    loop_ctx = (
        any(_LOOP_FOLDER.search(f) for f in folders)
        or "loop" in tokens
        or any(t.startswith("loop") for t in tokens)
    )
    # fills win over loop folders ('drum-fills/120bpm' is a fill, not a loop)
    if fill_ctx:
        info.kind = "phrase"
        info.type = "fill"
        info.type_source = "loop-context"
        if "roll" in flat:
            info.subtype = "roll"
        elif "glitch" in flat or "chop" in flat:
            info.subtype = "glitch"
    elif loop_ctx:
        info.kind = "loop"
        info.type_source = "loop-context"
        kw = _keyword(flat)
        beat_ctx = any(f == "beats" for f in folders) or "beat" in flat
        if beat_ctx or (prefix in ("kick", "snare", "clap", "tom") and not _is_voice_loop(flat)):
            info.type = "drums"
            sub = next((v for k, v in _DRUM_LOOP_FLAVOURS.items() if k in flat), None)
            info.subtype = sub or "beat"
        elif kw is not None and kw[0] in ("perc",):
            info.type, info.subtype = kw
        elif prefix in _PREFIX and _PREFIX[prefix][0] == "perc":
            info.type, info.subtype = _PREFIX[prefix]
        elif kw is not None:
            info.type, info.subtype = kw
        else:
            info.type = "other"
    else:
        # ---- one-shot: prefix, then keywords, then folders -----------------
        if prefix in _PREFIX:
            info.type, info.subtype = _PREFIX[prefix]
            info.type_source = "prefix"
            refined = _keyword(flat[len(prefix) :]) if flat.startswith(prefix) else _keyword(flat)
            if refined is not None:
                rt, rst = refined
                if info.type in ("hat", "cym") and rt in ("ohat", "chat", "hat", "cym"):
                    if rt in ("ohat", "chat") or (rt == "hat" and rst == "pedal"):
                        info.type = rt if rt != "hat" else "hat"
                    if rst:
                        info.subtype = rst
                elif info.type == "perc" and rt == "perc" and rst and info.subtype is None:
                    info.subtype = rst
                elif info.type in ("snare",) and rst in ("sidestick", "flam", "snares-off"):
                    info.subtype = rst
                elif info.type == "cym" and rt == "cym" and rst:
                    info.subtype = rst
        else:
            kw = _keyword(flat)
            if kw is not None:
                info.type, info.subtype = kw
                info.type_source = "keyword"
            else:
                ft = _folder_type(folders)
                if ft is not None:
                    info.type, info.subtype = ft
                    info.type_source = "folder"

    info.bpms = _name_bpms(tokens, folders) if info.kind != "one-shot" else []
    return info


def _is_voice_loop(flat: str) -> bool:
    """A loop file named after a pitched/percussion voice, not a drum beat."""
    return bool(
        re.search(r"conga|bell|shaker|maracas|guiro|cajon|djembe|handdrum|stringdrum", flat)
    )


# ---------------------------------------------------------------------------
# machine / source tags
# ---------------------------------------------------------------------------

_MACHINE_TAGS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"(?<![a-z\d])(?:tr-?)?909(?!\d)|tr-909|909-kit"), ["909", "roland"]),
    (re.compile(r"(?<![a-z\d])(?:tr-?|e|cassette)?808(?!\d)|tr-808"), ["808", "roland"]),
    (re.compile(r"(?<![a-z\d])(?:tape)?606(?!\d)|dmx606|tr-?606"), ["606", "roland"]),
    (re.compile(r"707|727"), ["707", "roland"]),
    (re.compile(r"tr-?8(?![\d])|roland-tr-8|tr-8"), ["tr8", "roland"]),
    (re.compile(r"tr-?66|tapetr66"), ["tr66", "roland"]),
    (re.compile(r"linn"), ["linn"]),
    (re.compile(r"80s|gated"), ["80s"]),
    (re.compile(r"gated"), ["gated-reverb"]),
    (re.compile(r"dmx"), ["dmx", "oberheim"]),
    (re.compile(r"dx-?200|yamaha"), ["dx200", "yamaha"]),
    (re.compile(r"sp-?1200"), ["sp1200", "sampler"]),
    (re.compile(r"mpc"), ["mpc60", "sampler"]),
    (re.compile(r"tape|valve|cassette|decoclap|dr220"), ["tape"]),
    (
        re.compile(r"goldbaby|tape606|cassette808|dmx606|decoclaptrap|valvedr220|tapetr66|mpcvb"),
        ["goldbaby"],
    ),
    (re.compile(r"legowelt"), ["legowelt", "lofi"]),
    (re.compile(r"(?<![a-z])er-?1(?!\d)|korger1|mr-?16"), ["korg"]),
    (re.compile(r"alesis|hr-?16"), ["alesis"]),
    (re.compile(r"drumbrute"), ["drumbrute", "arturia"]),
    (re.compile(r"nord"), ["nord"]),
    (re.compile(r"jomox|airbase"), ["jomox"]),
    (re.compile(r"vermona|drm1"), ["vermona"]),
    (re.compile(r"kurz"), ["kurzweil"]),
    (re.compile(r"volca"), ["volca"]),
    (
        re.compile(r"acoust|ludwig|tama|sonor|sabian|zildjian|piccolo|loose-kick|kes-kick"),
        ["acoustic"],
    ),
    (re.compile(r"vinyl"), ["vinyl"]),
    (re.compile(r"electro|eleck"), ["electronic"]),
    (re.compile(r"foley"), ["foley"]),
    (re.compile(r"chiptune|chip-tune"), ["chiptune"]),
    (re.compile(r"synthesi[sz]ed"), ["synth"]),
    (re.compile(r"drumbox|drum-box|vintage"), ["vintage"]),
    (
        re.compile(r"hand-drums?|djembe|cajon|conga|darbuka|tabla|udu|world|handdrum|stringdrum"),
        ["hand-percussion"],
    ),
    (re.compile(r"french-house|fhc"), ["french-house"]),
    (re.compile(r"digital"), ["digital"]),
    (re.compile(r"trash"), ["trash"]),
]


def _machine_tags(rel_path: str) -> list[str]:
    text = rel_path.lower()
    tags: list[str] = []
    for pat, ts in _MACHINE_TAGS:
        if pat.search(text):
            tags.extend(ts)
    return tags


# ---------------------------------------------------------------------------
# feature-based verification
# ---------------------------------------------------------------------------


def feature_family(f: AudioFeatures, duration_s: float) -> str | None:
    """Coarse audio family from features alone, or None when ambiguous.

    ``low-thud`` (kick/bass-like), ``bright-noise`` (hat/cymbal-like) or
    ``mid-body`` (snare/clap/tom-like).
    """
    if f.spectral_centroid_hz is None or f.sub is None:
        return None
    low = (f.sub or 0) + (f.low or 0)
    if low >= 0.8 and f.spectral_centroid_hz < 700:
        return "low-thud"
    if f.spectral_centroid_hz >= 4500 and (f.high or 0) + (f.air or 0) >= 0.6:
        return "bright-noise"
    if f.spectral_centroid_hz >= 900:
        return "mid-body"
    return None


def _mismatch(
    name_type: str, f: AudioFeatures, duration_s: float, subtype: str | None = None
) -> str | None:
    """Audio family label when the features CLEARLY contradict the name."""
    fam = feature_family(f, duration_s)
    c = f.spectral_centroid_hz or 0.0
    low = (f.sub or 0) + (f.low or 0)
    if name_type == "kick" and fam == "bright-noise" and low < 0.2:
        return "hat/cymbal-like"
    if name_type in ("hat", "chat", "ohat", "cym") and fam == "low-thud":
        return "kick-like"
    if name_type == "snare" and subtype == "snares-off":
        return None  # a snare drum with the snares off is a low, tom-like thud
    if name_type in ("snare", "clap", "rim"):
        if fam == "low-thud" and c < 300 and duration_s < 1.5:
            return "kick-like"
        if fam == "bright-noise" and c >= 9000 and (f.tonality or 0) < 0.15:
            return "hat-like"
    if name_type == "tom" and c >= 6000:
        return "hat/cymbal-like"
    if name_type == "bass" and c >= 2500:
        return "bright (not a bass)"
    return None


def _guess_type(f: AudioFeatures, duration_s: float) -> str | None:
    """Conservative type guess for files whose names say nothing."""
    fam = feature_family(f, duration_s)
    if fam == "low-thud" and duration_s < 2.5:
        return "kick"
    if fam == "bright-noise":
        if duration_s < 0.4:
            return "chat"
        return "ohat" if duration_s < 1.5 else "cym"
    return None


# ---------------------------------------------------------------------------
# descriptive tags
# ---------------------------------------------------------------------------

#: decay_ms thresholds (short_below, long_above), per type (default last).
DECAY_MS: dict[str, tuple[float, float]] = {
    "kick": (150.0, 450.0),
    "tom": (200.0, 700.0),
    "snare": (120.0, 450.0),
    "clap": (120.0, 450.0),
    "rim": (60.0, 250.0),
    "chat": (70.0, 200.0),
    "ohat": (200.0, 700.0),
    "hat": (120.0, 450.0),
    "cym": (600.0, 2500.0),
    "bass": (200.0, 900.0),
    "perc": (100.0, 500.0),
}
_DECAY_DEFAULT = (120.0, 600.0)

#: spectral centroid (dark_below, bright_above) in Hz, per type (default last).
BRIGHTNESS_HZ: dict[str, tuple[float, float]] = {
    "kick": (110.0, 350.0),
    "tom": (200.0, 800.0),
    "bass": (150.0, 600.0),
    "snare": (1500.0, 3500.0),
    "clap": (1500.0, 3000.0),
    "rim": (1500.0, 4000.0),
    "chat": (5500.0, 9500.0),
    "ohat": (5000.0, 9000.0),
    "hat": (5500.0, 9500.0),
    "cym": (4000.0, 8000.0),
}
_BRIGHT_DEFAULT = (700.0, 4000.0)


def descriptor_tags(
    type_: str | None,
    f: AudioFeatures,
    *,
    kind: str,
    peak_dbfs: float,
    clipping: bool,
    channels: int,
    side_ratio: float,
    sample_rate: int,
) -> list[str]:
    """Plain-language tags derived from measurements (thresholds in docs)."""
    t: list[str] = []
    ty = type_ or "other"
    hit = kind == "one-shot"
    low_body = ty in _LOW_BODY
    if hit and f.decay_ms is not None:
        short_below, long_above = DECAY_MS.get(ty, _DECAY_DEFAULT)
        if f.decay_ms < short_below:
            t.append("short")
        elif f.decay_ms > long_above:
            t.append("long")
    sub = f.sub or 0.0
    body = (f.sub or 0.0) + (f.low or 0.0)
    if hit and f.attack_ms is not None and f.decay_ms is not None:
        quick = f.attack_ms <= 6.0
        if ty in ("kick", "tom", "bass"):
            if f.attack_ms <= 3.0 and f.decay_ms <= 320.0 and body >= 0.5:
                t.append("punchy")
            if (
                f.attack_ms <= 3.0
                and ty != "bass"
                and (f.mid or 0) + (f.high or 0) + (f.air or 0) >= 0.08
            ):
                t.append("clicky")
        elif ty in ("snare", "clap", "rim"):
            if quick and f.decay_ms <= 350.0:
                t.append("punchy")
        if ty in ("kick", "tom") and f.decay_ms > 400.0 and sub >= 0.4:
            t.append("boomy")
    if ty in ("kick", "tom", "bass", "perc", "other") and sub >= 0.8:
        t.append("subby")
    if f.spectral_centroid_hz is not None and (hit or kind == "phrase" or ty not in ("drums",)):
        dark_below, bright_above = BRIGHTNESS_HZ.get(ty, _BRIGHT_DEFAULT)
        if f.spectral_centroid_hz >= bright_above:
            t.append("bright")
        elif f.spectral_centroid_hz <= dark_below:
            t.append("dark")
    if f.tonality is not None:
        if f.tonality <= 0.12:
            t.append("noisy")
        elif f.tonality >= 0.5:
            t.append("tonal")
    if low_body and f.fundamental_hz is not None and f.note:
        t.append("tuned")
    if channels >= 2 and side_ratio >= 0.35:
        t.append("wide")
    elif channels >= 2 and side_ratio >= 0.05:
        t.append("stereo")
    if peak_dbfs >= -0.3:
        t.append("hot")
    elif peak_dbfs <= -12.0:
        t.append("quiet")
    t.append("clipped" if clipping else "clean")
    if sample_rate <= 22050:
        t.append("lofi")
    return t


# ---------------------------------------------------------------------------
# bpm cross-check
# ---------------------------------------------------------------------------


def bpm_relation(a: float, b: float, tol: float = 0.03) -> str | None:
    """How ``b`` relates to ``a``: 'same', 'double', 'half' or None.

    (``double``: b is twice a; ``half``: b is half of a.)
    """
    for rel, mult in (("same", 1.0), ("double", 2.0), ("half", 0.5)):
        if abs(a * mult - b) <= tol * b:
            return rel
    return None


# ---------------------------------------------------------------------------
# the classifier
# ---------------------------------------------------------------------------


@dataclass
class Classification:
    """Every derived field of an index entry (except the measurements)."""

    type: str | None = None
    subtype: str | None = None
    kind: str = "one-shot"
    kit: str | None = None
    tags: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    bpm: float | None = None
    bpm_source: str | None = None
    bpm_confidence: float | None = None
    key: str | None = None
    key_confidence: float | None = None


_URI = TypeAdapter(LibraryURI)


def _uri_ok(rel_path: str) -> bool:
    """Can ``library://<rel_path>`` appear in a song.yaml?"""
    try:
        _URI.validate_python(f"{LIBRARY_SCHEME}{rel_path}")
    except ValidationError:
        return False
    return True


def probe_needs(info: NameInfo) -> tuple[bool, bool]:
    """(want_bpm, want_key) for a name — decides what the probe computes."""
    want_bpm = info.kind != "one-shot"
    want_key = info.kind != "one-shot" and info.type in (
        "bass",
        "keys",
        "synth",
        "guitar",
        "pad",
        "vox",
        "other",
    )
    return want_bpm, want_key


def classify(
    rel_path: str, probe: AudioProbe | None, info: NameInfo | None = None
) -> Classification:
    """Build the descriptive fields for one file.

    ``probe`` may be None (unreadable file): the name still classifies.
    """
    info = info or parse_name(rel_path)
    c = Classification()
    c.kit = kit_name(rel_path)
    c.kind = info.kind
    c.type, c.subtype = info.type, info.subtype
    feats = probe.features if probe else AudioFeatures()
    duration = probe.duration_s if probe else 0.0

    # ---- type: the name decides; features verify or fill gaps --------------
    if probe is not None:
        if info.type is not None and info.kind == "one-shot":
            audio = _mismatch(info.type, feats, duration, info.subtype)
            if audio:
                c.flags.append(f"type-mismatch:name={info.type},audio={audio}")
        elif info.type is None:
            guess = _guess_type(feats, duration) if info.kind == "one-shot" else None
            if guess:
                c.type = guess
                c.flags.append(f"type-inferred:{guess}")
            else:
                c.type = "other"
    if c.type is None:
        c.type = "other"

    if not _uri_ok(rel_path):
        c.flags.append("unusable-uri")  # song.yaml could not reference this file

    # ---- kind from audio when the name is silent -----------------------------
    if (
        probe is not None
        and info.kind == "one-shot"
        and probe.periodic
        and duration >= 4.0
        and (probe.bpm_confidence or 0) >= 0.5
    ):
        c.kind = "loop"

    # ---- bpm: name, cross-checked by detection ---------------------------------
    if c.kind != "one-shot":
        detected = probe.bpm if probe else None
        d_conf = probe.bpm_confidence if probe else None
        named = info.bpms
        if named:
            agree = None
            if detected:
                for cand in named:
                    rel = bpm_relation(cand, detected)
                    if rel:
                        agree = (cand, rel)
                        break
            if agree:
                c.bpm = agree[0]
                c.bpm_source = "name+detected"
                c.bpm_confidence = min(1.0, 0.7 + 0.3 * (d_conf or 0.0))
            elif detected and (d_conf or 0) >= 0.5:
                # name and audio disagree: trust neither silently
                c.bpm = named[0]
                c.bpm_source = "name"
                c.bpm_confidence = 0.4
                c.flags.append(f"bpm-disagreement:name={named[0]:g},detected={detected:g}")
            else:
                c.bpm = named[0]
                c.bpm_source = "name"
                c.bpm_confidence = 0.7
        elif detected and (d_conf or 0) >= 0.5:
            c.bpm = detected
            c.bpm_source = "detected"
            c.bpm_confidence = d_conf
            c.flags.append("bpm-from-audio-only")

    # ---- key ---------------------------------------------------------------------
    if probe is not None and probe.key and (probe.key_confidence or 0) >= 0.2:
        c.key, c.key_confidence = probe.key, probe.key_confidence
        if (c.key_confidence or 0) < 0.45:
            c.flags.append("low-confidence-key")

    # ---- tags --------------------------------------------------------------------
    tags: list[str] = [c.type]
    if c.subtype:
        tags.append(c.subtype)
    tags.append(c.kind if c.kind != "phrase" else "phrase")
    tags.extend(_machine_tags(rel_path))
    if c.kit:
        tags.append(c.kit)
    if probe is not None:
        tags.extend(
            descriptor_tags(
                c.type,
                feats,
                kind=c.kind,
                peak_dbfs=probe.peak_dbfs,
                clipping=probe.clipping,
                channels=probe.channels,
                side_ratio=probe.side_ratio,
                sample_rate=probe.sample_rate,
            )
        )
        if probe.clipping:
            c.flags.append("clipping")
        if probe.peak_dbfs <= -12.0:
            c.flags.append("low-level")
        if probe.duration_s < 0.02:
            c.flags.append("very-short")
    if c.key:
        tags.append(c.key)
    seen: set[str] = set()
    c.tags = [t for t in tags if t and not (t in seen or seen.add(t))]
    return c
