"""A persistent subprocess isolates native plugin crashes and enforces deadlines."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

from ..errors import Issue, MuestraError


class PluginHost:
    def __init__(
        self,
        path: Path,
        preset: Path | None = None,
        params=None,
        *,
        deterministic=False,
        timeout=120.0,
    ):
        self.timeout = timeout
        self.path = path
        self.directory = tempfile.TemporaryDirectory(prefix="muestra-plugin-")
        self.root = Path(self.directory.name)
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "muestra.plugins.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.info = self.request(
                op="load",
                path=str(path),
                preset=str(preset) if preset else None,
                params=params or {},
                deterministic=deterministic,
            )
        except BaseException:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            if line.startswith("MUESTRA:"):
                self.messages.put(json.loads(line[8:]))
        self.messages.put(None)

    def request(self, **command):
        try:
            self.process.stdin.write(json.dumps(command, allow_nan=False) + "\n")
            self.process.stdin.flush()
            response = self.messages.get(timeout=self.timeout)
        except queue.Empty as e:
            self.close()
            raise MuestraError.one(
                "plugin_timeout",
                f"Plugin {self.path.name} exceeded {self.timeout:g} seconds.",
                path="plugins",
                hint="Disable this plugin or increase the scan timeout; try SFZ.",
            ) from e
        except (BrokenPipeError, OSError) as e:
            response = None
            cause = e
        else:
            cause = None
        if response is None:
            self.close()
            raise MuestraError.one(
                "plugin_crashed",
                f"Plugin worker exited while handling {self.path.name}.",
                path="plugins",
                hint="Update or disable the plugin; use a known-good preset or SFZ.",
            ) from cause
        if "errors" in response:
            raise MuestraError([Issue.model_validate(i) for i in response["errors"]])
        return response

    def render(self, events, sample_rate, n_frames, *, reset=True):
        target = self.root / "output.npy"
        self.request(
            op="render",
            events=events,
            sample_rate=sample_rate,
            n_frames=n_frames,
            output=str(target),
            reset=reset,
        )
        return np.load(target, allow_pickle=False)

    def effect(self, audio, sample_rate, *, reset=False, params=None, buffer_size=8192):
        source, target = self.root / "input.npy", self.root / "output.npy"
        np.save(source, audio, allow_pickle=False)
        self.request(
            op="effect",
            input=str(source),
            output=str(target),
            sample_rate=sample_rate,
            reset=reset,
            params=params or {},
            buffer_size=buffer_size,
        )
        return np.load(target, allow_pickle=False)

    def close(self):
        process = getattr(self, "process", None)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            self.process = None
        directory = getattr(self, "directory", None)
        if directory is not None:
            directory.cleanup()
            self.directory = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()
