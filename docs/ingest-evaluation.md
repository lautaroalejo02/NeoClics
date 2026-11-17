# WP19 ingest evaluation - 2026-11-17

Status: in progress. CPU-only Windows 11; no listening claims, paid services,
commercial recordings, or LLM inference. Audio stays outside this repository.

## Material and provenance

MDB Drums, published by Carl Southall et al., explicitly licenses audio and
annotations CC-BY-NC-SA 4.0. Evaluation only; do not bundle with the product.
Source: https://github.com/CarlSouthall/MDBDrums, revision
`b29e2d63c3a023506f4bf353c5b2e8a558eed135`.
Its 23 Music Delta recordings include 80s rock, disco, funk/fusion/swing jazz,
rock/pop and harder/slower controls, with published beat-position annotations
and isolated instrument stems. These are stylistic demonstrations/excerpts,
not Argentine rock or city pop. No generalisation to the owner's music is proven.

## Reproduction / pending measurements

`uv sync --extra separation` completed. Dataset setup and benchmark commands,
partial results and final aggregates will be appended at committed checkpoints.
Audio, per-item dated licence notes, hashes and raw results remain outside git.
MDB Drums supplies no key/section/phrase annotations: estimator consensus must
not be called ground truth. Reference stem mixing/scaling needs validation.
Short excerpts cannot establish actual four-minute peak RAM. Human listening
is still required for bar phase and sample bleed. Nothing has been heard here.
