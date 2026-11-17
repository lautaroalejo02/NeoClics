# Muestra

AI-native music studio core. See `docs/spec.md` for the full spec and
`docs/contracts.md` for the data contracts every component builds against.

## Install

```sh
uv sync
```

Python is managed by `uv` (>=3.11 required, 3.12 pinned in `.python-version`).

## Test

```sh
uv run pytest
```

## Lint / format

```sh
uv run ruff check
uv run ruff format --check
```

## Export JSON Schemas

The committed JSON Schemas under `schemas/` are generated from the pydantic
models. After changing a model, regenerate them:

```sh
uv run python -m muestra.schemas.export schemas/
```

A test (`tests/schemas/test_schema_export.py`) fails if the committed files
are stale.
