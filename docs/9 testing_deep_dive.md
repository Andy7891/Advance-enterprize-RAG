# Testing Deep Dive: Reverse-Engineering Phase 2, Microstep 5

A self-contained learning path for the testing work in P2-M5:
[`tests/conftest.py`](../tests/conftest.py),
[`tests/ingestion/test_coverage_gaps.py`](../tests/ingestion/test_coverage_gaps.py) and
[`tests/ingestion/test_cli.py`](../tests/ingestion/test_cli.py) — validated by deliberately
breaking the source code and watching which tests noticed.

**How to use this doc:** run every exercise before reading its checkpoint. Five exercises per
concept, easiest → hardest, with hints for anyone who has never written a test before.

**The measured facts this doc is built on** (all verified, none estimated):

| | before M5 | after M5 |
|---|---|---|
| tests | 66 | 82 |
| line coverage | 93% | 99% |
| branch coverage | — | 99% (52 branches, 1 partial) |

And the finding that made the step worth doing, proven by experiment:

```
With _merge_undersized's body DELETED:
  OLD tests:  2 passed   <- they never tested it
  NEW tests:  3 failed   <- they actually do
```

---

## 0. What this step did, in plain English

Imagine you hire a building inspector to check a house. They walk around, tick every box,
and hand you a clean report. Reassuring — until you notice their muddy footprints only go
through the kitchen. They never entered the other rooms. The report isn't *lying* about the
kitchen; it just quietly says nothing about anywhere else.

**Coverage is the muddy footprints.** It doesn't tell you the tests are good. It tells you
which rooms the inspector actually walked through.

We had 66 tests and a clean report. Following the footprints showed a room nobody had
entered: `_merge_undersized`'s merge logic. There was even a test *named* after it — and it
passed while the code never ran.

**The concept map** (top to bottom):

```
Measuring what actually ran:      code coverage (line vs branch)
Tests that pass without testing:  vacuous tests + mutation testing
Reusable setup for tests:         pytest fixtures + conftest.py
Cheap stand-ins for real things:  test doubles (fakes)
Temporarily swapping something:   monkeypatch
Asserting on log messages:        caplog
Asserting something FAILS:        pytest.raises
A clean folder per test:          tmp_path
Running only some tests:          markers and -m deselection
Where test data comes from:       fixture corpus vs the real corpus
```

---

## 1. Concept-by-concept

---

### 1.1 Code coverage — following the footprints

**1. Definition (plain English):** coverage is a recording of which lines of your program
actually executed while the tests ran. Run tests with `--cov` and you get a list of lines
nobody touched. Analogy: dusting a room for footprints after someone walks through it — you
learn exactly where they went, and (more usefully) where they didn't.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is "do the tests pass? then we're fine." But passing only tells you *the assertions
you wrote* held. It says nothing about the code you forgot to write assertions for — or
worse, code you *think* you tested. Coverage turns "I believe this is tested" into "here are
the exact line numbers that ran." In this project it immediately produced a work list of 25
unexecuted lines, replacing guesswork with evidence.

**3. A clean, minimal code example:**
```bash
pytest tests/ --cov=src --cov-report=term-missing
```
```
Name                       Stmts   Miss  Cover   Missing
------------------------------------------------------------
src\ingestion\chunker.py      87     10    89%   174-180, 204, 208, 218-223, 257-259
src\ingestion\schemas.py      64      1    98%   142
```
That `Missing` column is the whole point: those are real line numbers you can open and read.

**4. Main benefits, in short:** (a) converts "what should I test next?" from opinion into a
concrete list; (b) reveals code that no test exercises even when a test *appears* to cover
it; (c) shows when a line is untestable-by-design (like `if __name__ == "__main__"`), which
is useful information too.

**What breaks if you don't measure it:** exactly what happened here — you keep a green suite
and a false sense of safety, while a whole branch of logic sits unexercised behind a test
named after it.

**An honest limit, measured here:** coverage tells you what RAN, not whether the assertions
were meaningful. A test with zero `assert` statements gives 100% coverage of everything it
touches. Also, **line coverage and branch coverage barely differ in this project** — both
99% (52 branches, 1 partial). Branch coverage is stricter in general (it asks whether each
`if` went both ways), but here it revealed nothing extra. Don't assume it always will.

**Canonical reading:** [pytest-cov docs](https://pytest-cov.readthedocs.io/); [coverage.py's own explanation of branch coverage](https://coverage.readthedocs.io/en/latest/branch.html); and [Martin Fowler — Test Coverage](https://martinfowler.com/bliki/TestCoverage.html), whose point is that coverage is excellent at finding untested code and terrible as a quality target.

**5 micro-exercises:**

1. **(5 min) See the report for yourself.** Run
   `pytest tests/ --cov=src --cov-report=term-missing`. Find the `Missing` column. Which two
   files still have uncovered lines, and what are the line numbers?
   *Hint:* the answer should be `chunker.py 257-259` and `pipeline.py 256`.

2. **(5 min) Open one uncovered line and judge it.** Look at `chunker.py` line 257 and
   `pipeline.py` line 256. For each, decide: is this *untested* (needs a test) or
   *untestable* (can't run offline)? Write one sentence justifying each.

3. **(10 min) Prove coverage says nothing about assertion quality.** Write a test with no
   assertions at all:
   ```python
   def test_useless():
       from src.ingestion.schemas import count_tokens_approx
       count_tokens_approx("hello world")   # no assert -- tests nothing
   ```
   Run with `--cov`. Did coverage go UP? What does that tell you?

4. **(10 min) Get the visual version.** Run
   `pytest tests/ --cov=src --cov-report=html` then open `htmlcov/index.html` in a browser
   and click into `chunker.py`. Uncovered lines are highlighted red — far easier to scan
   than a terminal table.
   *Hint:* `htmlcov/` is generated output; don't commit it.

5. **(15 min) Compare line vs branch coverage yourself.** Run
   `pytest tests/ --cov=src --cov-report=term` then again with `--cov-branch`. Compare the
   TOTAL rows. Explain what the extra `Branch` and `BrPart` columns mean, and why the
   percentage barely moved in this project.

**How it works internally:** `coverage.py` installs a *trace function* via Python's
`sys.settrace` hook, which the interpreter calls on every line executed. It records each
`(filename, line number)` pair into a database, then compares that set against every
executable line found by parsing the source. Lines in the source but not in the recording
are "missing." That tracing is why running with coverage is measurably slower than without.

**Checkpoint:** explain why 100% coverage would NOT prove the code is correct, using the
no-assert test from exercise 3 as your example.

---

### 1.2 Vacuous tests and mutation testing — the star of this microstep

**1. Definition (plain English):** a *vacuous* test is one that passes without actually
exercising the thing it claims to test. *Mutation testing* is how you catch them: deliberately
break the source code (introduce a "mutant"), then check whether any test notices. A test
that stays green while the code is broken is not protecting you. Analogy: testing a smoke
alarm by pressing its "test" button proves the button works. Holding a lit match under it
proves the alarm works. Only one of those tells you anything useful.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
check is "the test passes, so the code works." But a test can pass for reasons completely
unrelated to the code under test. That's exactly what happened here:
`test_undersized_chunks_are_merged_backwards` set `min_chunk_chars=10_000`, asserted
`len(chunks) == 1`, and went green — but the splitter already returned **one chunk before
merging ever ran**, so `if merged and ...` was never true. The assertion was true. The code
never executed.

**3. A clean, minimal code example — the actual experiment, with real results:**
```python
# Step 1: gut the function under test
def _merge_undersized(self, spans):
    return spans                      # <- merge logic deleted entirely
    merged = []                       # (unreachable)
    ...
```
```
$ pytest -k "merged_backwards or merging_preserves"     # the OLD tests
2 passed          <- they did not notice the code was gone

$ pytest tests/ingestion/test_coverage_gaps.py -k merge  # the NEW tests
3 failed          <- they did
```
That contrast is the entire justification for this microstep.

**4. Main benefits, in short:** (a) it's the only way to tell a protective test from a
decorative one; (b) it's fast and needs no tooling — comment out a line and rerun; (c) it
teaches you to write tests that *depend* on the behaviour, not merely coexist with it.

**What breaks if you never do this:** you accumulate tests that look like safety and aren't.
The suite goes green through a refactor that broke real behaviour, which is strictly worse
than having no test at all — because no test at least prompts caution.

**Canonical reading:** [Wikipedia — Mutation testing](https://en.wikipedia.org/wiki/Mutation_testing) for the formal technique; [`mutmut`](https://mutmut.readthedocs.io/) is the standard Python tool that automates it (we did it by hand here, which is enough to learn the idea).

**5 micro-exercises:**

1. **(10 min) Reproduce the whole experiment.** Back up `chunker.py`, add
   `return spans` as the first line of `_merge_undersized`, run
   `pytest tests/ingestion/test_chunker.py -k "merged_backwards"` — watch it PASS. **Restore
   the file afterwards.**
   *Hint:* `cp src/ingestion/chunker.py /tmp/backup.py` first, `cp` back when done.

2. **(10 min) Now watch the new tests catch it.** With the same gutted version, run
   `pytest tests/ingestion/test_coverage_gaps.py -k merge` — 3 failures. Read one failure
   message and identify exactly which assertion detected the missing behaviour.

3. **(10 min) Mutate something else.** In `schemas.py`, change `compute_chunk_id`'s
   `f"{normalized}_{chunk_index}"` to `f"{normalized}-{chunk_index}"` (underscore →ureau
   hyphen). Run the full suite. How many tests fail? Is that number reassuring or worrying?

4. **(10 min) Find a mutant nothing catches.** Change `count_tokens_approx`'s
   `len(text) // 4` to `len(text) // 5` and run the suite. Does anything fail? If not, what
   does that tell you about how well that function is actually protected?
   *Hint:* look at `test_count_tokens_approx_scales_with_length` — what does it actually
   assert?

5. **(15 min) Write a deliberately vacuous test, then fix it.** Write a test that asserts
   something true but unrelated to the code (e.g. `assert len(chunks) >= 0` — always true).
   Confirm it passes even with the function gutted. Then rewrite it so it genuinely fails
   when the behaviour is removed.

**How it works internally:** there's no machinery — mutation testing is a *method*, not a
feature. You change source, run tests, observe. Automated tools like `mutmut` just do this
thousands of times programmatically, generating small mutations (flip `<` to `<=`, change a
constant, delete a line) and reporting which "survived" — i.e. which changes no test caught.
Surviving mutants are your untested behaviours.

**Checkpoint:** explain, in your own words, why `len(chunks) == 1` was a *true* assertion
that nevertheless tested nothing — and what makes the replacement test different.

---

### 1.3 pytest fixtures and `conftest.py`

**1. Definition (plain English):** a fixture is reusable setup. You write a function,
decorate it `@pytest.fixture`, and any test that wants it just names it as an argument —
pytest sees the name and passes it in automatically. Put fixtures in `conftest.py` and every
test file in that folder (and below) can use them without importing anything. Analogy: a
shared prep station in a kitchen. Every chef who needs chopped onions just says "onions" and
gets a bowl; nobody chops their own.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is copy-pasting setup into every test — build the corpus path, build a fake
embedder, build a fake counter, in all 82 tests. When something changes, you edit 82 places.
Fixtures put it in one place, and `conftest.py` makes it available with zero imports. It's
dependency injection, but for tests: the test declares *what* it needs, pytest decides *how*
to supply it.

**3. A clean, minimal code example:**
```python
# tests/conftest.py
import pytest

@pytest.fixture
def fake_counter():
    class FakeCounter:
        def count(self, text): return len(text.split())
    return FakeCounter()

# tests/ingestion/test_anything.py  -- no import needed!
def test_uses_the_fixture(fake_counter):        # <- just name it
    assert fake_counter.count("a b c") == 3
```

**4. Main benefits, in short:** (a) setup lives in one place; (b) no imports needed in test
files — pytest resolves fixture names by discovery; (c) fixtures can request *other*
fixtures, so complex setup composes from small pieces.

**What breaks if you remove `conftest.py`:** every test file would need to import
`FakeEmbedder`, `FakeCounter` and the corpus paths itself. Worse, tests in *this* project
used to import fakes from each other (`from tests.ingestion.test_chunker import
FakeEmbedder`) — a test file importing another test file, which couples them so that
renaming one test file breaks unrelated tests.

**Canonical reading:** [pytest docs — fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html) and [conftest.py: sharing fixtures](https://docs.pytest.org/en/stable/reference/fixtures.html#conftest-py-sharing-fixtures-across-multiple-files).

**5 micro-exercises:**

1. **(5 min) Use an existing fixture.** Write a new test anywhere in `tests/ingestion/` that
   takes `fake_counter` as an argument and asserts `fake_counter.count("one two") == 2`.
   Note you wrote no import line at all.

2. **(5 min) Break the name and read the error.** Rename your test's argument to
   `fake_countr` (typo) and run it. Read pytest's error — it lists every fixture it *does*
   know about, which is a useful discovery tool.

3. **(10 min) List every available fixture.** Run `pytest --fixtures -q | head -40`. Find
   `fixture_corpus`, `fake_embedder` and the built-ins `tmp_path`, `monkeypatch`, `caplog`.

4. **(10 min) Make a fixture that uses another fixture.** Add to a scratch test file:
   ```python
   @pytest.fixture
   def loaded_doc(fixture_corpus):          # requests ANOTHER fixture
       from src.ingestion.loader import load_document
       return load_document(fixture_corpus / "company" / "about.md", fixture_corpus)

   def test_it(loaded_doc):
       assert loaded_doc.category == "company"
   ```

5. **(15 min) Observe fixture scope.** Add `print("SETUP")` inside the `fake_counter`
   fixture and run two tests that both use it with `pytest -s`. How many times does SETUP
   print? Now add `@pytest.fixture(scope="module")` and rerun — what changed, and why might
   that matter for an expensive fixture?

**How it works internally:** at collection time pytest inspects each test function's
*parameter names* (via the `inspect` module) and looks for a registered fixture with each
name, searching the test's own module first, then `conftest.py` files upward through the
directory tree. It builds a dependency graph, calls the fixtures in order, and passes the
results in as arguments. That name-based lookup is why a typo produces "fixture not found"
rather than a normal Python `NameError`.

**Checkpoint:** explain how a test file can use `fake_counter` without importing it, and why
that's different from a normal Python import.

---

### 1.4 Test doubles (fakes) — cheap stand-ins for expensive things

**1. Definition (plain English):** a test double is a simple replacement for a real
dependency, used only in tests. `FakeEmbedder` returns a vector based on which keyword the
text contains; `FakeCounter` counts words. Neither talks to Ollama or loads tiktoken.
Analogy: a crash-test dummy. It isn't a person, and that's the point — it's predictable,
always available, and you can run the same crash a thousand times.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is testing against the real thing. For this project that means a running Ollama
container and a model pulled — a ~9-minute full run, per the measured M4 pipeline pass, and
non-deterministic output. With fakes, the entire 82-test suite runs in **under 2 seconds**,
offline, with identical results every time. That difference is what makes running tests on
every save practical.

**3. A clean, minimal code example** — the real `FakeEmbedder`, simplified:
```python
class FakeEmbedder:
    TOPICS = ("acme", "beta", "gamma")

    def embed_documents(self, texts): return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        lowered = text.lower()
        vector = [1.0 if topic in lowered else 0.0 for topic in self.TOPICS]
        return vector if any(vector) else [0.33] * len(self.TOPICS)
```
It's deliberately *realistic in the one way that matters*: same-topic sentences get similar
vectors, different-topic sentences get distant ones — so cosine distance behaves the way the
chunker expects, with no model involved.

**4. Main benefits, in short:** (a) tests run in seconds, offline, deterministically;
(b) you can construct exact scenarios (a "broken" embedder, a counter returning 42) that a
real model would never reliably produce; (c) CI needs no Docker, no GPU, no API key.

**What breaks if you use the real Ollama in tests:** the suite becomes a multi-minute job
requiring Docker running and a model pulled, fails on any machine without them, and produces
subtly different results run to run — so a failure might be a real bug or might be the model
having a slightly different day.

**The trade-off, honestly:** fakes test your code against *your assumptions* about the
dependency. If Ollama's real behaviour differs from `FakeEmbedder`'s, tests pass and
production breaks. That's why this project also does real end-to-end runs (the M4 pipeline
pass) — fakes for speed, real runs for truth.

**Canonical reading:** [Martin Fowler — Test Double](https://martinfowler.com/bliki/TestDouble.html), which distinguishes fakes, stubs, mocks and spies (they are genuinely different things, and the words are widely misused).

**5 micro-exercises:**

1. **(5 min) Watch the fake behave sensibly.**
   ```python
   from tests.conftest import FakeEmbedder
   e = FakeEmbedder()
   print(e.embed_query("acme grew"))     # predict before running
   print(e.embed_query("beta shipped"))
   print(e.embed_query("nothing here"))
   ```

2. **(5 min) Confirm the speed claim.** Time the suite: `pytest tests/ -q` — note the
   seconds. Then recall the measured 9m12s for one real pipeline run over 76 documents.

3. **(10 min) Build a hostile fake.** Write a `BrokenCounter` whose `.count()` raises
   `RuntimeError`, pass it to `enrich_with_token_counts`, and confirm the exception
   propagates rather than being silently swallowed.

4. **(10 min) Build a degenerate fake.** Write an embedder returning the SAME vector for
   every input, run the chunker with it, and see how many chunks you get. Explain the result
   in terms of cosine distance.
   *Hint:* if every sentence is identical in vector space, what is the distance between
   neighbours, and will it ever exceed mean + 1.5σ?

5. **(15 min) Find the fake's blind spot.** `FakeEmbedder` returns 3-dimensional vectors;
   `nomic-embed-text` returns 768-dimensional ones. Write down one bug that could exist in
   `chunker.py` that the fake would never catch. (Think about anything that depends on
   vector *dimensionality* rather than direction.)

**How it works internally:** nothing special — a fake is just a class with the right method
names. It works because `Chunker` and `enrich_with_token_counts` accept a `Protocol`
(`Embedder`, `TokenCounter`), so Python never checks the concrete type at all; it just calls
`.embed_documents(...)` or `.count(...)` and trusts they exist. See
[docs/6 chunker_deep_dive.md](6%20chunker_deep_dive.md) section 1.1 for how `Protocol` makes
that safe rather than reckless.

**Checkpoint:** explain the difference between "my tests pass" and "my code works in
production" specifically in terms of what a fake does and does not verify.

---

### 1.5 `monkeypatch` — temporarily replacing something, safely

**1. Definition (plain English):** `monkeypatch` is a built-in pytest fixture that swaps out
a function, attribute or environment variable *for the duration of one test*, then puts
everything back automatically. Analogy: a film stunt double. They step in for one dangerous
shot, the real actor returns immediately after, and the audience never sees the swap.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is assigning over the thing manually (`pipeline.build_ollama_embedder = fake`) — and
then remembering to undo it. If the test fails partway through, you never undo it, and every
*subsequent* test in the session silently uses your fake. `monkeypatch` guarantees the undo
happens even when a test fails, because pytest tears the fixture down regardless. In
`test_cli.py` this is what lets `main()` run for real — argparse, `run_pipeline`, file
writing — while the two dependency *builders* are swapped out.

**3. A clean, minimal code example** — the real pattern from `test_cli.py`:
```python
def test_cli(monkeypatch, tmp_path):
    # Replace the BUILDERS, not the pipeline -- so the real argparse ->
    # run_pipeline path still executes, just without a network.
    monkeypatch.setattr(pipeline, "build_ollama_embedder", lambda *a, **k: FakeEmbedder())
    monkeypatch.setattr(pipeline, "build_tiktoken_counter", lambda *a, **k: FakeCounter())
    monkeypatch.setattr(sys, "argv", ["pipeline", "--threshold", "0.25"])

    pipeline.main()          # runs for real, offline
# everything above is automatically restored here
```

**4. Main benefits, in short:** (a) undo is automatic, even on failure; (b) it can patch
attributes, dict entries, and environment variables (`monkeypatch.setenv`); (c) patching the
*builder* rather than the *client* keeps the wiring under test genuinely real.

**What breaks if you patch manually instead:** a failing test leaves the fake installed, and
later tests mysteriously pass or fail depending on *run order* — one of the hardest classes
of test bug to diagnose.

**Canonical reading:** [pytest docs — monkeypatching](https://docs.pytest.org/en/stable/how-to/monkeypatch.html).

**5 micro-exercises:**

1. **(5 min) Patch something trivial.**
   ```python
   def test_patch(monkeypatch):
       import os
       monkeypatch.setenv("MY_TEST_VAR", "hello")
       assert os.environ["MY_TEST_VAR"] == "hello"

   def test_it_was_undone():
       import os
       assert "MY_TEST_VAR" not in os.environ      # proves automatic cleanup
   ```

2. **(10 min) Prove the automatic undo on FAILURE.** Add `assert False` to the end of the
   first test above so it fails, then run both. Does the second test still pass? What does
   that prove?

3. **(10 min) Patch a function and watch it take effect.**
   ```python
   def test_patched(monkeypatch):
       from src.ingestion import enrichment
       monkeypatch.setattr(enrichment, "DEFAULT_ENCODING", "fake_encoding")
       assert enrichment.DEFAULT_ENCODING == "fake_encoding"
   ```

4. **(10 min) Understand WHY we patch the builder, not the client.** In `test_cli.py`,
   `build_ollama_embedder` is patched rather than `OllamaEmbeddings`. Write one sentence on
   what would still be exercised (argparse, `run_pipeline`, file writing) versus what would
   be skipped if we'd patched `run_pipeline` itself instead.

5. **(15 min) Hit the classic monkeypatch trap.** `monkeypatch.setattr(pipeline,
   "build_ollama_embedder", ...)` patches the name *in the pipeline module*. Try instead
   patching it in `chunker` (where it's defined) and see whether `main()` still uses the
   fake. Explain the result in terms of `from x import y` binding a name at import time.

**How it works internally:** `monkeypatch` records the original value of everything it
changes on an internal undo stack, then replays those restorations in reverse during fixture
teardown. Because it's a fixture, pytest runs that teardown whether the test passed, failed,
or errored — which is precisely the guarantee manual assignment cannot give you.

**Checkpoint:** explain why patching `pipeline.build_ollama_embedder` works but patching
`chunker.build_ollama_embedder` might not, referring to how `from ... import ...` binds
names.

---

### 1.6 `caplog` — asserting that a warning actually fired

**1. Definition (plain English):** `caplog` is a built-in fixture that captures everything
your code logs during a test, so you can assert on it. Analogy: recording a phone call so
you can later prove someone said a specific sentence.

**2. The "Why" — what problem this solves vs. the traditional approach:** some behaviour has
no return value to assert on. `_recover_spans`'s fallback path doesn't return a special
value when it can't locate a chunk — it logs a warning and carries on with an estimate. The
only way to prove that safety net fired is to capture the log. Without `caplog` the branch
would be untestable, and it was in fact completely uncovered before M5.

**3. A clean, minimal code example:**
```python
def test_warns_when_chunk_cannot_be_located(fake_embedder, monkeypatch, caplog):
    ...
    chunks = chunker.chunk(document, Path("data"))

    assert "Could not locate chunk verbatim" in caplog.text
    assert chunks[0].metadata["start_char"] == 7      # used the fallback estimate
```

**4. Main benefits, in short:** (a) log-only behaviour becomes testable; (b) you verify the
message is actually useful (does it name the file?); (c) you can assert on log *level* via
`caplog.records`, not just text.

**What breaks without it:** the fallback path can only be verified by eyeballing a terminal,
which nobody does repeatedly — so the safety net silently rots.

**Canonical reading:** [pytest docs — logging](https://docs.pytest.org/en/stable/how-to/logging.html).

**5 micro-exercises:**

1. **(5 min) Capture a log.**
   ```python
   import logging
   def test_caplog(caplog):
       logging.getLogger("demo").warning("something odd")
       assert "something odd" in caplog.text
   ```

2. **(10 min) Assert on the LEVEL, not just the text.** Use `caplog.records` and check
   `record.levelname == "WARNING"`. Why might text-only assertions be too loose?

3. **(10 min) Run the real fallback test with `-s`.**
   `pytest tests/ingestion/test_coverage_gaps.py -k unlocatable -s` and watch the warning
   appear in the terminal as well as being captured.

4. **(10 min) Break the assertion deliberately.** Change the expected string to
   `"Could not locate chunkk"` (typo) and confirm the test fails — proving it's genuinely
   checking the message, not just that *some* log happened.

5. **(15 min) Test a log that does NOT fire.** Write a test asserting that a *successful*
   chunk run produces NO "Could not locate" warning (`assert "Could not locate" not in
   caplog.text`). Which is more valuable here — asserting a warning fires, or asserting it
   doesn't?

**How it works internally:** `caplog` installs a custom `logging.Handler` on the root logger
for the duration of the test, collecting every `LogRecord` into a list, and removes it
afterwards. `caplog.text` is those records rendered to strings; `caplog.records` is the
structured objects, which is why level and logger-name assertions are possible.

**Checkpoint:** explain why `_recover_spans`'s fallback could not be tested by checking the
function's return value, and what `caplog` gives you instead.

---

### 1.7 `pytest.raises` — asserting that something fails

**1. Definition (plain English):** `pytest.raises` asserts an exception *is* thrown. The code
inside the `with` block is expected to blow up; if it doesn't, the test fails. Analogy:
testing a door's lock. You don't check that you can walk through — you check that you
*can't*, and that the door specifically refuses rather than falling off its hinges.

**2. The "Why" — what problem this solves vs. the traditional approach:** error paths are
behaviour too, and they're the easiest to leave untested because "nothing happens" feels like
nothing to assert. `infer_category`'s "no category subfolder" `ValueError` — a deliberate
guard we reasoned about extensively in earlier steps — was **completely uncovered** before
M5. Nobody had ever verified it fires. `match=` additionally checks the message, so you also
verify the error is *useful*, not just present.

**3. A clean, minimal code example:**
```python
def test_infer_category_rejects_a_file_with_no_category_folder(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    loose_file = data_root / "orphan.md"
    loose_file.write_text("no category folder above me")

    with pytest.raises(ValueError, match="no category subfolder"):
        infer_category(loose_file, data_root)
```

**4. Main benefits, in short:** (a) guards get proven rather than assumed; (b) `match=`
verifies the message would actually help someone debugging; (c) it asserts the *specific*
exception type, so a different crash fails the test rather than accidentally satisfying it.

**What breaks if you assert loosely:** `pytest.raises(Exception)` passes for *any* failure
— including a `TypeError` from a bug in your test setup — so you can "prove" a guard works
while it's actually broken.

**Canonical reading:** [pytest docs — assertions about expected exceptions](https://docs.pytest.org/en/stable/how-to/assert.html#assertions-about-expected-exceptions).

**5 micro-exercises:**

1. **(5 min) Basic usage.**
   ```python
   import pytest
   def test_divide_by_zero():
       with pytest.raises(ZeroDivisionError):
           1 / 0
   ```

2. **(5 min) Watch it fail when nothing raises.** Change `1 / 0` to `1 / 1` and read the
   failure message — pytest says `DID NOT RAISE`.

3. **(10 min) Make the type too broad.** Change the real test to
   `pytest.raises(Exception)` and confirm it still passes. Then deliberately break the test
   setup (pass a nonexistent path) and see it *still* pass — demonstrating why the broad
   version is dangerous.

4. **(10 min) Use `match=` to check the message.** Change `match="no category subfolder"` to
   `match="this text is not in the message"` and confirm the test now fails even though the
   right exception type was raised.

5. **(15 min) Inspect the exception object.** Use the `as` form and assert on details:
   ```python
   with pytest.raises(ValueError) as exc_info:
       infer_category(loose_file, data_root)
   assert "orphan.md" in str(exc_info.value)
   ```
   Why does naming the offending FILE in the message matter for a 76-document corpus?

**How it works internally:** `pytest.raises` is a context manager. On `__exit__`, Python
hands it whatever exception occurred (or `None`). It checks whether one was raised and
whether it's an instance of the expected type — if not, it raises a `Failed` error itself.
`match=` then runs `re.search` against `str(exception)`, so it's a regex, not a substring
test (which matters if your expected text contains regex characters like `(` or `.`).

**Checkpoint:** explain why `pytest.raises(Exception)` is almost always the wrong choice, and
what `match=` adds beyond checking the type.

---

### 1.8 `tmp_path` — a clean folder for every test

**1. Definition (plain English):** `tmp_path` is a built-in fixture giving each test its own
brand-new empty directory, automatically deleted later. Analogy: a fresh sheet of paper for
every exam candidate, rather than everyone writing on the same whiteboard.

**2. The "Why" — what problem this solves vs. the traditional approach:** tests that write
files need somewhere to write. Using a fixed folder means tests interfere with each other
(one test's leftover file breaks the next), test order suddenly matters, and running tests
twice behaves differently the second time. `tmp_path` gives complete isolation — which is
exactly why pipeline tests can safely assert "exactly 3 files exist here."

**3. A clean, minimal code example:**
```python
def test_writes_one_file_per_document(fixture_corpus, tmp_path):
    out = tmp_path / "chunks"           # guaranteed empty, unique to this test
    run_pipeline(data_root=fixture_corpus, output_dir=out, ...)
    assert len(list(out.glob("*.json"))) == 4     # safe: nothing else can be here
```

**4. Main benefits, in short:** (a) tests can't contaminate each other; (b) exact-count
assertions become possible; (c) no cleanup code to write, and no risk of a test deleting
something real.

**What breaks if you use a fixed path like `data/chunks/`:** the test would assert against
whatever your last *real* pipeline run left behind — 77 files, not 4 — and running the real
pipeline would change your test results.

**Canonical reading:** [pytest docs — tmp_path](https://docs.pytest.org/en/stable/how-to/tmp_path.html).

**5 micro-exercises:**

1. **(5 min) See the path.** `def test_show(tmp_path): print(tmp_path)` run with
   `pytest -s`. Note it's a real directory on disk with a unique name.

2. **(5 min) Confirm isolation.** Write two tests that each create `tmp_path / "f.txt"` and
   assert the directory contains exactly one file. Both pass — because each got its own.

3. **(10 min) Find where pytest keeps them.** Run a test with `-s`, copy the printed path,
   and `ls` its parent. pytest retains the last few runs' directories for debugging.

4. **(10 min) Break isolation on purpose.** Rewrite one pipeline test to use a hardcoded
   `Path("data/chunks")` instead of `tmp_path`, run the real pipeline first, then the test.
   Watch the exact-count assertion fail. **Then revert.**

5. **(15 min) Build a whole fake corpus in `tmp_path`.** Create
   `tmp_path/data/company/x.md` and `tmp_path/data/products/y.md`, run `discover_documents`
   on it, and assert you get exactly 2 files. Compare this approach with using the committed
   `tests/fixtures/corpus/` — when is each better?

**How it works internally:** `tmp_path` is built on `tmp_path_factory`, which creates a
per-session base directory (under the OS temp folder) and then a subdirectory named after
the test for each request. pytest keeps the last 3 sessions' directories and garbage-collects
older ones, which is why you can still inspect files after a failing run.

**Checkpoint:** explain why `tmp_path` makes the assertion "exactly 4 JSON files exist"
trustworthy, when the same assertion against `data/chunks/` would be meaningless.

---

### 1.9 Markers and deselection — running only some tests

**1. Definition (plain English):** a marker is a label you stick on a test with
`@pytest.mark.something`, so you can later run or skip that group. Analogy: tagging some
recipes "needs an oven" — when the oven's broken, you cook everything *except* those.

**2. The "Why" — what problem this solves vs. the traditional approach:** one test
(`test_real_tiktoken_counts_differ_from_the_heuristic`) genuinely needs tiktoken's encoding
file, which a fresh CI runner would have to fetch over the network. Tagging it
`@pytest.mark.tiktoken` lets CI run `-m "not tiktoken"` and stay fully offline, without
deleting the test or permanently skipping it. Verified: CI's command gives **80 passed, 2
deselected**.

**3. A clean, minimal code example:**
```python
@pytest.mark.tiktoken
def test_needs_the_real_encoding():
    ...
```
```bash
pytest tests/ -q                      # 82 passed
pytest tests/ -q -m "not tiktoken"    # 80 passed, 2 deselected
```
Registered in `pyproject.toml` so a typo'd marker name warns instead of silently doing
nothing:
```toml
[tool.pytest.ini_options]
markers = ["tiktoken: needs tiktoken's encoding file ..."]
```

**4. Main benefits, in short:** (a) slow or network-dependent tests can coexist with fast
ones; (b) one flag selects the subset, not a list of test names to maintain; (c) registering
markers turns typos into visible warnings.

**What breaks without markers:** CI either runs the network-dependent test (and fails on a
cold runner) or you delete it (losing the only check that real tiktoken works).

**Canonical reading:** [pytest docs — marking test functions](https://docs.pytest.org/en/stable/how-to/mark.html).

**5 micro-exercises:**

1. **(5 min) Run both commands** above and compare the "passed / deselected" counts.

2. **(5 min) Run ONLY the marked tests.** `pytest tests/ -q -m tiktoken` — how many run?

3. **(10 min) Trigger the unregistered-marker warning.** Add
   `@pytest.mark.completely_made_up` to a scratch test and run it. Read the
   `PytestUnknownMarkWarning` — note the test still passes; only a warning appears.

4. **(10 min) Register a new marker.** Add `"slow: takes over a second"` to `pyproject.toml`,
   tag a test, and confirm `pytest -m slow` selects just it.

5. **(15 min) Compare `-m` with `-k`.** `-k` filters by test NAME substring, `-m` by marker.
   Run `pytest -k tiktoken` and `pytest -m tiktoken` — do they select the same tests here?
   Construct a case where they'd differ.

**How it works internally:** `@pytest.mark.x` attaches metadata to the function object rather
than executing anything. During collection pytest reads those marks and evaluates your `-m`
expression (a small boolean language supporting `not`, `and`, `or`) to decide which tests
enter the run. Deselected tests are never executed at all — which is why they can't fail.

**Checkpoint:** explain the difference between a test being *deselected*, *skipped*, and
*failing*, and which of the three `-m "not tiktoken"` produces.

---

### 1.10 Where test data comes from — fixture corpus vs. the real corpus

**1. Definition (plain English):** this project deliberately keeps **two** sources of test
data: small committed files in `tests/fixtures/corpus/` that the suite fully controls, and
the real `data/` directory used by a few "canary" tests. Analogy: a driving test on a closed
course *and* a supervised drive on real roads. The course tests specific manoeuvres
repeatably; the real road contains surprises nobody designed.

**2. The "Why" — what problem this solves vs. the traditional approach:** using only the real
corpus means editing a document breaks unrelated tests — which already happened in this
project (a test asserted 82 files; the true de-duplicated count was 76). But using *only*
controlled fixtures means your tests can only contain problems you already thought of — and
the real corpus is exactly what surfaced the `.ipynb_checkpoints` duplication nobody
predicted. Keeping both gets repeatability *and* a canary.

**3. A clean, minimal code example** — both fixtures, side by side in `conftest.py`:
```python
FIXTURE_CORPUS = Path(__file__).resolve().parent / "fixtures" / "corpus"
REAL_CORPUS = Path(__file__).resolve().parents[1] / "data"

@pytest.fixture
def fixture_corpus() -> Path:      # default: controlled, 3 files, 2 categories
    return FIXTURE_CORPUS

@pytest.fixture
def real_corpus() -> Path:         # canary only: the actual 76-document corpus
    return REAL_CORPUS
```

**4. Main benefits, in short:** (a) most tests are immune to data edits; (b) a few tests
still meet reality, catching things no fixture would contain; (c) naming them differently
makes each test's dependency explicit at a glance.

**What breaks if you use only one:** real-only → data edits break code tests (and you learn
to ignore failures, which is worse). Fixtures-only → you lose the canary, and problems like
stray checkpoint folders reach production undetected.

**Canonical reading:** no single canonical source; this is the general
[test data management](https://martinfowler.com/bliki/ObsoleteFixture.html) trade-off, and
Fowler's note on obsolete fixtures covers the failure mode fixtures drift into.

**5 micro-exercises:**

1. **(5 min) Count both corpora.** Use `discover_documents` on `fixture_corpus` (expect 3)
   and on `real_corpus` (expect 76). Confirm the sizes differ by two orders of magnitude.

2. **(5 min) Find which tests use which.** `grep -rn "real_corpus\|DATA_ROOT" tests/ | head`
   — how many tests depend on the real corpus?

3. **(10 min) Break the real corpus and see what fails.** Temporarily rename one file in
   `data/company/`, run the suite, note which tests fail. **Rename it back.** Were the
   failures about the *code* or about the *data*?

4. **(10 min) Add a file to the fixture corpus.** Create
   `tests/fixtures/corpus/company/new.md`, rerun the suite. Which tests break, and is that
   good (a real assertion) or annoying (a brittle one)?

5. **(15 min) Write a canary test of your own.** Write a test asserting that NO file in the
   real corpus lives outside a category folder (i.e. `infer_category` never raises for any
   real document). That's a data-quality check, not a code test — argue whether it belongs
   in the test suite at all.

**How it works internally:** both are ordinary `Path` objects computed from `__file__`, which
is the path of `conftest.py` itself — `parent` gets the `tests/` directory, `parents[1]` gets
the project root. Using `__file__` rather than a relative path is what makes the tests work
regardless of which directory you run `pytest` from.

**Checkpoint:** explain what a fixture corpus can never catch, and what the real corpus can
never guarantee.

---

## 2. Validating the tests are ACTUALLY working (not just green)

This section is unusual: the thing being validated *is* the validation. Run each yourself.

| What to check | Command / snippet | What "working" looks like |
|---|---|---|
| Coverage is what you think | `pytest tests/ --cov=src --cov-report=term-missing` | 99%, with only `chunker.py:257-259` and `pipeline.py:256` missing |
| The remaining gaps are untestable, not untested | Open those two locations | `build_ollama_embedder`'s body (needs a live server) and `if __name__ == "__main__"` |
| Tests actually protect the code (mutation test) | Gut `_merge_undersized` (`return spans` as its first line), run `pytest -k merge` | **3 failures.** If everything passes, your tests are decorative |
| Old tests really were vacuous | Same gutted code, run `pytest tests/ingestion/test_chunker.py -k merged_backwards` | **Passes** — the proof this whole step exists for |
| CI's exact command works offline | `pytest tests/ -q -m "not tiktoken" --cov=src --cov-report=term-missing` | 80 passed, 2 deselected, 98% |
| Tests don't depend on run order | `pip install pytest-randomly` then rerun | Still 82 passed. Failures here mean hidden shared state |
| No test leaks files into the repo | `git status --short` after a full run | Clean. `.coverage` and `htmlcov/` are already in `.gitignore` (lines 29-30), so coverage runs leave no trace |
| The suite is genuinely fast | `pytest tests/ -q --durations=5` | Total under ~2s; nothing individually over ~0.5s |

**Failure-mode drills** (predict first, then run — restore afterwards):

1. **Mutate a constant.** Change `count_tokens_approx`'s `// 4` to `// 5`. Does any test
   fail? If not, that function is effectively unprotected — what test would you add?
2. **Delete an assertion.** Remove the `assert` from any one test and rerun. It still
   "passes" — coverage stays identical. This is the clearest demonstration that coverage
   measures execution, not verification.
3. **Break fixture discovery.** Rename `tests/conftest.py` to `tests/fixtures_setup.py` and
   run. Read the error — which fixtures disappear, and why does the filename matter?

---

## 3. Visualizing it working

**How a single test actually executes:**

```
pytest tests/ingestion/test_cli.py::test_cli_threshold_flag_reaches_the_chunker_config
        │
        ▼
COLLECTION
  ├── walks tests/, imports test modules
  ├── reads the function's PARAMETER NAMES: (cli_run)
  ├── finds `cli_run` fixture in test_cli.py
  └── `cli_run` itself requests: monkeypatch, fixture_corpus, tmp_path
             │  (fixture_corpus comes from tests/conftest.py -- no import needed)
             ▼
SETUP  (fixtures run in dependency order)
  ├── tmp_path      -> creates a brand-new empty directory
  ├── monkeypatch   -> records an empty undo stack
  └── cli_run       -> patches pipeline.build_ollama_embedder  -> FakeEmbedder
                       patches pipeline.build_tiktoken_counter -> FakeCounter
                       patches pipeline.run_pipeline           -> spy wrapper
             │
             ▼
CALL   the test body runs
  └── pipeline.main()
        ├── argparse parses ["--threshold", "0.25"]  -> args.threshold = 0.25 (float!)
        ├── builders return FAKES (patched)          -> no network at all
        ├── run_pipeline(...) -> spy records kwargs, calls the REAL function
        │     └── discover -> load -> chunk -> enrich -> write into tmp_path
        └── print(format_report(report))
             │
             ▼
ASSERT  captured["config"].breakpoint_threshold_amount == 0.25
             │
             ▼
TEARDOWN  (automatic, even if the assert failed)
  ├── monkeypatch replays its undo stack -> real builders restored
  └── tmp_path directory retained briefly, then garbage-collected
```

**Tools worth using for deeper visibility:**
- **`pytest --cov=src --cov-report=html`** then open `htmlcov/index.html` — every uncovered
  line highlighted red, clickable per file. Far better than scanning a terminal table.
- **`pytest --durations=10`** — the ten slowest tests. A test that's suddenly slow is often
  one that accidentally hit a real network or model.
- **`pytest --fixtures`** — lists every fixture available, with docstrings; the fastest way
  to discover what's already built for you.
- **`pytest -v --setup-show`** — prints each fixture's SETUP and TEARDOWN as it happens, so
  the lifecycle diagrammed above becomes visible in real output.
- **`pytest-randomly`** (`pip install pytest-randomly`) — shuffles test order, exposing tests
  that secretly depend on running after another.

**One end-to-end "watch it work" exercise:**

1. **Establish the baseline.** `pytest tests/ -q --cov=src --cov-report=term-missing`.
   Note: 82 passed, 99%, only two "Missing" locations.
2. **See the fixture machinery.** `pytest tests/ingestion/test_cli.py -v --setup-show` —
   watch SETUP/TEARDOWN lines for `tmp_path`, `monkeypatch`, `fixture_corpus` and `cli_run`,
   and match them against the diagram above.
3. **Back up the source.** `cp src/ingestion/chunker.py /tmp/backup.py` — you're about to
   break it deliberately.
4. **Introduce the mutant.** Add `return spans` as the first line of `_merge_undersized`.
5. **Run the OLD test.** `pytest tests/ingestion/test_chunker.py -k merged_backwards -q` —
   **it passes.** The code it names is gone, and it doesn't care. Sit with that.
6. **Run the NEW tests.** `pytest tests/ingestion/test_coverage_gaps.py -k merge -q` —
   **3 failures.** Read one message and see exactly which assertion noticed.
7. **Check coverage with the mutant in place.**
   `pytest tests/ --cov=src --cov-report=term-missing -q` — the merge lines now show as
   Missing (they're unreachable after the early `return`), a second independent signal.
8. **Restore.** `cp /tmp/backup.py src/ingestion/chunker.py`, rerun the full suite, confirm
   82 passed. Verify with `git diff --stat` that nothing was left modified.

If you can explain, at every step, *why* the old test passed and the new one failed — you've
understood the testing mindset this microstep is actually about, not just the pytest API.
