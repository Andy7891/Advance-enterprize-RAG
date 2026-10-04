# Phase 7, Microstep 3 — Request/Response Validation & Error Handling

**Date:** 2026-09-30
**Core learning: Yes (production readiness).** The first step that asks "what does this app do
when something goes wrong," rather than "what does it do when everything works" — and the first
place a genuinely load-bearing constraint from P7-M2 (headers sent before the SSE generator ever
runs) directly limits what a standard, idiomatic FastAPI mechanism can actually accomplish.

## What was already done vs. what this step closes
"Pydantic models for validation" (this task's own first output) was already built in P7-M2 —
`ChatRequest`'s `query`/`k` constraints. This step closes the other two: **error handlers**
(400/500/504) and **structured error responses** — plus one small, genuinely-in-scope gap in
"response validation": `/health` returned a bare `dict` with no schema until now.

## The finding that shaped every decision here
Read `starlette/responses.py`'s real `StreamingResponse.stream_response` directly before designing
anything:
```python
async def stream_response(self, send: Send) -> None:
    await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
    async for chunk in self.body_iterator:
        ...
```
The 200 status line is sent **before** the generator is ever touched. Every exception `rag.stream()`
can raise — a Qdrant/Neo4j failure during retrieval, an Ollama timeout during generation — happens
strictly after that point. A global FastAPI exception handler, the standard mechanism this step
asks for, **cannot** change `/chat`'s status code for any of those failures. They stay exactly what
P7-M2 already built: the in-stream `data: [ERROR] ...` event. Every decision below either works
within that constraint or explicitly documents it rather than implying protection it can't deliver.

Also checked empirically before designing the 400/422 split: FastAPI's own default returns **422**
for both a genuinely malformed (unparseable) JSON body and a well-formed-but-invalid one — there
was no real 400 anywhere in this app until this step added the distinction deliberately.

## What was built
- **`HealthResponse`** (`BaseModel`): `/health` now declares `response_model=HealthResponse`
  instead of returning a bare `dict` — satisfies "response validation" literally.
- **`_error_envelope(code, message)`**: the one JSON shape (`{"error": {"code", "message"}}`)
  every new/changed error response returns.
- **`_handle_validation_error`** (`@app.exception_handler(RequestValidationError)`): 400 for a
  body that fails to parse as JSON at all, 422 (with every field error joined into one message)
  for a well-formed body that fails a field's own rules.
- **`_handle_timeout`** (`@app.exception_handler(TimeoutError)` + `@app.exception_handler(httpx.TimeoutException)`,
  one function, two registrations): structured 504, logged as a warning. Built for future
  synchronous endpoints; documented as unreachable from `/chat`'s own real failures.
- **`_handle_unexpected_error`** (`@app.exception_handler(Exception)`): structured 500, logs the
  real traceback via `logger.exception`, hides implementation details from the response body.
- **`EMBED_TIMEOUT_SECONDS = 60.0`** (`src/embedding/embedder.py`): `build_ollama_client` now
  passes an explicit timeout — previously `ollama.Client(host=ollama_url)` with no `timeout` kwarg,
  which defaults to `timeout=None`, confirmed to mean **no timeout at all**, not httpx's own 5s
  default. Every `/chat` request embeds its query through this exact client.
- **[`tests/test_errors.py`](../tests/test_errors.py)** (new, 8 tests): the 400/422 split, the
  shared envelope shape, both new catch-alls (via two test-only routes — neither `/health` nor
  `/chat` has a real synchronous path that can fail this way), `/health`'s new response model, and
  a regression proving `/chat`'s own mid-stream `TimeoutError` is still handled by `_sse_events`'s
  existing `try`/`except`, not intercepted by the new 504 handler.

## Decisions (all "recommended")
| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| D21 400 vs 422 | 400 only for unparseable JSON; field errors stay 422 | Remap everything to 400 | Matches real REST semantics; FastAPI defaults both to 422 today (verified) |
| D22 Error shape | One `{"error": {"code", "message"}}` envelope everywhere | Keep two different shapes (old + new) | The task's own ask was a *unified*, structured shape |
| D23 500 scope | One global `Exception` handler | Per-exception-type handlers; per-endpoint try/except | Proven to only ever reach pre-stream code; one handler is enough for that surface |
| D24 504 despite the limitation | Build it now, document the `/chat` blind spot | Restructure `/chat`'s control flow; skip it entirely | Correct for future endpoints; restructuring was a bigger change than asked, and still incomplete |
| D25 Embedder timeout | Fix it now, as part of this step | Leave it out of scope (different module) | Live, real-path risk, directly on-theme for a "timeout handling" step |
| D26 504 exception types | Real `TimeoutError` + `httpx.TimeoutException` | A new, unused `UpstreamTimeout` type | Matches what this project's real httpx-based dependencies actually raise |
| D27 File location | Inline in `main.py` | New `src/api/errors.py` | ~55 lines in a 111-line file; no real size problem yet |

## Verified, not assumed
```
malformed JSON -> 422 today   : confirmed via a live TestClient call BEFORE writing any handler
                                 (both a genuinely unparseable body and a valid-JSON-wrong-shape
                                 body defaulted to 422 -- no real 400 existed anywhere in this app)
headers sent before generator : read starlette/responses.py's StreamingResponse.stream_response
  runs, for real                directly -- http.response.start is awaited BEFORE the body_iterator
                                 is ever touched, proving no exception inside _sse_events can ever
                                 reach a global exception handler
Exception handler routing     : read starlette/applications.py's build_middleware_stack directly --
                                 `if key in (500, Exception): error_handler = value` (lines 59-69)
                                 confirms a handler for the literal Exception class is routed to
                                 ServerErrorMiddleware specifically, not the same dict used by
                                 RequestValidationError/TimeoutError's own, different handling path
Embedder's real timeout       : ollama.Client(host=...) with no timeout kwarg defaults to
  before the fix                 timeout=None; confirmed httpx.Client(timeout=None).timeout has
                                 connect/read/write/pool all None -- genuinely no timeout, checked
                                 directly rather than assumed to mean "httpx's normal default"
embed_query / embed_batch     : measured live against this project's own warm Ollama (2026-09-30):
  real latency                  embed_query 0.701s; a full 16-text embed_batch call 1.005s
hermetic tests                 : 8 new (tests/test_errors.py) + 29 regression (test_chat.py,
                                 test_health.py, embedding/test_embedder.py) all passing
```

## Findings
1. **A standard FastAPI mechanism can be simultaneously "correctly built" and "unable to help the
   one endpoint that most needs it."** The 500/504 handlers are idiomatic, correctly wired, and
   pass real tests — and still cannot touch `/chat`'s own real failures, because of a structural
   fact (headers-before-body in `StreamingResponse`) that has nothing to do with whether the
   handlers themselves are written correctly. Building them anyway, with the limitation stated
   plainly, was the right call for future endpoints — but it would have been easy to ship this and
   imply more protection than it actually provides.
2. **`TestClient`'s `raise_server_exceptions` default made a correctly-working handler look
   broken.** The very first test run of the 500 handler failed — not because `_handle_unexpected_error`
   was wrong, but because registering a handler for the literal `Exception` class routes through
   `ServerErrorMiddleware`, which re-raises the original exception *after* sending the handler's own
   response (so a real server's own error logging still sees it). `TestClient`'s default
   (`raise_server_exceptions=True`) surfaces that re-raise back into the test. Reading
   `starlette/applications.py` directly explained exactly why only the 500 test (not the 504 ones,
   registered for specific classes) needed `raise_server_exceptions=False`.
3. **A "timeout" config value that silently means "no timeout" is a different, more serious bug
   than a too-short one.** `Embedder`'s client wasn't missing a *generous* timeout — it had none at
   all (`timeout=None`, verified to disable every httpx timeout kind). A too-short timeout fails
   fast and visibly; `timeout=None` can hang a worker thread indefinitely with nothing to catch.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🟡 **Streaming responses break the "exception → clean HTTP status" assumption structurally, not
  just by convention.** This isn't specific to RAG, but it's close to universal across real
  LLM-streaming APIs (this project's own, and OpenAI's) — worth understanding as a general shape,
  not a one-off quirk of this codebase.
- 🟡 **An explicit, bounded timeout is part of a RAG pipeline's own reliability surface**, same
  category as the already-fixed `GENERATION_TIMEOUT_SECONDS` gap — an unbounded call anywhere in
  the retrieval→generation chain is a real, not hypothetical, availability risk.
- ⚪ The 400/422 HTTP-semantics distinction, the structured envelope shape, and
  `TestClient(raise_server_exceptions=...)` — real, useful, but general API/testing mechanics, not
  RAG-specific.

## Honest limitations
1. **The 500/504 handlers have no real path to firing through either existing endpoint today.**
   `/health` has no failure mode of its own; `/chat`'s own real failures all occur inside
   `_sse_events`, after headers are sent. Both are tested exclusively through dedicated test-only
   routes (`tests/test_errors.py`) — a legitimate, disclosed way to test a cross-cutting handler in
   isolation, not a claim that either production endpoint currently exercises them.
2. **Neo4j and Qdrant calls inside the retrieval path still have no query-level timeout** (Neo4j's
   driver has only a 5s *connection* timeout; Qdrant's client has none at all) — noticed while
   investigating `Embedder`'s gap, but left out of this step's scope: fixing it would mean auditing
   every remaining client construction site, a larger undertaking than this step's own literal ask.
3. **The 400/422 split only distinguishes "not JSON" from "JSON but invalid."** A request with the
   right JSON shape but a wrong `Content-Type` header, or other more exotic malformed-request
   cases, aren't specifically classified — they'd fall through to whatever FastAPI/Starlette already
   does by default, unexamined here.

## How this connects
**Previous:** P7-M2 built `/chat` and, in doing so, established the in-stream `[ERROR]` pattern and
the headers-already-sent constraint this step had to design around rather than fight. **This step:**
gives the whole app (not just `/chat`) a consistent, structured way to fail, within that constraint's
real limits. **Next (not yet specified):** `/ingest`/`/query` (flagged as coming in `main.py`'s own
docstring) are the first endpoints where the 500/504 handlers would have real, natural coverage,
since neither needs to stream.

## How to do this yourself
1. Before adding a global error-handling mechanism to any framework, check — by reading the
   framework's own source for the specific response type in use, not by assuming — whether there's
   a structural reason (like headers already being sent) that limits what it can actually catch.
2. When a test for new code fails, don't assume the new code is wrong before checking whether the
   TEST HARNESS itself has a default (like `TestClient`'s `raise_server_exceptions`) that changes
   what "the test failed" even means.
3. A timeout value of `None` and a merely-short timeout value are different classes of risk — check
   which one an "unconfigured" default actually produces, rather than assuming "no timeout set"
   always means "some reasonable library default applies."

## Practice exercises
1. Add a fourth test-only route that raises `ValueError` (not `TimeoutError`, not a plain
   `RuntimeError`) and confirm which handler catches it — trace through the MRO-based lookup
   yourself before running it, then check your prediction.
2. Temporarily remove `raise_server_exceptions=False` from `tests/test_errors.py`'s `client` and
   re-run just the 500 test. Read the real traceback that surfaces and confirm it matches this
   step's own Finding 2, in your own words.
3. Add a `/_test/raises-neo4j-unavailable` route that raises `neo4j.exceptions.ServiceUnavailable`
   directly. Does it get caught by the 500 handler, the 504 handler, or neither? What does that
   tell you about whether Neo4j's own exceptions should get their own registered handler someday?
4. Using `curl -i` against a real running server, send a request with a `Content-Type` header of
   `text/plain` instead of `application/json` but a valid JSON body. What status code comes back
   today, and does it match either the 400 or 422 path this step built?
5. Sketch (on paper, no code needed) how `/ingest`'s own error handling might differ from `/chat`'s
   — specifically, which of today's four handlers would actually have a natural, reachable path to
   fire for a slow or failing document-ingestion request, given it wouldn't need to stream.

## Sources
- [FastAPI: Handling Errors](https://fastapi.tiangolo.com/tutorial/handling-errors/) — the
  documented mechanism for overriding default exception handlers.
- [MDN: 400 Bad Request](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400)
- [MDN: 422 Unprocessable Content](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/422)
- [MDN: 504 Gateway Timeout](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/504)
- [httpx: Exceptions](https://www.python-httpx.org/exceptions/) — the real `TimeoutException`
  hierarchy `_handle_timeout` is registered against.
- `starlette/responses.py` (`StreamingResponse.stream_response`) and `starlette/applications.py`
  (`build_middleware_stack`) — read directly from the installed package; `www.starlette.io`'s own
  docs site could not be reached from this environment (DNS resolution failure, confirmed via both
  `curl` and the browser tool), so the installed source was used instead — consistent with this
  project's own established preference for reading real source over trusting prose documentation.
