# Chunker Deep Dive: Reverse-Engineering `src/ingestion/chunker.py`

This is a self-contained learning path for Phase 2, Microstep 2:
[`src/ingestion/chunker.py`](../src/ingestion/chunker.py), validated against your real
[`tests/ingestion/test_chunker.py`](../tests/ingestion/test_chunker.py) and — where it
matters — against the ACTUAL LangChain library source code installed in your `.venv`, not
just its docs. Written for someone who has never seen this code before.

**How to use this doc:** for every concept, actually run the exercise — type the code into
a REPL, look at real output — before reading the checkpoint. Nobody learns to ride a bike by
reading about bikes. Five exercises per concept, ordered easiest → hardest.

---

## 0. What this file does, in plain English

Imagine you're handed a 50-page book (one document from `data/`) and told: "cut this into
index cards, one idea per card, so someone can find the right card later without reading
the whole book." That's chunking. This file is the machine that does the cutting.

The **dumb** way to cut it: every 500 characters, chop, no matter what — even if that slices
a sentence clean in half, mid-word. The way THIS file does it: read sentence by sentence,
and notice **when the topic changes**. Cut there instead. That's called **semantic
chunking** — "semantic" just means "about meaning."

How does a computer "notice a topic change"? It turns every sentence into a list of numbers
(called an **embedding** — think of it as GPS coordinates, but in a space with hundreds of
dimensions instead of 2). Sentences about similar things get coordinates close together;
sentences about different things land far apart. The code measures the "distance" between
each pair of neighboring sentences, and cuts wherever that distance is unusually large —
i.e., wherever the topic visibly jumped.

**The concept map** (read top to bottom — later ideas build on earlier ones):

```
Talking to an unknown "thing":       Protocol (a checklist of abilities, not a specific class)
Getting that "thing" handed to you:  Dependency Injection (don't build it yourself, receive it)
Settings as one bundle:              ChunkerConfig (a labeled box of dials, not loose variables)
A dropdown instead of free text:     Literal (only 4 legal spellings allowed)
The actual "find the topic change":  SemanticChunker — sentences → embeddings → distance → cut
Remembering WHERE a cut came from:   start_char / end_char offset tracking
Throwing away tiny leftover scraps:  merging undersized chunks backwards into the previous one
Noticing (not panicking) if a
  chunk got too big:                 the soft-cap warning pattern
Not building the tool until
  someone actually needs it:         factory function + lazy import
Doing this for MANY documents:       chunk_all — a loop that keeps everything in order
```

---

## 1. Concept-by-concept

---

### 1.1 `Protocol` — a checklist of abilities, not a family tree

**1. Definition (plain English):** a `Protocol` is a checklist of abilities, not a family
tree. Normally, to say "this thing must be an `Embedder`," you'd make `Embedder` a parent
class and force everything to inherit from it — like a door that only opens for your
literal cousins. A `Protocol` instead says "you can come in if you can do X and Y" — it
never asks what family you're from, only what you can DO. Imagine a "Can Open This Door"
sign that just says "must be able to insert a key and turn it" — it doesn't care if it's a
Yale key, a Schlage key, or a homemade one. ANY object with a working `turn()` method gets
in. That's Python checking "does this thing quack like a duck," formalized with real
type-checking instead of just hoping for the best.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
way to say "this parameter must support embeddings" is inheritance: make `Embedder` a real
base class, and require every implementation — including your test doubles — to extend it.
That drags a big, possibly network-capable library class into your test code just so a fake
object is "allowed." A `Protocol` solves this by checking SHAPE instead of ANCESTRY: any
object with the right methods qualifies, inheritance or not. `Chunker` needs "something
that can turn text into a list of numbers" — that could be real Ollama, real Cohere, or a
completely fake, instant, offline stand-in — and `Protocol` lets it accept all three without
caring which.

**3. A clean, minimal code example:**
```python
from typing import Protocol

class CanBark(Protocol):
    def bark(self) -> str: ...

class Dog:                       # never mentions CanBark anywhere
    def bark(self) -> str:
        return "Woof!"

class Robot:                     # also never mentions CanBark
    def bark(self) -> str:
        return "BEEP-WOOF"

def make_it_bark(thing: CanBark) -> None:
    print(thing.bark())

make_it_bark(Dog())     # works
make_it_bark(Robot())   # ALSO works — no shared parent class at all
```
This is exactly the pattern `chunker.py` uses: `class FakeEmbedder:` in the test file has
no parent class in parentheses at all, yet it satisfies `Embedder`.

**4. Main benefits, in short:** (a) test doubles need zero inheritance from real/heavy
library classes; (b) any future embedder provider (Cohere, a new local model) works
automatically as long as it has the right two methods, no registration needed; (c) `mypy`
still catches a missing method at type-check time, so you don't lose safety by dropping
inheritance.

**What breaks if removed:** if `Embedder` were a real class you had to inherit from (say,
LangChain's own `Embeddings` base class), your test fake would either need to inherit from a
big, heavy LangChain class (dragging network-capable code into your tests) or you'd need to
fight `mypy`'s type-checker, which would complain "FakeEmbedder is not an Embeddings" even
though it behaves identically.

**Canonical reading:** [Python docs — typing.Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol), and [PEP 544 (the proposal that added this to Python)](https://peps.python.org/pep-0544/).

**5 micro-exercises (absolute beginner, do them in order):**

1. **(5 min) See a Protocol accept something unrelated.** Type this into a Python REPL:
   ```python
   from typing import Protocol

   class CanBark(Protocol):
       def bark(self) -> str: ...

   class Dog:
       def bark(self) -> str:
           return "Woof!"

   class Robot:  # NOT a Dog, no relation at all
       def bark(self) -> str:
           return "BEEP-WOOF"

   def make_it_bark(thing: CanBark) -> None:
       print(thing.bark())

   make_it_bark(Dog())
   make_it_bark(Robot())
   ```
   *Hint if stuck:* just copy-paste it exactly, run it, see both print successfully.
   **What you should see:** both lines print fine — `Robot` was never told it's a `CanBark`,
   it just happens to have the right shape.

2. **(5 min) Break it on purpose.** Remove the `bark` method from `Robot` entirely (delete
   those two lines) and call `make_it_bark(Robot())` again — but FIRST predict: will this
   crash immediately when Python reads the function definition, or only when you actually
   call `make_it_bark(Robot())`?
   *Hint:* Python's Protocol checking mostly happens via a separate tool (`mypy`), not at
   the moment you run the code — try running `mypy` on a scratch file with this bug and
   compare that to just running `python scratch.py` directly.

3. **(10 min) Check `FakeEmbedder` really satisfies `Embedder`.** In the project's venv:
   ```python
   from src.ingestion.chunker import Embedder
   from tests.ingestion.test_chunker import FakeEmbedder
   import typing
   print(isinstance(FakeEmbedder(), Embedder))  # runtime check — does this even work?
   ```
   *Hint:* Protocols marked `@runtime_checkable` support `isinstance()`; this one isn't
   decorated that way — read the actual error/result you get and figure out why.

4. **(10 min) Write your own two-method Protocol from scratch.** Make a `Protocol` called
   `CanGreet` requiring a `hello(name: str) -> str` method. Write TWO unrelated classes that
   both satisfy it (e.g. `Robot` and `Parrot`), and a function `greet_everyone(thing:
   CanGreet)` that calls it. Run `mypy` on your scratch file to confirm no type errors.

5. **(10 min) Find the exact two methods `Chunker` actually calls.** Open
   `chunker.py` and search for every place `self._embedder` is used. It's used exactly
   once, indirectly — inside `SemanticChunker` (the library class), not directly in our
   code. Use `grep -n "embed" .venv/Lib/site-packages/langchain_experimental/text_splitter.py`
   to find which of the two `Embedder` methods (`embed_documents` or `embed_query`) the
   library ACTUALLY calls. Is `embed_query` ever used by this pipeline at all?

**How it works internally:** at RUN time (when your code actually executes), a `Protocol` is
almost invisible — Python doesn't check anything, it just calls `.embed_documents(...)` and
trusts it exists. All the "does this shape match" checking happens separately, at TYPE-CHECK
time, when you run `mypy` — which reads your code without running it and reasons: "does
`FakeEmbedder` have a method named `embed_documents` that takes a `list[str]` and returns a
`list[list[float]]`? Yes? Then it satisfies `Embedder`." If `mypy` is never run, a
Protocol violation would only surface as a real crash (`AttributeError: no attribute
'embed_documents'`) the moment the missing method actually gets called.

**Checkpoint — you should now be able to explain, in your own words:** why `Chunker`
doesn't need to know or care whether it's talking to Ollama, Cohere, or a test fake, and at
what POINT in time (writing code vs running `mypy` vs running the program) a Protocol
mismatch would actually be caught.

---

### 1.2 Dependency Injection — don't build your own tools, get them handed to you

**1. Definition (plain English):** instead of a class creating the things it needs INSIDE
itself, those things are created OUTSIDE and handed to it ("injected") through the
constructor. Imagine you're told to bake a cake. Option A: you grow your own wheat, mill
your own flour, raise your own chickens for eggs. Option B: someone hands you a box with
flour and eggs already in it, and you just bake. Option B is dependency injection — the
`Chunker` doesn't grow its own embedder, someone hands it one.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional,
simpler-looking approach is for a class to just build what it needs internally —
`self._embedder = OllamaEmbeddings(...)` hardcoded inside `__init__`. That looks convenient
until you try to TEST it: every single test would now need a real, running Ollama server —
slow, needs Docker/network, and non-deterministic (a real model's output can shift between
runs). Injection solves this by making the embedder a PARAMETER instead of an internal
detail: `Chunker.__init__(self, embedder: Embedder, ...)`. Now a test can hand it a
`FakeEmbedder` — instant, offline, deterministic — with zero change to `Chunker` itself.

**3. A clean, minimal code example:**
```python
class CarBad:
    def __init__(self):
        self.engine = "V8 engine built right here"   # builds its OWN engine

class CarGood:
    def __init__(self, engine):
        self.engine = engine                          # RECEIVES an engine

print(CarBad().engine)
print(CarGood("electric motor").engine)
print(CarGood("fake engine for testing").engine)      # swap freely, no code changes
```
Every test in `tests/ingestion/test_chunker.py` does exactly this:
`Chunker(FakeEmbedder(), ...)` — the fake is handed in, never built by `Chunker` itself.

**4. Main benefits, in short:** (a) tests run instantly and offline, no Docker/Ollama
required; (b) swapping providers (Ollama today, Cohere later) needs zero changes inside
`Chunker`; (c) `Chunker`'s own logic can be reasoned about and tested completely separately
from any specific embedding provider's behavior.

**What breaks if removed:** delete the `embedder` parameter and hardcode
`OllamaEmbeddings(...)` inside `Chunker.__init__` — now `import chunker` itself might try
to reach a network address, `tests/ingestion/test_chunker.py`'s 13 tests would either need
Docker+Ollama running just to execute, or fail outright, and CI (`.github/workflows/ci.yml`)
would need Ollama installed on the GitHub Actions runner — a much heavier, slower, flakier
pipeline.

**Canonical reading:** [Martin Fowler — Inversion of Control Containers and the Dependency Injection pattern](https://martinfowler.com/articles/injection.html) (the original, most-cited explanation of this exact idea).

**5 micro-exercises:**

1. **(5 min) See the difference with your own eyes.** Write two tiny classes:
   ```python
   class CarBad:
       def __init__(self):
           self.engine = "V8 engine built right here"  # builds its OWN engine

   class CarGood:
       def __init__(self, engine):
           self.engine = engine  # RECEIVES an engine

   print(CarBad().engine)
   print(CarGood("electric motor").engine)
   print(CarGood("V8 for testing").engine)  # swap it freely!
   ```
   Notice: `CarGood` can be handed ANY engine string; `CarBad` is stuck with one forever.

2. **(10 min) Find where injection happens in `chunker.py`.** Open the file and find the
   ONE line where `embedder` gets stored (`self._embedder = embedder`). Then find the
   SEPARATE function at the bottom of the file, `build_ollama_embedder`, that actually
   CREATES a real embedder. Answer: why are these two things kept in two completely
   different places instead of one?

3. **(10 min) Break it, on purpose, to feel the pain.** In a scratch copy of `chunker.py`,
   hardcode `self._embedder = build_ollama_embedder("http://localhost:11434")` directly
   inside `Chunker.__init__`, deleting the `embedder` parameter. Now try running
   `pytest tests/ingestion/test_chunker.py` WITHOUT Docker/Ollama running. Read the exact
   error. (Then undo your change — don't leave the real file broken.)

4. **(10 min) Inject a SECOND kind of fake to prove the pattern is general.** Write a
   `BrokenEmbedder` whose `embed_documents` raises an exception. Construct
   `Chunker(BrokenEmbedder())` and call `.chunk(...)` on some document — confirm the
   exception propagates up cleanly, proving `Chunker` has zero special-case logic tied to
   any one specific embedder implementation.

5. **(15 min) Find a SECOND example of injection already in this codebase.** Look at
   `src/ingestion/schemas.py`'s `make_chunk` function — is anything injected there, or is
   everything computed internally? Now look at `Chunker.chunk()`'s `data_root: Path`
   parameter — is that a form of injection too (a path being handed in rather than assumed/
   hardcoded)? Write one sentence justifying your answer either way.

**How it works internally:** there's no special Python feature for dependency injection —
it's just... passing an argument to a function. That's the whole trick. The "pattern" part
is a DISCIPLINE: consistently choosing to receive collaborators as parameters instead of
constructing them inside, specifically so the pieces can be swapped, tested, and reasoned
about independently. Python has no compiler-enforced concept of "injected" vs "not injected"
— it's a design choice a human makes, visible only by reading `__init__` signatures.

**Checkpoint:** explain why `Chunker` importing `langchain_ollama` INSIDE
`build_ollama_embedder` (not at the top of the file) matters for keeping tests fast — tie
your answer to what you observed in exercise 3.

---

### 1.3 `ChunkerConfig` — a labeled box of dials, not five loose variables

**1. Definition (plain English):** a labeled box of dials, instead of five loose variables
scattered around. Imagine a video game's difficulty settings. You COULD pass five separate
numbers into every function that needs them (`enemy_health=100, enemy_speed=5, ...`,
repeated everywhere) — messy, and easy to mix up the order. OR you make one
`DifficultySettings` object, fill it in once, and hand THAT around instead.
`ChunkerConfig` is the second option, bundling `breakpoint_threshold_type`,
`breakpoint_threshold_amount`, `target_chunk_chars`, `min_chunk_chars`, and `buffer_size`
into one object. (It uses the same `@dataclass(frozen=True, slots=True)` mechanism as
`schemas.py`'s `DocumentChunk` — for how `frozen`/`slots` work internally, see
[docs/schemas_deep_dive.md](schemas_deep_dive.md) sections 1.2–1.3; this section is about
WHY a config deserves its own class at all.)

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach — five separate function parameters — makes it easy to accidentally mix up
argument order, and impossible to hold "two different settings" side by side without five
extra variable names for each. Bundling them into `ChunkerConfig` fixes both: arguments are
matched by NAME not position, and the whole docstring is built around being able to create
`ChunkerConfig` MULTIPLE times with different values, side by side, specifically so you can
compare them — "an experiment can hold several configs side by side and diff their output on
the same corpus" (see exercise 1 in section 3, Validation, below).

**3. A clean, minimal code example:**
```python
from src.ingestion.chunker import ChunkerConfig

aggressive = ChunkerConfig(breakpoint_threshold_amount=0.5)
conservative = ChunkerConfig(breakpoint_threshold_amount=3.0)

print(aggressive)      # every field printed at once, auto-generated by @dataclass
print(aggressive == conservative)   # False -- compares field by field
```

**4. Main benefits, in short:** (a) related settings travel together as one named object
instead of five loose variables that could drift out of sync; (b) two configurations can be
held side by side and compared, which is how chunking actually gets tuned in practice; (c)
being `frozen` means a config can't be silently mutated mid-experiment, so a comparison
between "config A" and "config B" is always trustworthy.

**What breaks if removed:** `Chunker.__init__` would need five separate parameters
(`breakpoint_threshold_type=..., breakpoint_threshold_amount=..., target_chunk_chars=...`
etc.) instead of one `config=...` — every CALL SITE constructing a `Chunker` would need to
repeat all five, and comparing "config A vs config B" would mean juggling ten loose
variables instead of two tidy objects.

**Canonical reading:** [Python docs — dataclasses](https://docs.python.org/3/library/dataclasses.html) (same reference as `schemas.py`'s deep dive).

**5 micro-exercises:**

1. **(5 min) Build the default config and read every field.**
   ```python
   from src.ingestion.chunker import ChunkerConfig
   c = ChunkerConfig()
   print(c)
   print(c.breakpoint_threshold_amount, c.target_chunk_chars, c.min_chunk_chars)
   ```
   *Hint:* just run it — `print(c)` shows every field at once because `@dataclass` auto-
   generates a readable `__repr__` (you learned this exact mechanism in the schemas deep
   dive, section 1.1).

2. **(5 min) Make TWO configs and compare them side by side.**
   ```python
   aggressive = ChunkerConfig(breakpoint_threshold_amount=0.5)
   conservative = ChunkerConfig(breakpoint_threshold_amount=3.0)
   print(aggressive == conservative)  # predict True or False before running
   ```

3. **(10 min) Try to mutate a config field and observe what stops you.** Run
   `aggressive.breakpoint_threshold_amount = 99` and read the exact exception. Which earlier
   concept (from the schemas deep dive) is this?

4. **(10 min) Prove `Chunker` actually USES your config, not just stores it.** Build two
   `Chunker`s with different `min_chunk_chars` (one very low like `1`, one very high like
   `10_000`), run BOTH against the same short document, and compare `len(chunks)` for each
   — confirm the high-`min_chunk_chars` version merges everything into fewer chunks.

5. **(15 min) Add a brand-new config field yourself.** Add a field called
   `max_chunks_per_document: int | None = None` to `ChunkerConfig` (default `None` = no
   limit). Don't wire it into `Chunker` yet — just add the field, rerun
   `pytest tests/ingestion/test_chunker.py`, and confirm all 13 existing tests still pass
   (a new field with a default shouldn't break anything that doesn't reference it — verify
   that claim rather than assume it).

**How it works internally:** `ChunkerConfig()` with no arguments works because every field
has a `=` default value right in the class body — Python's `@dataclass` decorator reads
those and builds a constructor where every parameter is optional, falling back to the
default. When you write `ChunkerConfig(breakpoint_threshold_amount=0.5)`, Python matches
your keyword argument to that ONE field by name and leaves every other field at its default.

**Checkpoint:** explain why `Chunker.__init__` does `self._config = config or ChunkerConfig()`
instead of just `self._config = config` — what would happen if someone called
`Chunker(embedder)` with no config argument at all, and this line were written the simpler,
shorter way instead?

---

### 1.4 `Literal` — a dropdown menu instead of a blank text box

**1. Definition (plain English):** a dropdown menu, instead of a blank text box. A normal
`str` type hint means "any text at all is allowed." `Literal["a", "b", "c"]` means "ONLY
these exact strings, nothing else." Imagine a form asking "what's your favorite color?"
with a plain text box — someone might type "Red", "red", "REDD" (typo), or "cheese"
(nonsense). Now imagine the SAME form as a dropdown with exactly four choices. You
physically cannot submit "REDD" or "cheese" — the dropdown won't let you. `Literal` is that
dropdown, for your code instead of a web form.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach — a plain `str` type hint — accepts ANY string, so `mypy` has nothing to check a
value against; a typo like `"standard_devation"` (missing an "i") sails through
type-checking with zero complaints. `chunker.py`'s `SemanticChunker` (verified by reading
its actual installed source, `.venv/Lib/site-packages/langchain_experimental/text_splitter.py`,
line 88) only accepts four EXACT spellings — anything else raises a `ValueError` INSIDE the
library, at chunking time, potentially deep into a long-running ingestion job.
`BreakpointThresholdType = Literal["percentile", "standard_deviation", "interquartile",
"gradient"]` moves that same typo-catching to `mypy` time, BEFORE ingestion ever runs.

**3. A clean, minimal code example:**
```python
from typing import Literal

Direction = Literal["up", "down", "left", "right"]

def move(direction: Direction) -> None:
    print(f"moving {direction}")

move("up")         # fine
move("sideways")   # plain python won't complain, but `mypy move.py` WILL
```

**4. Main benefits, in short:** (a) a typo becomes a `mypy` error caught in seconds, not a
`ValueError` discovered mid-ingestion; (b) your code editor autocompletes the legal values,
so you never have to remember or look up the exact spellings; (c) the type hint itself
documents "these are the only four options" without needing a separate comment.

**What breaks if removed:** change `breakpoint_threshold_type: BreakpointThresholdType` back
to plain `breakpoint_threshold_type: str` — `ChunkerConfig(breakpoint_threshold_type=
"standard_devation")` (typo'd) would pass `mypy` with zero complaints, run for a while, and
only fail deep inside the library's `_calculate_breakpoint_threshold` method — a much later,
much less helpful failure point. (This is the exact bug that was caught and fixed in the
previous session — see `mypy`'s error in `notes/step-2.2-semantic-chunker.md`.)

**Canonical reading:** [Python docs — typing.Literal](https://docs.python.org/3/library/typing.html#typing.Literal), and [PEP 586](https://peps.python.org/pep-0586/).

**5 micro-exercises:**

1. **(5 min) See it accept the right value.**
   ```python
   from typing import Literal
   Direction = Literal["up", "down", "left", "right"]
   def move(direction: Direction) -> None:
       print(f"moving {direction}")
   move("up")  # fine
   ```

2. **(10 min) See `mypy` (not Python itself) catch a bad value.** Add
   `move("sideways")` to the scratch file above. Run it with plain `python scratch.py` first
   — does it crash? THEN run `mypy scratch.py` — does IT catch it? Write one sentence on
   the difference between these two tools' jobs.

3. **(10 min) Find the real bug this exact pattern already caught in this project.** Look
   at the git history / `notes/step-2.2-semantic-chunker.md` — find the sentence describing
   the `mypy` error about `breakpoint_threshold_type` having "incompatible type str." Type
   the ORIGINAL, broken version (`breakpoint_threshold_type: str = "standard_deviation"`)
   back into a scratch copy and reproduce that exact `mypy` error yourself.

4. **(10 min) Make your own typo and watch `Literal` save you.** In a scratch file, use
   `BreakpointThresholdType` from `chunker.py`, write
   `bad: BreakpointThresholdType = "percentle"` (missing an "i"), and run `mypy` on it —
   read the exact error message it produces, including which 4 values it says ARE allowed.

5. **(15 min) Extend the Literal and see the ripple effect.** Temporarily add a 5th fake
   value, `"my_custom_method"`, to `BreakpointThresholdType` in a scratch copy of the file.
   Does `mypy` complain? Now actually try USING that value in a real `Chunker` — where does
   it fail, and why does adding it to the type hint NOT make the library actually support it
   (what's the relationship — or lack of one — between a type hint and real behavior)?

**How it works internally:** exactly like `Protocol` (section 1.1), `Literal` does
**nothing at all while your program is actually running** — Python doesn't check it at
runtime. It exists purely for `mypy` (and your code editor's autocomplete) to read. If you
never run `mypy`, a `Literal`-typed field will happily accept ANY string at runtime with no
complaint whatsoever — the "protection" only exists at the type-checking step, which is why
this project's CI pipeline (Step 1.2) runs `mypy` on every push.

**Checkpoint:** explain, using your own words, the difference between "this code will crash
if run" and "this code will crash mypy if type-checked" — and why `Literal` only gives you
the second kind of protection, not the first.

---

### 1.5 The actual algorithm: sentences → embeddings → distance → cut

**1. Definition (plain English):** this is the core trick of the whole file. Break text into
sentences. Turn each sentence into a list of numbers (an embedding). Measure how "far apart"
each pair of neighboring sentences is. Cut wherever that distance is unusually large. Picture
a giant room with hundreds of dimensions (pretend it's 3D). Every sentence gets dropped into
this room at some exact spot, based on what it MEANS — sentences about dogs land near other
dog sentences, sentences about pizza land far away from the dogs. Walking through the
document sentence by sentence, most steps are short hops (still the same topic); every so
often there's a HUGE jump (the topic changed). Those big jumps are exactly where we cut.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional,
"dumb" way to chunk is to cut every N characters, no matter what — even mid-sentence,
mid-word. That guarantees clean, predictable sizes, but produces chunks that start or end in
gibberish, which embeds and retrieves poorly. Semantic chunking solves this by cutting based
on MEANING instead of a fixed count: a chunk boundary only ever lands where the topic
actually shifts, so every chunk is internally coherent, at the cost of chunk sizes that vary
document to document.

**3. A clean, minimal code example** (cosine similarity — the actual "distance" measurement,
computed by hand on two tiny vectors so you can see the raw math, not a black box):
```python
import numpy as np

a = np.array([1, 0])   # a "topic A" sentence
b = np.array([1, 0])   # another "topic A" sentence -- same direction
c = np.array([0, 1])   # a "topic B" sentence -- perpendicular, unrelated

def cos_sim(x, y):
    return np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y))

print(cos_sim(a, b))   # close to 1: very similar
print(cos_sim(a, c))   # close to 0: very different
```
`chunker.py` does this same computation, but on real embedding vectors with hundreds of
dimensions instead of 2, once per pair of neighboring sentences.

**4. Main benefits, in short:** (a) chunk boundaries respect actual meaning, never slicing
mid-idea the way fixed-size cutting does; (b) each chunk embeds more cleanly because it's
about one coherent topic; (c) `standard_deviation` thresholding (this project's choice)
additionally means a genuinely uniform document isn't force-cut into arbitrary pieces just
to satisfy a size target.

**Canonical reading:** [Pinecone — Chunking Strategies for LLM Applications](https://www.pinecone.io/learn/chunking-strategies/); [Greg Kamradt's original notebook this implementation is copied from](https://github.com/FullStackRetrieval-com/RetrievalTutorials/blob/main/tutorials/LevelsOfTextSplitting/5_Levels_Of_Text_Splitting.ipynb) (the library's own docstring credits this exact source — verified in the file you read above); [3Blue1Brown's visual intro to embeddings/vectors](https://www.3blue1brown.com/lessons/vectors) if the "meaning-room" idea feels shaky.

**5 micro-exercises:**

1. **(5 min) Compute cosine similarity BY HAND on two tiny vectors.**
   ```python
   import numpy as np
   a = np.array([1, 0])
   b = np.array([1, 0])       # identical direction
   c = np.array([0, 1])       # perpendicular / unrelated
   def cos_sim(x, y):
       return np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y))
   print(cos_sim(a, b))  # predict before running: close to 1 or close to 0?
   print(cos_sim(a, c))
   ```

2. **(10 min) Watch the `FakeEmbedder` produce DIFFERENT vectors for different topics.**
   ```python
   from tests.ingestion.test_chunker import FakeEmbedder
   e = FakeEmbedder()
   print(e.embed_query("I love alpha things"))
   print(e.embed_query("I love beta things"))
   print(e.embed_query("nothing special here"))
   ```
   *Hint:* read `FakeEmbedder`'s source in `test_chunker.py` first — it's deliberately
   simple (keyword-matching, not a real model) so you can predict its output before running.

3. **(10 min) Compute the REAL cosine distance the library would compute, using the fake
   embedder, for two "same topic" sentences vs two "different topic" sentences.**
   ```python
   from src.ingestion.chunker import Embedder  # just for reference
   from tests.ingestion.test_chunker import FakeEmbedder
   import numpy as np
   e = FakeEmbedder()
   v1 = e.embed_query("Alpha one thing.")
   v2 = e.embed_query("Alpha two thing.")
   v3 = e.embed_query("Beta something totally different.")
   def cos_dist(x, y):
       x, y = np.array(x), np.array(y)
       return 1 - np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y))
   print("same topic distance:", cos_dist(v1, v2))
   print("different topic distance:", cos_dist(v1, v3))
   ```
   Confirm the "different topic" number is bigger.

4. **(15 min) Try the DEFAULT config first — and be surprised (verified, not hypothetical).**
   Use `Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1))` (default
   `breakpoint_threshold_amount=1.5`) on the text: `"Alpha one. Alpha two. Alpha three.
   Beta one. Beta two."` — print `len(chunks)`. Before running, predict: will this cut at
   the obvious Alpha/Beta topic switch? **Run it — you should get exactly 1 chunk, no cut
   at all.** This is real, verified behavior, not a bug in the exercise. Now explain why:
   print the raw distances yourself —
   ```python
   from langchain_experimental.text_splitter import SemanticChunker
   s = SemanticChunker(embeddings=FakeEmbedder(), breakpoint_threshold_type="standard_deviation",
                        breakpoint_threshold_amount=1.5, buffer_size=1)
   sentences = s._get_single_sentences_list("Alpha one. Alpha two. Alpha three. Beta one. Beta two.")
   distances, _ = s._calculate_sentence_distances(sentences)
   print(distances)
   ```
   — and compare the largest distance against `mean + 1.5*stdev` of those same distances.
   *Hint:* `buffer_size=1` means EVERY sentence gets blended with its immediate neighbors
   BEFORE embedding (see `combine_sentences` in the library source) — so the sentence right
   at the topic boundary ends up partly "Alpha-flavored" and partly "Beta-flavored," making
   the jump look smaller than you'd expect from reading the raw text.

5. **(15 min) Lower the threshold until a cut actually appears, and notice WHERE it lands.**
   Rerun exercise 4's text with `breakpoint_threshold_amount=0.5`, then `0.3`, then `0.1`.
   You should get **3 chunks**, not 2, at every one of these thresholds:
   `["Alpha one. Alpha two.", "Alpha three. Beta one.", "Beta two."]` — note that "Alpha
   three." and "Beta one." end up in the SAME chunk, straddling the real topic boundary,
   not split cleanly at it. Tie this back to exercise 4's explanation: `buffer_size`
   smoothing means the sentence immediately at a topic switch is the HARDEST one to cut
   correctly, precisely because it's been blended with sentences from both sides.

**How it works internally** (verified line-by-line against the actual installed library
source, `.venv/Lib/site-packages/langchain_experimental/text_splitter.py`, not guessed and
not taken from LangChain's docs, which can drift from what a specific pinned version
actually does — a lesson from Step 1.1 about not trusting anything you haven't checked):

1. `split_text()` breaks the whole document into sentences using a regex:
   `r"(?<=[.?!])\s+"` — "split right after a `.`, `?`, or `!`, wherever there's
   whitespace." (`buffer_size` from your `ChunkerConfig` then groups a few neighboring
   sentences together before the NEXT step, to smooth out noise from very short sentences.)
2. `embed_documents(...)` is called ONCE with ALL the combined sentences — this is exactly
   the method on YOUR `Embedder` that gets called; verified by exercise 5 in section 1.1.
3. `calculate_cosine_distances()` computes, for every pair of NEIGHBORING sentences, `1 -
   cosine_similarity(embedding_A, embedding_B)`. Cosine similarity ranges from -1 to 1 (1 =
   pointing the exact same direction, 0 = unrelated, -1 = opposite); subtracting from 1 turns
   "similarity" into "distance" (0 = identical meaning, up to 2 = totally opposite meaning).
4. With `breakpoint_threshold_type="standard_deviation"` (this project's choice), the code
   computes `mean(all distances) + 1.5 * standard_deviation(all distances)` — any distance
   ABOVE that number counts as a real topic-jump; everything below is normal variation. With
   `"percentile"` instead, the code computes `np.percentile(distances, threshold_amount)` —
   "always cut at roughly the top X% of biggest jumps IN THIS DOCUMENT," even in a perfectly
   uniform document where nothing is truly unusual. That's the concrete difference verified
   by reading `_calculate_breakpoint_threshold` in the library source.
5. It slices the sentence list at every one of those jump-points and joins each slice back
   into one chunk of text.

**Checkpoint:** explain, in your own words, what a "cosine distance of 0" would mean for two
sentences, and what a "cosine distance of 2" would mean — then explain why
`breakpoint_threshold_amount` being SMALLER makes the chunker cut MORE often, not less
(this trips people up — make sure your explanation actually holds up against exercise 5's
results).

---

### 1.6 `start_char` / `end_char` — bookmarking exactly where a chunk came from

**1. Definition (plain English):** every chunk remembers the exact character position (like
a page and line number) where it started and ended in the ORIGINAL document — so later, a UI
could highlight "this is the exact sentence the AI's answer came from." Imagine highlighting
a paragraph in a physical book with a yellow marker, then writing a sticky note: "this
highlight starts at word 47, ends at word 112" — so anyone can find that exact highlight
again later, even in a photocopy. `start_char`/`end_char` are those sticky notes.

**2. The "Why" — what problem this solves vs. the traditional approach:** without offset
tracking, a `DocumentChunk` only knows WHAT text it contains, not WHERE that text lived in
the source document. That's fine for embedding and retrieval, but useless for a UI trying to
show "here's the exact source passage this answer came from" — you'd have to re-search the
whole document for a substring match, which can be ambiguous if similar text appears twice.
`SemanticChunker(..., add_start_index=True)` turns position tracking ON (verified in the
library source, line 122: `self._add_start_index = add_start_index`, only recorded when
`True`); `Chunker.chunk()` reads that value and passes it into
`make_chunk(..., start_char=start, end_char=end)`, which stores it inside
`DocumentChunk.metadata` (per `decision.md`, D-2026-09-15-02).

**3. A clean, minimal code example:**
```python
from src.ingestion.chunker import Chunker, ChunkerConfig
from src.ingestion.loader import LoadedDocument
from tests.ingestion.test_chunker import FakeEmbedder
from pathlib import Path

text = "Alpha one. Alpha two."
doc = LoadedDocument(source_path="company/about.md", category="company", title="t",
                      format="md", text=text, char_count=len(text))
chunk = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1)).chunk(doc, Path("data"))[0]
print(chunk.metadata["start_char"], chunk.metadata["end_char"])
```

**4. Main benefits, in short:** (a) exact source-region highlighting in a UI — the span
covers the real source text, blank lines and all; (b) the ability to fetch "the text right
before/after this chunk" from the original document without re-searching for it; (c) a
chunk stays traceable to its exact origin even after `_merge_undersized` has combined it
with a neighbour. **One caveat that is expected behaviour, not a bug** — see the block
below: `source[start:end]` matches the chunk only after whitespace normalisation, never
byte-for-byte.

**⚠️ A REAL BUG THAT WAS FOUND HERE — and has since been FIXED (read this whole block):**
writing this doc surfaced a genuine bug: the offsets were **not exact past the first chunk**.
It has since been fixed in `Chunker._recover_spans` / `Chunker._locate` — see
`decision.md`, D-2026-09-15-14/15. The history below is kept because reproducing the
original failure is the best way to understand what the fix actually does.

**How to see the ORIGINAL bug** (works if you `git checkout` the commit before the fix):
```python
from src.ingestion.chunker import Chunker, ChunkerConfig
from src.ingestion.loader import LoadedDocument
from tests.ingestion.test_chunker import FakeEmbedder
from pathlib import Path

text = "Alpha topic sentence one.\n\nBeta topic sentence two, quite different now."
doc = LoadedDocument(source_path="company/about.md", category="company", title="t",
                      format="md", text=text, char_count=len(text))
chunker = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1))
chunks = chunker.chunk(doc, Path("data"))
for c in chunks:
    s, e = c.metadata["start_char"], c.metadata["end_char"]
    print("claimed slice:", repr(text[s:e]))
    print("actual chunk :", repr(c.text))
    print("MATCH:", text[s:e] == c.text)
```
Running this yourself will show `MATCH: False`. **Why:** the library's sentence-splitting
regex consumes whitespace (including blank lines between markdown paragraphs — extremely
common in your real `data/` corpus) and reassembles sentences by joining them back together
with a SINGLE space (`" ".join(...)`, verified in `split_text()`, line 257 of the library
source). If the original had a blank line (`\n\n`, 2 characters) between sentences, but the
rebuilt chunk only has one space (1 character) there, the reconstructed chunk's LENGTH no
longer matches the original text's length at that position — and the library tracks the
NEXT chunk's start position using `start_index += len(chunk)` (verified at line 289), which
silently drifts off course for every chunk after the first whitespace mismatch.

**How it was fixed, and the subtlety that made the OBVIOUS fix wrong:** the intuitive fix is
"don't trust the library's tracker — just search for the chunk text with `str.find`." That
was tried, and **it did not work**: because the rebuilt chunk has single spaces where the
source has `\n\n`, the chunk often isn't verbatim present in the source at all, so `find`
returns `-1` and falls straight back to the broken estimate. The working fix builds a regex
from the chunk's WORDS joined by `\s+`, so any whitespace difference is tolerated:

```python
words = [re.escape(word) for word in chunk_text.split()]
match = re.compile(r"\s+".join(words)).search(source_text, search_from)
```

**The second half of the fix is changing what "correct" means.** After this,
`source[start:end] == chunk.text` is still `False` — and that is now EXPECTED, not a bug:
the slice keeps the source's blank lines, the chunk text does not. The real correctness
criterion is word-level equality, which is what the tests assert:
`source[start:end].split() == chunk.text.split()`. The span now points at the true source
region (blank lines included), which is exactly what a UI highlight needs.

**Canonical reading:** there's no official doc describing this specific bug (it's not
documented anywhere in LangChain's own materials) — this finding comes from reading the
actual source code directly, which is itself the lesson: **read the library's source when
behavior matters, don't just trust the docstring.**

**5 micro-exercises:**

1. **(5 min) Confirm chunk 0's offset is always exactly right.** Using ANY document, print
   `chunks[0].metadata["start_char"]` — it should always be `0`. Explain in one sentence
   why the FIRST chunk can never be affected by the drift bug described above.

2. **(10 min) Run the blank-line case and confirm the fix holds.** Use the code block above
   on the current code — the span should now cover the FULL source region including the
   `\n\n`, `sliced.split() == chunk.text.split()` should be `True`, and no "Could not locate
   chunk verbatim" warning should appear. Then check `sliced == chunk.text` and confirm it's
   `False` — and be able to say why that's correct, not a bug.

3. **(10 min) Prove why the OBVIOUS fix fails.** In a scratch file, write the naive version:
   `source.find(chunk_text, 0)` for the blank-line example above. Confirm it returns `-1`.
   This is exactly why `_locate` falls back to a regex instead of stopping at `str.find` —
   an earlier attempt at this fix did stop there, and silently kept the bug.

4. **(15 min) Trigger the fallback path deliberately.** Feed the chunker a document where a
   chunk genuinely cannot be located (hint: you'd have to make the chunk text contain
   characters absent from the source — try monkey-patching `create_documents` to return a
   fabricated chunk). Confirm the "Could not locate chunk verbatim" warning fires and
   ingestion still completes rather than crashing.

5. **(15 min) Measure the cost of the regex path.** Time `_locate` on a long real document
   (`data/contracts/*.md`) for both branches: once where the exact `str.find` succeeds, once
   where it must fall through to the regex. Is the regex path meaningfully slower, and would
   it matter across all 76 documents?

**How it works internally:** covered in full above, verified against the actual installed
library source rather than assumed.

**Checkpoint:** explain, in your own words, WHY the drift only shows up starting from the
SECOND chunk onward, never the first — and why a document made entirely of single-spaced
sentences with no blank lines would never trigger this bug at all.

---

### 1.7 Merging undersized chunks backwards

**1. Definition (plain English):** if the LAST slice of a cut is too tiny to be useful on its
own (like a 20-character scrap), glue it onto the chunk right before it instead of leaving it
stranded alone. You're slicing a pizza. If the final slice is a paper-thin sliver, nobody
wants that as its own plate — you'd rather stick it onto the slice next to it so every plate
has a reasonable amount of food. `_merge_undersized` does exactly that for text chunks.

**2. The "Why" — what problem this solves vs. the traditional approach:** the "traditional"
(i.e. do-nothing) approach is to keep every semantic cut point as its own chunk, no matter
how small. Semantic chunking can produce a tiny trailing chunk if a topic-change happens to
fall right near the end of a document (e.g., the last sentence is a signature line: "—
Insurellm, Inc."). A 20-character chunk barely has any meaning to embed — retrieving it
later would be nearly useless noise. `_merge_undersized` solves this by folding a too-small
chunk backwards into its predecessor, keeping the content reachable without leaving a
near-empty chunk in the index.

**3. A clean, minimal code example** (the actual merge logic):
```python
def merge_undersized(spans, min_chunk_chars):
    merged = []
    for text, start, end in spans:
        if merged and len(text) < min_chunk_chars:
            prev_text, prev_start, _ = merged[-1]
            merged[-1] = (prev_text + " " + text, prev_start, end)   # glue onto predecessor
            continue
        merged.append((text, start, end))
    return merged

print(merge_undersized([("AB", 0, 2), ("C", 2, 3)], min_chunk_chars=5))
# [('AB C', 0, 3)] -- the tiny "C" chunk got absorbed
```
The `merged and` guard matters: **the very FIRST chunk is never merged away**, even if it's
tiny, because there's nothing before it to merge INTO — merging it would silently discard
content, which this project's whole design philosophy (see `loader.py`'s deep dive) treats
as worse than an imperfect chunk.

**4. Main benefits, in short:** (a) no near-empty, low-signal chunks end up in the vector
index; (b) content is never silently dropped, only combined; (c) the guard for the first
chunk means "there was nothing to merge into" never turns into "so we deleted it instead."

**What breaks if removed:** every semantic cut point gets kept as its own chunk no matter
how small — you'd occasionally get a `DocumentChunk` with `char_count=12`, e.g. just
`"Thank you."`, which embeds to something close to meaningless and can surface as a
context-free, useless retrieval hit later (this exact risk is documented in `decision.md`,
D-2026-09-15-07).

**A verified comparison worth knowing:** `SemanticChunker` itself has a BUILT-IN
`min_chunk_size` parameter — but read the library source again (section 1.5's code, lines
259–263): `if len(combined_text) < self.min_chunk_size: continue` — this **silently DROPS**
the undersized chunk entirely (skips appending it, no merge). That's why this project wrote
its OWN `_merge_undersized` instead of using the library's built-in option — merging
PRESERVES content, the library's own feature DELETES it. This is a real, deliberate design
choice, not an oversight — verify this claim yourself with exercise 4 below.

**Canonical reading:** no single canonical source for this exact micro-pattern — it's a
general data-cleaning technique ("handle edge-of-range remainders") that shows up across
many domains, from pagination logic to video encoding's last-frame handling.

**5 micro-exercises:**

1. **(5 min) Trigger a merge and count chunks before/after.**
   ```python
   from src.ingestion.chunker import Chunker, ChunkerConfig
   from tests.ingestion.test_chunker import FakeEmbedder, _doc
   from pathlib import Path
   text = "Alpha one. Alpha two. Beta one. Beta two. Gamma one. Gamma two."
   normal = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1))
   aggressive_merge = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=10_000))
   print(len(normal.chunk(_doc(text), Path("data"))))
   print(len(aggressive_merge.chunk(_doc(text), Path("data"))))  # predict: bigger or smaller?
   ```

2. **(10 min) Prove the FIRST chunk survives even when tiny.** Construct a document that
   starts with a very short first "sentence" (e.g. `"Hi. " + "Alpha topic filler text " *
   20 + "Beta topic filler text " * 20`) with `min_chunk_chars` set high enough that "Hi."
   alone would normally be merge-eligible. Confirm chunk 0 is still `"Hi."` on its own,
   proving the `merged and` guard works.

3. **(10 min) Read the `_merge_undersized` code and trace the OFFSET math by hand on
   paper first**, then verify with code. For two chunks `("AB", 0, 2)` and `("C", 2, 3)`
   with `min_chunk_chars=5` (forcing a merge), what should the merged tuple be? Write your
   predicted `(text, start, end)` down, THEN run:
   ```python
   chunker = Chunker(FakeEmbedder())
   result = chunker._merge_undersized([("AB", 0, 2), ("C", 2, 3)])
   print(result)
   ```

4. **(15 min) Prove the library's OWN `min_chunk_size` drops content (the claim above).**
   ```python
   from langchain_experimental.text_splitter import SemanticChunker
   from tests.ingestion.test_chunker import FakeEmbedder
   s = SemanticChunker(embeddings=FakeEmbedder(), breakpoint_threshold_type="standard_deviation",
                        breakpoint_threshold_amount=0.01, min_chunk_size=10_000)
   text = "Alpha one. Alpha two. Beta one. Beta two."
   chunks = s.split_text(text)
   print(chunks)
   print("total chars kept:", sum(len(c) for c in chunks), "vs original:", len(text))
   ```
   Confirm the total characters kept is LESS than the original — proving real content was
   silently thrown away, unlike our own `_merge_undersized`.

5. **(15 min) Write your own alternative: merge FORWARDS instead of backwards.** Sketch (in
   plain pseudocode or real code) a `_merge_undersized_forwards` that glues a too-small chunk
   onto the NEXT one instead of the previous one. What NEW edge case does this introduce
   that backwards-merging doesn't have (hint: think about what happens to the very LAST
   chunk in a document if it's tiny)?

**How it works internally:** a single pass through the list of `(text, start, end)` tuples,
keeping a running `merged` list. Each incoming chunk either gets appended fresh, or — if it's
both too small AND there's already something to merge into — gets glued onto whatever is
CURRENTLY LAST in `merged` (not the original neighbor, which matters if MULTIPLE tiny chunks
appear consecutively: the second tiny one merges into the ALREADY-merged first, not into
the original pre-merge chunk).

**Checkpoint:** explain what would happen if THREE tiny chunks appeared in a row (e.g. three
5-character fragments after a big chunk) — would they end up as one giant merged blob, or
three separate small merges? Trace through the code's logic to justify your answer, then
verify with a real test.

---

### 1.8 The soft-cap warning — noticing without panicking

**1. Definition (plain English):** when a chunk turns out bigger than the target size, the
code writes a note in the log (`logger.info(...)`) instead of crashing or forcibly cutting
the chunk in half. Imagine a gym coach watching you lift weights. If you lift MORE than
planned, a good coach writes it down in your training log ("nice, you went over today") —
they don't yank the barbell out of your hands mid-rep to force you back to the planned
weight. `_warn_oversized` is the coach who just writes it down.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach for a size limit is to ENFORCE it — hard-split anything over the target. But
semantic chunking's whole point is cutting based on MEANING, not a fixed size; forcibly
splitting an oversized chunk at exactly 500 characters would very likely land mid-sentence,
reintroducing the exact problem semantic chunking was chosen to avoid (see `decision.md`,
D-2026-09-15-08). Logging instead of enforcing keeps the size limit as a TUNING SIGNAL for a
human, not a hard rule the algorithm fights against.

**3. A clean, minimal code example:**
```python
import logging
logging.basicConfig(level=logging.INFO)

from src.ingestion.chunker import Chunker, ChunkerConfig
from tests.ingestion.test_chunker import FakeEmbedder, _doc
from pathlib import Path

c = Chunker(FakeEmbedder(), ChunkerConfig(target_chunk_chars=5, min_chunk_chars=1))
c.chunk(_doc("Alpha one. Alpha two. Beta one."), Path("data"))
# watch INFO log lines print live -- nothing crashes, nothing gets force-split
```

**4. Main benefits, in short:** (a) an oversized chunk never causes a crash mid-ingestion;
(b) the size limit stays informative (a tuning hint) rather than destructive (a forced,
meaning-blind cut); (c) a human reviewing logs can decide whether to lower the breakpoint
threshold, with full context, rather than the algorithm silently overriding itself.

**What breaks if changed to `raise` instead of `logger.info`:** in your OWN real test run
from the previous session (see `notes/step-2.2-semantic-chunker.md`), real chunks came out
at 1107 and 1143 characters against a 500-character target — MORE than double. If this were
a hard `raise ValueError(...)`, ingesting `data/company/about.md` would have CRASHED the
whole pipeline over something that isn't actually a bug, just a config that needs tuning.

**Canonical reading:** [Python docs — logging levels (INFO vs WARNING vs ERROR)](https://docs.python.org/3/howto/logging.html#logging-levels) — understanding WHY this uses `.info()` (a note-to-self level) rather than `.warning()` or `.error()` (more urgent levels) is worth reading directly.

**5 micro-exercises:**

1. **(5 min) See the log line fire.**
   ```python
   import logging
   logging.basicConfig(level=logging.INFO)
   from src.ingestion.chunker import Chunker, ChunkerConfig
   from tests.ingestion.test_chunker import FakeEmbedder, _doc
   from pathlib import Path
   c = Chunker(FakeEmbedder(), ChunkerConfig(target_chunk_chars=5, min_chunk_chars=1))
   c.chunk(_doc("Alpha one. Alpha two. Beta one."), Path("data"))
   ```
   *Hint:* with `target_chunk_chars=5` almost EVERY chunk will trigger the warning — watch
   the log lines print live.

2. **(5 min) Turn logging OFF and confirm nothing crashes.** Remove the
   `logging.basicConfig(...)` line and rerun — the chunking still works identically, just
   silently. Explain why "no visible log" and "no logging configured" are different from
   "chunking failed."

3. **(10 min) Read the exact log message format string** in `chunker.py`'s
   `_warn_oversized` and manually fill in the `%s`/`%d` placeholders by hand on paper for a
   chunk that's 800 characters against a 500-character target, source
   `"contracts/foo.md"`, at index `2`. Then verify your hand-written prediction against the
   REAL printed output.

4. **(10 min) Change `.info` to `.warning` and observe the visual difference.** Edit a
   scratch copy of `_warn_oversized` to use `logger.warning(...)` instead of
   `logger.info(...)`, rerun exercise 1, and compare the printed output — does the log line
   LOOK more urgent (check for a `WARNING:` prefix vs `INFO:`)? This is purely cosmetic/
   organizational — nothing else in the code changes behavior — but it changes how a
   monitoring system reading these logs later might prioritize them.

5. **(15 min) Build a tiny "chunk size report" using this pattern.** Write a short script
   that runs `chunk_all` across ALL documents in `data/company/` (4 files), and instead of
   just letting `_warn_oversized`'s logs fly by, COUNT how many total chunks exceeded
   `target_chunk_chars=500`, and print `"{oversized_count}/{total_count} chunks exceeded
   target"`. This is the seed of a real chunking-quality dashboard.

**How it works internally:** `logger.info(...)` doesn't print anything by default — Python's
`logging` module is silent until something CONFIGURES it (via `logging.basicConfig(...)` or
similar, as you did in exercise 1). This is the exact same lesson from `loader.py`'s deep
dive (section on `_read_text_file`'s Latin-1 fallback warning) — logging calls are cheap
and harmless to leave in code, but invisible until a human deliberately turns them on.

**Checkpoint:** explain why `_warn_oversized` was written to LOG rather than RAISE, tying
your answer specifically to what semantic chunking is trying to optimize for (meaning-based
cuts) versus what a hard size limit would force it to do instead.

---

### 1.9 Factory function + lazy import — don't build the car until someone orders one

**1. Definition (plain English):** `build_ollama_embedder(...)` is a small function whose
ONLY job is to construct and return a real, usable `Embedder` — and notice:
`from langchain_ollama import OllamaEmbeddings` is written INSIDE that function, not at the
top of the file. Imagine a car dealership's brochure. It describes cars in detail, but the
dealership doesn't actually BUILD a car until a customer walks in and orders one.
`build_ollama_embedder` is the "build me a car" button — it only runs, and only imports the
car-building blueprint (`langchain_ollama`), the moment someone actually calls it.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is to put every import at the top of the file, unconditionally. If
`from langchain_ollama import OllamaEmbeddings` lived at the top of `chunker.py`, then
`import src.ingestion.chunker` (which EVERY test file does, even ones that never touch
Ollama) would immediately load the `langchain_ollama` package regardless of whether it's
ever used. Moving the import inside the one function that actually needs it keeps the
module's "cheap to import" surface small, and clearly isolates the real dependency (a live
Ollama SERVER, reached only when `OllamaEmbeddings(...)` is actually CALLED, not merely
imported) to exactly one function — matching the file-level comment: "Isolated in a factory
so importing this module never opens a network connection."

**3. A clean, minimal code example:**
```python
def get_current_time():
    import datetime          # import lives INSIDE the function
    return datetime.datetime.now()

print(get_current_time())    # works fine -- datetime was never imported at module top
```
Same idea as `build_ollama_embedder`: the import only executes the moment the function is
actually called, not when the module is merely imported.

**4. Main benefits, in short:** (a) importing `chunker.py` never has a side effect of
loading a networked provider's client library unless you actually asked for one; (b) the
module stays testable and fast to import even when a specific provider's package is heavy or
occasionally flaky to install; (c) the exact one place a network-capable dependency enters
the picture is easy to find and audit.

**What breaks if the import moved to the top:** nothing crashes immediately (the package IS
installed, per `pyproject.toml`) — but the separation of "this needs LangChain-Ollama
installed" from "this needs LangChain-Ollama installed AND actually used" gets blurred, and
every future reader has to trace further to know which parts of the file are safe to use
completely offline.

**Canonical reading:** [Python docs — the import statement, and where it's legal to put one](https://docs.python.org/3/reference/simple_stmts.html#the-import-statement) (imports are legal ANYWHERE a statement is legal, including inside a function — not just at module top).

**5 micro-exercises:**

1. **(5 min) Prove imports work inside a function.**
   ```python
   def get_current_time():
       import datetime  # import INSIDE a function
       return datetime.datetime.now()
   print(get_current_time())
   ```
   Confirm this runs with no errors, despite `datetime` never being imported at the top.

2. **(10 min) Measure whether `import chunker` alone touches the network.** With no
   Ollama/Docker running at all, run:
   ```python
   import src.ingestion.chunker  # just the import, nothing else
   print("Import succeeded with no Ollama running!")
   ```
   Confirm this works. THEN try calling `src.ingestion.chunker.build_ollama_embedder(
   "http://localhost:11434")` — does the FUNCTION CALL itself fail immediately (since it
   just constructs an object), or only when you later try to actually USE the embedder to
   embed text?

3. **(10 min) Move the import to the top and see if any test breaks.** In a scratch copy
   of `chunker.py`, move `from langchain_ollama import OllamaEmbeddings` to the top of the
   file (alongside the other imports). Rerun `pytest tests/ingestion/test_chunker.py` —
   do the tests still pass? (They should — this exercise is about noticing WHAT DOESN'T
   change, which tells you the real protection here is about clean separation/discipline,
   not about test-breaking necessity, in this specific case where the package happens to
   already be installed.)

4. **(10 min) Simulate the package NOT being installed** to see when the real failure
   would occur. You can't easily uninstall it without breaking your venv, so instead:
   rename the factory function's import to a fake package name,
   `from totally_fake_package_xyz import Something`, in a scratch copy. Confirm `import
   chunker` alone (without calling the function) still works fine — the broken import is
   only discovered the moment `build_ollama_embedder` actually RUNS.

5. **(15 min) Write your own factory function for `FakeEmbedder`.** Add a
   `build_fake_embedder()` function to a scratch file, following the exact same pattern —
   should its import be lazy (inside the function) too? Why or why not, given that
   `FakeEmbedder` lives in your OWN test file with no heavy external dependency at all? Write
   one sentence justifying your answer — this is testing whether you understand WHY lazy
   imports exist (avoiding heavy/networked dependencies), not just mimicking the pattern
   everywhere blindly.

**How it works internally:** Python imports are executed the first time they're reached,
exactly like any other statement, and cached in `sys.modules` afterward (this exact
mechanism was covered in depth in
[docs/modulenotfound_debugging_guide.md](modulenotfound_debugging_guide.md), section on
Branch F) — an import statement sitting inside a function body simply doesn't RUN until
that function is called, because Python executes function bodies lazily, line by line, only
when invoked.

**Checkpoint:** explain the precise difference between "this package is installed" and
"this package has been imported" and "this package's remote server has been contacted" —
and identify which of those three things happens at which exact line/moment in
`build_ollama_embedder`.

---

### 1.10 `chunk_all` — many documents, one ordered list

**1. Definition (plain English):** a simple loop that calls `.chunk(...)` once per document
and glues all the results into one big list, keeping documents in the same order they came
in. Imagine an assembly line with several boxes of unsorted Lego bricks arriving one at a
time. `chunk_all` opens box 1, dumps its sorted pieces onto the conveyor belt, THEN opens
box 2 and dumps its pieces right after — never mixing pieces from box 2 in BEFORE finishing
box 1.

**2. The "Why" — what problem this solves vs. the traditional approach:** without
`chunk_all`, every caller that needs "all chunks from all documents" would have to write
`for doc in documents: chunks.extend(chunker.chunk(doc, data_root))` themselves — a small
loop, but one that would end up duplicated at every call site (the future embedding step, a
CLI ingestion script, a test), instead of living in exactly one place. Later pipeline steps
will want to process ALL 76 real documents' worth of chunks at once; `chunk_all` is that
single batch entry point.

**3. A clean, minimal code example:**
```python
from src.ingestion.chunker import Chunker, ChunkerConfig
from tests.ingestion.test_chunker import FakeEmbedder, _doc
from pathlib import Path

chunker = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1))
docs = [_doc("Alpha one. Alpha two.", "company/about.md"),
        _doc("Beta one. Beta two.", "company/careers.md")]

chunks = chunker.chunk_all(docs, Path("data"))
print([c.source_path for c in chunks])   # about.md's chunks always precede careers.md's
```

**4. Main benefits, in short:** (a) one place to call instead of a duplicated loop at every
call site; (b) guaranteed, simple ordering — documents in, chunks out, same relative order;
(c) a document that produces zero chunks (an empty one) doesn't break processing of the
next document in the list.

**What breaks if removed:** nothing FUNCTIONALLY breaks — every caller could write
`for doc in documents: chunks.extend(chunker.chunk(doc, data_root))` themselves. But that
loop would then be duplicated at every call site (the embedding step, a future CLI ingestion
script, a test), instead of living in exactly one place.

**Canonical reading:** no special canonical reference needed — this is plain Python list/
loop mechanics, the same as any `for` loop you've written before.

**5 micro-exercises:**

1. **(5 min) Run it on two tiny fake documents and confirm order.**
   ```python
   from src.ingestion.chunker import Chunker, ChunkerConfig
   from tests.ingestion.test_chunker import FakeEmbedder, _doc
   from pathlib import Path
   chunker = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1))
   docs = [_doc("Alpha one. Alpha two.", "company/about.md"),
           _doc("Beta one. Beta two.", "company/careers.md")]
   chunks = chunker.chunk_all(docs, Path("data"))
   print([c.source_path for c in chunks])
   ```
   Confirm every `about.md` chunk appears before every `careers.md` chunk.

2. **(10 min) Reverse the input order and confirm output order follows.** Swap the two
   documents in the `docs` list above, rerun, and confirm the printed source_path order
   flips too — proving `chunk_all` has no hidden re-sorting logic, it's pure pass-through
   order preservation.

3. **(10 min) Feed it an EMPTY list and see what happens.** Run
   `chunker.chunk_all([], Path("data"))` — predict the result before running (hint: think
   about what a `for` loop over an empty list does).

4. **(10 min) Feed it a document that produces ZERO chunks** (recall section on empty
   documents — `_doc("   ")`, all whitespace) mixed in with a normal document, and confirm
   `chunk_all` correctly skips the empty one's contribution while still processing the
   normal one — i.e., a document producing zero chunks doesn't break the loop for the next
   document.

5. **(15 min) Rewrite `chunk_all` using a list comprehension instead of a loop**, and prove
   it's equivalent:
   ```python
   def chunk_all_v2(self, documents, data_root):
       return [chunk for document in documents for chunk in self.chunk(document, data_root)]
   ```
   Run both versions against the same `docs` list and confirm
   `[c.chunk_id for c in v1] == [c.chunk_id for c in v2]`. Which version do YOU find easier
   to read — there's no wrong answer, but form your own opinion by comparing them side by
   side.

**How it works internally:** a plain `for` loop with `.extend(...)` (which appends every
item from one list onto another, one at a time) — there is no batching, parallelism, or
cleverness here at all. Each document is chunked completely independently and sequentially;
`Chunker` has no shared state between calls to `.chunk()` that could make order matter for
correctness (each call constructs a fresh `spans` list internally) — order is preserved
purely because a `for` loop naturally processes items one after another, not because of any
special safeguard.

**Checkpoint:** explain why `chunk_all` calling `self.chunk(document, data_root)` for EACH
document separately (rather than, say, batching all documents' text together into ONE big
call to the embedder) is or isn't a performance concern — tie your answer to what you
learned in section 1.5 about `embed_documents` being called once per `.chunk()` call.

---

## 2. Validating it's ACTUALLY working (not just "no errors")

Direct validation against your real project — run every one of these yourself.

| What to check | Command / snippet | What "working" looks like |
|---|---|---|
| Chunker imports with zero network access | `python -c "import src.ingestion.chunker"` with Docker/Ollama OFF | Succeeds instantly, no hang, no connection error |
| Fake-embedder tests are fast (offline signal) | `pytest tests/ingestion/test_chunker.py -v --durations=0` | All 13 tests complete in well under 1 second total — if any test takes multiple seconds, suspect it's accidentally hitting a real network call |
| Config actually changes behavior | Run the same document through `ChunkerConfig(breakpoint_threshold_amount=0.1)` vs `ChunkerConfig(breakpoint_threshold_amount=10.0)` | Chunk counts differ meaningfully (low threshold → many chunks, high threshold → few/one chunk) |
| chunk_id scheme matches the design | `chunks[i].chunk_id == f"{chunks[i].source_path}_{i}"` for every chunk of one document | True for every index — proves the positional ID scheme (decision.md D-2026-09-15-01) is wired correctly end-to-end |
| Offsets point at the true source region | Run the snippet from section 1.6 on a real file, comparing `document.text[s:e].split()` to `chunk.text.split()` | `True` for EVERY chunk (asserted by `test_offsets_are_whitespace_accurate_on_a_real_corpus_file`). Note byte-for-byte `==` is still `False` by design — see 1.6 |
| Offset recovery never silently falls back | Run any real document with `logging.basicConfig(level=logging.WARNING)` | No "Could not locate chunk verbatim" warning appears — if one does, offsets for that document are estimates, not real positions |
| Merging actually preserves content | `"".join(one_big_chunk[0].text.split()) == "".join("".join(c.text.split()) for c in many_small_chunks)` (see `test_merging_preserves_total_content`) | True — proves no text is silently lost during a merge, only whitespace differs |
| Real Ollama path works end-to-end | The exact script from `notes/step-2.2-semantic-chunker.md`'s verification run | Chunks produced, `char_count`/`approx_token_count` populated, offsets tile contiguously (`(0,1107)` then `(1107,2250)`, etc.) |
| Static checks clean | `ruff check src/ingestion tests/ingestion && ruff format --check src/ingestion tests/ingestion && mypy src/ingestion` | All three report clean |

**Failure-mode drills** (predict the outcome before running):

1. **Pass an embedder whose `embed_documents` returns THE SAME vector for every sentence**
   (a "broken" embedder that can't tell sentences apart at all) — does chunking still
   "succeed" (no exception), and what does that tell you about the DIFFERENCE between "ran
   without error" and "produced meaningful chunks"? (This is the whole point of this
   document's title.)
2. **Pass a document that's a single word repeated 1000 times, no punctuation at all** —
   since the sentence-split regex needs `.`, `?`, or `!` to find a boundary, what does
   `single_sentences_list` end up containing, and how many chunks come out?
3. **Set `breakpoint_threshold_amount` to a negative number** — does the library reject it,
   or does it just cut at EVERY possible boundary (since every distance would exceed a
   negative threshold)? Verify directly rather than guessing.

---

## 3. Visualizing it working

**Data flow, as this module actually implements it:**

```
LoadedDocument.text (one document's raw string, e.g. 2254 chars)
        │
        ▼
SemanticChunker.create_documents([text])
        │
        ├── split_text(): regex-split into sentences  (?<=[.?!])\s+
        ├── combine_sentences(): group neighbors per buffer_size
        ├── embedder.embed_documents([...]): text -> vectors  <-- YOUR Embedder, injected
        ├── calculate_cosine_distances(): 1 - cosine_similarity per neighbor pair
        ├── _calculate_breakpoint_threshold(): mean + 1.5*stdev (your config)
        └── slice at every distance ABOVE that threshold
        │
        ▼
list[Document] with .page_content (chunk text) and .metadata["start_index"]
        │
        ▼
Chunker.chunk(): recover (text, start, end) spans
        │
        ├── _merge_undersized(): fold tiny trailing chunks backwards
        ├── _warn_oversized(): log (don't enforce) anything over target_chunk_chars
        │
        ▼
enumerate(spans) -> make_chunk(chunk_index=i, start_char=s, end_char=e, ...)
        │        (schemas.py: chunk_id = f"{source_path}_{i}")
        ▼
list[DocumentChunk]  <-- ready for embedding + Qdrant upsert (not built yet)
```

**Tools worth using for deeper visibility:**
- **A plain Python REPL** — exactly as with `schemas.py`/`loader.py`, everything here is a
  callable function you can invoke directly and print the result of. No server, no
  container needed for the fake-embedder path.
- **`logging.basicConfig(level=logging.DEBUG)`** — turn this up before running `.chunk(...)`
  to see every `logger.info`/`logger.warning` call live (see section 1.8's exercises).
- **A tiny plotting script** — if you have `matplotlib` available, plot the raw cosine
  distances between consecutive sentences of a real document as a line chart, with a
  horizontal line at the computed threshold — you will SEE the "spikes" where cuts happen,
  turning an abstract number into an actual visual. (This is optional/stretch — not required
  to understand the core mechanics.)

**One end-to-end "watch it work" exercise, tying validation + visualization together:**

1. Open a Python REPL, turn logging on:
   `import logging; logging.basicConfig(level=logging.INFO)`.
2. Load a real document:
   ```python
   from pathlib import Path
   from src.ingestion.loader import load_document
   root = Path("data")
   doc = load_document(root / "company" / "culture.md", root)
   print(doc.char_count, "characters")
   ```
3. Build a chunker with the REAL Ollama embedder (requires `docker compose up -d ollama` and
   `docker exec advancerag-ollama-1 ollama pull nomic-embed-text` from the previous
   session — reuse it if still running):
   ```python
   from src.ingestion.chunker import Chunker, build_ollama_embedder
   embedder = build_ollama_embedder("http://localhost:11434")
   chunker = Chunker(embedder)
   ```
4. Run it and WATCH the log lines print live as it computes:
   ```python
   chunks = chunker.chunk(doc, root)
   print(f"{len(chunks)} chunks produced")
   ```
5. For EACH chunk, print `chunk_id`, `char_count`, and the FIRST 60 characters of `.text` —
   read them in order and confirm they genuinely read as separate "ideas," not arbitrary
   cuts.
6. Now verify offsets on THIS real document: for every chunk, check
   `doc.text[s:e].split() == chunk.text.split()` — all `True`. Then check byte-for-byte
   `doc.text[s:e] == chunk.text` and see `False`, and be able to explain why the second
   result is correct rather than a bug (section 1.6 covers exactly this).
7. Finally, change `ChunkerConfig(breakpoint_threshold_amount=0.5)` (much more aggressive),
   rerun steps 3–5, and compare the NEW chunk count and boundaries against your first run —
   you should see visibly more, smaller chunks, with different (probably more sensible, or
   possibly over-eager — judge for yourself) cut points.

If you can predict, before running each numbered step, roughly what will happen and why —
you've internalized this module for real, not just "the tests pass."
