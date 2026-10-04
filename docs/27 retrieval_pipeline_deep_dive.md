# 27 — Retrieval Pipeline: Deep Dive (P5-M4)

> Every output block below was produced on 2026-09-29/30 by running the scripts in
> [`practice codes/phase 5/m4_retrieval_pipeline_deep_dive/`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/)
> against the real [`src/retrieval/pipeline.py`](../src/retrieval/pipeline.py). None of them needs a
> database, a Cohere key, or the network. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 5/m4_retrieval_pipeline_deep_dive/d1_frozen_tuple_immutability.py"
> ```

---

## 0. Plain-language summary

`RetrievalPipeline` doesn't invent any new retrieval logic. Everything that finds and ranks chunks
already exists (`HybridRetriever` from P5-M2, `Reranker` from P5-M3). This file's ENTIRE job is
**composition**: deciding how those two already-correct pieces connect, and what the connected
answer looks like.

```
query
  │
  ▼
_check_k / _check_query            <- C5: refuse bad input before anything expensive runs
  │
  ▼
pool_size = max(k*mult, floor)     <- C3: how many candidates the reranker gets to choose from
  │
  ▼
hybrid.retrieve_with_scores(...)   <- calls the REAL HybridRetriever (or a fake -- C1)
  │
  ▼
hybrid_by_id = {chunk_id: item}    <- C4: index the pool once, so...
  │
  ▼
reranker.rerank_with_scores(...)   <- calls the REAL Reranker (or a fake -- C1)
  │
  ▼
RankedChunk(...) for each result   <- ...each reranked chunk's hybrid data can be found again
  │
  ▼
RetrievalResult(query, results)    <- C2: one frozen, tuple-based answer object
```

Five concepts, in the order the code uses them:

| # | Concept | Core RAG? | Where in the code |
|---|---|---|---|
| C1 | Protocol-based composition (structural typing) | 🔴 core | `HybridSearcher`, `Reranking`, lines 26-38 |
| C2 | Immutable schema design: frozen dataclass + tuple | 🔴 core | `RankedChunk`, `RetrievalResult`, lines 40-69 |
| C3 | The pool-size formula at the composition seam | 🔴 core | `max(k * multiplier, floor)`, line 124 |
| C4 | Joining two ranked lists by a shared key | 🟡 important | `hybrid_by_id`, lines 128-149 |
| C5 | Fail-fast validation before any expensive call | ⚪ mostly plumbing | `_check_k`/`_check_query`, lines 72-83, 119-122 |

---

## C1 — Protocol-based composition (structural typing)

### Do this first
Run [`d0_protocol_composition.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d0_protocol_composition.py).
**Predict first:** `Robot.greet` takes an EXTRA parameter (`volume`) that `CanGreet` never
mentions. Will passing a `Robot` to `introduce()` work, or fail?

### 1. Definition + analogy
**What it is.** A `Protocol` describes what an object can DO (which methods it has, with which
shapes) instead of what it IS (which class it inherits from). This is "structural typing" --
sometimes called "static duck typing": if an object has the right methods with the right
signatures, it counts, no matter its class, and with no inheritance required at all.

**Analogy.** A restaurant needs "someone who can carry four plates at once." The manager doesn't
ask for a specific person by name, or check anyone's job title -- there's just ONE requirement,
written on a checklist: "can carry four plates without dropping them." Anyone who passes that one
check gets to work the floor, whether they're a lifelong waiter, someone's cousin helping out for a
day, or (in a cartoon) a robot built for an unrelated purpose that happens to have four steady arms.

### For a 14-year-old
Imagine a school talent show that needs "someone who can juggle three balls for ten seconds." The
organizer writes this ONE requirement on a clipboard: "must keep 3 balls in the air for 10s." That's
the whole audition. Three people show up. First, a professional-looking magician who ALSO happens to
juggle (on top of doing card tricks nobody asked about) -- the organizer doesn't care about the card
tricks, just checks the juggling, and waves them through. Second, a homemade robot with a weird extra
dial labeled "volume" that has nothing to do with juggling -- again, the organizer ignores the dial,
checks only the juggling, and lets the robot on stage too. Third, a very good cat, who can meow
beautifully but has never juggled anything in its life -- the cat gets turned away at the door,
because it fails the ONE thing the clipboard actually asks for.

Now imagine a stricter version of this story: instead of finding out ON STAGE, in front of the whole
school, that the cat can't juggle (which would be a live, public disaster), the organizer hires a
clipboard assistant who checks EVERYONE beforehand, quietly, backstage. Anyone who doesn't match the
checklist gets stopped before the curtain even opens -- the mistake is caught early and privately,
not live and publicly. That assistant is exactly what `mypy` does for this code: it checks, by reading
the code (never running it), whether every object handed to `introduce()` actually has a matching
`.greet(name)` -- and if a `Cat` sneaks in, it's flagged before the program is ever run, not during it.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the audition checklist: "juggle 3 balls for 10s" | `HybridSearcher`/`Reranking`, each one method's exact signature |
| the show organizer, who only checks the clipboard | `RetrievalPipeline`, which only ever calls the Protocol's own methods |
| the magician who ALSO juggles | the real `HybridRetriever` -- has lots of other methods, but satisfies the ONE the pipeline needs |
| the robot with an extra, irrelevant "volume" dial | a real method with EXTRA optional parameters (`vector_weight`, `graph_weight`) that the narrower Protocol never mentions |
| the cat, who can only meow | any object missing the required method -- crashes with `AttributeError` |
| the backstage clipboard assistant | `mypy`, checking the match BEFORE the program runs |
| "nobody had to say out loud they were auditioning for THIS show" | `HybridRetriever` never writes `class HybridRetriever(HybridSearcher)` -- no inheritance link exists anywhere |

### 2. Why here specifically
`RetrievalPipeline` must be testable without a running Qdrant, Neo4j, or Cohere account. If its
constructor demanded the CONCRETE class `HybridRetriever` (as a type hint), a test could still pass
in a fake object at runtime -- Python never enforces type hints -- but it would look wrong to a
reader and to `mypy`, and nothing would make the fake's legitimacy explicit. `VectorSearcher`/
`GraphSearcher` (P5-M2) and `RerankClient` (P5-M3) already use this exact pattern; `HybridSearcher`/
`Reranking` are the third and fourth uses of it in this one small package.

### What breaks if this changes
- **Type-hint the constructor with the concrete classes** (`hybrid: HybridRetriever`): nothing
  breaks at runtime, but a fake passed in a test now structurally "shouldn't" be allowed by the
  hint, inviting a future reader to add real-class-specific logic that would silently break testing.
- **Make the Protocol require MORE than the real class provides** (e.g. a required, non-default
  `vector_weight` parameter): `HybridRetriever` would stop satisfying `HybridSearcher` and `mypy`
  would refuse to compile the composition -- a good demonstration that Protocols must describe only
  what's actually called.
- **Replace the Protocol with `Any`**: everything still runs, but a typo like
  `hybrid.retrieve_with_score` (missing the trailing "s") would only be caught by a crash at
  runtime, potentially deep inside a real, paid call.

### [Interview relevance: very high — "program to an interface, not an implementation" (the Dependency Inversion Principle) and Python's structural typing via `Protocol` are core AI-engineering interview topics for anyone who swaps vendors, mocks services, or tests systems that wrap paid APIs]

**Interview question:** *"How would you design a class that calls a vector database AND a
reranking service, so you can swap either one for a fake or a different vendor without changing the
class itself?"*
**A strong answer covers:** depend on an abstraction, not a concrete class (Dependency Inversion,
the "D" in SOLID); the consuming class only calls methods the abstraction defines; in Python,
`typing.Protocol` gives you this via structural typing, with NO explicit inheritance required, which
matters because you often want to accept objects from libraries that were never written with your
abstraction in mind; and that this is checked STATICALLY (by mypy), not enforced at runtime.

### 3. Minimal code that explains the concept
The complete script, [`d0_protocol_composition.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d0_protocol_composition.py) (run from the project root):
```python
# Minimal demo of C1: a Protocol lets RetrievalPipeline accept ANY object with the right
# methods -- not just the real HybridRetriever/Reranker -- which is what makes it testable
# without a database, and what "duck typing, formalized" means in practice.
from typing import Protocol  # the tool that lets us describe a SHAPE instead of a class


class CanGreet(Protocol):  # describes anything with a .greet(name) -> str method
    def greet(self, name: str) -> str: ...  # the only thing CanGreet cares about


class Robot:  # a class that never inherits from CanGreet and never mentions it
    def greet(self, name: str, volume: str = "normal") -> str:  # has an EXTRA optional param
        return f"BEEP BOOP HELLO {name.upper()}"  # a robot-flavoured greeting


class Human:  # a completely different, unrelated class
    def greet(self, name: str) -> str:  # matches CanGreet's shape exactly
        return f"Hi {name}, nice to meet you"  # a human-flavoured greeting


class Cat:  # a class that does NOT satisfy CanGreet at all
    def meow(self) -> str:  # has no greet method whatsoever
        return "meow"  # the only thing a Cat can say


def introduce(greeter: CanGreet, name: str) -> str:  # only cares that greeter CAN greet
    return greeter.greet(name)  # calls the method, not caring what class greeter really is


print(introduce(Robot(), "Ada"))  # a Robot satisfies CanGreet, despite never mentioning it
print(introduce(Human(), "Ada"))  # so does a Human, despite being unrelated to Robot

try:  # tries the thing mypy would refuse to even let you write
    introduce(Cat(), "Ada")  # a Cat has no greet method at all
except AttributeError as error:  # Python only discovers this at runtime, the hard way
    print(f"Cat crashes at RUNTIME: {error}")  # mypy would have caught this before running

# mypy accepts Robot even though its greet() has an EXTRA optional parameter (volume) --
# a concrete method with MORE optional parameters still satisfies a Protocol that calls
# it with FEWER arguments. This is exactly the relationship between HybridSearcher's
# retrieve_with_scores(query, k) and the real HybridRetriever.retrieve_with_scores(query,
# k, vector_weight=None, graph_weight=None).
```
Real output:
```
BEEP BOOP HELLO ADA
Hi Ada, nice to meet you
Cat crashes at RUNTIME: 'Cat' object has no attribute 'greet'
```
And the same file checked with `mypy` -- **before running it at all**:
```
practice codes\phase 5\m4_retrieval_pipeline_deep_dive\d0_protocol_composition.py:34: error: Argument 1 to "introduce" has incompatible type "Cat"; expected "CanGreet"  [arg-type]
```
`mypy` flags line 34 (the `Cat`) by name, and says nothing about `Robot` or `Human` on lines 32-33,
even though `Robot.greet` has an extra parameter `CanGreet` never mentions.

### The exact code I wrote (`src/retrieval/pipeline.py`)
```python
# src/retrieval/pipeline.py, lines 26-38, verbatim
 26 | class HybridSearcher(Protocol):
 27 |     def retrieve_with_scores(self, query: str, k: int) -> list[FusedChunk]: ...  # 🎯 the entire checklist: one method, this exact shape
 28 | 
 29 |     # anything that can return a fused, scored ranking for a question, such as HybridRetriever
 30 | 
 31 | 
 32 | class Reranking(Protocol):
 33 |     def rerank_with_scores(
 34 |         self, query: str, chunks: Sequence[DocumentChunk], k: int  # 🎯 the entire checklist: one method, this exact shape
 35 |     ) -> list[RerankedChunk]: ...
 36 | 
 37 |     # anything that can reorder a candidate list by relevance, such as Reranker
 38 | 
```
```python
# src/retrieval/pipeline.py, lines 96-115, verbatim
 96 |     def __init__(
 97 |         self,
 98 |         hybrid: HybridSearcher,  # 🎯 typed by the Protocol above, not by the concrete HybridRetriever class
 99 |         reranker: Reranking,  # 🎯 typed by the Protocol above, not by the concrete Reranker class
100 |         *,
101 |         pool_multiplier: int = DEFAULT_POOL_MULTIPLIER,
102 |         pool_floor: int = DEFAULT_POOL_FLOOR,
103 |     ) -> None:
104 |         _check_positive_int("pool_multiplier", pool_multiplier)
105 |         # refuses a multiplier that would ask the hybrid stage for nothing
106 |         _check_positive_int("pool_floor", pool_floor)
107 |         # refuses a floor that would ask the hybrid stage for nothing
108 |         self._hybrid = hybrid  # 🎯 just stored -- never inspected, never type-checked at runtime
109 |         # keeps the hybrid retriever every query is sent through first
110 |         self._reranker = reranker
111 |         # keeps the reranker every hybrid pool is sent through second
112 |         self._pool_multiplier = pool_multiplier
113 |         # keeps how many times k the hybrid stage is asked for
114 |         self._pool_floor = pool_floor
115 |         # keeps the smallest pool ever requested, regardless of k
```
**🎯 Interview-relevant lines:** 26-27 and 32-35 are the entire "checklist" -- one method
signature each. Line 108 is the moment a Protocol-typed value is simply stored as `self._hybrid`;
nothing here ever asks "is this really a `HybridRetriever`?"

### Canonical docs
- Python docs, [`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
- [PEP 544 -- Protocols: Structural subtyping](https://peps.python.org/pep-0544/)

### Micro-exercises
1. Add a `Dog` class to `d0` with `def greet(self, name): return f"Woof {name}"`. Predict, then
   run: does `introduce(Dog(), "Ada")` work? *Hint: check its shape against `CanGreet`'s checklist.*
2. Change `Human.greet` to require a SECOND parameter with no default: `def greet(self, name,
   language)`. Run `introduce(Human(), "Ada")` -- what crashes, and where? Then run `mypy` on the
   file -- does it catch the problem before you run anything?
3. Add `from typing import runtime_checkable` and `@runtime_checkable` above `CanGreet`. Try
   `isinstance(Human(), CanGreet)`. What does it print? Remove the decorator and try again --
   what error do you get instead? *Hint: Protocols are not isinstance-checkable by default.*
4. Open [`src/retrieval/pipeline.py`](../src/retrieval/pipeline.py) and find `HybridSearcher`.
   Write, on paper, a fake class with a DIFFERENTLY NAMED method (`search_with_scores` instead of
   `retrieve_with_scores`). Predict what happens if you pass it to `RetrievalPipeline`, then try it.
5. Run `mypy` on your modified file from exercise 2. Read its exact message. Does it name the
   missing parameter? *Hint: `.venv/Scripts/python -m mypy path/to/file.py`.*

**How it works internally.** At RUNTIME, Python does nothing special at all for a `Protocol` class
-- calling `some_object.some_method()` is ordinary duck typing, exactly as Python has always had;
it fails with `AttributeError` if the method is missing, Protocol or not. What Protocol adds exists
only for a STATIC type checker: `mypy` reads your source, without running it, and checks every
argument passed to a Protocol-typed parameter against that Protocol's declared method shapes.

### How to validate this yourself
Run `mypy` on `src/retrieval/pipeline.py` -- it passes clean. Then open
`tests/retrieval/test_pipeline.py` and note that `FakeHybrid`/`FakeReranker` never inherit from
`HybridSearcher`/`Reranking` at all, yet both mypy and `RetrievalPipeline` accept them everywhere a
real one is expected -- that's the concept being *used*, not just described. In a REPL, try
`isinstance(some_object, HybridSearcher)` and confirm it raises `TypeError` (Protocols aren't
runtime-checkable by default) unless `@runtime_checkable` is added.

### How to visualize this
Draw two boxes labelled "HybridSearcher (the checklist)" and "Reranking (the checklist)". Draw
THREE separate, unconnected boxes -- `HybridRetriever`, `FakeHybrid`, and the `real_hybrid` test
fixture -- each with an arrow pointing INTO the checklist box, labelled "satisfies structurally, no
inheritance line". This is deliberately NOT a normal class-inheritance diagram: there is no line
connecting these classes to each other or to the Protocol in the actual class hierarchy at all.

**You should now be able to explain:** why `RetrievalPipeline` never imports `HybridRetriever` or
`Reranker` by name in its type hints, what a Protocol enforces and when, and why `isinstance()`
doesn't normally work on one.

---

## C2 — Immutable schema design: frozen dataclass + tuple

### Do this first
Run [`d1_frozen_tuple_immutability.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d1_frozen_tuple_immutability.py).
**Predict first:** `leaky` is built with `@dataclass(frozen=True)`. Will
`leaky.chunks.append("SNUCK IN")` succeed, or raise an error?

### 1. Definition + analogy
**What it is.** A "schema" here means a precise, agreed shape for a piece of data -- which fields
exist, what type each is, and whether it can change after it's built. `RetrievalResult` is frozen
(you cannot reassign its fields after construction) and stores its chunks as a `tuple`, not a
`list`, so nothing about it can change once built -- not even by accident, three function calls away.

**Analogy.** A sealed exam-result envelope: the seal stops you swapping the whole grade sheet
inside for a different one. But if the grade sheet itself has loose sticky notes for each subject,
sealing the envelope does nothing to stop someone peeling one note off and sticking on a new grade
-- the seal only guards the envelope, not what's glued inside it.

### For a 14-year-old
You get a sealed exam-result envelope from school. It's taped shut -- you genuinely cannot open it
and swap the entire page inside for a different one; that's `frozen=True` in action, and it really
works for THAT. But now imagine the grade sheet inside isn't a solid printed card -- it's a page
with loose STICKY NOTES for each subject's grade, held on with weak glue. Even though the ENVELOPE
is sealed, you could still peel one sticky note off and stick on a different grade, and the seal
would never notice, because the seal only ever guarded against replacing the WHOLE page -- it has
no power over something merely glued to a page that's already safely inside.

Now imagine instead the grades are engraved directly into a solid metal plate. There's no sticky
note to peel off at all -- the whole thing is one solid piece, permanently marked. That's a `tuple`:
it has no method for adding, removing, or swapping an item in place. Combine the sealed envelope
(`frozen=True`) with the engraved metal plate (a `tuple`) and now you really do have something fully
locked: you can't swap the whole plate (the envelope's seal blocks that), and you can't scratch a
new mark into the plate either (a tuple simply doesn't support that).

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the sealed envelope | `@dataclass(frozen=True)` on `RetrievalResult` |
| "you can't swap the whole page" | `leaky.chunks = [...]` raises `FrozenInstanceError` |
| loose sticky notes glued onto the page | a `list` field: mutable, even inside a frozen dataclass |
| peeling one sticky note off | `leaky.chunks.append(...)` -- succeeds silently, frozen or not |
| the solid engraved metal plate | a `tuple` field: no in-place mutating methods exist at all |
| "there's no sticky note to peel -- it's solid metal" | `safe.chunks.append(...)` raises `AttributeError` |
| the records office trusting the sealed envelope completely | any later code that receives a `RetrievalResult` and assumes it never changed |

### 2. Why here specifically
`RetrievalResult` is meant to be handed off -- probably to a prompt-construction step, maybe a cache,
maybe an evaluation harness. If `results` were a plain `list`, ANY code that later receives this
object could silently `.append()`, `.sort()`, or `.pop()` on it, changing what the original caller
still believes it has. Since the whole point of `RankedChunk` is to be an honest audit trail --
exactly what the system decided, and why -- letting that trail be edited after the fact would defeat
its purpose.

### What breaks if this changes
- **`tuple[RankedChunk, ...]` -> `list[RankedChunk]`:** the dataclass still LOOKS frozen (`mypy`
  raises no error, reassignment is still blocked), but any caller can silently mutate the list in
  place -- a dangerous bug because it looks completely safe.
- **Remove `frozen=True`:** now `result.query = "something else"` would also silently succeed --
  the audit-trail guarantee disappears in both directions at once.
- **Add `slots=True` but not `frozen=True`** (this project uses both together elsewhere, e.g.
  `DocumentChunk`): saves memory, but does nothing for immutability -- the two features are
  unrelated and only happen to travel together by convention here.

### [Interview relevance: high — Python's mutability model (which built-in types are mutable, and that dataclass "frozen" is shallow, not deep) is a classic gotcha question, and matters directly in any system that caches or shares retrieval results]

**Interview question:** *"You mark a dataclass `frozen=True`, and one of its fields is a `list`.
Is the object now fully immutable?"*
**A strong answer covers:** `frozen=True` only intercepts `__setattr__`/`__delattr__` -- it stops
REASSIGNING a field to a new object, but cannot stop you mutating an object a field already points
to; a `list`, `dict`, or `set` field stays fully mutable in place even on a frozen dataclass; true
"deep" immutability needs every field to itself be immutable (`tuple`, `frozenset`, another frozen
dataclass, or a primitive) all the way down; naming "shallow vs. deep immutability" explicitly is a
strong signal.

### 3. Minimal code that explains the concept
The complete script, [`d1_frozen_tuple_immutability.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d1_frozen_tuple_immutability.py) (run from the project root):
```python
# Minimal demo of C2: a frozen dataclass stops you REASSIGNING a field, but only a tuple
# (not a list) stops you MUTATING what a field points to. This is why RetrievalResult uses
# tuple[RankedChunk, ...], not list[RankedChunk].
from dataclasses import FrozenInstanceError, dataclass  # the decorator and its own error type


@dataclass(frozen=True)  # looks fully immutable at first glance
class LeakyResult:
    query: str  # the question
    chunks: list[str]  # a LIST -- looks like the obvious choice


@dataclass(frozen=True)  # the real project's choice
class SafeResult:
    query: str  # the question
    chunks: tuple[str, ...]  # a TUPLE -- no method exists to change it in place


leaky = LeakyResult("q", ["A", "B"])  # builds a "frozen" result holding a list
safe = SafeResult("q", ("A", "B"))  # builds a genuinely frozen result holding a tuple

try:  # tries to REPLACE the whole field on the leaky one
    leaky.chunks = ["X"]  # reassigning a field
except FrozenInstanceError as error:  # frozen=True DOES stop this
    print(f"Reassigning leaky.chunks:   blocked -- {error}")

leaky.chunks.append("SNUCK IN")  # does NOT reassign the field, just mutates the list IN PLACE
print(f"leaky.chunks after append:  {leaky.chunks}  <- frozen=True did NOT stop this!")

try:  # tries the same trick on the tuple-based one
    safe.chunks.append("SNUCK IN")  # tuples have no .append method at all
except AttributeError as error:  # so this fails a different way, for a different reason
    print(f"safe.chunks.append(...):   blocked -- {error}")

try:  # tries to reassign the field on the safe one too, for completeness
    safe.chunks = ("X",)  # reassigning a field
except FrozenInstanceError as error:  # frozen=True stops this one too
    print(f"Reassigning safe.chunks:   blocked -- {error}")

print(f"\nleaky ended up as: {leaky}")  # shows the corrupted "frozen" object
print(f"safe stayed as:    {safe}")  # shows the tuple-based one is genuinely untouched
```
Real output:
```
Reassigning leaky.chunks:   blocked -- cannot assign to field 'chunks'
leaky.chunks after append:  ['A', 'B', 'SNUCK IN']  <- frozen=True did NOT stop this!
safe.chunks.append(...):   blocked -- 'tuple' object has no attribute 'append'
Reassigning safe.chunks:   blocked -- cannot assign to field 'chunks'

leaky ended up as: LeakyResult(query='q', chunks=['A', 'B', 'SNUCK IN'])
safe stayed as:    SafeResult(query='q', chunks=('A', 'B'))
```

### The exact code I wrote (`src/retrieval/pipeline.py`)
```python
# src/retrieval/pipeline.py, lines 40-69, verbatim
 40 | @dataclass(frozen=True, slots=True)
 41 | class RankedChunk:
 42 |     """One final result: the chunk, its place in the answer, and every stage's own verdict."""
 43 | 
 44 |     chunk: DocumentChunk
 45 |     # the chunk itself, exactly as the reranker returned it
 46 |     final_rank: int
 47 |     # this chunk's 1-based position in the pipeline's own final answer
 48 |     relevance_score: float
 49 |     # the reranker's score for this chunk, meaningful only next to scores from the same call
 50 |     hybrid_score: float
 51 |     # the fused vector+graph score the hybrid stage gave this chunk, before reranking
 52 |     hybrid_rank: int
 53 |     # this chunk's 1-based position in the pool the hybrid stage handed to the reranker
 54 |     vector_rank: int | None  # 🎯 int OR None -- vector search can miss a chunk entirely
 55 |     # this chunk's 1-based position in the vector list, or None if vector search missed it
 56 |     graph_rank: int | None
 57 |     # this chunk's 1-based position in the graph list, or None if graph retrieval missed it
 58 |     vector_score: float | None
 59 |     # the raw cosine similarity vector search gave it, or None if vector search missed it
 60 | 
 61 | 
 62 | @dataclass(frozen=True, slots=True)
 63 | class RetrievalResult:
 64 |     """The answer to one query: the question itself, plus every chunk that answers it, ranked."""
 65 | 
 66 |     query: str  # 🎯 NOT list -- see C2's whole point
 67 |     # the question this result answers, exactly as it was asked
 68 |     results: tuple[RankedChunk, ...]  # 🎯 the exact reason, spelled out in the comment
 69 |     # every returned chunk, best first; a tuple so the frozen result is immutable all the way down
```
**🎯 Interview-relevant lines:** line 68's own comment states the rule this whole concept is built
on: "a tuple so the frozen result is immutable all the way down." That one line is the entire design
decision, in the code itself, not just in a doc.

### Canonical docs
- Python docs, [dataclasses: frozen instances](https://docs.python.org/3/library/dataclasses.html#frozen-instances)
- Python docs, [`tuple` vs `list`](https://docs.python.org/3/library/stdtypes.html#sequence-types-list-tuple-range)

### Micro-exercises
1. Add a third class to `d1`, `MedResult`, with `chunks: tuple` but WITHOUT `frozen=True`. Predict:
   can you reassign `.chunks` now? Run it and check. *Hint: which feature stops reassignment, and
   which stops in-place mutation -- are they the same feature?*
2. Try `leaky.chunks[0] = "REPLACED"` (item assignment, not `.append`). Then try the same on
   `safe.chunks[0] = "REPLACED"`. What's different, and why?
3. Open [`src/ingestion/schemas.py`](../src/ingestion/schemas.py) and find
   `EmbeddedChunk.vector: tuple[float, ...]`. Read its own comment about why it's a tuple. Does it
   match what you just learned?
4. In a scratch script, `from src.retrieval.pipeline import RankedChunk` and build one with made-up
   values. Try changing `.final_rank` afterward. Does the error name the field?
5. Add `eq=False` to one `@dataclass(...)` in your scratch copy and compare two otherwise-identical
   instances with `==`. What changes? *Hint: dataclasses auto-generate `__eq__` by default.*

**How it works internally.** `@dataclass(frozen=True)` generates a custom `__setattr__` that raises
`FrozenInstanceError` for every attempt to set an attribute after construction (it uses
`object.__setattr__` internally, once, during `__init__`, to get around its own block just long
enough to build the object). It has no idea what TYPE any field holds. `list.append()` never goes
near `__setattr__` at all -- it calls a method ON the list object itself, which the dataclass's
override never sees. Two unrelated mechanisms (setting an attribute vs. calling a method on what
that attribute points to) are involved, and `frozen=True` only ever touches the first one.

### How to validate this yourself
In a REPL: `import dataclasses; dataclasses.fields(RankedChunk)` lists every field and its declared
type -- the schema's real shape, not what you assume from reading the source. `dataclasses.asdict(a
_retrieval_result)` gives you a plain nested dict/list, handy for printing or JSON. Then deliberately
try `answer.results.append(x)` and `answer.results[0] = x` on a real `RetrievalResult` from a
hermetic test and confirm both raise, proving the tuple choice is load-bearing, not decorative.

### How to visualize this
Draw `RetrievalResult` as a locked box with one slot, "results". Inside that slot, draw a ROW of
smaller locked boxes (the tuple's items), each containing a `RankedChunk`'s 8 fields. Now draw a
second version using a list: the row of boxes has a dashed, open lid, showing they can still be
reached into even though the outer box is "locked".

**You should now be able to explain:** the difference between a dataclass being frozen and its DATA
being immutable, and why this codebase always pairs `frozen=True` with `tuple`, never `list`.

---

## C3 — The pool-size formula at the composition seam

### Do this first
Run [`d2_pool_size_formula.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d2_pool_size_formula.py).
**Predict first:** for `k=1`, will `k * multiplier` (3) or `floor` (20) decide the pool size? For
`k=10`?

### 1. Definition + analogy
**What it is.** `pool_size = max(k * multiplier, floor)` is the one arithmetic decision at the SEAM
between the two stages: how many candidates should stage 1 (hybrid retrieval) produce, so stage 2
(reranking) has real material to work with, even though the caller only wants `k` results back?

**Analogy.** A head chef gets an order for "your best 3 dishes tonight." A sensible chef doesn't
cook exactly 3 and hope -- they cook a wider spread (say 9: `k * 3`) and let a taster pick the best 3
from those 9. But if someone orders just 1 dish, cooking only 3 (`1 * 3`) to choose from feels too
thin -- so the kitchen has a house rule: never prepare fewer than 20, no matter how small the order.
`max()` picks whichever of "3x the order" or "the house minimum" is bigger.

### For a 14-year-old
You're the head chef. A ticket says "3 of your best dishes" -- that's `k=3`. You don't cook exactly
3 and hope they're the actual best 3 you could have made; you cook a WIDER spread first, say 9
dishes (`k` times a multiplier of 3), and let your head taster pick the genuine best 3 out of those
9. But then a ticket comes in for just ONE dish (`k=1`). Cooking only 3 (`1 x 3`) for the taster to
choose from feels way too thin for a fair shot at greatness -- so the kitchen has a standing HOUSE
RULE: "no matter how tiny the order, ALWAYS prepare at least 20 dishes for the taster." `max(...)`
is simply: whichever number is bigger wins, the "3x the order" number or the "house minimum of 20."

Here's the twist, and it's a real one, not made up for the story: you might assume "cooking MORE
dishes can only help the taster pick well." That turns out to be false. The real, measured data
from this project shows the correct dish's position, WITHIN the pool, was a good rank 3 when the
kitchen prepared 10 dishes -- but got WORSE, dropping to rank 15, once the kitchen prepared 20! More
choices sometimes buries a good answer under a pile of new, merely-okay ones that happen to look
appealing together. The house minimum guarantees there's always ENOUGH to choose from -- it does
NOT guarantee that more is easier to judge.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "3 of your best dishes" | `k` |
| "cook 3 times as many as ordered" | `k * pool_multiplier` |
| the house minimum of 20 | `pool_floor` (default 20) |
| "whichever is bigger wins" | Python's `max(...)` |
| the head taster | `Reranker`, picking the final `k` from the pool |
| more dishes sometimes confusing the taster | the CEO answer's real, MEASURED rank getting WORSE from pool 10 to pool 20 |
| the chef's tempting but WRONG assumption | the docstring I almost wrote before re-checking the measured table |

### 2. Why here specifically
Neither existing class owns this number. `HybridRetriever` doesn't know a reranker exists downstream
of it. `Reranker` explicitly refuses to choose its own pool size (P5-M3's own decision D4) -- it
takes whatever it's handed. `RetrievalPipeline` is the only place both stages meet, so it is the only
place this decision *can* live.

### What breaks if this changes
- **`+` instead of `max()`** (`k*multiplier + floor`): at `k=10` this gives `30 + 20 = 50` instead
  of `30` -- always more expensive than intended, turning a safety minimum into an unconditional tax.
- **`min()` instead of `max()`**: at `k=1`, `min(3, 20) = 3` -- exactly the starvation problem the
  floor exists to prevent comes right back.
- **Remove the floor** (`pool_size = k * multiplier`): at `k=1`, pool=3, which the real measurements
  already show is far too small for several real questions on this corpus.

### [Interview relevance: high — "how many candidates do you retrieve before reranking" is one of the most common RAG system-design interview questions, and answering "I'd measure recall@N on labelled data" is an immediate differentiator]

**Interview question:** *"How would you decide how many candidates to retrieve before reranking?
Would you use the same number for every k?"*
**A strong answer covers:** the pool must be at least `k`, but usually needs real margin above it,
because reranking can only reorder what it's given (a recall ceiling); a common pattern is `max(k *
constant, floor)` so small requests aren't starved; the numbers should come from measuring recall@N
on labelled data, not guesswork; and the sharper point -- a bigger pool is NOT automatically safer,
because it can dilute a strong candidate among more numerous, merely-mediocre ones.

### 3. Minimal code that explains the concept
The complete script, [`d2_pool_size_formula.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d2_pool_size_formula.py) (run from the project root):
```python
# Minimal demo of C3: pool_size = max(k * multiplier, floor). Shows why max() (not + or
# min()), and reuses the REAL measured pool-rank table from P5-M3 to show the floor's limit.
DEFAULT_MULTIPLIER = 3  # the real project's default, from pipeline.py
DEFAULT_FLOOR = 20  # the real project's default, from pipeline.py


def pool_size(k, multiplier=DEFAULT_MULTIPLIER, floor=DEFAULT_FLOOR):
    return max(k * multiplier, floor)  # the exact formula used at pipeline.py line 124


print(f"{'k':<4}{'k*multiplier':<16}{'pool_size':<12}decided by")  # column headings
for k in (1, 2, 5, 7, 10, 20):  # a spread of k values, small to large
    scaled = k * DEFAULT_MULTIPLIER  # what the multiplier alone would give
    size = pool_size(k)  # what the real formula gives
    decided_by = "the FLOOR" if scaled < DEFAULT_FLOOR else "the multiplier"  # which term won
    print(f"{k:<4}{scaled:<16}{size:<12}{decided_by}")

# MEASURED on the real corpus (see notes/step-5.3-reranker.md): the CEO question's accepted
# chunk's rank INSIDE the hybrid pool, at different pool sizes. A floor guarantees CAPACITY
# (pool_size >= k), it does NOT guarantee the answer is easier to find in a bigger pool.
CEO_RANK_BY_POOL = {3: 2, 10: 3, 20: 15, 30: 21, 50: 9}  # real, measured data -- not invented
print("\npool size -> CEO answer's rank inside that pool (lower is better; MEASURED, not simulated):")
for size, rank in CEO_RANK_BY_POOL.items():
    flag = "  <- the DEFAULT pool size (20) is actually one of the WORSE ones here" if size == 20 else ""
    print(f"  {size:<4} -> rank {rank}{flag}")
```
Real output:
```
k   k*multiplier    pool_size   decided by
1   3               20          the FLOOR
2   6               20          the FLOOR
5   15              20          the FLOOR
7   21              21          the multiplier
10  30              30          the multiplier
20  60              60          the multiplier

pool size -> CEO answer's rank inside that pool (lower is better; MEASURED, not simulated):
  3    -> rank 2
  10   -> rank 3
  20   -> rank 15  <- the DEFAULT pool size (20) is actually one of the WORSE ones here
  30   -> rank 21
  50   -> rank 9
```
The `CEO_RANK_BY_POOL` numbers are copied straight from `notes/step-5.3-reranker.md`'s real,
measured table -- not invented for this demo.

### The exact code I wrote (`src/retrieval/pipeline.py`)
```python
# src/retrieval/pipeline.py, lines 20-23, verbatim
 20 | DEFAULT_POOL_MULTIPLIER = 3  # 🎯 constructor default: the multiplier
 21 | # how many times k the hybrid stage is asked for, before the pool floor is applied
 22 | DEFAULT_POOL_FLOOR = 20  # 🎯 constructor default: the floor
 23 | # the smallest candidate pool ever requested, regardless of how small k is
```
```python
# src/retrieval/pipeline.py, lines 117-127, verbatim
117 |     def retrieve(self, query: str, k: int) -> RetrievalResult:
118 |         """The k best chunks for `query`: hybrid-retrieved, then reranked, best first."""
119 |         _check_k(k)  # 🎯 guard clauses run FIRST -- see C5
120 |         # refuses a k that is not a positive integer before either stage is called
121 |         _check_query(query)
122 |         # refuses an empty question before either stage is called
123 | 
124 |         pool_size = max(k * self._pool_multiplier, self._pool_floor)  # 🎯 THE formula
125 |         # works out how many candidates the hybrid stage should hand to the reranker
126 |         pool = self._hybrid.retrieve_with_scores(query, pool_size)  # 🎯 the pool size becomes the k passed to the hybrid stage
127 |         # asks the hybrid stage for its top `pool_size` fused, scored candidates
```
**🎯 Interview-relevant lines:** line 124 is the entire decision, in one expression. Line 126 shows
exactly where that number leaves this file and becomes someone else's problem (`HybridRetriever`'s
own `k`, which P5-M2 then multiplies AGAIN internally by its own `candidate_multiplier`).

### Canonical docs
- [notes/step-5.3-reranker.md](../notes/step-5.3-reranker.md) -- this project's own measured pool
  table, the primary source for this concept's central finding.
- Cohere, [Reranking best practices](https://docs.cohere.com/docs/reranking-best-practices)

### Micro-exercises
1. Change `DEFAULT_FLOOR` to 5 in `d2`. Compute the table for k=1..10 BY HAND first, then run and
   check. *Hint: at what k does `k*3` overtake a floor of 5?*
2. Change the formula to `+` instead of `max`. Recompute the table. Where does it diverge most from
   the original? *Hint: exactly where the floor previously "won".*
3. Using `CEO_RANK_BY_POOL`, write one sentence arguing FOR `pool_floor=20` and one AGAINST it,
   using the actual numbers.
4. Open `tests/retrieval/test_pipeline.py` and find
   `test_the_pool_floor_applies_when_k_times_the_multiplier_is_smaller`. Predict `calls[0]` before
   reading the assertion.
5. Design, on paper only, a different formula that could avoid "bigger pool sometimes hurts." What
   extra information would it need that `max(k*mult, floor)` never looks at?

**How it works internally.** Pure arithmetic -- `max()` evaluates both arguments (one multiplication,
one stored integer) and returns the larger. The only thing worth tracing further is where the RESULT
goes: it becomes the literal `k` argument to `HybridRetriever.retrieve_with_scores`, which (from
P5-M2) multiplies IT by its own internal `candidate_multiplier` before asking the vector store and
graph retriever for candidates -- one user-facing `k` fans out into several different numbers by the
time it reaches the bottom of the stack.

### How to validate this yourself
In a REPL: `from src.retrieval.pipeline import DEFAULT_POOL_MULTIPLIER, DEFAULT_POOL_FLOOR; print(...)`
confirms the real, shipped defaults. Build a tiny fake hybrid stage whose `retrieve_with_scores`
just PRINTS the `k` it receives, wire it into a real `RetrievalPipeline`, and call `.retrieve("q",
7)` -- watch the actual computed pool size fly by, proving the formula ran, not just that the source
"looks right."

### How to visualize this
A simple chart with `k` on the x-axis and `pool_size` on the y-axis: a FLAT line at height 20 until
`k` reaches about 7 (`20 / 3 ≈ 6.67`), then a rising diagonal after that. The "elbow" where the floor
stops mattering is visually obvious.

**You should now be able to explain:** why neither existing class could have made this decision
alone, what `max(k*mult, floor)` actually guarantees (capacity, not quality), and why "more
candidates" is not automatically safer.

---

## C4 — Joining two ranked lists by a shared key

### Do this first
Run [`d3_join_by_key.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d3_join_by_key.py).
**Predict first:** will the naive and indexed joins produce the SAME final answer? Roughly how much
faster do you expect the indexed one to be -- 2x? 20x? 200x?

### 1. Definition + analogy
**What it is.** A "join" (the word comes straight from databases) means: two separate lists both
refer to the same "things" (chunks), each carrying DIFFERENT information about them, and you want
one combined list with BOTH pieces per item. The slow way searches the whole of list A for every
item in list B. The fast way builds a dictionary from list A first, so finding "the record for this
id" becomes one lookup instead of a search.

**Analogy.** You have List A: student ID -> locker number. List B: student ID -> exam grade
(handed to you separately, in a different order). You need ONE list of ID -> locker AND grade.

### For a 14-year-old
Picture a school with 4,000 students and two separate paper lists. List A has every student's ID
number next to their locker number. List B, handed to you separately, has every student's ID next
to their final exam grade. Your job: build ONE list showing, for every student, BOTH their locker
AND their grade.

The SLOW way: for every name on List B, you pick up List A and read it from the very top, line by
line, checking "is this the same ID?" until you find a match or run out of names. With 4,000 names
on each list, the worst case is 4,000 x 4,000 = 16 MILLION checks -- like re-reading the entire phone
book from page one, separately, for every single person you look up.

The FAST way: first, spend ONE pass through List A building yourself a set of labelled DRAWERS, one
drawer per ID number, and file each student's locker number into their own drawer. That costs one
pass, 4,000 steps. NOW go through List B: for each ID, walk STRAIGHT to that drawer and pull the
answer out instantly -- no searching. Another 4,000 steps. Total: 8,000 steps instead of 16 million.
The "drawers" are exactly what a Python `dict` is: a structure built so "find the thing with this
exact label" is (almost) instant, no matter how many drawers exist.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| List A (ID -> locker) | `pool`, the hybrid stage's output |
| List B (ID -> grade) | `reranked`, the reranker's output, in a DIFFERENT order |
| the student ID number | `chunk.chunk_id`, the key both lists share |
| re-reading List A from the top, every time | `join_naive`'s nested loop |
| building labelled drawers, one pass | `hybrid_by_id = {item.chunk.chunk_id: item for item in pool}`, line 128 |
| walking straight to the right drawer | `hybrid_by_id[item.chunk.chunk_id]` -- an average-case O(1) lookup |
| the final list (locker + grade, per student) | each `RankedChunk`, carrying both stages' data |

### 2. Why here specifically
`HybridRetriever` and `Reranker` are two totally independent classes with no knowledge of each
other, each producing its OWN result type (`FusedChunk`, `RerankedChunk`). The only thing they
share is that both describe the same underlying chunks, identifiable by `chunk_id`. Building one
final record with BOTH stages' information requires explicitly reconnecting them by that shared id.

### What breaks if this changes
- **Search `pool` in a loop instead of building `hybrid_by_id`:** correctness is unaffected, but for
  a pool of 100 candidates reranked to 10, that's up to 1,000 comparisons instead of about 110 --
  small here, but the PATTERN gets dramatically worse as pools grow, for no benefit at all.
- **Key the dictionary by `chunk.text` instead of `chunk_id`:** two different chunks sharing
  identical text would silently merge -- `chunk_id` exists specifically to be a stable, unique
  identifier for exactly this reason (see P2's chunk-id design).
- **Treat `pool` (a list) like a dict directly** (`pool[item.chunk.chunk_id]`): raises `TypeError`
  immediately -- a plain list simply cannot be indexed by a string, so this mistake can't go unnoticed.

### [Interview relevance: very high — "combine two lists by a shared id efficiently" is an extremely common coding-interview pattern; recognizing "index once, then look up many times" over "search repeatedly" is one of the most transferable skills in this whole file]

**Interview question:** *"You have two lists of records sharing an ID field and need to combine
matching records. What's the naive approach's complexity, and how do you improve it?"*
**A strong answer covers:** naive nested-loop search is O(n x m) worst case; building a dictionary
from one list costs O(n), and each lookup during the second pass is O(1) on average, for O(n+m)
total; this trades a little memory (the dictionary) for a large time saving; bonus: noting Python
dict lookups are "average O(1)," not worst-case, because of hashing (rare collisions aside).

### 3. Minimal code that explains the concept
The complete script, [`d3_join_by_key.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d3_join_by_key.py) (run from the project root):
```python
# Minimal demo of C4: joining two lists by a shared id. A naive nested loop is O(n*m); a
# dict index turns it into two O(n) passes. N is big enough to actually SEE the time gap.
import time  # measures how long each approach takes

N = 4000  # how many items in each list

hybrid_pool = [{"id": f"chunk_{i}", "hybrid_score": i / 1000} for i in range(N)]  # stage 1's output
reranked = [{"id": f"chunk_{i}", "relevance_score": 1.0 - i / N} for i in reversed(range(N))]  # stage 2's output, in a DIFFERENT order


def join_naive(reranked, pool):
    joined = []  # will hold one merged record per reranked item
    for r in reranked:  # for each reranked item...
        for p in pool:  # ...scan the ENTIRE pool looking for a matching id
            if p["id"] == r["id"]:  # found the matching hybrid record
                joined.append({**r, "hybrid_score": p["hybrid_score"]})  # merges the two dicts
                break  # stops scanning once a match is found
    return joined  # hands back every merged record


def join_by_index(reranked, pool):
    by_id = {p["id"]: p for p in pool}  # ONE pass to build an id -> record lookup table
    return [{**r, "hybrid_score": by_id[r["id"]]["hybrid_score"]} for r in reranked]  # ONE pass using the table


started = time.perf_counter()  # starts the naive timer
naive_result = join_naive(reranked, hybrid_pool)  # runs the naive nested-loop join
naive_seconds = time.perf_counter() - started  # stops the naive timer

started = time.perf_counter()  # starts the indexed timer
indexed_result = join_by_index(reranked, hybrid_pool)  # runs the dict-indexed join
indexed_seconds = time.perf_counter() - started  # stops the indexed timer

print(f"naive nested-loop join:  {naive_seconds * 1000:8.2f} ms  (up to {N * N:,} comparisons)")  # the slow way
print(f"dict-indexed join:       {indexed_seconds * 1000:8.2f} ms  ({2 * N:,} steps total)")  # the pipeline.py way
print(f"results identical:       {naive_result == indexed_result}")  # proves both give the same answer
print(f"indexed join was {naive_seconds / indexed_seconds:.0f}x faster")  # the measured speed-up
```
Real output:
```
naive nested-loop join:    647.99 ms  (up to 16,000,000 comparisons)
dict-indexed join:           2.15 ms  (8,000 steps total)
results identical:       True
indexed join was 301x faster
```

### The exact code I wrote (`src/retrieval/pipeline.py`)
```python
# src/retrieval/pipeline.py, lines 128-150, verbatim
128 |         hybrid_by_id = {item.chunk.chunk_id: item for item in pool}  # 🎯 ONE pass, builds the drawers
129 |         # indexes the pool by chunk_id, so each reranked chunk's hybrid data can be found again
130 | 
131 |         chunks = [item.chunk for item in pool]  # 🎯 the chunk order is preserved from the pool, unrelated to the dict
132 |         # keeps only the chunk objects, in hybrid order, for the reranker to score
133 |         reranked = self._reranker.rerank_with_scores(query, chunks, k)
134 |         # asks the reranker to pick and order the best k chunks of the pool
135 | 
136 |         results = tuple(
137 |             RankedChunk(
138 |                 chunk=item.chunk,
139 |                 final_rank=rank,
140 |                 relevance_score=item.relevance_score,
141 |                 hybrid_score=hybrid_by_id[item.chunk.chunk_id].score,  # 🎯 an O(1) lookup, not a search
142 |                 hybrid_rank=item.input_rank,
143 |                 vector_rank=hybrid_by_id[item.chunk.chunk_id].vector_rank,  # 🎯 the SAME lookup, reused for a different field
144 |                 graph_rank=hybrid_by_id[item.chunk.chunk_id].graph_rank,  # 🎯 the SAME lookup, reused again
145 |                 vector_score=hybrid_by_id[item.chunk.chunk_id].vector_score,  # 🎯 the SAME lookup, reused a third time
146 |             )
147 |             for rank, item in enumerate(reranked, start=1)
148 |         )
149 |         # builds one RankedChunk per reranked result, joining each one back to its hybrid record
150 |         return RetrievalResult(query=query, results=results)
```
**🎯 Interview-relevant lines:** line 128 is the entire "build the drawers" step -- one dict
comprehension. Lines 141-145 are the entire "walk straight to the drawer" step, done four times per
result because each `RankedChunk` needs four different fields out of the SAME hybrid record.

### Canonical docs
- Python docs, [Dictionaries](https://docs.python.org/3/tutorial/datastructures.html#dictionaries)
- [Python wiki: Time Complexity](https://wiki.python.org/moin/TimeComplexity) (see `dict`)

### Micro-exercises
1. In `d3`, change `N` from 4000 to 500, then to 8000. Predict how the TIMING RATIO changes before
   running. *Hint: the naive cost grows with N², the indexed one with N.*
2. Modify `join_naive` to also `break` out of the OUTER loop once every reranked item is matched.
   Does the measured time change much at N=4000? Why or why not?
3. Remove the inner `break` from `join_naive` entirely. Does the RESULT change? Does the TIME
   change? Why?
4. Deliberately give two pool entries the same fake `id`. Run `join_by_index` -- which one wins,
   and why? *Hint: what happens when you insert two values under one dict key, one after another?*
5. In `src/retrieval/pipeline.py`, add a temporary `print(len(hybrid_by_id), len(pool))` right after
   line 128 in a scratch copy, run a hermetic test through it, and confirm the two numbers match.

**How it works internally.** Python's `dict` is a hash table: `hybrid_by_id[some_id]` computes a
numeric hash of the string, uses it to jump almost directly to the right internal storage slot, and
checks that the key there truly matches (handling rare hash collisions). This is why dict lookups
don't get meaningfully slower as the dictionary grows -- you're computing an address, not searching.
Building `hybrid_by_id` does this hashing-and-storing once per pool item; every later lookup reuses
that work instead of repeating it.

### How to validate this yourself
Wrap the real join code from `retrieve()` with `time.perf_counter()` (temporarily, in a scratch
copy) at a much larger fake pool size, and confirm it stays fast as the pool grows. In a REPL, build
`hybrid_by_id` from a small fake pool and `print()` it directly -- you should see chunk_id strings as
keys and `FusedChunk` objects as values. Then pass a `chunk_id` that ISN'T in the pool into
`hybrid_by_id[...]` in a scratch script and watch the `KeyError` -- a failure that can never happen
in the real pipeline (since `chunks` always comes FROM `pool`), but seeing it clarifies exactly what
invariant is being relied on.

### How to visualize this
Draw two parallel columns of labelled boxes ("chunk_9", "chunk_3", "chunk_9"...) -- LEFT in the
hybrid's own order, RIGHT in the reranker's different order -- with a MIDDLE column of "drawers"
(the dict). Draw an arrow from every left box INTO its matching drawer, then an arrow from every
right box back OUT of that same drawer into a new, final column. The left and right orders visibly
don't need to match at all.

**You should now be able to explain:** why the pipeline builds `hybrid_by_id` instead of searching
the pool directly, what complexity that saves, and what a Python dict does internally on a lookup.

---

## C5 — Fail-fast validation before any expensive call

### Do this first
Run [`d4_validate_before_calling.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d4_validate_before_calling.py).
**Predict first:** in `validate_last(-1)`, will "...calling the REAL hybrid+rerank service" print
BEFORE the error, AFTER it, or not at all?

### 1. Definition + analogy
**What it is.** A "guard clause" is a check at the very TOP of a function that immediately stops
execution if something is wrong, before any real work happens. "Fail fast" means: if something is
going to fail anyway, make it fail as early and cheaply as possible, rather than doing expensive
work first and discovering the problem at the end.

**Analogy.** Before driving to a shop for a $30 ingredient and spending half a day baking a cake,
glance at the recipe card. If it says "serves -3 people," stop right there -- before spending a cent.

### For a 14-year-old
You're about to bake a big cake for a party. It needs one special, expensive ingredient from a shop
across town, and the whole process (drive there, buy it, drive back, mix, bake for an hour) takes
half a day. Right before you leave, you glance at the recipe card and see it says "serves -3
people" -- obviously nonsense. A SENSIBLE baker stops immediately, right at the card, and fixes the
recipe first, before spending a single cent or minute. A FOOLISH baker drives across town, buys the
ingredient, drives back, mixes everything, bakes it for an hour... and only THEN, trying to cut
"-3 servings" of cake, realizes the recipe made no sense all along -- having already wasted the
money, the time, and the ingredients for nothing. "Validate first" is the sensible baker's habit:
check the recipe (the arguments) before the shopping trip (calling `HybridRetriever`, then
`Reranker`, which spends real Cohere API quota every single time it runs).

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "serves -3 people" on the recipe card | a bad `k` (like `-1`) or an empty `query` |
| glancing at the card FIRST | `_check_k(k)` / `_check_query(query)`, lines 119-122, run before anything else |
| driving to the shop, buying the ingredient | calling `self._hybrid.retrieve_with_scores(...)` |
| baking for an hour | calling `self._reranker.rerank_with_scores(...)` -- costs real Cohere quota |
| discovering the mistake only at the very end | `validate_last()` in the demo |
| "no money or time wasted" | zero calls recorded in the pipeline's fakes, PROVEN by a real test |

### 2. Why here specifically
`RetrievalPipeline.retrieve()` is the first place a caller's raw, unchecked input enters this whole
three-stage system. Letting a bad `k` or empty `query` through would immediately trigger a real
network call to a vector database, then another real call to Cohere -- both wasted, and the Cohere
one costs actual money and counts against a monthly quota (P5-M3: 1,000 calls/month on a trial key).

### What breaks if this changes
- **Remove the checks entirely:** `k=0` sails into `pool_size = max(0*3, 20) = 20` -- this specific
  bug is masked by the floor! -- fetches a real pool of 20, asks the reranker for `top_n =
  min(0, 20) = 0`, and returns an empty `RetrievalResult` with NO error at all: silently wrong
  instead of loudly wrong, which is much harder to debug later.
- **Move the checks to the END of `retrieve()`** (mirroring `validate_last`): both real network
  calls already happened, and quota was already spent, by the time the caller learns their input
  was bad all along.
- **Check only `query`, not `k` (or the reverse):** whichever check is skipped lets its bad input
  through until something further downstream happens to catch it, or doesn't.

### [Interview relevance: medium-high — "fail fast" / validating at a function's boundary is a basic but frequently-tested discipline, especially for anyone wrapping paid, rate-limited, or slow external APIs, which is most of modern AI engineering]

**Interview question:** *"Where should you validate a function's arguments, and why does it matter
more for a function that calls a paid external API?"*
**A strong answer covers:** validate at the very top, before any side-effecting or expensive work
("guard clauses", "fail fast"); for a function wrapping a paid/rate-limited API, an unvalidated bad
argument wastes real money and quota, and buries the real error inside a less-informative
third-party failure; bonus: some systems ALSO re-validate at every layer (defense in depth) even
though it looks redundant, because each layer might be called independently, from a context where an
earlier check never ran.

### 3. Minimal code that explains the concept
The complete script, [`d4_validate_before_calling.py`](../practice%20codes/phase%205/m4_retrieval_pipeline_deep_dive/d4_validate_before_calling.py) (run from the project root):
```python
# Minimal demo of C5: validating BEFORE calling either stage means a bad argument costs
# ZERO calls to the (possibly expensive/paid) stages, instead of one wasted call.
def check_k_first(k):  # the exact rule pipeline.py's _check_k uses
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:  # not a plain positive integer
        raise ValueError(f"k must be a positive integer, got {k!r}")  # refuses immediately


class ExpensiveStage:  # stands in for HybridRetriever/Reranker -- imagine a real network call
    def __init__(self, name):
        self.name = name  # remembers which stage this is, for the printed trace
        self.calls = 0  # counts how many times it was actually invoked

    def run(self, k):
        self.calls += 1  # counts this call
        print(f"  ...calling the REAL {self.name} service (call #{self.calls})")  # simulates real cost
        return f"{self.name} result for k={k}"  # a fake result


def validate_first(k):  # the pattern pipeline.py actually uses
    check_k_first(k)  # fails HERE if k is bad, before anything else runs
    stage = ExpensiveStage("hybrid+rerank")  # only ever reached if k was valid
    return stage.run(k), stage.calls  # runs the (now trusted) call


def validate_last(k):  # the REJECTED pattern -- call first, check after
    stage = ExpensiveStage("hybrid+rerank")  # runs UNCONDITIONALLY
    result = stage.run(k)  # the expensive call already happened by this point
    check_k_first(k)  # only NOW does the bad k get noticed
    return result, stage.calls


print("validate_first(-1):")  # tries the good pattern with a bad k
try:
    validate_first(-1)  # should fail before any call is made
except ValueError as error:
    print(f"  refused before any call was made: {error}")

print("\nvalidate_last(-1):")  # tries the rejected pattern with the same bad k
try:
    validate_last(-1)  # the expensive call above already ran once by the time this raises
except ValueError as error:
    print(f"  refused, but only AFTER the call above already ran: {error}")
```
Real output:
```
validate_first(-1):
  refused before any call was made: k must be a positive integer, got -1

validate_last(-1):
  ...calling the REAL hybrid+rerank service (call #1)
  refused, but only AFTER the call above already ran: k must be a positive integer, got -1
```

### The exact code I wrote (`src/retrieval/pipeline.py`)
```python
# src/retrieval/pipeline.py, lines 72-90, verbatim
 72 | def _check_k(k: int) -> None:
 73 |     if isinstance(k, bool) or not isinstance(k, int) or k < 1:  # 🎯 not a plain positive integer -- bool is rejected too, since bool is secretly an int in Python
 74 |         # rejects anything that is not a plain positive integer
 75 |         raise ValueError(f"k must be a positive integer, got {k!r}")
 76 |         # names the argument and the value so the caller can see what was wrong
 77 | 
 78 | 
 79 | def _check_query(query: str) -> None:
 80 |     if not isinstance(query, str) or not query.strip():  # 🎯 not a string, or empty, or only whitespace
 81 |         # rejects a value that is not a string, or is empty or only whitespace
 82 |         raise ValueError(f"query must be a non-empty string, got {query!r}")
 83 |         # names the argument and the value so the caller can see what was wrong
 84 | 
 85 | 
 86 | def _check_positive_int(name: str, value: int) -> None:
 87 |     if isinstance(value, bool) or not isinstance(value, int) or value < 1:
 88 |         # rejects anything that is not a plain integer of at least 1
 89 |         raise ValueError(f"{name} must be a positive integer, got {value!r}")
 90 |         # names the argument and the value so the caller can see what was wrong
```
```python
# src/retrieval/pipeline.py, lines 117-134, verbatim
117 |     def retrieve(self, query: str, k: int) -> RetrievalResult:
118 |         """The k best chunks for `query`: hybrid-retrieved, then reranked, best first."""
119 |         _check_k(k)  # 🎯 the FIRST thing retrieve() does
120 |         # refuses a k that is not a positive integer before either stage is called
121 |         _check_query(query)  # 🎯 the SECOND thing -- still before any real work
122 |         # refuses an empty question before either stage is called
123 | 
124 |         pool_size = max(k * self._pool_multiplier, self._pool_floor)  # 🎯 only reached if both checks passed
125 |         # works out how many candidates the hybrid stage should hand to the reranker
126 |         pool = self._hybrid.retrieve_with_scores(query, pool_size)  # 🎯 the first EXPENSIVE call
127 |         # asks the hybrid stage for its top `pool_size` fused, scored candidates
128 |         hybrid_by_id = {item.chunk.chunk_id: item for item in pool}
129 |         # indexes the pool by chunk_id, so each reranked chunk's hybrid data can be found again
130 | 
131 |         chunks = [item.chunk for item in pool]
132 |         # keeps only the chunk objects, in hybrid order, for the reranker to score
133 |         reranked = self._reranker.rerank_with_scores(query, chunks, k)  # 🎯 the second EXPENSIVE call
134 |         # asks the reranker to pick and order the best k chunks of the pool
```
**🎯 Interview-relevant lines:** 119-122 are the whole guard. Nothing between the top of the
function and line 126 (the first real, possibly-costly call) can run unless both checks pass.

### Canonical docs
- Martin Fowler's refactoring catalog, [Replace Nested Conditional with Guard Clauses](https://refactoring.com/catalog/replaceNestedConditionalWithGuardClauses.html)

### Micro-exercises
1. Add a `validate_middle` function to `d4` that calls the stage ONCE, then checks `k`, then --
   only if valid -- calls the stage a SECOND time. Run it with a bad `k`. How many times does
   "...calling the REAL..." print?
2. Change `check_k_first` to also reject `k > 1000`. Test with `k=5000`. Does the guard-clause
   PATTERN change, or just the RULE?
3. In a LOCAL, uncommitted copy of `src/retrieval/pipeline.py`, comment out `_check_k(k)` in
   `retrieve()` and call `pipeline.retrieve("test", 0)` against fakes. What actually happens? Work
   out `pool_size = max(0*3, 20)` by hand first.
4. Write a guard clause for a made-up rule: "reject any query longer than 500 characters." Where in
   `retrieve()` would you add it, and before or after the existing two checks? Justify the order.
5. Compare `_check_k` here with its near-twins in `src/retrieval/graph_retriever.py` and
   `src/retrieval/reranker.py`. Are they word-for-word identical? If you fixed a bug in one, would
   fixing only that file be enough?

**How it works internally.** No special mechanism -- `_check_k`/`_check_query` are ordinary
functions, and `raise ValueError(...)` immediately unwinds the current call until something catches
it (or the program/test fails with that error). Because Python runs a function's statements strictly
in order, placing the checks as the first two lines of `retrieve()` is entirely sufficient to
guarantee neither stage is ever reached when a check fails.

### How to validate this yourself
Read `test_a_bad_k_is_rejected_before_either_stage_is_called` in `tests/retrieval/test_pipeline.py`
-- note it asserts `calls == []` AFTER the expected error, not just that an error was raised. That
second assertion is what actually proves "fail fast," not merely "fails." Build your own spy around
a real `HybridRetriever` (count calls, then delegate), pass a bad `k` through a real
`RetrievalPipeline`, and confirm your spy's count stayed at zero. Or insert `breakpoint()` right
before `_check_k(k)` in a scratch copy and single-step through with a debugger, watching execution
stop at `raise` and never reach the hybrid call.

### How to visualize this
Draw `retrieve()` as a short vertical pipe with a NARROW GATE at the very top (the two checks)
before it widens into the two big stage boxes below. Bad input hits the gate and is diverted off to
the side (an exception) before ever reaching the wide part of the pipe.

**You should now be able to explain:** what "fail fast" means, why it matters more when the skipped
code costs real money, and how to PROVE (not assume) a guard clause is preventing expensive calls.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Predict, then trace.** Before running anything, write down, for
   `pipeline.retrieve("Who founded Insurellm?", 3)`: the computed `pool_size`, and which two checks
   run before it (C5, C3).
2. **Add temporary prints.** In a local, uncommitted copy of `src/retrieval/pipeline.py`, add
   `print(f"pool_size={pool_size}")` after line 124 and `print(f"hybrid_by_id keys=
   {list(hybrid_by_id)}")` after line 128. Run any hermetic test from `tests/retrieval/
   test_pipeline.py` that calls `.retrieve(...)` and watch your prints appear in the output
   (`pytest -s`).
3. **Break C1 on purpose.** Build a fake hybrid stage with a method named `retrieve_scores`
   (missing the `_with`) and try to construct a `RetrievalPipeline` with it. Run `mypy` on your
   scratch file first -- does it catch the mismatch? Then run it anyway -- where does it crash,
   and does the error message point at the actual problem?
4. **Break C2 on purpose.** In a REPL, get a real `RetrievalResult` from a hermetic test fixture
   and try `answer.results = ()`  and `answer.results[0] = None`. Confirm both are refused, and
   name which mechanism refuses each one.
5. **Break C3 on purpose.** Construct a `RetrievalPipeline` with `pool_floor=1, pool_multiplier=1`
   and a fake hybrid stage that only ever returns 1 candidate. Call `.retrieve("q", 5)`. How many
   results come back, and which line in `retrieve()` (or in `Reranker`) explains why it's not 5?
6. **Break C4 on purpose.** Build a fake reranker that returns a `RerankedChunk` for a `chunk_id`
   that was NEVER in the pool you gave it. Call `.retrieve(...)` through a real `RetrievalPipeline`
   and watch the real `KeyError` this causes -- this is the one invariant C4's "how to validate"
   section told you never happens in the real system; now you've seen why.
7. **Break C5 on purpose.** Call `.retrieve("", 3)` and `.retrieve("q", -1)` against a pipeline
   whose fakes record every call, and confirm the call log stays empty both times.
8. **Watch it work live** (Docker services up, no Cohere key needed): run
   `pytest tests/retrieval/test_pipeline.py -m live -s -p no:cacheprovider` and read the two
   passing "no Cohere key" live tests' output -- a real `HybridRetriever`, wired through this whole
   file, answering a real question.

If you predicted correctly in steps 1, 3, 5 and 6 before running anything, you've internalized how
every piece of this file actually works together -- not just that it works.
