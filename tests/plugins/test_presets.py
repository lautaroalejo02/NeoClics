import struct
import xml.etree.ElementTree as ET

from muestra.plugins import presets


def test_surge_names_paths_and_binary_wavetables_survive(project, monkeypatch):
    root = project.root / "Surge XT/patches_factory/Basses"
    root.mkdir(parents=True)
    xml = (
        b'<patch><parameters><a_osc1_retrigger value="0"/>'
        b'<a_drift value=".5"/></parameters></patch>'
    )
    data = b"sub3" + struct.pack("<I", len(xml)) + b"\0" * 24 + xml + b"BINARY_WAVETABLE"
    path = root / "Example.fxp"
    path.write_bytes(b"CcnK" + b"\0" * 4 + b"FPCh" + b"\0" * 48 + data)
    monkeypatch.setattr(presets, "surge_roots", lambda: [root.parent])
    assert presets.resolve_preset(project, "Basses/Example", surge=True) == path
    assert presets.resolve_preset(project, str(path), surge=True) == path
    changed = presets.surge_component(path, deterministic=True)
    n = struct.unpack_from("<I", changed, 4)[0]
    params = ET.fromstring(changed[32 : 32 + n]).find("parameters")
    assert params.find("a_osc1_retrigger").get("value") == "1"
    assert params.find("a_drift").get("value") == "0"
    assert changed[32 + n :] == b"BINARY_WAVETABLE"
