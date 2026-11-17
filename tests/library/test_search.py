"""Search: vocabulary (EN/ES), filters, ranking, similarity, payload shape."""

from __future__ import annotations

import pytest

from muestra.errors import MuestraError
from muestra.library.search import compatible_key, parse_query, quality, search
from muestra.project import open_project
from muestra.registry import run_tool
from muestra.schemas.library import LibraryIndexEntry

K1 = "library://kits/machine-a/kick-m-kick-01.wav"


@pytest.fixture
def proj(project, indexed):  # indexed library + a project pointing at it
    return project


def paths(outcome):
    return [h.path.split("/")[-1] for h in outcome.results]


def test_spanish_and_english_words_find_the_same_things(proj):
    en = search(proj, query="kick")
    es = search(proj, query="bombo")
    assert paths(en) == paths(es) and en.total == es.total == 5
    assert {h.type for h in en.results} == {"kick"}
    assert paths(search(proj, query="redoblante")) == paths(search(proj, query="snare"))
    assert search(proj, query="platillo").total == 0  # no cymbals in this library


def test_empty_local_library_is_quiet_when_global_index_exists(proj):
    local = proj.path("library")
    local.mkdir(exist_ok=True)
    proj.config.library.roots.insert(0, "library")
    result = run_tool("library.search", {"project": str(proj.root), "query": "kick"})
    # Search on the in-memory config exercises the same discovery with an
    # explicit empty local root, alongside the configured indexed root.
    assert search(proj, query="kick").unindexed_roots == []
    assert result.ok


def test_hat_word_covers_the_family_and_openness_words_narrow_it(proj):
    assert {h.type for h in search(proj, query="hi hat").results} == {"chat", "ohat"}
    assert {h.type for h in search(proj, query="open hat").results} == {"ohat"}
    assert {h.type for h in search(proj, query="charles abierto").results} == {"ohat"}
    assert {h.type for h in search(proj, query="hat cerrado").results} == {"chat"}


def test_type_param_aliases_and_comma_lists(proj):
    assert {h.type for h in search(proj, type="bombo").results} == {"kick"}
    both = search(proj, type="kick,snare")
    assert {h.type for h in both.results} == {"kick", "snare"}


def test_ranking_prefers_clean_unclipped_files(proj):
    out = search(proj, query="kick", limit=50)
    names = paths(out)
    assert names.index("kick-ac-clipped-01.wav") > names.index("kick-m-kick-01.wav")
    assert names.index("kick-ac-liar-01.wav") > names.index("kick-m-kick-01.wav")
    clipped = next(h for h in out.results if h.path.endswith("clipped-01.wav"))
    assert clipped.clipping and any("clipped" in r for r in clipped.reasons)
    liar = next(h for h in out.results if h.path.endswith("liar-01.wav"))
    assert any("mismatch" in f for f in liar.flags)


def test_descriptor_words_rank_matching_files_first_and_say_why(proj):
    out = search(proj, query="kick long boomy")
    top = out.results[0]
    assert top.path.endswith("kick-m-kick-02.wav")  # the 300 ms-decay kick
    assert "long" in top.tags and any("tag long" in r for r in top.reasons)
    short = search(proj, query="kick short")
    assert short.results[0].path.endswith("kick-ac-kick-01.wav") or "short" in short.results[0].tags


def test_results_explain_themselves_in_musical_terms(proj):
    h = search(proj, query="kick").results[0]
    assert h.summary and "kick" in h.summary and "pitch" in h.summary and "decay" in h.summary
    assert h.reasons and h.score is not None and 0 <= h.score <= 1
    assert h.uri.startswith("library://kits/")


def test_compact_by_default_and_full_on_request(proj):
    compact = search(proj, query="kick").results[0]
    assert compact.features is None and compact.license is None
    assert len(compact.tags) <= 10 and "kick" not in compact.tags  # no duplicates of fields
    full = search(proj, query="kick", detail="full").results[0]
    assert full.features is not None and full.features.fundamental_hz
    assert full.license is not None and "no-redistribution" in full.license.restrictions


def test_ai_restriction_is_surfaced_even_in_compact_results(proj):
    b = next(h for h in search(proj, query="kick", limit=50).results if h.kit == "acoustic-b")
    assert "licence:no-ai-training" in b.flags


def test_bpm_filter_with_tolerance_and_half_double_time(proj):
    out = search(proj, bpm=120)
    assert {h.bpm for h in out.results} == {120.0, 100.0} - {100.0} | {120.0}
    assert all(h.kind == "loop" for h in out.results)
    assert any("exact" in r for r in out.results[0].reasons)
    # target 60 BPM: the 120 loops are DOUBLE-time of it, never a plain match
    dbl = search(proj, bpm=60)
    assert dbl.results and all("double-time of 60" in " ".join(h.reasons) for h in dbl.results)
    # target 240: the 120 loops are half-time
    assert "half-time of 240" in " ".join(search(proj, bpm=240).results[0].reasons)
    assert search(proj, bpm=60, half_double=False).total == 0
    # tolerance: 128 is outside 4% of 120 but inside 8%
    assert 128.0 not in {h.bpm for h in search(proj, bpm=120).results}
    assert 128.0 in {h.bpm for h in search(proj, bpm=120, bpm_tolerance=0.08).results}


def test_bpm_in_free_text_works_too(proj):
    assert search(proj, query="drums loop 128bpm").results[0].bpm == 128.0
    assert search(proj, query="beat 128").results[0].bpm == 128.0


def test_key_filter_matches_compatible_keys(proj):
    exact = search(proj, key="Am", key_exact=True)
    assert [h.key for h in exact.results] == ["Am"]
    # C major is the relative major of A minor; E minor is a fifth away (same mode)
    assert search(proj, key="C").total == 1
    assert search(proj, key="Em").total == 1
    assert search(proj, key="F#m", key_exact=True).total == 0
    assert any("key Am" in r for r in exact.results[0].reasons)


def test_compatible_key_weights():
    assert compatible_key("Am", "Am") == 1.0
    assert compatible_key("Am", "C") == 0.9 and compatible_key("C", "Am") == 0.9
    assert compatible_key("Am", "Em") == 0.7 and compatible_key("Am", "Dm") == 0.7
    assert compatible_key("Am", "A") == 0.45
    assert compatible_key("Am", "F#m") is None
    assert compatible_key("Am", None) is None
    assert compatible_key("nonsense", "Am") is None
    assert compatible_key("Am", "C", exact=True) is None


def test_no_matches_hint_explains_bpm_key_semantics(proj):
    r = run_tool("library.search", {"project": str(proj.root), "query": "kick", "bpm": 120})
    assert r.ok and r.data["total"] == 0
    assert "one-shots" in r.data["hint"]


def test_similar_to_ranks_by_measured_features(proj):
    out = search(proj, similar=K1, limit=20)
    names = paths(out)
    assert "kick-m-kick-01.wav" not in names  # not itself
    assert {h.type for h in out.results} == {"kick"}  # same type as the anchor
    # kick-02 (52 Hz, long) is nearer to kick-01 (60 Hz, short) than the hat-like liar
    assert names.index("kick-m-kick-02.wav") < names.index("kick-ac-liar-01.wav")
    assert any("sound distance" in r for r in out.results[0].reasons)
    assert out.interpreted and any(i.startswith("similar-to=") for i in out.interpreted)


def test_similar_to_unknown_uri_is_reported(proj):
    out = search(proj, similar="library://kits/nope/x.wav")
    assert any("not in the index" in n for n in out.notes)


def test_filters_kit_kind_duration_tags(proj):
    assert {h.kit for h in search(proj, kit="acoustic").results} == {"acoustic-b"}
    assert {h.kind for h in search(proj, kind="loop").results} == {"loop"}
    assert all(h.duration_s >= 0.5 for h in search(proj, type="kick", duration_min=0.5).results)
    assert all(h.duration_s <= 0.3 for h in search(proj, type="chat", duration_max=0.3).results)
    assert all("acoustic" in h.tags for h in search(proj, tags=["acoustic"], limit=50).results)
    assert search(proj, tags=["no-such-tag"]).total == 0


def test_clean_only_drops_flagged_files(proj):
    names = paths(search(proj, query="kick", clean_only=True, limit=50))
    assert "kick-ac-clipped-01.wav" not in names and "kick-ac-liar-01.wav" not in names
    assert "kick-m-kick-01.wav" in names


def test_unknown_word_relaxes_with_a_note(proj):
    out = search(proj, query="kick zzzqqq")
    assert out.total == 5
    assert any("zzzqqq" in n for n in out.notes)


def test_free_text_matches_names_and_kits(proj):
    out = search(proj, query="machine-a snare")
    assert {h.kit for h in out.results} == {"machine-a"}


def test_limit_and_offset_page_through_results(proj):
    allk = paths(search(proj, query="kick", limit=50))
    assert paths(search(proj, query="kick", limit=2)) == allk[:2]
    assert paths(search(proj, query="kick", limit=2, offset=2)) == allk[2:4]
    assert search(proj, query="kick", limit=2).total == 5


def test_search_is_fast_on_a_big_index(tmp_path):
    """A 5,000-entry index answers well under a second (loaded once)."""
    import json
    import time

    from muestra.library.core import loaded_index
    from muestra.project import create_project
    from muestra.schemas import LibraryConfig, ProjectConfig

    root = tmp_path / "big"
    root.mkdir()
    entries = [
        {
            "path": f"kits/k{i % 7}/{'kick' if i % 3 == 0 else 'snare'}-x-{i:05d}.wav",
            "type": "kick" if i % 3 == 0 else "snare",
            "kind": "one-shot",
            "kit": f"k{i % 7}",
            "tags": ["909", "punchy", "short"],
            "duration_s": 0.3,
            "features": {"spectral_centroid_hz": 100.0 + i % 50, "decay_ms": 100.0 + i % 40},
        }
        for i in range(5000)
    ]
    (root / ".muestra-index.json").write_text(
        json.dumps({"schema_version": 0, "entries": entries}), encoding="utf-8"
    )
    project = create_project(
        tmp_path / "p", ProjectConfig(library=LibraryConfig(roots=[root.as_posix()]))
    )
    assert loaded_index(root) is not None
    t0 = time.perf_counter()
    for q in ("punchy 909 kick", "bombo", "snare short"):
        assert search(project, query=q).total > 1000
    search(project, similar="library://kits/k0/kick-x-00000.wav")
    assert time.perf_counter() - t0 < 3.0


def test_minimal_hand_written_index_still_searches(tmp_path):
    """The WP0 fixture shape (path/type/tags only) keeps working."""
    from muestra.project import open_project as op

    project = op(__import__("pathlib").Path(__file__).parents[2] / "fixtures" / "example-project")
    out = search(project, query="kick")
    assert out.total == 1 and out.results[0].uri == "library://kits/disco-01/kick.wav"


def test_quality_prior_orders_clean_over_clipped_and_quiet():
    base = {"path": "a/b.wav", "bit_depth": 24, "sample_rate": 44100, "peak_dbfs": -1.0}
    clean = LibraryIndexEntry(**base)
    clipped = LibraryIndexEntry(**base, clipping=True)
    quiet = LibraryIndexEntry(**{**base, "peak_dbfs": -25.0})
    mism = LibraryIndexEntry(**base, flags=["type-mismatch:name=kick,audio=hat-like"])
    q = {n: quality(e)[0] for n, e in dict(c=clean, x=clipped, q=quiet, m=mism).items()}
    assert q["c"] > q["q"] > q["x"] and q["c"] > q["m"]


def test_query_parser_understands_both_languages():
    q = parse_query("Bombo punchy 909, 128 bpm en Am")
    assert q.types == ["kick"] and [c[1][0][0] for c in q.tag_concepts] == ["punchy", "909"]
    assert q.bpms == [128.0] and q.keys == ["am"]
    q = parse_query("hi-hat abierto con güiro")
    assert q.types == ["ohat"] and q.subtypes == ["guiro"]
    q = parse_query("palmas")
    assert q.types == ["clap"]
    assert parse_query("tr-808 conga").tag_concepts[0][1][0][0] == "808"


def test_not_indexed_and_no_library_give_actionable_errors(tmp_path, lib):
    from muestra.project import create_project
    from muestra.schemas import LibraryConfig, ProjectConfig

    p = create_project(
        tmp_path / "p2", ProjectConfig(library=LibraryConfig(roots=[lib.as_posix()]))
    )
    r = run_tool("library.search", {"project": str(p.root), "query": "kick"})
    assert not r.ok and r.errors[0].code == "library_not_indexed"
    assert "library.index" in r.hint

    q = create_project(tmp_path / "p3", ProjectConfig(library=LibraryConfig(roots=[])))
    r = run_tool("library.search", {"project": str(q.root), "query": "kick"})
    assert not r.ok and r.errors[0].code == "no_library"
    assert "MUESTRA_LIBRARY" in r.hint

    with pytest.raises(MuestraError):
        from muestra.library.tools import LibrarySearchParams, library_search

        library_search(LibrarySearchParams(project=str(q.root)))
    assert open_project(p.root)  # sanity


def test_bpm_miss_lists_the_tempos_that_do_exist(proj):
    out = search(proj, bpm=90)
    assert out.total == 0
    note = " ".join(out.notes)
    assert "available" in note and "120" in note and "128" in note


def test_diversify_shows_several_sources_for_vague_queries():
    from muestra.library.search import _diversify
    from muestra.schemas.library import LibraryHit

    def hit(path):
        return LibraryHit(uri=f"library://{path}", root="r", path=path)

    scored = [(0.70, hit(f"kits/a/x/kick-{i}.wav")) for i in range(5)]
    scored += [(0.69, hit("kits/b/x/kick-0.wav")), (0.68, hit("kits/c/x/kick-0.wav"))]
    picked = [h.path.split("/")[1] for _s, h in _diversify(scored, 3)]
    assert picked == ["a", "b", "c"]  # one per pack before a second from the same pack
    assert _diversify(scored[:1], 3) == scored[:1]
    # clear quality differences still win over variety
    strong = [(0.95, hit(f"kits/a/x/k{i}.wav")) for i in range(3)] + [(0.5, hit("kits/b/x/k.wav"))]
    assert [h.path.split("/")[1] for _s, h in _diversify(strong, 3)] == ["a", "a", "a"]
