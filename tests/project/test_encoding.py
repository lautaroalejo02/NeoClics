from muestra.registry import run_tool


def test_non_utf8_song_reports_byte_offset(tmp_project):
    raw = tmp_project.song_path.read_bytes() + b"\n# editor: \x97\n"
    tmp_project.song_path.write_bytes(raw)
    result = run_tool("song.validate", {"project": str(tmp_project.root)})
    assert not result.data["valid"]
    issue = result.data["issues"][0]
    assert issue["code"] == "invalid_encoding" and issue["path"] == "song.yaml"
    assert str(raw.index(b"\x97")) in issue["message"]
    assert "UTF-8" in issue["hint"]
    assert (
        run_tool("song.get", {"project": str(tmp_project.root)}).errors[0].code
        == "invalid_encoding"
    )
