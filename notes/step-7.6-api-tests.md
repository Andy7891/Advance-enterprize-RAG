# Phase 7, Microstep 6 — Unit & Integration Tests for API

**Date:** 2026-10-03
**Core learning: Yes.** The first step whose job is to look for what the existing tests *cannot
see* — by measuring what 35 tests really cover, probing the real app with throwaway scripts, and
testing one layer below the test client.

## What was already done vs. what this step closes
The task asked for `tests/test_api.py` with endpoint tests. The endpoints already had them: 35
tests in five files (`test_health`, `test_chat`, `test_errors`, `test_logging`, `test_openapi`)
cover 88% of `src/api/main.py`. More tests for `/health` and `/chat` would have duplicated them —
the same lesson as P4-M6 and P5-M5: count what exists first. The real task was finding what those
tests, and line coverage itself, cannot see.

## The step I had skipped
Before designing anything, I ran the three static CI gates for the first time since P7-M2 and
found **all three red**:

| Gate | Result | Cause |
|---|---|---|
| `ruff check src/ tests/` | 15 × E501 | 6 in `src/api/main.py`; 8 in `src/orchestration/prompts.py` (the published `SYSTEM_PROMPT` text); 1 over-long comment in `tests/orchestration/test_prompts.py` |
| `ruff format --check` | 3 files | `main.py`, `prompts.py`, `rag.py` |
| `mypy src/` | 3 errors | all in `main.py`: P7-M4's middleware typed with `FastAPI`/`dict` instead of Starlette's ASGI types; P7-M5's `CHAT_RESPONSES` typed `dict[int, dict]` |

735 tests were green the whole time; none of this is something a test can catch. Nothing had been
pushed, so CI itself never went red — the first push would have. Fixed first (D-2026-10-03-01).

## What measuring and probing found
1. **Coverage.** The 14 uncovered statements of `src/api/main.py` were `lifespan` (139–163), the
   middleware's non-HTTP passthrough (89–90) and the JSON formatter's `exception` branch (54).
   Line 54 stayed uncovered because `logging.basicConfig()` is a **silent no-op under pytest**: the
   root logger already carries pytest's four handlers when `main.py` is imported, so the app's JSON
   handler is never attached in the suite (checked by listing the root handlers inside a test).
2. **Contract defects, found by probing the real app** (none visible in the 88%):
   - `GET /nonexistent` → `{"detail":"Not Found"}` and `GET /chat` → `{"detail":"Method Not
     Allowed"}`: not in the error envelope — so my earlier "every error shares one envelope" claim
     (D-2026-09-30-22, `docs/architecture.md`, `docs/api-usage.md`) was an overclaim.
   - `query: "   "` → **200** with `data: [ERROR] question must be a non-empty string, got '   '`,
     while `""` → 422. The API's `min_length=1` counts spaces; `RAGPipeline` strips.
   - Unhardened, contradicting no documented rule: `k=10**9` reached the retriever, a
     100,000-character query was accepted, the SSE response had no `Cache-Control` /
     `X-Accel-Buffering`, and `HEAD /health` → 405.
3. **`lifespan` is not network-free**: `Embedder` verifies the model digest with Ollama,
   `GraphStore` checks the Neo4j schema, `GraphRetriever` loads every entity name — all in their
   constructors. A prototype that replaced only those calls ran the real lifespan end to end, and
   exposed a bug: a failure after `build_driver()` never closed the driver.
4. **`TestClient` and httpx's `ASGITransport` both buffer the whole body**
   (`starlette/testclient.py:353–367`; `httpx/_transports/asgi.py`: awaits the app call, then builds
   the response from collected `body_parts`), so neither can show *when* a chunk left the app.
5. **Shared-app pollution.** Importing `tests/test_errors.py` adds three `/_test/*` routes to the one
   module-level `app`; `tests/test_logging.py` adds another at run time.

## What was built
- **Static gates green** (D1): `ruff format` on three files (proved AST-identical, layout only),
  `starlette.types` annotations on the middleware, `CHAT_RESPONSES: dict[int | str, dict[str, Any]]`,
  an E501 per-file-ignore for `prompts.py` (wrapping the prompt would change model input), and a
  direct `starlette==1.3.1` pin (the project pins whatever its own code imports).
- **[`tests/test_api.py`](../tests/test_api.py)** — 59 test items: 53 hermetic passing tests, 4
  strict xfails, 2 live. Sections: the public route table; framework-raised errors and boundary
  values; SSE protocol invariants across every way a stream can end; the real retrieval stack behind
  real HTTP; the real `lifespan` with its network calls replaced; incremental delivery and
  concurrency (raw-ASGI harness + two negative controls); operator-visible JSON logs; known gaps as
  strict xfails; the live tier.
- **Three production fixes the tests forced**, each a failing test first (12 red, then green):
  a Starlette `HTTPException` handler putting 404/405 in the envelope (keeping `Allow`);
  `ConfigDict(str_strip_whitespace=True)` on `ChatRequest`; `try`/`finally` around `lifespan`'s
  startup so the driver is closed when startup fails.
- **Docs:** `docs/api-usage.md` gained real captured 404, 405 and whitespace-422 examples;
  the README's "Reproducing the checks" now lists the four CI commands.

## Decisions (all "recommended")
| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| D1 CI gates | Fix all three gates first | Only `main.py`; leave out | A green suite beside a red type check is not an honest finish |
| D2 File role | Top-level cross-component, only the gaps | Consolidate 35 tests; a smoke layer | Precedent `tests/test_rag.py`; no duplicated assertions |
| D3 Lifespan | Hermetic, only network calls replaced, plus `try`/`finally` | Live-only; leave manual | Live tier is deselected in CI; the leak needed a failure-path test |
| D4 Streaming | Raw-ASGI harness + negative controls | httpx `ASGITransport`; real uvicorn subprocess | One tool sees both claims, hermetically |
| D5 404/405 | Starlette `HTTPException` handler | Pin the default; leave | Two error shapes, and a false claim |
| D6 Blank query | `str_strip_whitespace` | `pattern=r"\S"`; pin the quirk | Same message as `""`; canonical embedded text |
| D7 Unhardened gaps | Strict xfail of desired behavior | Characterization tests; fix now | Executable documentation without inventing numbers |
| D8 Live tier | Real lifespan + `/health` + one `/chat` that tolerates only `timed out` | Fast-only; none | Real generation was only ever verified by hand |
| D9 Shared app | Filter the `/_test/` prefix | App factory; teardown fixtures | One filter vs a production refactor |

## Verified, not assumed
```
CI gates, before -> after     : ruff check 15 errors -> 0; ruff format --check 3 files -> 0;
                                 mypy src/ 3 errors -> 0 (37 files)
the reformat is layout-only   : ast.dump(before) == ast.dump(after) for main.py, prompts.py, rag.py
typing fix, before applying   : scratch copy + mypy "Success: no issues found" -- AFTER redoing a
                                 check that first tested nothing (a /tmp path mismatch meant my
                                 edits never applied and mypy re-checked the unedited copy)
coverage of src/api/main.py   : before 88% (35 tests; missed 54, 89-90, 139-163); after 100% (127 stmts, 0 missed; 88 passed + 4 xfailed across all six API files)
probed on the real app        : 404/405 not enveloped; "   " -> 200 + in-band [ERROR]; k=10**9 and a
                                 100k query accepted; no SSE cache headers; HEAD /health -> 405
streaming, on the real app    : raw-ASGI harness: SSE events at 0.26/0.51/0.76/1.01 s (the stub LLM
                                 sleeps 0.25 s per fragment); /health answered in 4 ms mid-stream
mutation checks (real code)   : loop-blocking _sse_events -> ONLY the concurrency test fails
                                 (/health finished at 1.0047 s, after the 1.0022 s stream);
                                 drain-then-yield _sse_events -> ONLY the incremental test fails
red -> green                  : 12 failures (404 + 5x405, 3x whitespace, trim, trimmed prompt,
                                 lifespan leak) -> 0, after the three fixes
full suite (CI selection)     : 786 passed, 4 xfailed, 98 deselected (was 733 passed)
real uvicorn                  : 404/405/whitespace-422 captured live for docs/api-usage.md; a
                                 HEAD request answered with the enveloped 405, no server-side error
live tier (Docker restarted)  : 2 passed in 312 s; /chat ended "[ERROR] timed out" as its only event
                                 (this host's documented generation ceiling); retry with the model warm:
                                 "completed (13 events)" in 210 s -- so the [DONE] branch and its
                                 `Sources:` assertion have also run against reality
```

## Findings
1. **Coverage says which lines ran, not which behaviors are checked.** 88% hid a false documented
   claim, a client error reported as a 200, and a resource leak. Each surfaced only because
   something *probed* behavior (a throwaway script) or exercised a *failure path*.
2. **Observe a property at the layer where it exists.** Incremental delivery and non-blocking
   concurrency are properties of the ASGI messages; a client that buffers the response cannot show
   them. The harness is small precisely because it drops down one layer — and it is only trustworthy
   because it has negative controls and was mutation-checked against the real code.
3. **A failure-path test finds what a success-path test cannot.** "Startup fails, is the driver
   closed?" failed on the first run with `close_calls == 0`.
4. **A verification that cannot fail is not verification.** My first scratch mypy check silently
   tested the unedited file and "reproduced" the original errors. Redone with an `assert` that every
   edit applied, it passed for the right reason.
5. **`basicConfig` under pytest is a different program.** A module-level side effect that depends on
   "nothing else configured logging yet" behaves differently once pytest has set up its handlers, so
   the suite needed a fixture (to see the real bytes) and one clean-process test (to prove the real
   configuration works).

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🟡 **Proving a blocking, synchronous RAG pipeline does not freeze an async, LLM-streaming API** —
  every LLM-serving API depends on it, and it is only observable at the ASGI message level.
- 🟡 **Testing startup wiring and its failure paths** — the least-tested code in most apps and the
  place leaks hide.
- ⚪ Strict `xfail` as executable documentation of a known gap; running the CI gates before "done";
  HTTP edge-case contracts (404/405/redirects/boundary values).

## Honest limitations
1. **The live `/chat` outcome is a lottery on this host.** The test tolerates *only* the documented
   `timed out` ceiling and prints which outcome happened. Both branches were observed on this host: the first
   run (right after a Docker restart, model cold) ended `[ERROR] timed out` as its only event; a retry with
   the model warm completed (13 events, 210 s for the whole test). Which one you get is the hardware's
   decision, not the code's.
2. **Four gaps are documented, not fixed** (strict xfails): unbounded `k`, unbounded query length, no
   SSE cache headers, no `HEAD /health`. Picking real numbers deserves its own measured step.
3. **Shutdown still closes only the Neo4j driver.** The Qdrant client and both Ollama clients are
   never closed.
4. **JSON log timestamps are local time with no offset** (observed: `16:02:15` in the log vs
   `10:32:15 GMT` in the same response's `Date` header). Flagged as a separate follow-up task.
5. **No CORS.** `OPTIONS /chat` is a 405 (pinned by a test), so a browser app on another origin
   could not call this API; a server-side client is unaffected.
6. **`k: true` is silently coerced to 1** by Pydantic's lax mode (seen in a probe, not pinned).
7. **Timing-based tests can flake on a saturated machine.** Margins are relative (0.5× the model's
   delay, 0.4× the stream); if one flakes, move it behind a marker rather than loosening it.
8. **One test costs ~8 s** (the clean-process logging check imports the whole stack); the file runs
   in ~18 s, the rest of the suite ~50 s.
9. **`public_routes()` relies on the `/_test/` prefix convention**; a test route without it fails
   the route-table test, which is the intent.

## How this connects
**Previous:** P7-M1–M5 built and documented the API; P6-M6 set the precedent for a top-level
cross-component test file. **This step:** the API's behavior — including at its edges, at startup and
under concurrency — is now pinned by tests that fail for the right reasons, and the three static CI
gates are green. **Next (not yet specified):** `/ingest` and `/query`, if built, inherit this
structure: the route-table test will fail until they are declared, and framework errors already use
the envelope.

## How to do this yourself
1. Before writing tests for code that already has tests, **measure coverage, then probe the real
   thing with a throwaway script** for behaviors nobody wrote a test for. Each surprise is a test.
2. Write the test for a suspected defect **first** and watch it fail for the *predicted* reason;
   only then fix. A test that never failed has not been shown to detect anything.
3. To test a property, **observe it at the layer where it lives**, then prove the observer can fail
   (a negative control, plus a deliberate bug in the real code).

## Practice exercises
1. Remove the `try`/`finally` from `lifespan`. Predict which single test fails, then run it.
2. Remove the `HTTPException` handler. Predict the number of failures (404 + five 405 cases = 6),
   then check; does `test_every_request_leaves_one_parseable_json_access_line` also fail? Why not?
3. Make `_sse_events` yield `[DONE]` before the sources block. Which test catches it, and which
   existing test file would *not* have?
4. Fix one xfail gap (add `le=` to `k`) and watch the strict `XPASS` fail the suite; then remove
   the marker.
5. Write a Hypothesis test: for any text `t`, a request either gets a 422 or the retriever sees
   exactly `t.strip()`. Does it find anything the parametrized cases missed?
6. Extend `asgi_request` so `receive()` returns `http.disconnect` after N events. When a client
   leaves mid-stream, does the sync generator keep generating in its worker thread? (A real
   production cost on a CPU-bound model.)
7. Set `SLOW_REQUEST_MS` to 50 with `monkeypatch` and use `json_log_lines` to assert a streamed
   `/chat` logs at WARNING in the real JSON — the end-to-end twin of `tests/test_logging.py`.
8. Add CORS middleware and update the 405 test for `OPTIONS /chat`. What should it assert now?
9. Replace the clean-process logging test with a cheaper one that imports only the logging setup.
   What do you lose by no longer importing the real `src.api.main`?
10. Run `pytest tests/test_api.py --durations=10`. Which tests dominate, and what would you cut
    or move behind a marker?

## Sources
- [pytest: skip/xfail (`strict`)](https://docs.pytest.org/en/stable/how-to/skipping.html) and
  [monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)
- [ASGI: HTTP connection scope and messages](https://asgi.readthedocs.io/en/latest/specs/www.html) and
  [lifespan protocol](https://asgi.readthedocs.io/en/latest/specs/lifespan.html)
- [Python `logging.basicConfig`](https://docs.python.org/3/library/logging.html#logging.basicConfig)
  ("does nothing if the root logger already has handlers")
- [FastAPI: Handling Errors](https://fastapi.tiangolo.com/tutorial/handling-errors/) — overriding the
  default handlers; register Starlette's `HTTPException` to catch routing errors
- [Pydantic `ConfigDict`](https://docs.pydantic.dev/latest/api/config/) (`str_strip_whitespace`)
- [Ruff settings: `per-file-ignores`](https://docs.astral.sh/ruff/settings/)
- Read directly from the installed packages: `starlette/testclient.py` (lines 353–367),
  `starlette/responses.py` (`StreamingResponse.__call__`), `httpx/_transports/asgi.py`, and
  `uvicorn/protocols/http/h11_impl.py:203` (`spec_version "2.3"`).
- Earlier steps this builds on: [docs/35](<../docs/35 chat_streaming_deep_dive.md>) C4,
  [docs/36](<../docs/36 error_handling_deep_dive.md>) C1/C2/C4,
  [docs/37](<../docs/37 logging_observability_deep_dive.md>) C3.
