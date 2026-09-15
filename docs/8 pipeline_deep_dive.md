# Pipeline Deep Dive: Reverse-Engineering `src/ingestion/pipeline.py`

A self-contained learning path for Phase 2, Microstep 4:
[`src/ingestion/pipeline.py`](../src/ingestion/pipeline.py), validated against
[`tests/ingestion/test_pipeline.py`](../tests/ingestion/test_pipeline.py) and a real run
over the actual 76-document corpus. Written for someone who has never seen this code.

**The real run these numbers come from** (not invented — this is the actual `report.json`
produced by `python -m src.ingestion.pipeline` against `data/`):

```
Ingestion complete
  documents processed : 76
  documents failed    : 0
  chunks written      : 214
  avg chunk chars     : 1417.6
  tokens  min=20 p25=84 median=200 p75=347 max=2788 mean=298.6
```
Wall-clock time: **9 minutes 12 seconds**. Keep those numbers in view while reading — section
1.9 uses them to show something genuinely wrong with the current chunking configuration.

**How to use this doc:** for every concept, run the exercise BEFORE reading the checkpoint.
Five exercises per concept, easiest → hardest, with hints for anyone who has genuinely never
met the idea before.

---

## 0. What this file does, in plain English

Every other file in `src/ingestion/` is a specialist who does exactly one job:

- `loader.py` — "give me a file path, I'll give you its text"
- `chunker.py` — "give me text, I'll cut it into meaningful pieces"
- `enrichment.py` — "give me pieces, I'll count their tokens properly"

None of them knows the others exist. `pipeline.py` is the **shift manager**: it doesn't do
any of those jobs itself, it just knows the ORDER to call people in, what to do when someone
drops something, and it writes the end-of-shift report saying what actually got done.

That's genuinely all it is. If you deleted `pipeline.py`, every specialist would still work
perfectly — you'd just have to call them yourself, in the right order, remembering the right
arguments, every single time, and you'd have no record of what happened.

**The concept map** (top to bottom — later ideas build on earlier ones):

```
The shift manager idea itself:       orchestration (no domain logic, only ordering)
Who hands the manager their tools:   dependency injection + the "composition root"
Forcing callers to name arguments:   keyword-only parameters (the bare * in a signature)
Turning objects into files:          dataclasses.asdict + json.dumps
Naming those files safely:           flattening "company/about.md" -> "company__about.md.json"
What happens when one doc breaks:    failure isolation (try/except by SPECIFIC type)
Is it safe to run twice?:            idempotency + the overwrite policy
Telling you what actually happened:  the report as a data object, not a print statement
Describing a spread of numbers:      quantiles -- and a real bug found here
Making it runnable from a terminal:  argparse + if __name__ == "__main__"
```

---

## 1. Concept-by-concept

---

### 1.1 Orchestration — the manager who does none of the work

**1. Definition (plain English):** orchestration means writing code whose ONLY job is to
call other code in the right order. It contains no real logic of its own. Think of a
conductor of an orchestra: the conductor doesn't play a single note. They know the violins
come in at bar 12, the drums at bar 30, and if the oboe player faints, the show continues.
All the actual music comes from other people.

**2. The "Why" — what problem this solves vs. the traditional approach:** the "traditional"
approach for a small project is to just... write it all in one big script, or paste the
chunking loop into whatever notebook needs it. That works once. The problem shows up the
second time: now the ordering logic (load, then chunk, then enrich, in that exact sequence,
with those exact arguments) exists in two places, and they immediately start to drift. Worse,
the *policies* — what to do on failure, whether to overwrite — get re-decided ad hoc every
time. Pulling orchestration into one file means there is exactly ONE answer to "how is
ingestion actually run," and it's a file you can read.

**3. A clean, minimal code example** — the entire shape of this file, stripped to its bones:
```python
def run_pipeline(data_root, output_dir, embedder, counter):
    all_chunks = []
    for source_path in discover_documents(data_root):      # loader's job
        document = load_document(source_path, data_root)   # loader's job
        chunks = chunker.chunk(document, data_root)        # chunker's job
        chunks = enrich_with_token_counts(chunks, counter) # enrichment's job
        write_chunks(chunks, output_dir / ...)             # the only NEW thing here
        all_chunks.extend(chunks)
    return summarise(all_chunks, failures)                 # and this
```
Notice: four of those six lines are just calling other modules. `pipeline.py` genuinely
invents almost nothing.

**4. Main benefits, in short:** (a) one authoritative answer to "how do I run ingestion";
(b) each specialist module stays independently testable because none of them imports the
others; (c) changing the ORDER (say, inserting a cleaning step between load and chunk) is a
one-line change in one file, not a hunt across notebooks.

**What breaks if removed:** nothing *breaks* — every module still works. But every caller
now has to remember the whole sequence themselves, including the non-obvious bit that
`enrich_with_token_counts` returns NEW chunks that must be reassigned. Forget that one
detail and you silently save the wrong token numbers (see concept 1.7).

**Canonical reading:** [Martin Fowler — Orchestration vs Choreography](https://martinfowler.com/articles/patterns-of-distributed-systems/) (the general pattern language); for the batch-pipeline mindset specifically, [Designing Data-Intensive Applications, Ch. 10](https://dataintensive.net/).

**5 micro-exercises:**

1. **(5 min) Count the imports.** Open `pipeline.py` and count how many things it imports
   from other `src.ingestion` modules versus how many functions it defines itself. What does
   that ratio tell you about where the real logic lives?
   *Hint:* look at lines 20–27, then count the `def` statements.

2. **(10 min) Trace one document end-to-end on paper.** Pick `data/company/about.md`. Write
   down, in order, every function that touches it from `discover_documents` to the JSON file
   appearing on disk. You should end up with 5–6 steps.
   *Hint:* follow the body of `run_pipeline` line by line; each line is roughly one step.

3. **(10 min) Do the manager's job by hand.** Without using `run_pipeline` at all, manually
   chain the steps for ONE file in a REPL:
   ```python
   from pathlib import Path
   from src.ingestion.loader import load_document
   from src.ingestion.chunker import Chunker, ChunkerConfig
   from src.ingestion.enrichment import enrich_with_token_counts
   from tests.ingestion.test_chunker import FakeEmbedder
   from tests.ingestion.test_enrichment import FakeCounter

   root = Path("data")
   doc = load_document(root / "company" / "about.md", root)
   chunks = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1)).chunk(doc, root)
   chunks = enrich_with_token_counts(chunks, FakeCounter())
   print(len(chunks), "chunks")
   ```
   You just WERE the pipeline. Now imagine doing that for 76 files.

4. **(10 min) Insert a new step.** Add a fake "cleaning" step that lowercases the document
   text before chunking (just in a scratch copy). Where's the ONE place in `run_pipeline` you
   have to touch? Compare that to how many places you'd have touched in exercise 3's manual
   approach if you'd written it out 76 times.

5. **(15 min) Prove the specialists don't know each other.** Run
   `grep -n "import" src/ingestion/loader.py src/ingestion/chunker.py src/ingestion/enrichment.py`
   — does `loader.py` import `chunker.py`? Does `chunker.py` import `enrichment.py`? Write
   one sentence explaining why keeping them ignorant of each other is what makes them
   individually testable.

**How it works internally:** there's no special mechanism at all — orchestration is a
*discipline*, not a Python feature. The "pattern" is simply the deliberate choice to have
one module whose functions do nothing but call other modules' functions. Python can't
enforce or detect this; a human decides it and maintains it.

**Checkpoint:** explain why `pipeline.py` imports from `loader`, `chunker` and `enrichment`,
but none of those three imports `pipeline` — and what would go wrong (specifically) if
`chunker.py` imported `pipeline.py`.

---

### 1.2 Dependency injection + the "composition root"

**1. Definition (plain English):** instead of `run_pipeline` creating the tools it needs, it
RECEIVES them as arguments. And there's exactly one place in the whole program where the
real tools get built: `main()`. That single place is called the **composition root**.
Analogy: a restaurant kitchen. The chefs (functions) don't drive to the farm — one person
(the manager, at the start of the day) does all the buying, and hands ingredients to whoever
needs them. If you want to test a chef, you hand them fake ingredients; you don't have to
shut down the farm.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach is for `run_pipeline` to build its own Ollama connection internally. That makes it
impossible to test without a running Ollama server — and since a full real run takes
minutes, your test suite becomes unusable. By taking `embedder` and `counter` as parameters,
`run_pipeline` can be driven entirely by fakes in `test_pipeline.py`, while `main()` is the
only function that ever touches a real network.

**3. A clean, minimal code example:**
```python
# run_pipeline RECEIVES its tools -- it never builds one:
def run_pipeline(*, data_root, output_dir, embedder, counter, config=None):
    chunker = Chunker(embedder, config)      # uses what it was given
    ...

# main() is the COMPOSITION ROOT -- the one place real tools are constructed:
def main():
    report = run_pipeline(
        embedder=build_ollama_embedder(args.ollama_url, args.embed_model),  # real
        counter=build_tiktoken_counter(),                                   # real
        ...
    )

# a test supplies fakes to the SAME function, with zero changes to it:
run_pipeline(embedder=FakeEmbedder(), counter=FakeCounter(), ...)
```

**4. Main benefits, in short:** (a) the entire pipeline is testable offline, in
milliseconds, with no Docker; (b) swapping Ollama for Cohere is a change in ONE line of
`main()`, not a hunt through the pipeline; (c) `run_pipeline` itself provably cannot make a
network call, because it has no way to create a client.

**What breaks if removed:** hardcode `build_ollama_embedder(...)` inside `run_pipeline` and
every test in `test_pipeline.py` would need Docker + Ollama + the model pulled, turning a
2-second test suite into a multi-minute one that fails whenever Docker isn't running.

**Canonical reading:** [Martin Fowler — Inversion of Control Containers and the Dependency Injection pattern](https://martinfowler.com/articles/injection.html); for "composition root" specifically, Mark Seemann's [Composition Root pattern](https://blog.ploeh.dk/2011/07/28/CompositionRoot/).

**5 micro-exercises:**

1. **(5 min) Find the composition root yourself.** Search `pipeline.py` for
   `build_ollama_embedder` and `build_tiktoken_counter` — how many times does each appear,
   and in which function? That function IS the composition root.

2. **(10 min) Drive the whole pipeline with fakes.** Run this (no Docker needed):
   ```python
   from pathlib import Path
   from src.ingestion.pipeline import run_pipeline
   from src.ingestion.chunker import ChunkerConfig
   from tests.ingestion.test_chunker import FakeEmbedder
   from tests.ingestion.test_enrichment import FakeCounter

   report = run_pipeline(
       data_root=Path("data"), output_dir=Path("/tmp/scratch_chunks"),
       embedder=FakeEmbedder(), counter=FakeCounter(),
       config=ChunkerConfig(min_chunk_chars=1),
   )
   print(report.chunks_written)
   ```
   *Hint:* this runs the FULL 76-document pipeline in seconds, because no real embedding
   happens. That speed is the entire payoff of injection.

3. **(10 min) Swap in a deliberately weird counter.** Write a counter whose `.count()`
   always returns `42`, pass it in, then open one output JSON and confirm every
   `tokens_approx` is 42. You just changed the pipeline's behaviour without editing
   `pipeline.py` at all.

4. **(10 min) Try to break the isolation.** Add
   `print("NETWORK!")` inside `build_ollama_embedder`, then run exercise 2 again. Does it
   print? Explain why not, referencing which function does and doesn't call it.

5. **(15 min) Count the composition roots in this codebase.** `build_ollama_embedder`
   (chunker.py), `build_tiktoken_counter` (enrichment.py) and `main()` (pipeline.py) all
   relate to this pattern. Which one is the TRUE composition root — the place where the
   object graph is finally assembled — and which two are just factories it calls?

**How it works internally:** again, no special Python machinery — it's just function
parameters. What makes it a *pattern* is the discipline of pushing all construction to the
outermost layer (`main()`), so that everything beneath it is a pure function of its inputs.
Python has no way to enforce this; reading `__init__`/function signatures is how you verify
it.

**Checkpoint:** explain what "composition root" means in your own words, and identify the
exact line number in `pipeline.py` where the real Ollama connection is created.

---

### 1.3 Keyword-only parameters — the bare `*` in the signature

**1. Definition (plain English):** look closely at the signature:
`def run_pipeline(*, data_root, output_dir, embedder, counter, config=None)`. That lonely
`*` means "everything after me MUST be passed by name." You cannot call
`run_pipeline(path, path2, thing, thing2)` — you're forced to write
`run_pipeline(data_root=..., output_dir=..., embedder=..., counter=...)`. Analogy: a form
where every box is labelled, versus a form that's just four blank lines and you have to
remember what goes where.

**2. The "Why" — what problem this solves vs. the traditional approach:** with positional
arguments, `run_pipeline(data_root, output_dir, embedder, counter)` — what happens if you
accidentally swap `data_root` and `output_dir`? Both are `Path` objects, so Python won't
complain, `mypy` won't complain, and the pipeline will happily try to read documents from
your output folder and write chunks into your data folder. Forcing keywords makes that class
of bug impossible to write.

**3. A clean, minimal code example:**
```python
def send(*, to: str, subject: str) -> None:
    print(f"to={to} subject={subject}")

send(to="a@b.com", subject="hi")   # fine
send("a@b.com", "hi")              # TypeError: takes 0 positional arguments but 2 were given
```

**4. Main benefits, in short:** (a) two same-typed arguments can never be silently swapped;
(b) every call site reads self-documentingly (`data_root=` right there in the call); (c) you
can safely REORDER parameters later without breaking any existing caller.

**What breaks if removed:** delete the `*` and `run_pipeline(Path("data/chunks"),
Path("data"), ...)` becomes legal — reading documents from the chunks directory and writing
output into your source data directory, with no error at all.

**Canonical reading:** [Python docs — keyword-only arguments (PEP 3102)](https://peps.python.org/pep-3102/), and the [function definition reference](https://docs.python.org/3/reference/compound_stmts.html#function-definitions).

**5 micro-exercises:**

1. **(5 min) Trigger the TypeError yourself** with the `send()` example above. Read the
   exact wording of the error — it says "0 positional arguments", which is initially
   surprising.

2. **(5 min) Find the `*` in the real file.** Open `pipeline.py`, find `run_pipeline`'s
   signature, and identify the lone `*` on its own line. Then check `write_chunks` and
   `summarise` — do THEY use keyword-only parameters? Why might the answer differ?
   *Hint:* how many same-typed arguments does each one take?

3. **(10 min) Break a real call.** In a scratch copy, try calling
   `run_pipeline(Path("data"), Path("/tmp/x"), FakeEmbedder(), FakeCounter())` positionally.
   Confirm it raises, then fix it by adding the keyword names.

4. **(10 min) Simulate the swap bug.** Temporarily remove the `*` from a scratch copy, then
   deliberately call it with `data_root` and `output_dir` reversed. What actually happens —
   an error, or something worse and quieter?

5. **(15 min) Find the other keyword-only usage in this codebase.** `make_chunk` in
   `schemas.py` also uses `*`. Count how many `str`-typed parameters it has, and argue
   whether the `*` is more or less important there than in `run_pipeline`.

**How it works internally:** at function-definition time, CPython records which parameters
appear after the bare `*` in the function's code object (`co_kwonlyargcount`). At call time,
the interpreter checks positional arguments only against parameters BEFORE the `*` — so
supplying any positional argument to a fully keyword-only function fails immediately, before
the body ever runs.

**Checkpoint:** explain why the swap-two-`Path`s bug is specifically the kind of bug that
type checkers CANNOT catch, and why keyword-only arguments can.

---

### 1.4 Serialisation — turning Python objects into a file

**1. Definition (plain English):** serialisation means converting a live Python object into
plain text you can write to disk and read back later. `dataclasses.asdict(chunk)` turns a
`DocumentChunk` into a plain dictionary; `json.dumps(...)` turns that dictionary into a
string. Analogy: flat-pack furniture. The assembled chair (a live object) can't go in an
envelope, so you break it into labelled flat parts (a dict), print the instructions (JSON
text), and post it. Someone else can rebuild it from exactly that.

**2. The "Why" — what problem this solves vs. the traditional approach:** the alternative is
`pickle`, Python's built-in "just save the object" tool. `pickle` is easier (one line, keeps
the exact class) but produces a binary blob that ONLY Python can read, that silently breaks
when you rename a field, and that can execute arbitrary code when loaded — a genuine
security issue for anything you didn't create yourself. JSON is readable by humans, by
other languages, and by every tool on earth.

**3. A clean, minimal code example:**
```python
import dataclasses, json
from pathlib import Path
from src.ingestion.schemas import make_chunk

chunk = make_chunk(source_path=Path("data/company/about.md"), data_root=Path("data"),
                   title="t", chunk_index=0, text="hello", start_char=0, end_char=5)

as_dict = dataclasses.asdict(chunk)     # object  -> plain dict
as_text = json.dumps(as_dict, indent=2) # dict    -> text
print(as_text[:120])
print(json.loads(as_text) == as_dict)   # text -> dict, round-trips exactly: True
```

**4. Main benefits, in short:** (a) the output files are readable in any text editor, so you
can eyeball chunk quality without writing code; (b) any language or tool can consume them;
(c) `asdict` walks nested structures automatically, so the `metadata` dict comes along
without special handling.

**What breaks if you use `pickle` instead:** the files become unreadable binary, you lose
the ability to inspect a chunk by opening it, and renaming any `DocumentChunk` field turns
every previously-saved file into an unloadable relic.

**Canonical reading:** [Python docs — dataclasses.asdict](https://docs.python.org/3/library/dataclasses.html#dataclasses.asdict) and [json.dumps](https://docs.python.org/3/library/json.html#json.dumps). For why `pickle` is risky: [the pickle module's own security warning](https://docs.python.org/3/library/pickle.html).

**5 micro-exercises:**

1. **(5 min) Run the example above** and confirm `json.loads(...) == as_dict` prints `True`.

2. **(5 min) Look at a REAL output file.**
   `cat "data/chunks/company__about.md.json"` (or open it in an editor) — find the
   `metadata` object inside and confirm you can see `start_char`, `end_char` and
   `tokens_approx` with your own eyes.

3. **(10 min) Break serialisation on purpose.** Try `json.dumps({"path": Path("data")})` —
   read the `TypeError`. Then explain why `DocumentChunk` serialises fine even though the
   pipeline works with `Path` objects everywhere.
   *Hint:* check what TYPE `DocumentChunk.source_path` is — a `Path` or a `str`?

4. **(10 min) Test the `ensure_ascii` flag.** `json.dumps({"x": "café"})` vs
   `json.dumps({"x": "café"}, ensure_ascii=False)` — compare the output. Which one does
   `write_chunks` use, and why does that matter for a corpus with accented names?

5. **(15 min) Rebuild a chunk from its JSON.** Load one real output file and reconstruct a
   `DocumentChunk` from a dict: `DocumentChunk(**payload[0])`. Does it work? What does that
   tell you about whether the serialisation is genuinely lossless?

**How it works internally:** `dataclasses.asdict` uses the class's `__dataclass_fields__`
metadata to walk every declared field, recursing into nested dataclasses, lists and dicts,
producing plain Python primitives. `json.dumps` then walks THAT structure and emits text,
raising `TypeError` the moment it meets a type it has no rule for (like `Path`) — which is
exactly why `DocumentChunk` stores `source_path` as a `str`, not a `Path`.

**Checkpoint:** explain the chain of conversions a single chunk goes through from live object
to bytes on disk, naming the function responsible for each step.

---

### 1.5 Filename flattening — `company/about.md` → `company__about.md.json`

**1. Definition (plain English):** the source path has a folder in it, but the output is one
flat directory — so the slash is replaced with a double underscore, folding the folder name
into the filename. Analogy: filing papers. Instead of a filing cabinet with a drawer per
department, you use one big drawer and write the department name at the start of each label:
`SALES - Q3 report`. Everything's in one place, but nothing loses its origin.

**2. The "Why" — what problem this solves vs. the traditional approach:** the traditional
approach would be to mirror the directory tree (`data/chunks/company/about.md.json`). That
works, but then listing "all chunk files" means walking subdirectories, and you can't see
the whole dataset in one `ls`. Flattening means one `ls data/chunks/` shows everything,
sorted with categories naturally grouped together, and `rm data/chunks/*.json` resets
everything in one command.

**3. A clean, minimal code example:**
```python
def output_filename(source_path: str) -> str:
    return f"{source_path.replace('/', '__')}.json"

print(output_filename("company/about.md"))     # company__about.md.json
print(output_filename("contracts/Contract with DriveSmart Insurance for Carllm.md"))
# contracts__Contract with DriveSmart Insurance for Carllm.md.json
```

**4. Main benefits, in short:** (a) the whole dataset is visible in one directory listing;
(b) the category stays readable in the filename, so sorting groups by category for free;
(c) no nested-directory creation logic needed when writing.

**What breaks if you don't replace the slash:** `output_dir / "company/about.md.json"` would
try to write into a `company/` SUBDIRECTORY that was never created, raising
`FileNotFoundError` — because `Path.write_text` does not create missing parent directories.

**Canonical reading:** [Python docs — str.replace](https://docs.python.org/3/library/stdtypes.html#str.replace); for why path separators matter across OSes, [pathlib's PurePath docs](https://docs.python.org/3/library/pathlib.html#pure-paths).

**5 micro-exercises:**

1. **(5 min) Run `output_filename` on three different paths**, including one with spaces
   (the contracts folder is full of them). Confirm spaces survive fine — they're legal in
   filenames.

2. **(5 min) Check the real output directory.** `ls data/chunks/ | head -20` — confirm you
   can tell each file's original category just by reading its name.

3. **(10 min) Reproduce the failure.** In a scratch script, try writing to a path with an
   uncreated subdirectory:
   ```python
   from pathlib import Path
   (Path("/tmp/scratch_out") / "sub/file.json").write_text("x")
   ```
   *Hint:* create `/tmp/scratch_out` first but NOT `sub`. Read the exact error — that's what
   flattening avoids.

4. **(10 min) Write the reverse function.** Can you convert
   `company__about.md.json` back to `company/about.md`? Write `original_path(filename)` and
   test it round-trips. Now find a filename where your reverse function would break.
   *Hint:* what if a real folder or file name already contained `__`?

5. **(15 min) Compare with a hashing approach.** Some pipelines name output files by a hash
   of the source path (`a3f9c1b7.json`) instead. List two advantages of the readable name
   and one advantage of the hash. (Hint for the hash side: think about very long paths and
   OS filename length limits.)

**How it works internally:** `str.replace` scans left to right building a new string
(strings are immutable in Python, so it can't edit in place). `Path.__truediv__` (the `/`
operator on paths) then joins the directory and this flat name — and because the name
contains no separator, the result is guaranteed to be a direct child of `output_dir`.

**Checkpoint:** explain what would happen, step by step, if `output_filename` returned the
path unchanged (with its `/` intact) and the pipeline tried to write 76 files.

---

### 1.6 Failure isolation — catching SPECIFIC exceptions

**1. Definition (plain English):** the loop wraps each document in `try/except`, so one
broken file doesn't kill the run. But crucially it catches only *named* error types
(`DocumentLoadError, OSError, ValueError`) — not `except Exception`, which would swallow
everything. Analogy: a factory line with a "set aside faulty items" bin. A dented can goes
in the bin and the line keeps moving. But if the *machine itself* catches fire, you do NOT
want that quietly tossed in the bin — you want the line to stop and alarms to go off.

**2. The "Why" — what problem this solves vs. the traditional approach:** two traditional
approaches, both worse. (a) No try/except: one corrupt PDF at document 40 throws away the
embedding work already done for the first 39 — minutes of Ollama time, gone. (b)
`except Exception`: now a genuine bug in YOUR code (a typo, a `None` where a list was
expected) gets silently filed as "document failed", and you'll spend an afternoon wondering
why 30 documents "failed" when actually your own code is broken. Catching specific types
keeps expected failures isolated while letting real bugs crash loudly.

**3. A clean, minimal code example:**
```python
for source_path in discover_documents(data_root):
    try:
        document = load_document(source_path, data_root)
        ...
    except (DocumentLoadError, OSError, ValueError) as exc:
        # expected, per-document problems -> record and continue
        failures.append(DocumentFailure(source_path=str(source_path), error=str(exc)))
    # NOTE: a TypeError or AttributeError is NOT caught -- it crashes, on purpose
```

**4. Main benefits, in short:** (a) one bad file costs you one file, not the whole run; (b)
programming bugs still crash immediately and visibly instead of masquerading as data
problems; (c) every failure is recorded with its path and message, so the report tells you
exactly what to go look at.

**What breaks if you widen it to `except Exception`:** verified reasoning — a `TypeError`
from a bug in `write_chunks` would be caught per-document, so all 76 documents would
"fail" with a confusing message and the run would report success-with-failures rather than
crashing and pointing at the real bug.

**Canonical reading:** [Python docs — Errors and Exceptions tutorial](https://docs.python.org/3/tutorial/errors.html), particularly the section on handling multiple exception types.

**5 micro-exercises:**

1. **(5 min) See the skip-and-continue in action.** Drop a fake broken PDF into your corpus:
   `echo "not a pdf" > data/company/broken.pdf`, run the fake-embedder pipeline from
   exercise 1.2-2, and check `report.documents_failed`. **Then delete the file.**

2. **(10 min) Read the recorded failure.** After exercise 1, inspect
   `report.failures[0].source_path` and `.error` — does the message tell you enough to
   actually fix the problem?

3. **(10 min) Prove a real bug is NOT swallowed.** In a scratch copy of `pipeline.py`, add
   `raise TypeError("simulated bug")` inside the `try` block. Run it — does the pipeline
   record 76 failures, or crash immediately? Explain the result.

4. **(10 min) Now widen the catch and compare.** Change the `except` clause to
   `except Exception as exc:` in that same scratch copy and rerun with the simulated
   `TypeError` still there. Confirm the behaviour flips — this is the whole lesson in one
   experiment.

5. **(15 min) Decide which exceptions belong in the list.** `DocumentLoadError` and `OSError`
   are clearly per-document problems. Why is `ValueError` in there? *Hint:* find what
   `infer_category` raises in `schemas.py` when a file sits somewhere unexpected. Is that a
   data problem or a code problem?

**How it works internally:** when an exception is raised, Python walks up the call stack
looking for the nearest enclosing `except` clause whose type tuple matches the exception's
class (or any of its parent classes). `except (A, B, C)` matches if the exception is an
instance of A, B, or C — anything else keeps propagating up and, if nothing catches it,
terminates the program with a traceback. `Exception` is a parent of nearly everything, which
is exactly why catching it is so indiscriminate.

**Checkpoint:** explain the difference between "a document failed" and "my code is broken,"
and show how the `except` line in `run_pipeline` encodes that distinction.

---

### 1.7 Idempotency and the overwrite policy

**1. Definition (plain English):** idempotent means "running it twice gives the same result
as running it once." This pipeline achieves that the blunt way: it re-chunks every document
every time and overwrites the output. Analogy: repainting a wall versus adding a coat. Every
run repaints from scratch — slower, but you always know exactly what colour the wall is.
Adding coats is faster, but after three runs nobody knows what's underneath.

**2. The "Why" — what problem this solves vs. the traditional approach:** the obvious
optimisation is "skip documents whose output already exists." It's much faster — and it's
silently WRONG the moment you change a chunking parameter. Say you re-run with
`--threshold 0.5`: the skipped files keep boundaries from the OLD threshold, so your dataset
now contains two different chunking strategies mixed together, with nothing recording which
chunk came from which. Overwriting everything means the output always matches the current
code and current config, with no archaeology required.

**3. A clean, minimal code example:**
```python
# The entire policy, in one line -- there is no "if exists: skip":
write_chunks(chunks, output_dir / output_filename(document.source_path))

# Proven by test_rerun_overwrites_rather_than_appending:
first  = run_pipeline(...)
second = run_pipeline(...)
assert first.chunks_written == second.chunks_written   # not doubled
assert len(list(out.glob("*.json"))) == 3              # not 5
```

**4. Main benefits, in short:** (a) the dataset always reflects the current code and config,
never a mixture; (b) re-running after a crash is always safe — no half-finished state to
reason about; (c) zero staleness-detection logic, which is a category of bug that simply
cannot exist here.

**What breaks if you add "skip if exists":** you gain speed and lose correctness on every
config change. The failure is invisible: no error, just a dataset whose chunks were produced
by two different sets of rules.

**Canonical reading:** [Wikipedia — Idempotence](https://en.wikipedia.org/wiki/Idempotence) for the concept; for the incremental-build problem this deliberately avoids, the [GNU make manual on how targets are decided stale](https://www.gnu.org/software/make/manual/html_node/Rules.html).

**5 micro-exercises:**

1. **(5 min) Prove idempotency yourself.** Run the fake-embedder pipeline twice into the
   same output directory and compare `report.chunks_written` both times.

2. **(5 min) Count the files after two runs.** `ls /tmp/scratch_chunks | wc -l` after run 1
   and after run 2 — identical, or growing?

3. **(10 min) Verify byte-identical output.** Hash one output file after each run:
   ```python
   import hashlib
   print(hashlib.sha256(Path("/tmp/scratch_chunks/company__about.md.json").read_bytes()).hexdigest())
   ```
   Same hash both times? What does that prove beyond just "same count"?

4. **(10 min) Simulate the staleness bug.** Run once with
   `ChunkerConfig(breakpoint_threshold_amount=1.5)`, then manually copy one output file
   aside. Run again with `0.5`. Diff the old copy against the new file — THAT difference is
   exactly what "skip if exists" would have silently preserved.

5. **(15 min) Design the correct incremental version.** Sketch (don't implement) what a
   staleness check would need to consider to be safe. *Hint:* source file modification time
   is not enough — what else changed between your two runs in exercise 4, and how would you
   detect it? (Look up "content-addressed build cache" if you want the real answer.)

**How it works internally:** `Path.write_text` opens the file in `"w"` mode, which
*truncates* an existing file to zero length before writing. There's no append, no merge, no
check — the previous content is simply gone. That truncation is the entire mechanism behind
this policy.

**Checkpoint:** explain why "skip if output exists" is safe for a pipeline whose behaviour
never changes, but unsafe for this one specifically — name the thing that can change here
that a file-existence check cannot see.

---

### 1.8 The report as a data object — observability

**1. Definition (plain English):** instead of just printing numbers as it goes, the pipeline
builds a `PipelineReport` object and returns it (and saves it as `report.json`). Analogy:
the difference between a chef shouting "done!" across the kitchen and a chef writing a
delivery docket you can file, compare against yesterday's, and hand to someone else.
Shouting disappears; the docket persists.

**2. The "Why" — what problem this solves vs. the traditional approach:** printing is the
traditional approach and it's fine — until you want to answer "did last night's run produce
more chunks than tonight's?" With print-only, that answer lives in terminal scrollback
you've already closed. As a returned object, tests can assert on it
(`assert report.documents_failed == 1`); as `report.json`, two runs can be diffed
numerically. The same data serves three audiences: the human reading the terminal, the test
suite, and future-you comparing configurations.

**3. A clean, minimal code example:**
```python
@dataclasses.dataclass(frozen=True, slots=True)
class PipelineReport:
    documents_processed: int
    documents_failed: int
    chunks_written: int
    avg_chunk_chars: float
    token_distribution: TokenDistribution | None
    failures: list[DocumentFailure]

report = run_pipeline(...)          # returned  -> tests can assert on it
print(format_report(report))        # rendered  -> human reads it
# and written to data/chunks/report.json -> diffable across runs
```

**4. Main benefits, in short:** (a) tests assert on real outcomes instead of scraping
stdout; (b) two runs can be compared field by field; (c) failures are listed individually
with paths and messages, so the report is actionable rather than just a count.

**What breaks if it only printed:** `test_report_counts_documents_and_chunks` and six other
tests in `test_pipeline.py` could not exist in their current form — they'd have to capture
and parse stdout, which is brittle and breaks whenever you reword a message.

**Canonical reading:** no single canonical source for "return a report object", but the idea
sits under [observability](https://en.wikipedia.org/wiki/Observability_(software)); the
dataclass mechanics are the same [dataclasses docs](https://docs.python.org/3/library/dataclasses.html) used throughout this project.

**5 micro-exercises:**

1. **(5 min) Read the real report.**
   `cat data/chunks/report.json` — identify every top-level key and say in one sentence what
   each tells you.

2. **(10 min) Assert on it like a test does.** Run the fake pipeline, then
   `assert report.chunks_written > 0` and `assert report.documents_failed == 0`. Notice you
   never had to read a single line of printed text.

3. **(10 min) Diff two runs.** Run with `--threshold 1.5`, copy `report.json` aside, run
   with `--threshold 0.5`, then `diff` the two files. Which numbers moved, and in which
   direction? Does that match your prediction from the chunker deep dive?

4. **(10 min) Separate the data from its rendering.** Find `format_report` and confirm it
   does no computation at all — only formatting. Why is keeping "what happened" separate
   from "how it's displayed" useful here?

5. **(15 min) Add a field.** Add `avg_tokens: float` to `PipelineReport`, populate it in
   `summarise`, and show it in `format_report`. Run the tests — which existing tests break,
   and why does adding a field to a frozen dataclass require touching the constructor call?

**How it works internally:** `PipelineReport` is a frozen dataclass, so it's a fixed
snapshot — it cannot be mutated after `summarise` builds it. `dataclasses.asdict` then
converts the whole nested structure (including the `TokenDistribution` and the list of
`DocumentFailure` objects) into plain dicts and lists in one call, which is what makes
writing it as JSON a single line.

**Checkpoint:** name the three different consumers of the same report data, and explain why
returning an object serves all three better than printing would serve any one of them.

---

### 1.9 Quantiles — describing a spread (and a real bug found here)

**1. Definition (plain English):** an average tells you the middle, but hides the shape.
Quantiles cut your sorted data into quarters: p25 is the value a quarter of the way up, the
median is halfway, p75 is three quarters up. Analogy: describing a class's exam results. "The
average is 60" is true whether everyone scored exactly 60, or half scored 20 and half scored
100 — wildly different situations. Quartiles distinguish them.

**2. The "Why" — what problem this solves vs. the traditional approach:** the task spec only
asked for a "token distribution", and the easy reading is "print the average." But for
chunking, the SHAPE is exactly what you're tuning: a mean of 250 tokens is produced both by a
healthy corpus and by one made of 50-token fragments plus 900-token walls of text — and only
the second embeds badly. The quartiles make that difference visible at a glance.

**3. A clean, minimal code example** — including the real bug this code hit:
```python
import statistics

data = [6, 10]                     # only two chunks, min=6 max=10
print(statistics.quantiles(data, n=4))                        # [5.0, 8.0, 11.0]  <-- !!
print(statistics.quantiles(data, n=4, method="inclusive"))    # [7.0, 8.0, 9.0]
```
The DEFAULT method reports p25=**5** and p75=**11** — values below the minimum and above the
maximum of a dataset whose range is 6 to 10. That is not a rounding artefact; the default
(`"exclusive"`) deliberately extrapolates because it's estimating an unseen *population*.
This pipeline describes the chunks that actually exist, so it uses `method="inclusive"`.

**4. Main benefits, in short:** (a) you can see chunk-size skew, not just its centre; (b) the
reported values are always real, in-range numbers; (c) comparing p25/p75 between two configs
tells you whether a threshold change tightened the spread or just shifted it.

**What breaks if you use the default:** on small corpora you get impossible values —
`test_token_distribution_is_ordered` catches exactly this, and it DID catch it: the first
version of this code failed with `TokenDistribution(minimum=6, p25=5, ... p75=11,
maximum=10)`.

**Canonical reading:** [Python docs — statistics.quantiles](https://docs.python.org/3/library/statistics.html#statistics.quantiles) (read the `method` parameter carefully); [Wikipedia — Quantile, on the many competing definitions](https://en.wikipedia.org/wiki/Quantile#Estimating_quantiles_from_a_sample).

**5 micro-exercises:**

1. **(5 min) Reproduce the bug.** Run the two-line example above and see p25=5 with your own
   eyes on data whose minimum is 6.

2. **(10 min) Find where it stops mattering.** Try `[6,10]`, `[6,8,10]`, `[6,6,10,10]`, and
   a list of 20 numbers. At roughly what sample size do the two methods stop disagreeing
   noticeably? *Hint:* write a loop that prints both for increasing sample sizes.

3. **(10 min) Break the real code.** In a scratch copy of `pipeline.py`, remove
   `method="inclusive"` and run `pytest tests/ingestion/test_pipeline.py -k distribution`.
   Watch the test fail — then read its assertion to see exactly which invariant it protects.

4. **(10 min) Compute p25 by hand.** For `[1,2,3,4,5,6,7,8,9]`, work out which value sits a
   quarter of the way up, then check against `statistics.quantiles(..., method="inclusive")`.
   Did your intuition match?

5. **(15 min) Read the real distribution from YOUR corpus — it is telling you something is
   wrong.** Open `data/chunks/report.json`. The actual measured values from the real run:
   ```
   min=20  p25=84  median=200  p75=347  max=2788   (tokens)
   avg_chunk_chars = 1417.6     but ChunkerConfig.target_chunk_chars = 500
   ```
   Three things to notice, all verified by direct measurement of the output files:
   - **p75 is ~4x p25** — chunk sizes vary wildly, exactly the unhealthy shape this
     `TokenDistribution` class exists to reveal.
   - **The average chunk is 2.8x the target size.** Measured directly: **149 of 214 chunks
     (69%) exceed the 500-character target**, and the largest single chunk is **13,599
     characters**, roughly 3,400 tokens — a wall of text that will embed to mush.
   - **Only 214 chunks from 76 documents** — about 2.8 chunks per document. The chunker is
     barely cutting anything.

   Now diagnose it: this is precisely the behaviour predicted in
   [docs/6 chunker_deep_dive.md](6%20chunker_deep_dive.md) sections 1.5 and 1.8 —
   `standard_deviation` thresholding at 1.5σ only cuts at *statistical outliers*, and
   `buffer_size=1` blends each sentence with its neighbours, diluting the very topic shifts
   it's looking for. Re-run with `--threshold 0.5` and compare the new `report.json` against
   the old one. Which numbers move, and does the max chunk shrink?

**How it works internally:** `statistics.quantiles` sorts the data, then computes cut points
by *interpolating* between neighbouring values. The `exclusive` method assumes your data is a
SAMPLE from a larger population and positions cut points as if unseen values exist beyond
your observed range — which is why it can return values outside `[min, max]`. The `inclusive`
method treats your data as the entire population, so every cut point is bounded by the
actual observed values.

**Checkpoint:** explain, without looking, why `method="exclusive"` returning a p25 below the
dataset's minimum is mathematically intentional rather than a library bug — and why it's
still the wrong choice for this report.

---

### 1.10 `argparse` + `if __name__ == "__main__"` — making it runnable

**1. Definition (plain English):** `argparse` reads the options you type after the command
(`--threshold 0.5`) and turns them into Python values. The `if __name__ == "__main__":` line
means "only run this when the file is executed directly, not when it's imported." Analogy: a
building with a public front door (`main()`) and internal corridors (the functions). The
`if __name__` check is the rule that the front door only opens when someone actually arrives
at the building — not every time a corridor is used.

**2. The "Why" — what problem this solves vs. the traditional approach:** without the
`__name__` guard, importing anything from `pipeline.py` — which `test_pipeline.py` does on
every test run — would immediately execute `main()`, trying to connect to Ollama and chunk
your entire corpus. Every test run would become a full production ingestion. Without
`argparse`, changing the threshold would mean editing source code instead of typing a
different flag.

**3. A clean, minimal code example:**
```python
import argparse

def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk the corpus.")
    parser.add_argument("--threshold", type=float, default=1.5)
    args = parser.parse_args()
    print("running with threshold", args.threshold)

if __name__ == "__main__":     # only when run directly
    main()
```
```bash
python -m src.ingestion.pipeline --threshold 0.5
python -m src.ingestion.pipeline --help      # argparse generates this for free
```

**4. Main benefits, in short:** (a) importing the module is completely safe and side-effect
free; (b) `--help` documentation is generated automatically from your `add_argument` calls;
(c) `type=float` and `type=Path` convert and validate input before your code ever sees it.

**What breaks if you remove the `__name__` guard:** every `import` of `pipeline.py`,
including all 13 tests in `test_pipeline.py`, would launch a real full-corpus ingestion run
against Ollama.

**Canonical reading:** [Python docs — argparse tutorial](https://docs.python.org/3/howto/argparse.html) and [`__main__` — top-level code environment](https://docs.python.org/3/library/__main__.html).

**5 micro-exercises:**

1. **(5 min) Get free documentation.** Run
   `python -m src.ingestion.pipeline --help` and read what argparse generated from the
   `add_argument` calls — note that you never wrote that help text by hand.

2. **(5 min) See type conversion work.** Run with `--threshold abc` and read the error.
   Who produced that error — your code, or argparse? What does `type=float` buy you?

3. **(10 min) Prove the `__name__` guard matters.** In a scratch file:
   ```python
   print("module-level code runs on import")
   def main(): print("main() ran")
   if __name__ == "__main__":
       main()
   ```
   Run it directly (`python scratch.py`), then import it (`python -c "import scratch"`).
   Compare what prints in each case.

4. **(10 min) Add your own flag.** Add `--limit` (an int) to `main()` that would process only
   the first N documents. You don't have to wire it into `run_pipeline` — just add it,
   print `args.limit`, and confirm `--help` picks it up automatically.

5. **(15 min) Compare `python -m` with running the file directly.** Try
   `python src/ingestion/pipeline.py` and compare with
   `python -m src.ingestion.pipeline`. One of these likely fails on imports — explain why,
   referencing `docs/4 modulenotfound_debugging_guide.md`'s section on `sys.path[0]`.

**How it works internally:** Python sets a module-level variable `__name__` for every module
it loads. When a file is run directly, its `__name__` is set to the literal string
`"__main__"`; when it's imported, `__name__` is set to the module's dotted path
(`"src.ingestion.pipeline"`). The `if` is therefore just a normal string comparison —
nothing magic, just a value the interpreter sets differently depending on how the code was
reached.

**Checkpoint:** explain what `__name__` actually contains in each of the two cases, and why
that makes the guard work.

---

## 2. Validating it's ACTUALLY working (not just "no errors")

Run every one of these against your real project.

| What to check | Command / snippet | What "working" looks like |
|---|---|---|
| Every document produced an output file | `ls data/chunks/*.json \| wc -l` | 77 = 76 documents + `report.json`. Fewer means documents failed or produced zero chunks |
| The run didn't silently skip anything | `python -c "import json; print(json.load(open('data/chunks/report.json'))['documents_failed'])"` | `0`. **This is the single most important check** — a partial dataset looks identical to a complete one on disk |
| Enrichment actually ran | Open any chunk file; compare `approx_token_count` with `metadata.tokens_approx` | Identical for every chunk. If they differ, enrichment's return value was discarded somewhere |
| Offsets point into the real source | For one chunk: `source_text[start_char:end_char].split() == chunk_text.split()` | `True` — see `docs/6 chunker_deep_dive.md` section 1.6 for why byte-equality is NOT the right test |
| Re-running is idempotent | Run twice, `sha256` the same output file both times | Identical hashes |
| Chunk sizes aren't pathological | `token_distribution` in `report.json` | p25 and p75 reasonably close to the median. **On the current real run this check FAILS:** p75 (347) is ~4x p25 (84), avg chunk is 1417 chars against a 500 target, and 69% of chunks exceed target — see section 1.9 exercise 5 |
| The pipeline is importable without side effects | `python -c "import src.ingestion.pipeline; print('ok')"` | Prints `ok` instantly, starts no ingestion, needs no Ollama |
| Full suite passes | `pytest tests/ -q` | 66 passed |
| Static checks clean | `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/` | All three clean |

**Failure-mode drills** (predict the outcome first, then run):

1. **Stop Ollama mid-run.** Start a real run, then `docker compose stop ollama` after ~10
   seconds. Does the pipeline crash, or record failures and continue? Check
   `documents_failed` afterward — is the resulting dataset distinguishable from a complete
   one without reading the report?
2. **Feed it a directory with no supported files.** Point `--data-root` at an empty temp
   directory. Does it crash on the empty case, or produce a clean zero report with
   `token_distribution: null`?
3. **Make the output directory read-only** and run. Which exception type surfaces, and does
   the `except` clause in `run_pipeline` catch it (is it an `OSError`)? Should it?

---

## 3. Visualizing it working

**Data flow, as this file actually implements it:**

```
data/<category>/*.md   (76 real files)
        │
        ▼
discover_documents(data_root)           [loader.py]
        │  list[Path], sorted, noise dirs excluded
        │
        ▼  ── for EACH path, inside try/except ──────────────────┐
        │                                                         │
   load_document()          [loader.py]     -> LoadedDocument     │
        │                                                         │
   chunker.chunk()          [chunker.py]    -> list[DocumentChunk]│  on
        │   (uses injected Embedder -> Ollama)                    │  DocumentLoadError
        │                                                         │  OSError
   enrich_with_token_counts() [enrichment.py] -> NEW chunks       │  ValueError
        │   (uses injected TokenCounter -> tiktoken)              │      │
        │                                                         │      ▼
   write_chunks()           [pipeline.py]                         │  record in
        │   data/chunks/company__about.md.json                    │  failures[]
        │                                                         │  and CONTINUE
        └─────────────────────────────────────────────────────────┘
        │
        ▼
   summarise(all_chunks, failures)   -> PipelineReport
        │
        ├── data/chunks/report.json     (machine-readable, diffable)
        └── format_report() -> stdout   (human-readable)


Where the real tools come from (the composition root):

   main()  ── build_ollama_embedder() ──> Ollama container :11434
      │     ── build_tiktoken_counter() ─> tiktoken cl100k_base (cached)
      │
      └──> run_pipeline(embedder=..., counter=...)   <- receives, never builds

   tests ──> run_pipeline(embedder=FakeEmbedder(), counter=FakeCounter())
                                     ^ same function, no network at all
```

**Tools worth using for deeper visibility:**
- **`jq`** (if installed) — `jq '.token_distribution' data/chunks/report.json` reads the
  report without opening an editor; `jq 'length' data/chunks/company__about.md.json` counts
  one document's chunks.
- **`logging.basicConfig(level=logging.INFO)`** — the pipeline's own `logger.warning` calls
  for skipped documents, plus `httpx`'s per-request logs, show each Ollama call live so you
  can watch it progress document by document.
- **`watch -n 2 'ls data/chunks | wc -l'`** (or repeatedly running it on Windows) — turns a
  silent multi-minute run into a visible progress counter climbing toward 77.

**One end-to-end "watch it work" exercise:**

1. **Terminal 1** — watch output appear in real time:
   `ls data/chunks | wc -l` repeatedly (or `watch -n 2 'ls data/chunks | wc -l'`).
2. **Terminal 2** — make sure the dependencies are actually up:
   ```bash
   docker compose up -d ollama
   docker exec advancerag-ollama-1 ollama list      # nomic-embed-text must be there
   ```
3. **Terminal 2** — clear previous output so you're watching a genuine fresh run:
   `rm -rf data/chunks`
4. **Terminal 2** — run it for real, with logs on:
   `python -m src.ingestion.pipeline`
   Watch Terminal 1's count climb from 0 toward 77, and watch the `httpx` INFO lines in
   Terminal 2 — each one is a real embedding call to Ollama for one document's sentences.
   **Budget ~9 minutes** — that's the measured wall-clock time for this corpus on CPU
   Ollama, not an estimate.
5. When it finishes, read the printed summary. **Predict before looking:** will
   `documents_processed` be 76? Will `documents_failed` be 0? (Measured answers: yes and
   yes — but 214 chunks, far fewer and far larger than the 500-char target implies.)
6. Now verify the claim rather than trusting the summary — open one output file and check
   `approx_token_count == metadata.tokens_approx` for a few chunks, proving enrichment
   genuinely ran inside the pipeline and wasn't silently skipped.
7. Finally, prove idempotency: run `python -m src.ingestion.pipeline` a second time and
   confirm Terminal 1's file count stays at 77 rather than growing, and that
   `report.json`'s `chunks_written` is identical to the first run.

If you can predict what each step will show BEFORE running it — and explain why — you've
internalised this module for real, not just "it ran without errors."
