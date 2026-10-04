# Phase 7, Microstep 4 — Add Logging & Observability

**Date:** 2026-09-30
**Core learning: Yes.** The first step that asks "what does the app tell us about ITSELF while it
runs," not "what does it do for a caller" — and the first place a genuinely load-bearing lesson
from P7-M3 (two different middlewares for two different kinds of failures) directly determines
whether a new middleware can even see the thing it needs to log.

## What was already done vs. what this step closes
P7-M3 already added some error logging — `_handle_timeout`/`_handle_unexpected_error` call
`logger.warning`/`.exception`. Neither needed a single code change this step: attaching a
structured formatter to the logger they already use upgrades both to the new JSON format for
free. What's genuinely new: request/response logging, real latency measurement, and the logging
configuration itself — the API process had **zero** logging setup before this step (no
`basicConfig`, no handler, nothing beyond a bare `logging.getLogger(__name__)`).

## Three things verified before designing this
1. **FastAPI's usual `@app.middleware("http")` cannot correctly time `/chat`.** Measured directly:
   a streaming route taking ~0.95s real wall-clock time showed `call_next` returning after
   **0.005s** under `BaseHTTPMiddleware` — it returns once the response object exists, not once a
   stream finishes. A raw ASGI middleware, wrapping `send` directly and watching for the real
   final response message, measured **0.906s** for the same case.
2. **That same raw middleware needs its own `try`/`except`.** Confirmed directly: a request handled
   by a *specific* registered handler (422/504) is a normal completion the middleware sees fine;
   but the bare-`Exception` path (P7-M3's `_handle_unexpected_error`) propagates the exception
   *through* the middleware instead, because `ServerErrorMiddleware` sits outside any
   `app.add_middleware(...)` layer and sends its own response on its own `send`. Removing the
   `try`/`except` during design and re-running `tests/test_logging.py`'s bare-exception test
   confirmed it: without it, that one case — the most important to have visibility into — produced
   no log line at all.
3. **`logging.basicConfig()` in `main.py` survives real `uvicorn` startup.** Started a minimal app
   under real `uvicorn`, confirmed a custom format set at import time still applied to a log line
   from inside a live request handler — uvicorn configures its own named loggers independently
   rather than overwriting root's handlers.

## What was built
- **`_JsonFormatter`** (`logging.Formatter` subclass): one JSON object per log line — timestamp,
  level, logger name, message, every `extra={}` field, plus `exception` when `exc_info` is set.
- **`logging.basicConfig(level=logging.INFO, handlers=[_handler])`**, module-level in
  `src/api/main.py`: the API's first-ever logging configuration, mirroring the exact
  `basicConfig(level=logging.INFO, format=...)` pattern already used by this project's 5 other
  entry points.
- **`RequestLoggingMiddleware`** (raw ASGI middleware): logs one line per request — `method`,
  `path`, `status_code`, `duration_ms` — with the real, full duration (streaming included) and the
  real final status code for every error path, including the bare-`Exception` one.
- **`SLOW_REQUEST_MS = 5000.0`**: a request logs at `WARNING` instead of `INFO` once it crosses
  this bound.
- **[`tests/test_logging.py`](../tests/test_logging.py)** (new, 8 tests): normal-request shape,
  real positive `duration_ms`, the core claim (a streaming response's logged duration reflects the
  full stream, verified via a fake LLM that sleeps across fragments), both error paths (specific
  handler vs. bare `Exception`, via a test-only route), severity escalation in both directions, and
  the formatter itself producing valid, complete JSON.

## Decisions (all "recommended")
| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| D28 Structured format | Hand-written `_JsonFormatter` | `python-json-logger`; `structlog` | Small enough to teach directly; matches this project's SSE precedent |
| D29 Middleware type | Raw ASGI, wrapping `send` | `@app.middleware("http")` | Proven the latter mis-times streaming by ~3 orders of magnitude |
| D30 Exception handling in the middleware | `try`/`except` around `await self._app(...)` | `send`-wrapping alone | Proven the bare-`Exception` path produces no log line without it |
| D31 Log granularity | One combined line per request | Two lines + a request ID | Sufficient at this project's current scale; no aggregation system yet |
| D32 Logged fields | `method`/`path`/`status_code`/`duration_ms` only | Also log query/answer text | No prior endpoint has logged user input verbatim; not needed to answer "is this slow" |
| D33 "Metrics" scope | A `duration_ms` log field | A Prometheus `/metrics` endpoint | Logging-centric task framing; no scraper exists to consume a metrics endpoint |
| D34 Severity threshold | WARNING above 5000ms | Uniform INFO | Directly motivated by this project's own repeatedly-measured latency ceiling |

## Verified, not assumed
```
BaseHTTPMiddleware mis-times     : measured directly -- call_next returned after 0.005s for a
  a streaming response             ~0.95s real streaming route; a raw ASGI middleware wrapping
                                    send measured 0.906s for the identical case
the bare-Exception blind spot    : measured directly -- a request handled by a SPECIFIC handler
  in a custom middleware            (422/504-equivalent) is a normal completion my middleware
                                    sees fine; a bare-Exception-handled one propagates as a raised
                                    exception THROUGH my middleware instead (status_holder stayed
                                    at its None/default), confirmed with a live toy app before
                                    writing the real middleware
basicConfig survives real        : started a minimal app under real `uvicorn ...`, confirmed a
  uvicorn startup                   custom-formatted log line from a live request handler used
                                    the custom format, not anything uvicorn's own dictConfig set
a real side effect, found live   : every stdlib-logging-based dependency (confirmed: httpx, its
                                    own "HTTP Request: GET http://localhost:6333 ..." line) now
                                    also emits JSON through the same root-attached formatter
hermetic tests                    : 8 new (tests/test_logging.py) + 20 regression (test_chat.py,
                                    test_health.py, test_errors.py) + 728 project-wide (94
                                    deselected live), all passing
live, real server                 : started via `uvicorn src.api.main:app`; real /health (200)
                                     and two real error requests (400 malformed JSON, 422 missing
                                     fields) each produced a correct, complete JSON log line with
                                     the real status and a real, small duration_ms
```

## Findings
1. **A framework's most convenient-looking middleware decorator can be silently wrong for exactly
   the case that matters most.** `@app.middleware("http")` is the first result in nearly every
   FastAPI logging-middleware tutorial — and it would have made `/chat`'s latency, this project's
   own headline concern across four prior microsteps, invisible, logged as consistently near-zero
   regardless of how long a real generation actually took.
2. **A structural finding from the PREVIOUS microstep (P7-M3's two-middleware split) directly
   determined a real bug in THIS step's own first design, before it was ever written wrong.**
   Because P7-M3 had already proven `ServerErrorMiddleware` sits outside any `app.add_middleware(...)`
   layer, it was possible to predict — and then confirm — that a naive `send`-wrapping middleware
   would miss the bare-`Exception` case, and design the `try`/`except` fix in from the start rather
   than discovering it the hard way a second time.
3. **Attaching a formatter to the root logger has a real blast radius beyond this project's own
   code.** Every dependency that logs via stdlib `logging` without its own `propagate=False` now
   emits JSON too — not designed for, found by actually running the live server and reading its
   real output, and judged a reasonable outcome rather than something to suppress.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **A response object existing is not the same as a response being finished** — the same
  underlying fact `StreamingResponse` taught in P7-M2 (C4), now shown to also break a DIFFERENT
  framework feature (`BaseHTTPMiddleware`'s `call_next`) that looks unrelated on the surface but
  shares the exact same root cause.
- 🔴 **Raw ASGI middleware — wrapping `send` directly — is the general tool for anything that needs
  to observe a response's real, full lifecycle**, not just this one case; the same technique
  underlies `ServerErrorMiddleware`'s own `response_started` tracking (read directly in P7-M3).
- 🟡 **A custom `logging.Formatter` is a small, teachable amount of code** for something
  ("structured logging") that's often reached for as a library by default.
- ⚪ The exact JSON field names (`method`, `path`, `status_code`, `duration_ms`) and the 5000ms
  threshold — specific configuration choices, not new concepts.

## Honest limitations
1. **No request ID / correlation ID.** Fine at this project's current scale (one combined log line
   already carries everything known about a request), but a real limitation the moment concurrent
   traffic or a log-aggregation system makes untangling interleaved lines from different requests
   a real, felt need — disclosed in D-2026-09-30-31, not silently assumed away.
2. **"Performance metrics" is a log field, not a queryable metrics system.** Answering "what's our
   p95 latency this week" today means manually parsing log files, not querying Prometheus/Grafana —
   a real gap past this project's current single-developer, local-logs scale.
3. **`SLOW_REQUEST_MS = 5000.0` is a reasoned choice, not one derived from real `/chat` production
   traffic distribution** (unlike `GENERATION_TIMEOUT_SECONDS`/`EMBED_TIMEOUT_SECONDS`, both sized
   from real, measured latencies) — chosen to sit comfortably below this project's own known
   100+ second real generation times, but not tuned against a real distribution of many requests.
4. **Logging every dependency's own log output as JSON (the httpx finding) was not a deliberate
   design choice** — it's a side effect of attaching the formatter at the root logger level, judged
   acceptable after the fact rather than evaluated as its own decision beforehand.

## How this connects
**Previous:** P7-M3 built the error-handling machinery this step's middleware had to work
*around* (the `ServerErrorMiddleware` split) and *build on* (the logger calls it upgrades for
free). **This step:** the whole app now reports its own real behavior — every request, every
latency, every error — in one consistent, machine-parseable format. **Next (not yet specified):**
`/ingest`/`/query` (still flagged as coming) inherit this logging automatically, since
`RequestLoggingMiddleware` applies at the ASGI level, not per-route — no new logging code needed
for either.

## How to do this yourself
1. Before choosing a framework's own middleware mechanism for anything that needs to observe a
   response's real completion (timing, logging, auditing), check — by measuring, not assuming —
   whether that mechanism actually waits for the response to finish, especially for a streaming one.
2. When a new piece of infrastructure (middleware, a handler, a wrapper) needs to observe
   something a PREVIOUS step already proved has multiple distinct code paths, explicitly check
   each path individually rather than assuming one code path's behavior generalizes to all of them.
3. A "minimal reproduction" (a tiny throwaway app, not the real one) is often the fastest way to
   verify a framework mechanic's real behavior before committing it to real, harder-to-experiment-on
   production code — used three times in this one step alone.

## Practice exercises
1. Remove `RequestLoggingMiddleware`'s own `try`/`except` entirely and re-run
   `tests/test_logging.py::test_the_bare_exception_path_still_gets_logged_via_the_middlewares_own_try_except`.
   Confirm it fails, and explain exactly why in terms of P7-M3's own middleware-split finding.
2. Add a request ID (a `uuid4()` per request, attached to `scope["state"]` or a contextvar, included
   in the log's `extra={}` and echoed as an `X-Request-ID` response header). What's the smallest
   change that adds this without needing a second log line?
3. Lower `SLOW_REQUEST_MS` temporarily and hit a real, live `/chat` request against a real
   (currently-loaded) Ollama model. Confirm the WARNING-level line appears, and read its real
   `duration_ms` against this project's own previously-measured 108.3s real generation time.
4. Add a small counter (a plain module-level `int`, not a real metrics library) that
   `RequestLoggingMiddleware` increments per request, and expose it via a new, tiny
   `GET /_internal/request-count` route. How far is this from "real" observability, and what
   would break first if this app needed to run as more than one process?
5. Research `contextvars` (the stdlib module) as the standard way production logging systems
   propagate a request ID into log calls made deep inside business logic, far from any middleware.
   Would this project's own `RAGPipeline`/`LocalLLM` classes need to change at all to support it?

## Sources
- [Python docs: LogRecord attributes](https://docs.python.org/3/library/logging.html#logrecord-attributes) —
  the exact field names `_JsonFormatter` reads/merges.
- [Python docs: `logging.basicConfig`](https://docs.python.org/3/library/logging.html#logging.basicConfig)
- [ASGI spec: HTTP connection scope and messages](https://asgi.readthedocs.io/en/latest/specs/www.html) —
  the real `http.response.start`/`http.response.body` message shapes `RequestLoggingMiddleware`'s
  `wrapped_send` inspects directly.
- `starlette/middleware/errors.py` (`ServerErrorMiddleware`) — read directly from the installed
  package (see [docs/36](<../docs/36 error_handling_deep_dive.md>) C2 for the full finding this
  step's D30 builds on); `www.starlette.io`'s own docs site remains unreachable from this
  environment (confirmed again this step).
