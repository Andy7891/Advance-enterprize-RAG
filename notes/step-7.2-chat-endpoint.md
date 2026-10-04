# Phase 7, Microstep 2 — Create Chat Endpoint with Streaming

**Date:** 2026-09-30
**Core learning: Yes — FastAPI streaming.** The first endpoint that actually calls `RAGPipeline`,
and the first place this project has needed to think carefully about async vs. sync, HTTP
streaming semantics, and what "error handling" even means once response headers are already sent.

## The gap this step closes
`src/api/main.py` had a real, working `/health` endpoint (P7-M1) and Phase 6 had a fully-built,
tested `RAGPipeline` — but nothing connected them. This step is that connection: a real `POST
/chat` endpoint, with real request validation, that streams a real RAG answer back as it's
generated.

## What was built
- **`ChatRequest`** (Pydantic `BaseModel`): `query: str` (min length 1), `k: int = 3` (must be > 0).
- **`lifespan`** (FastAPI's `@asynccontextmanager` startup/shutdown hook): builds the real
  `RAGPipeline` once — real `HybridRetriever` (Qdrant + Neo4j), a real Cohere `Reranker` when
  `COHERE_API_KEY` is configured else `IdentityReranker`, a real `LocalLLM` — and stores it on
  `app.state.rag`. Closes the Neo4j driver cleanly at shutdown.
- **`POST /chat`**: validates the request via `ChatRequest`, reads the shared pipeline off
  `app.state`, and returns a `StreamingResponse` wrapping a hand-rolled SSE generator.
- **`_sse_events`**: walks `RAGPipeline.stream(query, k=k)`'s real fragments, framing each as
  `data: <text>\n\n` (re-prefixing every internal newline so the mechanical source list's own
  multi-line final chunk still frames correctly), ending with `data: [DONE]\n\n`. Catches any
  exception raised mid-stream and yields it as a final `data: [ERROR] ...` event instead of
  letting the connection die silently.
- **`RAGPipeline.query`/`.stream`** ([src/orchestration/rag.py](../src/orchestration/rag.py)):
  gained an optional per-call `k: int | None = None`, resolved with `self._k if k is None else k`
  and validated the same way construction-time `k` always was.
- **`IdentityReranker`** ([src/retrieval/reranker.py](../src/retrieval/reranker.py)): promoted
  from a local test-only fixture (previously duplicated three times) into a real, exported,
  validated production class.
- **[`tests/test_chat.py`](../tests/test_chat.py)** (new): 11 hermetic tests — request validation
  (missing/empty query, zero/negative/non-integer `k`), SSE framing (one event per fragment,
  multi-line framing, the `[DONE]` marker), `k` pass-through to real retrieval, the empty-retrieval
  refusal path, and the mid-stream error-to-SSE-event path.

## Decisions (all "recommended")
| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| D1 Per-request `k` | Optional per-call override on `query`/`stream`, `is None` not `or` | A new pipeline per request; ignore the client's `k` | mirrors `RetrievalPipeline`'s own per-call design; avoids rebuilding real client wiring every request |
| D2 Request validation | A real Pydantic `ChatRequest` model | A bare `dict`, validated by hand | FastAPI's own idiomatic mechanism; free 422s and OpenAPI docs |
| D3 Reranker | Real Cohere when configured, else `IdentityReranker` | Always require a key; always skip Cohere | mirrors this project's established no-key live-test fallback |
| D4 SSE implementation | Hand-rolled `StreamingResponse` + manual framing | `sse-starlette`'s `EventSourceResponse` | this step is explicitly about learning the raw mechanism |
| D5 Pipeline lifetime | Built once via `lifespan`, on `app.state` | Per-request rebuild; a module-level global | real clients are expensive; a global would break `tests/test_health.py` by running real setup at import time |
| D6 Mid-stream errors | Caught, sent as a final SSE error event | Propagate unchanged (this project's usual rule) | headers are already sent once streaming starts — propagating just resets the connection silently |

## Verified, not assumed
```
sync generator + async event loop : read Starlette's OWN StreamingResponse.__init__ source --
                                     confirmed a plain sync generator is wrapped in
                                     iterate_in_threadpool (anyio.to_thread.run_sync per item),
                                     so RAGPipeline.stream()'s real, blocking, 100-300+s network
                                     calls never block the event loop other requests share
lifespan vs. TestClient            : confirmed empirically that TestClient(app) WITHOUT `with`
                                     never triggers lifespan (tests/test_health.py's own passing
                                     test, unaffected by adding lifespan=lifespan to FastAPI(...))
hermetic tests                     : 11/11 passed; mypy clean
full project suite                 : 712 non-live tests passed (no regressions)
live, real server                  : started via `uvicorn src.api.main:app` (added QDRANT_URL/
                                      OLLAMA_URL host overrides to .env, matching the existing
                                      NEO4J_URI pattern); lifespan completed successfully (real
                                      Qdrant+Neo4j+Ollama all connected); /health returned the
                                      real, correct localhost URLs
live, /chat                        : two real requests both hit the already-disclosed ~300s host
                                      throughput ceiling (docker top showed the same genuine,
                                      multi-core-saturated computation pattern from P6-M5/M6/M7,
                                      not a new bug) -- but BOTH arrived at the client as a real,
                                      readable "data: [ERROR] timed out" SSE event, not a silent
                                      connection reset, directly confirming D6's design works
                                      under a real, unstaged failure
```

## Findings
1. **A real architectural risk (sync generator inside an async framework) was verified, not
   assumed, by reading Starlette's own source.** This project's entire RAG stack
   (`RetrievalPipeline`, `LocalLLM`, `RAGPipeline`) is synchronous by design (every prior
   microstep's own decisions). Wiring that directly into FastAPI could have silently blocked the
   whole server for the duration of one long request — reading `StreamingResponse.__init__` and
   `iterate_in_threadpool` directly (not trusting general "FastAPI handles async for you" folklore)
   confirmed this is already handled correctly, automatically, with zero extra code.
2. **A three-times-duplicated test fixture was a real signal, not just a smell.** `IdentityReranker`
   existed as three separate, near-identical local test classes before this step needed the exact
   same behavior in real production code. Promoting it once, to `src/retrieval/reranker.py`,
   serves both — a real convergence of "the tests already needed this" and "production now needs
   it too."
3. **The live throughput ceiling, already disclosed three times (P6-M5/M6/M7), incidentally
   became the test case for this step's own new error-handling design.** Rather than needing to
   contrive a failure to verify D6 worked, a genuine, already-known-to-be-marginal real request
   failed naturally during live testing — and the new code handled it exactly as designed.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **ASGI + sync/async boundaries:** understanding that Starlette automatically threads a sync
  generator, and WHY that matters for a project whose entire domain logic is synchronous by
  design — see [docs/34](<../docs/34 fastapi_skeleton_deep_dive.md>) C4 for the underlying mechanism.
- 🔴 **Server-Sent Events, hand-framed:** the real wire format (`data: ...\n\n`, multi-line
  framing, a `[DONE]` convention), built directly rather than hidden behind a library.
- 🔴 **Error handling changes shape once a response has started streaming:** the same "never
  silently degrade" principle this project has followed seven times before, applied correctly to a
  case where the USUAL mechanism (propagate, let the caller catch it) genuinely isn't available
  anymore.
- 🟡 **`lifespan` vs. a module-level global:** both build real dependencies once, but only one of
  them respects "importing this module should never have real side effects."
- ⚪ The exact Pydantic `Field` constraints (`min_length=1`, `gt=0`) — specific validation rules,
  not a new concept.

## Honest limitations
1. **Live, end-to-end success (a real completed streamed answer through `/chat`) was not observed
   this session** — both real attempts hit the already-disclosed host throughput ceiling before
   completing. The endpoint's own correctness is thoroughly proven (11 hermetic tests covering
   every code path, including a hermetic version of the exact failure mode that occurred live);
   what's unverified is only the "happy path, live, end to end" combination, blocked by hardware,
   not code.
2. **No authentication, rate limiting, or request size limits exist on `/chat` yet** — appropriate
   for local development, not yet for anything more exposed. Not this step's stated scope.
3. **`max_tokens` and `temperature` are not exposed in the request** — only `k`, matching the
   task's own literal request shape. A caller cannot currently ask for a shorter (faster) answer
   to work around the throughput ceiling via the API itself.

## How this connects
**Previous:** P7-M1 confirmed the FastAPI skeleton; Phase 6 built and thoroughly tested
`RAGPipeline`. **This step:** the two are connected for the first time, through a real, streaming,
validated endpoint. **Next (not yet specified):** likely request/response logging, error response
shaping for non-streaming failures (a bad request never reaches RAGPipeline at all, but what about
a `/health`-style readiness check for the pipeline itself?), or addressing Honest Limitation 3
given this step's own live finding.

## How to do this yourself
1. Before wiring synchronous, potentially-slow domain code into an async web framework, verify
   (don't assume) how the framework actually handles it — read the actual source of the exact
   function in the exact installed version, the way this step did for `StreamingResponse`.
2. When you notice the same small helper class duplicated across multiple test files, ask whether
   it's actually a sign that a REAL, production version belongs somewhere shared — not just a
   testing convenience to tolerate.
3. For any response type where headers are sent before the full outcome is known (streaming,
   chunked responses, long-polling), design error handling as an IN-BAND signal the client can
   still see, not an HTTP status code that can no longer be changed.
4. Validate a deliberately-risky design decision live, not just hermetically, when you get the
   chance — even an unplanned real failure (like this step's own timeout) can become the strongest
   possible proof a design choice works, if you built it to handle exactly that case.

## Practice exercises
1. Using `curl -N -X POST .../chat -d '{"query": "...", "k": 3}'`, watch the raw SSE bytes arrive
   in real time (the `-N` flag disables curl's own buffering). Compare this to what
   `response.text` shows in `tests/test_chat.py`'s own hermetic tests — same format, two very
   different ways of observing it.
2. Add a `max_tokens` field to `ChatRequest` (optional, defaulting to `RAGPipeline`'s own
   `DEFAULT_MAX_TOKENS`), and thread it through to `RAGPipeline.stream()`. This needs a SECOND
   per-call override on `RAGPipeline` itself, mirroring exactly how `k` was added — write it the
   same way.
3. In `tests/test_chat.py`, add a test using `with TestClient(app) as client:` (triggering the
   REAL `lifespan`) against a deliberately-down real service, and observe what happens to
   `app.state.rag` and the request — does FastAPI's own startup failure handling behave the way
   you'd expect?
4. Modify `_sse_events` to also yield a `data: [START]\n\n` event before the first real fragment.
   What real use might a client have for an explicit start marker, beyond the implicit "first byte
   arrived"?
5. Read `iterate_in_threadpool`'s source once more (quoted above) and explain, in your own words,
   why this mechanism specifically helps for I/O-bound blocking calls (like `RAGPipeline.stream()`)
   but would NOT meaningfully help for a CPU-bound blocking loop, given Python's GIL.

## Sources
- [FastAPI: Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)
- [MDN: Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) —
  the real SSE wire format this step hand-implements.
- [docs/34 fastapi_skeleton_deep_dive.md](<../docs/34 fastapi_skeleton_deep_dive.md>) — the ASGI
  fundamentals (C1, C4) this step builds directly on.
- `starlette.responses.StreamingResponse` / `starlette.concurrency.iterate_in_threadpool` (read
  directly from the installed package) — the real, verified mechanism behind Finding 1.
