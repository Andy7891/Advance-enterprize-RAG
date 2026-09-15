# Enrichment Deep Dive: Reverse-Engineering `src/ingestion/enrichment.py`

This is a self-contained learning path for Phase 2, Microstep 3:
[`src/ingestion/enrichment.py`](../src/ingestion/enrichment.py), plus the tiktoken-related
part of [`infra/docker/Dockerfile`](../infra/docker/Dockerfile), validated against
[`tests/ingestion/test_enrichment.py`](../tests/ingestion/test_enrichment.py) and against
the real, installed `tiktoken` library — not guessed. Written for someone who has never
seen this code before.

**How to use this doc:** for every concept, actually run the exercise before reading the
checkpoint. Five exercises per concept, ordered easiest → hardest, with hints for anyone who
has genuinely never touched the underlying idea before.

---

## 0. What this file does, in plain English

Imagine a librarian who has already cut a big book into index cards (that's Microstep 2,
the chunker). Each card has a rough guess written in pencil on the back: "about 100 words."
This module is a second librarian who comes along with a proper word-counting machine,
erases the pencil guess, and writes down the EXACT count in pen — on every card, without
touching what's written on the FRONT of the card at all.

That's it. This file doesn't chunk anything, doesn't embed anything, doesn't touch the
actual chunk text. Its only job is: take chunks that already exist, count how many "tokens"
(the pieces a language model actually reads, not quite the same as words) each one contains,
using a real, industry-standard counting tool instead of the cheap estimate from Step 2.1,
and hand back new, corrected chunks.

**The concept map** (read top to bottom):

```
Talking to an unknown "thing":       Protocol (same idea as chunker.py's Embedder)
Failing early vs failing late:       eager loading in the constructor
Not damaging what you were given:    pure functions + dataclasses.replace on a frozen object
What a "token" even is:              tokenization / BPE — the real ML concept here
Not paying for what you don't use:   factory function + lazy import (same pattern as chunker.py)
Marking some tests as "special":     pytest custom markers
Baking a dependency into an image:   multi-stage Docker cache pre-warming
A dependency that was never really
  "yours" until you said so:         transitive vs. pinned dependencies
```

---

## 1. Concept-by-concept

---

### 1.1 `TokenCounter` — a `Protocol` (the "any object with the right ability" trick)

**1. Definition (plain English):** a `Protocol` is a checklist of abilities, not a family
tree. It doesn't ask "are you a `TiktokenCounter`?" — it asks "do you have a `.count(text)`
method that takes a string and gives back a number?" Anything that answers yes is allowed
in, no matter what it actually is. Imagine a bouncer at a club whose only rule is "can you
show a valid ID?" — he doesn't care if it's a driver's license, a passport, or a student ID,
only that it does the ONE job of proving your age.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
way to say "this parameter must be able to count tokens" is inheritance — force every
counter, including test doubles, to extend some base `Counter` class. That drags real
counting machinery (and potentially a slow tiktoken load) into every test. `Protocol` fixes
this by checking the SHAPE of the object instead of its ancestry, so a test can hand
`enrich_with_token_counts` a `FakeCounter` that's a plain, tiny class with nothing borrowed
from anywhere.

**3. A clean, minimal code example:**
```python
from typing import Protocol

class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...

class WordCounter:                 # no inheritance, no relation to TokenCounter at all
    def count(self, text: str) -> int:
        return len(text.split())

def show_count(counter: TokenCounter, text: str) -> None:
    print(counter.count(text))

show_count(WordCounter(), "hello there friend")   # prints 3, no errors
```
This is exactly `tests/ingestion/test_enrichment.py`'s `FakeCounter` — a plain class,
accepted everywhere a real `TiktokenCounter` would be.

**4. Main benefits, in short:** (a) tests never load tiktoken's real encoding file, so the
suite stays instant and offline; (b) a future counter (Cohere's tokenizer, a local model's
own tokenizer) works automatically as long as it has a `.count()` method; (c) `mypy` still
catches a missing method, so dropping inheritance doesn't mean dropping safety.

**What breaks if removed:** make `TokenCounter` a real base class instead — `FakeCounter`
in the test file would need to inherit from it, and if that base class did anything heavy in
`__init__` (as `TiktokenCounter` does — see section 1.2), every single test would pay that
cost too, even ones that never touch a real tokenizer.

**Canonical reading:** [Python docs — typing.Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol). (Same mechanism as `chunker.py`'s `Embedder` — see [docs/6 chunker_deep_dive.md](6%20chunker_deep_dive.md) section 1.1 for a much deeper treatment if this is your first time meeting `Protocol`.)

**5 micro-exercises (absolute beginner, do them in order):**

1. **(5 min) Run the minimal example above yourself**, then delete the `.count()` method
   from `WordCounter` and predict: does `show_count(WordCounter(), "hi")` crash immediately
   when Python reads the class definition, or only when `.count()` is actually called?
   *Hint:* run it and find out — Python doesn't check Protocols at all while running, only
   `mypy` does (see section 1.1's checkpoint below for why this matters).

2. **(10 min) Confirm `FakeCounter` genuinely satisfies `TokenCounter`.**
   ```python
   from src.ingestion.enrichment import TokenCounter
   from tests.ingestion.test_enrichment import FakeCounter
   fc = FakeCounter()
   print(fc.count("one two three"))   # should print 3
   ```
   *Hint:* nothing here mentions `TokenCounter` by name inside `FakeCounter` — that's the
   whole point.

3. **(10 min) Write your own tiny counter that counts CHARACTERS instead of words.**
   ```python
   class CharCounter:
       def count(self, text: str) -> int:
           return len(text)
   ```
   Pass it into `enrich_with_token_counts([chunk], CharCounter())` on a real chunk and
   confirm `metadata["tokens_approx"]` now holds a character count, not a word count — proof
   that the enrichment function genuinely doesn't care WHAT kind of counting happens inside.

4. **(10 min) Try `isinstance()` on a Protocol.**
   ```python
   from src.ingestion.enrichment import TokenCounter
   from tests.ingestion.test_enrichment import FakeCounter
   print(isinstance(FakeCounter(), TokenCounter))
   ```
   *Hint:* read the actual error/result — plain Protocols (without `@runtime_checkable`)
   don't support this the way you might expect. Look up `@runtime_checkable` afterward.

5. **(15 min) Find every place `TokenCounter` is actually used in the real file.** Search
   `enrichment.py` for `counter.` — it's called exactly once. Now ask: does
   `enrich_with_token_counts` know or care whether `counter` is a `TiktokenCounter`, a
   `FakeCounter`, or something you haven't invented yet? Write one sentence justifying your
   answer by pointing at the specific line.

**How it works internally:** exactly like `chunker.py`'s `Embedder` — at RUN time, Python
does zero checking; it just calls `.count(text)` and trusts the method exists. All the
"does this shape match" reasoning happens separately, only when `mypy` reads your code
without running it.

**Checkpoint:** explain, in your own words, why `enrich_with_token_counts` would work fine
even with a counter class that was invented AFTER this file was written, with zero changes
to `enrichment.py` itself.

---

### 1.2 Eager loading in the constructor — fail now, not later

**1. Definition (plain English):** `TiktokenCounter.__init__` loads the real tokenizer file
IMMEDIATELY when the object is created, not later when you first ask it to count something.
Imagine buying a car. "Eager" is popping the hood and starting the engine in the dealership
parking lot BEFORE you drive off, so a dead battery is the dealer's problem, right there.
"Lazy" is driving off without checking, and finding out about the dead battery on the
highway, 50 miles from home, with nobody around to help.

**2. The "Why" — what problem this solves vs. the traditional approach:** the "lazy"/
traditional approach would load the tokenizer inside `count()`, the first time it's actually
called. That looks fine until you realize WHEN "the first time" happens: potentially chunk
#1 of document #1, AFTER you've already spent minutes chunking 76 documents with a real
embedding model. If the tokenizer name is wrong or the encoding can't be fetched, you find
out only then — deep into an expensive run, with partial results half-finished. Loading it
eagerly, in the constructor, means `TiktokenCounter("bad_name")` fails the INSTANT you try to
build one — before any chunking, before any real work, with an obvious traceback pointing
straight at the constructor call.

**3. A clean, minimal code example** (this is a REAL, run test, not hypothetical — verified
directly against this project's code):
```python
from src.ingestion.enrichment import TiktokenCounter
import time

t0 = time.perf_counter()
try:
    TiktokenCounter("not_a_real_encoding_xyz")
except ValueError as e:
    print(f"failed after {time.perf_counter() - t0:.4f}s: {e}")
# failed after 0.0355s: Unknown encoding not_a_real_encoding_xyz.
```

**4. Main benefits, in short:** (a) a bad tokenizer name is caught in milliseconds, at
startup, before touching a single real document; (b) the failure happens at the exact line
that constructed the counter, not somewhere deep inside a loop; (c) for a 76-document
ingestion run, this is the difference between "the whole run refuses to even start" and
"the run dies 40 documents in, having wasted real embedding time on the other 36."

**What breaks if changed to lazy loading:** move `tiktoken.get_encoding(...)` from
`__init__` into `count()` — `TiktokenCounter("bad_name")` would now succeed instantly (it
just stores the string), and the `ValueError` would only fire on the FIRST call to
`.count()`. If that first call happens after chunking dozens of real documents (an expensive
step), you've now paid for all that work before discovering the tokenizer was misconfigured
the whole time.

**Canonical reading:** this specific pattern is usually called "fail fast" — see [Martin Fowler's short note on Fail Fast](https://martinfowler.com/ieeeSoftware/failFast.pdf) for the general software-engineering argument.

**5 micro-exercises:**

1. **(5 min) Reproduce the exact timing test above yourself.** Run it, read the real
   error message, confirm it fails in well under a second.

2. **(10 min) Build the LAZY version and feel the difference.**
   ```python
   class LazyCounter:
       def __init__(self, encoding_name):
           self._name = encoding_name          # nothing loaded yet
           self._encoding = None
       def count(self, text):
           if self._encoding is None:
               import tiktoken
               self._encoding = tiktoken.get_encoding(self._name)  # loads HERE, late
           return len(self._encoding.encode(text))

   c = LazyCounter("not_a_real_encoding_xyz")
   print("constructed with no error!")           # succeeds
   c.count("this will now fail")                  # fails HERE instead
   ```
   *Hint:* just run it and watch WHERE the error actually happens versus where you might
   expect it to, given the bad name was passed in at construction time.

3. **(10 min) Simulate the "40 documents in" scenario.** Using the lazy version from
   exercise 2, write a loop that calls `.count()` on 5 GOOD strings first (succeeding every
   time, doing "real work"), then on a 6th call that would fail. Confirm the first 5 calls
   silently pretended everything was fine.

4. **(10 min) Time BOTH versions' constructors** with a VALID encoding name, and compare:
   ```python
   import time
   t0 = time.perf_counter(); TiktokenCounter("cl100k_base"); print("eager:", time.perf_counter()-t0)
   t0 = time.perf_counter(); LazyCounter("cl100k_base"); print("lazy: ", time.perf_counter()-t0)
   ```
   Which one is slower to CONSTRUCT? Is that a real cost, or does it just move the SAME cost
   to a different, more dangerous moment?

5. **(15 min) Find another eager-loading example already in this codebase.** Look at
   `chunker.py`'s `Chunker.__init__` — does it eagerly construct its `SemanticChunker`
   instance, or does it defer that? Read the constructor and decide, then explain your
   answer with a specific line reference.

**How it works internally:** there's no special Python feature here — it's simply WHERE you
choose to put a line of code inside a class. Python executes `__init__`'s body completely,
top to bottom, the moment `TiktokenCounter(...)` is called — so any line in there, including
`tiktoken.get_encoding(...)`, runs immediately and can raise immediately.

**Checkpoint:** explain why eager loading matters MORE for a 76-document batch ingestion run
than it would for, say, a single interactive chatbot request that only ever counts one
short message at a time.

---

### 1.3 Pure functions + `dataclasses.replace` — fixing a frozen object without breaking it

**1. Definition (plain English):** a "pure" function never changes anything you hand it —
it only returns something NEW. Imagine a photocopier: you feed it a page, it hands you back
a fresh copy with something added, and your ORIGINAL page is still sitting there, completely
unmarked, exactly as it was before. `enrich_with_token_counts` is that photocopier for
chunks: it takes chunks in, and gives you NEW chunks out, never touching the originals.

**2. The "Why" — what problem this solves vs. the traditional approach:** `DocumentChunk`
is `frozen=True` (from Step 2.1) specifically so nothing can quietly edit a chunk after
it's built. The traditional, tempting shortcut here would be
`chunk.metadata["tokens_approx"] = tokens` — and that ACTUALLY WORKS, because `frozen=True`
only blocks reassigning a FIELD (`chunk.metadata = {...}`), not mutating the dictionary
OBJECT that field happens to point at (a real gap, documented in
[docs/2 schemas_deep_dive.md](2%20schemas_deep_dive.md) section 1.2). Taking that shortcut
would quietly defeat the whole reason `frozen=True` exists. Using `dataclasses.replace` plus
a brand-new dict instead means the original chunk is genuinely, provably untouched.

**3. A clean, minimal code example** (verified directly, not assumed — the printed `id()`s
below really do differ, proving these are two separate objects in memory):
```python
import dataclasses
from pathlib import Path
from src.ingestion.schemas import make_chunk

root = Path("data")
original = make_chunk(source_path=root / "company" / "about.md", data_root=root,
                       title="t", chunk_index=0, text="hello world")
print("before:", original.approx_token_count, original.metadata, id(original))

updated = dataclasses.replace(
    original,
    approx_token_count=999,
    metadata={**original.metadata, "tokens_approx": 999},
)
print("original after:", original.approx_token_count, original.metadata, id(original))
print("new chunk      :", updated.approx_token_count, updated.metadata, id(updated))
print("same object?", original is updated)
```
Real output from this exact project: `original after: 2 {}` (completely unchanged) vs
`new chunk: 999 {'tokens_approx': 999}` — two different objects, different `id()`s,
`same object? False`.

**4. Main benefits, in short:** (a) you can always compare "before" and "after" side by
side, because "before" still genuinely exists; (b) re-running the enrichment twice can never
accidentally double-count or corrupt anything, since nothing is ever edited in place; (c) a
bug in enrichment can never reach backward and silently damage data some OTHER part of the
program is still holding a reference to.

**What breaks if changed to in-place mutation:** do `chunk.metadata["tokens_approx"] =
tokens` directly instead — it works today, with zero errors, but now ANY code anywhere that
kept a reference to the "original" chunk sees the mutation too, since it's the SAME object.
`test_original_chunks_are_not_modified` in the real test suite would fail immediately,
because it explicitly checks the original was left alone.

**Canonical reading:** [Python docs — dataclasses.replace](https://docs.python.org/3/library/dataclasses.html#dataclasses.replace), and [Wikipedia — pure function](https://en.wikipedia.org/wiki/Pure_function) for the general concept.

**5 micro-exercises:**

1. **(5 min) Run the exact code example above yourself** and confirm your own printed
   `id()` values differ, just like the ones shown.

2. **(10 min) Try the "shortcut" and watch it work — then understand why that's dangerous.**
   ```python
   original.metadata["tokens_approx"] = 12345    # DIRECT mutation, no dataclasses.replace
   print(original.metadata)                       # it worked! frozen didn't stop this
   ```
   *Hint:* this succeeding is exactly the documented gap in `frozen=True` — re-read
   `docs/2 schemas_deep_dive.md` section 1.2 if this surprises you.

3. **(10 min) Prove `dataclasses.replace` fails for a field that doesn't exist.**
   ```python
   dataclasses.replace(original, this_field_does_not_exist=123)
   ```
   Read the exact `TypeError` — this is `dataclasses.replace`'s own built-in safety net
   against typos, something a hand-written `metadata[...] = ...` line would never catch.

4. **(10 min) Write your OWN pure function** that takes a list of numbers and returns a new
   list with every number doubled, WITHOUT ever modifying the input list:
   ```python
   def double_all(numbers: list[int]) -> list[int]:
       return [n * 2 for n in numbers]      # complete this yourself first if you can

   original_list = [1, 2, 3]
   doubled = double_all(original_list)
   print(original_list, doubled, original_list is doubled)
   ```
   Confirm `original_list` is untouched.

5. **(15 min) Find the exact metadata line in `enrichment.py` and explain why it uses
   `{**chunk.metadata, "tokens_approx": tokens}` instead of just
   `chunk.metadata` (reused) or `{"tokens_approx": tokens}` (replaced entirely).** Test what
   would go wrong with each of those two alternatives using a chunk that already has
   `start_char`/`end_char` in its metadata (every real chunk does, from Microstep 2's fix).

**How it works internally:** `dataclasses.replace(obj, **changes)` reads every field off
the ORIGINAL object, builds a dictionary of "field name → current value" for everything you
didn't explicitly override, merges in your overrides, and calls the class's normal
constructor with the combined set — producing a genuinely new instance. It works on frozen
dataclasses specifically because it never tries to ASSIGN to the old object at all; it just
builds a new one from scratch using the same values.

**Checkpoint:** explain precisely which THREE lines of `enrich_with_token_counts` would you
need to change to make it a MUTATING function instead of a pure one, and what specific test
in `test_enrichment.py` would immediately start failing as a result.

---

### 1.4 Tokenization / BPE — what a "token" actually is

**1. Definition (plain English):** a token is NOT a word and NOT a character — it's
whatever chunk of text a specific model was trained to treat as one unit, and those chunks
are often WEIRD, sub-word fragments. Imagine a set of alphabet magnets, except instead of
just single letters, some magnets have whole common word-pieces stuck together already —
"ing", "tion", "un" — because those combinations show up so often it's more efficient to
have one magnet for them instead of building them letter by letter every time. A tokenizer
is the machine that looks at your sentence and decides which magnets to grab.

**2. The "Why" — what problem this solves vs. the traditional approach:** the "traditional",
naive approach is to count WORDS (split on spaces) or CHARACTERS (divide by 4, as Step 2.1's
heuristic did). Neither matches what a language model actually processes — models don't see
words or raw characters, they see tokens from a fixed vocabulary built by an algorithm called
Byte Pair Encoding (BPE), which merges the most frequently-occurring adjacent pairs of
characters over and over until it has a vocabulary of ~100,000 useful chunks. `tiktoken`
gives you the REAL count a specific model's vocabulary would produce, instead of a rough
guess.

**3. A clean, minimal code example** (real output, run against this project's actual
tiktoken installation — not invented):
```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

text = "unbelievable tokenization"
ids = enc.encode(text)
pieces = [enc.decode([token_id]) for token_id in ids]

print(ids)      # [359, 32898, 24694, 4037, 2065]
print(pieces)   # ['un', 'belie', 'vable', ' token', 'ization']
```
Two English words become FIVE tokens — "unbelievable" alone splits into three pieces,
because it wasn't common enough as a whole word to earn its own single token in this
vocabulary.

**4. Main benefits, in short:** (a) counts that actually correspond to how a model "reads"
text, useful for staying inside a real context-window limit; (b) a portable, reproducible
number — the same text always produces the same count with the same encoding; (c) an
industry-standard measurement that other tools/APIs quoting "token limits" are usually also
using (or something comparable), so your counts are comparable to published limits.

**What breaks if you assume tokens = words:** the example above alone proves this wrong —
2 words became 5 tokens, more than double. A budget of "500 words" and a budget of "500
tokens" are NOT the same constraint, and code that conflates them will silently allow chunks
that are too large for a model's real context window, or unnecessarily reject ones that
would have fit.

**Canonical reading:** [tiktoken's own GitHub README](https://github.com/openai/tiktoken) (short, has runnable examples); [Hugging Face — Byte-Pair Encoding tokenization](https://huggingface.co/learn/nlp-course/chapter6/5) for the algorithm itself, in more depth.

**5 micro-exercises:**

1. **(5 min) Run the exact "unbelievable tokenization" example above yourself.** Try 2-3
   of your own words and see which ones split into multiple pieces and which stay whole.
   *Hint:* very common short words (the, is, cat) usually stay as one token; longer or rarer
   words often split.

2. **(10 min) Compare a real chunk's word count, character count, and token count, side by
   side.**
   ```python
   from pathlib import Path
   from src.ingestion.loader import load_document
   from src.ingestion.enrichment import build_tiktoken_counter

   doc = load_document(Path("data/company/about.md"), Path("data"))
   text = doc.text[:400]
   counter = build_tiktoken_counter()
   print("words     :", len(text.split()))
   print("characters:", len(text))
   print("chars / 4 :", len(text) // 4)        # Step 2.1's old heuristic
   print("tokens    :", counter.count(text))    # the real count
   ```
   Which of the three estimates (words, chars/4) is CLOSER to the real token count on this
   real sample? By how much?

3. **(10 min) Find a word that becomes exactly ONE token, and one that becomes SEVERAL.**
   Try `"cat"`, `"the"`, `"Insurellm"` (this project's fictional company name), and
   `"reinsurance"` — predict which will split before running, then check.

4. **(10 min) Encode an EMOJI or a rare symbol and see what happens.**
   ```python
   print(enc.encode("🎉"))
   print(enc.decode(enc.encode("🎉")))
   ```
   How many tokens does one emoji cost? Is it more or less than you'd have guessed?

5. **(15 min) Measure the heuristic's real error across several real chunks, not just
   one.** Loop over the first 5 real files in `data/company/` and `data/products/`, and for
   each one's first 400 characters, print `char_count // 4` vs the real tiktoken count.
   Compute the average percentage error across all 5 — is Step 2.1's heuristic
   consistently an overestimate, an underestimate, or does it vary?

**How it works internally:** `tiktoken.get_encoding("cl100k_base")` loads a pre-built
lookup table (the "vocabulary") mapping specific byte sequences to integer IDs. `.encode()`
greedily finds the LONGEST sequence of bytes in your text that matches an entry in that
table, converts it to its ID, and moves on to the next unmatched byte — repeating until the
whole input is consumed. That vocabulary was built once, offline, by running BPE's
merge-the-most-frequent-pair algorithm over a huge amount of training text; using
`get_encoding` just loads the RESULT of that process, it doesn't run BPE itself each time.

**Checkpoint:** explain, using the "unbelievable" example, why a model with a smaller
vocabulary (fewer available token "magnets") would tend to need MORE tokens for the same
sentence than a model with a bigger vocabulary.

---

### 1.5 Factory function + lazy import — the same trick as `chunker.py`, applied again

**1. Definition (plain English):** `build_tiktoken_counter()` is a small function whose only
job is to construct and return a real, usable counter — and the actual
`import tiktoken` line lives INSIDE `TiktokenCounter.__init__`, not at the top of the file.
Same "don't build the car until someone orders one" idea from `chunker.py`'s
`build_ollama_embedder` — see
[docs/6 chunker_deep_dive.md](6%20chunker_deep_dive.md) section 1.9 for the full treatment;
this section applies the same pattern to a different library.

**2. The "Why" — what problem this solves vs. the traditional approach:** if
`import tiktoken` sat at the top of `enrichment.py`, then simply importing this module (as
every test file that touches enrichment does) would immediately load the `tiktoken` package
— and worse, if `tiktoken.get_encoding(...)` were ALSO called at module level (rather than
inside a constructor you choose whether to call), every test would pay the encoding-load
cost even ones using `FakeCounter` and never touching a real tokenizer at all.

**3. A clean, minimal code example:**
```python
def build_tiktoken_counter(encoding_name="cl100k_base"):
    from src.ingestion.enrichment import TiktokenCounter   # constructed only when called
    return TiktokenCounter(encoding_name)

# Importing enrichment.py alone never loads tiktoken's encoding file:
import src.ingestion.enrichment
print("module imported, nothing loaded yet")

# Only THIS line actually loads it:
counter = src.ingestion.enrichment.build_tiktoken_counter()
```

**4. Main benefits, in short:** (a) importing `enrichment.py` has zero cost beyond normal
Python import overhead; (b) tests using `FakeCounter` never load the real tiktoken
machinery; (c) the one place a real network-capable dependency enters the picture stays easy
to find and audit.

**What breaks if the import moved to the top:** nothing crashes immediately (tiktoken IS
installed and pinned) — but every test file importing `enrichment` would import `tiktoken`
too, regardless of whether that specific test ever constructs a `TiktokenCounter`.

**Canonical reading:** [Python docs — the import statement](https://docs.python.org/3/reference/simple_stmts.html#the-import-statement) (imports are legal anywhere a statement is legal).

**5 micro-exercises:**

1. **(5 min) Confirm importing `enrichment.py` alone is instant**, even without tiktoken
   available (simulate this by checking how fast the import runs — it should be
   milliseconds, not the ~5 seconds a cold `tiktoken.get_encoding` call takes).

2. **(10 min) Move `import tiktoken` to the top of a scratch copy** of `enrichment.py` and
   confirm all tests still pass. (They should — this exercise is about noticing the
   DISCIPLINE doesn't change test outcomes today, only future flexibility/cost isolation.)

3. **(10 min) Time the difference directly.**
   ```python
   import time
   t0 = time.perf_counter()
   import src.ingestion.enrichment
   print("import module:", time.perf_counter() - t0)

   t0 = time.perf_counter()
   src.ingestion.enrichment.build_tiktoken_counter()
   print("build real counter:", time.perf_counter() - t0)
   ```
   Compare the two numbers — which step actually costs time?

4. **(10 min) Trace exactly which function calls which.** Read `build_tiktoken_counter`,
   then `TiktokenCounter.__init__`, then find the literal line `import tiktoken` — count
   how many function calls deep that import statement sits from a bare
   `import src.ingestion.enrichment`.

5. **(15 min) Write your OWN factory for `FakeCounter`.** Should its import be lazy too?
   `FakeCounter` lives in your test file with no heavy dependency at all — write one
   sentence explaining why lazy imports matter for HEAVY/networked dependencies
   specifically, not for every function ever, so you're not just cargo-culting the pattern.

**How it works internally:** identical mechanism to section 1.2 of the chunker deep dive —
Python only executes an import statement the moment execution actually reaches that line,
and a function's body doesn't execute until the function is called.

**Checkpoint:** name the ONE line in the whole `enrichment.py` file where, if you deleted
the word `import` from in front of `tiktoken`, the import would happen at module level
instead of function level — and explain what test suite behavior would change as a result.

---

### 1.6 pytest custom markers — labeling a test as "special"

**1. Definition (plain English):** a marker is a sticky note you attach to a test with
`@pytest.mark.something`, so you can later say "run every test EXCEPT the ones with this
sticky note" (or the reverse). Imagine sorting laundry: you put a special tag on anything
that needs hand-washing, so later you can say "wash everything EXCEPT the hand-wash pile" in
one instruction, without having to remember which specific items those were.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach — one undifferentiated pile of tests — means EVERY test runs every time, including
the one test (`test_real_tiktoken_counts_differ_from_the_heuristic`) that genuinely needs a
real tiktoken encoding loaded, which needs either a warm cache or actual network access.
Tagging that ONE test with `@pytest.mark.tiktoken` means the other 53 tests can be run
completely offline with `pytest -m "not tiktoken"`, without deleting or skipping the real
test permanently.

**3. A clean, minimal code example:**
```python
import pytest

@pytest.mark.tiktoken
def test_needs_the_real_thing():
    assert True

def test_totally_normal():
    assert True
```
Run `pytest -v` (both run) vs `pytest -v -m "not tiktoken"` (only the second one runs) —
verified directly against this exact project: `53 passed, 1 deselected`.

**4. Main benefits, in short:** (a) a slow/network-dependent test can exist in the same file
as fast ones without slowing down every CI run; (b) `-m "not tiktoken"` is one flag, not a
list of test names to remember and keep updated; (c) registering the marker (see below)
turns a typo'd marker name into a visible warning instead of a silently-ignored no-op.

**What breaks if the marker is never registered** (in `pyproject.toml`'s
`[tool.pytest.ini_options] markers = [...]`): the marker still WORKS for selection purposes
(`-m tiktoken` still finds it) — verified directly by running an unregistered marker in a
scratch test — but pytest prints a `PytestUnknownMarkWarning: ... is this a typo?` for every
such test, which is exactly the safety net you'd want if you ever genuinely DID typo a
marker name.

**Canonical reading:** [pytest docs — marking test functions](https://docs.pytest.org/en/stable/how-to/mark.html).

**5 micro-exercises:**

1. **(5 min) Run the two commands yourself** on this real project:
   `pytest tests/ -q` then `pytest tests/ -q -m "not tiktoken"` — compare the pass counts.

2. **(10 min) Reproduce the unregistered-marker warning.** Write a scratch test file with
   `@pytest.mark.this_is_a_typo_marker` and run it — read the EXACT warning text pytest
   prints, including the URL it points you to. Then delete the scratch file.

3. **(10 min) Register your own new marker.** Add
   `"slow: takes more than a second"` to `pyproject.toml`'s `markers` list, tag one real
   test with `@pytest.mark.slow`, and confirm `pytest -m slow` selects just that one test.
   (Remove your change afterward if you don't want to keep it.)

4. **(10 min) Combine markers with `-k` and compare.** `-k` selects tests by NAME substring
   matching, `-m` selects by MARKER. Run `pytest -k "tiktoken"` (name-based) vs
   `pytest -m "tiktoken"` (marker-based) — do they select the same test here? Would they
   always?

5. **(15 min) Find the exact line in `pyproject.toml` that registers the `tiktoken` marker**
   and delete just that ONE line (not the test file). Rerun the full suite and read the new
   warning that appears — confirm the test still runs and still gets selected/deselected
   correctly, ONLY the warning changes.

**How it works internally:** `@pytest.mark.tiktoken` doesn't execute any code at test-run
time by itself — it attaches a small piece of metadata to the test function object, which
pytest's own collection step later reads when deciding, for a given `-m` expression, which
tests to include or exclude from the run. Registering a marker in `pyproject.toml` simply
tells pytest "this name is expected, don't warn about it" — it has no other effect on
behavior.

**Checkpoint:** explain the difference between "a test is marked `tiktoken`" and "a test
requires network access" — are these the same thing, or could you imagine a test marked
`tiktoken` that doesn't actually need network (hint: think about a warm vs. cold cache)?

---

### 1.7 Multi-stage Docker cache pre-warming — baking a dependency into the image

**1. Definition (plain English):** the Dockerfile fetches tiktoken's encoding file WHILE
BUILDING the image (when the build process still has internet access), saves it to a
specific folder, and then copies that folder into the final image — so the container that
actually RUNS later never needs to fetch anything itself. Imagine packing a suitcase for a
trip to a place with no shops: you buy everything you'll need BEFORE you leave, while
you're still near stores, and pack it all in — rather than hoping you can buy it once you
arrive somewhere that might have no shops at all.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional,
do-nothing approach is to just let `tiktoken.get_encoding(...)` run whenever the app
actually calls it. That works fine on your laptop with internet, but a real deployed
container often runs with restricted or NO outbound network access (for security reasons) —
turning a simple token count into a hard failure the first time anyone calls it, unless the
file happens to already be cached. Pre-warming solves this by doing the fetch at BUILD time,
when network access is virtually guaranteed, and shipping the result inside the image
itself.

**3. A clean, minimal code example** (the actual, verified-working Dockerfile lines):
```dockerfile
# --- builder stage: network is available here ---
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# --- runtime stage: this stage starts BLANK, nothing carries over automatically ---
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
COPY --from=builder /opt/tiktoken_cache /opt/tiktoken_cache
```
Verified directly against the real built image, with networking FULLY disabled:
```bash
docker run --rm --network none advance-rag:ci python -c "
import tiktoken
enc = tiktoken.get_encoding('cl100k_base')
print('OFFLINE LOAD OK -> tokens:', len(enc.encode('Alpha one. Alpha two.')))
"
# OFFLINE LOAD OK -> tokens: 6
```

**4. Main benefits, in short:** (a) the running container never needs outbound network just
to count tokens; (b) the encoding-download cost is paid ONCE, at build time, not on every
container start; (c) a network-restricted production environment (a common security
posture) doesn't silently break token counting.

**What breaks if either the `ENV` or the `COPY --from=builder` line is removed:** Docker
multi-stage builds share NOTHING between stages by default — each `FROM` starts a fresh,
blank filesystem. Skip the `COPY --from=builder` and the runtime stage never receives the
warmed cache at all, defeating the whole point. Skip re-declaring `ENV TIKTOKEN_CACHE_DIR`
in the runtime stage specifically, and tiktoken falls back to its DEFAULT cache location
(which the fetched file isn't in), meaning it tries to fetch again at runtime anyway,
despite the file having been "cached" during the build for nothing.

**Canonical reading:** [Docker docs — multi-stage builds](https://docs.docker.com/build/building/multi-stage/) (same reference as `docs/3 docker_deep_dive.md`'s section 1.4, applied here to a real dependency instead of application code).

**5 micro-exercises:**

1. **(5 min) Confirm the cache directory genuinely exists inside the built image.**
   ```bash
   docker run --rm advance-rag:ci ls -la /opt/tiktoken_cache
   ```
   You should see a cached file with a long hash-like filename.

2. **(10 min) Reproduce the FULL offline test yourself**, exactly as shown in part 3 above
   — `docker run --rm --network none advance-rag:ci python -c "..."` — and confirm it
   prints the token count with no network error.

3. **(10 min) Break it on purpose: remove the `COPY --from=builder` line** in a scratch copy
   of the Dockerfile, rebuild, and rerun the `--network none` test from exercise 2. Confirm
   it now FAILS (a network/connection error), proving the copy step is genuinely load-bearing
   and not decorative.

4. **(10 min) Break it a different way: remove ONLY the runtime stage's `ENV
   TIKTOKEN_CACHE_DIR` line** (keep the `COPY` line), rebuild, and rerun the offline test.
   Predict first: will this fail the same way, or differently? (Hint: the file IS still in
   the image somewhere — the question is whether tiktoken knows to LOOK there.)

5. **(15 min) Measure the actual disk cost.** Compare the image size with and without the
   cache pre-warming step (`docker images` before/after commenting out the RUN/COPY lines
   and rebuilding). Is baking in the encoding file a meaningfully large cost, or negligible?

**How it works internally:** `docker build` executes each `FROM` as the start of a
completely separate build context — layers from an earlier stage exist only in Docker's
build cache/history, not automatically inside a LATER stage's filesystem. `COPY --from=X` is
the ONE explicit mechanism for pulling specific files from an earlier stage into the current
one. `tiktoken.get_encoding(...)` itself checks the `TIKTOKEN_CACHE_DIR` environment
variable (or a default temp location) BEFORE attempting any network fetch — if a correctly-
named cache file is already sitting there, it loads it straight from disk and skips the
network entirely, which is exactly what makes the `--network none` test succeed.

**Checkpoint:** explain, step by step, the exact chain of things that would have to ALL be
true simultaneously for `docker run --network none advance-rag:ci ...` to successfully load
the encoding — name every Dockerfile line that contributes to making that chain hold.

---

### 1.8 Transitive vs. pinned dependencies — a dependency that was never really "yours"

**1. Definition (plain English):** a "transitive" dependency is a package that's installed
on your machine only because SOMETHING ELSE you depend on needs it — you never asked for it
directly, and nothing guarantees it'll still be there tomorrow. A "pinned" dependency is one
YOU explicitly listed, with an exact version, in your own project's dependency file. Imagine
borrowing a friend's car that happens to have a spare umbrella in the trunk. That umbrella is
"transitively" available to you right now — but the moment you give the car back, or your
friend cleans out their trunk, the umbrella is just... gone, and you never had any right to
expect it would stay.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional,
easy-to-fall-into mistake is using a library that HAPPENS to be installed (because some
OTHER package pulled it in) without ever declaring your own dependency on it. This project
did exactly that with `tiktoken` — it was importable and working, purely because
`langchain-openai` (itself pulled in by other LangChain packages) happened to need it. If
LangChain ever dropped that internal dependency in a future version, `tiktoken` could vanish
from a fresh install with ZERO warning, breaking `enrichment.py` on a machine where it had
always "just worked" before.

**3. A clean, minimal code example** (the actual verification commands run against this
real project, with real output):
```bash
pip show tiktoken
# Name: tiktoken
# Version: 0.14.0

grep tiktoken pyproject.toml
# (before this step: no output at all -- NOT actually declared anywhere)
# (after this step:)  "tiktoken==0.14.0",
```

**4. Main benefits, in short (of pinning it explicitly):** (a) `pip install -e .` on a
completely fresh machine, with no LangChain packages installed for some future reason, would
still install `tiktoken`; (b) the EXACT version (`0.14.0`) is locked, so a `pip` upgrade of
something else can't silently change tiktoken's behavior underneath this code; (c) anyone
reading `pyproject.toml` can see, at a glance, that this project genuinely depends on
tiktoken, without having to trace it through LangChain's own dependency tree.

**What breaks if left transitive:** nothing breaks TODAY — that's exactly the trap. It looks
completely fine until some unrelated `pip install --upgrade langchain-openai` (or a version
bump anywhere in that dependency chain) removes tiktoken as a side effect, and
`enrichment.py` starts raising `ModuleNotFoundError: No module named 'tiktoken'` on a machine
where it "always worked before" — a confusing, hard-to-diagnose failure with no connection,
on the surface, to anything you actually changed.

**Canonical reading:** [Python Packaging User Guide — dependency specifiers](https://packaging.python.org/en/latest/specifications/dependency-specifiers/) explains how declared dependencies work; there's no single canonical source for the "transitive dependency trap" itself, but it's the same underlying lesson as Step 1.1's `pip install --dry-run` discipline (see `docs/1 ci_deep_dive.md` and this project's `NOTES.md`/`notes/` for that earlier lesson).

**5 micro-exercises:**

1. **(5 min) Run both verification commands yourself** on this real project, exactly as
   shown in part 3, and confirm tiktoken really is now listed in `pyproject.toml`.

2. **(10 min) Find ANOTHER transitively-installed package right now, before it bites you.**
   Run `pip list` in this project's venv, pick 3 package names you don't recognize needing
   directly, and for each one run `grep <name> pyproject.toml` — are any of them silently
   relied upon without being declared?

3. **(10 min) Simulate the failure.** In a SCRATCH virtual environment (not this project's
   real one), `pip install langchain-openai` alone (which pulls tiktoken in transitively),
   confirm `import tiktoken` works — then `pip uninstall langchain-openai`, and try
   `import tiktoken` again. Does it still work? (It might, depending on whether uninstalling
   the parent package also removes packages IT pulled in — find out for yourself rather than
   guessing, since `pip uninstall` behavior here is worth knowing directly.)

4. **(10 min) Check `requirements.txt` too**, not just `pyproject.toml` — this project
   maintains BOTH (a decision from Step 1.1). Confirm `tiktoken==0.14.0` appears in both
   files, and explain in one sentence why keeping two files in sync matters here (hint:
   which one does the Docker build actually read from, versus which one `pip install -e
   ".[dev]"` reads from?).

5. **(15 min) Write a tiny script that audits this for you.** Using `pip list --format
   json` and reading `pyproject.toml`'s `dependencies` list, write a script that prints
   every installed package NOT explicitly declared in `pyproject.toml` — a rough first
   pass at automating exercise 2's manual check across the WHOLE dependency tree, not just
   3 packages.

**How it works internally:** `pip` resolves an entire dependency GRAPH when you run
`pip install`, not just the one package you named — if package A requires package B, `pip`
installs both, and B is now present on disk regardless of whether YOUR project file
mentions it anywhere. Nothing about the installed environment itself distinguishes "a
package I explicitly asked for" from "a package that arrived as someone else's requirement"
— that distinction exists ONLY in your dependency declaration file, which is exactly why
`pip show` (what's actually installed) and `grep pyproject.toml` (what you actually
declared) can disagree, and why checking both is the only way to catch this.

**Checkpoint:** explain, in your own words, why `pip show tiktoken` succeeding is NOT
evidence that `tiktoken` is safe to keep relying on, and what the ONE command is that tells
you whether it's actually safe.

---

## 2. Validating it's ACTUALLY working (not just "no errors")

Direct validation against your real project — run every one of these yourself.

| What to check | Command / snippet | What "working" looks like |
|---|---|---|
| Enrichment never touches the original chunk | `original.metadata == before_copy` after calling `enrich_with_token_counts([original], ...)` | `True` — asserted directly by `test_original_chunks_are_not_modified` |
| Both counts always agree | `chunk.approx_token_count == chunk.metadata["tokens_approx"]` for every enriched chunk | `True` for every chunk — asserted by `test_field_and_metadata_hold_the_same_number` |
| The real tiktoken count differs from the heuristic | Compare `char_count // 4` vs `build_tiktoken_counter().count(text)` on a real chunk | Measured on real corpus text: heuristic 100 vs tiktoken 79 — a real, non-trivial gap |
| A bad encoding name fails immediately, not later | `TiktokenCounter("not_a_real_encoding")` | Raises within milliseconds, at construction, not on first `.count()` call |
| The offline test set is genuinely offline | `pytest tests/ -q -m "not tiktoken"` with no network and a cold cache | All 53 non-tiktoken tests still pass |
| The Docker image needs no runtime network | `docker run --rm --network none advance-rag:ci python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"` | Succeeds, prints nothing alarming — this is the strongest, most literal proof of the design |
| tiktoken is a REAL, declared dependency | `grep tiktoken pyproject.toml requirements.txt` | Appears, pinned to `0.14.0`, in both files |
| Static checks clean | `ruff check src/ingestion tests/ingestion && ruff format --check src/ingestion tests/ingestion && mypy src/ingestion` | All three report clean |

**Failure-mode drills** (predict the outcome before running):

1. **Pass a counter whose `.count()` always returns `0`, no matter the input.** Does
   `enrich_with_token_counts` still "succeed" (no exception)? What does this tell you about
   the difference between "ran without error" and "produced meaningful data"?
2. **Call `enrich_with_token_counts` with an EMPTY list of chunks.** Predict, then confirm:
   does it return `[]` cleanly, or does something break on an empty input?
3. **Enrich the SAME chunks twice in a row** (`enrich_with_token_counts(enrich_with_token_counts(chunks, c), c)`)
   — do the final token counts change on the second pass, or stay identical? What property
   does this prove (or fail to prove) about the function?

---

## 3. Visualizing it working

**Data flow, as this module actually implements it:**

```
list[DocumentChunk]   (from chunker.py -- each has approx_token_count from char/4 heuristic)
        │
        ▼
enrich_with_token_counts(chunks, counter)
        │
        │  for each chunk:
        ├── tokens = counter.count(chunk.text)         <- YOUR TokenCounter, injected
        │
        ├── dataclasses.replace(chunk,
        │       approx_token_count=tokens,             <- overwrites the old estimate
        │       metadata={**chunk.metadata,
        │                 "tokens_approx": tokens})     <- new dict, old chunk untouched
        │
        ▼
list[DocumentChunk]   (NEW objects; originals still exist, unchanged, elsewhere in memory)


Where TiktokenCounter's real count comes from, at build time vs runtime:

  DOCKER BUILD (builder stage, network available)
        │
        ├── tiktoken.get_encoding('cl100k_base')  --network fetch--> writes to
        │       $TIKTOKEN_CACHE_DIR (/opt/tiktoken_cache)
        │
        ▼
  COPY --from=builder /opt/tiktoken_cache /opt/tiktoken_cache   (explicit, stage-to-stage)
        │
        ▼
  DOCKER RUNTIME (fresh stage, ENV re-declared, --network none tested and confirmed)
        │
        └── tiktoken.get_encoding('cl100k_base')  --finds cached file, no network--> instant
```

**Tools worth using for deeper visibility:**
- **A plain Python REPL** — as with every other module in this project, everything here is
  a callable function you can invoke directly and inspect.
- **`id()` on objects, before and after enrichment** — the single clearest way to SEE purity
  in action: two different memory addresses is concrete, undeniable proof that
  `enrich_with_token_counts` returned something new rather than editing in place.
- **`docker history advance-rag:ci`** — shows every layer of the built image, including
  the tiktoken cache-warming `RUN` step, with its actual size — a direct look at what that
  pre-warming step really costs in image size.

**One end-to-end "watch it work" exercise, tying validation + visualization together:**

1. Open a Python REPL at the repo root.
2. Load a real document and chunk it (reusing the fake embedder for speed):
   ```python
   from pathlib import Path
   from src.ingestion.loader import load_document
   from src.ingestion.chunker import Chunker, ChunkerConfig
   from tests.ingestion.test_chunker import FakeEmbedder

   root = Path("data")
   doc = load_document(root / "company" / "culture.md", root)
   chunks = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1)).chunk(doc, root)
   print(len(chunks), "chunks before enrichment")
   for c in chunks:
       print(" ", c.chunk_id, "approx_token_count =", c.approx_token_count, "(heuristic)")
   ```
3. Build a REAL tiktoken counter and watch the eager-load cost happen right here, live:
   ```python
   import time
   from src.ingestion.enrichment import build_tiktoken_counter, enrich_with_token_counts

   t0 = time.perf_counter()
   counter = build_tiktoken_counter()
   print(f"counter built in {time.perf_counter() - t0:.2f}s")   # slow only if cache is cold
   ```
4. Enrich, and watch every number change:
   ```python
   before_ids = [id(c) for c in chunks]
   enriched = enrich_with_token_counts(chunks, counter)
   after_ids = [id(c) for c in enriched]

   print("any id() reused?", set(before_ids) & set(after_ids))   # should print an empty set
   for original, new in zip(chunks, enriched):
       print(" ", new.chunk_id, original.approx_token_count, "->", new.approx_token_count)
   ```
5. Prove the ORIGINAL list is still exactly as it was:
   ```python
   print([c.approx_token_count for c in chunks])       # unchanged heuristic values
   print([c.metadata for c in chunks])                 # no "tokens_approx" key anywhere
   ```
6. Finally, tie it to the Docker side: run
   `docker run --rm --network none advance-rag:ci python -c "import tiktoken; print(len(tiktoken.get_encoding('cl100k_base').encode('test')))"`
   and watch it succeed with zero network — the exact same encoding your REPL just used,
   now proven to also work fully offline, inside the real deployment artifact.

If you can predict, before running each numbered step, roughly what will happen and why —
you've internalized this module for real, not just "the tests pass."
