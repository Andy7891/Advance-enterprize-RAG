# Architecture

## Embedding model (locked P3-M2, 2026-09-16)

| Property | Value | How it was established |
|---|---|---|
| Model | `nomic-embed-text` (served by Ollama) | Chosen P3-M1; already used for semantic chunking |
| Pinned digest | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` | Read from Ollama `/api/tags` |
| Dimension | 768 | Measured by embedding a real string |
| Context length | 2048 model tokens | `ollama show nomic-embed-text` |
| Distance | Cosine | Model is trained for it; vectors measured at norm 1.0 |
| Query prefix | `search_query: ` | nomic model card |
| Document prefix | `search_document: ` | nomic model card |
| Hard chunk cap | 1500 tokens, counted as chars÷4 (6000 chars) | Set below the measured rejection point |
| Qdrant collection | `chunks` | P3-M1 |

**Single source of truth:** `Settings` in [`src/core/config.py`](../src/core/config.py).
Everything that depends on the model — the chunker's embedder default, the pipeline CLI,
and the Qdrant collection size — reads from it. Any value can be overridden via `.env`.

### Why this model

- **One model for both boundaries and retrieval.** The chunker already embeds sentences with
  `nomic-embed-text` to find topic shifts, so the same notion of "similar" defines both where
  chunks are cut and what a query retrieves.
- **Local, free, offline.** It runs in the Ollama container already defined in
  `docker-compose.yml`; no API key, no per-call cost.
- **Rejected:** `all-MiniLM-L6-v2` (384 dims — needs sentence-transformers and torch, and a
  different model from the chunker's); OpenAI `text-embedding-3-small` (1536 dims — API key,
  cost, network dependency, a third provider).

### Measured evidence behind the lock

**Context limit is a hard failure, not a quality issue.** Embedding every chunk from the
first pipeline run against the live model:

```
chunks rejected: 1 / 214     error: "the input length exceeds the context length"
rejected         13,599 chars / 2,788 tiktoken tokens
largest accepted 10,530 chars / 2,119 tiktoken tokens
```
The original pipeline never hit this because the chunker only embeds single sentences.
Fix: a hard cap in the chunker that re-splits any chunk over 6,000 characters at sentence
boundaries (words as a last resort), applied after merging so it is always the final word.

**Task prefixes were kept on the model card's recommendation, not on measurement.** A
2-query spot check:

```
                            no prefixes            with prefixes
When was Insurellm founded? relevant .600 irrel .431  relevant .650 irrel .526
                            gap .169                  gap .124
What does Carllm do?        gap .107                  gap .083
```
Prefixes raised every score but narrowed the relevant/irrelevant gap in both cases. Two
queries is not evidence either way. The prefix is **configurable** and must be decided by a
proper retrieval evaluation. Changing it later requires re-embedding the collection, because
the document prefix is baked into every stored vector.

### Invariants that must never drift

1. **Digest.** `src/embedding/lock.py` compares the served model's digest to the pin and
   refuses to run on a mismatch, in both `python -m src.ingestion.pipeline` and
   `python -m src.vectorstore.setup`. A re-pull that changes the weights stops the pipeline
   instead of silently mixing vector spaces.
2. **Dimension.** The Qdrant collection is created from `settings.embed_dim`, and
   `ensure_collection` refuses a collection with any other size.
3. **Prefixes.** Documents are stored with `as_document()`, queries embedded with
   `as_query()`. Mixing prefixed and unprefixed vectors in one collection is a silent error.

### Changing the model (the full procedure)

Every step is required; skipping any leaves old and new vectors in different spaces.

1. Pull the new model and measure its dimension and context length.
2. Update `embed_model`, `embed_model_digest`, `embed_dim`, `embed_context_tokens`,
   `embed_max_chunk_tokens` and prefixes in `src/core/config.py`.
3. Delete the Qdrant `chunks` collection.
4. Re-run the ingestion pipeline (chunk boundaries depend on the model too).
5. Recreate the collection and re-embed everything.

## Embedding pipeline (P3-M3, 2026-09-17)

`src/embedding/embedder.py` is the only path from chunk text to stored vectors, and enforces
the locks above rather than trusting callers:

| Guarantee | Where |
|---|---|
| Served model digest == pin | `Embedder.__init__` (calls `verify_model`) |
| `search_document: ` on every chunk | `Embedder._embed_one_batch` |
| Over-length input refused, never truncated | `truncate=False` on every request |
| Count, 768 dims, finite, unit norm | `Embedder._validate`, per batch |
| Provenance on every vector | `Embedding.model`, `.model_digest`, `.document_prefix` |

Measured: 223 chunks in 199.9s at batch size 16 on CPU; batched vectors identical to single.
Run: `python -m src.embedding.embedder`.
