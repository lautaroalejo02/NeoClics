"""Non-invasive readiness checks; never downloads models or loads native plugins.

Every check is ``{name, status, detail, fix}``: ``ok``, ``missing`` (a core
feature is unusable; ``ok`` of the whole report becomes false) or
``optional`` (an optional feature is unavailable). Every non-ok check
carries the one command or action that fixes it.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import socket
import sys
from pathlib import Path

from ..project.core import open_project
from ..project.validate import load_song

REPO = Path(__file__).resolve().parents[3]

#: packages a plain ``uv sync`` installs (everything but stem separation)
CORE_PACKAGES = {
    "fastapi": "HTTP API + UI server",
    "uvicorn": "HTTP API + UI server",
    "mcp": "MCP server for agents",
    "portalocker": "cross-process feedback lock",
    "pedalboard": "effects and plugin hosting",
    "soundfile": "audio files",
    "beat_this": "beat/downbeat tracking (source.analyze)",
    "torch": "beat/downbeat tracking (source.analyze)",
}
#: the optional heavy group (``uv sync --extra separation``)
SEPARATION_PACKAGES = {
    "demucs_infer": "htdemucs stem separation (default model)",
    "audio_separator": "mel-roformer+htdemucs separation model",
}


def _sync_hint(extra: str | None = None) -> str:
    cmd = "uv sync" + (f" --extra {extra}" if extra else "")
    return f'{cmd}   (run in the Muestra checkout: cd "{REPO}")'


def _ffmpeg_candidates() -> list[str]:
    """Known install locations, to tell 'not installed' from 'not on PATH yet'."""
    home = Path.home()
    patterns = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))
        patterns += [
            f"{local}/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin/ffmpeg.exe",
            f"{local}/Microsoft/WinGet/Links/ffmpeg.exe",
            str(home / "scoop/shims/ffmpeg.exe"),
            "C:/ProgramData/chocolatey/bin/ffmpeg.exe",
        ]
    else:
        patterns += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]
    found: list[str] = []
    for pattern in patterns:
        found += [str(Path(p)) for p in sorted(glob.glob(pattern))]
    return found


def _model_files() -> dict[str, tuple[list[Path], str]]:
    home = Path.home()
    torch_home = Path(os.environ.get("TORCH_HOME", home / ".cache/torch"))
    muestra_models = Path(os.environ.get("MUESTRA_MODEL_DIR", home / ".cache/muestra/models"))
    return {
        "beat_this-final0": (
            [torch_home / "hub/checkpoints/beat_this-final0.ckpt"],
            "source.analyze downloads it (~80 MB) on first use",
        ),
        "htdemucs": (
            [*sorted((home / ".cache/demucs-infer").glob("955717e8*.th"))]
            or [home / ".cache/demucs-infer/955717e8-8726e21a.th"],
            "source.separate downloads it (~80 MB) on first use",
        ),
        "mel-roformer": (
            [muestra_models / "vocals_mel_band_roformer.ckpt"],
            "only for model 'mel-roformer+htdemucs'; downloads on first use",
        ),
    }


def doctor(project: Path | None, port: int = 8765) -> dict:
    checks = []

    def check(name, ok, detail, fix="", *, optional=False):
        checks.append(
            {
                "name": name,
                "status": "ok" if ok else "optional" if optional else "missing",
                "detail": detail,
                "fix": "" if ok else fix,
            }
        )

    # ---- executables -------------------------------------------------------
    git = shutil.which("git")
    check(
        "git",
        bool(git),
        git or "git is not on PATH; every project is a git repo and versions are commits.",
        "Install git: Windows `winget install Git.Git`; macOS `xcode-select --install`. "
        "Then open a new terminal.",
    )
    ffmpeg = os.environ.get("MUESTRA_FFMPEG") or shutil.which("ffmpeg")
    try:
        import soundfile as sf

        native = {"WAV", "FLAC", "MP3", "OGG", "AIFF"} & set(sf.available_formats())
    except Exception:  # noqa: BLE001 - doctor must never crash
        native = set()
    elsewhere = [] if ffmpeg else _ffmpeg_candidates()
    if ffmpeg:
        detail = ffmpeg
    elif elsewhere:
        detail = (
            f"ffmpeg is installed at {elsewhere[0]} but is not on this terminal's PATH "
            "(the PATH changed after the terminal started). Needed for m4a/aac imports; "
            f"{'/'.join(sorted(native)).lower()} decode without it."
        )
    else:
        detail = (
            "ffmpeg is not installed. Needed for m4a/aac imports; "
            f"{'/'.join(sorted(native)).lower()} decode without it."
        )
    check(
        "ffmpeg",
        bool(ffmpeg),
        detail,
        (
            f'Open a new terminal, or set MUESTRA_FFMPEG="{elsewhere[0]}".'
            if elsewhere
            else "Install ffmpeg: Windows `winget install Gyan.FFmpeg`; macOS "
            "`brew install ffmpeg`. Then open a new terminal."
        ),
        optional=bool(native),
    )

    # ---- python packages ---------------------------------------------------
    missing_core = [p for p in CORE_PACKAGES if importlib.util.find_spec(p) is None]
    check(
        "python-core",
        not missing_core,
        "Core packages installed (server, MCP, effects, beat tracking)."
        if not missing_core
        else "Missing: " + ", ".join(f"{p} ({CORE_PACKAGES[p]})" for p in missing_core) + ".",
        _sync_hint(),
    )
    missing_sep = [p for p in SEPARATION_PACKAGES if importlib.util.find_spec(p) is None]
    check(
        "python-separation",
        not missing_sep,
        "Stem separation installed (htdemucs, mel-roformer+htdemucs)."
        if not missing_sep
        else "Stem separation is not installed (source.separate will fail; everything else "
        "works). Missing: " + ", ".join(missing_sep) + ".",
        _sync_hint("separation"),
        optional=True,
    )

    # ---- model weights (cached files only; never downloaded here) ----------
    for name, (paths, note) in _model_files().items():
        present = [p for p in paths if p.is_file()]
        check(
            f"model:{name}",
            bool(present),
            str(present[0]) if present else f"Not cached yet ({paths[0]}).",
            f"Nothing to do: {note}.",
            optional=True,
        )

    # ---- UI build ----------------------------------------------------------
    dist = REPO / "ui/dist/index.html"
    check(
        "ui",
        dist.is_file(),
        str(dist.parent) if dist.is_file() else "The listening UI is not built.",
        f'cd "{REPO / "ui"}" && npm ci && npm run build   (needs Node.js 20+)',
    )

    # ---- project -----------------------------------------------------------
    try:
        if project is None:
            raise ValueError("No project selected.")
        opened = open_project(project)
        check("project", True, str(opened.root))
    except Exception as exc:  # noqa: BLE001
        check(
            "project",
            False,
            str(exc),
            "muestra project create --name my-song --dir <folder>, then pass --project "
            "<folder>/my-song (or run inside it).",
            optional=project is None,
        )
        opened = None
    if opened:
        _project_checks(opened, check)

    # ---- port --------------------------------------------------------------
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        check("port", True, f"127.0.0.1:{port} is available.")
    except OSError:
        check(
            "port",
            False,
            f"127.0.0.1:{port} is busy (another `muestra serve`?).",
            "Nothing to do: muestra serve picks a free port and prints it; or pass --port.",
            optional=True,
        )
    return {"ok": not any(c["status"] == "missing" for c in checks), "checks": checks}


def _project_checks(opened, check) -> None:
    index_file = ".muestra-index.json"
    roots = [r for r in opened.library_dirs() if r.is_dir()]
    configured = opened.library_dirs()
    if not roots:
        check(
            "library",
            False,
            "No sample library found (checked "
            + ", ".join(str(r) for r in configured or [Path.home() / "Documents/MuestraLibrary"])
            + ").",
            "Put samples in ~/Documents/MuestraLibrary (or set MUESTRA_LIBRARY), then "
            f'muestra library index --project "{opened.root}"',
            optional=True,
        )
    for root in roots:
        indexed = (root / index_file).is_file()
        has_audio = any(root.rglob("*.wav")) if not indexed else True
        if not indexed and not has_audio:
            continue  # an empty project library/ dir is normal
        check(
            f"library:{root}",
            indexed,
            f"{root} ({'indexed' if indexed else 'not indexed'})",
            f'muestra library index --project "{opened.root}" --path "{root}"',
        )
    from ..errors import MuestraError
    from ..plugins.core import instrument_spec
    from ..plugins.discovery import resolve_plugin

    required = {}
    try:
        song = load_song(opened)
        for track in song.tracks:
            if track.instrument:
                instrument = track.instrument
                required.setdefault(
                    instrument.plugin, lambda i=instrument: _located(instrument_spec(opened, i))
                )
            for fx in track.fx:
                if fx.type == "plugin":
                    required.setdefault(fx.plugin, lambda n=fx.plugin: resolve_plugin(opened, n))
        for fx in song.master.fx:
            if fx.type == "plugin":
                required.setdefault(fx.plugin, lambda n=fx.plugin: resolve_plugin(opened, n))
    except Exception:  # noqa: BLE001
        check(
            "song",
            False,
            "song.yaml cannot be parsed, so its plugin requirements are unknown.",
            f'muestra song validate --project "{opened.root}"',
        )
    for alias, locate in sorted(required.items()):
        try:
            check(f"plugin:{alias}", True, str(locate()))
        except MuestraError as exc:
            check(
                f"plugin:{alias}",
                False,
                exc.issues[0].message,
                exc.hint
                or exc.issues[0].hint
                or f'muestra plugins list --project "{opened.root}" shows usable aliases.',
            )
    if not required:
        check("plugins", True, "No external plugins required by this song.")


def _located(spec) -> object:
    """instrument_spec -> (plugin path | 'sfz', preset path, curated): show the file."""
    plugin, preset, _curated = spec
    return preset if plugin == "sfz" else plugin
