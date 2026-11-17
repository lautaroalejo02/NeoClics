"""Test-only subprocess entrypoint: the real CLI/MCP with fast ML fakes installed.

``python -m tests.integration.host mcp --project ...`` behaves exactly like
``muestra mcp`` except that separation and beat tracking are the
deterministic fakes from :mod:`tests.integration.fakes`.
"""

from tests.integration.fakes import install

install()

if __name__ == "__main__":
    from muestra.cli.main import entrypoint

    entrypoint()
