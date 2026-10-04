# 22 — Graph Integration Tests: Deep Dive (P4-M6)

> Every output block in this document was produced on 2026-09-22 by running the code: against a
> live Neo4j **5.26.0 community** + APOC (`docker compose up -d neo4j`) already holding the real
> 223-chunk corpus from P4-M5, and the real `tests/test_graph.py`. All scripts are in
> [`practice codes/phase 4/m6_graph_integration_tests_deep_dive/`](../practice%20codes/phase%204/m6_graph_integration_tests_deep_dive/).
> Run any of them from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 4/m6_graph_integration_tests_deep_dive/d0_test_pyramid_gap.py"
> ```
>
> `d1` needs `pytest` (run it with `pytest ... -v -s`, shown below); `d3` needs
> `docker compose up -d neo4j` and is read-only (no `DETACH DELETE` — it only proves a scan is
> risky, it never runs one for real).

---

## 0. Plain-language summary

`tests/test_graph.py` is not a test of the graph *extractor*, the graph *store*, or the graph
*resolver* — those already have 177 tests between them. It is a test of whether those three, wired
together, actually work: a small, throwaway corpus goes in one end, and a real, queryable graph
comes out the other, capable of answering real questions. It exists because P4-M5 proved that 191
passing unit tests can still hide a broken composition — the `DESCRIBED_IN` bug slipped past every
one of them and was only caught by running the real pipeline.

```
tests/test_graph.py
   │
   ├─ hermetic (no database)   3 chunks -> real GraphExtractor -> real resolve_entities
   │                            -> assert the PLAN has the right nodes and edges        (C1, C2)
   │
   └─ live (skips if Neo4j down)  the same plan -> real GraphStore -> real Neo4j
        │                          -> assert real QUERIES return real answers            (C4, C5)
        └─ fixture named "M6Fixture..." so it can never collide with, or be mistaken
           for, anything the real corpus already loaded                                  (C3)
```

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | The test pyramid: why correct units can still compose into a wrong system | 🔴 **Core engineering** (interview-heavy) |
| C2 | pytest fixtures: setup, teardown, and `scope` | 🟡 Engineering (everyday pytest) |
| C3 | A test fixture has to survive the REAL code's assumptions, not just look fake | 🔴 **Core RAG-adjacent** (testing text-parsing systems) |
| C4 | Cleanup by exact identity, never by scanning, on shared mutable state | 🟡 Engineering (interview-heavy) |
| C5 | Multi-hop connectivity: entities linked with no direct edge between them | 🔴 **Core RAG** (graph retrieval) |

---

## C1 — The test pyramid: correct units, wrong composition

### Do this first
Before reading on, run
[`d0_test_pyramid_gap.py`](../practice%20codes/phase%204/m6_graph_integration_tests_deep_dive/d0_test_pyramid_gap.py).
**Predict** first: if `apply_discount` and `add_shipping` each pass their own unit test, can calling
them in the wrong order still produce a wrong final price?

### 1. Definition + analogy
**What it is.** The **test pyramid** is a way of thinking about test coverage in three layers: many
fast **unit tests** (one function, in isolation) at the bottom, fewer **integration tests** (several
real components wired together) in the middle, and very few **end-to-end tests** (the whole system,
as a user would touch it) at the top. The point isn't "more tests are always better" — it's that each
layer catches a DIFFERENT class of bug. A unit test can only ever prove that ONE piece does what it
promises; it can never prove that piece was called at the right time, with the right input, by the
right caller.

**Analogy.** Two chefs each perfectly cook their one dish — a steak, a sauce. Both pass their own
"taste test." Whether the sauce goes ON the steak before or after it rests is a decision NEITHER chef
individually tests; only someone tasting the finished plate would notice it came out wrong.

### For a 14-year-old
Imagine two kids building a soapbox derby car for a school project. One kid is in charge of the
wheels — they build wheels that spin perfectly smoothly on a test rig, no wobble, no friction. Great
wheels, tested and proven. The other kid is in charge of the steering — they build a steering
mechanism that turns exactly the right amount when you turn the wheel, tested on its own workbench,
also perfect. Both parts, separately, are 100% correct.

Now they bolt the two parts together — and the car pulls hard to the left every time you try to go
straight, because the steering mechanism was mounted backwards. Neither kid's individual test would
EVER catch that, because neither test ever looked at the finished car. The wheel test only checks the
wheels. The steering test only checks the steering, on its own workbench, disconnected from the actual
wheels it will be bolted to. You only find the "pulls left" bug by putting the WHOLE car together and
actually rolling it down the driveway.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the wheels, tested alone on a rig | `GraphExtractor`, tested alone in `tests/graph/test_extractor.py` (62 tests) |
| the steering, tested alone on a workbench | `GraphStore`, tested alone in `tests/retrieval/test_graph_store.py` (40 tests) |
| bolting the two together and rolling it down the driveway | `tests/test_graph.py`'s live tests: real extractor output written through the real store |
| "pulls left" — invisible until the whole car moves | the real `DESCRIBED_IN` bug: 191 unit tests passed, only a live run of the composed system found it |

### 2. Why here specifically
Every one of the 177 unit tests that already existed for the graph subsystem checks ONE component's
contract in isolation. None of them ever asked "if I feed real extractor output into the real store
and then ask it a real question, do I get the right answer back?" — that composition-level question
is exactly what P4-M5's bug lived in, and exactly what `tests/test_graph.py` was built to close.

### 3. Minimal code
`d0_test_pyramid_gap.py` — two individually correct functions, wired in the wrong order:
```python
# Minimal demo of C1: two functions can each pass their OWN unit test, and still be
# WIRED together incorrectly. Only a test that checks the composed result -- an
# integration test -- catches that. No pytest needed: plain asserts, printed by hand.
def apply_discount(price, pct):
    # Subtracts a percentage off a price. Tested alone, in isolation, below.
    return price - price * pct  # e.g. 100 with 20% off -> 80.0


def add_shipping(price, flat_fee):
    # Adds a FLAT fee on top of a price -- not a percentage, so order will matter.
    return price + flat_fee  # e.g. 100 plus a $5 fee -> 105.0


def test_apply_discount_alone():
    # A UNIT test: one function, checked against its own, narrow contract.
    assert apply_discount(100, 0.20) == 80.0  # 20% off 100 is 80


def test_add_shipping_alone():
    # Another UNIT test: the other function, checked against ITS own contract.
    assert add_shipping(100, 5) == 105.0  # $5 shipping on top of 100 is 105


test_apply_discount_alone()  # run it -- passes
test_add_shipping_alone()  # run it -- passes
print("Both unit tests pass: each function does exactly what IT promises.")

# The business rule this checkout is supposed to follow: the discount applies to the
# PRODUCT price only -- shipping is added afterwards, at full price, never discounted.
correct_order = add_shipping(apply_discount(100, 0.20), 5)  # discount first, then shipping
wrong_order = apply_discount(add_shipping(100, 5), 0.20)  # shipping first, then discount

print(f"discount-then-shipping (the intended rule) : {correct_order}")  # (100*0.8)+5 = 85.0
print(f"shipping-then-discount (wired backwards)   : {wrong_order}")  # (100+5)*0.8 = 84.0


# THIS is the integration test: it checks the WIRING, not either function alone.
def test_checkout_never_discounts_the_shipping_fee():
    total = add_shipping(apply_discount(100, 0.20), 5)
    assert total == 85.0  # would FAIL if someone swapped the call order above


test_checkout_never_discounts_the_shipping_fee()  # passes only for the correct wiring
print("\nBoth functions were individually correct the whole time.")
print("Only a test of the COMPOSED result can tell the two wirings apart.")
```
Real output:
```
Both unit tests pass: each function does exactly what IT promises.
discount-then-shipping (the intended rule) : 85.0
shipping-then-discount (wired backwards)   : 84.0

Both functions were individually correct the whole time.
Only a test of the COMPOSED result can tell the two wirings apart.
```
85.0 versus 84.0 — a $1 bug that no unit test could ever see, because neither function is "wrong."

### The exact code I wrote (`tests/test_graph.py`)
```python
# lines 1-20 (module docstring)
"""Graph tests across components: chunks -> extraction -> resolution -> Neo4j -> query.

Two kinds, deliberately kept apart, the same split as tests/test_retrieval.py:
...
Per-component unit tests live in tests/graph (extraction: 62 tests, dedup: 75 tests) and
tests/retrieval/test_graph_store.py (storage and query, against hand-built records: 40
tests); tests/graph/test_ingest.py already proves the full 223-chunk corpus loads with
the right COUNTS. None of them prove a query against data the real extractor produced
returns the right ANSWER -- which is the one gap P4-M5's own DESCRIBED_IN bug exposed  # 🎯 INTERVIEW
(every unit test passed; only a live run caught it). This file closes that gap, and nothing
else: it does not re-verify what the files above already cover.
"""
```
**🎯 Interview-relevant line:** the sentence naming the gap explicitly. Being able to say, in an
interview, WHY a new test file exists — not just what it tests — is what separates "I wrote tests"
from "I understood what was untested."

### 4. What breaks if this changes
- **Delete this file, trust the 191 unit tests alone:** the next composition-level bug (a different
  wrong wiring between extraction, resolution and storage) would ship the same way `DESCRIBED_IN` did
  — every unit test green, the real system wrong.
- **Turn this file into more unit tests instead:** you'd have 198 tests instead of 191+7, and the
  actual gap (composition) would still be untested.

### [Interview relevance: high — "unit vs. integration testing, and what each catches" is one of the most common testing questions in any engineering interview]

**Interview question:** *"Your team has 95% unit test coverage and a bug still reached production. How
is that possible, and what would you add?"*
**A strong answer covers:** that coverage percentage measures LINES executed, not COMBINATIONS of
components exercised together; that a bug can live entirely in the WIRING between two correct units
(the discount/shipping order example, or a real one they've hit); and that the fix is deliberately
adding integration tests that exercise real components together, not just more unit tests of the same
pieces — plus a concrete example of a composition-level bug they've personally seen or fixed.

### Canonical docs
- Fowler, M., ["TestPyramid"](https://martinfowler.com/bliki/TestPyramid.html)
- Cohn, M., *Succeeding with Agile*, ch. 15 (the original diagram)
- [tests/test_retrieval.py](../tests/test_retrieval.py) — this project's own precedent

### Micro-exercises (absolute-beginner, learn by doing)
1. Run `d0_test_pyramid_gap.py`. Change `add_shipping`'s flat fee from `5` to `0`. Do the two orders
   still disagree? Why or why not — what does that tell you about WHEN order stops mattering?
   *Hint:* with a $0 fee, `add_shipping` does nothing at all.
2. Write a THIRD function, `apply_loyalty_points(price, points)` that subtracts a flat amount. Compose
   all three in two different orders and find a case where the order changes the result.
3. In your own words (no code), describe a real bug you've seen (in this project or elsewhere) where
   two individually-correct pieces were wired together wrong. If you can't think of one, ask
   yourself: has a test ever passed while the actual feature was broken?
4. Count: in this project, how many total unit tests exist for the graph subsystem
   (`grep -c "^def test_" tests/graph/*.py tests/retrieval/test_graph_store.py`)? How many
   integration tests does `tests/test_graph.py` add? What percentage of the total is integration?
5. Read the `DESCRIBED_IN` bug story in [docs/21](21%20graph_ingestion_deep_dive.md#c3--a-node-was-created-is-not-this-is-its-home-page).
   Which specific unit test, if it had existed, WOULD have caught it without needing a live database?
   (Trick question — think carefully about what such a test would need to know.)

**How it works internally.** Nothing here is pytest-specific — the demo runs with plain `assert`
statements and no test runner at all, because the test-pyramid IDEA is about what each test's SCOPE
covers, not about any particular tool.

### How to validate this yourself
Run the script and read the two totals side by side; then delete
`test_checkout_never_discounts_the_shipping_fee` and ask: with only the two unit tests left, would
anyone notice if a future edit swapped the call order in the real checkout code?

### How to visualize this
Draw the pyramid by hand: a wide base labelled with the 177 existing unit-test names (or just their
counts, 62/75/40), a narrower middle band labelled "tests/test_graph.py (7)" and "tests/graph/test_ingest.py
(14)", and a top you leave empty — this project doesn't have a true end-to-end test yet (one that
starts from a user's typed question). That empty top is itself worth noticing.

**You should now be able to explain:** why 100% unit test coverage does not mean a system is correct,
and specifically what class of bug an integration test catches that a unit test structurally cannot.

---

## C2 — pytest fixtures: setup, teardown, and `scope`

### Do this first
Before reading on, run:
```bash
.venv/Scripts/python -m pytest "practice codes/phase 4/m6_graph_integration_tests_deep_dive/d1_fixture_scope.py" -v -s
```
**Predict** first: if two tests both ask for a `scope="module"` fixture, does its setup code run
once, or twice?

### 1. Definition + analogy
**What it is.** A pytest **fixture** is a function decorated with `@pytest.fixture` that pytest calls
FOR you and hands the result to any test that lists it as a parameter, by NAME — no test ever calls
it directly. `scope` controls how often pytest re-runs the setup: `"function"` (the default) re-runs
it for every single test; `"module"` runs it once and reuses the same result for every test in that
file. A fixture written with `yield` splits into two halves: everything before `yield` is setup,
everything after is teardown, and teardown runs even if the test in between fails or raises.

**Analogy.** A `scope="function"` fixture is like getting a brand-new sheet of scratch paper for every
single maths problem. A `scope="module"` fixture is like sharing one whiteboard for the whole class —
someone writes the reference table on it ONCE at the start of the lesson, and every problem after that
just reads off the same board instead of redrawing it.

### For a 14-year-old
Picture a school science class where, before each experiment, someone has to set up equipment. There
are two kinds of setup: for some experiments, EVERY student needs their OWN fresh beaker of chemical —
if two students shared one beaker, one student's leftover chemical would contaminate the next
student's result. That's a "fresh every time" setup. But for other things — like the room's Bunsen
burner gas supply, which takes five minutes to warm up and is identical no matter who uses it — it
would be silly to turn it off and re-warm it for every single student. You warm it up ONCE at the
start of class, and everyone shares that same warmed-up supply for the whole lesson.

Pytest fixtures work exactly like this. Some setup (like building a small test database) is cheap and
needs to be FRESH for every test, so nothing one test does can leak into the next — that's
`scope="function"`. Other setup (like extracting and resolving 3 fixture chunks, which never changes
no matter which test looks at it) is safe to share, and computing it fresh for every single test would
just waste time re-doing identical work — that's `scope="module"`, the shared Bunsen burner.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| a fresh beaker for every student | a `scope="function"` fixture (the pytest default — not actually used in `tests/test_graph.py`, but shown in the demo) |
| the shared, once-warmed Bunsen burner | the `plan` fixture, `scope="module"` — computed once, reused by all 3 hermetic tests |
| the gas line staying on until the last student is done | the `live_store` fixture's `yield` keeping Neo4j's fixture data alive until the LAST live test finishes |
| turning the gas off after the last student | the `finally:` block after `yield store` — teardown, guaranteed to run |
| "everyone reads off the same board" | all 4 live tests calling `live_store.multi_hop_search`/`search_related` against the SAME written data, never re-writing it |

### 2. Why here specifically
Writing the fixture chunks, running the real extractor, and resolving entities takes real (if small)
work, and it's completely deterministic — doing it 7 times (once per test) would be pure waste with
zero benefit. `scope="module"` computes it once and lets all 7 tests share the result; `live_store`
additionally depends on `plan`, so writing to Neo4j also happens exactly once per test run, not once
per test.

### 3. Minimal code
`d1_fixture_scope.py` — a fixture that reruns every test versus one shared across the whole file:
```python
# Minimal demo of C2: a pytest FIXTURE is a function pytest calls for you and hands the
# result to any test that asks for it by NAME. `scope` decides how often it re-runs.
# Run with: .venv/Scripts/python -m pytest <this file> -v -s
import pytest

calls = {"function_scoped": 0, "module_scoped": 0}  # counts how many times each fixture body actually ran


@pytest.fixture()  # default scope: "function" -- runs fresh for EVERY test that uses it
def function_scoped():
    calls["function_scoped"] += 1  # bump the counter every time this fixture's body runs
    print(f"\n  [setup]  function_scoped body ran (call #{calls['function_scoped']})")
    yield "fresh-every-time"  # this is the VALUE the test receives as its argument
    print("  [teardown] function_scoped cleanup")  # runs after the test, every time


@pytest.fixture(scope="module")  # runs ONCE per test file, its result is reused after that
def module_scoped():
    calls["module_scoped"] += 1  # bump the counter -- this should only ever reach 1
    print(f"\n  [setup]  module_scoped body ran (call #{calls['module_scoped']})")
    yield "computed-once"  # every test in this file gets the SAME object back
    print("  [teardown] module_scoped cleanup")  # runs once, after the LAST test uses it


def test_one(function_scoped, module_scoped):
    print(f"  test_one received: {function_scoped!r}, {module_scoped!r}")
    assert function_scoped == "fresh-every-time"


def test_two(function_scoped, module_scoped):
    print(f"  test_two received: {function_scoped!r}, {module_scoped!r}")
    assert module_scoped == "computed-once"  # the SAME value test_one saw, not a new one


def test_three_confirms_the_counts():
    assert calls == {"function_scoped": 2, "module_scoped": 1}
```
Real output (`pytest ... -v -s`):
```
d1_fixture_scope.py::test_one
  [setup]  module_scoped body ran (call #1)

  [setup]  function_scoped body ran (call #1)
  test_one received: 'fresh-every-time', 'computed-once'
PASSED  [teardown] function_scoped cleanup

d1_fixture_scope.py::test_two
  [setup]  function_scoped body ran (call #2)
  test_two received: 'fresh-every-time', 'computed-once'
PASSED  [teardown] function_scoped cleanup

d1_fixture_scope.py::test_three_confirms_the_counts PASSED  [teardown] module_scoped cleanup

3 passed in 0.04s
```
`function_scoped` ran **twice** — once per test. `module_scoped` ran **once**, and its teardown only
fires at the very end, after `test_three` — the last test in the file to (indirectly) depend on it.

### The exact code I wrote (`tests/test_graph.py`)
```python
# lines 98-115
@pytest.fixture(scope="module")                                    # computed once, shared by all 7 tests
def plan() -> tuple[list[GraphNode], list[GraphEdge]]:
    chunks = _fixture_chunks()
    extractor = GraphExtractor.from_chunks(chunks)                  # closed lists read from THESE 3 files only
    extractions = extractor.extract_all(chunks)
    nodes = [node for extraction in extractions for node in extraction.nodes]
    edges = [edge for extraction in extractions for edge in extraction.edges]

    resolution = resolve_entities(
        nodes, known_names={"Employee": (EMPLOYEE,), "Company": (CLIENT,)}
    )
    edges = remap_edges(edges, resolution.alias_map)
    return resolution.nodes, edges                                  # 🎯 no yield -- nothing to tear down

# lines 198-220
@pytest.fixture(scope="module")
def live_store(plan):                                               # 🎯 one fixture depending on another
    reason = neo4j_down()
    if reason:
        pytest.skip(reason)                                         # skip, don't fail, when Neo4j is down
    nodes, edges = plan
    with build_driver() as driver:
        store = GraphStore(driver, verify_schema=True)
        for node in nodes:
            store.upsert_node(node)                                 # SETUP: write the fixture once
        for edge in edges:
            store.upsert_edge(edge)
        try:
            yield store                                             # 🎯 every live test runs HERE, in the middle
        finally:
            with driver.session(database="neo4j") as session:       # TEARDOWN: always runs, pass or fail
                for node in nodes:
                    pattern = ", ".join(f"{k}: ${k}" for k in node.key)
                    session.run(f"MATCH (n:{node.label} {{{pattern}}}) DETACH DELETE n", **node.key)
```
**🎯 Interview-relevant lines:** `plan` has no `yield` at all (it returns a value — there's nothing to
clean up for a pure computation), while `live_store` uses `yield` inside a `try/finally` specifically
BECAUSE it has a side effect (writing to Neo4j) that must be undone. And `live_store(plan)` — a
fixture can depend on another fixture by naming it as its own parameter, exactly like a test does.

### 4. What breaks if this changes
- **Make `plan` function-scoped:** all 3 hermetic tests still pass, but the extractor and resolver run
  3 separate times for identical input — wasted, if harmless, work. If `live_store` also became
  function-scoped, Neo4j would be written to and swept 4 separate times instead of once.
- **Remove the `finally:`** around the cleanup: a failing assertion in any live test would skip
  cleanup entirely, leaving `M6Fixture` nodes in the real, shared Neo4j.

### [Interview relevance: high — pytest fixtures, and specifically the setup/yield/teardown pattern, come up in almost every Python testing interview]

**Interview question:** *"What's the difference between `@pytest.fixture` with `return` and one with
`yield`, and when would you use each?"*
**A strong answer covers:** `return` gives a fixture with no teardown — appropriate for a pure value
with no side effect to clean up (like this project's `plan`); `yield` splits the function into
setup-then-teardown, with the code after `yield` guaranteed to run even if the test raises, which
matters whenever the fixture has a side effect (a file, a database write, a network connection) that
must be undone regardless of whether the test passed.

### Canonical docs
- [pytest fixtures documentation](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest fixture scope reference](https://docs.pytest.org/en/stable/how-to/fixtures.html#fixture-scopes)

### Micro-exercises
1. Run `d1_fixture_scope.py` with `pytest -v` (no `-s`). What output disappears? Why does pytest hide
   `print()` output by default?
2. Change `module_scoped` to `scope="function"`. Re-run with `-v -s`. How many times does its setup
   run now, and does `test_three`'s assertion still pass?
3. Add a THIRD test, `test_four`, that also asks for `module_scoped`. Does its call count go up?
4. Make `function_scoped` raise an exception instead of `yield`-ing. Does its `print("[teardown]...")`
   line still run? What does that tell you about `return`/raise versus `yield` inside a fixture?
5. In `tests/test_graph.py`, what would happen if `live_store` were declared WITHOUT depending on
   `plan` (i.e., it built its own fixture chunks from scratch instead of reusing `plan`'s)? Would the
   hermetic and live tests still be testing the SAME data?

**How it works internally.** Pytest inspects a test function's PARAMETER NAMES and matches each one
against a fixture of the same name, resolving the whole dependency graph (fixtures depending on
fixtures) before running anything. `yield`-based fixtures are Python generators under the hood: pytest
calls `next()` to run the setup half, then calls it again (after the test finishes) to resume execution
past the `yield` and run the teardown half.

### How to validate this yourself
Run the demo with `-v -s` and count the `[setup]` lines by eye against the number of tests — the
count IS the proof, no special tool needed.

### How to visualize this
`pytest --setup-show tests/test_graph.py -m "not live"` prints a tree showing exactly when each
fixture's SETUP and TEARDOWN run relative to each test — run it and watch `plan`'s setup appear once,
before the first test, and its teardown (if it had one) appear once, after the last.

**You should now be able to explain:** the difference between `scope="function"` and `scope="module"`,
why `yield`-based fixtures guarantee teardown, and why `plan` doesn't need `yield` at all.

---

## C3 — A fixture has to survive the real code's assumptions

### Do this first
Before reading on, run
[`d2_fixture_must_survive_real_regexes.py`](../practice%20codes/phase%204/m6_graph_integration_tests_deep_dive/d2_fixture_must_survive_real_regexes.py).
**Predict** first: will `"__test_Founder"` and `"M6Fixture Founder"` both be recognised as a founder's
name by the extractor's real `founded by ...` rule?

### 1. Definition + analogy
**What it is.** A test fixture that feeds text through a REAL parser (regex-based, in this project's
case) has to satisfy that parser's actual assumptions about what valid input looks like — not just
look obviously fake to a human reader. `src/graph/extractor.py`'s `_FOUNDED_BY` rule requires a name
to start with a capital letter (`[A-Z][\w.'-]*`), because that's how real names are capitalised in the
real corpus. An underscore (`_`) is not a capital letter, so a fixture name like `"__test_Founder"` —
which LOOKS like an obviously-fake test value to a person — is actually INVISIBLE to that regex: the
rule doesn't fire at all, and no warning is raised either, because as far as the regex is concerned,
there was simply no name-shaped text there to find.

**Analogy.** A metal detector is tuned to find anything metallic, shape irrelevant. A cardboard cutout
shaped exactly like a coin will fool a HUMAN glancing at the ground, but the detector — which only
"sees" metal — walks right over it in total silence. Whether something is "obviously fake" to a human
and whether it's "detectable by the actual rule" are two completely different questions.

### For a 14-year-old
Imagine you're testing a vending machine's coin slot to make sure it correctly rejects fake coins. You
make a "test coin" out of a bright pink plastic disc with "FAKE COIN — TEST ONLY" written on it in
huge letters — to a human, this is about as obviously fake as a coin could possibly be. You drop it
in... and the machine does absolutely nothing. Not a rejection, not an error message, nothing at all.
Why? Because the coin slot only ever checks ONE thing: is this disc the right SIZE and WEIGHT to even
fit through the slot mechanism? Your bright pink disc is a totally different shape, so it doesn't even
reach the part of the machine that would test whether it's a "real" coin — it just falls straight
through a gap, completely unnoticed by the mechanism you were trying to test.

To actually test the coin-rejection logic, your fake coin needs to be the RIGHT SIZE AND SHAPE first —
maybe a coin-sized, coin-weighted metal disc, just from the wrong country. NOW it reaches the part of
the machine that actually inspects it, and you can see whether the rejection logic really works. Being
"obviously fake to a human" and "shaped correctly enough to reach the part of the system being tested"
are two completely different requirements, and only the second one matters for a real test.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the vending machine's coin slot mechanism | `extractor.py`'s `_FOUNDED_BY` regex: `[A-Z][\w.'-]*` |
| the bright pink "FAKE COIN" disc, obviously fake to a human | the name `"__test_Founder"` — starts with `_`, not a capital letter |
| falling straight through, never reaching the rejection logic | `EX.extract(chunk)` finding **0** FOUNDED edges — the rule never even fired |
| a coin-shaped disc from the wrong country | the name `"M6Fixture Founder"` — starts with a capital `M`, shaped like a real name |
| reaching the actual inspection logic | `EX2.extract(chunk2)` finding **1** FOUNDED edge — the SAME regex that ignored the pink disc now fires correctly |

### 2. Why here specifically
`tests/test_graph.py`'s whole fixture only proves anything if the REAL extractor's REAL rules run on
it exactly as they'd run on real corpus text. If the fixture's entity names accidentally fell outside
what those rules can recognise, every "hermetic" assertion in the file would still pass — but for the
wrong reason: not because the pipeline works, but because the fixture never really exercised it.

### 3. Minimal code
`d2_fixture_must_survive_real_regexes.py` — the SAME real extractor, two candidate fixture names:
```python
# Minimal demo of C3: a synthetic test fixture has to be shaped like the REAL thing the
# code under test expects -- not just "clearly fake." An underscore prefix looks safely
# test-only to a human, but breaks the extractor's own regex. Uses the REAL extractor.
from src.graph.extractor import GraphExtractor
from src.ingestion.schemas import DocumentChunk, compute_chunk_id, compute_doc_id

EX = GraphExtractor(products=[], employees=["__test_Founder"], cities={})  # a tiny extractor, roster of one


def company_page(text):
    path = "company/about.md"
    return DocumentChunk(
        chunk_id=compute_chunk_id(path, 0),
        doc_id=compute_doc_id(path),
        source_path=path,
        category="company",
        title="about",
        chunk_index=0,
        text=text,
        char_count=len(text),
        approx_token_count=len(text) // 4,
    )


print("Attempt 1: an underscore-prefixed fixture name, the naive choice for 'test-only'")
chunk = company_page("Insurellm was founded by __test_Founder in 2015.")  # looks safe to a human
extraction = EX.extract(chunk)
founded_edges = [e for e in extraction.edges if e.type == "FOUNDED"]
print(f"  FOUNDED edges found: {len(founded_edges)}  warnings: {extraction.warnings}")

print("\nAttempt 2: a capitalised, word-shaped fixture name")
EX2 = GraphExtractor(products=[], employees=["M6Fixture Founder"], cities={})
chunk2 = company_page("Insurellm was founded by M6Fixture Founder in 2015.")
extraction2 = EX2.extract(chunk2)
founded_edges2 = [e for e in extraction2.edges if e.type == "FOUNDED"]
print(f"  FOUNDED edges found: {len(founded_edges2)}  warnings: {extraction2.warnings}")
```
Real output:
```
Attempt 1: an underscore-prefixed fixture name, the naive choice for 'test-only'
  FOUNDED edges found: 0  warnings: ()

Attempt 2: a capitalised, word-shaped fixture name
  FOUNDED edges found: 1  warnings: ()
```
Zero edges AND zero warnings for attempt 1 — the worst kind of test failure, because nothing looks
wrong. A test built on `"__test_Founder"` would have silently tested nothing at all.

### The exact code I wrote (`tests/test_graph.py`)
```python
# lines 37-49
# A prefix no real corpus entity uses, kept as a normal capitalised word (not "__test_")
# because it has to survive the extractor's own regexes, which expect city/founder names   # 🎯 INTERVIEW
# shaped like real proper nouns. Every fixture entity's name starts with it.
FIXTURE = "M6Fixture"

PRODUCT = f"{FIXTURE}Llm"                                 # "M6FixtureLlm" -- matches the "-llm" product pattern
EMPLOYEE = f"{FIXTURE} Employee"                          # "M6Fixture Employee" -- two capitalised words
CLIENT = f"{FIXTURE} Client"
CLIENT_LEGAL_NAME = f"{CLIENT} Corp."
CITY = f"{FIXTURE}ville, {FIXTURE} State"                 # still "City, State" shaped, for dedup's location rule
VENDOR_SIGNATORY = f"{FIXTURE} VendorSigner"
CLIENT_SIGNATORY = f"{FIXTURE} ClientSigner"
CONTRACT_TITLE = f"Contract with {CLIENT} for {PRODUCT}"  # matches "Contract with X for Y" exactly
```
**🎯 Interview-relevant line:** the comment itself. Explaining WHY a naming choice was made — not just
what it is — is exactly the kind of reasoning an interviewer is checking for when they ask "walk me
through a design decision in your test suite."

### 4. What breaks if this changes
- **Use `"__test_"` (the convention `test_graph_store.py` uses for its hand-built records):** every
  regex-based rule that expects a capitalised proper noun (founder names, city names) silently stops
  firing, and the fixture would test nothing while LOOKING like it tests something.
- **Use a real-sounding but genuinely ambiguous name** (e.g. just `"Test"`) that happens to overlap
  with real corpus vocabulary: the isolation guarantee (D4 in P4-M6's decisions) breaks, because the
  name could collide with something real.

### [Interview relevance: high — testing any text-parsing or NLP-adjacent system requires fixtures that respect the parser's real constraints, not just "clearly fake" data]

**Interview question:** *"You're writing a test fixture for a system that parses natural-language
text with regex or an NLP pipeline. What's a mistake people commonly make when picking fake/test
values?"*
**A strong answer covers:** assuming "obviously fake to a human" is the same as "will be correctly
processed by the parser"; the fact that a fixture silently failing to trigger the code path under test
is worse than a crash, because it looks like a passing test; and the fix — checking the fixture
actually produces non-trivial output from the real parser (asserting a NON-empty result, not just "no
exception"), before trusting any assertion built on top of it.

### Canonical docs
- [Python `re` HOWTO](https://docs.python.org/3/howto/regex.html) — character classes like `[A-Z]`
- Feathers, M., *Working Effectively with Legacy Code*, ch. 4 — "seams" and why test data has to fit
  the shape the code under test actually expects

### Micro-exercises
1. Run `d2_fixture_must_survive_real_regexes.py`. Change `"M6Fixture Founder"` to `"m6fixture founder"`
   (all lowercase). Does the FOUNDED edge still get found? Why or why not?
2. Look up `_FOUNDED_BY` in `src/graph/extractor.py`. Write a name that WOULD match the regex but
   ISN'T on the `employees` roster passed to `GraphExtractor(...)`. What does the extractor do with it
   instead of creating a FOUNDED edge? *Hint:* look for a warning.
3. Try a name with a number NOT at the front, like `"Fixture6 Founder"`. Does it still match
   `[A-Z][\w.'-]*`? What does that tell you about which characters `\w` actually includes?
4. Find ONE other regex in `extractor.py` (besides `_FOUNDED_BY`) that requires a specific text shape,
   and write a one-line fixture string that would accidentally NOT match it.
5. In `tests/test_graph.py`'s real fixture, `CITY = "M6Fixtureville, M6Fixture State"` — is this a
   REAL US state name? Does it need to be, for the tests to pass? Read `dedup.py`'s `_STATES` dict and
   `_split_location` to find out what happens with an unrecognised state name.

**How it works internally.** Python's `re` module compiles a pattern into a state machine that
consumes the input character by character; `[A-Z]` is a character class matching exactly one of 26
specific bytes (uppercase ASCII letters). An underscore's byte value simply isn't in that class, so
the regex engine rejects that starting position instantly and moves on, with no special-cased "this
looks like a test value" logic anywhere — regex engines have no concept of "obviously fake."

### How to validate this yourself
The two attempts side by side in the real output above ARE the validation: same rule, same code path,
different fixture shape, opposite result.

### How to visualize this
Use [regex101.com](https://regex101.com) (or any offline regex tester) with the pattern
`[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*` against both `"__test_Founder"` and `"M6Fixture Founder"` — seeing
the highlighted (or absent) match visually is often more convincing than reading the character class.

**You should now be able to explain:** why a fixture value being "obviously fake to a human" says
nothing about whether it will be correctly processed by the real code under test, and how to verify a
fixture actually reaches the code path it's meant to exercise.

---

## C4 — Cleanup by exact identity, never by scanning

### Do this first
Before reading on, run (needs `docker compose up -d neo4j`):
```bash
.venv/Scripts/python "practice codes/phase 4/m6_graph_integration_tests_deep_dive/d3_cleanup_scan_vs_exact_identity.py"
```
**Predict** first: could a Cypher query that scans every property of every node for a text prefix ever
FAIL outright, rather than just being slow?

### 1. Definition + analogy
**What it is.** When a test writes data into a SHARED, persistent resource (a database everyone else
also uses), it must clean up after itself — and there are two fundamentally different ways to find
what to delete. A **scan** searches the whole dataset for something matching a pattern (e.g. "any
property starting with a prefix"). An **exact-identity delete** instead uses the SAME specific
identities (label + key) the test already knows it wrote, and deletes exactly those, nothing else. A
scan has to handle EVERY kind of data it might encounter, including kinds its author never
anticipated; an exact-identity delete only ever touches what it already has a precise reference to.

**Analogy.** If you lost your one specific set of keys somewhere in a stadium, you could either
frisk every single person in the stadium looking for anything key-shaped (a scan — slow, and you might
grab someone else's keys, or something that isn't a key at all and reacts badly to being grabbed), or
you could retrace your OWN exact steps and check only the seats you personally sat in (exact identity
— you can only ever find your own keys, because you're only looking where you know you've been).

### For a 14-year-old
Picture a huge shared toy box that your whole family uses — yours, your siblings', even some toys from
a family friend who's staying over. You borrowed a handful of toys from it to build something, and now
it's time to put them back and clean up. You have two ways to do this:

**Way 1 — the scan:** you go through the ENTIRE toy box, picking up anything that "looks like it might
be yours" — maybe anything the same colour as the toys you remember using. This is risky in two ways:
you might grab a toy that just HAPPENS to be that colour but isn't actually yours (over-matching), and
some things in that box aren't even toys in the shape you're expecting — like a wobbly bag of marbles
that isn't a solid "thing" you can pick up the same way a block is, and trying to grab it the way you'd
grab a block might just spill marbles everywhere (a crash).

**Way 2 — exact identity:** before you even started building, you kept a mental (or written) list of
the EXACT toys you took out: "the red truck, the blue block, the small dinosaur." When it's time to
clean up, you don't search the box at all — you just walk straight to those three EXACT items, by
name, and put back only those. You physically cannot grab the wrong toy, because you're never
searching in the first place — you already know exactly what you're looking for.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the shared family toy box | the live Neo4j database, shared with the real 223-chunk corpus |
| grabbing anything "the same colour" | a `WHERE toString(n[k]) STARTS WITH 'M6Fixture'` scan |
| the wobbly bag of marbles, not a solid block | a `Company` node's `aliases` LIST property — `toString()` can't handle it |
| marbles spilling everywhere | the real crash: `CypherTypeError: Invalid input for function 'toString()'... got: StringArray` |
| your written list of the exact 3 toys you took out | the `nodes` list `live_store` already has, from writing them |
| walking straight to those items by name | `MATCH (n:{label} {{key}}) DETACH DELETE n` — one exact match per node |

### 2. Why here specifically
The real Neo4j this test writes to is the SAME database the real 223-chunk corpus already lives in
(Community Edition has one database). A cleanup mistake here doesn't just fail loudly in a sandbox —
it risks deleting real corpus data. Exact-identity deletion makes that risk structurally impossible:
there is no code path by which this cleanup could ever touch a node it didn't itself just write.

### 3. Minimal code
`d3_cleanup_scan_vs_exact_identity.py` — the actual crash, reproduced live and safely (read-only):
```python
# Minimal demo of C4: cleaning up by SCANNING for a name pattern can crash or over-match
# on data you didn't anticipate; cleaning up by the EXACT identity you wrote cannot.
# Live (read-only: MATCH and RETURN, no DETACH DELETE) against the real Neo4j.
from neo4j.exceptions import ClientError

from src.graph.setup import build_driver

with build_driver() as driver:
    with driver.session(database="neo4j") as session:
        print("Attempt 1: a text-scan sweep, the naive cleanup approach")
        try:
            session.run(
                "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH $prefix) "
                "RETURN count(n) AS c",
                prefix="M6Fixture",
            ).single()
            print("  scan succeeded")
        except ClientError as exc:
            print(f"  scan CRASHED: {exc.code}")
            print(f"  {exc.message}")

        print("\nAttempt 2: exact-identity lookup, this project's actual cleanup approach")
        result = session.run(
            "MATCH (n:Company {name: $name}) RETURN count(n) AS c", name="M6Fixture Client"
        ).single()
        print(f"  exact lookup succeeded, found: {result['c']} node(s) (0 is fine -- cleanup already ran)")
```
Real output:
```
Attempt 1: a text-scan sweep, the naive cleanup approach
  scan CRASHED: Neo.ClientError.Statement.TypeError
  Invalid input for function 'toString()': Expected a String, Float, Integer, Boolean, Temporal or Duration, got: StringArray[San Francisco, CA]

Attempt 2: exact-identity lookup, this project's actual cleanup approach
  exact lookup succeeded, found: 0 node(s) (0 is fine -- cleanup already ran)
```
This is not a hypothetical: this exact crash happened while I was hand-verifying this project's real
cleanup, on the real corpus's own `Location` node (a `Company`'s `aliases` list property, from P4-M4's
dedup, tripped `toString()`).

### The exact code I wrote (`tests/test_graph.py`)
```python
# lines 213-220 (the finally block of live_store)
finally:
    # Cleanup by EXACT identity, not a name scan: every node this fixture wrote
    # is already known from `nodes`, so nothing else in the graph can be touched,   # 🎯 INTERVIEW
    # whatever the real corpus does or doesn't currently hold.
    with driver.session(database="neo4j") as session:
        for node in nodes:                                              # the EXACT nodes this fixture wrote
            pattern = ", ".join(f"{k}: ${k}" for k in node.key)         # e.g. "name: $name"
            session.run(                                                # 🎯 one exact MATCH per node, no scan
                f"MATCH (n:{node.label} {{{pattern}}}) DETACH DELETE n", **node.key
            )
```
**🎯 Interview-relevant lines:** `for node in nodes` — reusing the SAME list the code wrote from,
rather than re-deriving "what did I write" from a separate scan, is the whole safety property in one
line. And the property NAMES (`node.label`, the keys in `pattern`) are spliced into the query text
while every VALUE (`**node.key`) is passed as a parameter — the same safe-splicing pattern
`GraphStore`'s own `_key_pattern`/`_key_params` use (P4-M3), reused here for the same reason.

### 4. What breaks if this changes
- **Switch to a prefix scan** (as attempted in the demo): the cleanup can crash outright on a property
  type it didn't anticipate (proven above), and even when it doesn't crash, it risks matching a node
  this test never wrote, if the "real" corpus ever happens to contain the same prefix by coincidence.
- **Skip cleanup and rely on the next test run's `MERGE` to "fix" it:** the fixture nodes would remain
  visible in Neo4j Browser between runs, and any OTHER live test or manual query that scans by label
  (not by exact name) would see them mixed in with real data.

### [Interview relevance: high — "how do you safely test against a shared, persistent resource" comes up in any interview touching integration testing or database-backed systems]

**Interview question:** *"Your integration tests write to a shared staging database that other tests
and real data also use. How do you make sure your test's cleanup never deletes something it
shouldn't?"*
**A strong answer covers:** preferring deletion by the EXACT identity/primary-key the test itself just
created (kept in a variable, not re-derived), over any pattern-matching or "delete everything newer
than X" heuristic; running cleanup in a `finally` block so it happens even on test failure; and
naming test data distinctively enough that a human glancing at the shared resource can immediately
tell it's test data, even though the CLEANUP mechanism itself doesn't rely on that naming for safety.

### Canonical docs
- [Neo4j Cypher manual: `toString()`](https://neo4j.com/docs/cypher-manual/current/functions/scalar/#functions-tostring)
- [Neo4j Cypher manual: parameters](https://neo4j.com/docs/cypher-manual/current/syntax/parameters/)
- Meszaros, G., *xUnit Test Patterns*, the chapter on test data cleanup / "fresh fixture"

### Micro-exercises
1. Run `d3_cleanup_scan_vs_exact_identity.py` yourself and read the exact error message. Which
   property type triggered it, and can you find that property in the real graph via Neo4j Browser
   (`MATCH (c:Company) WHERE c.aliases IS NOT NULL RETURN c LIMIT 1`)?
2. Rewrite the scan query to skip list properties instead of crashing (`WHERE NOT n[k] IS :: LIST<ANY>`
   or similar — check the Cypher manual for the right type-predicate syntax). Does it now run safely?
   Would you still trust it more than exact-identity deletion?
3. In `tests/test_graph.py`, temporarily change the cleanup loop to delete by
   `MATCH (n) WHERE n.name STARTS WITH 'M6Fixture' DETACH DELETE n` (a single scan, not per-node). Run
   the live tests. Does it still work? What did you lose by simplifying it this way?
4. Count how many `session.run()` calls the real cleanup makes for this fixture's ~9 nodes. At what
   node count would you start to worry about the cost of one-query-per-node, and what would you do
   about it instead of switching to a scan?
5. Find another place in this codebase that deletes data (`_wipe_managed_labels` in
   `src/graph/ingest.py`). Is it a scan or an exact-identity delete? Given it deletes by LABEL, not by
   individual identity, why is that acceptable there but not in `tests/test_graph.py`'s cleanup?
   *Hint:* re-read decision D-2026-09-22-13 in `decision.md`.

**How it works internally.** `MATCH (n:{label} {pattern}) DETACH DELETE n` compiles to an index seek
on the label's uniqueness constraint (from P4-M2) followed by a delete of that one node and every
relationship touching it — the same mechanism `GraphStore.upsert_node`'s `MERGE` uses to find a node,
just followed by deletion instead of an update.

### How to validate this yourself
The crash message IS the validation — it's real, reproducible, and names the exact property type that
broke the naive approach.

### How to visualize this
Open Neo4j Browser, run `MATCH (c:Company) RETURN c.name, c.aliases LIMIT 25`, and find a row where
`aliases` shows as a bracketed list `[...]` rather than a plain string — that's the exact shape of data
a `toString()`-based scan chokes on.

**You should now be able to explain:** the difference between cleaning up by a pattern scan and by
exact identity, and a concrete, reproducible reason (not a hypothetical one) why the scan is riskier.

---

## C5 — Multi-hop connectivity: linked with no direct edge

### Do this first
Before reading on, run
[`d4_multi_hop_through_an_intermediate_node.py`](../practice%20codes/phase%204/m6_graph_integration_tests_deep_dive/d4_multi_hop_through_an_intermediate_node.py).
**Predict** first: if Client connects to Contract, and Contract connects to Product, but NO edge
connects Client directly to Product, is Client "connected to" Product at all?

### 1. Definition + analogy
**What it is.** Two entities in a graph can be **connected** — reachable from one another by
following a chain of edges — without there being a SINGLE edge directly between them.
`multi_hop_search` finds every entity within N steps of a starting point by exploring outward one
step (**hop**) at a time, through however many intermediate nodes it takes, and reports the SHORTEST
number of hops to each one it finds. This is the actual value a knowledge graph adds over a plain
lookup table: a lookup table only ever answers "what IS this row," while a graph traversal answers
"what is this thing CONNECTED to, transitively, through everything in between."

**Analogy.** You and a stranger on a train might not know each other directly, but you might both know
the same third person — "a friend of a friend." No direct connection exists between you and the
stranger, yet you're still meaningfully connected, ONE step further removed than a direct friendship.

### For a 14-year-old
Imagine a game of "who knows who" at a party. You want to know: is there ANY way I'm connected to that
person across the room, even if we've never spoken? You don't need to have met them directly — maybe
you know Sarah, and Sarah happens to know them. There's no direct line between you and that person,
but there IS a path: you → Sarah → them. That's TWO hops. If it turned out Sarah didn't know them
either, but Sarah knows Tom, and Tom knows them, that would be THREE hops — still connected, just
further apart.

Now imagine trying to figure this out by only being ALLOWED to ask one specific question: "do you know
[that exact person]?" directly. You'd ask that person's name to everyone at the party one at a time,
and if nobody says "yes, directly," you'd wrongly conclude you're not connected to them at all — even
though Sarah-knows-them would have answered "yes" instantly. To find the REAL answer, you have to be
willing to explore OUTWARD, one friendship at a time, however many steps it takes, and stop as soon as
you reach them — that's exactly what `multi_hop_search` does with hops instead of friendships.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| you, at the party | the starting entity, e.g. `M6Fixture Client` (a `Company`) |
| the stranger across the room | the target entity, e.g. `M6FixtureLlm` (a `Product`) |
| Sarah, the friend-of-a-friend link | the `Contract` node — no name of its own, just a connector |
| "you → Sarah → them" = 2 hops | `Client -WITH_CLIENT- Contract -FOR_PRODUCT- Product` = 2 hops |
| only being allowed to ask "do you know them DIRECTLY" | checking for a single edge type between two labels, missing the connection entirely |
| exploring outward, one friendship at a time | `multi_hop_search`'s breadth-first walk, `hops=1`, `hops=2`, ... |

### 2. Why here specifically
`WITH_CLIENT` connects `Contract → Company`; `FOR_PRODUCT` connects `Contract → Product`. There is no
`RelationshipType` in `schema.py` that EVER goes directly `Company → Product` — the model was designed
this way on purpose (P4-M2), because a client's relationship to a product only ever exists THROUGH a
contract. Proving `multi_hop_search` actually surfaces that connection — not just that the two
individual edges exist — is exactly the "does the graph answer real questions" claim this whole test
file exists to check.

### 3. Minimal code
`d4_multi_hop_through_an_intermediate_node.py` — the same shape, a plain breadth-first search, no
database:
```python
# Minimal demo of C5: two entities can be CONNECTED with no DIRECT edge between them --
# only a path through something in between. A tiny hand-rolled graph + breadth-first
# search stands in for GraphStore.multi_hop_search, so the idea is visible with no
# database at all.
from collections import deque

edges = [
    ("Client", "Contract"),  # WITH_CLIENT, direction doesn't matter for this demo
    ("Contract", "Product"),  # FOR_PRODUCT
]

neighbours = {}  # build an undirected adjacency list: each node's list of directly-linked nodes
for a, b in edges:
    neighbours.setdefault(a, []).append(b)  # a can reach b directly
    neighbours.setdefault(b, []).append(a)  # and b can reach a directly (undirected)


def hops_away(start, target):
    visited = {start: 0}  # every node found so far, with its distance from `start`
    queue = deque([start])  # nodes waiting to have their own neighbours explored
    while queue:
        node = queue.popleft()  # take the next node to explore
        if node == target:
            return visited[node]  # found it -- return how many hops it took
        for neighbour in neighbours.get(node, []):  # look at everything directly linked to `node`
            if neighbour not in visited:  # only visit each node once
                visited[neighbour] = visited[node] + 1  # one hop further than `node` was
                queue.append(neighbour)  # explore ITS neighbours later
    return None  # never reached


print("Is there a DIRECT edge from Client to Product?", ("Client", "Product") in edges)
print("Hops from Client to Contract:", hops_away("Client", "Contract"))  # 1: a direct edge exists
print("Hops from Client to Product: ", hops_away("Client", "Product"))  # 2: only through Contract
```
Real output:
```
Is there a DIRECT edge from Client to Product? False
Hops from Client to Contract: 1
Hops from Client to Product:  2
```
No direct edge, and yet a clear, correct answer: 2 hops. `GraphStore.multi_hop_search` does exactly
this, over real Cypher, with a hop limit and label filtering on top.

### The exact code I wrote (`tests/test_graph.py`)
```python
# lines 228-235
@pytest.mark.live
def test_live_graph_connects_the_client_to_the_product_through_its_contract(live_store):
    # No edge goes directly from Company to Product: WITH_CLIENT and FOR_PRODUCT both
    # start at the Contract in between. multi_hop_search must walk THROUGH it to connect
    # the two -- the reason a graph answers this and a single Cypher MATCH on one          # 🎯 INTERVIEW
    # relationship type could not.
    assert PRODUCT not in live_store.multi_hop_search(CLIENT, hops=1)   # 🎯 NOT reachable in 1 hop
    assert PRODUCT in live_store.multi_hop_search(CLIENT, hops=2)       # 🎯 IS reachable in 2 hops
```
**🎯 Interview-relevant lines:** asserting BOTH the negative (`not in ... hops=1`) and the positive
(`in ... hops=2`) in the same test. Asserting only the positive would also pass if `multi_hop_search`
had a bug that returned every entity regardless of hop count — the negative assertion is what actually
proves the hop LIMIT is doing real work, not just that the entity exists somewhere in the graph.

### 4. What breaks if this changes
- **Drop the `hops=1` negative assertion:** the test would still pass even if `multi_hop_search`
  ignored its `hops` argument entirely and just returned everything reachable at any distance — a real
  regression that this test, without the negative check, would never catch.
- **Add a direct `Company -> Product` edge to the schema "for convenience":** it would contradict the
  corpus's own facts (a company doesn't license a product except through a specific contract), and
  this test's whole point — that the graph correctly connects things with NO direct edge — would stop
  being demonstrated at all.

### [Interview relevance: very high — multi-hop / multi-hop reasoning is one of the most commonly asked GraphRAG interview topics, and knowing to test the NEGATIVE case (not just the positive) is a mark of testing maturity]

**Interview question:** *"How would you test that a graph traversal function respects its own hop
limit, not just that it eventually finds the right answer?"*
**A strong answer covers:** asserting a NEGATIVE result at a hop count too low to reach the target
(proving the limit is enforced, not ignored), in addition to the positive result at a sufficient hop
count; recognizing that a positive-only test can pass even for a badly broken implementation that
returns everything unconditionally; and, ideally, a THIRD case at exactly the boundary hop count to
catch an off-by-one error in the traversal's stopping condition.

### Canonical docs
- [Neo4j Cypher manual: variable-length pattern matching](https://neo4j.com/docs/cypher-manual/current/patterns/reference/#variable-length-relationships)
- Edge et al. (2024), ["From Local to Global..."](https://arxiv.org/abs/2404.16130), arXiv:2404.16130
  — why multi-hop reasoning is the actual value proposition of GraphRAG over plain vector search
- [docs/19 graph_store_deep_dive.md](19%20graph_store_deep_dive.md) — `multi_hop_search`'s own
  implementation deep dive (P4-M3)

### Micro-exercises
1. Run `d4_multi_hop_through_an_intermediate_node.py`. Add a THIRD edge, `("Product", "Warranty")`,
   and compute `hops_away("Client", "Warranty")`. What do you predict before running it?
2. Modify `hops_away` to return the actual PATH (the sequence of nodes), not just the hop count. What
   extra bookkeeping does that need in the BFS loop?
3. In the real `tests/test_graph.py` fixture, is there a 3-hop connection from `EMPLOYEE` to `CLIENT`?
   Trace it by hand through the edges the hermetic tests already assert exist (`WORKS_ON`,
   `FOR_PRODUCT`, `WITH_CLIENT`) before checking with a live query.
4. Add a live test asserting `CLIENT not in live_store.multi_hop_search(EMPLOYEE, hops=1)` (a negative
   check at the WRONG label too — try `label="Company"` explicitly). Does narrowing by label change
   the hop count needed?
5. Read `GraphStore.multi_hop_search`'s real implementation (`src/retrieval/graph_store.py`). Does it
   use domain relationships only, or does it also walk provenance edges like `MENTIONS`? Why does that
   matter for whether two entities merely mentioned in the same chunk would show up as "connected"?

**How it works internally.** `multi_hop_search` compiles to a single Cypher query with a
variable-length relationship pattern, `-[:TYPE1|TYPE2|...*1..N]-`, which Neo4j's query planner
executes as a graph traversal (not N separate queries) — cost scales with the number of PATHS explored,
not with the total size of the graph, which is why a 3-hop cap exists (P4-M3, doc 19).

### How to validate this yourself
The BFS demo's printed hop counts ARE the validation for the concept; for the real system, the live
test's two assertions (`not in ... hops=1`, `in ... hops=2`) together prove the hop limit is real, not
decorative.

### How to visualize this
Open Neo4j Browser and run
`MATCH p = (c:Company {name: 'M6Fixture Client'})-[*1..2]-(pr:Product {name: 'M6FixtureLlm'}) RETURN p`
— Neo4j Browser renders the actual PATH as a small graph diagram, with the `Contract` node visibly
sitting between the two, which is the clearest possible picture of "connected, but not directly."

## Consolidated end-to-end exercise: watch the whole thing work

1. **Run the hermetic layer and watch it build the plan.** `pytest tests/test_graph.py -v -m "not
   live"`. Add a temporary `print(nodes)` inside the `plan` fixture, re-run with `-s`, and read the
   exact `GraphNode` objects it built from 3 tiny fixture files (C1, C2).
2. **Confirm the fixture actually reaches the extractor's real rules.** Temporarily rename `FIXTURE`
   to `"__test_"` in `tests/test_graph.py`. Re-run the hermetic tests. Which ones fail, and does the
   failure message make it obvious the fixture stopped matching the regexes, or does it look like a
   more confusing failure? (C3)
3. **Run the live layer and watch Neo4j fill up, then empty out.** Before running,
   `MATCH (n) WHERE n.name STARTS WITH 'M6Fixture' RETURN count(n)` in Neo4j Browser (should be 0).
   Run `pytest tests/test_graph.py -v -m live`. Immediately after (before pytest exits, if you can
   manage it, or by adding a `time.sleep(30)` inside `live_store` right after the writes) re-run that
   same query and watch the count jump to ~7. After the run finishes, run it again — back to 0. (C2, C4)
4. **See the multi-hop connection with your own eyes.** In Neo4j Browser, run
   `MATCH p = (c:Company {name: 'M6Fixture Client'})-[*1..2]-(pr:Product {name: 'M6FixtureLlm'})
   RETURN p` — but only WHILE the live tests are running (the fixture is deleted afterwards), so
   either add a breakpoint/sleep, or just trust the test's own assertion and re-derive the path by
   hand from the hermetic layer's node/edge list instead. (C5)
5. **Break something on purpose and watch the RIGHT test catch it.** In a scratch copy, change
   `_HOME_CATEGORY` back to label-only matching (the real P4-M5 bug). Does anything in
   `tests/test_graph.py` catch it? (It shouldn't — this fixture has no contract-vs-product-page
   ambiguity built in.) What does that tell you about the LIMITS of even a good integration test
   fixture, and why `tests/graph/test_ingest.py`'s real-corpus live test still matters separately?

If you can answer why step 5's fixture does NOT catch that specific bug, you've understood not just
what this test file does, but the boundary of what it does — which is the harder and more valuable
half of understanding any test suite.
