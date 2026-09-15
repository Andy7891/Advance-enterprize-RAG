# Schemas Deep Dive: Reverse-Engineering `src/ingestion/schemas.py`

This is a self-contained learning path for Step 2.1:
[`src/ingestion/schemas.py`](../src/ingestion/schemas.py), validated against your real
[`tests/ingestion/test_schemas.py`](../tests/ingestion/test_schemas.py) and the actual
82-file `data/` knowledge base.

**How to use this doc:** for each concept, attempt every exercise yourself in a real Python
REPL or scratch file — actually run it, actually look at the output — form your own
explanation, *then* read the checkpoint. Each exercise ends with a **Concept & Learning**
line stating exactly what it proves.

---

## 0. High-level summary + concept map

This file defines **Contract 1**: the `DocumentChunk` shape every later ingestion/retrieval
module will consume, plus the utility functions that build one correctly from a real
markdown file. Nothing here embeds, searches, or stores anything yet — it's pure data
modeling and ID-generation logic, the foundation everything downstream (chunking strategy,
embedding, Qdrant, Neo4j, citations in the UI) is built on top of.

Think of it like a shipping manifest system. Before anything gets loaded onto a truck
(embedded into a vector store), every crate (chunk) needs a permanent, unforgeable serial
number (ID) and a label (category, title, source) — and crucially, if you re-print the same
crate's label twice, it MUST get the same serial number both times, or the warehouse
(Qdrant) ends up with duplicate crates nobody can tell apart.

Concept map, in dependency order:

```
Data modeling:       dataclass basics → frozen (immutability) → slots
                      → field(default_factory=...) for mutable defaults
Identity design:      deterministic hashing → why doc_id ≠ chunk_id derivation
                      → idempotency as the payoff
Path/filesystem:      Path.rglob → Path.relative_to → path normalization
                      (Windows backslash vs POSIX forward slash)
Text extraction:      regex title extraction → fallback values
Estimation:            approximate token counting → the "cheap heuristic vs
                      exact/coupled tool" trade-off
Composition:          make_chunk() as the single assembly point tying
                      everything above together
```
Understand "data modeling" and "identity design" before touching `make_chunk` — everything
else composes into it.

---

## 1. Concept-by-concept

---

### 1.1 `@dataclass` — auto-generated boilerplate for data-holding classes

**Definition:** a decorator that auto-generates `__init__`, `__repr__`, and `__eq__` from a
class's annotated fields, so you don't hand-write that boilerplate for a class whose whole
job is holding data. _init _  is called wen we make an object of that class. 
_repr_  Print out or view content of an object, for debugging. __  .
_eq_ When we compare instance of two objects. 

**Why here:** `DocumentChunk` has 8 fields — writing `__init__` by hand (assigning each
`self.x = x`) would be pure repetition. `@dataclass` also gives us `__eq__` for free, which
is exactly what the idempotency tests rely on implicitly (two `DocumentChunk`s built from
identical inputs should be comparable) and `__repr__`, which makes failing test assertions
readable (`DocumentChunk(chunk_id='abc123', ...)` instead of `<object at 0x7f...>`).

**What breaks if changed:** remove `@dataclass` and keep the field annotations as plain class
attributes — Python treats them as class-level defaults shared across all instances (a
classic Python footgun), `DocumentChunk(chunk_id=...)` construction syntax stops working
entirely, and nothing auto-generates `__eq__`/`__repr__`.

**Canonical reading:** [Python docs: dataclasses](https://docs.python.org/3/library/dataclasses.html)
https://www.youtube.com/watch?v=5mMpM8zK4pY   - data class.

**Exercise 1 (5 min) — see what `@dataclass` actually generates:**
```python
from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int

p = Point(1, 2)
print(p)          # what does this print, before you run it?
print(p == Point(1, 2))
```
Predict the output of both `print` lines, then run it.
> **Concept & Learning:** `@dataclass` generates a `__repr__` that shows field names and
> values (not a memory address), and an `__eq__` that compares field-by-field — two
> separately-constructed instances with identical field values are `==`, which is exactly
> why `test_make_chunk_is_idempotent_across_reingestion` can meaningfully compare
> `chunk_run_1.chunk_id == chunk_run_2.chunk_id` and trust it reflects real equality.

**Exercise 2 (10 min) — remove `@dataclass` and watch it break:**
Take the `Point` class above, delete the `@dataclass` line, and try `Point(1, 2)` again.
Read the exact `TypeError`.
> **Concept & Learning:** without `@dataclass`, a class with only annotated attributes (no
> `__init__`) doesn't accept constructor arguments at all — annotations alone are just type
> hints, not behavior. This is direct proof `@dataclass` is doing real generation work, not
> just adding metadata.

**Exercise 3 (10 min) — a plain class attribute vs a dataclass field, mutable-default trap:**
```python
class Bad:
    items = []  # NOT a dataclass field — a shared class attribute

a, b = Bad(), Bad()
a.items.append("x")
print(b.items)  # predict this before running
```
This happens because items = [] is defined directly inside the class body, making it a class attribute instead of an instance attribute.
In Python, class attributes are shared by all instances of that class. 
They are created once when the module loads, not fresh for every object.

Then try the same thing with `DocumentChunk`'s actual `metadata: dict[str, Any] =
field(default_factory=dict)` pattern — construct two chunks, mutate one's `.metadata` (if
you temporarily remove `frozen=True` to allow it), and check the other is unaffected.
> **Concept & Learning:** this is the exact bug `field(default_factory=dict)` in
> `DocumentChunk.metadata` exists to prevent — a bare mutable default (`metadata: dict =
> {}`) would silently share ONE dict across every `DocumentChunk` instance ever constructed,
> so mutating one chunk's metadata would corrupt every other chunk's metadata too.

**Checkpoint:**
*Why does `dataclass` require `field(default_factory=dict)` instead of just
`metadata: dict[str, Any] = {}`?*

<details><summary>Reveal</summary>
Default values in a class body are evaluated ONCE, at class-definition time, and then
shared by reference across every instance that doesn't override them. For an immutable
default (like `0` or `""`) this is harmless since you can't mutate an int in place. But `{}`
is mutable — if every `DocumentChunk` without explicit metadata shared the literal same dict
object, calling `.metadata["x"] = 1` on one chunk would make `"x": 1` appear in every other
chunk's metadata too. `field(default_factory=dict)` instead calls `dict()` fresh for EACH
new instance, giving every chunk its own independent dict.
</details>

---

### 1.2 `frozen=True` — immutability as an invariant guard

**Definition:** makes a dataclass's instances read-only after construction — any attempt to
assign to a field raises `dataclasses.FrozenInstanceError`.

**Why here:** `chunk_id` is mathematically DERIVED from `doc_id + chunk_index + text` (via
`compute_chunk_id`). If `DocumentChunk` were mutable, someone could construct a chunk, then
later do `chunk.text = "edited"` — and now `chunk.chunk_id` silently no longer matches
`chunk.text`. That's not a hypothetical: it's exactly the kind of bug that would surface as
"why did Qdrant return a chunk whose text doesn't match what I searched for" weeks later,
far from where the actual mistake happened. `frozen=True` converts that mistake into an
immediate `FrozenInstanceError` at the exact line that tried to do it.

**What breaks if changed:** remove `frozen=True` — `DocumentChunk` still works for every
normal use case (construction, reading fields, equality) — the difference only shows up if
something tries to mutate a field post-construction, which `test_document_chunk_is_immutable`
exists specifically to catch.

**Canonical reading:** [Python docs: dataclasses — frozen instances](https://docs.python.org/3/library/dataclasses.html#frozen-instances)

**Exercise 1 (5 min) — trigger the error directly:**
```python
from src.ingestion.schemas import make_chunk
from pathlib import Path

DATA_ROOT = Path("data")
source = DATA_ROOT / "company" / "about.md"
chunk = make_chunk(source_path=source, data_root=DATA_ROOT, title="x", chunk_index=0, text="hi")
chunk.text = "tampered"  # predict the exception type before running
```
> **Concept & Learning:** you'll get `dataclasses.FrozenInstanceError: cannot assign to
> field 'text'` — direct, hands-on confirmation of what `test_document_chunk_is_immutable`
> asserts programmatically. Immutability isn't a comment, it's enforced at the language
> level.

**Exercise 2 (10 min) — immutability doesn't mean "deeply frozen":**
```python
chunk = make_chunk(source_path=source, data_root=DATA_ROOT, title="x", chunk_index=0, text="hi")
chunk.metadata["sneaky"] = "value"  # does THIS raise FrozenInstanceError?
print(chunk.metadata)
```
Predict, then run.
> **Concept & Learning:** `frozen=True` only prevents REASSIGNING a field (`chunk.metadata =
> {...}`) — it does nothing to prevent mutating the OBJECT a field points to (calling
> `.metadata[...] = ...` on the dict itself). This is a real, important gap: `metadata` is
> the one field on `DocumentChunk` that is NOT actually protected by freezing, despite the
> class being "immutable." Worth knowing before you rely on `metadata` for anything where
> tamper-proofing matters.

**Exercise 3 (10 min) — construct a MODIFIED copy instead of mutating:**
```python
import dataclasses
new_chunk = dataclasses.replace(chunk, chunk_index=99)
print(chunk.chunk_index, new_chunk.chunk_index)
print(chunk.chunk_id == new_chunk.chunk_id)  # predict before running
```
> **Concept & Learning:** `dataclasses.replace()` is the correct, idiomatic way to get a
> "changed" version of a frozen dataclass — it builds an entirely new instance with the
> given fields overridden. Notice that `new_chunk.chunk_id` is STILL the OLD chunk_id here,
> because `replace()` doesn't re-run `make_chunk`'s ID-computation logic — it just copies
> fields verbatim. This is a subtle trap: `replace()` can produce an internally-inconsistent
> chunk (wrong `chunk_id` for its new `chunk_index`) unless you explicitly also recompute
> and pass the new `chunk_id`. Real lesson: `make_chunk()` is the ONLY safe way to produce a
> chunk with guaranteed-consistent IDs; `replace()` bypasses that guarantee entirely.

**Checkpoint:**
*Does `frozen=True` protect `chunk.metadata`'s contents from being changed after
construction?*

<details><summary>Reveal</summary>
No — `frozen=True` only blocks reassigning the `metadata` attribute itself
(`chunk.metadata = {...}`), not mutating the dict object it currently points to
(`chunk.metadata["key"] = value`). True deep immutability would require either using an
immutable mapping type (like `types.MappingProxyType`) or avoiding mutable field types
altogether — worth considering if `metadata` integrity ever becomes load-bearing.
</details>

---

### 1.3 `slots=True` — memory/attribute-access optimization

**Definition:** tells the dataclass to use `__slots__` internally instead of a per-instance
`__dict__`, which both saves memory per instance and prevents adding attributes that weren't
declared as fields.

**Why here:** with potentially thousands of `DocumentChunk` instances in memory at once
during a full ingestion run (82 files × several chunks each), `__slots__` avoids the
overhead of a full `__dict__` per instance. It's a genuinely free efficiency win here since
we never needed dynamic attribute assignment on chunks anyway (that's what the `metadata`
field is explicitly for).

**What breaks if changed:** remove `slots=True` — everything still works identically at the
functional level; you'd only notice a (usually small) memory increase at scale, and you'd
lose the side effect that `slots=True` also blocks accidentally setting an undeclared
attribute (e.g. `chunk.oops = 1` would silently succeed without slots, and raise
`AttributeError` with it).

**Canonical reading:** [Python docs: dataclasses — slots](https://docs.python.org/3/library/dataclasses.html#dataclasses.dataclass) (see the `slots` parameter, Python 3.10+)

**Exercise 1 (5 min) — try to add an undeclared attribute:**
```python
chunk.oops = "not a real field"  # predict: works, or raises?
```
> **Concept & Learning:** with `slots=True`, this raises `AttributeError: 'DocumentChunk'
> object has no attribute 'oops'` — slots doesn't just save memory, it also closes off an
> entire class of "typo'd an attribute name and it silently created a new one instead of
> erroring" bugs.

**Exercise 2 (10 min) — measure the memory difference directly:**
```python
import sys
from dataclasses import dataclass

@dataclass(slots=True)
class WithSlots:
    a: int; b: int; c: str

@dataclass
class WithoutSlots:
    a: int; b: int; c: str

x = WithSlots(1, 2, "hi")
y = WithoutSlots(1, 2, "hi")
print(sys.getsizeof(x), sys.getsizeof(x.__dict__) if hasattr(x, "__dict__") else "no __dict__")
print(sys.getsizeof(y), sys.getsizeof(y.__dict__))
```
> **Concept & Learning:** you'll see `WithSlots` has no `__dict__` at all (hence the
> conditional), while `WithoutSlots` carries one — a real, measurable per-instance memory
> difference you can reproduce directly, not just take on faith.

**Exercise 3 (10 min) — combine `frozen` and `slots`, check for interaction issues:**
Construct a `DocumentChunk`, then try `dataclasses.asdict(chunk)` and
`dataclasses.astuple(chunk)`. Do both work normally with `frozen=True, slots=True` together?
> **Concept & Learning:** `frozen` and `slots` are independent, composable dataclass
> features — using both together (as `DocumentChunk` does) doesn't create any special
> interaction; standard dataclass utilities like `asdict`/`astuple` work exactly the same
> regardless. Useful to confirm since combining "advanced" options can sometimes surprise
> you, and here it doesn't.

**Checkpoint:**
*If `DocumentChunk` used `slots=True` WITHOUT `frozen=True`, could you still do
`chunk.text = "new value"`?*

<details><summary>Reveal</summary>
Yes — `slots` and `frozen` control different things. `slots` restricts WHICH attribute
names can exist on an instance (no arbitrary new ones); it says nothing about whether
existing declared fields can be reassigned. `frozen` is what blocks reassignment. Without
`frozen`, `chunk.text = "new value"` would succeed fine even with `slots=True`, since `text`
IS a declared field/slot.
</details>

---

### 1.4 Deterministic hashing for identity (`compute_doc_id`, `compute_chunk_id`)

**Definition:** deriving an identifier by hashing specific input data (with SHA-256 here),
so that identical inputs ALWAYS produce the identical output ID — as opposed to a randomly
generated ID like `uuid4()`.

**Why here:** this is the single most important design decision in the whole file. Ingestion
needs to be re-runnable — you WILL re-run it (fixed a typo in a contract, added a new file,
changed chunking parameters). If chunk IDs were random, every re-run would create entirely
new IDs for unchanged content, and Qdrant would accumulate duplicate vectors for the same
text forever. Deterministic hashing means "same input → same ID," so a vector-store `upsert`
naturally overwrites instead of duplicating.

**What breaks if changed:** swap `compute_chunk_id` to use `uuid.uuid4()` instead — every
single test in `test_schemas.py` that checks determinism/idempotency
(`test_compute_chunk_id_stable_for_unchanged_input`,
`test_make_chunk_is_idempotent_across_reingestion`) would immediately fail, because two
calls with identical arguments would now produce different IDs.

**Canonical reading:** [Python docs: hashlib](https://docs.python.org/3/library/hashlib.html) and [Wikipedia: content-addressable storage](https://en.wikipedia.org/wiki/Content-addressable_storage) (the general pattern this is an instance of)

**Exercise 1 (5 min) — prove determinism directly in a REPL:**
```python
import sys
from pathlib import Path

# 1. Convert the target folder path to an absolute path
target_folder = Path(r"E:\udemy gen ai\Advance Rag\src\ingestion")

# 2. Append that folder path to Python's search list
sys.path.append(str(target_folder))

# 3. Import your function normally
from schemas import compute_chunk_id

from src.ingestion.schemas import compute_chunk_id
id1 = compute_chunk_id("doc123", 0, "hello world")
id2 = compute_chunk_id("doc123", 0, "hello world")
id3 = compute_chunk_id("doc123", 0, "hello WORLD")  # note the capitalization
print(id1 == id2, id1 == id3) 
```
Predict both booleans before running.
> **Concept & Learning:** `id1 == id2` (same inputs, same output — determinism) but
> `id1 != id3` (SHA-256 is extremely sensitive to input — a single capitalization change
> produces a completely different hash, not a "slightly different" one). This second part
> matters: hash outputs give you NO sense of "how similar" two inputs were, only "identical
> or not."

**Exercise 2 (10 min) — swap in `uuid4()` and watch a real test fail:**
Temporarily change `compute_chunk_id` to `return str(uuid.uuid4())[:16]` (ignoring its
arguments), rerun `pytest tests/ingestion/test_schemas.py -v`, and read which specific tests
fail and why.
> **Concept & Learning:** direct proof, via your own test suite, of exactly what
> non-determinism breaks — `test_compute_chunk_id_stable_for_unchanged_input` fails because
> two calls now produce different IDs even with identical arguments; this is the concrete,
> automated version of the "duplicate vectors on re-ingestion" bug described in NOTES.md.

**Exercise 3 (10 min) — measure real-world collision risk of the `[:16]` truncation:**
Full SHA-256 is 64 hex characters (256 bits); we truncate to 16 (64 bits). Look up the
"birthday problem" approximation (`sqrt(2^64)` ≈ 4.3 billion) — for a knowledge base that
will realistically have a few thousand chunks (this project has 82 files, likely a few
hundred to low-thousands of chunks after real chunking), is a collision plausible? Compute
the rough probability using the birthday-paradox formula
`p ≈ n² / (2 × 2^64)` for `n` = 10,000 chunks.
> **Concept & Learning:** you get a real, computed number instead of a hand-wavy "should be
> fine" — with `n=10,000`, `p ≈ 10000² / (2 × 2^64) ≈ 2.7 × 10⁻¹²` (verified by running the
> formula directly, not eyeballed) — roughly 1-in-370-billion, so the `[:16]` truncation is a
> safe, deliberate size/readability trade-off for this project's realistic scale, not a
> hidden risk. This is the actual due-diligence process for validating a truncation choice,
> not just trusting it.

**Checkpoint:**
*If two different source files happened to have byte-for-byte identical content, would
`compute_doc_id` give them the same ID?*

<details><summary>Reveal</summary>
No — `compute_doc_id` hashes the file's PATH (`source_path`), not its content. Two files
with identical content but different filenames/locations get different `doc_id`s, because
`doc_id`'s entire purpose is "identify this specific document slot for replacement on
re-ingestion," not "deduplicate identical content across the corpus" (a different, separate
problem this module doesn't attempt to solve).
</details>

---

### 1.5 The `doc_id` (path-based) vs `chunk_id` (content-based) split

**Definition:** two different hash inputs for two different identity questions — "which
document is this part of" (path-derived, survives edits) vs "is this exact chunk's content
unchanged" (content-derived, changes when text changes).

**Why here:** these answer genuinely different questions that both matter for ingestion
lifecycle management. If a contract file gets edited, you want the SAME `doc_id` (so the
system knows "these new chunks replace that file's old chunks") but DIFFERENT `chunk_id`s
for any chunks whose text actually changed (so the vector store's diffing/upsert logic knows
which specific chunks are new content vs unchanged).

**What breaks if changed:** make `doc_id` content-based too (e.g. hash the whole file's
text) — editing even one word of a contract would produce a brand-new `doc_id`, and nothing
would connect the new chunks to the OLD chunks from that same logical document — the old
chunks become orphaned dead weight in Qdrant with no ID linking them for cleanup.

**Canonical reading:** No single canonical doc for this exact pattern — it's a general data-
modeling principle ("identity vs versioning" separation), related to how Git separates a
file's path-identity from its content-hash-per-commit.

**Exercise 1 (10 min) — simulate an edit and observe both IDs:**
```python
from src.ingestion.schemas import compute_doc_id, compute_chunk_id

doc_id = compute_doc_id("contracts/foo.md")
chunk_before = compute_chunk_id(doc_id, 0, "Original contract text.")
chunk_after = compute_chunk_id(doc_id, 0, "Original contract text, amended.")
print("doc_id same file, before/after edit — always identical:", doc_id)
print("chunk_id before:", chunk_before)
print("chunk_id after: ", chunk_after)
print("chunk changed:", chunk_before != chunk_after)
```
> **Concept & Learning:** you directly observe the split working as designed — `doc_id`
> never even needs recomputing (it only depends on the unchanging path), while `chunk_id`
> changes exactly when content changes, giving you a natural "did this specific chunk's
> content change" signal for free.

**Exercise 2 (10 min) — what if you RENAMED the file instead of editing it?**
```python
doc_id_before_rename = compute_doc_id("contracts/foo.md")
doc_id_after_rename = compute_doc_id("contracts/foo-renamed.md")
print(doc_id_before_rename == doc_id_after_rename)  # predict first
```
> **Concept & Learning:** a rename produces a DIFFERENT `doc_id` — from this system's
> perspective, a renamed file is indistinguishable from "delete the old file, add a brand
> new one," because `doc_id` only knows about the path string, not any deeper notion of file
> identity (like an inode or a content-similarity check). This is a real, current limitation
> worth knowing: renaming files in `data/` will orphan their old chunks exactly like deleting
> them would, unless a separate rename-detection step is added later.

**Exercise 3 (10 min) — design check: could you swap which one is content-based?**
Thought experiment (no code): what would go wrong if we flipped it — `chunk_id` path+index
based (no text), `doc_id` content-based (whole file text hashed)? Write down your prediction,
then check it against the "what breaks if changed" paragraph above for `doc_id`.
> **Concept & Learning:** reasoning through the INVERSE of a design decision, without
> running code, is a real way to test whether you actually understand WHY a design choice
> was made — if you can predict the failure mode of the opposite choice correctly, you
> understand the original choice for real, not just as a memorized fact.

**Checkpoint:**
*You edit one paragraph in `data/contracts/foo.md`, which happens to fall entirely within
chunk index 3 of 7. After re-running ingestion, which chunk_ids change, and which stay the
same?*

<details><summary>Reveal</summary>
Only chunk 3's `chunk_id` changes (its text changed, and `chunk_id` is content-derived).
Chunks 0, 1, 2, 4, 5, 6 keep their exact same `chunk_id`s (their text is unchanged, and
`chunk_id` doesn't depend on any OTHER chunk's content). All 7 chunks keep the same `doc_id`
(path unchanged). This means a re-ingestion upsert would touch exactly ONE vector in Qdrant,
not rewrite the whole document's worth of vectors — an efficient, surgical update, which is
the entire payoff of this identity design.
</details>

---

### 1.6 `Path.rglob` — recursive filesystem traversal

**Definition:** `Path.rglob(pattern)` recursively searches a directory tree (all
subdirectories, arbitrarily deep) for paths matching a glob pattern, returning an iterator.

**Why here:** `data/` has a fixed structure (`data/<category>/<file>.md`, one level deep),
but `discover_markdown_files` uses `rglob("*.md")` (recursive) rather than `glob("*/*.md")`
(exactly one level) — a deliberate choice to not hard-code an assumption about folder depth,
so it would keep working unmodified if a category ever gained sub-subfolders.

**What breaks if changed:** swap `rglob("*.md")` for `glob("*.md")` (non-recursive, direct
children only) — `discover_markdown_files(DATA_ROOT)` would find ZERO files, since every real
`.md` file lives inside a category subfolder, one level below `data_root` itself, not
directly inside it.

**Canonical reading:** [Python docs: pathlib — Path.rglob](https://docs.python.org/3/library/pathlib.html#pathlib.Path.rglob)

**Exercise 1 (5 min) — see the difference directly on real data:**
```python
from pathlib import Path
data_root = Path("data")
print(len(list(data_root.glob("*.md"))))    # predict: 0 or 82?
print(len(list(data_root.rglob("*.md"))))   # predict: 0 or 82ish?
```
> **Concept & Learning:** `glob("*.md")` returns 0 (no `.md` files live DIRECTLY in
> `data/`), while `rglob("*.md")` finds all of them regardless of depth — concrete proof of
> why `discover_markdown_files` had to choose the recursive variant.

**Exercise 2 (10 min) — `rglob` also finds the checkpoint duplicates — see it BEFORE the filter:**
```python
all_md = list(data_root.rglob("*.md"))
checkpoint_files = [p for p in all_md if ".ipynb_checkpoints" in p.parts]
print(len(all_md), len(checkpoint_files))
```
> **Concept & Learning:** `rglob` itself has NO awareness of "noise" directories — it finds
> everything matching the pattern, checkpoints included. The exclusion logic in
> `discover_markdown_files` (`if not _EXCLUDED_DIR_NAMES & set(path.parts)`) is a completely
> separate, deliberate filtering step layered on top — `rglob` alone would silently ingest
> duplicate content if that filter were ever removed.

**Exercise 3 (10 min) — glob pattern precision matters:**
```python
print(len(list(data_root.rglob("*.md"))))     # all .md files
print(len(list(data_root.rglob("*.MD"))))     # different case
print(len(list(data_root.rglob("**/*.md"))))  # explicit recursive-glob syntax
```
On your OS, are the case-sensitivity results what you expected? Is `rglob("**/*.md")`
identical to `rglob("*.md")`?
> **Concept & Learning:** `rglob("*.md")` and `rglob("**/*.md")` are actually equivalent —
> `rglob` already implies the `**/` recursive prefix, making the explicit form redundant (a
> common point of confusion). Case-sensitivity of glob matching is OS-dependent (Windows
> filesystems are typically case-insensitive, Linux typically case-sensitive) — worth
> knowing since this project's CI (Step 1.2) runs on `windows-latest`, matching your local
> dev OS, so this particular gotcha won't bite you in THIS project, but would on a different
> CI runner OS.

**Checkpoint:**
*If a new category folder `data/policies/subfolder/deep.md` were added (a `.md` file TWO
levels deep, not one), would `discover_markdown_files` find it, and what would
`infer_category` return for it?*

<details><summary>Reveal</summary>
`discover_markdown_files` WOULD find it — `rglob` doesn't care about depth. `infer_category`
would return `"policies"` — it only looks at `relative.parts[0]`, the FIRST path segment
after `data_root`, regardless of how many more segments follow. This is a real, positive
consequence of the "why here" design choice: both functions gracefully handle a case they
weren't explicitly tested against yet, because neither hard-codes an assumption about exact
depth.
</details>

---

### 1.7 `Path.relative_to` — path normalization and category inference

**Definition:** computes a path relative to a given base path, raising `ValueError` if the
target path isn't actually inside that base.

**Why here:** `infer_category` uses `relative_to` specifically because it's the mechanism
that both (a) strips the `data/` prefix so `source_path` stored on each chunk is short and
portable (`contracts/foo.md`, not `E:/udemy gen ai/Advance Rag/data/contracts/foo.md`), and
(b) doubles as a validity check — if a path genuinely isn't under `data_root`, you get an
explicit error instead of nonsense output.

**What breaks if changed:** use plain string manipulation instead (e.g.
`str(source_path).split("data/")[1]`) — this looks like it works on your dev machine, but
is fragile: it'd break on Windows paths (backslashes), break if "data" ever appeared as a
substring elsewhere in the full path, and give no clear error for a genuinely-wrong path —
just silently wrong or crashing output.

**Canonical reading:** [Python docs: pathlib — PurePath.relative_to](https://docs.python.org/3/library/pathlib.html#pathlib.PurePath.relative_to)

**Exercise 1 (5 min) — see the ValueError directly:**
```python
from pathlib import Path
data_root = Path("data").resolve()
outside = Path("/tmp/not_in_data.md").resolve()
outside.relative_to(data_root)  # predict: works, or raises?
```
> **Concept & Learning:** raises `ValueError: '...' is not in the subpath of '...'` — this
> is EXACTLY the exception `infer_category` catches and re-raises as a more helpful,
> project-specific `ValueError` message; `test_infer_category_rejects_file_outside_data_root`
> asserts this behavior directly.

**Exercise 2 (10 min) — `.resolve()` matters, and here's why:**
```python
import os
os.chdir("data")  # simulate running from a different working directory
from pathlib import Path
rel = Path("company/about.md")            # a relative path, no data/ prefix
data_root = Path("../data")               # also relative, and different in form
print(rel.relative_to(data_root))         # predict: works cleanly, or not?
print(rel.resolve().relative_to(data_root.resolve()))  # now with .resolve() first
```
(Remember to `os.chdir("..")` back afterward, or just do this in a fresh script.)
> **Concept & Learning:** without `.resolve()` first, `relative_to` compares paths as
> literal strings/segments — two paths that point to the SAME actual location on disk but
> are spelled differently (relative vs absolute, `..` segments, etc.) can fail to match.
> `.resolve()` converts both sides to a canonical absolute form first, which is why
> `infer_category` and `make_chunk` both call `.resolve()` before `.relative_to()` — it's
> what makes the function robust to being called with either a relative or absolute
> `source_path`.

**Exercise 3 (10 min) — Windows backslash vs POSIX forward slash normalization:**
```python
from src.ingestion.schemas import compute_doc_id
id_backslash = compute_doc_id("contracts\\foo.md")   # Windows-style
id_forward = compute_doc_id("contracts/foo.md")       # POSIX-style
print(id_backslash == id_forward)  # predict before running
```
Then look at `compute_doc_id`'s first line (`source_path.replace("\\", "/").strip("/")`) and
explain why this test result makes sense given that code.
> **Concept & Learning:** they ARE equal — `compute_doc_id` explicitly normalizes backslashes
> to forward slashes before hashing. This matters concretely for this project: you're on
> Windows, `Path` objects render with backslashes there, but you want `doc_id` to be
> consistent regardless of what OS ingestion runs on (dev machine vs a Linux CI runner or
> Docker container) — without this normalization, the SAME file would get a DIFFERENT
> `doc_id` depending only on which OS computed it, silently breaking idempotency across
> environments.

**Checkpoint:**
*Why does `make_chunk` call `.replace("\\", "/")` on `source_path_str` AFTER computing it
via `relative_to`, given that `compute_doc_id` already does its own backslash normalization
internally?*

<details><summary>Reveal</summary>
Two different concerns being normalized for two different purposes. `compute_doc_id`
normalizes internally purely so its HASH OUTPUT is OS-independent. `make_chunk` separately
normalizes `source_path_str` (the human-readable string stored on the chunk itself, used for
citations) so that field's DISPLAYED VALUE is also always POSIX-style
(`contracts/foo.md`), consistent and portable, regardless of which OS ingestion ran on —
without this, a chunk's `source_path` field could literally look different (backslashes)
depending on what machine built the index, even though its `doc_id` would still correctly
match. Two related but separate normalization points, each guarding a different piece of
data.
</details>

---

### 1.8 Regex title extraction with a fallback (`extract_title`)

**Definition:** `re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)` finds the first markdown
H1 heading line; if none exists, a caller-supplied fallback value is used instead.

**Why here:** every real file in `data/` (confirmed in Step 2.1's testing) starts with a
`# Title` line, but the function is written defensively — NOT assuming that will always be
true — so a future data source without H1 headings degrades gracefully (falls back to
filename) instead of crashing ingestion entirely.

**What breaks if changed:** remove the fallback parameter and just `return match.group(1)`
unconditionally — calling `extract_title` on any file without an H1 (or on any raw text
snippet, e.g. mid-chunking) raises `AttributeError: 'NoneType' object has no attribute
'group'` — a crash instead of a graceful degradation, and `test_extract_title_falls_back_
when_no_h1` would fail.

**Canonical reading:** [Python docs: re — regex HOWTO](https://docs.python.org/3/howto/regex.html) and [re.MULTILINE flag](https://docs.python.org/3/library/re.html#re.MULTILINE)

**Exercise 1 (5 min) — see why `re.MULTILINE` is necessary:**
```python
import re
text = "Some intro line.\n# The Real Title\nMore text."
without_multiline = re.search(r"^#\s+(.+)$", text)
with_multiline = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
print(without_multiline)
print(with_multiline.group(1) if with_multiline else None)
```
Predict both outputs before running.
> **Concept & Learning:** without `re.MULTILINE`, `^` and `$` only match the very start/end
> of the ENTIRE string, not the start/end of each line — so a heading that isn't literally
> the first character of the whole text is invisible to the regex. `re.MULTILINE` makes `^`/
> `$` match at every line boundary instead, which is essential here since real files often
> have content before or around the heading in ways you can't fully predict.

**Exercise 2 (10 min) — test the regex against a real file with an edge case:**
Pick any real file in `data/employees/` and check: does its first line start with exactly
`# ` (hash, space)? Try a markdown variant like `##Title` (no space) or `#Title` (no space at
all) as a scratch string through `extract_title` — does it match?
> **Concept & Learning:** `\s+` requires at least one whitespace character after the `#` —
> `#Title` (no space) does NOT match this regex and would silently fall through to the
> fallback value, even though many markdown renderers WOULD treat `#Title` as a valid H1.
> This is a real edge case worth knowing about your own regex's exact boundaries rather than
> assuming it's more lenient than it is.

**Exercise 3 (10 min) — what about a file with an H1 that ISN'T on line 1?**
```python
from src.ingestion.schemas import extract_title
text = "---\ndate: 2024-01-01\n---\n\n# Actual Title\n\nBody text."
print(extract_title(text, fallback="fallback-used"))
```
Predict, then run — and cross-check against a real file to see if any of your 82 markdown
files have anything (blank lines, metadata) before their H1.
> **Concept & Learning:** because of `re.MULTILINE`, the H1 is found regardless of what
> precedes it on earlier lines — this specific test demonstrates the function correctly
> handles a frontmatter-like preamble even though NONE of the current real files in `data/`
> happen to have one; the defensive design pays for itself the moment a new file WITH
> preamble content gets added later.

**Checkpoint:**
*If a file's first H1 heading were `## Subtitle` (an H2, two hashes) appearing before any H1
at all, what would `extract_title` return?*

<details><summary>Reveal</summary>
The fallback value, NOT the H2 text — the regex `^#\s+(.+)$` matches a line starting with
exactly one `#` followed by whitespace. A line starting with `##` does technically START
with `#`, but the very next character is another `#`, not whitespace, so `\s+` fails to
match right after the first `#` — the regex engine does NOT match this line at all (it
doesn't "settle" for the H2). This is worth verifying yourself in a REPL rather than trusting
this explanation blindly, since regex behavior at boundaries like this is exactly where
intuition most often goes wrong.
</details>

---

### 1.9 Approximate estimation vs exact computation (`count_tokens_approx`)

**Definition:** a cheap heuristic (`len(text) // 4`) standing in for an exact, tool-dependent
computation (a real tokenizer's token count), deliberately trading precision for
simplicity/portability.

**Why here:** documented at length in the function's own docstring — real tokenization
requires a specific tokenizer (e.g. `tiktoken`), which couples this module to one provider
and can require network access on first use. Since this schema module is meant to be usable
offline/provider-agnostically, and the token count here is only used for rough chunk-sizing
sanity checks (not exact context-window budgeting), the approximation is judged "good
enough for now."

**What breaks if changed:** nothing breaks in THIS module if you swap in real `tiktoken`
counts — but you'd add a new dependency, and `Settings`/ingestion code that currently runs
fully offline would need network access (or a pre-cached encoding file) the first time it
runs a fresh environment — worth doing deliberately, not accidentally.

**Canonical reading:** [tiktoken (OpenAI)](https://github.com/openai/tiktoken) — the canonical example of an exact tokenizer this heuristic stands in for.

**Exercise 1 (5 min) — measure the heuristic's actual behavior:**
```python
from src.ingestion.schemas import count_tokens_approx
print(count_tokens_approx(""))        # predict: 0, or something else?
print(count_tokens_approx("a"))       # 1 character
print(count_tokens_approx("a" * 4))   # exactly 4 characters
print(count_tokens_approx("a" * 3))   # 3 characters — integer division!
```
> **Concept & Learning:** note `count_tokens_approx("")` returns `1`, not `0` (because of
> the `max(1, ...)` floor) — a deliberate choice so a chunk is never reported as "0 tokens,"
> which could cause confusing downstream division-by-zero or "empty chunk" bugs. Also note
> `"aaa"` (3 chars) still gives `0 // 4 = 0`, floored to `1` — integer division truncates,
> it doesn't round, worth knowing when reasoning about exact boundary values.

**Exercise 2 (15 min) — install `tiktoken` and measure the REAL error on your own corpus:**
```bash
pip install tiktoken
```
```python
import tiktoken
from src.ingestion.schemas import discover_markdown_files, count_tokens_approx
from pathlib import Path

enc = tiktoken.get_encoding("cl100k_base")
data_root = Path("data")
errors = []
for f in discover_markdown_files(data_root):
    text = f.read_text(encoding="utf-8")
    real = len(enc.encode(text))
    approx = count_tokens_approx(text)
    errors.append(abs(real - approx) / real)

print(f"mean error: {sum(errors) / len(errors):.1%}, max error: {max(errors):.1%}")
```
> **Concept & Learning:** this is the actual due-diligence exercise NOTES.md pointed you at
> — you get a REAL, measured error percentage across your REAL 82-file corpus, not a guess.
> Whatever number you get, you now have data-backed grounds to decide "is char/4 good
> enough for chunk-sizing, or do we need the real tokenizer" instead of debating it in the
> abstract.

**Exercise 3 (10 min) — does the error vary by content type?**
Rerun the measurement from Exercise 2, but separately bucket results by category
(`company`, `contracts`, `employees`, `products` — use `infer_category` to bucket). Is the
approximation more or less accurate for, say, contracts (likely dense legal prose) vs
employee bios (likely more conversational)?
> **Concept & Learning:** the char/4 heuristic is an ENGLISH-TEXT AVERAGE — it's not
> uniformly accurate across all writing styles (technical/legal text with long words and
> punctuation-heavy formatting tokenizes differently than conversational prose). Discovering
> whether YOUR specific corpus's categories have meaningfully different error rates is real,
> practical evidence for deciding whether the current one-size-fits-all heuristic is
> sufficient, or whether some categories need special-casing later.

**Checkpoint:**
*Would `count_tokens_approx` give the same result for a 400-character chunk of English
prose and a 400-character chunk of Python code?*

<details><summary>Reveal</summary>
Yes, identically — `len(text) // 4` only looks at character COUNT, completely blind to
content type. But real tokenizers behave very differently across content types (code often
tokenizes LESS efficiently than prose per-character, due to symbols, indentation, and
identifiers rarely matching common subword units) — meaning the approximation's error margin
is likely worse for code-like content than for prose, a real limitation worth knowing if this
knowledge base ever ingests code snippets or config files alongside markdown prose.
</details>

---

### 1.10 `make_chunk` as the single composition/assembly point

**Definition:** one function that calls every other utility function in the right order and
assembles their outputs into a single `DocumentChunk` — the only sanctioned way to construct
one.

**Why here:** every ID-consistency guarantee this module provides (doc_id matches its
source_path, chunk_id matches its doc_id+index+text) depends entirely on ALL of them being
computed together, from the same inputs, in one place. If chunk construction were scattered
across multiple call sites (e.g. the future chunking module calling `DocumentChunk(...)`
directly), each site would need to correctly reimplement the ID-computation calls in the
right order — any one of them getting it slightly wrong (e.g. forgetting to normalize the
path, or passing the wrong `doc_id` into `compute_chunk_id`) would silently break the whole
idempotency guarantee, in a way that might not surface until a duplicate shows up in Qdrant
much later.

**What breaks if changed:** have a future module call `DocumentChunk(chunk_id="whatever",
...)` directly instead of going through `make_chunk` — nothing PREVENTS this at the type
level (the dataclass constructor is public), so it's a discipline/convention, not an
enforced rule — worth noting as a real gap (see the checkpoint).

**Canonical reading:** No single canonical doc — this is the general "constructor function /
factory function" pattern, related to the [Factory Method](https://refactoring.guru/design-patterns/factory-method) concept from OOP design patterns literature (used loosely here, not the full GoF pattern).

**Exercise 1 (10 min) — trace the exact call order by hand:**
Read `make_chunk`'s body line by line and write down, in your own words, the order
operations happen in (relative path computed → doc_id computed → chunk_id computed →
category inferred → DocumentChunk constructed). Then check: does `category` inference depend
on `doc_id` or `chunk_id` at all, or is it fully independent?
> **Concept & Learning:** `infer_category` is called completely independently — it only
> needs `source_path`/`data_root`, nothing from the ID-computation steps. This means the
> ORDER of `doc_id`/`chunk_id` computation vs the `infer_category` call doesn't actually
> matter functionally (only `chunk_id` genuinely depends on `doc_id` having been computed
> first) — a good exercise in separating "this order is REQUIRED by data dependencies" from
> "this order is just how the code happens to read top-to-bottom."

**Exercise 2 (10 min) — bypass `make_chunk` and construct a chunk directly, badly:**
```python

from src.ingestion.schemas import DocumentChunk

import sys
from pathlib import Path

# 1. Convert the target folder path to an absolute path
target_folder = Path(r"E:\udemy gen ai\Advance Rag\src\ingestion")

# 2. Append that folder path to Python's search list
sys.path.append(str(target_folder))

# 3. Import your function normally
from schemas import DocumentChunk

bad_chunk = DocumentChunk(
    chunk_id="not-a-real-hash",
    doc_id="also-fake",
    source_path="contracts/foo.md",
    category="contracts",
    title="Foo",
    chunk_index=0,
    text="some text",
    char_count=999,       # deliberately WRONG — doesn't match len("some text")
    approx_token_count=1,
)
print(bad_chunk.char_count, len(bad_chunk.text))  # do these match? does anything stop this?
```
> **Concept & Learning:** this SUCCEEDS with no error at all — nothing in `DocumentChunk`
> itself validates that `char_count` actually equals `len(text)`, or that `chunk_id` is
> actually a valid hash of the other fields. `make_chunk` is a CONVENTION that produces
> consistent chunks, not a type-level GUARANTEE — the dataclass itself would happily accept
> internally-inconsistent data if something bypassed the factory function. This is a real,
> current limitation worth understanding (see checkpoint for how you might close this gap).

**Exercise 3 (10 min) — write a validation function that would catch the bypass:**
Write a small function `def validate_chunk(chunk: DocumentChunk) -> bool:` that checks
`chunk.char_count == len(chunk.text)` and `chunk.chunk_id ==
compute_chunk_id(chunk.doc_id, chunk.chunk_index, chunk.text)`, and run it against both the
`bad_chunk` from Exercise 2 and a properly-constructed chunk from `make_chunk`.
> **Concept & Learning:** you've just built the missing piece — a way to VERIFY the
> invariants `make_chunk` is supposed to guarantee, independent of whether construction went
> through the "correct" path. This is a real, useful pattern (a validation/invariant-checking
> function) worth considering adding to the actual module, e.g. as a `__post_init__` check —
> which would make the guarantee enforced rather than merely conventional. Worth raising as a
> genuine follow-up improvement.

**Checkpoint:**
*Does anything in the current code actually PREVENT constructing a `DocumentChunk` with an
inconsistent `chunk_id` that doesn't match its own `doc_id`/`chunk_index`/`text`?*

<details><summary>Reveal</summary>
No — as Exercise 2 demonstrates directly, `DocumentChunk`'s constructor has no validation
logic (no `__post_init__` that checks the hash matches). The consistency guarantee this
whole module is built around is currently a matter of DISCIPLINE (always go through
`make_chunk`), not something the type system or runtime enforces. This is a legitimate,
identified gap — closing it with a `__post_init__` validation check (as sketched in
Exercise 3) would be a reasonable, concrete follow-up improvement, and noticing this gap
yourself, by trying to break it, is exactly the kind of understanding this exercise is
meant to build.
</details>

---

## 2. Validating it's ACTUALLY working (not just "no errors")

Direct validation against your real project (run every one of these yourself):

| What to check | Command / snippet | What "working" looks like |
|---|---|---|
| Full corpus discovery works | `python -c "from pathlib import Path; from src.ingestion.schemas import discover_markdown_files; print(len(discover_markdown_files(Path('data'))))"` | Prints `82` — matches the file count from Step 2.1's initial exploration |
| No checkpoint pollution leaked through | `python -c "from pathlib import Path; from src.ingestion.schemas import discover_markdown_files; print(any('.ipynb_checkpoints' in p.parts for p in discover_markdown_files(Path('data'))))"` | Prints `False` |
| Category coverage is complete | `python -c "from pathlib import Path; from src.ingestion.schemas import discover_markdown_files, infer_category; root=Path('data'); print(sorted({infer_category(p, root) for p in discover_markdown_files(root)}))"` | `['company', 'contracts', 'employees', 'products']` |
| IDs are actually 16 hex characters | Inspect any real chunk's `.chunk_id`/`.doc_id` in a debugger or `print()` | 16-character lowercase hex string, e.g. `a3f9c1b7e2d84f01` |
| Idempotency holds across a full corpus pass, not just one file | Loop `make_chunk` over ALL 82 files (chunk_index=0, whole-file text) twice, into two lists, and assert every corresponding pair of IDs matches | All 82 pairs equal — proves idempotency isn't a one-file fluke |
| Test suite passes | `pytest tests/ingestion/ -v` | 14 passed |
| Static checks clean | `ruff check src/ingestion tests/ingestion && ruff format --check src/ingestion tests/ingestion && mypy src/ingestion` | All three report clean |

**Failure-mode drills** (predict the outcome before running):

1. **Delete `data/company/about.md` temporarily** (move it elsewhere, don't actually
   delete), rerun `discover_markdown_files` — confirm it now returns 81, and that nothing
   crashes just because a previously-known file vanished (this module doesn't cache or
   assume a fixed file list).
2. **Create an empty `data/empty_category/` folder with no `.md` files inside it** — confirm
   `discover_markdown_files` simply doesn't surface it (an empty category directory is
   invisible to `rglob("*.md")`, not an error).
3. **Create `data/onefile.md` directly in `data/` (no category subfolder)** — run
   `infer_category` on it directly and confirm it raises the expected `ValueError` with a
   helpful message (this is `test_infer_category_rejects_file_outside_data_root`'s sibling
   scenario, worth reproducing by hand once).

---

## 3. Visualizing it working

**Data flow, as this module actually implements it** (not a generic ETL diagram):

```
data/<category>/<file>.md  (82 real files on disk)
        │
        ▼
discover_markdown_files(data_root)          ── excludes .ipynb_checkpoints, .git, __pycache__
        │  (list[Path], sorted, real files only)
        ▼
  for each file:
        │
        ├── text = file.read_text()
        ├── title = extract_title(text, fallback=file.stem)     ── regex, first H1
        │
        ▼
  [ eventually: chunking strategy splits `text` into N pieces — NOT built yet ]
        │
        ▼
  for each (chunk_index, chunk_text):
        │
        ▼
    make_chunk(source_path, data_root, title, chunk_index, chunk_text)
        │
        ├── relative_path = source_path.resolve().relative_to(data_root.resolve())
        ├── doc_id = compute_doc_id(relative_path)         ── hash of PATH only
        ├── chunk_id = compute_chunk_id(doc_id, index, chunk_text)  ── hash of doc_id+index+TEXT
        ├── category = infer_category(source_path, data_root)      ── top-level folder name
        ├── char_count = len(chunk_text)
        ├── approx_token_count = count_tokens_approx(chunk_text)   ── len//4 heuristic
        │
        ▼
    DocumentChunk(...)   ── frozen, slots — immutable from here on
        │
        ▼
  [ eventually: embedding → Qdrant upsert (by chunk_id) → Neo4j linking (by doc_id) ]
        (not built yet — this module's output is exactly what those steps will consume)
```

**Tools worth using for deeper visibility:**
- **A plain Python REPL, run from the repo root** (`python`, then `from src.ingestion.schemas
  import *`) — the single most useful tool for this module specifically, since there's no
  server/container to inspect; everything is pure, side-effect-free functions you can call
  directly and print the result of.
- **`pytest --collect-only -q tests/ingestion/`** — lists every test without running them,
  useful for seeing at a glance which invariants are already covered vs which of your own
  exercises above would make good NEW permanent tests to add.
- **`python -m pdb` or your editor's debugger, breakpointed inside `make_chunk`** — step
  through the exact order of operations from Exercise 1.10-1 interactively, watching each
  local variable (`relative_path`, `doc_id`, `chunk_id`, `category`) populate in real time.

**One end-to-end "watch it work" exercise, tying validation + visualization together:**

1. Open a Python REPL at the repo root.
2. Run `from pathlib import Path; from src.ingestion.schemas import *; root = Path("data")`.
3. `files = discover_markdown_files(root)` — print `len(files)`, confirm 82.
4. Pick one real, interesting file: `f = root / "contracts" / "Contract with DriveSmart Insurance for Carllm.md"`.
5. `text = f.read_text(encoding="utf-8")` — print `len(text)` and the first 100 characters.
6. `title = extract_title(text, fallback=f.stem)` — print it, confirm it's a real, sensible
   contract title (not a fallback).
7. `chunk = make_chunk(source_path=f, data_root=root, title=title, chunk_index=0, text=text[:500])`
   — print the WHOLE chunk (`print(chunk)`) and read every field's actual value against what
   you'd expect from steps 4-6.
8. Now do steps 5-7 AGAIN for the exact same file — compare the new `chunk.chunk_id` and
   `chunk.doc_id` against the first run's. They should be byte-for-byte identical strings —
   you're watching idempotency happen live, not trusting a test's assertion about it.
9. Finally, change ONLY the `text=text[:500]` slice to `text=text[:501]` (one more
   character) and rerun `make_chunk` — watch `chunk_id` change while `doc_id` stays
   identical, live proof of the content-vs-path identity split from section 1.5, on data you
   picked yourself rather than a canned example.

If you can predict, before running each of these 9 steps, exactly what will print — and
you're right — you've internalized this module for real, not just "the tests pass."

