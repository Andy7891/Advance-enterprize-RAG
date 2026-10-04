# Phase 8, Microstep 1 — Create Basic Streamlit Chat Interface

**Date:** 2026-10-03
**Core learning: Yes.** The first UI, and the first in-repo *client* of the API. It teaches how
Streamlit really runs a script (re-executed on every interaction, interruptible only at certain
calls), how to consume a streaming API correctly, and why "a basic chat page" is not basic when the
first word takes minutes.

## What was asked vs. what this step closes
The task: `src/ui/app.py` with a Streamlit chat UI; inputs: Streamlit and the API endpoint; output: an
interactive chat interface. The API's own facts decided what "basic" had to mean: its first word takes
minutes on this CPU-only host, it keeps no conversation, an abandoned request keeps the model busy
(gaps G1, G8), and everything in the response is untrusted text. The contract written in P7-M7
(`docs/api-contract.md`) is the specification the client is built and tested against.

## Open items carried over from P7-M7, closed first
Before designing anything I ran the two gates P7-M7 had left open: the CI test selection (813 passed,
98 deselected, 5 xfailed, up from 786 + 4) and the 23-clause contract check (21 PASS, 1 known GAP, 1 SKIP,
0 FAIL). Not closed, and not blocking: the real-stack probes for G1 and G2.

## Facts checked in the installed package before deciding (not from memory)
1. **Streamlit 1.61.1** has `chat_input` (with `disabled`, `key`, `max_chars`), `chat_message`,
   `write_stream`, `spinner(show_time=True)` and the `AppTest` harness.
2. **`st.write_stream` renders text as markdown** (`stream_container._markdown(...)`), so `$…$` is
   maths, `![](url)` is an image the browser fetches, and `:red[x]` is a colour directive.
3. **An interrupt (a new submit, Stop, a rerun) is honoured only at the next `st.*` call.**
   `script_runner.py` hands `_maybe_handle_execution_control_request` to the session state as its
   `yield_callback` and to the element queue as `yield_check`. While our code waits on the API for the
   first word, nothing can interrupt it.
4. **`streamlit run src/ui/app.py` puts `src/ui` on `sys.path`, not the repo root**, but the project is
   installed editable, so `import src…` works. `src/ui/` was an empty folder without `__init__.py`.
5. **`response.iter_lines()` loses text.** Measured on httpx 0.28.1: a fragment `sep: after`
   arrives as the two lines `data: sep:` and `after`, and a client that keeps `data:` lines returns
   `sep:`. Same for `\x0b` and `\x85`. (httpx splits like `str.splitlines()`; SSE does not.)
6. **The corpus is full of prices**: 71 chunk files contain `$<digit>` (`$15,000`, `$12,000/month`).
7. **Streamlit listens on every network interface by default** and printed a Network and an External URL.

## What was built
- **[`src/ui/client.py`](../src/ui/client.py)** (300 lines, no Streamlit): `iter_sse_data` (an
  incremental reader following the WHATWG rules), `ask()` (sends the question, checks the status now, so
  a refused connection or a 400/422/500/504 is a `ChatError` before any streaming), `AnswerStream`
  (iterate once for text; then `.text`, `.sources`, `.complete`, `.error`; `close()`), `parse_sources`,
  `check_health`, `explain_stream_error`, and the timeouts.
- **[`src/ui/app.py`](../src/ui/app.py)** (216 lines): the page. A sidebar (API status from
  `/health`, the `k` slider 1–5, New chat), the history, `st.chat_input` capped at 1000 characters, and
  the two-run submit: the first run records the question and reruns with the input disabled; the second
  streams the answer (`st.spinner` with a clock until the first word, then `st.write_stream`), then reruns
  to unlock. `Chat` (messages, pending, busy) is a plain object, `as_plain_text()` escapes everything
  shown.
- **Config:** `Settings.api_url` (env `API_URL`, default `http://localhost:8000`); `.streamlit/config.toml`
  (listen on 127.0.0.1, telemetry off).
- **[`tests/ui/`](../tests/ui)**: 104 tests. 76 for the client (against `httpx.MockTransport`, and against
  the REAL FastAPI app in-process, so it is checked against the server's own framing) and 28 for the page
  under `AppTest`.
- **[`practice codes/phase 8/m1_streamlit_chat_ui/`](<../practice codes/phase 8/m1_streamlit_chat_ui>)**:
  `ui_stub.py` (the P7-M7 stub API plus two scripted answers: `prices` and `slow-start`) and
  `stop_pattern_probe.py` (proves what a `finally` can and cannot do while Streamlit is stopping).

## Decisions (all "recommended")
| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| D1 Streaming | `st.write_stream` | collect then show | one real answer: 289 s; a cold run showed nothing for 302 s |
| D2 Layout | `client.py` + `app.py` + `__init__.py` | one file | SSE logic testable without a Streamlit runtime |
| D3 SSE reading | hand-written WHATWG reader | `httpx-sse`; `iter_lines()` | `iter_lines()` loses text (measured); no new runtime dependency |
| D4 API address | `settings.api_url`, default localhost | constant; sidebar box | one place, Docker can override |
| D5 Sources and errors | expander, plain-language error, health in sidebar | inline; hide sources | a RAG answer is only checkable with its sources |
| D6 History | display only, each question alone | prepend turns; none | the API has no memory; prepending lengthens the prompt (G8) |
| D7 Concurrent submit | lock the input while busy; no Stop | leave enabled; fix G1 first | an interrupt lands only at the next `st.*`; an abandoned request blocks the next |
| D8 Waiting | 330 s read timeout, spinner with a clock | no timeout; short timeout + retry | no heartbeat (G6); a retry starts a second generation |
| D9 `k` | slider 1–5, default 3 | fixed; free number | k=5 timed out once on this host |
| D10 Tests | client unit + `AppTest` + browser run | client only; manual | the page logic is where Streamlit bugs live |
| (new) State | a plain `Chat` object | `st.session_state` keys | cleanup in a `finally` cannot use `st` while Streamlit stops |
| (new) Rendering | every ASCII punctuation escaped | raw markdown | prices become maths; an image link makes the browser fetch a URL |
| (new) Network | 127.0.0.1 only, telemetry off | Streamlit defaults | no login in front of minutes of model work |

## Verified, not assumed
```
gates carried over from P7-M7        : CI selection 813 passed, 98 deselected, 5 xfailed; contract check
                                       21 PASS, 1 GAP, 1 SKIP, 0 FAIL (23 clauses)
tests/ui                             : 104 passed (76 client, 28 page) in ~15 s
red -> green                         : the client tests failed on collection (module missing), then 72 of 72
                                       passed first time; adding spec cases then found a real bug (below)
mutation check, client.py            : 10 deliberate bugs (no truncation check, no CR hold-back, LF-only line
                                       ends, stripped data values, no close in finally, sources left in the
                                       text, read timeout below the API's, health ignoring status, injected
                                       client closed, text dropped after an in-band error): each caught by
                                       the test it should have been
mutation check, app.py               : 16 deliberate bugs (input never locked, busy never cleared, reply lost,
                                       question not trimmed, blank accepted, k ignored, no escaping, typed
                                       fragments unescaped, no explicit close, no unlocking redraw, New chat
                                       inert, text/sources not kept, in-band error ignored, health ignored,
                                       question cap changed): all caught
real browser, scripted API           : plain answer + sources expander; prices/markdown shown literally (no
                                       image, link or bold element); mid-answer failure keeps the partial
                                       text with a plain-language error; no-hits refusal without sources;
                                       awkward line endings rebuilt; API down: sidebar names the address and
                                       how to start it, the question gets a plain error and the input unlocks
busy lock, in the real DOM           : during an answer the text area, send button and New chat are
                                       `disabled`; afterwards all enabled
Streamlit's own Stop button          : run stops, widgets stay drawn disabled until a rerun (press r), then
                                       the history holds the partial answer and nothing is asked again
listen address                       : before: `:::8501` with Network and External URLs; after the config
                                       file: `127.0.0.1:8501`, and the LAN address refuses connections
real stack, real browser             : Streamlit with NO API_URL override (so the new default, localhost:8000)
                                       -> real API -> real Qdrant/Neo4j/Ollama; k set to 1 with the sidebar
                                       slider; "Who founded Insurellm?". The waiting message and its clock were
                                       on screen at 6.2 s, 1 min 45.8 s, 2 min 35.8 s and 3 min 23.2 s, input
                                       disabled throughout. Answer: "Avery Lancaster founded Insurellm [1]." with
                                       "Sources (1)" -> "[1] About Insurellm (company/about.md)". The API's own
                                       access log: POST /chat 200 in 266787.8 ms (4 min 27 s); input enabled again
                                       afterwards. One sample; the first word arrived after 3 min 23 s
CI gates                             : all four green at the end: ruff check; ruff format --check (95 files);
                                       mypy src/ (40 files); `pytest tests/ -m "not tiktoken and not live"`:
                                       917 passed, 98 deselected, 5 xfailed (was 813 passed before the UI tests)
```

## Findings
1. **A `finally` cannot use Streamlit.** The interruption test failed on my first version: after
   `st.stop()` the page was left with `busy=True` and the partial reply unsaved. Probed in isolation
   (`stop_pattern_probe.py`), inside a `finally` while Streamlit is stopping a script: reading
   `st.session_state`, writing it and calling `st.write` all raise `StopException` again, and only plain
   Python works. Every `st.*` call and every session-state access is an interruption checkpoint. Worse
   than a locked input: the next rerun would have seen `busy=True, pending=None` and sent a `None`
   question to the API. The fix is structural: state that cleanup must change lives in a plain object.
2. **A test written from the specification found a bug in code written from the specification.** The
   first reader held back a trailing CR (it might be half a CRLF) but never released it when the stream
   ended, dropping the last event. The server never sends CR, so nothing but a spec case could find it.
3. **`AppTest` only sees the settled tree, which exposed redundant code.** Mutating my page showed that
   an in-run `st.error` and source list were untestable, and invisible to a user: the closing rerun
   redraws everything in milliseconds. Removing them left one renderer for a reply, which is what the
   tests can observe.
4. **Mutation exposed a hidden dependency in my own test.** Removing the page's explicit
   `stream.close()` changed nothing, because the client's generator also closes the connection when an
   exception passes through it. The page's close only matters when the interrupt lands *between*
   fragments, so that case got its own test, holding a reference so the suspended generator cannot be
   finalised by chance (the same lesson as G1, one layer up).
5. **Escape per character, not per pattern.** Tokens arrive in arbitrary pieces: escaping the two-character
   sequence `![` fails when `!` and `[` come in different fragments. Escaping every ASCII punctuation
   character works on each fragment alone.
6. **Defaults expose things.** A development server that happily listens on every interface is a
   decision someone else made for you; here it fronted minutes of CPU-bound work with no login.
7. **The browser tool's emulated viewport and my first clicks misled me for a while.** Typing into the
   chat box silently did nothing under an emulated 1100×800 viewport, and after closing the sidebar the
   box needed a pause before it took focus. A page bug would have shown in the server log; there was
   no request, so it was not the page.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Streamlit's execution model**: the whole file re-runs on every interaction; only
  `st.session_state` survives; a widget interaction *requests* a rerun, which is honoured at the next
  `st.*` call. A chat is therefore state plus a redraw, and a question is two runs.
- 🔴 **Interruption safety**: anything that must happen when a script is stopped (release a connection,
  save a partial answer, unlock the page) has to run on plain Python objects.
- 🔴 **Consuming a streaming API correctly**: incremental decoding, the three line terminators, a CR at a
  chunk boundary, terminal events, a stream that just stops, and releasing the connection yourself.
- 🟡 **Untrusted text in a markdown renderer** (maths, images, directives) and why a per-character
  escape suits streaming.
- 🟡 `st.write_stream` (it also accepts a generator and returns the text), `AppTest` (follows
  `st.rerun()` chains inside one `.run()`; observe transient state by recording widget arguments).
- ⚪ Page config, sidebar, expander, `st.spinner(show_time=True)`, `.streamlit/config.toml`.

## Honest limitations
1. **No Stop button.** The input is locked instead (D7). Streamlit's own Stop works, but leaves the
   widgets drawn disabled until the next rerun (press `r`, or Rerun in the menu): verified, and documented
   here rather than hidden.
2. **G1, G8 and G2 are still open** in the API. The lock keeps this page from creating abandoned
   requests by habit, but a closed tab, a refresh or Streamlit's Stop still abandons one.
3. **A down API costs time**: the sidebar's `/health` check waits up to 2 s on every rerun, and the
   question's connect timeout is 5 s. On Windows a closed local port is slow to refuse.
4. **The history is lost on reload** (session state is per browser session) and is never sent to the API.
5. **Sources are not linked to the `[n]` markers** in the answer text.
6. **Plain text only**: answers have no bold or lists. The system prompt asks for plain prose, so
   little is lost today; D-2026-10-03-31 says when to revisit.
7. **The built-in browser pane is phone-sized** (406×318 at its natural size), so what I saw was the
   narrow layout with a collapsed sidebar; the wide layout was checked through the DOM, not by eye.
8. **One browser engine**, one real answer, one host. `K_MAX = 5` rests on a single measurement.
9. **No container yet**: the page is run with `streamlit run` on the host; a compose `ui` service is a
   later step and would set `API_URL` and revisit the listen address.

## How this connects
**Previous:** P7-M7 wrote the API's contract and found its gaps; this step is the first program that has
to live by that contract, and its tests are the consumer's half of it (the client is replayed against the
nine awkward fragments of clause S6, and against the real app). **Next (not yet specified):** whatever
P8-M2 asks for; likely candidates are a Stop button (needs a reader thread, D7), clickable citations, or a
containerised UI.

**Deep dive:** [docs/39](<../docs/39 streamlit_chat_ui_deep_dive.md>) teaches the five ideas behind this page
(the rerun model, reading a stream, interruption safety, the two-run submit, untrusted text) with predict-first
demos saved in `practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/`.

## How to do this yourself
1. Before building a client, **read the contract and the gaps** of the thing it calls, and let them
   decide the UX (here: a visible wait, a locked input, plain-text rendering).
2. **Check the framework's rules in its source** where behaviour is surprising (when does it interrupt
   a script?), and prove them with a ten-line probe before relying on them.
3. Put everything that talks to the outside world in a framework-free module and test it with a fake
   transport, then test the page with the framework's own harness, then **mutate both** to see the tests fail.

## Practice exercises
1. Run `stop_pattern_probe.py` and predict which of its four operations raise before you look. Then move
   `busy` back into `st.session_state` in `app.py` and watch the interruption test fail.
2. Change `as_plain_text` to escape only `$`. Which tests fail, and what does the `prices` scenario show
   in the browser?
3. Replace `iter_sse_data` with a version based on `response.iter_lines()`. Which tests fail and why?
4. Ask `[[scenario:slow-start]] x` against `ui_stub.py`, press Streamlit's Stop, then press `r`. Explain
   the state of the input at each moment.
5. Add a real "Stop generating" button. What must the generator yield so that Streamlit gets an interrupt
   point every half second while the model is silent? (Hint: a reader thread and a queue.)
6. Remove the lock (`disabled=chat.busy`) and submit a second question while the first runs, against the
   stub and against the real stack. What differs?
7. Make the sidebar's `/health` check use `st.cache_data(ttl=10)`. What do you gain when the API is down,
   and what do you lose?
8. Link each `[n]` in the answer to its entry in the Sources expander. What must the client keep?
9. Write a compose `ui` service. What must `API_URL` be, and what must change in `.streamlit/config.toml`?
10. Write a Hypothesis test: for any text, `as_plain_text` leaves no unescaped ASCII punctuation. Then
    find out whether Streamlit renders the escaped text back to the original characters.

## Sources
- [Streamlit: chat elements](https://docs.streamlit.io/develop/api-reference/chat) and
  [`st.write_stream`](https://docs.streamlit.io/develop/api-reference/write-magic/st.write_stream) and
  [`st.spinner`](https://docs.streamlit.io/develop/api-reference/status/st.spinner)
- [Streamlit: how a script runs](https://docs.streamlit.io/develop/concepts/architecture/run-your-app)
  and [session state](https://docs.streamlit.io/develop/concepts/architecture/session-state)
- [Streamlit: app testing (`AppTest`)](https://docs.streamlit.io/develop/api-reference/app-testing) and
  [`config.toml`](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
- [Streamlit tutorial: build a conversational app](https://docs.streamlit.io/develop/tutorials/chat-and-llm-apps/build-conversational-apps)
- [WHATWG HTML: server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html)
  (the rules the reader follows) and [httpx quickstart](https://www.python-httpx.org/quickstart/)
- [CommonMark: backslash escapes](https://spec.commonmark.org/0.31.2/) (any ASCII punctuation character
  may be escaped) and [OWASP LLM01: prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
  (why model output is untrusted)
- Read directly from the installed packages: `streamlit/runtime/scriptrunner/script_runner.py`
  (`_maybe_handle_execution_control_request`), `streamlit/elements/write.py` (`write_stream`),
  `streamlit/web/bootstrap.py` (`_fix_sys_path`).
- Earlier steps this builds on: [docs/api-contract.md](../docs/api-contract.md),
  [notes/step-7.7-api-contract-checkpoint.md](step-7.7-api-contract-checkpoint.md),
  [docs/35](<../docs/35 chat_streaming_deep_dive.md>).
