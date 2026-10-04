# 29 — LLM Wrapper (`LocalLLM`): Deep Dive (P6-M2)

> The demo outputs below were produced on 2026-09-30 by running the scripts in
> [`practice codes/phase 6/m2_llm_wrapper_deep_dive/`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/)
> against the real [`src/orchestration/llm.py`](../src/orchestration/llm.py). None of them needs
> Ollama, Docker or the network at all. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 6/m2_llm_wrapper_deep_dive/d0_generator_streaming.py"
> ```

---

## 0. Plain-language summary

`LocalLLM` is a small class with exactly two jobs: turn a prompt into one complete answer
(`generate`), or turn it into a live sequence of pieces as the model produces them (`stream`).
Nothing outside this file ever needs to know it's really the `ollama` package underneath, or
what that package's own calling convention looks like — that's the whole point of it.

```
LocalLLM(client, model=None)
  │  model resolved from settings.llm_model HERE, not at import time     <- C3
  ▼
.generate(prompt, temperature, max_tokens)          .stream(prompt, temperature)
  │  validate, then ONE real call                      │  validate, then ONE real call
  │  options = {temperature, num_predict}               │  options = {temperature}        <- C5
  │  stream=False -> one GenerateResponse                │  stream=True -> an iterator     <- C4
  ▼                                                      ▼
return response.response                             for chunk in chunks: yield chunk.response
                                                          (nothing runs until iterated)      <- C1
                    (both hide ollama.Client entirely -- calling code never sees it)  <- C2
```

Five concepts, in the order they matter most to understanding this file:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | Streaming as a Python generator | 🔴 very high | `stream`, lines 98-117 |
| C2 | The adapter / wrapper pattern | 🔴 very high | the whole `LocalLLM` class, lines 64-117 |
| C3 | Resolving a default with `is None`, not `or` | 🔴 high | `__init__`, lines 67-72 |
| C4 | A Protocol matching an SDK's own overloaded method | 🟡 medium-high | `LLMClient`, lines 21-36 |
| C5 | Vocabulary mapping across an interface boundary | 🟡 medium | `options={...}`, lines 91, 108 |

---

## C1 — Streaming as a Python generator

### Do this first
Run [`d0_generator_streaming.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d0_generator_streaming.py).
**Predict first:** right after the line `generator = streamed_answer([...])` runs, has ANY of
`streamed_answer`'s own code executed yet — including its very first `print` line?

### 1. Definition + analogy
**What it is.** A Python **generator** is a function containing `yield` instead of (or as well
as) `return`. Calling it does NOT run its body at all — it hands back a paused, resumable object.
Each time the caller asks for the next value (`next(...)`, or a `for` loop doing that for you),
the function's body runs from where it left off, up to the next `yield`, hands that one value
over, and pauses again. `LocalLLM.stream` is exactly this: a generator function.

**Analogy.** A cook who plates and serves each dish the moment it's ready, versus a cook who
prepares the entire five-course meal in the kitchen and only brings everything out at the very
end. The first way, you're eating (using data) while the kitchen (the function) is still working.

### For a 14-year-old
Imagine ordering a big meal at a restaurant. One kind of restaurant makes you wait in your seat,
doing nothing, while the kitchen prepares every single course — starter, soup, main, dessert —
and only brings out ALL of it at once, the moment the very last dish is ready. You get everything
at the same time, but you waited the ENTIRE time for even the first bite.

A different restaurant brings each dish out the SECOND it's ready — the starter arrives while the
kitchen is still working on the soup, and you're already eating while the rest of your meal is
still being cooked. You get your first bite much sooner, even though the kitchen finishes at
roughly the same total time either way.

A Python generator function works exactly like the second restaurant. The moment you "order"
(call the function), the kitchen (the function's code) doesn't even START cooking yet — it just
hands you a ticket number (the generator object). Only when you actually ask "is my first course
ready?" (calling `next()`, which a `for` loop does automatically) does the kitchen actually start
working, cook ONE course, and hand it straight to your table (`yield`). Then it waits, doing
nothing, until you ask for the next course. If you never ask for a second course, the kitchen
never even starts cooking it.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| ordering, getting a ticket number, kitchen hasn't started | calling `llm.stream(...)`, which returns instantly with nothing computed yet |
| "is my first course ready?" | the `for` loop's first `next()` call |
| the kitchen cooking exactly one course | one trip through the `for chunk in chunks:` loop body |
| the dish arriving at your table | `yield chunk.response` |
| the kitchen pausing until you ask again | the generator suspending at `yield`, resuming only on the next `next()` |
| never ordering dessert, so it's never cooked | a caller that stops iterating early — Ollama's own request can even be cancelled |

### 2. Why here specifically
Generation is genuinely slow (P6-M1 measured 68.8s cold, and even warm, a long answer takes real
time token by token). If `stream` collected the whole answer first and returned it as one string,
a caller would wait for the ENTIRE answer before seeing a single word — indistinguishable from
`generate()`, defeating the entire purpose of the task asking for a separate streaming method.
`yield` is what lets a caller (eventually, a chat UI) show words appearing as they're produced.

### What breaks if this changes
- **Replace `yield chunk.response` with building a list and `return`ing it at the end:**
  functionally similar output, but the caller now waits for the FULL answer before getting
  anything — `stream()` degrades into a slower, differently-shaped `generate()`.
- **Remove the `if chunk.response:` check** (yield every chunk unconditionally): the final,
  empty, `done=True` chunk would be yielded too — a caller building a string by concatenation
  wouldn't notice, but one printing each fragment would show one extra, confusing empty line.
- **Call `list(llm.stream(...))` immediately after getting it, everywhere:** this doesn't break
  anything technically, but it throws away the entire benefit — you've turned a generator back
  into a plain list voluntarily, paying stream's complexity for buffered's behavior.

### [Interview relevance: very high — "how would you implement streaming for an LLM response" is one of the most common practical AI-engineering interview questions, and understanding generators (not just using `yield` from memory) is what separates "I've seen this work" from "I know why it works"]

**Interview question:** *"How would you stream tokens from a local or hosted LLM back to a
caller, and why use a generator instead of collecting the full response first?"*
**A strong answer covers:** a generator function (`yield`) suspends and resumes rather than
running to completion before returning anything; this lets a caller start displaying/processing
output the moment the first piece arrives, not after the whole (possibly slow) generation
finishes; the underlying SDK call itself must actually support streaming (an HTTP response read
incrementally, not one big buffered response) for this to have any real effect; and that
generators are lazy — nothing runs until iteration actually starts, which matters for correctly
reasoning about WHEN validation or errors will actually surface (see the micro-exercises).

### 3. Minimal code that explains the concept
The complete script, [`d0_generator_streaming.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d0_generator_streaming.py) (run from the project root):
```python
# Minimal demo of C1: a generator function's BODY does not run until you ask it for the
# next value -- proven here by comparing WHEN print statements fire for a "buffered"
# (returns a full list) version versus a "streamed" (yields one piece at a time) version.
import time  # lets this demo simulate a slow model "thinking" between words


def buffered_answer(words):
    print("  buffered: starting work (nothing has been produced yet)...")  # runs immediately
    pieces = []  # will hold every word before handing ANY of it back
    for word in words:  # simulates the model producing one word at a time
        time.sleep(0.3)  # simulates the real thinking time each word costs
        print(f"  buffered: produced {word!r} (but the CALLER still has nothing)")  # proves the caller is still waiting
        pieces.append(word)  # keeps it, instead of handing it over right away
    return pieces  # only NOW, after everything is done, does the caller get anything


def streamed_answer(words):
    print("  streamed: function CALLED, but not one line inside it has run yet")  # proves nothing ran yet
    for word in words:  # the SAME loop as above
        time.sleep(0.3)  # the SAME simulated thinking time
        print(f"  streamed: about to yield {word!r}")  # runs just before handing this one word over
        yield word  # hands this ONE word to the caller immediately -- this IS LocalLLM.stream()


print("--- calling buffered_answer(...) ---")  # heading for the buffered version
result = buffered_answer(["The", "answer", "is", "4"])  # this line blocks until EVERYTHING is ready
print("  caller received:", result)  # only prints after the full 1.2s wait

print("\n--- calling streamed_answer(...) ---")  # heading for the streamed version
generator = streamed_answer(["The", "answer", "is", "4"])  # THIS LINE RUNS INSTANTLY
print("  streamed_answer(...) returned already -- did any 'about to yield' line print above? No.")
for word in generator:  # each loop step is what actually resumes the function's body
    print("  caller received:", repr(word), "<- the moment this line runs, that word is already usable")
```
Real output:
```
--- calling buffered_answer(...) ---
  buffered: starting work (nothing has been produced yet)...
  buffered: produced 'The' (but the CALLER still has nothing)
  buffered: produced 'answer' (but the CALLER still has nothing)
  buffered: produced 'is' (but the CALLER still has nothing)
  buffered: produced '4' (but the CALLER still has nothing)
  caller received: ['The', 'answer', 'is', '4']

--- calling streamed_answer(...) ---
  streamed_answer(...) returned already -- did any 'about to yield' line print above? No.
  streamed: function CALLED, but not one line inside it has run yet
  streamed: about to yield 'The'
  caller received: 'The' <- the moment this line runs, that word is already usable
  streamed: about to yield 'answer'
  caller received: 'answer' <- the moment this line runs, that word is already usable
  streamed: about to yield 'is'
  caller received: 'is' <- the moment this line runs, that word is already usable
  streamed: about to yield '4'
  caller received: '4' <- the moment this line runs, that word is already usable
```
Notice: `streamed_answer(...)` was CALLED, and immediately returned — with none of the "about to
yield" lines from inside it having printed yet. They only start appearing once the `for` loop
begins asking for values.

### The exact code I wrote (`src/orchestration/llm.py`)
```python
# src/orchestration/llm.py, lines 98-117, verbatim
 98 |     def stream(self, prompt: str, temperature: float) -> Iterator[str]:  # 🎯 a generator function -- calling this returns INSTANTLY, nothing below has run yet
 99 |         """`prompt`'s answer, yielded one real fragment at a time as it is generated."""
100 |         _check_text("prompt", prompt)
101 |         # refuses an empty prompt before any call is made
102 |         _check_temperature(temperature)
103 |         # refuses a bad temperature before any call is made
104 | 
105 |         chunks = self._client.generate(  # 🎯 not reached until the first next() / for-loop iteration begins
106 |             model=self._model,
107 |             prompt=prompt,
108 |             options={"temperature": temperature},
109 |             stream=True,
110 |         )
111 |         # asks the model to start streaming its answer, one real fragment at a time
112 |         for chunk in chunks:  # 🎯 only the for loop's first next() reaches this line
113 |             # walks every fragment Ollama sends, in the exact order it sends them
114 |             if chunk.response:  # 🎯 skips the one chunk that carries no real text
115 |                 # skips the final fragment, which only carries done=True and no text
116 |                 yield chunk.response  # 🎯 THE line -- one real fragment reaches the caller here
117 |                 # hands this one fragment to the caller immediately, not buffered
```
**🎯 Interview-relevant lines:** the mere presence of `yield` on line 116 is what makes lines 98-117
a generator FUNCTION rather than an ordinary one — Python decides this purely by scanning the
function body for a `yield` keyword anywhere in it, at compile time, before the function ever runs.

### Canonical docs
- Python docs, [Generators](https://docs.python.org/3/tutorial/classes.html#generators)
- Python docs, [`yield` expression](https://docs.python.org/3/reference/expressions.html#yield-expressions)
- [Ollama API reference](https://github.com/ollama/ollama/blob/main/docs/api.md) — `"stream":
  true`, the real, underlying mechanism `stream()` relies on.

### Micro-exercises
1. In `d0`, add a `print("right after calling")` line immediately after
   `generator = streamed_answer(...)`. Where does it appear relative to the function's own first
   print line? *Hint: you predicted this already — now prove it.*
2. Change the `for word in generator:` loop to only take the FIRST value
   (`next(generator)` once, no loop). Does `streamed_answer`'s "about to yield 'answer'" line ever
   print? *Hint: what happens to a paused generator nobody asks again?*
3. In a scratch copy of `src/orchestration/llm.py`, call `llm.stream("bad prompt", temperature=-1)`
   (an invalid temperature) but DON'T iterate the result — just store it in a variable. Does a
   `ValueError` get raised immediately? Now call `next(...)` on that stored variable. *Hint: this
   is the real-world version of exercise 1's lesson, and it matters for when validation errors
   actually surface.*
4. Rewrite `buffered_answer` to also use `yield`, one word at a time, but keep the `pieces.append`
   line too (so it does both). Run it. Does making it a generator change when the `time.sleep`
   calls happen, or only when the RESULTS become visible to the caller?
5. Look up Python's `itertools.islice`. Use it to take only the first 2 fragments from
   `streamed_answer(["a", "b", "c", "d"])` without writing your own loop-and-break.

**How it works internally.** When Python compiles a function whose body contains `yield`
anywhere, it doesn't produce an ordinary function at all — it produces a **generator function**,
which, when called, returns a generator OBJECT without running any of the body. That object
implements `__next__`: each call runs the underlying code from its current paused position
(initially, the very start) until it hits a `yield`, captures that value, and freezes execution
state (local variables, the current line) exactly where it stopped, ready to resume on the next
call. A `for` loop is just repeatedly calling `__next__` until it catches the special
`StopIteration` signal generators raise when the function body actually finishes.

### How to validate this yourself
Add a `print(f"chunk arrived at {time.perf_counter():.2f}s")` right before every `yield` in a
scratch copy of `stream()`, then call it against the REAL, running Ollama and print a timestamp
in the CALLING code too, right after each `for chunk in llm.stream(...)` iteration. The two sets
of timestamps should be almost identical — proof the fragment reaches the caller the moment it's
produced, not batched up afterward.

### How to visualize this
Draw a horizontal timeline. Mark `buffered_answer`'s single "return" event as ONE dot, far to the
right (after all work is done). Mark `streamed_answer`'s FOUR `yield` events as four separate
dots, spread out left to right, each one at the moment that specific word became ready. The
"first dot reached" position is dramatically earlier for the streamed version.

**You should now be able to explain:** why calling a generator function does nothing by itself,
what actually triggers its body to run, and why that specific behavior is exactly what makes
"streaming" a real, useful difference from `generate()` rather than just a different method name.

---

## C2 — The adapter / wrapper pattern

### Do this first
Run [`d1_adapter_pattern.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d1_adapter_pattern.py).
**Predict first:** `VendorAClient` and `VendorBClient` have completely different method names and
argument shapes. Will the SAME `llm.generate(PROMPT, temperature=0.0)` line work against both,
unmodified?

### 1. Definition + analogy
**What it is.** An **adapter** (or wrapper) is a class whose whole job is translating between one
interface (what YOUR code wants to call) and a different interface (what some library, service,
or vendor actually provides) — so the rest of your codebase only ever depends on YOUR interface,
never on the library's specific shape. `LocalLLM` is an adapter around `ollama.Client`: calling
code writes `llm.generate(prompt, temperature, max_tokens)`, never
`client.generate(model=..., prompt=..., options={...}, stream=False)`.

**Analogy.** A universal travel power adapter: your laptop charger always plugs into the SAME
adapter shape, no matter which country's wall socket (a completely different physical shape) is
on the other side. You never redesign your charger's plug for each country.

### For a 14-year-old
Imagine you have one phone charger, and you travel to three different countries, each with a
DIFFERENT shaped wall socket — one round, one flat, one with three prongs at odd angles. You
could buy a brand new charger for every single country you visit... or you could buy ONE travel
adapter, plug your SAME charger into it every time, and just swap which "socket side" of the
adapter you use depending on where you are. Your charger itself never changes. Your phone never
even knows or cares which country's socket it's ultimately connected to — the adapter hides all
of that.

`LocalLLM` is that travel adapter for your code. Later parts of this project (a future
prompt-construction step, eventually maybe an API endpoint) are like your phone — they just want
to say "generate an answer to this prompt" and get text back, the exact same way every single
time. Today, the "wall socket" on the other side happens to be the raw `ollama` package. If this
project ever switched to a different model-serving library, or even a completely different local
model runtime, ONLY `LocalLLM`'s own insides would need to change — every single piece of code
that calls `llm.generate(...)` would keep working, completely unaware anything changed at all.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| your ONE phone charger, unchanged everywhere | calling code: `llm.generate(prompt, temperature, max_tokens)` |
| the different wall sockets in each country | `ollama.Client`'s own real shape (today); some other library (potentially, later) |
| the travel adapter itself | the `LocalLLM` class |
| "your phone never knows which country it's in" | nothing outside `llm.py` ever imports `ollama` directly |
| swapping which socket-side of the adapter you use | `LLMClient` Protocol + `build_client` -- the ONE place that would change |
| buying a whole new charger for every country (the alternative) | every caller writing its own `ollama.Client(...)` calls directly, everywhere |

### 2. Why here specifically
The task's own stated inputs listed "LangChain or LlamaIndex library" as options, and D1's actual
choice was the raw `ollama` package instead — the whole reason that choice was SAFE to make
without locking the project in is that `LocalLLM` is an adapter. If a later step needs LangChain's
chain/prompt-template ecosystem after all, only `src/orchestration/llm.py`'s internals need to
change; every future call site written against `LocalLLM.generate`/`.stream` keeps working exactly
as written.

### What breaks if this changes
- **Call `ollama.Client(...)` directly from many different places in the codebase** (skip the
  adapter entirely): every one of those call sites is now coupled to `ollama`'s specific method
  names and argument shapes; switching libraries later means hunting down and rewriting every
  single one, instead of editing one file.
- **Leak the underlying client through the adapter's own interface** (e.g., add a
  `llm.raw_client` property callers start relying on): the adapter stops being a real boundary —
  calling code that reaches through it is coupled to `ollama` again, just less obviously.
- **Make `LocalLLM`'s constructor build its OWN client internally**, instead of accepting one:
  every hermetic test in `tests/orchestration/test_llm.py` would need a real Ollama server, since
  there'd be no seam left to inject a fake through.

### [Interview relevance: very high — the adapter pattern (and more broadly, "depend on an abstraction, not a concrete implementation") is one of the most frequently tested general software-design concepts in technical interviews, doubly so for AI engineering roles where swapping model providers is routine]

**Interview question:** *"Why wrap a third-party SDK in your own class instead of calling it
directly throughout your codebase?"*
**A strong answer covers:** isolates the cost of a future vendor/library swap to one file instead
of every call site; lets you unit-test calling code against a fake, without a real service
running; lets you enforce your OWN validation/defaults/naming consistently, independent of
whatever the underlying library happens to expose; and — the sharper point — the adapter's
interface should be designed around what YOUR application needs (here: `prompt, temperature,
max_tokens`), not simply mirrored from the underlying library's own shape.

### 3. Minimal code that explains the concept
The complete script, [`d1_adapter_pattern.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d1_adapter_pattern.py) (run from the project root):
```python
# Minimal demo of C2: two completely different vendor libraries, each with their OWN
# calling convention, hidden behind ONE adapter class -- so calling code never changes
# when the backend underneath is swapped, exactly like LocalLLM hides ollama.Client.
class VendorAClient:  # imagine this is a real, installed third-party library
    def complete_text(self, text_in, *, randomness):  # its OWN, vendor-specific names
        return f"[VendorA saw {text_in!r} at randomness={randomness}] -> 42"  # its OWN reply shape


class VendorBClient:  # a COMPLETELY different library, for a different provider
    def run(self, payload):  # a different method name, a different argument shape entirely
        return {"output_text": f"[VendorB got {payload['input']!r}] -> 42"}  # a different reply shape


class LocalLLM:  # the ONE stable interface calling code is written against
    def __init__(self, vendor):
        self._vendor = vendor  # stores WHICHEVER vendor object was handed in
        self._kind = type(vendor).__name__  # remembers which one, only to explain this demo

    def generate(self, prompt, temperature):  # ONE method name, ONE argument shape, always
        if self._kind == "VendorAClient":  # translates to Vendor A's own convention
            return self._vendor.complete_text(prompt, randomness=temperature)  # A's real call
        if self._kind == "VendorBClient":  # translates to Vendor B's own, different convention
            return self._vendor.run({"input": prompt})["output_text"]  # B's real call
        raise TypeError(f"no adapter written for {self._kind}")  # a vendor nobody taught this class about


PROMPT = "What is 6 times 7?"  # the ONE question asked through both backends, unchanged

for vendor in (VendorAClient(), VendorBClient()):  # tries the SAME calling code against BOTH vendors
    llm = LocalLLM(vendor)  # wraps whichever vendor this loop iteration has
    print(f"backend = {type(vendor).__name__:<14} -> llm.generate(...) = {llm.generate(PROMPT, temperature=0.0)!r}")
    # the calling code above (llm.generate(PROMPT, temperature=0.0)) NEVER changed between the two runs
```
Real output:
```
backend = VendorAClient  -> llm.generate(...) = "[VendorA saw 'What is 6 times 7?' at randomness=0.0] -> 42"
backend = VendorBClient  -> llm.generate(...) = "[VendorB got 'What is 6 times 7?'] -> 42"
```

### The exact code I wrote (`src/orchestration/llm.py`)
```python
# src/orchestration/llm.py, lines 64-77, verbatim
 64 | class LocalLLM:
 65 |     """Generates text with a local Ollama model, hiding the client library underneath."""  # 🎯 the ONE interface every future caller depends on
 66 | 
 67 |     def __init__(self, client: LLMClient, *, model: str | None = None) -> None:
 68 |         resolved_model = settings.llm_model if model is None else model
 69 |         # reads the configured default at construction time, never at import time;
 70 |         # "is None", not "or" -- an explicitly-passed "" must still be rejected below,
 71 |         # not silently replaced by the default the way "" or settings.llm_model would
 72 |         _check_text("model", resolved_model)
 73 |         # refuses a blank model name before it is ever used
 74 |         self._client = client  # 🎯 the vendor object is stored, never inspected or branched on by type
 75 |         # keeps the client every generate()/stream() call is sent through
 76 |         self._model = resolved_model
 77 |         # keeps the model name every call uses
```
```python
# src/orchestration/llm.py, lines 1-18, verbatim
  1 | """Local LLM wrapper: hide which library talks to Ollama behind one small interface, so  # 🎯 the docstring states the adapter's whole purpose in its first sentence
  2 | later Phase 6 steps call `generate`/`stream` without knowing or caring what sits underneath.
  3 | 
  4 | Retrieval (Phases 3-5) only ever fetched text that already existed. This is the first class
  5 | in the whole project that PRODUCES new text -- every call here is a live, unpredictable
  6 | inference request to a real model, not a lookup against something already stored.
  7 | """
  8 | 
  9 | from __future__ import annotations
 10 | 
 11 | import math
 12 | from collections.abc import Iterator, Mapping
 13 | from typing import Any, Literal, Protocol, overload
 14 | 
 15 | import ollama  # 🎯 THIS is the one import that ties the adapter to a specific implementation
 16 | 
 17 | from src.core.config import settings
 18 | from src.generation.setup import build_client
```
**🎯 Interview-relevant lines:** line 15 is the entire "wall socket" side of the adapter — it is
the ONLY line in this whole project (outside `llm.py` and `generation/setup.py` themselves) that
names the `ollama` package. Every other file that will ever call `LocalLLM` never needs to.

### Canonical docs
- [Refactoring Guru: Adapter pattern](https://refactoring.guru/design-patterns/adapter) — a
  clear, language-agnostic explanation with diagrams.
- Gamma, Helm, Johnson, Vlissides (1994), *Design Patterns* ("the Gang of Four book") — the
  original, canonical source of the Adapter pattern's name and definition.

### Micro-exercises
1. In `d1`, add a `VendorCClient` with yet another different calling convention (invent one).
   Extend `LocalLLM.generate` to support it. How many places OUTSIDE `LocalLLM` did you have to
   change to make the demo's final loop work with the new vendor too?
2. Remove the `if self._kind == "VendorAClient":` branch entirely and just always call Vendor A's
   method. Run the demo. What error do you get when it reaches Vendor B, and does the error
   message make the REAL cause obvious or confusing?
3. In `src/orchestration/llm.py`, count how many places (outside the file itself) currently
   import `ollama` directly. *Hint: use a project-wide search.* Is the count what C2's whole
   argument predicts?
4. Design, on paper, what would need to change in `LocalLLM` (not in any caller) if this project
   switched its local model server from Ollama to a different local runtime entirely. List every
   method/line that would need editing.
5. Find one OTHER adapter already in this project (hint: `src/vectorstore/store.py`'s
   `QdrantVectorStore`, or `src/retrieval/graph_store.py`'s `GraphStore`). What library does IT
   hide, and what's the one interface everything else calls instead?

**How it works internally.** There's no special Python mechanism here at all — an adapter is
purely a DESIGN discipline, enforced by convention (and code review), not by the language. Python
will happily let you import `ollama` from ten different files; nothing stops you except the
decision to keep that coupling in one place.

### How to validate this yourself
Run `grep -rn "import ollama" src/` from the project root. Today it should return exactly two
files: `src/generation/setup.py` and `src/orchestration/llm.py` — confirming the adapter boundary
is real, not just claimed. Then check `tests/orchestration/test_llm.py`: every hermetic test
builds a `FakeClient` that never imports `ollama.Client` at all, proving `LocalLLM` really can be
exercised without the real library underneath.

### How to visualize this
Draw a vertical line down the middle of a page. On the LEFT, draw every future caller (a box
labelled "prompt construction," another "API endpoint," another "tests") each with ONE arrow
pointing to a single box in the middle: `LocalLLM`. On the RIGHT of `LocalLLM`, draw exactly ONE
arrow leading to a box labelled `ollama.Client`. The left side has many arrows converging on one
box; the right side has exactly one arrow leaving it.

**You should now be able to explain:** what a wrapper/adapter class actually buys you, and why
"which library are we using" is a decision that should only ever live inside one file.

---

## C3 — Resolving a default with `is None`, not `or`

### Do this first
Run [`d2_is_none_vs_or.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d2_is_none_vs_or.py).
**Predict first:** a caller explicitly passes `model=""` (an empty string) — deliberately, maybe
by a bug elsewhere, maybe from an empty form field. Should that count as "no model given, use the
default," or as "a bad model name that should be rejected"?

### 1. Definition + analogy
**What it is.** `value or default` in Python doesn't mean "if `value` is missing, use `default`"
— it means "if `value` is *falsy* (`None`, `""`, `0`, `False`, `[]`, and more), use `default`."
`value if value is None else ...` (or, equivalently, `default if value is None else value`) means
exactly and only what it says: check for `None` specifically, and nothing else. This is the exact
bug found and fixed while building `LocalLLM`, documented in
[decision.md D-2026-09-30's own finding](../decision.md).

**Analogy.** A form that says "leave this field blank to use the default" versus one that (by
mistake) also treats "0" or "no" as if you'd left it blank — silently overriding an answer you
actually gave.

### For a 14-year-old
Imagine an online form with a box that says: "Enter how many pets you have (leave blank for the
default of 1)." You type "0" because you genuinely have zero pets. A badly-written form processor
checks "is this field EMPTY-ish?" using a lazy rule that treats "0" the SAME as actually leaving
it blank — because to a computer, both can look like "nothing here" if you're not careful about
HOW you check. So your honest "0" answer gets silently thrown away and replaced with the
default, "1" — even though you very clearly and deliberately typed a real answer.

A carefully-written form processor instead checks ONE specific thing: "did the person literally
never touch this field at all?" — not "does their answer look empty-ish." That's the difference
between `value or default` (the lazy, buggy check — "does this LOOK falsy?") and
`default if value is None else value` (the careful check — "was this LITERALLY never provided?").
Zero pets is a real, valid, different answer from "you didn't tell me" — and only the careful
check tells them apart.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "leave blank for the default" | `model: str | None = None` |
| typing "0" as a real, deliberate answer | a caller explicitly passing `model=""` |
| the lazy form processor treating "0" as blank | `model or settings.llm_model` (the REJECTED, buggy version) |
| the careful processor checking "was it literally never touched" | `settings.llm_model if model is None else model` (the ACTUAL code) |
| your honest "0" getting silently overwritten | `""` silently becoming `"mistral"` instead of being rejected |
| the careful form still validating "0" makes sense afterward | `_check_text("model", resolved_model)` running AFTER resolution, catching the real `""` |

### 2. Why here specifically
`LocalLLM.__init__` needs THREE different things to be true at once: (1) a caller who passes
nothing should get the configured default; (2) a caller who passes an explicit, valid model name
should get exactly that; (3) a caller who passes an explicit but INVALID value (like `""`) should
get a clear error, not have their mistake silently hidden behind the default. Only checking for
`None` specifically can tell case (1) apart from case (3) — `or` cannot, because it has no way to
distinguish "never given" from "given, but happens to be falsy."

### What breaks if this changes
- **This project's own real bug** (`model or settings.llm_model`): `LocalLLM(client, model="")`
  silently built a perfectly normal-looking `LocalLLM` using the DEFAULT model — the caller's
  actual (bad) input vanished with no error at all, exactly the kind of bug that's invisible until
  something downstream behaves unexpectedly and nobody can figure out why.
- **The same mistake would apply to `temperature=0.0`, if that parameter's default were EVER
  resolved this way:** `0.0 or default` would wrongly replace a deliberately-chosen "no
  randomness at all" setting with whatever the default temperature is — this project's actual
  `_check_temperature` doesn't have this bug (it validates its literal parameter directly, no `or`
  involved), but it's worth noticing WHY that design choice avoided the trap.

### [Interview relevance: high — "what's wrong with `x or default_value`" is a classic Python gotcha interview question, and this project has now hit the real version of it twice in two consecutive steps]

**Interview question:** *"What's the difference between `value or default` and `default if value
is None else value`, and when does it actually matter?"*
**A strong answer covers:** `or` treats EVERY falsy value the same as `None` (`0`, `""`, `False`,
`[]`, `{}` all trigger the fallback); this only matters when a falsy value could be a legitimate,
meaningful input (an explicit `0`, an explicit empty string meant to be rejected, a `False` flag);
`is None` (or a dedicated sentinel) is the precise way to check "was this argument actually
supplied" versus "does it happen to look empty"; and — the strongest answer — naming this
project's own repeat of the mistake (`hybrid_retriever.py`'s comment, then `LocalLLM`'s own first
draft) as proof it's a genuinely easy trap to fall into even when you already know the rule.

### 3. Minimal code that explains the concept
The complete script, [`d2_is_none_vs_or.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d2_is_none_vs_or.py) (run from the project root):
```python
# Minimal demo of C3: `x or default` silently replaces ANY falsy value (not just missing
# ones) with the default -- the exact bug LocalLLM's first draft had for its model name.
FALSY_BUT_REAL_VALUES = ["", 0, False, [], "   "]  # values a caller might deliberately pass
DEFAULT = "fallback-value"  # stands in for settings.llm_model


def resolve_with_or(value):
    return value or DEFAULT  # the REJECTED, buggy pattern LocalLLM's first draft used


def resolve_with_is_none(value):
    return DEFAULT if value is None else value  # the FIXED pattern LocalLLM actually uses


print(f"{'value passed':<14}{'or-based result':<20}{'is None-based result':<22}should differ from DEFAULT?")
for value in [*FALSY_BUT_REAL_VALUES, None]:  # tries every falsy value, plus the real "not given" case
    via_or = resolve_with_or(value)  # what the buggy pattern produces
    via_is_none = resolve_with_is_none(value)  # what the correct pattern produces
    should_differ = value is not None  # only a REAL, explicitly-passed value should differ from DEFAULT
    print(f"{value!r:<14}{via_or!r:<20}{via_is_none!r:<22}{should_differ}")

print()
print("The row for None is the ONLY row where both columns should say the same thing --")
print("every OTHER row is a real, deliberately-passed value that `or` wrongly replaced anyway.")
```
Real output:
```
value passed  or-based result     is None-based result  should differ from DEFAULT?
''            'fallback-value'    ''                    True
0             'fallback-value'    0                     True
False         'fallback-value'    False                 True
[]            'fallback-value'    []                    True
'   '         '   '               '   '                 True
None          'fallback-value'    'fallback-value'      False

The row for None is the ONLY row where both columns should say the same thing --
every OTHER row is a real, deliberately-passed value that `or` wrongly replaced anyway.
```
Every row except `None` is a real, deliberately-passed value — and the `or`-based column gets
FOUR of the five wrong, silently substituting the default for a genuine answer.

### The exact code I wrote (`src/orchestration/llm.py`)
```python
# src/orchestration/llm.py, lines 67-72, verbatim
 67 |     def __init__(self, client: LLMClient, *, model: str | None = None) -> None:
 68 |         resolved_model = settings.llm_model if model is None else model  # 🎯 the fix: is None, checking ONLY for "never given"
 69 |         # reads the configured default at construction time, never at import time;
 70 |         # "is None", not "or" -- an explicitly-passed "" must still be rejected below,  # 🎯 the comment records exactly what would have gone wrong, and why
 71 |         # not silently replaced by the default the way "" or settings.llm_model would
 72 |         _check_text("model", resolved_model)
```
**🎯 Interview-relevant lines:** line 68 is the entire fix, and line 70's own comment is the exact
sentence a strong interview answer would say out loud.

### Canonical docs
- Python docs, [Truth value testing](https://docs.python.org/3/library/stdtypes.html#truth-value-testing)
  — the complete, official list of what counts as falsy in Python.
- [PEP 8: Programming Recommendations](https://peps.python.org/pep-0008/#programming-recommendations)
  — recommends `is`/`is not` for comparisons to singletons like `None`, for exactly this reason.

### Micro-exercises
1. In `d2`, add `float("nan")` to `FALSY_BUT_REAL_VALUES`. Predict: is `nan` falsy in Python? Run
   it and check both columns' results.
2. Write a THIRD resolver, `resolve_with_get(value)`, using `dict.get`-style thinking: treat only
   the SENTINEL object `...` (Python's `Ellipsis`) as "not given," so even `None` itself could be
   passed through as a real value. When might you want a default resolver that treats `None` as a
   genuine, valid input rather than "missing"?
3. Find the OTHER two places in `src/orchestration/llm.py` that validate an argument
   (`_check_temperature`, `_check_max_tokens`). Do either of THEM use `or` anywhere? Why did they
   never have this specific bug to begin with?
4. Search `src/retrieval/hybrid_retriever.py` for its own `is None, not or` comment (mentioned in
   this concept's story). Read the surrounding code. Is it solving the exact same problem, or a
   related-but-different one?
5. Deliberately reintroduce `model or settings.llm_model` into a scratch copy of
   `src/orchestration/llm.py` and run `pytest tests/orchestration/ -k blank_model`. Read the
   failure. Does the test's failure message make the ACTUAL bug obvious, or would you need to
   already know what to look for?

**How it works internally.** Python's `or` operator does NOT return `True`/`False` the way you
might expect from other languages — it evaluates its left operand, and if that value is "truthy"
(passes `bool(value) == True`), it returns THAT VALUE unchanged; only if the left operand is
falsy does it evaluate and return the right operand. `is None` instead calls no conversion at
all — it checks, at the identity level, whether `value` is literally the one singleton `None`
object that exists anywhere in a running Python program, which is exactly why it can never be
fooled by an unrelated falsy value.

### How to validate this yourself
Run `python -c "print(bool(0), bool(''), bool([]), bool(False), bool('   '))"` yourself and read
which ones print `False` (all except the whitespace string) — that's the complete, exact list of
values `or` will silently treat as "missing." Then run
`tests/orchestration/test_llm.py::test_a_blank_model_name_is_rejected_at_construction` and read
its two parametrized cases (`""` and `"   "`) — only `""` would have been silently swallowed by
the buggy version; `"   "` was never the bug (it's truthy), which is exactly why the mutation
check (see the consolidated exercise) targets the `or` line specifically, not the validator.

### How to visualize this
Draw a decision diamond labelled "value is falsy?" feeding the OLD, buggy path — every falsy
value, including real ones, funnels into "use default." Draw a SECOND diamond labelled "value is
literally None?" feeding the FIXED path — only the true "nothing was passed" case funnels into
"use default"; every other value, falsy or not, flows straight through untouched.

**You should now be able to explain:** exactly which values `or` silently swallows that `is None`
would not, and why "it works for the common case" isn't the same claim as "it's correct."

---

## C4 — A Protocol matching an SDK's own overloaded method

### Do this first
Run [`d3_protocol_overload.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d3_protocol_overload.py).
**Predict first:** `client.ask("hello", many=True)` is used in a `for r in several` loop
afterward. If the Protocol only ever declared ONE shape for `ask` (say, always returning a single
`Reply`), would that loop still type-check correctly?

### 1. Definition + analogy
**What it is.** `@overload` lets one function name describe SEVERAL different call shapes, each
with its OWN accurate return type, when a single signature couldn't honestly describe all of
them. `LLMClient.generate` needs exactly this: called with `stream=False`, it returns ONE
`GenerateResponse`; called with `stream=True`, it returns an ITERATOR of them — genuinely
different types depending on one argument's value, which a single, non-overloaded signature
cannot express truthfully.

**Analogy.** A vending machine with one button that, pressed once, gives you a single candy bar —
but held down, dispenses a whole handful. A sign describing only "press for one item" would be
lying about what happens when you hold it down.

### For a 14-year-old
Picture a vending machine with a special button. Tap it once, quickly, and exactly ONE candy bar
drops out. But hold that SAME button down, and instead a whole HANDFUL of different candies comes
tumbling out — not one, several, all different from each other. If the machine only had a sign
saying "press button, get ONE candy bar," that sign would be straight-up WRONG about what happens
when you hold it down — and worse, it might trick you into reaching in expecting just one thing
and being confused (or missing candies) when a pile shows up instead.

A GOOD sign on this machine would actually say TWO separate things: "tap it → one candy bar
comes out" AND, separately, "hold it → a handful of different candies comes out." Two different
promises, for two different ways of using the SAME button, because the truth genuinely is
different depending on how you press it. `@overload` is exactly that second, honest, two-part
sign — one for calling `generate(..., stream=False)`, a completely separate one for calling
`generate(..., stream=True)`, because what actually comes back is genuinely different each time.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| tapping the button once | calling `generate(..., stream=False)` |
| one candy bar coming out | one `ollama.GenerateResponse` coming back |
| holding the button down | calling `generate(..., stream=True)` |
| a whole handful of different candies | an `Iterator[ollama.GenerateResponse]` |
| a sign only describing the "tap" behaviour | a Protocol declaring only ONE, non-overloaded `generate` signature |
| the two-part, honest sign | `LLMClient`'s two `@overload`-decorated `generate` methods |

### 2. Why here specifically
`LocalLLM` genuinely calls the underlying client BOTH ways — `generate()` calls with
`stream=False`, `stream()` calls with `stream=True` — and expects a DIFFERENT return type back
each time (one object to read `.response` from, versus something to loop over). The real
`ollama.Client.generate` is itself `@overload`-decorated for exactly this reason (P6-M1's own
docs/28 C5 found this for a single-overload case; this file needs BOTH shapes, since unlike
`health_check`, `LocalLLM` genuinely streams).

### What breaks if this changes
- **Only declare the `stream=False` overload** (verified for real, restoring this file
  afterward): `mypy` immediately reports THREE real errors at the `stream=True` call site,
  including a genuinely useful one — treating the return value as iterable when the (now, wrongly
  narrowed) type says it's a single object, not a list. See the real captured output below.
- **Declare one, un-overloaded signature returning `ollama.GenerateResponse |
  Iterator[ollama.GenerateResponse]`** (a union, instead of two overloads): technically not
  wrong, but every caller would then need its own `isinstance`/type-narrowing check to use the
  result safely — the two-overload version lets `mypy` pick the RIGHT type automatically, based
  on the literal `stream=` value at each specific call site.
- **Forget the `= False` default on the `stream: Literal[False]` overload:** every existing call
  to `generate(...)` that relies on the implicit default would now need an explicit `stream=False`
  everywhere, or mypy would refuse to infer which overload applies.

### [Interview relevance: medium-high — `@overload` comes up specifically when reviewing typed Python codebases or designing an SDK's own public API, and understanding it signals real depth with Python's type system, not just familiarity with basic type hints]

**Interview question:** *"You have one method that returns a different type depending on a
boolean flag argument. How do you type it correctly?"*
**A strong answer covers:** a single, non-overloaded signature would have to use a union type for
the return, forcing every caller to narrow it themselves; `@overload` lets you declare one
signature per meaningfully different call shape, each with its OWN accurate return type, so
`mypy` infers the exact type at each call site from the literal argument value; the LAST,
un-decorated definition is what actually runs at runtime (the `@overload` stubs are purely for the
type checker and carry no real code); and naming `Literal[True]`/`Literal[False]` specifically
(not plain `bool`) as the discriminant type each overload dispatches on.

### 3. Minimal code that explains the concept
The complete script, [`d3_protocol_overload.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d3_protocol_overload.py) (run from the project root):
```python
# Minimal demo of C4: a Protocol method needs ONE @overload per SHAPE the real code
# actually calls it with -- generate(..., stream=False) and generate(..., stream=True)
# return DIFFERENT types, so ONE signature cannot honestly describe both.
from typing import Literal, Protocol, overload  # the exact tools LLMClient itself uses


class Reply:  # stands in for ollama.GenerateResponse
    def __init__(self, text):
        self.text = text  # the one field this toy reply carries


class TwoShapeClient(Protocol):  # the REAL project's approach: one overload per shape
    @overload
    def ask(self, q: str, *, many: Literal[False] = False) -> Reply: ...  # ONE reply back

    @overload
    def ask(self, q: str, *, many: Literal[True]) -> list[Reply]: ...  # MANY replies back


class RealClient:  # a concrete class that actually satisfies TwoShapeClient
    def ask(self, q, *, many=False):
        if many:  # covers the "give me several" shape
            return [Reply(q.upper()), Reply(q.lower())]  # returns a LIST, matching many=True's promise
        return Reply(q.title())  # returns ONE Reply, matching many=False's promise


def use_client(client: TwoShapeClient) -> None:
    single = client.ask("hello", many=False)  # mypy expects a Reply here, and gets one
    print("many=False ->", type(single).__name__, single.text)  # .text works: single IS a Reply
    several = client.ask("hello", many=True)  # mypy expects a list[Reply] here, and gets one
    print("many=True  ->", type(several).__name__, [r.text for r in several])  # needs a loop: it's a LIST


use_client(RealClient())  # RealClient never mentions TwoShapeClient, yet satisfies it structurally

# Try this by hand: delete the SECOND @overload above (the many=True one) and re-run mypy on
# this file. It will now complain at the `client.ask("hello", many=True)` call site, because
# the Protocol never told it that shape returns a list -- exactly the gap LLMClient would have
# if it only declared stream=False and something tried calling stream=True through it.
```
Real output:
```
many=False -> Reply Hello
many=True  -> list ['HELLO', 'hello']
```
And the real, complete `mypy` output with the SECOND overload deliberately deleted (captured by
actually deleting it and re-running the type checker, then restoring the file):
```
practice codes\phase 6\m2_llm_wrapper_deep_dive\d3_protocol_overload.py:13: error: Single overload definition, multiple required  [misc]
practice codes\phase 6\m2_llm_wrapper_deep_dive\d3_protocol_overload.py:28: error: Argument "many" to "ask" of "TwoShapeClient" has incompatible type "Literal[True]"; expected "Literal[False]"  [arg-type]
practice codes\phase 6\m2_llm_wrapper_deep_dive\d3_protocol_overload.py:29: error: "Reply" has no attribute "__iter__" (not iterable)  [attr-defined]
```
The THIRD error is the most telling one: with only one overload, mypy genuinely believes
`client.ask("hello", many=True)` returns a single `Reply`, and correctly flags that you cannot
loop over one — exactly the class of real bug this pattern exists to catch before runtime.

### The exact code I wrote (`src/orchestration/llm.py`)
```python
# src/orchestration/llm.py, lines 21-36, verbatim
 21 | class LLMClient(Protocol):
 22 |     """The part of `ollama.Client` LocalLLM uses, so tests can pass a fake."""
 23 | 
 24 |     @overload
 25 |     def generate(
 26 |         self, model: str, prompt: str, *, options: Mapping[str, Any], stream: Literal[False] = False  # 🎯 first shape: stream defaults to False
 27 |     ) -> ollama.GenerateResponse: ...  # 🎯 ...returns ONE response
 28 | 
 29 |     # the non-streaming shape: one call in, one complete answer back
 30 | 
 31 |     @overload
 32 |     def generate(
 33 |         self, model: str, prompt: str, *, options: Mapping[str, Any], stream: Literal[True]  # 🎯 second shape: stream is REQUIRED True here
 34 |     ) -> Iterator[ollama.GenerateResponse]: ...  # 🎯 ...returns an ITERATOR
 35 | 
 36 |     # the streaming shape: one call in, a whole sequence of fragments back
```
**🎯 Interview-relevant lines:** lines 26 and 33 differ in exactly two places — the `Literal`
value and the return type — and that's the whole mechanism. Nothing else about `@overload` is
more complicated than "one block per honestly-different shape."

### Canonical docs
- Python docs, [`typing.overload`](https://docs.python.org/3/library/typing.html#typing.overload)
- Python docs, [`typing.Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)
- [docs/28 C5](28%20ollama_llm_setup_deep_dive.md) — the earlier, single-overload version of this
  exact lesson, found while building P6-M1.

### Micro-exercises
1. In `d3`, add a THIRD overload for `many` accepting an `int` (return exactly that many
   `Reply`s). Update `RealClient.ask` to actually support it. Does `mypy` accept three overloads
   as readily as two?
2. Swap the ORDER of the two `@overload` blocks (put `many=True` first). Run `mypy`. Does order
   matter here? *Hint: it does for some overload ambiguity cases — check whether this is one.*
3. Remove `@overload` from just the FIRST definition (keep it on the second only). What error do
   you get, and does its wording help you understand the rule "you need `@overload` on ALL
   variants, or none"?
4. Add a call `client.ask("hi")` (no `many=` argument at all) to `d3`. Which overload does it
   resolve to, and why?
5. In your own words (no looking it up), explain why the LAST definition in an `@overload` group
   — the one WITHOUT the decorator, if you were writing a real implementation rather than a
   Protocol — is the only one Python actually executes at runtime.

**How it works internally.** At runtime, `@overload`-decorated function bodies literally never
run — the real `typing.overload` decorator replaces each one with a stub that raises
`NotImplementedError` if somehow called directly, and Python's normal name-binding rules mean only
the LAST definition with that name (the real, undecorated implementation, in an ordinary class) is
what actually gets called. Inside a `Protocol`, there IS no real implementation at all — every
variant stays a stub (`...`), because a Protocol never runs any code; it exists purely for `mypy`
to check against.

### How to validate this yourself
Run `mypy src/orchestration/llm.py` — clean today. Then, in a scratch copy, delete the SECOND
`@overload` block from `LLMClient` (the `stream: Literal[True]` one) and re-run `mypy` — you
should see it complain specifically at the `stream=True` call site inside `stream()`, the same
shape of error `d3`'s own experiment reproduced above.

### How to visualize this
Draw `generate` as a single door with TWO different signs hanging above it, side by side: "IN:
stream=False → OUT: one GenerateResponse" and "IN: stream=True → OUT: an Iterator of
GenerateResponse." Both signs belong to the same door; which one applies depends entirely on what
you carry IN.

**You should now be able to explain:** why one method needed two type signatures instead of one,
and what specifically goes wrong (not just "an error happens," but WHICH error, and why) if only
one of the two is declared.

---

## C5 — Vocabulary mapping across an interface boundary

### Do this first
Run [`d4_parameter_mapping.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d4_parameter_mapping.py).
**Predict first:** if the mapping table used to translate `max_tokens` into Ollama's own name is
missing that one entry, what happens when you call the (broken) translator — a crash, a clear
error, or something quieter?

### 1. Definition + analogy
**What it is.** `LocalLLM`'s own vocabulary (`max_tokens`, chosen by the task's spec) is not the
same word Ollama's API uses for the same idea (`num_predict`). Wrapping a library often means
maintaining a small, explicit translation between your interface's names and the underlying
system's names — and if a translation is ever missing, most APIs don't complain; they just
silently ignore whatever key they don't recognize.

**Analogy.** Ordering food in a country where you don't speak the language, using a phrasebook.
If your phrasebook is missing the word for "no onions," the waiter doesn't refuse your order or
warn you — your dish just arrives with onions in it, because your request for "no onions" was
never actually communicated at all.

### For a 14-year-old
Imagine you're traveling in a country where you don't speak the language, and you're using a
paper phrasebook to order food. You want to say "no onions, please," so you flip to that phrase in
your book... except it's missing. Your phrasebook writer simply forgot to include it. So what
happens? You don't get an error message. Nobody stops you and says "sorry, that phrase isn't in
here." You just... don't say anything about onions at all, because there was never a real word for
it to translate INTO. Your food arrives, onions and all, and you might not even realize why —
you THOUGHT you asked for no onions, but your request never actually reached the kitchen in a
language they understood.

That's exactly the risk with translating `max_tokens` into Ollama's `num_predict`: if that one
line of translation were ever deleted or misspelled, nothing would crash. Ollama's own API would
simply see a request with no recognizable length limit in it, and generate however much it wants
— your "please stop after N tokens" request silently never arrived, the same way "no onions"
never reached the kitchen.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| your own language's phrase, "no onions" | `max_tokens`, the name `LocalLLM.generate`'s own signature uses |
| the kitchen's language's real word for it | `num_predict`, Ollama's own name for the same idea |
| the phrasebook's translation entry | `options={"temperature": temperature, "num_predict": max_tokens}` |
| a missing entry in the phrasebook | that dict simply never including a `"num_predict"` key |
| the waiter not complaining, just leaving it out | Ollama's `options` accepting unknown keys silently, and ignoring missing ones |
| the food arriving with onions anyway | a generation call running with NO length limit at all, unnoticed |

### 2. Why here specifically
The task's spec names the parameter `max_tokens`, matching common LLM-API vocabulary (OpenAI's
own API uses this exact name). Ollama, underneath, calls the same idea `num_predict`. Someone has
to bridge that naming gap, and it has to happen inside `LocalLLM` specifically — that's the one
place both vocabularies are in scope at once (the task's own interface on one side, `ollama`'s
real API on the other).

### What breaks if this changes
- **This project's own real risk, demonstrated (not yet happened) in `d4`:** delete or misspell
  `"num_predict"` in the `options` dict, and `max_tokens` silently has NO effect — Ollama neither
  errors nor warns, it just generates without any length limit. A test
  (`test_generate_sends_temperature_and_max_tokens_mapped_to_ollamas_own_names`) exists
  specifically to catch this by asserting the EXACT dict sent, not just that SOME options were
  sent.
- **Pass `max_tokens` through unchanged, as `"max_tokens"`, assuming Ollama understands the same
  word:** it wouldn't error either — it would be silently ignored, the identical failure mode as
  a missing mapping, just caused by never having translated in the first place.
- **Map it to the wrong Ollama field** (say, `"num_ctx"`, which controls context window size, a
  DIFFERENT concept): no crash, no obvious symptom — just consistently wrong behavior that could
  take a long time to notice, because the call still "works."

### [Interview relevance: medium — naming/vocabulary mismatches at integration boundaries are a common, underrated source of silent production bugs, and knowing that most JSON-based APIs ignore unknown fields rather than rejecting them is a genuinely useful, specific fact]

**Interview question:** *"You're integrating with a third-party API whose field names don't match
your own domain vocabulary. What's a common, easy-to-miss failure mode, and how do you guard
against it?"*
**A strong answer covers:** many APIs (REST/JSON especially) silently accept and ignore unknown
fields rather than rejecting the request, so a missing or misspelled translation produces NO
error at all, just quietly wrong behavior; the fix isn't just "write the mapping," it's "test
that the EXACT translated payload matches what you expect," not merely that a call succeeded; and
naming that this is exactly the kind of bug that's invisible in development (things "work") and
only shows up as a mysterious behavioral difference much later.

### 3. Minimal code that explains the concept
The complete script, [`d4_parameter_mapping.py`](../practice%20codes/phase%206/m2_llm_wrapper_deep_dive/d4_parameter_mapping.py) (run from the project root):
```python
# Minimal demo of C5: translating between two different vocabularies for the SAME concept
# -- and what happens when a translation is simply forgotten (it is silently ignored, not
# an error), exactly like max_tokens -> num_predict in the real code.
OUR_REQUEST = {"max_tokens": 50, "temperature": 0.2}  # the vocabulary LocalLLM's own callers use
OLLAMA_NAMES = {"max_tokens": "num_predict", "temperature": "temperature"}  # the REAL translation table


def translate_correctly(request, name_map):
    return {name_map[key]: value for key, value in request.items()}  # renames every key it knows about


def translate_with_a_forgotten_mapping(request, name_map):
    return {name_map.get(key, key): value for key, value in request.items() if key in name_map}
    # BUT the caller below passes an INCOMPLETE name_map on purpose, to show what happens


print("correct translation:", translate_correctly(OUR_REQUEST, OLLAMA_NAMES))
# every key ends up under Ollama's own real name -- {'num_predict': 50, 'temperature': 0.2}

BROKEN_NAMES = {"temperature": "temperature"}  # "max_tokens" was simply forgotten from this table
sent_to_ollama = translate_with_a_forgotten_mapping(OUR_REQUEST, BROKEN_NAMES)
print("with a forgotten mapping:", sent_to_ollama)
print("max_tokens=50 silently VANISHED -- Ollama never even sees it, and raises NO error at all")
print("(a real API rejecting an unknown field would be safer than one that just ignores it)")
```
Real output:
```
correct translation: {'num_predict': 50, 'temperature': 0.2}
with a forgotten mapping: {'temperature': 0.2}
max_tokens=50 silently VANISHED -- Ollama never even sees it, and raises NO error at all
(a real API rejecting an unknown field would be safer than one that just ignores it)
```

### The exact code I wrote (`src/orchestration/llm.py`)
```python
# src/orchestration/llm.py, lines 88-96, verbatim
 88 |         response = self._client.generate(
 89 |             model=self._model,
 90 |             prompt=prompt,
 91 |             options={"temperature": temperature, "num_predict": max_tokens},  # 🎯 the ENTIRE translation, both keys, in one dict literal
 92 |             stream=False,
 93 |         )
 94 |         # asks the model for one complete, real answer
 95 |         return response.response or ""
 96 |         # returns the generated text, or an empty string if that field was ever missing
```
```python
# src/orchestration/llm.py, lines 105-110, verbatim
105 |         chunks = self._client.generate(
106 |             model=self._model,
107 |             prompt=prompt,
108 |             options={"temperature": temperature},  # 🎯 notice: no num_predict key at all here -- stream() takes no max_tokens (D3)
109 |             stream=True,
110 |         )
```
**🎯 Interview-relevant lines:** line 91 versus line 108 is worth reading side by side — the
SAME `options` dict pattern, but deliberately carrying a different set of keys, because
`generate()` and `stream()` were given deliberately different parameter lists by the task's own
spec (see docs/28's sibling project's D3 decision).

### Canonical docs
- [Ollama API reference](https://github.com/ollama/ollama/blob/main/docs/api.md) — the full,
  real list of `options` fields Ollama recognizes (and silently ignores anything outside it).
- [OpenAI API reference: max_tokens](https://platform.openai.com/docs/api-reference/completions/create#completions-create-max_tokens) —
  the vocabulary the task's own spec is implicitly drawing from.

### Micro-exercises
1. In `d4`, add `"top_p"` to `OUR_REQUEST` but NOT to `OLLAMA_NAMES`. Run
   `translate_with_a_forgotten_mapping`. Does it crash, or silently drop it? *Hint: you already
   know the answer for `max_tokens` — is `top_p` any different?*
2. Fix `BROKEN_NAMES` to include `max_tokens` again, but map it to the WRONG name on purpose
   (`"num_ctx"` instead of `"num_predict"`). What would this look like in a live system — a
   crash, or a request that "succeeds" while doing the wrong thing?
3. Open `src/orchestration/llm.py` and find BOTH `options={...}` dict literals (lines 91 and
   108). Write out, in plain English, every key each one sends and why `stream()`'s is shorter.
4. Read `tests/orchestration/test_llm.py`'s
   `test_generate_sends_temperature_and_max_tokens_mapped_to_ollamas_own_names`. What EXACT dict
   does it assert against? Would it have caught a "mapped to the wrong name" bug (exercise 2),
   or only a "mapping missing entirely" bug?
5. Look up Ollama's full `options` reference. Name TWO more fields (besides `temperature` and
   `num_predict`) that `LocalLLM` does not currently expose at all. Would exposing them need new
   parameters on `generate`/`stream`, or could they have sensible hardcoded defaults instead?

**How it works internally.** `self._client.generate(..., options={...})` ultimately serializes
that dict straight into a JSON request body Ollama's HTTP server reads. JSON objects have no
required schema enforcement by default — Ollama's own server code looks up specific keys it
expects (`temperature`, `num_predict`, and so on) and simply never looks at, or complains about,
any key it doesn't recognize; anything genuinely missing just keeps that setting at Ollama's own
internal default.

### How to validate this yourself
With the real, running Ollama: call `llm.generate("Count to 100.", temperature=0.0, max_tokens=5)`
for real and count the actual words in the reply — it should stop very close to 5 tokens' worth
of output, proving `num_predict` really is reaching the server and taking effect, not just being
constructed correctly on your side. Then, in a scratch copy, delete the `"num_predict"` mapping
and run the SAME call — the reply should come back noticeably longer, since nothing is limiting
it anymore.

### How to visualize this
Draw two columns side by side: "LocalLLM's vocabulary" (`max_tokens`, `temperature`) and
"Ollama's vocabulary" (`num_predict`, `temperature`). Draw an arrow from `max_tokens` to
`num_predict` labelled "translated." Draw `temperature` connecting straight across with NO
relabeling — a reminder that not every concept needs translating, only the ones whose names
actually differ.

**You should now be able to explain:** why `max_tokens` and `num_predict` are the same idea with
different names, what happens (and — critically — what does NOT happen) if that translation is
ever broken, and why testing the exact translated payload matters more than testing "a call
succeeded."

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Predict, then trace.** Before running anything, write down what you expect
   `llm.stream("Count from 1 to 5.", temperature=0.0)` to print if you do
   `for fragment in llm.stream(...): print(fragment, end="", flush=True)` — one smooth sentence
   appearing word by word, or something else?
2. **Watch it work, layer by layer.** In one terminal, run `docker logs -f advancerag-ollama-1`.
   In another, run a short script calling `llm.stream(...)` and printing each fragment with a
   timestamp. Watch Ollama's own log line for `/api/generate` appear, and compare its timing to
   your printed fragments.
3. **Break C1 on purpose.** In a scratch copy, change `stream()`'s `for chunk in chunks: ...`
   loop to `return list(chunks)` instead of yielding (making it a normal function again, no
   generator). Run the real hermetic test file. Which test's assertion (about `list(llm.stream
   (...))` still working) still happens to pass, and which one (about laziness) would need to be
   rewritten to even test the right thing anymore?
4. **Break C2 on purpose.** Add one line at the very top of `tests/orchestration/test_llm.py`:
   `import ollama; ollama.Client.__init__ = None`. Run the hermetic tests. Do they still pass?
   What does that prove about whether they actually depend on the real library?
5. **Break C3 on purpose (safely).** In a scratch copy, change line 68 back to
   `resolved_model = model or settings.llm_model`. Run
   `pytest tests/orchestration/test_llm.py -k blank_model`. Read the failure. Does it point you
   at the actual line that's wrong?
6. **Break C4 on purpose.** In a scratch copy of `src/orchestration/llm.py`, delete the SECOND
   `@overload` from `LLMClient`. Run `mypy src/orchestration/llm.py`. Read every error. Which one
   is about `stream()`'s own `for chunk in chunks:` loop specifically?
7. **Break C5 on purpose.** Change `"num_predict": max_tokens` to `"max_tokens": max_tokens` (the
   WRONG, untranslated name) in a scratch copy. Run ONLY the hermetic tests
   (`-m "not live"`) first — one should fail immediately, because it checks the exact `options`
   dict sent, not just that a call happened. Now imagine that specific test had never been
   written: would the OTHER hermetic tests have caught this? Would a live test have?
8. **Put it back together.** Undo every change from steps 3-7, re-run
   `pytest tests/orchestration/ -v -p no:cacheprovider`, and confirm all 35 tests pass again.

If your predictions in steps 1, 3, 6 and 7 matched what actually happened, you understand not
just that this file works, but exactly which line is responsible for each of its five real
decisions.
