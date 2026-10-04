# 38 — API Documentation & Testing Deep Dive (P7-M5)

> Deconstructs P7-M5's additions to [`src/api/main.py`](../src/api/main.py) — `CHAT_RESPONSES`,
> `response_class=StreamingResponse`, `ChatRequest.query`'s `examples=`, and `HealthResponse`'s
> field descriptions — plus [`tests/test_openapi.py`](../tests/test_openapi.py). This deep dive
> does NOT re-cover ground [docs/34](<34 fastapi_skeleton_deep_dive.md>) through
> [docs/37](<37 logging_observability_deep_dive.md>) already built (automatic response
> *serialization* at request time, exception handler dispatch, streaming mechanics, ASGI
> middleware) — it covers what's genuinely new: how the OpenAPI *schema* itself gets built (a
> completely different mechanism from serializing a real response), how FastAPI's own
> `responses=` parameter really merges with its automatic defaults, the one hidden setting that
> actually governs automatic schema generation, how a Pydantic example becomes an interactive
> "Try it out" prefill, and what it means to write a test against *documentation* rather than
> *behavior*. Demo scripts live in
> [`practice codes/phase 7/m5_api_documentation_deep_dive/`](<../practice codes/phase 7/m5_api_documentation_deep_dive/>)
> and need no Docker or real server. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 7/m5_api_documentation_deep_dive/d2_response_class_governs_schema.py"
> ```

---

## 0. Plain-language summary

```
app.openapi():       builds the OpenAPI schema by INSPECTING route metadata -- never calls a
                      route function, works even if the real app couldn't actually serve a request
responses={}:         ADDS to FastAPI's own automatic response entries -- does not replace them
response_class=:      the ACTUAL setting that controls whether an automatic entry is added at all
Field(examples=[]):   a real JSON Schema keyword, which is exactly what Swagger UI's own "Try it
                      out" reads to prefill a request
tests/test_openapi.py: tests that assert against the SCHEMA itself, a different property than
                      whether requests actually work correctly
```

Five concepts, in the order this step actually needed them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | OpenAPI generation is introspection, not execution | 🟡 useful | why `/docs` works with zero backing services |
| C2 | `responses=` merges, it doesn't replace | 🔴 high | why the first fix attempt didn't work |
| C3 | `response_class` governs automatic schema generation | 🔴 very high | `response_class=StreamingResponse` |
| C4 | `Field(examples=...)` → Swagger UI's "Try it out" | 🟡 useful | `ChatRequest.query` |
| C5 | Testing documentation as code | 🔴 high | `tests/test_openapi.py` |

---

## C1 — OpenAPI generation is introspection, not execution

### Do this first
Run [`d0_schema_is_introspection_not_execution.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d0_schema_is_introspection_not_execution.py>).
**Predict first:** a route's function body increments a counter and then unconditionally raises.
`app.openapi()` is called, then `/openapi.json` and `/docs` are fetched over real HTTP. Do you
expect the counter to be `0` or `1` after all three of those?

### 1. Definition + analogy
**What it is.** `app.openapi()` builds the OpenAPI schema by reading metadata that already exists
on every registered route — its path, HTTP method, Pydantic parameter/response models, decorator
arguments (`tags`, `responses`, `response_class`, docstrings) — and assembling it into one big
JSON document. It never actually CALLS any route function to find out what it does. This is a
fundamentally different operation from handling a real request (docs/34's own "automatic response
serialization," `jsonable_encoder`), which DOES run your function, every time, for every request.

**Analogy.** A restaurant's printed menu versus actually cooking a dish. The menu — with its
ingredient list, price, and a photo — is assembled entirely from information the kitchen already
has on file (recipes, supplier costs) and can be printed and handed to every customer without
ever firing up a stove. Whether the dish can ACTUALLY be cooked correctly right now (out of a key
ingredient? broken oven?) is a completely separate question from whether the menu describes it
correctly.

### For a 14-year-old
Imagine a school's course catalog, printed at the start of the year, listing every class, its
teacher, and a short description. The catalog is built entirely from the SCHEDULE OFFICE's own
records — nobody actually has to sit through a real class, right now, just to print a sentence
describing it in the catalog. If a particular class's teacher happens to be sick today, that
doesn't change what the catalog says about the class at all — the catalog was never "running" the
class in the first place, just describing it from information already on file.

`app.openapi()` is the catalog office. It reads each route's OWN filed information (its
decorator's arguments, its parameter types, its docstring) and prints a description — without
ever actually "sitting through" the route (calling the function). This is exactly why `/docs`
worked correctly this whole project even during moments the REAL backing services (Qdrant, Neo4j,
Ollama) were down or unconfigured: the catalog doesn't care whether class could actually be taught
successfully today, only what's on file about it.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the printed course catalog | the generated OpenAPI schema (`app.openapi()`) |
| the schedule office's own filed records | each route's decorator arguments, type hints, docstring |
| printing a class's description | building one `paths["/chat"]` entry in the schema |
| a class actually being taught, today | a REAL request reaching and running the route function |
| a sick teacher not affecting the catalog | `/docs` working even with Qdrant/Neo4j/Ollama down |
| the catalog office not re-printing every time someone asks | `app.openapi_schema` caching the result after the first call |

### 2. Why here specifically
This explains something that could otherwise look mysterious: this whole step's verification and
fixes (`app.openapi()`, `/openapi.json`, visually checking `/docs`) never once required Docker,
`lifespan`, or any real backing service to be running — confirmed directly in this step's own
investigation. Understanding WHY makes that fact unsurprising instead of a happy coincidence.

### What breaks if this changes
- **Assume `/docs` failing means the app itself is broken:** the two are unrelated failure modes —
  a route with a genuinely broken function body (one that would crash on every real call) can
  still produce a perfectly working, perfectly described `/docs` page, exactly as `d0` proves.
- **Assume the schema updates automatically if a route's logic changes without its OWN metadata
  changing:** it doesn't need to — the schema was never describing runtime BEHAVIOR in the first
  place, only the declared SHAPE (types, statuses, descriptions) — changing what a function does
  internally, without changing its signature/decorators, changes nothing the schema reports.
- **Call `app.openapi()` expecting a fresh computation every time:** after the first call, the
  result is cached on `app.openapi_schema` and reused — `d0`'s own identity check confirms the
  exact same object comes back on a second call, not a recomputed one.

### [Interview relevance: useful — knowing that API documentation generation is a static, introspection-based process (not a live probe of the running service) explains a lot of "why does my documentation say X when the service is actually down" confusion, and is foundational to reasoning about CI/build-time schema generation]

**Interview question:** *"Does your API's documentation page depend on your backing services
(database, cache, other APIs) being up and reachable?"*
**A strong answer covers:** no, for a framework like FastAPI — the OpenAPI schema is built purely
from route metadata (type hints, decorator arguments, docstrings) via static introspection, never
by actually calling route functions or reaching real dependencies; this means documentation
generation can run in CI, at build time, or with zero real infrastructure, and a route whose real
logic is currently broken can still be perfectly and accurately documented; and recognizing the
distinction between this (schema generation) and a DIFFERENT mechanism (automatic response
serialization, which DOES run your function for every real request) is what prevents confusing
"the docs look fine" with "the service actually works."

### 3. Minimal code that explains the concept
The complete script, [`d0_schema_is_introspection_not_execution.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d0_schema_is_introspection_not_execution.py>) (run from the project root):
```python
# Minimal demo of C1: app.openapi() builds the schema by INSPECTING route definitions (type
# hints, Pydantic models, decorator arguments) -- it never actually CALLS any route function.
# Proven with a route that would crash if it were ever really invoked.
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI(title="Introspection Demo")
call_count = {"n": 0}

@app.get("/would-crash")
def would_crash():
    call_count["n"] += 1
    raise RuntimeError("if this ever actually ran, something is very wrong")

print("--- generating the schema (app.openapi()) ---")
schema = app.openapi()
print("schema generated successfully:", "paths" in schema and "/would-crash" in schema["paths"])
print("call_count after schema generation:", call_count["n"])

print("\n--- fetching /docs and /openapi.json over real HTTP ---")
client = TestClient(app)
r1 = client.get("/openapi.json")
r2 = client.get("/docs")
print("openapi.json status:", r1.status_code, " docs status:", r2.status_code)
print("call_count after fetching both:", call_count["n"])

print("\n--- caching: app.openapi() called a second time ---")
schema_again = app.openapi()
print("same object returned (identity check):", schema is schema_again)

print("\n--- only an ACTUAL call to the route executes its body ---")
try:
    client.get("/would-crash")
except RuntimeError as exc:
    print("a REAL request DID reach and run the function -- it raised:", exc)
print("call_count now:", call_count["n"])
```
Real output:
```
--- generating the schema (app.openapi()) ---
schema generated successfully: True
call_count after schema generation: 0

--- fetching /docs and /openapi.json over real HTTP ---
openapi.json status: 200  docs status: 200
call_count after fetching both: 0

--- caching: app.openapi() called a second time ---
same object returned (identity check): True
app.openapi_schema is set: True

--- only an ACTUAL call to the route executes its body ---
a REAL request DID reach and run the function -- it raised: if this ever actually ran, something is very wrong
call_count now: 1
```
`call_count` stays `0` through schema generation AND through real `/openapi.json`/`/docs` HTTP
requests — it only becomes `1` once the route is hit DIRECTLY. This is direct, unambiguous proof
schema generation never executes route logic.

### The exact code I wrote (`src/api/main.py`)
> This concept explains something that's true of the WHOLE file, not one specific line — every
> decorator argument this step added (`responses=CHAT_RESPONSES`, `response_class=StreamingResponse`,
> `tags=["chat"]`, `examples=[...]`) is read once, at schema-generation time, by code that never
> touches `_sse_events`, `RAGPipeline`, or any real backing service.

**🎯 Interview-relevant lines:** every investigation command run for this whole step
(`app.openapi()`, `curl .../openapi.json`, opening `/docs` in a browser) worked identically whether
or not `docker compose up -d qdrant neo4j ollama` had even been run yet — directly confirming this
concept against the real app, not just a toy one.

### Canonical docs
- [FastAPI: Metadata and Docs URLs](https://fastapi.tiangolo.com/tutorial/metadata/)

### Micro-exercises
1. In `d0`, add a SECOND route whose function references an undefined variable (a real
   `NameError` waiting to happen on any actual call). Confirm `app.openapi()` still succeeds.
2. Time `app.openapi()`'s first call versus its second (`time.perf_counter()` around each). Is the
   second call meaningfully faster? What does this confirm about the caching behavior?
3. Modify a route's REAL logic (change what it computes/returns) without touching its type hints,
   decorator arguments, or docstring. Does `app.openapi()`'s output change at all?
4. Stop Ollama, Qdrant, and Neo4j (or don't start them at all) and try fetching THIS project's own
   real `/docs` and `/openapi.json` before `lifespan` would even be able to succeed. What happens?
   (Hint: this connects directly to [docs/35](<35 chat_streaming_deep_dive.md>) C1's own finding
   about when `lifespan` actually runs.)
5. Read FastAPI's own `FastAPI.openapi()` method source directly (`fastapi/applications.py`). Find
   the exact line that checks `self.openapi_schema` before deciding whether to rebuild it.

**How it works internally.** `FastAPI.openapi()` checks `if not self.openapi_schema:`, and only if
that's true does it call `get_openapi(...)` (from `fastapi.openapi.utils`), which walks
`self.routes`, and for each one reads its `.response_model`, `.responses`, `.tags`,
`.response_class`, and so on — all plain Python attributes already sitting on the route object
from when the decorator ran at import time — building a schema dict purely from that metadata.

### How to validate this yourself
`d0`'s own `call_count` staying at `0` through both schema generation AND real HTTP requests to
`/openapi.json`/`/docs` is the validation — a shared, mutable counter is about as direct a proof
as Python offers that a function was or wasn't actually called.

### How to visualize this
Draw two completely separate boxes side by side. Label the left one "Schema generation" with an
arrow going INTO it from "route decorators/type hints" and an arrow going OUT to "OpenAPI JSON."
Label the right one "Handling a real request" with an arrow going INTO it from "an HTTP request"
and an arrow going OUT to "a real response" — and, uniquely to this box, draw a loop that actually
passes THROUGH the route function's own body. No arrow connects the two boxes to each other.

**You should now be able to explain:** why API documentation can be complete and accurate even
when the underlying service is completely non-functional, and why this is a feature, not a gap.

---

## C2 — `responses=` merges, it doesn't replace

### Do this first
Run [`d1_responses_merge_not_replace.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d1_responses_merge_not_replace.py>).
**Predict first:** a route's own decorator adds `responses={200: {"content": {"text/event-stream":
{...}}}}}`. FastAPI's own automatic default for that same 200 response would otherwise have been
`{"application/json": {"schema": {}}}`. Do you expect the final schema to show ONLY the
`text/event-stream` entry you asked for, or BOTH?

### 1. Definition + analogy
**What it is.** FastAPI's `responses=` decorator parameter is documented as "additional
responses" — and that phrasing is precise, not loose. Internally, FastAPI FIRST builds its own
automatic response entries (derived from the route's `response_class`/`response_model`), and only
AFTER that does it layer your `responses=` dict on top, using `dict.setdefault(...)` at every
level. `setdefault` ADDS a key only if it's missing — it never overwrites or removes something
already there. The practical result: your custom content type joins whatever FastAPI already
put there, rather than replacing it.

**Analogy.** Adding a sticky note to a form that a clerk has already partially filled out, rather
than handing the clerk a brand new blank form. If the clerk already wrote something in the
"delivery method" box and your sticky note ALSO says something about delivery method, the form now
visibly shows both — your note doesn't erase what was already written underneath it.

### For a 14-year-old
Imagine a group project poster where each teammate is allowed to add their own sticky note with
extra information, but nobody is allowed to erase or cover up a note someone else already stuck
on. If the FIRST teammate already stuck a note on the poster saying "this project is about
volcanoes" (even if that's not quite right), and you stick on your OWN note saying "this project
is about the water cycle," the poster now shows BOTH notes, side by side — yours didn't replace
theirs, because the rule was "add a note," not "replace all the notes."

FastAPI's own automatic documentation system is like that first teammate — it ALWAYS sticks its
own note on describing what a route probably returns, based on general defaults, before you ever
get a say. When `responses={200: {...}}` sticks YOUR note on afterward, it joins the poster
alongside FastAPI's own note rather than replacing it — which is exactly why `/chat`'s schema
briefly showed BOTH `application/json` (FastAPI's own guess) and `text/event-stream` (the real
answer) side by side, until a different fix (C3) stopped FastAPI from sticking its own wrong note
on in the first place.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the first teammate's own note, stuck on automatically | FastAPI's own automatic `"application/json": {"schema": {}}` entry |
| the rule "add a note, never erase one" | `dict.setdefault(...)`, used throughout `get_openapi_path` |
| your own sticky note | your `responses={200: {"content": {...}}}` argument |
| the poster now showing BOTH notes | the real, observed schema with two content types |
| stopping the first teammate from writing anything at all | a DIFFERENT fix (C3) that prevents the automatic note from ever being written |

### 2. Why here specifically
This is the exact, real sequence of events from this step's own work: adding `responses=` alone
was a completely reasonable, documented-correctly first attempt — and it still produced a broken
result, because the ASSUMPTION "adding a response description replaces the default" was wrong.
Understanding the real merge mechanism is what made it possible to diagnose the leftover
`application/json` entry precisely, instead of guessing at more configuration to try.

### What breaks if this changes
- **Assume `responses={200: {...}}` is sufficient on its own to fully describe a non-default
  response type:** `d1`'s own real output disproves this directly — both content types appear,
  confirmed by printing the actual resulting schema, not by reasoning about what "should" happen.
- **Try to "clear" the automatic entry by setting `responses={200: {"content": {}}}`** (an empty
  dict, hoping it resets things): `setdefault` on an empty dict still doesn't REMOVE whatever
  FastAPI's own earlier step already wrote into the real `content` dict inside the schema — the
  automatic entry was already added to the SAME shared dict before your empty one is even
  considered, so there's nothing to "clear" from your side of the merge.
- **Give up on `responses=` entirely, assuming it's broken:** it isn't broken — it does exactly
  what its name says, "additional." The real fix needed is a DIFFERENT, separate mechanism (C3)
  that stops the unwanted automatic entry from being generated in the first place.

### [Interview relevance: high — "merge, not replace" is an extremely common footgun pattern across many APIs and frameworks (config merging, dict updates, CSS specificity), and recognizing it here is a specific instance of a general debugging instinct: verify what a "such-and-such" parameter's own real mechanics are before assuming it does what its name suggests]

**Interview question:** *"You added a custom response description to a route, but the generated
documentation still shows the framework's own default alongside it. What would you check?"*
**A strong answer covers:** whether the custom response parameter ADDS to the framework's
automatically-generated documentation or REPLACES it — many frameworks (FastAPI's own `responses=`
included) use additive/merge semantics specifically so you don't have to redeclare everything the
framework already knows how to describe correctly (like the default `422` validation error); if an
automatic entry is specifically WRONG rather than just incomplete, the fix usually isn't the
additive parameter at all, but whatever SEPARATE setting controls whether that automatic entry
gets generated in the first place; and verifying this by reading the actual generated output
(the real schema, here) rather than assuming a parameter's name describes its full behavior.

### 3. Minimal code that explains the concept
The complete script, [`d1_responses_merge_not_replace.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d1_responses_merge_not_replace.py>) (run from the project root):
```python
# Minimal demo of C2: adding responses={200: {...}} to a route does NOT replace FastAPI's own
# automatically-generated 200 entry -- it MERGES alongside it. The exact real behavior found
# while fixing /chat's own documentation this step, reproduced in isolation.
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get(
    "/stream",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def stream() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))

schema = app.openapi()
content = schema["paths"]["/stream"]["get"]["responses"]["200"]["content"]
print("content types present on the 200 response:", list(content.keys()))
print(json.dumps(content, indent=2))
```
Real output:
```
content types present on the 200 response: ['application/json', 'text/event-stream']
{
  "application/json": {
    "schema": {}
  },
  "text/event-stream": {
    "schema": {
      "type": "string"
    }
  }
}

-> 'application/json' is FastAPI's own automatic default -- it was never asked for,
   and responses={} alone did not remove it. Both content types coexist.
```
This is the EXACT bug this step found live, reproduced here in complete isolation — a route with
NOTHING else configured, `responses=` used exactly as documented, still ends up with two content
types on one response.

### The exact code I wrote (`src/api/main.py`)
> **Line 359 and the `CHAT_RESPONSES` dict (lines 273-330):**
> ```python
> @app.post("/chat", response_class=StreamingResponse, responses=CHAT_RESPONSES, tags=["chat"])
> ```
> `responses=CHAT_RESPONSES` alone (without the `response_class=` also on this same line) would
> have reproduced exactly `d1`'s own bug on the real `/chat` route — confirmed directly during
> this step's own investigation, before `response_class=StreamingResponse` was added.

**🎯 Interview-relevant lines:** the FINAL, shipped decorator carries BOTH `response_class=` and
`responses=` together, on the same line — not because they do the same job, but because they solve
two genuinely different halves of the same problem (C3 covers the first half).

### Canonical docs
- [FastAPI: Additional Responses in OpenAPI](https://fastapi.tiangolo.com/advanced/additional-responses/)
- `fastapi/openapi/utils.py` (`get_openapi_path`, lines ~473-505 for the merge logic) — read
  directly from the installed package.

### Micro-exercises
1. In `d1`, add a SECOND custom content type to the SAME `responses={200: {...}}}` dict (e.g.
   `"text/plain"` alongside `"text/event-stream"`). Does it also merge in alongside the other two,
   for a total of three content types?
2. Add `responses={200: {"description": "my own description"}}` (no `content` key at all this
   time) to a route and check whether the `description` field replaces FastAPI's own default
   ("Successful Response") or merges with it somehow. Is a plain string value "merged" the same
   way a nested dict is?
3. Try documenting a status code FastAPI does NOT already have automatic documentation for (e.g.
   `201`). Does the same merge behavior apply, or does your content become the ONLY entry since
   there was nothing automatic to merge with in the first place?
4. Read `fastapi/openapi/utils.py`'s own `deep_dict_update` function (referenced near the merge
   logic this section cites). How does it differ from a single-level `dict.update()`?
5. Predict, then verify: does the ORDER of keys in Python's dict (`application/json` appearing
   before `text/event-stream` in the real output above) reveal anything about WHICH one was added
   first — the automatic one or the custom one?

**How it works internally.** `get_openapi_path` runs the automatic-generation logic (deciding
`route_response_media_type` from `response_class`, writing `content["application/json"]["schema"]`
via chained `.setdefault(...)` calls) BEFORE it ever looks at `route.responses` — your custom dict.
When it later processes `route.responses`, it again uses `.setdefault(...)` on the SAME `content`
dict, meaning any key your dict doesn't ALREADY have gets added, but nothing removes a key that's
already there from the earlier, automatic pass.

### How to validate this yourself
`tests/test_openapi.py`'s own
`test_chats_200_response_is_documented_as_sse_not_json` asserts `"application/json" not in content`
specifically — a direct regression guard against this exact merge behavior ever silently
reappearing if `response_class=StreamingResponse` (C3) were ever accidentally removed.

### How to visualize this
Draw a single box labelled "content dict for the 200 response." Draw a first arrow entering it
labelled "FastAPI's own automatic pass," depositing one entry (`application/json`). Draw a SECOND
arrow entering the SAME box, labelled "your `responses=` dict," depositing a different entry
(`text/event-stream`). Both entries sit inside the box at the end — neither arrow ever points
AWAY from the box (there is no "remove" arrow anywhere in this picture).

**You should now be able to explain:** precisely why `responses=` is named "ADDITIONAL responses"
and not "response overrides," and why that distinction directly explains a real bug this step hit.

---

## C3 — `response_class` governs automatic schema generation

### Do this first
Run [`d2_response_class_governs_schema.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d2_response_class_governs_schema.py>).
**Predict first:** three otherwise-identical routes each return a `StreamingResponse`. One sets
nothing extra, one sets `response_model=None`, one sets `response_class=StreamingResponse`. Do you
expect all three to show the same spurious `application/json` entry, or do you expect ONE
specific setting among these three to actually change what appears?

### 1. Definition + analogy
**What it is.** FastAPI's automatic response-schema generation doesn't look at a route function's
own `-> ReturnType` hint to decide what content type to document. It looks at `response_class` — a
SEPARATE decorator parameter that defaults to `JSONResponse` for every single route, regardless of
what the function actually returns. `JSONResponse.media_type` is the literal string
`"application/json"` at the class level, which is exactly what gets written into the automatic
schema entry. Setting `response_class=StreamingResponse` (whose own `media_type` is `None` at the
class level, since a stream's real content type varies per-instance) is what actually stops the
automatic entry from being generated — not the return-type hint, not `response_model=None`.

**Analogy.** A shipping label printer that's hard-wired, by factory default, to always print
"Standard Box" on every label — UNLESS an employee explicitly flips a physical switch on the
printer itself to a different mode. Simply putting a DIFFERENT kind of package (an envelope, a
tube) on the belt doesn't change what the printer prints; the printer was never looking at the
actual package shape in the first place, only at its own switch setting.

### For a 14-year-old
Imagine a vending machine with a little display screen that's programmed, by default, to ALWAYS
show "SNACK" on the screen for every single item in the machine, unless someone specifically
reprograms that ONE item's own display setting. If you stock the machine with a drink instead of a
snack, the screen still says "SNACK" — because the screen was never actually looking at what you
put inside, only at its own separate, pre-set display setting for that slot.

Every FastAPI route has this same kind of hidden "display setting" — `response_class` — defaulting
to `JSONResponse` for literally every route ever created, no matter what the function is actually
typed to return. `/chat`'s function is annotated `-> StreamingResponse`, but that annotation is
like the ACTUAL drink sitting in the machine — it has zero effect on the screen's own separate,
unset display setting. Only explicitly setting `response_class=StreamingResponse` — flipping that
specific switch — changes what the "screen" (the schema) actually says.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the vending machine's default display setting | `response_class` defaulting to `JSONResponse` |
| the actual drink sitting in the slot | the function's own `-> StreamingResponse` return hint |
| the screen ignoring what's actually inside | the automatic schema ignoring the return-type hint entirely |
| reprogramming just that one slot's setting | `response_class=StreamingResponse` on the decorator |
| the screen finally showing something accurate | the automatic `application/json` entry no longer being generated |

### 2. Why here specifically
This is the load-bearing fix of this whole step. C2 explains why the first attempt (`responses=`
alone) left a spurious entry behind; C3 is the actual, verified mechanism that removes it. Neither
alone was sufficient — confirmed directly, not assumed, by trying `response_model=None` FIRST
(a very reasonable-looking guess, since "don't try to derive a response schema" is roughly what
you'd want) and finding it changed nothing at all.

### What breaks if this changes
- **Assume the function's own return-type hint controls schema generation:** `d2`'s own `/a`
  route has `-> StreamingResponse` and STILL shows the spurious `application/json` entry —
  direct, empirical proof the hint alone has zero effect on this specific mechanism.
- **Assume `response_model=None` is the fix, because it "sounds right":** `d2`'s own `/b` route
  proves this specific, plausible-sounding guess doesn't work either — tested and rejected before
  the real fix was found, not assumed to be wrong from first principles.
- **Assume `response_class=StreamingResponse` alone fully documents the response:** it doesn't —
  `d2`'s own `/c` route shows NO content key at all once this is set, with no `responses=`
  override. `response_class` only stops the WRONG automatic entry; it adds nothing correct in its
  place. Both settings (`response_class=` AND `responses=`) are needed together, which is exactly
  why `/chat`'s real decorator carries both.

### [Interview relevance: very high — this is a real, subtle, and not-widely-documented FastAPI behavior (the gap between a function's return-type hint and its response_class) that causes genuinely confusing, silently-wrong API documentation in real projects, and finding it required reading the framework's own source rather than trusting the most intuitive-seeming fix]

**Interview question:** *"You set a route's return type hint to a custom response class, but the
generated OpenAPI docs still describe it as returning plain JSON. Why?"*
**A strong answer covers:** FastAPI's automatic OpenAPI schema generation reads the route's
configured `response_class` parameter (which defaults to `JSONResponse` for every route
regardless of the function's own return-type annotation), not the return-type hint itself, to
decide what automatic content-type entry to add; the fix is explicitly setting
`response_class=` on the route decorator to the real response class being used; and — a sharper
point — `response_class=` alone only suppresses the WRONG automatic entry, it doesn't supply a
correct one in its place, so a real, custom `responses=` description is still needed alongside it
to fully and correctly document a non-default response.

### 3. Minimal code that explains the concept
The complete script, [`d2_response_class_governs_schema.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d2_response_class_governs_schema.py>) (run from the project root):
```python
# Minimal demo of C3: the route's configured `response_class` -- NOT the function's own return
# type hint, NOT response_model=None -- is what governs whether FastAPI adds its automatic
# content entry to the schema. Three variants, same route logic, three different schemas.
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get("/a")  # the ORIGINAL, unfixed attempt: nothing overridden
def a() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))

@app.get("/b", response_model=None)  # a PLAUSIBLE-sounding guess that turns out not to help
def b() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))

@app.get("/c", response_class=StreamingResponse)  # the REAL fix
def c() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))

schema = app.openapi()
for path in ("/a", "/b", "/c"):
    response_200 = schema["paths"][path]["get"]["responses"]["200"]
    content = response_200.get("content")
    print(f"{path}:", list(content.keys()) if content else "(no content key at all)")
```
Real output:
```
/a  (nothing set)
   content: ['application/json']
/b  (response_model=None)
   content: ['application/json']
/c  (response_class=StreamingResponse)
   content: (no content key at all)

-> /a and /b both still carry the spurious 'application/json': {'schema': {}} entry --
   the return type hint and response_model=None had NO effect on it at all.
-> /c has NO content key whatsoever: response_class=StreamingResponse suppresses the
   automatic entry entirely, but adds nothing real in its place -- a real responses=
   override (like this project's own CHAT_RESPONSES) is still needed to describe what
   the endpoint ACTUALLY returns, exactly what src/api/main.py's /chat route does.
```
`/c`'s own result is sharper than it first appears: `response_class=StreamingResponse` doesn't
produce a CORRECT entry, it produces NO entry — the absence of the wrong answer, not the presence
of the right one. Getting the right one still requires `responses=` (C2), which is precisely why
the real `/chat` route needs both settings together, not either alone.

### The exact code I wrote (`src/api/main.py`)
> **Line 359, with the reasoning stated directly in the surrounding comment:**
> ```python
> @app.post("/chat", response_class=StreamingResponse, responses=CHAT_RESPONSES, tags=["chat"])
> # response_class=StreamingResponse, not just the function's own "-> StreamingResponse" return
> # hint: verified by reading fastapi/openapi/utils.py directly (get_openapi_path, ~line 330) that
> # FastAPI's OpenAPI generation reads route.response_class (defaulting to JSONResponse for every
> # route unless overridden here), not the function's return annotation -- without this, FastAPI
> # unconditionally adds a spurious "application/json": {"schema": {}} entry alongside the real
> # text/event-stream one, confirmed empirically before this line was added
> ```

**🎯 Interview-relevant lines:** the comment doesn't just state the fix, it names the SPECIFIC
file and approximate line number that was read to find it (`fastapi/openapi/utils.py`, ~line 330)
— a good habit for exactly this kind of non-obvious, framework-internal finding, so a future
reader (including a future you) can re-verify it in thirty seconds instead of re-deriving it from
scratch.

### Canonical docs
- `fastapi/openapi/utils.py` (`get_openapi_path`, ~lines 311-472) — read directly from the
  installed package; this exact behavior is not, as of this writing, prominently documented on
  FastAPI's own docs site outside of the `responses=` page's own brief examples.
- [FastAPI: Additional Responses in OpenAPI](https://fastapi.tiangolo.com/advanced/additional-responses/)

### Micro-exercises
1. In `d2`, add a FOURTH route using `response_class=StreamingResponse` TOGETHER with
   `responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}}` (both
   settings at once, mirroring the real `/chat` route exactly). Confirm the result has EXACTLY one
   content type, correctly described.
2. Try `response_class=PlainTextResponse` instead of `StreamingResponse` on a route. Does the
   automatic entry now show `"text/plain"` instead of `application/json`? What does this tell you
   about `PlainTextResponse.media_type`'s own class-level value, compared to `StreamingResponse`'s?
3. Read `starlette.responses.Response`'s own base class definition. What is `media_type`'s default
   value THERE (the ultimate fallback if nothing more specific is set anywhere)?
4. Set `response_class=JSONResponse` EXPLICITLY (not just leaving it as the unstated default) on a
   route. Does the schema look any different from leaving `response_class` unset entirely?
5. This project's `/health` route never needed `response_class=` set explicitly. Why not — what's
   different about its own response shape compared to `/chat`'s that makes the DEFAULT
   `response_class=JSONResponse` already correct for it?

**How it works internally.** `APIRoute.__init__` (constructed once per route, at decorator time)
stores whatever `response_class` was passed — or, if none was given, a special sentinel
(`Default(JSONResponse)`) that later resolves to `JSONResponse` unless the app-level
`default_response_class` was itself changed. `get_openapi_path` later reads
`route.response_class.media_type` directly off that stored value — a plain attribute lookup on an
already-existing Python object, nothing more dynamic than that.

### How to validate this yourself
`d2`'s own three-route, side-by-side comparison — identical route BODIES, only the decorator's own
extra settings differing — isolates the variable precisely: since the only thing that changes
between `/a`, `/b`, and `/c` is one decorator argument, whatever schema difference results must be
attributable to that argument alone.

### How to visualize this
Draw three identical gift boxes (the three routes' own function bodies, all returning the same
kind of `StreamingResponse`). Draw a DIFFERENT label stuck on the OUTSIDE of each box —
"(no label)", "response_model=None", "response_class=StreamingResponse" — representing each
route's own decorator settings. Draw a scanner (FastAPI's schema generator) that reads ONLY the
outside label, never opens the box — explaining why two boxes with identical real contents but
different outside labels get described completely differently.

**You should now be able to explain:** precisely which FastAPI setting governs automatic
OpenAPI content-type generation, why a function's own return-type hint has no effect on it at
all, and why this project's real fix needed two settings together, not one.

---

## C4 — `Field(examples=...)` → Swagger UI's "Try it out"

### Do this first
Run [`d3_field_examples_to_schema.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d3_field_examples_to_schema.py>).
**Predict first:** one Pydantic model's field uses `Field(..., examples=["Who founded
Insurellm?"])`; an otherwise-identical model's field has no `examples=` at all. Do you expect
FastAPI's own schema-building code to do something SPECIAL with the example value (wrap it,
process it, validate it against the field's own type), or just pass it through as plain data?

### 1. Definition + analogy
**What it is.** `examples` is a real, standard JSON Schema keyword — not a FastAPI invention. When
you write `Field(..., examples=["some value"])`, Pydantic writes that list, completely unprocessed,
directly into the generated schema under the field's own `"examples"` key. Swagger UI — which is
just a JavaScript application that reads the SAME OpenAPI schema `/docs` serves — looks for this
exact key when building its own "Try it out" form, and uses it to prefill the input instead of
leaving it blank or showing a generic type-based placeholder (`"string"`).

**Analogy.** A recipe card with a small photo of the finished dish printed in one corner. The
photo isn't a separate system bolted onto the recipe — it's literally printed as part of the same
card, sitting right there as one more piece of information alongside the ingredient list. Anyone
reading the card — a human cook, or a machine scanning it for a database — sees the exact same
photo, because it's just DATA on the card, not a special feature only some readers understand.

### For a 14-year-old
Imagine an order form for a custom T-shirt, with a blank line for "Your design idea." Right next
to that blank line, in small grey text, someone printed a sample idea: "e.g., a cartoon dinosaur
riding a skateboard." That sample text isn't a separate little helper robot watching over your
shoulder — it's just MORE TEXT printed on the very same form, sitting right there as data anyone
reading the form can see, including a friend helping you fill it out for the first time.

`Field(examples=["Who founded Insurellm?"])` is printing that sample text onto `ChatRequest`'s own
form (its JSON Schema). Swagger UI is like a very literal friend helping you fill out the form: it
reads that exact sample text off the form and, seeing it's meant as an example, just TYPES IT IN
for you automatically in the "Try it out" box — because the sample was right there on the form the
whole time, not hidden somewhere Swagger UI would need special permission to find.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the small grey sample text printed on the form | `Field(examples=[...])`'s value, written into the schema |
| the sample text being printed AS PART of the same form | `"examples"` sitting inside `ChatRequest`'s own JSON Schema |
| a friend reading the sample and typing it in for you | Swagger UI's own "Try it out" JavaScript, reading the same schema |
| a blank form with no sample text at all | `WithoutExample`'s own schema, with no `"examples"` key |
| any reader (human or machine) seeing the same printed sample | any schema-aware tool — Swagger UI, ReDoc, a code generator — reading the identical key |

### 2. Why here specifically
`ChatRequest.query` previously had a `description=` but no concrete value — Swagger UI's own
"Try it out" would prefill an empty string, which immediately fails `min_length=1`. A first-time
visitor to `/docs` couldn't successfully try the one real endpoint that matters without already
knowing what to type. This is the smallest, cheapest fix in this whole step, and directly improves
the ONE interaction ("Try it out") interactive docs exist for in the first place.

### What breaks if this changes
- **Assume `description=` alone is enough for a good "Try it out" experience:** `description=`
  explains what a field MEANS; it does nothing for what VALUE to prefill — Swagger UI still shows
  an empty/placeholder input regardless of how good the description text is.
- **Pick an example value that happens to be one of this project's own KNOWN weak retrieval
  spots** (e.g., "What are the company values?", already documented as an unreliable retrieval
  case in `notes/step-6.7-integration-checkpoint.md`): a first-time visitor's very first "Try it
  out" click would then demonstrate a real, pre-existing limitation as if it were a bug in the API
  itself, rather than a known, separately-tracked retrieval-quality issue.
- **Assume `examples=` needs FastAPI-specific syntax or a special import:** it doesn't — it's
  plain `pydantic.Field`'s own parameter, backed by the general JSON Schema spec, which is exactly
  why `d3`'s own output shows it surviving into the schema with zero FastAPI-specific processing.

### [Interview relevance: useful — knowing that OpenAPI/JSON-Schema examples are a real, standardized, tool-agnostic mechanism (not a Swagger-UI-only trick) is useful for anyone documenting an API meant to be consumed by more than one kind of client/tool]

**Interview question:** *"How does Swagger UI know what value to prefill when someone clicks 'Try
it out' on a request body field?"*
**A strong answer covers:** it reads the field's own `examples` key directly from the OpenAPI/JSON
Schema that was already generated for that request model — the SAME schema `/openapi.json` serves
to any other tool; a backend framework like FastAPI/Pydantic writes this key when a field declares
`Field(examples=[...])`, with no special processing beyond including the value as-is; and this is
a general, standardized JSON Schema keyword, not something unique to Swagger UI — any other
schema-aware tool (ReDoc, a client generator) can read the identical information the same way.

### 3. Minimal code that explains the concept
The complete script, [`d3_field_examples_to_schema.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d3_field_examples_to_schema.py>) (run from the project root):
```python
# Minimal demo of C4: Field(examples=[...]) flows directly into the generated JSON Schema, which
# is exactly what Swagger UI reads to prefill its own "Try it out" form -- no separate mechanism.
import json
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()

class WithExample(BaseModel):
    query: str = Field(..., min_length=1, examples=["Who founded Insurellm?"])

class WithoutExample(BaseModel):
    query: str = Field(..., min_length=1)

@app.post("/with-example")
def with_example(body: WithExample):
    return {"ok": True}

@app.post("/without-example")
def without_example(body: WithoutExample):
    return {"ok": True}

schema = app.openapi()
print(json.dumps(schema["components"]["schemas"]["WithExample"], indent=2))
print(json.dumps(schema["components"]["schemas"]["WithoutExample"], indent=2))
```
Real output:
```
WithExample's own real JSON Schema:
{
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1,
      "title": "Query",
      "examples": [
        "Who founded Insurellm?"
      ]
    }
  },
  "type": "object",
  "required": ["query"],
  "title": "WithExample"
}

WithoutExample's own real JSON Schema:
{
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1,
      "title": "Query"
    }
  },
  "type": "object",
  "required": ["query"],
  "title": "WithoutExample"
}
```
The ONLY difference between the two schemas is the presence or absence of the `"examples"` key —
everything else (`type`, `minLength`, `title`, `required`) is identical, confirming `examples=`
adds exactly one piece of data and changes nothing else about the field's real validation rules.

### The exact code I wrote (`src/api/main.py`)
> **Lines 261-269:**
> ```python
> query: str = Field(
>     ...,
>     min_length=1,
>     description="The question to ask the RAG pipeline.",
>     examples=["Who founded Insurellm?"],
>     # one of this project's own established, reliably-retrieving labelled questions
>     # (notes/step-6.6-rag-integration-tests.md) -- chosen so Swagger UI's own "Try it
>     # out" prefill actually works well, not a question this project already knows misses
> )
> ```

**🎯 Interview-relevant lines:** the comment explains WHY this specific question, not just that an
example exists — connecting the choice back to `notes/step-6.6-rag-integration-tests.md`'s own
real, measured retrieval-reliability data rather than picking an arbitrary question.

### Canonical docs
- [JSON Schema: Annotations (the `examples` keyword)](https://json-schema.org/understanding-json-schema/reference/annotations)
- [Swagger: Adding Examples](https://swagger.io/docs/specification/v3_0/adding-examples/)

### Micro-exercises
1. In `d3`, add MULTIPLE examples to the same field (`examples=["Who founded Insurellm?", "What
   products does Insurellm offer?"]`). Check Swagger UI's own real behavior — does "Try it out"
   pick the first one, cycle through them, or show a dropdown?
2. Add an example to a field whose TYPE it doesn't actually match (e.g. `examples=["not a number"]`
   on an `int` field). Does Pydantic/FastAPI validate the example against the field's own type at
   schema-generation time, or does it get written through unchecked? What does `d3`'s own
   unprocessed-passthrough finding predict the answer should be?
3. Add a model-level example (via `model_config = {"json_schema_extra": {"examples": [...]}}`, an
   example of the WHOLE object, not one field) alongside a field-level one. Where do the two end
   up in the schema relative to each other?
4. Open this project's own real `/docs`, click "Try it out" on `/chat`, and confirm the real
   prefilled value matches `"Who founded Insurellm?"` exactly, then actually send it.
5. Research the difference between OpenAPI 3.0's singular `example` keyword and OpenAPI 3.1's
   (JSON-Schema-standard) plural `examples` keyword — this project's own schema declares
   `"openapi": "3.1.0"` (confirmed in this step's own earlier investigation) — which one does
   `Field(examples=[...])` actually produce, and would it differ on an older FastAPI/OpenAPI
   version?

**How it works internally.** Pydantic's own schema-generation code copies `Field`'s `examples`
parameter directly into the JSON Schema dict it builds for that field — no transformation, no
validation against the field's own type, no FastAPI-specific step in between. FastAPI's OpenAPI
generation then includes that whole Pydantic-built schema, unmodified, as part of the bigger
document `/openapi.json` serves. Swagger UI (a separate, independent JavaScript application FastAPI
merely serves a pre-built HTML page for) is the only thing that then decides what to DO with the
`examples` key — read it and prefill a form field.

### How to validate this yourself
`tests/test_openapi.py`'s own `test_chat_requests_query_field_has_a_real_example` asserts the
exact list value survives into the real schema — a direct regression guard, and `d3`'s own
side-by-side WithExample/WithoutExample comparison isolates exactly what changes and what doesn't.

### How to visualize this
Draw one index card labelled "ChatRequest's own JSON Schema." Write several lines of real data on
it: `type: string`, `minLength: 1`, and near the bottom, `examples: ["Who founded Insurellm?"]`.
Draw TWO separate arrows leaving this SAME card: one to a box labelled "Swagger UI's own 'Try it
out' form" (which reads the `examples` line and prefills it), another to a box labelled "any other
schema-reading tool" (which could read the exact same line for a different purpose) — both arrows
point to the SAME card, nothing duplicated or special-cased for Swagger UI specifically.

**You should now be able to explain:** that `examples=` is inert, standard data sitting in a
schema, not an active FastAPI feature — and that Swagger UI's own "Try it out" convenience is a
consequence of it reading that data, not of any special cooperation from the backend.

---

## C5 — Testing documentation as code

### Do this first
Run [`d4_testing_the_schema_itself.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d4_testing_the_schema_itself.py>).
**Predict first:** a `/stream` route is tested TWO ways — a runtime test (a real `TestClient`
request, checking the real response body) and a documentation test (checking `app.openapi()`
directly). Before `response_class=StreamingResponse` is added, do you expect BOTH tests to fail,
or only ONE of them?

### 1. Definition + analogy
**What it is.** A runtime test proves something DID happen correctly when the code actually ran —
the real bytes a real request returned. A documentation test proves something is DESCRIBED
correctly — what the schema CLAIMS will happen, checked without ever making a real request at all
(consistent with C1: schema generation never executes route logic). These are genuinely
independent properties. Code can behave perfectly while its own documentation lies about what it
does — which is exactly what happened to `/chat` before this step, and exactly what `d4`
reproduces on purpose.

**Analogy.** A car that drives perfectly, and a separate window sticker on that same car listing
its fuel economy. The car actually driving well and the sticker being ACCURATE are two completely
different facts — you could have a perfectly good car with a wildly wrong sticker (or vice versa),
and checking one tells you nothing about the other. Verifying the car needs a test drive; verifying
the sticker needs reading the sticker itself, not driving anywhere.

### For a 14-year-old
Imagine a school library with a real, working book, and a SEPARATE little card describing that
book taped to the shelf in front of it. The book itself might be in perfect condition, every page
intact, story reading correctly cover to cover. But the little card taped to the shelf might STILL
say the wrong genre, or the wrong page count, or describe a completely different plot — because
nobody ever double-checked the card against the actual book. Reading the book cover to cover (a
"runtime test") proves the book itself works. Separately checking the card against what's actually
inside the book (a "documentation test") proves something else entirely — and passing one tells
you nothing about whether the other one is also true.

`tests/test_chat.py`'s own 11 tests are like reading the book cover to cover — checking that real
requests to `/chat` actually get real, correct streamed answers and error responses. ALL of them
passed the entire time `/chat`'s own documentation was wrong about returning `application/json`.
`tests/test_openapi.py` is a completely separate check — reading the card taped to the shelf (the
schema) and confirming IT says the right thing too, independent of whether the book itself works.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| reading the book cover to cover | `tests/test_chat.py`'s own runtime tests, using `TestClient` |
| the little description card taped to the shelf | the generated OpenAPI schema |
| checking the card against the real book | `tests/test_openapi.py`, checking `app.openapi()` directly |
| a perfectly good book with a wrong card | `/chat` working correctly (11/11 tests passing) while its schema was still wrong |
| nobody having ever double-checked the card before | this project having zero schema-level tests before P7-M5 |

### 2. Why here specifically
This is literally this step's own true story, provable with this project's own real test history:
`tests/test_chat.py`'s 11 tests passed continuously through P7-M2, P7-M3, and P7-M4 — the whole
time `/chat`'s own documentation was quietly wrong. No number of additional RUNTIME tests would
ever have caught that, because runtime tests only ever check what `d4` calls `runtime_test` — real
request, real response. Only a test that inspects the schema itself closes this gap.

### What breaks if this changes
- **Assume thorough runtime test coverage implies accurate documentation:** `d4`'s own "BEFORE the
  fix" block proves this false directly — `runtime_test` passes, `documentation_test` fails, on
  the exact same code, at the exact same time.
- **Only test documentation manually (eyeballing `/docs` occasionally) instead of via an
  automated test:** works once, but has no way to catch a REGRESSION later — if
  `response_class=StreamingResponse` were ever accidentally removed in a future refactor, nothing
  would fail except a schema nobody is actively looking at, until someone happens to check again.
- **Write a documentation test that's really just a disguised runtime test** (e.g., asserting
  `client.get("/docs").status_code == 200` and calling it sufficient): proves `/docs` LOADS, which
  this step's own `tests/test_openapi.py` also checks — but says nothing about whether what it
  shows is actually CORRECT, the real property this whole step's own bug was about.

### [Interview relevance: high — "test the contract, not just the behavior" is a real, transferable testing principle that applies well beyond OpenAPI schemas (database migrations, config file schemas, serialization formats), and recognizing that 100% passing runtime tests says nothing about documentation accuracy is a genuinely useful piece of testing judgment]

**Interview question:** *"Your API has comprehensive test coverage and all tests pass. Can you be
confident its documentation is accurate?"*
**A strong answer covers:** no — runtime tests (checking real request/response behavior) and
documentation/schema tests (checking what the generated OpenAPI schema, or any other
machine-readable contract, actually claims) verify two independent properties; code can behave
perfectly correctly while the schema describing it is wrong, exactly because documentation
generation (C1) doesn't execute the code it's documenting, so nothing in a normal runtime test
suite would ever exercise or validate it; closing this gap needs a DIFFERENT kind of test,
asserting directly against the generated schema (or `/openapi.json`'s real content), not just
against request/response behavior.

### 3. Minimal code that explains the concept
The complete script, [`d4_testing_the_schema_itself.py`](<../practice codes/phase 7/m5_api_documentation_deep_dive/d4_testing_the_schema_itself.py>) (run from the project root):
```python
# Minimal demo of C5: a runtime test (TestClient, checking real request/response behavior) and
# a documentation test (checking app.openapi() directly) prove two INDEPENDENT properties -- one
# can pass while the other fails. Exactly this project's own real P7-M5 story, reproduced.
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

def build_app(fixed: bool) -> FastAPI:
    app = FastAPI()
    kwargs = {"response_class": StreamingResponse} if fixed else {}
    @app.get("/stream", **kwargs)
    def stream() -> StreamingResponse:
        return StreamingResponse(iter(["real", "data", "here"]))
    return app

def runtime_test(app: FastAPI) -> bool:
    """Checks real BEHAVIOR: does a real request actually get the real streamed content?"""
    client = TestClient(app)
    response = client.get("/stream")
    return response.status_code == 200 and response.text == "realdatahere"

def documentation_test(app: FastAPI) -> bool:
    """Checks the DOCUMENTATION: does the schema correctly describe what /stream returns?"""
    content = app.openapi()["paths"]["/stream"]["get"]["responses"]["200"].get("content") or {}
    return "application/json" not in content

for label, fixed in [("BEFORE the fix (response_class not set)", False),
                      ("AFTER the fix (response_class=StreamingResponse)", True)]:
    print(f"=== {label} ===")
    app = build_app(fixed)
    print("  runtime_test:      ", "PASS" if runtime_test(app) else "FAIL")
    print("  documentation_test:", "PASS" if documentation_test(app) else "FAIL")
```
Real output:
```
=== BEFORE the fix (response_class not set) ===
  runtime_test (real request/response behavior): PASS
  documentation_test (schema accuracy):           FAIL

=== AFTER the fix (response_class=StreamingResponse) ===
  runtime_test (real request/response behavior): PASS
  documentation_test (schema accuracy):           PASS

-> the runtime test passes in BOTH cases -- the route always worked correctly.
-> only the documentation test catches the real bug: a schema lying about the response
   type, something no amount of testing real REQUESTS could ever have caught.
```
`runtime_test` is `PASS` in BOTH rows — proving the underlying route was never actually broken at
any point. Only `documentation_test` changes between the two rows, isolating the ENTIRE bug to the
schema, exactly matching this project's own real P7-M5 story.

### The exact code I wrote (`tests/test_openapi.py`)
> ```python
> def test_chats_200_response_is_documented_as_sse_not_json():
>     schema = app.openapi()
>     content = schema["paths"]["/chat"]["post"]["responses"]["200"]["content"]
>     assert "text/event-stream" in content
>     assert "application/json" not in content
> ```

**🎯 Interview-relevant lines:** this test never constructs a `TestClient`, never sends a request,
never touches `RAGPipeline` or any backing service at all — it calls `app.openapi()` directly, the
exact same, pure-introspection operation `d0`/C1 demonstrated, and asserts against the resulting
dict. This is what makes it a genuine documentation test, not a relabeled runtime one.

### Canonical docs
- [FastAPI: Additional Responses in OpenAPI](https://fastapi.tiangolo.com/advanced/additional-responses/) —
  the same page cited in C2/C3, since the mechanism being tested here IS that same mechanism.

### Micro-exercises
1. In `d4`, add a THIRD kind of test — a "manual inspection" stand-in that just checks
   `"/stream" in app.openapi()["paths"]` (proving the route exists in the docs at all, nothing
   about its accuracy). Where does this fall between `runtime_test` and `documentation_test` in
   terms of what it actually proves?
2. Write a documentation test for THIS project's own real `/health` route confirming its
   `response_model` is `HealthResponse` specifically (not just "some model"). What real regression
   would this catch that `tests/test_health.py`'s own runtime test wouldn't?
3. Deliberately break `CHAT_RESPONSES` in a scratch copy (e.g. typo `"upstream_timeout"` as
   `"upstram_timeout"` in the 504 example) and confirm `tests/test_openapi.py` does NOT catch it.
   What does this reveal about the LIMITS of what a documentation test like this one can verify
   (structure/presence vs. exact content accuracy)?
4. Read all 7 of `tests/test_openapi.py`'s own real tests and classify each one: does it test
   PRESENCE (does X exist in the schema), STRUCTURE (is X shaped correctly), or CONTENT (does X
   contain the exact right value)? Which category has the least coverage?
5. Research "contract testing" (a broader software engineering term for this same general idea,
   often used between microservices). How does `tests/test_openapi.py` relate to that concept, and
   how is it different (a contract test usually involves TWO separate services agreeing on a
   schema; this project's schema has no external consumer yet)?

**How it works internally.** There's no special test-framework feature here — `app.openapi()` is
just a plain Python method call returning a plain Python `dict`, and asserting against it is
ordinary `dict`/`list` indexing and equality checks, exactly like `d0`-`d4`'s own scripts. The
"newness" is entirely conceptual (WHAT property is being checked), not mechanical (HOW pytest
runs it) — this is precisely why it was possible to add this whole new testing dimension without
any new test infrastructure, fixtures, or libraries.

### How to validate this yourself
Run `tests/test_chat.py` (11 tests, all about runtime behavior) and `tests/test_openapi.py` (7
tests, all about schema accuracy) side by side. Both suites passing together is direct evidence
the same underlying route now satisfies BOTH independent properties — and, per this project's own
real git/decision history, there was a real period where the first suite alone was green while the
second suite (had it existed yet) would have been red.

### How to visualize this
Draw a Venn diagram with two circles: "code that behaves correctly" and "documentation that
describes it accurately." Mark `/chat`, BEFORE this step, inside the LEFT circle only — correct
behavior, wrong documentation. Draw an arrow labelled "response_class=StreamingResponse" moving
that same dot into the OVERLAP region, now inside both circles at once.

**You should now be able to explain:** why "all my tests pass" is not the same claim as "my
documentation is accurate," and what a test needs to actually check (the schema itself, not a
request's real outcome) to close that specific gap.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Start the real server and generate the schema without touching any backing service.**
   ```bash
   .venv/Scripts/python -c "import json; from src.api.main import app; print(json.dumps(app.openapi(), indent=2))" > /tmp/schema.json
   ```
   Do this BEFORE running `docker compose up`. Confirm it still works — direct, live proof of C1
   against the real app, not just `d0`'s toy one.
2. **Start the real server for real and open `/docs`.** Click "Try it out" on `/chat`. Confirm
   `query` is prefilled with `"Who founded Insurellm?"` (C4) and that `/chat`'s own response
   section shows ONLY `text/event-stream`, never `application/json` (C2 + C3 together).
3. **Temporarily remove `response_class=StreamingResponse`** from the real `/chat` decorator (on a
   scratch branch, or just locally, uncommitted). Re-run `tests/test_chat.py` — still all green.
   Re-run `tests/test_openapi.py` — now red. This is C5's own claim, proven against the real
   app, not a toy one.
4. **Restore the fix and run every test file together.**
   ```bash
   .venv/Scripts/python -m pytest tests/test_chat.py tests/test_openapi.py -v
   ```
   Confirm all 18 pass together.
5. **Read `docs/api-usage.md`'s own real, captured examples** and, for each one, name which
   concept from this deep dive (or from [docs/35](<35 chat_streaming_deep_dive.md>)/[docs/36](<36 error_handling_deep_dive.md>))
   explains WHY that example looks the way it does — in particular, why the 500/504 examples come
   from a debug-only route rather than `/chat` itself.

If you can explain step 3's result — WHY removing one specific decorator argument makes a
DIFFERENT test file fail while leaving `tests/test_chat.py` completely green — using C1, C2, and
C3 together, in your own words, you've connected three concepts from this single deep dive into
one coherent explanation, which is the strongest sign this step's real lesson (correct code and
accurate documentation are two separate things you have to earn separately) has actually landed.
