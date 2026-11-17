"""Non-invasive readiness checks; never downloads models or loads native plugins."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import socket
from pathlib import Path

from ..project.core import open_project
from ..project.validate import load_song


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

    for executable, fix in (
        (
            "ffmpeg",
            "Install ffmpeg: Windows winget install Gyan.FFmpeg; macOS brew install ffmpeg. "
            "Restart the terminal so ffmpeg is on PATH.",
        ),
        ("git", "Install git: Windows winget install Git.Git; macOS xcode-select --install."),
    ):
        found = shutil.which(executable)
        check(executable, bool(found), found or f"{executable} is absent from PATH.", fix)
    for package in ("fastapi", "uvicorn", "mcp", "portalocker"):
        check(
            package,
            importlib.util.find_spec(package) is not None,
            f"Python package {package}",
            "Run uv sync --extra server in the Muestra repository.",
        )
    try:
        if project is None:
            raise ValueError("No project selected.")
        opened = open_project(project)
        check("project", True, str(project))
    except Exception as exc:
        check(
            "project",
            False,
            str(exc),
            "Run muestra project create --name my-song, then pass --project <my-song>.",
        )
        opened = None
    if opened:
        roots = [
            Path(p) if Path(p).is_absolute() else opened.root / p
            for p in opened.config.library.roots
        ]
        for root in roots:
            check(
                f"library:{root}", root.is_dir(), str(root), f"Create the library directory {root}."
            )
            check(
                f"library-index:{root}",
                (root / ".muestra-index.json").is_file(),
                str(root),
                f'muestra library index --project "{project}" --path "{root}"',
            )
        if not roots:
            check(
                "library",
                False,
                "No library roots configured.",
                'Add [library] roots = ["library"] to muestra.toml.',
            )
        os_key = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
        required = set()
        try:
            song = load_song(opened)
            for track in song.tracks:
                if track.instrument:
                    required.add(track.instrument.plugin)
                required.update(fx.plugin for fx in track.fx if fx.type == "plugin")
            required.update(fx.plugin for fx in song.master.fx if fx.type == "plugin")
        except Exception:
            check(
                "song",
                False,
                "Cannot inspect song plugin requirements.",
                "Run muestra song validate.",
            )
        for alias in sorted(required | set(opened.config.plugins.aliases)):
            entry = opened.config.plugins.aliases.get(alias)
            location = getattr(entry, os_key, None) if entry else None
            check(
                f"plugin:{alias}",
                bool(location and Path(location).expanduser().exists()),
                location or f"No {os_key} alias configured.",
                f"Install {alias} and set [plugins.{alias}] {os_key} to its installed plugin path.",
            )
        if not required and not opened.config.plugins.aliases:
            check("plugins", True, "No external plugins required by this song.")
        model = opened.config.separation.model
        locations = [opened.root / "cache/models", Path.home() / ".cache/audio-separator-models"]
        found = bool(model and any((root / model).is_file() for root in locations))
        check(
            "models",
            found,
            f"Separation model: {model or 'not configured'}; checked "
            + ", ".join(str(p) for p in locations),
            "Set [separation] model in muestra.toml and install that model using your separation "
            "backend. Doctor checks cached filenames only; it never downloads models.",
            optional=True,
        )
    else:
        for name in ("library", "plugins", "models"):
            check(
                name,
                False,
                "Needs a valid project to inspect configuration.",
                "Select a valid project with --project <path>.",
            )
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        check("port", True, f"127.0.0.1:{port} is available.")
    except OSError:
        check(
            "port",
            False,
            f"127.0.0.1:{port} is busy.",
            "muestra serve automatically picks a free port; or pass --port <free-port>.",
        )
    return {"ok": not any(c["status"] == "missing" for c in checks), "checks": checks}
