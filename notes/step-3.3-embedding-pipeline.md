# Phase 3, Microstep 3 — Embedding pipeline

**Date:** 2026-09-17

## What was built
- **`Embedding` contract** in `src/ingestion/schemas.py`: frozen dataclass holding the
  `chunk`, its `vector` (tuple), `model`, `model_digest` and `document_prefix`.
- **`src/embedding/embedder.py`** (spec said `src/retrieval/embeddings.py`, see D-2026-09-17-01):
  - `Embedder(client, *, ollama_url, model, dim, batch_size, verify)` verifies the pinned
    model **in the constructor**.
  - `embed_batch(chunks) -> list[Embedding]`: splits into batches of
    `settings.embed_batch_size` (16), applies `search_document: `, calls Ollama with
    `truncate=False`, validates every batch, returns embeddings in input order.
  - `EmbeddingError`: raised on server/HTTP/connection failure (naming the batch's
    chunk_ids) or on a failed validation.
  - CLI: `python -m src.embedding.embedder` embeds everything in `data/chunks/`.
- **`read_chunks()`** in `src/ingestion/pipeline.py`: loads `data/chunks/*.json` (except
  `report.json`) back into `DocumentChunk`s, the mirror of `write_chunks()`.
- **Config:** `embed_batch_size = 16`. **Dependency:** `ollama==0.6.2` pinned directly.
- **Cleanups:** chunker Protocol `Embedder` → `SentenceEmbedder`; fixed the `DocumentChunk`
  docstring that wrongly claimed `chunk_id` is a content hash.
- **Tests:** `tests/embedding/test_embedder.py`, 15 tests with a fake client. Suite: 130 passing.

## Verified, not assumed
```
batched vs single vectors      : identical (max abs diff 0.0)
batch size, 64 chunks, CPU     : 1 -> 93.9s   16 -> 63.9s   64 -> 76.8s
full corpus                    : 223 chunks embedded in 199.9s, all validated (dim 768, digest 0a109f422b47)
live failure path              : "embedding failed for ['company/about.md_0', 'TEST_oversized']: the input
                                  length exceeds the context length (status code: 400)"
mutation tests (9 mutants)     : truncate, prefix, count, dim, NaN, norm, order, verify, catch-all
                                  -> every mutant fails at least one test
ruff / mypy                    : clean
```

## Why this approach
| Choice | Over | Because |
|---|---|---|
| Embedding carries the whole chunk | chunk_id + later join | No second list to misalign |
| `ollama` client | LangChain `OllamaEmbeddings` | LangChain has no `truncate` option, so silent truncation couldn't be disabled |
| Batch 16 from config | one big request | Fastest measured; one failure affects 16 chunks, not 223 |
| Fail fast | skip bad chunks | The size cap makes a 400 a bug; skipping builds an index that *looks* complete |
| Verify in constructor | verify in caller | An Embedder existing proves the pin matched |
| In-memory output | disk cache | ~200s re-embed is cheaper than cache invalidation rules |

## Key concepts
- 🔴 **Batch embedding:** many texts per request; saves per-request overhead, doesn't change vectors.
- 🔴 **Document-side task prefix:** `search_document: ` on every stored chunk; queries will use `search_query: `.
- 🔴 **Refuse, don't truncate:** `truncate=False` turns silent recall loss into an error.
- 🔴 **Provenance:** recording the model digest and prefix on each vector so a mixed collection is detectable.
- 🟡 **Output validation:** count / dimension / finiteness / unit norm, because each failure is a plausible vector, not an exception.
- 🟡 **Batch-level failure:** one bad input fails the whole request, so errors name every chunk in the batch.
- ⚪ **Protocol + fake client:** tests without a server (same pattern as `verify_model(fetch=...)`).
- ⚪ **Catch by type:** `except (ResponseError, HTTPError, ConnectionError)` so our own bugs aren't disguised.

## Honest limitations
1. Stops on the first failure: a transient Ollama restart at chunk 200 throws away ~190s of work.
2. The norm check assumes a normalising model; a future model may need it switched off.
3. Everything is in memory; a crash before M4 loses the run.
4. First retrieval look was imperfect: "Who is the CEO?" ranked an HR note (0.720) above the
   About page naming the founder (0.685) on a 40-chunk subset. Not yet diagnosed.
5. `verify_model` raises a raw `URLError` when Ollama is down (seen live), not a friendly message.

## How this connects
**Previous:** P3-M2 pinned the model, set prefixes and capped chunk size; P2 wrote chunks to
`data/chunks/`. **This step:** turns those chunks into validated, provenance-tagged vectors.
**Next (P3-M4):** upsert `list[Embedding]` into the Qdrant `chunks` collection.
- Qdrant point IDs must be UUIDs or ints, not `company/about.md_3`.
- **Delete by `doc_id` before re-upserting** (positional chunk IDs).
- Store the digest and prefix in the payload.

## How to do this yourself
1. Read the Ollama `/api/embed` docs: note that `input` accepts a list and `truncate` defaults to true.
2. Embed the same texts one at a time and batched; confirm the vectors are identical before trusting batching.
3. Time a few batch sizes on your own hardware; don't copy someone else's number.
4. Define the output contract first (what does one embedding carry?), then write the function.
5. Send a batch containing one oversized text and look at what fails.
6. Write validation for every failure that produces numbers instead of exceptions.
7. Write tests with a fake client whose vectors encode the input, so order bugs show up.
8. Mutation-test: delete each safeguard in turn and confirm a test fails.
9. Run on the real corpus and spot-check that a query retrieves something sensible.

## Practice exercises
1. Run `python -m src.embedding.embedder --batch-size 1`, then `--batch-size 64`, and compare with 16.
2. Stop the Ollama container mid-run and read the error. Is it actionable?
3. Add a retry with exponential backoff for `ConnectionError` only; write a test with a client that fails twice then succeeds.
4. Implement bisection: on a 400, split the batch in halves to find the single bad chunk.
5. Change `embed_document_prefix` to `""` via env and check which test fails and why.
6. Make the fake client return vectors in reverse order and watch `test_returns_one_embedding_per_chunk_in_input_order` fail.
7. Embed all 223 chunks and plot a histogram of each chunk's similarity to its nearest neighbour; look for near-duplicates.
8. Repeat the "Who is the CEO?" check over all 223 chunks with prefixes on vs off.
9. Add a `--limit N` flag to the CLI for quick experiments.
10. Cache embeddings to `data/embeddings/` keyed by `sha256(text + digest + prefix)` and write the invalidation test.
