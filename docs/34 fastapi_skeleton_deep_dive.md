# 34 — FastAPI App Skeleton Deep Dive (P7-M1)

> Deconstructs [`src/api/main.py`](../src/api/main.py) — a small, already-existing file (present
> since before Phase 7 was asked for, tested by [`tests/test_health.py`](../tests/test_health.py))
> that is nonetheless doing real, specific work: creating a real ASGI application, registering a
> real route, and reporting real configuration rather than a bare "hello world." Demo scripts live
> in [`practice codes/phase 7/m1_fastapi_skeleton_deep_dive/`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/>)
> and need no server or Docker at all — every one runs the real `FastAPI`/`TestClient` machinery
> in-process. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d0_app_instance.py"
> ```

---

## 0. Plain-language summary

```python
app = FastAPI(title="Advance RAG", version="0.1.0")   # 1. a real object, nothing runs yet

@app.get("/health")                                    # 2. register GET /health -> health()
def health() -> dict:
    return {"status": "ok", "qdrant_url": ..., ...}     # 3. a plain dict, auto-turned into JSON
```
Nineteen lines, and every one of them is doing real, nameable work. Five concepts, in the order
this file actually uses them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | The app instance as a real ASGI object | 🔴 high | `app = FastAPI(...)` |
| C2 | Path operation decorators | 🔴 very high | `@app.get("/health")` |
| C3 | Automatic response serialization | 🟡 high | `return {...}` |
| C4 | The ASGI server relationship (app vs. server) | 🔴 very high | implicit — what actually runs this file |
| C5 | The health-check design pattern | 🟡 medium-high | what `health()` actually returns |

---

## C1 — The app instance as a real ASGI object

### Do this first
Run [`d0_app_instance.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d0_app_instance.py>).
**Predict first:** `app = FastAPI(...)` is called, but no server is started and no request is
made. Do you expect `app.routes` to be completely empty, or to already contain something?

### 1. Definition + analogy
**What it is.** `FastAPI()` doesn't start a server, open a socket, or do anything "live" — it
constructs an ordinary Python object (an instance of the `FastAPI` class) that happens to
implement the ASGI interface (it's `callable`, and calling it the right way is how HTTP requests
eventually get handled). Everything else — routes, docs, middleware — is just data stored on that
one object, added incrementally.

**Analogy.** Buying a restaurant's kitchen equipment and hanging a sign doesn't mean customers can
walk in and order food yet — the building exists, real and inspectable, but nothing "opens" until
someone unlocks the doors and starts seating people. `FastAPI()` is buying the equipment and
hanging the sign; a server (C4) is unlocking the doors.

### For a 14-year-old
Imagine you build a vending machine in your garage. You bolt on the coin slot, wire up the
buttons, load a few snacks into their slots — the machine is COMPLETE and real, sitting right
there. But it's in your garage. Nobody can buy anything from it yet, because it isn't PLUGGED IN
and it isn't standing somewhere people can actually walk up to it. Building the machine and
placing it somewhere people can use it are two completely different steps.

`app = FastAPI(title="Advance RAG", version="0.1.0")` is building the vending machine. It's a
real, complete Python object sitting in memory the instant this line runs — you can inspect it,
check its `.title`, look at what's loaded into it. But nothing on the internet can reach it yet.
That only happens once a SEPARATE program (`uvicorn`, C4) picks up this exact object and "plugs it
in" — opens a real network port and starts handing it real requests.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| bolting together the vending machine in the garage | `app = FastAPI(title="Advance RAG", version="0.1.0")` |
| the machine's coin slot and buttons, already wired | `app`'s internal route table, `.routes` |
| snacks already loaded before anyone can buy them | FastAPI's OWN built-in routes (`/docs`, `/openapi.json`) already present at creation |
| the machine sitting in the garage, unreachable | the real Python object, sitting in memory, no network access yet |
| plugging the machine in at a real location | `uvicorn src.api.main:app` actually serving it (C4) |

### 2. Why here specifically
Every FastAPI application — no matter how large it eventually grows — starts with exactly this
one line. `src/api/main.py`'s whole job, per its own docstring ("RAG endpoints ... added in later
steps"), is to be the SINGLE, stable object every later Phase 7 microstep will keep adding routes
to. Getting this first line right (a real, importable `app` object, not a script that only runs
things when executed directly) is what makes `uvicorn src.api.main:app` — and `TestClient(app)` in
`tests/test_health.py` — both possible.

### What breaks if this changes
- **Wrap `app = FastAPI()` inside `if __name__ == "__main__":`:** `uvicorn src.api.main:app` would
  fail immediately — uvicorn needs to `import` the module and find a module-level `app` attribute;
  it doesn't run the file as a script.
- **Create a NEW `FastAPI()` instance inside each route function instead of one shared `app`:**
  every route would live on a DIFFERENT, disconnected app — `/health` and any later `/query` route
  would never appear together in the same running application at all.
- **Skip `title`/`version` entirely:** the app still works identically for real requests — these
  two fields only affect the auto-generated interactive docs (`/docs`), not any actual behavior.

### [Interview relevance: high — understanding that a web framework's "app" is an ordinary object you can inspect, not a hidden, magical global, is the difference between debugging confidently and guessing]

**Interview question:** *"When exactly does a FastAPI/Starlette application start handling real
HTTP traffic?"*
**A strong answer covers:** creating the `FastAPI()` instance only builds an in-memory object
(routes, metadata) — it does not open any socket or start listening; a separate ASGI SERVER
(uvicorn, hypercorn, etc.) is what actually binds a port and starts routing real connections to
that object; and this separation (framework object vs. server process) is exactly what lets the
SAME `app` object be driven two completely different ways in this project already — by a real
server for production traffic, and by `TestClient` for in-process tests with zero network
involved at all.

### 3. Minimal code that explains the concept
The complete script, [`d0_app_instance.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d0_app_instance.py>) (run from the project root):
```python
# Minimal demo of C1: a FastAPI app is a real Python OBJECT, not magic -- it's an ASGI-
# compatible callable you can inspect, just like any other class instance.
from fastapi import FastAPI  # the class whose instances are ASGI applications

app = FastAPI(title="Demo", version="0.1.0")  # creates a real object, nothing runs yet
print("type:", type(app))  # shows it's a real, ordinary Python class instance
print("is callable (ASGI app):", callable(app))  # confirms it implements __call__, like any ASGI app
print("title:", app.title, " version:", app.version)  # reads back the metadata we set
print("registered routes before any @app.get:", [r.path for r in app.routes])
# FastAPI ships a couple of its OWN built-in routes even before we add any (docs, openapi.json)
```
Real output:
```
type: <class 'fastapi.applications.FastAPI'>
is callable (ASGI app): True
title: Demo  version: 0.1.0
registered routes before any @app.get: ['/openapi.json', '/docs', '/docs/oauth2-redirect', '/redoc']
```
Four real routes already exist before a single `@app.get` was written — FastAPI adds its own
interactive documentation routes automatically, the moment the object is created.

### The exact code I wrote (`src/api/main.py`)
> **Lines 1-7:**
> ```python
> """FastAPI entrypoint. RAG endpoints (ingest/query) are added in later steps."""
>
> from fastapi import FastAPI
>
> from src.core.config import settings
>
> app = FastAPI(title="Advance RAG", version="0.1.0")
> ```

**🎯 Interview-relevant lines:** `app` is a MODULE-LEVEL name — this is what lets
`uvicorn src.api.main:app` (a module path plus an attribute name) find it, and what lets
`tests/test_health.py` write `from src.api.main import app` and get the exact same object a real
server would drive.

### Canonical docs
- [FastAPI: First Steps](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [Starlette (the ASGI toolkit FastAPI is built on)](https://github.com/encode/starlette) —
  `FastAPI` is a subclass of Starlette's own `Starlette` application class.

### Micro-exercises
1. In `d0`, add a third print: `print(app.description)`. What's the default value when you never
   set one? Check FastAPI's own docs to see what else `FastAPI(...)` accepts beyond title/version.
2. Create TWO separate `FastAPI()` instances in the same script, give them different titles, and
   print both `app.title`s. Confirm they're genuinely independent objects (`app1 is not app2`).
3. Add `@app.get("/ping")` returning `{"pong": True}` AFTER the `print(app.routes)` line in a copy
   of `d0`. Print `app.routes` again — does the new route appear? What does this prove about WHEN
   routes actually get added to the object?
4. Try `print(app.routes[0])` (not `.path`, the whole object). What TYPE is a single route? Look
   up `starlette.routing.Route` to see what other information it carries besides a path.
5. Import `src.api.main`'s real `app` directly in a Python REPL (`from src.api.main import app`)
   and print `app.title`. Confirm it matches what `uvicorn src.api.main:app` would actually serve.

**How it works internally.** `FastAPI.__init__` (which `FastAPI(...)` calls) stores every
argument you pass as an attribute on the new object, and initializes an internal `routes` list —
starting with a handful of routes FastAPI adds itself for the interactive docs. Nothing about
`__init__` touches the network; it's ordinary object construction, exactly like building any other
Python class instance, just one whose LATER behavior (once driven by a server) happens to be
"answer HTTP requests."

### How to validate this yourself
`tests/test_health.py` itself is the validation: `from src.api.main import app` imports the exact
module-level object, and `TestClient(app)` drives it directly, in-process, with zero real network
socket ever opened — proving the object itself is fully functional independent of any real server.

### How to visualize this
Draw a plain box labelled "app (a FastAPI object, in memory)" with a small list inside it: routes,
title, version. Draw NO arrows leaving the box at all — nothing is connected to anything else yet.
That empty, disconnected picture IS the accurate one, until C4's server enters the diagram.

**You should now be able to explain:** why `app = FastAPI(...)` alone doesn't mean your API is
"running," and what specifically becomes real the instant that line executes versus what doesn't.

---

## C2 — Path operation decorators

### Do this first
Run [`d1_path_operations.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d1_path_operations.py>).
**Predict first:** `hello_name(name: str)` takes a parameter, but nothing in the script ever calls
`hello_name("Ada")` directly. How does the string `"Ada"` end up inside the function at all?

### 1. Definition + analogy
**What it is.** `@app.get("/health")` is an ordinary Python decorator — it takes the function
defined right below it and does two things: registers `("GET", "/health")` in the app's internal
route table pointing at that function, then returns the function completely unchanged so it can
still be called normally (like in `tests/test_health.py`... actually, like anywhere Python code
wants to call `health()` directly). When a real request for `GET /health` arrives later, FastAPI
looks it up in that table and calls the matching function.

**Analogy.** A hotel's mail room has a wall of labelled pigeonholes, one per room number. Sticking
a label on a pigeonhole doesn't deliver any mail right now — it just means that whenever mail
addressed to "Room 204" arrives LATER, the mail room clerk (FastAPI) knows exactly which slot
(function) to put it in.

### For a 14-year-old
Imagine a school's front office has a big board of mail slots, one per teacher, each with a
name label on it. Putting Mr. Chen's name label on slot 7 doesn't deliver any letters right that
second — it's just a RULE for later: "whenever a letter arrives addressed to Mr. Chen, it goes in
slot 7." The actual delivering happens LATER, whenever mail actually shows up, by someone checking
the address on each letter and matching it against the labels on the board.

`@app.get("/health")` is sticking a label on a slot. It says: "whenever a GET request arrives
addressed to `/health`, hand it to THIS function." The decorator itself doesn't run the function —
it just registers the rule. Later, when uvicorn (C4) actually receives a real request for
`GET /health`, FastAPI reads the address, checks it against every label it has, finds the match,
and only THEN calls `health()` for real.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the labelled mail slot board | `app.routes`, FastAPI's internal route table |
| sticking "Mr. Chen" on slot 7, ahead of time | `@app.get("/health")` registering `("GET", "/health") -> health` |
| a letter later arriving addressed to Mr. Chen | a real HTTP request: `GET /health` |
| the clerk matching the address to a slot | FastAPI's own routing logic, matching method+path to a function |
| Mr. Chen actually reading the letter | Python actually calling `health()`, right then |
| `{name}` in `/hello/{name}` | a slot whose label has a BLANK to fill in — "Room ___", matched to any value |

### 2. Why here specifically
`src/api/main.py` needs exactly one thing from FastAPI right now: a way to say "when someone asks
this running server if it's alive, run THIS specific function." The decorator is the entire
mechanism — no routing configuration file, no separate URL-to-function mapping table to keep in
sync by hand; the function definition and its own address are written in the same three lines.

### What breaks if this changes
- **Forget the decorator entirely, just define `def health(): ...`:** the function exists in
  Python, perfectly validly — but FastAPI has no idea it should ever be called for any request.
  `GET /health` would return a real 404, because nothing is registered for that path at all.
- **Use `@app.post("/health")` instead of `.get`:** a real `GET /health` request (what
  `tests/test_health.py`'s `client.get("/health")` sends) would now get a 405 Method Not Allowed —
  the PATH matches, but the METHOD doesn't, and FastAPI treats those as genuinely different rules.
- **Register TWO different functions for the exact same `("GET", "/health")`:** FastAPI doesn't
  merge them — whichever was registered would simply shadow/override in the routing table,
  depending on registration order, a real and confusing bug if it ever happened by accident.

### [Interview relevance: very high — decorators are one of the most commonly tested general Python concepts, and "how does routing actually work under a decorator-based web framework" is a very standard backend interview question]

**Interview question:** *"How does `@app.get('/health')` actually connect a URL to a Python
function? Walk me through what happens at import time versus request time."*
**A strong answer covers:** at IMPORT time (when the module is loaded), the decorator runs once,
registering the (method, path) pair and a reference to the function in the app's internal route
table — the function itself is NOT called yet; at REQUEST time (whenever a real HTTP request
arrives later), the server hands the request to the app, which looks up the method+path against
that table and calls the matching function THEN, with the actual request data; and path
parameters (like `{name}`) work by the router extracting a piece of the URL and passing it as a
real function argument, matched by name.

### 3. Minimal code that explains the concept
The complete script, [`d1_path_operations.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d1_path_operations.py>) (run from the project root):
```python
# Minimal demo of C2: a path operation decorator just REGISTERS a plain function against a
# (method, path) pair -- no magic beyond an ordinary Python decorator.
from fastapi import FastAPI  # the class whose .get()/.post()/etc. are the decorators
from fastapi.testclient import TestClient  # drives the app in-process, no real network needed

app = FastAPI()


@app.get("/hello")  # registers: GET /hello -> call hello_world()
def hello_world():
    return {"message": "hello"}  # a plain Python dict, returned from a plain Python function


@app.get("/hello/{name}")  # a path PARAMETER: {name} becomes a real function argument
def hello_name(name: str):
    return {"message": f"hello {name}"}  # FastAPI extracts "name" from the URL and passes it in


client = TestClient(app)  # drives the ASGI app directly, in-process
print(client.get("/hello").json())  # calls GET /hello for real, through the real app
print(client.get("/hello/Ada").json())  # calls GET /hello/Ada -- "Ada" becomes name="Ada"
print("routes:", [(r.methods, r.path) for r in app.routes if hasattr(r, "methods")])
# shows the real registry: each decorator added one entry, mapping (method, path) -> function
```
Real output:
```
{'message': 'hello'}
{'message': 'hello Ada'}
routes: [({'GET', 'HEAD'}, '/openapi.json'), ({'GET', 'HEAD'}, '/docs'), ({'GET', 'HEAD'}, '/docs/oauth2-redirect'), ({'GET', 'HEAD'}, '/redoc'), ({'GET'}, '/hello'), ({'GET'}, '/hello/{name}')]
```
`hello_name` was never called directly anywhere in this script — `TestClient(app).get("/hello/Ada")`
sent a real (in-process) HTTP request, FastAPI's router matched `/hello/{name}` against
`/hello/Ada`, extracted `"Ada"`, and called `hello_name(name="Ada")` for real.

### The exact code I wrote (`src/api/main.py`)
> **Lines 10-11:**
> ```python
> @app.get("/health")
> def health() -> dict:
> ```

**🎯 Interview-relevant lines:** this route takes NO path parameters and NO request body — the
simplest possible shape a path operation can have — which is exactly appropriate for a health
check (it answers "are you alive," which needs no input at all from the caller).

### Canonical docs
- [FastAPI: Path Parameters](https://fastapi.tiangolo.com/tutorial/path-params/)

### Micro-exercises
1. In `d1`, add `@app.get("/hello/{name}/{times}")` where `times: int` repeats the greeting that
   many times (e.g., `["hello Ada", "hello Ada"]` for `times=2`). Call it via `TestClient` and
   confirm the path segment becomes a real Python `int`, not a string.
   *Hint: FastAPI converts path parameters using the type hint you give them.*
2. Register the SAME path twice with different HTTP methods (`@app.get("/thing")` and
   `@app.post("/thing")`, two different functions). Confirm both work independently via
   `client.get("/thing")` and `client.post("/thing")`.
3. Try calling `hello_name("Ada")` directly in Python (not through `TestClient`, not through any
   HTTP call) — just as an ordinary function call. Does it work? What does this prove about what
   the decorator did (and didn't) change about the function itself?
4. Register a route for a path that collides with FastAPI's own built-in ones, e.g.
   `@app.get("/docs")` returning something custom. What happens when you request `/docs` now —
   does your version win, or FastAPI's own built-in one?
5. Read `app.routes[-1]` (the LAST route, which should be your own, most-recently-added one) and
   print its `.endpoint` attribute. What is it — is it literally the same function object you
   defined?

**How it works internally.** `@app.get(path)` is really `app.get(path)(function)` — `app.get(path)`
itself returns a NEW, small function (a decorator factory), and THAT function is what actually
receives `health` as its argument, records `(GET, path, health)` in the app's internal route list,
and returns `health` unchanged. Nothing about this needs any special language feature beyond
ordinary closures and first-class functions — the exact same mechanism any Python decorator uses.

### How to validate this yourself
`tests/test_health.py`'s own `TestClient(app).get("/health")` IS this validation, already written
and passing — it proves the registered route really does connect a real HTTP GET to the real
`health()` function, through the real FastAPI routing machinery, with no server process involved.

### How to visualize this
Draw the mail-slot board from the analogy directly: a row of labelled boxes (`/health` → `health`,
`/docs` → FastAPI's own docs handler, etc.). Draw ONE arrow arriving from outside (a real request)
pointing at the matching label, then a second arrow from that label down into the matched function.

**You should now be able to explain:** the two completely separate moments involved in a path
operation — registration (once, at import time) and invocation (every time a matching request
actually arrives) — and why confusing the two is a common source of routing bugs.

---

## C3 — Automatic response serialization

### Do this first
Run [`d2_auto_serialization.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d2_auto_serialization.py>).
**Predict first:** `as_dict()` returns a plain Python `dict` — no `json.dumps()` anywhere in the
script. Do you expect `client.get("/dict").content` to be raw Python `repr()` text, or real JSON
bytes?

### 1. Definition + analogy
**What it is.** When a path operation function returns an ordinary Python object (a `dict`, a
`list`, a string, a number), FastAPI doesn't hand that object back to the caller as-is — it
converts it into JSON text, encodes that text as bytes, and sets the correct
`Content-Type: application/json` header, automatically, before the response ever leaves the
server.

**Analogy.** Handing a chef a list of ingredients in your own handwriting and getting back a
printed, professionally-formatted menu card — you provided the CONTENT in a convenient form for
you to write; something else took care of turning it into the correctly-formatted thing the
customer (the HTTP client) actually expects to receive.

### For a 14-year-old
Imagine you're texting a friend a grocery list. You just type it out normally: "milk, eggs,
bread." You don't have to convert your thoughts into some special "text message format" — your
phone's software does that invisibly, turning your words into the actual signal that travels over
the network and shows up correctly on your friend's screen. You just provide the CONTENT; the
formatting and transmission happen automatically, behind the scenes, every single time.

`health()` returning `{"status": "ok", "qdrant_url": ...}` is exactly like typing out that grocery
list normally. You write plain Python — a dict, values, nothing special. FastAPI is the "phone's
software" here: it automatically converts that plain Python dict into the specific text format
(JSON) that web browsers and API clients actually expect, wraps it with the right label
(`Content-Type: application/json`) so the receiving side knows how to read it, and sends it as real
bytes. You never touch `json.dumps()` yourself, anywhere in this whole file.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| typing a grocery list in plain words | `return {"status": "ok", "qdrant_url": ...}` |
| the phone converting your words into the real signal | FastAPI/Starlette serializing the dict into real JSON bytes |
| labelling the message so it displays correctly | the `Content-Type: application/json` header, set automatically |
| your friend's phone correctly displaying the text | the HTTP client (browser, `TestClient`, `curl`) correctly parsing the JSON |
| you never thinking about signal encoding yourself | `health()` never calling `json.dumps()` or setting headers itself |

### 2. Why here specifically
`health()`'s whole job is to hand back a small piece of structured information — exactly the
shape JSON exists for. Writing `return {...}` and trusting FastAPI to handle the rest keeps this
function focused entirely on WHAT to report, with zero boilerplate about HOW to format or transmit
it — the same convenience every future Phase 7 endpoint (eventually returning a real RAG answer)
will also lean on.

### What breaks if this changes
- **Return a Python object JSON has no representation for** (e.g., a raw `set`, or a custom class
  instance with no serialization rule): FastAPI would raise a real error trying to convert it —
  this is exactly why Pydantic models (not used yet in this tiny file, but coming in later Phase 7
  steps) exist: they give FastAPI an explicit, checked shape to serialize from.
- **Manually call `json.dumps()` and return a plain string instead of a dict:** would technically
  still "work" for simple cases, but FastAPI would treat it as a STRING response by default (not
  guaranteed `application/json`), and you'd be redoing work FastAPI already does correctly and
  consistently.
- **Return `None`:** FastAPI happily serializes this to the JSON literal `null` — not an error, but
  worth knowing explicitly, since silently returning `None` from a route that should return real
  data is an easy, hard-to-notice bug.

### [Interview relevance: medium-high — understanding what a framework does "for free" versus what you're responsible for is core to using any framework well, and explaining FastAPI's serialization layer (backed by Pydantic) shows real depth beyond "I called an endpoint and it worked"]

**Interview question:** *"If a path operation function just returns a Python dict, how does the
client actually receive valid JSON?"*
**A strong answer covers:** FastAPI inspects the returned value and runs it through a
serialization step (backed by Pydantic's own encoding logic) that converts native Python types
into JSON-compatible ones, then a JSON renderer turns that into actual bytes; the correct
`Content-Type` header is set automatically to match; and this same mechanism is what lets FastAPI
validate and document a RESPONSE shape too, when a `response_model` (a Pydantic model) is declared
on the route — not used in this minimal file yet, but the natural next step once responses need a
guaranteed, documented shape rather than "whatever dict I happened to return."

### 3. Minimal code that explains the concept
The complete script, [`d2_auto_serialization.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d2_auto_serialization.py>) (run from the project root):
```python
# Minimal demo of C3: returning a plain Python object gets automatically turned into a real
# JSON HTTP response -- no manual json.dumps(), no manual Content-Type header.
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/dict")
def as_dict():
    return {"status": "ok", "count": 3}  # a plain Python dict -- nothing JSON-specific here


@app.get("/list")
def as_list():
    return ["a", "b", "c"]  # a plain Python list, also a valid return value


client = TestClient(app)
r1 = client.get("/dict")
print("status:", r1.status_code, " content-type:", r1.headers["content-type"])
print("raw bytes:", r1.content)  # shows the REAL bytes actually sent over the wire
print("parsed json:", r1.json())  # the same bytes, decoded back into Python

r2 = client.get("/list")
print("\nstatus:", r2.status_code, " parsed json:", r2.json())
```
Real output:
```
status: 200  content-type: application/json
raw bytes: b'{"status":"ok","count":3}'
parsed json: {'status': 'ok', 'count': 3}

status: 200  parsed json: ['a', 'b', 'c']
```
The `raw bytes` line is the proof: `health()` (and `as_dict()` here) never once mentions JSON, yet
the literal bytes crossing the wire are correctly-formatted JSON text, with the right header set.

### The exact code I wrote (`src/api/main.py`)
> **Lines 10-18:**
> ```python
> @app.get("/health")
> def health() -> dict:
>     """Liveness check + confirms which backing services this instance is configured for."""
>     return {
>         "status": "ok",
>         "qdrant_url": settings.qdrant_url,
>         "neo4j_uri": settings.neo4j_uri,
>         "ollama_url": settings.ollama_url,
>     }
> ```

**🎯 Interview-relevant lines:** the `-> dict` return type hint is NOT what makes serialization
happen (FastAPI would serialize the return value regardless) — it's purely for readability and
tooling (mypy, IDE autocomplete). The serialization itself is driven by the ACTUAL returned value
at runtime, not by the annotation.

### Canonical docs
- [FastAPI: First Steps](https://fastapi.tiangolo.com/tutorial/first-steps/) (the same page as C1
  — it covers this exact "return a dict" behavior right after the first route example).

### Micro-exercises
1. In `d2`, add `@app.get("/none")` returning `None`. Call it via `TestClient` and print both
   `.content` and `.json()`. What does FastAPI actually send?
2. Try returning a Python `set` (e.g., `{1, 2, 3}`) from a new route. Run it and read the real
   error FastAPI raises — what does it say, and why can't a `set` be serialized the same way a
   `list` can?
3. Return a plain string (`return "hello"`, no dict) from a new route. Check the response's
   `content-type` header — is it still `application/json`? What do the raw bytes actually look
   like for a bare string?
4. Add a THIRD field to `/dict`'s returned dict with a nested dict as its value (e.g.,
   `"meta": {"a": 1}`). Confirm the nested structure survives correctly in `r1.json()`.
5. Using `import time; return {"now": time.time()}`, confirm a Python `float` serializes to a
   JSON number, not a JSON string — read the raw bytes to check there are no quote marks around it.

**How it works internally.** FastAPI runs the returned value through `jsonable_encoder` (its own
conversion step, built on Pydantic), which recursively turns Python-native types into
JSON-compatible ones (dicts stay dicts, tuples become lists, datetimes become ISO strings, and so
on), then hands the result to a JSON renderer that produces the final bytes and sets the response
headers — all of this happening AFTER your function returns, not inside it.

### How to validate this yourself
`r1.content` in `d2`'s own output (`b'{"status":"ok","count":3}'`) is the validation — reading the
RAW bytes, not the convenience-parsed `.json()`, proves the actual wire format is real JSON text,
not just something that happens to parse correctly by coincidence.

### How to visualize this
Draw a small pipe: a box labelled "your plain Python dict," feeding into a second box labelled
"FastAPI's serializer," feeding into a final box labelled "real JSON bytes + Content-Type header."
Label the FIRST box "the only part you write" and the rest "automatic, every single time."

**You should now be able to explain:** what "automatic serialization" actually means concretely
(a real conversion step, not hand-waving), and why `health()` can stay so short despite producing
a fully valid, correctly-labelled HTTP JSON response.

---

## C4 — The ASGI server relationship (app vs. server)

### Do this first
Run [`d3_asgi_server_relationship.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d3_asgi_server_relationship.py>).
**Predict first:** `app.__call__` — the method that actually gets invoked to handle a request — is
inspected without ever starting a server. Do you expect it to be a normal function, or something
that needs `await`?

### 1. Definition + analogy
**What it is.** ASGI (Asynchronous Server Gateway Interface) is a STANDARD, agreed-upon contract:
any Python web framework that implements `async def __call__(self, scope, receive, send)` can be
driven by ANY ASGI-compatible server. FastAPI implements this contract; it does NOT implement a
network server itself. `uvicorn` (or another ASGI server) is the separate program that actually
opens a TCP socket, speaks real HTTP over it, and calls your app's `__call__` once per connection.

**Analogy.** A universal remote control's INFRARED CODE FORMAT is a standard — any TV that
understands that format can be controlled by any remote that speaks it, regardless of who made
either one. FastAPI is like a TV that correctly understands the standard signal format (ASGI); it
still needs an actual remote control (uvicorn) pointed at it and pressing buttons (real requests)
for anything to happen.

### For a 14-year-old
Imagine a universal remote control and a TV from a completely different, unrelated brand. They
still work together perfectly, because both of them agree to speak the SAME signal language (a
standard infrared code format) — neither one needed to be built by the same company, or even know
the other exists, as long as both follow the same agreed-upon rules. The remote's whole job is
sending real signals; the TV's whole job is correctly reacting to signals it receives. Neither one
does the other's job.

ASGI is that agreed-upon signal language for Python web apps. `FastAPI` is the TV — it knows how
to correctly REACT when it receives a properly-formatted "request" signal (by calling the right
registered function, building a response, etc.). `uvicorn` is the remote control — its whole job is
listening to REAL incoming network connections and translating them into the exact signal format
(`scope`, `receive`, `send`) that any ASGI app, including FastAPI, has agreed to understand.
Neither one does the other's job, and — just like you could point a DIFFERENT remote at the same
TV — you could swap `uvicorn` for a different ASGI server and this exact same `app` object would
still work correctly, with zero changes.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the agreed-upon infrared signal format | the ASGI specification itself |
| the TV, correctly reacting to signals | `app`, via its `async def __call__(scope, receive, send)` |
| the remote control, sending real signals | `uvicorn`, listening on a real TCP port |
| pressing a button on the remote | a real browser/client making an HTTP request |
| swapping in a different-brand remote, same TV | running `hypercorn src.api.main:app` instead of uvicorn — same `app`, different server |
| `tests/test_health.py`'s `TestClient` | a THIRD kind of "remote" — one that skips the real network entirely and calls `__call__` directly, in-process |

### 2. Why here specifically
`src/api/main.py` defines `app` and stops — it deliberately does NOT call `uvicorn.run(app)` or
include any "if this file is run directly, start a server" logic. This is exactly the shape ASGI
expects: a clean, importable `app` object that the actual running command
(`uvicorn src.api.main:app`, eventually wired into `docker-compose.yml`'s own `api` service) is
responsible for driving — keeping "what the app does" and "how the app is served" as two separate
concerns, never tangled together in one file.

### What breaks if this changes
- **Add `uvicorn.run(app)` directly inside `main.py` at import time:** importing this module for
  ANY other reason (like `tests/test_health.py`'s `from src.api.main import app`) would try to
  start a real server as a side effect of just importing a Python file — a serious, surprising bug.
- **Implement a sync `def __call__` instead of `async def __call__`:** ASGI specifically requires
  an async callable; a sync one simply wouldn't satisfy the protocol, and no ASGI server would be
  able to drive it correctly (this is FastAPI's own implementation detail, not something
  `src/api/main.py` itself controls, but worth knowing WHY `async` matters here).
- **Try to run this file with a WSGI server (like plain `gunicorn` without an ASGI worker) instead
  of an ASGI one:** WSGI and ASGI are DIFFERENT, incompatible protocols — a WSGI server calling a
  `scope/receive/send`-shaped app would fail immediately; this is exactly why async frameworks
  (FastAPI, and this project's own eventual streaming RAG endpoint) specifically need ASGI, not
  the older WSGI standard Flask/Django traditionally used.

### [Interview relevance: very high — the difference between WSGI and ASGI, and between "framework" and "server," is one of the most fundamental distinctions in modern Python web development, and is DIRECTLY relevant to an AI engineer building anything that needs to stream a response (like tokens from an LLM) back to a client]

**Interview question:** *"Why does a framework like FastAPI need a separate server like uvicorn,
instead of just running itself?"*
**A strong answer covers:** ASGI is a STANDARD interface, not a specific implementation — this
separation lets any compliant framework work with any compliant server, and lets a server focus
entirely on low-level concerns (sockets, HTTP parsing, concurrency) while the framework focuses
entirely on routing and business logic; this project's own `app` object is driven two different
ways already (a real server, and `TestClient` for tests) with zero changes needed to the app
itself; and — the sharpest point for an AI-adjacent role — ASGI's async nature is specifically
what makes STREAMING responses (like tokens arriving from an LLM one at a time, exactly what this
project's own `RAGPipeline.stream()` produces) practical, in a way the older, synchronous WSGI
standard was never designed for.

### 3. Minimal code that explains the concept
The complete script, [`d3_asgi_server_relationship.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d3_asgi_server_relationship.py>) (run from the project root):
```python
# Minimal demo of C4: FastAPI's "app" object does NOT listen on any network port by itself --
# it's an ASGI application: an async callable a separate SERVER (uvicorn) drives.
import inspect  # used to inspect app.__call__ without ever running a real server
from fastapi import FastAPI

app = FastAPI()

print("is app.__call__ an async function?", inspect.iscoroutinefunction(app.__call__))
print("app.__call__ signature:", inspect.signature(app.__call__))
# ASGI's real contract: async def __call__(self, scope, receive, send) -- three arguments:
# scope (metadata about this one connection/request), receive (an async function to get
# incoming messages), send (an async function to send a response back)

print("\nNo TCP socket is open right now -- 'app' is just this callable sitting in memory.")
print("Running `uvicorn src.api.main:app` is what actually:")
print("  1. opens a real TCP socket and listens for HTTP connections")
print("  2. for each request, builds a real 'scope' dict and calls `await app(scope, receive, send)`")
print("  3. writes whatever 'send' was called with back onto the socket as real HTTP bytes")
```
Real output:
```
is app.__call__ an async function? True
app.__call__ signature: (scope: collections.abc.MutableMapping[str, typing.Any], receive: collections.abc.Callable[[], collections.abc.Awaitable[collections.abc.MutableMapping[str, typing.Any]]], send: collections.abc.Callable[[collections.abc.MutableMapping[str, typing.Any]], collections.abc.Awaitable[None]]) -> None

No TCP socket is open right now -- 'app' is just this callable sitting in memory.
Running `uvicorn src.api.main:app` is what actually:
  1. opens a real TCP socket and listens for HTTP connections
  2. for each request, builds a real 'scope' dict and calls `await app(scope, receive, send)`
  3. writes whatever 'send' was called with back onto the socket as real HTTP bytes
```
The exact `scope`/`receive`/`send` signature printed here IS the real ASGI contract, word for
word — not a simplification, the actual thing uvicorn calls on every single request.

### The exact code I wrote (`src/api/main.py`)
> There is no explicit server-starting code in this file at all — and that absence is itself the
> point. The whole file ends after defining `app` and one route:
> ```python
> @app.get("/health")
> def health() -> dict:
>     ...
> ```
> No `if __name__ == "__main__":`, no `uvicorn.run(...)`. `app` is left as a clean, importable
> module-level object for whatever actually runs it (a real `uvicorn` process, or `TestClient` in
> tests) to pick up.

**🎯 Interview-relevant lines:** the ABSENCE of server-starting code is as deliberate and
meaningful as any line that IS present — it's what keeps this file importable (by tests, by other
modules) without any surprising side effects.

### Canonical docs
- [ASGI specification: Introduction](https://asgi.readthedocs.io/en/latest/introduction.html)
- [uvicorn (the ASGI server used by this project)](https://github.com/encode/uvicorn)
- [FastAPI: Deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/)

### Micro-exercises
1. Run `.venv/Scripts/python -m uvicorn src.api.main:app --port 8000` from the project root (a
   real terminal command, not a demo script) and visit `http://localhost:8000/health` in a real
   browser. Confirm you see the real JSON response. Stop the server with Ctrl+C when done.
2. With the server still running from exercise 1, visit `http://localhost:8000/docs`. This
   interactive page is ALSO one of FastAPI's own built-in routes (seen in `d0`'s own output) —
   try calling `/health` directly from that page's UI.
3. In `d3`, change `inspect.signature(app.__call__)` to `inspect.signature(app.routes[0].endpoint)`
   (FastAPI's own docs-serving function). Is IT async too? What does this suggest about whether
   ASGI requires every individual FUNCTION to be async, or just the app's own top-level `__call__`?
4. Look up `hypercorn` (a different real ASGI server) and read its own first-steps docs. Would
   `hypercorn src.api.main:app` work with zero changes to `src/api/main.py`? Why or why not,
   based on what ASGI actually standardizes?
5. Read `TestClient`'s own source (or its docs) for how it drives an ASGI app WITHOUT a real
   network socket. What does it use instead of a real TCP connection to call `app.__call__`?

**How it works internally.** `uvicorn src.api.main:app` parses that string as "import the module
`src.api.main`, then use its `app` attribute." Internally, uvicorn opens a real socket using
Python's own `asyncio` event loop, and for every incoming TCP connection, it parses the raw HTTP
bytes into the `scope` dictionary ASGI expects (method, path, headers, etc.), then calls
`await app(scope, receive, send)` — `receive` and `send` are themselves just async functions
uvicorn provides, wired to that specific connection's actual socket.

### How to validate this yourself
Exercise 1 above IS the validation — running the real command and hitting a real port with a real
browser is the only way to directly observe the "app alone does nothing, a server makes it real"
distinction, since every OTHER technique in this deep dive (including `TestClient`) deliberately
bypasses the real network layer.

### How to visualize this
Draw three separate boxes in a row: "your browser" → "uvicorn (a real process, holding a real
socket)" → "app (your FastAPI object)". Draw the arrow between browser and uvicorn as a THICK,
labelled "real TCP/HTTP" line. Draw the arrow between uvicorn and app as a THIN, labelled
"in-process Python function call (scope, receive, send)" line — visually distinguishing "real
network" from "just calling a Python function."

**You should now be able to explain:** what ASGI actually is, why FastAPI needs uvicorn (or an
equivalent) to be reachable at all, and why this project's own `TestClient`-based tests never need
Docker or a real port to fully exercise the app's real behavior.

---

## C5 — The health-check design pattern

### Do this first
Run [`d4_health_check_pattern.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d4_health_check_pattern.py>).
**Predict first:** a correctly-configured app and a MISCONFIGURED one (pointed at the wrong
Qdrant host) are both asked for their health. Do you expect `status` to differ between them?

### 1. Definition + analogy
**What it is.** A health-check endpoint answers "is this specific running instance okay?" — but
"okay" can mean very different things depending on how much information it reports. A MINIMAL
health check only confirms the process itself is alive and answering requests. An INFORMATIVE one
(what `src/api/main.py` actually implements) also reports the instance's own real configuration,
so a human debugging a problem can see WHAT this specific instance thinks it's talking to, without
needing separate access to its environment variables or config files.

**Analogy.** Calling a store to ask "are you open?" gets a yes/no. Calling and asking "are you
open, and which warehouse is currently supplying your shelves?" gets you something you can actually
ACT on if deliveries aren't showing up — the first answer only tells you the STORE itself is fine;
the second tells you something about what it depends on.

### For a 14-year-old
Imagine calling a pizza place to check if they're open. "Yes, we're open!" is nice to know, but if
your pizza never arrives, that answer doesn't help you figure out WHY. Now imagine instead they
say: "Yes, we're open, and tonight we're using Downtown Cheese Co. for mozzarella and Eastside
Bakery for dough." If your pizza shows up wrong or late, THAT answer gives you something to
actually check — maybe Eastside Bakery is having a bad night. The store being "open" and the store
having everything it NEEDS lined up correctly are two different questions, and the second answer
lets you tell them apart without guessing.

`src/api/main.py`'s `/health` is the second kind of answer. It doesn't just say `{"status": "ok"}`
— it also reports `qdrant_url`, `neo4j_uri`, and `ollama_url`: exactly which three "suppliers"
this running instance currently thinks it's using. If something goes wrong later (a RAG query
fails, say), a human can call `/health` FIRST and immediately see "oh, this instance is pointed at
the wrong Qdrant host" — without needing shell access to the container to check its environment
variables by hand.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "Are you open?" -> "Yes!" | a minimal health check: `{"status": "ok"}` alone |
| "...and which warehouse supplies you?" | `/health` also reporting `qdrant_url`, `neo4j_uri`, `ollama_url` |
| the pizza arriving wrong, with no clue why | a real RAG query failing, with no easy way to check what the server was even configured to talk to |
| being told exactly which supplier is in use | `settings.qdrant_url` etc. — this INSTANCE's real, current configuration |
| checking the supplier without visiting the warehouse | checking configuration via one HTTP call, without needing shell/container access |

### 2. Why here specifically
This project's own `RAGPipeline` genuinely depends on THREE separate real services (Qdrant, Neo4j,
Ollama) — any one of them being unreachable, or the app being pointed at the WRONG one (a real,
already-documented risk in this project: `settings.ollama_url` defaults to a Docker-internal
hostname that doesn't resolve from the host, found and fixed back in P6-M1), would break real
requests in ways that are hard to diagnose from the outside. Reporting the actual configured URLs
turns "something's broken, good luck" into "here's exactly what this instance thinks it's talking
to — check that first."

### What breaks if this changes
- **Report only `{"status": "ok"}`, nothing else:** `d4`'s own demo shows this directly — a
  misconfigured instance reports the EXACT SAME thing as a correctly-configured one. The health
  check would be unable to help diagnose the single most likely real failure mode (wrong URLs).
- **Have `/health` actually TRY to connect to Qdrant/Neo4j/Ollama and fail if any are down:**
  this would make `/health` slower and more fragile (this project's own `src/generation/setup.py`
  health check DOES do a real generation call for exactly this reason, for a DIFFERENT purpose —
  verifying the model actually loads, not just reporting config) — a real design trade-off between
  "fast, cheap liveness check" and "thorough, slower readiness check," not shown in this minimal
  file but worth knowing the distinction exists.
- **Hardcode the reported URLs as string literals instead of reading `settings`:** would make the
  health check LIE the moment the real configuration ever differs from what was hardcoded —
  exactly the kind of drift `settings.qdrant_url` (reading the REAL, live configuration) prevents.

### [Interview relevance: medium-high — designing a genuinely USEFUL health/readiness endpoint (not just a token "200 OK") is real production-engineering judgment, and distinguishing "liveness" from "readiness" checks is a concept that comes up constantly in any containerized/orchestrated deployment, including this project's own docker-compose.yml healthchecks]

**Interview question:** *"What should a good health-check endpoint actually return, and why?"*
**A strong answer covers:** at minimum, confirmation the process itself is alive and answering
requests (liveness); ideally, enough about the instance's OWN configuration or dependency state to
help a human diagnose a problem without separate access to logs or environment variables
(readiness/diagnostic value); the trade-off between a FAST check (just report config) and a
THOROUGH one (actually probe dependencies, at the cost of latency and the check itself becoming
another thing that can fail); and that orchestration systems (Docker Compose's own `healthcheck:`
blocks, Kubernetes liveness/readiness probes) often call endpoints like this automatically and act
on the result — making the DESIGN of this endpoint a real operational decision, not a formality.

### 3. Minimal code that explains the concept
The complete script, [`d4_health_check_pattern.py`](<../practice codes/phase 7/m1_fastapi_skeleton_deep_dive/d4_health_check_pattern.py>) (run from the project root):
```python
# Minimal demo of C5: a GOOD health check reports enough to diagnose a problem, not just "ok" --
# exactly why src/api/main.py's /health returns qdrant_url/neo4j_uri/ollama_url, not just status.
from fastapi import FastAPI
from fastapi.testclient import TestClient


def build_app(qdrant_url, ollama_url):  # mirrors src/api/main.py's own settings-driven health check
    app = FastAPI()

    @app.get("/health_minimal")
    def health_minimal():
        return {"status": "ok"}  # tells you NOTHING about what this instance is even pointed at

    @app.get("/health_informative")
    def health_informative():
        return {"status": "ok", "qdrant_url": qdrant_url, "ollama_url": ollama_url}
        # tells a human debugging a problem EXACTLY what this running instance is configured for

    return app


print("--- a correctly-configured instance ---")
client = TestClient(build_app("http://qdrant:6333", "http://ollama:11434"))
print("minimal:    ", client.get("/health_minimal").json())
print("informative:", client.get("/health_informative").json())

print("\n--- a MISCONFIGURED instance (still reports 'ok'!) ---")
client2 = TestClient(build_app("http://wrong-host:6333", "http://ollama:11434"))
print("minimal:    ", client2.get("/health_minimal").json())
print("informative:", client2.get("/health_informative").json())
print("\nBoth say 'status: ok' -- the app itself is running fine either way. Only the")
print("INFORMATIVE version lets a human actually SEE the misconfiguration without guessing.")
```
Real output:
```
--- a correctly-configured instance ---
minimal:     {'status': 'ok'}
informative: {'status': 'ok', 'qdrant_url': 'http://qdrant:6333', 'ollama_url': 'http://ollama:11434'}

--- a MISCONFIGURED instance (still reports 'ok'!) ---
minimal:     {'status': 'ok'}
informative: {'status': 'ok', 'qdrant_url': 'http://wrong-host:6333', 'ollama_url': 'http://ollama:11434'}

Both say 'status: ok' -- the app itself is running fine either way. Only the
INFORMATIVE version lets a human actually SEE the misconfiguration without guessing.
```
Both apps report `status: ok` — because the FastAPI process itself genuinely IS running fine in
both cases; the bug is in what it's configured to talk to, which only the informative version
actually reveals.

### The exact code I wrote (`src/api/main.py`)
> **Lines 10-18 (the full route, quoted again with C5's own lens this time):**
> ```python
> @app.get("/health")
> def health() -> dict:
>     """Liveness check + confirms which backing services this instance is configured for."""
>     return {
>         "status": "ok",
>         "qdrant_url": settings.qdrant_url,
>         "neo4j_uri": settings.neo4j_uri,
>         "ollama_url": settings.ollama_url,
>     }
> ```

**🎯 Interview-relevant lines:** the docstring itself states the design intent precisely —
"Liveness check **+** confirms which backing services this instance is configured for" — naming
BOTH halves of what this endpoint does, not just the trivial half.

### Canonical docs
- [Kubernetes: Liveness, Readiness, and Startup Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/) —
  the canonical, widely-used vocabulary for "liveness" vs. "readiness" this concept borrows from,
  even though this project doesn't (yet) run on Kubernetes.
- `docker-compose.yml`'s own `healthcheck:` blocks for `qdrant`/`neo4j`/`ollama` (this repo) — the
  SAME underlying idea, applied at the container-orchestration level instead of the application level.

### Micro-exercises
1. In `d4`, add a THIRD field to `health_informative`'s return value: a fixed, hardcoded string
   `"version": "0.1.0"` instead of reading it from anywhere real. What's the problem with this,
   compared to reading `app.version`?
2. Extend `health_informative` to ALSO attempt a real, tiny check — e.g., raise or catch an
   exception if `qdrant_url` is an empty string. Design (on paper) what a genuinely broken
   "readiness" version of this endpoint would check beyond just reporting configured URLs.
3. Compare `src/api/main.py`'s `/health` to `src/generation/setup.py`'s own `health_check`
   function (from Phase 6). Both are called "health checks" — what's the real difference in what
   each one verifies, and why does one need a real model call while the other doesn't?
4. Look at `docker-compose.yml`'s `ollama` service's own `healthcheck:` block
   (`test: ["CMD-SHELL", "ollama list || exit 1"]`). Is this a "minimal" or "informative" health
   check, in this concept's own terms? What would make it more informative?
5. Design, on paper, what `/health`'s response SHOULD look like once this project's real RAG
   endpoints exist — should it report whether Ollama has the configured model actually PULLED
   (like `src/generation/setup.py` already checks), or does that belong in a separate, slower
   `/ready` endpoint instead? Argue both placements.

**How it works internally.** `settings` (imported from `src.core.config`) is this project's own,
already-built configuration object — `health()` reads THREE existing attributes off it
(`qdrant_url`, `neo4j_uri`, `ollama_url`) at the exact moment a request arrives, so the reported
values are always whatever this specific running process's configuration ACTUALLY is right now —
never a stale snapshot, never hardcoded, always the real, live truth for this instance.

### How to validate this yourself
Change your own `.env` file's configured URLs (or an environment variable this project's
`settings` object reads), restart the API, and call `/health` for real — confirm the reported
values change to match, proving the endpoint genuinely reflects live configuration rather than
something baked in at build time.

### How to visualize this
Draw a single running server box. Draw three small arrows LEAVING the box toward three other
boxes labelled "Qdrant," "Neo4j," "Ollama" — but draw them as DASHED lines (not yet verified,
just configured). Label the whole diagram "what `/health` reports: WHERE this instance is
pointed, not WHETHER those arrows actually work" — visually capturing the liveness/readiness
distinction.

**You should now be able to explain:** the difference between a health check that merely proves a
process is alive and one that's actually useful for diagnosing a real problem, and why
`src/api/main.py`'s specific design choice (report config, don't probe dependencies) is a
reasonable trade-off for a FAST liveness check, not a complete substitute for a slower readiness
check.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Start the real server for real.** From the project root:
   ```bash
   .venv/Scripts/python -m uvicorn src.api.main:app --port 8000
   ```
   Leave it running in one terminal.
2. **Trigger a real request and watch it at every layer.** In a SECOND terminal (or a browser),
   run `curl http://localhost:8000/health`. In the FIRST terminal, watch uvicorn's own access log
   print a line for this exact request the instant it arrives — this is C4's server layer,
   observably doing its job.
3. **Read the real response.** Confirm the JSON you get back matches exactly what `health()`
   returns in `src/api/main.py` — including whatever `qdrant_url`/`neo4j_uri`/`ollama_url` your
   own `.env` currently configures (C3 + C5, together, for real).
4. **Visit `/docs` in a real browser** (`http://localhost:8000/docs`). This is FastAPI's own
   built-in route (seen first in `d0`'s output) — expand `/health`, click "Try it out," and
   execute it from the UI. Watch uvicorn's log print a SECOND line for this second real request.
5. **Break it on purpose.** Stop the server (Ctrl+C). Run `curl http://localhost:8000/health`
   again — observe the connection simply fail (no server listening at all). This is the cleanest
   possible proof of C1/C4 together: the `app` OBJECT still exists as real Python code in
   `src/api/main.py`, completely unchanged, but with no server running it, nothing on the network
   can reach it at all.
6. **Compare it to the in-process path.** Run `.venv/Scripts/python -m pytest tests/test_health.py
   -v`. Notice it passes in well under a second, with the server never started at all — the SAME
   `app` object, driven entirely differently (C4's `TestClient` path, not a real socket).

If you can explain, in your own words, why step 5 fails but step 6 still passes — using the exact
same `app` object in both cases — you've understood the core distinction this whole deep dive was
built around: the app and the server are two different things, and almost everything else in this
file follows from that one separation.
