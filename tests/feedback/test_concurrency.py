"""WP13 finding 8: concurrent appends from real processes never lose a line."""

from __future__ import annotations

import subprocess
import sys
import textwrap

from muestra.feedback import append_event, read_events
from muestra.project import create_project
from muestra.schemas.feedback import CommentEvent

WRITER = textwrap.dedent(
    """
    import sys
    from datetime import UTC, datetime
    from pathlib import Path
    from muestra.feedback import append_event
    from muestra.project import open_project
    from muestra.schemas.feedback import CommentEvent
    project = open_project(Path(sys.argv[1]))
    sys.stdin.readline()  # start together
    for i in range(int(sys.argv[3])):
        text = f"{sys.argv[2]}-{i} " + "x" * 200 + " \u2028 sep"
        append_event(project, CommentEvent(ts=datetime.now(UTC), text=text))
    """
)


def test_parallel_processes_append_every_event(tmp_path):
    project = create_project(tmp_path / "p")
    writers, per = 3, 150
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER, str(project.root), f"w{k}", str(per)],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for k in range(writers)
    ]
    seen, cursor = 0, 0
    for proc in procs:
        proc.stdin.write(b"go\n")
        proc.stdin.flush()
    while any(proc.poll() is None for proc in procs):
        page = read_events(project, cursor=cursor)  # a reader racing the writers
        assert not page.skipped, page.skipped[:1]
        seen += len(page.events)
        cursor = page.next_cursor
    for proc in procs:
        assert proc.wait(timeout=120) == 0, proc.stderr.read().decode()
    page = read_events(project, cursor=cursor)
    seen += len(page.events)
    assert not page.skipped
    assert seen == writers * per
    texts = {e.text.split(" ", 1)[0] for e in read_events(project).events}
    assert texts == {f"w{k}-{i}" for k in range(writers) for i in range(per)}


def test_append_after_a_hand_edit_without_final_newline(tmp_path):
    from datetime import UTC, datetime

    project = create_project(tmp_path / "p")
    project.feedback_path.write_bytes(
        b'{"ts":"2026-11-17T10:00:00Z","type":"comment","text":"a mano"}'
    )
    assert read_events(project).events == []  # an unterminated line is not read yet
    append_event(project, CommentEvent(ts=datetime.now(UTC), text="nuevo"))
    assert [e.text for e in read_events(project).events] == ["a mano", "nuevo"]
