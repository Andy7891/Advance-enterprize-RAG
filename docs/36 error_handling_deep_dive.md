# 36 — Request/Response Validation & Error Handling Deep Dive (P7-M3)

> Deconstructs P7-M3's additions to [`src/api/main.py`](../src/api/main.py) — `_error_envelope`,
> `_handle_validation_error`, `_handle_timeout`, `_handle_unexpected_error`, and `HealthResponse`
> — plus the real fix to [`src/embedding/embedder.py`](../src/embedding/embedder.py). This deep
> dive does NOT re-cover ground [docs/34](<34 fastapi_skeleton_deep_dive.md>) or
> [docs/35](<35 chat_streaming_deep_dive.md>) already built (the ASGI app object, `lifespan`,
> Pydantic request validation basics, SSE framing, sync-generator-in-threadpool safety, or
> `_sse_events`'s own in-stream error handling) — it covers what's genuinely new: how a raised
> exception actually gets matched to a registered handler, why one specific registration
> (`Exception` itself) behaves structurally differently from every other, how to read what HTTP
> status codes actually mean and engineer a real distinction between two of them, a test-harness
> setting that can make correct code look broken, and the difference between an absent timeout and
> a disabled one. Demo scripts live in
> [`practice codes/phase 7/m3_error_handling_deep_dive/`](<../practice codes/phase 7/m3_error_handling_deep_dive/>)
> and need no Docker or real server — every one runs the real `FastAPI`/`Starlette`/`httpx`/`ollama`
> machinery in-process. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 7/m3_error_handling_deep_dive/d1_two_middlewares_not_one.py"
> ```

---

## 0. Plain-language summary

```
_error_envelope:             one shared {"error": {"code", "message"}} shape for every failure
_handle_validation_error:    RequestValidationError -> 400 if unparseable, else 422 (with a real
                              message built from Pydantic's own errors() list)
_handle_timeout:              TimeoutError / httpx.TimeoutException -> a structured 504
_handle_unexpected_error:     anything else -> a structured, detail-hiding 500, logged server-side
EMBED_TIMEOUT_SECONDS:        Embedder's Ollama client went from "no timeout at all" to a real,
                              measured 60s bound
```

Five concepts, in the order this step actually needed them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | Exception handler registration & MRO-based dispatch | 🔴 high | `@app.exception_handler(...)` |
| C2 | Two middlewares, not one: why `Exception`/500 is special | 🔴 very high | why `_handle_unexpected_error` needed a test fix |
| C3 | HTTP status code semantics: engineering a real 400/422 split | 🔴 high | `_handle_validation_error` |
| C4 | `TestClient(raise_server_exceptions=...)` | 🟡 high | `tests/test_errors.py`'s own `client` |
| C5 | An absent timeout vs. a disabled one | 🟡 high | `EMBED_TIMEOUT_SECONDS` |

---

## C1 — Exception handler registration & MRO-based dispatch

### Do this first
Run [`d0_handler_registration_and_dispatch.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d0_handler_registration_and_dispatch.py>).
**Predict first:** `QdrantDown` is a subclass of `UpstreamError`. Only `UpstreamError` ever gets a
`@app.exception_handler(...)` registration — `QdrantDown` gets none of its own. When a route
raises `QdrantDown`, do you expect a 500-style crash with no handler found, or for `UpstreamError`'s
handler to catch it anyway?

### 1. Definition + analogy
**What it is.** `@app.exception_handler(SomeExceptionClass)` registers a function to run whenever
a path operation raises `SomeExceptionClass` — or any *subclass* of it. When an exception is
raised, FastAPI/Starlette don't look for an exact type match; they walk the exception's own MRO
(Method Resolution Order — the chain `type(exc), ..., Exception, BaseException, object`) and use
the handler registered for the *first* class in that chain that has one.

**Analogy.** A lost-and-found desk with labeled bins: one bin says "electronics," a more specific
one says "phones." If you hand in a laptop (no "laptop" bin exists), the desk clerk doesn't shrug —
they know a laptop IS a kind of electronics, so it goes in that bin. The clerk is matching by
*category membership*, not by an exact, pre-printed label.

### For a 14-year-old
Imagine a school where every student's excuse for missing class has to be sorted into a folder by
the attendance office. There's a folder labeled "Illness" and a more specific one labeled "Flu."
If a student's note just says "I was sick," the office has a rule: if there's no exact folder for
the specific thing (no folder literally labeled "sick"), look at what CATEGORY it belongs to —
"sick" is a kind of "Illness" — and file it there instead. Now imagine a DIFFERENT student's note
says "I had the flu" — even more specific. If there's still no "Flu" folder of its own, it still
works, because "Flu" is ALSO a kind of "Illness," and the office keeps checking one category up
until it finds a folder that exists.

`UpstreamError` is like the "Illness" folder — a general category for "something about a backend
service went wrong." `QdrantDown` is like "Flu" — much more specific, but nobody ever made it its
own folder. When a route raises `QdrantDown`, FastAPI doesn't fail because there's no exact match;
it walks UP `QdrantDown`'s own family tree (`QdrantDown` → `UpstreamError` → `Exception` → ...)
until it finds the first ancestor that DOES have a registered folder — `UpstreamError` — and uses
that one.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the "Illness" folder, which exists | `@app.exception_handler(UpstreamError)`, registered |
| the "Flu" folder, which was never made | `QdrantDown`, never separately registered |
| "I had the flu" still reaching the Illness folder | raising `QdrantDown` still reaching `UpstreamError`'s handler |
| walking up the family tree one level at a time | walking `QdrantDown.__mro__` until a match is found |
| the folder actually used, printed on the note afterward | `calls` in `d0`, proving which handler really ran |

### 2. Why here specifically
This project's real backing services (Qdrant, Neo4j, Ollama) each have their own distinct
exception types (`neo4j.exceptions.ServiceUnavailable`, `httpx.TimeoutException`, ...). MRO-based
dispatch means a FUTURE handler registered for a shared, general category (like this project's own
`EmbeddingError(RuntimeError)`, or a hypothetical `UpstreamError` base class) would automatically
catch every more-specific subclass raised beneath it, without needing a separate registration for
each one — the same reason Python exception hierarchies exist as hierarchies at all.

### What breaks if this changes
- **Assume "only exact type matches are caught":** would wrongly predict that `QdrantDown`
  crashes with no handler found — `d0`'s own real output disproves this directly.
- **Register a handler for `QdrantDown` only, expecting it to ALSO catch a plain `UpstreamError`:**
  the reverse direction does NOT work — a raised `UpstreamError` (the base) does NOT match a
  handler registered only for `QdrantDown` (the more specific subclass), since `UpstreamError`'s
  own MRO never reaches `QdrantDown` at all. MRO walking only ever goes from specific → general,
  never the other way.
- **Register handlers for BOTH `UpstreamError` and `QdrantDown`:** the MORE SPECIFIC one
  (`QdrantDown`'s own handler) wins for a raised `QdrantDown` — dispatch always stops at the
  FIRST match walking outward from the exact raised type, so the more specific registration takes
  priority over the more general one, exactly matching ordinary Python `except` clause ordering
  intuition.

### [Interview relevance: high — understanding that exception handling (in FastAPI, or plain Python try/except with multiple except clauses) matches by class hierarchy, not exact type, is fundamental to designing a sane exception taxonomy for any real application]

**Interview question:** *"If you register an error handler for a base exception class, will it
catch a more specific subclass raised somewhere in your code?"*
**A strong answer covers:** yes — exception matching (in FastAPI's handler dispatch, or a plain
Python `except SomeBaseClass:`) walks the raised exception's own class hierarchy (MRO) and uses
the first match, so a handler registered for a general base class transparently catches any more
specific subclass, without needing its own separate registration; the reverse isn't true (a
handler for a subclass won't catch its own base class); and when BOTH a base and a subclass have
their own registered handler, the more specific one wins for an instance of that subclass
specifically — the same "most specific match wins" principle behind ordinary multi-clause
`try/except` blocks.

### 3. Minimal code that explains the concept
The complete script, [`d0_handler_registration_and_dispatch.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d0_handler_registration_and_dispatch.py>) (run from the project root):
```python
# Minimal demo of C1: a raised exception is dispatched to the handler registered for the
# MOST SPECIFIC matching class in its own MRO -- not "was this exact type registered."
# QdrantDown below is NEVER registered directly; only its ancestor, UpstreamError, is.
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

app = FastAPI()

calls = []  # records which handler actually ran, so dispatch is PROVEN, not assumed


class UpstreamError(Exception):
    """A made-up base class, standing in for a family of related backend failures."""


class QdrantDown(UpstreamError):
    """A more specific failure -- one particular KIND of UpstreamError."""


@app.exception_handler(UpstreamError)
async def handle_upstream_error(request, exc):
    calls.append("handle_upstream_error")
    # registered for UpstreamError -- but will ALSO catch any subclass of it
    return JSONResponse({"handled_by": "UpstreamError handler", "type": type(exc).__name__})


@app.get("/raise-base")
def raise_base():
    raise UpstreamError("a generic upstream problem")


@app.get("/raise-specific")
def raise_specific():
    raise QdrantDown("qdrant specifically is down")
    # QdrantDown has NO handler of its own registered anywhere in this app


client = TestClient(app)

r1 = client.get("/raise-base")
print("raise-base     ->", r1.status_code, r1.json())

r2 = client.get("/raise-specific")
print("raise-specific ->", r2.status_code, r2.json())

print("\nhandlers that ran, in order:", calls)
print("QdrantDown's own MRO:", [c.__name__ for c in QdrantDown.__mro__])
print("-> dispatch walked this MRO and used the first ancestor with a REGISTERED handler")
```
Real output:
```
raise-base     -> 200 {'handled_by': 'UpstreamError handler', 'type': 'UpstreamError'}
raise-specific -> 200 {'handled_by': 'UpstreamError handler', 'type': 'QdrantDown'}

handlers that ran, in order: ['handle_upstream_error', 'handle_upstream_error']
QdrantDown's own MRO: ['QdrantDown', 'UpstreamError', 'Exception', 'BaseException', 'object']
-> dispatch walked this MRO and used the first ancestor with a REGISTERED handler
```
The SAME handler function ran both times — once for an exact match, once for a subclass three
steps down `QdrantDown`'s own MRO — and `type(exc).__name__` inside the handler still correctly
reports `QdrantDown`, proving the handler receives the REAL, specific exception instance, not a
generic stand-in.

### The exact code I wrote (`src/api/main.py`)
> **Lines 91 and 112-113 (the registration DECORATORS themselves are the whole mechanism):**
> ```python
> @app.exception_handler(RequestValidationError)
> ...
> @app.exception_handler(TimeoutError)
> @app.exception_handler(httpx.TimeoutException)
> async def _handle_timeout(request: Request, exc: Exception) -> JSONResponse:
> ```

**🎯 Interview-relevant lines:** stacking TWO `@app.exception_handler(...)` decorators on
`_handle_timeout` (lines 112-113) works for exactly the reason C1 explains generally: each
decorator call independently registers the SAME function for a DIFFERENT class, and
`Starlette.exception_handler(...)` (the decorator factory) returns the original function
unchanged — the same pattern that lets `@app.get(...)` be stacked for multiple routes pointing to
one handler.

### Canonical docs
- [FastAPI: Handling Errors](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [Python docs: the built-in exception hierarchy](https://docs.python.org/3/library/exceptions.html#exception-hierarchy)

### Micro-exercises
1. In `d0`, add a SECOND, even-more-specific subclass, `QdrantConnectionRefused(QdrantDown)`, and
   raise it from a new route. Predict which handler catches it before running.
2. Now ALSO register a handler specifically for `QdrantDown` (in addition to the existing
   `UpstreamError` one). Raise `QdrantConnectionRefused` again — which handler wins now? Which
   MRO position determined it?
3. Try registering TWO handlers for the exact same class (`UpstreamError`) by calling
   `@app.exception_handler(UpstreamError)` twice, on two DIFFERENT functions. Which one actually
   runs? What does this tell you about `app.exception_handlers` being a plain dict (one key, one
   value, not a list)?
4. Raise a completely unrelated exception (e.g. `ValueError`, sharing no ancestor with
   `UpstreamError` except `Exception` itself) from a new route, with no handler registered for
   `ValueError` or `Exception`. What happens? (Hint: this previews C2.)
5. Print `UpstreamError.__mro__` and `Exception.__mro__` side by side. At which exact class do
   they converge?

**How it works internally.** Starlette's `ExceptionMiddleware._lookup_exception_handler` walks
`type(exc).__mro__` in order and returns `self._exception_handlers[cls]` for the first `cls` that
is a key in that dict — a direct, simple linear scan, not anything more exotic. `app.exception_handlers`
(the flat dict every `@app.exception_handler(...)` call populates) is the single source of truth
that dict is built from.

### How to validate this yourself
`d0`'s own `calls` list is direct, unambiguous proof — if dispatch worked by exact-type matching
only, `raise_specific`'s call would have found NO handler and crashed instead of appending a
second `"handle_upstream_error"` entry.

### How to visualize this
Draw `QdrantDown` as a small circle, with an arrow pointing to a larger circle `UpstreamError`
around it, with an even larger circle `Exception` around THAT. Only `UpstreamError`'s circle gets
shaded in (registered). A dot marking "the raised exception" starts inside the smallest circle and
moves outward one circle at a time until it lands inside a SHADED one — that's the handler that runs.

**You should now be able to explain:** why FastAPI's exception dispatch is really just "walk the
MRO, use the first registered match," and why this makes a handler registered for a general base
class automatically future-proof against new, more specific subclasses added later.

---

## C2 — Two middlewares, not one: why `Exception`/500 is special

### Do this first
Run [`d1_two_middlewares_not_one.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d1_two_middlewares_not_one.py>).
**Predict first:** `app.exception_handlers` is ONE flat dict containing both a specific
(`SpecificError`) and a general (`Exception`) registration. Do you expect Starlette's real,
constructed ASGI middleware chain to also treat them identically — stored together, handled the
same way — or differently?

### 1. Definition + analogy
**What it is.** Every handler you register — no matter the class — goes into the same
`app.exception_handlers` dict. But when Starlette actually BUILDS its real middleware stack
(`Starlette.build_middleware_stack`), it specifically pulls the handler registered for the literal
`Exception` class (or status code `500`) OUT of that dict and gives it to a DIFFERENT middleware —
`ServerErrorMiddleware`, always the outermost layer — while every other handler stays in
`ExceptionMiddleware`, one layer in. These two middlewares behave differently on purpose:
`ExceptionMiddleware` handles its exceptions and moves on; `ServerErrorMiddleware` handles its ONE
exception and then **re-raises it anyway**, specifically so a real server's own error logging
still sees it, even though a client-facing response was already sent.

**Analogy.** A hospital with a normal nurses' station for everyday complaints (a specific
diagnosis, a known condition — treated, then the patient's chart is closed) and a SEPARATE, always
on standby emergency-escalation line for "we don't know what this is" cases. The escalation line
still stabilizes the patient (sends a response) — but it ALSO automatically pages the head doctor
every single time, regardless of outcome, because an unidentified case needs to be reviewed at a
higher level even after it's been handled.

### For a 14-year-old
Imagine a school has two very different ways problems get handled. If a teacher already has a
KNOWN plan for something — say, "a student forgot their homework" — the teacher just follows the
normal, printed rule for it, marks it down, and moves on; nobody else ever hears about it. But if
something happens that's a genuine EMERGENCY with no specific plan — a fire alarm, someone badly
hurt — there's a totally different, separate system: an intercom straight to the principal's
office. Even after the principal handles the immediate emergency (gets everyone safe), the
principal STILL always writes an incident report afterward, no matter what — because a genuine
emergency needs to be reviewed at a higher level even once it's over, unlike a routine forgotten-
homework case that's simply closed once it's dealt with.

`_handle_validation_error` and `_handle_timeout` are like the normal, printed rule — known,
specific problems FastAPI handles through its everyday system (`ExceptionMiddleware`) and moves
on. `_handle_unexpected_error`, registered for the bare `Exception` class, is the intercom line —
handled through a COMPLETELY SEPARATE system (`ServerErrorMiddleware`) that ALWAYS re-raises the
original problem afterward too, specifically so a real deployed server's own error logs still
record that something unanticipated happened, even though the caller already got a clean response.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the normal, printed rule for a known problem | a handler registered for a SPECIFIC class (`RequestValidationError`, `TimeoutError`) |
| the separate intercom line straight to the principal | the ONE handler registered for the bare `Exception` class |
| the principal handling it AND still filing a report | `ServerErrorMiddleware` sending a response AND still re-raising |
| the normal system never involving the principal at all | `ExceptionMiddleware` never re-raises after handling |
| "which system handles this" decided the moment the rule is written | decided the moment `build_middleware_stack()` sorts `Exception`/500 out from everything else |

### 2. Why here specifically
`_handle_unexpected_error` exists to catch truly unanticipated bugs — by definition, the kind
nobody wrote a specific handler for. A real deployed server (uvicorn) SHOULD still log these
loudly (via its own error-reporting path, which the re-raise feeds), even while the CLIENT still
gets a clean, safe 500 response. This is exactly the dual requirement `ServerErrorMiddleware`'s
re-raise-after-send design satisfies — not a quirk, a deliberate choice.

### What breaks if this changes
- **Assume registering `Exception` behaves exactly like registering any other class:** this is
  precisely the assumption that broke the FIRST real test run of `_handle_unexpected_error` — the
  test failed not because the handler was wrong, but because the test's own `TestClient` surfaced
  the re-raise. Section C4 covers the fix.
- **Register a handler for `RuntimeError` (a specific, common class) and expect it to ALSO
  behave like the `Exception` case (re-raising after sending):** it won't — `RuntimeError` isn't
  `Exception` or `500`, so it stays in `ExceptionMiddleware`'s dict, which never re-raises. Only
  the LITERAL `Exception` class (or the integer `500`) gets the special treatment.
- **Never register ANYTHING for `Exception`:** `build_middleware_stack`'s own `error_handler`
  stays `None`, and `ServerErrorMiddleware` falls back to its own built-in default behavior
  (a generic plain-text 500, or a debug traceback page if `debug=True`) — still re-raising either
  way, since the re-raise isn't conditional on whether a custom handler exists.

### [Interview relevance: very high — recognizing that "the base Exception class" is often treated specially by a framework's own error-handling machinery (not just "a slightly more general registration") is the kind of framework-internals knowledge that distinguishes surface familiarity from real understanding]

**Interview question:** *"You registered a handler for the base `Exception` class in a web
framework. A test using that handler seems to fail even though the handler's own logic looks
correct — what would you check?"*
**A strong answer covers:** many frameworks (FastAPI/Starlette specifically) treat a handler
registered for the literal base exception class (or the generic 500 status) as special — routed
through an outermost "last resort" middleware that both sends the handler's response AND
re-raises the original exception afterward, specifically so the exception still reaches the real
server's own error logging; a test HARNESS (like `TestClient`) may have its own setting controlling
whether that re-raise surfaces back into the test itself, which can make perfectly correct handler
logic look like a failure; the fix is usually a test-client configuration change, not a change to
the handler — verified by reading the framework's own source for the specific routing logic
involved, rather than assuming the handler itself must be buggy.

### 3. Minimal code that explains the concept
The complete script, [`d1_two_middlewares_not_one.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d1_two_middlewares_not_one.py>) (run from the project root):
```python
# Minimal demo of C2: registering a handler for the literal Exception class does NOT go into
# the same place as every other registered type. Proven structurally, by inspecting Starlette's
# own constructed middleware stack directly -- not inferred from behavior.
from fastapi import FastAPI
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware.exceptions import ExceptionMiddleware

app = FastAPI()


class SpecificError(Exception):
    pass


@app.exception_handler(SpecificError)
async def handle_specific(request, exc):
    return None  # never actually called in this script -- only registration matters here


@app.exception_handler(Exception)
async def handle_anything(request, exc):
    return None


print("app.exception_handlers -- BOTH registrations live in the SAME flat dict:")
for key, handler in app.exception_handlers.items():
    print(f"  {key!r} -> {handler.__name__}")

print("\nwalking the REAL, constructed ASGI middleware chain (app.build_middleware_stack()):")
stack = app.build_middleware_stack()
node = stack
depth = 0
while hasattr(node, "app"):
    print(f"  depth {depth}: {type(node).__name__}")
    if isinstance(node, ServerErrorMiddleware):
        name = node.handler.__name__ if node.handler else None
        print(f"      .handler (ONE slot, Exception/500 only)  = {name}")
    if isinstance(node, ExceptionMiddleware):
        names = [k.__name__ if isinstance(k, type) else k for k in node._exception_handlers]
        print(f"      ._exception_handlers (a dict, everything else) = {names}")
    node = node.app
    depth += 1
    if depth > 6:
        break

print("\n-> SpecificError's handler lives in ExceptionMiddleware's own dict.")
print("-> Exception's handler was pulled OUT into ServerErrorMiddleware's single .handler slot.")
print("-> These are two different middleware classes with two different behaviors on purpose.")
```
Real output:
```
app.exception_handlers -- BOTH registrations live in the SAME flat dict:
  <class 'starlette.exceptions.HTTPException'> -> http_exception_handler
  <class 'fastapi.exceptions.RequestValidationError'> -> request_validation_exception_handler
  <class 'fastapi.exceptions.WebSocketRequestValidationError'> -> websocket_request_validation_exception_handler
  <class '__main__.SpecificError'> -> handle_specific
  <class 'Exception'> -> handle_anything

walking the REAL, constructed ASGI middleware chain (app.build_middleware_stack()):
  depth 0: ServerErrorMiddleware
      .handler (ONE slot, Exception/500 only)  = handle_anything
  depth 1: ExceptionMiddleware
      ._exception_handlers (a dict, everything else) = ['HTTPException', 'WebSocketException', 'RequestValidationError', 'WebSocketRequestValidationError', 'SpecificError']
  depth 2: AsyncExitStackMiddleware
  depth 3: APIRouter

-> SpecificError's handler lives in ExceptionMiddleware's own dict.
-> Exception's handler was pulled OUT into ServerErrorMiddleware's single .handler slot.
-> These are two different middleware classes with two different behaviors on purpose.
```
Notice FastAPI ALREADY pre-registers three handlers of its own (`HTTPException`,
`RequestValidationError`, `WebSocketRequestValidationError`) before this project ever adds
anything — `_handle_validation_error`'s own registration (C3) REPLACES the default
`request_validation_exception_handler` entry in this exact same dict, rather than adding a second,
competing one.

### The exact code I wrote (`src/api/main.py`)
> **Line 128:**
> ```python
> @app.exception_handler(Exception)
> async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
> ```

**🎯 Interview-relevant lines:** this ONE decorator argument — `Exception`, not
`(Exception, 500)` or anything more elaborate — is the entire trigger for the special
`ServerErrorMiddleware` routing. Nothing else about how `_handle_unexpected_error` is written
(its body, its return type) causes the special behavior; it's purely the registered CLASS itself.

### Canonical docs
- `starlette/applications.py` (`Starlette.build_middleware_stack`) and
  `starlette/middleware/errors.py` (`ServerErrorMiddleware`) — read directly from the installed
  package; `www.starlette.io`'s own docs site could not be reached from this environment (DNS
  resolution failure, confirmed via both `curl` and the browser tool).
- [FastAPI: Handling Errors](https://fastapi.tiangolo.com/tutorial/handling-errors/) — documents
  overriding the default handlers this project's own `app.exception_handlers` dict shows pre-populated.

### Micro-exercises
1. In `d1`, register a handler for the INTEGER `500` instead of the class `Exception`
   (`@app.exception_handler(500)`). Does it ALSO get pulled into `ServerErrorMiddleware.handler`?
   What does this tell you about `build_middleware_stack`'s own `if key in (500, Exception):` check?
2. Register a handler for `starlette.exceptions.HTTPException` yourself (overriding FastAPI's own
   default). Does IT get the special `ServerErrorMiddleware` treatment, or does it stay in
   `ExceptionMiddleware`? Where does it fall in `app.exception_handlers`'s print-out?
3. Remove the `@app.exception_handler(Exception)` registration entirely from `d1` and re-run.
   What does `stack.handler` (the `ServerErrorMiddleware` node) show now?
4. `depth 2` and `depth 3` in the real output are `AsyncExitStackMiddleware` and `APIRouter` —
   two middlewares this project's own code never registered directly. Research what
   `AsyncExitStackMiddleware` is for (hint: it relates to FastAPI's dependency injection system).
5. Read `starlette/middleware/errors.py`'s own `ServerErrorMiddleware.__call__` directly (the file
   is in this project's `.venv`). Find the exact line that re-raises, and confirm it runs
   regardless of whether `self.handler` is `None` or a real function.

**How it works internally.** `Starlette.build_middleware_stack` (the method `d1` calls directly)
does, in essence: `error_handler = self.exception_handlers.pop(Exception, self.exception_handlers.pop(500, None))`-style
extraction (the real code checks `if key in (500, Exception)`), then constructs
`ServerErrorMiddleware(next_app, handler=error_handler)` as the OUTERMOST wrapper around
everything else, including `ExceptionMiddleware(next_app, handlers=<everything remaining>)` one
layer in. "Outermost" means it's the FIRST thing a request hits and the LAST thing a response (or
a re-raised exception) passes back through.

### How to validate this yourself
`d1`'s own middleware-stack walk is the validation — it doesn't infer the split from behavior, it
reads the real, constructed object graph directly and shows `handle_anything` sitting in exactly
one place (`ServerErrorMiddleware.handler`) while `handle_specific` sits in a completely different
one (`ExceptionMiddleware._exception_handlers`).

### How to visualize this
Draw a request arriving from the left, passing through boxes left to right: `ServerErrorMiddleware`
first (outermost), then `ExceptionMiddleware`, then the app's routes. Draw the RESPONSE path as an
arrow going back right-to-left. For a `SpecificError`, draw the response arrow starting at
`ExceptionMiddleware` — it never needs to go further right, back through `ServerErrorMiddleware`.
For an `Exception`-class failure, draw TWO arrows leaving `ServerErrorMiddleware` on the way back:
one going left (the real response, sent), and a second one, dashed, labeled "re-raised," continuing
past `ServerErrorMiddleware` toward whatever called the whole ASGI app (a real server's own logging,
or — as C4 covers — a test client).

**You should now be able to explain:** why a handler registered for the bare `Exception` class is
not "just a more general version" of every other registration, but is routed through an entirely
different middleware with a deliberately different contract.

---

## C3 — HTTP status code semantics: engineering a real 400/422 split

### Do this first
Run [`d2_status_code_semantics.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d2_status_code_semantics.py>).
**Predict first:** all four cases send a POST to the same endpoint. Case 1's body isn't valid JSON
at all; cases 2 and 3 ARE valid JSON but violate a field's own rule. Do you expect the printed raw
`errors()` structure to look meaningfully different between case 1 and cases 2/3, or basically the same?

### 1. Definition + analogy
**What it is.** HTTP status codes aren't interchangeable synonyms for "something's wrong" — each
one has a specific, standardized meaning. `400 Bad Request` means the request itself is malformed
— the server can't even make sense of its shape. `422 Unprocessable Content` means the request WAS
understood just fine, but its contents fail some rule. FastAPI's own default collapses this
distinction (both return 422) — engineering a real split means inspecting `RequestValidationError.errors()`'s
own `type` field to tell the two cases apart.

**Analogy.** A form dropped off at an office. If it's literally illegible — torn, written in the
wrong language, missing pages — the clerk can't even begin to process it (400: "I can't read
this"). If it's perfectly legible, filled out in the right format, but says your age is "−5" or
leaves the name field blank, the clerk read it just fine and is rejecting it on its CONTENTS
(422: "I understood this, and it's wrong").

### For a 14-year-old
Imagine turning in a homework assignment two different ways it could go wrong. In the FIRST way,
you hand in a page that's been through the wash — smudged, torn, the teacher literally cannot
read half of what you wrote. The teacher can't even grade it; they just hand it back and say "I
can't read this at all, try again." In the SECOND way, your homework is perfectly neat and
readable — but you answered question 3 with "purple" when it asked for a number, or you left
question 5 completely blank. The teacher CAN read every word just fine; they're marking it wrong
because of what it actually SAYS, not because they couldn't understand it.

`_handle_validation_error` looks at WHICH of these two situations actually happened by checking
each error's own `"type"` field — Pydantic secretly tells you which case you're in.
`"type": "json_invalid"` means "this was the smudged, unreadable page" (400). Anything else —
`"missing"`, `"greater_than"`, and so on — means "I read this fine, but something you wrote is
wrong" (422).

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| a smudged, unreadable page | a request body that fails to parse as JSON at all |
| "I can't even read this" | `error["type"] == "json_invalid"` |
| a perfectly readable page with a wrong answer | valid JSON with a field that fails its own rule |
| "I read it fine, but this answer is wrong" | `error["type"]` is `"missing"`, `"greater_than"`, etc. |
| handing back an unreadable page immediately | status 400 |
| marking specific answers wrong on a readable page | status 422, with each wrong field named in the message |

### 2. Why here specifically
The task's own explicit ask named all three status codes (400, 500, 504) individually — treating
them as interchangeable would ignore that they were named separately on purpose. Engineering the
real distinction, rather than just remapping everything to one code, is what makes the resulting
API genuinely informative to a caller trying to figure out whether THEY made a syntax mistake
(400 — fix your JSON) or a content mistake (422 — fix this specific field).

### What breaks if this changes
- **Check `isinstance` or count of errors instead of the `type` field:** a single missing-field
  error and a single JSON-parse error both produce exactly ONE entry in `errors()` — length alone
  can't distinguish them; only the `type` string actually names which case occurred.
- **Assume `type == "json_invalid"` only ever appears alone:** verified in `d2`'s own case 1 — it
  does appear as the SOLE error when the body can't parse — but the defensive `if errors and
  all(...)` check (not just `errors[0]["type"] == ...`) protects against a hypothetical mixed batch,
  even though nothing in this app currently produces one.
- **Trust `RequestValidationError.errors()`'s exact keys to be stable across Pydantic versions
  without checking:** this project's own `d2` output and the earlier empirical check (done before
  writing any handler) are what PROVED the real shape — not Pydantic's changelog, not assumption.

### [Interview relevance: high — correctly distinguishing 400 from 422 (and more broadly, knowing what each 4xx status code specifically promises a caller) is a frequently-tested API-design fundamental, and demonstrating that you verified a framework's default behavior empirically rather than assuming it is exactly this project's own established methodology]

**Interview question:** *"What's the difference between a 400 and a 422 response, and when would
you use each?"*
**A strong answer covers:** 400 Bad Request means the server couldn't even parse/understand the
request's basic structure (malformed JSON, wrong content type); 422 Unprocessable Content means
the request WAS understood, but its contents violate a business or validation rule (a required
field missing, a value out of range); many frameworks' DEFAULTS blur this distinction (FastAPI
returns 422 for both by default) — a careful API author checks what their framework's validation
error actually reports (here, Pydantic's own `errors()[i]["type"]`) to engineer the real split
rather than assuming the framework already draws it correctly.

### 3. Minimal code that explains the concept
The complete script, [`d2_status_code_semantics.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d2_status_code_semantics.py>) (run from the project root):
```python
# Minimal demo of C3: building the real 400 (malformed JSON) vs 422 (well-formed, invalid)
# split from RequestValidationError.errors()'s own real structure -- and printing that raw
# structure so the distinction is seen directly, not just asserted.
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field


class Item(BaseModel):
    name: str = Field(..., min_length=1)
    count: int = Field(default=1, gt=0)


app = FastAPI()


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request, exc: RequestValidationError):
    errors = exc.errors()
    print(f"  raw errors(): {errors}")
    if errors and all(e["type"] == "json_invalid" for e in errors):
        # every reported error is a parse failure -- the request itself is malformed
        return JSONResponse(status_code=400, content={"error": {"code": "invalid_json"}})
    message = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)
    return JSONResponse(
        status_code=422, content={"error": {"code": "validation_error", "message": message}}
    )


@app.post("/items")
def create_item(item: Item):
    return {"ok": True}


client = TestClient(app)

print("case 1: malformed JSON body (not parseable at all)")
r = client.post("/items", content=b"{oops", headers={"content-type": "application/json"})
print(f"  -> {r.status_code} {r.json()}\n")

print("case 2: valid JSON, missing required field")
r = client.post("/items", json={"count": 5})
print(f"  -> {r.status_code} {r.json()}\n")

print("case 3: valid JSON, a field value that fails its own rule")
r = client.post("/items", json={"name": "x", "count": -1})
print(f"  -> {r.status_code} {r.json()}\n")

print("case 4: fully valid")
r = client.post("/items", json={"name": "x", "count": 1})
print(f"  -> {r.status_code} {r.json()}")
```
Real output:
```
case 1: malformed JSON body (not parseable at all)
  raw errors(): [{'type': 'json_invalid', 'loc': ('body', 1), 'msg': 'JSON decode error', 'input': {}, 'ctx': {'error': 'Expecting property name enclosed in double quotes'}}]
  -> 400 {'error': {'code': 'invalid_json'}}

case 2: valid JSON, missing required field
  raw errors(): [{'type': 'missing', 'loc': ('body', 'name'), 'msg': 'Field required', 'input': {'count': 5}}]
  -> 422 {'error': {'code': 'validation_error', 'message': 'body.name: Field required'}}

case 3: valid JSON, a field value that fails its own rule
  raw errors(): [{'type': 'greater_than', 'loc': ('body', 'count'), 'msg': 'Input should be greater than 0', 'input': -1, 'ctx': {'gt': 0}}]
  -> 422 {'error': {'code': 'validation_error', 'message': 'body.count: Input should be greater than 0'}}

case 4: fully valid
  -> 200 {'ok': True}
```
The raw `errors()` output makes the whole design obvious at a glance: `"type": "json_invalid"` is
a genuinely different STRING from `"type": "missing"` or `"type": "greater_than"` — the handler's
`if` check is really just reading a label Pydantic already attached.

### The exact code I wrote (`src/api/main.py`)
> **Lines 91-109:**
> ```python
> @app.exception_handler(RequestValidationError)
> async def _handle_validation_error(
>     request: Request, exc: RequestValidationError
> ) -> JSONResponse:
>     errors = exc.errors()
>     if errors and all(error["type"] == "json_invalid" for error in errors):
>         return JSONResponse(
>             status_code=400,
>             content=_error_envelope("invalid_json", "the request body is not valid JSON"),
>         )
>     message = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)
>     return JSONResponse(status_code=422, content=_error_envelope("validation_error", message))
> ```

**🎯 Interview-relevant lines:** line 101's `if errors and all(...)` — the `errors and` guard
specifically prevents an EMPTY list from vacuously satisfying `all(...)` (Python's `all([])` is
`True`) and wrongly returning 400 for a case with zero actual errors — a small, easy-to-miss
defensive detail.

### Canonical docs
- [MDN: 400 Bad Request](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400)
- [MDN: 422 Unprocessable Content](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/422)

### Micro-exercises
1. In `d2`, send a request with the WRONG `Content-Type` header (e.g. `text/plain`) but a valid
   JSON body. What `errors()` shape comes back, if any? Does it hit the 400 or 422 branch?
2. Add a THIRD field to `Item`, `tags: list[str]`, and send `"tags": "not-a-list"` (a string, not
   a list). Print the raw `errors()` entry this produces — what's its `"type"`?
3. Modify case 1's malformed body to be valid JSON but not an OBJECT at all (e.g. `b"[1,2,3]"`).
   Is this a `json_invalid` error, or something else? Which status code does it get under the
   current handler, and does that feel like the RIGHT status code for this specific case?
4. Trigger TWO simultaneous field errors (like the real `test_multiple_field_errors_are_joined_into_one_message`
   test does) and confirm the `"; ".join(...)` logic produces one readable string naming both.
5. Research the HTTP status code `415 Unsupported Media Type`. Would it be a MORE precise code
   than 400 for micro-exercise 1's wrong-Content-Type case, if FastAPI's default behavior doesn't
   already produce it?

**How it works internally.** Pydantic's own validation engine (`pydantic-core`, a compiled Rust
library) attaches a specific, stable `"type"` string to every validation error it produces — these
strings are part of Pydantic's own documented error taxonomy, not something this project invented.
`RequestValidationError` (FastAPI's own wrapper) just forwards Pydantic's `.errors()` output
through unchanged, which is why reading it directly (rather than guessing) was the right first
step.

### How to validate this yourself
`tests/test_errors.py`'s own `test_malformed_json_body_returns_400_with_the_structured_envelope`
and `test_a_missing_required_field_still_returns_422_not_400` assert the exact status codes for
both real cases — a regression on this exact distinction ever collapsing back together.

### How to visualize this
Draw a fork in the road right after "a request body arrives." Label the fork's question
"did this parse as JSON at all?" One branch, short, leads straight to a box labeled "400." The
other branch leads to a SECOND box, "does every field pass its own rule?" — which itself forks
into "yes → 200" and "no → 422, naming which fields failed."

**You should now be able to explain:** the real semantic difference between 400 and 422, and how
to engineer that distinction from a validation framework whose own default doesn't draw it.

---

## C4 — `TestClient(raise_server_exceptions=...)`

### Do this first
Run [`d3_testclient_raise_server_exceptions.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d3_testclient_raise_server_exceptions.py>).
**Predict first:** three routes raise exceptions — one with NO handler registered anywhere, one
with a handler for its OWN specific class, one with no specific handler (falling back to a bare
`Exception` handler). Using the DEFAULT `TestClient(app)`, do you expect all three calls to behave
the same way, or do you expect one of them to behave differently from the other two?

### 1. Definition + analogy
**What it is.** `TestClient` (used by every test file in this project) has a constructor parameter,
`raise_server_exceptions`, defaulting to `True`. When `True`, if the ASGI app's own
`ServerErrorMiddleware` re-raises an exception (C2's finding — this happens for the bare
`Exception`/500 case, and for anything with NO handler at all), `TestClient` re-raises it AGAIN,
directly into the calling test's own code — even if a perfectly valid HTTP response was already
produced and "sent." Setting it to `False` suppresses this, letting the test just inspect the
response object normally, the way a real deployed client always would.

**Analogy.** A driving instructor sitting in the passenger seat with their OWN brake pedal. By
default, the instructor will slam their brake (interrupting the lesson entirely) the instant the
student does anything risky — even if the student would have actually recovered fine on their own.
Turning this "instructor brake" off lets the student's own actual driving be observed and judged
on its own outcome, exactly as a real road would judge it — not an artificially strict simulation.

### For a 14-year-old
Imagine you're testing a vending machine you built. Most of the time, if someone presses a button
and the right snack drops, you just check "did the right snack come out?" and move on. But imagine
your TEST RIG has a special, extra-strict setting: if the machine EVER does anything even slightly
unusual internally — even something it already recovered from and handled correctly, like a coin
briefly getting stuck before popping back out — the test rig stops everything and yells "ERROR!"
at you immediately, rather than just checking whether the right snack actually came out in the end.

That's `raise_server_exceptions=True`, the default: an extra-strict testing mode that treats
certain internal events as automatic test failures, even when the app already produced a
perfectly correct response. Turning it OFF (`raise_server_exceptions=False`) makes your test rig
behave like a REAL customer would: it just looks at what actually came out of the machine (the
HTTP response), not what happened internally along the way.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the vending machine's snack actually dropping correctly | a real, valid `JSONResponse` already sent |
| the extra-strict test rig yelling "ERROR!" anyway | `TestClient`'s re-raised exception reaching the test |
| a real customer, who only sees the snack come out | a real deployed client, which never sees this re-raise at all |
| turning the extra-strict mode off | `TestClient(app, raise_server_exceptions=False)` |

### 2. Why here specifically
This project's very first attempt to test `_handle_unexpected_error` failed — not because the
handler was wrong, but because of this exact setting, interacting exactly as C2 predicts (only the
bare-`Exception`-registered handler triggers the re-raise `TestClient` then surfaces). Recognizing
this as a TEST HARNESS behavior, not a handler bug, is what let the fix be a one-line test-client
change instead of (wrongly) rewriting working handler code.

### What breaks if this changes
- **Leave `raise_server_exceptions` at its default for a test specifically checking a 500
  handler's OWN response body:** the test crashes with the raw exception instead of ever reaching
  the assertion — this is EXACTLY what happened on this step's first real test run.
- **Set `raise_server_exceptions=False` everywhere, unconditionally, project-wide:** would hide a
  genuinely different class of bug — an exception that escapes ALL registered handling entirely
  due to a real application bug (not this project's current state, but a real risk) would now
  silently become "just another response" instead of loudly failing the test suite, which is
  exactly the opposite of what a test suite should do for truly un-caught errors.
- **Assume the specific-handler routes (`/raise-specific` in `d3`) also need this setting:** they
  don't — `d3`'s own real output shows `/raise-specific` returns a clean response under BOTH
  client configurations, because its handler routes through `ExceptionMiddleware`, which never
  re-raises regardless of this setting at all.

### [Interview relevance: high — understanding that a test HARNESS can have its own configurable behavior that changes what "the test passed/failed" even means is a genuinely useful debugging instinct, applicable well beyond this one framework]

**Interview question:** *"A test for your error-handling code fails with the raw exception instead
of a normal assertion failure. What would you check?"*
**A strong answer covers:** whether the test client/harness itself has a setting controlling
whether server-side exceptions are re-raised into the test versus only reflected in the response
object — many frameworks' test clients default to the stricter, exception-surfacing behavior
specifically to catch unintentional bugs during ordinary (non-error-path) testing, which can make
testing an ERROR PATH on purpose look like a crash; the fix is usually a test-client configuration
change (like `TestClient(app, raise_server_exceptions=False)`), not a change to the code under test
— confirmed by checking whether the response itself (inspected via that relaxed setting) is
actually correct.

### 3. Minimal code that explains the concept
The complete script, [`d3_testclient_raise_server_exceptions.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d3_testclient_raise_server_exceptions.py>) (run from the project root):
```python
# Minimal demo of C4: TestClient's raise_server_exceptions parameter (default True) changes
# what "the test failed" even means -- and, combined with C2's finding, explains exactly why
# THIS project's own 500-handler test failed on its first real run.
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/no-handler-at-all")
def no_handler_at_all():
    raise RuntimeError("nothing anywhere catches this")
    # zero custom exception handlers registered in this whole app -- the most basic case


@app.exception_handler(ValueError)
async def handle_value_error(request, exc):
    return JSONResponse({"caught": "ValueError"}, status_code=500)


@app.exception_handler(Exception)
async def handle_anything(request, exc):
    return JSONResponse({"caught": "Exception"}, status_code=500)


@app.get("/raise-specific")
def raise_specific():
    raise ValueError("a SPECIFIC, registered failure")


@app.get("/raise-unregistered")
def raise_unregistered():
    raise RuntimeError("only the bare Exception handler can catch this one")


print("=== TestClient(app)  -- the default, raise_server_exceptions=True ===")
client_default = TestClient(app)

for path in ("/no-handler-at-all", "/raise-specific", "/raise-unregistered"):
    try:
        r = client_default.get(path)
        print(f"{path:24s} -> clean response: {r.status_code} {r.json()}")
    except Exception as e:
        print(f"{path:24s} -> RAISED into the caller: {type(e).__name__}: {e}")

print("\n=== TestClient(app, raise_server_exceptions=False) ===")
client_quiet = TestClient(app, raise_server_exceptions=False)

for path in ("/no-handler-at-all", "/raise-specific", "/raise-unregistered"):
    r = client_quiet.get(path)
    print(f"{path:24s} -> clean response: {r.status_code} {r.json()}")
```
Real output:
```
=== TestClient(app)  -- the default, raise_server_exceptions=True ===
/no-handler-at-all       -> RAISED into the caller: RuntimeError: nothing anywhere catches this
/raise-specific          -> clean response: 500 {'caught': 'ValueError'}
/raise-unregistered      -> RAISED into the caller: RuntimeError: only the bare Exception handler can catch this one

=== TestClient(app, raise_server_exceptions=False) ===
/no-handler-at-all       -> clean response: 500 {'caught': 'Exception'}
/raise-specific          -> clean response: 500 {'caught': 'ValueError'}
/raise-unregistered      -> clean response: 500 {'caught': 'Exception'}
```
`/raise-specific` is identical under BOTH configurations — direct, empirical proof that this
setting only matters for the `Exception`/500/no-handler cases C2 identified, never for a
specifically-registered handler.

### The exact code I wrote (`tests/test_errors.py`)
> ```python
> client = TestClient(app, raise_server_exceptions=False)
> # raise_server_exceptions=False, not TestClient(app)'s own default of True -- verified while
> # writing these tests: a handler registered for the literal Exception class is routed through
> # Starlette's ServerErrorMiddleware, which re-raises the original exception AFTER sending the
> # handler's own response ...
> ```

**🎯 Interview-relevant lines:** this is a ONE-LINE fix for what initially looked like a real bug
in `_handle_unexpected_error` — the comment directly beside it is what keeps a future reader from
"fixing" the working handler instead, if this test is ever touched again without this context.

### Canonical docs
- `starlette/testclient.py` (the installed package — `www.starlette.io`'s own docs site could not
  be reached from this environment during this step; see C2's own citation note).
- [FastAPI: Handling Errors](https://fastapi.tiangolo.com/tutorial/handling-errors/)

### Micro-exercises
1. In `d3`, add a FOURTH route that raises a handled `ValueError` but where the handler itself
   ALSO raises a new exception internally (a bug in the handler). Which `TestClient` setting
   surfaces THIS failure, and why does that make sense given C2's own routing logic?
2. Check `httpx`/`starlette`'s installed version's own `TestClient.__init__` signature directly
   (`inspect.signature`) and confirm `raise_server_exceptions`'s literal default value matches
   what this section claims.
3. Write a test using `pytest.raises(RuntimeError)` around a call to `/no-handler-at-all` via the
   DEFAULT client. Does this pass? What does that tell you about `raise_server_exceptions=True`
   being sometimes exactly what you WANT (catching a genuinely un-handled bug loudly)?
4. Predict, then verify: does `raise_server_exceptions` affect `response.status_code` for the
   cases where NO exception is raised into the caller either way (i.e. does it change anything
   about `/raise-specific`'s behavior at all, ever)?
5. Read this project's own `tests/test_chat.py` — none of its tests needed
   `raise_server_exceptions=False`. Explain why, referencing P7-M2's own C5 finding (from
   [docs/35](<35 chat_streaming_deep_dive.md>)) about WHERE `/chat`'s exceptions are actually caught.

**How it works internally.** `TestClient` wraps the real ASGI app call in its own exception
handling: when `raise_server_exceptions=True` and the app raises (rather than just returning a
response), `TestClient` catches it, still constructs whatever partial response info it can, AND
re-raises the original exception from within its own request-sending method — which is exactly
what a Python test function calling `client.get(...)` observes as "the call itself raised."

### How to validate this yourself
`d3`'s own side-by-side comparison, run under BOTH settings against the SAME three routes, is the
validation — the asymmetry (two routes behave differently across settings, one doesn't) is exactly
what C2's structural finding predicts, confirmed behaviorally here rather than just structurally.

### How to visualize this
Draw `TestClient` as a box with a single toggle switch on its side, labeled
"raise_server_exceptions." Draw two parallel paths leaving `ServerErrorMiddleware`: one, always
active, carries the real HTTP response back to `TestClient`. The second, only active when a
RE-RAISE happens (C2's cases) AND the toggle is `True`, carries the exception itself past
`TestClient` and into the calling test code, drawn as a path that bypasses the normal response
entirely.

**You should now be able to explain:** why a test for a 500 handler can fail even when the handler
is correct, and specifically which `TestClient` setting to reach for — and why it only matters for
some registrations, not all of them.

---

## C5 — An absent timeout vs. a disabled one

### Do this first
Run [`d4_timeout_none_vs_default.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d4_timeout_none_vs_default.py>).
**Predict first:** `httpx.Client()` (no timeout argument at all) and `httpx.Client(timeout=None)`
(explicitly passed `None`) both involve "no timeout value typed in." Do you expect them to produce
the SAME resulting timeout configuration, or different ones?

### 1. Definition + analogy
**What it is.** In httpx (and libraries built on it, like `ollama`), OMITTING the `timeout`
argument entirely triggers a library-chosen default (`Timeout(timeout=5.0)` — a real, finite
bound). But explicitly passing `timeout=None` is a DIFFERENT instruction: "disable timeouts
entirely" — connect, read, write, and pool all become unbounded. These look similar in casual
reading ("no specific number was given either way") but are opposite in effect.

**Analogy.** Two ways to leave a form field blank. On some forms, leaving a field EMPTY means "use
our standard default" (a restaurant reservation with no party size specified defaults to a table
for two). But writing the WORD "none" in that same field is a completely different, explicit
instruction — "I am telling you there is no limit," which a restaurant might read as "seat
whatever comes."

### For a 14-year-old
Imagine setting a timer for how long you're allowed to play a video game. If you just don't touch
the timer setting at all, the game console has its OWN default — say, it automatically stops you
after 2 hours, because that's the sensible built-in limit. But imagine there's ALSO a specific
option you can select called "No Limit" — picking that isn't the same as "I didn't set a timer," it's
an active, deliberate choice that the console will NEVER stop you, no matter how long you play.
Both might look like "I didn't type in a specific number of minutes" if you're skimming quickly —
but one still protects you with a sensible default, and the other one genuinely, actively removes
all protection.

`Embedder`'s old code didn't SAY "None" out loud anywhere in `embedder.py` itself — it just never
passed a `timeout=` argument to `ollama.Client(...)` at all. But `ollama.Client`'s OWN internal
default for that argument (checked directly, not guessed) turned out to already BE `None` — so
omitting it was secretly the same as actively selecting "No Limit." It looked like "I didn't set a
timer," but it actually meant "the console will never, ever stop me."

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the console's own sensible built-in default (2 hours) | `httpx.Client()`'s own default, `Timeout(timeout=5.0)` |
| selecting "No Limit" on purpose | `httpx.Client(timeout=None)`, explicitly |
| not touching the timer setting, assuming the default applies | `ollama.Client(host=...)`, no `timeout=` passed |
| discovering the console's OWN default for an untouched setting was secretly "No Limit" | checking `ollama.BaseClient.__init__`'s own default, finding `timeout: Any = None` |
| setting an actual, deliberate time limit | `EMBED_TIMEOUT_SECONDS = 60.0`, passed explicitly |

### 2. Why here specifically
`Embedder.embed_query` runs on the very first step of every real `/chat` request (query
embedding, before retrieval can even begin). An unbounded call here doesn't just fail slowly — it
can hang a worker thread FOREVER, with nothing anywhere ever timing it out, which is a materially
worse failure mode than even a too-short timeout (which at least fails fast and visibly).

### What breaks if this changes
- **Assume "no timeout kwarg passed" always means "the library's sensible default applies":**
  `d4`'s own real output disproves this for `ollama.Client` specifically — its own default for
  that parameter is `None`, not "defer to httpx."
- **Fix this by adding `timeout=5.0` (httpx's OWN default) instead of a value sized for this
  project's real workload:** would reintroduce the EXACT class of bug `GENERATION_TIMEOUT_SECONDS`
  already fixed once for `LocalLLM` (P6-M6) — a timeout borrowed from somewhere else, not derived
  from this call's own real, measured latency (0.701s for `embed_query`, 1.005s for a full batch).
- **Check only the function signature's default, not the REAL constructed client:** `d4`'s final
  section deliberately inspects `real_embedder_client._client.timeout` — the ACTUAL underlying
  httpx client this project's code builds — rather than stopping at "the signature says `None`,"
  since a wrapping library could in principle transform a default before using it (this one
  doesn't, but checking the real object rather than the declared default is the more rigorous habit).

### [Interview relevance: high — distinguishing "a sensible default because none was specified" from "an explicit instruction that happens to look similar" is a general debugging skill (the same class of bug as confusing a function's default parameter value with `None` meaning something semantically different, e.g. pandas' `inplace=None` vs a real default), not specific to HTTP clients]

**Interview question:** *"You find a network client in a codebase with no explicit timeout
configured. What do you check before assuming it's 'using a sensible default'?"*
**A strong answer covers:** checking the SPECIFIC library's own documented (or source-verified)
behavior for an omitted timeout argument — some libraries substitute a real, finite default
(httpx's own bare `Client()`, 5 seconds), while others (like `ollama.Client`, checked directly in
this case) have their OWN default that happens to BE `None`, which many underlying HTTP libraries
treat as "disable timeouts entirely," not "defer to my own default"; the only reliable way to know
is reading the actual library source or testing the real constructed object's own resulting
configuration, not assuming based on how the code LOOKS (an omitted argument looking the same as
"a default applies" regardless of which specific behavior actually results).

### 3. Minimal code that explains the concept
The complete script, [`d4_timeout_none_vs_default.py`](<../practice codes/phase 7/m3_error_handling_deep_dive/d4_timeout_none_vs_default.py>) (run from the project root):
```python
# Minimal demo of C5: an explicitly-absent timeout kwarg is NOT the same as httpx's own
# sensible default -- ollama.Client(host=...) with no timeout= passes timeout=None straight
# through to httpx, and None means "disabled", not "use the normal default."
import httpx

print("httpx.Client() -- no timeout argument at all:")
print(" ", httpx.Client().timeout)

print("\nhttpx.Client(timeout=None) -- EXPLICITLY passed None:")
c = httpx.Client(timeout=None)
print(" ", c.timeout)
print(f"   connect={c.timeout.connect} read={c.timeout.read} write={c.timeout.write} pool={c.timeout.pool}")

print("\nhttpx.Client(timeout=60.0) -- an explicit, real bound:")
print(" ", httpx.Client(timeout=60.0).timeout)

print("\nollama.Client(host=...) with no timeout kwarg -- what this project's own Embedder")
print("used to do, before EMBED_TIMEOUT_SECONDS was added. ollama.Client stores its real")
print("underlying httpx.Client as `._client` (src: ollama/_client.py Client.__init__ calls")
print("`super().__init__(httpx.Client, host, **kwargs)`), so the REAL, constructed client's")
print("own timeout can be read directly, not just a function signature's default value:")
import ollama

real_embedder_client = ollama.Client(host="http://localhost:11434")
print(" ", real_embedder_client._client.timeout)
print("  -> exactly the same Timeout(timeout=None) as the explicit case above: no timeout at")
print("     all, not httpx's own 5s default -- this is what every /chat request embedded its")
print("     query through, until EMBED_TIMEOUT_SECONDS fixed it (decision.md D-2026-09-30-25)")
```
Real output:
```
httpx.Client() -- no timeout argument at all:
  Timeout(timeout=5.0)

httpx.Client(timeout=None) -- EXPLICITLY passed None:
  Timeout(timeout=None)
   connect=None read=None write=None pool=None

httpx.Client(timeout=60.0) -- an explicit, real bound:
  Timeout(timeout=60.0)

ollama.Client(host=...) with no timeout kwarg -- what this project's own Embedder
used to do, before EMBED_TIMEOUT_SECONDS was added. ollama.Client stores its real
underlying httpx.Client as `._client` (src: ollama/_client.py Client.__init__ calls
`super().__init__(httpx.Client, host, **kwargs)`), so the REAL, constructed client's
own timeout can be read directly, not just a function signature's default value:
  Timeout(timeout=None)
  -> exactly the same Timeout(timeout=None) as the explicit case above: no timeout at
     all, not httpx's own 5s default -- this is what every /chat request embedded its
     query through, until EMBED_TIMEOUT_SECONDS fixed it (decision.md D-2026-09-30-25)
```
The middle two blocks are the crux: `httpx.Client()` (nothing passed) and
`httpx.Client(timeout=None)` (explicitly passed) produce VISIBLY different `Timeout` objects —
`Timeout(timeout=5.0)` versus `Timeout(timeout=None)` — proving these are genuinely different
instructions, not two spellings of the same thing.

### The exact code I wrote (`src/embedding/embedder.py`)
> ```python
> EMBED_TIMEOUT_SECONDS = 60.0
> # a real embed_query call measured at 0.701s and a real 16-text embed_batch call measured
> # at 1.005s, both against this project's own warm, already-loaded Ollama (2026-09-30) ...
> # Previously the client was built with no timeout kwarg at all, which ollama.Client passes
> # through as timeout=None -- confirmed via httpx.Client(timeout=None).timeout to mean
> # connect/read/write/pool all None, i.e. no timeout whatsoever, not httpx's own 5s default
>
> def build_ollama_client(ollama_url: str) -> EmbedClient:
>     return ollama.Client(host=ollama_url, timeout=EMBED_TIMEOUT_SECONDS)
> ```

**🎯 Interview-relevant lines:** the fix is a SINGLE added keyword argument
(`timeout=EMBED_TIMEOUT_SECONDS`) — the entire bug was an ABSENCE, not a wrong value anywhere, which
is exactly why it was easy to miss on a normal code read and needed a direct, measured check to surface.

### Canonical docs
- [httpx: Timeouts](https://www.python-httpx.org/advanced/timeouts/)
- [httpx: Exceptions](https://www.python-httpx.org/exceptions/)

### Micro-exercises
1. In `d4`, construct `httpx.Client(timeout=httpx.Timeout(connect=2.0, read=60.0, write=10.0, pool=5.0))`
   — httpx's own per-phase timeout control. Print its `.timeout` and explain when you'd want
   read and connect timeouts to differ (hint: think about `RAGPipeline.stream()`'s own long,
   slow-to-finish-but-fast-to-start real generation calls).
2. Check `qdrant_client.QdrantClient`'s own constructor (used by this project's
   `build_qdrant_client`) for its default timeout behavior, the same way `d4` checked `ollama`'s.
   Does it share the same "omitted means None" risk, or something different?
3. Check `neo4j.GraphDatabase.driver`'s own timeout-related parameters (this project's
   `build_driver` already sets `connection_timeout=5`). Is there a SEPARATE parameter for how long
   an individual QUERY (not just the initial connection) is allowed to run? Would a slow Cypher
   query currently be bounded by anything at all?
4. Time how long `EMBED_TIMEOUT_SECONDS = 60.0` gives as a MULTIPLE of the real measured
   `embed_query` latency (0.701s). Compare that ratio to `GENERATION_TIMEOUT_SECONDS`'s own ratio
   over ITS real measured latency (108.3s, per `notes/step-6.6-rag-integration-tests.md`). Which
   is more generous, and can you think of a reason embedding deserves a LARGER safety margin than
   generation does (hint: cold-start / model-load variance)?
5. Write a tiny script that calls `time.sleep` inside a thread with a real `httpx.Client(timeout=0.001)`
   pointed at something slow (or simply a deliberately unroutable address) to observe a REAL
   `httpx.TimeoutException` firing — the same exception type `_handle_timeout` (C1) is registered
   to catch.

**How it works internally.** httpx's `Timeout` object has four independent phases — `connect`,
`read`, `write`, `pool` (waiting for a free connection in the pool) — each can be set separately,
or all at once via a single number. Passing `None` for the single-number form sets ALL FOUR to
`None`, which httpx's own transport layer interprets as "never time out this phase," genuinely
removing the operating-system-level socket timeout that would otherwise bound the call.

### How to validate this yourself
`d4`'s own final block reads `real_embedder_client._client.timeout` — the literal, real object
this project's actual `build_ollama_client` used to construct, not a simulation of it — making
this the strongest possible confirmation the fix targets a genuinely real object, not a
hypothetical one.

### How to visualize this
Draw three horizontal number lines, all starting at 0 seconds. On the first (httpx's own default),
mark a solid stop sign at 5. On the second (`timeout=None`), draw the line trailing off with an
arrow into infinity — no stop sign at all. On the third (`EMBED_TIMEOUT_SECONDS`), mark a stop sign
at 60, with a small bracket underneath spanning 0 to ~1 showing where the two REAL measured calls
(0.701s, 1.005s) actually landed — visibly tiny compared to the new bound.

**You should now be able to explain:** why "no timeout argument was passed" is not, by itself,
enough information to know whether a call is safely bounded, and why checking the REAL resulting
configuration (not just reading the call site) is the only reliable way to know.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Start the real server and send a genuinely malformed request.**
   ```bash
   .venv/Scripts/python -m uvicorn src.api.main:app --port 8000
   ```
   ```bash
   curl -i -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{bad json'
   ```
   Confirm a real `400` with the `{"error": {"code": "invalid_json", ...}}` body (C3) — and notice
   the server process itself does NOT crash or log a traceback for this one, since it's a routine,
   expected validation failure, not the `Exception` catch-all's own path (C2).
2. **Send a well-formed-but-invalid request.**
   ```bash
   curl -i -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"k": 0}'
   ```
   Confirm a real `422`, with BOTH the missing `query` and the bad `k` named in one joined message
   (C3's own `"; ".join(...)` logic, seen live).
3. **Trigger the 500 handler for real, not just via a test-only route.** Temporarily comment out
   `app.state.rag = RAGPipeline(...)` in `lifespan` (or stop Qdrant/Neo4j/Ollama before starting
   the server so `lifespan` itself fails) and send a normal, valid `/chat` request. Does the server
   even finish starting? What does this tell you about `lifespan` failures versus per-request 500s
   (hint: revisit [docs/35](<35 chat_streaming_deep_dive.md>) C1)?
4. **Watch a real embedding call happen well within its new bound.** With everything running
   normally, send a real, valid `/chat` request and watch the server's own logs — no
   `upstream timeout` warning should appear (C5), since `EMBED_TIMEOUT_SECONDS = 60.0` comfortably
   covers the real, measured ~0.7s `embed_query` call every request makes.
5. **Run the full test file and connect each test back to a concept above.**
   ```bash
   .venv/Scripts/python -m pytest tests/test_errors.py -v
   ```
   For each of the 8 tests, name which of C1-C5 it's really proving — and for
   `test_chat_mid_stream_timeout_is_still_handled_in_stream_not_by_the_new_504_handler`
   specifically, explain in your own words why THIS test needed no `raise_server_exceptions=False`
   at all, tracing back through C2 and [docs/35](<35 chat_streaming_deep_dive.md>)'s own C5 to say why.

If you can explain step 3's answer precisely — using `lifespan`'s own real behavior from
[docs/35](<35 chat_streaming_deep_dive.md>) C1, not a guess — you've connected this step's new
material to the previous one correctly, which is the real test of whether either was understood
rather than just completed.
