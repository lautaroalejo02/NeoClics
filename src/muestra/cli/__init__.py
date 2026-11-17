"""Registry-generated CLI and local agent setup helpers."""

import os as _os

# Pydantic scans every installed package's entry points for plugins the first
# time a model is built (~70 ms per CLI call). Muestra uses none; a user who
# does can still opt back in by setting the variable themselves (WP12).
_os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
