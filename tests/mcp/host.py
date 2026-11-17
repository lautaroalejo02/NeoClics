"""Test-only subprocess entrypoint adding fake tools before interface generation."""

from tests.api.fakes import register

register()

if __name__ == "__main__":
    from muestra.cli.main import entrypoint

    entrypoint()
