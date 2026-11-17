"""Private native-plugin worker. Never import a plugin in the parent process."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from ..errors import MuestraError
from .presets import load_preset


def set_parameters(plugin, params):
    for name, value in params.items():
        if name not in plugin.parameters:
            raise MuestraError.one(
                "plugin_parameter",
                f"Unknown parameter {name!r} for {plugin.name}.",
                path=f"params.{name}",
                hint="Use plugins.scan to list native parameter names.",
            )
        try:
            setattr(plugin, name, value)
        except (ValueError, TypeError, RuntimeError) as e:
            raise MuestraError.one(
                "plugin_parameter",
                str(e),
                path=f"params.{name}",
                value=value,
                hint="Use the parameter's native numeric range reported by plugins.scan.",
            ) from e


def main():
    from .lazy import load_plugin

    plugin = None
    for line in sys.stdin:
        try:
            command = json.loads(line)
            op = command["op"]
            if op in {"load", "preset"}:
                if op == "load":
                    plugin = load_plugin(command["path"])
                if command["preset"]:
                    try:
                        load_preset(
                            plugin, Path(command["preset"]), deterministic=command["deterministic"]
                        )
                    except Exception as e:
                        raise MuestraError.one(
                            "preset_invalid",
                            str(e),
                            path="preset",
                            hint="Choose a compatible .vstpreset or Surge XT .fxp file.",
                        ) from e
                plugin.refresh_parameters()
                set_parameters(plugin, command["params"])
                response = dict(
                    name=plugin.name,
                    is_instrument=plugin.is_instrument,
                    is_effect=plugin.is_effect,
                    latency_samples=plugin.reported_latency_samples,
                    parameters=list(plugin.parameters),
                )
            elif op == "render":
                if not plugin.is_instrument:
                    raise MuestraError.one(
                        "plugin_kind",
                        "This plugin is not an instrument.",
                        hint="Choose an instrument from plugins.scan.",
                    )
                audio = plugin.process(
                    [(bytes(msg), timestamp) for msg, timestamp in command["events"]],
                    duration=command["n_frames"] / command["sample_rate"],
                    sample_rate=command["sample_rate"],
                    num_channels=2,
                    buffer_size=512,
                    reset=command["reset"],
                )
                np.save(command["output"], audio.T.astype(np.float32), allow_pickle=False)
                response = {}
            elif op == "effect":
                if not plugin.is_effect:
                    raise MuestraError.one(
                        "plugin_kind",
                        "This plugin is not an effect.",
                        hint="Choose an effect from plugins.scan.",
                    )
                set_parameters(plugin, command["params"])
                audio = plugin.process(
                    np.load(command["input"], allow_pickle=False),
                    sample_rate=command["sample_rate"],
                    reset=command["reset"],
                    buffer_size=command["buffer_size"],
                )
                np.save(command["output"], audio, allow_pickle=False)
                response = {}
            elif op == "parameter_info":
                response = {
                    name: dict(
                        min_value=p.min_value, max_value=p.max_value, valid_values=p.valid_values
                    )
                    for name in command["names"]
                    if (p := plugin.parameters.get(name))
                }
            else:
                raise ValueError(f"Unknown worker operation: {op}")
        except MuestraError as e:
            response = {"errors": [i.model_dump(mode="json") for i in e.issues]}
        except Exception as e:
            response = {
                "errors": [
                    dict(
                        code="plugin_load" if plugin is None else "plugin_process",
                        message=f"{type(e).__name__}: {e}",
                        path="plugins",
                        hint="Update this plugin or choose another plugin/preset.",
                    )
                ]
            }
        print("MUESTRA:" + json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
