# 37 — Logging & Observability Deep Dive (P7-M4)

> Deconstructs P7-M4's additions to [`src/api/main.py`](../src/api/main.py) — `_JsonFormatter`,
> the `logging.basicConfig` setup, and `RequestLoggingMiddleware`. This deep dive does NOT
> re-cover ground [docs/34](<34 fastapi_skeleton_deep_dive.md>), [docs/35](<35 chat_streaming_deep_dive.md>),
> or [docs/36](<36 error_handling_deep_dive.md>) already built (the ASGI app object, `lifespan`,
> SSE framing, sync-generator-in-threadpool safety, exception handler MRO dispatch, or the
> `ServerErrorMiddleware`/`ExceptionMiddleware` split itself) — it covers what's genuinely new:
> how Python's own `logging.Formatter`/`LogRecord` actually work, why FastAPI's most common
> middleware decorator cannot correctly time a streaming response, what a raw ASGI middleware's
> `scope`/`receive`/`send` triad actually looks like on the wire, how a middleware's POSITION in
> the stack (built directly on docs/36's own finding) forces a specific code-level design choice,
> and how one root-level logging config reaches every dependency in the process. Demo scripts live
> in [`practice codes/phase 7/m4_logging_observability_deep_dive/`](<../practice codes/phase 7/m4_logging_observability_deep_dive/>)
> and need no Docker or real server. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 7/m4_logging_observability_deep_dive/d1_basehttpmiddleware_streaming_blindspot.py"
> ```

---

## 0. Plain-language summary

```
_JsonFormatter:            turns a LogRecord (a real Python object) into one JSON line
logging.basicConfig(...):  attaches that formatter to the ROOT logger, once, at import time
RequestLoggingMiddleware:  a RAW ASGI middleware -- not @app.middleware("http") -- that wraps
                            send() directly to measure a request's REAL, full duration and log
                            one structured line per request, including both error paths
```

Five concepts, in the order this step actually needed them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | `logging.Formatter` / `LogRecord` internals | 🟡 useful | `_JsonFormatter` |
| C2 | Why `@app.middleware("http")` can't time a stream | 🔴 very high | why `RequestLoggingMiddleware` exists at all |
| C3 | Raw ASGI middleware: `scope`/`receive`/`send` | 🔴 high | `RequestLoggingMiddleware.__call__` |
| C4 | Middleware position forces a design choice | 🔴 very high | the `try`/`except` in `__call__` |
| C5 | Root logger propagation | 🟡 useful | why `logging.basicConfig` affects `httpx` too |

---

## C1 — `logging.Formatter` / `LogRecord` internals

### Do this first
Run [`d0_logrecord_and_formatter.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d0_logrecord_and_formatter.py>).
**Predict first:** a log call passes `extra={"method": "GET", "duration_ms": 12.3}`. Do you expect
these to end up somewhere special (a nested dict, a separate parameter FastAPI-style), or as
perfectly ordinary attributes directly on the record object, indistinguishable from `levelname` or
`created`?

### 1. Definition + analogy
**What it is.** Every call like `logger.info("message", extra={...})` creates one real
`LogRecord` object — a plain Python object with real attributes (`name`, `levelname`, `created`,
`msg`, `args`, and so on). `extra={...}` doesn't do anything magical — it just calls `setattr` for
each key, adding them as MORE ordinary attributes on that same object. A `Formatter`'s job is to
take one `LogRecord` and produce a string; the default `Formatter` reads a handful of standard
attributes, but nothing stops a custom one from reading ALL of them, including whatever `extra`
added.

**Analogy.** A shipping label that starts with a few required fields printed by the warehouse's
own system (weight, destination, date) — but the warehouse lets a sender staple on extra tags of
their own (fragile, gift-wrapped, signature-required). The label itself doesn't care which tags
are "official" and which are "extra" — anyone reading the whole label just sees ALL the tags
stuck to it, official and extra together, as one combined object.

### For a 14-year-old
Imagine a school attendance sheet where the office prints some columns automatically for every
student (name, date, whether they were present) — those are the "built-in" columns, always there.
Now imagine a teacher is allowed to staple a sticky note onto any individual student's row with
whatever extra info matters that day — "brought permission slip," "left early." The sticky note
isn't stored somewhere separate and hidden; it's physically attached to THAT student's row,
alongside the built-in columns, and anyone looking at that row sees both the printed columns AND
the sticky note together.

A `LogRecord` is exactly that row. The built-in columns (`levelname`, `created`, `name`, ...) get
filled in automatically every single time. `extra={"method": "GET", "duration_ms": 12.3}` is the
sticky note — `method` and `duration_ms` get stapled directly onto that ONE record as real
attributes, sitting right alongside the built-in ones with no special marking distinguishing them.
`_JsonFormatter`'s whole job is to read the row, print every printed column AND every sticky note
into one clean, combined line — and the only way to know which columns were "printed" versus
"stapled on" is to compare against a BLANK row that has no sticky notes at all.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the attendance sheet's own printed columns | a `LogRecord`'s built-in attributes (`levelname`, `created`, ...) |
| a teacher's sticky note, stapled onto one row | `extra={...}` fields, set directly via `setattr` |
| the row itself, columns and notes all together | `vars(record)` — one dict with everything |
| comparing against a blank row to spot the sticky notes | `_LOG_RECORD_RESERVED_FIELDS`, computed from a fresh, empty `LogRecord` |
| reading the whole row into one clean printout | `_JsonFormatter.format`, building one `dict`, then `json.dumps` |

### 2. Why here specifically
`RequestLoggingMiddleware` needs to attach request-specific data (`method`, `path`, `status_code`,
`duration_ms`) to EVERY log line it emits, while P7-M3's OWN pre-existing calls
(`logger.warning("upstream timeout on %s: %s", ...)`) attach nothing extra at all. A single
formatter that handles BOTH — reading whatever extra fields happen to exist, none or several —
is what lets one formatter serve every log call in the app uniformly, without each call site
needing to know or care about the output format.

### What breaks if this changes
- **Hard-code the expected extra fields into the formatter** (e.g., always look for `.method`):
  would crash (`AttributeError`) on P7-M3's own existing log calls, which never set `.method` at
  all — the whole point of computing `_LOG_RECORD_RESERVED_FIELDS` dynamically and looping over
  `vars(record)` is to handle ANY set of extra fields, including none.
- **Hand-copy the reserved-field list from documentation instead of computing it from a real
  instance:** Python's own `LogRecord` gained a new field (`taskName`) in 3.12 — a hand-copied list
  written against an older version would silently treat `taskName` as an "extra" field and leak it
  into every single log line's JSON, even though it's actually a built-in one.
- **Forget the `record.exc_info` check:** `logger.exception(...)` calls (used by
  `_handle_unexpected_error`, P7-M3) would lose their real traceback entirely — `exc_info` is set
  but never READ unless the formatter explicitly checks for it and calls `self.formatException(...)`.

### [Interview relevance: useful — knowing that "structured logging" isn't a separate system bolted onto Python's own logging module, but a thin formatting layer over the SAME LogRecord objects the stdlib already creates, is the kind of foundational knowledge that makes a custom logging setup debuggable rather than mysterious]

**Interview question:** *"How would you add structured (JSON) logging to a Python application
that already uses the standard `logging` module?"*
**A strong answer covers:** writing a custom `logging.Formatter` subclass whose `format()` method
reads the real `LogRecord` object's attributes (built-in ones like `levelname`/`created`, plus any
ad-hoc fields attached via a call's own `extra={}`) and serializes them as JSON instead of the
default text template; recognizing that `extra={}` doesn't create a nested structure, it sets
plain attributes directly on the record, so the formatter needs its own way to distinguish
"built-in" from "extra" fields (comparing against a fresh, unmodified `LogRecord`'s own attribute
set is a reliable way to do this without hard-coding a list that could drift from the installed
Python version); and that this approach requires zero changes to EXISTING log calls anywhere in
the codebase — only the formatter attached to the handler changes.

### 3. Minimal code that explains the concept
The complete script, [`d0_logrecord_and_formatter.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d0_logrecord_and_formatter.py>) (run from the project root):
```python
# Minimal demo of C1: what a LogRecord actually contains, how extra={} attaches new fields to
# one, and how a custom Formatter turns it into real JSON -- built up step by step, the exact
# pieces _JsonFormatter (src/api/main.py) is made of.
import json
import logging

# Step 1: a LogRecord is a real, ordinary Python object with real attributes -- not magic.
record = logging.LogRecord(
    name="demo", level=logging.INFO, pathname=__file__, lineno=10,
    msg="hello %s", args=("world",), exc_info=None,
)
print("record.getMessage() does the %-interpolation for you:", record.getMessage())

# Step 2: extra={...} on a real logging call just SETS plain attributes on the record object.
logger = logging.getLogger("demo")
handler = logging.Handler()
captured = []
handler.emit = lambda r: captured.append(r)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.info("a request happened", extra={"method": "GET", "duration_ms": 12.3})
real_record = captured[0]
print("real_record.method =", real_record.method, " real_record.duration_ms =", real_record.duration_ms)

# Step 3: telling "extra" fields apart from the record's OWN built-in ones -- computed from a
# real instance, not hand-copied from documentation (exactly what src/api/main.py does).
reserved = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}
extra_only = {k: v for k, v in vars(real_record).items() if k not in reserved}
print("fields NOT in a fresh LogRecord's own reserved set:", extra_only)

# Step 4: a minimal version of _JsonFormatter, built from exactly the pieces above.
def to_json(record: logging.LogRecord) -> str:
    payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
    for key, value in vars(record).items():
        if key not in reserved:
            payload[key] = value
    return json.dumps(payload)

print("final JSON line:", to_json(real_record))
```
Real output:
```
record.getMessage() does the %-interpolation for you: hello world
a few of its own built-in attributes:
  name = 'demo'
  levelname = 'INFO'
  created = 1790793503.3088913
  msecs = 308.0
  process = 16352
  thread = 6096

after extra={'method': 'GET', 'duration_ms': 12.3}:
  real_record.method       = GET
  real_record.duration_ms  = 12.3
  -> extra fields are just ordinary attributes, set directly on the record object

fields NOT in a fresh LogRecord's own reserved set: {'method': 'GET', 'duration_ms': 12.3}

final JSON line: {"level": "INFO", "logger": "demo", "message": "a request happened", "method": "GET", "duration_ms": 12.3}
```
`extra_only` correctly contains EXACTLY the two fields `extra={}` added — nothing more, nothing
less — proving the reserved-field comparison technique works precisely.

### The exact code I wrote (`src/api/main.py`)
> **Lines 30-36:**
> ```python
> _LOG_RECORD_RESERVED_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
>     "message",
>     "asctime",
> }
> ```
> **Lines 39-57:**
> ```python
> class _JsonFormatter(logging.Formatter):
>     def format(self, record: logging.LogRecord) -> str:
>         payload = {
>             "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
>             "level": record.levelname,
>             "logger": record.name,
>             "message": record.getMessage(),
>         }
>         for key, value in vars(record).items():
>             if key not in _LOG_RECORD_RESERVED_FIELDS:
>                 payload[key] = value
>         if record.exc_info:
>             payload["exception"] = self.formatException(record.exc_info)
>         return json.dumps(payload, default=str)
> ```

**🎯 Interview-relevant lines:** `"message"` and `"asctime"` are added to the reserved set
EXPLICITLY (line 32), even though a fresh `LogRecord` doesn't actually have either as a real
attribute yet — `message`/`asctime` are only populated by `Formatter.format()` itself (the base
class's own `format()` calls `record.getMessage()` and, if needed, `self.formatTime()`, then
CACHES the results back onto the record as `record.message`/`record.asctime`). Since
`_JsonFormatter.format` already reads `record.getMessage()` directly into `payload["message"]`,
these two names need to be excluded manually so they're never accidentally double-counted as
"extra" fields if the base class (or a future code path) ever sets them first.

### Canonical docs
- [Python docs: LogRecord attributes](https://docs.python.org/3/library/logging.html#logrecord-attributes)
- [Python docs: `logging.basicConfig`](https://docs.python.org/3/library/logging.html#logging.basicConfig)

### Micro-exercises
1. In `d0`, print `vars(logging.LogRecord("", 0, "", 0, "", (), None))` directly — the FULL
   reserved set this project's own code computes. How many fields does it contain on your
   installed Python version? (Hint: this can genuinely differ between Python versions.)
2. Call `logger.info("count is %d", 5)` (positional `%`-style args, no `extra`) and inspect
   `record.args` and `record.getMessage()` separately. What's stored in `.msg` versus what
   `.getMessage()` actually returns?
3. Try `extra={"levelname": "HACKED"}` — a key that collides with a REAL built-in `LogRecord`
   attribute. What happens? (Hint: read Python's own `Logger.makeRecord` source, or just try it
   and explain the resulting error.)
4. Add a non-JSON-serializable value to `extra` (e.g. `extra={"conn": object()}`) and confirm
   `json.dumps(..., default=str)` handles it gracefully rather than raising. What would happen
   WITHOUT `default=str`?
5. Write a formatter that ALSO includes `record.filename` and `record.lineno` (both real built-in
   fields) in the JSON output, without changing the reserved-field logic at all. Why doesn't
   adding them require touching `_LOG_RECORD_RESERVED_FIELDS`?

**How it works internally.** `Logger.makeRecord` (called internally by `.info()`/`.warning()`/etc.)
constructs the `LogRecord`, then — for every key in `extra` — calls `setattr(rv, key, extra[key])`,
raising a `KeyError` if the key collides with one of the record's own reserved attribute names
(this is Python's own built-in collision guard, exercised in micro-exercise 3). `Formatter.format()`
is just a method that receives one such object and returns a string — nothing about "structured
logging" requires a different architecture from this.

### How to validate this yourself
[`tests/test_logging.py`](../tests/test_logging.py)'s own
`test_the_json_formatter_produces_valid_parseable_json_with_the_expected_fields` builds a real
`LogRecord`, sets extra fields directly, and asserts every one survives into real,
`json.loads`-parseable output — the strongest proof the formatter handles the general case, not
just what a live request happens to produce.

### How to visualize this
Draw a `LogRecord` as a table row with several pre-filled cells (labelled `levelname`, `created`,
...). Draw `extra={...}` as new cells appended to the SAME row, in the same visual style as the
pre-filled ones — nothing marks them as different. Draw the formatter as a function that reads
the WHOLE row and writes out a new, JSON-shaped row underneath.

**You should now be able to explain:** why a custom JSON formatter needs no cooperation from
individual log call sites beyond an optional `extra={}`, and why computing the reserved-field set
from a real object is more robust than hard-coding a list.

---

## C2 — Why `@app.middleware("http")` can't time a stream

### Do this first
Run [`d1_basehttpmiddleware_streaming_blindspot.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d1_basehttpmiddleware_streaming_blindspot.py>).
**Predict first:** the SAME slow (~0.9s) streaming route is timed two ways on two otherwise
identical apps — once via `@app.middleware("http")`'s `call_next`, once via a raw ASGI middleware
watching for the real final response message. Do you expect both timings to be close to the real
~0.9s, or do you expect one of them to report something much smaller?

### 1. Definition + analogy
**What it is.** `@app.middleware("http")` (built on Starlette's `BaseHTTPMiddleware`) offers a
simple-looking contract: `response = await call_next(request)`, do something with `response`,
return it. But `call_next` doesn't actually wait for the downstream route to FINISH — it runs the
route in a background task and returns as soon as the FIRST response message
(`http.response.start`) arrives, wrapping everything AFTER that in a lazy, not-yet-consumed
generator. For an ordinary JSON response, the first message and the whole response are produced at
basically the same instant, so this distinction is invisible. For a STREAMING response, the first
message can arrive almost instantly while the real body takes seconds or minutes — and
`call_next` returns at the FIRST moment, not the last.

**Analogy.** A relay race baton handoff, timed by a watch that starts the moment the SECOND
runner's hand first closes around the baton — not when that runner actually crosses the finish
line. If the second leg is instant, this timing mistake never shows up. If the second leg is a
whole separate marathon, the watch would report a nearly-zero time for a leg that actually took
hours — because it was only ever measuring the HANDOFF, not the run.

### For a 14-year-old
Imagine you're timing how long it takes a friend to make and eat a whole pizza, but your stopwatch
has a weird rule: it stops the INSTANT the oven timer dings and the pizza is pulled out — not when
your friend actually finishes eating it. If your friend eats fast, this barely matters — the two
moments are close together anyway. But if your friend takes twenty minutes to slowly eat that
pizza, your stopwatch would report "it took 2 minutes!" — technically true for "time until the
pizza EXISTED," completely wrong for "time until the whole thing was actually done."

`@app.middleware("http")`'s `call_next` is that weird stopwatch. For `/health` (a fast, ordinary
JSON response), the "pizza exists" moment and the "pizza is finished" moment are practically the
same instant, so nobody would ever notice the difference. But for `/chat` — which can take real
minutes to fully stream — `call_next` reports "done!" the moment the STREAMING RESPONSE OBJECT
exists, long before the real answer has actually finished generating and streaming out.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the oven timer dinging, pizza pulled out | `http.response.start`, the FIRST ASGI message |
| your friend still slowly eating the pizza | the real streamed body, still being generated/sent |
| the stopwatch stopping at the wrong moment | `call_next` RETURNING once that first message arrives |
| "it took 2 minutes!" (wrong) | `d1`'s own measured `0.005s` for a real ~0.9s request |
| actually watching your friend finish the whole pizza | a raw ASGI middleware watching for the REAL final message |

### 2. Why here specifically
`/chat`'s entire purpose (P7-M2) is to stream a real, slow-to-generate answer — its latency is
this project's own most extensively measured and most concerning property (108-300+s real
generation times, a 37.5% real timeout rate). A logging design that can't measure THIS endpoint's
real latency correctly would be actively worse than no latency logging at all — it would
confidently report a wrong, reassuring number for the one case that most needs an honest one.

### What breaks if this changes
- **Use `@app.middleware("http")` anyway, not realizing the gap:** every `/chat` request would log
  a near-zero `duration_ms` regardless of whether it took 1 second or 300 — this project's own
  headline latency concern would become invisible in its OWN logs, the opposite of observability.
- **Assume the gap only matters for genuinely slow requests:** it applies to EVERY streaming
  response, including a fast one — `d1`'s own `/stream` route isn't `/chat` specifically; the
  mistiming is a property of `StreamingResponse` + `BaseHTTPMiddleware` generally, not something
  tied to how slow any one particular request happens to be.
- **Fix it by adding `await response.body_iterator.__anext__()`-style manual draining inside the
  middleware after `call_next`:** would work but fights the framework's own abstraction in a
  fragile, undocumented way — the raw ASGI approach (C3) is the actual, supported mechanism for
  observing a response's real completion.

### [Interview relevance: very high — this is a real, well-known gotcha with `BaseHTTPMiddleware` and streaming responses that has caused real production incidents (silently-wrong latency metrics) at companies using FastAPI for exactly this kind of LLM-streaming API; knowing to verify a middleware's real timing behavior before trusting it is a direct, practical skill]

**Interview question:** *"You added logging middleware to measure API latency, and it's reporting
suspiciously low numbers for your streaming endpoints specifically. What would you check?"*
**A strong answer covers:** whether the middleware is built on `@app.middleware("http")`
(`BaseHTTPMiddleware`), whose `call_next` is known to return as soon as a response object's FIRST
message is available — correct for ordinary responses, but returning long before a
`StreamingResponse`'s real body has finished for a streaming one; verifying this directly (timing
a deliberately slow streaming route) rather than assuming the middleware "must" be measuring full
duration just because it looks like it wraps the whole request; and the fix — a raw ASGI
middleware wrapping `send` directly, watching for the actual final response message, which
observes the true end-to-end duration regardless of response type.

### 3. Minimal code that explains the concept
The complete script, [`d1_basehttpmiddleware_streaming_blindspot.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d1_basehttpmiddleware_streaming_blindspot.py>) (run from the project root):
```python
# Minimal demo of C2: FastAPI's @app.middleware("http") (BaseHTTPMiddleware) returns from
# call_next as soon as a streaming response's FIRST message arrives, not once its real body
# finishes -- proven side by side against a raw ASGI middleware on the SAME slow route.
import time
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

def slow_stream():
    for i in range(3):
        time.sleep(0.3)
        yield f"chunk-{i}\n"

app1 = FastAPI()
base_http_elapsed = []

@app1.middleware("http")
async def base_http_timing(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    base_http_elapsed.append(time.perf_counter() - start)
    return response

@app1.get("/stream")
def stream1():
    return StreamingResponse(slow_stream())

app2 = FastAPI()
raw_asgi_elapsed = []

class RawTimingMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        async def wrapped_send(message):
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                raw_asgi_elapsed.append(time.perf_counter() - start)
            await send(message)
        await self.app(scope, receive, wrapped_send)

app2.add_middleware(RawTimingMiddleware)

@app2.get("/stream")
def stream2():
    return StreamingResponse(slow_stream())

client1 = TestClient(app1)
client1.get("/stream")
client2 = TestClient(app2)
client2.get("/stream")

print(f"@app.middleware('http')  -- call_next's own elapsed time: {base_http_elapsed[0]:.3f}s")
print(f"raw ASGI middleware      -- send-to-final-chunk elapsed:   {raw_asgi_elapsed[0]:.3f}s")
```
Real output:
```
real total request time (both apps, same route): app1=0.929s  app2=0.916s

@app.middleware('http')  -- call_next's own elapsed time: 0.005s
raw ASGI middleware      -- send-to-final-chunk elapsed:   0.909s

-> call_next returned almost immediately: it saw the response OBJECT exist, not finish.
-> the raw ASGI middleware's own timer only stopped once the real, full body was sent.
```
Both apps take the SAME real ~0.92s to actually serve the request (confirmed by `overall1`/`overall2`)
— the difference is entirely in what each middleware technique OBSERVES about that same real time.

### The exact code I wrote (`src/api/main.py`)
> **Lines 76-80 (the design reasoning, stated directly in the docstring):**
> ```python
> class RequestLoggingMiddleware:
>     """Raw ASGI middleware, not FastAPI's own @app.middleware("http"): verified directly that
>     the latter's call_next returns almost immediately for a StreamingResponse (long before a
>     real stream finishes), which would log /chat's own latency as near-instant. Wrapping
>     `send` directly and watching for the real final response message measures the REAL, full
>     duration instead."""
> ```

**🎯 Interview-relevant lines:** the docstring states the REASON for the design choice directly,
including the specific measured evidence — a good habit reinforced from
[docs/35](<35 chat_streaming_deep_dive.md>) and [docs/36](<36 error_handling_deep_dive.md>): when a
decision exists specifically BECAUSE of a verified, non-obvious finding, write the finding down
right there, not just the choice.

### Canonical docs
- `starlette/middleware/base.py` (`BaseHTTPMiddleware.call_next`, specifically lines 112-187 of the
  installed package) — read directly: it runs the downstream app in a background task
  (`task_group.start_soon(coro)`), reads only the FIRST message from an internal stream
  (`message = await recv_stream.receive()`), and immediately constructs and returns a
  `_StreamingResponse` wrapping the REST of the body in a lazy `body_stream()` generator that is
  only consumed later. `www.starlette.io`'s own docs site remains unreachable from this
  environment (confirmed again this step, matching [docs/36](<36 error_handling_deep_dive.md>)'s
  own note).

### Micro-exercises
1. In `d1`, change `slow_stream` to yield its first chunk IMMEDIATELY (no `time.sleep` before the
   first `yield`) but still sleep between later chunks. Does `call_next`'s own elapsed time change
   at all? What does this confirm about WHICH message `call_next` actually waits for?
2. Replace the streaming route in `app1` with a NON-streaming one that takes the same total real
   time (e.g., `time.sleep(0.9)` then `return {"ok": True}`). Does `call_next`'s elapsed time now
   match the real total time? Why does the gap only appear for `StreamingResponse` specifically?
3. Read `starlette/middleware/base.py`'s own `body_stream()` function (quoted in this section's own
   citation). What ASGI message type does it check for as its own stopping condition? Compare it to
   `RequestLoggingMiddleware`'s own `wrapped_send` check.
4. Time `BaseHTTPMiddleware` against `/chat` in THIS project's own real app (temporarily, in a
   scratch copy) and confirm it shows the same near-zero pattern for a real, slow generation —
   connecting this toy demo directly to the real, shipped code.
5. Research whether `BaseHTTPMiddleware`'s behavior here is considered a bug or a documented
   design trade-off by Starlette's own maintainers (check its GitHub issues/changelog). Has this
   specific streaming-timing gotcha been discussed there?

**How it works internally.** `call_next` creates an `anyio` memory-object-stream pair, starts the
real downstream ASGI app in a background task writing into one end of it, and reads from the other
end just long enough to get the first message and assemble a `Response` object around a lazy
generator for everything after. The REST of the real work continues running in that background
task regardless of whether anything ever actually iterates the generator `call_next` returned —
which is exactly why a plain `await call_next(...)` followed by immediate timing code sees almost
no elapsed time.

### How to validate this yourself
`d1`'s own dual-app, side-by-side comparison against the IDENTICAL route is the validation — both
apps serve the same real request in the same real ~0.92s; only the OBSERVED timing differs,
isolating the discrepancy to the measurement technique itself, not anything about the route.

### How to visualize this
Draw one long horizontal bar representing the real, full request duration (~0.92s). Mark a tick
very near the LEFT edge, labelled "first message arrives — `call_next` returns here." Mark a
second tick at the RIGHT edge, labelled "real final chunk sent — the raw ASGI middleware's timer
stops here." The gap between these two ticks, visually almost the entire bar, IS the measurement
error `@app.middleware("http")` would introduce for a streaming response.

**You should now be able to explain:** precisely why FastAPI's most commonly-tutorialized
middleware pattern silently fails to correctly time a streaming endpoint, and what to check before
trusting ANY middleware's own timing of a response type it wasn't specifically verified against.

---

## C3 — Raw ASGI middleware: `scope`/`receive`/`send`

### Do this first
Run [`d2_raw_asgi_middleware_mechanics.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d2_raw_asgi_middleware_mechanics.py>).
**Predict first:** a streaming route yields three plain string chunks (`"a"`, `"b"`, `"c"`). Do
you expect `send()` to be called once per chunk, or once total with all three chunks already
combined into a single message?

### 1. Definition + analogy
**What it is.** Underneath every FastAPI route, decorator, and response class is ASGI (Asynchronous
Server Gateway Interface) — a simple, real, inspectable protocol: an application is just an
`async def __call__(scope, receive, send)` callable. `scope` is a plain dict describing the
connection (method, path, headers...). `send` is a function you call, potentially many times, to
actually transmit the response — first an `http.response.start` message (status + headers), then
one or more `http.response.body` messages (each with a `more_body` flag telling the receiver
whether more is coming). A raw ASGI middleware is just something that sits between the real server
and the real app, forwarding these calls through — optionally watching or modifying them along the
way.

**Analogy.** A conveyor belt at a package sorting facility, with a worker standing at one specific
point along it. Every package that passes gets physically handled — the worker can read its label,
note it down, even relabel it — but the worker's job is to always place it back on the belt and
let it continue. The "protocol" here isn't abstract: it's the literal, physical stream of packages
passing one specific point, each one a real, inspectable object.

### For a 14-year-old
Imagine a mail sorting room where letters and packages come down a chute one at a time. Each item
that comes down is a real, physical thing you could pick up and look at — not some invisible
signal. Now imagine you're asked to stand partway down the chute with one simple job: every item
that reaches you, you note down what it is (a letter? a package? how big?) in a little notebook,
and then you ALWAYS let it continue down the chute to wherever it was already going. You never
stop reading, but you also never actually change where anything ends up — you're just observing
the real, physical stream as it passes your one spot.

`scope` is like a label attached to the WHOLE delivery before anything starts moving — "this
delivery is a GET request for `/stream`." `send` is the chute itself — calling it is the literal
act of putting one more real item onto the belt. `RequestLoggingMiddleware` is the worker: for
every message `send` would normally deliver, it peeks at it (checking `message["type"]`,
noting the status or whether this is the LAST item), and then always calls the REAL `send`
afterward, so nothing is ever actually blocked or lost.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the label on the whole delivery | `scope`, a dict describing the request |
| the mail chute itself | `send`, the function that actually transmits each message |
| one item passing the worker's spot | one call to `send(message)` |
| noting "this is the LAST item" | checking `message.get("more_body", False)` for the final chunk |
| always putting the item back on the belt | `await send(message)`, called unconditionally inside `wrapped_send` |
| a worker who reads EVERY item, not just the first one | the middleware seeing every `http.response.body` message, one per streamed chunk |

### 2. Why here specifically
C2 already proved that "wrap the high-level `call_next` result" doesn't work for streaming.
Dropping down to the actual ASGI messages — the real, literal things being sent over the wire — is
the only way to observe EVERY message, including the true last one, regardless of what response
type produced them (`JSONResponse`, `StreamingResponse`, or anything else). This is also exactly
the SAME mechanism `ServerErrorMiddleware` itself uses internally (its own `response_started`
tracking, read directly in [docs/36](<36 error_handling_deep_dive.md>) C2) — not a special trick
invented for this project, but the standard, general-purpose tool for this whole class of problem.

### What breaks if this changes
- **Check `message["type"] == "http.response.start"` to end timing (instead of the LAST
  `http.response.body`):** would reintroduce exactly C2's own bug at the raw-ASGI level — timing
  would stop at the first message again, defeating the entire point of dropping to this lower
  level in the first place.
- **Forget `more_body`, treating every `http.response.body` message as the last one:** would log a
  request as "finished" after its FIRST streamed chunk, not its last — `d2`'s own real output shows
  three separate `http.response.body` messages for a 3-chunk stream, each needing its own check.
- **Forget to `await send(message)` inside the wrapper:** the real message would never actually
  reach the client at all — `wrapped_send`'s entire contract is "observe, then ALWAYS forward,"
  and skipping the forward silently breaks every single response the middleware touches.

### [Interview relevance: high — understanding that a web framework's high-level abstractions (routes, responses, dependency injection) sit on top of a real, simple, inspectable low-level protocol is valuable for debugging anything the high-level abstraction doesn't cleanly support, and ASGI specifically is the shared foundation under FastAPI, Starlette, and Django Channels]

**Interview question:** *"How would you build middleware that needs to observe a response's real,
final outcome — after streaming, after any exception handling — regardless of what kind of
response a route returns?"*
**A strong answer covers:** dropping below the framework's high-level middleware abstractions to
the raw ASGI protocol itself — `async def __call__(self, scope, receive, send)` — and wrapping the
`send` callable directly, since every response of every kind (JSON, streaming, file) ultimately
gets transmitted as a sequence of real ASGI messages (`http.response.start`, one or more
`http.response.body`) passing through that one function; checking `message["type"]` and
`message.get("more_body")` to detect the real final message rather than the first one; and
recognizing this is the SAME general mechanism the framework's own internal middleware
(`ServerErrorMiddleware`) uses, not a hack specific to one use case.

### 3. Minimal code that explains the concept
The complete script, [`d2_raw_asgi_middleware_mechanics.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d2_raw_asgi_middleware_mechanics.py>) (run from the project root):
```python
# Minimal demo of C3: what `scope`, `receive`, and `send` actually ARE -- the real ASGI message
# dicts a plain FastAPI route produces, made visible by printing every one that passes through
# a middleware's own wrapped `send`. No FastAPI magic hidden here: this IS the wire protocol.
from fastapi import FastAPI
from fastapi.testclient import TestClient

class PrintEverythingMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        print("scope (trimmed):", {k: scope[k] for k in ("type", "method", "path")})
        async def logging_send(message):
            print("send() was called with:", message)
            await send(message)  # ALWAYS forward -- this is what makes it a passthrough
        await self.app(scope, receive, logging_send)

app = FastAPI()
app.add_middleware(PrintEverythingMiddleware)

@app.get("/ping")
def ping():
    return {"ok": True}

def three_chunks():
    yield "a"; yield "b"; yield "c"

from fastapi.responses import StreamingResponse

@app.get("/stream")
def stream():
    return StreamingResponse(three_chunks())

client = TestClient(app)
client.get("/ping")
client.get("/stream")
```
Real output:
```
=== a plain JSON route ===
scope (trimmed to the interesting keys):
  'type': 'http'
  'method': 'GET'
  'path': '/ping'
  'http_version': '1.1'
send() was called with: {'type': 'http.response.start', 'status': 200, 'headers': [(b'content-length', b'11'), (b'content-type', b'application/json')]}
send() was called with: {'type': 'http.response.body', 'body': b'{"ok":true}'}

=== a streaming route, to see MULTIPLE http.response.body messages ===
scope (trimmed to the interesting keys):
  'type': 'http'
  'method': 'GET'
  'path': '/stream'
  'http_version': '1.1'
send() was called with: {'type': 'http.response.start', 'status': 200, 'headers': []}
send() was called with: {'type': 'http.response.body', 'body': b'a', 'more_body': True}
send() was called with: {'type': 'http.response.body', 'body': b'b', 'more_body': True}
send() was called with: {'type': 'http.response.body', 'body': b'c', 'more_body': True}
send() was called with: {'type': 'http.response.body', 'body': b'', 'more_body': False}
```
The plain JSON route sends exactly TWO messages total (start, one body, no `more_body` key at all
— implicitly final). The streaming route sends ONE `http.response.body` per real yielded chunk,
each explicitly `more_body: True`, plus one final, empty-bodied message with `more_body: False` —
this EXACT last message is what `RequestLoggingMiddleware`'s own `wrapped_send` (and `d1`'s
`RawTimingMiddleware`) checks for.

### The exact code I wrote (`src/api/main.py`)
> **Lines 100-104:**
> ```python
> async def wrapped_send(message: dict) -> None:
>     if message["type"] == "http.response.start":
>         status_holder["status"] = message["status"]
>         # captures the REAL status a specific handler (400/422/504) chose
>     await send(message)
> ```

**🎯 Interview-relevant lines:** notice this particular wrapper checks for `http.response.start`
(to capture the status), NOT the final `http.response.body` — unlike `d1`'s/`d2`'s own timing
demos. `RequestLoggingMiddleware` doesn't need to detect the LAST message explicitly at all: it
measures duration in its OUTER `try`/`except`/`else` block (C4), which naturally only runs once
`await self._app(...)` has fully returned — meaning the ENTIRE app, including every streamed
chunk, has already finished. The status capture and the duration measurement use two different,
complementary techniques for two different needs.

### Canonical docs
- [ASGI spec: HTTP connection scope and messages](https://asgi.readthedocs.io/en/latest/specs/www.html) —
  the authoritative definition of `scope`, `http.response.start`, and `http.response.body`.

### Micro-exercises
1. In `d2`, print `len(message.get("body", b""))` for every `http.response.body` message on the
   streaming route. Confirm it matches the real byte length of each yielded chunk (`"a"`, `"b"`,
   `"c"`, then `0` for the final empty one).
2. Modify `PrintEverythingMiddleware` to also print every message `receive()` would produce for a
   POST request with a real JSON body (hint: you'll need to wrap `receive` the same way `send` is
   wrapped, and call it yourself to see what it returns).
3. Add a FOURTH route that raises an unhandled exception with NO registered handler for it at all.
   What does `PrintEverythingMiddleware` print for this case — any `send()` calls at all, or none?
   (Hint: this previews C4.)
4. Research the ASGI `lifespan` protocol's own message types (`lifespan.startup`,
   `lifespan.startup.complete`) — the SAME `scope["type"] != "http"` check `RequestLoggingMiddleware`
   uses to skip non-HTTP scopes is specifically guarding against these.
5. Time how many TOTAL `send()` calls a real `/chat` request makes in this project's own app
   (temporarily add a print to `wrapped_send`) for a short, multi-fragment real answer. Does the
   count roughly match the number of real fragments `RAGPipeline.stream()` yields?

**How it works internally.** ASGI is genuinely just a calling CONVENTION, not a library or a
protocol requiring special tooling to inspect — `scope`, `receive`, and `send` are plain Python
objects (a dict and two async callables) that any `async def __call__` can accept and use directly,
which is exactly why `print()`ing them works with zero special setup.

### How to validate this yourself
`d2`'s own real, printed message sequence for the streaming route — three distinct
`http.response.body` messages, matching the three real `yield` statements in `three_chunks()` —
is direct, unambiguous proof that ASGI transmits a stream as a real SEQUENCE of discrete messages,
not one buffered blob.

### How to visualize this
Draw a horizontal conveyor belt. Place a few real, labeled boxes on it moving left to right:
one box labeled `http.response.start`, then one box per streamed chunk labeled
`http.response.body (more_body=True)`, then a final box labeled
`http.response.body (more_body=False)`. Draw a single worker standing at a fixed point along the
belt, with an arrow looping from "sees box" to "notes it down" to "puts it back on the belt" for
EVERY box that passes, without exception.

**You should now be able to explain:** what `scope`/`receive`/`send` concretely are (not as an
abstraction, but as real, printable Python values), and why wrapping `send` is the general-purpose
way to observe any response's real, complete lifecycle.

---

## C4 — Middleware position forces a design choice

### Do this first
Run [`d3_middleware_position_and_the_try_except.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d3_middleware_position_and_the_try_except.py>).
**Predict first:** the SAME middleware logic runs twice — once WITHOUT a `try`/`except` around its
own call into the app, once WITH one. Both versions are tested against two routes: one that raises
an exception with a SPECIFIC registered handler, one that raises with only the bare-`Exception`
catch-all. Do you expect BOTH versions to log both routes correctly, or do you expect one specific
combination to silently log nothing?

### 1. Definition + analogy
**What it is.** [docs/36](<36 error_handling_deep_dive.md>) C2 proved that a handler registered
for the literal `Exception` class is routed through `ServerErrorMiddleware` specifically — the
OUTERMOST layer in Starlette's whole middleware stack. Anything registered via
`app.add_middleware(...)` (like `RequestLoggingMiddleware`) sits INSIDE that outermost layer, but
OUTSIDE `ExceptionMiddleware` (the layer that handles every OTHER, specifically-registered
exception type). This position means: for a SPECIFIC handler's error, your middleware sees a
normal, successful completion (because `ExceptionMiddleware`, further in, already converted it to
a response). For the BARE-`Exception` case, your middleware sees the RAW EXCEPTION ITSELF
propagate through its own code — because `ServerErrorMiddleware`, further OUT, hasn't handled it
yet. The exact same middleware code needs to correctly handle both shapes.

**Analogy.** A security checkpoint partway down a hallway, between two different offices. Visitors
with a normal, pre-approved reason (a specific handler) get fully processed by the FIRST office
(further down the hall) and walk back past the checkpoint as ordinary, already-resolved visitors —
the checkpoint just waves them through normally. But a visitor with NO pre-approved reason at all
doesn't reach that first office — they get stopped and sent back EARLY, as an unresolved problem,
passing back through the checkpoint in a completely different state: as someone being escorted
out, not an ordinary, processed visitor. The checkpoint needs a plan for BOTH kinds of traffic,
because they look completely different by the time they reach it.

### For a 14-year-old
Imagine a school where a hall monitor stands at one spot, and there are two very different offices
further down the hallway. Office A handles COMMON, expected problems (a forgotten hall pass) —
instantly, quietly, and the student walks calmly back past the monitor afterward like nothing
happened. Office B (further away, past the monitor, the LAST resort) is for a real emergency — and
if a student has a real emergency, they don't calmly walk back past the monitor as a "resolved"
case; instead, someone comes RUNNING back past the monitor yelling "emergency!" before Office B
has even finished dealing with it, because word needs to spread immediately, not wait.

If the hall monitor's only plan is "write down every student who calmly walks past me," they'll
correctly log every Office-A case — but they'll COMPLETELY MISS every real emergency, because
those never calmly walk past; they come RUSHING past in a totally different way (shouting, not
walking) before the monitor's normal "calmly walked past" check ever triggers. The monitor needs a
SECOND, separate plan — watching for someone running past in a panic, not just for calm walkers —
to make sure emergencies get logged too.

`RequestLoggingMiddleware`'s `try`/`except` IS that second plan. The `else` branch (calm walking)
catches every request handled by a SPECIFIC handler. The `except Exception` branch (someone
running past) catches the bare-`Exception` case — which reaches the middleware as a RAISED
EXCEPTION, not a normal, finished response, precisely because `ServerErrorMiddleware` is still
FURTHER OUT, not yet having sent anything.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| a common problem, resolved by the nearer office | a SPECIFIC registered handler (`_handle_validation_error`, `_handle_timeout`) |
| calmly walking back past the monitor | a normal ASGI completion — `await self._app(...)` just returns |
| a true emergency, unresolved by the nearer office | the bare-`Exception` catch-all case |
| someone running past in a panic, before Office B finishes | the raw exception propagating THROUGH the middleware |
| "write down every calm walker" (an incomplete plan) | `send`-wrapping alone, with no `try`/`except` |
| a second plan for the panicked runners too | the `try`/`except Exception:` block, logging, then re-raising |

### 2. Why here specifically
This IS the real, concrete design consequence of docs/36's own abstract finding. Understanding
"there are two middlewares" (docs/36 C2) is necessary but not sufficient — a middleware AUTHOR
specifically needs to know WHERE their own code sits relative to that split, and react to it in
code, or silently lose visibility into exactly the failures most worth knowing about.

### What breaks if this changes
- **Skip the `try`/`except` entirely** (the REJECTED version in `d3`): `d3`'s own real output
  shows this directly — the `/bare` case logs "NOTHING LOGGED" at all, while `/specific` still
  works fine. The bug is silent and easy to miss in testing, since most error paths (anything with
  a specific handler) would appear to work correctly.
- **Catch the exception but DON'T re-raise it:** `ServerErrorMiddleware` (further out) would never
  receive the exception at all, meaning it could never send ITS OWN response — the client would
  get no response, or a broken/hung connection, instead of the intended structured 500.
- **Log the exception's own message directly into the response**, instead of just logging it
  server-side and re-raising: would duplicate work `_handle_unexpected_error` (P7-M3) already does
  correctly (building the safe, detail-hiding response body) — the middleware's job is observation
  and logging, not response construction.

### [Interview relevance: very high — recognizing that a middleware's own POSITION in a stack determines what it can and can't observe, and specifically designing for the error paths that behave differently, is real, production-grade middleware-authoring judgment, not something most tutorials cover]

**Interview question:** *"You wrote middleware to log every request, including failures. How do
you make sure it doesn't silently miss certain kinds of errors?"*
**A strong answer covers:** understanding exactly where your middleware sits in the framework's
own middleware stack relative to any OTHER error-handling middleware (in Starlette/FastAPI,
specifically relative to `ServerErrorMiddleware` and `ExceptionMiddleware`); recognizing that
different error paths can reach your middleware in structurally different forms — a normal
response for one, a raised exception for another — rather than assuming uniform behavior; wrapping
your own call into the downstream app in a `try`/`except` specifically to catch the exception-
shaped case, logging it there, and re-raising so any OUTER error-handling middleware still gets to
do its own job; and verifying this deliberately, by testing BOTH kinds of error paths explicitly,
since the failure mode (silently missing one specific case) doesn't show up under ordinary,
happy-path testing at all.

### 3. Minimal code that explains the concept
The complete script, [`d3_middleware_position_and_the_try_except.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d3_middleware_position_and_the_try_except.py>) (run from the project root):
```python
# Minimal demo of C4: a middleware registered via app.add_middleware(...) sits OUTSIDE
# ExceptionMiddleware but INSIDE ServerErrorMiddleware -- so it sees a SPECIFIC handler's
# response as a normal completion, but sees the bare-Exception handler's case as a raised
# exception passing THROUGH it instead. Proven with and without the fix.
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

def build_app(with_try_except: bool):
    app = FastAPI()
    seen = []

    class Middleware:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            status_holder = {"status": None}
            async def wrapped_send(message):
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                await send(message)
            if with_try_except:
                try:
                    await self.app(scope, receive, wrapped_send)
                except Exception as exc:
                    seen.append(("caught-then-reraised", type(exc).__name__, status_holder["status"]))
                    raise
                else:
                    seen.append(("normal-completion", None, status_holder["status"]))
            else:
                await self.app(scope, receive, wrapped_send)  # the REJECTED version
                seen.append(("normal-completion", None, status_holder["status"]))
                # this line NEVER runs for the bare-Exception case -- the exception skips it

    app.add_middleware(Middleware)

    class SpecificError(Exception): pass

    @app.exception_handler(SpecificError)
    async def h1(request, exc):
        return JSONResponse({"x": 1}, status_code=409)

    @app.exception_handler(Exception)
    async def h2(request, exc):
        return JSONResponse({"x": 2}, status_code=500)

    @app.get("/specific")
    def specific():
        raise SpecificError("handled by a SPECIFIC registered handler")

    @app.get("/bare")
    def bare():
        raise RuntimeError("only the bare Exception handler catches this")

    return app, seen

for label, with_try_except in [("WITHOUT the try/except (the rejected version)", False),
                                ("WITH the try/except (the real, shipped version)", True)]:
    print(f"=== {label} ===")
    app, seen = build_app(with_try_except)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/specific")
    client.get("/bare")
    for i, path in enumerate(("/specific", "/bare")):
        print(f"  {path:10s} -> middleware saw: {seen[i] if i < len(seen) else 'NOTHING LOGGED'}")
```
Real output:
```
=== WITHOUT the try/except (the rejected version) ===
  /specific  -> middleware saw: ('normal-completion', None, 409)
  /bare      -> middleware saw: NOTHING LOGGED

=== WITH the try/except (the real, shipped version) ===
  /specific  -> middleware saw: ('normal-completion', None, 409)
  /bare      -> middleware saw: ('caught-then-reraised', 'RuntimeError', None)
```
The REJECTED version correctly logs `/specific` (status 409, the real one `h1` chose) but
completely fails to log `/bare` at all — not a wrong value, a MISSING log line, which is a
particularly dangerous kind of bug since nothing crashes or errors to reveal it.

### The exact code I wrote (`src/api/main.py`)
> **Lines 106-114:**
> ```python
> try:
>     await self._app(scope, receive, wrapped_send)
> except Exception:
>     self._log(scope, status_holder["status"], started)
>     raise
>     # re-raises so ServerErrorMiddleware (outside this middleware) still sends its
>     # own response and still does its own re-raise for the real server's error log
> else:
>     self._log(scope, status_holder["status"], started)
> ```

**🎯 Interview-relevant lines:** `self._log(...)` is called in BOTH branches — not shared via a
`finally` block. This is deliberate: a `finally` block would run `self._log(...)` even if `_log`
ITSELF somehow raised inside the `except` branch, right before the critical `raise` statement,
risking the re-raise never happening at all. Calling it explicitly in both `except` and `else`
keeps the re-raise unconditionally guaranteed to execute right after logging, not intermixed with
`finally`'s own, subtly different exception-safety guarantees.

### Canonical docs
- `starlette/applications.py` (`Starlette.build_middleware_stack`) — read directly in
  [docs/36](<36 error_handling_deep_dive.md>) C2; this concept applies that exact finding to a new
  design problem. `www.starlette.io`'s own docs site remains unreachable from this environment.

### Micro-exercises
1. In `d3`, add a THIRD route with NO exception handler registered for its own exception type AT
   ALL (not even the bare `Exception` one — remove that registration temporarily for this specific
   test). What does the WITH-try/except version log for it? Does it still correctly re-raise?
2. Change the `except Exception:` clause to `except RuntimeError:` specifically. Raise a
   `ValueError` from `/bare` instead. Does the middleware still log it? What does this reveal about
   matching the except clause's own specificity to what you actually need to observe?
3. Swap the ORDER of `app.add_middleware(Middleware)` relative to a SECOND middleware you add.
   Does middleware registration order change which one sits closer to `ServerErrorMiddleware`?
   (Hint: check Starlette's own documentation/source for how `user_middleware` is ordered.)
4. Remove the `raise` statement from the `except` block (keep the logging). Run the WITH-version
   again. What does the CLIENT actually receive now for `/bare` — does `h2`'s own 500 response
   still get sent? Trace through why or why not.
5. Read this project's own [`tests/test_logging.py`](../tests/test_logging.py)'s
   `test_the_bare_exception_path_still_gets_logged_via_the_middlewares_own_try_except` and explain,
   in your own words, how it proves this exact finding against the REAL app, not just a toy one.

**How it works internally.** Python's own exception propagation is the entire mechanism here —
there's nothing ASGI-specific about it. `await self._app(scope, receive, wrapped_send)` is an
ordinary awaited call; if the code it runs (eventually reaching `ServerErrorMiddleware`'s own
internals, further out in the call stack from the ROUTE's perspective but actually reached via a
normal Python call chain from THIS middleware's own perspective) raises without being caught
anywhere in between, that exception propagates up through every enclosing `await` exactly like any
other Python exception would — `RequestLoggingMiddleware` is just one more frame in that chain,
needing its own `try`/`except` like any other.

### How to validate this yourself
`tests/test_logging.py`'s own `test_the_bare_exception_path_still_gets_logged_via_the_middlewares_own_try_except`
is the validation against the REAL app — it was specifically written by first removing the
`try`/`except` from the real code during design, confirming the test FAILED (no log line), then
restoring it and confirming the test passed — proving the test genuinely exercises this exact
mechanism, not just asserting something that happens to already be true.

### How to visualize this
Draw three nested boxes, labelled (outer to inner) `ServerErrorMiddleware`, `RequestLoggingMiddleware`,
`ExceptionMiddleware`. For a SPECIFIC-handler error, draw an arrow entering from the left, going
all the way into the innermost box, turning into a clean response there, and flowing back out
through BOTH outer boxes normally. For the bare-`Exception` case, draw the arrow going in, but
turning into a JAGGED, exception-shaped line at `ExceptionMiddleware`'s own boundary (since no
handler exists there) — that jagged line passes THROUGH `RequestLoggingMiddleware`'s own box
(which must catch and release it) before finally reaching `ServerErrorMiddleware`, where it turns
into a clean response only at the very outermost layer.

**You should now be able to explain:** why understanding a middleware's exact position in a stack
is not just trivia, but something that directly determines what code that middleware needs to
contain to avoid silently missing real, important failures.

---

## C5 — Root logger propagation

### Do this first
Run [`d4_root_logger_propagation.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d4_root_logger_propagation.py>).
**Predict first:** `logging.basicConfig(...)` is called once, with a custom format, attached to a
logger named (in the real app) `"src.api.main"`. A COMPLETELY SEPARATE logger, standing in for a
third-party dependency, never calls `basicConfig` or attaches anything of its own. Do you expect
its log messages to appear in your custom format too, or in Python's own plain default format,
or not at all?

### 1. Definition + analogy
**What it is.** Every `logging.Logger` has a `.propagate` attribute, `True` by default. When a
logger handles a record, it passes that record not just to its OWN handlers (if any), but also UP
to its PARENT logger's handlers, and that parent's parent, and so on — all the way to the ROOT
logger, unless propagation is stopped somewhere along the way (either `propagate = False`, or
running out of ancestors). `logging.basicConfig()` attaches a handler specifically to the ROOT
logger. The practical consequence: ANY logger anywhere in the process — including ones created by
completely unrelated third-party libraries that never heard of your app's own logging setup —
still reaches your root handler, UNLESS that library specifically opted out.

**Analogy.** A company-wide announcement system where every department's own intercom is wired, by
default, to relay UP to the building's central speaker system, unless a specific department
installed its own separate, disconnected speakers. If the building manager sets a new rule on the
CENTRAL system ("announce everything in French"), every department that never disconnected its own
intercom now has its announcements come out in French too — not because anyone told THAT
department anything, but because it was always wired to relay upward by default.

### For a 14-year-old
Imagine a big school where every classroom has its own small speaker, and by default, every one of
those speakers is ALSO wired to relay whatever it plays up to the school's main hallway speaker
system too — automatically, unless a specific classroom's teacher unplugged that wire on purpose.
If the principal changes the MAIN hallway speakers to announce everything in a funny robot voice,
EVERY classroom that never unplugged their own wire would suddenly have their own announcements
ALSO come out in that robot voice over the hallway — even a classroom that has nothing to do with
the principal's decision and never even knew about it.

`logging.basicConfig()` is the principal changing the main hallway speakers. `src.api.main`'s own
logger is one classroom. `httpx`'s own internal logger is a COMPLETELY DIFFERENT classroom, run by
someone who's never even heard of this project — but since `httpx`'s own logger never unplugged
its wire (`propagate = False`), its announcements ("HTTP Request: GET ...") get relayed up to the
SAME main hallway speakers too, automatically coming out in the same JSON "robot voice" as
everything else, with zero code changes needed anywhere inside `httpx` itself.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| every classroom's own small speaker | any `logging.getLogger(...)` anywhere in the process |
| the wire relaying up to the main hallway speakers | `.propagate = True`, the default |
| the principal changing the MAIN speakers | `logging.basicConfig(handlers=[_handler])` |
| a classroom that unplugged its own wire | a logger with `.propagate = False` (mirrors uvicorn's own access log) |
| every non-unplugged classroom suddenly sounding different | `httpx`'s own log lines, confirmed live, now appearing as JSON |

### 2. Why here specifically
This project's whole POINT with structured logging is consistent, parseable output — a log
aggregation tool (or a human grepping a file) benefits from EVERY line being the same shape,
including lines from Qdrant's client library, Neo4j's driver, or httpx. Understanding propagation
is what explains — correctly, not by accident — why attaching ONE formatter at the root achieves
this project-wide, rather than needing to hunt down and individually configure every dependency's
own logger.

### What breaks if this changes
- **Assume a third-party library's logs are invisible/separate unless explicitly configured:**
  confirmed false, live — `httpx`'s own internal request-logging line appeared in this project's
  real server output, in the SAME JSON format, with zero `httpx`-specific configuration anywhere
  in this codebase.
- **Assume EVERY logger propagates, including uvicorn's own access log:** also false —
  `uvicorn.access` has its own handler/configuration from uvicorn's own `dictConfig`, independent
  of this project's root config, which is exactly why uvicorn's own request lines
  (`INFO: 127.0.0.1:... "GET /health HTTP/1.1" 200 OK`) kept THEIR OWN format in the live test
  from [notes/step-7.4](<../notes/step-7.4-logging-observability.md>), unaffected by
  `_JsonFormatter` at all.
- **Attach the custom handler directly to `logging.getLogger(__name__)` instead of root, assuming
  it's "more precise":** would ONLY affect this project's own `src.api.main` logger — none of the
  dependency logs would be captured in the structured format at all, since they're entirely
  different logger objects that only converge at the ROOT, not at `src.api.main`.

### [Interview relevance: useful — understanding logger propagation explains a lot of "why did my logging config affect/not affect X" confusion in real codebases, and is foundational to correctly reasoning about where to attach handlers in any non-trivial application]

**Interview question:** *"You configured a custom log format at the root logger. Why do
third-party library logs also show up in that format, even though you never touched their code?"*
**A strong answer covers:** every `logging.Logger` propagates its records up through its parent
loggers to the root logger by default (`.propagate = True`), and a handler attached via
`logging.basicConfig()` (or manually) at the root level receives every record that isn't
explicitly stopped earlier in that chain; a library would need to deliberately set
`propagate = False` (and configure its own handler) to opt out, which most libraries don't do for
their default loggers; and this is generally a FEATURE for consistent, structured logging across
a whole process, though it does mean a root-level config decision has a genuinely wider blast
radius than just "my own code's log calls."

### 3. Minimal code that explains the concept
The complete script, [`d4_root_logger_propagation.py`](<../practice codes/phase 7/m4_logging_observability_deep_dive/d4_root_logger_propagation.py>) (run from the project root; note log output
goes to `stderr` by default, which is why its lines may appear interleaved differently than the
`print()` calls' own `stdout` output when both are captured together):
```python
# Minimal demo of C5: logging.basicConfig() attaches a handler to the ROOT logger, and every
# OTHER logger (including a stand-in for a third-party dependency) propagates its own records
# up to root by default -- so a root-level formatter affects the whole process, not just
# "your own" log calls. Also shows the one real way a logger can OPT OUT of this.
import logging

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("CUSTOM-FORMAT | %(name)s | %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler])

app_logger = logging.getLogger("src.api.main")
app_logger.info("a real request was handled")

dependency_logger = logging.getLogger("some_dependency.internal")
dependency_logger.info("an HTTP request the dependency made internally")
# never called basicConfig, never attached its own handler -- just logs normally

independent_logger = logging.getLogger("independent_access_log")
independent_logger.propagate = False
independent_handler = logging.StreamHandler()
independent_handler.setFormatter(logging.Formatter("INDEPENDENT | %(message)s"))
independent_logger.addHandler(independent_handler)
independent_logger.info("this one manages its own formatting entirely")

print("app_logger.propagate        =", app_logger.propagate)
print("dependency_logger.propagate =", dependency_logger.propagate)
print("independent_logger.propagate=", independent_logger.propagate)
```
Real output:
```
CUSTOM-FORMAT | src.api.main | a real request was handled
CUSTOM-FORMAT | some_dependency.internal | an HTTP request the dependency made internally
INDEPENDENT | this one manages its own formatting entirely
...
logger hierarchy check:
  app_logger.propagate        = True
  dependency_logger.propagate = True
  independent_logger.propagate= False
  root logger's own handlers  = [<StreamHandler <stderr> (NOTSET)>]
```
`some_dependency.internal` — a logger this script never configured at all — still printed in the
EXACT custom format, proving propagation reached it. `independent_access_log` — the ONLY one with
`propagate = False` — printed in its OWN, completely different format, proving that's the one real
way to opt out.

### The exact code I wrote (`src/api/main.py`)
> **Lines 60-67:**
> ```python
> _handler = logging.StreamHandler()
> _handler.setFormatter(_JsonFormatter())
> logging.basicConfig(level=logging.INFO, handlers=[_handler])
> # every logger.*() call project-wide that reaches the root logger -- including P7-M3's own
> # existing logger.warning/.exception calls, unchanged -- now emits this same JSON shape.
> # Verified directly (not assumed) that this survives real `uvicorn src.api.main:app` startup:
> # uvicorn configures its OWN named loggers (uvicorn, uvicorn.access) independently rather
> # than overwriting the root logger's handlers.
> ```

**🎯 Interview-relevant lines:** the comment names BOTH halves of this concept directly —
propagation reaching P7-M3's own existing calls "for free" (the intended, designed-for effect),
and uvicorn's own loggers staying independent (the boundary of that effect) — both empirically
confirmed, not assumed, exactly as documented in `decision.md`'s own D-2026-09-30-28 side-effect note.

### Canonical docs
- [Python docs: Logger.propagate](https://docs.python.org/3/library/logging.html#logging.Logger.propagate)
- [Python logging HOWTO: Logging Flow](https://docs.python.org/3/howto/logging.html#logging-flow) —
  the official diagram of exactly this propagation path.

### Micro-exercises
1. In `d4`, add a logger named `"some_dependency"` (the PARENT of `"some_dependency.internal"`,
   one level up) with ITS OWN handler and `propagate = False`. Does `"some_dependency.internal"`'s
   own message still reach root, or does it now stop at its immediate parent instead?
2. Set `app_logger`'s OWN level to `logging.ERROR` (stricter than root's `INFO`). Log an INFO
   message through it. Does it reach root's handler? What does this reveal about a record needing
   to pass EACH logger's own level check along the way, not just propagate unconditionally?
3. Research why `uvicorn`'s own loggers (`uvicorn`, `uvicorn.access`, `uvicorn.error`) don't get
   affected by this project's root config, by reading `uvicorn.config.LOGGING_CONFIG` directly in
   the installed package. Do they use `propagate = False`, their own handlers, or something else?
4. Call `logging.basicConfig(...)` a SECOND time in `d4`, with a different format. What happens?
   (Hint: `basicConfig`'s own documentation specifically addresses being called more than once.)
5. In the REAL project, temporarily add `logging.getLogger("httpx").propagate = False` to
   `src/api/main.py` and restart the live server. Confirm `httpx`'s own request logs disappear
   from the structured output entirely, proving this is a real, working opt-out mechanism, not
   just theoretical.

**How it works internally.** `Logger.callHandlers` (called internally whenever a record passes a
logger's own level check) walks UP the logger hierarchy via each logger's `.parent` attribute,
calling every handler it finds along the way, and stops only when it reaches a logger with
`propagate = False` or runs out of parents (reaching the root logger, which has no parent).
`logging.getLogger("src.api.main")`'s parent is `logging.getLogger("src")` (even though nothing
ever explicitly created that logger — Python creates the chain implicitly from the dotted name),
whose parent is the root logger itself.

### How to validate this yourself
`d4`'s own three-logger comparison — one project logger, one unconfigured "dependency" logger, one
explicitly-opted-out logger — run side by side under the IDENTICAL root configuration, is the
validation: only the propagation SETTING differs between the second and third, and only that
setting determines whether the custom format applies.

### How to visualize this
Draw a tree. The root node is "root logger," with a handler icon attached. Draw two child nodes,
"src.api.main" and "some_dependency.internal," both with arrows pointing UP toward root (since
both propagate). Draw a THIRD child, "independent_access_log," with its OWN, separate handler icon
attached directly to it, and an arrow pointing up toward root that's drawn as CUT OFF partway
(propagation stopped) — nothing from this one ever reaches the root handler.

**You should now be able to explain:** why one `logging.basicConfig()` call at the root affects
far more than just this project's own code, and the exact mechanism (`.propagate`) that determines
which loggers are included and which aren't.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Start the real server and watch real JSON log lines arrive.**
   ```bash
   .venv/Scripts/python -m uvicorn src.api.main:app --port 8000
   ```
   ```bash
   curl -s http://localhost:8000/health
   ```
   Find the real `{"timestamp": ..., "level": "INFO", ..., "method": "GET", "path": "/health",
   "status_code": 200, "duration_ms": ...}` line in the server's own console output (C1, C3).
2. **Watch `httpx`'s own internal logging appear in the SAME format**, right alongside this
   project's own lines, without any `httpx`-specific code anywhere in this codebase (C5).
3. **Send a real, slow `/chat` request and watch its `duration_ms`.** Confirm it reflects the REAL
   total time (potentially 100+ real seconds on this project's own dev host), not a near-zero
   number — the one claim this whole step's design exists to make true (C2).
4. **Trigger the bare-`Exception` path for real**, using a temporarily-added broken route (mirror
   `tests/test_logging.py`'s own test-only route), and confirm a real log line still appears for
   it, with `status_code: 500` — proving C4's `try`/`except` fix works outside of tests too, not
   just hermetically.
5. **Run the full test file and connect each test back to a concept above.**
   ```bash
   .venv/Scripts/python -m pytest tests/test_logging.py -v
   ```
   For each of the 8 tests, name which of C1-C5 it's really proving.

If you can explain step 4's result using ONLY this step's own `try`/`except` reasoning (C4) PLUS
[docs/36](<36 error_handling_deep_dive.md>)'s own `ServerErrorMiddleware` finding (C2 there) — not
a guess, not "middleware just works" — you've connected three consecutive microsteps' own real,
verified findings into one coherent mental model, which is the strongest sign either was actually
understood rather than just completed.
