import sys
import time

import numpy as np
import pytest

from muestra.errors import MuestraError
from muestra.plugins import host
from muestra.plugins.core import PluginEffect
from muestra.plugins.host import PluginHost


@pytest.mark.parametrize(
    "script,code",
    [
        ("import os;os._exit(17)", "plugin_crashed"),
        ("import time;time.sleep(60)", "plugin_timeout"),
    ],
)
def test_bad_plugin_cannot_crash_or_hang_parent(project, monkeypatch, script, code):
    monkeypatch.setattr(host, "worker_command", lambda: [sys.executable, "-u", "-c", script])
    start = time.perf_counter()
    with pytest.raises(MuestraError) as exc:
        PluginHost(project.root / "bad.vst3", timeout=2)
    assert exc.value.issues[0].code == code
    assert time.perf_counter() - start < 15


def test_invalid_binary_is_structured(project):
    path = project.root / "invalid.vst3"
    path.write_bytes(b"not a plugin")
    with pytest.raises(MuestraError) as exc:
        PluginHost(path, timeout=30)
    assert exc.value.issues[0].code == "plugin_load"


def test_fx_automation_keeps_instance_and_state():
    class FakeHost:
        info = {"parameters": ["gain"], "latency_samples": 17}
        calls = []

        def effect(self, audio, sample_rate, **kwargs):
            self.calls.append(kwargs)
            return audio * kwargs["params"]["gain"]

        def close(self):
            pass

    effect = PluginEffect(FakeHost(), {"gain": 1})
    effect.gain = 0.5
    audio = np.ones((2, 100), dtype=np.float32)
    assert np.all(effect.process(audio, 48000) == 0.5)
    effect.set_parameters({"gain": 0.25})
    assert np.all(effect.process(audio, 48000) == 0.25)
    assert all(not c["reset"] for c in effect._host.calls)
    assert effect.reported_latency_samples == 17
    with pytest.raises(MuestraError):
        effect.missing = 1
