# API contract (P7-M7)

What a client of this API may rely on, written clause by clause. **Every clause names the check
that enforces it**: a sentence here that no check backs is a bug in this document, and a behaviour
a check pins that is not written here is a bug in it too. Written for someone building a frontend
(or any other client); the *why* behind the design is in [docs/34](<34 fastapi_skeleton_deep_dive.md>)
to [docs/38](<38 api_documentation_deep_dive.md>), real captured requests and responses are in
[docs/api-usage.md](api-usage.md), and the machine-readable twin of this page is `/openapi.json`.

**How "verified" is meant.** Three independent layers check the clauses, so no single blind spot
(mine included) can hide a defect:

| Layer | What it is | Where |
|---|---|---|
| Hermetic tests | the real app in-process, with scripted stand-ins for the model and retriever; run in CI | `tests/test_api.py` (and the five per-concern API test files) |
| The contract checker | a frontend simulator that drives a *running* server over HTTP, clause by clause (`PASS` / `FAIL` / `SKIP` / known `GAP`), against the real app with a scripted model (**deterministic tier**) and against the real Qdrant + Neo4j + Ollama stack (**real tier**) | `practice codes/phase 7/m7_api_contract_checkpoint/check_api_contract.py` |
| A real browser | a page that uses `fetch` + `ReadableStream` with its own SSE parser, a Stop button, and `EventSource`; served same-origin by the stub server and cross-origin by a second server | `…/web/frontend_sim.html` (steps B1-B9) |

## 1. The surface

| Method and path | Purpose | Success | Other outcomes |
|---|---|---|---|
| `GET /health` | liveness, plus which backing services this instance targets | `200` JSON | `405` for any other method |
| `POST /chat` | a grounded, cited answer, streamed | `200` `text/event-stream` | `400`, `422` before streaming starts; `500`, `504` declared (E5); a failure *after* the `200` starts is an in-band `[ERROR]` event (S7) |
| `GET /docs`, `/redoc`, `/openapi.json` | interactive docs and the machine-readable contract | `200` | |

`POST /chat` takes `{"query": <string, at least 1 character after trimming surrounding
whitespace>, "k": <integer greater than 0, default 3>}` with `Content-Type: application/json`.
Unknown fields are ignored. A body labelled anything else (for example `text/plain`) is a `422`.

## 2. The clauses

"Checker" is a clause id in `check_api_contract.py` (D = deterministic tier, R = real tier);
"Test" is a hermetic test in `tests/test_api.py` unless another file is named; "Browser" is a step of
`frontend_sim.html`.

### A: discovery

| Id | Promise | Checker | Test | Browser |
|---|---|---|---|---|
| A1 | `GET /health` is `200 application/json` with `status: "ok"` and the three service URLs, and matches its declared `HealthResponse` (all four fields required) | A1 (D, R) | `test_health_matches_its_declared_schema` | B1 |
| A2 | `/openapi.json` describes `GET /health` and `POST /chat`; `/chat` declares exactly `200 400 422 500 504`; its `200` is `text/event-stream` only; every declared JSON response has a schema **and** an example, and the example validates against the schema | A2 (D, R) | `test_every_declared_json_example_validates_against_its_own_declared_schema`; `tests/test_openapi.py` | |
| A3 | `GET /docs` serves the interactive documentation (`200 text/html`) | A3 (D, R) | `tests/test_openapi.py::test_docs_redoc_and_openapi_json_all_load` | |

### S: the streaming success path

| Id | Promise | Checker | Test | Browser |
|---|---|---|---|---|
| S1 | `POST /chat` answers `200`, `Content-Type: text/event-stream; charset=utf-8`, chunked, with **no** `Content-Length` (one would mean the body was buffered) | S1 (D, R) | `test_the_declared_success_response_is_the_observed_one`, `test_unicode_survives_the_round_trip_and_the_stream_declares_utf8` | B2 |
| S2 | The body is plain SSE: every non-blank line starts with `data:` (the server never sends `event:`, `id:`, `retry:` or comments), every event is a `message`, and two independent parsers (a hand-written WHATWG one and `httpx-sse`) read the same events. Held for an ordinary stream **and** a stream full of awkward line endings | S2 (D, R) | `test_the_reference_sse_parser_follows_the_spec` (pins the reference parser itself) | B3 |
| S3 | Exactly one terminal event ends every stream, and nothing follows it: `[DONE]` on success, or `[ERROR] <message>` | S3 (D, R) | `test_every_stream_ends_with_exactly_one_terminal_event_and_it_is_last` | B2, B4 |
| S4 | A completed answer ends with its source list, then `[DONE]`. The list is one event whose data is `\n\nSources:\n[1] <title> (<path>)\n[2] ...`, numbered 1..n with 1 <= n <= k. It is sent only after a complete generation, so a stream that ends in `[ERROR]` has none | S4 (D, R) | `test_a_real_question_streams_a_real_grounded_source_list` | B2 |
| S5 | Events arrive as the model produces them, not buffered into one burst at the end | S5 (D) | `test_sse_fragments_reach_the_client_as_they_are_generated` | B2 |
| S6 | A line break inside a model fragment (`\n`, `\r\n` or a lone `\r`) arrives as one `\n`, and nothing else about the text changes (U+2028, `\x0b`, `\x0c`, `\x85`, `\x1c` and a trailing newline all survive). No fragment can end its own event, start another, or inject a field. No `\r` is ever on the wire | S2, S6 (D) | `test_a_line_ending_inside_a_fragment_can_neither_split_nor_forge_an_sse_event` (9 cases) | B3 |
| S7 | A failure after the `200` has started is the stream's last event, `[ERROR] <message>`, on the same `200`; `[DONE]` never follows it | S7 (D) | `test_a_retrieval_failure_is_reported_in_band_and_the_model_is_never_called` | B4 |
| S8 | A question that retrieves nothing streams the single refusal sentence, then `[DONE]`, with no source list, and the model is never called | S8 (D) | `test_a_genuinely_empty_index_streams_the_real_refusal` | |
| S9 | A client that walks away stops the server *asking the model for more*: at most the one fragment already in flight is still produced | S9 (D) | `test_a_client_that_walks_away_stops_the_model_being_asked_for_more` | B5 (+ the stub's yield log) |
| S10 | A slow stream blocks neither other requests (`/health` stays in milliseconds while a stream runs) nor other streams | S10 (D) | `test_a_slow_chat_stream_does_not_block_other_requests` | |
| S11 | `k` decides how many sources are cited | S11 (D) | `test_the_requested_k_decides_how_many_sources_are_cited` | |
| S12 | **Known gap G1.** A client that walks away has the model's stream *closed at once*, releasing the connection to the model server | S12 (D) reports `GAP` | `test_a_client_that_walks_away_gets_the_model_stream_closed_at_once` (strict `xfail`) | |

### E: errors

All of them use one envelope, `{"error": {"code": <slug>, "message": <text>}}`, built from the same
`ErrorResponse` model that `/openapi.json` declares.

| Id | Promise | Checker | Test | Browser |
|---|---|---|---|---|
| E1 | A body that is not JSON is a `400` with code `invalid_json`; declared | E1 (D, R) | `test_every_error_chat_declares_matches_what_its_handlers_really_send[400]` | B6 |
| E2 | Valid JSON that breaks a field's rules is a `422` with code `validation_error`, and `message` names every failing field as `body.<field>: <reason>`, joined with `; `. Covered: `{}`, an empty or blank `query`, `k` of 0, -3 or 2.5, a numeric `query`, an array body, a `text/plain` body; declared | E2 (D, R) | `[422]` of the test above; `test_a_body_that_is_not_a_labelled_json_object_is_a_422_envelope`; `test_a_whitespace_only_query_is_a_422_before_anything_streams` | B6 |
| E3 | A wrong method is a `405` with code `method_not_allowed` and an `Allow` header naming the accepted method | E3 (D, R) | `test_a_wrong_method_is_a_405_envelope_that_still_names_the_allowed_method` | B6 |
| E4 | An unknown path is a `404` with code `not_found` | E4 (D, R) | `test_an_unknown_path_is_a_404_in_the_error_envelope` | B6 |
| E5 | `500` (`internal_error`) and `504` (`upstream_timeout`) are declared with the envelope. Neither can be reached from `/chat` on a healthy server, so the checker reports `SKIP` and the observed side is pinned in-process | A2 (declared side) | `[500]` and `[504]` of the test above | |
| E6 | `POST /chat/` (trailing slash) is answered with a `307` to `/chat`, method preserved | E6 (D, R) | `test_a_trailing_slash_is_redirected_with_a_307_that_preserves_the_method` | |

`404`, `405` and `307` are raised by routing for every path, so, as is conventional, they are not
declared per route in `/openapi.json`; the envelope still applies to the first two.

### X: cross-cutting

| Id | Promise | Checker | Test | Browser |
|---|---|---|---|---|
| X1 | Every response the checker observed matched what `/openapi.json` declares for it: status, media type, schema validity, and every `required` property present. A schema with *no* `required` property does not count as a match, because it would accept any object (this was true of FastAPI's stock 422 schema before P7-M7) | X1 (D, R) | `assert_matches_the_declared_contract`, used by the tests above | |
| X2 | The API sends **no CORS headers**, and `OPTIONS /chat` is a `405`. A browser page on another origin can neither read a response nor send a JSON `POST`; only same-origin and server-side clients work (decision D-2026-10-03-15) | X2 (D, R) | the `OPTIONS` case of the 405 test above | B8, B9 |

## 3. What a client author needs to know

1. **Read the body as a stream and parse SSE yourself.** Lines end with `\r\n`, `\r` or `\n`; a blank
   line ends an event; strip *exactly one* space after `data:`; join an event's `data:` lines with
   `\n`. The server only ever sends `data:` lines (S2).
2. **Check the HTTP status before parsing.** `200` is the stream; `400` and `422` are JSON envelopes
   that arrive instead of a stream.
3. **Branch on each event's whole data:** `[DONE]` ends a successful answer, a string starting with
   `[ERROR]` ends a failed one (the message follows the space), anything else is answer text to
   append. The source list is the last text event of a completed answer and starts with `\n\nSources:\n`.
4. **A stream that ends with no terminal event is a failure**, whatever the status said: it is what
   a dropped connection or a proxy timeout looks like.
5. **Fragments are raw model tokens**, not words (`Lanc` / `aster`), and may begin with a space.
   Inline citations like `[1]` are the model's own; the appended source list is the reliable one.
6. **Do not use `EventSource`**: it only issues `GET`, and `/chat` is `POST` (browser step B7). Use
   `fetch` and read `response.body`; `AbortController` is the Stop button (B5).
7. **There is no heartbeat** (G6): nothing is sent until the model's first fragment, which on this
   CPU-only host takes minutes (measured: a cold-model run produced no fragment at all within 302 s and ended `[ERROR] timed out`; a warm run took 289 s in all). Set client and proxy timeouts accordingly.

**In this repository** the first client is the Streamlit chat page (P8-M1): `src/ui/client.py` implements
items 1 to 5 above, and `tests/ui/test_client.py` checks them from the consumer's side, by replaying clause
S6's awkward fragments and by running the client against the real app in-process. It also shows what the
gaps mean for a user: a 330 s read timeout and a visible clock for the silent wait (G6), an input locked
while a question is in flight (G1, G8), and the model's text shown literally rather than as markdown.

## 4. Known gaps

Each gap is **executable documentation**: a strict `xfail` in the hermetic suite and/or a clause the
checker reports as `GAP`, so the day one is fixed the suite or checker goes red and demands this
section be updated on purpose. None contradicts something documented earlier; they are behaviours
nobody had written down, found by checking.

**G1: an abandoned stream's model connection is released only when the cyclic garbage collector
runs.** When a client disconnects mid-stream, Starlette cancels the response task and the server
stops asking for fragments (S9 holds). But the generator chain that holds the model connection,
`_sse_events` → `RAGPipeline.stream` → `LocalLLM.stream` → the Ollama client's streaming response,
is left *suspended, not closed*, until a garbage-collection pass breaks the reference cycle the
cancellation leaves behind.
- *Evidence.* On the stub with the GC on, the generator was still not closed 60 s after the client
  left, three generators suspended and held by frames, cells, a tuple and a list; `gc.collect()`
  closed them at once (the stub logs `closed completed=False`). A real browser's Stop button shows
  the same: after B5 the stub's log has a `closed` line for every stream that ended normally and
  none for the aborted one, a minute later. On the real stack: **unmeasured**. The probe meant to measure it (`real_stack_probes.py abandon`,
  against the unmodified server) never saw a fragment: the API's own 300 s timeout ended the stream first
  (see G8 for what that showed), so no client walked away mid-stream. A control shows Ollama itself stops
  within seconds when a client leaves it directly (CPU 224 % at t+1.3 s, 0.0 % at t+3.3 s), so an open
  connection is what would keep it busy.
- *Pinned by.* `test_a_client_that_walks_away_gets_the_model_stream_closed_at_once` (strict xfail,
  with the cyclic GC disabled so the result cannot be luck), its positive control
  `test_the_abandoned_model_stream_is_closed_once_the_cyclic_gc_runs`, and checker clause S12.
- *Candidate fix, verified but **not applied** (the contract-scope decision D-2026-10-03-14 fixes
  only what contradicts a documented promise).* Give the `StreamingResponse` a background task that
  closes the generator once the response ends however it ended:
  `StreamingResponse(events, media_type="text/event-stream", background=BackgroundTask(events.close))`.
  Starlette runs `background` after a disconnect-cancelled stream, and the cancellation waits for the
  in-flight `next()`, so `close()` cannot race a running generator. Applied to the stub it makes S12
  report "the known gap is gone"; applied in-process it turns the strict xfail into an `XPASS`; the
  eight neighbouring streaming tests still pass; and on the real stack it is **not yet verified** (`real_with_g1_fix.py` and
  `real_stack_probes.py abandon` exist for exactly that comparison; use a smaller `k` so a fragment
  arrives before the 300 s timeout).
- *Flip.* Apply the fix (and remove the markers) before this API serves more than one user at a time
  against a single model.

**G2: one answer at a time (expected, not measured).** The two-simultaneous-users probe
(`real_stack_probes.py two-users`) was written but **not run**. What is known: Ollama 0.20.0 runs here with
no `OLLAMA_NUM_PARALLEL` override on 8 CPUs and 8.1 GB RAM, one real answer takes about 290 s and uses
about four cores, and the API's Ollama client has a 300 s read timeout, so a second simultaneous question
is expected to queue behind the first and may end `[ERROR] timed out`. That is an expectation, not a result.

**G3: no request-size limits.** `k` has no upper bound (`k = 10**9` reaches the retriever) and
`query` has no maximum length. Pinned by strict xfails `test_an_absurdly_large_k_is_rejected` and
`test_an_enormous_query_is_rejected`; picking real numbers deserves its own measured step.

**G4: the SSE response is not proxy-safe.** It sets neither `Cache-Control: no-cache` nor
`X-Accel-Buffering: no`, so a proxy in front of it is free to cache or buffer the stream. Pinned by
`test_the_sse_response_is_safe_to_put_behind_a_proxy` (strict xfail); the checker lists the missing
headers in S1's evidence.

**G5: `HEAD /health` is a `405`**, which some load balancers use as a liveness probe. Pinned by
`test_health_answers_a_head_probe` (strict xfail).

**G6: no keep-alive during silences.** Nothing is sent before the model's first fragment, and a
fragment can be minutes away on this host (measured: a cold-model run produced no fragment at all within 302 s and ended `[ERROR] timed out`; a warm run took 289 s in all). A reverse proxy whose read timeout is shorter than such a
silence (see nginx's [`proxy_read_timeout`](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout); its default, which I recall as 60 s, was not verified in this session),
would cut such a stream, and the client would see it as a stream with no terminal event (section 3, item 4).
A comment line (`: keep-alive`) every few seconds would fix it without changing what clients parse
(S2 would need to allow comments). Not tested; recorded here so the next person to put a proxy in
front of this knows.

**G7: the terminal markers are in-band.** A fragment whose entire text is `[DONE]`, or that starts
with `[ERROR]`, is indistinguishable from a terminator. Never observed (real tokenizers split such
text into several tokens, `[`, `DONE`, `]`), and not tested. If the API ever moves to named events
(`event: done`) this disappears.

**G8: after the API's own timeout fires, the model keeps working for a while (measured once).** With the broad question and `k = 5` no fragment arrived within the API's 300 s generation timeout, so the stream ended with its in-band `[ERROR]` at 301.3 s (the abandon probe saw 1 event and no client ever walked away mid-stream, which is why it is not a G1 measurement). Ollama nevertheless stayed at about 390 % CPU for another ~46 s and was idle at ~48 s: it only notices that its client has gone when it next writes, and prompt evaluation on this CPU-only host is not interruptible. One run, one host, not pinned by any test. It also shows that a larger `k` can push the first fragment past the timeout, so this question did not complete in that run.

## 5. Decisions this contract encodes

| Decision | Where |
|---|---|
| Verify with two clients, a Python simulator and a real browser | D-2026-10-03-10 |
| Fix only what contradicts a documented promise; document and pin the rest | D-2026-10-03-14 |
| No CORS: server-side clients only, until a policy is chosen on purpose | D-2026-10-03-15 |
| Split fragments on exactly `\r\n`, `\r`, `\n` and re-join with `\ndata: ` | D-2026-10-03-16 |
| One `ErrorResponse` model both declares and builds every error body | D-2026-10-03-17 |
| Known gaps are executable: strict `xfail` / a `GAP` verdict that flips to `FAIL` | D-2026-10-03-18 |

## 6. Re-running the verification

```bash
# the in-process tests (CI runs these)
.venv/Scripts/python -m pytest tests/test_api.py -q -m "not live"

# deterministic tier: the REAL app with a scripted model, on a free port (about 12 s)
.venv/Scripts/python "practice codes/phase 7/m7_api_contract_checkpoint/check_api_contract.py" --tier deterministic

# prove the checker can fail: eight mutants of the app, one deliberate bug each, plus an unmutated baseline (about 2 minutes)
.venv/Scripts/python "practice codes/phase 7/m7_api_contract_checkpoint/run_mutants.py"

# real tier: needs `docker compose up -d qdrant neo4j ollama` and the API running; minutes, because
# one real answer is generated, and a generation that times out is tolerated and reported
.venv/Scripts/python -m uvicorn src.api.main:app --port 8000
.venv/Scripts/python "practice codes/phase 7/m7_api_contract_checkpoint/check_api_contract.py" --tier real --base-url http://127.0.0.1:8000

# real-stack one-offs behind G1 and G2 (each waits for Ollama to go idle first)
.venv/Scripts/python "practice codes/phase 7/m7_api_contract_checkpoint/real_stack_probes.py" abandon --base-url http://127.0.0.1:8000
```

For the browser leg, start the stub (`stub_server.py`, its docstring has the command) and open
`http://127.0.0.1:<port>/web/frontend_sim.html?auto=1`; for the cross-origin leg serve the `web/`
folder from a second port (`python -m http.server 8090 --directory web`) and open
`http://localhost:8090/frontend_sim.html?api=http://127.0.0.1:<port>&auto=1`. The page writes its
verdicts to the table and to `document.title`.
