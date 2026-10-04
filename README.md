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
| 3 | Ingestion job (M5): preflight, orphan sync, verification, `data/ingestion_log.json` | done |
| 3 | Retrieval tests (M6): hermetic flow + live tests, known misses as strict xfails | done |
| 4 | Neo4j (M1): authenticated 5.26 LTS + APOC, readiness check `python -m src.graph.setup` | done |
| 4 | Graph model (M2): 8 node labels, 10 relationship types, 8 uniqueness constraints, `python -m src.graph.schema` (graph still empty) | done |
| 4 | Graph records + store (M3): validated `GraphNode` / `GraphEdge`, `GraphStore` with `upsert_node`, `upsert_edge`, `search_related`, `multi_hop_search`; tested live on synthetic data (no extractor yet) | done |
| 4 | Entity deduplication (M4): `deduplicate_entities`, `resolve_entities`, `remap_edges` in `src/graph/dedup.py`; per-label rules, subset match only when one candidate fits, aliases kept; checked against the corpus's own name variants (still no extractor) | done |
| 4 | Graph extractor (M4b): `GraphExtractor` in `src/graph/extractor.py` reads the 223 P2 chunks with rules (no database, no model) and is checked against the P4-M2 audit counts; the ingestion job (M5) is next | done |
| 4 | Graph ingestion (M5): `python -m src.graph.ingest [--rebuild]` in `src/graph/ingest.py` extracts, resolves and writes all 223 chunks into Neo4j (143 domain nodes, 76 Document, 223 Chunk, 154 domain edges, 502 provenance edges), verified by reading the graph back; `data/graph_ingestion_log.json` | done |
| 4 | Graph integration tests (M6): `tests/test_graph.py` — a synthetic fixture through the real extractor, resolver and store, checked hermetically (plan shape) and live (real Neo4j answers real questions); per-component tests stay in `tests/graph/` and `tests/retrieval/test_graph_store.py` | done |
| 4 | Integration checkpoint (M7): 6 spot-checks of the live graph against raw source `.md` files, read independently of the extractor — [notes/step-4.7-graph-spotcheck-report.md](notes/step-4.7-graph-spotcheck-report.md); all 6 matched, 1 accepted limitation and 1 source-narrative discrepancy recorded | done |
| 5 | Graph-based retrieval (M1): `GraphRetriever.retrieve(query, k)` in `src/retrieval/graph_retriever.py` — links entities named in a question against the graph's own 111 names, traverses 2 hops, ranks by hop distance, then by how many query entities reach a chunk at its nearest distance, then by `chunk_id` (so ties are reproducible), returns real `DocumentChunk`s | done |
| 5 | Hybrid retriever (M2): `HybridRetriever.retrieve(query, k, vector_weight, graph_weight)` in `src/retrieval/hybrid_retriever.py` — fuses vector and graph rankings with weighted Reciprocal Rank Fusion (c=60, each retriever asked for 3k candidates); on the five labelled questions an accepted chunk reaches the CEO question's top 3 at k=3 (the label is lenient: it accepts the About page that names the founder) and the Carllm miss at k=10 (not at k=3) — [notes/step-5.2-hybrid-retriever.md](notes/step-5.2-hybrid-retriever.md) | done |
| 5 | Reranker (M3): `Reranker.rerank(query, chunks, k)` in `src/retrieval/reranker.py` — Cohere `rerank-v3.5` reorders the hybrid's candidates by reading the question and each chunk together; SDK retries switched off so errors surface; 40 tests (35 hermetic, 5 live) — [notes/step-5.3-reranker.md](notes/step-5.3-reranker.md); **live run pending a Cohere key in `.env`** | built, live run pending |
| 5 | Retrieval pipeline (M4): `RetrievalPipeline.retrieve(query, k) -> RetrievalResult` in `src/retrieval/pipeline.py` -- chains the hybrid retriever into the reranker (pool size `max(k*3, 20)`), returning one lineage-preserving result carrying every stage's own rank/score; 36 tests (31 hermetic, 5 live: 3 pass with no Cohere key, 2 pending one) -- [notes/step-5.4-retrieval-pipeline.md](notes/step-5.4-retrieval-pipeline.md); **full live run pending a Cohere key in `.env`** | built, live run pending |
| 5 | Retrieval test suite (M5): measured 212 existing tests / 99% coverage of `src/retrieval/` before writing anything; closed the one real gap coverage found (the graph list's own duplicate-skipping branch) plus a real `HybridRetriever` + real `Reranker` (SDK-stubbed) composition test and a real-Cohere-error-type propagation test -- now 100% coverage, 667 passed live / 592 CI-mode -- [notes/step-5.5-retrieval-test-suite.md](notes/step-5.5-retrieval-test-suite.md) | done |
| 6 | Local LLM setup (M1): `mistral` (7B, Q4, 4.37 GB) pulled into the existing `ollama` service; `src/generation/setup.py` readiness check (presence + one real generation call, not just "listed") -- warm 0.9-1.0s, cold 68.8s (66.3s of it model load); found Docker Desktop's WSL2 VM budget (~7.6 GB) is tighter than host RAM and a cold load once crashed Neo4j+Qdrant (OOM, since recovered) -- [notes/step-6.1-ollama-llm-setup.md](notes/step-6.1-ollama-llm-setup.md); **mitigation choice (accept / smaller model / raise WSL2 memory) still open** | built, risk disclosed |
| 6 | LLM wrapper (M2): `LocalLLM.generate(prompt, temperature, max_tokens) -> str` and `.stream(prompt, temperature) -> Iterator[str]` in `src/orchestration/llm.py` -- wraps the raw `ollama` package (reuses M1's `build_client`), maps `max_tokens` to Ollama's own `num_predict`; found and fixed a real `or`-vs-`is None` default bug, and a live test's wrong assumption that the model follows "say exactly this word" instructions -- 100% coverage, 35 tests -- [notes/step-6.2-llm-wrapper.md](notes/step-6.2-llm-wrapper.md) | done |
| 6 | Prompt template design (M3, pure design, no code): system + user prompt templates in [docs/prompts.md](docs/prompts.md) -- strict grounding, hybrid citation strategy, a measured 2000-token context budget (verified against real chunk-size percentiles), explicit contradiction handling for this project's own known CEO discrepancy; a real, unedited worked example from live `HybridRetriever` output plus a hand-verified constructed example -- [notes/step-6.3-prompt-templates.md](notes/step-6.3-prompt-templates.md); **live follow-up testing (RULES text run against the real model, [docs/31](<docs/31 few_shot_tie_investigation_deep_dive.md>)) found the few-shot example teaches "pick a winner" and under-performs zero-shot (1/5 vs 4/5) at flagging the exact CEO disagreement it was built to handle -- disclosed, not fixed, see decision.md D-2026-09-29-25** | designed, not yet implemented; one real risk found and disclosed |
| 6 | RAG pipeline (M4): `RAGPipeline.query(question) -> str` / `.stream(question) -> Iterator[str]` in `src/orchestration/rag.py` wires `RetrievalPipeline` + `LocalLLM` + M3's design together for the first time; new `src/orchestration/prompts.py` turns the design into real, tested code (budget, numbered sources, mechanical citation fallback); `LocalLLM` gained the `system` parameter M3 pre-specified, verified live to measurably change real output; empty retrieval short-circuits to the exact refusal text without calling the model -- 32 hermetic tests + 2 live tests (fake reranker, no Cohere key needed) -- [notes/step-6.4-rag-pipeline.md](notes/step-6.4-rag-pipeline.md); **live testing found a real, separate timeout bug in `src/embedding/embedder.py`, flagged not fixed; the M3 few-shot risk (D-2026-09-29-25) ships as-is, backstopped by the mechanical fallback** | done, one new risk flagged separately |
| 6 | Context window management (M5): `fit_context(chunks: List[DocumentChunk], prompt, max_tokens) -> List[DocumentChunk]` in `src/orchestration/prompts.py` -- generalizes M4's fixed `CONTEXT_BUDGET_TOKENS=2000` into a real computation (`4096 - count_tokens_approx(prompt) - max_tokens`); `apply_budget` refactored to share the same `_greedy_best_fit` helper instead of duplicating the algorithm; `RAGPipeline` now measures its own real system prompt + question via `build_user_prompt(question, [])` rather than assuming a fixed size -- 96 hermetic tests, all passing; mypy clean -- [notes/step-6.5-context-window-management.md](notes/step-6.5-context-window-management.md); **disclosed trade-off: the new dynamic budget carries no safety margin, unlike the fixed 2000 it replaced (~1260 tokens of deliberate cushion, now gone) -- see decision.md D-2026-09-30-06; live end-to-end RAGPipeline verification is currently blocked by a host-level Ollama stall (isolated via a direct, code-free repro -- not this step's own bug), not yet re-confirmed** | logic done and hermetically proven; live re-verification pending |
| 6 | RAG integration tests (M6): new top-level `tests/test_rag.py` -- real `RetrievalPipeline` + real `prompts.py` + real `RAGPipeline`, only the `LLM` faked (hermetic, 3/3 passing, mypy clean); live tier mirrors `test_pipeline.py`'s no-Cohere-key fixture, reports all five labelled questions, strictly checks the three already known (Phase 5) to retrieve well -- [notes/step-6.6-rag-integration-tests.md](notes/step-6.6-rag-integration-tests.md); **found and fixed a real bug along the way: `build_llm` was reusing the readiness probe's 120s timeout for real ~400-token generation calls, leaving almost no margin -- corrected P6-M5's own "stuck runner" misdiagnosis to the real cause (a genuine host throughput ceiling, not a hang) and added a separate `GENERATION_TIMEOUT_SECONDS=300.0` (decision.md D-2026-09-30-11/-12); live result after the fix: 2/5 passing (up from 0/5), remaining 3 accepted as a disclosed host CPU limitation** | done -- hermetic proof + 2/5 strict live checks + mypy; remaining live gap is host hardware, not code |
| 6 | Integration checkpoint (M7, manual validation): 8 real questions (5 Phase-5 benchmark + 3 new: an out-of-scope refusal test, a CEO rephrasing, a compound question) run through the real `RAGPipeline` end to end -- [notes/step-6.7-integration-checkpoint.md](notes/step-6.7-integration-checkpoint.md); **5/8 completed (no OOM crash this run), 3/8 timed out even at 300s -- a measured 37.5% real timeout rate, the checkpoint's own headline finding; grounding/citations were strong (zero hallucinations, two documented Phase-5 misses handled safely -- one correct despite the miss, one honestly refused); found a NEW retrieval gap ("company values" misses culture.md's real Core Values section), contradicting M6's own `KNOWN_RELIABLE` assumption -- flagged via spawn_task (task_a1bdb8a8), not fixed inline** | done; surfaced a new retrieval gap and a material latency concern for Phase 7 |
| 7 | FastAPI app skeleton (M1): `src/api/main.py` and its `/health` endpoint already existed (present since before this session, tested by `tests/test_health.py`) and already exceeded the task's own "hello-world" bar -- confirmed rather than rebuilt; deep dive built instead: [docs/34 fastapi_skeleton_deep_dive.md](<docs/34 fastapi_skeleton_deep_dive.md>) (the ASGI app object, path operation decorators, automatic response serialization, the app-vs-server relationship, the health-check design pattern) | confirmed already satisfied; deep dive built |
| 7 | Chat endpoint with streaming (M2): `POST /chat` in `src/api/main.py` -- Pydantic `ChatRequest` (`query`, `k`) validation, real `RAGPipeline` built once at startup via FastAPI `lifespan` (real Qdrant/Neo4j/Ollama, real Cohere reranker if configured else the newly-promoted `IdentityReranker`), hand-rolled Server-Sent Events streaming `RAGPipeline.stream()`'s real fragments; `RAGPipeline.query`/`.stream` gained a per-call `k` override (decision.md D-2026-09-30-15 through -20) -- 11 new hermetic tests + 712 project-wide passing, mypy clean -- [notes/step-7.2-chat-endpoint.md](notes/step-7.2-chat-endpoint.md); **verified live: real end-to-end wiring confirmed (real Neo4j traversal, real config at `/health`), and a genuine real timeout correctly arrived at the client as a real SSE `[ERROR]` event, not a silent reset -- proving the new error-handling design works under a real, unstaged failure, not just hermetic fakes** | done, verified live |
| 7 | Validation & error handling (M3): app-wide `{"error": {"code", "message"}}` envelope for every error response; a real 400 (unparseable JSON) split from 422 (well-formed but invalid), a global 500 catch-all, and a 504 for `TimeoutError`/`httpx.TimeoutException`, all in `src/api/main.py`; `/health` gained a real `HealthResponse` model -- 8 new hermetic tests (`tests/test_errors.py`) -- [notes/step-7.3-error-handling.md](notes/step-7.3-error-handling.md); **proved before building anything (by reading `StreamingResponse.stream_response` directly) that the 500/504 handlers structurally cannot catch `/chat`'s own mid-stream failures -- headers are sent before the generator ever runs -- so they're built for future non-streaming endpoints, with that limitation documented rather than implied away; also closed a real, live-path gap found along the way: `Embedder`'s Ollama client had `timeout=None` (confirmed to mean no timeout at all), now a measured `EMBED_TIMEOUT_SECONDS=60.0`** | done |
| 7 | Logging & observability (M4): a hand-written `_JsonFormatter` (structured, one JSON object per log line) attached via the API's first-ever `logging.basicConfig`; a raw ASGI `RequestLoggingMiddleware` logging `method`/`path`/`status_code`/`duration_ms` per request, escalating to `WARNING` above `SLOW_REQUEST_MS=5000.0` -- 8 new hermetic tests (`tests/test_logging.py`) -- [notes/step-7.4-logging-observability.md](notes/step-7.4-logging-observability.md); **measured before choosing a middleware style: FastAPI's own `@app.middleware("http")` returned from `call_next` after 0.005s for a ~0.95s real streaming route (timing the response object's creation, not its completion) -- a raw ASGI middleware wrapping `send` directly measured the real 0.906s instead; also found and fixed, before it shipped, that the SAME middleware needed its own `try`/`except` to log P7-M3's own bare-`Exception` path at all, a direct, predicted consequence of that step's own `ServerErrorMiddleware` finding** | done |
| 7 | API documentation & testing (M5): verified the real, auto-generated OpenAPI schema (`app.openapi()`, read directly, not assumed) and closed what it found -- `/chat`'s 200 was an empty, wrong `application/json` schema (fixed via `response_class=StreamingResponse` + a real `text/event-stream` example), and 400/500/504 (real P7-M3 response shapes) were completely absent from the docs (added via `CHAT_RESPONSES`); `ChatRequest` gained a real example value, `HealthResponse`'s fields gained descriptions, both routes gained tags -- 7 new hermetic tests (`tests/test_openapi.py`) -- new [docs/api-usage.md](<docs/api-usage.md>) with every example run for real against the live app, including 500/504 via a temporary debug route (see [notes/step-7.5-api-documentation.md](notes/step-7.5-api-documentation.md)); **found and fixed a real bug in the fix itself: adding `responses={200: {"content": {"text/event-stream": ...}}}` alone did NOT replace FastAPI's own spurious default, it MERGED alongside it -- caught by re-fetching the real schema after the first attempt, traced to `fastapi/openapi/utils.py`'s own source (`response_class` governs this, not `responses=`), fixed with `response_class=StreamingResponse`** | done |
| 7 | API tests (M6): new top-level [`tests/test_api.py`](tests/test_api.py) -- 59 test items (53 hermetic, 4 strict xfails of known unhardened gaps, 2 live) asserting only what the 35 existing API tests could not see: the public route table, framework-raised errors and boundary values, the real retrieval stack behind real HTTP, the real `lifespan` (only its network calls replaced), incremental SSE delivery and concurrency via a raw-ASGI harness with negative controls, the JSON log lines an operator sees, and a small live tier; `src/api/main.py` coverage 88% -> 100%; full CI selection 733 -> 786 passed -- [notes/step-7.6-api-tests.md](notes/step-7.6-api-tests.md); **first ran the three CI gates since P7-M2 and found all red (15 E501, 3 unformatted files, 3 mypy errors) -- fixed first; probing the real app then found three contract defects no existing test could (404/405 outside the error envelope, contradicting my own "every error shares one envelope" claim; a whitespace-only query returned 200 with an in-band `[ERROR]`; `lifespan` leaked the Neo4j driver when startup failed) -- each written as a failing test first (12 red), then fixed; mutation-checked the streaming tests against the real `_sse_events`; live tier: one run hit this host's documented generation ceiling, a warm retry completed** | done |
| 7 | Integration checkpoint (M7): verified the API contract by being its client -- a Python frontend simulator ([`check_api_contract.py`](<practice codes/phase 7/m7_api_contract_checkpoint/check_api_contract.py>), 23 clauses: a deterministic tier on the real app with a scripted model, and a tolerant real-stack tier) plus a real-browser page (`fetch` + `ReadableStream`, Stop button, `EventSource`, cross-origin), written down as [`docs/api-contract.md`](docs/api-contract.md) with every clause naming its check -- [notes/step-7.7-api-contract-checkpoint.md](notes/step-7.7-api-contract-checkpoint.md); **found two defects in claims I had made (a lone `\r` in model text corrupted and could forge SSE events, confirmed with two independent parsers; `/openapi.json` declared a 422 body the app stopped sending in P7-M3, and a schema with no `required` validated anything) -- both fixed test-first; found and pinned gap G1 (an abandoned stream's model connection is closed only by the cyclic GC), with a verified but unapplied fix; my own reference SSE parser had a bug that its self-test caught; the checker was shown able to fail (9 of 9 mutation results as expected, after it corrected three of my expectations); the real browser confirmed no-CORS and `EventSource`'s GET-only limit** | done |
| 8 | Basic Streamlit chat UI (M1): [`src/ui/app.py`](src/ui/app.py) and [`src/ui/client.py`](src/ui/client.py) (no Streamlit: a hand-written SSE reader, error mapping, `AnswerStream`) -- the answer streams into the page with `st.write_stream`, sources go in an expander, the input is locked while a question is in flight, a clock shows the minutes-long wait for the first word, and model text is shown as plain text (the corpus's prices would otherwise render as maths); `Settings.api_url`; `.streamlit/config.toml` (127.0.0.1 only) -- 104 tests in [`tests/ui/`](tests/ui) (client unit tests plus `AppTest`), mutation-checked (10 + 16 deliberate bugs, all caught) -- [notes/step-8.1-streamlit-chat-ui.md](notes/step-8.1-streamlit-chat-ui.md); **found that inside a `finally` every `st.*` call and `st.session_state` access raises again while Streamlit stops a script (my first version would have left the page locked and sent a `None` question to the API), a spec-driven test caught a bug in my own SSE reader (a final CR), and `iter_lines()` was measured to lose text; verified in a real browser against the stub API and once against the real stack (4 min 27 s for a one-sentence answer on this CPU-only host)**; deep dive: [docs/39](<docs/39 streamlit_chat_ui_deep_dive.md>) | done |
| 8 | Configuration & environment (M3): the chat page's API address is the setting `API_URL` (environment variable, then `.env` in the current folder, then the default `http://localhost:8000`), checked by [`src/ui/endpoint.py`](src/ui/endpoint.py) -- a mistyped value (no `http://`, a stray space, `/chat` pasted in, a password in the address) now stops the page with an error that names the fix, where before the sidebar said "Cannot reach the API" (measured: `localhost:8000` without a scheme was reported as a lost connection); trailing slashes are tidied; the precedence and the run-from-the-repo-root pitfall are pinned by tests and written up in [Configuration](#configuration) below -- [notes/step-8.3-ui-configuration.md](notes/step-8.3-ui-configuration.md); **measured why not Streamlit's own config: `config.toml` rejects custom keys and `st.secrets` raises when no `secrets.toml` exists** | done |

Run the ingestion pipeline:

```bash
cp .env.example .env          # then set NEO4J_PASSWORD (required)
docker compose up -d ollama
docker exec advancerag-ollama-1 ollama pull nomic-embed-text
python -m src.ingestion.pipeline
```

## Configuration

Settings live in one object, `Settings` in [src/core/config.py](src/core/config.py). Each field takes
the first value it finds in this order: **an environment variable, then a line in `.env`, then the
default written in the code.** Two things catch people out:

- `.env` is looked up in the *current folder*. Start things from the repository root; from anywhere
  else the file is silently not found and the defaults apply.
- Settings are read once, when the process starts. After editing `.env`, restart `uvicorn` or
  `streamlit run`.

| Variable | Default | Read by | Notes |
|---|---|---|---|
| `API_URL` | `http://localhost:8000` | the chat page, `streamlit run src/ui/app.py` | Where the page finds the API. Must start with `http://` or `https://`; a trailing slash is fine. A bad value (no scheme, a space, `/chat` pasted in, a password in the address) shows an error page that names the fix, with no chat input. |

The other variables (`NEO4J_*`, `COHERE_API_KEY`, ...) are listed in [.env.example](.env.example)
and defined, with their defaults, in `src/core/config.py`. To try another API address without touching
`.env` (the environment wins over `.env`):

```bash
API_URL=http://localhost:8004 streamlit run src/ui/app.py                 # bash
```

```powershell
$env:API_URL = "http://localhost:8004"; streamlit run src/ui/app.py       # PowerShell
Remove-Item Env:API_URL     # PowerShell keeps it set for the whole window; bash's prefix lasted one command
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
pytest tests/ -m live -rs                          # real Qdrant, Ollama, Neo4j (skip if down)
pytest tests/ --cov=src --cov-report=term-missing   # coverage
python -m src.graph.schema --check                  # graph constraints in force? (read-only)
ruff check src/ tests/                              # CI gate 1: lint
ruff format --check src/ tests/                     # CI gate 2: formatting (ruff format src/ tests/ fixes it)
mypy src/                                           # CI gate 3: types
pytest tests/ -m "not tiktoken and not live"        # CI gate 4: exactly what CI's test job runs
python "practice codes/phase 7/m7_api_contract_checkpoint/check_api_contract.py" --tier deterministic  # P7-M7: the API contract as a frontend sees it (~12 s)
python "practice codes/phase 7/m7_api_contract_checkpoint/run_mutants.py"       # P7-M7: prove that checker can fail (~2 min)
streamlit run src/ui/app.py                         # P8-M1: the chat page (start the API first; API_URL overrides its address)
```

The contract tests **skip** when `data/chunks/` is empty, which is the normal state on a
fresh clone and in CI (the directory is gitignored generated output). Run the pipeline first
to enforce them for real.
