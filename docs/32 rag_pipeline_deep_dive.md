# 32 — RAG Pipeline Deep Dive (P6-M4)

> Deconstructs [`src/orchestration/rag.py`](../src/orchestration/rag.py) and
> [`src/orchestration/prompts.py`](../src/orchestration/prompts.py) — the class that finally wires
> Phases 3-6 into one usable thing, and the module that turned P6-M3's design into real code.
> Demo scripts live in
> [`practice codes/phase 6/m4_rag_pipeline_deep_dive/`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/>)
> and need no Docker at all — every one is a pure, offline Python toy. Run any from the project
> root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 6/m4_rag_pipeline_deep_dive/d2_budget_algorithm_trace.py"
> ```

---

## 0. Plain-language summary

`rag.py` and `prompts.py` do not do any retrieval or any generation themselves. They do exactly
one job each:

```
prompts.py: turn (question, ranked chunks) into two exact strings, and one exact fallback list
rag.py:     own the ONE place that calls a Retriever, then prompts.py, then an LLM, in order
```

Five concepts, in the order this file actually uses them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | Protocol-based dependency injection | 🔴 very high | `rag.py` lines 29-46 (`Retriever`, `LLM`) |
| C2 | Orchestration / composition | 🔴 very high | `rag.py` lines 81-162 (`RAGPipeline`) |
| C3 | The greedy best-fit budget algorithm | 🔴 high | `prompts.py` lines 43-63 (`apply_budget`) |
| C4 | Guard clause / short-circuit | 🟡 high | `rag.py` lines 111-126, 132-135, 149-154 |
| C5 | Generator composition for streaming | 🟡 high | `rag.py` lines 145-162 (`stream`) |

---

## C1 — Protocol-based dependency injection

### Do this first
Run [`d0_protocol_dependency_injection.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d0_protocol_dependency_injection.py>).
**Predict first:** `FormalGreeter` and `CasualGreeter` never inherit from `Greeter`. Do you expect
`announce(FormalGreeter(), "Dr. Rossi")` to work anyway? What about `announce(Silent(), "Anyone")`,
where `Silent` has no `greet` method at all — does it fail, and if so, when?

### 1. Definition + analogy
**What it is.** A `Protocol` (Python's `typing.Protocol`) describes a SHAPE — a set of methods an
object must have — rather than a lineage. Any object with matching methods satisfies it, with zero
inheritance, zero registration, and zero import of the Protocol itself by the class that satisfies
it. "Dependency injection" means a class (like `RAGPipeline`) receives its collaborators
(`Retriever`, `LLM`) as constructor arguments, typed by their SHAPE, instead of constructing them
itself or hard-coding a specific class.

**Analogy.** A car park's height barrier doesn't care what BRAND of car is coming — it only cares
whether the vehicle is under 2 meters tall. Any vehicle satisfying that one measurable property
gets in: a hatchback, a different-brand hatchback, a van built to the same height limit. The
barrier never asks for a certificate proving "this is an approved brand."

### For a 14-year-old
Imagine a height-restriction sign at the entrance to a theme park ride: "You must be at least this
tall to ride." The person checking doesn't care if you arrived by car, bike, or on foot, whether
you're wearing a red shirt or blue, or which school you go to. They check exactly ONE thing —
your height — and if you meet it, you're in. Two totally different kids, from totally different
families, who've never met each other, can BOTH ride, as long as they both happen to be tall
enough. Neither kid had to fill out a form beforehand proving "I am the kind of person allowed on
this ride" — meeting the one actual requirement was enough, automatically.

That's exactly what a `Protocol` does for code. `RAGPipeline` doesn't say "you must be a
`RetrievalPipeline`, specifically" — it says "you must have a method called `retrieve` that takes
a question and a number and returns a `RetrievalResult`." A REAL `RetrievalPipeline` (Phase 5) has
that method. A FAKE, made up just for a test, that returns made-up data ALSO has that method —
neither one had to announce "I am compatible with RAGPipeline" anywhere in their own code. They
just happen to have the right shape, the same way two different kids just happen to both be tall
enough.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the height-restriction sign | the `Retriever`/`LLM` `Protocol` classes (`rag.py` lines 29-46) |
| "at least this tall" | "has a method named `retrieve(query, k)` returning a `RetrievalResult`" |
| a kid who meets the height requirement | a real `RetrievalPipeline`, satisfying `Retriever` |
| a totally different kid who ALSO meets it | a `FakeRetriever` in a test, satisfying the same `Retriever` |
| neither kid filling out a form first | neither class ever writes `class RetrievalPipeline(Retriever):` |
| the ride operator checking height, not a certificate | mypy checking the Protocol's shape at type-check time |

### 2. Why here specifically
`RAGPipeline` needs to work with a REAL `RetrievalPipeline` and `LocalLLM` in production, and with
completely fake, controllable stand-ins in tests (`FakeRetriever`, `FakeLLM` in
`tests/orchestration/test_rag.py`) — without either side needing to know about the other. A
Protocol makes both acceptable, checked structurally, with no inheritance relationship required in
either direction. This exact pattern already appears three times before this file
(`HybridSearcher`/`Reranking` in `pipeline.py`, `LLMClient` in `llm.py`) — `rag.py`'s `Retriever`
and `LLM` are the fourth and fifth uses of the same idea in this project.

### What breaks if this changes
- **Require a specific base class instead** (`class RAGPipeline: def __init__(self, retriever:
  RetrievalPipeline, ...)`): a hermetic test's `FakeRetriever` would either need to inherit from
  the real, heavy `RetrievalPipeline` (dragging in Qdrant/Neo4j-shaped dependencies just to fake
  it) or mypy would reject passing it at all.
- **No type at all** (`def __init__(self, retriever, llm, ...)`): still works at runtime (Python
  doesn't enforce types), but a caller gets zero static guidance about what methods `retriever`
  needs, and a typo like `self._retriever.retreive(...)` would only be caught by actually running
  the code, not by mypy.
- **A Protocol with the WRONG shape** (e.g., missing the `system` parameter on `LLM.generate`):
  mypy would reject passing a real `LocalLLM` (whose `generate` DOES have `system`) the moment the
  Protocol's declared shape stopped matching what `RAGPipeline` actually calls.

### [Interview relevance: very high — structural typing / duck typing formalized via Protocol is one of the clearest signals a candidate understands Python's type system beyond just "add some type hints," and dependency injection via narrow interfaces is a foundational testability pattern in any language]

**Interview question:** *"How do you design a class so it can be tested with a fake dependency,
without that fake needing to inherit from anything?"*
**A strong answer covers:** define the DEPENDENCY as a narrow, structural interface (a `Protocol`
in Python, an interface in Java/TypeScript, a duck-typed contract in Ruby) describing only the
methods actually used; the real production class and a test fake both satisfy it independently,
with no inheritance relationship needed between them; this decouples "can I test this easily" from
"does my fake correctly extend a big, heavy base class"; and the interface should be OWNED by the
consumer (`rag.py` defines `Retriever`, not `pipeline.py`), so it only ever demands exactly what
that specific consumer needs — never more.

### 3. Minimal code that explains the concept
The complete script, [`d0_protocol_dependency_injection.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d0_protocol_dependency_injection.py>) (run from the project root):
```python
# Minimal demo of C1: a Protocol describes SHAPE (which methods must exist), not lineage --
# any object with the right methods satisfies it, with zero inheritance and zero registration.
from typing import Protocol  # the tool that lets us describe a shape instead of a base class


class Greeter(Protocol):  # defines the SHAPE: anything with a greet(name) -> str method
    def greet(self, name: str) -> str: ...  # the one method this Protocol requires


class FormalGreeter:  # does NOT inherit from Greeter at all -- structural typing needs no base class
    def greet(self, name: str) -> str:  # matches the shape Greeter requires
        return f"Good day, {name}."  # a real, distinct implementation


class CasualGreeter:  # also does NOT inherit from Greeter
    def greet(self, name: str) -> str:  # also matches the shape
        return f"Hey {name}!"  # a completely different implementation, same shape


def announce(greeter: Greeter, name: str) -> None:  # accepts ANYTHING satisfying the Protocol
    print(greeter.greet(name))  # calls the one method the Protocol promises will exist


print("--- two unrelated classes, both satisfying the same Protocol ---")
announce(FormalGreeter(), "Dr. Rossi")  # a "real" implementation, exactly like RetrievalPipeline
announce(CasualGreeter(), "Sam")  # a "fake" implementation, exactly like a test double


class Silent:  # does NOT define greet() at all -- does not satisfy the Protocol's shape
    pass  # an empty class, nothing more


print("\n--- what happens if an object does NOT satisfy the Protocol ---")
try:
    announce(Silent(), "Anyone")  # this is a REAL runtime error, not a type-checker-only one
except AttributeError as exc:
    print(f"AttributeError: {exc}")  # Protocol checking is STATIC (mypy, before running); at
    # runtime, Python only discovers a missing method the instant it's actually called
```
Real output:
```
--- two unrelated classes, both satisfying the same Protocol ---
Good day, Dr. Rossi.
Hey Sam!

--- what happens if an object does NOT satisfy the Protocol ---
AttributeError: 'Silent' object has no attribute 'greet'
```
Notice: at RUNTIME, `Silent` fails with a plain `AttributeError`, the same error you'd get from any
missing method — Python itself does not know or care about `Protocol` when the code actually runs.
The Protocol only matters to mypy, BEFORE the code runs. Try adding `-> None:` type hints and
running `mypy` on this file (add `greeter: Greeter` to `announce`'s signature, already present) —
mypy would reject `announce(Silent(), "Anyone")` at type-check time, without ever running it.

### The exact code I wrote (`src/orchestration/rag.py`)
> **Lines 29-32:**
> ```python
> class Retriever(Protocol):
>     def retrieve(self, query: str, k: int) -> RetrievalResult: ...
>
>     # anything that can return a ranked answer for a question, such as RetrievalPipeline
> ```
> **Lines 35-46:**
> ```python
> class LLM(Protocol):
>     def generate(
>         self, prompt: str, temperature: float, max_tokens: int, *, system: str | None = None
>     ) -> str: ...
>
>     # anything that can answer a prompt in one call, such as LocalLLM
>
>     def stream(
>         self, prompt: str, temperature: float, *, system: str | None = None
>     ) -> Iterator[str]: ...
>
>     # anything that can stream an answer to a prompt, such as LocalLLM
> ```

**🎯 Interview-relevant lines:** neither `Retriever` nor `LLM` imports `RetrievalPipeline` or
`LocalLLM` at all — the Protocol is defined entirely in terms of the SHAPE `RAGPipeline` needs,
owned by `rag.py` itself, not borrowed from the classes that happen to satisfy it.

### Canonical docs
- [Python docs: `typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
- [PEP 544 — Protocols: Structural subtyping](https://peps.python.org/pep-0544/)

### Micro-exercises
1. In `d0`, add a THIRD class, `RoboticGreeter`, with a `greet` method returning `"GREETINGS,
   {name}."` (all caps). Confirm `announce` works with it too, with zero changes to `announce`
   itself.
   *Hint: it needs nothing but a matching `greet(self, name: str) -> str` method.*
2. Change `Greeter`'s required method to `greet(self, name: str, *, loud: bool = False) -> str`.
   Do `FormalGreeter` and `CasualGreeter` (unchanged) still satisfy it? Run mypy to check, not just
   Python. *Hint: extra, unused OPTIONAL parameters on the Protocol are fine to ignore in an
   implementation that never receives them — but a REQUIRED new parameter would break both.*
3. Write a `Loud` class with `greet(self, name)` (no type hints at all). Does `announce(Loud(),
   "X")` work at runtime? Does mypy accept it if you run it? *Hint: Python itself never checks
   Protocols; only a type checker does — an untyped class can still satisfy one at runtime.*
4. Try to instantiate `Greeter()` directly (`g = Greeter()`). What error do you get, and why does
   it make sense that a Protocol itself can't be instantiated?
5. Look at `tests/orchestration/test_rag.py`'s `FakeRetriever` and `FakeLLM` classes. Confirm
   neither one writes `class FakeRetriever(Retriever):` — find the exact methods that make them
   satisfy `Retriever`/`LLM` anyway.

**How it works internally.** At runtime, Python does nothing special at all — calling
`greeter.greet(name)` is a completely ordinary attribute lookup and method call, exactly as if
`Greeter` didn't exist. `Protocol` only matters to a STATIC type checker (mypy): when it sees
`announce(some_object, name)`, it checks whether `some_object`'s CLASS declares (or infers) a
`greet` method with a compatible signature, walking the class's own methods — no runtime
dispatch table, no registration step, nothing stored on the object itself.

### How to validate this yourself
Run `mypy` against a file using an object that's MISSING a required method — the error appears
before the program ever runs, unlike the `AttributeError` above (which only appears once `announce`
actually calls `.greet()`). Compare the two failure modes directly: `python d0...py` fails at
runtime on the `Silent()` call; `mypy d0...py` (after adding a similarly broken case) fails before
any code executes at all.

### How to visualize this
Draw two shapes: a "hexagon" (the Protocol's declared method signature) and several other shapes
representing different classes. Only draw an arrow from a class to the Protocol if that class's own
edges (its methods) actually MATCH the hexagon's edges — no arrows drawn based on which "family" of
shapes each one belongs to, only based on matching outline.

**You should now be able to explain:** why `RAGPipeline` can accept a real `RetrievalPipeline` and
a totally unrelated `FakeRetriever` with zero shared inheritance, and why that matters for testing.

---

## C2 — Orchestration / composition

### Do this first
Run [`d1_orchestration_composition.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d1_orchestration_composition.py>).
**Predict first:** `Fetcher` and `Writer` never call each other directly anywhere in the code. Who
is the ONLY thing that ever calls both?

### 1. Definition + analogy
**What it is. "**Orchestration" is a class or function whose entire job is calling OTHER pieces in
the right order and passing data between them — it contains no domain logic of its own (no
retrieval logic, no generation logic), only WIRING. "Composition" is the general technique of
building a bigger behavior out of smaller, independently-understandable pieces, by having one
thing hold references to the others.

**Analogy.** A restaurant's expediter (the person at the pass between kitchen and dining room)
doesn't cook anything and doesn't serve anything — they call out orders to the right cook station,
then hand finished plates to the right server. Neither the cook nor the server needs to know the
other exists; the expediter is the only one who does.

### For a 14-year-old
Imagine a restaurant kitchen. The grill cook only knows how to grill things. The dessert chef only
knows how to make dessert. Neither one ever talks to the other, and neither one knows or cares
which server is going to carry the finished plate to which table. There's one person — the
expediter — standing at the pass, who reads each order, calls "two burgers, one cheesecake!" to
the right stations, waits for each part to be ready, and then hands the finished, COMBINED plate to
a server. The expediter doesn't grill anything and doesn't bake anything — their entire job is
knowing WHO does WHAT and in WHAT ORDER, and making sure the right pieces end up together at the
end.

`RAGPipeline` is the expediter of this whole project. It doesn't know how to search a vector
database (that's `Retriever`'s job) and it doesn't know how to generate text from a prompt (that's
`LLM`'s job) — it just knows: first ask the Retriever, then build a prompt from what came back,
then ask the LLM, then attach the source list. Every one of those four steps is someone ELSE's
actual expertise; `RAGPipeline` only knows the ORDER and how to pass each step's output into the
next step's input.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the grill cook, who only grills | `Retriever` (a real `RetrievalPipeline`), who only retrieves |
| the dessert chef, who only bakes | `LLM` (a real `LocalLLM`), who only generates |
| the expediter calling out orders in sequence | `RAGPipeline._prepare`/`query`/`stream`, calling each step in order |
| neither cook knowing the other exists | `Retriever` and `LLM` Protocols never reference each other |
| the expediter combining finished parts into one plate | `query()` combining the model's answer with `mechanical_source_list` |
| customers only ever talking to the expediter, never the kitchen directly | any future caller (a CLI, an API) only ever imports `RAGPipeline`, never `Retriever`/`LLM` directly |

### 2. Why here specifically
Before this step, using the system for a real question required a caller to manually call
`RetrievalPipeline.retrieve()`, then manually apply the budget, then manually build the prompt
strings, then manually call `LocalLLM.generate()`, then manually append the source list — five
manual steps, done correctly, every single time. `RAGPipeline` exists so that logic is written
and tested EXACTLY ONCE, in one place, instead of being re-derived (and potentially gotten subtly
wrong) by every future caller.

### What breaks if this changes
- **No orchestration class at all, just a module-level function:** would work functionally, but
  loses the constructor-time configuration this class relies on (`k`, `temperature`, `max_tokens`
  set once, reused by every call) — every call site would need to pass all three every time.
- **Put retrieval OR generation logic directly inside `RAGPipeline`** (e.g., re-implementing
  `apply_budget` inline instead of importing it): duplicates logic that's already independently
  tested in `prompts.py`, and now two places need to change together if the algorithm ever does.
- **Let `RAGPipeline` reach into `Retriever`'s internals** (e.g., calling a private method, or
  inspecting `RetrievalResult.results` for something beyond "is it empty"): breaks the whole point
  of the Protocol boundary — the orchestrator should only ever use the PUBLIC shape it declared.

### [Interview relevance: very high — "how do you structure a system that combines multiple independently-built components" is close to the single most common systems-design interview question, and demonstrating a real orchestration layer with zero domain logic inside it is exactly what a strong answer looks like in code]

**Interview question:** *"You have a retrieval system and a generation system, both already
built and tested separately. How do you combine them into one production-ready pipeline?"*
**A strong answer covers:** a dedicated orchestration class/function whose only job is calling
each piece in the right order and passing data between them, with NO retrieval or generation logic
duplicated inside it; each piece stays independently testable, and the orchestrator's own tests use
fakes for both dependencies, testing only the WIRING (call order, data passed through correctly,
error propagation); configuration that doesn't change per-call (like `k`, `temperature`) belongs in
the orchestrator's constructor, not threaded through every method call; and the orchestrator is the
ONLY place in the codebase that imports both halves — nothing else should need to.

### 3. Minimal code that explains the concept
The complete script, [`d1_orchestration_composition.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d1_orchestration_composition.py>) (run from the project root):
```python
# Minimal demo of C2: orchestration -- one class that KNOWS about two other pieces and wires
# them together, so no OTHER code ever needs to know both exist at the same time.
class Fetcher:  # stands in for RetrievalPipeline: knows how to fetch raw material
    def fetch(self, topic: str) -> str:  # returns something relevant to the topic
        return f"facts about {topic}"  # a toy stand-in for real retrieved chunks


class Writer:  # stands in for LocalLLM: knows how to turn material into a finished answer
    def write(self, material: str) -> str:  # turns raw material into a finished answer
        return f"Report: {material}, summarized."  # a toy stand-in for a real generated answer


class ReportPipeline:  # the ORCHESTRATOR: the only place that knows BOTH Fetcher and Writer exist
    def __init__(self, fetcher: Fetcher, writer: Writer) -> None:  # takes both dependencies in
        self._fetcher = fetcher  # keeps the fetcher for later use
        self._writer = writer  # keeps the writer for later use

    def run(self, topic: str) -> str:  # the ONE public method a caller ever needs to call
        material = self._fetcher.fetch(topic)  # step 1: fetch, exactly like RAGPipeline._prepare
        return self._writer.write(material)  # step 2: write, using step 1's result as input


pipeline = ReportPipeline(Fetcher(), Writer())  # wires the two pieces together, exactly once
print(pipeline.run("insurance"))  # a caller asks for ONE thing, knowing nothing about the 2 steps

print("\nNotice: nothing outside ReportPipeline ever imports or calls Fetcher/Writer directly --")
print("that is the whole point of orchestration: RAGPipeline plays this exact role for a real")
print("Retriever and a real LLM, and no other module in this project needs to know both exist.")
```
Real output:
```
Report: facts about insurance, summarized.

Notice: nothing outside ReportPipeline ever imports or calls Fetcher/Writer directly --
that is the whole point of orchestration: RAGPipeline plays this exact role for a real
Retriever and a real LLM, and no other module in this project needs to know both exist.
```

### The exact code I wrote (`src/orchestration/rag.py`)
> **Lines 111-126 (`_prepare`, the shared first half of both `query` and `stream`):**
> ```python
> def _prepare(self, question: str) -> tuple[str, list[RankedChunk]] | None:
>     """Retrieves and budgets `question`'s context; None means nothing was retrieved."""
>     _check_question(question)
>     result = self._retriever.retrieve(question, self._k)
>     if not result.results:
>         return None
>     kept = apply_budget(result.results)
>     user_prompt = build_user_prompt(question, kept)
>     return user_prompt, kept
> ```
> **Lines 128-143 (`query`, the non-streaming half):**
> ```python
> def query(self, question: str) -> str:
>     prepared = self._prepare(question)
>     if prepared is None:
>         return REFUSAL_TEXT
>     user_prompt, kept = prepared
>     answer = self._llm.generate(
>         user_prompt, self._temperature, self._max_tokens, system=SYSTEM_PROMPT
>     )
>     return answer.strip() + "\n\n" + mechanical_source_list(kept)
> ```

**🎯 Interview-relevant lines:** `query` and `stream` (below) both call `_prepare` first — the
retrieval-and-budgeting logic is written ONCE and shared, rather than duplicated between the
sync and streaming paths. This is composition happening at TWO levels at once: `RAGPipeline`
composes `Retriever` + `prompts.py` + `LLM`, and `query`/`stream` themselves compose `_prepare`.

### Canonical docs
- [Martin Fowler, Refactoring catalog](https://refactoring.com/catalog/) — general vocabulary for
  separating orchestration/wiring from domain logic (see also C4 below).
- This project's own [`src/retrieval/pipeline.py`](../src/retrieval/pipeline.py) — the exact same
  orchestration pattern, one phase earlier (`RetrievalPipeline` composing `HybridSearcher` +
  `Reranking`).

### Micro-exercises
1. In `d1`, add a THIRD stand-in class, `Formatter`, with a `format(self, report: str) -> str`
   method that wraps the report in `"=== {report} ==="`. Add it as a third dependency to
   `ReportPipeline` and call it as a third step in `run`.
   *Hint: mirror exactly how `fetcher`/`writer` are stored and called.*
2. Delete `_prepare` from your mental model and imagine writing `query` and `stream` WITHOUT it —
   write out (on paper) what code would have to be duplicated between them.
3. In `tests/orchestration/test_rag.py`, find the test that checks a specific call ORDER (that
   `retrieve` happens before `generate`). If it doesn't exist yet, write one using `FakeRetriever`
   and `FakeLLM`'s own recorded-calls lists.
4. Modify `d1`'s `ReportPipeline.run` to call `self._writer.write` BEFORE `self._fetcher.fetch`
   (swap the order, passing a placeholder to `write` first). Does it still "work"? What does this
   reveal about why ORDER matters in an orchestrator, not just which pieces are present?
5. Find one other class in this project that plays the same "orchestrator" role as `RAGPipeline`
   (hint: it's one phase earlier, in `src/retrieval/`). Compare its constructor signature to
   `RAGPipeline`'s — what's structurally identical between them?

**How it works internally.** There's no special mechanism — this is just two attribute references
(`self._retriever`, `self._llm`) stored at construction time, and two ordinary method calls at
invocation time. The entire "pattern" is a naming and organizational discipline (this class does
ONLY wiring), not a special Python feature.

### How to validate this yourself
Grep `rag.py` for anything that looks like retrieval logic (vector math, graph traversal) or
generation logic (token sampling, prompt templating beyond calling `build_user_prompt`) — there
should be none. Everything domain-specific should trace back to an import from `prompts.py` or a
method call on `self._retriever`/`self._llm`.

### How to visualize this
Draw three boxes side by side: "Retriever," "RAGPipeline," "LLM." Draw arrows FROM RAGPipeline TO
both other boxes (it calls them), and NO arrow between Retriever and LLM directly — they never
touch. Label the RAGPipeline box "wiring only, no domain logic inside."

**You should now be able to explain:** what "orchestration" means concretely, and why
`RAGPipeline` containing zero retrieval or generation logic of its own is a deliberate design
property, not an accident.

---

## C3 — The greedy best-fit budget algorithm

### Do this first
Run [`d2_budget_algorithm_trace.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d2_budget_algorithm_trace.py>).
**Predict first (this is the exact question this deep dive grew out of):** three chunks, best
ranked first, sized 1800, 400, and 100 tokens, with a 2000-token budget. Trace by hand: which
chunks end up in the final prompt, and what's the final total?

### 1. Definition + analogy
**What it is.** A greedy algorithm makes the locally-best choice at each step without
reconsidering earlier choices. "Best-fit" here means: try to keep each chunk if it fits in what's
left of the budget; if it doesn't fit, skip it and KEEP CHECKING later chunks, rather than giving
up entirely the moment one doesn't fit.

**Analogy.** Filling a jar with rocks, pebbles, and sand where you go through your rocks
LARGEST-FIRST: if the next rock won't fit in the remaining space, you don't give up and leave the
jar half-empty — you set that rock aside and try the NEXT one, since a smaller one might still slot
in.

### For a 14-year-old
Imagine you're packing a box for shipping, and you have a strict weight limit. You have four items,
already sorted from "most important to include" to "least important": a 1800-gram item, a
400-gram item, and a 100-gram item, with an 2000-gram limit. You pack the 1800-gram item first —
easy, it fits, and now you've used 1800 of your 2000 grams, leaving 200 grams of room. Next up is
the 400-gram item: does it fit in the remaining 200 grams? No — 400 is bigger than 200. A LAZY
packer would just stop right there and ship the box with only one item in it. But you're smarter
than that: you set the 400-gram item aside, and check the NEXT one anyway — the 100-gram item.
Does THAT fit in your remaining 200 grams? Yes! You add it, and now your box has two items in it
instead of one, using every gram of room efficiently, just by refusing to give up after the first
item that didn't fit.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| items sorted most-to-least important | `RankedChunk`s in `final_rank` order, best first |
| the 2000-gram weight limit | `CONTEXT_BUDGET_TOKENS = 2000` |
| packing the 1800-gram item, using up most of the room | `apply_budget` keeping chunk A (1800 tokens), `total` becomes 1800 |
| the 400-gram item not fitting in the remaining 200 grams | chunk B (400 tokens): `1800 + 400 = 2200 > 2000` |
| the lazy packer giving up right there | the REJECTED "stop at first miss" rule |
| checking the 100-gram item anyway, and it fitting | chunk C (100 tokens): `1800 + 100 = 1900 <= 2000` -> KEPT |
| never shipping an EMPTY box, even if the first item alone were over the limit | the `or not kept` clause: the very first chunk is always kept regardless |

### 2. Why here specifically
This corpus has real chunks up to 1265 approx-tokens; several large chunks together can plausibly
exceed the 2000-token retrieval budget on a real question. Without the "keep checking after a
miss" behavior, a perfectly useful smaller chunk sitting right behind an oversized one would be
discarded for no reason — real, measured value lost for free, which is exactly what
`docs/prompts.md` §4's own worked comparison against a naive rule demonstrated before any of this
was real code.

### What breaks if this changes
- **"Stop at the first chunk that doesn't fit"** (the naive rule): in the exact scenario above,
  would keep ONLY chunk A (1800 tokens, total 1800/2000) and discard chunk C entirely, even though
  it fits easily on its own — real, measured, avoidable information loss.
- **Remove the `or not kept` clause:** a single top-ranked chunk bigger than the entire budget
  (`HUGE`, 5000 tokens, in `d2`'s second scenario) would be dropped along with everything else,
  leaving `RAGPipeline` with ZERO sources despite retrieval having found something relevant — worse
  than one over-budget source.
- **Sort chunks by size instead of by rank before applying the budget:** would maximize how MANY
  chunks fit, but would throw away the retrieval/reranking system's own judgment about which chunk
  is actually most RELEVANT — a smaller, less relevant chunk could bump a larger, more relevant one.

### [Interview relevance: high — "how do you fit variable-sized retrieved context into a fixed token budget" is a standard, practical RAG systems question, and greedy algorithms with a "never leave empty-handed" edge case are a common enough pattern to be worth recognizing by name]

**Interview question:** *"You have a ranked list of retrieved chunks with different token sizes,
and a fixed context budget smaller than their total size. How do you decide what to include?"*
**A strong answer covers:** walk the ranked list best-to-worst (never re-sort by size, which would
override the ranking's own relevance judgment); for each chunk, keep it if it fits in what's left
of the budget; if it doesn't fit, SKIP it but keep checking the rest of the list, since a smaller,
lower-ranked chunk might still fit (a greedy "best-fit," not "stop at first miss"); and always
guarantee at least one chunk survives, even if the single best one alone exceeds the budget, since
an over-budget answer is better than an ungrounded one.

### 3. Minimal code that explains the concept
The complete script, [`d2_budget_algorithm_trace.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d2_budget_algorithm_trace.py>) (run from the project root):
```python
# Minimal demo of C3: apply_budget's exact control flow, traced step by step -- answers the
# question directly: given chunks of 1800, 400, 100 tokens, what survives a 2000-token budget?
def apply_budget(chunks_with_sizes, budget):  # chunks_with_sizes: a list of (name, size) pairs
    kept = []  # will hold the surviving (name, size) pairs, in their original order
    total = 0  # the running total of tokens committed so far
    for name, size in chunks_with_sizes:  # walks the list best-to-worst, exactly like the real code
        fits = total + size <= budget  # would adding this chunk stay within budget?
        is_first = not kept  # is this the very first chunk seen so far (nothing kept yet)?
        print(f"considering {name} ({size} tok): running total so far = {total}")
        if fits or is_first:  # the real condition: fits, OR this is the first chunk regardless
            kept.append((name, size))  # keeps this chunk
            total += size  # updates the running total
            print(f"  -> KEPT. new running total = {total}")
        else:
            print(f"  -> SKIPPED (would make {total + size} > {budget}), but still checking the rest")
    return kept, total  # hands back the survivors and their final combined total


print("--- three chunks: 1800, 400, 100 tokens; budget = 2000 ---")
chunks = [("A", 1800), ("B", 400), ("C", 100)]  # best-ranked first, exactly as RankedChunk arrives
survivors, total = apply_budget(chunks, 2000)  # runs the real algorithm against this exact case
print(f"\nfinal survivors: {survivors}")
print(f"final total: {total} / 2000")

print("\n--- what if the FIRST chunk alone is bigger than the whole budget? ---")
huge = [("HUGE", 5000), ("small", 100)]  # the top-ranked chunk alone already exceeds the budget
survivors2, total2 = apply_budget(huge, 2000)
print(f"final survivors: {survivors2}  (HUGE is kept anyway -- 'or is_first' never allows zero)")
```
Real output — **this is the direct, traced answer to this deep dive's opening question:**
```
--- three chunks: 1800, 400, 100 tokens; budget = 2000 ---
considering A (1800 tok): running total so far = 0
  -> KEPT. new running total = 1800
considering B (400 tok): running total so far = 1800
  -> SKIPPED (would make 2200 > 2000), but still checking the rest
considering C (100 tok): running total so far = 1800
  -> KEPT. new running total = 1900

final survivors: [('A', 1800), ('C', 100)]
final total: 1900 / 2000

--- what if the FIRST chunk alone is bigger than the whole budget? ---
considering HUGE (5000 tok): running total so far = 0
  -> KEPT. new running total = 5000
considering small (100 tok): running total so far = 5000
  -> SKIPPED (would make 5100 > 2000), but still checking the rest
final survivors: [('HUGE', 5000)]  (HUGE is kept anyway -- 'or is_first' never allows zero)
```
**A (1800) is kept first. B (400) is checked and SKIPPED, because 1800+400=2200 exceeds the
2000 budget — but the loop does not stop. C (100) is checked next and KEPT, because
1800+100=1900 fits. Final result: A and C survive, total 1900/2000; B is the only one dropped.**

### The exact code I wrote (`src/orchestration/prompts.py`)
> **Lines 43-63:**
> ```python
> def apply_budget(
>     chunks: Sequence[RankedChunk], budget: int = CONTEXT_BUDGET_TOKENS
> ) -> list[RankedChunk]:
>     """The best-ranked chunks of `chunks` that fit `budget` approx-tokens; always keeps the
>     first chunk even if it alone exceeds the budget, so a caller never gets zero sources."""
>     kept: list[RankedChunk] = []
>     total = 0
>     for chunk in chunks:
>         if total + chunk.chunk.approx_token_count <= budget or not kept:
>             kept.append(chunk)
>             total += chunk.chunk.approx_token_count
>         # else: this one chunk doesn't fit right now -- it is skipped, but the loop keeps
>         # checking the rest of the ranked list, since a smaller, lower-ranked chunk might fit
>     return kept
> ```

**🎯 Interview-relevant lines:** the single line `if total + chunk.chunk.approx_token_count <=
budget or not kept:` does THREE jobs at once: the budget-fit check, the "always keep the first
chunk" guarantee, and (implicitly, via `else` falling through to nothing but a comment) the
skip-and-continue behavior — a dense but well-named line worth being able to explain component by
component in an interview.

### Canonical docs
- `docs/prompts.md` [section 4](../docs/prompts.md#4-context-token-budget-measured-not-guessed) —
  this algorithm's own original design and worked arithmetic.
- [`tests/orchestration/test_prompts.py`](../tests/orchestration/test_prompts.py) — the real,
  hermetic tests proving this exact behavior, including the real measured percentiles case.

### Micro-exercises
1. In `d2`, change the budget to `1000`. Trace by hand first, then run it: how many of the three
   chunks (1800, 400, 100) survive now?
2. Add a FOURTH chunk, `("D", 50)`, to the end of the three-chunk list. Does it get kept alongside
   A and C? Trace the running total before running the code.
   *Hint: after C is kept, what is the new running total, and does 50 more fit under 2000?*
3. Rewrite `apply_budget` to implement the REJECTED "stop at first miss" rule (break out of the
   loop the first time a chunk doesn't fit) and re-run it against the exact 1800/400/100 scenario.
   Confirm chunk C is lost.
4. Using the real `apply_budget` from `prompts.py` (not the toy version), construct three
   `RankedChunk`s with `approx_token_count` 1800, 400, 100 (mirroring
   `tests/orchestration/test_prompts.py`'s own `ranked()` helper) and confirm it produces the same
   result as `d2`'s toy version.
5. Design a scenario (on paper) where the "always keep the first chunk" rule (`or not kept`)
   changes the OUTCOME for a THIRD chunk too — i.e., where a chunk two positions later than the
   oversized first one still gets kept because of, not despite, that rule.

**How it works internally.** A single linear pass, O(n) in the number of chunks — no sorting
needed, since the input already arrives ranked. Exactly one decision (`fits-or-first -> keep`,
otherwise implicitly `-> skip, continue`) is made per chunk, with a running total carried forward
mutably across iterations — the entire algorithm's state is two local variables, `kept` and
`total`.

### How to validate this yourself
`tests/orchestration/test_prompts.py`'s `test_apply_budget_skips_a_chunk_that_does_not_fit_but_
keeps_a_smaller_one_behind_it` asserts EXACTLY this behavior against the real measured percentiles
(1265, 909, 613, 204) from `docs/prompts.md` — run it, then break the algorithm on purpose (change
`or not kept` to nothing) and confirm that specific test starts failing.

### How to visualize this
Draw the three chunks as rectangles in a row, sized proportionally: a big one (A), a medium one
(B), a small one (C). Draw a vertical "budget line" after 2000 units of combined width. Shade A and
C green (inside or extending into acceptable territory) and B red, with an arrow drawn AROUND B
connecting A directly to C — visually showing the "skip, don't stop" behavior.

**You should now be able to explain:** exactly what happens, chunk by chunk, when `apply_budget`
processes a ranked list against a fixed budget, and why the algorithm never simply stops at the
first chunk that doesn't fit.

---

## C4 — Guard clause / short-circuit

### Do this first
Run [`d3_guard_clause_short_circuit.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d3_guard_clause_short_circuit.py>).
**Predict first:** both versions print roughly the same kind of "I don't know" message for a
question with zero sources. Does that mean they behave the SAME way internally? What's the one
observable difference the script prints?

### 1. Definition + analogy
**What it is.** A guard clause is an early check, near the top of a function, that immediately
handles a case that's ALREADY fully known, before running the rest of the function's (often more
expensive or less certain) logic. "Short-circuiting" means skipping the remaining work entirely
once that early check fires.

**Analogy.** A security guard checking IDs at a club entrance who turns away anyone under 18
immediately at the door — they never let that person go all the way to the bar, order a drink, and
get carded THERE. The outcome ("no drink for you") is the same either way, but the guard clause
avoids wasted effort and avoids ever trusting the bartender to catch it later.

### For a 14-year-old
Imagine a school test that starts with "If you did not attend the lecture on Tuesday, skip to the
bonus question at the end — the rest of this test assumes information from that lecture." A
student who skipped Tuesday's lecture doesn't need to struggle through the questions and probably
get them all wrong one by one — the guard clause at the top of the test SAVES them the trouble,
because the teacher already knows, with total certainty, that those questions are unanswerable for
that student. It's not a lucky guess or a shortcut that risks missing something — it's a case the
teacher can verify with 100% confidence BEFORE the student even reads question one.

`RAGPipeline.query()` does the exact same thing. If retrieval comes back with ZERO chunks, there is
NOTHING for the model to work with — this isn't a case where the model might figure out the right
answer despite having nothing; it's a case that's already certain. Rather than sending the model an
empty "Sources:" section and HOPING it notices and says "I don't know" (which Rule 1 asks it to do,
but which this project has already measured models not ALWAYS following), the code checks for this
certain case itself and answers directly — no model call needed, no chance of the model getting it
wrong.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "if you skipped Tuesday's lecture, skip to the bonus question" | `if not result.results: return None` (`_prepare`, line 117-119) |
| the teacher already knowing, with certainty, the student can't answer | the code already knowing, with certainty, there's nothing to ground an answer in |
| skipping straight to the bonus question, no time wasted on the rest | `query`/`stream` returning/yielding `REFUSAL_TEXT` immediately, no model call |
| NOT trusting the student to somehow figure it out anyway | NOT trusting the model to reliably self-report "I don't know" every time |
| the ordinary questions, answered normally by students who DID attend | the ordinary path: retrieval found chunks, budget applied, model called normally |

### 2. Why here specifically
This project already measured (docs/31) that this model doesn't ALWAYS follow instructions
reliably, even simple ones. Rule 1 (grounding) asks the model to say "I don't have enough
information" when sources are insuffient — but for the SPECIFIC case of zero sources at all, the
code doesn't need to trust that instruction; it can know the right answer with total certainty
itself, and never even give the model a chance to get it wrong.

### What breaks if this changes
- **Always call the model, even with zero sources** (the REJECTED alternative, D4 in
  decision.md): correct behavior depends entirely on the model correctly interpreting Rule 1 for
  an edge case it may not have seen much of in training (an EMPTY sources list is a slightly
  unusual prompt shape) — a real, avoidable risk for a case the code could answer with certainty.
- **Short-circuit on the WRONG condition** (e.g., `if len(result.results) < 2`): would refuse to
  answer questions that actually DID retrieve something useful, just because retrieval found only
  one chunk — a guard clause is only safe when its condition is genuinely, unambiguously certain.
- **Forget to short-circuit in BOTH `query` AND `stream`:** since `_prepare` is shared, both
  methods already inherit the guard for free — but if someone added a THIRD method bypassing
  `_prepare`, it would need its own guard, or this certainty would silently be lost for that path.

### [Interview relevance: high — recognizing when a case can be handled with certainty versus when it must be delegated to a less certain downstream step (here, the model itself) is a core systems-design instinct, and "don't trust an unreliable component for something you can determine yourself" generalizes far beyond RAG]

**Interview question:** *"Your system calls an external service (an API, a model, anything with
some failure or inconsistency rate). When should you handle a case yourself versus delegating it
to that service?"*
**A strong answer covers:** if a case can be determined with TOTAL certainty using information
already available locally, handle it directly rather than delegating — this avoids an unnecessary
call AND avoids depending on the external service's own reliability for something you already
know; delegate only genuinely uncertain cases, where the external service's judgment adds real
value; and recognize that "the instructions I gave the external service SHOULD handle this" is not
the same guarantee as "I verified it myself" — especially once you've measured that service being
unreliable at following instructions, as this project explicitly has (docs/31).

### 3. Minimal code that explains the concept
The complete script, [`d3_guard_clause_short_circuit.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d3_guard_clause_short_circuit.py>) (run from the project root):
```python
# Minimal demo of C4: a guard clause / short-circuit -- when a case is ALREADY certain, handle
# it immediately, and never call an expensive or unreliable step "just in case" it agrees.
CALLS_MADE = []  # a stand-in for tracking real model calls, so we can prove one never happened


def unreliable_expensive_model(prompt):  # stands in for a real LLM call
    CALLS_MADE.append(prompt)  # records that a real call happened
    return "some plausible-sounding but not-guaranteed-correct answer"  # simulates model output


def answer_without_guard(question, sources):  # the naive version: ALWAYS calls the model
    if not sources:  # no sources at all
        prompt = f"{question} (no sources provided -- please say you don't know)"
    else:
        prompt = f"{question} using: {sources}"
    return unreliable_expensive_model(prompt)  # called EVERY time, even with zero sources


def answer_with_guard(question, sources):  # the guarded version: short-circuits on a certain case
    if not sources:  # this case is 100% certain -- there is nothing to answer from
        return "I don't have enough information to answer that."  # answered WITHOUT calling the model
    prompt = f"{question} using: {sources}"
    return unreliable_expensive_model(prompt)  # only called when there is real material to use


print("--- WITHOUT a guard clause ---")
CALLS_MADE.clear()  # resets the call counter before this scenario
print(answer_without_guard("Who is the CEO?", sources=[]))  # asks with zero sources
print(f"real model calls made: {len(CALLS_MADE)}")

print("\n--- WITH a guard clause ---")
CALLS_MADE.clear()  # resets the call counter before this scenario
print(answer_with_guard("Who is the CEO?", sources=[]))  # asks the same question, zero sources
print(f"real model calls made: {len(CALLS_MADE)}")

print("\nSame outward wording in THIS toy case -- but the guarded version never trusted the model")
print("to correctly notice 'no sources' on its own, and never paid for a call to find out.")
```
Real output:
```
--- WITHOUT a guard clause ---
some plausible-sounding but not-guaranteed-correct answer
real model calls made: 1

--- WITH a guard clause ---
I don't have enough information to answer that.
real model calls made: 0

Same outward wording in THIS toy case -- but the guarded version never trusted the model
to correctly notice 'no sources' on its own, and never paid for a call to find out.
```
Notice the unguarded version's toy "model" doesn't even try to say "I don't know" — it answers
with something plausible-SOUNDING regardless. That's the real risk a guard clause eliminates: it
doesn't just save a wasted call, it removes an entire category of possible wrong answer.

### The exact code I wrote (`src/orchestration/rag.py`)
> **Lines 111-120 (the guard itself, inside `_prepare`):**
> ```python
> def _prepare(self, question: str) -> tuple[str, list[RankedChunk]] | None:
>     _check_question(question)
>     result = self._retriever.retrieve(question, self._k)
>     if not result.results:
>         return None
>     ...
> ```
> **Lines 132-135 (`query` acting on the guard's signal):**
> ```python
> prepared = self._prepare(question)
> if prepared is None:
>     return REFUSAL_TEXT
> ```
> **Lines 149-154 (`stream` acting on the same signal, its own way):**
> ```python
> prepared = self._prepare(question)
> if prepared is None:
>     yield REFUSAL_TEXT
>     return
> ```

**🎯 Interview-relevant lines:** the guard condition lives in ONE place (`_prepare`), but is ACTED
ON in two different ways (`return` a value vs. `yield` a value then `return` to end the generator)
— the certainty check is shared; how each caller responds to it is not.

### Canonical docs
- [Martin Fowler, Replace Nested Conditional with Guard Clauses](https://refactoring.com/catalog/replaceNestedConditionalWithGuardClauses.html)

### Micro-exercises
1. In `d3`, change `answer_without_guard`'s toy model to ACTUALLY check for empty sources and
   return the right refusal itself. Does this fully close the gap with `answer_with_guard`? What's
   still different (hint: check `CALLS_MADE`)?
2. Write a guard clause for a DIFFERENT certain case: a `question` that's just whitespace. Where
   in `RAGPipeline` does this guard already exist? Find the exact line.
3. Remove the guard clause from a COPY of `_prepare` (comment out the `if not result.results:
   return None` block) and trace, by hand, what `build_user_prompt` would receive if `kept` ends
   up as an empty list. What would the resulting prompt's "Sources:" section actually look like?
4. Find a case in `RAGPipeline` that is NOT guarded (there is at least one uncertain case the code
   deliberately delegates to the model rather than trying to detect itself). What is it, and why
   is that the right call per D-2026-09-29-28?
5. Design a NEW guard clause (on paper) for a hypothetical case: retrieval returns exactly one
   chunk, but its `approx_token_count` is 0 (empty text). Should this be guarded the same way as
   zero results? Argue both sides.

**How it works internally.** Nothing beyond an ordinary `if` statement and an early `return`
(or `yield` + `return` inside a generator) — there is no special Python mechanism for "guard
clauses" as a named feature; it's a naming convention for a specific STYLE of using `if`/`return`
that programmers recognize by pattern, not by syntax.

### How to validate this yourself
`tests/orchestration/test_rag.py`'s
`test_query_on_empty_retrieval_returns_the_refusal_text_without_calling_the_model` asserts BOTH
the correct return value AND `llm.generate_calls == []` — proving the guard didn't just produce
the right STRING by coincidence, but genuinely prevented the model call.

### How to visualize this
Draw a fork in a path right at the very start (not partway through), labelled with the guard
condition. One branch is short and leads directly to an exit sign ("REFUSAL_TEXT"). The other
branch is long, winding through several more boxes (budget, prompt, generate, append sources)
before reaching its own exit. Label the short branch "certain — no model needed."

**You should now be able to explain:** the difference between a case a system can determine with
certainty itself versus a case it must delegate to a less-than-perfectly-reliable component, and
why that distinction is exactly what decides where a guard clause belongs.

---

## C5 — Generator composition for streaming

### Do this first
Run [`d4_generator_composition_streaming.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d4_generator_composition_streaming.py>).
**Predict first:** look at the `print` statements inside `inner_stream`. Do you expect ALL of them
to print before `outer_stream` yields its first value to the `for` loop, or do you expect them
interleaved with the `received:` lines?

### 1. Definition + analogy
**What it is.** A generator function (one using `yield`) produces values LAZILY — one at a time,
only when actually requested, rather than computing everything up front. "Composing" generators
means one generator function walks another generator's output with a `for` loop (or `yield from`),
passing each value through, and can ALSO `yield` extra values of its own before or after.

**Analogy.** A relay race where each runner (an inner generator step) only starts running the
instant they receive the baton — not a moment before — and the anchor runner (the outer generator)
can also run one final extra lap of their own after taking the baton from the last teammate.

### For a 14-year-old
Imagine a bucket brigade passing water buckets down a line to put out a fire. Each person in the
line doesn't fill five buckets in advance and stack them up — they wait until the person before
them actually HANDS them a bucket, then immediately pass it on to the next person. Nothing happens
"ahead of time." Now imagine the LAST person in the line, after passing on every bucket that came
from up the chain, also has their OWN personal bucket that they add to the effort right at the very
end, once all the passed-along buckets are done.

`RAGPipeline.stream()` is exactly that last person. It receives fragments one at a time from
`LLM.stream()` (the chain of buckets coming from upstream) and immediately passes each one straight
through to whoever is consuming `RAGPipeline.stream()` — nothing is held back or buffered. Then,
once the model has finished sending every fragment it has, `RAGPipeline.stream()` adds ONE more
thing of its own — the mechanical source list — as a final, extra "bucket" that didn't come from
the model at all.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| each bucket-brigade worker waiting for the actual bucket before acting | `for fragment in self._llm.stream(...):` only running its body once a real fragment arrives |
| passing the bucket straight on immediately | `yield fragment` (line 159) — no buffering, no batching |
| the last person's own extra bucket, added after the chain is done | `yield "\n\n" + mechanical_source_list(kept)` (line 161), after the loop ends |
| nothing happening until buckets actually start moving | nothing in `stream()`'s body executes until the FIRST `next()` call on it (true of every Python generator) |

### 2. Why here specifically
`LLM.stream()` exists specifically so a caller (e.g., a future UI) can display an answer as it's
generated, token by token, rather than waiting for the whole thing. `RAGPipeline.stream()` needs to
preserve that same real-time property while ALSO attaching the mechanical source list — but the
source list can only be known once the model is done producing content that might need citing
alongside it, so it has to arrive as a genuinely SEPARATE, final piece, not mixed into the model's
own fragments.

### What breaks if this changes
- **Collect all fragments into a list first, then yield them one by one:** would defeat the entire
  purpose of streaming — the caller would wait for the FULL answer before seeing anything at all,
  identical in user-experience to just calling `query()`.
- **Yield the source list BEFORE the loop instead of after:** the reader would see citation numbers
  before ever seeing the text that uses them, actively confusing rather than helpful.
- **Use `yield from self._llm.stream(...)` instead of an explicit `for` loop:** would work
  identically for this case (both forward every value transparently) — `yield from` is the more
  idiomatic choice; the explicit loop is not WRONG here, just slightly more verbose than necessary.

### [Interview relevance: high — understanding that generators are lazy (nothing runs until iteration begins, and nothing runs AHEAD of the current request) is fundamental to explaining streaming APIs correctly, and composing generators (one wrapping another, adding its own extra values) is a common real pattern in any system that streams data through multiple layers]

**Interview question:** *"You need to stream a model's response to a client in real time, but
you also need to append some metadata (like source citations) that's only known once generation
finishes. How would you implement this?"*
**A strong answer covers:** a generator function that iterates the model's own streaming
output with a `for` loop (or `yield from`), forwarding each fragment immediately with no
buffering, preserving the real-time property; after that inner loop completes (meaning generation
is fully done), `yield` the metadata as one final, separate value — never mixed into or held back
alongside the model's own fragments; and an understanding that nothing in a generator function's
body executes until the caller actually starts iterating (calling `next()` on it, e.g. via a `for`
loop), which is why constructing the generator object itself is instant even though consuming it
takes real time.

### 3. Minimal code that explains the concept
The complete script, [`d4_generator_composition_streaming.py`](<../practice codes/phase 6/m4_rag_pipeline_deep_dive/d4_generator_composition_streaming.py>) (run from the project root):
```python
# Minimal demo of C5: composing generators -- a generator function can walk ANOTHER generator's
# values one at a time, then yield something extra of its own once the inner one is exhausted.
def inner_stream():  # stands in for LLM.stream(): yields a few real fragments, lazily
    print("  [inner] producing fragment 1")  # printed only when this fragment is actually requested
    yield "Hello"
    print("  [inner] producing fragment 2")
    yield ", world"
    print("  [inner] producing fragment 3")
    yield "!"


def outer_stream():  # stands in for RAGPipeline.stream(): wraps inner_stream, adds one more value
    for fragment in inner_stream():  # walks every value inner_stream yields, one at a time
        yield fragment  # passes it straight through to whoever is consuming outer_stream
    yield " [Sources: 1]"  # after the inner generator is fully exhausted, yields ONE more value


print("--- consuming outer_stream() one piece at a time ---")
for piece in outer_stream():  # each loop turn pulls exactly one more value, lazily, not all at once
    print(f"received: {piece!r}")

print("\nNotice the [inner]/received print order above: nothing runs ahead of time -- each")
print("'producing fragment N' line only appears the instant that specific piece is requested.")
```
Real output:
```
--- consuming outer_stream() one piece at a time ---
  [inner] producing fragment 1
received: 'Hello'
  [inner] producing fragment 2
received: ', world'
  [inner] producing fragment 3
received: '!'
received: ' [Sources: 1]'

Notice the [inner]/received print order above: nothing runs ahead of time -- each
'producing fragment N' line only appears the instant that specific piece is requested.
```
Each `[inner] producing fragment N` line appears IMMEDIATELY BEFORE its matching `received:` line
— proving nothing was computed in advance. The final `' [Sources: 1]'` has no matching `[inner]`
line at all, because it never came from `inner_stream` — it's `outer_stream`'s own addition.

### The exact code I wrote (`src/orchestration/rag.py`)
> **Lines 145-162:**
> ```python
> def stream(self, question: str) -> Iterator[str]:
>     """`question`'s answer, yielded one real fragment at a time, sources appended last."""
>     prepared = self._prepare(question)
>     if prepared is None:
>         yield REFUSAL_TEXT
>         return
>     user_prompt, kept = prepared
>     for fragment in self._llm.stream(user_prompt, self._temperature, system=SYSTEM_PROMPT):
>         yield fragment
>     yield "\n\n" + mechanical_source_list(kept)
> ```

**🎯 Interview-relevant lines:** line 153's bare `return` inside a generator is not "returning a
value" (a generator's `return` can't produce one the caller receives via iteration) — it's ending
the generator immediately, which is why no code after it (including the final `mechanical_source_
list` yield below) ever runs for the empty-retrieval path.

### Canonical docs
- [Python HOWTO: Generators](https://docs.python.org/3/howto/functional.html#generators)
- [PEP 380 — Syntax for delegating to a subgenerator (`yield from`)](https://peps.python.org/pep-0380/)

### Micro-exercises
1. In `d4`, replace the explicit `for fragment in inner_stream(): yield fragment` loop with
   `yield from inner_stream()`. Run it again — is the printed output identical?
2. Add a `print("outer_stream body has started")` as the very FIRST line inside `outer_stream`.
   Call `gen = outer_stream()` (without iterating yet) and confirm NOTHING prints until you call
   `next(gen)` for the first time. *Hint: this mirrors `test_stream_rejects_an_empty_question_
   before_any_retrieval`'s own comment about a generator's body not running until the first
   `next()`.*
3. Make `inner_stream` raise an exception partway through (after the second yield). What happens
   to `outer_stream`'s final `yield " [Sources: 1]"` line — does it still run?
4. Using `list(outer_stream())`, collect ALL values eagerly instead of iterating one at a time.
   Does the OUTPUT differ from the lazy version? Does anything about WHEN the prints happen
   differ?
5. Write a THIRD-level wrapper, `outermost_stream()`, that wraps `outer_stream()` the same way
   `outer_stream` wraps `inner_stream`, adding its own final value. Confirm three levels of
   composition still work correctly.

**How it works internally.** Calling a generator function (`outer_stream()`) does NOT run any of
its code — it immediately returns a generator OBJECT, a paused coroutine-like structure holding
the function's local state. Each call to `next()` (which a `for` loop does automatically) resumes
execution from exactly where it last paused (at the previous `yield`), runs until the NEXT `yield`
or `return`, and pauses again — this is why `inner_stream`'s print statements interleave with
`outer_stream`'s consumption rather than all running upfront.

### How to validate this yourself
`tests/orchestration/test_rag.py`'s `test_stream_yields_the_models_fragments_then_the_sources_last`
asserts the EXACT list of yielded values in order, including the final mechanical-source-list
chunk arriving last — the strongest possible proof this composition works as designed, not just
"doesn't crash."

### How to visualize this
Draw a conveyor belt with items moving one at a time from a "model" machine into a "RAGPipeline"
box, which passes each item straight through to an "output" tray with no storage bin in between.
At the very end of the belt (after the model machine shuts off), draw the RAGPipeline box placing
ONE more item onto the tray by itself, sourced from a small "Sources" bin sitting next to it, not
from the conveyor belt at all.

**You should now be able to explain:** why `RAGPipeline.stream()` can preserve real-time streaming
AND still guarantee the source list always arrives, and why nothing in a generator function's body
runs until a caller actually starts pulling values from it.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Answer the deep dive's opening question for real.** Using the real
   `tests/orchestration/test_prompts.py` helpers (`ranked("a", tokens=1800, rank=1)`, etc.),
   construct the exact 1800/400/100-token scenario from C3 and call the REAL `apply_budget`
   (not `d2`'s toy version). Confirm it matches `d2`'s traced output exactly.
2. **Trace a real `RAGPipeline.query()` call by hand**, end to end: pick a question, imagine three
   retrieved chunks with token counts you choose, apply the C3 budget algorithm by hand, build the
   user prompt string by hand (C2/§3's template), and predict the final returned string's shape
   (model text + blank line + `Sources:` block) before running any code.
3. **Break C1 on purpose.** In `tests/orchestration/test_rag.py`'s `FakeLLM`, delete the `stream`
   method entirely. Try constructing a `RAGPipeline` with it and calling `.stream(...)`. What
   error do you get, and at what point (construction, or first iteration)?
4. **Break C2 on purpose.** Make `RAGPipeline.query` call `self._llm.generate` BEFORE
   `self._retriever.retrieve` (swap the order in a copy of the code). What breaks, concretely?
5. **Break C3 on purpose.** In `prompts.py`, change `apply_budget`'s condition from `or not kept`
   to nothing. Run `tests/orchestration/test_prompts.py` — which specific test catches this, and
   what does its failure message say?
6. **Break C4 on purpose.** Remove the `if not result.results: return None` guard from `_prepare`.
   Run `tests/orchestration/test_rag.py::test_query_on_empty_retrieval_returns_the_refusal_text_
   without_calling_the_model` — does it fail, and does the failure clearly point at what changed?
7. **Break C5 on purpose.** Move `stream()`'s final `yield "\n\n" + mechanical_source_list(kept)`
   line to BEFORE the `for fragment in ...` loop instead of after. Run
   `test_stream_yields_the_models_fragments_then_the_sources_last` — what does the assertion
   failure show about the wrong ORDER of yielded values?
8. **Put it all back together.** Undo every change from steps 3-7. Re-run
   `tests/orchestration/test_prompts.py` and `tests/orchestration/test_rag.py` and confirm all 32
   hermetic tests pass again, exactly as they did before you started breaking things.

If you can predict the outcome of each break BEFORE running it, and explain in one sentence WHY
each specific test catches it, you understand this file's control flow at the level this whole
deep dive set out to reach.
