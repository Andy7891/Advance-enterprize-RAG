# Phase 3, Microstep 5 — Ingest all chunks into the vector store

**Date:** 2026-09-17

## Audit first: most of the task already existed
P3-M4's `store index` had already loaded, embedded and upserted everything. Checked live before
starting: 223 points from 76 documents, `chunk_id` set identical to `data/chunks/`, 0 orphans.
What was missing was everything that makes ingestion a **job you can trust and inspect**: a log,
orphan handling, the provenance gap from deep dive 14, pre-flight checks and verification.

## What was built
- **`src/vectorstore/ingest.py`**
  - `run_ingestion(client, make_embedder, *, collection, chunks_dir, log_path, rebuild) -> IngestionReport`
  - Stages, each timed: **preflight → load → embed → upsert → orphans → verify**.
  - **Preflight:** build the Embedder (model digest check), `ensure_collection`, count stored points
    from another vector space and refuse unless `--rebuild`.
  - **Orphans:** stored documents absent from disk are deleted, unless `report.json` shows chunking
    failures (then kept, with a warning).
  - **Verify:** stored `chunk_id`s (minus kept orphans) must equal disk's, and no incompatible points
    may remain.
  - The log is written in a `finally` block, so failed runs are logged too; errors are re-raised.
  - CLI: `python -m src.vectorstore.ingest [--collection --chunks-dir --log --rebuild --url --ollama-url]`,
    exit code 0/1.
- **`src/vectorstore/store.py`:** new `ensure_collection()`, `count_incompatible()`,
  `stored_chunk_ids_by_document()` (paged scroll of two payload keys), `delete_documents()`.
  `store index` now delegates to the job.
- **`.gitignore`:** `data/ingestion_log.json`.
- **Tests:** `tests/vectorstore/test_ingest.py`, 12 tests. Suite: **161 passing**, ruff and mypy clean.

## Verified, not assumed
```
full live run      : SUCCEEDED, 223 chunks / 76 docs, points 223 -> 223, verified=True
                     stages: preflight 0.16s, load 0.02s, embed 197.7s, upsert 4.8s, orphans 0.02s, verify 0.05s
provenance refusal : EMBED_DOCUMENT_PREFIX="" -> failed in preflight after 0.2s, 223 incompatible points,
                     real collection untouched (223 points), exit 1
store index alias  : same refusal through `python -m src.vectorstore.store index`
orphans (live)     : scratch collection, 3 docs -> 7 points; delete one chunk file -> rerun -> 4 points,
                     1 orphan removed, verified; scratch collection deleted
mutation tests     : 10 mutants (refusal, rebuild flag, orphan guard, orphan delete, verification,
                     incompatible check in verify, log on failure, failed stage, empty dir, ensure_collection)
                     -> all killed
```
**Mutation testing changed the design twice:**
1. "Verification ignores incompatible points" first **survived**, so a test was added for rebuilding
   while a stale orphan is protected. That case now fails verification instead of being reported
   as verified.
2. "Health check ignored" **survived** because the check could never fail right after
   `ensure_collection`. The dead call was removed (D-2026-09-17-33).

## Why this approach
| Choice | Over | Because |
|---|---|---|
| full re-embed | incremental | always consistent; no change-detection state to get wrong |
| delete orphans with a failure guard | report only | index matches source without trusting a half-failed chunking run |
| compare against stored provenance | compare against settings only | settings change with the embedder, stored points don't |
| embed all, then write | per-document embed+write | embedding failures leave the collection untouched |
| exact chunk_id verification | point count | a count can hide a missing and a stale chunk together |
| log in `finally` | log on success | failed runs are the ones that need a record |

## Key concepts
- 🔴 **Full vs incremental ingestion:** re-embed everything vs only what changed (needs change detection).
- 🔴 **Sync semantics / orphans:** an index must also *remove* what the source no longer contains.
- 🔴 **Index compatibility:** new writes must match the vector space of what's already stored.
- 🟡 **Pre-flight checks:** cheap failures before expensive work.
- 🟡 **Post-run verification:** compare the result with the source instead of trusting "no exception".
- 🟡 **Partial failure / atomicity:** fail fast leaves a possibly mixed index; blue/green + alias
  avoids it.
- ⚪ **Run log:** structured JSON with UTC timestamps and per-stage `perf_counter` timings.
- ⚪ **`try/except/finally` + re-raise:** record a failure without swallowing it.

## Honest limitations
1. Every run re-embeds everything (~198 s) even when nothing changed.
2. A failure during the upsert stage can leave some documents new and some old until the next run.
3. The orphan guard trusts `report.json`; if that file is deleted after a failed chunking run,
   missing documents are deleted from the index.
4. The provenance refusal protects **this job**; code calling `QdrantVectorStore.upsert` directly can
   still write from a changed configuration.
5. The log keeps only the latest run; a failed run's record is overwritten by the next attempt.
6. Verification proves which IDs are stored, not that each vector matches its text.

## How this connects
**Previous:** P3-M3 embeds chunks; P3-M4 stores and searches them. **This step:** turns "run the
indexer" into a repeatable, verified, logged job that keeps the index in sync with `data/chunks/`.
**Next:** retrieval quality. P3-M4 and deep dive 14 showed dense search misses exact names
(Carllm), so evaluation with labelled questions and hybrid search come next.

## How to do this yourself
1. Before building, check what already exists: count stored points and compare `chunk_id` sets
   with disk.
2. Write the stages down in order and mark which ones are expensive.
3. Move every cheap check before the expensive stage.
4. Decide what "the index matches the source" means, including deletions, and when *not* to delete.
5. Compare new writes with what's **stored**, not only with configuration.
6. Build a report object filled stage by stage; write it in `finally`; re-raise errors.
7. Verify the result with an exact comparison.
8. Test each refusal and failure with in-memory Qdrant and a fake embedding client.
9. Mutation-test the guards; delete code that tests prove can't matter.
10. Run it live, including a deliberate refusal and a scratch-collection orphan test.

## Practice exercises
1. Run `python -m src.vectorstore.ingest` twice and compare `stage_seconds` between the two logs.
2. `EMBED_DOCUMENT_PREFIX="" python -m src.vectorstore.ingest` against `chunks`. Read the refusal.
   **Don't add `--rebuild`.**
3. Make the log append to `data/ingestion_log.jsonl` as well, and plot embed seconds per run.
4. Implement hash-based incremental ingestion: store `sha256` of each document's chunk texts in the
   payload and skip unchanged documents. Write the test that catches a stale skip.
5. Stop Ollama mid-embed (`docker compose stop ollama`) and inspect the failed log. Is the
   collection unchanged?
6. Simulate a failed chunking run: set `documents_failed: 1` in a scratch `report.json`, delete a chunk
   file, and confirm the orphan is kept.
7. Implement blue/green ingestion into `chunks_<timestamp>` with a Qdrant alias switch.
8. Add a `--dry-run` flag that runs preflight, load and orphan detection but writes nothing.
9. Add per-document timings to the log and find the slowest document to embed.
10. Explain, from the log alone, how long a run would take for 10× the chunks, and what would
    dominate.
