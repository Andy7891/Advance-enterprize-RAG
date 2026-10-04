# 39 — Streamlit Chat UI Deep Dive (P8-M1)

> Deconstructs P8-M1's two files: [`src/ui/app.py`](../src/ui/app.py) (the page) and
> [`src/ui/client.py`](../src/ui/client.py) (everything that talks to the API). It does NOT re-teach
> the API side of streaming ([docs/35](<35 chat_streaming_deep_dive.md>)) or the contract the client
> obeys ([docs/api-contract.md](api-contract.md)); it covers what is genuinely new when you are the
> **client**: how Streamlit really runs a script, how to read a stream without losing a character,
> why cleanup code behaves differently when a script is stopped, why one question takes three runs,
> and why text from a model must never be handed to a renderer raw. Demo scripts live in
> [`practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/>)
> and need no Docker and no real model: they use Streamlit's own test harness (`AppTest`) and fake
> HTTP responses. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d1_rerun_model.py"
> ```
>
> **About the "exact code I wrote" blocks:** line numbers refer to the real files, and the code lines are
> quoted verbatim, but the explanatory comments that follow most lines in the real files, and some docstrings,
> are left out where the block says so, to keep this page readable. Read the files for the full text.
>
> (If a script ever says `AppTest script run timed out after 3(s)`, it was only the first, cold
> import being slow: add `default_timeout=30` to `AppTest.from_file(...)`. d3 already does.)

---

## 0. Plain-language summary

```
client.py:   knows how to ask the API a question and how to read the answer that trickles back
app.py:      draws a chat page, remembers the conversation, and calls client.py when you press Enter
```

In one breath: you type a question; the page records it and locks the box; the page asks the API;
words trickle back over many minutes and are typed into a chat bubble as they arrive; the sources
appear under it; the box unlocks. Underneath that sentence are five ideas, in the order this step
needed them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | Streamlit's rerun model and `session_state` | 🔴 high | `get_chat`, `main` in `app.py` |
| C2 | Reading a stream correctly (incremental SSE) | 🔴 very high | `iter_sse_data`, `AnswerStream` in `client.py` |
| C3 | Interruption safety: a `finally` cannot use Streamlit | 🔴 very high | `Chat`, `stream_into_page`, `answer_pending_question` |
| C4 | The two-run submit and the lock (a tiny state machine) | 🟡 high | `main`, `answer_pending_question` |
| C5 | Untrusted model text in a markdown renderer | 🔴 high | `as_plain_text` |

**How to use this document.** Every concept starts with **Do this first**: run a script and
*predict* before you read on. The explanation comes after you have committed to a guess. Guessing
wrong is the point; it is where the learning happens.

---

## C1 — Streamlit's rerun model and `session_state`

### Do this first
Run [`d1_rerun_model.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d1_rerun_model.py>).
The page it drives has two counters: an ordinary variable (`local_runs`) and a `session_state`
counter (`runs`), and a button. **Predict first:** the script opens the page, reruns it with no
interaction, clicks the button, reruns again. For each of the four moments, which numbers do you
expect for `local_runs`, `session_runs`, `clicks` and `button_is_true`?

### 1. Definition + analogy
**What it is.** A normal web app has handlers: "when this button is clicked, run *this* function".
Streamlit has none. Instead, **every time anything happens** (page opened, a button clicked, a
slider moved, a question submitted) Streamlit **runs your whole Python file again from the first
line to the last**, as if you had just started the program. The only thing that survives from one
run to the next is `st.session_state`, a dictionary Streamlit keeps for each open browser tab. A
widget such as `st.button` does not "call you back": it simply *returns a value during this run*,
`True` if the click that caused this run was on it, `False` otherwise.

**Analogy.** A teacher with a peculiar rule: whenever a student raises a hand, she goes back to
page 1 of her lesson plan and reads the entire plan aloud again, top to bottom, adjusting only
where the student's question changes the answer. She remembers nothing on her own. But on her desk
is a notebook that is never thrown away between readings.

### For a 14-year-old
Imagine you are playing a video game, but the game has a weird rule: every single time you press
any button, the game console **reboots and replays the level from the very start**, super fast, so
fast you barely notice. How does it still feel like one continuous game? Because before each reboot
the console saves a few things on a memory card, and after rebooting it reads the memory card first.
If your health, your score and your position are on the memory card, then after the reboot you are
exactly where you were.

Now think about what happens to things that are **not** on the memory card. Suppose the game keeps
a number in its head called "number of times I have counted". Every reboot, that number starts again
at zero, adds one, and ends at one. It will *always* say one, no matter how long you play. That is
the trap: if you store the chat history in an ordinary variable, it is wiped at every reboot, and
your chat would disappear after every message.

Also think about the "jump" button. In this game the jump button is not a thing that *makes* you
jump. The console, on the reboot caused by your press, simply asks "was jump the button pressed to
cause this reboot?" and answers yes for that one reboot, and no for every other. The yes is gone on
the next reboot.

One more danger: the memory card is **per player**. If the game stored the score in the console's
own head instead of on your card, two friends playing on the same console would share one score.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the console rebooting and replaying the level | Streamlit re-executing `app.py` top to bottom on every interaction |
| the memory card | `st.session_state` |
| the number in the console's head that always ends at one | `local_runs` (a normal variable): always 1 |
| the saved "times I have rebooted" on the card | `st.session_state.runs`: 1, 2, 3, 4 |
| "was jump the reason for this reboot?" | `st.button(...)` returning `True` for exactly one run |
| two friends sharing the console's head score | a module-level variable shared by every browser tab (see exercise 2) |
| the card holding a whole save file | `st.session_state.chat`, one `Chat` object holding the entire conversation |

### 2. Why here specifically
A chat is *state over time*: the list of messages, whether we are waiting for an answer, which
question is pending. Streamlit gives us nothing but `session_state` to hold it, so `get_chat()`
(line 89) creates one `Chat` object the first time and hands the same object back on every later
run. Every other function receives that object as an argument. The page file is just "draw whatever
the `Chat` currently says".

### What breaks if this changes
- **History as a plain variable at the top of the file** (`messages = []`): every question wipes the
  conversation, because the file starts again from line 1.
- **History in a module-level variable of an imported module** (the "global" way): it survives
  reruns, but it is **shared by every browser tab and every user** of this Streamlit process (the
  claim is checked in exercise 2). You would see strangers' questions.
- **Remove the `if "chat" not in st.session_state` guard** (line 90): a fresh empty `Chat` replaces
  the old one on every run, so the history vanishes again.

### [Interview relevance: high: "how does your framework execute your code, and where does state live?" is the first question to ask about any UI framework. Streamlit's rerun-the-script model is unusual, and being able to explain it (and its cost: expensive work must be cached or kept out of the rerun path) is a common signal that a candidate has built a real LLM front end rather than copied a snippet]

**Interview question:** *"How does Streamlit's execution model differ from a conventional web
framework, and where do you keep state that must survive an interaction?"*
**A strong answer covers:** the whole script re-executes top to bottom on every interaction (widget
change, button, `st.rerun()`); widget calls return their current value during the run; ordinary
variables are recreated each run; persistent state goes in `st.session_state` (per browser session);
module-level globals are shared across sessions, so they are wrong for per-user data; expensive
resources belong behind `st.cache_resource` or in `session_state`; and because everything re-runs,
long blocking work in the script delays the redraw and has interruption consequences (C3).

### 3. Minimal code that explains the concept
The page: [`d1_counter_app.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d1_counter_app.py>)
```python
# The tiny Streamlit "page" that d1_rerun_model.py drives. It is NOT started with a server here:
# Streamlit's own test harness (AppTest) runs this file the same way the real server would.
import streamlit as st  # the Streamlit library; every st.something(...) call draws or reads the page

local_runs = 0  # an ordinary Python variable: it is created from scratch every time this file runs
local_runs += 1  # so after this line it is ALWAYS 1, no matter how many times the page has run
st.session_state.setdefault("runs", 0)  # session_state is Streamlit's memory for ONE browser tab; start it at 0
st.session_state.runs += 1  # this one survives between runs, so it counts how many runs there have been
pressed = st.button("Click me")  # draws a button; returns True ONLY in the run caused by clicking it
if pressed:  # only the run that the click triggered gets in here
    st.session_state.setdefault("clicks", 0)  # create the click counter the first time it is needed
    st.session_state.clicks += 1  # remember the click for all later runs
st.write(  # draws one line of text so the driver can read what this run saw
    f"local_runs={local_runs} session_runs={st.session_state.runs} "  # the two counters side by side
    f"clicks={st.session_state.get('clicks', 0)} button_is_true={pressed}"  # clicks so far, and the button's value
)
```
The driver: [`d1_rerun_model.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d1_rerun_model.py>)
```python
# Demo of C1: Streamlit re-runs the WHOLE file on every interaction, and only session_state remembers.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d1_rerun_model.py"
from pathlib import Path  # builds the path to the app file that sits next to this script

from streamlit.testing.v1 import AppTest  # Streamlit's own harness: runs a page's script without a browser

app_file = Path(__file__).with_name("d1_counter_app.py")  # the page to drive: the file next to this script
at = AppTest.from_file(str(app_file))  # load it (nothing has run yet)

at.run()  # run 1: what a browser tab does when it first opens the page
print("after opening      :", at.markdown[0].value)  # the one line of text the page drew in that run

at.run()  # run 2: a plain rerun with NO interaction (like pressing the R key in the browser)
print("after a plain rerun:", at.markdown[0].value)  # watch which counter moved

at.button[0].click().run()  # run 3: click the button; the click is delivered together with this run
print("after one click    :", at.markdown[0].value)  # button_is_true should be True in THIS run only

at.run()  # run 4: another plain rerun; the click was a one-time event, not a state
print("after another rerun:", at.markdown[0].value)  # button_is_true is False again; clicks is still 1
```
Real output:
```
after opening      : local_runs=1 session_runs=1 clicks=0 button_is_true=False
after a plain rerun: local_runs=1 session_runs=2 clicks=0 button_is_true=False
after one click    : local_runs=1 session_runs=3 clicks=1 button_is_true=True
after another rerun: local_runs=1 session_runs=4 clicks=1 button_is_true=False
```
`local_runs` never leaves 1: the file really did start from scratch four times. `session_runs`
counts them. `button_is_true` is `True` in exactly one line: a click is an *event delivered with one
run*, not a lasting state, which is why the page copies what matters into `session_state`.

### The exact code I wrote (`src/ui/app.py`)
> **Lines 89-93:**
> ```python
> def get_chat() -> Chat:
>     if "chat" not in st.session_state:
>         st.session_state.chat = Chat()
>         # created once per browser session; later runs get the same object back
>     return st.session_state.chat
> ```
> **Lines 192-206 (the start of `main`, which runs on every interaction):**
> ```python
> def main() -> None:
>     st.set_page_config(page_title=PAGE_TITLE, page_icon="\N{SPEECH BALLOON}")
>     st.title("Advance RAG chat")
>     try:
>         api_url = endpoint.configured_api_url()
>     except endpoint.ConfigError as problem:
>         st.error(as_plain_text(problem.message))
>         st.stop()
>     st.caption(INDEPENDENT_NOTE)
>     chat = get_chat()
>     render_sidebar(chat, api_url)
>     for message in chat.messages:
>         render_message(message)
>     question = st.chat_input(PLACEHOLDER, max_chars=MAX_QUESTION_CHARS, disabled=chat.busy)
> ```
> *(The `try`/`except` on lines 195-200 was added in P8-M3: a mistyped API address is reported before anything else is drawn. See [notes/step-8.3](../notes/step-8.3-ui-configuration.md).)*

**🎯 Interview-relevant lines:** line 90 is the whole trick: *create once, return the same object
forever*. Everything the page "remembers" is reachable from the one object that line returns, which
is also what makes C3 possible.

### Canonical docs
- [Streamlit: how a script runs](https://docs.streamlit.io/develop/concepts/architecture/run-your-app)
- [Streamlit: session state](https://docs.streamlit.io/develop/concepts/architecture/session-state)

### Micro-exercises (for an absolute beginner; hints included)
1. **Add a second button.** Copy `d1_counter_app.py`, add `if st.button("Reset"):` that sets
   `st.session_state.clicks = 0`. In the driver click Reset after two normal clicks. *Predict* the
   `clicks` value before running. *Hint:* `at.button[1]` is the second button; `at.button[0]` is
   still the first.
2. **Prove that a module-level variable is shared between sessions.** Create `shared_state.py`
   containing `counter = {"n": 0}`. Make a page that imports it, adds 1, and prints it. Create *two*
   `AppTest` objects from that page (these stand for two browser tabs) and run tab A twice, tab B
   once. *Predict* what tab B prints. *Hint:* Python imports a module once per process. *(Verified
   result: tab A shows 2, tab B shows 3.)*
3. **Remove the guard.** In a copy of the chat page, change `get_chat` to always do
   `st.session_state.chat = Chat()`. Ask two questions in a row with the test harness. What does the
   history contain afterwards? *Hint:* count `chat.messages`.
4. **Count runs with a leash.** Write a page whose only code is a counter in `session_state` and
   `if st.session_state.n < 3: st.rerun()`. How many runs happen after one `at.run()`? *Hint:*
   `st.rerun()` stops the current run and immediately starts another; AppTest keeps following them.
5. **Explain it back.** Without running anything, say what the page would print on the third run if
   `local_runs` were stored as `st.session_state.local_runs` instead, and why that changes the
   meaning of the variable's name.

**How it works internally.** Each open browser tab is a *session*. For each session Streamlit keeps
a "script runner" thread and a `session_state` dictionary. When you interact, the browser sends the
new widget values to the server; the server asks the script runner to run your file again from the
top *in a fresh module namespace* (so ordinary variables start empty) with those widget values in
force. Elements you create (`st.write`, `st.button`) are sent to the browser as they are created.
Nothing but `session_state` is carried over.

### How to validate this yourself
- Add `st.write(st.session_state)` temporarily to `main()` and watch the dictionary change as you
  interact. Predicting the next value *before* each click is the validation.
- In the real app, add `print("RUN", id(st.session_state))` to `main()` and watch the terminal that
  ran `streamlit run`: one line per interaction, proving the file really re-executes.
- `tests/ui/test_app.py::test_a_rerun_after_an_answer_does_not_ask_again` is the project's own proof
  that a later rerun does not repeat earlier work.

### How to visualize this
Draw a horizontal line of numbered boxes, one per run (1, 2, 3, 4). Under each box write the local
variables (they reset every time: always `1`). Above all boxes draw one long bar labelled
`session_state` that never resets; write its changing contents above each box.

**Main benefits.** No callbacks to wire; UI code reads like a script; the page is always a pure
function of its state, so bugs are easy to reproduce by setting the state.

**You should now be able to explain:** why the chat history lives in one object in
`st.session_state`, why an ordinary variable or a module global is wrong, and what `st.button`
actually returns.

---

## C2 — Reading a stream correctly (incremental SSE)

### Do this first
Run [`d2_sse_reader.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d2_sse_reader.py>).
**Predict first:** the body has three events: `Hello `, a text that contains the invisible
character U+2028 (`sep:` + U+2028 + `after`), and `[DONE]`. The first part uses the tempting shortcut
`response.iter_lines()`. How many *words* of the second event do you expect it to keep? Then:
when the very same bytes arrive **one byte at a time**, do you expect the project's reader to give
the same events?

### 1. Definition + analogy
**What it is.** The API answers with a **Server-Sent Events** stream: one long HTTP response whose
body is text that arrives in pieces over minutes. Each *event* is one or more lines `data: …`
ended by a blank line. Reading it correctly means turning *arbitrary pieces of bytes* into *whole
events* without losing or inventing a character. Three things make that harder than it looks:
network chunks are cut at random places (even in the middle of a character that takes several bytes);
"end of line" can be written three ways (`\n`, `\r`, `\r\n`) and **only** those three; and the stream
can simply stop without an ending marker.

**Analogy.** Taking dictation over a bad phone line. The speaker reads short messages. A pause
(blank line) means "that message is finished". The line crackles: sometimes it cuts out in the
middle of a word, so you must keep what you have and wait for the rest. You write the word only when
you have all of it.

### For a 14-year-old
Imagine your friend is reading a long story to you over a walkie-talkie, but the walkie-talkie
delivers the story in random little pieces: sometimes a whole sentence, sometimes half a word.
Your job is to write the story into a notebook correctly, and to know when each *paragraph* is
finished. The friend's rule is: "when I stay silent for a moment, a paragraph just ended."

First problem: a piece might end in the middle of a letter that is built from several parts (like
an emoji, or the euro sign €, which is three parts glued together). If you write down the first part
alone, you get garbage. So you keep the half-letter in your hand and wait for the other parts.

Second problem: your friend says "new line" in three different ways, and you must know exactly which
three. If someone gives you a fourth, fancier way to say "new line" (a special invisible symbol), the
friend's rulebook says: that is **not** a new line, it is just a funny character inside a sentence,
write it down and keep going. A lazy listener who treats every fancy symbol as "new line" will cut
sentences in half and throw away the second half without telling anyone. That is exactly what
`iter_lines()` does.

Third problem: the friend says "new line" by saying the first half of the word "carriage-return" at
the very end of one piece, and the second half ("line-feed") at the start of the next piece. If you
decide "carriage-return alone means new line" too early, you will count a *second* new line when the
second half arrives. So when a piece ends with that first half, you wait to see what comes next. But
if the walkie-talkie goes dead right then, there is no next piece, so you must treat it as a plain
"new line" and stop waiting.

Fourth problem: what if the story just stops mid-paragraph? A half-finished paragraph is not trusted;
you throw it away and tell the teacher "the story was cut off".

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the walkie-talkie delivering random pieces | the HTTP body arriving in network chunks (`chunks: Iterable[bytes]`) |
| the notebook where you write | `buffer`, text received but not yet cut into lines |
| keeping half a letter until the rest arrives | `codecs.getincrementaldecoder("utf-8")` |
| the three allowed ways to say "new line" | `_LINE_END = re.compile(r"\r\n\|\r\|\n")` |
| the fancy invisible symbol that is NOT a new line | U+2028, `\x0b`, `\x85`: treated as ordinary text |
| waiting after hearing the first half of "carriage-return" | `held = "\r"` |
| "the walkie-talkie went dead, so stop waiting" | the final pass with `chunk is None` |
| a paragraph finished by silence | a blank line completing an event (`yield "\n".join(data_lines)`) |
| a paragraph that never finished | an event dropped; `AnswerStream` then sets `TRUNCATED_MESSAGE` |

### 2. Why here specifically
The page needs the answer **live**, minute by minute, and needs to know *how it ended*: `[DONE]`
(success), `[ERROR] …` (the model failed after the 200), or just silence (a dropped connection). It
also needs the source list kept apart from the answer text. The contract
([docs/api-contract.md](api-contract.md), clauses S2 to S6) fixes the rules; this module is the
client half of them.

### What breaks if this changes
- **`response.iter_lines()` instead** (the shortcut): demo section 1 shows it silently drops the text
  after U+2028, with no error. The same family of bug the server itself had in P7-M7.
- **No incremental decoder** (decode each chunk alone): a character cut by the network raises an
  error or turns into garbage. (Section 4 of the demo.)
- **No `held` rule**: a CRLF cut between two chunks is counted as two line ends, producing a spurious
  blank line, i.e. a spurious event end. (The trace script shows chunk #1.)
- **No final pass (`chunk is None`)**: a stream ending on a lone CR loses its last event. This was a
  real bug in my first version, found by a test written straight from the specification.
- **No "stream ended with no terminal event" check**: a dropped connection would look like a finished
  answer.

### [Interview relevance: very high: every LLM product streams tokens, and "how do you consume a token stream robustly?" separates people who called an SDK from people who understand the wire. Chunk boundaries, incremental decoding, delimiter handling, terminal signals, timeouts and cancellation are all fair game]

**Interview question:** *"You're consuming a streaming LLM response over HTTP. What can go wrong in the
client, and how do you defend against it?"*
**A strong answer covers:** chunk boundaries are arbitrary, so parse incrementally and keep a buffer
(including a multi-byte UTF-8 character split across chunks); use the protocol's real delimiters, not
a convenience splitter; know the terminal signals and treat "ended without one" as failure; set read
timeouts that account for a slow first token, and know that a silent server needs a heartbeat to be
distinguishable from a dead one; release the connection on cancellation; and treat the text as
untrusted when rendering (C5).

### 3. Minimal code that explains the concept
[`d2_sse_reader.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d2_sse_reader.py>)
```python
# Demo of C2: reading a Server-Sent Events stream correctly. Compares the tempting shortcut
# (response.iter_lines()) with the project's own reader (src/ui/client.py: iter_sse_data).
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d2_sse_reader.py"
import httpx  # the HTTP library the page uses; here only to build a fake response from bytes

from src.ui.client import iter_sse_data  # the real reader being studied

# What the API puts on the wire for three answer fragments and the end marker. Each event is
# "data: <text>" followed by a blank line. The second fragment contains U+2028 (LINE SEPARATOR),
# which Python's splitlines() treats as a line break but the SSE rules do NOT.
body = "data: Hello \n\ndata: sep: after\n\ndata: [DONE]\n\n".encode("utf-8")  # text -> bytes, like a socket delivers

print("1) the shortcut: response.iter_lines()")  # section title
response = httpx.Response(200, content=body)  # a fake response whose body is those bytes
lines = list(response.iter_lines())  # httpx cuts the body into lines the splitlines() way
print("   lines     :", [ascii(line) for line in lines])  # ascii() shows invisible characters as escapes
naive = [line[5:].removeprefix(" ") for line in lines if line.startswith("data:")]  # keep only data lines
print("   naive text:", [ascii(text) for text in naive])  # the text after U+2028 has vanished

print("\n2) the project's reader: iter_sse_data")  # section title
print("   events    :", [ascii(event) for event in iter_sse_data([body])])  # whole body in one chunk

print("\n3) same bytes, delivered ONE BYTE AT A TIME (network chunks can cut anywhere)")  # section title
one_byte_chunks = [body[i : i + 1] for i in range(len(body))]  # a list of 1-byte pieces
print("   events    :", [ascii(event) for event in iter_sse_data(one_byte_chunks)])  # must be identical to 2)

print("\n4) a character that needs 3 bytes, cut in the middle by the network")  # section title
euro = "data: price 10€\n\n".encode("utf-8")  # the euro sign is 3 bytes in UTF-8
cut = euro.index("€".encode("utf-8")) + 1  # cut after the FIRST byte of the euro sign
print("   events    :", [ascii(event) for event in iter_sse_data([euro[:cut], euro[cut:]])])  # reassembled correctly

print("\n5) a lone CR as the very last byte of the stream")  # section title
print("   events    :", [ascii(event) for event in iter_sse_data([b"data: a\r\rdata: b\r\r"])])  # both events arrive

print("\n6) a stream that stops in the middle of an event")  # section title
print("   events    :", [ascii(event) for event in iter_sse_data([b"data: complete\n\ndata: unfinis"])])  # only the finished one
```
Real output:
```
1) the shortcut: response.iter_lines()
   lines     : ["'data: Hello '", "''", "'data: sep:'", "'after'", "''", "'data: [DONE]'", "''"]
   naive text: ["'Hello '", "'sep:'", "'[DONE]'"]

2) the project's reader: iter_sse_data
   events    : ["'Hello '", "'sep:\\u2028after'", "'[DONE]'"]

3) same bytes, delivered ONE BYTE AT A TIME (network chunks can cut anywhere)
   events    : ["'Hello '", "'sep:\\u2028after'", "'[DONE]'"]

4) a character that needs 3 bytes, cut in the middle by the network
   events    : ["'price 10\\u20ac'"]

5) a lone CR as the very last byte of the stream
   events    : ["'a'", "'b'"]

6) a stream that stops in the middle of an event
   events    : ["'complete'"]
```
Read section 1 carefully: `iter_lines()` turned one line `data: sep: after` into **two** lines
(`data: sep:` and `after`). The second has no `data:` prefix, so the shortcut ignores it, and the
user would see `sep:` with `after` missing, with no error anywhere.

To **see** the reader's insides, run the trace
[`d2b_trace_reader.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d2b_trace_reader.py>),
which repeats the reader's steps and prints its state after every chunk. Real output:
```
#0   bytes=b'data: price 10\xe2'              decoded='data: price 10'   held=''    
      lines=[]  buffer_left='data: price 10'  events=[]
#1   bytes=b'\x82\xac\r'                      decoded='€\r'         held='\r'  
      lines=[]  buffer_left='data: price 10€\r'  events=[]
#2   bytes=b'\n\r\n'                          decoded='\n\r\n'           held=''    
      lines=["'data: price 10\\u20ac'", "''"]  buffer_left=''  events=["'price 10\\u20ac'"]
#3   bytes=b'data: a\r\rdata: b\r'            decoded='data: a\r\rdata: b\r' held='\r'  
      lines=["'data: a'", "''"]  buffer_left='data: b\r'  events=["'a'"]
END  bytes=None                               decoded=''                 held=''    
      lines=["'data: b'"]  buffer_left=''  events=[]
```
Walk through it: chunk #0 ends with `\xe2`, the first byte of `€`; the decoder emits nothing for it
(`decoded` stops before the euro sign). Chunk #1 completes the euro sign **and** ends with a CR, which
is *held*. Chunk #2 starts with the LF that completes that CRLF, so one line ends; then `\r\n` is a
blank line, and the event `price 10€` is released. At the end, `data: b` is a complete line but no
blank line follows it, so event `b` is correctly **not** delivered.

### The exact code I wrote (`src/ui/client.py`)
> **Lines 37-41 (the two patterns):**
> ```python
> _LINE_END = re.compile(r"\r\n|\r|\n")
> # the three line terminators the SSE grammar defines, CRLF first so it counts as ONE: not
> # str.splitlines(), which also splits on U+2028, \x0b, \x85 and more (httpx's iter_lines() does too)
> _SOURCE_LINE = re.compile(r"\[(\d+)\] (.+) \((.+)\)")
> # `[n] title (path)`, as the API writes it; the LAST parenthesised group is the path
> ```
> **Lines 85-121 (the reader):**
> ```python
> def iter_sse_data(chunks: Iterable[bytes]) -> Iterator[str]:
>     decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
>     buffer = ""
>     data_lines: list[str] = []
>     for chunk in itertools.chain(chunks, [None]):
>         # None marks the end of the stream: one last pass with nothing more to wait for
>         at_end = chunk is None
>         buffer += decoder.decode(b"" if chunk is None else chunk, final=at_end)
>         held = "\r" if buffer.endswith("\r") and not at_end else ""
>         lines = _LINE_END.split(buffer[: len(buffer) - len(held)])
>         buffer = lines.pop() + held
>         for line in lines:
>             if line == "":
>                 if data_lines:
>                     yield "\n".join(data_lines)
>                 data_lines = []
>             elif not line.startswith(":"):
>                 name, _, value = line.partition(":")
>                 if name == "data":
>                     data_lines.append(value.removeprefix(" "))
> ```
> (the real file has a comment under most of these lines; they are omitted here for space)
>
> **Lines 167-198 (turning events into an answer):**
> ```python
>     def __iter__(self) -> Iterator[str]:
>         try:
>             for data in iter_sse_data(self._response.iter_bytes()):
>                 if data == DONE:
>                     self.complete = True
>                     return
>                 if data.startswith(ERROR_PREFIX):
>                     self.error = data.removeprefix(ERROR_PREFIX).strip()
>                     return
>                 if data.startswith(SOURCES_PREFIX):
>                     self.sources = parse_sources(data)
>                     continue
>                 self.text += data
>                 yield data
>             self.error = TRUNCATED_MESSAGE
>         except httpx.ReadTimeout as silence:
>             raise ChatError("timeout", f"The API sent nothing for {READ_TIMEOUT_SECONDS:.0f} s, so the page gave up.") from silence
>         except httpx.TransportError as lost:
>             raise ChatError("connection_lost", "The connection to the API dropped mid-answer.") from lost
>         finally:
>             self.close()
> ```
> (comments omitted, and in the real file the two `raise ChatError(...)` calls are wrapped over several lines)

**🎯 Interview-relevant lines:** line 99, `itertools.chain(chunks, [None])`, is a small, elegant trick:
it appends an "end of stream" marker to the chunks so the loop body has one last pass in which
nothing more can arrive. Line 185, `self.error = TRUNCATED_MESSAGE`, runs only when the `for` ends
*without* a `return`, i.e. when the stream stopped with no terminal event.

### Canonical docs
- [WHATWG HTML: server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html)
  (the rules the reader follows; read the "interpreting an event stream" section)
- [MDN: Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)
- [httpx: quickstart](https://www.python-httpx.org/quickstart/)

### Micro-exercises
1. **Cut it everywhere.** Take `body` from d2. Write a loop that cuts it into two chunks at *every*
   possible position (`body[:i]`, `body[i:]`) and checks that `iter_sse_data` always returns the same
   three events. *Hint:* `for i in range(len(body) + 1)`. How many cut positions are there, and how
   many did you test?
2. **Break it on purpose.** Copy `iter_sse_data` into a scratch file and delete the `held` rule
   (make `held` always `""`). Predict which of d2's six sections change, then run them. *Hint:* only
   a CR at the end of a chunk matters; construct `[b"data: a\r", b"\ndata: b\r\n\r\n"]` and compare
   the events with and without the rule (expected with: `["a\nb"]`).
3. **Meet the fancy separators.** In d2, replace U+2028 by `\x0b`, then `\x85`. Does `iter_lines()`
   lose text each time? Then try a plain `\n` inside the fragment sent as two `data:` lines
   (`data: a\ndata: b\n\n`): what single event should result, and why is it `a\nb`? *Hint:* data lines
   are joined with LF.
4. **Add the `event:` field.** The reader ignores `event:` lines. Change it to yield
   `(event_type, data)` pairs, defaulting to `"message"`. *Hint:* keep an `event_type` variable that
   resets to `""` when an event ends, exactly like `data_lines`. Run the six d2 sections again.
5. **Watch real bytes.** Start the stub API (see the end-to-end exercise) and run
   `curl -s -N -X POST http://127.0.0.1:8004/chat -H "Content-Type: application/json" -d '{"query":"hi","k":2}' | cat -A`.
   `cat -A` prints `$` at each line end, so you can *see* the blank lines. Count the events, then
   feed the same text into `iter_sse_data` and confirm the count.

**How it works internally.** HTTP/1.1 sends this response with `Transfer-Encoding: chunked`: the
server flushes a piece whenever it has one, and the client library hands you the pieces as they
arrive. The pieces have no relationship to event boundaries. That is why the reader keeps `buffer`
(text so far) and only cuts *complete* lines out of it, leaving the unfinished tail for the next
chunk. UTF-8 characters are 1 to 4 bytes, so the decoder must remember half-characters between calls.

### How to validate this yourself
- Compare against an independent implementation: the dev dependency `httpx-sse` reads the same
  bytes (`EventSource(httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body))`).
  Two parsers written separately agreeing is strong evidence.
- Run `tests/ui/test_client.py` (76 tests), then the mutation habit from this step: delete one
  safeguard at a time and check that exactly the matching test fails.
- Run it against the **real** server's framing: `test_the_client_agrees_with_the_real_servers_own_framing`
  does so in-process.

### How to visualize this
Run `d2b_trace_reader.py` and draw, for each chunk, three boxes: *bytes in*, *buffer after*, *events
out*. Colour the `held` CR red. You will see that events leave the system only when a blank line
completes them, not when bytes arrive.

**Main benefits.** The page never shows a corrupted character, never drops text, never mistakes a
half-delivered answer for a finished one, and is independent of how the network happens to slice
the data.

**You should now be able to explain:** why `iter_lines()` is wrong for SSE, what the incremental
decoder and the `held` CR each protect against, and why the last pass (`chunk is None`) exists.

---

## C3 — Interruption safety: a `finally` cannot use Streamlit

### Do this first
Run [`d3_interruption.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d3_interruption.py>).
It drives two tiny pages. Both stream a few words, then the user presses Streamlit's Stop button
(`st.stop()` inside the stream), and both try to **unlock the page** in a `finally` block. The first
does it with `st.session_state.busy = False`. The second changes a plain Python object.
**Predict first:** after the Stop, which page is unlocked? Does the first page's `finally` block
raise, finish quietly, or never run?

### 1. Definition + analogy
**What it is.** A `finally` block is Python's promise: "this code runs no matter how the `try`
ends". Streamlit stops a running script by raising a special exception (a *control exception*) at
certain **checkpoints**: every `st.*` call and every access to `st.session_state`. While the stop is
pending, **every checkpoint raises again**, *even inside a `finally`*. So cleanup code that uses
Streamlit is cut short at its first checkpoint, and everything after it never executes. Cleanup that
uses only plain Python (changing attributes of an object you already hold) cannot be interrupted.

**Analogy.** A fire alarm during an exam. The moment it rings, the doors to the school office, the
lockers and the supply cupboard are all locked. You may leave, but you can only take what is already
in your backpack. If your plan was "on the way out I'll grab my form from the office", you are
stuck at the locked door.

### For a 14-year-old
Picture a theatre. The play is the script and the stage manager is Streamlit. Every time the actors
want a prop, a light, or the curtain, they ask the stage manager for it. That is every `st.` call,
and also every time they look at the notice board backstage (`session_state`).

Now someone hits the big red STOP button. The stage manager shouts "FREEZE!" and, from that moment,
**refuses every request**: no props, no lights, not even a look at the notice board. Every request
gets the answer "FREEZE!". The actors are told to tidy up their own costumes before leaving the
stage (that is the `finally`). If their tidying plan is "ask the stage manager for a bin and write
'play finished' on the notice board", it fails at the very first step, because every request is
refused, and they leave without finishing anything. The sign on the stage door still says "PLAY IN
PROGRESS", forever.

A clever actor, before the play, puts a small notebook **in their own pocket**. Tidying up means
writing in their own notebook and putting their own costume in their own bag. No stage manager is
needed. That works even during FREEZE.

In our app, the tidying matters a lot. If the page is not unlocked and the half-finished answer is
not saved, the "busy" sign stays up and the next time the page re-runs it thinks "someone is waiting
for an answer" with no question attached, and it asks the API a question that is literally nothing.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the stage manager | Streamlit's script runner |
| asking for a prop or the notice board | any `st.*` call, any `st.session_state` access |
| the red STOP button | Streamlit's own Stop button, a rerun request, `st.stop()` |
| "FREEZE!" as the answer to every request | `StopException` raised again at every checkpoint |
| tidying costumes before leaving | the `finally` block |
| the sign "PLAY IN PROGRESS" never taken down | `busy` left `True`, so the input stays disabled |
| the notebook in the actor's pocket | the plain `Chat` object, held in a local variable |
| writing in your own notebook during FREEZE | `chat.busy = False`, `chat.messages.append(reply)` |

### 2. Why here specifically
When an answer takes minutes, the user **will** stop it, refresh, or close the tab. The `finally`
blocks in `stream_into_page` and `answer_pending_question` must (a) release the HTTP connection,
(b) keep the part of the answer that arrived, and (c) unlock the page. (b) and (c) have to be
performed on a plain object, which is why `Chat` is a plain `dataclass` and why `answer_pending_question`
reads `st.session_state.k` *before* the `try`, never inside the `finally`.

### What breaks if this changes
- **`busy` kept as `st.session_state.busy`**: demo A. The `finally` raises at its first line and
  `busy` stays `True`: the input stays locked, and the next rerun sees "busy, with nothing pending".
  My first version had exactly this bug and the interruption test caught it.
- **Grabbing the `Chat` object *inside* the `finally`** (`chat = st.session_state.chat`): that is a
  session_state access, so it raises. The reference must be taken *before* anything can interrupt.
- **Catching only `Exception`**: Streamlit's control exceptions derive from `BaseException`
  (verified: `issubclass(StopException, Exception)` is `False`), so `except Exception` never sees
  them; only `finally` (or `except BaseException`) does.

### [Interview relevance: very high: cooperative cancellation is a general engineering topic (asyncio's `CancelledError`, Go's `context`, Java's `InterruptedException`). Explaining that cancellation arrives at checkpoints, that cleanup must not depend on cancellable operations, and that you hold references and use shielded or plain operations in cleanup, is a senior-level answer, and very relevant to streaming LLM apps where users abandon requests constantly]

**Interview question:** *"A user cancels a long-running request halfway. What must your code
guarantee, and what are the classic ways cleanup goes wrong?"*
**A strong answer covers:** resources (connections, locks, GPU slots) must be released and partial
results saved on every exit path (`finally`/context managers); cancellation is usually delivered as
an exception at suspension points, and a framework may keep delivering it while you clean up, so the
cleanup path must not call cancellable APIs (or must shield itself, like `asyncio.shield`); keep
references to what cleanup needs; distinguish `BaseException` control signals from ordinary errors;
make cleanup idempotent; and test it by injecting a cancellation at each point.

### 3. Minimal code that explains the concept
The wrong page: [`d3_bad_app.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d3_bad_app.py>)
```python
# The WRONG design for C3: the cleanup lives in st.session_state. Driven by d3_interruption.py.
import builtins  # lets this script leave a note that the driver can read after the run

import streamlit as st  # the Streamlit library

NOTES = builtins.__dict__.setdefault("D3_NOTES", [])  # a shared list for messages to the driver
st.session_state.setdefault("busy", True)  # pretend a question is in flight: the page is "locked"


def fragments():  # stands in for the answer stream
    yield "first words"  # one fragment arrives normally
    st.stop()  # the user presses Streamlit's Stop button while the answer is still being written


try:  # the same shape as the real page: stream inside try, clean up in finally
    st.write_stream(fragments())  # draws the fragments; the stop interrupts it half way
finally:  # runs even though the script is being stopped
    try:  # wrapped only so we can SEE what happens instead of crashing the demo
        st.session_state.busy = False  # the cleanup: unlock the page via session_state
        NOTES.append("cleanup through st.session_state: worked")  # reached only if the line above worked
    except BaseException as problem:  # Streamlit's stop signal is a BaseException, not an Exception
        NOTES.append(f"cleanup through st.session_state: RAISED {type(problem).__name__}")  # record what happened
```
The right page: [`d3_good_app.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d3_good_app.py>)
```python
# The RIGHT design for C3: the cleanup changes a plain Python object. Driven by d3_interruption.py.
import builtins  # lets this script leave a note that the driver can read after the run
from dataclasses import dataclass, field  # a tiny way to declare a plain data object

import streamlit as st  # the Streamlit library

NOTES = builtins.__dict__.setdefault("D3_NOTES", [])  # a shared list for messages to the driver


@dataclass  # generates __init__ for us; the class is ordinary Python, nothing Streamlit-specific
class Chat:
    busy: bool = True  # pretend a question is in flight: the page is "locked"
    saved: list = field(default_factory=list)  # where the partial answer is kept


if "chat" not in st.session_state:  # first run of this browser tab only
    st.session_state.chat = Chat()  # store ONE Chat object; later runs get the same object back
chat = st.session_state.chat  # grab a direct reference to the plain object, before anything can interrupt


def fragments():  # stands in for the answer stream
    yield "first words"  # one fragment arrives normally
    st.stop()  # the user presses Streamlit's Stop button while the answer is still being written


try:  # the same shape as the real page
    st.write_stream(fragments())  # draws the fragments; the stop interrupts it half way
finally:  # runs even though the script is being stopped
    chat.busy = False  # plain attribute assignment: no st call, so nothing can interrupt it
    chat.saved.append("first words")  # keep the partial answer, again as plain data
    NOTES.append("cleanup on a plain object: worked")  # tell the driver it worked
```
The driver: [`d3_interruption.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d3_interruption.py>)
```python
# Demo of C3: while Streamlit is stopping a script, a `finally` block cannot use st.* or st.session_state.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d3_interruption.py"
import builtins  # to read the notes the two apps leave behind
from pathlib import Path  # to find the two app files next to this script

from streamlit.testing.v1 import AppTest  # Streamlit's harness: runs a page without a browser

here = Path(__file__).parent  # the folder holding this script and the two apps

print("A) WRONG: cleanup through st.session_state")  # section title
bad = AppTest.from_file(str(here / "d3_bad_app.py"), default_timeout=30)  # load the bad page
bad.run()  # run it: it streams, gets stopped, and tries to clean up
print("   notes          :", builtins.__dict__["D3_NOTES"])  # what the finally block experienced
print("   busy afterwards:", bad.session_state.busy, " <- still True: the page would stay locked")  # the damage

builtins.__dict__["D3_NOTES"].clear()  # empty the shared list before the second experiment

print("\nB) RIGHT: cleanup on a plain object")  # section title
good = AppTest.from_file(str(here / "d3_good_app.py"), default_timeout=30)  # load the good page
good.run()  # run it: same stream, same stop
print("   notes          :", builtins.__dict__["D3_NOTES"])  # what the finally block experienced
chat = good.session_state.chat  # the plain object the page kept in session state
print("   busy afterwards:", chat.busy, " <- unlocked")  # the cleanup took effect
print("   partial answer :", chat.saved, " <- kept")  # and the partial answer was saved
```
Real output:
```
A) WRONG: cleanup through st.session_state
   notes          : ['cleanup through st.session_state: RAISED StopException']
   busy afterwards: True  <- still True: the page would stay locked

B) RIGHT: cleanup on a plain object
   notes          : ['cleanup on a plain object: worked']
   busy afterwards: False  <- unlocked
   partial answer : ['first words']  <- kept
```
The same stream, the same stop; the only difference is *what the cleanup touched*. The earlier probe
[`stop_pattern_probe.py`](<../practice codes/phase 8/m1_streamlit_chat_ui/stop_pattern_probe.py>) tried
four operations in a `finally`: reading `st.session_state`, writing it, calling `st.write` (all three
raised `StopException`) and mutating a plain dict (worked).

### The exact code I wrote (`src/ui/app.py`)
> **Lines 60-73 (the plain object):**
> ```python
> @dataclass
> class Chat:
>     messages: list[dict[str, Any]] = field(default_factory=list)
>     pending: str | None = None
>     busy: bool = False
> ```
> **Lines 148-165 (inside `stream_into_page`):**
> ```python
>     try:
>         fragments = iter(stream)
>         with st.spinner(WAITING_TEXT, show_time=True):
>             first = next(fragments, None)
>         if first is not None:
>             st.write_stream(
>                 as_plain_text(fragment) for fragment in itertools.chain([first], fragments)
>             )
>     except client.ChatError as problem:
>         reply["error"] = problem.message
>     finally:
>         stream.close()
>         reply["content"] = stream.text
>         reply["sources"] = stream.sources
> ```
> **Lines 171-189 (`answer_pending_question`):**
> ```python
> def answer_pending_question(chat: Chat, api_url: str) -> None:
>     question = chat.pending
>     chat.pending = None
>     if question is None:
>         chat.busy = False
>         return
>     k = st.session_state.k
>     # read now, before any streaming: a session_state access, which is off limits in `finally`
>     reply: dict[str, Any] = {"role": "assistant", "content": "", "sources": [], "error": None}
>     try:
>         with st.chat_message("assistant"):
>             stream_into_page(api_url, question, k, reply)
>     finally:
>         chat.messages.append(reply)
>         chat.busy = False
> ```

**🎯 Interview-relevant lines:** line 179, `k = st.session_state.k`, sits deliberately *before* the
`try`; if it moved into the `finally` the cleanup would raise. And `stream.close()` on line 161 is
called explicitly, not left to the garbage collector (the lesson of gap G1 in
[docs/api-contract.md](api-contract.md)).

### Canonical docs
- [Streamlit: how a script runs](https://docs.streamlit.io/develop/concepts/architecture/run-your-app)
- Python docs: [`try` statement and `finally`](https://docs.python.org/3/reference/compound_stmts.html#the-try-statement)
  and [the exception hierarchy](https://docs.python.org/3/library/exceptions.html#exception-hierarchy)
  (`BaseException` vs `Exception`)
- Read the source: `streamlit/runtime/scriptrunner/script_runner.py`, function
  `_maybe_handle_execution_control_request`

### Micro-exercises
1. **Add a fifth operation.** Open `practice codes/phase 8/m1_streamlit_chat_ui/stop_pattern_app.py`
   and add `("read st.session_state.get", lambda: st.session_state.get("busy"))` to the list of
   operations in the `finally`. *Predict* worked/RAISED, then run `stop_pattern_probe.py`.
   *Hint:* decide whether `.get` goes through the same checkpoint as `[]` and attribute access.
2. **Prove it is only about Streamlit's signal.** In `d3_bad_app.py`, change `st.stop()` to
   `raise ValueError("model crashed")`. *Predict* whether the cleanup through `session_state` still
   fails. *Hint:* the checkpoints only raise when a **stop or rerun request is pending**; a plain
   `ValueError` creates none. (Verified: the cleanup works and `busy` becomes `False`; the page just
   shows the ValueError. Keep this in mind: the bug only appears on Stop/rerun, which is why it hides
   in normal testing.)
3. **Be exact about the exception.** In a Python shell run
   `from streamlit.runtime.scriptrunner_utils.exceptions import StopException` and print
   `issubclass(StopException, Exception)` and `issubclass(StopException, BaseException)`. Then
   change `except BaseException` in `d3_bad_app.py` to `except Exception`. What happens to the notes
   and to the run, and why?
4. **Grab the reference too late.** In `d3_good_app.py`, move the line `chat = st.session_state.chat`
   from above the `try` to inside the `finally`. *Predict* what `D3_NOTES` and `chat.busy` show.
5. **Break the real page.** Copy `src/ui/app.py` to a scratch file, replace `chat.busy = False` in
   `answer_pending_question` with `st.session_state.chat.busy = False`, and run
   `tests/ui/test_app.py::test_an_interruption_mid_answer_closes_the_connection_and_unlocks_the_page`
   against it. *Hint:* the test file reads its page path from the `APP_FILE` constant; point it at
   your scratch file. Which assertion fails first?

**How it works internally.** Streamlit's script runner passes `_maybe_handle_execution_control_request`
to the session state as its `yield_callback` (`script_runner.py`, line 237) and to the element queue
as `yield_check`. Both are called whenever you touch `st.session_state` or create an element. The
function asks "is there a pending rerun or stop request?" and, if so, raises. A stop request is not
cleared until the whole run ends, so *every* later checkpoint raises too, including those in your
`finally`.

### How to validate this yourself
- `tests/ui/test_app.py::test_an_interruption_mid_answer_closes_the_connection_and_unlocks_the_page`
  and `..._between_fragments_still_closes_the_connection_at_once` are the project's executable proof.
- In the real browser: ask a slow question (stub scenario `slow-start`), press Streamlit's **Stop**
  and read the DOM: the input stays *drawn* disabled (no rerun has happened), but press `r` and it
  unlocks, with the partial answer in the history and nothing asked a second time.

### How to visualize this
Draw the script run as a line with small flags at every `st.` call and every `session_state` access
(the checkpoints). Mark the moment of the Stop. After it, colour every flag red: any code that reaches
a red flag is cut off. Your cleanup code must live entirely on the stretches with no flags.

**Main benefits.** After any Stop, refresh or crash-mid-answer the page returns to a consistent,
usable state: connection released, partial answer saved, input unlocked, nothing re-asked.

**You should now be able to explain:** why the state is a plain object, why `k` is read before the
`try`, and what bug a `session_state`-based `busy` flag would cause after a Stop.

---

## C4 — The two-run submit and the lock (a tiny state machine)

### Do this first
Run [`d4_two_runs.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d4_two_runs.py>). It
runs the **real page** with a fake API and prints the page's state at the start of every script run.
**Predict first:** after the page opens, the user types one question and presses Enter. How many
script runs does that single question cause? In which of them is the input drawn disabled? What are
`busy` and `pending` at the start of each?

### 1. Definition + analogy
**What it is.** A *state machine* is a thing that is always in exactly one of a few named states and
moves between them on events. This page has three: **idle** (nothing happening), **asked** (a
question has been accepted but not yet answered) and **answering** (the answer is being fetched).
Because of C1, each state change that must be *visible* needs a new run of the script, so one
question is handled by three runs: the run that receives it, the run that fetches the answer, and
the run that redraws the page unlocked.

**Analogy.** A restaurant. The waiter writes your order and flips the door sign to "kitchen busy"
(run 1: record and lock). The kitchen cooks while the sign is up (run 2: do the work). When the plate
is delivered the waiter flips the sign back (run 3: unlock).

### For a 14-year-old
Imagine a very small café with one cook. When you walk in, there is a bell on the counter and a sign
on the door that says OPEN. Everyone is allowed to ring the bell and order.

You ring the bell and say "burger". Here is the funny part: the sign on the door was painted **before**
you rang the bell. The painter can't know yet that you will order. By the time you order, the sign
already says OPEN and the café's rule says the sign can only be repainted by starting the painting
job over. So the waiter does this: **first** he writes your order on a slip and says "okay, I'll
repaint the sign" (shift one). The painter repaints it to BUSY (shift two). **Then** the cook, looking
at the slip, cooks the burger while the BUSY sign is already up, so nobody can order a second burger
and confuse the cook (also shift two). When the burger is done, the slip is thrown away, and one more
repaint turns the sign back to OPEN (shift three).

Why does the sign matter? Because our cook is very slow (this computer takes minutes to cook one
answer) and has only one stove. If a second customer showed up and the first customer's order got
abandoned, the cook would keep cooking the abandoned burger for nobody, and the new customer's order
would wait behind it, maybe forever.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the sign on the door, painted before you order | `st.chat_input(..., disabled=chat.busy)`, drawn before we know a question exists |
| ringing the bell and saying "burger" | `question = st.chat_input(...)` returning the text |
| the order slip | `chat.pending` |
| "repaint the sign to BUSY" | `chat.busy = True` followed by `st.rerun()` |
| the slow cook cooking while the sign is up | `answer_pending_question(chat, api_url)` running in the second run |
| throwing the slip away so it can't be cooked twice | `chat.pending = None` (line 173) |
| the final repaint to OPEN | `st.rerun()` after the answer (line 219) |
| a sign stuck on BUSY with no order on the slip | the stale state guard (lines 175-178) |

### 2. Why here specifically
The input is *drawn before* its own return value is known: you cannot both read the question from
`st.chat_input` and, in the same run, redraw that same widget as disabled. So locking requires
another run. The lock itself exists because an abandoned request keeps the one CPU model busy (gaps
G1 and G8 in [docs/api-contract.md](api-contract.md)) and the next question would queue behind it.

### What breaks if this changes
- **Remove the lock** (`disabled=False`): the user can submit twice; Streamlit's default then
  interrupts the first answer, which is abandoned while the model keeps working. (Mutation test:
  `input never locked`.)
- **Remove the final `st.rerun()`**: the answer appears, but the input is still drawn disabled until
  something else triggers a run. (Mutation test: `no unlocking redraw`.)
- **Don't clear `chat.pending`**: harmless while `busy` is cleared in the `finally`, but if `busy` is
  ever left `True`, a later run would ask the same question again. (Defence in depth.)
- **Forget the stale-state guard**: `busy=True` with `pending=None` would send `None` to the API.

### [Interview relevance: medium-high: modelling UI flow as explicit states, and preventing double submission, are classic design questions. The deeper point is "UI locks are a convenience, not a guarantee": a server-side guard (idempotency, one in-flight request per user) is what really protects a scarce resource]

**Interview question:** *"How would you prevent a user from submitting a second request while the
first is still running, in a framework that re-renders on every event?"*
**A strong answer covers:** a explicit busy/pending state stored outside the render; disabling the
control on the render *after* the submit is recorded (which in a rerun-based framework means a second
pass); clearing the pending item the moment it is taken so it cannot run twice; guarding the invalid
state (busy with nothing to do); and, since a client-side lock can be bypassed (a second tab, a direct
API call), also limiting concurrency on the server.

### 3. Minimal code that explains the concept
[`d4_two_runs.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d4_two_runs.py>)
```python
# Demo of C4: ONE question takes THREE script runs. Runs the REAL page (src/ui/app.py) with a fake API
# and prints the page's state at the start of every run.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d4_two_runs.py"
from pathlib import Path  # to locate src/ui/app.py

import httpx  # to build a fake API with MockTransport (no server, no network)
import streamlit as st  # to wrap st.chat_input so we can watch every run
from streamlit.testing.v1 import AppTest  # runs the page without a browser

from src.ui import client  # the module the page calls; we replace two of its functions below

# What the fake API will "stream": two answer fragments, a source list, then the end marker.
BODY = (  # one long string made of four SSE events
    "data: Avery \n\n"  # event 1: first fragment of the answer
    "data: Lancaster\n\n"  # event 2: second fragment
    "data: \ndata: \ndata: Sources:\ndata: [1] About (company/about.md)\n\n"  # event 3: the source list
    "data: [DONE]\n\n"  # event 4: the normal end
).encode("utf-8")  # text -> bytes, as it would arrive over a socket


def fake_ask(base_url, query, k, **_):  # replaces client.ask: same inputs, canned answer
    handler = lambda request: httpx.Response(  # noqa: E731 - a one-line handler for MockTransport
        200, headers={"content-type": "text/event-stream"}, content=BODY  # always answer with BODY
    )
    http = httpx.Client(transport=httpx.MockTransport(handler))  # an HTTP client wired to that handler
    return real_ask(base_url, query, k, http_client=http)  # run the REAL reader over the canned bytes


real_ask = client.ask  # remember the real function before replacing it
client.ask = fake_ask  # the page now "talks" to the fake API
client.check_health = lambda base_url, **_: client.Health(True, "ok")  # the sidebar thinks the API is up

rows = []  # one row per script run
original_chat_input = st.chat_input  # remember the real input widget


def recording_chat_input(*args, **kwargs):  # called once per run, because main() draws the input once
    chat = st.session_state.chat  # the page's state object (created earlier in the same run)
    rows.append((chat.busy, chat.pending, len(chat.messages), kwargs["disabled"]))  # snapshot at this moment
    return original_chat_input(*args, **kwargs)  # then behave exactly like the real widget


st.chat_input = recording_chat_input  # install the recorder

app_file = Path(__file__).resolve().parents[3] / "src" / "ui" / "app.py"  # the real page
at = AppTest.from_file(str(app_file))  # load it
at.run()  # run 0: the page opens (nobody has asked anything yet)
rows_before_question = len(rows)  # how many rows the opening produced
at.chat_input[0].set_value("Who founded Insurellm?").run()  # the user types and presses Enter

print("run | busy  | pending                 | messages | input drawn disabled")  # table header
print("----+-------+-------------------------+----------+---------------------")  # table rule
for number, (busy, pending, count, disabled) in enumerate(rows):  # one line per run
    marker = "   <- page opened" if number < rows_before_question else ""  # label the opening run
    print(f"{number:3} | {str(busy):5} | {str(pending):23} | {count:8} | {str(disabled):5}{marker}")  # the row

chat = at.session_state.chat  # the final state
print("\nfinal messages:", [(m["role"], m["content"]) for m in chat.messages])  # question + answer
print("final sources :", [m["sources"] for m in chat.messages if m["role"] == "assistant"])  # parsed sources
print("final busy    :", chat.busy)  # unlocked again
```
Real output:
```
run | busy  | pending                 | messages | input drawn disabled
----+-------+-------------------------+----------+---------------------
  0 | False | None                    |        0 | False   <- page opened
  1 | False | None                    |        0 | False
  2 | True  | Who founded Insurellm?  |        1 | True 
  3 | False | None                    |        2 | False

final messages: [('user', 'Who founded Insurellm?'), ('assistant', 'Avery Lancaster')]
final sources : [[Source(number=1, title='About', path='company/about.md')]]
final busy    : False
```
Read the table as a story. **Run 0** is just the page opening. **Run 1** is the run in which the user
pressed Enter: at its *start* nothing is pending (the question only becomes visible when
`st.chat_input` returns it, after the snapshot), and it ends by recording the question and calling
`st.rerun()`. **Run 2** starts with `busy=True`, the question pending and the input drawn disabled; it
fetches the answer and unlocks. **Run 3** starts with everything idle again and one more message in
the history: the assistant's reply.

### The exact code I wrote (`src/ui/app.py`)
> **Lines 206-220 (inside `main`):**
> ```python
>     question = st.chat_input(PLACEHOLDER, max_chars=MAX_QUESTION_CHARS, disabled=chat.busy)
>     if question and question.strip() and not chat.busy:
>         chat.messages.append(
>             {"role": "user", "content": question.strip(), "sources": [], "error": None}
>         )
>         chat.pending = question.strip()
>         chat.busy = True
>         st.rerun()
>         # first run of two: the question is recorded and the page redraws with the input locked
>     if chat.busy:
>         answer_pending_question(chat, api_url)
>         st.rerun()
>         # second run: the answer is streamed, then one more redraw unlocks the input
> ```
> **Lines 172-178 (the order slip is taken, and the stale state is handled):**
> ```python
>     question = chat.pending
>     chat.pending = None
>     if question is None:
>         chat.busy = False
>         return
> ```

**🎯 Interview-relevant lines:** line 209, `if question and question.strip() and not chat.busy`, is a
three-way guard: something was typed, it is not just spaces, and no answer is already in flight. And
`st.rerun()` raises an exception (it is a control exception too), so nothing after it in that branch
runs: it really is "stop this run, start another".

### Canonical docs
- [Streamlit: `st.chat_input`](https://docs.streamlit.io/develop/api-reference/chat)
- [Streamlit: app testing (`AppTest`)](https://docs.streamlit.io/develop/api-reference/app-testing)

### Micro-exercises
1. **Two questions.** Extend d4: after the first question, ask a second one. *Predict* the number of
   rows and which rows have `disabled=True`. *Hint:* each question adds 3 runs; with the opening run
   that is 7 rows, exactly two of them `True` (verified).
2. **Delete the unlock.** Copy `src/ui/app.py` to a scratch file, delete the second `st.rerun()`
   (line 219), point d4's `app_file` at it, and run it. *Predict* the last row and `final busy`.
   *Hint:* `busy` is cleared in the `finally`, but the *widget* is redrawn only by a run.
3. **Fire the guard.** Write a driver that does
   `at.session_state["chat"] = Chat(busy=True, pending=None)` (import `Chat` from `src.ui.app`),
   then `at.run()`. *Predict* what `api.asked` would contain and what the final `busy` is. *(This is
   `test_a_locked_page_with_nothing_to_answer_unlocks_itself`.)*
4. **Draw the machine.** On paper draw three boxes: `idle`, `asked` (busy and pending), `answering`
   (busy, pending cleared). Label every arrow with the line number that moves it (209 to 215, 217 to
   219, 185 to 187). Which arrow is the only one that needs a rerun to become *visible*?
5. **Replace two fields by one.** Design (on paper, then in a scratch copy) a single
   `state: Literal["idle", "asked", "answering"]` instead of `busy` and `pending`. What new invalid
   states disappear, and what does the stale-state guard become?

**How it works internally.** `st.rerun()` raises `RerunException`, a `BaseException`. The script runner
catches it, ends the current run, and immediately queues a new run with the same `session_state`.
`AppTest.run()` keeps following these chained reruns, which is why the driver sees all three runs from
one call.

### How to validate this yourself
- d4's table *is* the validation: if the lock were missing, the `disabled` column would never be
  `True`. `tests/ui/test_app.py::test_the_input_is_locked_only_while_the_answer_is_being_fetched`
  asserts exactly `[False, True, False]`.
- In the real browser read the DOM during an answer: `document.querySelector('textarea').disabled` is
  `true`, and `false` afterwards (done during this step for the text area, the send button and "New
  chat").

### How to visualize this
Draw three columns (runs 1 to 3). In each, a miniature of the page: the input box shaded or not, the
chat messages, and underneath the values of `busy` and `pending`. Add arrows labelled `st.rerun()`
between columns.

**Main benefits.** One question can never be asked twice; the single model slot is not flooded with
abandoned work; the state at every moment is one of three understandable situations.

**You should now be able to explain:** why locking takes an extra run, what each of the three runs
does, and which line stops a question being asked twice.

---

## C5 — Untrusted model text in a markdown renderer

### Do this first
Run [`d5_plain_text.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d5_plain_text.py>).
**Predict first:** what do you think happens in a chat page if the model's answer contains
`$10,000 per month or $12,000/month`? And `![x](http://evil.example/p.png)`? Then think about
section 3: if the model streams a hostile image in two tokens, `!` and then `[…`, will a defence that
only escapes the two-character sequence `![` stop it?

### 1. Definition + analogy
**What it is.** `st.markdown` (and `st.write_stream`, which uses it) does not just *show* text: it
**interprets** it. `$…$` becomes maths, `![alt](url)` makes the browser download an image from that
URL, `:red[text]` colours text, `**x**` makes bold. Text produced by a language model, or copied from
documents, is **untrusted input**: anyone who can influence a document (or the question) can influence
what the model writes. Escaping every punctuation character with a backslash tells the renderer "this
is just a character, do not act on it".

**Analogy.** Reading someone else's note aloud in a quiz show where certain words are *commands to
the host*, like "ring the bell". To read the note safely you put every word inside quotation marks, so
that "ring the bell" is just words and not an order.

### For a 14-year-old
Imagine a robot butler who reads a letter out loud to you. The robot has a rule: whenever it sees
certain special symbols in the text, it must **do** something instead of reading them. If it sees
`$ … $` it starts doing maths. If it sees a picture-code it runs out to fetch the picture from
whatever address is written in the letter. If it sees `:red[...]` it paints the words red.

Now suppose a stranger can sneak sentences into the letter (maybe they wrote into one of the
documents the letter was made from). They write: "go and fetch the picture from `evil.example` and,
in the address, tell the shop everything the butler knows about the household". The butler obeys,
because that is its rule, and the secret leaves the house inside a picture request.

The fix is a plastic sleeve on every symbol: before the butler reads, each special symbol gets a
backslash in front, which means "this is just a drawing of a symbol; do not obey it, just show it".
The sleeve has to go on **every single symbol individually**. Why? Because the letter arrives in
little scraps, and a dangerous picture-code can be torn in half: the `!` on one scrap and the `[` on
the next. A guard who only looks for the two symbols stuck together never sees them.

There is a second, friendlier reason too: our company's documents are full of prices like `$10,000`
and `$12,000/month`. Without sleeves the butler thinks `$10,000 per month or $` is a maths formula and
the page shows nonsense.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the robot butler reading aloud | Streamlit's markdown renderer (`st.markdown`, `st.write_stream`) |
| special symbols the butler obeys | markdown syntax: `$`, `![]()`, `:red[]`, `**`, backticks, HTML |
| the stranger sneaking in sentences | text from documents or the model: untrusted |
| the picture request leaking information | an image URL the browser fetches (an exfiltration channel) |
| the plastic sleeve on a symbol | the backslash added by `as_plain_text` |
| sleeving every symbol individually | `_ASCII_PUNCTUATION.sub(r"\\\1", text)` on every punctuation character |
| scraps of the letter arriving separately | streamed fragments / tokens |
| the guard who only looks for `![` stuck together | the rejected "escape only `![`" defence |

### 2. Why here specifically
This corpus is full of money (71 chunk files contain a `$` followed by a digit), the page streams
text token by token, and a RAG system by design feeds document text into the model, so the model's
output is exactly as trustworthy as the least trustworthy document. `as_plain_text` is applied to the
question, every streamed fragment, the history, the sources and the errors.

### What breaks if this changes
- **Raw `st.markdown`**: prices turn into maths; an `![](http://…)` in an answer makes the browser
  contact that URL; `**` and `:red[]` change the look of the page. (Checked in a real browser during
  this step: with the escape, all of it appears literally.)
- **Escape only `$` or only `![`** (demo section 3): the split-token image survives.
- **Forget to escape the typed fragments** (only the stored history): the streamed bubble would be
  unsafe for the seconds it takes to type, before the redraw. (Mutation test: `typed fragments
  unescaped`.)
- **Leave newlines alone**: single newlines would collapse into spaces in markdown; the function
  turns each into a hard line break (two spaces plus newline).

### [Interview relevance: high: "what are the security risks of rendering LLM output?" is now a standard AI-engineering question. Markdown image exfiltration, XSS via HTML, phishing links and indirect prompt injection are the vocabulary]

**Interview question:** *"Your RAG chatbot renders the model's answer as markdown. What can go wrong,
and how would you defend?"*
**A strong answer covers:** the model's output depends on retrieved documents and on the user, so
it is untrusted (indirect prompt injection); markdown can load remote images (data exfiltration via
URL), contain links (phishing), inject HTML/script if the renderer allows it, or trigger renderer
features such as maths; defences: treat output as data, sanitise or escape (or render as plain text),
disable images/links or allow-list domains, apply a Content-Security-Policy, and strip or flag
markup before streaming it; and an honest note that escaping per token is needed for streams.

### 3. Minimal code that explains the concept
[`d5_plain_text.py`](<../practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d5_plain_text.py>)
```python
# Demo of C5: text from a model is UNTRUSTED, and a markdown renderer acts on it. Shows what
# as_plain_text (src/ui/app.py) does, and why it escapes character by character.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d5_plain_text.py"
import re  # for the "pattern" version of escaping that we compare against

from src.ui.app import as_plain_text  # the real function (importing the page does NOT start it)

samples = [  # things a model, or a document, could really contain
    "Bizllm costs $10,000 per month or $12,000/month.",  # two prices: markdown would read $...$ as maths
    "![pixel](http://evil.example/p.png)",  # an image: the browser would fetch that URL
    "Click [here](http://evil.example) now",  # a link
    ":red[not red] **not bold** `not code`",  # colour directive, bold, code
    "line one\nline two",  # a newline: must stay a line break
]
print("1) what as_plain_text does to each sample")  # section title
for sample in samples:  # one block per sample
    print("   in :", ascii(sample))  # the original (ascii() makes escapes visible)
    print("   out:", ascii(as_plain_text(sample)))  # the escaped version Streamlit receives

print("\n2) the invariant: after removing escaped pairs, NO ascii punctuation is left")  # section title
punctuation = r"[!-/:-@\[-`{-~]"  # every ASCII punctuation character, as three-four ranges
everything = "".join(chr(code) for code in range(33, 127))  # every printable ASCII character once
leftover = re.sub(r"\\" + punctuation, "", as_plain_text(everything))  # drop each backslash+punct pair
print("   leftover punctuation:", re.findall(punctuation, leftover), "(empty list = nothing to interpret)")  # proof


def escape_only_image_start(text):  # a tempting, WRONG alternative: escape just the two-character "!["
    return text.replace("![", "\\![")  # works only when ! and [ are in the same piece


tokens = ["Look: !", "[pixel](http://evil.example/p.png)"]  # a model streams in tokens; "!" and "[" arrive apart
print("\n3) the same hostile image, streamed in two tokens ('!' then '[...')")  # section title
print("   escape-only-'![' :", ascii("".join(escape_only_image_start(t) for t in tokens)))  # the image survives
print("   as_plain_text    :", ascii("".join(as_plain_text(t) for t in tokens)))  # neutralised
```
Real output:
```
1) what as_plain_text does to each sample
   in : 'Bizllm costs $10,000 per month or $12,000/month.'
   out: 'Bizllm costs \\$10\\,000 per month or \\$12\\,000\\/month\\.'
   in : '![pixel](http://evil.example/p.png)'
   out: '\\!\\[pixel\\]\\(http\\:\\/\\/evil\\.example\\/p\\.png\\)'
   in : 'Click [here](http://evil.example) now'
   out: 'Click \\[here\\]\\(http\\:\\/\\/evil\\.example\\) now'
   in : ':red[not red] **not bold** `not code`'
   out: '\\:red\\[not red\\] \\*\\*not bold\\*\\* \\`not code\\`'
   in : 'line one\nline two'
   out: 'line one  \nline two'

2) the invariant: after removing escaped pairs, NO ascii punctuation is left
   leftover punctuation: [] (empty list = nothing to interpret)

3) the same hostile image, streamed in two tokens ('!' then '[...')
   escape-only-'![' : 'Look: ![pixel](http://evil.example/p.png)'
   as_plain_text    : 'Look\\: \\!\\[pixel\\]\\(http\\:\\/\\/evil\\.example\\/p\\.png\\)'
```
(`ascii()` doubles every backslash when printing, so `\\$` on screen is the two characters `\$`.)
Section 3 is the key result: the sequence `![pixel](…)` survives intact when the two halves are
processed separately by the "clever" defence.

### The exact code I wrote (`src/ui/app.py`)
> **Lines 56-57 and 76-86:**
> ```python
> _ASCII_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")
> # every ASCII punctuation character: the ranges ! to /, : to @, [ to `, and { to ~
>
> def as_plain_text(text: str) -> str:
>     """... (docstring explains the three attacks) ..."""
>     return _ASCII_PUNCTUATION.sub(r"\\\1", text).replace("\n", "  \n")
> ```
> **Where it is used (lines 127, 134, 137, 154-156):**
> ```python
>         st.markdown("\n".join(f"- {as_plain_text(source.label)}" for source in sources))
>             st.markdown(as_plain_text(message["content"]))
>             st.error(as_plain_text(message["error"]))
>             st.write_stream(
>                 as_plain_text(fragment) for fragment in itertools.chain([first], fragments)
>             )
> ```

**🎯 Interview-relevant lines:** the replacement string `r"\\\1"` means "a literal backslash, then
whatever character group 1 matched". Because each character is escaped independently, the function is
**stateless**, which is exactly what a token-by-token stream needs.

### Canonical docs
- [CommonMark spec: backslash escapes](https://spec.commonmark.org/0.31.2/) (any ASCII punctuation
  character may be backslash-escaped)
- [OWASP LLM01: prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [Streamlit: `st.write_stream`](https://docs.streamlit.io/develop/api-reference/write-magic/st.write_stream)

### Micro-exercises
1. **Property check by hand.** Write a loop over 1000 random strings built from
   `string.printable` and assert the d5 section-2 invariant for each. *Hint:* `random.choices`.
   Does any case fail?
2. **See the difference in a browser.** Start the demo stack from the end-to-end exercise and ask
   `[[scenario:prices]] what does it cost?`. Then, in a *scratch copy* of `app.py`, change
   `as_plain_text` to `return text` and ask again. *Predict* what the dollar amounts and the image
   tag look like. *Hint:* in the browser console count `img` elements inside the last chat bubble.
3. **Weaken the regex.** Remove `:` from the character class (`[!-/;-@…]`: note the changed range).
   *Predict* which sample from d5 section 1 is no longer neutralised, and which Streamlit feature
   it would re-enable.
4. **HTML.** Add `<script>alert(1)</script>` and `<img src=x onerror=alert(1)>` to `samples`. Which
   characters get escaped, and why does Streamlit not execute HTML by default anyway? *Hint:*
   `unsafe_allow_html`.
5. **Compare with plain text.** Replace `st.markdown(as_plain_text(x))` by `st.text(x)` in a copy
   of `render_message`. What do you gain (simplicity) and what do you lose (the typewriter streaming
   of `write_stream`, hard-line-break control)?

**How it works internally.** Streamlit sends the string to the browser, where a markdown library
(with maths and Streamlit's own extensions such as `:red[…]`) turns it into HTML elements. A backslash
before an ASCII punctuation character is the CommonMark rule for "literal character", and the
renderer removes the backslash when drawing. So the *source* is ugly (`\$10\,000`) but the *screen*
shows `$10,000`.

### How to validate this yourself
- d5 section 2 (the invariant) and `tests/ui/test_app.py::test_as_plain_text_leaves_markdown_nothing_to_interpret`.
- In the real browser, run in the console after an answer to the `prices` scenario:
  `const b=[...document.querySelectorAll('[data-testid="stChatMessage"]')].pop(); [b.querySelectorAll('img').length, b.querySelectorAll('a').length, b.querySelectorAll('strong').length]`.
  Expected `[0, 0, 0]` (verified during this step).

### How to visualize this
Make a two-column table: left, the raw model text; right, a screenshot of how the page shows it with
and without `as_plain_text`. The images, links and maths in the left screenshot are the attack surface
made visible.

**Main benefits.** Prices display as prices; a hostile document cannot make the browser fetch a URL
or restyle the page; the function is tiny, stateless, and safe for streamed fragments.

**You should now be able to explain:** why model output is untrusted, what each kind of markdown can
do, and why escaping must be per character rather than per pattern.

---

## Consolidated end-to-end exercise: watch the whole thing work

This ties C1 to C5 together using the scripted stub API (instant, repeatable) and the real Streamlit
page. Nothing needs Docker. From the project root, in Git Bash. (`PYTHONPATH` entries are separated by
`;` on Windows.)

1. **Start the stub API** (terminal 1). It is the real FastAPI app with a scripted model:
   ```bash
   PYTHONPATH="practice codes/phase 7/m7_api_contract_checkpoint;practice codes/phase 8/m1_streamlit_chat_ui;." \
     .venv/Scripts/python -m uvicorn ui_stub:app --port 8004
   ```
2. **See the raw stream** (terminal 2). This is C2 on the wire; `cat -A` shows line ends as `$`:
   ```bash
   curl -s -N -X POST http://127.0.0.1:8004/chat -H "Content-Type: application/json" -d '{"query":"hello","k":2}' | cat -A
   ```
   Real output (the blank lines are the events' ends):
   ```
   data: Acme $
   $
   data: was $
   $
   data: founded $
   $
   data: by $
   $
   data: Ada.$
   $
   data: $
   data: $
   data: Sources:$
   data: [1] Doc 1 (company/doc1.md)$
   data: [2] Doc 2 (company/doc2.md)$
   $
   data: [DONE]$
   $
   ```
   Predict before reading: how many events are there? (Seven: five fragments, the source list, `[DONE]`;
   count the blank lines.) Notice the source list is *one* event of several `data:` lines.
3. **Start the page** (terminal 3), pointing it at the stub:
   ```bash
   API_URL=http://127.0.0.1:8004 .venv/Scripts/python -m streamlit run src/ui/app.py --server.port 8501
   ```
   The startup line should say `127.0.0.1:8501` (the `.streamlit/config.toml` file keeps it off your
   network). Open that address in a browser.
4. **Watch C1.** Add `print("RUN", chat.busy, chat.pending)` right after `chat = get_chat()` in
   `main()` (remove it afterwards). Open the page (one `RUN` line), then ask a question and count the
   new `RUN` lines in terminal 3: you should see three, the runs from C4's table.
5. **Watch C4 live.** Ask `[[scenario:slow-start]] hello` (the first word takes 5 s). While the clock
   counts, open the browser console and run
   `[document.querySelector('textarea').disabled, [...document.querySelectorAll('button')].filter(b=>/New chat/.test(b.innerText))[0].disabled]`.
   Expected `[true, true]`; after the answer, `[false, false]`.
6. **Watch C5.** Ask `[[scenario:prices]] what does it cost?`. Read the bubble: dollar amounts and
   the image/colour/bold/HTML text appear *literally*. Count images with the console line from C5.
7. **Watch C2 with an awkward stream.** Ask `[[scenario:framing]] edge cases`. In terminal 2 run the
   same `curl … | cat -A` with that scenario in the question and compare what is on the wire with what
   the bubble shows: line breaks arrive as plain line breaks, U+2028 survives.
8. **Watch C3 happen.** Ask `[[scenario:slow-start]] stop me`, wait 8 seconds, then click Streamlit's
   **Stop** button (top right). Look at the input: it is still drawn disabled (no run happened). Press
   the `r` key. It unlocks, and the history contains your question and the partial answer
   (`The first words took`). In terminal 1, check that `/chat` was requested exactly once: nothing was
   asked twice.
9. **Break it on purpose, then fix it.** Stop terminal 1 (Ctrl+C) and ask another question. Expected:
   the sidebar turns red ("Cannot reach the API… Start it with: uvicorn src.api.main:app"), and the
   bubble says it cannot reach the API; the input unlocks after a few seconds. Restart the stub and
   ask again: it works without restarting the page (C1: the state lives in the session, not in a
   connection).
10. **Run the safety net.** Stop everything and run:
    ```bash
    .venv/Scripts/python -m pytest tests/ui -q
    ```
    150 tests pass in about 17 seconds (104 were there after P8-M1; P8-M3 added 46). Then break one
    safeguard (for example delete line 187,
    `chat.busy = False`) and watch which tests fail. Put it back.
11. **Real stack, once (optional, minutes).** With Docker up and the real API on port 8000, run the page
    without `API_URL`, set `k` to 1 with the sidebar slider and ask "Who founded Insurellm?". Watch
    the running clock; on this CPU-only host the whole request took 4 min 27 s in the run recorded in
    [notes/step-8.1-streamlit-chat-ui.md](../notes/step-8.1-streamlit-chat-ui.md).

When all eleven steps make sense, you can explain the whole page: how it remembers, how it reads, how
it survives being stopped, why a question takes three runs, and why every word it shows is treated as
untrusted.
