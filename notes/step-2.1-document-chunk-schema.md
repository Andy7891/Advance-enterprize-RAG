# Step 2.1 — `DocumentChunk` data contract: `src/ingestion/schemas.py`

**Date:** 2026-08-20

### What was built
- [src/ingestion/schemas.py](src/ingestion/schemas.py) — `DocumentChunk`, a frozen
  (immutable) dataclass, plus the utility functions that construct it from a real markdown
  file on disk:
  - `compute_doc_id(source_path)` — deterministic hash of a file's path (relative to
    `data/`), stable across re-ingestion of the same file.
  - `compute_chunk_id(doc_id, chunk_index, text)` — deterministic hash of a chunk's
    position + content; changes if content or chunking strategy changes, stays the same
    otherwise.
  - `infer_category(source_path, data_root)` — reads the top-level `data/` subfolder
    (`company`/`contracts`/`employees`/`products`) as the chunk's category, erroring loudly
    if a file has no category subfolder.
  - `extract_title(text, fallback)` — pulls the first `# H1` heading, falls back to the
    filename stem.
  - `count_tokens_approx(text)` — cheap `len(text) // 4` estimate, not an exact tokenizer
    count (see trade-off below).
  - `discover_markdown_files(data_root)` — walks `data/` for `*.md`, explicitly excluding
    `.ipynb_checkpoints`/`.git`/`__pycache__` noise directories.
  - `make_chunk(...)` — the single call site that assembles a fully-populated,
    correctly-ID'd `DocumentChunk`; all chunk construction should go through this, not
    hand-rolled elsewhere, so the ID logic can never drift between call sites.
- [tests/ingestion/test_schemas.py](tests/ingestion/test_schemas.py) — 14 tests, run against
  the project's **real** `data/` folder (82 actual `.md` files across 4 categories), not
  synthetic fixtures — including tests that the `.ipynb_checkpoints` folders under
  `data/company/`, `data/employees/`, etc. get correctly excluded, and that re-running
  `make_chunk` on the same input twice produces identical IDs (idempotency, proven not just
  asserted).

### Why this approach (trade-offs vs alternatives)
- **Deterministic, content-derived IDs instead of `uuid4()`:** re-ingesting an unchanged file
  reproduces the exact same `chunk_id`/`doc_id`, so a future Qdrant upsert overwrites the
  existing point instead of creating a duplicate — this is what makes ingestion *idempotent*,
  safe to re-run without a separate manual dedup step. `uuid4()` would be simpler code but
  silently double the index on every re-run.
- **`doc_id` derived from PATH, `chunk_id` derived from CONTENT:** a deliberate split. If
  `doc_id` were content-based, editing a contract file would produce an unrelated new
  `doc_id`, orphaning the file's old chunks in the vector store with nothing to identify and
  delete them by. Path-based `doc_id` means "same file" always maps to "same document,"
  regardless of edits — content changes are tracked at the finer-grained `chunk_id` level
  instead.
- **Frozen (`frozen=True, slots=True`) dataclass**, not a plain mutable one: `chunk_id` is
  mathematically derived from `doc_id + chunk_index + text`. A mutable dataclass would let
  other code edit `.text` post-construction and leave `.chunk_id` silently wrong — pointing
  at content that no longer matches. Freezing turns that into a `FrozenInstanceError` at the
  point of the mistake, not a silent data-integrity bug discovered much later during
  retrieval debugging. `slots=True` is a minor, free efficiency win (less per-instance
  memory) with no real downside here.
- **`count_tokens_approx` uses a `len(text) // 4` heuristic, not `tiktoken`:** a real,
  considered trade-off, not laziness. Exact token counts need a specific tokenizer, and
  `tiktoken` (OpenAI's) both couples this schema module to one provider AND can require a
  one-time network fetch of encoding data on first use in some environments — a poor fit for
  a project meant to build/test offline and stay provider-agnostic (this stack already uses
  Cohere for reranking and Ollama for local inference — never intended to be OpenAI-only).
  The approximation is accurate enough to catch "this chunk is way too big," not precise
  enough for exact context-window budgeting — acceptable now, revisit once actual chunk-size
  tuning starts (see practice exercises).
- **`infer_category` raises loudly instead of defaulting to `"unknown"`:** every chunk MUST
  have a real category for the filtering/hybrid-search use case category exists for in the
  first place — a silent `"unknown"` fallback would let a misplaced file quietly degrade
  search quality instead of failing ingestion immediately where it's obvious and cheap to fix.

### Key concepts used
- **Data contract** — a shared shape (here, `DocumentChunk`) that every producer and
  consumer in a pipeline agrees to, so they can be built/tested independently without
  coordinating on internals.
- **Idempotency** — running an operation multiple times with the same input produces the
  same result as running it once; here, achieved via deterministic ID derivation rather than
  random IDs.
- **Content-addressed / deterministic hashing** — deriving an identifier from a hash of the
  content/inputs themselves, rather than generating a random one, so identical inputs always
  produce identical IDs.
- **Immutability (`frozen` dataclass)** — preventing an object's fields from being modified
  after construction, used here to protect a derived-field invariant (`chunk_id` matching
  `text`) from ever silently breaking.

### How this connects to the pipeline
**Previous:** Steps 1.1/1.2 built the environment, containers, and CI — infrastructure with
no actual RAG logic yet. **This step** is the first real ingestion-pipeline code: the shape
every chunk will have from here on.
**Next:** the actual **chunking strategy** (splitting each markdown file's text into
`DocumentChunk`s of reasonable size/overlap, likely `src/ingestion/chunking.py`) — this is
flagged as a genuine core RAG concept to spend real time on, not boilerplate: chunk size and
overlap directly determine retrieval quality (too large → irrelevant context dilutes the
answer; too small → answers lose necessary surrounding context). After that: embedding the
chunks and upserting into Qdrant, using exactly the `DocumentChunk` shape defined here.

### How to do this yourself (reproduce without me)
1. Look at your actual data source structure first (`find data -type f -name "*.md"`) before
   designing any schema — the category/folder convention here came directly from how your
   `data/` folder was already organized, not invented in the abstract.
2. Decide what a "chunk" needs to carry to support every later use case you can already
   foresee: citation (source path), filtering (category), debugging chunk boundaries
   (chunk_index, char_count), and idempotent re-ingestion (a deterministic ID) — design the
   dataclass fields backward from those use cases, not just "text + source."
3. For any ID field, ask: "if I run this twice on the same input, do I want the same ID or a
   different one?" That answer determines whether to hash content, hash a path, or use a
   random UUID — get this wrong and you'll be debugging silent duplicate-or-orphan bugs in
   your vector store much later.
4. Use `dataclasses.dataclass(frozen=True)` for any object with fields that are
   mathematically derived from other fields — it converts a whole class of "field went stale"
   bugs into an immediate, loud error.
5. Write tests against your REAL data directory, not just synthetic strings — a real 82-file,
   4-category knowledge base surfaces edge cases (like `.ipynb_checkpoints` pollution) that
   hand-written test fixtures would never think to include.
6. Run `pytest`, `ruff check`, `ruff format --check`, and `mypy` locally before considering
   the module done — all four check different things (correctness, style, formatting,
   types), and this step's CI pipeline (Step 1.2) will run all four on every push anyway.

### Practice exercises
1. Swap `count_tokens_approx`'s heuristic for real `tiktoken` counts
   (`tiktoken.get_encoding("cl100k_base").encode(text)`) and measure the actual error
   percentage across a sample of your real `data/*.md` files — is char/4 an overestimate or
   underestimate for this specific corpus?
2. Deliberately edit `data/company/about.md`'s content (even by one character) and re-run
   `make_chunk` on it with the same `chunk_index` — confirm `doc_id` stays identical but
   `chunk_id` changes, proving the path-vs-content ID split works as designed.
3. Try removing `frozen=True` from `DocumentChunk`, then rerun
   `test_document_chunk_is_immutable` — watch it fail, and read exactly what `pytest.raises`
   was expecting vs what happened.
4. Add a fifth data category folder (e.g. `data/policies/`) with one dummy `.md` file, rerun
   `test_discover_markdown_files_finds_known_categories`, and update the test's expected set
   — practice the workflow of "data changed, tests need updating too."
5. Call `infer_category` on a file placed directly in `data/` (no subfolder) and confirm it
   raises `ValueError` with a message that would actually help someone debug it.
6. Try hashing the FULL sha256 hexdigest (64 chars) instead of the truncated `[:16]` we use —
   compute the theoretical collision probability difference for a knowledge base of ~10,000
   chunks and decide whether 16 characters was actually a safe truncation.
7. Write a quick script that calls `discover_markdown_files` and `extract_title` across all
   82 real files, and print any file where the extracted title looks wrong (e.g. no H1
   found, so it fell back to the filename) — a real data-quality pass over your own corpus.
8. Add a `content_hash` field to `DocumentChunk` that's independent of `chunk_id` (e.g. hash
   of `text` alone) — think through why you might want this as a SEPARATE field from
   `chunk_id` even though `chunk_id` already incorporates the text (hint: think about
   detecting "did the CONTENT change" vs "did the ID change," across a chunking-strategy
   change that shifts every chunk_index).
9. Benchmark `compute_doc_id`/`compute_chunk_id` against all 82 real files' worth of
   (eventual) chunks — at roughly how many chunks would SHA-256 hashing become a measurable
   bottleneck in ingestion, if ever?
10. Intentionally break one thing at a time (comment out the `.ipynb_checkpoints` exclusion,
    remove the `category subfolder` check, etc.) and predict which specific test in
    `test_schemas.py` will fail before running it — then confirm.

**Correction from this step, worth noting:** earlier chat summaries said "82 real markdown
files" — that count includes `.ipynb_checkpoints/` duplicates. The real, de-duplicated
count (what `discover_markdown_files`/`discover_documents` actually return) is **76**. Fixed
in Step 2.2's tests below; if you still have "82" anywhere in your own notes, it's the raw
on-disk count including checkpoint noise, not the real corpus size.
