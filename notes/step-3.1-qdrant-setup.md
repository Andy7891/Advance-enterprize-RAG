# Phase 3, Microstep 1 — Qdrant vector database setup

**Date:** 2026-09-16

## Audit before starting
- **Qdrant was already ~80% done** — defined in `docker-compose.yml` since P1-M3 (image `v1.19.0`, ports 6333/6334, named volume, healthcheck). Brought up and confirmed: `healthz check passed`, zero collections.
- **The spec's vector sizes were wrong for this project.** Measured directly: `nomic-embed-text` produces **768**-dim vectors; the spec offered only 384 or 1536.
- **The spec had an ordering bug:** create the collection in M1, pick the model in M2 — but Qdrant vector size is fixed at creation, so the model had to be chosen first.
- **Chunk-sizing problem still open** (69% of chunks over target, largest 13,599 chars). Harmless for M1; must be fixed before M3 embeds anything.

## What was built
- `src/vectorstore/setup.py` — `ensure_collection()` creates `chunks` (768, cosine) if missing; if present, verifies size and distance and raises `CollectionConfigMismatch` on any difference, including a named-vector collection. Never auto-repairs. CLI: `python -m src.vectorstore.setup`.
- `tests/vectorstore/test_setup.py` — 8 tests using `QdrantClient(":memory:")`, fully offline.

## Verified against the real server
```
first run : collection 'chunks' created (size=768, distance=Cosine)
second run: already present, config verified
REST      : status green | vectors {'size': 768, 'distance': 'Cosine'} | points 0
after `docker compose restart qdrant`: still present, config verified
```
101 tests passing; ruff and mypy clean.

## Key concepts
- **Collection** — Qdrant's equivalent of a table: a named set of points sharing one vector configuration.
- **Vector size (dimension)** — the length of each embedding; fixed at creation and determined by the model.
- **Distance metric** — the definition of "similar" (cosine = angle between vectors, ignoring length).
- **Idempotency** — running setup twice is safe and changes nothing the second time.
- **Fail loud on config drift** — refusing to reuse a mismatched collection instead of discovering it at upsert.
- **In-memory mode** — the real Qdrant client logic with no server, for offline tests.

## Honest limitations
1. The collection is welded to `nomic-embed-text`; changing models means drop + full re-embed.
2. Mismatches raise with no repair path — deliberate, but you delete the collection by hand if you change config.
3. In-memory mode is not the server; a behaviour difference between them would slip past the tests.
4. The chunk-sizing fix before M3 is recorded, not enforced.

## How this connects
**Previous:** Phase 2 produced 214 schema-verified chunks in `data/chunks/`. **This step:** a correctly configured, empty collection to hold their vectors. **Next:** re-tune chunk sizing, then P3-M2/M3 — embed with `nomic-embed-text` and upsert, remembering the positional `chunk_id` scheme needs a delete-by-`doc_id` guard before re-ingesting.

## How to do this yourself
1. `docker compose up -d qdrant`, then `curl localhost:6333/healthz`.
2. Measure your model's dimension: embed one string and take `len()` of the vector — don't trust docs.
3. Create the collection with that size and the metric the model was trained for.
4. Write setup as create-if-missing + verify-if-present, failing loudly on mismatch.
5. Test with `QdrantClient(":memory:")`.
6. Restart the container and confirm the collection persists (named volume).

## Practice exercises
1. Open `http://localhost:6333/dashboard` and find the `chunks` collection.
2. `docker compose down -v`, bring Qdrant back, confirm the collection is gone, then recreate it.
3. Create a collection with size 384, run setup, and read the mismatch error.
4. Upsert one 768-dim point via curl and read it back.
5. Upsert a 384-dim point and read Qdrant's rejection.
6. Compare cosine and dot-product scores for two unnormalised vectors by hand.
7. Normalise those vectors and confirm cosine and dot product now agree.
8. Measure the dimension of another Ollama embedding model (e.g. `mxbai-embed-large`).
9. Time `ensure_collection` against the server vs in-memory.
10. Write an integration test marked `@pytest.mark.qdrant` that runs setup against the real server.
