# Phase 3, Microstep 4 — Vector store client

**Date:** 2026-09-17

## What was built
- **`src/vectorstore/store.py`** (spec said `src/retrieval/vector_store.py`, see D-2026-09-17-11):
  - `QdrantVectorStore(client, query_embedder, *, collection, dim, model_digest, document_prefix)`
  - `upsert(embeddings) -> None`: refuses mismatched provenance and incomplete documents, then
    per document deletes points by `doc_id` and upserts new ones with `wait=True`.
  - `search(query_text, k) -> list[DocumentChunk]` (spec) and
    `search_with_scores(query_text, k) -> list[ScoredChunk]`.
  - `health_check() -> bool`: readiness (reachable + collection with 768/cosine); never raises.
  - `count()`, `point_id(chunk_id)` (UUIDv5), `to_payload` / `chunk_from_payload`.
  - Errors: `ProvenanceMismatch`, `IncompleteDocument`.
  - CLI: `python -m src.vectorstore.store index | search "question" -k 5 | health`.
- **`Embedder.embed_query(text)`**: `search_query: ` prefix, `truncate=False`, same validation.
  Documents and queries now share one private `_embed_texts` path.
- **`src/vectorstore/setup.py`**:
  - `ensure_collection` now also creates keyword payload indexes on `doc_id` and `category`.
  - The config check is extracted as `check_collection_config` so health checks can reuse it.
  - New `build_qdrant_client(url)` pins `Accept-Encoding: gzip`.
- **Tests:** `tests/vectorstore/test_store.py` has 17 tests against in-memory Qdrant; 2 new
  `embed_query` tests. Suite: **149 passing**, ruff and mypy clean.

## Verified, not assumed
```
live index              : 223 chunks embedded in 187.2s, upserted in 4.8s (76 documents) -> 223 points
payload indexes (live)  : {'doc_id': 'keyword', 'category': 'keyword'}
provenance (live)       : all 223 points in one group (0a109f422b47, 'search_document: ')
point ids (live)        : every id == uuid5(chunk_id)
exactness (live)        : indexed_vectors_count 0; Qdrant top-5 == numpy brute force for 2 questions
latency (live)          : query_points ~18 ms; search incl. query embedding ~80 ms
stale guard (live)      : scratch collection 2 points -> upsert twice -> 2 -> re-upsert 1 chunk -> 1 (deleted after)
health (live)           : "ready" on 6333; "NOT ready" (no exception) on a closed port
mutation tests          : 10 mutants (delete guard, random ids, provenance, completeness, delete field,
                          provenance payload, liveness-only health, health raises, k check, limit) -> all killed
```

## Why this approach
| Choice | Over | Because |
|---|---|---|
| uuid5(chunk_id) IDs | random / content IDs | re-upsert overwrites; readable chunk_id stays the source of identity |
| delete by doc_id then upsert | recreate collection | only the re-ingested document is touched |
| full payload + provenance | IDs only | one source for search results; mixed model versions detectable |
| `embed_query` on Embedder | store calls Ollama | both sides share pin, truncation policy and validation |
| readiness health check | liveness | a reachable Qdrant with the wrong collection fails every request |
| in-memory Qdrant tests | mocks | real filter/delete/query semantics without a server |

## Key concepts
- 🔴 **Point:** `(id, vector, payload)`; the payload is what retrieval returns to the LLM.
- 🔴 **Upsert + deterministic ID = idempotent writes:** rerunning overwrites instead of duplicating.
- 🔴 **Delete-by-filter guard:** removes a document's stale positional chunks before re-inserting.
- 🔴 **Query/document asymmetry:** questions use `search_query: `, stored chunks `search_document: `.
- 🔴 **Exact vs approximate search:** below Qdrant's indexing threshold every search is a full scan;
  HNSW trades recall for speed only at larger scale.
- 🟡 **Payload index:** makes filters on a field fast instead of scanning every payload.
- 🟡 **`wait=True`:** the write returns only once the points are searchable.
- 🟡 **Readiness vs liveness:** "can serve requests" vs "process is up".
- ⚪ **UUIDv5:** a UUID derived by hashing a name within a namespace.
- ⚪ **`query_points`:** Qdrant's current query API; `QdrantClient.search` no longer exists in 1.19.

## Honest limitations
1. **Retrieval quality is poor for names.** "What does the Carllm product do?" ranked Lifellm first,
   and no `products/Carllm.md` chunk made the top 10 although that page says "Carllm" 13 times.
   "Where are the offices?" returned only contracts. Dense search alone isn't enough (see Phase 4:
   hybrid search / reranking).
2. `IncompleteDocument` catches gaps (0, 2) but not a document missing its **last** chunks.
3. A document is briefly absent from search during its own re-upsert.
4. Payload indexes are ignored by the in-memory client, so tests can't prove they exist; that was
   checked live only.
5. The zstd decoding failure seen once has **no known root cause**; the gzip pin avoids the code
   path rather than fixing it.
6. `index` re-embeds everything (~190 s) on every run.
7. **Provenance guard gap (found in deep dive 14, verified on a scratch collection):** the check
   compares against `settings`, which the Embedder also reads, so changing `EMBED_DOCUMENT_PREFIX`
   (or the pinned digest together with the model) in the environment makes both sides agree and
   `upsert` accepts vectors from a different space. Fix: also compare with provenance already stored
   in the collection before writing (D-2026-09-17-20, option C). **Closed for the ingestion job in
   P3-M5** (preflight refusal, D-2026-09-17-27); direct `store.upsert` callers still bypass it.

## How this connects
**Previous:** P3-M1 created the collection; P3-M2 pinned the model and prefixes; P3-M3 produced
validated `Embedding`s with provenance. **This step:** stores them durably and retrieves top-k
chunks for a question. **Next:** Phase 4 retrieval work: evaluate retrieval with labelled
questions, then add hybrid (keyword + vector) search and reranking, which the Carllm and offices
failures above show are needed.

## How to do this yourself
1. Read Qdrant's docs on points, filtering, payload indexes and the Query API; note that `search`
   was replaced by `query_points`.
2. Decide what a point's ID is. Check that re-running your upsert doesn't grow the count.
3. Decide what goes in the payload: everything search must return, plus provenance.
4. Write the stale-data guard (delete by `doc_id`) and a test that re-chunks to fewer chunks.
5. Ask what a partial input would do to your guard, and refuse it.
6. Embed queries with the query prefix through the same component as documents.
7. Test against `QdrantClient(":memory:")`, then verify live what local mode can't (indexes).
8. Compare Qdrant's top-k with a numpy brute-force search to prove results are exact at your scale.
9. Mutation-test the guards, and notice when one guard hides another's absence.
10. Ask real questions and write down the failures honestly.

## Practice exercises
1. `python -m src.vectorstore.store index` twice; confirm the point count stays 223.
2. Change `POINT_ID_NAMESPACE` locally, re-index into a scratch collection, and count the duplicates.
3. Add `category` filtering to `search_with_scores` (`query_filter=`), then search only `products`
   for "What does Carllm do?". Does the Carllm page appear now?
4. Add `score_threshold` and find a value that separates good from bad hits for 5 questions.
5. Write 10 questions with known answer chunks and measure hit@5 (your first retrieval evaluation).
6. Implement upsert-then-delete-leftovers (D3 option B) and compare with the current approach.
7. Store a per-document chunk count and extend `IncompleteDocument` to catch missing last chunks.
8. Time `query_points` for k = 1, 5, 50, 223.
9. Use the Qdrant dashboard (http://localhost:6333/dashboard) to browse points and filter by
   `doc_id`.
10. Grep `products/Carllm.md_0` for its opening lines and explain why its vector isn't close to
    "What does the Carllm product do?".
