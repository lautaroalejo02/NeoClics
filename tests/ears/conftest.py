"""Shared helpers for the ears tests: cached synthetic renders."""

from __future__ import annotations

import pytest

from muestra.ears.core import analyze
from muestra.ears.corpus import CorpusSpec, Defects, generate
from muestra.project import open_project

#: detection tests run at 24 kHz / 8 bars to keep the suite fast; the
#: analyzer scales every window with the sample rate (48 kHz is covered
#: by the performance test and a few spot checks).
FAST = {"sr": 24000, "bars": 8}


@pytest.fixture(scope="session")
def render(tmp_path_factory):
    """render(**defects, spec=...) -> (project, metrics), cached per arguments."""
    cache: dict[tuple, tuple] = {}

    def make(spec: dict | None = None, **defects):
        spec = {**FAST, **(spec or {})}
        key = (tuple(sorted(spec.items())), tuple(sorted(defects.items())))
        if key not in cache:
            root = tmp_path_factory.mktemp("corpus")
            generate(CorpusSpec(defects=Defects(**defects), **spec), root)
            project = open_project(root)
            cache[key] = (project, analyze(project, "v001"))
        return cache[key]

    return make


def codes(metrics, severities=("warn", "error")) -> set[str]:
    return {a.code for a in metrics.alarms if a.severity in severities}
