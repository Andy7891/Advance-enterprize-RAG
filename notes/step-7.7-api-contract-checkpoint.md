# Phase 7, Microstep 7 — Integration Checkpoint: Verify API Contract

**Date:** 2026-10-03
**Core learning: Yes.** The first step that checks an API *as its client sees it*: two independent
SSE parsers, a real browser, a declared-vs-observed schema check, and a checker that is itself shown
to fail. It found two defects in code I had called documented and correct, and one gap nobody had
written down.

## What was asked vs. what this step closes
The task: *verify the API works with client requests (simulate a frontend); input: the running app;
output: a verified API contract.* The API already had ~90 tests, so "more tests" was not the answer.
The question was different: what does a **client** observe, and does it match what the API *says*
(`/openapi.json`, `docs/api-usage.md`, my own decision log)? So this step plays the client twice,
a Python frontend simulator and a real browser page, and writes the result down as a contract in
which every clause names the check that enforces it ([docs/api-contract.md](../docs/api-contract.md)).

## What checking found
1. **A lone `\r` in model text corrupted and could forge SSE events.** `_sse_events` split fragments
   on `\n` only, but SSE has three line terminators. A fragment `"bare\rcarriage"` reached the
   client as `bare` + `carriage` (two lines of different events' worth of damage), `"inject\revent:
   error"` made the client see an `event: error` field, and `"a\r\rdata: forged"` produced a second,
   forged event. Reproduced with two independent parsers (`httpx-sse` and a hand-written WHATWG one)
   *before* touching the code. My own `docs/35` had called the one `.replace("\n", ...)` line "the
   entire difference between correct and broken framing", an overclaim, now corrected in place.
2. **The declared contract did not match the observed one.** `/openapi.json` described `/chat`'s 422
   with FastAPI's stock `HTTPValidationError` (`{"detail": [...]}`), a body this app stopped sending in
   P7-M3 (I had even written a comment saying the default was "already correct"), and described
   400/500/504 with an example but no schema. A first validation of observed bodies against those
   schemas *passed*, vacuously: a schema with no `required` keyword accepts any object.
3. **A browser cannot call this API from another origin, and cannot use `EventSource`.** Verified in a
   real browser, not inferred: `No 'Access-Control-Allow-Origin' header is present` for a `GET`, a
   failed preflight for a JSON `POST` (`OPTIONS /chat` is a 405), and `new EventSource('/chat')`
   errors at once with `readyState 2` because `EventSource` can only `GET`.
4. **A client that walks away stops the server asking the model, but the model's stream is not
   closed** until a garbage-collection pass runs (gap **G1**). Found because my new checker's S9
   clause *failed* on its first run (it asserted the generator was closed), which I then
   investigated for longer than I should have: see finding 3 of "Findings".
5. **On the real stack** the first real-tier run produced no answer at all within the host's 300 s
   ceiling (`[ERROR] timed out`, tolerated and reported); a warm retry completed (19 events, 289 s, `[DONE]`,
   three real sources), so S1-S4 pass on a real answer. The probe meant to quantify G1 never saw a
   fragment (with `k = 5` the API's 300 s timeout ended the stream at 301.3 s), but it showed something else:
   Ollama stayed at ~390 % CPU for another ~46 s, because it only notices a departed client on its next
   write (recorded as gap **G8**). G1's real-stack cost is therefore **unmeasured**.

## What was built
- **Two production fixes, each a failing test first** (12 red, then green): `_sse_events` now splits on
  exactly `\r\n`, `\r` and `\n` (`_SSE_LINE_END`) and re-joins with `\ndata: ` (a CR or CRLF arrives as
  one `\n`); `ErrorBody`/`ErrorResponse` models are both what `/openapi.json` declares for `/chat`'s
  400, 422, 500 and 504 and what builds the real error bodies, which also removes FastAPI's stock
  422 documentation.
- **28 new test items in [`tests/test_api.py`](../tests/test_api.py)** (59 → 87: 80 hermetic passing,
  5 strict xfails, 2 live): a spec-compliant SSE reference parser *with its own nine spec tests*, nine
  line-ending cases (four of them exist to catch the obvious wrong fix, `str.splitlines()`), four
  declared-vs-observed error tests with the vacuity guard, three more declared-contract tests, and
  three abandon tests (what holds, a positive control, and the strict xfail for G1).
- **Tooling in [`practice codes/phase 7/m7_api_contract_checkpoint/`](<../practice codes/phase 7/m7_api_contract_checkpoint>)**:
  `scenarios.py` (scripted model behaviours), `stub_server.py` (the REAL app with only its lifespan
  swapped), `check_api_contract.py` (23 clauses, two tiers, `PASS`/`FAIL`/`SKIP`/known `GAP`),
  `mutant_stub.py` + `run_mutants.py` (8 deliberate bugs + a baseline), `web/frontend_sim.html` (nine
  browser steps), `real_stack_probes.py` and `real_with_g1_fix.py` (real-stack measurements).
- **Docs:** `docs/api-contract.md` (new, normative); `docs/api-usage.md` (SSE reading rules, a
  browser section, the declared error schema); a correction note in `docs/35`; decisions
  D-2026-10-03-10 to -20; two dev pins (`jsonschema`, `httpx-sse`).

## Decisions (all "recommended", D1-D7 as presented, plus what the work added)
| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| D1 Clients | Python simulator + a real browser | one of them | each sees what the other cannot (CORS vs. millisecond timing and CI) |
| D2 Artifacts | tooling in `practice codes/`, contract doc, regression tests in `tests/` | checker in CI; doc only | the checker spends minutes of CPU; the defects it found must still be pinned in CI |
| D3 Tiers | real app + scripted model, and the real stack, plus one-offs | real stack only | a CPU-only model made the real tier a lottery: its first run ended `[ERROR] timed out` as its only event |
| D4 Declared vs observed | status + media type + schema + every `required` + a vacuity guard | status only | a schema without `required` accepts anything |
| D5 Fix scope | only what contradicts a documented promise | fix everything; fix nothing | the step verifies; each other gap is pinned so it cannot go stale |
| D6 CORS | not added | wildcard; allow-list | an unauthenticated, minutes-per-request API must not be drivable by any web page |
| D7 SSE fix | split on `\r\n`/`\r`/`\n`, re-join with `\ndata: ` | `splitlines()`; strip `\r` | `splitlines()` also splits U+2028 and drops a trailing newline; stripping deletes text |
| (new) known gaps | strict xfail + a `GAP` verdict that flips to `FAIL` | prose list | prose goes stale silently |
| (new) G1 | pinned, fix verified, **not applied** | fix it here | outside D5; a few lines when more than one user shares the model |

## Verified, not assumed
```
independent parsers, BEFORE the fix  : hand-written WHATWG parser and httpx-sse agreed the output was
                                       wrong: "bare\rcarriage" -> "bare"; "inject\revent: error" ->
                                       event type "error"; "a\r\rdata: forged" -> two events
parser self-test                     : first run FAILED -- my own reference parser dispatched an unfinished
                                       event (empty remainder after the last terminator); fixed with [:-1]
red -> green                         : first run 12 red: 11 for the predicted production reasons (6 line-ending
                                       cases, 4 declared-error cases, 1 declared-examples) and 1 that was my own
                                       parser's bug; the 504 case had first failed for a WRONG reason (my
                                       provocation hit a 500 before reaching the patched call: no pipeline was
                                       installed) and was fixed in the test before the production code changed;
                                       all green after the two production fixes
wrong-fix check (splitlines)         : a scratch plugin swapping in str.splitlines(): 4 of the 9 cases fail
                                       (trailing CR, U+2028, other separators, trailing LF)
suite                                : tests/test_api.py 80 passed, 5 xfailed, 2 live deselected (was 53 passed,
                                       4 xfailed, 2 live)
deterministic tier                   : final 23-clause run (E6 included): 21 PASS, 1 known GAP (S12), 1 SKIP (E5),
                                       0 FAIL; the unmutated baseline of the mutation run was also fully green
mutation run                         : 9 of 9 as expected (baseline all green; each of 8 mutants turns exactly
                                       its clauses red; the G1 fix turns S12 into "the known gap is gone")
G1 fix, in-process                   : with the candidate fix the strict xfail XPASSes (suite would demand the
                                       marker's removal); 8 neighbouring streaming/concurrency tests still pass
browser, same origin                 : 7 pass, 0 fail, 2 skip (B8/B9 are cross-origin only); framing fragments
                                       rebuilt exactly by the browser's own parser; first event 160 ms, fifth 765 ms
browser, other origin                : B8, B9 pass (blocked); console: "No 'Access-Control-Allow-Origin' header
                                       is present" (GET) and "Response to preflight request doesn't pass access
                                       control check" (JSON POST)
G1 in the real browser               : after the Stop button, the stub model produced one more fragment and then
                                       no more, and logged no "closed" line for that stream a minute later
real tier, cold model                : 12 pass, 0 fail, 2 skip; the real answer ended "[ERROR] timed out" after
                                       302 s as its ONLY event (tolerated, reported); S4 had nothing to judge
real tier, warm model                : S1-S4 PASS on a completed real answer: 19 events in 289 s ending [DONE];
                                       sources [1] company/about.md, [2] employees/Avery
                                       Lancaster.md, [3] employees/Alex Harper.md
real-stack one-offs                  : control (a client leaves Ollama directly): CPU 224 % at t+1.3 s, 0.0 % at
                                       t+3.3 s. Abandon probe, unmodified server, k=5: the only event came at 301.3 s
                                       (the API's own timeout); Ollama stayed ~390 % until t+46 s, idle at t+48 s (G8).
                                       The fixed-server comparison and the two-users probe were NOT run (usage limit)
CI gates                             : all green at the end: ruff check, ruff format --check (88 files), mypy src/
                                       (37 files), and the CI selection `pytest tests/ -m "not tiktoken and not live"`:
                                       813 passed, 98 deselected, 5 xfailed (was 786 passed, 4 xfailed before P7-M7)
```

## Findings
1. **Be the client, twice, with parsers you did not write.** The `\r` defect was invisible to every
   test that parsed the stream with the same assumption the server framed it with. It surfaced only
   when two parsers written from the specification (one third-party, one mine) read the real bytes.
2. **An independent oracle is only independent if it is itself tested.** My hand-written reference
   parser had a bug of its own (it treated the empty remainder after the last line end as a blank
   line); a nine-case spec test caught it on its first run. Without that test my "independent" parser
   would have shared a flaw with nothing at all and still looked authoritative.
3. **Instrumentation can lie, and the first surprise is rarely the answer.** S9's failure ("the
   generator was never closed") was real, but while explaining it I spent four probes on a debug server
   that imported an *old* `stub_server.py` from my scratch directory (it was first on `PYTHONPATH`), so
   its log never showed a `closed` line even after a manual `close()`. The clean first probe had already
   given the right answer. The habit: when a result contradicts the previous one, print `__file__`
   before theorising.
4. **A checker that has never been seen to fail proves nothing.** The mutation run was green on the
   baseline and red on exactly the right clauses, *after* it corrected three of my own expectations:
   S2 could not see a CR in a stream that contains none (so I made it also check the awkward stream),
   buffering also removes S10's premise (a stream to hold open), and a blocking generator on the event
   loop collapsed S5's spread to 0.00 s (healthy: 0.63 s) on this host's proactor loop.
5. **A schema with no `required` keyword is a rubber stamp.** Validating observed bodies against
   FastAPI's stock 422 schema passed; the guard "a schema must require something" is what made the
   check mean something.
6. **A gap is not a bug, and not nothing.** G1 is real but undocumented before today, so under the
   chosen scope it is pinned (strict xfail, a `GAP` verdict) and its fix is verified but not applied;
   the day it is fixed, both go red on purpose.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **SSE framing per the WHATWG spec**: three line terminators, `field: value` parsing with exactly
  one leading space removed, an event dispatched by a blank line, an unfinished event dropped. A
  server that puts model text into a line-oriented protocol must neutralise *every* line terminator,
  or the text can add fields and events (the same class of bug as header injection).
- 🔴 **Generator lifecycle under cancellation**: `close()` throws `GeneratorExit` at the suspended
  `yield` so `finally` blocks run; dropping the last reference does it via the finaliser, but a
  reference cycle (here, a cancellation exception whose traceback holds the frame that holds the
  generator) postpones it to the cyclic GC. Anything a generator holds open (a connection to the model)
  stays open meanwhile.
- 🟡 **Declared vs. observed**: compare what an API says about itself with what it does; a JSON Schema
  check needs a vacuity guard.
- 🟡 **CORS in one paragraph**: a cross-origin request is *sent* if it is "simple" (a safelisted
  content type such as `text/plain`) but its response is unreadable without
  `Access-Control-Allow-Origin`; anything else (a JSON `POST`) is preceded by an `OPTIONS` preflight.
  `EventSource` is GET-only and has no custom headers.
- ⚪ Mutation testing of a verifier; strict `xfail` and a `GAP` verdict as executable documentation;
  `fetch` + `ReadableStream` + `AbortController`.

## Honest limitations
1. **The first real-tier run produced no answer at all** (cold model, 302 s, `[ERROR] timed out`); the
   source-list clause (S4) was judged only on a warm retry: it passed there (19 events, 289 s, three real sources).
2. **G1's mechanism is inferred, not traced to the exact reference cycle.** Evidence: all three
   generators suspended and held by frames, cells, a tuple and a list; freed by `gc.collect()`; not
   closed in 60 s idle. The Starlette code path (a cancelled `iterate_in_threadpool` frame) is
   consistent with it, not proven.
3. **The proactor-loop explanation for S5 under a blocking generator is consistent, not proven.** What
   was measured: the events arrive in one burst (spread 0.00 s) on `WindowsProactorEventLoopPolicy`.
4. **One browser engine** (the built-in Chromium-based pane); Firefox and Safari were not run.
5. **The deterministic tier checks the protocol, not the model's answers.** Answer quality belongs to
   P6.
6. **The checker is not in CI** (D-2026-10-03-11): only what it found is pinned there.
7. **CPU numbers describe this host** (CPU-only Mistral-7B; Docker sees 8 CPUs and 8.1 GB RAM and
   generation uses about four cores, ~400 % in `docker stats`; Ollama 0.20.0 with no
   `OLLAMA_NUM_PARALLEL` override); the shape transfers, the digits do not.
8. **Redirect behind a proxy is unverified**: the `307` for a trailing slash carries an absolute
   `Location` built from the request's `Host`, which behind a TLS-terminating proxy can name the wrong
   scheme or host.
9. **Gap G6 (no heartbeat) and G7 (in-band markers) are reasoned, not tested.**

## How this connects
**Previous:** P7-M6 pinned the API's behaviour in-process and found three defects by probing. **This
step:** checks the API from outside, as a client, and writes the result down as a contract that names
its own checks. **Next (not yet specified):** if `/ingest` or `/query` are added they inherit the
declared-vs-observed check, the error envelope and the route-table test; if a frontend is built, G1,
G6 and the CORS decision come first.

## How to do this yourself
1. To verify an API, **write the client**, and parse its output with something you did not write.
2. For each promise the API makes about itself (its schema, its docs, its error shape), **check the
   promise against the behaviour**, and make the check unable to pass vacuously.
3. **Show every verifier failing**: mutate the code under test one bug at a time and require the right
   clauses, and only those, to go red.
4. When you find something that contradicts nothing you wrote down, **pin it instead of silently
   fixing it**: a strict `xfail` or a `GAP` verdict that flips the day it closes.

## Practice exercises
1. Run `check_api_contract.py --tier deterministic` and read S6's evidence line. Then change
   `_SSE_LINE_END` in `src/api/main.py` back to `re.compile("\n")`. Predict which clauses and which
   tests fail before you run them.
2. Run `run_mutants.py splitlines-fix`. Which four of the nine line-ending cases fail, and what does
   each one teach about `str.splitlines()`?
3. Add a scenario to `scenarios.py` whose fragments include the empty string `""`. Predict the exact
   bytes the server sends, then confirm with `curl -N` piped through `od -c`.
4. Write a Hypothesis test: for any text `t`, parsing the server's framing of `t` yields exactly `t`
   with CR and CRLF replaced by LF. Does Hypothesis find a case your nine did not?
5. Implement the G1 fix in `src/api/main.py` (the candidate is in `real_with_g1_fix.py`). Watch the
   strict `XPASS` fail the suite, remove the marker, and watch S12 turn into "the known gap is gone".
6. Remove `gc.disable()` from the G1 xfail test and run it 50 times. How often does it `XPASS`, and
   what does that tell you about testing code whose behaviour depends on the garbage collector?
7. Add `CORSMiddleware` for exactly one origin. Which clause fails first, which browser steps change,
   and what must X2 say afterwards?
8. Add a `: keep-alive` comment line every few seconds while waiting for the model's first fragment.
   Which clause must change (S2), and why do two independent parsers still agree?
9. Make the stub drop the connection halfway through a response. What does `frontend_sim.html`
   report, and what should rule 4 of "What a client author needs to know" tell a client to do?
10. Put the API behind nginx with its default `proxy_read_timeout` and `proxy_buffering` and run the
    browser page against it. Which known gaps (G4, G6) become visible, and in what order?

## Sources
- [WHATWG HTML Standard: Server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html)
  (the "parsing an event stream" and "interpreting an event stream" algorithms the reference parser
  follows)
- [MDN: Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)
  and [MDN: EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)
- [MDN: Using readable streams](https://developer.mozilla.org/en-US/docs/Web/API/Streams_API/Using_readable_streams)
  and [MDN: AbortController](https://developer.mozilla.org/en-US/docs/Web/API/AbortController)
- [MDN: Cross-Origin Resource Sharing](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)
  (simple requests and preflight)
- [OpenAPI 3.1.0](https://spec.openapis.org/oas/v3.1.0) and
  [JSON Schema: object `required`](https://json-schema.org/understanding-json-schema/reference/object);
  [python-jsonschema](https://python-jsonschema.readthedocs.io/en/stable/)
- [FastAPI: Additional Responses in OpenAPI](https://fastapi.tiangolo.com/advanced/additional-responses/)
  (declaring `model` for a status, and how an explicit entry replaces the default)
- [Starlette: responses](https://starlette.dev/responses/) (`StreamingResponse`, `background`; the
  notes of P7-M3/M4 call `www.starlette.io` "unreachable from this environment": that domain no longer
  resolves, the documentation now lives at starlette.dev);
  [AnyIO: threads](https://anyio.readthedocs.io/en/stable/threads.html) (a cancelled `run_sync` waits
  for the thread)
- [Python: `generator.close()`](https://docs.python.org/3/reference/expressions.html),
  [`gc`](https://docs.python.org/3/library/gc.html),
  [PEP 442: safe object finalization](https://peps.python.org/pep-0442/) and
  [`str.splitlines`](https://docs.python.org/3/library/stdtypes.html) (the full list of boundaries)
- [httpx-sse](https://github.com/florimondmanca/httpx-sse) (the second parser) and
  [`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source) (SSE over `fetch`)
- [nginx `proxy_read_timeout` / `proxy_buffering`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
  and [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)
- Read directly from the installed packages: `starlette/responses.py` (`StreamingResponse.__call__`),
  `starlette/concurrency.py` (`iterate_in_threadpool`), `httpx_sse/_api.py` (its own line decoder).
- Earlier steps this builds on: [docs/35](<../docs/35 chat_streaming_deep_dive.md>) C3/C4,
  [docs/36](<../docs/36 error_handling_deep_dive.md>), [docs/38](<../docs/38 api_documentation_deep_dive.md>),
  [notes/step-7.6-api-tests.md](step-7.6-api-tests.md).
