# Schema Contract Deep Dive: Reverse-Engineering Phase 2, Microstep 6

A self-contained learning path for
[`tests/ingestion/test_chunk_contract.py`](../tests/ingestion/test_chunk_contract.py) and the
schema section of [`README.md`](../README.md) — validated against the real 214-chunk dataset
and by deliberately breaking things to see what noticed.

**How to use this doc:** run each exercise before reading its checkpoint. Five exercises per
concept, easiest → hardest, with hints for anyone who has never done this before.

**The measured facts this is built on** (all verified, none estimated):

```
214 chunks across 76 files
  distinct top-level key sets : 1      <- every chunk identical
  distinct metadata key sets  : 1      <- start_char, end_char, tokens_approx
  type consistency            : 100%
  9 semantic invariants       : all hold

With data/chunks/ absent:  1 passed, 10 skipped
Drift guard vs a typo:     FAILED -> only in README: {'char_kount'}, only in code: {'char_count'}
Module-scoped fixture:     1 SETUP event for 10 tests
```

---

## 0. What this step does, in plain English

Picture a factory that makes phone cases, shipping them to a company that packs them into
boxes. Someone has to write down what a "phone case" is: dimensions, weight, what's printed
on it. That written-down agreement is a **contract**. The factory can change its machines
freely, the packer can redesign its boxes — as long as both keep honouring the contract.

Microstep 6 writes that contract for chunks, and — this is the part that matters — makes it
*checkable* rather than just written down.

But there are two very different things to check, and conflating them is the core mistake:

- **Structural:** "does the box contain a phone case, with a length, a width and a weight?"
- **Semantic:** "does the number written on the weight label actually match what it weighs?"

A box can pass the first check perfectly and fail the second. A chunk can have every correct
key with every correct type and still claim `char_count: 500` for a 12-character string. No
schema language on earth catches that — only a check comparing fields *to each other* does.

**The concept map** (top to bottom):

```
The written-down agreement:        data contract
"Right keys, right types?":        structural validation (bidirectional set comparison)
"Do the values agree?":            semantic invariants
Loading expensive data once:       module-scoped fixtures
"Can't check this right now":      skip vs fail
Asking the code what it is:        introspection (dataclasses.fields)
Marking a region of a document:    HTML comment markers + regex extraction
Documentation that can't lie:      drift guards (docs as a test subject)
Checking per-group properties:     defaultdict grouping
Saying what you did NOT verify:    scoping a sign-off
```

---

## 1. Concept-by-concept

---

### 1.1 The data contract

**1. Definition (plain English):** a data contract is an agreement about the shape and
meaning of data handed from one stage to another, written down so both sides can change
independently. Analogy: a plug and a socket. Neither manufacturer needs to know anything
about the other's internals — they both agree on the shape of the pins and the voltage. The
kettle can be redesigned entirely; as long as the plug matches, it still works.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is that the producer and consumer just... know. The chunker writes JSON, and
whoever reads it next looks at a file and infers the format. That works exactly until
someone renames a field, and then a component nobody touched breaks for reasons nobody can
trace. A written contract means the producer can change *how* it chunks without changing
*what* it emits — and a violation becomes a test failure at the boundary rather than a
mystery three stages downstream.

**3. A clean, minimal code example** — a contract is just an agreed set of facts, asserted:
```python
SCHEMA_FIELDS = {field.name for field in dataclasses.fields(DocumentChunk)}

def test_every_chunk_has_exactly_the_schema_fields(chunks):
    for chunk in chunks:
        assert set(chunk) == SCHEMA_FIELDS
```
Everything else in this file is elaboration on that one idea.

**4. Main benefits, in short:** (a) the chunker's internals can change freely as long as its
output shape holds; (b) a violation surfaces at the boundary, with a clear message, instead
of as a `KeyError` deep in Qdrant upsert code; (c) the next stage (embedding) can be written
against a documented promise rather than by reading sample files.

**What breaks without it:** Phase 3 will read these chunks. Without a contract, a rename in
`DocumentChunk` silently produces files that the embedding step can't parse — discovered not
at chunk time, but mid-way through an expensive embedding run.

**Canonical reading:** [Andrew Jones — Data Contracts](https://www.oreilly.com/library/view/driving-data-quality/9781098153083/) for the full treatment; [JSON Schema](https://json-schema.org/) as the standard formal way to express the structural half.

**5 micro-exercises:**

1. **(5 min) Read the contract as a human would.** Open `README.md` and find the section
   between `<!-- chunk-schema:start -->` and `<!-- chunk-schema:end -->`. Then open one real
   file: `data/chunks/company__about.md.json`. Do the keys match the table?

2. **(5 min) Read it as the code does.**
   ```python
   import dataclasses
   from src.ingestion.schemas import DocumentChunk
   print({f.name for f in dataclasses.fields(DocumentChunk)})
   ```
   Compare that set against what you read in the README table.

3. **(10 min) Break the contract and watch it caught.** Edit one file in `data/chunks/` and
   delete the `"title"` key from the first chunk object. Run
   `pytest tests/ingestion/test_chunk_contract.py -q`. Which test fails, and does the message
   tell you *which chunk*? **Restore the file afterwards** (re-run the pipeline or `git` won't
   help — the directory is gitignored, so keep a backup first).

4. **(10 min) Add an extra key.** Add `"surprise": 1` to one chunk and rerun. Does the
   structural test catch an EXTRA key, or only a missing one?
   *Hint:* look at whether the assertion uses `==` or `issubset`.

5. **(15 min) Write the contract for something else.** Pick `LoadedDocument` in
   `loader.py` and write a three-line contract test asserting its field set. What would
   consume `LoadedDocument`, and would a contract there be worth the effort?

**How it works internally:** there is no machinery — a contract is a *convention* plus
whatever enforces it. Here the enforcement is `set(chunk) == SCHEMA_FIELDS`, comparing two
Python sets. What makes it a contract rather than a spot check is that it runs on every test
run, against every chunk, forever.

**Checkpoint:** explain why a contract benefits the *producer* (the chunker) as much as the
consumer, rather than being a constraint imposed on it.

---

### 1.2 Structural validation — and why the comparison goes both ways

**1. Definition (plain English):** checking that the right keys are present with the right
types. Analogy: an airport check-in. They verify you have a passport, a ticket and a bag
under the weight limit. They don't read your novel or judge your packing — just that the
required items exist and are the right kind of thing.

**2. The "Why" — what problem this solves vs. the traditional approach:** the tempting
shortcut is `assert "chunk_id" in chunk` for each field you care about — or
`assert SCHEMA_FIELDS.issubset(set(chunk))`. Both catch a **missing** field and neither
catches an **extra** one. An extra key is a real signal: it usually means someone added a
field to the dataclass and half the pipeline knows about it. Comparing sets with `==`
catches both directions at once.

**3. A clean, minimal code example:**
```python
schema = {"a", "b"}

chunk_missing = {"a"}                  # missing "b"
chunk_extra   = {"a", "b", "surprise"} # unexpected key

print(schema.issubset(chunk_missing))  # False -> caught
print(schema.issubset(chunk_extra))    # True  -> NOT caught!
print(schema == chunk_extra)           # False -> caught
```
That third line is why the real test uses `==`.

**4. Main benefits, in short:** (a) catches additions as well as omissions; (b) one assertion
covers all 10 fields rather than 10 separate `in` checks; (c) the failure message shows both
sets, so you can see exactly what differs.

**What breaks if you use `issubset`:** a chunk carrying an undocumented extra field passes
silently. Six months later a consumer relies on that undocumented field, and nothing ever
warned that it wasn't part of the contract.

**Canonical reading:** [Python docs — set operations](https://docs.python.org/3/library/stdtypes.html#set), particularly `issubset` vs `==`.

**5 micro-exercises:**

1. **(5 min) Run the three-line example above.** Confirm `issubset` returns `True` for the
   chunk with an extra key — the exact blind spot.

2. **(5 min) Read the real assertion.** Find `test_every_chunk_has_exactly_the_schema_fields`
   and confirm it uses `==`. Read the failure message format — what does it include to help
   you find the offending chunk?

3. **(10 min) Weaken it deliberately.** Change that test to use
   `assert SCHEMA_FIELDS.issubset(set(chunk))`, add a junk key to one real chunk file, and
   confirm the test now passes when it shouldn't. **Then restore both.**

4. **(10 min) Check types, not just presence.** Look at
   `test_field_types_match_the_dataclass`. Why does it use an explicitly written dict of
   expected types rather than reading them from the dataclass? (See concept 1.6 —
   there's a specific, verified reason.)

5. **(15 min) Find a structural bug this layer cannot catch.** Construct a chunk where every
   key is present with the correct type, but the data is nonsense (e.g. `char_count: 99999`,
   `text: "hi"`). Confirm the structural tests pass. Which layer catches it?

**How it works internally:** Python sets are hash tables. `set(chunk)` builds a set of the
dict's keys (dict iteration yields keys), and `==` on two sets compares membership in both
directions — it's `issubset` AND `issuperset` at once. That symmetry is the whole point.

**Checkpoint:** explain, with an example, a chunk that passes `issubset` but fails `==`, and
say which real-world mistake that represents.

---

### 1.3 Semantic invariants — what no schema language can express

**1. Definition (plain English):** an invariant is a statement that must always be true about
the relationship *between* values, not about any single value's type. `char_count` must equal
`len(text)`. Analogy: a receipt. Every line can be a valid number, correctly formatted — and
the total can still not match the sum of the items. Checking each number is well-formed will
never catch that; only adding them up will.

**2. The "Why" — what problem this solves vs. the traditional approach:** JSON Schema,
dataclasses and type checkers all validate *individual values*: is this a string, is this an
integer, is this key present. None of them can express "this integer must equal the length of
that string." That whole class of corruption — the values being individually fine and
collectively wrong — is invisible to structural validation. It's also exactly where pipeline
bugs live, because pipelines transform values and can easily update one and forget another.

**3. A clean, minimal code example** — a well-formed but self-contradictory chunk:
```python
chunk = {
    "text": "hi",
    "char_count": 500,          # a valid int... that is simply wrong
    "approx_token_count": 7,
    "metadata": {"tokens_approx": 99},   # disagrees with the field above
}
# Structural validation: PASSES (all keys present, all types correct)
# Semantic invariants:
assert chunk["char_count"] == len(chunk["text"])                        # FAILS
assert chunk["approx_token_count"] == chunk["metadata"]["tokens_approx"] # FAILS
```

**4. Main benefits, in short:** (a) catches the corruption class that schemas structurally
cannot see; (b) each invariant documents a real design decision (the two token counts agree
*by design* — D-2026-09-15-18); (c) invariants often catch pipeline bugs rather than data
bugs, because they encode what the transformation promised to do.

**What breaks without them:** enrichment's return value could be dropped somewhere (it's a
pure function — a caller who ignores the result silently gets unenriched chunks), and every
structural check would still pass while `approx_token_count` quietly held the char÷4 estimate.

**Canonical reading:** [Wikipedia — Invariant (computer science)](https://en.wikipedia.org/wiki/Invariant_(mathematics)#Invariants_in_computer_science); for the design-by-contract framing, [Bertrand Meyer's original notion of class invariants](https://en.wikipedia.org/wiki/Design_by_contract).

**5 micro-exercises:**

1. **(5 min) Run the contradictory-chunk example above** and confirm the structural
   properties hold while both invariants fail.

2. **(10 min) Break one invariant in real data.** Back up `data/chunks/`, then edit one
   chunk's `char_count` to `9999` and run the contract tests. Which test fails? Does the
   message let you find the file?

3. **(10 min) Break the token agreement.** Change one chunk's `metadata.tokens_approx` so it
   differs from `approx_token_count`. Which invariant catches it, and what real pipeline bug
   would produce that in practice?
   *Hint:* think about `enrich_with_token_counts` being a pure function.

4. **(10 min) List the invariants yourself, from the code.** Open the SEMANTIC section of
   the test file and write out all 7 in plain English. Which one is the *strongest* (hardest
   to satisfy accidentally)?

5. **(15 min) Invent a new invariant.** What must be true that isn't currently checked?
   Candidates: `approx_token_count <= char_count` (a token is at least one character);
   `end_char - start_char >= len(text)` (the source span can't be shorter than the text).
   Implement one and see whether the real 214 chunks satisfy it.

**How it works internally:** nothing special — these are ordinary `assert` statements
comparing two computed values. What makes them *invariants* rather than spot checks is that
they're asserted over the entire dataset, so a single violating record fails the run. The
design work is not the code; it's deciding *which* relationships must always hold.

**Checkpoint:** explain why `char_count == len(text)` cannot be expressed in JSON Schema,
even though JSON Schema can require both fields and check their types.

---

### 1.4 Module-scoped fixtures — loading expensive data once

**1. Definition (plain English):** `@pytest.fixture(scope="module")` means the fixture runs
**once per test file**, not once per test. All tests in that file share the same result.
Analogy: unlocking the building once in the morning rather than re-unlocking the front door
before every single meeting.

**2. The "Why" — what problem this solves vs. the traditional approach:** the default is
`scope="function"` — fresh setup per test. That's the right default (isolation), but here the
fixture reads **76 JSON files and parses 214 chunk objects**. Ten tests use it. Function
scope would do that work ten times for data that is read-only and identical every time.
Verified: with `scope="module"` there is exactly **1 SETUP event for 10 tests**.

**3. A clean, minimal code example:**
```python
@pytest.fixture(scope="module")     # once per file, not once per test
def chunks() -> list[dict]:
    files = sorted(CHUNKS_DIR.glob("*.json"))
    loaded = []
    for path in files:
        loaded.extend(json.loads(path.read_text(encoding="utf-8")))
    return loaded
```
Check it yourself: `pytest tests/ingestion/test_chunk_contract.py --setup-show | grep chunks`
shows a single `SETUP M chunks` line (the `M` means module scope).

**4. Main benefits, in short:** (a) expensive I/O happens once; (b) the tests stay fast enough
to run constantly; (c) scope is declared in one place rather than by hand-rolling a cache.

**What breaks if a module-scoped fixture returns something MUTABLE:** this is the real
danger. All ten tests share the *same list object*. If any test mutated it (appended,
sorted, edited a dict), later tests would silently see the modified version — and because
pytest can run tests in any order, the failure would be intermittent. It's safe here only
because every test reads and never writes.

**Canonical reading:** [pytest docs — fixture scopes](https://docs.pytest.org/en/stable/how-to/fixtures.html#scope-sharing-fixtures-across-classes-modules-packages-or-session).

**5 micro-exercises:**

1. **(5 min) Count the setups.** Run
   `pytest tests/ingestion/test_chunk_contract.py --setup-show | grep -c "SETUP    M chunks"`
   — expect `1`.

2. **(10 min) Switch to function scope and re-measure.** Change to
   `@pytest.fixture` (default scope), rerun the same grep, and compare both the count and
   the total runtime. **Then revert.**

3. **(10 min) Cause the mutation bug on purpose.** With module scope, add a test that does
   `chunks.clear()` and place it *before* others alphabetically. Run the file. What happens
   to the later tests, and why is this class of bug so unpleasant to debug?

4. **(10 min) Try the other scopes.** Change to `scope="session"` and run the whole suite
   with `--setup-show`. When would session scope be right, and what new risk does it add?

5. **(15 min) Measure the actual cost.** Time loading all 76 files and parsing 214 chunks
   in a plain script. Multiply by 10. Is the optimisation worth the mutation risk here, and
   would your answer change at 10,000 chunks?

**How it works internally:** pytest keeps a cache keyed by `(fixture name, scope, params)`.
On the first request within a scope it executes the fixture and stores the result; subsequent
requests in the same scope return the cached object — **the same object**, not a copy. That
identity is exactly why mutation leaks between tests.

**Checkpoint:** explain why module scope is safe for this fixture specifically, and name the
one property of the tests that makes it safe.

---

### 1.5 Skip vs. fail — "I can't check this right now" is not "this is broken"

**1. Definition (plain English):** `pytest.skip()` declares a test cannot meaningfully run in
this environment, so it isn't executed and isn't counted as a failure. Analogy: a fire drill
cancelled because the building is closed for renovation. That isn't a failed drill — but it
also isn't a passed one, and pretending otherwise is how you end up believing you're
protected when you aren't.

**2. The "Why" — what problem this solves vs. the traditional approach:** `data/chunks/` is
gitignored generated output, so a fresh clone and every CI run legitimately has none.
*Failing* would make a correctly-cloned repo look broken — the classic false alarm that
teaches people to ignore red builds. *Generating* the data inside the fixture would need a
live Ollama and ~9 minutes. Skipping, with a reason naming the exact command to fix it, is
the honest option.

**3. A clean, minimal code example:**
```python
@pytest.fixture(scope="module")
def chunks() -> list[dict]:
    files = sorted(CHUNKS_DIR.glob("*.json"))
    if not files:
        pytest.skip(
            "no chunk output in data/chunks/ — run `python -m src.ingestion.pipeline` "
            "first; this contract is only enforceable against real output"
        )
    ...
```
Verified behaviour with the directory moved aside:
```
SKIPPED [1] ...: no chunk output in data/chunks/ — run `python -m src.ingestion.pipeline` first
1 passed, 10 skipped
```

**4. Main benefits, in short:** (a) a clean checkout doesn't look broken; (b) the reason
string tells the reader exactly how to make the test run; (c) skipping from a *fixture* skips
every test that requests it, without repeating the condition on each one.

**The failure mode you must know about:** **a skip reads as green.** `pytest -q` shows
`10 skipped` in passing-coloured output, and CI reports success. Since chunk data never
exists in CI, **these contract tests never actually run there.** They protect a developer
who ran the pipeline locally, and nobody else. Use `pytest -rs` to surface skip reasons.

**Canonical reading:** [pytest docs — skipping](https://docs.pytest.org/en/stable/how-to/skipping.html).

**5 micro-exercises:**

1. **(5 min) Trigger the skip.** `mv data/chunks /tmp/backup`, run
   `pytest tests/ingestion/test_chunk_contract.py -q`, then `mv /tmp/backup data/chunks`.
   Note the count.

2. **(5 min) See the reasons.** Repeat with `-rs` and read the skip reason text. Does it tell
   you what to do?

3. **(10 min) Notice what still ran.** In that same skipped run the result was
   `1 passed, 10 skipped`. Which test passed, and why did it not need chunk data? What does
   that imply about what CI *does* still enforce?

4. **(10 min) Compare skip with xfail.** Read about `@pytest.mark.xfail`. What's the
   difference between "can't run here" and "expected to fail"? Which would be wrong here?

5. **(15 min) Design around the blind spot.** Propose a way to make CI genuinely enforce the
   contract. (Candidate: commit a tiny 2-file sample of chunk output purely as a contract
   fixture.) What are the costs — and does it reintroduce the drift problem the gitignore
   rule was avoiding?

**How it works internally:** `pytest.skip()` raises a special `Skipped` exception. Raised
inside a fixture, it propagates to every test requesting that fixture, so each is reported
skipped without its body running. Because it's an exception, code after the `skip()` call is
unreachable — which is why the fixture can safely assume files exist below that line.

**Checkpoint:** explain why "10 skipped" in a green CI run is more dangerous than "10 failed",
and what you'd check to notice it.

---

### 1.6 Introspection — asking the code what it is

**1. Definition (plain English):** introspection is a program examining itself at runtime.
`dataclasses.fields(DocumentChunk)` hands back a description of every field the class
declares. Analogy: a form that can list its own boxes, instead of you keeping a separate
piece of paper listing what's on the form.

**2. The "Why" — what problem this solves vs. the traditional approach:** the alternative is
typing the field names into the test by hand:
`SCHEMA_FIELDS = {"chunk_id", "doc_id", ...}`. That's a *second* copy of the schema, which
immediately starts drifting — add a field to the dataclass and the test keeps passing against
the old list, cheerfully validating a schema that no longer exists. Reading fields from the
class means the test automatically knows about changes.

**3. A clean, minimal code example:**
```python
import dataclasses
from src.ingestion.schemas import DocumentChunk

SCHEMA_FIELDS = {field.name for field in dataclasses.fields(DocumentChunk)}
print(SCHEMA_FIELDS)
# {'chunk_id', 'doc_id', 'source_path', 'category', 'title',
#  'chunk_index', 'text', 'char_count', 'approx_token_count', 'metadata'}
```

**4. Main benefits, in short:** (a) one source of truth — add a field and the test knows;
(b) impossible to typo a field name into the test; (c) the test describes a *relationship*
("the data matches the class") rather than a snapshot.

**A real, verified gotcha worth knowing:** `field.type` does **not** give you the type
object. Because `schemas.py` starts with `from __future__ import annotations`, every
annotation is stored as a **string**:
```python
f = dataclasses.fields(DocumentChunk)[0]
print(f.name, repr(f.type), type(f.type).__name__)
# chunk_id 'str' str        <- the STRING "str", not the class str
```
That's why `test_field_types_match_the_dataclass` writes its expected types out explicitly
instead of introspecting them — comparing against `'str'` the string would be comparing
against text, not checking a type. Resolving those strings back into real types needs
`typing.get_type_hints()`, which is a meaningfully bigger step.

**Canonical reading:** [Python docs — dataclasses.fields](https://docs.python.org/3/library/dataclasses.html#dataclasses.fields); [PEP 563 — postponed evaluation of annotations](https://peps.python.org/pep-0563/) for why the annotations are strings; [typing.get_type_hints](https://docs.python.org/3/library/typing.html#typing.get_type_hints) for turning them back into types.

**5 micro-exercises:**

1. **(5 min) List the fields.** Run the example above and compare the output against the
   README table.

2. **(5 min) See the string-annotation gotcha.**
   ```python
   f = dataclasses.fields(DocumentChunk)[0]
   print(repr(f.type), type(f.type))
   print(f.type == str)      # predict before running: True or False?
   ```

3. **(10 min) Resolve the annotations properly.**
   ```python
   import typing
   print(typing.get_type_hints(DocumentChunk))
   ```
   Now the values are real type objects. Why doesn't the test use this?
   *Hint:* consider `metadata: dict[str, Any]` — what does comparing that to `dict` involve?

4. **(10 min) Prove introspection tracks changes.** Add a throwaway field
   `experiment: str = ""` to `DocumentChunk` and rerun the contract tests. Which test fails,
   and what does that prove about the hand-typed alternative? **Then remove the field.**

5. **(15 min) Introspect something else.** Use `dataclasses.fields` on `PipelineReport` in
   `pipeline.py` and write a one-line test asserting `report.json`'s keys match it.

**How it works internally:** `@dataclass` stores a `__dataclass_fields__` dict on the class at
decoration time, mapping names to `Field` objects that record name, declared type, default
and metadata. `dataclasses.fields()` simply returns those values. The type being a string is
purely a consequence of `from __future__ import annotations`, which tells Python to store
annotations unevaluated rather than resolving them at class-creation time.

**Checkpoint:** explain why reading field names from the class is better than typing them
into the test, and name the one thing introspection here *cannot* easily give you.

---

### 1.7 Marker-delimited sections — carving a machine-readable region out of prose

**1. Definition (plain English):** the README's schema table sits between two HTML comments:
`<!-- chunk-schema:start -->` and `<!-- chunk-schema:end -->`. They're invisible when the
markdown renders, but a program can find them. Analogy: highlighter tabs in a textbook. A
reader doesn't notice them mid-sentence, but you can flip straight to the tagged pages.

**2. The "Why" — what problem this solves vs. the traditional approach:** the drift guard
needs to read *just the schema table*, not the whole README. Without markers it would have to
guess — "find the first markdown table," or "the table after the heading containing
'Schema'." Both break the moment someone adds another table or renames a heading. Explicit
markers say precisely "this region is machine-read," and survive any amount of editing
elsewhere in the file.

**3. A clean, minimal code example:**
```python
import re

readme = README.read_text(encoding="utf-8")
section = re.search(
    r"<!-- chunk-schema:start -->(.*?)<!-- chunk-schema:end -->", readme, re.DOTALL
)
assert section, "README is missing the chunk-schema markers"

# field names are the first cell of each row, in backticks
documented = set(re.findall(r"^\|\s*`(\w+)`\s*\|", section.group(1), re.MULTILINE))
```
Two flags carry the weight: `re.DOTALL` lets `.` match newlines (the section spans many
lines), and `re.MULTILINE` lets `^` match at the start of each line (each table row).

**4. Main benefits, in short:** (a) invisible to readers, obvious to tooling; (b) the rest of
the README can be rewritten freely without breaking the guard; (c) a missing marker is caught
explicitly with a clear message rather than silently matching nothing.

**What breaks if you drop the markers and grab "the first table":** adding the pipeline-status
table above the schema table — which this README has — would make the guard read the wrong
table entirely and compare status rows against field names.

**Canonical reading:** [Python docs — re.DOTALL and re.MULTILINE](https://docs.python.org/3/library/re.html#re.DOTALL); [the regex HOWTO on non-greedy matching](https://docs.python.org/3/howto/regex.html#greedy-versus-non-greedy) for why the pattern uses `.*?` rather than `.*`.

**5 micro-exercises:**

1. **(5 min) Find the markers.** `grep -n "chunk-schema" README.md`. Then view the rendered
   README (in an editor preview or on GitHub) and confirm they're invisible.

2. **(10 min) Run the extraction yourself.** Paste the code example into a REPL and print
   `documented`. Does it match `dataclasses.fields(DocumentChunk)`?

3. **(10 min) Break `re.DOTALL`.** Remove that flag and rerun. Why does the match fail
   entirely?
   *Hint:* what does `.` match by default, and does the section fit on one line?

4. **(10 min) Break `re.MULTILINE`.** Remove it from the `findall` and see how many field
   names you get. What is `^` anchoring to now?

5. **(15 min) Break greediness.** Add a *second* pair of markers later in the README, then
   change `.*?` to `.*` and observe what the match captures. Explain the difference between
   greedy and non-greedy matching using what you saw.

**How it works internally:** `re.search` scans for the first position where the whole pattern
matches. `(.*?)` is a non-greedy capture group — it consumes as few characters as possible
while still allowing the rest of the pattern (the end marker) to match, which is why it stops
at the *first* end marker rather than the last. `re.findall` with a single capture group
returns a list of just that group's contents, which is why the result is field names rather
than whole matched rows.

**Checkpoint:** explain what would go wrong with `.*` instead of `.*?` if the README ever
contained two marker pairs.

---

### 1.8 Drift guards — making documentation a test subject

**1. Definition (plain English):** a drift guard is a test that compares your *documentation*
against your *code* and fails when they disagree. Analogy: a museum label next to an exhibit.
If the exhibit is swapped and nobody changes the label, every visitor is now confidently
misinformed — worse off than if there were no label, because they'd have asked.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is discipline — "remember to update the README." Everyone means it; nobody does it
reliably. And a *wrong* schema table is worse than no table, because a reader trusts it and
builds against it. The guard converts "please remember" into "the build tells you." Verified
by introducing a typo:
```
AssertionError: README table and DocumentChunk disagree —
  only in README: {'char_kount'}, only in code: {'char_count'}
```

**3. A clean, minimal code example** — the whole idea in four lines:
```python
def test_readme_schema_table_lists_exactly_the_dataclass_fields():
    documented = set(re.findall(r"^\|\s*`(\w+)`\s*\|", section, re.MULTILINE))
    assert documented == SCHEMA_FIELDS, (
        f"only in README: {documented - SCHEMA_FIELDS}, "
        f"only in code: {SCHEMA_FIELDS - documented}"
    )
```
The message subtracts the sets *both ways*, so it names exactly which side is wrong.

**4. Main benefits, in short:** (a) documentation can't silently rot; (b) the failure message
tells you precisely what to change and where; (c) it needs no data, so it runs everywhere —
including CI, where the data-dependent tests all skip.

**A real limitation, stated plainly:** the guard compares **field names only**. The README
could describe `chunk_index` as a `str`, or give a completely wrong description, and nothing
would notice. Closing that means resolving the string annotations (concept 1.6) — a
meaningfully bigger job than matching names.

**Canonical reading:** no single canonical source; the general idea is "executable
documentation," of which [doctest](https://docs.python.org/3/library/doctest.html) is Python's
built-in form — it runs the examples *in* your docstrings and checks their output.

**5 micro-exercises:**

1. **(5 min) Watch it pass.** `pytest tests/ingestion/test_chunk_contract.py -k readme -v`.

2. **(10 min) Cause real drift.** Change `char_count` to `char_kount` in the README table
   and rerun. Read the message — does it tell you both sides? **Restore it.**

3. **(10 min) Drift the other way.** Add a field to `DocumentChunk` without touching the
   README. Which side does the message say is missing now? **Remove the field.**

4. **(10 min) Prove the limitation.** Change the README's `chunk_index` *type* cell from
   `int` to `str`. Does the guard fail? What does that tell you about its actual scope?

5. **(15 min) Close the gap.** Extend the regex to capture the type column too, and compare
   against `typing.get_type_hints(DocumentChunk)`. What makes `metadata: dict[str, Any]`
   awkward to compare?

**How it works internally:** it's an ordinary test — the only unusual thing is the *subject*.
Instead of importing code and asserting on its behaviour, it reads a `.md` file as text and
asserts on its contents. Nothing in pytest knows or cares that the input is documentation.

**Checkpoint:** explain why a wrong schema table is worse than no schema table, and how the
guard changes the cost of keeping documentation accurate.

---

### 1.9 Grouping with `defaultdict` — checking per-group properties

**1. Definition (plain English):** some invariants are about a *group*, not a single record.
"Chunk indices must be contiguous 0..N-1" is meaningless for one chunk — it's a property of
all chunks from one document, together. `defaultdict(list)` collects records into buckets
without you having to check whether each bucket exists yet. Analogy: sorting post into
pigeonholes where a new hole appears automatically the first time mail arrives for a name.

**2. The "Why" — what problem this solves vs. the traditional approach:** with a plain dict
you must guard every insert:
`if key not in groups: groups[key] = []` then `groups[key].append(x)`. That's two extra lines
in every grouping loop and an easy `KeyError` to forget. `defaultdict(list)` calls `list()`
automatically for any missing key.

**3. A clean, minimal code example:**
```python
from collections import defaultdict

by_document = defaultdict(list)
for chunk in chunks:
    by_document[chunk["source_path"]].append(chunk["chunk_index"])   # no existence check

for source_path, indices in by_document.items():
    assert sorted(indices) == list(range(len(indices))), source_path
```
That assertion catches both a **gap** (a chunk lost between chunking and writing) and a
**duplicate** (two chunks sharing an id, so one overwrites the other on upsert).

**4. Main benefits, in short:** (a) grouping loops stay two lines; (b) no `KeyError` class of
bug; (c) `sorted(indices) == list(range(n))` is a compact way to assert "exactly 0..N-1, no
gaps, no repeats."

**What breaks with a plain dict:** the first append for each new document raises `KeyError`
unless you write the existence check — and a missed check fails only for the *first* document
of each group, which is easy to miss in a small test.

**Canonical reading:** [Python docs — collections.defaultdict](https://docs.python.org/3/library/collections.html#collections.defaultdict).

**5 micro-exercises:**

1. **(5 min) See the automatic default.**
   ```python
   from collections import defaultdict
   d = defaultdict(list)
   d["new"].append(1)     # no KeyError, despite "new" never being set
   print(d)
   ```

2. **(5 min) See it fail without.** Repeat with a plain `{}` and read the `KeyError`.

3. **(10 min) Understand the contiguity assertion.** For `[0, 1, 3]` and for `[0, 1, 1]`,
   predict whether `sorted(x) == list(range(len(x)))` is True. Run both.

4. **(10 min) Break contiguity in real data.** Back up `data/chunks/`, edit one chunk's
   `chunk_index` from `1` to `5`, and run the contract tests. Which test fails, and does the
   message name the document?

5. **(15 min) Write a new group invariant.** Assert that within each document, `start_char`
   values are strictly increasing with `chunk_index` (chunks appear in reading order). Do the
   real 214 chunks satisfy it?

**How it works internally:** `defaultdict` subclasses `dict` and implements `__missing__`,
which `dict.__getitem__` calls when a key is absent. `__missing__` invokes the factory you
passed (`list`), stores the result under that key, and returns it — so the lookup *creates*
the entry as a side effect. Note this means a mere read of a missing key inserts it, which
occasionally surprises people.

**Checkpoint:** explain why chunk-index contiguity is a *group* invariant and cannot be
checked one chunk at a time.

---

### 1.10 Scoping a sign-off — saying what you did NOT verify

**1. Definition (plain English):** stating the boundaries of what a verification covers. This
sign-off certifies format and internal consistency; it explicitly does **not** certify chunk
quality. Analogy: a building inspection certificate. It says the wiring is safe. It doesn't
say the house is nice, well-located, or worth the price — and a certificate that didn't make
that distinction would be actively misleading.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
version is writing "verified ✅" and moving on. Six months later nobody remembers the scope,
and "signed off" is read as "approved." Here that would be badly wrong: **149 of 214 chunks
(69%) exceed the 500-character target, and the largest is 13,599 characters**. Those chunks
are schema-valid *and* badly sized simultaneously. Both facts are true; only one is certified.

**3. A clean, minimal code example** — this concept is documentation, not code, but it's
precise documentation:
```markdown
### Scope of this sign-off

This certifies the **format and internal consistency** of the chunk data. It does **not**
certify chunk *quality*. A known open issue at sign-off time: 149 of 214 chunks (69%)
exceed the 500-character target and the largest is 13,599 characters.
```

**4. Main benefits, in short:** (a) a future reader knows exactly what was and wasn't
checked; (b) the known problem stays visible instead of being buried by a green checkmark;
(c) it separates "the pipe is correctly shaped" from "the right thing is flowing through it."

**What breaks without it:** Phase 3 embeds these chunks into a vector index. Someone reading
"schema signed off" could reasonably conclude the dataset was ready — and bake 13,599-char
chunks into an index, where bad boundaries cap retrieval quality permanently.

**Canonical reading:** no canonical source for this specific habit, but it's the same
instinct behind [Model Cards for Model Reporting (Mitchell et al., 2019)](https://arxiv.org/abs/1810.03993), whose entire contribution is a standard place to state a model's intended scope and known limitations.

**5 micro-exercises:**

1. **(5 min) Read the scope statement.** Open `README.md`, find "Scope of this sign-off," and
   note the specific numbers. Why include numbers rather than "some chunks are large"?

2. **(10 min) Re-measure the claim.** Verify the 69% figure yourself:
   ```python
   import json, glob
   sizes = [c["char_count"] for f in glob.glob("data/chunks/*.json") if "report" not in f
            for c in json.load(open(f, encoding="utf-8"))]
   over = sum(1 for s in sizes if s > 500)
   print(f"{over}/{len(sizes)} = {100*over//len(sizes)}%, max {max(sizes)}")
   ```

3. **(10 min) Find what else isn't covered.** List three properties of the chunks nobody has
   verified. (Candidates: are boundaries in sensible places? Is any content duplicated across
   chunks? Does any chunk consist only of a markdown heading?)

4. **(10 min) Check one of them.** Pick a property from exercise 3 and measure it on the real
   data. Is it a problem?

5. **(15 min) Write a scope statement for a different step.** Write the "what this does NOT
   cover" paragraph for M5's test suite (99% coverage). What does 99% coverage genuinely not
   guarantee? *Hint:* you built a vacuous test that proved exactly that.

**How it works internally:** no mechanism — this is a writing discipline. Its value is
entirely in being explicit *at the moment of sign-off*, because that is the only moment when
you still remember what you did and didn't look at.

**Checkpoint:** explain the difference between "schema-valid" and "good," using the
13,599-character chunk as your example.

---

## 2. Validating that the contract is ACTUALLY working

The contract checks the data; this section checks the checks. Run each yourself.

| What to check | Command / snippet | What "working" looks like |
|---|---|---|
| The contract runs at all | `pytest tests/ingestion/test_chunk_contract.py -v` | 11 passed. If you see "skipped", you have no chunk data — run the pipeline |
| It genuinely catches structural breakage | Back up `data/chunks/`, delete a key from one chunk, rerun | `test_every_chunk_has_exactly_the_schema_fields` fails, naming the chunk |
| It catches semantic breakage | Set one chunk's `char_count` to `9999`, rerun | `test_char_count_matches_the_actual_text_length` fails |
| The drift guard catches doc rot | Change a field name in the README table, rerun | Fails with `only in README: {...}, only in code: {...}` |
| The drift guard needs no data | `mv data/chunks /tmp/b; pytest tests/ingestion/test_chunk_contract.py -q` | **1 passed, 10 skipped** — the guard still runs |
| Skips are loud, not silent | Same, with `-rs` | Skip reason names `python -m src.ingestion.pipeline` |
| The fixture loads once, not 11 times | `pytest tests/ingestion/test_chunk_contract.py --setup-show \| grep -c "SETUP    M chunks"` | `1` |
| Contract survives a re-chunk | Re-run the pipeline into a temp dir with a different threshold, re-check the invariants | **Verified:** a config producing **91 chunks instead of 214** — completely different boundaries — violated **zero** invariants. This is the point: the contract constrains *structure*, not *where the cuts fall* |
| Static checks clean | `ruff check src/ tests/ && mypy src/` | Clean |

**Failure-mode drills** (predict first; back up `data/chunks/` before each — it's gitignored,
so git will not save you):

1. **Add a fourth metadata key** to one chunk. Which layer catches it — structural or
   semantic? Would a JSON Schema have caught it?
2. **Make `chunk_index` non-contiguous** (change a `1` to a `5`). Which test fails, and why
   could no single-chunk check have caught it?
3. **Shift one chunk's `start_char` by +10.** Does any test notice? Which one, and what does
   it compare against to know?

---

## 3. Visualizing it working

**The two layers, and what each can and cannot see:**

```
                       one chunk from data/chunks/*.json
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
     STRUCTURAL LAYER                             SEMANTIC LAYER
   (what JSON Schema covers)                 (what no schema can express)
              │                                           │
   set(chunk) == SCHEMA_FIELDS                char_count == len(text)
   isinstance(v, expected_type)               approx_token_count == metadata[...]
   set(metadata) == EXPECTED_KEYS             chunk_id == f"{source}_{index}"
              │                                text.strip() is non-empty
              │                                0 <= start_char < end_char
              │                                source[start:end].split() == text.split()
              │                                indices contiguous 0..N-1  (GROUP invariant)
              ▼                                           ▼
        catches: renames,                    catches: values disagreeing,
        missing/extra keys,                  dropped return values, offset
        wrong types                          drift, lost or duplicated chunks
              │                                           │
              └─────────────────────┬─────────────────────┘
                                    ▼
                       chunk is contract-compliant


   SEPARATE, NEEDS NO DATA — which is why it still runs in CI:

     README.md                              schemas.py
     <!-- chunk-schema:start -->            @dataclass
     | `chunk_id` | str | ... |    <-->     class DocumentChunk:
     | `doc_id`   | str | ... |                 chunk_id: str
     <!-- chunk-schema:end -->                  doc_id: str
              │                                      │
        regex extract                      dataclasses.fields()
              └──────────── == ────────────────────┘
                     drift guard
```

**Tools worth using:**
- **`pytest --setup-show`** — makes fixture scope visible; you can literally count the
  `SETUP M chunks` lines to confirm the module-scope optimisation.
- **`pytest -rs`** — the only way to see *why* things skipped in summary output. Get in the
  habit; it's what stops a green-but-hollow run from fooling you.
- **`jq`** — `jq '.[0]' "data/chunks/company__about.md.json"` prints one chunk formatted, and
  `jq '[.[].char_count] | max'` finds the largest chunk without writing Python.
- **`python -m json.tool`** — same idea with no extra install:
  `python -m json.tool "data/chunks/company__about.md.json" | head -20`.

**One end-to-end "watch it work" exercise:**

1. **Back up the data** — it's gitignored, so nothing else will save you:
   `cp -r data/chunks /tmp/chunks_backup`
2. **Establish the baseline.** `pytest tests/ingestion/test_chunk_contract.py -v` → 11 passed.
3. **Look at one real chunk.**
   `python -m json.tool "data/chunks/company__about.md.json" | head -25` — match every key
   against the README table, and check `char_count` against the visible `text` length.
4. **Break it structurally.** Open that file, delete `"title"` from the first object, rerun
   the tests. Note *which* test fires first and whether the message identifies the chunk.
5. **Fix that, break it semantically instead.** Restore `"title"`, then set `"char_count"` to
   `9999`. Rerun. Different test, same dataset — this is the two layers made visible.
6. **Break the documentation.** Restore the chunk, then change `char_count` to `char_kount`
   in the README table. Rerun — the drift guard fires, naming both sides.
7. **Prove the CI blind spot.** Restore the README. Now
   `mv data/chunks /tmp/hidden && pytest tests/ingestion/test_chunk_contract.py -q` →
   **1 passed, 10 skipped**. That's exactly what CI sees on every run. Sit with the fact that
   it looks green.
8. **Restore everything** — `mv /tmp/hidden data/chunks`, verify with
   `pytest tests/ -q` → 93 passed, and `git status --short` → clean.

If at every step you can predict which test fires *before* running it, and explain why the
other layer stayed silent, you've understood the distinction this microstep is really about.
