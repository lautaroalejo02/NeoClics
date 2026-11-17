from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
from pathlib import Path

import httpx

from muestra.api.server import bind_socket, serve


def test_real_serve_subprocess_uses_free_port_and_serves_project(tmp_project):
    with bind_socket("127.0.0.1", 0) as busy:
        occupied = busy.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "muestra.cli.main",
                "serve",
                "--project",
                str(tmp_project.root),
                "--port",
                str(occupied),
            ],
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:

                def read_url():
                    assert process.stdout.readline().startswith("Muestra")
                    return process.stdout.readline().strip()

                future = executor.submit(read_url)
                try:
                    url = future.result(timeout=20)
                except TimeoutError:
                    process.kill()
                    raise
            assert url.startswith("http://127.0.0.1:")
            assert int(url.rsplit(":", 1)[1]) != occupied
            # Uvicorn takes the already-listening socket. Retry initial connection only.
            import time

            for attempt in range(40):
                try:
                    response = httpx.get(url + "/project", timeout=2, trust_env=False)
                    break
                except httpx.TransportError:
                    if process.poll() is not None or attempt == 39:
                        raise
                    time.sleep(0.05)
            assert response.status_code == 200
            assert response.json()["project_path"] == str(tmp_project.root)
        finally:
            process.terminate()
            process.communicate(timeout=10)


def test_explicit_lan_warning_and_open_browser(tmp_project, monkeypatch, capsys):
    import uvicorn

    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, *, sockets: None)
    serve(tmp_project.root, host="0.0.0.0", port=0, open_browser=True)
    captured = capsys.readouterr()
    assert "no authentication" in captured.err
    assert "LAN-IP" in captured.out
    assert opened[0].startswith("http://127.0.0.1:")
