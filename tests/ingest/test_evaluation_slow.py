import json
import os
from pathlib import Path

import pytest


@pytest.mark.slow
def test_real_licensed_evaluation_tracks_when_present(tmp_path):
    configured = os.environ.get("MUESTRA_EVAL_MANIFEST")
    if not configured or not Path(configured).is_file():
        pytest.skip("Set MUESTRA_EVAL_MANIFEST to the prepared MDB Drums manifest")
    pytest.importorskip("mir_eval")
    from muestra.ingest.evaluation import evaluate_track

    manifest = Path(configured)
    tracks = json.loads(manifest.read_text(encoding="utf-8"))["tracks"]
    selected = [track for track in tracks if track["id"] in {"80srock", "disco", "fusionjazz"}]
    assert len(selected) == 3
    for track in selected:
        result = evaluate_track(manifest.parent, track, tmp_path, previews=False)
        assert result["beats"]["f70"] >= 0.9
        assert result["downbeats"]["f70"] >= 0.85
        assert result["reference_strength"] == "published beat-position annotations"
