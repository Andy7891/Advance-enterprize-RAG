# Phase 7, Microstep 5 — API Documentation & Testing

**Date:** 2026-10-01
**Core learning: Yes.** The first step whose job is to look critically at something FastAPI
already builds automatically, rather than building something new — and a direct demonstration
that "auto-generated" does not mean "automatically correct" once custom error handlers and
streaming responses enter the picture.

## What was already done vs. what this step closes
`/docs` (Swagger UI) and `/redoc` already existed and worked before this step — FastAPI generates
both automatically from route definitions, confirmed live (both return `200`, both list `/health`
and `/chat`). Nothing needed to be built to satisfy "auto-generated OpenAPI docs at `/docs`"
literally. What "verify" actually meant: fetch the real, generated schema and read it critically,
which surfaced concrete, real gaps — not a generic polish pass. `docs/api-usage.md` is wholly new.

## What reading the real schema found
Ran `app.openapi()` directly, before assuming anything:
1. **`/chat`'s `200` response schema was `{}`**, under `content-type: application/json` — actively
   wrong on both counts. The real response is `text/event-stream`, raw SSE text, never JSON.
2. **`400`, `500`, and `504` — real, tested, already-shipped response shapes from P7-M3 — were
   completely absent.** Only `200` and FastAPI's own default `422` appeared anywhere in the schema.
3. No top-level API description, no field descriptions on `HealthResponse`, no example value on
   `ChatRequest`, both endpoints under one generic "default" tag (confirmed visually in the live
   Swagger UI too, not just the raw JSON).

## A bug found in the fix itself
Adding `responses={200: {"content": {"text/event-stream": {...}}}}` to `/chat`'s decorator did
**not** replace FastAPI's own spurious `application/json` entry — re-fetching `app.openapi()`
after the first attempt showed both content types still listed for the same response. Reading
`fastapi/openapi/utils.py`'s own `get_openapi_path` directly (not guessing) showed why: FastAPI's
automatic-schema logic reads the route's configured `response_class` — which defaults to
`JSONResponse` for *every* route regardless of the function's own `-> StreamingResponse` return
hint — to decide whether to emit that entry at all. `response_class=StreamingResponse` on the
decorator (its `media_type` is `None` at the class level) was the real fix, confirmed by
re-checking the schema a second time.

## What was built
- **`CHAT_RESPONSES`** (`src/api/main.py`): a module-level dict documenting `/chat`'s real `200`
  (SSE, with a real captured example), `400`, `500`, and `504` — each matching `_error_envelope`'s
  actual shape, with `504`'s own description explicitly stating it can never really be produced by
  `/chat` itself (citing [docs/36](<../docs/36 error_handling_deep_dive.md>) C2/C4 directly).
- **`response_class=StreamingResponse`** on `/chat`'s decorator, closing the bug found above.
- **`tags=["health"]`/`tags=["chat"]`**, a real `FastAPI(description=...)`, a real `examples=[...]`
  on `ChatRequest.query`, and real `description=` on every `HealthResponse` field.
- **[`tests/test_openapi.py`](../tests/test_openapi.py)** (new, 7 tests): pins every fact found
  and fixed above against regression — `/docs`/`/redoc`/`/openapi.json` all load, the top-level
  description exists, `/chat`'s `200` shows only `text/event-stream`, all four real statuses are
  documented, `ChatRequest`'s example exists, `HealthResponse`'s fields are described, both routes
  are tagged.
- **[`docs/api-usage.md`](<../docs/api-usage.md>)** (new): every example run for real against the
  live app — `/health`, a full real `/chat` streaming response, 400, 422, and 500/504 via a
  temporary debug-only route (with a clear explanation of why `/chat` itself can't produce those
  two statuses, per `docs/36`'s own already-verified finding).

## Decisions (all "recommended")
| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| D1 `/chat`'s SSE type | `responses={200: {"content": {"text/event-stream": ...}}}` | Leave FastAPI's default; or a fake JSON `response_model` | The default was actively wrong; a fake model would be actively dishonest |
| D2 The fix's own bug | `response_class=StreamingResponse` | Assume `responses={}` alone was enough | Verified the "fix" didn't work before trusting it |
| D3 Error documentation | Add 400/500/504, leave 422 as FastAPI's default | Leave only 200/422 documented | These are real, tested, already-shipped shapes |
| D4 `ChatRequest` example | A real, already-reliable question | No example, description only | "Try it out" previously prefilled an empty, invalid string |
| D5 Field descriptions + tags | Add both, now | Leave fields bare, routes ungrouped | Cheap; more endpoints are already flagged as coming |
| D6 Usage-doc methodology | Every example run for real | Hand-written, illustrative | Matches this project's own established verification standard |
| D7 500/504 in the usage doc | Live-triggered via a debug route, limitation explained | Omit 500/504 entirely | Real, shipped behavior; omitting it under-documents the API |

## Verified, not assumed
```
/docs, /redoc, /openapi.json load  : confirmed live, all three return 200, via a real running
                                      uvicorn server, not just TestClient
/chat's 200 schema before this step: fetched app.openapi() directly -- {"application/json":
                                      {"schema": {}}}, confirmed empty and wrong
responses={} alone doesn't replace : re-fetched app.openapi() after the first fix attempt --
  FastAPI's own default entry        BOTH application/json and text/event-stream were present
response_class=StreamingResponse   : re-fetched app.openapi() a second time after adding it --
  fixes it                           only text/event-stream remained, confirmed correct
fastapi/openapi/utils.py           : read directly (get_openapi_path, ~lines 330-472) -- confirmed
                                      route.response_class (not responses=) governs whether the
                                      automatic default-content entry is added at all
every docs/api-usage.md example    : run for real against a live server with real Docker services
                                      up -- see the doc's own captured output
hermetic tests                     : 7 new (tests/test_openapi.py) + 28 regression (test_chat.py,
                                      test_health.py, test_errors.py, test_logging.py), all passing
```

## Findings
1. **"Auto-generated" documentation is only as accurate as what the framework can infer —
   anything added via a mechanism FastAPI doesn't specifically understand (a custom exception
   handler, a streaming response with no `response_model`) is silently invisible to it.** P7-M3's
   own 400/500/504 handlers are real, tested, and correct — and were nonetheless completely absent
   from the one place (`/docs`) a new API consumer would actually look first.
2. **A fix for a documentation bug needs the same verification discipline as the original bug
   itself.** The first attempt at declaring `/chat`'s SSE type looked correct by inspection (the
   `responses=` parameter was used exactly as FastAPI's own docs describe) — and was still
   incomplete, caught only by re-fetching the real schema and comparing it against what was
   actually intended, not what the code merely appeared to do.
3. **Reading a framework's own source to find the SPECIFIC attribute that controls a specific
   behavior (`response_class`, not `responses=`) is faster and more reliable than guessing at
   plausible-sounding fixes one at a time.** This is the fourth consecutive microstep (P7-M2
   through P7-M5) where reading the installed package's real source directly resolved a question
   that trial-and-error or assumption would have gotten wrong or taken longer to find.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🟡 **OpenAPI/Swagger documentation is generated from code, not written by hand — but only
  reflects what the generator was told or can infer.** A custom exception handler or a streaming
  response needs to be EXPLICITLY described (`responses=`, `response_class=`) or it's invisible,
  regardless of how correct and well-tested the underlying behavior actually is.
- 🟡 **Verifying a fix is a distinct step from writing it** — re-checking the real, observable
  output after a change, not just trusting the change looks right by inspection.
- ⚪ The specific `CHAT_RESPONSES` dict contents, the exact example question chosen, the specific
  tag names — configuration choices, not new concepts.

## Honest limitations
1. **`docs/api-usage.md`'s real streaming example reflects this project's own current live
   throughput** (real generation time on this dev host) — it will look different (faster or, more
   likely given this project's own documented ceiling, occasionally much slower or timed out) on
   different hardware, and is a snapshot, not a guaranteed contract.
2. **The 500/504 examples in `docs/api-usage.md` come from a temporary debug route, not from
   `/chat` itself** — clearly disclosed in the doc, but worth restating here: this project's own
   real, live traffic has never actually produced a clean HTTP 500/504 from any endpoint, only via
   this deliberately-constructed demonstration.
3. **OpenAPI schema coverage was checked for `/health` and `/chat` only** — the two endpoints that
   exist today. `/ingest`/`/query` (flagged as coming) will need the same kind of scrutiny applied
   to them individually when they're built, not assumed to inherit correctness automatically.

## How this connects
**Previous:** P7-M1 through P7-M4 built the real application behavior (the skeleton, streaming,
error handling, logging) this step's documentation now accurately describes. **This step:** closes
the loop — what the app actually does and what its docs claim it does are now the same thing,
verified rather than assumed. **Next (not yet specified):** `/ingest`/`/query`, whenever built,
inherit this same documentation standard from day one rather than needing a separate "verify the
docs" pass added on afterward.

## How to do this yourself
1. After adding any custom exception handler or non-standard response type to a FastAPI app, fetch
   `app.openapi()` (or hit `/openapi.json`) directly and read it — don't assume the auto-generated
   docs already know about something you just added through a mechanism FastAPI wasn't told about.
2. When a fix to a documentation/schema issue LOOKS right by reading the code, re-verify by
   checking the actual, real output again afterward — a fix that follows the documented pattern
   correctly can still be incomplete, as this step's own `response_class` finding shows directly.
3. When a framework's behavior doesn't match your expectation, look for the SPECIFIC attribute or
   parameter actually driving it by reading the framework's own source, rather than trying several
   plausible-sounding configuration changes one at a time and checking each by trial and error.

## Practice exercises
1. Temporarily remove `response_class=StreamingResponse` from `/chat`'s decorator, re-fetch
   `app.openapi()`, and confirm the spurious `application/json` entry returns — reproducing this
   step's own finding yourself, then restoring the fix.
2. Add a `response_model=None` attempt (mirroring this step's own first wrong guess, verified in
   this step's own investigation to NOT fix the problem) and confirm it really doesn't help,
   rather than taking this note's word for it.
3. Read `fastapi/openapi/utils.py`'s own `get_openapi_path` function in full (the installed
   package). Find the exact line where `route.responses` (your custom dict) gets merged with the
   automatically-generated entries, and explain in your own words why `.setdefault(...)` produces
   a merge rather than a replace.
4. Add a THIRD custom response (e.g., `429 Too Many Requests`, not currently used anywhere in this
   app) to `CHAT_RESPONSES` with a real example, even though nothing in the app raises it yet.
   What does this reveal about the difference between DOCUMENTING a response and the app actually
   being ABLE to produce it?
5. Open `/redoc` (not `/docs`) in a browser and compare how it presents the same schema — does
   ReDoc show `/chat`'s `text/event-stream` example differently than Swagger UI does?

## Sources
- [FastAPI: Additional Responses in OpenAPI](https://fastapi.tiangolo.com/advanced/additional-responses/) —
  the documented `responses=` mechanism this step uses.
- [FastAPI: OpenAPI docs](https://fastapi.tiangolo.com/tutorial/metadata/) — `FastAPI(description=...)`
  and route-level `tags=`.
- `fastapi/openapi/utils.py` (`get_openapi_path`) — read directly from the installed package; the
  real mechanism behind both the original bug and the bug found in the first fix attempt.
