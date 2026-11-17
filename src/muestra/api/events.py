"""Debounced disk watching: sees writes from CLI/MCP processes as well as HTTP."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from ..feedback.store import EVENT_ADAPTER
from ..schemas.api import WsEvent
from ..schemas.chops import ChopCandidateSet
from ..schemas.metrics import Metrics
from ..schemas.render_meta import RenderMeta
from .summary import read_model


class EventBus:
    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(queue)
        return queue

    def publish(self, kind: str, data: dict):
        event = WsEvent(type=kind, ts=datetime.now(UTC), data=data).model_dump(mode="json")
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


def _stamp(path: Path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _live_set(path: Path) -> bool:
    """A candidate set file (not its fingerprint sidecar)."""
    return not path.name.endswith(".meta.json")


class ProjectWatcher:
    """Portable polling avoids platform-specific recursive watcher semantics on Windows.

    Only lightweight stat checks run each interval. Files are parsed after a quiet period;
    invalid/incomplete JSON is retried, even if its timestamp doesn't change.
    """

    def __init__(self, root: Path, bus: EventBus, *, interval=0.1, debounce=0.2):
        self.root, self.bus = root, bus
        self.interval, self.debounce = interval, debounce
        self.observed: dict[Path, tuple] = {}
        self.delivered: dict[Path, tuple] = {}
        self.pending: dict[Path, float] = {}
        self.feedback_offset = 0
        self.feedback_identity = None

    def _files(self):
        return [
            self.root / "song.yaml",
            *(self.root / "renders").glob("v*/meta.json"),
            *(self.root / "renders").glob("v*/metrics.json"),
            *(p for p in (self.root / "cache/chops").glob("*/*.json") if _live_set(p)),
            *(
                p
                for p in (self.root / "cache/analysis").glob("*.json")
                if not p.name.endswith(".meta.json")
            ),
            *(self.root / "cache/previews").glob("*/grid/index.json"),
        ]

    def initialize(self):
        for path in self._files():
            stamp = _stamp(path)
            if stamp is not None:
                self.observed[path] = stamp
                if path.name == "meta.json" and read_model(path, RenderMeta) is None:
                    self.pending[path] = time.monotonic()
                else:
                    self.delivered[path] = stamp
        feedback = self.root / "feedback.jsonl"
        try:
            data = feedback.read_bytes()
            self.feedback_offset = data.rfind(b"\n") + 1
            self.feedback_identity = feedback.stat().st_ino
        except OSError:
            pass

    def scan(self) -> list[tuple[str, dict]]:
        events = []
        now = time.monotonic()
        for path in self._files():
            stamp = _stamp(path)
            if stamp is None:
                continue
            if self.observed.get(path) != stamp:
                self.observed[path] = stamp
                self.pending[path] = now
            if (
                self.delivered.get(path) == stamp
                or now - self.pending.get(path, now) < self.debounce
            ):
                continue
            relative = path.relative_to(self.root).as_posix()
            if path.name in ("meta.json", "metrics.json"):
                meta = read_model(path.with_name("meta.json"), RenderMeta)
                if meta is None:
                    continue
                # Re-announced (upsert) when metrics.json lands after meta.json.
                metrics = read_model(path.with_name("metrics.json"), Metrics)
                events.append(
                    (
                        "version.created",
                        {"version": meta["version"], "meta": meta, "metrics": metrics},
                    )
                )
            elif relative.startswith("cache/analysis/") or relative.endswith("/grid/index.json"):
                # source.regrid / source.preview_grid (WP12): the grid or its
                # click previews changed; stale candidate sets were retired.
                source_id = (
                    path.stem if relative.startswith("cache/analysis/") else path.parent.parent.name
                )
                events.append(("grid.changed", {"source_id": source_id}))
            elif relative.startswith("cache/chops/"):
                candidate = read_model(path, ChopCandidateSet)
                if candidate is None:
                    continue
                events.append(
                    (
                        "candidates.created",
                        {"set_id": candidate["set_id"], "source_id": candidate["source_id"]},
                    )
                )
            else:
                events.append(("song.changed", {}))
            self.delivered[path] = stamp
        events.extend(self._feedback())
        return events

    def _feedback(self) -> list:
        path = self.root / "feedback.jsonl"
        try:
            stat = path.stat()
            if stat.st_ino != self.feedback_identity or stat.st_size < self.feedback_offset:
                self.feedback_offset = 0
            self.feedback_identity = stat.st_ino
            with path.open("rb") as stream:
                stream.seek(self.feedback_offset)
                data = stream.read()
        except OSError:
            return []
        complete = data.rfind(b"\n") + 1
        self.feedback_offset += complete
        events = []
        for line in data[:complete].splitlines():
            try:
                event = EVENT_ADAPTER.validate_python(json.loads(line))
            except (ValueError, UnicodeError):
                continue
            events.append(("feedback.appended", event.model_dump(mode="json")))
        return events

    async def run(self):
        while True:
            await asyncio.sleep(self.interval)
            for kind, data in await asyncio.to_thread(self.scan):
                self.bus.publish(kind, data)
