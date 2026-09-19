# 16 — Retrieval Tests: Deep Dive (P3-M6)

> Every output block in this document was produced on 2026-09-19 by running the code: the demos in
> [`practice codes/phase 3/m6_retrieval_tests_deep_dive/`](../practice%20codes/phase%203/m6_retrieval_tests_deep_dive/),
> and the project's own `tests/test_retrieval.py` against the live Qdrant **v1.19.0** and Ollama **0.20.0**
> servers.
>
> Every concept has **two** code parts:
> 1. **Minimal code:** a small runnable demo built to show the mechanism.
> 2. **The exact project code:** copied from `tests/test_retrieval.py`, `tests/support.py`,
>    `pyproject.toml` or `.github/workflows/ci.yml`, with **line numbers**, then explained line by line.
>
> Run demos from the project root:
> ```bash
> .venv/Scripts/python "practice codes/phase 3/m6_retrieval_tests_deep_dive/c2_test_doubles.py"
> .venv/Scripts/python -m pytest "practice codes/phase 3/m6_retrieval_tests_deep_dive/demo_c7_xfail_strict.py" -q -rxX -p no:cacheprovider
> ```
> Files named `demo_*.py` are pytest files that **fail on purpose** to show a behaviour. They're named so
> a plain `pytest` run never collects them; pass their path explicitly.

---

## 0. Plain-language summary

By P3-M5 every component (lock, embedder, setup, store, ingestion) had its own unit tests: 61 of them.
P3-M6 asked a different question: **do the components give the right answers *together*, and against
the *real* model and server?**

`tests/test_retrieval.py` answers it in two layers:

```
HERMETIC (runs anywhere, CI included)                   LIVE (needs Qdrant + Ollama; skips with a reason)
chunks ─► run_ingestion ─► in-memory Qdrant              8 real docs ─► real model ─► real Qdrant (scratch collection)
          │ toy bag-of-words model (C2, C3)                            │
          ▼                                                           ▼
   store.search("Who founded Acme?")  (C4)               known answers in top 3 (C4) · known misses = strict xfail (C7)
   CLIs run in-process (C8, C9)                          server-vs-fake contract checks (C10)
```

Around them: fixtures that build things once and always clean up (C5), markers that decide what runs
where (C6), and two lessons from mistakes made while writing them (C11, C12).

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | Coverage is not testing; auditing before writing tests | 🟡 Engineering |
| C2 | Test doubles: stub, fake, spy, mock | 🟡 Engineering (classic interview topic) |
| C3 | A toy embedding model: bag of words + the hashing trick | 🔴 **Core RAG** (what an embedding *is*, stripped down) |
| C4 | End-to-end retrieval tests, asserted by rank | 🔴 **Core RAG** (the seed of retrieval evaluation) |
| C5 | Fixtures: scope, sharing, and guaranteed teardown | ⚪ Boilerplate (pytest mechanics) |
| C6 | Markers, skipif, deselection, registration | ⚪ Boilerplate |
| C7 | `xfail(strict=True)`: known bugs as executable records | 🟡 Engineering |
| C8 | Monkeypatching: patch where a name is looked up | 🟡 Engineering (a very common test bug) |
| C9 | Testing command-line programs in-process | ⚪ Boilerplate |
| C10 | Contract tests: does the fake behave like the real thing? | 🟡 Engineering |
| C11 | pytest's reserved names (the `setup_module` slip) | ⚪ Boilerplate |
| C12 | Mutation-testing the tests themselves | 🟡 Engineering |

> **Open Question 1** (the exact order of events when running `-m live` with Qdrant stopped vs running)
> is not answered here. C5 and C6 give you the pieces; the answer is yours to trace.

---

## C1 — Coverage is not testing; audit before writing tests

### 1. Definition + analogy

**What it is.** **Line coverage** is the percentage of your program's lines that *executed* while the
tests ran. `pytest --cov` measures it by watching the interpreter: every line that runs gets a tick.

It tells you something useful but limited:
- **Low coverage is proof of a gap.** Lines that never ran were certainly never tested.
- **High coverage is *not* proof of testing.** A line can run without any assertion checking its result.
  Coverage measures *execution*, not *verification*.

That's why P3-M6 began with an **audit**, not with code: list the existing tests, measure coverage, then
ask what *behaviours* no test checks. The audit found 61 unit tests at 70–79 % coverage, and four gaps
that coverage alone would have pointed to only partly:
1. no test ran the components **together**;
2. no test touched the **real server or model**;
3. nothing recorded **which questions retrieval gets right or wrong**;
4. the **command-line `main()` functions** were most of the uncovered lines.

**The test pyramid** organises what to test where: many fast **unit** tests at the bottom (one component,
fakes around it), fewer **integration** tests in the middle (components together), and a few **end-to-end
/ live** tests at the top (real services: slow, sometimes unavailable, but the only ones that see
reality). P3-M6 filled the middle and top layers.

**Analogy.** A driving examiner who only checks that you *sat in every seat of the car* (coverage) hasn't
checked that you can drive. Sitting in the driver's seat is necessary, not sufficient.

### For a 14-year-old

Imagine your teacher gives you 10 homework questions, and your parent checks your homework by making sure
**every page has writing on it**. Every page does, so you "pass". But you wrote the wrong answer to
question 4. Nobody checked the *answers*, only that each page was *touched*.

That's line coverage. The computer counts which lines of code were "touched" while tests ran. 100 % sounds
perfect, but it only means every line was *run*, not that anyone checked it did the right thing.

A better check is to actually **mark the answers**: "question 4 should say 12". That's an assertion. In our
demo below, a search function returns the *worst* result instead of the best, gets **100 % coverage**, and
passes a lazy test. Only a test that checks "is the best result first?" catches it.

So before writing new tests, we first looked at what already existed and asked "what's never *marked*?",
not just "what's never *touched*?".

### 2. Why (vs the traditional approach)
Teams often chase a coverage number ("80 % or the build fails"). That rewards tests that execute code without
checking it, which is why the project's CI reports coverage but deliberately doesn't enforce a threshold
(comment in `ci.yml`).

### 3a. Minimal code — 100 % coverage, wrong answer
`c1_coverage_is_not_testing.py`
```python
# Line coverage says which lines RAN during tests, not whether anything was CHECKED.
import importlib
import sys
import tempfile
from pathlib import Path

import coverage

module_dir = Path(tempfile.mkdtemp())
(module_dir / "retrieval_demo.py").write_text(
    "def top_k(scores, k):\n"
    "    ranked = sorted(scores, key=lambda s: s[1])      # BUG: ascending, worst first\n"
    "    return ranked[:k]\n",
    encoding="utf-8",
)
sys.path.insert(0, str(module_dir))

cov = coverage.Coverage(data_file=None, include=[str(module_dir / "*")])
cov.start()
retrieval_demo = importlib.import_module("retrieval_demo")
result = retrieval_demo.top_k([("a", 0.9), ("b", 0.2), ("c", 0.5)], k=1)
cov.stop()

weak_test_passed = len(result) == 1                     # "returns one result" -- checks nothing useful
_, statements, _, missing, _ = cov.analysis2(str(module_dir / "retrieval_demo.py"))
covered = len(statements) - len(missing)
print(f"weak test (one result returned) passed : {weak_test_passed}")
print(f"line coverage                          : {covered}/{len(statements)} statements = "
      f"{100 * covered // len(statements)}%")
print(f"actual top-1                           : {result}  <- the WORST chunk")
print(f"strong test (best chunk first) passed  : {result[0][0] == 'a'}")
```
Real output:
```
weak test (one result returned) passed : True
line coverage                          : 3/3 statements = 100%
actual top-1                           : [('b', 0.2)]  <- the WORST chunk
strong test (best chunk first) passed  : False
```
**How it works.**
- `coverage.Coverage(...).start()` installs a tracing hook in the Python interpreter; every executed line of
  a file matching `include` is recorded until `stop()`.
- The module is written to a real file so coverage can map executed lines back to source.
- `analysis2` returns the file's statements and the ones never executed. All 3 ran, so **100 %**.
- The bug (`sorted` ascending) puts the *lowest* score first. The weak test only checks the *length*, so it
  passes. The strong test checks the *behaviour retrieval exists for* and fails.

### 3b. The exact project code — what CI runs, and what P3-M6 measured
`.github/workflows/ci.yml`, lines 60–71:
```yaml
      # -v so a failure in the Actions log names the exact failing test, not just a dot.
      # --cov reports which lines ran, which is how a test that passes without
      # exercising the code it claims to test gets caught (see P2-M5). Reported,
      # not enforced: a threshold tempts writing filler tests to hit a number.
      # -m "not tiktoken" keeps the run offline; that one test needs an encoding
      # file a fresh runner would have to fetch over the network. Live tests need
      # Qdrant and Ollama, which this Windows runner cannot start as service
      # containers, so they are deselected by name rather than left to skip
      # quietly; -rs still lists anything else that skipped, with its reason.
      - name: Run pytest
        run: |
          pytest tests/ -v -rs -m "not tiktoken and not live" --cov=src --cov-report=term-missing
```
Measured before and after P3-M6 (same command, embedding + vectorstore modules):

| Module | Before | After | What the new lines are |
|---|---|---|---|
| `embedding/embedder.py` | 78 % | 96 % | its `main()` CLI |
| `embedding/lock.py` | 75 % | 91 % | its `main()` CLI |
| `vectorstore/setup.py` | 70 % | 95 % | its `main()` CLI |
| `vectorstore/store.py` | 78 % | 98 % | `main()`: index / search / health |
| `vectorstore/ingest.py` | 79 % | 98 % | `run_from_args`, `main`, `summarise` |

**How to read it.** The jump came almost entirely from the CLI tests (C9). The *behavioural* gaps (components
together, real server, retrieval answers) don't show up as coverage at all: every line of `store.search` was
*already* covered by unit tests with a one-hot fake. Coverage found one gap; thinking about behaviour found the
other three.

### 4. Benefits
Coverage finds untested code quickly; an audit finds untested *behaviour*.

- **What breaks:** coverage thresholds as goals; assuming a covered line is a tested line.
- **Docs:** [coverage.py](https://coverage.readthedocs.io/) · [pytest-cov](https://pytest-cov.readthedocs.io/) ·
  [Martin Fowler, "The Practical Test Pyramid"](https://martinfowler.com/articles/practical-test-pyramid.html)

### Micro-exercises
1. In the demo, change the weak test to `result[0][1] == max(s for _, s in scores)`. Does it catch the bug?
2. Run `pytest tests --cov=src.vectorstore --cov-report=term-missing` and name one uncovered line and why.
3. Enable branch coverage (`--cov-branch`). Does any module drop below its line coverage?
4. Name one behaviour of `store.search` that is 100 % covered yet untested by the unit tests alone.
5. Why does the CI comment refuse a coverage threshold? Argue the opposite side too.

**Internals.** coverage.py uses `sys.settrace` (or `sys.monitoring` on Python 3.12+) to receive a callback on
every executed line, and records (file, line) pairs.
**Checkpoint:** explain why 100 % line coverage is compatible with a completely wrong result.

---

## C2 — Test doubles: stub, fake, spy, mock

### 1. Definition + analogy

**What it is.** A **test double** is any object that stands in for a real dependency during a test, like a
stunt double stands in for an actor. Our retrieval code depends on an **embedding client** (Ollama) and a
**vector database** (Qdrant). Using the real ones in every test would be slow, need running services, and
give different results on different machines. So most tests swap in doubles. There are four kinds, and
choosing the wrong one gives tests that pass while proving nothing:

| Kind | What it does | What it can verify |
|---|---|---|
| **Stub** | returns a **canned** answer, whatever the input | that the code copes with *that* answer |
| **Fake** | a **working, simplified implementation** | real behaviour, e.g. "does the right chunk come first?" |
| **Spy** | a fake (or stub) that also **records how it was called** | *what* was sent: prefixes, batch sizes |
| **Mock** | pre-programmed **expectations about calls**, checked afterwards | "was `embed` called once with exactly these args?" |

The project uses:
- a **fake + spy**: `BagOfWordsClient` really embeds (as a toy) *and* records every input in `self.inputs`;
- a **stub**: `pinned_digest` always answers "yes, the model is the pinned one";
- **real objects where cheap**: the in-memory `QdrantClient`, a real, working implementation from the Qdrant
  library itself.

**The danger of stubs for retrieval:** a stub embedding client returns the same vectors for every question, so
the search always returns the same document. Any "retrieval works" test would pass, or fail, by accident.
That's why retrieval tests need a **fake**.

**Analogy.** Rehearsing a play. A **stub** is a cardboard cut-out that always "says" the same line. A **fake** is
an understudy who actually acts, just not as well. A **spy** is an understudy who also writes down every cue you
gave them. A **mock** is a stage manager with a checklist: "the actor must say exactly this line, once".

### For a 14-year-old

You're practising for a tennis match, but your real opponent isn't available. Who do you practise with?

- **A stub:** a ball machine that always shoots the ball to the *same spot*. Fine for practising one shot, but
  you'll never learn to react, because it never changes.
- **A fake:** your friend who plays tennis, not as well as your opponent, but who actually returns the ball to
  different places depending on how you hit it. Now you're really practising a match.
- **A spy:** that same friend, but they also **write down** every shot you hit. After practice you can check:
  "did I use my backhand at least 5 times?"
- **A mock:** your coach standing there with a checklist: "you must serve exactly once, to the left corner". If
  you do anything else, the coach blows the whistle.

For testing search, a ball machine (stub) is useless: it would return the same "answer" to every question. So we
built a **fake**: a tiny, simple AI that actually gives different answers to different questions. We also made it a
**spy**, so tests can check exactly what text was sent to it.

### 2. Why (vs the traditional approach)
Many projects reach for `unittest.mock.Mock` for everything. Mocks verify *calls*, not *outcomes*: a mock-based test
of search would confirm `embed` was called, and would still pass if the ranking were reversed.

### 3a. Minimal code — the four doubles on one tiny retriever
`c2_test_doubles.py`
```python
# Four kinds of test double for one dependency: an embedding client with .embed().
from unittest.mock import Mock


def search(client, question, documents):
    """Tiny retriever: embed question + documents, return the best document."""
    vectors = client.embed(input=[question, *documents])["embeddings"]
    q, docs = vectors[0], vectors[1:]
    scores = [sum(a * b for a, b in zip(q, d)) for d in docs]
    return documents[scores.index(max(scores))]


docs = ["acme was founded in 2015", "the widget is blue"]


class Stub:
    """Returns a canned answer; knows nothing about the input."""
    def embed(self, input):
        return {"embeddings": [[1, 0], [1, 0], [0, 1]]}


class Fake:
    """A working, simplified implementation: 2-D 'model' = [has 'founded', has 'widget']."""
    def embed(self, input):
        return {"embeddings": [[float("founded" in t), float("widget" in t)] for t in input]}


class Spy(Fake):
    """A fake that also records how it was called."""
    def __init__(self):
        self.calls = []

    def embed(self, input):
        self.calls.append(list(input))
        return super().embed(input)


print("stub :", search(Stub(), "who founded acme?", docs), "(same answer for ANY question)")
print("stub :", search(Stub(), "what colour is the widget?", docs), "<- wrong, the stub cannot tell")
print("fake :", search(Fake(), "who founded acme?", docs))
print("fake :", search(Fake(), "what colour is the widget?", docs))

spy = Spy()
search(spy, "who founded acme?", docs)
print("spy  : recorded call ->", spy.calls)

mock = Mock()
mock.embed.return_value = {"embeddings": [[1, 0], [1, 0], [0, 1]]}
search(mock, "who founded acme?", docs)
mock.embed.assert_called_once_with(input=["who founded acme?", *docs])
print("mock : assert_called_once_with passed; calls =", mock.embed.call_args_list)
```
Real output:
```
stub : acme was founded in 2015 (same answer for ANY question)
stub : acme was founded in 2015 <- wrong, the stub cannot tell
fake : acme was founded in 2015
fake : the widget is blue
spy  : recorded call -> [['who founded acme?', 'acme was founded in 2015', 'the widget is blue']]
mock : assert_called_once_with passed; calls = [call(input=['who founded acme?', 'acme was founded in 2015', 'the widget is blue'])]
```
**How it works.**
- **Stub:** the question vector is always `[1, 0]`, so the first document always wins, including for the widget
  question, where it's wrong. A test "who founded acme → acme doc" would pass by coincidence.
- **Fake:** its 2-number "model" really depends on the text (`"founded" in t`, `"widget" in t`), so each question
  gets its own answer. Only a fake can test *retrieval*.
- **Spy:** the same fake behaviour, plus `self.calls` records the exact list sent, so a test can check the question
  came first and was followed by both documents.
- **Mock:** `Mock()` accepts any call; `return_value` scripts the answer; `assert_called_once_with` verifies the
  call's arguments. It checks the *conversation*, not the *result*.

### 3b. The exact project code — `tests/support.py`
Lines 57–81 (the fake + spy) and 98–100 (the stub):
```python
    57	class BagOfWordsClient:
    58	    """A toy embedding model with the same `embed` shape as `ollama.Client`.
    ...
    74	    def __init__(self, dim: int = settings.embed_dim) -> None:
    75	        self.dim = dim
    76	        self.inputs: list[str] = []
    77
    78	    def embed(self, model="", input=(), truncate=None):
    79	        texts = [input] if isinstance(input, str) else list(input)
    80	        self.inputs.extend(texts)
    81	        return {"embeddings": [self.vector(text) for text in texts]}
    ...
    98	def pinned_digest(url: str, model: str | None = None) -> str:
    99	    """A stand-in for verify_model that accepts without a network call."""
   100	    return settings.embed_model_digest
```
**How it works in the project.**
- **Line 78:** the signature `embed(self, model="", input=(), truncate=None)` matches the `EmbedClient` Protocol
  from P3-M3, so the *real* `Embedder` accepts it with no special code (structural typing, doc 13 C6). Everything
  above the client (batching, prefixes, validation, provenance) runs for real.
- **Line 79:** Ollama's `embed` accepts a string or a list, and the fake does the same.
- **Line 80 (the spy part):** every text sent is appended to `self.inputs`, which is how
  `test_both_sides_reach_the_model_with_their_own_prefix` checks that the query carried `search_query: ` and
  the documents `search_document: `.
- **Line 81 (the fake part):** each text becomes a real vector (C3), and the response shape
  `{"embeddings": [...]}` matches Ollama's.
- **Lines 98–100 (the stub):** `Embedder.__init__` calls `verify(ollama_url, model=model)`. This stub accepts any
  arguments and returns the pinned digest, so no network call happens. It's a stub on purpose: the *model check*
  isn't what these tests are about (`tests/embedding/test_lock.py` covers it).

### 4. Benefits
The right double for each job: fakes for behaviour, spies for "what was sent", stubs for irrelevant
dependencies, real lightweight implementations (in-memory Qdrant) where available.

- **What breaks:** stubbing something whose behaviour the test is about; mocks that make tests re-state the
  implementation, so any refactor breaks them even when behaviour is unchanged.
- **Docs:** [Martin Fowler, "Mocks Aren't Stubs"](https://martinfowler.com/articles/mocksArentStubs.html) ·
  [Gerard Meszaros, *xUnit Test Patterns* (test doubles)](http://xunitpatterns.com/Test%20Double.html) ·
  [`unittest.mock`](https://docs.python.org/3/library/unittest.mock.html)

### Micro-exercises
1. Write a stub-based test "widget question returns the widget doc". Does it pass? What does that prove?
2. Replace the `Fake` with `Mock(side_effect=...)` that computes vectors. Is it now a mock or a fake?
3. Use `BagOfWordsClient.inputs` to assert the number of batches sent during `indexed.ingest()`.
4. Which project double would you replace with a real object if Ollama were always available? What would you lose?
5. Classify each double in `tests/embedding/test_embedder.py` (FakeClient, RejectingClient, DownClient...).

**Internals.** `Mock` creates attributes on first access (`mock.embed` is itself a `Mock`) and records every call
in `call_args_list`; `assert_called_once_with` compares the single recorded call with the expected arguments.
**Checkpoint:** explain why a stub embedding client can't be used to test retrieval ranking.

---

## C3 — A toy embedding model: bag of words + the hashing trick

### 1. Definition + analogy

**What it is.** An embedding turns text into a vector so that **similar texts get nearby vectors** (doc 12,
C7). Real models such as nomic-embed-text *learn* that mapping from huge amounts of text, which is why they
understand that "cost" and "price" are related. For tests we need something **deterministic**, **offline**, and
still meaningful enough that a question finds the chunk that answers it. The oldest, simplest answer is a
**bag-of-words (BoW)** vector:

1. **Tokenise:** lower-case the text and split it into words (`[a-z0-9]+`).
2. **Drop words with no topic**, like "the", "what", "who", and our task prefixes `search_query` /
   `search_document`, which appear in *every* input and would make every query look like every document.
3. **The hashing trick:** instead of keeping a dictionary of every word, **hash** each word to a number and take it
   modulo 768 (our vector size). The word goes into that **slot**. Any vocabulary size then fits in a fixed-size
   vector. The price is **collisions**: two different words can land in the same slot.
4. **Count** occurrences per slot, then **normalise** to length 1, so cosine similarity is just a dot product,
   exactly like the real model's vectors.

Cosine similarity of two BoW vectors then measures **shared words**: no shared words gives 0; more shared,
distinctive words give a higher score. It's called "bag" of words because order is thrown away: "dog bites man"
equals "man bites dog".

**Which hash matters.** Python's built-in `hash()` of a string is **randomised per process** (a security feature,
controlled by `PYTHONHASHSEED`). A model built on it would put "acme" in a different slot on every test run, so
the "same" model would give different results each time. `hashlib.md5` is a fixed algorithm and gives the same
number everywhere, forever. (MD5 is broken for *security*, but that's irrelevant here: we only need a stable
spread of numbers.)

**Analogy.** Sorting a class's words into 768 labelled boxes by a fixed rule: "count the letters, do some maths,
that's your box". Two texts are "similar" if their boxes fill up the same way. Sometimes two different words end
up in the same box; that's a collision.

### For a 14-year-old

Imagine you want a quick way to tell which book a question is about, without reading like a human.

1. You take the question "Who founded Acme?" and **throw away boring words** like "who", "the", "what". You're
   left with **"founded"** and **"acme"**.
2. You have **768 boxes** in a row. For each word you use a fixed secret recipe (the *hash*) that always turns the
   same word into the same box number. "acme" always goes in box **166**, "founded" always in box **563**. You drop
   a counter in each word's box.
3. You do the same for every page of every book.
4. A page is a good match for the question if **its counters are in the same boxes** as the question's counters.
   "Acme was founded in 2015..." has counters in boxes 166 *and* 563, a great match. "The widget ships in blue" has
   none in those boxes, so no match at all.

Two catches:
- **The recipe must never change.** Python's built-in recipe (`hash()`) secretly changes every time you start the
  program, so "acme" might land in box 194 today and 726 tomorrow. That would ruin everything, so we use a recipe
  that never changes (`md5`).
- **It only understands exact words.** "cost" and "price" mean the same to you, but to the boxes they're totally
  different words. That's why this toy is only for *testing the plumbing*, not for real searching.

### 2. Why (vs the traditional approach)
Before neural embeddings, search engines used exactly this family of methods (BoW, TF-IDF, BM25). For tests, a toy
like this beats both a stub (no behaviour) and recorded real vectors (large, and stale on model change).

### 3a. Minimal code — every step of the toy model, visible
`c3_bag_of_words.py`
```python
# The toy embedding model, step by step: words -> slots -> counts -> unit vector -> cosine.
import hashlib
import math
import re
import subprocess
import sys

# Run from the project root: the `tests` package is importable from there, not from this folder.
sys.path.insert(0, ".")
from tests.support import BagOfWordsClient  # noqa: E402

# Step 1: Python's built-in hash() changes between processes; hashlib does not
code = "import hashlib; print(hash('acme') % 768, int(hashlib.md5(b'acme').hexdigest(), 16) % 768)"
for seed in ("1", "2"):
    out = subprocess.run([sys.executable, "-c", code], env={"PYTHONHASHSEED": seed, "SYSTEMROOT": "C:\\Windows"},
                         capture_output=True, text=True).stdout.split()
    print(f"1. PYTHONHASHSEED={seed}: hash('acme') % 768 = {out[0]:>3} | md5('acme') % 768 = {out[1]}")

# Step 2: which slot each word lands in, and which words are ignored
model = BagOfWordsClient()
text = "search_document: Acme was founded in 2015 by Ada Lovelace."
words = re.findall(r"[a-z0-9]+", text.lower())
for w in words:
    slot = "ignored" if w in model.IGNORED else int(hashlib.md5(w.encode()).hexdigest(), 16) % 768
    print(f"2. {w!r:<12} -> {slot}")

# Step 3: counts become a unit vector
v = model.vector(text)
nonzero = {i: round(x, 3) for i, x in enumerate(v) if x}
print(f"3. non-zero slots: {nonzero} | length = {math.sqrt(sum(x * x for x in v)):.3f}")


def cosine(a, b):
    return sum(x * y for x, y in zip(model.vector(a), model.vector(b)))


# Step 4: similarity is word overlap, nothing more
q = "search_query: Who founded Acme?"
for doc in ["Acme was founded in 2015 by Ada Lovelace.", "Acme employs thirty people across two offices.",
            "Widget pricing starts at ten dollars per unit."]:
    print(f"4. cos(question, {doc[:34]!r:<37}) = {cosine(q, 'search_document: ' + doc):.3f}")

# Step 5: the known blind spot -- synonyms share no words
print(f"5. cos('How much does a widget cost?', 'Widget pricing starts...') = "
      f"{cosine('How much does a widget cost?', 'Widget pricing starts at ten dollars'):.3f} (only 'widget' shared)")

# Step 6: collisions -- different words landing in the same slot
corpus_words = set(re.findall(r"[a-z0-9]+", open("data/company/about.md", encoding="utf-8").read().lower()))
slots = {}
for w in corpus_words - model.IGNORED:
    slots.setdefault(int(hashlib.md5(w.encode()).hexdigest(), 16) % 768, []).append(w)
shared = {s: ws for s, ws in slots.items() if len(ws) > 1}
print(f"6. about.md: {len(corpus_words - model.IGNORED)} distinct words -> {len(slots)} slots; "
      f"{len(shared)} slots shared, e.g. {list(shared.values())[:2]}")
```
Real output:
```
1. PYTHONHASHSEED=1: hash('acme') % 768 = 194 | md5('acme') % 768 = 166
1. PYTHONHASHSEED=2: hash('acme') % 768 = 726 | md5('acme') % 768 = 166
2. 'search'     -> ignored
2. 'document'   -> ignored
2. 'acme'       -> 166
2. 'was'        -> 457
2. 'founded'    -> 563
2. 'in'         -> ignored
2. '2015'       -> 443
2. 'by'         -> ignored
2. 'ada'        -> 601
2. 'lovelace'   -> 339
3. non-zero slots: {166: 0.408, 339: 0.408, 443: 0.408, 457: 0.408, 563: 0.408, 601: 0.408} | length = 1.000
4. cos(question, 'Acme was founded in 2015 by Ada Lo' ) = 0.577
4. cos(question, 'Acme employs thirty people across ' ) = 0.471
4. cos(question, 'Widget pricing starts at ten dolla' ) = 0.000
5. cos('How much does a widget cost?', 'Widget pricing starts...') = 0.236 (only 'widget' shared)
6. about.md: 169 distinct words -> 149 slots; 20 slots shared, e.g. [['portfolio', 'employees'], ['along', 'product']]
```
**How it works.**
- **Step 1:** two Python processes with different hash seeds put `acme` in slot **194** and **726** with `hash()`, but
  **166** both times with `md5`. The toy model must use the stable one.
- **Step 2:** the prefix words (`search`, `document`) and filler (`in`, `by`) are ignored; 6 topic words get slots.
  `search_document:` splits into `search` and `document` because `_` isn't in `[a-z0-9]`.
- **Step 3:** 6 counts of 1 each; dividing by √6 gives 6 × 0.408. The length is exactly 1.0, just like nomic's
  vectors, so the real `Embedder`'s unit-norm validation (doc 13 C4) passes.
- **Step 4:** the question keeps `founded` and `acme`. The founding chunk shares both words: 2/(√2·√6) = **0.577**.
  The staff chunk shares only `acme`: **0.471**. The widget chunk shares nothing: **0.000**. The ranking is exactly
  what a retrieval test needs.
- **Step 5:** "cost" vs "pricing" share nothing; only `widget` overlaps (0.236). This is the toy's documented blind
  spot.
- **Step 6:** the real `about.md` has 169 distinct topic words in 149 slots: **20 collisions** (e.g. "portfolio" and
  "employees" share a slot). Harmless for tests, but a reason real models don't work this way.

### 3b. The exact project code — `BagOfWordsClient.vector` and `IGNORED`
`tests/support.py`, lines 67–72 and 83–95:
```python
    67	    # The task prefixes and question words carry no topic for a word-count model;
    68	    # counting them would make every query look similar to every document.
    69	    IGNORED = {
    70	        "search", "query", "document", "what", "who", "which", "how", "does", "do", "is",
    71	        "are", "the", "a", "an", "of", "in", "to", "and", "for", "by", "our", "per",
    72	    }  # fmt: skip
    ...
    83	    def vector(self, text: str) -> list[float]:
    84	        counts = [0.0] * self.dim
    85	        for word in re.findall(r"[a-z0-9]+", text.lower()):
    86	            if word in self.IGNORED:
    87	                continue
    88	            # hashlib, not hash(): Python randomises str hashes per process, which
    89	            # would give a different "model" on every test run.
    90	            counts[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
    91	        norm = math.sqrt(sum(c * c for c in counts))
    92	        if norm == 0:
    93	            # A text with no counted words still needs a valid unit vector.
    94	            counts[0], norm = 1.0, 1.0
    95	        return [c / norm for c in counts]
```
**How it works in the project.**
- **Lines 69–72:** the ignore list contains the three prefix words, question words, and a few fillers. `# fmt: skip`
  stops ruff's formatter from spreading the set over 22 lines.
- **Line 84:** `self.dim` defaults to `settings.embed_dim` (768), so the toy's vectors pass the same dimension check
  as nomic's.
- **Line 85:** lower-case, then keep runs of letters/digits. Punctuation and underscores split words.
- **Line 90:** the hashing trick. `hexdigest()` gives 32 hex characters; `int(..., 16)` turns it into a (huge)
  integer; `% self.dim` picks a slot from 0 to 767.
- **Lines 91–94:** normalise to length 1. A text made only of ignored words (e.g. "What is the?") would be all zeros,
  and dividing by 0 is impossible, so it gets slot 0. Without this, `Embedder`'s norm check would reject the vector.
- **Line 95:** the unit vector, as a plain list of floats, like Ollama returns.
- The project test that pins these properties is `test_toy_model_is_deterministic_normalised_and_word_based`
  (lines 98–111): same input gives the same vector, length 1, and shared topic words beat no shared words.

### 4. Benefits
Deterministic, offline, fast, and semantically *just* meaningful enough: real questions work in hermetic tests.

- **What breaks:** using `hash()` (a different model per run); forgetting to ignore the prefixes (every query looks
  alike); expecting synonyms to work.
- **Docs:** [Weinberger et al., "Feature Hashing for Large Scale Multitask Learning" (ICML 2009)](https://arxiv.org/abs/0902.2206) ·
  [scikit-learn `HashingVectorizer`](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.HashingVectorizer.html) ·
  [Python `PYTHONHASHSEED`](https://docs.python.org/3/using/cmdline.html#envvar-PYTHONHASHSEED) ·
  [Manning, Raghavan & Schütze, *Introduction to Information Retrieval*, ch. 6 (vector space model)](https://nlp.stanford.edu/IR-book/)

### Micro-exercises
1. Remove `"search"` and `"query"` from `IGNORED`. Recompute step 4. Do the widget chunk's scores stay 0?
2. Reduce `dim` to 16. How many collisions does `about.md` produce now?
3. Add TF-IDF-style weighting: divide each count by how many documents contain the word. Which chunk wins step 4?
4. Why does step 4's first score equal exactly 1/√3? *Hint:* 2 shared words, 2 and 6 total words.
5. Make a hermetic xfail test for the synonym blind spot ("How much does a widget cost?" → `widget.md_1`).

**Internals.** MD5 produces 128 bits; taking them modulo 768 spreads words almost uniformly over the slots, so the
expected number of colliding word pairs for n words is roughly n(n−1)/(2·768). For 169 words that's about 18.5; we observed
20 shared slots.
**Checkpoint:** explain why the toy uses `hashlib.md5` and not `hash()`, and what the toy can't represent.

---

## C4 — End-to-end retrieval tests, asserted by rank

### 1. Definition + analogy

**What it is.** An **end-to-end (flow) test** runs a feature through all its real components in the order
production uses them. Our retrieval flow is:

```
write chunk files ─► run_ingestion (preflight, load, embed, upsert, orphans, verify) ─► QdrantVectorStore.search
```

The hermetic version replaces only the two external services: Ollama → `BagOfWordsClient`, Qdrant server →
in-memory `QdrantClient`. Everything else (ingestion stages, `Embedder` batching, prefixes and validation, store
upsert with the delete-by-doc_id guard, `query_points`, payload → `DocumentChunk`) is the real code.

**Known-answer tests** pick questions whose answering chunk is known and assert the system finds it. This is the
smallest form of **retrieval evaluation**; later phases grow it into labelled question sets and metrics such as
**hit@k** ("the answer is somewhere in the top k") and **MRR** (mean reciprocal rank).

**Assert by rank, not by score.** A similarity score is a *model-specific* number. Retrain the model, change a
prefix, or update a library, and every score shifts, while the ranking (which chunk comes first) may be unchanged.
A test like `score == 0.8165` breaks on every harmless change, so people learn to "just update the number", and
the test stops protecting anything. The property retrieval exists for is **order**: "the answering chunk is at
position 1" (hermetic) or "within the top 3" (live, where margins can be tiny).

**Freshness tests** check the flow over *time*: after a document is **edited**, search finds the new text (and the
old extra chunk is gone); after it's **deleted**, search never returns it. These exercise the P3-M4 delete guard and
the P3-M5 orphan step together, through search.

**Analogy.** Testing a library's catalogue by asking the librarian real questions ("where's the book about
Ada Lovelace?") and checking they walk to the right shelf, not by measuring how many seconds they hesitated.

### For a 14-year-old

Imagine you built a machine that finds the right page in a scrapbook when you ask it a question. How do you test it?

**Not like this:** "When I ask 'Who founded Acme?', the machine should say it's **81.65 % sure**." Next week you
oil the machine, it says **71 % sure**, and your test fails, even though it still found the right page! The exact
percentage was never the point.

**Like this:** "When I ask 'Who founded Acme?', the page about the founding should be the **first page** the
machine shows me." Oil it, tweak it, whatever: as long as the right page comes first, it's working.

We also test the scrapbook **changing over time**:
- **You rewrite a page** ("founded by Grace Hopper" instead of "Ada Lovelace"). Ask "Who is Grace Hopper?" and the
  machine must show the **new** page, and the old leftover page must be gone.
- **You tear a page out.** Ask about it, and the machine must **never** show it again.

And we test the whole machine at once (loading pages, reading them, filing them, finding them) instead of each gear
on its own, because gears that each work alone can still be put together wrong.

### 2. Why (vs the traditional approach)
Component tests with isolated fakes can all pass while the assembled pipeline is wrong: a prefix applied twice, a
store expecting different payload keys than ingestion writes, a delete guard filtering on the wrong field. Only a
test through all components sees those.

### 3a. Minimal code — score tests break on harmless changes, rank tests don't
`c4_rank_vs_score.py`
```python
# Assert by RANK, not by SCORE: a harmless model update breaks score tests, not rank tests.
import math

VOCAB = ["founded", "acme", "2015", "widget", "values"]


def embed(text, weights):
    # count vocabulary words, scale each by a per-word weight (the "model"), make unit length
    raw = [text.lower().count(word) * w for word, w in zip(VOCAB, weights)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


DOCS = {
    "about_0": "Acme was founded in 2015",
    "about_1": "Acme has two offices",
    "widget_0": "The widget ships in blue",
}
MODEL_V1 = [1.0, 1.0, 1.0, 1.0, 1.0]
MODEL_V2 = [1.4, 0.8, 1.6, 1.0, 1.0]        # "retrained": same idea, slightly different weights


def search(question, weights):
    q = embed(question, weights)
    scored = [(sum(a * b for a, b in zip(q, embed(t, weights))), cid) for cid, t in DOCS.items()]
    return sorted(scored, reverse=True)


baseline_score = search("Who founded Acme?", MODEL_V1)[0][0]      # the number a score test would hard-code
print(f"score test written against v1: expects {baseline_score:.4f}")
for label, weights in (("v1", MODEL_V1), ("v2", MODEL_V2)):
    ranking = search("Who founded Acme?", weights)
    top_score, top_id = ranking[0]
    score_ok = abs(top_score - baseline_score) < 0.001
    rank_ok = top_id == "about_0"
    print(f"model {label}: ranking {[(cid, round(s, 3)) for s, cid in ranking]}")
    print(f"          score test {'PASS' if score_ok else 'FAIL'} | rank test {'PASS' if rank_ok else 'FAIL'}")
```
Real output:
```
score test written against v1: expects 0.8165
model v1: ranking [('about_0', 0.816), ('about_1', 0.707), ('widget_0', 0.0)]
          score test PASS | rank test PASS
model v2: ranking [('about_0', 0.71), ('about_1', 0.496), ('widget_0', 0.0)]
          score test FAIL | rank test PASS
```
**How it works.**
- The "model" is 5 word counts, each scaled by a weight. v2 changes the weights, like retraining would.
- Under v1 the top score is 0.8165, which is what a score-based test would hard-code.
- Under v2, `about_0` is **still first** and the order is unchanged, but its score dropped to 0.71. The score test
  **fails**, the rank test **passes**. Nothing is actually broken, so only the rank test gives the right verdict.

### 3b. The exact project code — the `indexed` fixture and the flow tests
`tests/test_retrieval.py`, lines 53–92:
```python
    53	CORPUS = {
    54	    "company/about.md": [
    55	        "Acme was founded in 2015 by Ada Lovelace.",
    56	        "Acme employs thirty people across two offices.",
    57	    ],
    58	    "company/culture.md": ["Our values are curiosity, honesty and kindness."],
    59	    "products/widget.md": [
    60	        "The widget ships in blue and red colours.",
    61	        "Widget pricing starts at ten dollars per unit.",
    62	    ],
    63	}
    64
    65
    66	def toy_embedder_factory(model: BagOfWordsClient):
    67	    return lambda: Embedder(model, verify=pinned_digest)
    68
    69
    70	@pytest.fixture
    71	def indexed(tmp_path):
    72	    """The toy corpus ingested into in-memory Qdrant through the real job."""
    73	    client = QdrantClient(":memory:")
    74	    model = BagOfWordsClient()
    75	    chunks_dir = tmp_path / "chunks"
    76	    write_corpus(chunks_dir, CORPUS)
    77
    78	    def ingest(**kwargs):
    79	        return run_ingestion(
    80	            client,
    81	            toy_embedder_factory(model),
    82	            collection=COLLECTION,
    83	            chunks_dir=chunks_dir,
    84	            log_path=tmp_path / "log.json",
    85	            **kwargs,
    86	        )
    87
    88	    ingest()
    89	    store = QdrantVectorStore(client, Embedder(model, verify=pinned_digest), collection=COLLECTION)
    90	    return SimpleNamespace(
    91	        client=client, model=model, store=store, chunks_dir=chunks_dir, ingest=ingest
    92	    )
```
Lines 117–130 (known answers) and 157–176 (freshness):
```python
   117	@pytest.mark.parametrize(
   118	    ("question", "expected"),
   119	    [
   120	        ("Who founded Acme?", "company/about.md_0"),
   121	        ("What are our values?", "company/culture.md_0"),
   122	        ("What is the widget pricing?", "products/widget.md_1"),
   123	        ("Which colours does the widget ship in?", "products/widget.md_0"),
   124	    ],
   125	)
   126	def test_ingested_corpus_answers_questions(indexed, question, expected):
   127	    # INTERVIEW: asserted by rank, not by score. Scores depend on the model and
   128	    # shift for harmless reasons; "the answering chunk comes first" is the property
   129	    # retrieval is actually for.
   130	    assert indexed.store.search(question, k=3)[0].chunk_id == expected
   ...
   157	def test_edited_document_is_found_by_its_new_text_after_reingest(indexed):
   158	    write_corpus(
   159	        indexed.chunks_dir,
   160	        {**CORPUS, "company/about.md": ["Acme was founded in 1999 by Grace Hopper."]},
   161	    )
   162	    indexed.ingest()
   163
   164	    top = indexed.store.search("Who is Grace Hopper?", k=1)[0]
   165	    assert top.chunk_id == "company/about.md_0" and "Grace Hopper" in top.text
   166	    # The document shrank from 2 chunks to 1: its old second chunk must be gone.
   167	    stored = indexed.store.stored_chunk_ids_by_document()["doc-company/about.md"]
   168	    assert stored == {"company/about.md_0"}
   169
   170
   171	def test_deleted_document_disappears_from_search(indexed):
   172	    (indexed.chunks_dir / "products__widget.md.json").unlink()
   173	    indexed.ingest()
   174
   175	    ids = [c.chunk_id for c in indexed.store.search("widget pricing colours", k=10)]
   176	    assert not any(chunk_id.startswith("products/widget.md") for chunk_id in ids)
```
**How it works in the project.**
- **Lines 53–63:** 3 documents, 5 chunks. Each question is designed so exactly one chunk shares its distinctive
  words: "pricing" only in `widget.md_1`, "colours" only in `widget.md_0`.
- **Lines 66–67:** `run_ingestion` needs a *factory* (it builds the Embedder in its preflight stage, P3-M5); this
  returns one that wires the toy model into the real `Embedder`, with the stub model check.
- **Lines 73–76:** a fresh in-memory Qdrant and a temporary chunks folder per test (function scope, C5), so tests
  can't affect each other.
- **Lines 78–88:** `ingest()` runs the **real P3-M5 job**: all six stages, including verification. It's also exposed
  on the returned object so freshness tests can re-run it after changing files.
- **Line 89:** a store for querying, with its own `Embedder` over the *same* toy model, the same model on both sides,
  as in production.
- **Line 130:** the whole flow in one line; `[0]` is the rank-1 chunk.
- **Lines 158–162:** `write_corpus` rewrites `about.md` with **one** chunk (it had two), then re-ingests.
- **Lines 164–168:** the new text is found at rank 1, **and** the stored chunk IDs for that document are exactly
  `{"company/about.md_0"}`. The stale `about.md_1` was removed by the P3-M4 delete-by-doc_id guard.
- **Lines 172–176:** deleting the chunk file makes `widget.md` an orphan; the P3-M5 orphan stage removes it, and
  searching with *all* of its words (k=10, more than the corpus holds) returns nothing from it.

### 4. Benefits
One test exercises every component's contract with its neighbours; rank-based assertions survive harmless change;
freshness tests prove the index follows the source over time.

- **What breaks:** score assertions (brittle); flow tests with a stub model (meaningless); flow tests that bypass
  the real ingestion job (would miss its stages).
- **Docs:** [pytest parametrize](https://docs.pytest.org/en/stable/how-to/parametrize.html) ·
  [Manning et al., *Introduction to IR*, ch. 8 (evaluation)](https://nlp.stanford.edu/IR-book/) ·
  [Ragas: retrieval metrics](https://docs.ragas.io/)

### Micro-exercises
1. Add a question the toy answers *wrongly* on purpose and explain why from its words.
2. Change line 130 to compare scores (`>= 0.5`). Run the suite, then change `IGNORED` slightly. What happens?
3. Write a test that edits `widget.md` from 2 chunks to 3 and checks all three are searchable.
4. Compute hit@1 over the 4 parametrised questions. What would MRR be if one answer came second?
5. Why is `k=10` used in the deletion test rather than `k=1`?

**Internals.** `@pytest.mark.parametrize` generates one test item per tuple, with IDs like
`test_ingested_corpus_answers_questions[Who founded Acme?-company/about.md_0]`. Each item gets its own `indexed`
fixture instance, because the fixture has function scope.
**Checkpoint:** name the components between `write_corpus` and `search` that line 130 exercises, and why a rank
assertion is safer than a score assertion.

---

## C5 — Fixtures: scope, sharing, and guaranteed teardown

### 1. Definition + analogy

**What it is.** A **pytest fixture** is a function that prepares something a test needs (data, a client, a temporary
folder) and optionally cleans it up afterwards. A test asks for it simply by naming it as a parameter
(`def test_x(indexed): ...`). pytest runs the fixture, passes the result in, and cleans up after.

Two properties matter most.

**(a) Scope: how often setup runs, and how long the result lives.**
| Scope | Setup runs... | Lives until... | Our use |
|---|---|---|---|
| `function` (default) | once **per test** | that test ends | `indexed`, `cli_env`: every test gets a fresh in-memory index |
| `module` / `class` | once per file / class | the file / class is done | — |
| `session` | once per **pytest run**, the first time a test asks for it | the whole run ends | `live_index`: embedding 20 real chunks (~21 s) once, shared by every live test |

The trade-off: wider scope means **faster** runs (expensive setup happens once), but **shared state**. If one
test changed the shared scratch collection, later tests would see it. Our live tests only *read* from it.

**(b) Teardown with `yield` + `finally`.** A fixture that `yield`s splits into "before" (setup) and "after"
(teardown). Code after the `yield` runs when the fixture's scope ends. Putting cleanup in a **`finally`**
guarantees it runs even if the setup itself fails halfway, or a test fails. For live tests that matters: every
failed run would otherwise leave a `test_live_…` collection behind on the shared Qdrant server.

**Built-in fixtures used:** `tmp_path` (a fresh temporary folder per test), `tmp_path_factory` (the session-wide
version, to make folders from session fixtures), `monkeypatch` (C8), `capsys` (C9).

**Analogy.** Catering. A function-scope fixture is a fresh plate for every guest. A session-scope fixture is the big
soup pot: cooked once, everyone serves from it, and washed up once at the end, however the party went.

### For a 14-year-old

Think of a science lab at school.

- For each experiment you need **clean test tubes**. You don't reuse a tube from the last experiment, or leftover
  chemicals would mess up your result. That's a **per-test fixture**: fresh every time.
- But the big **oven** takes 20 minutes to heat up. You don't switch it on and off for every experiment; you heat it
  **once** at the start of the lesson, everyone uses it, and it's switched off **once** at the end. That's a
  **session fixture**.

There's one rule the teacher is strict about: **the oven gets switched off at the end, no matter what.** Even if an
experiment explodes (a test fails), even if someone drops something while the oven was still heating up (setup
fails). In code, that "no matter what" is the word **`finally`**.

In our project, the "oven" is a special practice collection in the database, filled with 20 real chunks, which
takes about 21 seconds to build. We build it once, every live test uses it, and at the very end it's deleted, even
if tests failed, so we never leave mess on the database.

### 2. Why (vs the traditional approach)
`setUp`/`tearDown` methods (xUnit style) run around *every* test and can't easily share an expensive resource
across files; manual cleanup at the end of a test is skipped when an assertion fails earlier in the test.

### 3a. Minimal code — watch setup and teardown order
`demo_c5_fixture_scopes.py` (run with `pytest ... -q -s -p no:cacheprovider`)
```python
import pytest

events = []


@pytest.fixture
def per_test():
    events.append("setup per_test")
    yield "fresh"
    events.append("teardown per_test")


@pytest.fixture(scope="session")
def expensive():
    events.append("SETUP expensive (once)")
    try:
        yield "shared"
    finally:
        events.append("TEARDOWN expensive (once, after the last test)")
        print("\n" + "\n".join(f"  {i + 1:>2}. {e}" for i, e in enumerate(events)))


def test_one(per_test, expensive):
    events.append("  run test_one")


def test_two(per_test, expensive):
    events.append("  run test_two (fails)")
    assert False, "a failing test must not skip teardown"


def test_three(per_test):
    events.append("  run test_three (does not ask for expensive)")
```
Real output (event log printed at session end):
```
   1. SETUP expensive (once)
   2. setup per_test
   3.   run test_one
   4. teardown per_test
   5. setup per_test
   6.   run test_two (fails)
   7. teardown per_test
   8. setup per_test
   9.   run test_three (does not ask for expensive)
  10. teardown per_test
  11. TEARDOWN expensive (once, after the last test)
1 failed, 2 passed in 0.27s
```
**How it works.**
- **Line 1:** the session fixture is set up when the **first test that asks for it** (test_one) starts, not at
  import time.
- **Lines 2–10:** `per_test` is set up and torn down around **each** test: three times.
- **Lines 6–7:** test_two **failed**, and its teardown still ran.
- **Line 9:** test_three doesn't ask for `expensive`, so it neither creates nor tears it down; it runs while the
  session resource still exists.
- **Line 11:** `expensive` is torn down **once**, after the last test, in its `finally` block, even though a test
  failed along the way.

### 3b. The exact project code — the session-scoped `live_index`
`tests/test_retrieval.py`, lines 295–329:
```python
   295	@pytest.fixture(scope="session")
   296	def live_index(tmp_path_factory):
   297	    """A scratch collection built once per session from 8 real documents.
   298
   299	    Uniquely named so parallel or crashed runs never collide with each other or
   300	    with the real `chunks` collection, and deleted afterwards.
   301	    """
   302	    work = tmp_path_factory.mktemp("live")
   303	    chunks_dir = work / "chunks"
   304	    chunks_dir.mkdir()
   305	    for name in LIVE_FILES:
   306	        shutil.copy(REAL_CHUNKS / name, chunks_dir / name)
   307	    (chunks_dir / "report.json").write_text(json.dumps({"documents_failed": 0}), encoding="utf-8")
   308
   309	    client = build_qdrant_client(QDRANT_URL)
   310	    collection = f"test_live_{uuid.uuid4().hex[:8]}"
   311
   312	    def make_embedder():
   313	        return Embedder(embedder_module.build_ollama_client(OLLAMA_URL), ollama_url=OLLAMA_URL)
   314
   315	    try:
   316	        report = run_ingestion(
   317	            client,
   318	            make_embedder,
   319	            collection=collection,
   320	            chunks_dir=chunks_dir,
   321	            log_path=work / "log.json",
   322	        )
   323	        store = QdrantVectorStore(client, make_embedder(), collection=collection)
   324	        yield SimpleNamespace(client=client, store=store, report=report, collection=collection)
   325	    finally:
   326	        # INTERVIEW: cleanup in finally, so a failing test cannot leave a scratch
   327	        # collection behind on a shared server.
   328	        if client.collection_exists(collection):
   329	            client.delete_collection(collection)
```
**How it works in the project.**
- **Line 295:** `scope="session"`: one instance for the whole pytest run, shared by all 11 live tests.
- **Line 296:** `tmp_path_factory` is the session-scoped temp-folder maker; the per-test `tmp_path` can't be used in
  a session fixture (pytest refuses a narrower-scoped fixture inside a wider one).
- **Lines 302–307:** copy the 8 real chunk files (the subset probed before writing assertions) plus a clean chunking
  report, so the orphan guard (P3-M5) behaves normally.
- **Line 310:** `test_live_` + 8 random hex characters. Two test runs at once, or a crashed earlier run, can never
  collide with each other, and the name can never be `chunks`.
- **Lines 312–313:** real Ollama client through the real `Embedder`, so the real model check (`verify_model`) runs in
  preflight.
- **Lines 316–322:** the real ingestion job: embeds 20 chunks with the real model (~21 s) and verifies the result.
- **Line 324:** everything the tests need, in one `SimpleNamespace` (a lightweight object with attributes).
- **Lines 315 and 325–329:** the `try` opens **before** ingestion, so even if ingestion fails halfway (after creating
  the collection), the `finally` still deletes it. The `collection_exists` check avoids an error when setup failed
  before anything was created.
- Verified after every run in P3-M6: `curl .../collections` lists only `chunks`.

### 4. Benefits
Expensive setup once; fresh state where tests modify things; cleanup that survives failures.

- **What breaks:** session fixtures that tests modify (order-dependent results); cleanup outside `finally`;
  fixed resource names (parallel runs collide; crashed runs block the next one).
- **Docs:** [pytest fixtures: scope](https://docs.pytest.org/en/stable/how-to/fixtures.html#scope-sharing-fixtures-across-classes-modules-packages-or-session) ·
  [yield fixtures / teardown](https://docs.pytest.org/en/stable/how-to/fixtures.html#teardown-cleanup-aka-fixture-finalization) ·
  [`tmp_path` / `tmp_path_factory`](https://docs.pytest.org/en/stable/how-to/tmp_path.html)

### Micro-exercises
1. In the demo, change `expensive` to `scope="module"` and add a second file using it. How many setups now?
2. In the demo, make `expensive` raise *before* its `yield`. Which tests error, and does `finally` run?
3. Try using `tmp_path` inside `live_index` instead of `tmp_path_factory`. Read pytest's error message.
4. What would go wrong if a live test called `live_index.client.delete_collection(live_index.collection)`?
5. **Open Question 1 work:** using this demo and C6's, decide whether a fixture requested *only* by skipped tests
   is ever set up. Then trace `-m live` with Qdrant stopped.

**Internals.** pytest builds a fixture dependency graph per test. For a session fixture it caches the value after
first setup and registers its teardown (the rest of the generator) as a finalizer, run in reverse setup order when
the session ends.
**Checkpoint:** explain what scope `live_index` has, why, and exactly which line guarantees the scratch collection is
deleted.

---

## C6 — Markers, skipif, deselection, registration

### 1. Definition + analogy

**What it is.** **Markers** are labels attached to tests with `@pytest.mark.<name>`. pytest uses them in two very
different ways:

1. **Selection before running** (`-m` expressions). `pytest -m "not live"` **deselects** live tests: they're never
   considered for this run, and the summary counts them as "deselected". CI uses this: `-m "not tiktoken and not live"`.
2. **Behaviour markers** built into pytest: `skip`, `skipif(condition, reason=...)`, `xfail(...)` (C7),
   `parametrize(...)`. `skipif` makes a *selected* test **skip** when its condition is true, and the summary counts it
   as "skipped" with the reason (shown with `-rs`).

**Skipped vs deselected** is an honesty question:
- **deselected** = "we chose not to run this here" (CI has no Qdrant: known and intentional);
- **skipped** = "this was supposed to run, but a precondition wasn't met" (a developer forgot to start Docker).
Printing skip reasons (`-rs`) means nobody mistakes a quiet "s" for coverage.

**When the `skipif` condition is evaluated.** A `skipif` condition is ordinary Python evaluated when the decorator
is applied, i.e. **when pytest imports the test file**, before any test runs. In the project, the network check
`live_services_down()` is stored once in `_SERVICES_DOWN` at import, so a run with the services down doesn't repeat
a 2-second timeout for each of 11 tests.

**Registration.** Custom markers should be declared in `pyproject.toml` under `[tool.pytest.ini_options] markers`.
Unregistered markers produce a `PytestUnknownMarkWarning`; with `--strict-markers` they're an error. That's
important because `-m "not liv"` (a typo) would otherwise silently select everything.

**Stacking marks with a helper.** `live_test` applies a *list* of marks to a function. A decorator is just a
function that takes a function and returns one, so applying several marks is a loop.

**Analogy.** Coloured stickers on exam questions. A sticker ("live") lets the teacher choose which questions to hand
out (deselect). A note on a question ("skip if calculators aren't available") is checked on the day, and if it
applies, the question is handed out but marked "not attempted, reason: no calculator".

### For a 14-year-old

Imagine your teacher has a big stack of test questions. Some need a **calculator**, some need the **computer room**.

- **Stickers (markers):** the teacher puts a green "computer" sticker on questions that need the computer room.
- **Deselect (`-m "not computer"`):** in a normal classroom, the teacher **doesn't hand out** the green-sticker
  questions at all. They're not failed, not skipped, just not part of today's test. That's what our online test
  robot (CI) does, because it has no database to use.
- **Skip with a reason (`skipif`):** on a day when the computer room is *supposed* to be available, the teacher hands
  out the questions, but if the room turns out to be locked, you write "**Skipped: computer room locked**" on them.
  Everyone can see *why* they weren't done.
- **Registering stickers:** the teacher keeps an official list of sticker colours. If someone uses a sticker that's
  not on the list (like "computr", a typo), the teacher notices immediately instead of silently ignoring it.

One more thing: the teacher checks whether the computer room is open **once, first thing in the morning**, not
separately for every question. Our code does the same: it checks whether the database is running **once**, when the
test file is loaded, and remembers the answer.

### 2. Why (vs the traditional approach)
Common alternatives are commenting tests out, `if not available: return` inside the test (which *passes* instead of
skipping, so it's invisible), or separate test directories nobody runs.

### 3a. Minimal code — skipped vs deselected vs unregistered
`demo_c6_markers_skip.py`
```python
import pytest

print("\n[import] module-level code runs once, when pytest imports this file")
SERVICE_DOWN = "pretend service is not reachable"      # stand-in for live_services_down()

LIVE_MARKS = [pytest.mark.slowlive, pytest.mark.skipif(SERVICE_DOWN is not None, reason=SERVICE_DOWN)]


def live_test(function):
    for mark in LIVE_MARKS:
        function = mark(function)
    return function


def test_hermetic():
    assert 1 + 1 == 2


@live_test
def test_needs_service():
    raise AssertionError("never runs while the service is down")


@live_test
@pytest.mark.parametrize("question", ["a", "b"])
def test_needs_service_parametrised(question):
    raise AssertionError("never runs while the service is down")
```
Real output, three ways:
```
$ pytest demo_c6_markers_skip.py -q -rs -s
[import] module-level code runs once, when pytest imports this file
  ...demo_c6_markers_skip.py:10: PytestUnknownMarkWarning: Unknown pytest.mark.slowlive - is this a typo?  You can register custom marks to avoid this warning ...
SKIPPED [1] ...demo_c6_markers_skip.py:23: pretend service is not reachable
SKIPPED [2] ...demo_c6_markers_skip.py:28: pretend service is not reachable
1 passed, 3 skipped, 1 warning in 0.03s

$ pytest demo_c6_markers_skip.py -q -rs -m "not slowlive"
1 passed, 3 deselected, 1 warning in 0.02s

$ pytest demo_c6_markers_skip.py -q --strict-markers
'slowlive' not found in `markers` configuration option
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```
**How it works.**
- **"[import]" printed once:** module-level code, including the skip condition, runs when the file is imported.
- **Run 1:** the 3 service tests are **skipped**; `-rs` groups them with their reason (`[1]` and `[2]` are counts:
  the parametrised test produced 2 items). The `AssertionError` inside never runs. The warning flags the
  unregistered `slowlive` marker.
- **Run 2:** with `-m "not slowlive"` the same 3 tests are **deselected**: not skipped, never considered.
- **Run 3:** `--strict-markers` turns the unregistered marker into a collection **error**. That's why the project
  registers `live` (next section).
- `live_test` works because marks are decorators: applying each in turn attaches them all.

### 3b. The exact project code — `live_test`, the marker registration, and CI
`tests/test_retrieval.py`, lines 275–292:
```python
   275	# Checked once at import: each check is a network call with a timeout, and repeating
   276	# it per test would make a run with the services down slow for no benefit.
   277	_SERVICES_DOWN = live_services_down()
   278	_LIVE_MARKS = [
   279	    pytest.mark.live,
   280	    pytest.mark.skipif(_SERVICES_DOWN is not None, reason=_SERVICES_DOWN or ""),
   281	    pytest.mark.skipif(
   282	        not all((REAL_CHUNKS / name).exists() for name in LIVE_FILES),
   283	        reason="data/chunks is missing the live subset; run `python -m src.ingestion.pipeline`",
   284	    ),
   285	]
   286
   287
   288	def live_test(function):
   289	    """Mark a test as live and skip it, with the reason, when it cannot run here."""
   290	    for mark in _LIVE_MARKS:
   291	        function = mark(function)
   292	    return function
```
`tests/support.py`, lines 103–124:
```python
   103	def reachable(url: str) -> bool:
   104	    try:
   105	        with urllib.request.urlopen(url, timeout=2):
   106	            return True
   107	    except (urllib.error.URLError, OSError):
   108	        return False
   109
   110
   111	def live_services_down() -> str | None:
   112	    """None if Qdrant and Ollama both answer, otherwise a reason to skip."""
   113	    missing = [
   114	        name
   115	        for name, url in (
   116	            ("Qdrant", f"{QDRANT_URL}/readyz"),
   117	            ("Ollama", f"{OLLAMA_URL}/api/version"),
   118	        )
   119	        if not reachable(url)
   120	    ]
   121	    if missing:
   122	        services = " and ".join(missing)
   123	        return f"{services} not reachable; start with `docker compose up -d qdrant ollama`"
   124	    return None
```
`pyproject.toml`, `[tool.pytest.ini_options]`:
```toml
markers = [
  "tiktoken: needs tiktoken's encoding file (network on a cold cache); deselect with -m 'not tiktoken'",
  "live: needs running Qdrant and Ollama (docker compose up -d qdrant ollama); skips itself when they are down",
]
```
**How it works in the project.**
- **Line 277:** the one network check, at import. It returns `None` (all fine) or a *sentence* naming what's missing
  and how to fix it, which becomes the skip reason.
- **Line 279:** the `live` label, used by `-m live` / `-m "not live"`.
- **Line 280:** skip when a service is down; `reason=_SERVICES_DOWN or ""` because `reason` must be a string even
  when the condition is false.
- **Lines 281–284:** a second, independent precondition. `data/chunks` is gitignored generated output, so a fresh
  clone must skip (with instructions) rather than crash in `shutil.copy`.
- **support.py lines 105–108:** `/readyz` and `/api/version` are cheap endpoints. The 2-second timeout bounds the
  wait; `URLError`/`OSError` cover refused connections and timeouts.
- **pyproject markers:** registration documents each marker and keeps `--strict-markers` possible.
- **CI (`-m "not tiktoken and not live" -rs`):** deselects live tests explicitly, so a CI run says "13 deselected"
  rather than "11 skipped", and `-rs` shows anything else that skipped. Verified in P3-M6: CI-equivalent
  `174 passed, 13 deselected`; with Qdrant stopped locally, `15 passed, 11 skipped`, each reason naming Qdrant.

### 4. Benefits
One suite serves CI, laptops without Docker, and laptops with Docker, and every run states what it didn't run and why.

- **What breaks:** unregistered markers (typos select everything); skip conditions that call the network per test;
  `return` instead of skip (silent passes).
- **Docs:** [pytest: marks](https://docs.pytest.org/en/stable/how-to/mark.html) ·
  [skipping](https://docs.pytest.org/en/stable/how-to/skipping.html) ·
  [`-m` expressions](https://docs.pytest.org/en/stable/example/markers.html)

### Micro-exercises
1. Run `pytest tests/test_retrieval.py -m live --collect-only -q`. How many items, and why that number?
2. Add `--strict-markers` to your local run. Does anything fail?
3. Change `reachable`'s timeout to 10 s and stop Qdrant. How long does collection take now?
4. Write `-m "live and not xfail"`. Does that work? *Hint:* xfail isn't a selectable *custom* marker in the
   same way; test it.
5. Why is the `data/chunks` condition a *separate* skipif rather than part of `_SERVICES_DOWN`?

**Internals.** `pytest.mark.skipif(cond, reason=...)` attaches a `Mark` object to the function's `pytestmark` list.
During collection each item gathers its marks; before running, the skipping plugin evaluates any `skipif` and raises
`Skipped` for the item. `-m` expressions are matched against item marker names during
`pytest_collection_modifyitems`, *before* any test or fixture runs.
**Checkpoint:** state the difference between "skipped" and "deselected", and which one CI produces for live tests.

---

## C7 — `xfail(strict=True)`: known bugs as executable records

### 1. Definition + analogy

**What it is.** `@pytest.mark.xfail` declares "this test is **expected to fail**", usually because of a known bug.
pytest then reports:
- **XFAIL** if it fails as expected (the suite stays green);
- **XPASS** if it *unexpectedly passes*.

What happens on XPASS depends on **`strict`**:
- `strict=False` (default): XPASS is reported but the suite **stays green**. The note that "this is broken" is
  silently wrong from then on.
- **`strict=True`**: XPASS **fails the run** (`[XPASS(strict)]`). The moment someone fixes the bug, the suite forces
  them to remove the xfail and turn it into a normal passing test.

That turns a known bug into an **executable record**: it documents the bug, proves it still exists on every run,
and **can't go stale**. The `reason=` text says what the bug is and what would fix it.

A third option, **`raises=...`**, restricts which failure counts as "expected". If the test fails with a different
exception (a crash, not the known miss), it's reported as a real failure.

P3-M6 has two strict xfails, both **found by measurement** (probing the live subset before writing assertions):
- "What does the Carllm product do?" never gets a `products/Carllm.md` chunk into the top 3 (doc 14 C11);
- "Who is the CEO of Insurellm?" gets neither the CEO's HR record nor `about.md` into the top 3 (found in P3-M6).

They're the **acceptance tests** for later retrieval work: hybrid search or reranking "works" when these two start
failing as XPASS(strict).

**Analogy.** A "known issue" sign on a lift: "Button 4 doesn't work". A normal sign stays up after the repair,
confusing everyone. A strict sign rings an alarm the moment button 4 starts working, so someone takes it down.

### For a 14-year-old

Your bike's back brake is weak, and you know it. You stick a note on the handlebar: "**Back brake is weak. Fix:
new brake pads.**"

- **Normal note (not strict):** your dad fixes the brake, but nobody removes the note. Months later someone reads
  "back brake is weak" and doesn't trust a perfectly good brake. The note is **lying** now, and nobody noticed.
- **Strict note:** a magic note that **starts beeping** the moment the brake is fixed. You can't ignore it: you have
  to take it off and replace it with "back brake works". The note can never lie.

Our search has two known weak spots: asking about the product "Carllm" and asking "Who is the CEO?". We wrote tests
for them marked **strict xfail**, meaning "we know this fails today". Every time tests run, they confirm the problem
still exists. When we improve the search later and it starts finding the right answers, these tests **beep** (fail
the run), and we update them. That's how we'll know the improvement actually worked.

### 2. Why (vs the traditional approach)
Known bugs usually live in issue trackers or comments, which nobody re-checks, or as skipped tests, which don't run at
all. An xfail runs every time; a strict one also notices the fix.

### 3a. Minimal code — strict, non-strict, and `raises`
`demo_c7_xfail_strict.py` (run with `pytest ... -q -rxX`)
```python
import pytest

ranking_today = ["products/Lifellm.md_2", "contracts/TechDrive_1", "products/Lifellm.md_1"]
ranking_after_fix = ["products/Carllm.md_0", "products/Lifellm.md_2", "contracts/TechDrive_1"]


@pytest.mark.xfail(strict=True, reason="known miss: product names not separated by embeddings")
def test_known_miss_still_present():
    assert any(c.startswith("products/Carllm.md") for c in ranking_today)


@pytest.mark.xfail(strict=True, reason="known miss: product names not separated by embeddings")
def test_known_miss_after_the_fix_strict():
    assert any(c.startswith("products/Carllm.md") for c in ranking_after_fix)


@pytest.mark.xfail(reason="same, but not strict")
def test_known_miss_after_the_fix_not_strict():
    assert any(c.startswith("products/Carllm.md") for c in ranking_after_fix)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="only an assertion counts as the known miss")
def test_crash_is_not_the_known_miss():
    ranking_today[99]                                   # an IndexError is a different problem
```
Real output:
```
[XPASS(strict)] known miss: product names not separated by embeddings
XFAIL ...::test_known_miss_still_present - known miss: product names not separated by embeddings
XPASS ...::test_known_miss_after_the_fix_not_strict - same, but not strict
E       IndexError: list index out of range
2 failed, 1 xfailed, 1 xpassed in 0.27s
```
**How it works.**
- **Test 1 (today's ranking):** no Carllm chunk, so the assertion fails as expected: **XFAIL**, green.
- **Test 2 (after a fix, strict):** the assertion now passes, and strict turns that into a **failure**
  (`[XPASS(strict)]`), one of the "2 failed".
- **Test 3 (after a fix, not strict):** also passes, but is only reported as **XPASS**; the suite doesn't fail.
  This is the silently stale note.
- **Test 4 (`raises=AssertionError`):** it fails with an `IndexError`, which isn't the declared known failure, so it
  counts as a **real failure**, the other of the "2 failed". Without `raises`, any crash would be hidden as "expected".

### 3b. The exact project code — the two known misses
`tests/test_retrieval.py`, lines 357–376:
```python
   357	@live_test
   358	@pytest.mark.xfail(
   359	    strict=True,
   360	    reason="Known dense-retrieval miss (docs/14 C11): product names are not separated by "
   361	    "embeddings; expected to pass once hybrid keyword + vector search exists.",
   362	)
   363	def test_live_named_product_question_finds_its_product_page(live_index):
   364	    ids = top_ids(live_index, "What does the Carllm product do?")
   365	    assert any(chunk_id.startswith("products/Carllm.md") for chunk_id in ids), ids
   366
   367
   368	@live_test
   369	@pytest.mark.xfail(
   370	    strict=True,
   371	    reason="Known miss: 'CEO' questions rank product and contract chunks above the CEO's "
   372	    "own record; expected to pass once hybrid search or reranking exists.",
   373	)
   374	def test_live_ceo_question_finds_the_ceo(live_index):
   375	    ids = top_ids(live_index, "Who is the CEO of Insurellm?")
   376	    assert {"employees/Avery Lancaster.md_0", "company/about.md_0"} & set(ids), ids
```
The measurement that produced them (probe on the 8-document live subset, before any assertion was written):
```
What does the Carllm product do?
   1. 0.677 products/Lifellm.md_2
   2. 0.668 contracts/Contract with TechDrive Insurance for Carllm.md_1
   3. 0.661 products/Lifellm.md_1
Who is the CEO of Insurellm?
   1. 0.697 products/Lifellm.md_2
   2. 0.682 contracts/Contract with TechDrive Insurance for Carllm.md_0
   3. 0.646 contracts/Contract with TechDrive Insurance for Carllm.md_1
   4. 0.640 employees/Avery Lancaster.md_1
```
**How it works in the project.**
- **Line 357:** `@live_test` is outermost, so when services are down the test is *skipped*, not xfailed. Skip wins.
- **Lines 359–361:** `strict=True`, and a `reason` that names the cause *and* the expected fix, so whoever sees the
  XPASS knows why.
- **Line 365:** the assertion states the **desired** behaviour (a Carllm product chunk in the top 3). The test fails
  today *because* retrieval misses it. The trailing `, ids` puts the actual top 3 into the failure message, visible
  with `pytest -rx -vv`.
- **Line 376:** the CEO question accepts either chunk that answers it (the CEO's own record, or `about.md_0`, which
  names the founder). The probe shows neither in the top 3; `Avery Lancaster.md_1` only reaches #4.
- Neither uses `raises=`. If the Qdrant query itself crashed, that exception would also count as the "expected"
  failure. Exercise 3 asks whether that's acceptable.

### 4. Benefits
Known limitations are recorded in the test suite itself, re-verified on every run, and automatically flagged when fixed.

- **What breaks:** non-strict xfails (stale records); xfails without `raises` hiding unrelated crashes; xfail used to
  silence tests nobody intends to fix.
- **Docs:** [pytest: xfail and `strict`](https://docs.pytest.org/en/stable/how-to/skipping.html#xfail-mark-test-functions-as-expected-to-fail) ·
  [`xfail_strict` ini option](https://docs.pytest.org/en/stable/reference/reference.html#confval-xfail_strict)

### Micro-exercises
1. Add `raises=AssertionError` to both project xfails. Why might that be better?
2. Set `xfail_strict = true` in `pyproject.toml`. What changes for future xfails?
3. Temporarily change line 364's question to "Who founded Insurellm?" and run `-m live`. Read the XPASS(strict) failure.
4. Write a strict xfail for the toy model's synonym blind spot (C3).
5. When hybrid search makes the Carllm test pass, what should the commit do to this test?

**Internals.** The xfail plugin wraps the test call. On failure it records XFAIL (checking `raises` if given). On
success it records XPASS and, if strict, converts the report outcome to "failed" with the `[XPASS(strict)]` message.
**Checkpoint:** explain what `strict=True` changes, and why the Carllm test asserts the behaviour we *want* rather
than the behaviour we *have*.

---

## C8 — Monkeypatching: patch where a name is looked up

### 1. Definition + analogy

**What it is.** **Monkeypatching** means replacing an attribute (a function, class or variable) **at runtime**, for
the duration of a test. pytest's `monkeypatch` fixture does it with `setattr(target, name, new)` and **undoes every
change automatically** when the test ends, so tests can't leak patches into each other.

The hard part is **which** object to patch. In Python, `from lib import build_client` doesn't create a link to
`lib.build_client`. It **copies the reference** into the importing module's own namespace. After that there are *two*
names pointing at the same function:

| How the code gets the name | Where it's looked up at call time | Patch this |
|---|---|---|
| `from lib import build_client` at the **top** of `caller.py` | `caller.build_client` | `caller` |
| `from lib import build_client` **inside the function** | `lib.build_client`, each call | `lib` |
| `def f(factory=build_client)` (a **default argument**) | nowhere: bound once when `def` ran | neither works; pass the argument, or patch the *function* that uses it |

The rule: **patch where the name is looked up, not where it's defined.** Getting it wrong is silent: the patch
"succeeds", the real object is still used, and a test may pass for the wrong reason or hit the network.

The project hits all three styles:
- `ingest.py` does `from src.vectorstore.setup import build_qdrant_client` at the **top**, so tests patch
  `ingest_module.build_qdrant_client`;
- `run_from_args` and `store.main` import `build_ollama_client` / `Embedder` **inside** the function, so tests patch
  `embedder_module`;
- `Embedder.__init__(..., verify=verify_model)` is a **default argument**, so patching `embedder_module.verify_model`
  would do nothing. Tests instead replace the `Embedder` *name* with `functools.partial(Embedder, verify=pinned_digest)`.

**Analogy.** A class phone list. When you join the class you **copy** your friend's number into your own phone. If your
friend changes number and only updates the *school's* list, your phone still calls the old number. To redirect your
calls you must change the number in *your* phone. That's where you "look it up".

### For a 14-year-old

At the start of the year, everyone **copies** the class rep's phone number from the notice board **into their own
phone**. Later the class rep gets a new phone, and the teacher updates the **notice board**.

Who gets the new number?
- **Kids who copied the number into their phone:** they still call the **old** number. The notice board changed,
  but they never look at it again. They look in their *own* phone.
- **Kids who check the notice board every time they call:** they get the **new** number.
- **Kids who wrote the number on the back of their hand on day one** (like a "default argument"): old number
  forever, whatever anyone changes.

In testing, we often want to secretly swap a real thing (like the real database) for a practice version. The trick
is to change it **in the place the code actually looks**. If the code copied it into its own "phone", we must
change it *there*. Change the wrong place and nothing happens, and nobody gets an error message telling you.

### 2. Why (vs the traditional approach)
Alternatives are editing global state by hand (and forgetting to restore it), or designing everything for dependency
injection (P3-M3's `client=` parameters, which the project uses wherever it can). CLIs build their dependencies
internally, so monkeypatching is the practical way in.

### 3a. Minimal code — three lookup styles, two patch targets
`c8_monkeypatch_lookup.py`
```python
# Patch a name WHERE IT IS LOOKED UP. Three lookup styles, three different outcomes.
import sys
import types

import pytest

# A tiny "library" module, like src.embedding.embedder
lib = types.ModuleType("lib")
exec("def build_client():\n    return 'REAL client'\n", lib.__dict__)
sys.modules["lib"] = lib

# A "caller" module that uses it in three different ways, like our CLIs do
caller = types.ModuleType("caller")
exec(
    "from lib import build_client                         # 1. copied into caller at import\n"
    "def copied():\n"
    "    return build_client()\n"
    "def lazy():\n"
    "    from lib import build_client as b                # 2. looked up in lib at CALL time\n"
    "    return b()\n"
    "def default_arg(factory=build_client):               # 3. bound when `def` ran\n"
    "    return factory()\n",
    caller.__dict__,
)

fake = lambda: "FAKE client"

with pytest.MonkeyPatch.context() as mp:
    mp.setattr(lib, "build_client", fake)                  # patch the DEFINITION site
    print("patched lib.build_client:")
    print("   copied()      ->", caller.copied())
    print("   lazy()        ->", caller.lazy())
    print("   default_arg() ->", caller.default_arg())

with pytest.MonkeyPatch.context() as mp:
    mp.setattr(caller, "build_client", fake)               # patch the LOOKUP site
    print("patched caller.build_client:")
    print("   copied()      ->", caller.copied())
    print("   lazy()        ->", caller.lazy())
    print("   default_arg() ->", caller.default_arg())

print("after the with-blocks (monkeypatch undone):", caller.copied(), "|", caller.lazy())
```
Real output:
```
patched lib.build_client:
   copied()      -> REAL client
   lazy()        -> FAKE client
   default_arg() -> REAL client
patched caller.build_client:
   copied()      -> FAKE client
   lazy()        -> REAL client
   default_arg() -> REAL client
after the with-blocks (monkeypatch undone): REAL client | REAL client
```
**How it works.**
- `types.ModuleType` + `sys.modules` create two real modules in memory, so `from lib import ...` works exactly as with
  files.
- **Patching `lib`:** only `lazy()` sees the fake, because it runs `from lib import ...` on every call. `copied()` uses
  its own copy made at import; `default_arg()` uses the object bound when `def` ran.
- **Patching `caller`:** now `copied()` sees the fake, and `lazy()` doesn't (it reads from `lib`, which is untouched).
- **`default_arg()` is REAL both times.** No module patch reaches a default that was evaluated at definition time.
- **Last line:** `MonkeyPatch.context()` restored everything on exit. In a test, the `monkeypatch` fixture does the
  same at test end.

### 3b. The exact project code — `cli_env`
`tests/test_retrieval.py`, lines 182–195:
```python
   182	@pytest.fixture
   183	def cli_env(monkeypatch, tmp_path):
   184	    """Route every CLI's clients to in-memory Qdrant and the toy model."""
   185	    client = QdrantClient(":memory:")
   186	    model = BagOfWordsClient()
   187	    toy_embedder = functools.partial(Embedder, verify=pinned_digest)
   188	    monkeypatch.setattr(ingest_module, "build_qdrant_client", lambda url: client)
   189	    monkeypatch.setattr(ingest_module, "Embedder", toy_embedder)
   190	    monkeypatch.setattr(store_module, "build_qdrant_client", lambda url: client)
   191	    monkeypatch.setattr(embedder_module, "Embedder", toy_embedder)
   192	    monkeypatch.setattr(embedder_module, "build_ollama_client", lambda url: model)
   193	    chunks_dir = tmp_path / "chunks"
   194	    write_corpus(chunks_dir, CORPUS)
   195	    return SimpleNamespace(client=client, chunks_dir=chunks_dir, log=tmp_path / "log.json")
```
and lines 241–246:
```python
   241	def test_embedder_cli_reports_every_chunk(cli_env, monkeypatch, capsys):
   242	    from src.ingestion.pipeline import read_chunks
   243
   244	    monkeypatch.setattr(pipeline_module, "read_chunks", lambda: read_chunks(cli_env.chunks_dir))
   245	    assert run_main(monkeypatch, embedder_module.main) == 0
   246	    assert "OK: embedded 5 chunks" in capsys.readouterr().out
```
**How it works in the project.** Match each patch to the lookup style in the source:
- **Line 187:** `functools.partial(Embedder, verify=pinned_digest)` is a callable that builds a real `Embedder` with
  the stub model check pre-filled. It exists because `verify=verify_model` is a **default argument** (style 3), which no
  module patch can change.
- **Line 188:** `ingest.py` imports `build_qdrant_client` at the **top** (style 1), and `run_from_args` calls it by
  that copied name, so the patch goes on `ingest_module`.
- **Line 189:** `run_from_args` builds `Embedder(...)` using `ingest.py`'s top-level `Embedder` name (style 1), so it's
  patched on `ingest_module` too.
- **Line 190:** `store.main` calls `build_qdrant_client`, imported at the top of `store.py` (style 1), so it's patched
  on `store_module`.
- **Lines 191–192:** `store.main` and `run_from_args` do `from src.embedding.embedder import Embedder,
  build_ollama_client` **inside** the function (style 2), so the patch goes on `embedder_module`, the lookup site at call
  time. The same patch also covers `embedder.main`, which uses the module's own globals.
- **Line 244:** `embedder.main` does `from src.ingestion.pipeline import read_chunks` **inside** `main` (style 2), so
  the patch goes on `pipeline_module`. Line 242 imports the *real* `read_chunks` first, so the lambda can call it with
  the test folder.
- A wrong target would not raise an error. It would fail to connect to `localhost:6333`, or worse, use the real server.
  The mutation "CLI ranks start at 0" was caught by these tests (C12), which shows the CLI really ran against the toy.

### 4. Benefits
CLIs and other code that builds its own dependencies become testable offline, with automatic undo.

- **What breaks:** patching the definition site of a top-level import (no effect); trying to patch a default argument;
  global patches without monkeypatch (they leak into later tests).
- **Docs:** [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html) ·
  [`unittest.mock` — "Where to patch"](https://docs.python.org/3/library/unittest.mock.html#where-to-patch) ·
  [`functools.partial`](https://docs.python.org/3/library/functools.html#functools.partial)

### Micro-exercises
1. In the demo, add `import lib` at the top of `caller` and a function calling `lib.build_client()`. Which patch reaches it?
2. Remove line 190 from `cli_env` and run `test_store_cli_health_exit_codes` with Qdrant stopped. What happens?
3. Why does line 187 use `functools.partial` instead of `lambda client, **kw: Embedder(client, verify=..., **kw)`? Are they equivalent?
4. Find another default-argument dependency in `src/` (hint: `verify_model`'s own `fetch=fetch_tags`). How do the lock tests handle it?
5. Rewrite `store.main` (on paper) so it takes the client builder as a parameter, and remove the need for line 190.

**Internals.** `monkeypatch.setattr(obj, name, value)` stores `(obj, name, old_value)` on an undo stack and calls
`setattr`; at teardown it restores entries in reverse order. Python function globals are a dict (`function.__globals__`),
which is exactly the module's namespace, so replacing a module attribute changes what every function in that module
sees on its next lookup.
**Checkpoint:** state the "patch where it's looked up" rule and apply it to lines 188 and 191.

---

## C9 — Testing command-line programs in-process

### 1. Definition + analogy

**What it is.** A command-line program's interface is: **arguments in** (`sys.argv`), **text out** (stdout/stderr),
and an **exit code** (C9 of doc 15). Testing it **in-process** means calling its `main()` function directly inside the
test, instead of starting a new Python process:

1. **Set `sys.argv`** to the fake command line. `argparse` reads `sys.argv[1:]`, so `["prog", "search", "-k", "2"]`
   behaves like typing `store search -k 2`.
2. **Call `main()` and catch `SystemExit`**, because `raise SystemExit(code)`, `sys.exit()` and `argparse` errors all
   raise it. Its `.code` is the exit code: `None` or no exception means 0.
3. **Capture printed output** with pytest's `capsys` fixture (or `contextlib.redirect_stdout` outside pytest).

Compared with running a subprocess, in-process tests are **much faster** (measured for `store health`: 184 ms in-process
vs 2,597 ms as a subprocess, **14×**, mostly Python start-up and imports), **count toward coverage**, and allow
**monkeypatching** (C8), which is how the CLIs are pointed at in-memory Qdrant and the toy model. The cost: the process
boundary itself (real imports in a fresh interpreter, environment variables, the real exit) isn't exercised.

**argparse behaviours worth knowing:** a bad value (`-k three`) or unknown choice makes `argparse` print a usage error to
stderr and exit with code **2**, the Unix convention for "command-line usage error". Our CLIs use 0 (ok) and 1 (failed).

**Analogy.** Testing a car radio on the workbench, powered by a test battery with a microphone recording what it plays,
instead of installing it in a car and driving around.

### For a 14-year-old

A command-line program is like a vending machine: you **press buttons** (the command you type), it **shows a message**
on its little screen (the printed text), and then either gives you a snack (**success, code 0**) or flashes a red light
(**failure, code 1**, or **2** if you pressed buttons that don't exist).

To test it properly, you could walk to the machine every time and press buttons. That's slow (like starting a whole
new program). Or you can take the machine's **control board** onto your desk, wire fake buttons to it, and read its
screen directly. It's the same brain, just much faster to test, and you can swap its snack supply for a pretend one.

That's what our tests do. They:
1. pretend someone typed a command (by setting `sys.argv`),
2. run the program's brain (`main()`),
3. catch its "red light / green light" signal (`SystemExit`),
4. read what it printed on its screen (`capsys`).

### 2. Why (vs the traditional approach)
CLIs are often left untested ("it's just glue"), or tested only by hand. Here they were the largest block of uncovered
code (C1), and they contain real logic: argument wiring, exit codes, output format.

### 3a. Minimal code — the pattern, with a toy CLI
`c9_cli_in_process.py`
```python
# Testing a command-line program without starting a new process: sys.argv in, SystemExit + stdout out.
import argparse
import contextlib
import io
import sys


def main():
    parser = argparse.ArgumentParser(prog="store")
    parser.add_argument("command", choices=["health", "search"])
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()                 # reads sys.argv[1:]
    if args.command == "health":
        print("ready")
        raise SystemExit(0)
    print(f"searching top {args.k}")           # falls off the end -> exit code 0


def run_main(*argv):
    """The same pattern as tests/test_retrieval.py::run_main, plus stdout capture."""
    old_argv = sys.argv
    sys.argv = ["prog", *argv]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code
    finally:
        sys.argv = old_argv
    return code, out.getvalue().strip()


for argv in (["health"], ["search", "-k", "3"], ["search", "-k", "three"], ["delete"]):
    code, printed = run_main(*argv)
    print(f"argv={argv!s:<24} -> exit code {code} | printed {printed!r}")
```
Real output:
```
argv=['health']               -> exit code 0 | printed 'ready'
argv=['search', '-k', '3']    -> exit code 0 | printed 'searching top 3'
argv=['search', '-k', 'three'] -> exit code 2 | printed ''
argv=['delete']               -> exit code 2 | printed ''
```
**How it works.**
- `sys.argv = ["prog", *argv]`: element 0 is the program name, which argparse ignores; the rest are the arguments.
- **`health`:** prints and raises `SystemExit(0)`, caught, so code 0.
- **`search -k 3`:** no `SystemExit` at all. Falling off the end of `main` means success, so code 0 (the
  `code = 0` line).
- **`-k three` and `delete`:** argparse can't convert `three` to `int`, and `delete` isn't a valid choice, so it
  prints usage to **stderr** (captured and discarded here) and raises `SystemExit(2)`.
- The `finally` restores the real `sys.argv`, the manual version of what `monkeypatch` does automatically.

### 3b. The exact project code — `run_main` and the CLI tests
`tests/test_retrieval.py`, lines 198–204, 207–220 and 232–238:
```python
   198	def run_main(monkeypatch, main, *argv):
   199	    monkeypatch.setattr(sys, "argv", ["prog", *argv])
   200	    try:
   201	        main()
   202	    except SystemExit as exit_:
   203	        return exit_.code
   204	    return 0
   ...
   207	def test_ingest_cli_returns_0_and_writes_the_log(cli_env, capsys):
   208	    args = cli_args(["--chunks-dir", str(cli_env.chunks_dir), "--log", str(cli_env.log)])
   209	    assert run_from_args(args) == 0
   210	    assert "SUCCEEDED: 5 chunks from 3 documents" in capsys.readouterr().out
   211	    assert json.loads(cli_env.log.read_text(encoding="utf-8"))["verified"] is True
   212
   213
   214	def test_ingest_cli_returns_1_and_still_writes_the_log(cli_env, tmp_path, capsys):
   215	    empty = tmp_path / "empty"
   216	    empty.mkdir()
   217	    args = cli_args(["--chunks-dir", str(empty), "--log", str(cli_env.log)])
   218	    assert run_from_args(args) == 1
   219	    assert "failed in load" in capsys.readouterr().out
   220	    assert json.loads(cli_env.log.read_text(encoding="utf-8"))["status"] == "failed"
   ...
   232	def test_store_cli_health_exit_codes(cli_env, monkeypatch, capsys):
   233	    assert run_main(monkeypatch, store_module.main, "health") == 1
   234	    cli_env.client.create_collection(
   235	        COLLECTION, vectors_config=VectorParams(size=settings.embed_dim, distance=Distance.COSINE)
   236	    )
   237	    assert run_main(monkeypatch, store_module.main, "health") == 0
   238	    assert capsys.readouterr().out.splitlines() == ["NOT ready", "ready"]
```
**How it works in the project.**
- **Line 199:** `monkeypatch.setattr(sys, "argv", ...)` replaces the argument list, and it's restored automatically
  after the test.
- **Lines 200–204:** the same three outcomes as the demo: `SystemExit(code)` gives `code`, no exception gives 0.
- **Line 208:** `cli_args([...])` is the ingest CLI's own parser function taking an explicit list, so the parser can be
  tested without touching `sys.argv`, and `run_from_args` returns an int rather than exiting (a testability choice made
  in P3-M5).
- **Lines 209–211:** exit code 0, the human summary line, and the machine log (`verified: true`): three independent
  outputs of one run.
- **Lines 214–220:** an empty chunks folder gives exit code **1**, the summary says where it failed, and **the log is
  still written** (P3-M5's `finally`).
- **Lines 233–238:** `store health` against in-memory Qdrant: no collection yet gives exit 1; after creating a
  correctly sized collection, exit 0. `capsys.readouterr()` returns everything printed so far, in order.
- Line 226 (`test_store_cli_index_then_search`) checks the CLI's output format: a line starting with `"1. "` that
  contains `company/about.md_0`. That's why the "ranks start at 0" mutant was caught.

### 4. Benefits
The CLIs are tested fast, offline, with coverage, including their exit codes and their failure paths.

- **What breaks:** forgetting `SystemExit` (the test itself exits); not restoring `sys.argv` (later tests get odd
  arguments); asserting on exact whole-output strings (brittle).
- **Docs:** [pytest `capsys`](https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html) ·
  [`argparse` exit codes](https://docs.python.org/3/library/argparse.html#exiting-methods) ·
  [`SystemExit`](https://docs.python.org/3/library/exceptions.html#SystemExit)

### Micro-exercises
1. Add a test for `store search` with `-k 0`. What does the store raise, and what exit code results?
2. In the demo, capture stderr for the `-k three` case and print the usage message.
3. Why does `run_main` return 0 when `main()` returns normally, but `exit_.code` otherwise? What is `SystemExit().code`?
4. Write a subprocess version of `test_store_cli_health_exit_codes` marked `live`. Compare runtimes.
5. What would break in these tests if `run_from_args` called `sys.exit()` itself instead of returning a code?

**Internals.** `capsys` replaces `sys.stdout`/`sys.stderr` with in-memory buffers for the test's duration;
`readouterr()` returns and clears what was written. `SystemExit` is a `BaseException`, not an `Exception`, which is why
`except Exception` doesn't catch it and the explicit `except SystemExit` is needed.
**Checkpoint:** list the three outputs of a CLI and the line in `run_main` that captures each one.

---

## C10 — Contract tests: does the fake behave like the real thing?

### 1. Definition + analogy

**What it is.** Every hermetic test relies on an assumption: **the fake behaves like the real dependency** in the ways
that matter. When that assumption is wrong, tests pass against the fake and the code fails in production. A **contract
test** checks the assumption directly: it runs the **same expectations against both** the fake and the real
implementation.

- If both pass, the fake is faithful for that behaviour.
- If the real one passes and the fake fails (or vice versa), you've found a **divergence**, and you know exactly which
  hermetic tests can't be trusted for it.

Deep dive 14 found three divergences between the in-memory `QdrantClient` and the real server:
1. the server **rejects** point ID −1; in-memory **accepts** it;
2. the server **creates** payload indexes; in-memory **ignores** them;
3. only the real client/server pair negotiates HTTP **compression** (the gzip pin).

P3-M6's live tests pin down each of these on the real side, so if either side changes in a future version, a test
notices. D7-A chose targeted checks; D7-B (running *all* store tests against both clients) is the stronger version,
shown as the minimal demo below.

**Analogy.** A driving simulator is only useful if its brakes behave like a real car's. A contract test drives the same
route in the simulator and in the real car and compares stopping distances.

### For a 14-year-old

Imagine you practise football on a **video game** because the real pitch is often closed. The game is great for
practising, but only if the game ball behaves like a real ball. If in the game the ball never goes out of bounds, you'll
practise a move that doesn't work on a real pitch.

So once in a while you go to the **real pitch** and try the **same moves** you practised. If a move works in both, the
game is trustworthy for that move. If it works in the game but not on the real pitch, you've found a way the game is
**lying**, and now you know not to trust the game for that move.

Our "video game" is a pretend database that lives in memory (fast, no setup). Our tests run mostly against it. But we
found that the pretend one **accepts** an ID of −1 while the real database **refuses** it. Our contract tests run the
same check on both, so we know exactly where the pretend version can't be trusted.

### 2. Why (vs the traditional approach)
Most projects trust their fakes forever. The divergence only shows up as a production bug, and even then the tests still
pass, so nobody suspects the fake.

### 3a. Minimal code — one contract, two implementations
`demo_c10_contract.py` (run with `pytest ... -v -rs`)
```python
import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.vectorstore.setup import build_qdrant_client
from tests.support import live_services_down

DOWN = live_services_down()


@pytest.fixture(params=["in-memory", "server"])
def client(request):
    if request.param == "in-memory":
        yield QdrantClient(":memory:"), "c"
        return
    if DOWN:
        pytest.skip(DOWN)
    server = build_qdrant_client("http://localhost:6333")
    name = f"demo_contract_{uuid.uuid4().hex[:8]}"
    try:
        yield server, name
    finally:
        if server.collection_exists(name):
            server.delete_collection(name)


def make(client_and_name):
    client, name = client_and_name
    client.create_collection(name, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    return client, name


def test_contract_upsert_then_count(client):
    c, name = make(client)
    c.upsert(name, wait=True, points=[PointStruct(id=1, vector=[1.0, 0.0])])
    assert c.count(name).count == 1


def test_contract_negative_ids_are_rejected(client):
    c, name = make(client)
    with pytest.raises((UnexpectedResponse, ValueError)):
        c.upsert(name, wait=True, points=[PointStruct(id=-1, vector=[1.0, 0.0])])
```
Real output (services up):
```
demo_c10_contract.py::test_contract_upsert_then_count[in-memory] PASSED
demo_c10_contract.py::test_contract_upsert_then_count[server] PASSED
demo_c10_contract.py::test_contract_negative_ids_are_rejected[in-memory] FAILED
demo_c10_contract.py::test_contract_negative_ids_are_rejected[server] PASSED
E       Failed: DID NOT RAISE any of (UnexpectedResponse, ValueError)
```
**How it works.**
- **`@pytest.fixture(params=[...])`** makes every test using `client` run **twice**, once per implementation, with IDs
  `[in-memory]` and `[server]`.
- The server branch skips with the same reason helper as the project when services are down, and deletes its uniquely
  named collection in `finally` (C5).
- **Upsert-then-count:** both pass, so the fake is faithful here.
- **Negative IDs:** the server raises (`UnexpectedResponse`, HTTP 400), while the in-memory client **did not raise**. The
  contract test found the divergence mechanically, exactly as deep dive 14 found it by hand.

### 3b. The exact project code — the live contract checks
`tests/test_retrieval.py`, lines 388–424:
```python
   388	@live_test
   389	def test_live_collection_is_ready_with_payload_indexes(live_index):
   390	    assert live_index.store.health_check() is True
   391	    # The in-memory client ignores payload indexes, so only the server can prove
   392	    # the delete guard and category filters are indexed.
   393	    schema = live_index.client.get_collection(live_index.collection).payload_schema
   394	    assert {"doc_id", "category"} <= set(schema)
   395
   396
   397	@live_test
   398	def test_live_server_rejects_ids_the_in_memory_client_accepts(live_index):
   399	    point = PointStruct(id=-1, vector=[1.0, 0.0])
   400	    memory = QdrantClient(":memory:")
   401	    memory.create_collection("c", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
   402	    memory.upsert("c", points=[point])  # accepted locally
   403
   404	    name = f"test_live_ids_{uuid.uuid4().hex[:8]}"
   405	    client = live_index.client
   406	    client.create_collection(name, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
   407	    try:
   408	        with pytest.raises(UnexpectedResponse):
   409	            client.upsert(name, points=[point])
   410	    finally:
   411	        client.delete_collection(name)
   412
   413
   414	@live_test
   415	def test_live_client_negotiates_gzip_only(live_index):
   416	    seen = []
   417	    http = live_index.client._client.openapi_client.client._client
   418	    http.event_hooks["response"].append(
   419	        lambda r: seen.append(
   420	            (r.request.headers["accept-encoding"], r.headers.get("content-encoding"))
   421	        )
   422	    )
   423	    live_index.client.count(live_index.collection)
   424	    assert seen[-1] == ("gzip", "gzip")
```
**How it works in the project.**
- **Lines 393–394:** the payload schema of the live scratch collection must include both keyword indexes created by
  `ensure_collection` during ingestion preflight. The in-memory version of this check would get `{}` (deep dive 14 C4),
  so only this test proves the indexes exist. `<=` on sets means "is a subset of".
- **Lines 399–402:** the divergence recorded *both ways* in one test. The in-memory upsert of id −1 **succeeds** (if a
  future qdrant-client version starts rejecting it, this line raises and the test fails, flagging the change).
- **Lines 404–411:** the same point against the real server must raise `UnexpectedResponse` (HTTP 400). A separate,
  uniquely named 2-dimensional collection is used, and removed in `finally`, so the shared scratch collection is never
  modified (C5).
- **Lines 417–424:** reach into the client's underlying `httpx.Client` (private attributes, acceptable in a test that
  exists to check transport details), register a response hook, make one request, and assert both the request's
  `Accept-Encoding` and the server's `Content-Encoding` are gzip, protecting the P3-M4 workaround
  (D-2026-09-17-23).

### 4. Benefits
Known divergences are pinned down; unknown ones become findable by extending the contract; the hermetic suite's blind
spots are written down as code.

- **What breaks:** trusting fakes indefinitely; contract tests that modify shared live resources; checking only one side of
  a divergence (a fix on the fake side would go unnoticed).
- **Docs:** [Martin Fowler, "Contract Test"](https://martinfowler.com/bliki/ContractTest.html) ·
  [pytest parametrized fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html#parametrizing-fixtures) ·
  [Qdrant local mode](https://github.com/qdrant/qdrant-client#local-mode)

### Micro-exercises
1. Add a third contract test to the demo: creating a payload index then reading `payload_schema`. Predict both results.
2. Parametrise `tests/vectorstore/test_store.py`'s `client` fixture like the demo. How many tests fail on the server side?
3. Why does line 402 have no assertion, yet still protects something?
4. The gzip test uses private attributes (`_client`). What would you do if a qdrant-client upgrade renamed them?
5. List a divergence between the *toy model* and nomic-embed-text that a contract test could check.

**Internals.** A parametrised fixture multiplies the test items at collection time. Each param value becomes part of the
item's ID, and pytest orders items to reuse a param's fixture instance where scope allows.
**Checkpoint:** explain what a contract test compares, and name the three in-memory/server divergences this project checks.

---

## C11 — pytest's reserved names (the `setup_module` slip)

### 1. Definition + analogy

**What it is.** pytest supports an older **xunit-style** setup system alongside fixtures: if a test module defines
functions with certain **reserved names**, pytest calls them automatically:

| Name at module level | Called... |
|---|---|
| `setup_module(module)` / `teardown_module(module)` | once before / after all tests in the file |
| `setup_function(function)` / `teardown_function(function)` | around each test function |

pytest finds them **by name**, and it doesn't check that they're functions. So *any* object bound to that name is treated
as a hook, including an imported module. P3-M6's first run did exactly this:

```python
import src.vectorstore.setup as setup_module      # alias looked reasonable: "the setup module"
```

pytest tried to call `setup_module(...)`, inspected `setup_module.__code__` to count its arguments, and failed with
`AttributeError: module ... has no attribute '__code__'`. That meant **24 errors, one per test in the file**, none
related to the tests themselves. The fix was renaming the alias to `vector_setup`.

The general lesson: frameworks give special meaning to some names (`test_*` functions, `conftest.py`, `__init__`,
`setup_module`...). When an error makes no sense for the code you wrote, check whether a *name* collided with a
convention.

**Analogy.** Naming your dog "Sit". Every time you say his name, he sits.

### For a 14-year-old

At some schools, certain words over the loudspeaker are **codes**: "Code Blue" means everyone goes to the hall. Now imagine
a new student is nicknamed "Code Blue". The first time someone calls "Code Blue!" across the playground, the whole school
walks to the hall.

pytest has code words too. If a test file contains something called `setup_module`, pytest thinks "that's the thing I must
**run before all the tests**" and tries to run it. We accidentally gave the name `setup_module` to a *file we imported*
(not something runnable), so pytest tried to "run a file" and crashed, 24 times, once for every test. Nothing was wrong
with the tests; just one name. We renamed it `vector_setup`, and everything worked.

### 2. Why (vs the traditional approach)
Nothing to compare: this is a trap to know about. It's also a reminder to read an error's *mechanism* ("pytest called
setup_module"), not only its message.

### 3a. Minimal code — reproduce the slip
`demo_c11_reserved_names.py` (run with `pytest ... -q -p no:cacheprovider`)
```python
# pytest calls module-level functions with these names as setup/teardown hooks:
#   setup_module / teardown_module / setup_function / teardown_function
# Any OBJECT bound to such a name is treated as a hook -- including an imported module.
import json as setup_module          # the P3-M6 slip, in miniature (was: import src.vectorstore.setup as setup_module)


def test_anything():
    assert setup_module.dumps({}) == "{}"
```
Real output:
```
E               AttributeError: module 'json' has no attribute '__code__'
1 error in 0.27s
```
**How it works.** `test_anything` would pass on its own, but the result is an **error** (not a failure): pytest's module
setup phase found the name `setup_module`, tried to call it with or without an argument, and needed `__code__` to decide,
which a module doesn't have. Setup errors are reported per test, which is why the project file showed 24.

### 3b. The exact project code — the fix
`tests/test_retrieval.py`, line 34:
```python
    34	import src.vectorstore.setup as vector_setup
```
used at lines 250–254 (`vector_setup.verify_model`, `vector_setup.build_qdrant_client`, `vector_setup.main`).
**How it works in the project.** Any name not in pytest's reserved list works; `vector_setup` also says *which* setup it
is, which reads better next to `ingest_module` and `store_module`.

### 4. Benefits
One name away from 24 confusing errors; knowing the reserved names avoids the trap entirely.

- **What breaks:** naming anything `setup_module`, `teardown_module`, `setup_function`, `teardown_function` (and, in
  classes, `setup_method`, `setup_class`...) at the wrong level.
- **Docs:** [pytest: classic xunit-style setup](https://docs.pytest.org/en/stable/how-to/xunit_setup.html)

### Micro-exercises
1. In the demo, rename the alias to `teardown_module`. Which phase errors now?
2. Define a real `def setup_module(module): print("hi")` in the demo. When does "hi" print (use `-s`)?
3. Find the pytest source that calls `setup_module` (hint: `_pytest/python.py`, `xunit_setup_module_fixture`).
4. Why was it reported as 24 **errors** rather than 24 **failures**?
5. List three other names or files pytest treats specially.

**Internals.** pytest's `Module` collector looks up `setup_module`/`setUpModule` on the module object and, if found, wraps
it in an autouse module-scoped fixture that calls it, passing the module if the function accepts an argument.
**Checkpoint:** explain why `import json as setup_module` breaks a test that never uses setup code.

---

## C12 — Mutation-testing the tests themselves

### 1. Definition + analogy

**What it is.** Doc 13 C8 and doc 15 C10 used mutation testing to check that *existing* guards were tested. P3-M6 turned it
on the *new tests*: break the **source code** in ways the new tests claim to protect against, and run only
`tests/test_retrieval.py` (hermetic part). A killed mutant shows the new test adds protection; a survivor would mean the
test is decoration.

The mutants target **cross-component** behaviour (the point of this step), plus the CLIs.

**Analogy.** Testing a new burglar alarm by actually opening each window it claims to cover.

### For a 14-year-old

You've just installed new smoke alarms in every room. How do you know they work? You don't just look at them. You hold a
match under each one, one room at a time, and check it beeps. If one stays silent, it's decoration, not protection.
We did that with our new tests: broke the program in 7 different ways, one at a time, and checked that a test "beeped"
(failed) every time.

### 3. The exact project results (from the P3-M6 run)

| Mutant (source broken on purpose) | File | Hermetic retrieval tests |
|---|---|---|
| query prefix dropped in `embed_query` | `embedder.py` | 1 failed (killed) |
| delete-by-doc_id guard removed | `store.py` | 1 failed (killed) |
| orphans never deleted | `ingest.py` | 1 failed (killed) |
| search ignores `k` (`limit=1`) | `store.py` | 1 failed (killed) |
| CLI rank numbering starts at 0 | `store.py` | 1 failed (killed) |
| ingest CLI exits 0 on failure | `ingest.py` | 1 failed (killed) |
| health CLI exit code inverted | `store.py` | 1 failed (killed) |

**How to read it.** 7 of 7 killed, each by exactly one, *different* test (re-verified while writing this document):

| Mutant | Killed by |
|---|---|
| query prefix dropped | `test_both_sides_reach_the_model_with_their_own_prefix` |
| delete guard removed | `test_edited_document_is_found_by_its_new_text_after_reingest` |
| orphans never deleted | `test_deleted_document_disappears_from_search` |
| search ignores `k` | `test_search_returns_complete_chunks_with_descending_scores` (expects 5 results) |
| CLI ranks start at 0 | `test_store_cli_index_then_search` |
| ingest CLI exits 0 on failure | `test_ingest_cli_returns_1_and_still_writes_the_log` |
| health exit inverted | `test_store_cli_health_exit_codes` |

One test per mutant means no redundancy, but also no backup: if any of these tests were deleted, its mutant would survive.
**A process lesson from the same run:** restoring `store.py` after the last mutant failed once with a Windows file error,
leaving the mutant in place for a moment. It was caught by comparing with the backup (`cmp`), restored byte-exact, and
`git diff` confirmed no source change before committing. The re-run for this document retries each restore and checks a
SHA-256 of the file afterwards. **Always verify the restore, not just the mutation.**

- **Docs:** [mutmut](https://mutmut.readthedocs.io/) · Petrović & Ivanković, "State of Mutation Testing at Google" (ICSE-SEIP 2018)

### Micro-exercises
1. Mutate `BagOfWordsClient.IGNORED` to include `"acme"`. Which hermetic tests fail, and is that a test bug or a model bug?
2. Mutate `chunk_from_payload` to drop `metadata`. Does any retrieval test catch it?
3. Mutate `write_corpus` (test helper) to skip `report.json`. What happens, and why is mutating *test helpers* also informative?
4. Which of the 7 mutants would the *unit* tests in `tests/vectorstore` also catch? Which only the new file?
5. Write a byte-exact mutate-and-restore script that retries the restore and verifies it with a hash.

**Checkpoint:** explain what a killed mutant proves about a test, and why the restore must be verified.

---

## Validation

Run from the project root; each should match.

```bash
.venv/Scripts/python -m pytest tests/test_retrieval.py -q -p no:warnings -rsx
# services up:   24 passed, 2 xfailed          services down: 15 passed, 11 skipped (reasons shown)
.venv/Scripts/python -m pytest tests -q -p no:warnings -rs -m "not tiktoken and not live"
# 174 passed, 13 deselected   (what CI runs)
.venv/Scripts/python -m pytest tests/test_retrieval.py -m live --collect-only -q
# lists the 11 live test items
curl -s http://localhost:6333/collections
# only "chunks" -- no test_live_* left behind
```

Self-check questions (answer without looking):
1. Which two external services does the hermetic flow replace, and with what?
2. Why does the toy model use `hashlib.md5` and ignore `search`/`query`/`document`?
3. What's the difference between *skipped* and *deselected*, and which does CI produce?
4. What does `strict=True` add to an xfail?
5. Where must you patch `build_qdrant_client` to affect `ingest.run_from_args`, and why?

## Visualization

**What each part of `tests/test_retrieval.py` touches**
```
                         toy model   in-memory Qdrant   real Ollama   real Qdrant   CLIs
toy model test (98)          ✓
flow tests (117-176)         ✓              ✓
CLI tests (207-258)          ✓              ✓                                         ✓
live tests (336-431)                                          ✓             ✓
```

**Test outcomes you'll see, and what they mean**
```
.   passed        s   skipped (precondition unmet; -rs shows why)     x   xfailed (known bug, still present)
F   failed        (deselected: not shown as a letter; counted in the summary)   X   xpassed (fixed bug; strict -> F)
E   error in setup/teardown (e.g. the setup_module slip)
```

**Tools to see it yourself**
- `pytest -v -rsxX` for full item IDs plus every skip, xfail and xpass reason.
- `pytest --collect-only -q` to see which items exist before anything runs.
- `pytest --setup-show` to see fixture setup and teardown per test (try it on `demo_c5_fixture_scopes.py`).
- `pytest --durations=5` for the slowest tests and fixtures; the live session fixture's embedding shows up here.
- `pytest --cov=src.vectorstore --cov-report=html` and open `htmlcov/index.html` to see covered lines in colour.

## End-to-end "watch it work" exercise

Do these in order and write down what you observe at each step.

1. **Coverage vs behaviour.** Run `c1_coverage_is_not_testing.py`. Then run the CI command and find `store.py`'s coverage.
2. **The toy model.** Run `c3_bag_of_words.py`. Pick a new question and predict its top chunk before checking with a
   one-line script.
3. **Fixture lifecycle.** Run `pytest "practice codes/phase 3/m6_retrieval_tests_deep_dive/demo_c5_fixture_scopes.py" -q -s --setup-show -p no:cacheprovider`
   and match each SETUP/TEARDOWN line to the event list.
4. **Skip vs deselect.** Run `pytest tests/test_retrieval.py -q -rs -m "not live"`, then `-q -rs` with Ollama stopped
   (`docker compose stop ollama`). Compare the summaries. Start Ollama again.
5. **Known misses.** Run `pytest tests/test_retrieval.py -m live -rx -vv` and read the actual top 3 in each xfail message.
6. **Patching.** Run `c8_monkeypatch_lookup.py`, then answer micro-exercise C8-2 by actually removing line 190 (restore it
   after).
7. **Contract.** Run `demo_c10_contract.py -v` and explain the one FAILED line.
8. **Trace open Question 1.** With what you now know about import-time `skipif` (C6) and session fixtures (C5), write the
   timeline for `-m live` with Qdrant stopped vs running. Then run both and check with `--durations=5 --setup-show`.
9. **Log it.** Add a `LEARNING_LOG.md` line: which concept would have saved you the most time on a past project.

## Canonical sources
- pytest: [fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html) · [marks](https://docs.pytest.org/en/stable/how-to/mark.html) · [skip/xfail](https://docs.pytest.org/en/stable/how-to/skipping.html) · [parametrize](https://docs.pytest.org/en/stable/how-to/parametrize.html) · [monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html) · [capsys](https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html) · [xunit setup](https://docs.pytest.org/en/stable/how-to/xunit_setup.html)
- Testing concepts: [Fowler, "The Practical Test Pyramid"](https://martinfowler.com/articles/practical-test-pyramid.html) · [Fowler, "Mocks Aren't Stubs"](https://martinfowler.com/articles/mocksArentStubs.html) · [Fowler, "Contract Test"](https://martinfowler.com/bliki/ContractTest.html) · [xUnit Test Patterns: Test Double](http://xunitpatterns.com/Test%20Double.html)
- Python: [`unittest.mock` / where to patch](https://docs.python.org/3/library/unittest.mock.html#where-to-patch) · [`PYTHONHASHSEED`](https://docs.python.org/3/using/cmdline.html#envvar-PYTHONHASHSEED) · [`argparse`](https://docs.python.org/3/library/argparse.html) · [coverage.py](https://coverage.readthedocs.io/)
- Retrieval: [Weinberger et al., feature hashing (arXiv:0902.2206)](https://arxiv.org/abs/0902.2206) · [Manning, Raghavan & Schütze, *Introduction to Information Retrieval*](https://nlp.stanford.edu/IR-book/) · [Ragas](https://docs.ragas.io/)
- Qdrant: [local mode](https://github.com/qdrant/qdrant-client#local-mode)
- Mutation testing: [mutmut](https://mutmut.readthedocs.io/) · Petrović & Ivanković, "State of Mutation Testing at Google" (ICSE-SEIP 2018)
