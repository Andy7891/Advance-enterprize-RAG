# Advance RAG

A production-grade Retrieval-Augmented Generation system, built step by step with the
reasoning recorded rather than just the result.

- **Build log, one file per step:** [notes/](notes/) ([index](notes/README.md))
- **Deep dives** (concept-by-concept, with exercises): [docs/](docs/)
- **Decision log** (decision | alternative rejected | reason | what would flip it):
  [decision.md](decision.md)
- **Learning log:** [LEARNING_LOG.md](LEARNING_LOG.md)

## Pipeline status

| Phase | Step | Status |
|---|---|---|
| 1 | Environment, Docker, CI | done |
| 2 | Loader → chunker → enrichment → pipeline | done |
| 2 | Chunk schema sign-off | done (this document) |
| 3 | Qdrant collection, model lock (M1–M2) | done |
| 3 | Batch embedding (M3): 223 chunks, validated | done |
| 3 | Qdrant vector store (M4): 223 points indexed, search + health | done |

Run the ingestion pipeline:

```bash
docker compose up -d ollama
docker exec advancerag-ollama-1 ollama pull nomic-embed-text
python -m src.ingestion.pipeline
```

## Chunk schema (signed off 2026-09-16)

Every file in `data/chunks/` is a JSON array of chunk objects with exactly these fields.
The authoritative definition is the `DocumentChunk` dataclass in
[src/ingestion/schemas.py](src/ingestion/schemas.py); this table describes it for humans, and
`tests/ingestion/test_chunk_contract.py` asserts the two agree.

<!-- chunk-schema:start -->

| Field | Type | Meaning |
|---|---|---|
| `chunk_id` | `str` | `"{source_path}_{chunk_index}"`, e.g. `company/about.md_3`. Position-derived, **not** content-derived. |
| `doc_id` | `str` | 16-char SHA-256 of the source path. Stable across edits, so all of a document's chunks stay groupable. |
| `source_path` | `str` | Path relative to `data/`, POSIX-style, e.g. `company/about.md`. |
| `category` | `str` | Top-level `data/` subfolder: `company`, `contracts`, `employees`, `products`. |
| `title` | `str` | First `# H1` heading, or the filename stem if the document has none. |
| `chunk_index` | `int` | 0-based position within its document. Contiguous with no gaps. |
| `text` | `str` | The chunk content itself. Never empty or whitespace-only. |
| `char_count` | `int` | `len(text)`. |
| `approx_token_count` | `int` | Real `tiktoken` `cl100k_base` count. Always equal to `metadata.tokens_approx`. |
| `metadata` | `dict` | Exactly three keys: `start_char`, `end_char`, `tokens_approx`. |

<!-- chunk-schema:end -->

### Verified invariants

Asserted by `tests/ingestion/test_chunk_contract.py` against real pipeline output —
**223 chunks across 76 documents, all passing** (re-verified after the P3-M2 hard token cap;
214 at original sign-off):

- `char_count == len(text)`
- `approx_token_count == metadata["tokens_approx"]`
- `chunk_id == f"{source_path}_{chunk_index}"`
- `text` is never empty or whitespace-only
- `0 <= start_char < end_char`, and `end_char` is within the source document
- `source_text[start_char:end_char].split() == text.split()`
- `chunk_index` values are contiguous `0..N-1` within each document
- every chunk has identical top-level keys and identical `metadata` keys

Note on the offsets: byte-for-byte `source_text[start:end] == text` is **false by design**.
The splitter rebuilds chunk text with single spaces while the source may contain blank
lines, so word-level equality is the correct criterion (see
[decision.md](decision.md), D-2026-09-15-15).

### Scope of this sign-off

This certifies the **format and internal consistency** of the chunk data. It does **not**
certify chunk *quality*.

- **Fixed in P3-M2:** the largest original chunk (13,599 chars) was rejected by the embedding
  model's 2048-token context. A hard cap now re-splits anything over 6,000 characters; the
  largest chunk is **5,983 chars** and **all 223 chunks embed successfully** against the
  pinned model (see [docs/architecture.md](docs/architecture.md)).
- **Still open:** **156 of 223 chunks (69%) exceed the 500-character soft target.** The cap
  prevents failures; it does not make chunks well-sized. See
  [notes/step-2.4-ingestion-pipeline.md](notes/step-2.4-ingestion-pipeline.md).

## Reproducing the checks

```bash
pytest tests/ -q                                    # full suite
pytest tests/ingestion/test_chunk_contract.py -v    # the schema contract only
pytest tests/ --cov=src --cov-report=term-missing   # coverage
```

The contract tests **skip** when `data/chunks/` is empty, which is the normal state on a
fresh clone and in CI (the directory is gitignored generated output). Run the pipeline first
to enforce them for real.
