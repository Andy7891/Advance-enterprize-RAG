# Phase 2, Microstep 2 — Semantic chunking: `src/ingestion/chunker.py`

**Date:** 2026-09-15

### Pre-flight audit (what we'd forgotten)
Before building, an audit of the repo found real gaps, all now closed:
- **No `.gitignore`, and 32,509 `.venv/` files were committed to git.** Untracked
  forward-only (the Initial commit was already pushed to GitHub, so a history rewrite would
  have required force-pushing published `main` — see decision.md D-2026-09-15-12).
- **CI was green while asserting nothing** — all five `ci.yml` steps still `echo`'d TODO
  placeholders. Now they run `ruff check`, `ruff format --check`, `mypy`, `pytest`, and
  `docker build` for real.
- Three files failing `ruff format --check`, and a stray `xxxx` in `src/api/main.py`'s
  docstring — both fixed.
- The Docker build had never been re-verified after the Fix #2 packaging change. Now
  confirmed passing.

### What was built
- [src/ingestion/chunker.py](src/ingestion/chunker.py):
  - `Embedder` — a `Protocol` matching LangChain's embeddings interface, so tests inject a
    deterministic fake and CI never needs a live model server.
  - `ChunkerConfig` — frozen config (`breakpoint_threshold_type`,
    `breakpoint_threshold_amount`, `target_chunk_chars`, `min_chunk_chars`, `buffer_size`).
  - `Chunker` — wraps `SemanticChunker`, recovers char offsets, merges undersized chunks
    backwards, warns on oversized ones, and emits `DocumentChunk`s via `make_chunk`.
  - `build_ollama_embedder()` — factory kept separate so importing the module opens no
    network connection.
- **Contract 1 amended** in [schemas.py](src/ingestion/schemas.py): `compute_chunk_id` now
  takes `(source_path, chunk_index)` and returns `"company/about.md_3"`; `make_chunk`
  accepts optional `start_char`/`end_char` that land in `metadata`.
- [tests/ingestion/test_chunker.py](tests/ingestion/test_chunker.py) — 13 tests using a fake
  embedder that maps keywords to vectors, so topic switches produce real cosine distance.
- Dependencies: `langchain-experimental==0.4.2`, `langchain-ollama==1.1.0`, and
  `langchain-community` bumped `0.3.30 → 0.4.2` (required by langchain-experimental).

### Key concepts
- **Semantic chunking** — cutting text where *meaning* shifts (measured by cosine distance
  between consecutive sentence embeddings) rather than at fixed character counts.
- **Breakpoint threshold** — the rule deciding which distances count as boundaries; we use
  `standard_deviation` (mean + 1.5σ), so uniform documents aren't force-split.
- **Soft vs hard size cap** — semantic boundaries can't honour a hard limit without
  overriding the signal they exist to follow, so `target_chunk_chars` logs rather than cuts.
- **Dependency inversion via Protocol** — depending on an interface, not a concrete
  embedder, which is what makes the tests offline and fast.

### Honest risks carried by this design
1. **Positional `chunk_id` has no content sensitivity.** Re-chunking with different settings
   reuses IDs for different text. If a run produces *fewer* chunks than before, surplus old
   chunks linger in the vector store forever and can still be retrieved. **The upsert layer
   MUST delete all chunks for a `doc_id` before re-ingesting.** A test
   (`test_compute_chunk_id_does_NOT_change_when_text_changes`) documents this deliberately.
2. **`langchain-experimental` is officially sunset**, and `langchain-community` is being
   sunset too — both emit `DeprecationWarning` on import. A core pipeline stage now depends
   on retired packages (decision.md D-2026-09-15-05).
3. **Ollama on CPU is slow.** A full 76-document run embeds thousands of sentences.

### How this connects to the pipeline
**Previous:** `loader.py` produced `LoadedDocument`s (raw text + metadata).
**This step:** turns that text into `DocumentChunk`s — the unit everything downstream
retrieves. **Next:** embedding those chunks and upserting into Qdrant, where the
delete-by-doc_id guard above becomes mandatory, not optional.

### How to do this yourself
1. Choose a chunking strategy deliberately; read
   [Pinecone's chunking guide](https://www.pinecone.io/learn/chunking-strategies/) and
   [Kamradt's 5 Levels of Text Splitting](https://github.com/FullStackRetrieval-com/RetrievalTutorials/blob/main/tutorials/LevelsOfTextSplitting/5_Levels_Of_Text_Splitting.ipynb).
2. Before adopting any library, run `pip install --dry-run` to see what it upgrades, and
   import it once to check for deprecation warnings — that is how the sunset status of
   `langchain-experimental` surfaced here.
3. Inject the embedder rather than constructing it inside the chunker, so tests can fake it.
4. Write the fake embedder to encode *topic* (keyword → vector), so cosine distance behaves
   realistically without a model.
5. Whenever an ID scheme drops a guarantee, write a test asserting the *new* behaviour so
   the lost guarantee stays visible instead of being rediscovered in production.

### Practice exercises
1. Run the chunker on `data/company/about.md` with `breakpoint_threshold_amount` at 0.5, 1.5
   and 3.0 — plot chunk count against threshold and find where it stops changing.
2. Switch `breakpoint_threshold_type` to `"percentile"` and compare chunk-count variance
   across 10 documents versus `standard_deviation`.
3. Feed a deliberately uniform document (the same sentence 50 times) and confirm
   `standard_deviation` produces exactly one chunk — the documented failure mode.
4. Set `min_chunk_chars=10_000` and verify everything merges to a single chunk; then reason
   about why the *first* chunk is never dropped.
5. Print `start_char`/`end_char` for every chunk of one file and use them to slice the
   original text — confirm the slices reconstruct the document (modulo merge whitespace).
6. Time `chunk()` with the fake embedder vs real Ollama on one document; compute the
   per-sentence embedding cost and extrapolate to all 76 files.
7. Write a chunk whose text contains no sentence-ending punctuation at all and see how
   `sentence_split_regex` handles it.
8. Add a `strategy="recursive"` branch using `RecursiveCharacterTextSplitter` and diff its
   boundaries against the semantic ones on the same document.
9. Deliberately re-chunk a file with two different configs and inspect the resulting
   `chunk_id`s — reproduce the orphaned-chunk risk from Risk #1 on paper.
10. Replace `nomic-embed-text` with a different Ollama embedding model and check whether the
    chunk boundaries move — evidence for how model-specific your tuning actually is.

---

## Follow-up (same day): offset-drift bug found and fixed

Writing `docs/6 chunker_deep_dive.md` surfaced a real bug in this step's code: the
`start_char`/`end_char` values stored in `DocumentChunk.metadata` were **wrong for every
chunk after the first**, in any document where sentences are separated by a blank line —
which is most of the real markdown corpus.

**Root cause** (read from the installed library source, not its docs): `SemanticChunker`
rebuilds each chunk by re-joining sentences with a single `" "`, then advances its internal
`start_index` tracker by that REBUILT length. Wherever the source had `\n\n` (2 chars) but
the rebuild has `" "` (1 char), every subsequent offset drifts further out.

**The fix that did NOT work, and why it's instructive:** the obvious repair — "ignore the
library's tracker, just `str.find` the chunk text in the source" — was implemented and
verified to FAIL. The same whitespace normalisation means the rebuilt chunk usually isn't
verbatim present in the source, so `find` returns `-1` and falls back to the broken
estimate. It made the bug louder (a warning) without fixing it.

**The fix that works:** `Chunker._locate` tries the cheap exact `str.find` first, then falls
through to a regex built from the chunk's words joined by `\s+`, which tolerates any
whitespace difference. `Chunker._recover_spans` drives this over all chunks, advancing a
`search_from` cursor so a later identical chunk can't match an earlier position.

**The second half of the fix was redefining "correct".** `source[start:end] == chunk.text`
is impossible by construction and will always be `False`. The tests now assert word-level
equality (`.split() == .split()`), which proves the span covers the right source REGION —
which is what a UI highlight actually needs. Four new tests cover this; 42 tests pass.

Recorded as `decision.md` D-2026-09-15-14 and D-2026-09-15-15.
