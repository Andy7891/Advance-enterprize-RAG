# Phase 3, Microstep 2 — Choose & lock the embedding model

**Date:** 2026-09-16

## Audit: the model was already chosen — the real work was elsewhere
`nomic-embed-text` (768 dims) was chosen in P3-M1. Auditing what "lock" should mean found:

1. **A hard failure hidden since Phase 2.** Embedding every real chunk against the live
   model: **1/214 rejected** — `"the input length exceeds the context length"` (13,599 chars).
   The pipeline had never sent a whole chunk, only single sentences, so it never surfaced.
2. **The model name hardcoded in three unconnected places** — `chunker.py`, `pipeline.py`, and
   `setup.py`'s `VECTOR_SIZE = 768`.
3. **No version pin.** `nomic-embed-text:latest` is a moving tag.
4. **`src/config.py` doesn't exist**; the real config is `src/core/config.py`.

## What was built
- **`src/core/config.py`** — `Settings` now holds `embed_model`, `embed_model_digest`,
  `embed_dim`, `embed_context_tokens`, `embed_max_chunk_tokens`, both prefixes, and
  `qdrant_collection`. All three former hardcoded sites read from it.
- **`src/embedding/lock.py`** — `verify_model()` compares Ollama's served digest to the pin
  and raises `ModelVersionMismatch` on a difference or a missing model; `as_query()` /
  `as_document()` apply the task prefixes. Called at startup by the pipeline and Qdrant setup.
  CLI: `python -m src.embedding.lock`.
- **Hard token cap in `chunker.py`** — `_split_oversized()` runs after merging and re-splits
  any chunk over 6,000 chars at sentence boundaries (words as a last resort), recomputing
  offsets inside the original span.
- **`docs/architecture.md`** — the locked config, the measured evidence, and the procedure for
  changing models.
- **Tests:** 7 lock tests (injected fetcher, offline), 7 cap tests. 115 passing.

## Verified, not assumed
```
live lock check        : digest 0a109f422b47 matches pin
cap mutation test      : cap disabled -> 3 tests fail
real pipeline re-run   : 76 docs, 223 chunks, max tokens 2788 -> 1265
before -> after cap    : chunks > 6000 chars: 8 -> 0, largest 13,599 -> 5,983
every chunk embedded   : 0 / 223 rejected (with document prefix)
contract tests         : pass on the new dataset
```

## Honest limitations
1. **69% of chunks still exceed the 500-char soft target** (156/223). The cap prevents
   failures; it doesn't make chunks well-sized.
2. The cap counts chars/4, not nomic's tokenizer; unusually dense text could still exceed the
   model, which is why every chunk was verified live.
3. Forced splits ignore meaning — an oversized section is cut mid-topic.
4. Prefixes are on by the model card, not by measurement; a 2-query check pointed the other way.
5. A deliberate model upgrade fails until the pinned digest is updated by hand.

## Key concepts
- **Context window** — the maximum input a model accepts; exceeding it is rejected, not truncated, by Ollama here.
- **Model pinning** — identifying weights by digest rather than a mutable name/tag.
- **Asymmetric embedding / task prefixes** — different instructions for queries and documents.
- **Single source of truth** — one config object every dependent module reads from.
- **Embedding space compatibility** — vectors are only comparable if produced by the same model, version and prefix scheme.

## How this connects
**Previous:** P3-M1 created a 768-dim cosine collection. **This step:** made every chunk
embeddable and made the model impossible to change silently. **Next:** P3-M3 embeds all 223
chunks with `as_document()` and upserts to `chunks` — with a delete-by-`doc_id` guard, because
chunk IDs are position-derived.

## How to do this yourself
1. `ollama show <model>` — note context length and embedding length.
2. Embed EVERY real chunk once and count errors; don't assume the chunker's sizes fit.
3. `curl localhost:11434/api/tags` — record the digest and pin it in config.
4. Grep the codebase for the model name and dimension; route every occurrence through config.
5. Read the model card for prefixes/instructions; make them config, not code.
6. Add a startup check that refuses to run on a digest mismatch.
7. Write down the full procedure for changing models before you ever need it.

## Practice exercises
1. Set `embed_model_digest` to a wrong value and run `python -m src.embedding.lock`.
2. Lower `embed_max_chunk_tokens` to 500 and rerun the pipeline — how many chunks now?
3. Find the densest chunk (highest tiktoken tokens per char) and embed it doubled to find where it fails.
4. Build a 20-query labelled set and compare prefixes on vs off properly.
5. Pull `mxbai-embed-large`, record its dim/context, and write the config diff you'd need.
6. Check whether Ollama truncates or rejects when `num_ctx` is set lower.
7. Measure nomic token counts (via a tokenizer) vs chars/4 on 20 chunks.
8. Make the cap warning log the chunk index and piece count.
9. Write a test that fails if any hardcoded `768` or `"nomic-embed-text"` reappears in `src/`.
10. Time embedding one 6,000-char chunk vs one 500-char chunk.
