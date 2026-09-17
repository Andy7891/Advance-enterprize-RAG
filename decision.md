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
