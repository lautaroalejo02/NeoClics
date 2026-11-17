from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from muestra.api.core import Runtime, resolve_project
from muestra.registry import REGISTRY


def test_resolution_precedence(tmp_project, tmp_path, monkeypatch):
    inside = tmp_project.root / "cache/subdir"
    inside.mkdir()
    monkeypatch.chdir(inside)
    monkeypatch.delenv("MUESTRA_PROJECT", raising=False)
    assert resolve_project() == tmp_project.root
    monkeypatch.setenv("MUESTRA_PROJECT", str(tmp_path))
    assert resolve_project() == tmp_path
    assert resolve_project(tmp_project.root) == tmp_project.root


def test_concurrent_process_feedback_writers_and_reader(tmp_project):
    # Large Unicode lines exceed normal pipe/buffer sizes. An agent reads while several
    # independent surfaces append; no event is truncated and cursors never lose a line.
    script = """
import json,sys,time
from muestra.api.core import Runtime
from muestra.registry import REGISTRY
runtime=Runtime(sys.argv[1])
mode=sys.argv[2]
if mode == 'write':
    for n in range(12):
        result=runtime.call(REGISTRY['feedback.append'], {'event': {
            'type':'comment','version':'v001','text': 'ñ音' * 3000 + sys.argv[3] + str(n)}})
        assert result.ok, result
else:
    cursor=0
    for n in range(32):
        result=runtime.call(REGISTRY['feedback.read'], {'cursor':cursor})
        assert result.ok and not result.data['skipped'], result
        cursor=result.data['next_cursor']
        time.sleep(.005)
print(json.dumps({'ok': True}))
"""
    (tmp_project.root / "feedback.jsonl").write_text("", "utf-8")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_project.root), mode, str(index)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
            cwd=Path(__file__).resolve().parents[2],
        )
        for index, mode in enumerate(["write", "write", "write", "read"])
    ]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=45)
            assert process.returncode == 0, stderr
            assert json.loads(stdout)["ok"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    result = Runtime(tmp_project.root).call(REGISTRY["feedback.read"])
    assert result.ok and not result.data["skipped"]
    assert result.data["count"] == 36
    assert len({e["text"] for e in result.data["events"]}) == 36
