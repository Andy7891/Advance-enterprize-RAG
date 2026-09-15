# Phase 2, Microstep 3 — Token counting & metadata enrichment

**Date:** 2026-09-15

## Pre-flight audit (what the spec collided with)

Auditing before building surfaced three conflicts worth recording:

1. **The offset-drift bug was still open** — the delegated background task had produced a
   fix that was verified NOT to work. That was fixed and landed first (commit `8666b777`,
   see [step-2.2-semantic-chunker.md](step-2.2-semantic-chunker.md)).
2. **`approx_token_count` already existed** as a populated first-class field, so the spec's
   `metadata.tokens_approx` would have created a second, potentially disagreeing number.
3. **The spec's "use the same tokenizer throughout" was already violated by its own
   suggestion.** It suggests `tiktoken` (OpenAI's BPE), but this pipeline embeds with
   `nomic-embed-text` via Ollama, which tokenizes differently. Accepted deliberately — see
   the caveat below.

## What was built

- [src/ingestion/enrichment.py](../src/ingestion/enrichment.py):
  - `TokenCounter` — a `Protocol` (same pattern as `Embedder` in `chunker.py`), so tests
    inject a fake and the suite never loads tiktoken's encoding file.
  - `TiktokenCounter` — loads the encoding **eagerly in the constructor**, so a missing or
    unfetchable encoding fails at wiring time rather than midway through a corpus.
  - `enrich_with_token_counts(chunks, counter)` — a **pure** function returning NEW chunks
    via `dataclasses.replace` and a fresh metadata dict; input chunks are never modified.
  - `build_tiktoken_counter()` — factory keeping the encoding load out of import time.
- `tiktoken==0.14.0` pinned in `pyproject.toml` and `requirements.txt`. It was previously
  present only as a **transitive** dependency of `langchain-openai` — any LangChain change
  could have removed it silently.
- [infra/docker/Dockerfile](../infra/docker/Dockerfile): pre-warms the tiktoken encoding
  cache at build time into an explicit `TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache`, and copies
  that directory into the runtime stage. Multi-stage builds share nothing by default, and
  tiktoken's default cache location is a temp dir that would not survive the stage boundary.
- [tests/ingestion/test_enrichment.py](../tests/ingestion/test_enrichment.py) — 12 tests.
  The one test that genuinely loads tiktoken is marked `@pytest.mark.tiktoken` so it can be
  deselected (`pytest -m "not tiktoken"`), keeping the other 53 fully offline.

## Why this approach (trade-offs)

- **tiktoken over nomic's own tokenizer:** counting with the embedding model's real
  tokenizer would require a live Ollama server just to count tokens, undoing the offline
  test property built in M2. tiktoken gives a consistent, portable ruler for comparing
  chunks to each other — at the cost of not predicting what the embedding model sees.
- **Fail loudly, no heuristic fallback:** a silent fallback would make
  `metadata["tokens_approx"]` mean two different things depending on network conditions.
  The Docker cache pre-warm removes the main reason a fallback would be tempting.
- **Both numbers carry the same value:** keeping `approx_token_count` on char÷4 while
  metadata held tiktoken would force every reader to know which to trust. The gap is real,
  not theoretical — measured on real corpus text: **heuristic 100 vs tiktoken 79, a ~27%
  overestimate.**
- **Pure function over in-place mutation:** `DocumentChunk` is frozen to prevent
  post-construction edits. Mutating `chunk.metadata` in place works only by exploiting a
  gap in that guarantee (frozen blocks reassignment, not dict mutation).

## Key concepts

- **Tokenizer** — the component that splits text into the units a model actually processes;
  different model families use different ones, so counts are not interchangeable.
- **BPE (Byte Pair Encoding)** — the tokenization family tiktoken implements, building a
  vocabulary by repeatedly merging the most frequent adjacent pairs.
- **Pure function** — same inputs always produce the same output, with no side effects on
  the inputs; what makes `enrich_with_token_counts` safe to re-run.
- **Cache pre-warming** — fetching a runtime dependency during build, while network is
  still available, so the running container never needs it.
- **Test marker** — a pytest label (`@pytest.mark.tiktoken`) letting a subset of tests be
  selected or deselected, here separating offline tests from the one needing an encoding.

## Honest risks carried

1. **The counts measure the wrong model.** `cl100k_base` is OpenAI's; we embed with
   `nomic-embed-text`. Fine for comparing chunk sizes to each other; NOT valid for deciding
   "will this fit in the embedding model's context window."
2. **`approx_token_count`'s name now lies** — it holds an exact count, not an approximation.
3. **Pure function means callers can forget the return value.** `enrich(chunks)` with the
   result discarded silently does nothing.
4. **CI is only offline because tests inject a fake.** The Docker cache protects the
   container, not the GitHub Actions runner.

## How this connects to the pipeline

**Previous:** M2 produced `DocumentChunk`s with a char÷4 token estimate.
**This step:** replaces that estimate with a real tokenizer count.
**Next:** embedding those chunks and upserting into Qdrant — where the token count becomes
the input to batching decisions, and where the delete-by-`doc_id` guard (from M2's
positional `chunk_id` scheme) becomes mandatory.

## How to do this yourself

1. Check whether the tokenizer you're about to adopt matches the model you actually run —
   if not, decide explicitly whether a "consistent ruler" is good enough for your use case.
2. Check whether the library is a real dependency or just a transitive one:
   `pip show <pkg>` then `grep <pkg> pyproject.toml`. Pin it if you depend on it.
3. Time the first call (`tiktoken.get_encoding(...)`) and check for a cache directory
   afterwards — that's how you discover a hidden network dependency before it fails in CI.
4. Make the counter injectable behind a `Protocol` so tests never need the real thing.
5. For a frozen dataclass, use `dataclasses.replace` plus a fresh dict for nested mutables;
   mutating the nested dict directly works but defeats the immutability guarantee.
6. Pre-warm caches in the Docker BUILD stage and copy them into the runtime stage — nothing
   crosses a stage boundary implicitly.

## Practice exercises

1. Measure the heuristic-vs-tiktoken gap across all 76 real documents — is the ~27%
   overestimate consistent, or does it vary by category (contracts vs employee bios)?
2. Swap `cl100k_base` for `o200k_base` (GPT-4o's encoding) and compare counts on the same
   chunk — how much does the choice of encoding alone move the number?
3. Delete your tiktoken cache directory, disconnect from the network, and run the
   `@pytest.mark.tiktoken` test — confirm it fails loudly rather than silently degrading.
4. Run `docker run --rm --network none advance-rag:ci python -c "import tiktoken;
   tiktoken.get_encoding('cl100k_base'); print('ok')"` — prove the baked cache works with
   networking completely disabled.
5. Write a caller that ignores the return value (`enrich_with_token_counts(chunks, c)` with
   no assignment) and confirm nothing changes — the documented failure mode of a pure
   function.
6. Add a `tokens_per_char` ratio to the metadata during enrichment and see which documents
   in the corpus tokenize least efficiently.
7. Try enriching with a counter that returns 0 for everything — which downstream assumption
   breaks first?
8. Benchmark `enrich_with_token_counts` over all 76 documents' chunks — is tokenization or
   chunking the bigger cost in the ingestion pipeline?
9. Implement an `OllamaTokenCounter` that asks Ollama for the real `nomic-embed-text` token
   count, and compare it against tiktoken's number on the same chunks — quantify the
   mismatch this step deliberately accepted.
10. Remove the `markers` registration from `pyproject.toml` and rerun the suite — read the
    warning pytest produces, and decide whether strict marker registration is worth it.
