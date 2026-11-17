"""Spec section 3, end to end, through the CLI and through MCP (fast variant).

Real code everywhere except separation and beat tracking (deterministic
fakes, see fakes.py). Prints the per-step timing table (``pytest -s``).
"""

from __future__ import annotations

from . import fakes
from .scenario import McpTransport, Scenario, Sound, assert_scenario, cli_inprocess


def _sound(studio) -> Sound:
    instruments = studio["tmp"]
    bass = fakes.tiny_sfz(instruments, "tiny-bass", 65.4)
    bass2 = fakes.tiny_sfz(instruments, "tiny-bass-2", 65.4, harmonics=6)
    return Sound(
        bass=f'{{plugin: sfz, preset: "{bass}"}}', bass_v2=f'{{plugin: sfz, preset: "{bass2}"}}'
    )


def test_scenario_through_the_cli(studio):
    scenario = Scenario(
        call=cli_inprocess,
        workdir=studio["work"],
        record=studio["record"],
        sound=_sound(studio),
        name="cli-song",
        library=studio["library"],
    )
    out = scenario.play()
    print("\nCLI (in-process)\n" + scenario.table())
    assert_scenario(scenario.project, out)


def test_scenario_through_mcp_stdio(studio):
    """The official MCP client over stdio against a real `muestra mcp` process
    (fakes installed by tests.integration.host)."""
    with McpTransport("tests.integration.host", studio["env"]) as mcp:
        assert {"render", "source_find_chops", "versions_restore"} <= set(mcp.tools())
        scenario = Scenario(
            call=mcp,
            workdir=studio["work"],
            record=studio["record"],
            sound=_sound(studio),
            name="mcp-song",
            library=studio["library"],
        )
        out = scenario.play()
        print("\nMCP (stdio)\n" + scenario.table())
        assert_scenario(scenario.project, out)
        # long-running tools stream progress notifications over MCP
        separate = mcp.progress["source.separate"]
        assert separate and separate[-1][0] == 1.0
        assert any("Fake separation" in (m or "") for _, m in separate)
        assert mcp.progress["source.analyze"][-1][0] == 1.0
