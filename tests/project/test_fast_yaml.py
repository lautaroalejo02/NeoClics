"""WP12: the libyaml fast path reads song.yaml exactly like the ruamel round trip."""

from pathlib import Path

import pytest

from muestra.errors import MuestraError
from muestra.project import validate as v
from muestra.schemas.song import Song

REPO = Path(__file__).resolve().parents[2]
SONGS = sorted(
    [
        *(REPO / "skills" / "muestra" / "examples").glob("*.yaml"),
        *(REPO / "fixtures").rglob("song.yaml"),
    ]
)


@pytest.mark.parametrize("path", SONGS, ids=lambda p: p.parent.name + "/" + p.name)
def test_fast_and_round_trip_parsers_agree(path):
    fast = v._plain_doc(path, "song.yaml")
    slow = v._yaml_doc(path, "song.yaml")
    assert fast == slow
    assert Song.model_validate(fast) == Song.model_validate(slow)


def test_yaml_12_scalars_and_errors_fall_back(tmp_path):
    p = tmp_path / "song.yaml"
    p.write_text("a: yes\nb: on\nc: 1e3\nd: 012\ne: 0x1F\nf: true\ng: ~\nh: 1:30\n", "utf-8")
    assert v._plain_doc(p, "song.yaml") == v._yaml_doc(p, "song.yaml")
    p.write_text("meta: {}\nmeta: {}\n", "utf-8")
    with pytest.raises(MuestraError) as e:
        v._plain_doc(p, "song.yaml")
    assert e.value.issues[0].code == "duplicate_key"
    p.write_text("meta: [unclosed\n", "utf-8")
    with pytest.raises(MuestraError) as e:
        v._plain_doc(p, "song.yaml")
    assert e.value.issues[0].code == "parse_error"
