"""Avoid Pedalboard eagerly measuring ranges for every one of Surge's 2,855 params.

The native DSP host is unchanged. Only requested parameters get Pedalboard's
native-unit conversion wrapper. Keep this adapter covered when upgrading
Pedalboard: its normalization helper is an internal Python API.
"""

from collections.abc import Mapping

from pedalboard import AudioProcessorParameter, VST3Plugin
from pedalboard._pedalboard import _PythonExternalPluginMixin, to_python_parameter_name


class ParameterMap(Mapping):
    def __init__(self, plugin):
        self.plugin = plugin
        self.names = {
            name: p.name for p in plugin._parameters if (name := to_python_parameter_name(p))
        }
        self.cache = {}

    def __iter__(self):
        return iter(self.names)

    def __len__(self):
        return len(self.names)

    def __getitem__(self, name):
        if name not in self.cache:
            self.cache[name] = AudioProcessorParameter(self.plugin, self.names[name])
        return self.cache[name]

    def __contains__(self, name):
        return name in self.names


class LazyParameters:
    def __set_initial_parameter_values__(self, values):
        for name, value in (values or {}).items():
            setattr(self, name, value)

    @property
    def parameters(self):
        try:
            return object.__getattribute__(self, "_muestra_params")
        except AttributeError:
            params = ParameterMap(self)
            object.__setattr__(self, "_muestra_params", params)
            return params

    def __setattr__(self, name, value):
        if name.startswith("_") or hasattr(type(self), name):
            object.__setattr__(self, name, value)
        elif name in self.parameters:
            p = self.parameters[name]
            p.raw_value = p.get_raw_value_for(value)
        else:
            object.__setattr__(self, name, value)

    def refresh_parameters(self):
        object.__setattr__(self, "_muestra_params", ParameterMap(self))


def _parameter_by_name(self, name):
    return self.parameters.get(name)


def load_plugin(path):
    # pybind's constructor calls the base Python mixin even for subclasses.
    # Patch only inside the disposable worker, never the application process.
    _PythonExternalPluginMixin.__set_initial_parameter_values__ = (
        LazyParameters.__set_initial_parameter_values__
    )
    _PythonExternalPluginMixin.parameters = LazyParameters.parameters
    _PythonExternalPluginMixin.__setattr__ = LazyParameters.__setattr__
    _PythonExternalPluginMixin._get_parameter_by_python_name = _parameter_by_name
    _PythonExternalPluginMixin.refresh_parameters = LazyParameters.refresh_parameters
    if str(path).lower().endswith(".component"):
        from pedalboard import AudioUnitPlugin

        return AudioUnitPlugin(str(path))
    return VST3Plugin(str(path))
