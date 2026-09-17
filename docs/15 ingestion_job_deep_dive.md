# 15 — Ingestion Job: Deep Dive (P3-M5)

> Every output block in this document was produced on 2026-09-17 by running the code: against the
> live Qdrant **v1.19.0** server (`chunks`, 223 points), Ollama **0.20.0** (`nomic-embed-text`), the
> real `data/chunks/` (223 chunks, 76 documents) and the real `data/ingestion_log.json` from the P3-M5
> run. All scripts are in
> [`practice codes/phase 3/m5_ingestion_deep_dive/`](../practice%20codes/phase%203/m5_ingestion_deep_dive/).
> Run any of them from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 3/m5_ingestion_deep_dive/c1a_stage_timer.py"
> ```
>
> Scripts marked **(live)** need `docker compose up -d qdrant ollama`. Scripts that create scratch
> collections or files delete them before exiting. None of them modify the real `chunks` collection.

---

## 0. Plain-language summary

By the end of P3-M4 we could *put* chunks into Qdrant and *search* them. P3-M5 answers a different
question: **how do you keep an index correct over time, and how do you know it's correct?**

Documents change, get deleted, and get re-chunked. Settings get edited. Services go down halfway
through a run. A one-line "index everything" command handles none of that and tells you nothing
afterwards. The ingestion job (`src/vectorstore/ingest.py`) turns indexing into a **procedure with
checks and a record**:

```
python -m src.vectorstore.ingest
   │
   ├─ preflight  model pinned? collection exists & right shape? stored vectors from the same space?   (C2, C5)
   ├─ load       read all chunks from data/chunks                                                      (C3)
   ├─ embed      embed ALL chunks (≈198 s) — nothing written yet                                       (C6)
   ├─ upsert     per document: delete old points, write new ones                                       (C6)
   ├─ orphans    remove documents no longer on disk (unless chunking had failures)                     (C4)
   └─ verify     stored chunk_ids == disk chunk_ids, no incompatible points                            (C7)
   │
   └─ always: data/ingestion_log.json (status, failed stage, timings, counts)   + exit code 0/1        (C1, C8, C9)
```

Mutation testing then changed the design itself (C10).

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | A batch job as timed stages | 🟡 Engineering |
| C2 | Pre-flight checks: fail cheap, fail early | 🟡 Engineering (interview-heavy) |
| C3 | Full vs incremental ingestion (change detection) | 🔴 **Core RAG** (every production index) |
| C4 | Sync semantics: orphans, and when *not* to delete | 🔴 **Core RAG** |
| C5 | Compare with stored state, not with config | 🔴 **Core RAG / MLOps** |
| C6 | Write ordering, partial failure, blue/green indexing | 🔴 **Core RAG** (index freshness vs safety) |
| C7 | Verification by exact set comparison | 🟡 Engineering |
| C8 | Run logs: try/except/finally, re-raise, clocks | ⚪ Boilerplate (but where bugs hide) |
| C9 | Exit codes: telling other programs what happened | ⚪ Boilerplate |
| C10 | Mutation testing as design feedback | 🟡 Engineering |

> **Open Question 1** (what exactly happens when Qdrant fails *during the upsert stage*) is not
> answered here. Several concepts give you the pieces; putting them together is the exercise.

---

## C1 — A batch job as timed stages

### 1. Definition + analogy

**What it is.** A **batch job** is a program that processes a whole set of data in one run, usually
without a person watching, as opposed to a service that answers requests one at a time. Ingestion is a
batch job: it takes *all* chunks and brings the index up to date.

A well-built batch job is split into **stages**: named steps that run in a fixed order, each with one
responsibility. Ours has six: `preflight → load → embed → upsert → orphans → verify`. Splitting a job
into stages buys three things:

1. **Timing per stage.** Knowing a run took 203 seconds tells you little. Knowing that `embed` took
   197.7 s (97.5 %) and `upsert` 4.8 s tells you exactly where to optimise: making Qdrant writes
   faster would save at most 2 %.
2. **A precise failure location.** When a run fails, "failed in `embed`" immediately tells you which
   system to look at (the model server, not Qdrant, not the files).
3. **Reasoning about safety.** You can ask of each stage "does it change anything?".
   `preflight`, `load`, `embed` and `verify` only *read*; `upsert` and `orphans` *write*. That split is
   what C6 builds on.

**How the code times stages.** `_StageTimer` is a **context manager**: an object used with `with`,
which runs setup code on entry and cleanup code on exit. On entry it records the stage name and starts
a clock. On exit it stores the elapsed seconds in the report, **inside a `finally`**, so the time is
recorded *even if the stage raised an exception*. It also remembers `current`, the stage that was
running, which is how the log knows where a failure happened.

**Key terms.**
- **Context manager / `with`:** a guaranteed "before" and "after" around a block of code.
- **`@contextmanager`:** a decorator that turns a generator function into a context manager. The code
  before `yield` is the "before", the code after it (in `finally`) is the "after".
- **`time.perf_counter()`:** a clock designed for measuring durations (C8).

**Analogy.** A car assembly line with stations: body, paint, engine, inspection. Each station has a
stopwatch. If cars come out late, you look at the stopwatches, not at the whole factory. If a car is
rejected, the station where it stopped tells you who to talk to.

### For a 14-year-old

Imagine your mum asks you to "clean your room", and it takes three hours. She asks why it took so long.
"I don't know, it just did" isn't a great answer.

Now imagine you split the job into steps: *pick clothes off the floor*, *make the bed*, *sort the desk*,
*vacuum*. You set a timer for each step. At the end you can say: "Clothes took 10 minutes, the bed 5,
the desk **2 hours 30** (I kept reading old comics), vacuuming 15." Now everyone knows which step to
fix.

And if the vacuum cleaner breaks halfway, you don't just say "cleaning failed". You say "I finished
clothes, bed and desk, and it broke **during vacuuming**". That's exactly what our job does: it writes
down every step's time, and if something breaks, which step it broke in.

The clever bit is that the timer is stopped **even when something goes wrong**. It's like a stopwatch
that stops itself automatically when you drop it, so you still know how long you'd been going.

### 2. Why (vs the traditional approach)
A script that just calls `embed_all(); upsert_all()` and prints "done" works, until it's slow or
fails. Then the only way to find out *where* is to rerun it with extra prints, which for ingestion
means another ~200 seconds of embedding.

### 3. Minimal code

#### Example 1A — a stage timer, success and failure, step by step
`c1a_stage_timer.py`
```python
# A job as named, timed stages -- a tiny copy of ingest.py's _StageTimer, printed step by step.
import time
from contextlib import contextmanager

stage_seconds = {}
current = None


@contextmanager
def stage(name):
    global current
    current = name
    print(f"  enter {name!r}")
    started = time.perf_counter()
    try:
        yield                                   # the body of the `with` block runs here
    finally:                                    # runs whether the body finished or raised
        stage_seconds[name] = round(time.perf_counter() - started, 3)
        print(f"  leave {name!r} after {stage_seconds[name]}s")


print("RUN 1: every stage succeeds")
with stage("load"):
    time.sleep(0.05)
with stage("embed"):
    time.sleep(0.20)
with stage("write"):
    time.sleep(0.01)
print("  timings:", stage_seconds, "| slowest:", max(stage_seconds, key=stage_seconds.get))

print("RUN 2: the middle stage fails")
stage_seconds.clear()
try:
    with stage("load"):
        time.sleep(0.05)
    with stage("embed"):
        time.sleep(0.10)
        raise ConnectionError("model server went away")
    with stage("write"):                        # never reached
        time.sleep(0.01)
except ConnectionError as e:
    print(f"  caught {type(e).__name__}: {e}")
print("  timings:", stage_seconds, "| failed in:", current, "| 'write' present?", "write" in stage_seconds)
```
Real output:
```
RUN 1: every stage succeeds
  enter 'load'
  leave 'load' after 0.05s
  enter 'embed'
  leave 'embed' after 0.2s
  enter 'write'
  leave 'write' after 0.01s
  timings: {'load': 0.05, 'embed': 0.2, 'write': 0.01} | slowest: embed
RUN 2: the middle stage fails
  enter 'load'
  leave 'load' after 0.05s
  enter 'embed'
  leave 'embed' after 0.101s
  caught ConnectionError: model server went away
  timings: {'load': 0.05, 'embed': 0.101} | failed in: embed | 'write' present? False
```
**How to read it.**
- **Run 1:** each `with stage(...)` prints *enter*, runs its body, then prints *leave* with the time.
  `max(..., key=...)` finds the slowest stage: `embed`.
- **Run 2, the important part:** `embed` raised an exception, **but "leave 'embed' after 0.101s" was
  still printed**. The `finally` inside the context manager ran on the way out. So even a failed stage
  gets its time recorded.
- `current` is still `"embed"`, so the program knows where it failed.
- `'write' present? False`: the exception skipped the rest of the `try` block, so the `write` stage
  never started. In a log, **a missing stage means "never reached"**, which is itself information.

#### Example 1B — reading the real log from the P3-M5 run
`c1b_real_log_stages.py`
```python
# Read the REAL run log written by `python -m src.vectorstore.ingest` and see where the time went.
import json
from pathlib import Path

log = json.loads(Path("data/ingestion_log.json").read_text(encoding="utf-8"))
total = sum(log["stage_seconds"].values())
print(f"run {log['started_at']} -> {log['finished_at']}  status={log['status']}  verified={log['verified']}")
for name, seconds in log["stage_seconds"].items():
    bar = "#" * max(1, round(40 * seconds / total))
    print(f"  {name:<9} {seconds:>8.3f}s  {100 * seconds / total:5.1f}%  {bar}")
print(f"  {'total':<9} {total:>8.3f}s")
print("chunks per second while embedding:", round(log["chunks_on_disk"] / log["stage_seconds"]["embed"], 2))
```
Real output:
```
run 2026-09-17T12:33:45+00:00 -> 2026-09-17T12:37:08+00:00  status=succeeded  verified=True
  preflight    0.156s    0.1%  #
  load         0.019s    0.0%  #
  embed      197.709s   97.5%  #######################################
  upsert       4.758s    2.3%  #
  orphans      0.016s    0.0%  #
  verify       0.047s    0.0%  #
  total      202.705s
chunks per second while embedding: 1.13
```
**How to read it.**
- **97.5 % of the run is embedding.** Everything else together is under 5 seconds. Any speed
  improvement must target the model: batch size on a GPU, a smaller model, or incremental ingestion
  (C3). Tuning Qdrant would be wasted effort.
- **1.13 chunks/s** matches doc 13's warm measurement (~1.16 chunks/s). The log confirms an
  earlier benchmark instead of us re-measuring by hand.
- **Start and end timestamps** (12:33:45 → 12:37:08 UTC) give 3 min 23 s, which agrees with the
  202.7-second total.

### 4. Benefits
Diagnose slowness and failures from one file, without rerunning an expensive job.

- **What breaks without it:** "it's slow" with no idea why; "it failed" with no idea where.
- **Docs:** [Python `contextlib.contextmanager`](https://docs.python.org/3/library/contextlib.html#contextlib.contextmanager) ·
  [The `with` statement (PEP 343)](https://peps.python.org/pep-0343/)

### Micro-exercises
1. In 1A, make `load` fail instead of `embed`. Which stages appear in `timings`? *Hint:* only the one
   that started.
2. Remove the `try/finally` inside `stage` (keep the lines) and rerun Run 2. What's missing now?
3. In 1B, compute how long a run would take for 2,230 chunks at the same rate.
4. Add a `"total"` key to `stage_seconds` in 1A using a stage that wraps all others. Is it nested
   correctly?
5. Why does `_StageTimer` log "stage finished" *after* the `finally`, not inside it? *Hint:* what
   happens to that line when the stage raised?

**How it works internally.** `@contextmanager` wraps the generator. `with` calls `__enter__`, which runs
the generator up to `yield`. When the block ends normally, `__exit__` resumes the generator after
`yield`. When the block raises, `__exit__` throws the exception *into* the generator at the `yield`
line, so its `finally` runs, and then the exception continues outward.
**Checkpoint:** explain why a failed stage still has a time in `stage_seconds`, and what a *missing*
stage means.

---

## C2 — Pre-flight checks: fail cheap, fail early

### 1. Definition + analogy

**What it is.** A **pre-flight check** verifies a job's preconditions **before** it starts expensive or
irreversible work. The name comes from aviation. The principle: **order your checks by cost, and put
every cheap check in front of the first expensive step.**

Look at our stages' costs: `preflight` ≈ 0.2 s, `load` ≈ 0.02 s, **`embed` ≈ 198 s**, `upsert` ≈ 5 s
(and it *changes* data). Any problem that can be detected in the first 0.2 seconds but is instead
discovered after `embed` costs 198 seconds of waiting. It can also cost *damage*, if nothing stops the
writes.

Our preflight checks three things, each a precondition for the whole run to make sense:
1. **The model is the pinned one.** Building the `Embedder` calls `verify_model` (P3-M2).
2. **The collection exists and has the right shape.** `ensure_collection` creates it on a fresh server,
   and raises on an unreachable server or a wrongly sized collection (P3-M1).
3. **The stored vectors come from the same vector space as the settings.** `count_incompatible` refuses
   the run unless `--rebuild` is given (C5).

**Fail fast** is the broader idea: when something is wrong, stop at the earliest point where you can
know it, with a clear message, instead of carrying on and failing later in a confusing way.

**The limit of pre-flight checks:** they prove the world was fine *at the moment of checking*. A server
can still go down one minute later, during `embed` or `upsert`. Pre-flight reduces late failures; it
can't eliminate them. Later stages still need their own error handling (C6, C8).

**Analogy.** Before a long road trip you check fuel, tyres and that you have the right address, in the
driveway, in two minutes. Discovering the wrong address after four hours of driving is the same
mistake at a hundred times the cost.

### For a 14-year-old

You're baking a cake for a friend's birthday. The recipe takes 2 hours: mixing, baking, cooling,
decorating.

**Option A:** start straight away. After an hour and a half, you reach "add 3 eggs" and find there are
no eggs. Two hours wasted, half a bowl of useless mix, and no cake.

**Option B:** before touching anything, spend two minutes checking: eggs? flour? is the oven working?
is this even the right recipe (chocolate, not lemon)? If anything's missing, you find out in two
minutes, while it's still easy to fix.

That two-minute check is a **pre-flight check**. Our program does the same thing. Before the slow part
(turning 223 chunks into numbers, about 3 and a half minutes), it checks in a fraction of a second that:
- the "cooking machine" (the AI model) is the exact one we expect,
- the "cake tin" (the Qdrant collection) exists and is the right size,
- the cakes already on the shelf were made with the *same recipe* as today's (C5 explains why).

One honest catch: checking the oven works at the start doesn't mean it can't break halfway through
baking. The check makes surprises much rarer, not impossible.

### 2. Why (vs the traditional approach)
Scripts usually check things *where they're used*: the collection when writing, the model when
embedding. That puts the checks in the wrong order relative to cost.

### 3. Minimal code

#### Example 2 — the same mistake caught before vs after the expensive stage (live)
The mistake: running ingestion after someone changed `EMBED_DOCUMENT_PREFIX` to an empty string. This
is harmless to run: preflight refuses before anything is written.

`c2_preflight_cost.py`
```python
# Pre-flight: the same mistake found before vs after the expensive stage (live, harmless).
# Mistake used: running with a changed document prefix against the real 'chunks' collection.
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = Path(tempfile.gettempdir()) / "dd15_preflight_log.json"
env = {**os.environ, "EMBED_DOCUMENT_PREFIX": ""}          # the mistake

started = time.perf_counter()
result = subprocess.run([sys.executable, "-m", "src.vectorstore.ingest", "--log", str(log)],
                        env=env, capture_output=True, text=True)
elapsed = time.perf_counter() - started
print(f"WITH preflight : exit code {result.returncode} after {elapsed:.1f}s (includes Python start-up)")
print("                ", result.stdout.strip().splitlines()[-2].strip()[:150])

# Without preflight the same run would only fail (if at all) after embedding every chunk.
import json
real = json.loads(Path("data/ingestion_log.json").read_text(encoding="utf-8"))
print(f"WITHOUT        : the embed stage alone took {real['stage_seconds']['embed']:.1f}s in the last real run,"
      " and nothing would have stopped the write afterwards")
```
Real output:
```
WITH preflight : exit code 1 after 3.4s (includes Python start-up)
                 failed in preflight: IngestionAborted: 223 stored points were made with a different model digest or document prefix than the current settings (0a109f4
WITHOUT        : the embed stage alone took 197.7s in the last real run, and nothing would have stopped the write afterwards
```
**How to read it.**
- **3.4 seconds end to end**, most of it starting Python and importing libraries. The preflight stage
  itself took ~0.2 s (seen in P3-M5). The message says exactly what's wrong and how to proceed
  deliberately (`--rebuild`).
- **Exit code 1** (C9): a script or scheduler knows the run failed without parsing text.
- **Without preflight** it's worse than slow: the old P3-M4 command would have embedded for ~198 s and
  then *successfully* rewritten the whole collection with prefix-less vectors (proved on a scratch
  collection in deep dive 14). Preflight turns a silent, expensive corruption into a 0.2-second refusal.
- The log went to a temp file (`--log`), so the real `data/ingestion_log.json` still describes the last
  real run.

### 4. Benefits
Fast, clear refusals; expensive stages start only when they can succeed; less damage from obvious
mistakes.

- **What breaks:** checks placed after `embed` (slow feedback); checks that only warn instead of
  stopping; believing preflight makes later failures impossible.
- **Docs:** [Jim Shore, "Fail Fast" (IEEE Software, 2004)](https://www.martinfowler.com/ieeeSoftware/failFast.pdf) ·
  [Atul Gawande, *The Checklist Manifesto*](https://en.wikipedia.org/wiki/The_Checklist_Manifesto) (the aviation origin)

### Micro-exercises
1. Stop Qdrant (`docker compose stop qdrant`), run `python -m src.vectorstore.ingest --log scratch.json`,
   and read which stage failed and how long it took. Start Qdrant again.
2. Stop Ollama instead. Does preflight catch it? *Hint:* `make_embedder()` calls `verify_model`.
3. List every precondition of *embedding*. Which ones are not checked in preflight?
4. Estimate the cost of a failure discovered in `verify` versus in `preflight`, using the real log's
   timings.
5. Why is "check the collection's shape" done by `ensure_collection` and not by comparing with a
   hard-coded 768? *Hint:* single source of truth (doc 12, C6).

**How it works internally.** Preflight runs inside `with stage("preflight"):`. Any exception raised
there skips every later stage (they're later lines in the same `try` block) and goes straight to
`except` and `finally` (C8).
**Checkpoint:** name the three preflight checks and the precondition each protects.

---

## C3 — Full vs incremental ingestion

### 1. Definition + analogy

**What it is.** There are two strategies for bringing an index up to date:

- **Full ingestion:** re-process **everything** on every run, whether or not it changed. Ours embeds
  all 223 chunks each time (~198 s).
- **Incremental ingestion:** re-process **only what changed** since the last run, and leave the rest.
  If one document of 76 changed, embed only its chunks.

Incremental is faster, but it needs **change detection**: a reliable way to decide "this document is
different from last time". That requires remembering something about the last run, which is **state**
you must store, keep in sync and trust. There are two common signals:

1. **Content hash (fingerprint).** Compute a hash (e.g. SHA-256) of exactly the data that affects the
   stored vectors, here the document's chunk texts in order, and store it next to the vectors, for
   example in the payload. Next run, recompute and compare: same hash means unchanged. Hashes change if
   and only if the content changes (for practical purposes). But the fingerprint must cover
   *everything* that affects the vectors. Change the model or prefix and every hash still matches,
   while every vector is stale. That's why ingestion also needs C5.
2. **File modification time (mtime).** The timestamp the operating system stores when a file is
   written. It's cheap, but it's a **proxy**: it records *when a file was written*, not *whether its
   content changed*. Copies, git checkouts, restores and sync tools can change mtime without changing
   content, or restore an old mtime on new content.

**Why we chose full ingestion (D-2026-09-17-25).** At 223 chunks, 198 s is acceptable, and full
ingestion has **no change-detection bugs**: a wrong "unchanged" decision is a silent stale vector that
nothing reports. The flip condition is when a full run takes long enough that people start skipping it.

**Key terms.**
- **Hash / fingerprint:** a fixed-size value computed from data; any change in the data changes it.
- **Separator byte:** without one, chunk texts `"ab","c"` and `"a","bc"` would hash the same, since
  both concatenate to `"abc"`.
- **Stateless vs stateful job:** a full run needs no memory of previous runs; an incremental run does.

**Analogy.** Updating a printed phone directory. Full: reprint the whole book every year. Slow, but
always right. Incremental: print only the changed pages and slot them in. Fast, but if your list of
"changed pages" is wrong, the book silently contains old numbers.

### For a 14-year-old

You keep a photo album of 76 pages, and each page has a caption you typed. Every week you might change
a few captions.

**Full way:** every Sunday, reprint all 76 pages. It takes ages, but the album is always exactly right.
You never need to remember what you changed.

**Incremental way:** only reprint pages you changed. Much faster. But now you need to *know* which pages
changed. Two ways to find out:

1. **Look at the "last edited" date on each file.** Easy, but it can lie. If you copy the whole folder
   to a new laptop, every file gets a *new* date even though nothing changed, so you'd reprint
   everything for no reason. And some tools put the *old* date back on a file you *did* change, so you'd
   skip a page you actually edited. The album is quietly wrong, and nobody notices.
2. **Take a "fingerprint" of each caption.** A hash is like a fingerprint for text: a short code
   calculated from the words. Change even one letter and the fingerprint is completely different. Keep
   last week's fingerprints; if a page's fingerprint changed, reprint it. This doesn't care about dates
   at all.

But even fingerprints have a blind spot. If you switch to a **different printer** (a new AI model), all
the captions are the same, so all the fingerprints match, yet every page should be reprinted because it
looks different now. So fingerprints alone aren't enough (C5 covers that).

We chose the **full way** for now. With only 223 chunks it takes about 3 minutes, and it can never skip a
page by mistake.

### 2. Why (vs the traditional approach)
Frameworks such as LangChain's indexing API and LlamaIndex's ingestion pipeline implement incremental
ingestion with a separate record store of document hashes. It's powerful, but that's exactly the extra
state that has to stay correct.

### 3. Minimal code

#### Example 3 — fingerprints find the one changed document; mtime lies both ways
`c3_full_vs_incremental.py`
```python
# Full vs incremental ingestion: how change detection works, and how mtime lies.
import dataclasses
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from src.ingestion.pipeline import read_chunks

chunks = read_chunks()
by_doc = {}
for c in chunks:
    by_doc.setdefault(c.source_path, []).append(c)


def fingerprint(doc_chunks):
    # hash of everything that affects the stored vectors: the ordered chunk texts
    h = hashlib.sha256()
    for c in sorted(doc_chunks, key=lambda c: c.chunk_index):
        h.update(c.text.encode("utf-8"))
        h.update(b"\x00")                      # separator, so "ab"+"c" != "a"+"bc"
    return h.hexdigest()[:16]


# 1. "Last run" fingerprints, then pretend one document was edited
last_run = {path: fingerprint(cs) for path, cs in by_doc.items()}
edited = "company/about.md"
by_doc[edited][0] = dataclasses.replace(by_doc[edited][0], text=by_doc[edited][0].text + " Edited.")
changed = [p for p, cs in by_doc.items() if fingerprint(cs) != last_run[p]]
print(f"1. documents: {len(by_doc)} | chunks: {len(chunks)}")
print(f"   FULL run embeds        : {len(chunks)} chunks")
print(f"   INCREMENTAL run embeds : {sum(len(by_doc[p]) for p in changed)} chunks from changed documents {changed}")

# 2. Why file modification time is a weak signal: copying changes it, content does not change
tmp = Path(tempfile.mkdtemp())
src = Path("data/company/about.md")
copy = tmp / "about.md"
shutil.copy(src, copy)                          # shutil.copy does NOT preserve mtime
same_content = hashlib.sha256(src.read_bytes()).hexdigest() == hashlib.sha256(copy.read_bytes()).hexdigest()
print(f"2. copied file: content identical={same_content}, mtime changed={os.path.getmtime(copy) != os.path.getmtime(src)}")

# 3. And the reverse: content changes but mtime is set back (e.g. restored by a tool)
before = os.path.getmtime(copy)
copy.write_text(copy.read_text(encoding="utf-8") + "\nNew line.", encoding="utf-8")
os.utime(copy, (before, before))
print(f"3. edited file with restored mtime: mtime changed={os.path.getmtime(copy) != before}, "
      f"content changed={hashlib.sha256(copy.read_bytes()).hexdigest() != hashlib.sha256(src.read_bytes()).hexdigest()}")
shutil.rmtree(tmp)
```
Real output:
```
1. documents: 76 | chunks: 223
   FULL run embeds        : 223 chunks
   INCREMENTAL run embeds : 2 chunks from changed documents ['company/about.md']
2. copied file: content identical=True, mtime changed=True
3. edited file with restored mtime: mtime changed=False, content changed=True
```
**How to read it.**
- **Line 1:** one edited sentence in `about.md` changes that document's fingerprint and no other. An
  incremental run would embed **2 chunks instead of 223**: about 2 seconds instead of 198. That's the
  appeal.
- **Line 2, a false positive:** copying the file gives identical content but a new mtime. An mtime-based
  job would re-embed it for nothing, which is wasteful but harmless.
- **Line 3, a false negative:** content changed and the mtime was put back. An mtime-based job would
  **skip** it. The index would silently serve old text, which is the dangerous kind of error.
- **What the fingerprint doesn't include:** model, digest, prefix, chunking settings. If any of those
  change, every fingerprint matches and incremental ingestion would skip everything. A real
  implementation must include them in the fingerprint, or rely on a check like C5.

### 4. Benefits of each
- **Full:** no state, no change-detection bugs, easy to reason about, but cost grows with corpus size.
- **Incremental (content hash):** cost grows with the *amount of change*, but needs stored state and a
  complete fingerprint.

- **What breaks:** incremental with an incomplete fingerprint skips re-embedding after a model or prefix
  change; mtime-based detection skips real edits.
- **Docs:** [LangChain indexing API (record manager, cleanup modes)](https://python.langchain.com/docs/how_to/indexing/) ·
  [LlamaIndex ingestion pipeline (document management)](https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/) ·
  [Python `hashlib`](https://docs.python.org/3/library/hashlib.html)

### Micro-exercises
1. Remove the separator byte and find two different chunk lists with the same fingerprint.
2. Add `settings.embed_model_digest` and `settings.embed_document_prefix` to `fingerprint`. What happens
   to `changed` if the prefix changes?
3. Using the real log (1.13 chunks/s), at what corpus size does a full run exceed 30 minutes?
4. Use `shutil.copy2` instead of `shutil.copy` in step 2. Does mtime change now? *Hint:* copy2 preserves
   metadata.
5. Design the payload field and query needed to read last run's fingerprints back from Qdrant.

**Internals.** SHA-256 processes input in 512-bit blocks and produces 256 bits. Any single-bit change in
the input changes about half the output bits (the avalanche effect), which is why fingerprints of
nearly identical texts look unrelated.
**Checkpoint:** give one false positive and one false negative of mtime-based change detection, and say
which is worse.

---

## C4 — Sync semantics: orphans, and when *not* to delete

### 1. Definition + analogy

**What it is.** An index is a *copy* of a source. **Syncing** means making the copy match the source,
which has three parts, not one:
1. **Insert** what the source has and the index doesn't (new documents).
2. **Replace** what both have (changed or re-chunked documents; the delete-by-doc_id guard from P3-M4).
3. **Delete** what the index has and the source doesn't. These leftover documents are **orphans**.

The first two happen naturally when you upsert everything. The third doesn't: nothing in "upsert every
chunk on disk" ever touches a document that isn't on disk. So without a dedicated step, a document
removed from `data/` stays **searchable forever**, and the LLM can quote a policy the company withdrew.

It's easiest to think in **sets**. Let `S` be the documents stored in Qdrant and `D` those on disk:
- orphans = `S − D`
- new documents = `D − S`
- documents to replace = `S ∩ D`

**When *not* to delete.** Deleting orphans assumes that "not on disk" means "deliberately removed". That
assumption breaks after a **partly failed chunking run**. If `data/company/about.md` couldn't be read
during chunking, it's missing from `data/chunks/`, so it looks like an orphan, but the document still
exists and is still valid. Deleting it would remove good content because of an unrelated error.

So the job reads the chunking run's `report.json`. If `documents_failed > 0`, it **keeps** the orphans,
records a **warning**, and excludes them from verification (C7). The next clean chunking run
lets them be removed if they really were deleted.

That's a general pattern: **destructive operations need stronger evidence than constructive ones.**
Adding a document wrongly is easy to undo; deleting one wrongly loses data.

**Analogy.** A library catalogue. New books get added, and damaged books replaced. When a book isn't on
the shelf during stocktaking, you remove its catalogue card, **unless** the stocktaking itself was
interrupted halfway, in which case "not counted" doesn't mean "gone".

### For a 14-year-old

You have a big folder of song lyrics on your laptop, and a music app that shows lyrics while songs play.
Each night the app copies your folder so it can show lyrics offline.

**Adding and updating are easy.** A new song file gets added to the app. If you fix a typo in a song,
the app's copy gets replaced with the fixed version.

**Deleting is the tricky part.** Say you delete a song you hate from your folder. If the app only ever
*adds and updates*, it never notices the song is gone. It keeps showing those lyrics forever. That
leftover song is an **orphan**: its "parent" file is gone, but the copy is still hanging around. So the
app needs a step that asks "what do I have that the folder doesn't?" and removes those.

**But here's the trap.** One night, the copying crashes halfway through because the laptop went to
sleep. Half your songs weren't copied. If the app now says "anything I didn't see tonight must have
been deleted", it wipes half your lyrics, even though you deleted nothing.

So the app first checks a little report the copying step writes: "Did anything fail tonight?" If the
report says "yes, 3 songs couldn't be read", the app **doesn't delete anything** that night. It leaves
a warning saying "I kept some songs I couldn't check". Next night, when copying works properly, it can
safely clean up.

The rule to remember: **only throw things away when you're really sure they're meant to be gone.**

### 2. Why (vs the traditional approach)
"Upsert everything" tutorials don't mention deletion at all. Frameworks that handle it (LangChain's
`cleanup="full"` mode) warn that running full cleanup on a *partial* batch of documents deletes the rest.
It's the same trap our guard addresses.

### 3. Minimal code

#### Example 4A — sync as set arithmetic, with and without the guard
`c4a_orphans_set_math.py`
```python
# Sync = set arithmetic between what the index holds and what the source holds.
stored = {"doc-about": {"about_0", "about_1"}, "doc-carllm": {"carllm_0"}, "doc-old-policy": {"policy_0", "policy_1"}}
disk = {"doc-about": {"about_0", "about_1"}, "doc-carllm": {"carllm_0"}, "doc-new-faq": {"faq_0"}}

orphans = set(stored) - set(disk)          # in the index, gone from the source
new_docs = set(disk) - set(stored)         # in the source, not yet in the index
both = set(stored) & set(disk)             # in both: re-upserted (delete-by-doc_id + upsert)
print("stored documents :", sorted(stored))
print("disk documents   :", sorted(disk))
print("orphans (delete) :", sorted(orphans))
print("new (insert)     :", sorted(new_docs))
print("in both (replace):", sorted(both))


def sync(stored, disk, documents_failed):
    result = {doc: set(ids) for doc, ids in stored.items()}
    for doc, ids in disk.items():                       # upsert stage
        result[doc] = set(ids)
    orphan_docs = set(stored) - set(disk)
    if documents_failed:                                # the guard
        return result, f"kept {len(orphan_docs)} orphan(s) because {documents_failed} document(s) failed to chunk"
    for doc in orphan_docs:                             # orphan stage
        del result[doc]
    return result, f"removed {len(orphan_docs)} orphan(s)"


for failed in (0, 1):
    result, note = sync(stored, disk, failed)
    print(f"documents_failed={failed}: index now {sorted(result)} -> {note}")
```
Real output:
```
stored documents : ['doc-about', 'doc-carllm', 'doc-old-policy']
disk documents   : ['doc-about', 'doc-carllm', 'doc-new-faq']
orphans (delete) : ['doc-old-policy']
new (insert)     : ['doc-new-faq']
in both (replace): ['doc-about', 'doc-carllm']
documents_failed=0: index now ['doc-about', 'doc-carllm', 'doc-new-faq'] -> removed 1 orphan(s)
documents_failed=1: index now ['doc-about', 'doc-carllm', 'doc-new-faq', 'doc-old-policy'] -> kept 1 orphan(s) because 1 document(s) failed to chunk
```
**How to read it.**
- The three set operations split every document into exactly one bucket: `old-policy` is an orphan,
  `new-faq` is new, `about` and `carllm` get replaced.
- **Clean run:** after syncing, the index holds exactly the disk's three documents.
- **Run with a chunking failure:** `old-policy` is **kept**. The index now holds 4 documents, and the
  reason is recorded. Being wrong this way (keeping a deleted document one more day) is recoverable;
  deleting a valid one isn't.

#### Example 4B — the real job on a live scratch collection
`c4b_orphans_live.py`
```python
# Orphan removal and the failure guard on a LIVE scratch collection, through the real job.
# Uses 3 small documents so embedding takes seconds. The scratch collection is deleted at the end.
import json
import shutil
import tempfile
from pathlib import Path

from src.embedding.embedder import Embedder, build_ollama_client
from src.vectorstore.ingest import run_ingestion
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

URL, OLLAMA = "http://localhost:6333", "http://localhost:11434"
COLLECTION = "dd15_orphans_scratch"
work = Path(tempfile.mkdtemp())
chunks_dir, log = work / "chunks", work / "log.json"
chunks_dir.mkdir()
for name in ("company__about.md.json", "company__careers.md.json", "products__Carllm.md.json"):
    shutil.copy(Path("data/chunks") / name, chunks_dir / name)


def set_failed(n):
    (chunks_dir / "report.json").write_text(json.dumps({"documents_failed": n}), encoding="utf-8")


client = build_qdrant_client(URL)
if client.collection_exists(COLLECTION):
    client.delete_collection(COLLECTION)
make_embedder = lambda: Embedder(build_ollama_client(OLLAMA), ollama_url=OLLAMA)


def ingest(label):
    r = run_ingestion(client, make_embedder, collection=COLLECTION, chunks_dir=chunks_dir, log_path=log)
    docs = sorted(QdrantVectorStore(client, None, collection=COLLECTION).stored_chunk_ids_by_document())
    print(f"{label:<44} points {r.points_before}->{r.points_after} | removed {r.orphan_documents_removed}"
          f" kept {r.orphan_documents_kept} | verified={r.verified} | stored docs={len(docs)}")
    for w in r.warnings:
        print("   warning:", w)


set_failed(0)
ingest("1. first run, 3 documents")
(chunks_dir / "products__Carllm.md.json").unlink()
set_failed(1)
ingest("2. Carllm file gone, report says 1 failure")
set_failed(0)
ingest("3. Carllm file gone, clean report")

client.delete_collection(COLLECTION)
shutil.rmtree(work)
print("scratch collection deleted:", not client.collection_exists(COLLECTION))
```
Real output:
```
1. first run, 3 documents                    points 0->7 | removed 0 kept 0 | verified=True | stored docs=3
2. Carllm file gone, report says 1 failure   points 7->7 | removed 0 kept 1 | verified=True | stored docs=3
   warning: kept 1 orphan document(s): the chunking report records 1 failed document(s), so their absence is not proof of deletion
3. Carllm file gone, clean report            points 7->4 | removed 1 kept 0 | verified=True | stored docs=2
scratch collection deleted: True
```
**How to read it.**
- **Run 1:** 3 documents (about, careers, Carllm) → 7 points, verified.
- **Run 2:** the Carllm chunk file is gone, **but the report records a chunking failure**. The job
  can't tell "deleted" from "failed to chunk", so it keeps Carllm (7 → 7), warns, and still verifies:
  kept orphans are deliberately excluded from the comparison (C7).
- **Run 3:** same missing file, but a clean report. Now its absence is trusted: 7 → **4** points, 1
  orphan removed, verified, 2 documents stored.
- The only difference between runs 2 and 3 is one number in `report.json`. That shows how much the
  guard depends on that file (a limitation listed in the P3-M5 notes).

### 4. Benefits
The index stops serving withdrawn content, and a failed upstream run can't trigger mass deletion.

- **What breaks:** no orphan step means stale documents are searchable forever; no guard means a half-failed
  chunking run deletes valid documents; a deleted `report.json` means the guard can't see failures.
- **Docs:** [LangChain indexing: cleanup modes and their caveats](https://python.langchain.com/docs/how_to/indexing/) ·
  [Python set operations](https://docs.python.org/3/library/stdtypes.html#set-types-set-frozenset)

### Micro-exercises
1. In 4A, add a document to both `stored` and `disk` with *different* chunk IDs. Which bucket is it in,
   and what fixes its stale chunks? *Hint:* P3-M4's delete-by-doc_id.
2. Change the guard in 4A to "keep orphans only if more than 10 % of documents failed". Argue for or
   against.
3. In 4B, delete `report.json` entirely before run 2. What happens, and why is that a risk?
4. Design a stronger signal of intentional deletion than `report.json` (e.g. a tombstone file). What
   does it cost?
5. Why does the job compute orphans *after* upsert rather than before? *Hint:* which set of stored
   documents does it read?

**Internals.** `stored_chunk_ids_by_document()` pages through Qdrant with `scroll(limit=1000,
with_payload=["doc_id", "chunk_id"])`, so only two short fields per point cross the network.
`delete_documents()` then issues one filtered delete with `MatchAny(any=[...orphan doc_ids])`.
**Checkpoint:** state the three set operations of a sync and the one condition under which the job
refuses to delete.

---

## C5 — Compare with stored state, not with config

### 1. Definition + analogy

**What it is.** A **guard** protects an invariant by comparing two things. What you compare *against*
decides what the guard can catch. The invariant here is: **every vector in the collection lives in the
same vector space**, meaning made by the same model version with the same document prefix (doc 12, C7).

P3-M4's `upsert` guard compared **new embeddings** with **settings**. That sounds right, but look at
where both sides come from:
- the **Embedder** reads `settings` to know the prefix to add;
- the **store** reads the *same* `settings` to know what to expect.

Change a setting (an environment variable, a `.env` edit, a teammate's config) and **both sides change
together**. They still agree, so the guard passes, and new vectors from a *different* space get written
over the old ones. A guard whose two inputs come from the same source can never detect a change to that
source. Deep dive 14 proved this on a scratch collection.

The only thing that **doesn't** change when settings change is **what's already stored**. Every point
carries `model_digest` and `document_prefix` in its payload (P3-M4, D4). So the P3-M5 preflight asks
Qdrant: *"how many stored points were made with a digest or prefix different from the current
settings?"* A nested filter answers that in one server-side count:

```python
same_space = Filter(must=[digest == settings.digest, prefix == settings.prefix])
count(filter=Filter(must_not=[same_space]))      # points NOT in the current space
```

If that's more than 0, the run refuses. **`--rebuild`** is the explicit way to say "yes, I changed the
space on purpose, replace everything".

**The general principle:** to detect drift in X, compare X against an **independent record** of what X
used to be, not against something derived from X itself.

**Analogy.** A bank checks a withdrawal against the **account balance it has stored**, not against the
number the customer writes on the slip. If both numbers come from the customer, they'll always agree.

### For a 14-year-old

Your class keeps a shared "cake book": everyone's cake recipe, all in **grams**. One day the teacher
writes a rule on the board: "All recipes in this book must use grams."

Before adding a new recipe, a helper checks: "Does this new recipe match the rule on the board?"

Now imagine someone secretly rubs out "grams" on the board and writes **"cups"**. The next person writes
their recipe in cups, because that's what the board says. The helper checks it against the board: cups
= cups, looks fine! The recipe goes in the book.

But the book is now a **mess**: every old recipe is in grams, the new one in cups. Anyone following the
book will make terrible cakes, and the helper's check never noticed, because **the new recipe and the
rule on the board were changed by the same person at the same time.**

The fix: before adding anything, the helper **flips through the old recipes already in the book**. "Wait,
everything in here is in grams, but today's rule says cups. Something changed. I'm stopping until a
grown-up confirms." Only if the teacher says "yes, we're switching the *whole* book to cups" (that's our
`--rebuild`) does it go ahead and redo every recipe.

**Checking against what's already in the book catches changes that checking against the rule can't.**

### 2. Why (vs the traditional approach)
Most systems keep model and version information only in configuration or deployment files, so a changed
config is simply "the new truth". Nothing records which version produced the vectors already stored,
and a mixed index is invisible.

### 3. Minimal code

#### Example 5 — the same change, two guards
`c5_stored_vs_config.py`
```python
# Why a check against CONFIG cannot catch a CONFIG change, and a check against STORED data can.
from dataclasses import dataclass


@dataclass
class Settings:
    prefix: str


stored_points = [{"chunk": f"about_{i}", "prefix": "search_document: "} for i in range(3)]   # built last week


def check_against_config(settings, new_embedding_prefix):
    # what QdrantVectorStore.upsert does: new embeddings vs current settings
    return new_embedding_prefix == settings.prefix


def check_against_stored(settings):
    # what the ingestion preflight does: stored points vs current settings
    return sum(1 for p in stored_points if p["prefix"] != settings.prefix)


for label, settings in [("settings unchanged", Settings("search_document: ")),
                        ("someone set prefix to ''", Settings(""))]:
    new_prefix = settings.prefix                    # the embedder reads the SAME settings
    ok_config = check_against_config(settings, new_prefix)
    incompatible = check_against_stored(settings)
    print(f"{label:<26} config check passes: {ok_config!s:<5} | stored points in another space: {incompatible}"
          f" -> {'REFUSE' if incompatible else 'proceed'}")
```
Real output:
```
settings unchanged         config check passes: True  | stored points in another space: 0 -> proceed
someone set prefix to ''   config check passes: True  | stored points in another space: 3 -> REFUSE
```
**How to read it.**
- `new_prefix = settings.prefix` is the whole problem in one line: the embedding's prefix is *derived
  from* the settings it's then compared with.
- **Unchanged settings:** both checks pass, and ingestion proceeds.
- **Changed prefix:** the config check **still passes**, while the stored check finds **3 points** from a
  different space and refuses.
- The live version of this is example 2 above: the real preflight found **223** incompatible points and
  refused in 0.2 s.

### 4. Benefits
Configuration drift can no longer silently re-encode the collection, and intentional changes stay
possible via an explicit flag.

- **What breaks:** a guard comparing two things from the same source; `--rebuild` used by habit; writes that
  bypass the job (direct `store.upsert` calls still bypass this check, as noted in P3-M5's limitations).
- **Docs:** [Qdrant: nested filters (`must_not` with a clause)](https://qdrant.tech/documentation/concepts/filtering/#nested-filters) ·
  [Google, "Hidden Technical Debt in Machine Learning Systems" (NeurIPS 2015)](https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html), on configuration debt and data dependencies

### Micro-exercises
1. In example 5, change only the *model digest* (add a `digest` field) and extend both checks.
2. Why does the preflight count use `must_not` of an AND, rather than two separate `must_not`s ANDed
   together? *Hint:* De Morgan's law; which points does each version select?
3. What should the check do on a brand-new, empty collection? Does the current code handle it?
   *Hint:* count of 0.
4. Propose where else in the codebase a check compares two values derived from the same source.
5. Should `QdrantVectorStore.upsert` itself run the stored-state check? List one benefit and one cost.

**Internals.** Qdrant evaluates `must_not: [ {must: [A, B]} ]` as NOT (A AND B) = (NOT A) OR (NOT B),
using the keyword payload values, and returns an exact count because `exact=True` is passed.
**Checkpoint:** explain in two sentences why the P3-M4 upsert guard couldn't catch a prefix change and
why the preflight check can.

---

## C6 — Write ordering, partial failure, blue/green indexing

### 1. Definition + analogy

**What it is.** A job that changes stored data can fail **partway**. What state it leaves behind depends
on *when* it starts writing. Three ideas help reason about this.

**(a) Separate the slow, failure-prone *reading and computing* from the *writing*.** Our job embeds
**all** chunks first (the stage most likely to fail: model server, timeouts, bad input) and only then
writes. If embedding fails, **nothing has been written**, so the collection is exactly as it was
(D-2026-09-17-29). The alternative, embedding and writing one document at a time, saves progress as it
goes, but a failure leaves a **mixed index**: some documents new, some old.

**(b) Atomicity.** An operation is **atomic** if it either happens completely or not at all, with no
visible in-between. A single Qdrant upsert of one document's points behaves this way for that document.
But **our whole ingestion run is not atomic**: it's many separate writes (one delete + one upsert per
document, then orphan deletes). Once writing has started, a failure can stop it between two documents.
That's the "D5-A fail fast" trade-off, and the next successful run repairs it, because every write is
idempotent (P3-M4).

**(c) Blue/green indexing, the way to make the *switch* atomic.** Build the new index in a **separate
collection** ("green") while readers keep using the old one ("blue"). Verify green. Then flip a
**pointer** so readers use green. In Qdrant the pointer is an **alias**: a second name for a collection.
Applications query the alias (e.g. `live_index`), never a collection name. Moving an alias from blue to
green is done in **one request containing both "delete old alias" and "create new alias"**, applied
together. Readers see either the complete old index or the complete new one, never a half-built one. The
old collection can be kept for instant rollback, then deleted. The costs: twice the storage while
building, alias management, and cleanup. We didn't choose it (D5), but it's the standard answer when an
index serves live traffic.

**Key terms.**
- **Partial failure:** some steps of a multi-step change happened and some didn't.
- **Atomic operation:** all or nothing, never observed half-done.
- **Idempotent write:** repeating it gives the same result, which is what makes "just rerun it" a valid
  repair.
- **Alias:** an extra name pointing to a collection, which can be repointed.

**Analogy.** Renovating a shop. (a) Buy all the materials before knocking down any wall, so if the
supplier fails you haven't started demolishing. (b) Renovating aisle by aisle leaves the shop half old,
half new if you stop midway. (c) Blue/green: build a whole new shop next door, check it, then move the
"OPEN" sign in one go. Customers never see the building site.

### For a 14-year-old

You're redecorating your bedroom: new wallpaper, new posters, new bed covers. Three ways to do it:

**Way 1: shop first, then decorate.** You go to the shop and buy *everything* before touching the room.
If the shop is closed or out of your wallpaper, you come home and your room is just how it was, not
messed up. That's what our program does: it turns *all* the chunks into numbers first (the "shopping",
which can fail if the AI model breaks), and only then starts changing the database.

**Way 2: shop and decorate one wall at a time.** Buy paper for wall 1, put it up; buy paper for wall 2,
put it up... If the shop closes after wall 2, your room has two new walls and two old ones. It looks
weird until you finish another day.

Even Way 1 has a moment like that. Once you've got all the stuff home and start putting it up, if your
mum calls you for dinner halfway, the room *is* half done for a while. Our program has the same
weakness while it's writing, and it fixes it by simply doing the job again next time (redoing a
finished wall doesn't hurt).

**Way 3: the "secret second room".** Imagine you had a spare room. You decorate the *spare* room
completely, check everything is perfect, and then swap the **name sign** on the doors so "Bedroom" now
points to the new room. Anyone looking for your bedroom finds either the old one or the finished new
one, never a half-decorated one. The swap happens in one move. In Qdrant, that name sign is called an
**alias**. It's the safest way, but you need a whole spare room (twice the space) while you work.

### 2. Why (vs the traditional approach)
Most simple indexers write as they go and rely on rerunning after failures. That's fine offline, but
during that window users can get answers from a half-updated index.

### 3. Minimal code

#### Example 6A — where a failure leaves the index depends on when writing starts
`c6a_write_ordering.py`
```python
# Where a failure leaves the index depends on WHEN writing starts.
# Two orders over 4 documents; the model fails on document 3 in both.
docs = ["about", "careers", "carllm", "culture"]
old_index = {d: "v1" for d in docs}


def embed(doc):
    if doc == "carllm":
        raise ConnectionError("model server went away")
    return "v2"


def embed_all_then_write(index):
    vectors = {d: embed(d) for d in docs}      # fails here, before any write
    index.update(vectors)


def embed_and_write_each(index):
    for d in docs:
        index[d] = embed(d)                     # writes as it goes


for strategy in (embed_all_then_write, embed_and_write_each):
    index = dict(old_index)
    try:
        strategy(index)
    except ConnectionError as e:
        print(f"{strategy.__name__:<22} failed ({e}) -> index: {index}")
```
Real output:
```
embed_all_then_write   failed (model server went away) -> index: {'about': 'v1', 'careers': 'v1', 'carllm': 'v1', 'culture': 'v1'}
embed_and_write_each   failed (model server went away) -> index: {'about': 'v2', 'careers': 'v2', 'carllm': 'v1', 'culture': 'v1'}
```
**How to read it.**
- Same failure (the model dies on the 3rd document), same four documents, different order of work.
- **Embed all, then write:** the exception happens inside the dict comprehension, before
  `index.update` is reached. The index is **entirely v1**, consistent, as if the run never happened.
- **Embed and write each:** the first two documents were already written, so the index is **half v2,
  half v1**. Nothing is corrupt, but the index now mixes two versions until a rerun.
- Our job uses the first order for the *embedding* failure mode. Think about what the *upsert* stage
  itself looks like in this picture: that's the heart of open Question 1.

#### Example 6B — blue/green with a Qdrant alias (live)
`c6b_blue_green_alias.py`
```python
# Blue/green indexing with a Qdrant ALIAS (the option D5-C we did not choose), on the LIVE server.
# Readers always query the alias; the switch from old to new collection is one atomic call.
from qdrant_client.models import (CreateAlias, CreateAliasOperation, DeleteAlias, DeleteAliasOperation,
                                  Distance, PointStruct, VectorParams)

from src.vectorstore.setup import build_qdrant_client

client = build_qdrant_client("http://localhost:6333")
ALIAS, BLUE, GREEN = "dd15_live_index", "dd15_blue", "dd15_green"
for name in (BLUE, GREEN):
    if client.collection_exists(name):
        client.delete_collection(name)

def build(name, version, count):
    client.create_collection(name, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    client.upsert(name, wait=True, points=[PointStruct(id=i, vector=[1.0, 0.0], payload={"version": version})
                                           for i in range(count)])

def reader():
    points, _ = client.scroll(ALIAS, limit=10)          # the "application" only knows the alias
    return f"{len(points)} points, versions {sorted({p.payload['version'] for p in points})}"

build(BLUE, "v1", 3)
client.update_collection_aliases(change_aliases_operations=[
    CreateAliasOperation(create_alias=CreateAlias(collection_name=BLUE, alias_name=ALIAS))])
print("1. alias -> blue               :", reader())

build(GREEN, "v2", 2)                                   # build the new index while readers use blue
green_half_built = client.count(GREEN).count
print("2. green built, alias unchanged:", reader(), f"(green has {green_half_built} points, invisible to readers)")

client.update_collection_aliases(change_aliases_operations=[   # atomic: delete + create in ONE request
    DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=ALIAS)),
    CreateAliasOperation(create_alias=CreateAlias(collection_name=GREEN, alias_name=ALIAS)),
])
print("3. alias switched -> green     :", reader())
print("4. aliases on server           :", [(a.alias_name, a.collection_name) for a in client.get_aliases().aliases
                                           if a.alias_name == ALIAS])

client.delete_collection(BLUE)                          # old index kept until switch succeeded, then removed
client.delete_collection(GREEN)                         # (demo cleanup; also drops the alias)
print("5. cleanup: dd15 collections left:", [c.name for c in client.get_collections().collections if c.name.startswith("dd15")])
```
Real output:
```
1. alias -> blue               : 3 points, versions ['v1']
2. green built, alias unchanged: 3 points, versions ['v1'] (green has 2 points, invisible to readers)
3. alias switched -> green     : 2 points, versions ['v2']
4. aliases on server           : [('dd15_live_index', 'dd15_green')]
5. cleanup: dd15 collections left: []
```
**How to read it.**
- **Step 1:** the reader queries `dd15_live_index` (the alias) and sees blue's 3 points (v1). It never
  uses a collection name.
- **Step 2:** green is fully built alongside, with 2 points (v2), but **the reader still sees only v1**.
  Building a new index is invisible to readers until the switch.
- **Step 3:** one `update_collection_aliases` call carries *both* the delete and the create. Immediately
  afterwards the reader sees green's 2 points (v2). No moment existed where the alias pointed nowhere, or
  where the reader saw a mix of v1 and v2.
- **Step 4:** the server confirms the alias now points to green.
- In production you'd keep blue for a while (instant rollback = switch the alias back), then delete it.

### 4. Benefits
Knowing the failure state of each design lets you choose deliberately: "embed first" protects against
model failures; idempotent writes make reruns a repair; blue/green removes the mixed window completely
at the cost of storage and complexity.

- **What breaks:** writing before the risky computation is done; assuming a multi-write job is atomic;
  blue/green without cleanup (orphaned old collections fill the disk).
- **Docs:** [Qdrant: collection aliases](https://qdrant.tech/documentation/concepts/collections/#collection-aliases) ·
  [Martin Fowler, "BlueGreenDeployment"](https://martinfowler.com/bliki/BlueGreenDeployment.html) ·
  [Martin Kleppmann, *Designing Data-Intensive Applications*, ch. 7 (transactions, atomicity)](https://dataintensive.net/)

### Micro-exercises
1. In 6A, make the failure happen on the *first* document. Do the two strategies differ now?
2. In 6A, add a third strategy "write all, then verify, then roll back on failure". What extra data do
   you need to keep?
3. In 6B, after step 3, switch the alias back to blue (rollback) before deleting it. Time the switch.
4. Estimate the peak disk usage of blue/green for our collection using doc 14's size figure (669 KB of
   vectors plus payloads).
5. Write down, without looking at open Question 1's answer anywhere, what `embed_and_write_each` in 6A
   corresponds to in `run_ingestion`.

**Internals.** Qdrant applies all operations in one `update_collection_aliases` request together under a
single alias-map update, which is why readers can't observe the intermediate "no alias" state.
**Checkpoint:** explain what "atomic" means, why our whole run isn't atomic, and how an alias switch makes
the *visible* change atomic.

---

## C7 — Verification by exact set comparison

### 1. Definition + analogy

**What it is.** **Verification** means checking that a job's *result* is correct, after it ran, by
looking at the result itself. It's different from **"no exception was raised"**, which only means no
step *noticed* a problem. Plenty of failures raise nothing: a bug that skips a document, a stale point
left by earlier code, a write to the wrong collection.

The P3-M5 invariant is: **the collection contains exactly the chunks on disk, all from the current vector
space.** The strongest cheap check of "exactly" is to compare **sets of identities**, not counts:

- `expected` = every `chunk_id` in `data/chunks/`
- `actual` = every `chunk_id` stored in Qdrant, minus documents deliberately kept as orphans (C4)
- **missing** = `expected − actual` (should be in the index but isn't)
- **unexpected** = `actual − expected` (in the index but shouldn't be)
- plus: `count_incompatible()` must be 0 (C5)

Any non-empty result means `VerificationFailed` and a failed run.

**Why counts aren't enough.** A count compares *how many*, not *which*. One missing chunk plus one stale
chunk gives the **same count** as a perfect index. Errors can cancel out, and a count can never tell you
*which* chunk is wrong.

**What verification does *not* prove.** It proves the right **IDs** are present. It doesn't prove each
vector really matches its text (a misalignment bug, doc 13, C4, would pass), or that retrieval is good
(doc 14, C11). Verification checks *completeness and consistency*, not *quality*.

**Analogy.** Checking a school trip bus before leaving. Counting heads ("30 on the list, 30 on the bus")
misses that one pupil stayed home and a pupil from another class got on. Calling the register by
name catches both.

### For a 14-year-old

Your teacher takes 30 students to a museum. Leaving, she counts heads on the bus: "28, 29, 30. Everyone's
here!"

But actually **Sam is still in the museum gift shop**, and **a kid from another school** climbed onto the
bus by mistake. The count is still 30. The teacher's check said "perfect" while two things were wrong,
and she's about to drive off without Sam.

Now imagine she **reads out every name** from her list and ticks each one off, then asks "is anyone on
this bus whose name I didn't read?" She'd immediately find two problems:
- **Missing:** Sam (on the list, not on the bus)
- **Unexpected:** the kid from the other school (on the bus, not on the list)

That's what our program does after saving everything. It doesn't just count points in the database. It
takes the list of every chunk's name from the files, and the list of every name in the database, and
compares them **name by name**. If anyone is missing or extra, the run is marked as failed, even though
nothing crashed.

One thing the name check *can't* tell you: whether each student is sitting in the right seat with the
right bag. It only proves the right people are there. Checking the bags (whether each vector really
matches its text) would be a different, more expensive check.

### 2. Why (vs the traditional approach)
Many pipelines end with `print("done")` or at most `assert count == expected`. Both declare success
without looking at *what* was stored.

### 3. Minimal code

#### Example 7A — a count passes, the set comparison fails
`c7a_count_vs_set.py`
```python
# Why verification compares SETS of chunk_ids, not counts.
disk = {"about_0", "about_1", "carllm_0", "carllm_1"}
stored = {"about_0", "about_1", "carllm_0", "old_policy_0"}    # carllm_1 missing, old_policy_0 stale

print("count check : disk", len(disk), "| stored", len(stored), "->", "PASS" if len(disk) == len(stored) else "FAIL")
missing = sorted(disk - stored)
unexpected = sorted(stored - disk)
print("set check   : missing", missing, "| unexpected", unexpected, "->", "PASS" if not (missing or unexpected) else "FAIL")
```
Real output:
```
count check : disk 4 | stored 4 -> PASS
set check   : missing ['carllm_1'] | unexpected ['old_policy_0'] -> FAIL
```
**How to read it.** The two errors cancel in the count (4 = 4), so the count check **passes a broken
index**. The set check fails *and names both problems*: `carllm_1` never made it in, and `old_policy_0`
should have been deleted. Diagnosis comes for free.

#### Example 7B — the job's verification, by hand, on the live collection
`c7b_verify_live.py`
```python
# Run the job's verification logic by hand against the LIVE 'chunks' collection.
from src.ingestion.pipeline import read_chunks
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

store = QdrantVectorStore(build_qdrant_client("http://localhost:6333"), None)

disk = {}
for c in read_chunks():
    disk.setdefault(c.doc_id, set()).add(c.chunk_id)
stored = store.stored_chunk_ids_by_document()           # paged scroll of only doc_id + chunk_id

expected = set().union(*disk.values())
actual = set().union(*stored.values())
print("documents on disk / stored :", len(disk), "/", len(stored))
print("chunk_ids on disk / stored :", len(expected), "/", len(actual))
print("missing from index         :", sorted(expected - actual)[:3], len(expected - actual))
print("unexpected in index        :", sorted(actual - expected)[:3], len(actual - expected))
print("incompatible points        :", store.count_incompatible())
print("VERIFIED" if expected == actual and store.count_incompatible() == 0 else "NOT VERIFIED")
```
Real output:
```
documents on disk / stored : 76 / 76
chunk_ids on disk / stored : 223 / 223
missing from index         : [] 0
unexpected in index        : [] 0
incompatible points        : 0
VERIFIED
```
**How to read it.**
- Grouping by `doc_id` first (`{doc_id: {chunk_ids}}`) is what lets the real job exclude *kept* orphan
  documents from `actual`.
- `set().union(*disk.values())` flattens the per-document sets into one set of all 223 IDs.
- Both directions are empty and no point is from another space, so the live collection is exactly the
  disk's 223 chunks. This is the same result the job recorded as `verified: true`.
- The query embedder is `None` because verification never searches: it only lists and counts.

### 4. Benefits
A real proof of completeness after every run, a precise diagnosis on failure, and protection against
bugs that raise no exception.

- **What breaks:** trusting counts (errors cancel); trusting "no exception"; mistaking ID verification for a
  quality check.
- **Docs:** [Qdrant: scroll points](https://qdrant.tech/documentation/concepts/points/#scroll-points) ·
  [Python sets](https://docs.python.org/3/tutorial/datastructures.html#sets)

### Micro-exercises
1. In 7A, make the errors *not* cancel (two missing, one unexpected). Does the count check catch it now?
2. In 7B, time `stored_chunk_ids_by_document()`. Extrapolate to 1 million chunks. What would you change?
3. Add a check that each stored point's `char_count` equals the disk chunk's. What class of bug would it
   catch?
4. Why exclude kept orphans from `actual` instead of adding them to `expected`? *Hint:* the message on
   failure.
5. Design a *sampled* verification: check 50 random chunk_ids both ways. What does it lose compared with
   the full check?

**Internals.** Python sets are hash tables, so `expected - actual` takes time proportional to the number
of IDs (O(n)) rather than comparing every pair (O(n²)).
**Checkpoint:** give an index state where the count matches but verification fails, and one bug
verification would *not* catch.

---

## C8 — Run logs: try/except/finally, re-raise, and clocks

### 1. Definition + analogy

**What it is.** A **run log** is a structured record of one execution of a job: when it ran, with which
settings, what it did, how long each part took, and how it ended. Ours is `data/ingestion_log.json`,
written from the `IngestionReport` dataclass. Three pieces of Python make it trustworthy.

**(a) `try` / `except` / `finally`, and why all three are used.**
- `try:` holds the stages.
- `except Exception as exc:` runs **only if a stage raised**. It records `status = "failed"`,
  `failed_stage = stage.current`, and the error text, then does **`raise`** (with no argument), which
  re-throws the *same* exception unchanged.
- `finally:` runs **always**: after a normal `return`, after the `except` re-raises, even after an error
  inside `except`. It sets `finished_at` and **writes the log**.

The result: **every run leaves a log**, successful or not, and **no failure is hidden**. The caller (the
CLI) still receives the exception and can exit with code 1 (C9).

**(b) Why re-raising matters (never swallow).** If `except` recorded the error and then *returned
normally*, the log would say "failed" while the program behaved as if all was well: exit code 0,
schedulers happy, alerts silent. A log explains a failure; it must not *replace* signalling it.

**(c) Two different clocks for two different questions.**
- **"When did it happen?"** needs **wall-clock time**: the date and time a human understands. We record
  it in **UTC** with an explicit offset (`2026-09-17T12:33:45+00:00`), because local time depends on
  where the machine is and on daylight saving, and timestamps from different machines must compare
  correctly.
- **"How long did it take?"** needs a **monotonic** clock: `time.perf_counter()` only ever moves forward,
  with very fine resolution. Wall-clock time (`time.time()`) can **jump** backward or forward if the
  system clock is corrected (e.g. by network time sync), which would produce negative or wrong durations.

**Key terms.**
- **Structured log:** machine-readable fields (JSON keys), not free-text lines.
- **Naive vs aware datetime:** naive has no timezone; aware carries one. Python refuses to compare the
  two.
- **Monotonic clock:** guaranteed never to go backwards.

**Analogy.** A ship's logbook. Every voyage gets an entry, **especially** the ones that ended badly: the
captain writes "ran aground at 14:20" *and still radios for help*. Writing in the log is not a
substitute for raising the alarm. The log uses one agreed time zone so entries from different ships line
up, and voyage durations are measured with the ship's chronometer, not by reading the clock tower in
each port.

### For a 14-year-old

**Part 1: the diary that always gets written.** Imagine you keep a diary of every football match you
play. You promise yourself: *whatever happens, I write the entry before bed.* Won? Write it. Lost? Write
it. Got injured in the 20th minute and went home? **Especially** write that, with *when* it happened
("injured in minute 20") and *what* happened.

In code, `finally` is that promise: "whatever happened, do this at the end". `except` is the part that
fills in the details when something went wrong, like which minute you got hurt.

**Part 2: writing it down isn't the same as telling someone.** Now imagine you get injured, write "hurt
my ankle" in your diary... and tell nobody. Your coach thinks you're fine and puts you in the next match.
The diary is correct, but the problem was **hidden**.

That's what "swallowing" an error means. Our program always does both: it writes the diary entry (the
log), **and** it tells the grown-ups (it re-raises the error, so the program ends with an "I failed"
signal, C9).

**Part 3: two kinds of clock.** Your diary needs two different times:
- **What time the match started**, like "3 pm". But 3 pm where? If your cousin in another country writes
  "3 pm" too, those aren't the same moment. So programs use one shared world time called **UTC** and
  write it with a "+00:00" label, so everyone knows exactly which moment it was.
- **How long you played**, like "62 minutes". For that you use a **stopwatch**, not the wall clock. If
  someone moved the wall clock forward an hour during the match (like daylight saving time), the wall
  clock would say you played 122 minutes. A stopwatch only counts forward and can't be fooled.
  `perf_counter()` is the program's stopwatch.

### 2. Why (vs the traditional approach)
Scripts often `print` progress to the console, which disappears when the terminal closes, and wrap work
in `try: ... except: print("error")`, which hides failures from anything that isn't watching the screen.

### 3. Minimal code

#### Example 8A — try / except / finally with re-raise, vs swallowing
`c8a_try_except_finally.py`
```python
# try / except / finally + re-raise: record a failure without swallowing it.
import json


def job(fail_at):
    report = {"status": "running", "failed_stage": None, "error": None, "stages_done": []}
    current = None
    try:
        for current in ("load", "embed", "write"):
            if current == fail_at:
                raise ValueError(f"something broke in {current}")
            report["stages_done"].append(current)
        report["status"] = "succeeded"
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["failed_stage"] = current
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise                                    # the caller still sees the original exception
    finally:
        print("   finally -> writing log:", json.dumps(report))


for fail_at in (None, "embed"):
    print(f"job(fail_at={fail_at!r})")
    try:
        result = job(fail_at)
        print("   returned:", result["status"])
    except ValueError as e:
        print("   caller received the exception:", repr(e))


def swallowing_job():
    try:
        raise ValueError("something broke")
    except Exception:
        return {"status": "failed"}              # no re-raise: the caller cannot tell it failed

print("swallowing version returns:", swallowing_job(), "<- no exception, exit code would be 0")
```
Real output:
```
job(fail_at=None)
   finally -> writing log: {"status": "succeeded", "failed_stage": null, "error": null, "stages_done": ["load", "embed", "write"]}
   returned: succeeded
job(fail_at='embed')
   finally -> writing log: {"status": "failed", "failed_stage": "embed", "error": "ValueError: something broke in embed", "stages_done": ["load"]}
   caller received the exception: ValueError('something broke in embed')
swallowing version returns: {'status': 'failed'} <- no exception, exit code would be 0
```
**How to read it.**
- **Success:** `finally` runs *after* `return report` was reached but *before* the caller receives the
  value. That's why "writing log" prints before "returned". A `finally` runs even on the way out of a
  `return`.
- **Failure in `embed`:** order of events: exception → `except` fills in status, stage and error →
  `raise` re-throws → `finally` writes the log (`stages_done: ["load"]` shows how far it got) → the
  caller catches the **original** `ValueError`.
- **Swallowing version:** it *returns* `{"status": "failed"}` instead of raising. Any caller that doesn't
  inspect the dictionary treats this as success. As a script, it would exit with code 0.

#### Example 8B — UTC timestamps and the right clock for durations
`c8b_clocks_and_utc.py`
```python
# Two different clocks: wall-clock time for "when", a monotonic counter for "how long".
import time
from datetime import datetime, timezone

local = datetime.now()                               # naive: no timezone attached
utc = datetime.now(timezone.utc)                     # aware: carries +00:00
print("naive local  :", local.isoformat(timespec="seconds"), "| tzinfo:", local.tzinfo)
print("aware UTC    :", utc.isoformat(timespec="seconds"), "| tzinfo:", utc.tzinfo)

# Comparing a naive and an aware timestamp is an error, not a silent mistake
try:
    local < utc
except TypeError as e:
    print("naive < aware:", e)

# time.time() is wall-clock seconds (can jump if the system clock is adjusted);
# time.perf_counter() only moves forward and has fine resolution -> right for durations
print("clock info time         :", time.get_clock_info("time").monotonic, "monotonic,",
      time.get_clock_info("time").resolution, "s resolution")
print("clock info perf_counter :", time.get_clock_info("perf_counter").monotonic, "monotonic,",
      time.get_clock_info("perf_counter").resolution, "s resolution")
start = time.perf_counter()
sum(range(1_000_000))
print(f"summing 1,000,000 ints took {time.perf_counter() - start:.4f}s (measured with perf_counter)")
```
Real output:
```
naive local  : 2026-09-17T19:57:30 | tzinfo: None
aware UTC    : 2026-09-17T14:27:30+00:00 | tzinfo: UTC
naive < aware: can't compare offset-naive and offset-aware datetimes
clock info time         : False monotonic, 0.015625 s resolution
clock info perf_counter : True monotonic, 1e-07 s resolution
summing 1,000,000 ints took 0.1109s (measured with perf_counter)
```
**How to read it.**
- **Same moment, two strings:** local `19:57:30` (this machine is 5 h 30 min ahead of UTC) and UTC
  `14:27:30+00:00`. Without the `+00:00`, a reader can't know which one a log line means.
- **Naive vs aware can't be compared.** Python raises a `TypeError` instead of guessing, which is why the
  log always writes aware UTC times.
- **`time.time()` on this machine:** *not* monotonic, and a **resolution of 0.015625 s (1/64 s)**, so
  anything shorter than ~16 ms would measure as 0 or 16 ms.
- **`perf_counter()`:** monotonic, with a resolution of **0.1 microseconds**. That's why the log can
  report `load 0.019 s` meaningfully.

### 4. Benefits
Every run is inspectable after the fact, failures are both recorded **and** signalled, and times are
unambiguous and accurate.

- **What breaks:** a log written only on success (failed runs leave no trace; mutation-tested: 7 tests
  fail); swallowed exceptions (exit code 0 on failure); naive local timestamps; `time.time()` durations.
- **Docs:** [Python tutorial: `finally` clauses](https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions) ·
  [`datetime` aware vs naive](https://docs.python.org/3/library/datetime.html#aware-and-naive-objects) ·
  [PEP 418 (monotonic clocks, `perf_counter`)](https://peps.python.org/pep-0418/) ·
  [ISO 8601 date and time format](https://en.wikipedia.org/wiki/ISO_8601)

### Micro-exercises
1. In 8A, put `return {"status": "overridden"}` inside `finally`. What happens to the exception? *Hint:*
   a `return` in `finally` discards it. Never do this.
2. In 8A, make the `finally` block itself raise. Which exception reaches the caller?
3. Convert `2026-09-17T12:33:45+00:00` (the real run's start) to your local time with
   `datetime.fromisoformat(...).astimezone()`.
4. Time a 5 ms `time.sleep` 20 times with `time.time()` and with `perf_counter()`. Compare the spread.
5. Why does the job's `except` use `raise` and not `raise exc`? *Hint:* both keep the traceback in
   Python 3; which one states the intent more clearly?

**Internals.** When an exception occurs in `try`, Python looks for a matching `except`, runs it, and, on
leaving the whole statement by any path (return, re-raise, new exception), runs `finally`. If `finally`
completes normally, the pending exception or return value continues as if `finally` weren't there.
**Checkpoint:** describe the exact order of `except`, `raise` and `finally` when a stage fails, and why the
log can't be lost.

---

## C9 — Exit codes: telling other programs what happened

### 1. Definition + analogy

**What it is.** When any program ends, it hands the operating system a small integer, its **exit code**
(also called exit status or return code). By universal convention, **0 means success** and **any
non-zero value means failure**. Other programs use it without reading any output:

- a shell: `ingest && run_evaluation` runs the second command **only if** the first exited 0;
- CI systems (GitHub Actions) mark a step red on non-zero;
- schedulers (cron, Airflow, Kubernetes Jobs) retry or alert on non-zero;
- Docker `HEALTHCHECK` and Kubernetes probes treat non-zero as unhealthy.

In Python:
- the program exits 0 when it runs to the end;
- `raise SystemExit(n)` (what `sys.exit(n)` does) exits with code `n`;
- an **uncaught exception** prints a traceback and exits with **1**.

Our CLI: `run_from_args` returns `0` if the run succeeded and `1` if it raised; `main` does
`raise SystemExit(run_from_args(...))`. `store health` exits 0 if ready, 1 if not.

**The trap:** printing "FAILED" is **not** failing. Text is for humans; the exit code is for machines. A
script that prints an error message and then ends normally exits **0**, and every automated system
believes it succeeded.

**Analogy.** A traffic light at the end of a factory line. Workers read the detailed report, but the
next machine on the line only looks at the light: green (0) means "take the product", red (anything
else) means "stop". A report saying "defective" next to a green light still sends the product onwards.

### For a 14-year-old

When you finish your homework, you can tell your parents two ways:
1. **A long explanation:** "So, I did maths, but question 4 was really confusing, and then..."
2. **A thumbs up 👍 or thumbs down 👎.**

A parent who's busy cooking won't listen to the long story. They glance over: thumbs up, fine; thumbs
down, come and help. Computer programs work the same way. When a program finishes, it gives a "thumbs"
signal called an **exit code**: **0 means thumbs up**, **anything else means thumbs down**.

Other programs only look at the thumb. For example, you can tell the computer "run the ingestion, **and
only if it worked**, run the tests". The computer never reads what ingestion printed; it only checks for
0.

The sneaky mistake: a program that *prints* "I FAILED!!" but then gives a **thumbs up** (exit code 0).
It's like saying "I didn't do my homework" while doing a thumbs up. The busy parent only sees the thumb
and thinks everything's fine. Our ingestion program is careful: if anything went wrong, it gives a thumbs
**down** (exit code 1), so no other program is fooled.

### 2. Why (vs the traditional approach)
Many personal scripts end with `print("error: ...")` inside an `except` and fall off the end. They work
fine interactively, and they break every automation built around them.

### 3. Minimal code

#### Example 9 — what exit code each ending produces, and how a shell uses it
`c9_exit_codes.py`
```python
# Exit codes: how a script tells other programs whether it worked, without anyone reading its output.
import subprocess
import sys

cases = {
    "success": "raise SystemExit(0)",
    "failure": "raise SystemExit(1)",
    "uncaught exception": "raise ValueError('boom')",
    "printed 'FAILED' but exited normally": "print('FAILED')",
}
for label, code in cases.items():
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    print(f"{label:<38} -> exit code {r.returncode}")

# How a shell uses it: the second command only runs if the first returned 0
for first in ("raise SystemExit(0)", "raise SystemExit(1)"):
    r = subprocess.run(f'"{sys.executable}" -c "{first}" && echo "  next step ran"', shell=True,
                       capture_output=True, text=True)
    print(f"`{first}` && next ->", r.stdout.strip() or "  next step skipped")

r = subprocess.run([sys.executable, "-m", "src.vectorstore.store", "health"], capture_output=True, text=True)
print("real CLI `store health` -> exit code", r.returncode, "|", r.stdout.strip())
```
Real output:
```
success                                -> exit code 0
failure                                -> exit code 1
uncaught exception                     -> exit code 1
printed 'FAILED' but exited normally   -> exit code 0
`raise SystemExit(0)` && next -> "  next step ran"
`raise SystemExit(1)` && next ->   next step skipped
real CLI `store health` -> exit code 0 | ready
```
**How to read it.**
- **Rows 1–3:** explicit success is 0, explicit failure is 1, and an uncaught exception is also 1.
- **Row 4, the trap:** the program printed `FAILED`, yet exit code **0**. Any automation would treat it
  as a success.
- **The `&&` lines:** after exit 0, "next step ran"; after exit 1, the next step was **skipped**. (The
  quotes around the first line come from Windows `cmd`'s `echo`, which prints them literally.)
- **Real CLI:** `store health` on the running server returns 0 with `ready`. Example 2 showed
  `ingest` returning **1** when preflight refused.

### 4. Benefits
Ingestion can be chained, scheduled and monitored by standard tools, and failures stop pipelines instead
of flowing downstream.

- **What breaks:** catching every exception and exiting normally; exiting 1 for *every* problem when
  callers need to distinguish (use different non-zero codes then).
- **Docs:** [Python `sys.exit` / `SystemExit`](https://docs.python.org/3/library/sys.html#sys.exit) ·
  [POSIX shell: exit status and `&&` lists](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_09_03) ·
  [GitHub Actions: exit codes set step status](https://docs.github.com/en/actions/creating-actions/setting-exit-codes-for-actions)

### Micro-exercises
1. Run `python -m src.vectorstore.ingest --log scratch.json && echo OK` with Qdrant stopped. Does `OK`
   print?
2. What exit code does `raise SystemExit("some message")` produce? *Hint:* try it; a non-integer argument
   is printed and gives 1.
3. Change the CLI to exit 2 for `IngestionAborted` and 1 for other failures. Who benefits?
4. Write a two-line shell script that runs ingestion and then `store search "Who founded Insurellm?"`
   only on success.
5. Why does `run_from_args` *return* the code instead of calling `sys.exit` itself? *Hint:* `store index`
   reuses it.

**Internals.** On Windows the exit code is the process's `ExitProcess` value; on POSIX, the low 8 bits of
the value passed to `exit()`. Python's interpreter shutdown converts `SystemExit.code` (int → that value,
`None` → 0, anything else → printed, then 1).
**Checkpoint:** explain why printing an error is not the same as failing, from the point of view of a
scheduler.

---

## C10 — Mutation testing as design feedback

### 1. Definition + analogy

**What it is.** Doc 13 (C8) introduced **mutation testing**: deliberately break one piece of code (a
*mutant*) and check that some test fails ("killed"). If all tests still pass, the mutant **survived**.

P3-M5 showed the other half of its value: a surviving mutant doesn't always mean "add a test". There are
two very different reasons a mutant can survive:

1. **A missing test.** The code matters, but nothing checks it. *P3-M5 example:* "verification ignores
   incompatible points" survived because no test had an incompatible point left *after* writing. The fix
   was to **add a test**: rebuild while a stale orphan is protected. That run must *fail* verification.
2. **Redundant code.** The code **can't** change the outcome, because something earlier already
   guarantees what it checks. No test can kill such a mutant, since there's no behaviour to observe.
   *P3-M5 example:* the preflight `health_check()` call survived. `ensure_collection` runs right
   before it and already raises for an unreachable server or a wrongly shaped collection, and creates a
   missing one. After it succeeds, `health_check()` can only return `True`. The fix was to **delete the
   call** (D-2026-09-17-33).

Telling them apart requires reasoning: "is there *any* input where this line changes what happens?" If
yes, write the test that shows it. If no, the line is dead weight, and keeping it costs reading time and
a false sense of safety ("we have a health check!").

**Key terms.**
- **Killed / survived mutant:** a test failed / no test failed.
- **Equivalent mutant:** a change that doesn't alter behaviour at all, so no test *can* kill it. A
  redundant check produces exactly this.
- **Guarantee / postcondition:** what a function promises is true after it returns successfully.

**Analogy.** A house with a smoke detector wired to the fire alarm, and a second detector wired to
nothing. Pulling the second one off the ceiling changes nothing. That doesn't mean you need a test for
it; it means it isn't doing anything.

### For a 14-year-old

To leave your house, you go through two doors: the front door, then the garden gate.

The **front door** has a lock that only opens if you have your key, your phone and your jacket. If you're
missing any of them, it won't open. (That's `ensure_collection`: it refuses unless everything's right.)

Someone also put a sign on the **garden gate**: "STOP! Do you have your key, phone and jacket?" (That's
the `health_check`.)

Here's the question: **can anyone ever be stopped by the garden-gate sign?** To reach the gate you must
have come through the front door, and the front door only opened because you already had all three
things. So the sign can **never** stop anyone. It's not protecting you; it just *looks* like protection.

How did we find that out? By a trick called **mutation testing**. You secretly **take the sign down**
and see if anything goes wrong in all your tests. If nobody ever notices, you ask *why*:
- **Maybe your tests just aren't looking in the right place.** Then write a better test. (That happened
  with another check, and we added a test for it.)
- **Or maybe the sign really can't matter**, like the garden-gate sign. Then remove it, because it only
  makes the house more confusing.

Our program had one of each, and we fixed both.

### 2. Why (vs the traditional approach)
Code reviews tend to *add* defensive checks ("just to be safe") and rarely remove them. Over time the
checks that matter get buried under checks that can't fail.

### 3. Minimal code

#### Example 10 — proving a check can never change the outcome
`c10_redundant_check.py`
```python
# Mutation testing as design feedback: a check that can never fail after an earlier guarantee.
class Mismatch(Exception):
    pass


def ensure_ok(collection):
    # like ensure_collection: creates if missing, RAISES if misconfigured
    if collection.get("size") is None:
        collection["size"] = 768
    if collection["size"] != 768:
        raise Mismatch("wrong size")


def health(collection):
    # like health_check: returns False if missing or misconfigured
    return collection.get("size") == 768


def preflight(collection, use_health=True):
    ensure_ok(collection)
    if use_health and not health(collection):
        return "refused by health check"
    return "passed"


for label, collection in [("missing", {}), ("correct", {"size": 768}), ("wrong size", {"size": 384})]:
    results = []
    for use_health in (True, False):
        try:
            results.append(preflight(dict(collection), use_health))
        except Mismatch as e:
            results.append(f"raised {e}")
    print(f"{label:<11} with health: {results[0]:<18} | without health: {results[1]}")
print("-> identical in every case: the health check never changes the outcome after ensure_ok")
```
Real output:
```
missing     with health: passed             | without health: passed
correct     with health: passed             | without health: passed
wrong size  with health: raised wrong size  | without health: raised wrong size
-> identical in every case: the health check never changes the outcome after ensure_ok
```
**How to read it.**
- Three possible collection states: missing, correct, wrong size. For each, run preflight **with** and
  **without** the health check.
- **Missing:** `ensure_ok` creates it (size 768), so `health` then sees a correct collection.
- **Wrong size:** `ensure_ok` raises *before* `health` is reached.
- **Correct:** both pass.
- **The two columns are identical in every state.** There's no input where the health check matters, so it's
  an *equivalent mutant*. Deleting it loses nothing. The same reasoning, applied to the real
  `ensure_collection` and `health_check`, is why the call was removed from `run_ingestion`.
- `health_check` itself still exists and is still useful **elsewhere** (`store health`), where no
  `ensure_collection` runs first. Redundancy is about *position*, not about the function.

### 4. Benefits
Tests that actually protect behaviour, and code without decorative checks that mislead readers.

- **What breaks:** "adding a test" to kill an equivalent mutant (impossible, and wasted effort); deleting a
  check that *does* matter because the tests were weak (always look for an input first).
- **Docs:** [mutmut](https://mutmut.readthedocs.io/) ·
  [Wikipedia: Mutation testing — equivalent mutants](https://en.wikipedia.org/wiki/Mutation_testing#Equivalent_mutants) ·
  Petrović & Ivanković, "State of Mutation Testing at Google" (ICSE-SEIP 2018)

### Micro-exercises
1. In example 10, change `ensure_ok` so it *doesn't* create a missing collection. Does `health` matter
   now? For which state?
2. Find the P3-M5 mutant table in the notes and explain why "log only on success" failed 7 tests but
   "empty-disk check off" failed only 1.
3. Look at `QdrantVectorStore.upsert`: is `zip(..., strict=True)` in the embedder redundant given the count
   check? *Hint:* doc 13 calls it a "second line of defence". Is a redundant check sometimes worth
   keeping?
4. Write a mutant for `_pipeline_failures` (e.g. always return 0) and predict which test kills it
   before running.
5. Give one reason to keep a check that mutation testing proves redundant *today*. *Hint:* what if the
   earlier guarantee changes later?

**Internals.** Proving a mutant equivalent in general is undecidable (it's equivalent to deciding whether
two programs behave identically), which is why tools report "survived" and leave the reasoning to you.
**Checkpoint:** name the two reasons a mutant can survive and the right response to each.

---

## Validation

Run these from the project root; each should match.

```bash
.venv/Scripts/python -m pytest tests/vectorstore/test_ingest.py -q -p no:warnings
# 12 passed
.venv/Scripts/python -m pytest tests -q -p no:warnings
# 161 passed
.venv/Scripts/python "practice codes/phase 3/m5_ingestion_deep_dive/c7b_verify_live.py"
# ... VERIFIED
.venv/Scripts/python -c "import json; d=json.load(open('data/ingestion_log.json')); print(d['status'], d['verified'], d['points_after'])"
# succeeded True 223
git check-ignore data/ingestion_log.json
# data/ingestion_log.json
```

Self-check questions (answer without looking):
1. Name the six stages in order, and which ones write to Qdrant.
2. Why can't the P3-M4 upsert guard catch a changed prefix, and what does preflight compare instead?
3. Under what condition does the job refuse to delete orphans, and why?
4. Give an index state where the point count is right but verification fails.
5. Why does the job both write a log **and** re-raise?

## Visualization

**Stages, what they touch, and what they cost (real P3-M5 run)**
```
stage      reads          writes        seconds   can refuse the run?
preflight  Ollama, Qdrant (creates coll.) 0.156    yes: model, collection shape, incompatible points
load       data/chunks    —              0.019    yes: no chunks
embed      Ollama         —            197.709    (errors)
upsert     —              Qdrant         4.758    (errors)
orphans    Qdrant, report Qdrant         0.016    no — keeps orphans + warning when chunking failed
verify     Qdrant, disk   —              0.047    yes: missing / unexpected / incompatible
─────────────────────────────────────────────────
always     —              data/ingestion_log.json          exit code 0 / 1
```

**The sync picture (C4)**
```
         stored in Qdrant (S)        on disk (D)
        ┌───────────────┬───────────┬───────────────┐
        │  S − D        │  S ∩ D    │  D − S        │
        │  orphans      │  replace  │  insert       │
        │  (delete*)    │           │               │
        └───────────────┴───────────┴───────────────┘
        * kept, with a warning, if report.json shows documents_failed > 0
```

**Tools to see it yourself**
- **The log:** `data/ingestion_log.json`; `c1b_real_log_stages.py` turns it into a time bar chart.
- **Qdrant dashboard:** http://localhost:6333/dashboard, to watch `points_count` before and after a run,
  and to see aliases (after example 6B, before cleanup).
- **Exit codes:** `echo $?` (Git Bash) or `echo %ERRORLEVEL%` (cmd) right after a run.
- **History plot:** after exercise 3 of the notes (JSONL history), plot `stage_seconds.embed` per run with
  matplotlib to see drift over time.

## End-to-end "watch it work" exercise

Do these in order and write down what you observe at each step.

1. **Read the last run.** Run `c1b_real_log_stages.py`. Which stage would you optimise first, and why?
2. **Prove it's in sync.** Run `c7b_verify_live.py`, and confirm `VERIFIED`.
3. **Trip the preflight on purpose.** Run `c2_preflight_cost.py`. Note the exit code and time, and confirm
   the real collection still has 223 points (dashboard or `store health`).
4. **Watch a sync.** Run `c4b_orphans_live.py`. Explain why run 2 kept the document and run 3 removed it.
5. **Break the model mid-run (on a scratch collection only).** Copy 3 chunk files to a temp folder, start
   `python -m src.vectorstore.ingest --collection dd15_break --chunks-dir <temp> --log break.json`,
   and run `docker compose stop ollama` while it embeds. Read `break.json`: which stage failed? Is
   `dd15_break` empty or partly written? Start Ollama again and delete `dd15_break`.
6. **See a safe switch.** Run `c6b_blue_green_alias.py` and describe what a user querying the alias would
   have seen at each step.
7. **Chain it.** Run `python -m src.vectorstore.ingest && python -m src.vectorstore.store search "Who founded Insurellm?" -k 3`.
   Then do the same with Qdrant stopped, and confirm the search never runs.
8. **Log it.** Add a `LEARNING_LOG.md` line: which concept changed how you'd design your own job, and your
   answer to open Question 1 now that you've done step 5.

## Canonical sources
- Python: [`contextlib`](https://docs.python.org/3/library/contextlib.html) · [errors and `finally`](https://docs.python.org/3/tutorial/errors.html) · [`datetime` aware/naive](https://docs.python.org/3/library/datetime.html#aware-and-naive-objects) · [PEP 418 clocks](https://peps.python.org/pep-0418/) · [`sys.exit`](https://docs.python.org/3/library/sys.html#sys.exit) · [`hashlib`](https://docs.python.org/3/library/hashlib.html)
- Qdrant: [collection aliases](https://qdrant.tech/documentation/concepts/collections/#collection-aliases) · [nested filters](https://qdrant.tech/documentation/concepts/filtering/#nested-filters) · [scroll](https://qdrant.tech/documentation/concepts/points/#scroll-points)
- Incremental indexing: [LangChain indexing API](https://python.langchain.com/docs/how_to/indexing/) · [LlamaIndex ingestion pipeline](https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/)
- Deployment and data systems: [Martin Fowler, BlueGreenDeployment](https://martinfowler.com/bliki/BlueGreenDeployment.html) · [Kleppmann, *Designing Data-Intensive Applications*](https://dataintensive.net/) · [Jim Shore, "Fail Fast"](https://www.martinfowler.com/ieeeSoftware/failFast.pdf)
- ML systems: [Sculley et al., "Hidden Technical Debt in Machine Learning Systems" (NeurIPS 2015)](https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html)
- Mutation testing: [mutmut](https://mutmut.readthedocs.io/) · Petrović & Ivanković, "State of Mutation Testing at Google" (ICSE-SEIP 2018)
- Exit status: [POSIX shell command language](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html)
