# Decision Log

One line per decision: **decision | alternative rejected | reason | condition that would flip it**.
Newest at the bottom. Fuller reasoning lives in `NOTES.md`; this file is the scannable index.

---

## Phase 2, Microstep 2 — Text Chunking Strategy (2026-09-15)

**D-2026-09-15-01 — chunk_id scheme**
`chunk_id = f"{source_path}_{index}"` | SHA-256 content hash of (doc_id, index, text) | Human-readable IDs are trivially debuggable in logs, Qdrant payloads and error messages (`company/about.md_3` vs `a3f9c1b7e2d84f01`), and the spec called for it explicitly | Flip back to hashing the moment re-ingestion runs without a delete-by-doc_id guard, or if silent index corruption is ever observed in practice.

**D-2026-09-15-02 — start_char / end_char placement**
Stored inside `DocumentChunk.metadata` | First-class typed fields on the dataclass | Avoids widening the frozen Contract 1 field list that 14 tests and 3 deep-dive docs already reference; keeps the amendment additive | Flip to first-class fields as soon as retrieval or UI code reads offsets on a hot path, where a `KeyError` from a typo'd `metadata["strat_char"]` would be a production bug rather than a test failure.

**D-2026-09-15-03 — chunking strategy**
Semantic chunking (embedding-based boundary detection) | Recursive character splitting; fixed-size windows; markdown-header splitting | Cuts on actual meaning shifts rather than arbitrary character counts, giving chunks that are internally coherent and therefore embed more cleanly | Flip to recursive character splitting if ingestion latency becomes unacceptable, or if measured retrieval quality does not beat the recursive baseline enough to justify ~5-8k extra embedding calls per full run.

**D-2026-09-15-04 — embedding provider for boundary detection**
Ollama (`nomic-embed-text`, local) | Cohere API; sentence-transformers in-process | Free, offline, no API key, and already a running service in `docker-compose.yml` with `OLLAMA_URL` plumbed through `Settings` | Flip to Cohere if CPU inference time makes full-corpus chunking impractical and an API budget exists; flip to sentence-transformers if offline is required but Ollama's server dependency becomes a burden.

**D-2026-09-15-05 — chunker implementation**
`langchain_experimental.SemanticChunker` | Hand-rolling the ~60-line algorithm | Battle-tested implementation with four threshold methods already built; ships fastest | **Flip condition is already partially met** — the package emits `DeprecationWarning: langchain-experimental is being sunset and is no longer actively maintained`. Flip to a hand-rolled implementation at the first breaking upgrade, or before any production deployment.

**D-2026-09-15-06 — breakpoint threshold method**
`standard_deviation` (mean + 1.5σ) | `percentile`; absolute distance threshold | Cuts only where a distance is statistically unusual *for that document*, so a genuinely uniform document is not forced to split at invented boundaries | Flip to `percentile` if chunk-count variance across documents becomes unmanageable, since percentile guarantees a predictable number of cuts per document.

**D-2026-09-15-07 — undersized trailing chunks**
Merge backwards into the predecessor chunk | Keep as-is; drop below a min-size threshold | A 30-character fragment embeds to near-noise and pollutes retrieval with context-free hits, while dropping it would silently lose content (e.g. "Total contract value: $2.4M") | Flip to keeping them if a hard maximum chunk size ever becomes a real constraint, since merging deliberately breaks the size cap.

**D-2026-09-15-08 — chunk size as soft cap**
`target_chunk_chars=500` logged-but-not-enforced | Hard split at 500 chars | Semantic chunking derives boundaries from meaning; hard-splitting would override the exact signal the strategy exists to follow, reintroducing the mid-sentence cuts we chose semantic chunking to avoid | Flip to hard enforcement if a downstream embedding model has a strict token limit that oversized chunks actually exceed.

**D-2026-09-15-09 — overlap parameter**
Dropped entirely (not implemented) | Implementing ~15% overlap as originally chosen | Overlap is a sliding-window concept with no meaning in semantic chunking — boundaries are detected, not stepped, so there is no window to overlap | Flip if the strategy ever changes to recursive/fixed-size, where overlap becomes both meaningful and necessary.

**D-2026-09-15-10 — Ollama client library**
`langchain-ollama==1.1.0` | `langchain_community.embeddings.OllamaEmbeddings` | The community class is deprecated and `langchain-community` is itself being sunset; installing the standalone package added no conflicts and no version bumps | Flip only if `langchain-ollama` is itself deprecated in favour of another integration package.

**D-2026-09-15-11 — embedder injection**
Injected via constructor, typed as a `Protocol` | Constructing `OllamaEmbeddings` inside `Chunker` | Lets the test suite pass a deterministic fake, keeping CI fast and offline instead of depending on a live model server | Flip never; this is a straightforward dependency-inversion win with no real cost.

**D-2026-09-15-12 — git hygiene scope**
Forward-only untrack of `.venv` (new commit) | Amend Initial commit + force-push; local-only amend | The Initial commit is already published on GitHub, so rewriting history would require force-pushing over public `main` | Flip to a history rewrite if repository clone size becomes a genuine problem and the remote is confirmed to have no other consumers.

**D-2026-09-15-13 — clearing deliberate learning exercises**
Filled in all five CI TODOs and ran `ruff format` | Leaving them as hands-on exercises | CI was passing while asserting nothing, which is worse than no CI — a green check that verifies nothing actively misleads | Flip never; the exercises remain available in `docs/ci_deep_dive.md` without leaving the live pipeline hollow.

---

## Offset-drift fix (2026-09-15, same day, after P2-M2)

**D-2026-09-15-14 — recovering chunk character offsets**
Whitespace-tolerant regex search (`\s+` between the chunk's words) | (a) trusting `SemanticChunker`'s own `start_index`; (b) plain `str.find` | `start_index` drifts because the splitter rebuilds chunks with single spaces then advances its tracker by the REBUILT length; plain `str.find` fails too, because that same normalisation means the rebuilt chunk often is not verbatim present in the source — verified, it fell back to the broken estimate on the exact repro case | Flip to tracking positions during splitting ourselves if we ever replace `SemanticChunker` with a hand-rolled splitter, which would make offsets exact by construction rather than recovered after the fact.

**D-2026-09-15-15 — the correctness criterion for offsets**
Assert `source[start:end].split() == chunk.text.split()` | Assert byte-for-byte `source[start:end] == chunk.text` | Byte equality is impossible by construction: the slice retains the source's blank lines while the chunk text has single spaces. Word-level equality proves the span points at the right REGION, which is what a UI highlight actually needs | Flip to byte equality only if chunk text is ever reconstructed from the source slice itself rather than from re-joined sentences.

---

## Phase 2, Microstep 3 — Token Counting & Metadata Enrichment (2026-09-15)

**D-2026-09-15-16 — which tokenizer**
`tiktoken` `cl100k_base` | `nomic-embed-text`'s own tokenizer via Ollama; keeping the char÷4 heuristic | Industry-standard, well-documented, works without a model server, and gives a consistent ruler for comparing chunk sizes. **Known mismatch, accepted deliberately:** we embed with `nomic-embed-text`, which tokenizes differently — so these counts do NOT predict what the embedding model actually sees | Flip to the embedding model's own tokenizer the moment token counts are used to enforce a real context-window limit rather than to compare chunks to each other.

**D-2026-09-15-17 — behaviour when the encoding can't be fetched**
Fail loudly at construction time, plus pre-warm the cache in the Docker build | Silent fallback to the char÷4 heuristic; vendoring the `.tiktoken` file into the repo | A silent fallback would make `tokens_approx` mean two different things depending on network conditions, which is painful to debug; baking the cache into the image at build time means the container never needs network at runtime | Flip to vendoring the encoding file if builds must run air-gapped, where even build-time network is unavailable.

**D-2026-09-15-18 — both counts hold the same number**
`approx_token_count` AND `metadata["tokens_approx"]` both carry the tiktoken count | Field keeps char÷4 while metadata gets tiktoken; renaming the field | Two numbers that disagree would force every reader to know which to trust; measured on real corpus text the gap is real (heuristic 100 vs tiktoken 79, ~27% overestimate) | Flip to renaming `approx_token_count` if its now-inaccurate name ("approx" holding an exact value) starts causing confusion in code review.

**D-2026-09-15-19 — pure enrichment, not in-place mutation**
`enrich_with_token_counts` returns NEW chunks via `dataclasses.replace` and a fresh metadata dict | Mutating `chunk.metadata` in place | `DocumentChunk` is frozen specifically to prevent post-construction edits; mutating its dict works only by exploiting a gap in that guarantee, and would make enrichment invisible action-at-a-distance | Flip only if profiling shows the extra allocation matters at corpus sizes far beyond today's 76 documents.

**D-2026-09-15-20 — injectable TokenCounter**
`TokenCounter` Protocol with a fake in tests | Calling tiktoken directly inside the enrichment function | Keeps the test suite offline and instant; without it, "fail loudly" would make CI on a fresh GitHub runner depend on a network fetch, since the runner is not the Docker image the cache was baked into | Flip never; same dependency-inversion reasoning as `Embedder` in chunker.py.

---

## Phase 2, Microstep 4 — Ingestion Pipeline Script (2026-09-15)

**D-2026-09-15-21 — data layout**
Keep `data/<category>/` and point the pipeline at `data/` | Restructuring everything under `data/raw/` as the spec assumed | Zero file moves, no test churn, and it avoids a verified trap: files under `data/raw/` with `data_root=data/` make `infer_category` return `"raw"` for every single chunk, silently destroying the category dimension hybrid search filters on | Flip if a second processing stage genuinely needs its own sibling directory (`data/processed/`), at which point `data_root` must move to `data/raw/` in the same change.

**D-2026-09-15-22 — pipeline includes enrichment**
loader → chunker → **enrichment** | The spec's literal loader → chunker only | Without enrichment the saved chunks carry the char÷4 estimate, which would make the spec's own "token distribution" report describe numbers already measured as ~27% wrong | Flip only if enrichment becomes slow enough to want a separate opt-in pass.

**D-2026-09-15-23 — output format**
One JSON file per source document, named `company__about.md.json` | A single combined `chunks.json`; JSONL | One document's chunks can be inspected or regenerated in isolation, and a partial failure damages exactly one file | Flip to JSONL when the corpus outgrows comfortable per-file inspection or a streaming loader is needed.

**D-2026-09-15-24 — generated chunks are gitignored**
`data/chunks/` in `.gitignore` | Committing chunks so a fresh clone has them | Chunks are reproducible from `data/` + code; committing them adds an unreadable diff to every parameter experiment | Flip if a reviewer must see output without running Ollama, at which point commit a small curated sample rather than the whole set.

**D-2026-09-15-25 — re-run policy**
Always re-chunk every document, overwriting | Skip if output exists; skip if source older than output | Skipping is silently wrong the moment chunking parameters change — untouched files keep boundaries from the OLD config, mixing two strategies in one dataset with no marker saying which is which | Flip to timestamp+config-hash staleness checking when a full run (~4 min at 76 docs) becomes too slow to tolerate.

**D-2026-09-15-26 — failure policy**
Skip the failing document, record it in the report, continue | Abort the whole run; skip but exit non-zero | One unreadable file should not discard the embedding work already done for everything before it | **Flip condition is close:** the resulting dataset LOOKS complete, so if anything ever consumes `data/chunks/` without reading `documents_failed`, switch to exiting non-zero.

**D-2026-09-15-27 — quantile method for the token distribution**
`statistics.quantiles(..., method="inclusive")` | The default `method="exclusive"` | Exclusive extrapolates beyond the observed data: for two chunks of 6 and 10 tokens it reports p25=5 and p75=11, values impossible for that dataset. Inclusive describes the chunks that actually exist, which is what a report about them should do | Flip to exclusive only if the report is ever reinterpreted as estimating a wider population the corpus was sampled from.

**D-2026-09-15-28 — interface**
Importable `run_pipeline()` plus a `python -m` CLI | Either alone | The function is testable without subprocesses; the `__main__` block gives a real command for the README | Flip never; the cost is a dozen lines of argparse.

---

## Phase 2, Microstep 5 — Unit Tests for Ingestion (2026-09-16)

**D-2026-09-16-01 — test file layout**
Keep per-module files in `tests/ingestion/`, add tests only for verified-uncovered paths | Writing `tests/test_ingestion.py` from scratch as the spec says; consolidating all 66 existing tests into one file | 66 tests already covered loader/chunker/schemas at 93% coverage — a new monolithic file would have duplicated them, and consolidating would produce a ~700-line file where you can no longer run one module's tests alone | Flip if the per-module files ever grow so numerous that finding the right one costs more than scrolling one big file.

**D-2026-09-16-02 — what "done" means for a test step**
Measure coverage first, then write tests only for lines proven unexecuted | Writing more tests by intuition about what "feels" untested | Coverage found that `test_undersized_chunks_are_merged_backwards` passed while the merge body never ran — the splitter returned one chunk, so the assertion held for an unrelated reason. Intuition would never have found that | Flip never; measuring before writing is strictly better than guessing.

**D-2026-09-16-03 — fixing the vacuous merge test**
Unit-test `_merge_undersized` directly with crafted spans | Crafting splitter input that yields several chunks; doing both | Calling the method directly GUARANTEES the branch executes, with no dependence on FakeEmbedder's behaviour or threshold tuning — which is exactly what silently failed before | Flip to an end-to-end test if `_merge_undersized` is ever made public, since testing through the public path would then cost nothing.

**D-2026-09-16-04 — test data sources**
Committed `tests/fixtures/corpus/` for most tests, real `data/` kept for canary tests | Real corpus everywhere; `tmp_path` everywhere | Controlled fixtures stop a document edit from breaking unrelated tests (the 82-vs-76 incident), while a few real-corpus tests remain because a fixture can only contain problems we already thought of — the real corpus is what surfaced `.ipynb_checkpoints` | Flip to fixtures-only if real-corpus tests start failing for data reasons more often than they catch genuine problems.

**D-2026-09-16-05 — coverage in CI**
Report coverage on every run, do not enforce a threshold; pin `pytest-cov==7.1.0` | Enforcing `--cov-fail-under=90`; not adding coverage at all | The number stays visible so a regression is noticeable, without a threshold that tempts writing filler tests purely to clear a bar. Pinning avoids repeating the transitive-dependency trap from P2-M3 | Flip to enforcement if coverage silently drifts down over several commits without anyone reacting.

**D-2026-09-16-06 — CI runs the offline test subset**
`pytest -m "not tiktoken"` in CI | Running the full suite including the tiktoken test | A fresh GitHub runner has no tiktoken cache, so that one test would need a network fetch; the Docker cache warmed in P2-M3 protects the container, not the runner | Flip if the CI workflow gains a cache step for tiktoken's encoding directory.

---

## Phase 2, Microstep 6 — Integration Checkpoint: Verify Chunk Schema (2026-09-16)

**D-2026-09-16-07 — verification form**
Permanent contract tests in `tests/ingestion/test_chunk_contract.py` | Manually inspecting 1-2 files as the spec says; a standalone on-demand script | A manual check proves nothing about the other 74 documents and nothing about tomorrow; a script nobody is obliged to run rots the same way. Tests re-verify on every run | Flip to a script if the contract ever needs to run against output too large to load into a test process.

**D-2026-09-16-08 — structural AND semantic checking**
Assert both key/type structure and cross-field invariants | Structural validation only (what JSON Schema covers) | A chunk can be perfectly well-formed and still claim `char_count: 500` for a 12-character string. No schema language catches that; only an invariant comparing fields to each other does | Flip never; dropping the semantic layer removes the only check on the values actually agreeing.

**D-2026-09-16-09 — behaviour when chunk data is absent**
`pytest.skip` with an explicit reason naming the command to run | Failing; generating chunks inside the test fixture | `data/chunks/` is gitignored generated output, so a fresh clone and every CI run legitimately has none — failing would make a clean checkout look broken. Generating them inside a test would need a live Ollama and take ~9 minutes | **Known cost, accepted:** these tests therefore always skip in CI, and a skip reads as green. Flip if chunk output is ever committed or produced by a CI job.

**D-2026-09-16-10 — schema documented as a prose table**
Prose table in README, `DocumentChunk` remains the authoritative definition | A generated or hand-written JSON Schema file | A hand-written JSON Schema is a second definition to keep in sync; generating one needs a generator dependency for a 10-field structure. A table is what a human reviewer actually reads | Flip to a real JSON Schema the moment a non-Python consumer needs to validate this data.

**D-2026-09-16-11 — a test guards the prose table**
`test_readme_schema_table_lists_exactly_the_dataclass_fields` parses the README and compares against `dataclasses.fields` | Trusting the table to be updated by hand | Documentation nothing enforces becomes a lie, and a wrong schema table is worse than none because a reader trusts it. Verified: introducing `char_kount` in the table fails the test with a message naming both sides | Flip never; the guard costs one test and catches a whole class of silent doc rot.

**D-2026-09-16-12 — scope of the sign-off**
Sign off on format and internal consistency only, with the chunk-quality issue stated explicitly in the README | Signing off without qualification | 69% of chunks exceed the target size and the largest is 13,599 characters; those chunks are schema-valid but not well-sized. An unqualified "signed off" would later read as "this dataset was approved" | Flip to an unqualified sign-off once chunk sizing is tuned and re-verified.

---

## Phase 3, Microstep 1 — Qdrant Vector Database Setup (2026-09-16)

**D-2026-09-16-13 — embedding model and vector size**
`nomic-embed-text`, **768** dims | all-MiniLM-L6-v2 (384); OpenAI text-embedding-3-small (1536) — the spec's two options | Already running in Ollama and already used for semantic chunking, so one model defines both chunk boundaries and retrieval similarity. 768 was measured against the running model; neither spec option matched it | Flip only with a full drop-and-re-embed: vector size is fixed at collection creation and vectors cannot move between models.

**D-2026-09-16-14 — create the collection in M1, not M2**
Choose the model now and create `chunks` immediately | The spec's order (create in M1, pick model in M2) | Collection size is immutable, so creating it before choosing the model means guessing and later recreating | Flip never; the spec's ordering was a latent bug.

**D-2026-09-16-15 — distance metric**
Cosine | Dot product; Euclidean | nomic-embed-text is trained for cosine and the chunker already measures similarity this way; dot product matches cosine only for normalised vectors and silently misranks otherwise | Flip to dot product if a future model guarantees normalised output and latency becomes a bottleneck.

**D-2026-09-16-16 — how the collection is created**
Idempotent `ensure_collection()` that creates if missing and **raises** on config mismatch | One-off curl in README; lazy auto-create at first upsert | Re-runnable and tested; a mismatch surfaces immediately rather than as an upsert failure after an expensive embedding run. No auto-repair, because recreating destroys stored vectors | Flip to auto-recreate only for a disposable dev collection.

**D-2026-09-16-17 — offline tests via in-memory Qdrant**
`QdrantClient(":memory:")` | Tests against the Docker Qdrant | Same client API and collection logic with no server, keeping CI offline | Flip to a real-server integration test if in-memory and server behaviour ever diverge.

**D-2026-09-16-18 — chunk sizing deferred, with a gate**
Proceed with M1; re-tune chunking before M3 embeds anything | Fix first; accept current chunks | M1 is independent of chunk quality, but embedding bakes 69%-oversized boundaries into the index | A recorded promise, not an enforced check — must happen before P3-M3.

---

## Phase 3, Microstep 2 — Choose & Lock Embedding Model (2026-09-16)

**D-2026-09-16-19 — hard token cap in the chunker**
Re-split any chunk over `max_chunk_tokens` (1500, counted as chars/4 = 6000 chars) at sentence boundaries, words as a last resort, applied after merging | Re-tuning the breakpoint threshold only; truncating at embed time | Embedding every chunk showed 1/214 rejected ("input length exceeds the context length", 13,599 chars). Tuning guarantees nothing for future documents; truncation silently loses the tail. Verified after the fix: 0/223 rejected, largest 5,983 chars | Flip to counting with the embedding model's own tokenizer if a text type (code, tables) turns out to tokenize far denser than chars/4 assumes.

**D-2026-09-16-20 — single source of truth for the model**
Extend `Settings` in `src/core/config.py` (model, digest, dim, context, cap, prefixes, collection) and have chunker, pipeline and Qdrant setup read it | Creating `src/config.py` as the spec named; moving the existing module | The model name was hardcoded in three unconnected places (`chunker.py`, `pipeline.py`, `setup.py`'s `VECTOR_SIZE`); two config modules would recreate that problem | Flip never; deviating from the spec's filename is cheaper than a second config module.

**D-2026-09-16-21 — pin by digest and verify at startup**
Store the model digest and refuse to run if Ollama serves a different one (pipeline and Qdrant setup) | Model name only; digest recorded in docs without a check | `nomic-embed-text:latest` is a moving tag — a re-pull can swap the weights, and old document vectors silently stop matching new query vectors with no error | Flip to name-only only if the model is ever served from an immutable source that guarantees versioned weights.

**D-2026-09-16-22 — task prefixes on by default, configurable**
`search_query: ` / `search_document: ` per the nomic model card, held in config | Always on hardcoded; off | Following the model's training is the defensible default. A 2-query spot check showed prefixes narrowed the relevant/irrelevant gap (0.169 → 0.124, 0.107 → 0.083) — too small a sample to overrule the model card, but enough to demand a real evaluation | Flip to off if a proper retrieval evaluation shows better separation without prefixes — and budget a full re-embed, because the document prefix is in every stored vector.

---

## Phase 3, Microstep 3 — Embedding Pipeline (2026-09-17)

**D-2026-09-17-01 — module lives in `src/embedding/embedder.py`**
Next to `lock.py` | `src/retrieval/embeddings.py` as the spec named; a re-export from both paths | Everything about the embedding model (pin, prefixes, batching) in one package; "retrieval" is the query side | Flip if query-time embedding grows into its own package and document embedding is only one caller of it.

**D-2026-09-17-02 — `Embedding` carries the whole chunk plus provenance**
`Embedding(chunk, vector: tuple, model, model_digest, document_prefix)` in `schemas.py` | `Embedding(chunk_id, vector, ...)` joined later; bare aligned vector lists | No second list to join, so no way to pair a vector with the wrong chunk; digest and prefix recorded because nothing else records what produced a stored vector | Flip to chunk_id-only if embeddings are ever persisted separately from chunks at a scale where duplicating payloads matters.

**D-2026-09-17-03 — call Ollama through the `ollama` client, pinned directly**
`ollama.Client.embed(..., truncate=False)` | LangChain `OllamaEmbeddings`; raw urllib | LangChain's class exposes no `truncate` field (checked), so silent truncation could not be switched off; the client supports batched input natively | Flip to LangChain if it gains a truncate option and a second provider must be swappable behind one interface.

**D-2026-09-17-04 — fixed batch size from config, default 16**
`settings.embed_batch_size = 16` | One request for everything; token-budget batches | Batching verified not to change vectors. Speed turned out NOT to be the reason: re-measured with the model warm, sizes 1-32 all ran at ~1.16 chunks/s on this CPU (the first 93.9/63.9/76.8s reading was a cold-start artefact, corrected 2026-09-17). 16 is kept for fewer round trips and a bounded blast radius on failure. Full corpus: 223 chunks in 199.9s | Flip to token-budget batches if chunk sizes vary enough that fixed batches time out, or retune upward on a GPU.

**D-2026-09-17-05 — fail fast on any batch error, naming its chunk_ids**
Raise `EmbeddingError` for server, HTTP or connection errors | Bisect and skip the bad chunk; retry with backoff | The hard cap means a 400 should be impossible, so one is a bug to stop for; skipping would build an incomplete index that looks complete. Verified: one oversized item fails its whole batch | Flip to retries once embedding runs against a remote or shared server where transient failures are normal.

**D-2026-09-17-06 — validate count, dimension, finiteness and unit norm per batch**
All four checks, before any Embedding is built | Count and dimension only; no checks | Each failure would otherwise reach Qdrant as a plausible vector; misalignment is undetectable afterwards. Mutation-tested: removing any check fails a test | Flip the norm check off (make it configurable) if a model that does not normalise is ever adopted.

**D-2026-09-17-07 — the Embedder verifies the model and applies the prefix itself**
`verify_model` in the constructor, `as_document` inside `embed_batch` | Caller responsibility; split responsibility | A caller cannot forget either, and an Embedder instance existing proves the pin matched | Flip if the same class must also embed raw, unprefixed text for another model or task.

**D-2026-09-17-08 — embeddings stay in memory; M4 stores them**
`embed_batch` returns `list[Embedding]` | Disk cache keyed by chunk/text/digest; writing straight to Qdrant | Keeps the embed/store boundary visible and avoids a cache with its own invalidation rules; re-embedding costs ~200s | Flip to a cache when the corpus grows enough that re-embedding costs more than maintaining invalidation.

**D-2026-09-17-09 — rename the chunker's Protocol to `SentenceEmbedder`**
New class keeps the spec name `Embedder` | Different name for the new class; two classes named `Embedder` | The chunker's interface embeds sentences for boundary detection and knows nothing of prefixes or pinning; one name for two concepts invites wrong imports. No code outside src/tests imported it | Flip never; historical notes keep the old name as a record.

**D-2026-09-17-10 — `read_chunks` loads every JSON in data/chunks except the report**
Exclude `report.json` by name | Glob `*.md.json` | The loader also supports .txt and .pdf sources; a markdown-only glob would silently drop their chunks | Flip if the output directory gains other non-chunk JSON files — then switch to a manifest.

---

## Phase 3, Microstep 4 — Vector Store Client (2026-09-17)

**D-2026-09-17-11 — module lives in `src/vectorstore/store.py`**
Next to `setup.py` | `src/retrieval/vector_store.py` as the spec named; moving setup.py into `src/retrieval/` | Collection setup and point operations belong together, and moving P3-M1 code would churn its tests and docs for a name | Flip if a retrieval layer grows (hybrid search, reranking) that wraps several stores — then the store becomes one component under `src/retrieval/`.

**D-2026-09-17-12 — point ID = uuid5(fixed namespace, chunk_id)**
Deterministic UUIDv5 | Content-hash UUID; random uuid4 | Qdrant accepts only ints or UUIDs; a deterministic ID makes re-upserting overwrite. Mutation test showed random IDs are masked by the delete guard, so a dedicated ID test is needed | Flip to content-based IDs if chunks ever need stable identity across re-chunking (e.g. user feedback attached to a chunk).

**D-2026-09-17-13 — delete by doc_id, then upsert, per document**
Delete-then-upsert | Upsert then delete leftovers; recreate the collection | Simplest correct guard against stale positional chunks; verified live on a scratch collection (2 points -> re-upsert 1 -> 1). Plus an `IncompleteDocument` guard, because a partial list would otherwise delete the missing chunks | Flip to upsert-then-delete-leftovers if the brief per-document search gap matters (live traffic during re-indexing).

**D-2026-09-17-14 — payload = every DocumentChunk field + model, digest, prefix**
Full payload with provenance | IDs only with text from disk; full payload without provenance | Search returns complete chunks from one source; provenance makes mixed collections detectable (live: all 223 points in one digest/prefix group) | Flip to a slim payload if storage cost matters at scale, keeping provenance.

**D-2026-09-17-15 — keyword payload indexes on doc_id and category**
Created in `ensure_collection`, idempotently | No indexes; doc_id only | doc_id serves the delete guard, category serves filtered search; verified live in payload_schema. Local mode ignores indexes, so this is checked live, not in tests | Flip never at this scale; add indexes for any new filter field.

**D-2026-09-17-16 — `Embedder.embed_query` owns query embedding**
Query prefix, pin and validation in the same class as documents; the store receives it | Store calls Ollama itself; search takes a vector | The only difference between the two sides should be the prefix; duplicating the rest in the store is how they drift | Flip to vector-in search if queries are embedded elsewhere (e.g. a batched evaluation job).

**D-2026-09-17-17 — `search()` per spec plus `search_with_scores()`**
Both; `search` delegates | Chunks only; scored results only | Meets the spec signature without discarding scores that thresholds, evaluation and reranking need | Flip to scored-only once no caller uses the spec signature.

**D-2026-09-17-18 — health_check means readiness, returns bool, never raises**
Qdrant answers AND collection exists with the expected size and distance | Liveness only; readiness plus non-empty | A running Qdrant with a missing or wrongly sized collection fails every request; an empty collection is valid right after setup | Flip to include points_count > 0 for a query-serving API where empty means broken.

**D-2026-09-17-19 — one wait=True upsert per document**
Per-document calls | Fixed batches across documents; one call with wait=False | Aligns with the per-document delete; wait=True makes points searchable before returning. Live: 223 points, 76 documents in 4.8s | Flip to batched upserts when document counts make per-document round trips slow.

**D-2026-09-17-20 — reject embeddings whose digest or prefix differs from settings**
`ProvenanceMismatch` before any write | Store whatever arrives; also sample-check stored points | The startup check stops the wrong model embedding; this stops stale embeddings from an earlier model entering the collection. Mutation-tested | Flip to also sampling stored points if other writers can touch the collection.

**D-2026-09-17-21 — tests use the real in-memory QdrantClient**
`QdrantClient(":memory:")` | Mocked client; Docker Qdrant only | Real upsert, filter-delete and query semantics with no server; 10 hand mutants of the store all killed | Flip to adding Docker-backed integration tests in CI when behaviour that local mode lacks (indexes, HNSW) must be enforced automatically.

**D-2026-09-17-22 — CLI with index / search / health**
`python -m src.vectorstore.store` | Class only; hooking into the ingestion pipeline | Needed for live verification and experiments; keeps re-chunking and re-indexing separately runnable | Flip to pipeline integration once chunking parameters stabilise.

**D-2026-09-17-23 — Qdrant CLI clients accept only gzip responses**
`build_qdrant_client` sends `Accept-Encoding: gzip` | Default httpx negotiation (gzip, deflate, zstd); pinning zstandard | One live index failed with "zstd decompressor error: Allocation error" on a delete response; not reproducible in 200 deletes, a 70s idle test, or a rerun — root cause unknown. zstandard is only a langsmith dependency. gzip removes the failing decoder and was verified to take effect | Flip back to default negotiation if the zstd failure is diagnosed and fixed upstream.

---

## Phase 3, Microstep 5 — Ingest All Chunks into Vector Store (2026-09-17)

**D-2026-09-17-24 — the job lives in `src/vectorstore/ingest.py`**
`run_ingestion(client, make_embedder, ...) -> IngestionReport` | Growing the `index` branch of `store.py`; a stage of the chunking pipeline | A job with stages, a report and a log is testable only as a function, not as CLI code; keeping it out of the chunking pipeline keeps re-chunking and re-indexing separately runnable | Flip to a pipeline stage once chunking parameters stabilise and one end-to-end command matters more than separate reruns.

**D-2026-09-17-25 — full re-embed on every run**
Embed all 223 chunks each run (~198 s live) | Incremental by stored content hash; incremental by file mtime | Always consistent with no extra state; change detection is where silent staleness comes from | Flip to hash-based incremental ingestion when a full run takes long enough that it is skipped in practice.

**D-2026-09-17-26 — delete orphan documents, unless the chunking report shows failures**
Remove stored documents absent from disk; keep them (with a warning) if `report.json` records `documents_failed > 0`; empty chunks dir aborts earlier | Report only; ignore | The index should match the source, but after a partly failed chunking run an absent document may only have failed to chunk. Verified live: removing one chunk file took a scratch collection from 7 to 4 points, verified | Flip to report-only if chunk files can go missing for reasons the chunking report does not capture (e.g. files deleted by hand after a failed run).

**D-2026-09-17-27 — preflight refuses stored points from another vector space unless `--rebuild`**
Filtered count of points whose digest or prefix differs from settings | Leaving the gap documented; a separate index manifest file | Closes the gap found in deep dive 14: a changed setting makes new embeddings and the upsert check agree while stored vectors are in another space. Verified live: `EMBED_DOCUMENT_PREFIX=""` aborted in 0.2 s with 223 incompatible points, collection untouched | Flip to a manifest only if the vector store can no longer be queried by payload. Note: direct `QdrantVectorStore.upsert` callers still bypass this check.

**D-2026-09-17-28 — fail fast and log the failed stage**
Stop at the first error; the log records status, stage, error and counts gathered so far; the error is re-raised | Skip failing documents and continue; blue/green build with alias switch | Simple and honest; reruns are idempotent. A failure during upsert can leave some documents new and some old until the next successful run | Flip to blue/green (alias switch) when the index serves live traffic that must never see a mixed state.

**D-2026-09-17-29 — embed everything, then upsert**
Embedding stage completes before any write | Per-document embed+upsert; groups of N | An embedding failure leaves the collection exactly as it was (tested); 223 vectors in memory is trivial | Flip to grouped embed+upsert when vectors for the whole corpus no longer fit comfortably in memory.

**D-2026-09-17-30 — one JSON log, overwritten each run, gitignored**
`data/ingestion_log.json` | JSON Lines history; both | Matches the spec and is easy to read for the latest run; added to `.gitignore` because it changes on every run | Flip to also appending JSONL history once runs need comparing over time (e.g. tracking embed duration growth).

**D-2026-09-17-31 — log contains timings per stage, counts before/after, provenance, status, error**
Full report dataclass serialised as JSON | Counts and total time only; per-document detail | Separates a slow embed (197.7 s) from a slow upsert (4.8 s) without rerunning; records which vector space the run wrote | Flip to per-document detail when individual documents need diagnosing.

**D-2026-09-17-32 — verify exact chunk_id sets and zero incompatible points**
Stored chunk_ids (minus deliberately kept orphans) must equal disk's; no point from another vector space may remain | Point count only; no verification | A count can hide one missing and one stale chunk; mutation-tested: disabling either half fails a test | Flip to sampling-based verification when listing all chunk_ids becomes too slow.

**D-2026-09-17-33 — preflight = model check + ensure_collection + compatibility check**
All before loading or embedding | Letting errors surface late | Refusals take ~0.2 s instead of arriving after ~198 s of embedding. The planned separate `health_check()` call was removed: mutation testing showed it could never fail after `ensure_collection`, which already raises on an unreachable or misconfigured collection | Flip to adding an explicit readiness check if `ensure_collection` ever stops validating an existing collection.

**D-2026-09-17-34 — `store index` delegates to the ingestion job**
One implementation, two commands | Removing `store index`; keeping two implementations | Existing notes and deep dive 14 reference `store index`; two implementations would drift | Flip to removing the alias once docs point only to `python -m src.vectorstore.ingest`.

**D-2026-09-17-35 — tests: in-memory Qdrant, fake embedding client, temp chunks dir**
12 tests covering success, idempotency, orphan removal and its guard, refusals, rebuild, failures at preflight/load/embed/verify | Mocks; live runs only | Real filter, delete and scroll behaviour with no server; 10 hand mutants of the job all killed | Flip to adding a Docker-backed CI job when server-only behaviour must be enforced automatically.

---

## Phase 3, Microstep 6 — Unit & Integration Tests for Vector Store (2026-09-19)

**D-2026-09-19-01 — `tests/test_retrieval.py` adds hermetic flow tests + live integration tests**
Cross-component tests (chunks -> ingestion -> store -> search) with a toy model, plus live tests on the real model and server | Hermetic only; adding a labelled-set metric now | 61 unit tests already covered each component alone; nothing tested the components together, or the real server/model. A metric over labelled questions is evaluation work for a later phase | Flip to adding the metric once a labelled question set exists.

**D-2026-09-19-02 — known-answer live checks by rank, known misses as `xfail(strict=True)`**
3 known answers in the top 3; Carllm and CEO questions as strict xfails | No quality assertions; exact top-k snapshots | Records current retrieval behaviour both ways; a strict xfail fails the suite when the miss is fixed (verified: XPASS(strict) -> failed), so the record cannot go stale. The CEO miss was found while probing the subset before writing assertions | Flip to snapshots only if ranking must be frozen exactly (e.g. before a demo).

**D-2026-09-19-03 — `live` marker, auto-skip with the reason when a service is down**
`@live_test` = `pytest.mark.live` + `skipif` on reachability and on the data subset | Deselected by default; environment variable switch | Runs whenever services are up, with no extra command; verified: Qdrant stopped -> 11 live tests skipped, each naming the missing service | Flip to deselect-by-default if live tests become slow enough to annoy local runs.

**D-2026-09-19-04 — live tests use a uniquely named scratch collection from 8 real documents**
`test_live_<uuid8>`, filled once per session (20 chunks, ~21 s of embedding), deleted in `finally` | Read-only searches on the real `chunks`; Testcontainers | Isolated from the real index and from other runs; verified no `test_live_*` collection remains after runs | Flip to Testcontainers when tests must run where no Qdrant is already installed.

**D-2026-09-19-05 — hermetic tests use a bag-of-words toy embedding model**
Words hashed (hashlib, not `hash()`) into 768 slots, counted, normalised | Existing one-hot hashing fake; recorded real vectors | Deterministic and offline, yet a question finds the chunk sharing its words, so the flow can be tested with real questions | Flip to recorded real vectors if a hermetic test must depend on synonyms or meaning.

**D-2026-09-19-06 — CLIs tested in-process with swapped-in builders**
`run_from_args` / `main()` with `sys.argv` and monkeypatched client builders | Subprocess against live services; leaving CLIs untested | Covered the CLI code that was the bulk of the missing coverage; embedding/vectorstore coverage rose from 70-79% to 91-98% | Flip to subprocess tests if argument parsing or process exit behaviour itself breaks in practice.

**D-2026-09-19-07 — live contract tests for known in-memory vs server differences**
Server rejects id -1 (in-memory accepts), payload indexes exist on the server, client negotiates gzip | Parametrising all store tests over both clients; no contract tests | Small and targeted at differences already observed | Flip to full parametrisation if a new, unexpected difference is found.

**D-2026-09-19-08 — live semantic tests assert rank, not scores**
Expected chunk in the top 3 | Scores within a tolerance; snapshots | Robust to harmless score shifts (one question's #1 and #2 differ by 0.001); model drift is already caught by the digest pin | Flip to score tolerances if a change in confidence (not just order) must be detected.

**D-2026-09-19-09 — CI deselects live tests explicitly and reports skips**
`-rs -m "not tiktoken and not live"` | Linux job with a Qdrant service container; leaving live tests to skip | The Windows runner cannot start service containers; deselecting says so honestly instead of showing quiet skips | Flip to a Linux CI job with a Qdrant service when server-only behaviour needs protecting on every push.

**D-2026-09-19-10 — shared helpers in a new `tests/support.py`, used by the new file only**
Plain module of factories, the toy model and reachability checks | Moving helpers into conftest and editing 3 older files; a fourth copy | No churn to working tests; the older files can migrate later | Flip to migrating the older test files when they next need changes.

---

## Phase 4, Microstep 1 — Set Up Neo4j (2026-09-19)

**D-2026-09-19-11 — reset the empty neo4j_data volume so it initialises from .env**
Delete the P1-era volume (0 nodes, 0 relationships, verified) and let Neo4j re-initialise with NEO4J_AUTH from .env | Changing the password in place with ALTER CURRENT USER; keeping `changeme` | NEO4J_AUTH only applies when the volume is first created (the container log says so on every start), so a new .env password was silently rejected (verified: auth failure against the old volume). Resetting exercises the exact path a fresh clone takes. The deletion was blocked for the assistant by the permission classifier and run by the user | Flip to ALTER CURRENT USER as soon as the graph holds data worth keeping.

**D-2026-09-19-12 — credentials in gitignored .env; compose requires the password**
`NEO4J_PASSWORD` required via `${NEO4J_PASSWORD:?...}` for both neo4j and api services; `NEO4J_URI=bolt://localhost:7687` in .env for host-side scripts; Settings default password emptied | Keeping the `:-changeme` fallback; Docker secrets file | One source for Compose and Settings; a missing password fails loudly instead of running on a well-known one (verified: compose refuses without it). CI only builds the Dockerfile, so it is unaffected | Flip to Docker secrets (NEO4J_AUTH_FILE) if the stack is ever deployed beyond a single developer machine.

**D-2026-09-19-13 — keep Neo4j 5.26.0 LTS**
Pinned image already present | 2025.x calendar releases; 4.4 | Long-term support; verified with neo4j Python driver 6.2.0 (Bolt protocol 5.8) | Flip when a needed feature exists only in 2025.x.

**D-2026-09-19-14 — enable APOC Core from the bundled jar**
`NEO4J_PLUGINS='["apoc"]'` | No plugins; APOC + Graph Data Science | One line, no download (installed from /var/lib/neo4j/labs); 192 APOC procedures available; graph tooling such as LangChain's schema inspection expects it | Flip to adding GDS when community detection (Microsoft-style GraphRAG summaries) is implemented.

**D-2026-09-19-15 — fixed memory: heap 512m-1G, page cache 512m**
Explicit `NEO4J_server_memory_*` settings | Auto-sizing from host RAM; Docker memory limit | Predictable next to Ollama and Qdrant on a 7.6 GB host; verified via SHOW SETTINGS; container ~900 MiB at idle | Flip to larger values once the graph or queries outgrow them (watch for out-of-memory query errors).

**D-2026-09-19-16 — healthcheck logs in with cypher-shell**
`cypher-shell ... 'RETURN 1'` with credentials split from NEO4J_AUTH by shell parameter expansion | HTTP probe on 7474; Python-only check | "healthy" now means Bolt + authentication work. First attempt passed NEO4J_USER/NEO4J_PASSWORD as extra container variables and Neo4j refused to start: the image maps every NEO4J_* variable to a config setting | Flip to a dedicated health user if the admin password should not be used by probes.

**D-2026-09-19-17 — `src/graph/setup.py`: build_driver, non-raising health_check, CLI**
Readiness = connectivity + authentication + major version 5 + APOC loaded; settings read at call time; no `--password` flag | Manual Browser check only; also creating schema constraints now | Scriptable and testable like src/vectorstore/setup.py; schema waits until extraction decides the entities. Mutation-tested: 5 mutants killed | Flip to adding schema bootstrap here once the entity model is fixed.

**D-2026-09-19-18 — tests: fake driver + read-only live tests**
10 hermetic tests with a fake driver; 4 live tests skipped only when Neo4j is unreachable (a wrong password fails, it does not skip) | Live only; none | CI stays hermetic; the live tests prove the fresh volume took the .env password (old `changeme` rejected), APOC, memory and the HTTP endpoint. Community Edition has one user database, so later write tests need another isolation strategy than scratch databases | Flip to per-test labels or a throwaway container when graph writes are tested (P4-M2+).

---

## Phase 4, Microstep 2 — Graph Model and Schema Constraints (2026-09-20)

**D-2026-09-20-01 — a closed graph model derived from a measured audit: 8 node labels, 10 relationship types**
Fixed labels and relationship types (`src/graph/schema.py`), chosen after counting what the 76 documents contain | Letting an LLM propose entity and relationship types per chunk | Every category follows a template and its entities are enumerable (8 product files, 32 HR records, 32 contract titles, 5 offices). Uniqueness constraints only mean something on labels fixed in advance; an open extractor invents "Person / Employee / Staff" faster than a constraint can guard them | Flip to open extraction if a document category appears whose entities do not fit, and add labels then, on purpose.

**D-2026-09-20-02 — the graph holds IDs, not text; `MENTIONS` and `DESCRIBED_IN` bridge to chunks**
`Chunk` nodes carry only `chunk_id`; `(Chunk)-[:MENTIONS]->(entity)` and `(entity)-[:DESCRIBED_IN]->(Document)-[:HAS_CHUNK]->(Chunk)` | Copying chunk text into the graph; storing Qdrant's point id on the node | One owner of the text, so no second copy to keep in sync; graph and vector results join on `chunk_id`, and the Qdrant point id is derivable (`point_id(chunk_id)`), so storing it would add a value that can drift. `DESCRIBED_IN` lets a lookup prefer an entity's own page, the path meant for the Carllm miss (no products/Carllm.md chunk was in the dense top 10). Intended, not yet demonstrated: nothing is loaded | Flip to storing text on `Chunk` if graph queries must run without Qdrant.

**D-2026-09-20-03 — two person labels: `Employee` (HR roster) and `Contact` (named in a contract, keyed by name + organization)**
Separate labels, and `organization` inside the `Contact` key | One `Person` label keyed on name; fuzzy name matching | The corpus breaks a single label three ways: none of the 17 Insurellm-side signature blocks names a roster employee (Jennifer Rodriguez signs as "Chief Executive Officer" 7 times, Michael Torres 5, Sarah Chen 4) although the HR record and About page name Avery Lancaster as CEO; two client-side signatories (Robert Chen at Fortress, Marcus Johnson at Rapid Claims) share names with employees; and several names are one word from roster names (Sarah Chen / Alex Chen / Robert Chen). One label would merge namesakes and report two CEOs. Which CEO is true is left to retrieval and its evaluation | Flip to one `Person` label only if the corpus gains a stable person id usable across documents.

**D-2026-09-20-04 — identity is a natural, deterministic key chosen from what is always present**
`doc_id`, `chunk_id`, `name` (Company, Product, Employee, Location), `doc_id` for Contract, `(name, organization)` for Contact | Generated ids; `contract_number` as the contract key | Deterministic keys make every load idempotent. 13 of 32 contracts have no number, 8 header layouts exist and 7 contracts hold unfilled placeholders, so a number cannot be an identity while the source document always exists. Company is the short name from the contract title (32 distinct); the legal name differs in 11 of 12 Parties blocks, so it is a property. Product is checked against the closed list of 8 file names because 2 titles append a tagline. Location is `City, State` with the full state name because the corpus writes `San Francisco, California` and `San Francisco, CA` (11 strings, 9 cities). A constraint does not clean dirty input: normalise, then `MERGE` | Flip to surrogate ids if two entities ever share a key within a label (a uniqueness violation at load time is the signal).

**D-2026-09-20-05 — required properties are enforced by the loader, not the database**
Loader `MERGE`s with complete keys; the live tests pin the server behaviour this relies on | Neo4j Enterprise existence and node-key constraints; a periodic scan for keyless nodes | Observed on 5.26.0 Community: `IS NOT NULL`, type and `NODE KEY` constraints are refused ("requires Neo4j Enterprise Edition"); `MERGE` with a null key raises but `CREATE` makes a keyless node; a uniqueness constraint ignores a node missing any key property (two `Contact`s with no organization coexisted). So the database catches a duplicate key but not a missing one, and an edition change fails a test instead of going unnoticed | Flip to Enterprise constraints if the edition changes, or to a scan if the loader cannot be trusted to supply every key.

**D-2026-09-20-06 — apply is `IF NOT EXISTS` plus verification by definition, and a mismatch raises**
`apply_schema` creates what is missing, waits for indexes, compares definitions, raises `SchemaMismatch`; `check_schema` is read-only; `python -m src.graph.schema [--check]`; a separate module so `setup.py` stays a read-only readiness check | `IF NOT EXISTS` alone; dropping and recreating on mismatch | Observed live: the same name with a different definition is a silent no-op (0 constraints added), and so is the same definition under another name, so creating alone reports success while the rule is not enforced. Matching by definition accepts an equivalent constraint under another name. No auto-repair: dropping a constraint is a human decision, the same stance as `ensure_collection`. DDL runs in auto-commit because Neo4j forbids schema changes after a write in one transaction | Flip to drop-and-recreate when the graph is disposable.

**D-2026-09-20-07 — deliberately left out of the graph**
No salary or date of birth, performance reviews, prior employers, pricing-tier nodes, `WORKS_AT`, `PROVIDED_BY`/`OFFERS`, or `REPORTS_TO` | Storing every extractable fact | Salary and birth date are sensitive and Community has one admin user and no role-based access; reviews and prior employers are single-document facts vector search already reaches; contracts name 9 different tiers, so `tier` is a property; `WORKS_AT` and `PROVIDED_BY` would be constant edges (every employee works at Insurellm, Insurellm is on every contract); `REPORTS_TO` has one explicit statement in 32 HR records and its target is a role, not an entity | Flip to storing a fact when a question class needs it and, for salary, when access control exists.

**D-2026-09-20-08 — the same scenario tests run on a fake and on the real server; scratch data is swept by name prefix**
`schema_db` fixture parametrised `fake` / `live`; scratch labels `Scratch<hex>` and constraints `scratch...` swept before and after by prefix; enforcement tests on real labels use `__test_` values; a test compares the two architecture tables with the model | A fake only; live only; remembering created objects in memory | The fake encodes beliefs about Neo4j, and running identical scenarios on the real server keeps them true. Sweeping by prefix repairs a crashed run, which answers the P4-M1 open question about a crash before cleanup. 44 new tests; 11 hand-made mutants all killed | Flip to a throwaway container if tests must write to real labels in bulk.

**D-2026-09-20-09 — constraints only; no full-text index yet**
Only the 8 uniqueness constraints (each owns a range index for exact key lookups) | Adding a full-text index on entity names now | Fuzzy matching of question words to entities is a retrieval design decision that has not been made; `apply_schema` is idempotent, so adding one later costs a single entry | Flip when entity linking is built.

---

## Phase 4, Microstep 3 — Graph Records and the Neo4j Store (2026-09-20)

**D-2026-09-20-10 — build order: records, then the store, then the extractor**
`GraphNode` / `GraphEdge` first, then `GraphStore`, then the extractor | Extractor first (the plan's order); all three as one milestone | The plan's inputs did not exist: P4-M2 had delivered the model, not an extractor, and no record types were ever written. The store needs only the records and can be tested on the live server now; the extractor's output can then be checked by loading it and counting against the P4-M2 audit numbers | Flip to extractor-first if its output keeps forcing changes to the records.

**D-2026-09-20-11 — records: one generic set, validated against the model when built, endpoints by reference, no provenance field**
`GraphNode(label, key, properties)`, `NodeRef(label, key)`, `GraphEdge(type, source, target, properties)` in `src/graph/records.py`; label, key completeness, edge endpoints and declared edge properties are checked in `__post_init__`; provenance stays as `Chunk` nodes plus `MENTIONS` edges | A class per label; validation at write time; whole nodes as edge endpoints; a `source_chunk_ids` field on every record | One small set of types that follows the registry automatically (a test builds an edge for every relationship in the model); a bad record fails next to the code that made it; a `NodeRef` is enough to `MATCH` a node. Community Edition cannot enforce that a key is present (D-2026-09-20-05), so this is the guard the loader was promised | Flip to per-label classes if an extractor needs label-specific behaviour, or to write-time validation if records are built faster than they can be checked.

**D-2026-09-20-12 — records are read-only, refuse `None`, and refuse keys with surrounding whitespace**
A frozen dataclass whose dicts are copied into read-only views; a property may not be `None` (omit it); a key value may not start or end with whitespace | A plain frozen dataclass with mutable dicts; silently dropping `None` and trimming keys | `frozen=True` only stops re-assigning a field, so a caller could edit a validated record afterwards. `SET n += {x: null}` deletes `x`, so "unknown" must be an omitted property, and `"Carllm "` would `MERGE` into a ghost node next to `"Carllm"`. Refusing surfaces the extractor's bug instead of hiding it. Lists inside stay editable: the guard is shallow | Flip to dropping `None` if extraction routinely produces nulls and refusing them becomes noise.

**D-2026-09-20-13 — `GraphStore` at the planned path, with an injected driver, and it refuses a graph whose schema is not in force**
`src/retrieval/graph_store.py`; `GraphStore(driver, *, database="neo4j", verify_schema=True)` calls `check_schema` and raises `SchemaMismatch` naming the fix; it never opens or closes the driver | `src/graph/store.py` beside `schema.py`; building its own driver; applying the schema itself | The plan named the path. An injected driver leaves the lifetime with the caller and lets tests pass a fake (as `apply_schema` and `health_check` already do). Without the constraints `MERGE` is no longer atomic and every lookup scans, so refusing early is cheaper than finding duplicates. Not auto-applying, for the reason D-2026-09-20-06 gave: creating or dropping a rule is a human decision | Flip to `src/graph/store.py` if `retrieval/` should hold only retrievers; to auto-apply if the graph becomes disposable.

**D-2026-09-20-14 — writes: managed transactions, `MERGE` on the key with `SET +=`, and strict endpoints**
Every write runs in `execute_write`; a node is merged on its full key with `SET n += $props`; an edge `MATCH`es both endpoints and raises `MissingEndpoint`, naming which one, if either is absent | Auto-commit `session.run`; `SET n = $props`; `MERGE` for the endpoints | `execute_write` retries transient failures, which is safe only because every write is idempotent. `+=` never deletes properties, while `=` would wipe the key. `MERGE` on endpoints turns a typo (`Carlllm`) into a ghost node and splits one product in two, which no constraint can catch. Price: nodes must be loaded before edges | Flip to `MERGE`d endpoints if load order becomes a burden and the extractor checks names against the closed vocabulary itself.

**D-2026-09-20-15 — reads: exact name, both directions, domain edges by default, a limit and a total order**
`search_related(name, *, label=None, include_provenance=False, limit=50)`: exact match on `name` for the five labels that have one (one index seek per label, joined by `UNION`); every namesake is searched unless `label` narrows it; `MENTIONS`, `HAS_CHUNK` and `DESCRIBED_IN` are left out unless asked for | Case-insensitive or fuzzy lookup; a required label; provenance edges by default; no limit | Exact match is the simplest thing an entity linker can be built against. Namesakes exist (Robert Chen is an `Employee` and a `Contact`), so all are returned rather than one guessed. Provenance edges would return every chunk that merely names the entity. The ordering is total (a mutation check found the first version's was not), so the same question returns the same list | Flip to a full-text index when entity linking needs fuzzy matching (D-2026-09-20-09).

**D-2026-09-20-16 — multi-hop: variable-length Cypher, at most 3 hops, domain edges, either direction, nearest first**
`multi_hop_search(start, hops, *, label=None, limit=50)` walks domain relationships from every node with that name and returns distinct names ordered by shortest distance, never the start; it walks THROUGH nodes with no name (a `Contract`) but returns only named ones, so a contract with a `title` returns its title; the return type is `List[str]` as specified | APOC's path expander; a Python breadth-first search over `search_related`; walking provenance edges; exactly-N semantics | No extra dependency, and shortest-distance semantics are one query. Walking provenance edges would make two entities named in one chunk 2 hops apart (a test pins this). `List[str]` follows the plan but collapses namesakes. Path enumeration grows with routes, not entities, so the cap is the guard | Flip to APOC `subgraphNodes` (each node visited once) when the graph is large enough for path enumeration to be slow, and to a richer return type when namesakes must be told apart.

**D-2026-09-20-17 — tests: a fake driver for what is sent, live tests on the real labels with a swept `__test_` prefix**
36 hermetic and 18 live store tests, 44 record tests, and a 29-mutant check | Scratch labels with a model passed to the store; a throwaway container | The store exists for the real model, so it is tested on the real labels and constraints, with values prefixed `__test_` and swept before and after (the isolation P4-M2 used). The fake proves the exact queries and parameters. The mutation check found two guards no test covered (`DISTINCT`, and excluding the start from its own results) and one test that passed by luck (the tie-breaker in `ORDER BY`); all three now have tests | Flip to a throwaway container when tests must write in bulk.
