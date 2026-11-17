"""ffmpeg discovery and decode-failure messages shared by ingest, render and
``muestra doctor`` (WP13).

One resolver everywhere: an explicit override, then ``MUESTRA_FFMPEG``, then
``ffmpeg`` on PATH, then the usual install locations (a winget/brew install
made after the terminal started is not on its PATH yet). A file that cannot
be read is reported as such -- "this file is damaged or not audio" -- and
never blamed on a missing ffmpeg when soundfile reads that format natively.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import MuestraError

#: formats libsndfile decodes itself: a failure there means a bad file.
NATIVE_SUFFIXES = frozenset({".wav", ".wave", ".flac", ".aif", ".aiff", ".aifc", ".ogg", ".mp3"})

FFMPEG_INSTALL_HINT = (
    "Install ffmpeg (Windows: winget install Gyan.FFmpeg; macOS: brew install ffmpeg), "
    "restart your terminal, or set MUESTRA_FFMPEG to its executable path."
)


def known_ffmpeg_locations() -> list[str]:
    """Usual install locations, to find ffmpeg when PATH is stale."""
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


def find_ffmpeg(override: str | None = None, *, known_locations: bool = True) -> str | None:
    executable = override or os.environ.get("MUESTRA_FFMPEG") or shutil.which("ffmpeg")
    if executable:
        return str(executable)
    if known_locations:
        found = known_ffmpeg_locations()
        if found:
            return found[0]
    return None


def require_ffmpeg(override: str | None = None, path: Path | None = None) -> str:
    executable = find_ffmpeg(override)
    if executable:
        return executable
    needed = f" It is needed to read {path.name}." if path is not None else ""
    raise MuestraError.one(
        "ffmpeg_missing",
        f"ffmpeg was not found.{needed}",
        path="ffmpeg",
        hint=FFMPEG_INSTALL_HINT,
    )


def decode_failure(path: Path, detail: object) -> MuestraError:
    text = str(detail).strip().splitlines()[-1] if str(detail).strip() else type(detail).__name__
    return MuestraError.one(
        "decode_error",
        f"Cannot decode {path.name}: the file is damaged or is not audio ({text}).",
        path=str(path),
        value=str(path),
        hint="Check that it plays in a music player; re-export it as WAV or FLAC and use that.",
    )


def run_ffmpeg(path: Path, args: list[str], override: str | None = None, native_error=None):
    """Run ffmpeg decoding ``path``; ``args`` go after ``-i <path>``. Returns
    stdout bytes. Raises ``ffmpeg_missing`` only when ffmpeg is actually
    needed (not for a damaged WAV/FLAC), ``decode_error`` when ffmpeg can't
    read the file, ``ffmpeg_unusable`` when the executable won't run."""
    executable = find_ffmpeg(override)
    if executable is None:
        if path.suffix.lower() in NATIVE_SUFFIXES and native_error is not None:
            raise decode_failure(path, native_error)
        require_ffmpeg(path=path)
    command = [executable, "-v", "error", "-nostdin", "-i", str(path), *args]
    try:
        return subprocess.run(command, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        raise decode_failure(path, exc.stderr.decode("utf-8", "replace") or exc) from exc
    except OSError as exc:
        raise MuestraError.one(
            "ffmpeg_unusable",
            f"ffmpeg at {executable} could not run: {exc}",
            path="ffmpeg",
            value=executable,
            hint="Fix MUESTRA_FFMPEG (it must point at ffmpeg itself) or reinstall ffmpeg.",
        ) from exc
