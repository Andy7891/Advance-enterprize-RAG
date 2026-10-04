# 33 — Context Window Management Deep Dive (P6-M5)

> Deconstructs P6-M5's additions to [`src/orchestration/prompts.py`](../src/orchestration/prompts.py)
> (`fit_context`, `apply_budget`'s refactor, `_greedy_best_fit`, `CONTEXT_WINDOW_TOKENS`) and
> [`src/orchestration/rag.py`](../src/orchestration/rag.py) (the "shell" trick in `_prepare`) — the
> step that turned P6-M4's fixed, hand-picked budget into a real, per-call computation. This deep
> dive does NOT re-cover the greedy best-fit algorithm's own keep/skip/recover mechanics — that's
> already fully covered in [docs/30](<30 prompt_strategy_deep_dive.md>) and
> [docs/32](<32 rag_pipeline_deep_dive.md>). Instead it covers what's genuinely NEW here: turning a
> constant into a computation, sharing one algorithm across two types, solving a
> measure-before-you-know-it problem, a real trade-off this refactor quietly introduced, and the
> live debugging methodology this exact step's own verification actually required.
> Demo scripts live in
> [`practice codes/phase 6/m5_context_window_management_deep_dive/`](<../practice codes/phase 6/m5_context_window_management_deep_dive/>)
> and need no Docker at all. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 6/m5_context_window_management_deep_dive/d0_static_vs_dynamic_budget.py"
> ```

---

## 0. Plain-language summary

P6-M4 asked "how many chunks fit?" and answered it ONCE, by hand: 4096 - 386 - 50 - 400 ≈ 3260,
rounded down to 2000 for safety, then hard-coded forever. P6-M5 asks the SAME question but answers
it FRESH, every single call, from the REAL prompt and REAL `max_tokens` of that specific call:

```
old (P6-M4): budget = 2000                                    <- one number, decided once
new (P6-M5): budget = 4096 - count_tokens_approx(prompt) - max_tokens   <- measured, every call
```

Five concepts, in the order this step actually introduces them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | Static approximation → dynamic computation | 🔴 very high | `CONTEXT_WINDOW_TOKENS`, `fit_context`, `apply_budget` |
| C2 | Generic programming with `TypeVar` | 🔴 high | `_greedy_best_fit` |
| C3 | Solving a circular measurement with a stand-in call | 🟡 high | `rag.py` `_prepare`'s `shell` |
| C4 | A refactor can silently drop an assumption nobody re-checked | 🔴 very high | `decision.md` D-2026-09-30-06 |
| C5 | Isolating a live failure via minimal reproduction | 🟡 high | this step's own live-test debugging |

---

## C1 — Static approximation → dynamic computation

### Do this first
Run [`d0_static_vs_dynamic_budget.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d0_static_vs_dynamic_budget.py>).
**Predict first:** the SAME chunk size (8 tokens) is checked against a short prompt, then a much
longer one. Do you expect the FIXED rule's answer to change between the two? Do you expect the
DYNAMIC rule's answer to change?

### 1. Definition + analogy
**What it is.** A **static approximation** is a value worked out once, in advance, by a human
doing arithmetic on assumed inputs, then hard-coded as a constant. A **dynamic computation**
recomputes that same kind of value fresh, from the REAL inputs, every single time it's needed. Both
can be "correct" — the difference is WHEN the number gets decided, and WHAT it's actually based on.

**Analogy.** A parking garage sign that says "Spaces available: 40" painted on a wall, updated by
hand once a week — versus an electronic sign wired to real sensors that shows the ACTUAL count of
empty spaces right now. Both are "the number of available spaces," but one is a guess frozen in
time and the other is measured live.

### For a 14-year-old
Imagine a school cafeteria that used to have a sign painted on the wall: "Seats available: 30." A
teacher picked that number once, at the start of the year, by estimating how many students usually
show up and how big the room is. That number NEVER changes, no matter what actually happens in the
cafeteria on any given day — even if today it's a holiday and almost nobody's there, or today
there's a special event and it's packed. The painted sign has no idea what's really going on right
now; it only knows what someone GUESSED, once, a long time ago.

Now imagine the school replaces it with an electronic sign connected to real sensors on every seat,
showing the ACTUAL number of empty seats, updated every second. If today is unusually crowded, the
number goes down and the sign shows it. If today is quiet, the number goes up. The electronic sign
isn't smarter in some magical way — it just LOOKS at what's really happening instead of trusting a
number someone wrote down once and forgot about.

`CONTEXT_BUDGET_TOKENS = 2000` was the painted sign — a number worked out once from ASSUMED sizes
(a 386-token system prompt, a 400-token answer) that don't hold for every real question. The new
`fit_context`/`apply_budget` are the electronic sign — they measure the REAL system prompt, the
REAL question, and the REAL `max_tokens` for THIS specific call, every time, and compute the actual
number left over.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the painted sign, decided once | P6-M4's `CONTEXT_BUDGET_TOKENS = 2000` (now removed) |
| the assumptions behind the painted number | "system prompt ≈ 386 tokens, answer ≈ 400 tokens" |
| the electronic sign, wired to real sensors | `fit_context`/`apply_budget`'s live formula |
| the real-time sensor readings | `count_tokens_approx(prompt)` and the caller's real `max_tokens` |
| a busy or quiet day changing the real count | a longer or shorter real question changing the real budget |

### 2. Why here specifically
P6-M4's fixed 2000 assumed a system prompt of exactly 386 tokens and an answer of exactly 400 —
true for THAT system prompt at THAT time, but not guaranteed to stay true (a longer question, a
different `max_tokens` a caller chooses, or a future edit to `SYSTEM_PROMPT` itself would all make
the assumption wrong without anyone noticing). The dynamic version can never go stale this way,
because it never assumes anything — it measures.

### What breaks if this changes
- **Keep the fixed constant forever:** if `SYSTEM_PROMPT` is ever edited (say, a longer worked
  example is added for the docs/31-discovered risk, `decision.md` D-2026-09-29-25's own named fix),
  the fixed 2000 would silently become WRONG — too generous, risking real overflow — with nothing
  in the code to notice or warn.
- **Compute dynamically but forget to pass the REAL prompt** (e.g., pass an empty string instead
  of the real system prompt + question): the computed budget would be an OVERESTIMATE, defeating
  the entire point — this is exactly the kind of mistake C3's "shell" trick exists to prevent.
- **Recompute per-token-count on every single access instead of once per call** (a performance
  concern, not a correctness one): `count_tokens_approx` is cheap (`len(text)//4`), so this isn't a
  real risk here, but it's worth knowing dynamic computation isn't free in general.

### [Interview relevance: very high — recognizing when a cached/precomputed constant has gone stale relative to its own assumptions, and replacing it with a live computation, is one of the most common real-world "this used to work, now it doesn't" bugs in production systems]

**Interview question:** *"You inherit a system with a hard-coded constant that was 'measured once.'
How do you decide whether it's safe to keep, or whether it needs to become a live computation?"*
**A strong answer covers:** identify exactly what assumptions the constant encodes (here: a
specific system-prompt length and a specific max_tokens); ask whether those assumptions are
guaranteed to hold forever or could silently change (a prompt template IS going to be edited
eventually); if inputs vary in ways the constant can't see, replace it with a computation from the
real inputs; and — the sharper point — recognize that "more correct" isn't automatically "safer"
(see C4): a live computation can remove implicit safety margins the constant used to carry, and
that trade-off needs to be made ON PURPOSE, not discovered by accident.

### 3. Minimal code that explains the concept
The complete script, [`d0_static_vs_dynamic_budget.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d0_static_vs_dynamic_budget.py>) (run from the project root):
```python
# Minimal demo of C1: a FIXED, hand-picked budget number vs. a DYNAMIC one computed fresh from
# real inputs every call -- the exact upgrade P6-M5 made to P6-M4's own apply_budget.
CONTEXT_WINDOW = 20  # a tiny toy "context window" so the numbers stay easy to trace by hand


def count_tokens(text):  # a toy stand-in for count_tokens_approx (the real project function)
    return max(1, len(text) // 4)  # same chars/4 rule this project actually uses


def fits_fixed(chunk_size, FIXED_BUDGET=10):  # the OLD way: one number, decided ahead of time
    return chunk_size <= FIXED_BUDGET  # never looks at the real prompt or max_tokens at all


def fits_dynamic(chunk_size, prompt, max_tokens):  # the NEW way: computed fresh, every call
    budget = CONTEXT_WINDOW - count_tokens(prompt) - max_tokens  # real room left, right now
    return chunk_size <= budget  # compares against THIS call's real, measured budget


print("--- a short prompt ---")
short_prompt = "hi"
print("fixed says:  ", fits_fixed(8))
print("dynamic says:", fits_dynamic(8, short_prompt, max_tokens=5))

print("\n--- a much longer prompt, the SAME chunk size ---")
long_prompt = "x" * 40
print("fixed says:  ", fits_fixed(8))  # never changes -- it doesn't know the prompt got longer
print("dynamic says:", fits_dynamic(8, long_prompt, max_tokens=5))  # correctly notices less room

print("\nThe fixed rule gives the SAME answer both times -- it can't see the real prompt at all.")
print("The dynamic rule's answer changes, because it actually measures what's really left.")
```
Real output:
```
--- a short prompt ---
fixed says:   True
dynamic says: True

--- a much longer prompt, the SAME chunk size ---
fixed says:   True
dynamic says: False

The fixed rule gives the SAME answer both times -- it can't see the real prompt at all.
The dynamic rule's answer changes, because it actually measures what's really left.
```
Same chunk, same call site — the fixed rule says "fits" both times, blind to the real prompt.
The dynamic rule correctly notices the longer prompt leaves less room and says "doesn't fit."

### The exact code I wrote (`src/orchestration/prompts.py`)
> **Lines 16-18:**
> ```python
> CONTEXT_WINDOW_TOKENS = 4096
> # Ollama's real, measured served context window (docs/prompts.md section 1) -- a fixed
> # property of the deployed model, not something that varies per call
> ```
> **Lines 75-83 (`fit_context`):**
> ```python
> def fit_context(
>     chunks: Sequence[DocumentChunk], prompt: str, max_tokens: int
> ) -> list[DocumentChunk]:
>     budget = CONTEXT_WINDOW_TOKENS - count_tokens_approx(prompt) - max_tokens
>     # everything left over once the fixed, non-chunk parts of the call are accounted for
>     return _greedy_best_fit(list(chunks), [c.approx_token_count for c in chunks], budget)
> ```

**🎯 Interview-relevant lines:** `CONTEXT_WINDOW_TOKENS` (4096) is the ONE number this module still
hard-codes — and that's deliberate, not an oversight: it's a genuinely fixed property of the
deployed model (verified once, in P6-M3, from the real server), not something that changes per
call the way the OLD `CONTEXT_BUDGET_TOKENS` pretended a per-call-varying quantity was fixed.
Knowing WHICH numbers are truly constant versus which only look constant is the whole skill here.

### Canonical docs
- `docs/prompts.md` [section 1](../docs/prompts.md) — the real, measured 4096-token window this
  step's one remaining constant is based on.
- [notes/step-6.5-context-window-management.md](../notes/step-6.5-context-window-management.md) —
  this step's own build notes and decision record.

### Micro-exercises
1. In `d0`, change `long_prompt` to `"x" * 20` instead of `"x" * 40`. Predict, then check: does
   the dynamic rule's answer change back to `True`? Trace the arithmetic by hand first.
2. Add a THIRD scenario: keep the long prompt, but change `max_tokens` from 5 to 2. Does the
   dynamic rule's answer change again? Why does a caller's own `max_tokens` choice affect how
   much room chunks get?
3. Modify `fits_fixed` to ALSO take a `prompt` parameter, but never actually use it in the
   calculation. Confirm Python happily accepts this (no error) — what does this reveal about why
   "the function LOOKS like it considers the prompt" isn't the same as it actually doing so?
4. Using the real `fit_context`/`apply_budget` from `prompts.py` (not the toy version), construct
   two scenarios with a short and a long real `prompt` string and confirm the real functions show
   the same kind of divergence `d0` demonstrates.
5. Write, on paper, one sentence explaining why `CONTEXT_WINDOW_TOKENS = 4096` staying a constant
   is NOT the same mistake as `CONTEXT_BUDGET_TOKENS = 2000` being a constant. What's the real
   difference between them?

**How it works internally.** There's no special mechanism — `count_tokens_approx` is a plain,
synchronous function call (`len(text) // 4`), and Python evaluates it fresh every time
`fit_context`/`apply_budget` runs, exactly like any other function call. "Dynamic" here just means
"the code calls a function to get a fresh value," as opposed to "the code reads a number someone
already wrote down." Nothing about this needs caching, background updates, or any concept beyond
ordinary function calls.

### How to validate this yourself
`tests/orchestration/test_prompts.py`'s `test_apply_budget_shrinks_as_the_real_prompt_grows` is the
real proof: the SAME two chunks, checked against a short prompt then a long one, produce DIFFERENT
survivor lists — something the old fixed-constant test could never have demonstrated, because the
fixed constant had no way to react to a different prompt at all.

### How to visualize this
Draw two dials, side by side. The left dial ("fixed") is a plain painted number that never moves.
The right dial ("dynamic") has a needle connected to a live gauge, moving up and down as you slide
a "prompt length" lever back and forth underneath it.

**You should now be able to explain:** the real difference between a value computed once and
hard-coded versus one computed fresh from real inputs every call, and why P6-M5's change from one
to the other is a genuine upgrade in correctness, not just a stylistic refactor.

---

## C2 — Generic programming with `TypeVar`

### Do this first
Run [`d1_generic_shared_algorithm.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d1_generic_shared_algorithm.py>).
**Predict first:** `best_fit` is written ONCE, with no mention of strings or dictionaries anywhere
in its own code. Do you expect it to work correctly for BOTH a list of strings AND a list of
dictionaries, with zero changes to `best_fit` itself?

### 1. Definition + analogy
**What it is.** `TypeVar` lets a function be written ONCE, generically, so it works correctly for
MANY different types, while a type checker (mypy) still verifies it's being used consistently —
you don't get "any type, no guarantees" (which plain untyped Python already gives you for free);
you get "any type, but the SAME type in and out, checked."

**Analogy.** A shipping container is built to hold "whatever fits inside it" — furniture, boxes of
electronics, crates of fruit. The container itself doesn't care WHAT's inside, but a single ship
CAN'T mix random unrelated cargo without a manifest tracking what's actually in each specific
container. `TypeVar` is that manifest: "this container holds items of type T — I don't know what T
IS yet, but whatever goes in must be consistent with whatever comes out."

### For a 14-year-old
Imagine a vending machine that's specially built to dispense "whatever kind of snack you loaded
into it" — the SAME machine mechanism works whether you load it with chip bags, candy bars, or
granola bars, because the machine's actual mechanism (a spring pushes the front item forward when
you press a button) doesn't care what SHAPE the snack is. It only needs one rule to make sense:
whatever type of snack goes IN a particular slot is the SAME type of snack that comes OUT of that
slot — you'd be confused if you loaded chip bags into slot B3 and a candy bar popped out instead.

`_greedy_best_fit` is built exactly like that vending machine. It doesn't know or care whether it's
handling `DocumentChunk`s or `RankedChunk`s — it just needs "whatever list of THINGS you give me,
paired with a list of their sizes, I'll hand back the survivors as the SAME kind of thing you gave
me." `TypeVar` is the label on the machine that makes this promise CHECKABLE: if you feed it
`DocumentChunk`s, mypy guarantees you get `DocumentChunk`s back, never something else by accident.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the vending machine's spring mechanism | `_greedy_best_fit`'s loop (keep/skip/recover logic) |
| loading chip bags into slot B3 | calling `_greedy_best_fit` with a list of `DocumentChunk` |
| loading candy bars into slot C1 | calling `_greedy_best_fit` with a list of `RankedChunk` |
| the promise "what goes in a slot is what comes out" | `TypeVar("_T")` tying the input and output types together |
| a confused customer getting the wrong snack | a hypothetical bug where the function returned the wrong type — impossible here, checked by mypy |

### 2. Why here specifically
`fit_context` (DocumentChunk) and `apply_budget` (RankedChunk) need the EXACT same keep/skip/
recover algorithm — the only difference is which field holds each item's token count
(`chunk.approx_token_count` vs. `chunk.chunk.approx_token_count`). Without a generic shared helper,
the algorithm would have to be written and tested TWICE, and any future bug fix would need to be
applied in two places, with a real risk of the two copies silently drifting apart over time.

### What breaks if this changes
- **Duplicate the algorithm for each type instead of sharing it:** exactly the problem P6-M5's own
  Finding 1 in the notes warns about — two near-identical implementations that can silently diverge
  (e.g., a bug fix applied to one and forgotten in the other).
- **Use `Any` instead of `TypeVar("_T")`:** would also let ONE function serve both types, but loses
  the guarantee that the OUTPUT list contains the SAME type as the INPUT list — mypy would no
  longer catch a mistake like accidentally returning `list[str]` from a call given `list[RankedChunk]`.
- **Make `_greedy_best_fit` a method on a shared base class both `DocumentChunk` and `RankedChunk`
  inherit from instead:** would require changing two OTHER modules' class hierarchies just to share
  one small algorithm — a much bigger, more invasive change for the same benefit.

### [Interview relevance: high — writing genuinely reusable, type-safe generic code (not just "loosely typed" code) is a real skill gap between junior and senior engineers, and TypeVar is Python's own mechanism for it, directly comparable to generics in Java/C#/TypeScript]

**Interview question:** *"You have two functions that do the exact same thing but on two
different, unrelated types. How do you avoid duplicating the logic?"*
**A strong answer covers:** extract the shared logic into one function parameterized by a
`TypeVar`, so it works generically across both types while a type checker still verifies input/
output type consistency; recognize this is different from just using `Any` (which gives up all
type safety) or `object` (which would require unsafe casts to use the result); and know this is
the same underlying idea as generics in Java (`<T>`), C# (`<T>`), or TypeScript (`<T>`) — Python's
own version, via `typing.TypeVar`, arrived later (Python 3.5+) but solves the identical problem.

### 3. Minimal code that explains the concept
The complete script, [`d1_generic_shared_algorithm.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d1_generic_shared_algorithm.py>) (run from the project root):
```python
# Minimal demo of C2: one generic algorithm, shared by code working with two completely
# different types -- exactly how _greedy_best_fit serves both fit_context (DocumentChunk) and
# apply_budget (RankedChunk) without either one knowing the other's type exists.
from collections.abc import Sequence  # for clean type hints on the shared function
from typing import TypeVar  # the tool that lets one function work with ANY type, safely

T = TypeVar("T")  # a placeholder type -- "whatever type the caller actually gives us"


def best_fit(items: Sequence[T], sizes: Sequence[int], budget: int) -> list[T]:
    # works on items of ANY type T, as long as a parallel list of sizes is given alongside them
    kept: list[T] = []  # will hold survivors, of whatever type T turns out to be
    total = 0  # running total of "size" committed so far
    for item, size in zip(items, sizes):  # walks both lists together, item by item
        if total + size <= budget or not kept:  # fits, or this is the first item regardless
            kept.append(item)  # keeps this item, whatever type it is
            total += size  # updates the running total
    return kept  # hands back the survivors, still typed as T


# --- using it with plain strings ---
words = ["elephant", "cat", "dog"]
word_sizes = [8, 3, 3]
print("words kept:", best_fit(words, word_sizes, budget=11))

# --- using it with a totally different type: small dictionaries ---
boxes = [{"name": "big"}, {"name": "small"}]
box_sizes = [7, 2]
print("boxes kept:", best_fit(boxes, box_sizes, budget=8))

print("\nSame function, zero changes, two completely unrelated item types -- this is exactly")
print("how fit_context (DocumentChunk) and apply_budget (RankedChunk) share _greedy_best_fit.")
```
Real output:
```
words kept: ['elephant', 'cat']
boxes kept: [{'name': 'big'}]

Same function, zero changes, two completely unrelated item types -- this is exactly
how fit_context (DocumentChunk) and apply_budget (RankedChunk) share _greedy_best_fit.
```
`best_fit` never mentions "string" or "dict" anywhere in its own body — it worked correctly for
both, because the algorithm genuinely doesn't care what type `T` is, only that sizes are numbers.

### The exact code I wrote (`src/orchestration/prompts.py`)
> **Lines 20-22:**
> ```python
> _T = TypeVar("_T")
> # the item type _greedy_best_fit keeps -- DocumentChunk for fit_context, RankedChunk for
> # apply_budget, so the one algorithm below serves both public, differently-typed functions
> ```
> **Lines 49-71 (`_greedy_best_fit`):**
> ```python
> def _greedy_best_fit(items: Sequence[_T], token_counts: Sequence[int], budget: int) -> list[_T]:
>     kept: list[_T] = []
>     total = 0
>     for item, tokens in zip(items, token_counts):
>         if total + tokens <= budget or not kept:
>             kept.append(item)
>             total += tokens
>     return kept
> ```

**🎯 Interview-relevant lines:** the underscore prefix on `_T` and `_greedy_best_fit` both signal
"private, module-internal" — this generic helper is deliberately NOT part of this module's public
API; only the two differently-typed, purpose-named public functions (`fit_context`, `apply_budget`)
are meant to be imported and called from elsewhere.

### Canonical docs
- [Python docs: `typing.TypeVar`](https://docs.python.org/3/library/typing.html#typing.TypeVar)
- [PEP 484 — Generics](https://peps.python.org/pep-0484/#generics)

### Micro-exercises
1. In `d1`, add a THIRD call to `best_fit` using a list of tuples instead of strings or dicts.
   Confirm it works with zero changes to `best_fit` itself.
   *Hint: you only need a matching `sizes` list alongside your new `items` list.*
2. Try mixing types in one call: `best_fit(["a", 5, "c"], [1, 1, 1], budget=2)`. Does Python raise
   an error at runtime? Would mypy flag this if you added a type-checked call site? What does this
   reveal about the difference between a runtime check and a static one?
3. Remove the `TypeVar` entirely and replace every `_T` with `Any`. Run `d1` again — does the
   OUTPUT change at all? What's different only from mypy's point of view, not Python's?
4. Look at `src/retrieval/pipeline.py`'s `RetrievalPipeline` and `src/orchestration/rag.py`'s
   `RAGPipeline` — do either of these use `TypeVar`? Why might `Retriever`/`LLM` (Protocols) be the
   right tool there, while `_greedy_best_fit` specifically needed a `TypeVar` instead?
5. Write a second generic helper, `first_n(items: Sequence[_T], n: int) -> list[_T]`, and use it
   with three different types. What's the smallest possible generic function you can write that
   still meaningfully demonstrates the concept?

**How it works internally.** At runtime, `TypeVar` does essentially nothing — Python doesn't
enforce types by default, so `_greedy_best_fit` would run identically even with the type hints
deleted entirely. `TypeVar` exists purely for mypy: it lets the type checker track "whatever type
came in via `items` is the same type that must come out in the returned `list`," verified
statically, before the code ever runs — the same category of check as Protocol conformance (docs/32
C1), just solving a different problem (type CONSISTENCY across a function's own inputs/outputs,
rather than SHAPE-matching between a caller and a dependency).

### How to validate this yourself
Run `mypy` on `src/orchestration/prompts.py` and confirm it passes cleanly, then temporarily change
`fit_context`'s return type annotation to `list[RankedChunk]` (the WRONG type) while leaving its
body untouched — mypy should immediately flag the mismatch, proving the `TypeVar`-based typing is
actually doing real verification work, not just decoration.

### How to visualize this
Draw one machine (a box) with a label on top reading "T" in a dashed outline (meaning "fill this in
later"). Draw three separate pipes leading INTO the box, each carrying a different shape of item
(circles, squares, triangles) on three separate runs — and three separate pipes leading OUT, each
carrying the SAME shape that went in on that run, never a mixed or wrong shape.

**You should now be able to explain:** what `TypeVar` actually buys you over plain untyped Python
or `Any`, and why `_greedy_best_fit` needed one to safely serve two unrelated public functions.

---

## C3 — Solving a circular measurement with a stand-in call

### Do this first
Run [`d2_measure_via_stand_in.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d2_measure_via_stand_in.py>).
**Predict first:** two different ways of getting "the prompt's shape, minus the actual sources," are
shown side by side. Do you expect them to produce identical text? What happens to EACH one if the
real template later changes?

### 1. Definition + analogy
**What it is.** Sometimes you need to measure a PART of what a function will eventually produce,
before you have all the real inputs to produce the FULL thing. One risky option is writing a
SEPARATE function that approximates just that part by hand. A safer option is calling the REAL
function with a trivial/placeholder input for the part you don't have yet — getting an honest,
automatically-correct partial measurement, for free.

**Analogy.** Wanting to know how much an empty moving box weighs, before you've packed anything
into it. You could look up the box's weight in a spec sheet somewhere (a SEPARATE source that might
be outdated or wrong) — or you could just put the EMPTY box on the real scale. The scale is the
real, authoritative measurement tool; using it with nothing inside still gives you a true number,
and if the box's design ever changes, the scale will simply tell you the new true weight, with no
separate spec sheet to remember to update.

### For a 14-year-old
Imagine you're making sandwiches for a party, and you need to know how much a sandwich weighs with
NO filling — just the two slices of bread — before you've decided what filling to put in each one.
One way: write down, from memory, roughly how much two slices of bread usually weigh, and hope you
remembered right. Another way: literally take two real slices of bread from the real bag you're
using today, put them on a real kitchen scale, and read the actual number. The second way can NEVER
be wrong about today's actual bread, because it's not a memory or a guess — it's the real bread, on
the real scale, just without filling yet.

`RAGPipeline._prepare` needs to know how many tokens the FINAL prompt will cost, MINUS the chunks
(since it hasn't picked which chunks survive yet — that's the whole thing it's trying to figure
out). Instead of writing a SEPARATE function that guesses "the system prompt is about this many
tokens, plus the question, plus some labels," it calls the REAL `build_user_prompt` function — the
actual "scale" — with an EMPTY list of sources. That's the real template, doing the real work, just
with nothing in the "sources" slot yet — exactly like the empty sandwich on the real scale.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| a memorized guess at the bread's weight | `build_shell_by_hand` — a second, hand-maintained function |
| putting the real, empty-of-filling bread on the real scale | `build_user_prompt(question, [])` — the real function, empty sources |
| the guess going stale if the bread recipe changes | `build_shell_by_hand` silently becoming wrong after a real template edit |
| the scale always being right, automatically | `build_user_prompt(question, [])` always matching the real template, automatically |

### 2. Why here specifically
`apply_budget` needs `prompt` (everything EXCEPT the chunks) to compute the real budget, but the
FINAL prompt (with chunks) can't be built until the budget decides which chunks survive — a genuine
chicken-and-egg problem. `build_user_prompt(question, [])` breaks the cycle cheaply: it's the exact
same function that will build the REAL final prompt later, just called once early with an empty
placeholder for the one piece that isn't known yet.

### What breaks if this changes
- **Write a separate "shell" function** (as `d2`'s `build_shell_by_hand` does): works fine at
  first, but SILENTLY drifts out of sync the moment `build_user_prompt`'s real template changes —
  demonstrated directly in `d2`'s own output.
- **Skip measuring the shell entirely, assume it's roughly the system prompt alone:** would
  undercount the real prompt cost (missing the question text and the "Sources:\n...\nQuestion:...
  \nAnswer:" labels), making the computed budget too GENEROUS — a real overflow risk.
- **Try to build the REAL final prompt first, then measure, then decide chunks:** impossible —
  you'd need to already know which chunks survived to build the final prompt, which is exactly
  the thing being decided. The circularity can't be avoided this way; it can only be resolved by
  measuring a trivial stand-in, as C3 does.

### [Interview relevance: medium-high — recognizing and resolving a circular dependency by finding a cheap, honest partial measurement (rather than either guessing or getting stuck) is a broadly useful systems-design instinct that comes up anywhere a value both depends on, and is needed to compute, another value]

**Interview question:** *"You need to estimate the size of something before you have all the
information to build the real thing. How do you avoid writing a second, parallel estimation
function that could drift out of sync with the real one?"*
**A strong answer covers:** identify whether the real builder can be called with a trivial,
placeholder, or empty value for the part that isn't known yet, producing an honest partial
measurement for free; prefer this over a hand-written second function, since the real builder can
never silently drift out of sync with itself; and recognize the general pattern — measuring a
"shell" or "skeleton" version of a thing you'll build for real later — appears anywhere a
circular or not-yet-fully-known dependency needs to be broken cheaply.

### 3. Minimal code that explains the concept
The complete script, [`d2_measure_via_stand_in.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d2_measure_via_stand_in.py>) (run from the project root):
```python
# Minimal demo of C3: solving a circular-dependency measurement problem by calling the REAL
# builder with a trivial/empty input, instead of writing and maintaining a second builder.
def build_user_prompt(question, sources):  # a tiny stand-in for the real build_user_prompt
    sources_text = "\n".join(sources)  # joins whatever sources were given, even zero of them
    return f"Sources:\n{sources_text}\n\nQuestion: {question}\n\nAnswer:"


# --- Option A: measure the "shell" by calling the REAL builder with an empty source list ---
shell_via_reuse = build_user_prompt("Who is the CEO?", sources=[])
print("shell (via reuse):", repr(shell_via_reuse))

# --- Option B: a SEPARATE, hand-written "shell" function, maintained on its own ---
def build_shell_by_hand(question):  # a second, independent function -- has to be kept in sync
    return f"Sources:\n\n\nQuestion: {question}\n\nAnswer:"  # a manual guess at the same shape


shell_by_hand = build_shell_by_hand("Who is the CEO?")
print("shell (by hand):  ", repr(shell_by_hand))
print("identical?", shell_via_reuse == shell_by_hand)

# --- now the REAL builder's template changes (a realistic future edit) ---
def build_user_prompt_v2(question, sources):  # imagine a future edit: labels get capitalized
    sources_text = "\n".join(sources)
    return f"SOURCES:\n{sources_text}\n\nQUESTION: {question}\n\nANSWER:"  # <- changed!


shell_via_reuse_v2 = build_user_prompt_v2("Who is the CEO?", sources=[])
print("\nafter a template edit:")
print("shell (via reuse):", repr(shell_via_reuse_v2), "-- updates automatically")
print("shell (by hand):  ", repr(shell_by_hand), "-- now WRONG, nobody remembered to update it")
```
Real output:
```
shell (via reuse): 'Sources:\n\n\nQuestion: Who is the CEO?\n\nAnswer:'
shell (by hand):   'Sources:\n\n\nQuestion: Who is the CEO?\n\nAnswer:'
identical? True

after a template edit:
shell (via reuse): 'SOURCES:\n\n\nQUESTION: Who is the CEO?\n\nANSWER:' -- updates automatically
shell (by hand):   'Sources:\n\n\nQuestion: Who is the CEO?\n\nAnswer:' -- now WRONG, nobody remembered to update it
```
Both approaches start out identical — but the moment the REAL template changes, the reused version
updates itself automatically, and the hand-written second version silently becomes wrong, with no
error, no warning, nothing to notice.

### The exact code I wrote (`src/orchestration/rag.py`)
> **Lines 119-121:**
> ```python
> shell = SYSTEM_PROMPT + "\n\n" + build_user_prompt(question, [])
> # the real system prompt plus the real question, sources section left empty -- exactly
> # the non-chunk portion of the final prompt apply_budget needs to measure against (P6-M5)
> ```

**🎯 Interview-relevant lines:** this is a single line doing real architectural work — it's the
ENTIRE resolution to the circular-dependency problem, achieved by reusing existing, already-tested
code rather than adding a new function. No new function was written in `prompts.py` for this at
all; the fix was recognizing `build_user_prompt` already did what was needed, if called the right
way.

### Canonical docs
- No single canonical "name" for this exact pattern exists in the literature the way "guard
  clause" or "generics" do — it's closest to what's sometimes called a **partial application** or
  **degenerate-case measurement**, worth searching for those terms if you want to go deeper.
- [notes/step-6.5-context-window-management.md](../notes/step-6.5-context-window-management.md)
  Finding 2 — this project's own written record of choosing reuse over a second function.

### Micro-exercises
1. In `d2`, change `build_user_prompt`'s empty-sources case to instead put a placeholder string
   like `"[EMPTY]"` when `sources` is empty. Does `shell_via_reuse` still make sense as "the
   non-chunk part of the prompt"? What went wrong, and why?
2. Add a THIRD variant: a builder that takes an OPTIONAL sources parameter, defaulting to `None`,
   and handles `None` differently from an empty list. Which is a cleaner API for this shell-
   measuring use case — `sources=[]` or `sources=None`? Argue both sides.
3. Using the REAL `build_user_prompt` from `prompts.py`, call it with an empty chunk list for two
   DIFFERENT questions and compare the resulting shell strings. What's the only part that differs?
4. Modify `build_shell_by_hand` to correctly match `build_user_prompt`'s CURRENT template exactly
   (character for character). How confident are you this stays correct forever? What would make
   you re-check it?
5. Design (on paper) a case where calling the real builder with a trivial input would NOT safely
   substitute for a real measurement — i.e., a builder whose behavior meaningfully changes based on
   whether its input is empty vs. non-empty, in a way that breaks the "empty stand-in" trick.

**How it works internally.** `build_user_prompt(question, [])` runs through the EXACT same code
path a real, populated call would — the same f-string, the same `numbered_sources` call (which,
given an empty list, simply produces an empty generator and thus an empty joined string) — there is
no special "empty mode" branch anywhere. The trick works specifically BECAUSE the function was
already written generically enough to handle zero sources gracefully, without needing to be
special-cased for this new use.

### How to validate this yourself
Add a temporary `print(repr(shell))` inside `RAGPipeline._prepare` (or run it in a debugger/REPL)
and compare it, character for character, against what `build_user_prompt` would produce for the
same question with a REAL, non-empty chunk list — the only difference should be the
`Sources:\n...\n\n` portion.

### How to visualize this
Draw the real `build_user_prompt` function as a single machine with two input slots: "question"
and "sources." Draw one arrow feeding a real question in, and a SECOND arrow feeding an empty box
(labelled "nothing yet") into the sources slot. The machine's output pipe is labelled "the shell" —
the same machine, same wiring, just one input temporarily empty.

**You should now be able to explain:** why `RAGPipeline._prepare` needed to solve a circular
measurement problem, and why calling the real template builder with an empty source list was a
safer solution than writing a second, parallel "shell" function.

---

## C4 — A refactor can silently drop an assumption nobody re-checked

### Do this first
Run [`d3_margin_disappears.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d3_margin_disappears.py>).
**Predict first:** a chunk is rejected by the OLD fixed rule but accepted by the NEW dynamic rule.
Given the NEW rule is supposedly "more correct," do you expect that acceptance to always be safe?

### 1. Definition + analogy
**What it is.** When you replace a hand-picked, conservative constant with a computation that's
technically more "correct" (closer to the true theoretical limit), you can accidentally remove a
SAFETY MARGIN the constant used to carry — without ever deciding to do so. The new number is more
accurate about the THEORY, but the old number was ALSO doing a second job (absorbing error) that
the new one doesn't do at all unless you explicitly ask it to.

**Analogy.** A bridge's "maximum load: 8 tons" sign, when the engineers actually calculated it
could theoretically hold 10 tons — the extra 2 tons is a deliberate safety margin for measurement
error, material wear, and surprises. If a new team later "corrects" the sign to say "10 tons,
that's the REAL calculated limit," they've made the sign more theoretically accurate while quietly
removing the cushion that was protecting against everything the original calculation didn't
account for perfectly.

### For a 14-year-old
Imagine your parents tell you "you can carry a backpack up to 15 pounds to school" — even though
they know your back can technically handle up to 20 pounds without hurting. They picked 15, not
20, ON PURPOSE, as a safety cushion — in case the bathroom scale they used to weigh your backpack
is a little bit off, or in case they misjudged something, or in case you're extra tired one day.
The gap between 15 and 20 isn't a mistake; it's insurance.

Now imagine a new babysitter takes over and says "let's be more accurate — your back can ACTUALLY
handle 20 pounds, so you can carry up to 20 pounds now." Technically, this is a MORE correct number
than 15 — it matches what your back can really handle, in theory. But the babysitter just quietly
threw away your parents' safety cushion, without ever deciding to do that on purpose. If the scale
IS a little off, or if you ARE extra tired one day, there's no more room for that — the new "more
accurate" number is actually riskier in practice, even though it's more correct in theory.

P6-M4's `CONTEXT_BUDGET_TOKENS = 2000` was the "15 pounds" rule — deliberately below the ~3260
theoretical maximum, specifically because `approx_token_count` (the chars/4 estimate) can run
higher than real tokenization. P6-M5's dynamic computation is the "20 pounds" rule — more correct
in theory, but it silently dropped that cushion, because nobody explicitly decided whether to keep
it, shrink it, or remove it — the refactor just naturally produced the full theoretical number.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "carry up to 15 pounds" (below the real 20-pound limit) | `CONTEXT_BUDGET_TOKENS = 2000` (below the ~3260 theoretical) |
| the deliberate 5-pound safety cushion | the ~1260-token deliberate margin, named explicitly in docs/prompts.md §4 |
| "let's be more accurate, use the real 20-pound limit" | the new dynamic formula: `4096 - prompt_tokens - max_tokens` |
| the cushion disappearing without anyone deciding to remove it | the margin disappearing as a side effect of the refactor, not a chosen decision |
| the scale being a little bit off some days | `count_tokens_approx`'s own known chars/4 estimation error |

### 2. Why here specifically
The old margin existed for a SPECIFIC, named reason (docs/prompts.md §4: "because
`approx_token_count` is `len(text) // 4`, an estimate... and can run higher than the estimate for
some text"). That reason didn't stop being true just because the computation became dynamic — the
chars/4 estimate is EXACTLY as imprecise today as it was before this refactor. The margin's
disappearance is a genuine, real regression in safety, even though the number it replaced is more
theoretically accurate.

### What breaks if this changes
- **Ignore this and move on:** if `approx_token_count` ever underestimates badly enough on some
  real chunk of text, the new dynamic budget (with zero cushion) could genuinely let through more
  content than actually fits the real 4096-token window — a real overflow risk that the old margin
  would have caught.
- **Silently re-add a margin without disclosing it:** would fix the safety issue but repeat the
  EXACT mistake being taught here — an undiscussed, silent change to behavior, just in the other
  direction this time.
- **The chosen response (decision.md D-2026-09-30-06): disclose the trade-off explicitly, without
  fixing it in this step:** keeps the finding visible and actionable for whoever picks it up next,
  with a clear, specific flip condition (a real measured overflow) rather than a vague "someday."

### [Interview relevance: very high — "a refactor that's more theoretically correct just silently made the system less safe" is an extremely common, high-value class of real production incident, and knowing to explicitly ask "what implicit properties did the old code have that the new code doesn't?" during any refactor is a mark of a careful engineer]

**Interview question:** *"You're replacing a conservative, hand-picked constant with a computed
value that's closer to the theoretical limit. What should you check before shipping that change?"*
**A strong answer covers:** explicitly ask WHY the original constant was conservative — what
error, uncertainty, or edge case was it protecting against; verify that reason still applies to the
new computation (it usually does — replacing HOW a number is computed doesn't change WHY the old
number was cautious); decide ON PURPOSE whether to carry the margin forward, shrink it, or drop it
— and disclose that decision explicitly rather than letting it happen as a side effect; and
recognize "more theoretically correct" and "safer in practice" are different properties that don't
automatically travel together.

### 3. Minimal code that explains the concept
The complete script, [`d3_margin_disappears.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d3_margin_disappears.py>) (run from the project root):
```python
# Minimal demo of C4: generalizing a fixed, deliberately-conservative constant into a live
# formula can silently drop the SAFETY MARGIN that constant used to carry -- P6-M5's own real,
# disclosed finding (decision.md D-2026-09-30-06), reproduced here in miniature.
REAL_CONTEXT_WINDOW = 100  # a toy "real" limit, standing in for the real 4096
OLD_FIXED_BUDGET = 25  # picked by hand, ONCE, deliberately conservative (below the ~40 theoretical)


def old_fits(chunk_size):
    return chunk_size <= OLD_FIXED_BUDGET  # a fixed number -- includes a hidden safety cushion


def new_dynamic_fits(chunk_size, prompt_tokens, max_tokens):
    budget = REAL_CONTEXT_WINDOW - prompt_tokens - max_tokens  # the FULL theoretical number
    return chunk_size <= budget  # no cushion at all -- trusts the estimate completely


chunk = 36  # a chunk the OLD conservative rule would reject, but the new rule accepts
print("chunk size (ESTIMATED, chars/4-style):", chunk)
print("OLD fixed rule (budget=25):", old_fits(chunk))
# real budget = 100 - 30 - 30 = 40
print("NEW dynamic rule (real budget=40):", new_dynamic_fits(chunk, prompt_tokens=30, max_tokens=30))

print("\nbut if the real token count ever runs 15% higher than the chars/4 ESTIMATE says:")
real_chunk_tokens = int(chunk * 1.15)  # simulates the estimate being a bit too low, for real text
print("estimated:", chunk, "vs. what the model actually sees:", real_chunk_tokens)
print("would the REAL usage still fit the REAL window (40)?", real_chunk_tokens <= 40)
print("the OLD rule's built-in cushion (25, well below 40) would have caught this.")
print("the NEW dynamic rule has no such cushion -- it trusts the estimate completely.")
```
Real output:
```
chunk size (ESTIMATED, chars/4-style): 36
OLD fixed rule (budget=25): False
NEW dynamic rule (real budget=40): True

but if the real token count ever runs 15% higher than the chars/4 ESTIMATE says:
estimated: 36 vs. what the model actually sees: 41
would the REAL usage still fit the REAL window (40)? False
the OLD rule's built-in cushion (25, well below 40) would have caught this.
the NEW dynamic rule has no such cushion -- it trusts the estimate completely.
```
The OLD rule rejected this chunk (too cautious, in this one instance). The NEW rule accepted it —
correctly, IF the estimate were exact. But the estimate wasn't exact (a realistic 15% error), and
the REAL usage (41 tokens) would have genuinely overflowed the REAL 40-token window. The old
margin would have prevented this; the new computation, as built, does not.

### The exact code I wrote (`decision.md`)
> **D-2026-09-30-06:** "The old `CONTEXT_BUDGET_TOKENS = 2000` was deliberately conservative,
> reserving ~1260 tokens of margin below the theoretical ~3260 available, specifically because
> `approx_token_count` is an estimate that can run higher than real tokenization... The new dynamic
> computation... uses the full theoretical figure with zero cushion... not built this step -- this
> wasn't one of the decisions presented before implementation, and adding an undiscussed safety
> buffer now would be a silent scope expansion; disclosing the trade-off is the right move."

**🎯 Interview-relevant lines:** the phrase "wasn't one of the decisions presented before
implementation" is the crux of the whole finding — this project's own standing process (list
decisions, get them picked, THEN implement) makes it structurally easy to notice when something
happens that WASN'T one of the agreed decisions, which is exactly how this got caught instead of
silently shipping.

### Canonical docs
- `docs/prompts.md` [section 4](../docs/prompts.md#4-context-token-budget-measured-not-guessed) —
  the original, explicit reasoning for the margin this refactor dropped.
- [decision.md](../decision.md) D-2026-09-30-06 — the full disclosure and its stated flip condition.

### Micro-exercises
1. In `d3`, change the 15% error assumption to 25%. At what error percentage does the NEW rule's
   accepted chunk (36 estimated tokens) start to genuinely overflow the real 40-token window?
   Compute it by hand, then check with code.
2. Modify `new_dynamic_fits` to accept an optional `margin: int = 0` parameter, subtracted from
   the computed budget. Set it to `5` and re-run — does the chunk from the demo still get
   accepted?
3. Using the REAL `fit_context`/`apply_budget`, compute the real available budget for
   `RAGPipeline`'s actual defaults (`max_tokens=400`, a short real question). How many tokens of
   margin would need to be re-added to match the OLD constant's ~1260-token cushion, proportionally?
4. Find another place in this project where a constant was chosen with an EXPLICIT stated margin
   (hint: check `docs/prompts.md` section 4 itself, or `PROBE_TIMEOUT_SECONDS` in
   `src/generation/setup.py`). What was each margin protecting against?
5. Design (on paper) a test that would FAIL if someone silently changed `CONTEXT_WINDOW_TOKENS`
   from 4096 to some other number without updating anything else. Does one already exist in
   `tests/orchestration/test_prompts.py`?

**How it works internally.** There's no code mechanism here at all — this concept is entirely
about the PROCESS of refactoring, not a runtime behavior. The "internal" thing to understand is
that a constant like `CONTEXT_BUDGET_TOKENS = 2000` was doing TWO jobs at once (representing a
computed limit, AND absorbing estimation error), and a refactor that changes HOW the limit is
computed can accidentally only replace the FIRST job, silently losing the second.

### How to validate this yourself
There is no automated test that "validates" this finding, by design — it's a DISCLOSED, not-yet-
resolved trade-off. The way to validate the finding ITSELF is what `d3` does: construct a concrete
scenario where the old margin would have caught a real overflow that the new computation wouldn't,
using the REAL measured numbers where possible.

### How to visualize this
Draw a ruler from 0 to 100 (the real window). Mark the OLD budget (25) as a solid green line, well
short of the true theoretical maximum (40, marked with a dashed line). Then draw the NEW budget as
a solid line placed exactly ON the dashed theoretical line — visually, the "safety gap" between the
solid and dashed lines has vanished.

**You should now be able to explain:** why replacing a conservative constant with a more accurate
computation isn't automatically a pure improvement, and why this project chose to disclose that
trade-off explicitly rather than silently keep or silently fix it.

---

## C5 — Isolating a live failure via minimal reproduction

### Do this first
Run [`d4_isolate_via_minimal_repro.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d4_isolate_via_minimal_repro.py>).
**Predict first:** "layer 1" runs a full, multi-step pipeline and it fails. "Layer 2" runs ONLY the
first step of that same pipeline, alone. Do you expect it to still fail, or to succeed now that
everything else has been removed?

### 1. Definition + analogy
**What it is.** When something fails inside a complex system with many moving parts, the fastest
way to find out WHERE the real problem is (not just THAT something failed) is to strip away pieces
one at a time — or jump straight to the smallest possible piece — until you find the smallest thing
that STILL reproduces the failure. If a tiny, isolated piece still fails the same way, the problem
is in that piece (or something it depends on), not in anything you removed.

**Analogy.** A string of old Christmas lights where the whole strand won't turn on. You don't start
by rewiring the house — you unplug the strand from everything else, plug it DIRECTLY into a single
known-good outlet, and see if it lights up at all. If it still doesn't, the problem is in the
strand itself (or the outlet), not in the extension cords, the timer switch, or anything else you
just removed from the picture.

### For a 14-year-old
Imagine your bike won't move properly, and you're not sure why — maybe it's the pedals, the chain,
the back wheel, or something else. Instead of guessing and replacing random parts, a smart approach
is to isolate: lift the back wheel off the ground and pedal by hand. Does the chain move the wheel
at all? If yes, the chain and pedals are probably fine, and the problem is likely the wheel touching
the ground somehow. If the wheel doesn't even spin when you pedal by hand, you've just PROVEN the
problem is somewhere in the chain or pedals — you don't need to think about the wheel touching the
ground at all anymore. Each test you do removes a WHOLE category of possible causes, until only a
small handful of suspects are left.

That's exactly what happened live in this project. `RAGPipeline.query()` timed out. Instead of
guessing ("maybe it's the budget code I just wrote?" or "maybe it's retrieval?"), the test was:
call `LocalLLM.generate` DIRECTLY, with a tiny `max_tokens=10`, completely bypassing `RAGPipeline`,
retrieval, and every line of P6-M5's own new code. It STILL timed out, identically. That one test
proved, immediately and completely, that NONE of this step's own code was the cause — the problem
had to be Ollama itself, on this specific host, right now.

**Mapping the story back to the real debugging:**
| In the story | In the real debugging |
|---|---|
| the whole bike not moving | `RAGPipeline.query()`'s live test timing out |
| lifting the wheel, pedaling by hand | calling `LocalLLM.generate` directly, no `RAGPipeline` |
| the chain still not turning the wheel | the direct call STILL timing out, identically |
| "the problem isn't the wheel touching the ground" | "the problem isn't `fit_context`/`apply_budget`/`RAGPipeline`'s own code" |
| checking the chain and pedals next | checking `docker top`, finding the stuck 99%-CPU runner process |

### 2. Why here specifically
P6-M5 touched real, working code (`apply_budget`, `RAGPipeline._prepare`) — when the live tests
failed, the NATURAL first suspicion is "did my refactor break something?" Minimal reproduction
answered that question definitively and fast, instead of hours spent re-reading `fit_context`'s
logic looking for a bug that was never there. This is the SAME "measure, don't assume" instinct
this entire project has used since Phase 4 — applied here to debugging instead of to a design
decision.

### What breaks if this changes
- **Assume the newest code is guilty without checking:** would have wasted real time re-reading and
  re-testing `fit_context`/`apply_budget` — code that was, in fact, already proven correct by 96
  passing hermetic tests — instead of looking at the actual host/Ollama state.
- **Retry the SAME full, complex test repeatedly without simplifying:** each retry takes ~4-6
  minutes and gives only a yes/no answer, with no information about WHERE in the pipeline the
  problem is — exactly what happened in this exact session before the isolation step was taken.
- **Stop investigating the moment ANY test fails, without isolating first:** would have led to
  either wrongly blaming P6-M5's own code, or wasting effort "fixing" code that was never broken.

### [Interview relevance: high — "how do you debug an intermittent failure in a complex system" is an extremely common systems/SRE-adjacent interview question, and describing a concrete, methodical isolation process (not just "I'd add print statements") is what separates a strong answer from a weak one]

**Interview question:** *"A test in a system with many layers (retrieval, budgeting, generation)
starts failing intermittently. How do you find out which layer is actually responsible?"*
**A strong answer covers:** don't assume the newest or most recently-changed code is guilty without
evidence; construct the SMALLEST possible reproduction that still triggers the failure, removing
one layer/dependency at a time (or jumping straight to the most isolated possible call); if the
minimal reproduction STILL fails, the problem is in that minimal piece (or something below it),
which usually rules out most of the system at once; and once isolated, use the RIGHT tool for that
specific layer (here: `docker top`, showing a stuck process — not more application-level retries).

### 3. Minimal code that explains the concept
The complete script, [`d4_isolate_via_minimal_repro.py`](<../practice codes/phase 6/m5_context_window_management_deep_dive/d4_isolate_via_minimal_repro.py>) (run from the project root):
```python
# Minimal demo of C5: isolating a failure by testing progressively SMALLER pieces -- exactly
# the method used live in this project to prove a real Ollama timeout was NOT a bug in P6-M5's
# own code. Simulates a flaky "service" and walks through the same isolation process.
import random  # used only to make this toy "service" fail sometimes, like a real one might

random.seed(2)  # makes this demo's "flakiness" reproducible for anyone reading the output


def flaky_service(work_units):  # simulates something that sometimes fails under "load"
    if work_units > 5:  # pretend heavier calls are more likely to hit the problem
        raise TimeoutError(f"timed out doing {work_units} units of work")
    return f"done {work_units} units"  # succeeds when the work is small enough


def full_pipeline():  # the ORIGINAL, full-featured call -- lots of moving parts, like RAGPipeline
    step1 = flaky_service(work_units=8)  # retrieval-shaped work
    step2 = flaky_service(work_units=2)  # budgeting-shaped work (cheap, pure Python in reality)
    return step1, step2


print("--- layer 1: run the FULL pipeline ---")
try:
    print("result:", full_pipeline())
except TimeoutError as e:
    print(f"FAILED: {e}")  # a failure here alone doesn't say WHERE the problem is

print("\n--- layer 2: call just the FIRST step alone (removes everything else) ---")
try:
    print("result:", flaky_service(work_units=8))
except TimeoutError as e:
    print(f"FAILED: {e}  <- still fails, with NOTHING else involved")
    # this is the key moment: the SAME failure, with zero pipeline code in the loop

print("\n--- layer 3: shrink the work until it stops failing ---")
for units in [8, 6, 5, 3]:  # walks from "known to fail" down toward "known to succeed"
    try:
        result = flaky_service(work_units=units)
        print(f"work_units={units}: SUCCESS ({result})")
    except TimeoutError as e:
        print(f"work_units={units}: FAILED ({e})")

print("\nConclusion: the failure reproduces with ONE call, no pipeline, no other code --")
print("it's the service itself struggling under load, not a bug in the code calling it.")
```
Real output:
```
--- layer 1: run the FULL pipeline ---
FAILED: timed out doing 8 units of work

--- layer 2: call just the FIRST step alone (removes everything else) ---
FAILED: timed out doing 8 units of work  <- still fails, with NOTHING else involved

--- layer 3: shrink the work until it stops failing ---
work_units=8: FAILED (timed out doing 8 units of work)
work_units=6: FAILED (timed out doing 6 units of work)
work_units=5: SUCCESS (done 5 units)
work_units=3: SUCCESS (done 3 units)

Conclusion: the failure reproduces with ONE call, no pipeline, no other code --
it's the service itself struggling under load, not a bug in the code calling it.
```

### The exact commands I ran (this project's real debugging session)
```bash
# layer 2 equivalent: bypass RAGPipeline entirely, call LocalLLM directly
.venv/Scripts/python.exe -c "
from src.orchestration.llm import build_llm
llm = build_llm('http://localhost:11434')
reply = llm.generate('What is the capital of France? Reply with just the city name.',
                      temperature=0.0, max_tokens=10)
print(reply)
"
# -> FAILED after 120.0s: ReadTimeout('timed out')  -- proves it's not RAGPipeline's own code

# the tool that revealed WHY, once isolation pointed at Ollama itself:
docker top advancerag-ollama-1
# -> a single 'ollama runner' process pegged at 99% CPU for 38+ minutes, never completing
```

**🎯 Interview-relevant lines:** the direct `LocalLLM.generate(...)` call is the REAL "layer 2" from
this project's own live debugging — a five-line script that definitively separated "is this my
code" from "is this the infrastructure," in under two minutes, compared to the 4-6 minutes each
full-pipeline retry cost with no new information gained.

### Canonical docs
- [git-bisect documentation](https://git-scm.com/docs/git-bisect) — Git's own built-in tool for
  exactly this "shrink the search space by half, repeatedly" methodology, applied to finding which
  commit introduced a bug — the same underlying idea as C5, formalized into a tool.
- [notes/step-6.5-context-window-management.md](../notes/step-6.5-context-window-management.md)
  Finding 4 — this project's own written record of the real isolation process.

### Micro-exercises
1. In `d4`, change `work_units=8` to `work_units=4` in `full_pipeline`'s `step1` call. Predict,
   then check: does "layer 1" still fail? What would that tell you about where to look next?
2. Add a THIRD step to `full_pipeline` (`step3 = flaky_service(work_units=1)`) and deliberately
   introduce a bug in `full_pipeline`'s OWN code (e.g., call `step3` before `step1`). Design a test
   that would catch THIS kind of bug — one that minimal reproduction alone wouldn't catch, since
   `flaky_service` itself isn't broken here.
3. Using `docker top` (if you have a running container of any kind), find a real process's CPU
   TIME column. How would you tell, just from that output, whether a process has been "stuck"
   doing real work for far too long versus legitimately busy for a normal amount of time?
4. Write a one-paragraph incident report (as if for a teammate) describing this session's real
   Ollama-timeout investigation: what was observed, what was isolated, what was concluded, and
   what remains unresolved. Keep it under 100 words.
5. Think of a time (in any system, not just code) where you fixed the wrong thing because you
   didn't isolate the problem first. What would minimal reproduction have told you sooner?

**How it works internally.** There's no special tool required — `docker top` simply reads the
same process table the host's own `ps`/Task Manager would show, filtered to one container's
namespace; a direct Python call is simply calling one function instead of many. The "internal
mechanism" here is entirely a DISCIPLINE: always prefer removing variables over adding them when
something unexplained fails, and prefer a fast, cheap isolation test over a slow, expensive retry
of the whole thing.

### How to validate this yourself
The real validation already happened: `test_construction_defaults_match_the_published_
recommendations` and 95 other hermetic tests in `tests/orchestration/` pass consistently and
instantly, with zero dependency on Ollama's live state — proving P6-M5's own logic was never the
suspect, independent of whatever is happening with the live Ollama process on this host right now.

### How to visualize this
Draw a large circle labelled "everything that could be wrong" containing several smaller circles:
"my new code," "RAGPipeline," "retrieval," "Ollama itself," "the host machine." Draw an X through
"my new code" and "RAGPipeline" the moment the direct `LocalLLM.generate` call reproduces the exact
same failure — each isolation test shrinks the circle of suspects, without needing to prove
anything about the REMAINING suspects yet.

**You should now be able to explain:** why finding the SMALLEST reproduction of a failure is
usually faster than repeatedly retrying the full, complex scenario, and how this exact technique
was used, for real, to prove P6-M5's own code wasn't the cause of a live test failure.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Trace the real numbers by hand.** Using the real `SYSTEM_PROMPT` (386 tokens, verified) and a
   question of your choosing, compute the real shell's token count by hand (roughly: 386 + your
   question's `len//4` + a few labels), then compute the real available budget at
   `max_tokens=400`. Check your arithmetic against `fit_context`/`apply_budget`'s real behavior.
2. **Break C1 on purpose.** Change `CONTEXT_WINDOW_TOKENS` in `prompts.py` to `2000` (pretending
   the real model only had a 2000-token window). Run `tests/orchestration/test_prompts.py` — which
   tests fail, and does their failure message clearly point at the changed constant?
3. **Break C2 on purpose.** Change `_greedy_best_fit`'s type hints so `items` is typed as
   `Sequence[DocumentChunk]` specifically (no longer generic). Run mypy against `apply_budget`
   (which passes `RankedChunk`s) — what error appears, and does it clearly explain the mismatch?
4. **Break C3 on purpose.** In `rag.py`, change the `shell` line to `shell = SYSTEM_PROMPT` (drop
   the real question entirely). Run `tests/orchestration/test_rag.py` — does anything fail? If
   not, what does that reveal about how SMALL the question's own token contribution usually is
   relative to the system prompt?
5. **Reproduce C4's finding with the real numbers.** Using `count_tokens_approx`, compute the real
   available budget for a realistic scenario, then compute what a 15% chars/4 estimation error
   would mean for a chunk sized right at that boundary. Does this match the toy demo's own
   conclusion?
6. **Reproduce C5's isolation, for real, once Ollama is behaving normally.** Run
   `tests/orchestration/test_rag.py -m live -k live_query`. If it passes, you've confirmed the live
   integration works end to end. If it fails again, run the direct `LocalLLM.generate` isolation
   command from C5 yourself, and check `docker top` — do you see the same stuck-runner pattern, or
   something different this time?
7. **Put it all back together.** Undo every change from steps 2-4. Re-run
   `tests/orchestration/test_prompts.py` and `tests/orchestration/test_rag.py` (non-live) and
   confirm all 96 hermetic tests pass again, exactly as they did before you started breaking things.

If you can predict each break's exact failure BEFORE running it, and explain — in one sentence per
concept — why C1 through C5 each matter independently of the other four, you've reached the level
of understanding this deep dive set out to build.
