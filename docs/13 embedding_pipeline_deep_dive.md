# 13 — Embedding Pipeline: Deep Dive (P3-M3)

> Every output block in this document was produced by running the code on 2026-09-17 against
> Ollama **0.20.0** serving `nomic-embed-text` (digest `0a109f422b47…`) and the real 223-chunk
> corpus. All example scripts are saved in
> [`practice codes/phase 3/m3_embedding_deep_dive/`](../practice%20codes/phase%203/m3_embedding_deep_dive/).
> Run any of them from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 3/m3_embedding_deep_dive/c1a_batching_loop.py"
> ```
>
> Scripts marked **(live)** need the Ollama container running (`docker compose up -d ollama`).

---

## 0. Plain-language summary

P2 produced 223 chunks of *text*. Qdrant can only search *numbers*. P3-M3 is the step that turns
each chunk into a 768-number vector, and it is the **only** place in the codebase where that
happens. Because of that, it is the right place to enforce every rule from P3-M1/M2:

```
data/chunks/*.json
      │  read_chunks()                          (C7: contract round-trip)
      ▼
list[DocumentChunk]
      │  Embedder(client)  ── verify_model ──►  digest == pin?        (C3: choke point)
      │  embed_batch(chunks)
      │     for each batch of 16:                                     (C1: batching)
      │        texts = "search_document: " + chunk.text               (C3)
      │        client.embed(model, texts, truncate=False)             (C5: failure handling, C6: Protocol)
      │        _validate: count, 768 dims, finite, norm ≈ 1           (C4: validation)
      │        Embedding(chunk, vector, model, digest, prefix)        (C2: the contract)
      ▼
list[Embedding]  ──►  P3-M4: upsert into Qdrant     (C9: first nearest-neighbour search)
```

Tests prove the safeguards are real, not decorative (C8: mutation testing).

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | Batch embedding | 🔴 **Core RAG** (throughput of every indexing job) |
| C2 | The `Embedding` contract: immutability, pairing, provenance | 🔴 **Core RAG** |
| C3 | Enforcing invariants at a single choke point | 🔴 **Core RAG** (prefix, truncation, pinning) |
| C4 | Validating vectors before storage | 🟡 Engineering (interview-heavy) |
| C5 | Batch-level failure, error wrapping, catch by type | 🟡 Engineering |
| C6 | Protocols and fakes that encode their input | ⚪ Boilerplate pattern (but classic interview topic) |
| C7 | Contract round-trip to disk | ⚪ Boilerplate |
| C8 | Mutation testing | 🟡 Engineering (rarely known, impressive in interviews) |
| C9 | Brute-force nearest-neighbour search | 🔴 **Core RAG** (what Qdrant will do for us) |

---

## ⚠️ Correction found while verifying this document

P3-M3 chose batch size 16 partly because a measurement said *16 was fastest*
(1 → 93.9 s, 16 → 63.9 s, 64 → 76.8 s for 64 chunks). Re-measuring properly for this deep dive
(model loaded first, same 32 chunks for every size) showed **no speed difference at all** on
this CPU:

```
batch_size= 1: 32 requests,  27.6s, 1.16 chunks/s
batch_size= 4:  8 requests,  27.7s, 1.16 chunks/s
batch_size= 8:  4 requests,  27.7s, 1.16 chunks/s
batch_size=16:  2 requests,  28.7s, 1.12 chunks/s
batch_size=32:  1 requests,  27.2s, 1.18 chunks/s
```

The first reading was a **cold-start artefact**: the batch-1 run went first and paid the
model-loading cost, and nothing was repeated. 16 is still a fine default, but for different
reasons: fewer round trips, and a failure affects 16 chunks instead of everything. The config
comment, `decision.md` D-2026-09-17-04, the notes and the learning log have been corrected.
**Lesson: warm up, keep inputs identical, and repeat before trusting a benchmark.**

---

## C1 — Batch embedding

### 1. Definition + analogy

**What it is.** An embedding model is a function `text → vector`. *Batching* means sending
**several texts in one request** and getting back **a list of vectors in the same order**:

```
request : {"model": "nomic-embed-text", "input": ["text A", "text B", "text C"], "truncate": false}
response: {"embeddings": [[...768 numbers for A...], [...for B...], [...for C...]], "prompt_eval_count": 5119, ...}
```

The only link between `input[i]` and `embeddings[i]` is **position**. That fact drives C2 and
C4.

**Why batching can be faster.** Every request carries fixed costs that have nothing to do with
the text itself:
1. **Network round trip:** opening/using the HTTP connection, serialising JSON both ways.
2. **Scheduling:** Ollama queues the request, finds the loaded model, prepares memory.
3. **Compute:** running the text through the model.

Batching shares costs 1 and 2 across many texts. On a **GPU**, cost 3 also shrinks per text,
because the GPU runs many texts *in parallel* (one big matrix multiplication instead of many
small ones). On a **CPU** like this machine, cost 3 dominates and texts are effectively processed
one after another, so, as measured above, batching barely changes the total time. Costs 1 and 2
are tiny next to ~0.9 s of compute per chunk.

**Does batching change the vectors?** It must not, and here it doesn't (example 1B:
max difference **0.0**). Some model servers *pad* shorter texts to the length of the longest in
the batch; a correct implementation masks the padding so it has no effect. Verifying this once
is cheap insurance.

**What batch size controls.** It is a *throughput and failure-blast-radius* knob, never a
*quality* knob:

| Batch size | Requests for 223 chunks | If one chunk is rejected… | Memory per request |
|---|---|---|---|
| 1 | 223 | only that chunk's request fails | smallest |
| 16 | 14 | its batch of 16 fails | medium |
| 223 | 1 | everything fails | largest (and risks HTTP timeouts) |

**Analogy.** Posting letters. Walking to the post box once per letter (batch 1) versus
carrying a bundle (batch 16). If the walk is short and *writing* each letter takes the real
time (CPU), bundling saves almost nothing. If the post office can process a whole bundle at once
(GPU), bundling saves a lot. And if the bundle is lost, you lose every letter in it.

**For a 14-year-old.** Instead of asking the model "what does this mean?" 223 times, we hand
it 16 pages at a time. It answers with 16 lists of numbers in the same order as the pages.

### 2. Why (vs the traditional approach)

The naive loop `for chunk in chunks: vectors.append(embed(chunk))` works, but it ties the
number of network calls to the corpus size, and it gives no natural unit for errors or progress.
Libraries such as Sentence-Transformers (`encode(batch_size=32)`) and hosted APIs (OpenAI accepts
a list of inputs) all expose batching for the same reason.

### 3. Minimal code

#### Example 1A — how a list is cut into batches (no Ollama)
`c1a_batching_loop.py`
```python
# How embed_batch cuts a list into batches -- pure Python, no Ollama.
items = [f"chunk_{i}" for i in range(40)]
batch_size = 16

# range(start, stop, step) yields the START index of every batch: 0, 16, 32
starts = list(range(0, len(items), batch_size))
print("batch start indices:", starts)

for start in starts:
    batch = items[start : start + batch_size]      # slicing past the end is safe in Python
    print(f"batch [{start:>2}:{start + batch_size:>2}] -> {len(batch):>2} items, first={batch[0]}, last={batch[-1]}")

# The number of requests is the ceiling of len / batch_size
import math
print("requests needed:", math.ceil(len(items) / batch_size))
print("items[32:48] on a 40-item list has", len(items[32:48]), "items (no IndexError)")
```
Real output:
```
batch start indices: [0, 16, 32]
batch [ 0:16] -> 16 items, first=chunk_0, last=chunk_15
batch [16:32] -> 16 items, first=chunk_16, last=chunk_31
batch [32:48] ->  8 items, first=chunk_32, last=chunk_39
requests needed: 3
items[32:48] on a 40-item list has 8 items (no IndexError)
```
**How to read it.**
- `range(0, 40, 16)` produces only the *start* of each batch. This is exactly the loop header
  in `Embedder.embed_batch`.
- The last slice asks for `[32:48]` on a 40-item list. Python slicing clamps to the end, so the
  final batch simply has 8 items. No special "last batch" code is needed.
- Requests = ⌈40 / 16⌉ = 3. For the real corpus: ⌈223 / 16⌉ = **14**, which matches the 14
  requests seen in the P3-M3 live run.

#### Example 1B — one request per text vs one request for all (live)
`c1b_batch_vs_single.py`
```python
# One request per text vs one request for all texts: same vectors? how many round trips?
import time
import numpy as np
import ollama
from src.embedding.lock import as_document
from src.ingestion.pipeline import read_chunks

client = ollama.Client(host="http://localhost:11434")
texts = [as_document(c.text) for c in read_chunks()[:16]]
client.embed(model="nomic-embed-text", input=["warm-up"])   # load the model first so timing is fair

# A: 16 separate HTTP requests
t = time.perf_counter()
single = [client.embed(model="nomic-embed-text", input=[x], truncate=False)["embeddings"][0] for x in texts]
t_single = time.perf_counter() - t

# B: 1 HTTP request carrying all 16 texts
t = time.perf_counter()
response = client.embed(model="nomic-embed-text", input=texts, truncate=False)
batched = response["embeddings"]
t_batch = time.perf_counter() - t

print(f"A: 16 requests -> {len(single)} vectors in {t_single:.1f}s")
print(f"B:  1 request  -> {len(batched)} vectors in {t_batch:.1f}s")
print("response fields besides embeddings:", {k: v for k, v in dict(response).items() if k != "embeddings"})
diff = max(float(np.abs(np.array(a) - np.array(b)).max()) for a, b in zip(single, batched))
print("largest difference between A and B, over all 16x768 numbers:", diff)
```
Real output:
```
A: 16 requests -> 16 vectors in 15.1s
B:  1 request  -> 16 vectors in 14.7s
response fields besides embeddings: {'model': 'nomic-embed-text', 'created_at': None, 'done': None, 'done_reason': None, 'total_duration': 14678367427, 'load_duration': 16248025, 'prompt_eval_count': 5119, 'prompt_eval_duration': None, 'eval_count': None, 'eval_duration': None}
largest difference between A and B, over all 16x768 numbers: 0.0
```
**How to read it.**
- **Correctness first:** across 16 × 768 = 12,288 numbers, the largest difference is `0.0`.
  Batching is *safe* here.
- **Speed:** 15.1 s vs 14.7 s, about 3 %. On this CPU, compute dominates, so saving 15 round trips
  barely matters.
- **`total_duration` is in nanoseconds:** 14,678,367,427 ns ≈ 14.7 s, the same as our stopwatch.
  **`load_duration` 16 ms** confirms the model was already loaded thanks to the warm-up call.
  Remove the warm-up line and watch this number jump: that is the trap behind the correction
  above.
- **`prompt_eval_count` 5119** is the total number of tokens read for all 16 texts together,
  about 320 per chunk on average.

#### Example 1C — sweeping batch sizes (live, ~2.5 minutes)
`c1c_batch_sweep.py`
```python
# Throughput for different batch sizes on the SAME 32 chunks (model already warm).
import time
import ollama
from src.embedding.lock import as_document
from src.ingestion.pipeline import read_chunks

client = ollama.Client(host="http://localhost:11434")
texts = [as_document(c.text) for c in read_chunks()[:32]]
client.embed(model="nomic-embed-text", input=["warm-up"])

for batch_size in (1, 4, 8, 16, 32):
    t = time.perf_counter()
    for start in range(0, len(texts), batch_size):
        client.embed(model="nomic-embed-text", input=texts[start:start + batch_size], truncate=False)
    elapsed = time.perf_counter() - t
    print(f"batch_size={batch_size:>2}: {len(texts) // batch_size + (len(texts) % batch_size > 0):>2} requests, "
          f"{elapsed:5.1f}s, {len(texts) / elapsed:.2f} chunks/s")
```
Real output: the table in the correction section above (all ≈ 1.16 chunks/s).
**How to read it.** Requests fall from 32 to 1, but time stays flat. So on this machine, time is
spent *inside the model*, not in HTTP. A benchmark is only meaningful when everything except the
variable under test is held constant: same texts, warm model, same machine.

### 4. Benefits
- Far fewer network calls (14 instead of 223), which matters a lot against a remote or GPU server.
- A natural unit for progress logging (`Embedded 48/223 chunks`) and for error reporting.
- A bounded blast radius when something fails.

- **Why here:** the whole corpus must be embedded now, and again whenever chunking or the model
  changes.
- **What breaks if batching is done wrong:** a batch too large times out; an implementation that
  pads without masking silently changes vectors (checked: not the case here).
- **Docs:** [Ollama `/api/embed`](https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings) ·
  [Sentence-Transformers `encode(batch_size)`](https://sbert.net/docs/package_reference/sentence_transformer/SentenceTransformer.html#sentence_transformers.SentenceTransformer.encode) ·
  [OpenAI embeddings (list input)](https://platform.openai.com/docs/api-reference/embeddings/create)

### Micro-exercises
1. In 1A, change `batch_size` to 7 and predict the batch boundaries before running.
   *Hint:* ⌈40/7⌉.
2. Remove the warm-up line in 1B, restart the container (`docker compose restart ollama`) and
   rerun. Compare `load_duration`. *Hint:* this reproduces the flawed first benchmark.
3. Run 1C twice in a row. How much do the numbers vary between runs? *Hint:* that variance is
   your measurement noise.
4. In 1B, print `prompt_eval_count` for each single request and check they add up to 5119.
   *Hint:* `response["prompt_eval_count"]`.
5. Explain in two sentences why a GPU would change the result of 1C. *Hint:* parallel matrix
   multiplication.

**How it works internally.** `ollama.Client.embed` sends one `POST /api/embed` with a JSON list.
The server tokenises each text, runs the encoder, pools each text's token vectors into one
768-number vector, normalises it to length 1, and returns the list in input order.
**Checkpoint:** you can explain why batch size affects speed and failure scope but must never
affect vector values.

---

## C2 — The `Embedding` contract: immutability, pairing, provenance

### 1. Definition + analogy

**What it is.** A *contract* is the agreed shape of data passed between two steps. P3-M3
produces this one (in `src/ingestion/schemas.py`):

```python
@dataclass(frozen=True, slots=True)
class Embedding:
    chunk: DocumentChunk          # the text and all its metadata
    vector: tuple[float, ...]     # 768 numbers
    model: str                    # "nomic-embed-text"
    model_digest: str             # "0a109f422b47..."
    document_prefix: str          # "search_document: "
```

Three separate ideas are packed into it.

**(a) Immutability (`frozen=True`, `slots=True`, `tuple`).**
- `frozen=True` makes Python forbid *reassigning* a field after creation.
- But frozen is **shallow**: if a field holds a *list*, the list's contents can still be edited
  in place. So the vector is stored as a **tuple**, which cannot be edited at all.
- `slots=True` stores fields in fixed slots instead of a per-object dictionary. It uses less
  memory (it matters with thousands of 768-number vectors) and nobody can attach a stray
  attribute such as a typo'd `vectr`.

Why immutability matters here: the vector is checked in C4. If it could be modified *after*
validation, the check would prove nothing.

**(b) Pairing (the chunk travels with its vector).** The alternative is two parallel lists,
`chunks[i]` ↔ `vectors[i]`, joined later by position or ID. Any code that reorders, filters or
drops an element from *one* list and not the other silently pairs text with the wrong meaning.
Putting both in one object makes that bug impossible to write.

**(c) Provenance ("where did this come from?").** `model`, `model_digest` and `document_prefix`
record *how* the vector was made. P3-M2's startup check *prevents* mixing models. Provenance lets
you *detect* a mixed collection later, for example after someone ran an old script, or after a
prefix change that no check guards.

**Analogy.** A lab sample tube. The sample (vector) is sealed (immutable). The label is attached
to the tube itself, not written in a separate notebook (pairing). The label says which machine
and which protocol produced it (provenance), so a bad batch can be traced months later.

**For a 14-year-old.** Each answer is stapled to its question and stamped with which calculator
made it, so they can't get mixed up and you can find the ones made by a broken calculator.

### 2. Why (vs the traditional approach)
Many tutorials return `list[list[float]]` from an embedding function and zip it with the texts
at upsert time. That works in a notebook where both lists are created on adjacent lines. It breaks
once the lists travel through several functions, get filtered, or get sorted.

### 3. Minimal code

#### Example 2A — what frozen, slots and tuple actually protect
`c2a_frozen.py`
```python
# What frozen=True, slots=True and tuple-vs-list actually protect against.
import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WithList:
    vector: list[float]


@dataclass(frozen=True, slots=True)
class WithTuple:
    vector: tuple[float, ...]


a = WithList(vector=[0.6, 0.8])
b = WithTuple(vector=(0.6, 0.8))

# 1. frozen: you cannot REPLACE a field
try:
    b.vector = (1.0, 0.0)
except dataclasses.FrozenInstanceError as e:
    print("1. reassign field  ->", type(e).__name__, e)

# 2. but frozen is shallow: the list INSIDE can still be edited in place
a.vector[0] = 999.0
print("2. list edited in place, no error ->", a.vector)

# 3. a tuple has no in-place edit at all
try:
    b.vector[0] = 999.0
except TypeError as e:
    print("3. tuple edit      ->", type(e).__name__, e)

# 4. slots: no accidental new attributes (typo protection, less memory)
try:
    object.__setattr__(b, "vectr", (0.0,))
except AttributeError as e:
    print("4. new attribute   ->", type(e).__name__, e)

# 5. frozen dataclasses are hashable and compare by value
print("5. equal by value:", WithTuple((0.6, 0.8)) == b, "| hashable:", hash(b) == hash(WithTuple((0.6, 0.8))))
```
Real output:
```
1. reassign field  -> FrozenInstanceError cannot assign to field 'vector'
2. list edited in place, no error -> [999.0, 0.8]
3. tuple edit      -> TypeError 'tuple' object does not support item assignment
4. new attribute   -> AttributeError 'WithTuple' object has no attribute 'vectr'
5. equal by value: True | hashable: True
```
**How to read it.**
- **Line 2 is the important one:** a *frozen* dataclass holding a *list* let `999.0` in without
  any error. That is why `Embedding.vector` is a tuple (`vector=tuple(vector)` in `embed_batch`).
- **Line 4:** we used `object.__setattr__` to get past `frozen`, and `slots` *still* refused,
  because there is no slot named `vectr`.
- **Line 5:** equality compares field values. That is what lets the tests write
  `assert [e.chunk for e in result] == chunks`.

#### Example 2B — the parallel-list bug that pairing prevents
`c2b_join_bug.py`
```python
# Why Embedding carries the chunk instead of two parallel lists joined later.
chunks = ["refund policy: 30 days", "CEO is Avery Lancaster", "office in Paris"]
vectors = ["<vec refund>", "<vec ceo>", "<vec paris>"]          # vectors[i] belongs to chunks[i]

# Design C (parallel lists): someone "tidies" the chunks by sorting them, and forgets the vectors.
chunks_sorted = sorted(chunks)
print("Parallel lists after sorting only one of them:")
for text, vec in zip(chunks_sorted, vectors):
    print(f"   {text!r:<28} stored with {vec}")

# Design B (one object per pair): sorting moves text and vector together.
pairs = [{"chunk": c, "vector": v} for c, v in zip(chunks, vectors)]
pairs_sorted = sorted(pairs, key=lambda p: p["chunk"])
print("Pairs after sorting:")
for p in pairs_sorted:
    print(f"   {p['chunk']!r:<28} stored with {p['vector']}")
```
Real output:
```
Parallel lists after sorting only one of them:
   'CEO is Avery Lancaster'     stored with <vec refund>
   'office in Paris'            stored with <vec ceo>
   'refund policy: 30 days'     stored with <vec paris>
Pairs after sorting:
   'CEO is Avery Lancaster'     stored with <vec ceo>
   'office in Paris'            stored with <vec paris>
   'refund policy: 30 days'     stored with <vec refund>
```
**How to read it.** In the parallel-list version, **every** chunk is stored with the wrong
meaning, and nothing raised an error. A search for "refund" would now return the CEO chunk. In
the paired version, sorting cannot separate them. `Embedding(chunk=…, vector=…)` is the paired
version. Example 4A shows the same bug with real vectors.

#### Example 2C — provenance detects a mixed collection
`c2c_provenance.py`
```python
# Provenance: with digest + prefix on every Embedding, a mixed collection is detectable.
from collections import Counter
from src.core.config import settings
from src.embedding.embedder import Embedder
from src.ingestion.pipeline import read_chunks


class FakeClient:
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": [[1.0] + [0.0] * 767 for _ in input]}


chunks = read_chunks()[:6]
old = Embedder(FakeClient(), verify=lambda url, model: settings.embed_model_digest).embed_batch(chunks[:4])
new = Embedder(FakeClient(), verify=lambda url, model: "ffffffffffffbbbb").embed_batch(chunks[4:])
collection = old + new             # imagine these were upserted on two different days

e = collection[0]
print("one Embedding:", e.chunk.chunk_id, "| model", e.model, "| digest", e.model_digest[:12],
      "| prefix", repr(e.document_prefix), "| vector len", len(e.vector))

by_version = Counter((x.model_digest[:12], x.document_prefix) for x in collection)
print("vectors per (digest, prefix):", dict(by_version))
if len(by_version) > 1:
    print("MIXED COLLECTION: vectors from", len(by_version), "different model versions/prefixes")
print("pinned digest is", settings.embed_model_digest[:12], "-> stale vectors:",
      [x.chunk.chunk_id for x in collection if x.model_digest != settings.embed_model_digest][:2], "...")
```
Real output:
```
one Embedding: company/about.md_0 | model nomic-embed-text | digest 0a109f422b47 | prefix 'search_document: ' | vector len 768
vectors per (digest, prefix): {('0a109f422b47', 'search_document: '): 4, ('ffffffffffff', 'search_document: '): 2}
MIXED COLLECTION: vectors from 2 different model versions/prefixes
pinned digest is 0a109f422b47 -> stale vectors: ['company/culture.md_0', 'company/culture.md_1'] ...
```
**How to read it.**
- Two Embedders simulate two runs on different days with different model versions; the fake
  `verify` just returns a digest.
- Grouping by `(digest, prefix)` immediately shows two groups, and filtering by the pinned digest
  lists exactly which chunks must be re-embedded.
- Without provenance, both groups would be indistinguishable 768-number tuples. This is why P3-M4
  should copy these fields into the Qdrant payload.

### 4. Benefits
Mis-pairing becomes impossible; vectors can't be changed after validation; stale or mixed
vectors can be found and repaired.

- **What breaks without it:** list reordering bugs (2B/4A), post-validation edits (2A), and
  invisible model mixing (2C).
- **Docs:** [dataclasses (`frozen`, `slots`)](https://docs.python.org/3/library/dataclasses.html) ·
  [Data lineage / provenance (W3C PROV overview)](https://www.w3.org/TR/prov-overview/)

### Micro-exercises
1. Make `WithTuple` non-frozen and repeat step 1 of 2A. *Hint:* reassignment now succeeds.
2. Measure memory: `sys.getsizeof` of 1000 slotted vs non-slotted objects (use `pympler` for deep
   size if installed). *Hint:* compare `__dict__` presence.
3. In 2B, filter out `"office in Paris"` from `chunks` only and print the pairs. *Hint:* now the
   lists also have different lengths (see 4B).
4. Extend 2C to also detect a *prefix* change by creating an Embedder after setting
   `EMBED_DOCUMENT_PREFIX=""`. *Hint:* the env var must be set before `settings` is imported.
5. Why is `Embedding` itself hashable, and is that useful? *Hint:* think of `set()` for
   de-duplication.

**Internals.** `@dataclass(frozen=True)` generates a `__setattr__` that raises
`FrozenInstanceError`; `slots=True` generates a class with `__slots__`, so instances have no
`__dict__`.
**Checkpoint:** explain why `frozen=True` alone would not have been enough for the vector.

---

## C3 — Enforcing invariants at a single choke point

### 1. Definition + analogy

**What it is.** An *invariant* is a rule that must hold for every piece of data, always. For
stored vectors there are three from P3-M2:
1. made by the **pinned model digest**;
2. made from **`search_document: ` + chunk text**;
3. made from the **whole** text (never truncated).

There are two ways to enforce such rules:
- **By convention:** document them and trust every caller to remember. Each new script,
  notebook or API endpoint is a new chance to forget. The failure is silent (the vectors still
  look fine).
- **At a choke point:** make one component the *only* way to produce the data, and put the rules
  *inside* it so callers cannot skip them. `Embedder` is that component:
  - the digest is checked in `__init__`, so if an `Embedder` object exists, the check passed;
  - the prefix is added inside `_embed_one_batch`, and callers pass raw `chunk.text`;
  - `truncate=False` is hard-coded in the request, with no parameter to turn it off.

A related design idea is **"make illegal states unrepresentable"**: rather than checking that a
rule was followed, arrange the code so breaking it is impossible or very awkward.

**Analogy.** A car factory where every car leaves through one inspection gate. Brakes are
checked *at the gate*, not by asking each assembly worker to remember.

**For a 14-year-old.** Instead of reminding everyone "wash your hands before cooking," put the
sink in the kitchen doorway so you can't get in without passing it.

### 2. Why (vs the traditional approach)
A typical script calls the model client directly wherever it needs a vector. It works until the
second script forgets the prefix; then half the collection is in a different space (see doc 12,
C5/C7).

### 3. Minimal code

#### Example 3A — spy on what the Embedder really sends (live)
A **spy** wraps the real client, prints each call, then passes it through unchanged. It is the
cleanest way to *see* what a component does at its boundary.

`c3_choke_point.py`
```python
# Watch exactly what the Embedder sends to the REAL Ollama, using a spy that wraps the real client.
import ollama
from src.embedding.embedder import Embedder
from src.ingestion.pipeline import read_chunks


class SpyClient:
    """Passes every call through to the real client, printing what was sent and received."""

    def __init__(self, real):
        self.real = real

    def embed(self, model="", input=(), truncate=None):
        print(f"  -> embed(model={model!r}, truncate={truncate}, {len(input)} texts)")
        for text in input:
            print(f"       {text[:60]!r}...")
        response = self.real.embed(model=model, input=input, truncate=truncate)
        print(f"  <- {len(response['embeddings'])} vectors, tokens read: {response['prompt_eval_count']}")
        return response


url = "http://localhost:11434"
chunks = read_chunks()[:3]
print("constructing Embedder (verify_model runs now, against the real server):")
embedder = Embedder(SpyClient(ollama.Client(host=url)), ollama_url=url, batch_size=2)
print("  digest verified:", embedder._digest[:12])

print("caller passes raw chunk text:", repr(chunks[0].text[:40]))
result = embedder.embed_batch(chunks)
print("returned", len(result), "Embeddings; first vector starts", [round(x, 4) for x in result[0].vector[:3]])
```
Real output:
```
constructing Embedder (verify_model runs now, against the real server):
  digest verified: 0a109f422b47
caller passes raw chunk text: '# About Insurellm\n\nInsurellm was founded'
  -> embed(model='nomic-embed-text', truncate=False, 2 texts)
       'search_document: # About Insurellm\n\nInsurellm was founded by'...
       'search_document: Since the restructuring, Insurellm has cont'...
  <- 2 vectors, tokens read: 418
  -> embed(model='nomic-embed-text', truncate=False, 1 texts)
       'search_document: # Careers at Insurellm\n\n## Why Join Insurel'...
  <- 1 vectors, tokens read: 1083
returned 3 Embeddings; first vector starts [-0.0103, 0.0254, -0.2221]
```
**How to read it.**
- **Order of events:** the digest is verified *while constructing*, before any chunk is touched.
- **The caller** gave raw text (`'# About Insurellm…'`); **the wire** shows
  `'search_document: # About Insurellm…'`. The prefix was added inside, without the caller's help.
- **`truncate=False`** appears on every request, although the caller never mentioned truncation.
- **Batching:** with `batch_size=2` and 3 chunks, there are two requests (2 texts, then 1).
- **Tokens read** (418 for two short chunks, 1083 for the long careers chunk) are all under the
  2048 limit, as the P3-M2 cap guarantees.

### 4. Benefits
Rules hold for every current and future caller, rules live in one file, and the spy/fake pattern
makes the boundary observable in tests.

- **Why here:** P3-M3 is literally the only producer of stored vectors.
- **What breaks:** add a second "quick" embedding path (a notebook calling `ollama.embed`
  directly and upserting) and all three invariants become conventions again.
- **Docs:** [Yaron Minsky, "Effective ML" — make illegal states unrepresentable](https://blog.janestreet.com/effective-ml-revisited/) ·
  [Martin Fowler, Gateway pattern](https://martinfowler.com/articles/gateway-pattern.html)

### Micro-exercises
1. Run 3A with `batch_size=3`. How many `->` lines appear? *Hint:* ⌈3/3⌉.
2. Change the spy to also print `len(text)` and compare with `chunk.char_count + 17`. *Hint:*
   `len("search_document: ") == 17`.
3. Write a spy that *counts* calls instead of printing, and assert the count in a test.
4. Try to embed with a wrong digest: pass `verify=` a function that raises. Does the spy ever
   print? *Hint:* construction fails first.
5. `grep -rn "\.embed(" src` — is `Embedder` really the only place that embeds for storage?
   *Hint:* the chunker embeds sentences for a different purpose.

**Internals.** The spy works because `Embedder` accepts any object with an `embed` method (C6).
**Checkpoint:** name the three invariants and the exact line of `embedder.py` enforcing each.

---

## C4 — Validating vectors before storage

### 1. Definition + analogy

**What it is.** *Validation* means checking data against rules **before** passing it on, and
refusing it if a rule fails. For vectors, the dangerous failures are not exceptions. They are
**numbers that look fine**. Once such a vector is in Qdrant, it quietly degrades search forever.
`Embedder._validate` runs four checks on every batch:

| Check | Catches | What happens if it's skipped |
|---|---|---|
| `len(vectors) == len(ids)` | server returned fewer/more vectors | `zip` pairs wrongly or drops chunks silently (4B) |
| `len(vector) == 768` | a different model is being served | Qdrant upsert fails later, *after* all the embedding work, or a wrong collection is used |
| all numbers finite | NaN / infinity from a numerical fault | every score involving that vector becomes NaN; ranking breaks (4D) |
| norm ≈ 1 (±0.001) | a model that doesn't normalise, or a corrupted vector | nomic always returns unit vectors, so anything else signals the wrong model or garbage |

Plus a fifth guard: `zip(batch, vectors, strict=True)` refuses to pair lists of different
lengths.

**Key terms.**
- **Norm (length)** of a vector v = √(v₁² + v₂² + … + v₇₆₈²). A *unit vector* has norm 1.
- **NaN** ("not a number") is a special float produced by invalid maths, such as 0/0. It is
  contagious: any arithmetic with NaN gives NaN, and every comparison with NaN is `False`.
- **Tolerance:** floats are stored with limited precision, so "exactly 1" is checked as
  "within 0.001 of 1".

**Analogy.** Airport security for luggage going into the hold. Once the bag is on the plane,
it's too late to find the problem. Check at the gate, where rejecting is cheap.

**For a 14-year-old.** Before you save your homework answers, check there's one answer per
question, each has the right number of digits, none says "error", and none is obviously the wrong
size.

### 2. Why (vs the traditional approach)
Most code trusts whatever the model returns. That is reasonable in a notebook, where you look at
the output. In a pipeline that runs unattended and stores results permanently, "garbage in,
garbage stored" is the default unless someone checks.

### 3. Minimal code

#### Example 4A — misalignment with REAL vectors (live)
`c4a_misalignment.py`
```python
# What a misaligned batch does to retrieval, with REAL vectors: every chunk gets a neighbour's meaning.
import numpy as np
import ollama
from src.embedding.lock import as_document, as_query

client = ollama.Client(host="http://localhost:11434")
texts = [
    "Employees receive 20 days of paid annual leave per year.",
    "The Acme widget ships in blue and red.",
    "Contracts renew automatically every twelve months.",
]
vectors = np.array(client.embed(model="nomic-embed-text", input=[as_document(t) for t in texts])["embeddings"])
query = np.array(client.embed(model="nomic-embed-text", input=[as_query("How much holiday do staff get?")])["embeddings"][0])


def search(stored_vectors, label):
    scores = stored_vectors @ query                  # unit vectors: dot product == cosine
    best = int(np.argmax(scores))
    print(f"{label}: top hit = {texts[best]!r} (score {scores[best]:.3f})")


search(vectors, "correct pairing   ")
search(np.roll(vectors, 1, axis=0), "shifted by one    ")   # vector i stored next to text i+1
```
Real output:
```
correct pairing   : top hit = 'Employees receive 20 days of paid annual leave per year.' (score 0.614)
shifted by one    : top hit = 'The Acme widget ships in blue and red.' (score 0.614)
```
**How to read it.**
- `np.roll(vectors, 1, axis=0)` moves every vector one row down: the leave vector now sits next
  to the widget text.
- The **score is identical (0.614)**, because the search found the same *vector*. But the text
  returned to the user is about widgets. A confident, wrong answer with a normal score, and
  nothing in the output hints at a problem. That is why misalignment must be prevented *before*
  storage (C2 pairing + the count check + `strict=True`).

#### Example 4B — `zip` vs `zip(strict=True)`
`c4b_zip_strict.py`
```python
# zip() silently stops at the shorter list; zip(strict=True) refuses.
chunks = ["chunk_0", "chunk_1", "chunk_2"]
vectors = ["vec_0", "vec_1"]                 # the server "lost" one

print("zip()            ->", list(zip(chunks, vectors)), " <- chunk_2 vanished, no error")
try:
    list(zip(chunks, vectors, strict=True))
except ValueError as e:
    print("zip(strict=True) ->", type(e).__name__ + ":", e)
```
Real output:
```
zip()            -> [('chunk_0', 'vec_0'), ('chunk_1', 'vec_1')]  <- chunk_2 vanished, no error
zip(strict=True) -> ValueError: zip() argument 2 is shorter than argument 1
```
**How to read it.** Plain `zip` stops at the shorter list, so `chunk_2` would simply never be
indexed and never be found by any search. `strict=True` (Python 3.10+) turns that into an error.
`embed_batch` has both the explicit count check (clear message) and `strict=True` (a backstop if
the check is ever removed).

#### Example 4C — each check firing on the real `_validate`
`c4c_four_checks.py`
```python
# Feed hand-made bad vectors to the real _validate and see each check fire.
import math
from src.embedding.embedder import Embedder, EmbeddingError


class NoClient:
    def embed(self, **kwargs):
        raise AssertionError("not used")


embedder = Embedder(NoClient(), verify=lambda url, model: "digest")   # skip the live model check
good = [1.0] + [0.0] * 767                                             # length exactly 1
cases = {
    "valid batch        ": (["c0", "c1"], [good, good]),
    "one vector missing ": (["c0", "c1"], [good]),
    "wrong dimension    ": (["c0"], [[1.0] + [0.0] * 383]),
    "NaN inside         ": (["c0"], [[math.nan] + [0.0] * 767]),
    "not unit length    ": (["c0"], [[0.5] + [0.0] * 767]),
    "tiny rounding error": (["c0"], [[1.0004] + [0.0] * 767]),
}
for label, (ids, vectors) in cases.items():
    try:
        embedder._validate(ids, vectors)
        print(f"{label} -> passes")
    except EmbeddingError as e:
        print(f"{label} -> EmbeddingError: {e}")
```
Real output:
```
valid batch         -> passes
one vector missing  -> EmbeddingError: sent 2 texts but received 1 vectors
wrong dimension     -> EmbeddingError: c0: vector has 384 dims, expected 768 (is a different model being served?)
NaN inside          -> EmbeddingError: c0: vector contains NaN or infinity
not unit length     -> EmbeddingError: c0: vector norm is 0.5000, expected 1.0
tiny rounding error -> passes
```
**How to read it.**
- `[1.0, 0, 0, …]` has norm exactly 1: it's the simplest valid unit vector.
- Each bad case trips exactly one check, and the message names the chunk and the reason.
- **The last line matters too:** a norm of 1.0004 *passes*, because the tolerance is ±0.001. A
  check that rejects harmless float rounding would cause false alarms, and people learn to ignore
  or delete noisy checks.
- The dimension message suggests the likely cause ("a different model being served?"). Good error
  messages say what to investigate, not just what failed.

#### Example 4D — why NaN is poison
`c4d_nan_poison.py`
```python
# Why NaN must be stopped before storage: it poisons every comparison it touches.
import math

nan_vector = [math.nan, 0.0]
query = [1.0, 0.0]
score = sum(a * b for a, b in zip(nan_vector, query))
print("dot product with a NaN vector:", score)
print("NaN > 0.5 ?", score > 0.5, "| NaN < 0.5 ?", score < 0.5, "| NaN == NaN ?", score == score)

scores = [0.9, math.nan, 0.3, 0.7]
print("sorted with a NaN in the list:", sorted(scores, reverse=True), "<- order is no longer trustworthy")
```
Real output:
```
dot product with a NaN vector: nan
NaN > 0.5 ? False | NaN < 0.5 ? False | NaN == NaN ? False
sorted with a NaN in the list: [0.9, nan, 0.7, 0.3] <- order is no longer trustworthy
```
**How to read it.**
- One NaN coordinate makes the whole similarity score NaN.
- NaN is neither bigger nor smaller than anything, and **not even equal to itself**
  (`score == score` is `False`, which is the classic NaN test).
- Sorting relies on comparisons, so a NaN lands in an arbitrary position. Here it sits
  *between* 0.9 and 0.7, so 0.7 and 0.3 are no longer guaranteed to be ranked correctly relative
  to everything else. `math.isfinite(x)` is `False` for NaN and ±infinity, which is exactly what
  the check uses.

### 4. Benefits
Bad vectors are stopped at the cheapest possible moment, error messages name the chunk and the
likely cause, and the tolerance avoids false alarms.

- **What breaks:** removing any check lets a plausible-looking but wrong vector into Qdrant
  (each check was mutation-tested in C8).
- **Docs:** [`zip(strict=True)`, PEP 618](https://peps.python.org/pep-0618/) ·
  [`math.isfinite`](https://docs.python.org/3/library/math.html#math.isfinite) ·
  [IEEE 754 NaN comparisons (Python float docs)](https://docs.python.org/3/tutorial/floatingpoint.html)

### Micro-exercises
1. In 4A, use `np.roll(vectors, 2, axis=0)` and predict the top hit before running.
2. In 4C, add a case with `math.inf`. Which message appears? *Hint:* `isfinite` covers both.
3. Find the smallest norm error that fails, e.g. try 1.0009 and 1.0011. *Hint:* the check is `>`.
4. In 4D, sort `[0.9, 0.3, math.nan, 0.7]` (NaN moved). Does the result change? *Hint:* NaN
   breaks the ordering assumptions of sorting algorithms.
5. Add a check that rejects an all-zero vector. Would the norm check already catch it?
   *Hint:* norm of zeros is 0.

**Internals.** `_validate` loops over vectors in pure Python: 768 multiplications × 16 vectors per
batch is microseconds, negligible next to ~0.9 s of model compute per chunk.
**Checkpoint:** explain why 4A's wrong answer has the *same* score as the right one.

---

## C5 — Batch-level failure, error wrapping, catch by type

### 1. Definition + analogy

**What it is.** Three linked ideas about what happens when embedding goes wrong.

**(a) Failure is per request, so per batch.** An HTTP request either succeeds as a whole or fails
as a whole. If one text in a batch is rejected, Ollama returns an error for the **entire
request**, with no partial results and (as 5A shows) **no indication of which text** was the
problem. So the unit of failure is the batch, and the error we raise must name **every chunk in
the batch**. That is the only way a human can find the culprit.

**(b) Fail fast.** When a batch fails, `Embedder` raises immediately instead of skipping and
continuing. The reasoning: the P3-M2 hard cap makes an over-length chunk *impossible*, so a
rejection means a real bug. An index built by silently skipping chunks **looks complete**, and
those chunks can never be retrieved.

**(c) Wrapping errors with context, without losing the cause.** The server says "input length
exceeds the context length"; it doesn't know our chunk IDs. We catch the low-level error and
raise our own `EmbeddingError` with the IDs added, using `raise … from exc`. The `from` keeps the
original exception attached as `__cause__`, so the full story survives in the traceback.

**(d) Catch by type, not everything.** We catch only errors the *outside world* can cause:
`ollama.ResponseError` (the server said no), `httpx.HTTPError` (transport problems such as
timeouts) and `ConnectionError` (server unreachable). A bug in *our* code (a `KeyError`,
`TypeError`…) must crash as itself. Wrapping it as "embedding failed" would send you to
investigate Ollama instead of your code.

**Analogy.** A courier returns a sealed parcel of 16 letters marked only "refused". You can't
know which letter was the problem, so your report lists all 16 addresses (a). You stop sending
parcels until you understand why (b). You attach the courier's original note to your report
rather than retyping it (c). And if the parcel was never sent because *you* wrote the wrong
address format, that's your mistake, not a courier problem (d).

**For a 14-year-old.** If the teacher hands back a whole group project with "something's wrong",
you tell everyone in the group, stop and fix it, keep the teacher's note, and don't blame the
teacher for your own typo.

### 2. Why (vs the traditional approach)
Common patterns are `except Exception: pass` (hides everything) or letting the raw library error
escape (no context: *which* chunks?). Both make production failures slow to diagnose.

### 3. Minimal code

#### Example 5A — one bad text fails the whole request (live)
`c5a_batch_fails_whole.py`
```python
# One bad text inside a batch: does the server return the good ones? (real Ollama)
import ollama
from src.embedding.lock import as_document

client = ollama.Client(host="http://localhost:11434")
good_1 = as_document("Employees receive 20 days of paid annual leave per year.")
good_2 = as_document("The Acme widget ships in blue and red.")
too_long = as_document("policy " * 3000)

print("batch [good, good]           ->", len(client.embed(model="nomic-embed-text", input=[good_1, good_2], truncate=False)["embeddings"]), "vectors")
try:
    client.embed(model="nomic-embed-text", input=[good_1, too_long, good_2], truncate=False)
except ollama.ResponseError as e:
    print("batch [good, TOO LONG, good] ->", type(e).__name__, "status", e.status_code, "|", e.error)
    print("   the error mentions which input? ", "index" in e.error or "1" in e.error)
```
Real output:
```
batch [good, good]           -> 2 vectors
batch [good, TOO LONG, good] -> ResponseError status 400 | the input length exceeds the context length
   the error mentions which input?  False
```
**How to read it.** The two good texts in the second request got **no** vectors, and the error
doesn't say which of the three was too long. That is why `_embed_one_batch` puts all the batch's
`chunk_id`s into the message. (The live P3-M3 run showed it:
`embedding failed for ['company/about.md_0', 'TEST_oversized']: …`.)

#### Example 5B — `raise … from` keeps the original cause
`c5b_raise_from.py`
```python
# Wrapping an error: add context (which chunks) without losing the original cause.
import traceback


class EmbeddingError(RuntimeError):
    pass


def server_call():
    raise ConnectionError("Failed to connect to Ollama")


def embed_one_batch(ids):
    try:
        server_call()
    except ConnectionError as exc:
        raise EmbeddingError(f"embedding failed for {ids}: {exc}") from exc


try:
    embed_one_batch(["company/about.md_0", "company/about.md_1"])
except EmbeddingError as err:
    print("message   :", err)
    print("__cause__ :", repr(err.__cause__))
    print("--- full traceback (note the 'direct cause' line) ---")
    print(traceback.format_exc().replace(__file__, "c5b_raise_from.py"))
```
Real output:
```
message   : embedding failed for ['company/about.md_0', 'company/about.md_1']: Failed to connect to Ollama
__cause__ : ConnectionError('Failed to connect to Ollama')
--- full traceback (note the 'direct cause' line) ---
Traceback (most recent call last):
  File "c5b_raise_from.py", line 15, in embed_one_batch
    server_call()
  File "c5b_raise_from.py", line 10, in server_call
    raise ConnectionError("Failed to connect to Ollama")
ConnectionError: Failed to connect to Ollama

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "c5b_raise_from.py", line 21, in <module>
    embed_one_batch(["company/about.md_0", "company/about.md_1"])
  File "c5b_raise_from.py", line 17, in embed_one_batch
    raise EmbeddingError(f"embedding failed for {ids}: {exc}") from exc
EmbeddingError: embedding failed for ['company/about.md_0', 'company/about.md_1']: Failed to connect to Ollama
```
**How to read it.**
- The **message** now has what the server couldn't know: the chunk IDs.
- **`__cause__`** still holds the original `ConnectionError` object, so code can inspect it.
- The traceback prints **both** exceptions joined by *"The above exception was the direct cause of
  the following exception"*. Without `from exc` you'd see a different, misleading line ("During
  handling of the above exception, another exception occurred"), and with `from None` the
  original would be hidden entirely.

#### Example 5C — catch-all hides your own bugs
`c5c_catch_by_type.py`
```python
# Catch-all vs catch-by-type: which one hides our own bugs?
class EmbeddingError(RuntimeError):
    pass


def buggy_client():
    response = {"embedings": []}          # typo in OUR code, not a server problem
    return response["embeddings"]


def with_catch_all():
    try:
        return buggy_client()
    except Exception as exc:
        raise EmbeddingError(f"embedding failed: {exc!r}") from exc


def with_catch_by_type():
    try:
        return buggy_client()
    except (ConnectionError, TimeoutError) as exc:      # only failures the SERVER can cause
        raise EmbeddingError(f"embedding failed: {exc!r}") from exc


for fn in (with_catch_all, with_catch_by_type):
    try:
        fn()
    except Exception as e:
        print(f"{fn.__name__:<20} -> {type(e).__name__}: {e}")
```
Real output:
```
with_catch_all       -> EmbeddingError: embedding failed: KeyError('embeddings')
with_catch_by_type   -> KeyError: 'embeddings'
```
**How to read it.** The typo `"embedings"` is *our* bug. The catch-all reports it as an
**embedding failure**, which points you at Ollama. Catch-by-type lets the `KeyError` through
unchanged, which points you at the dictionary lookup. In the real module, the test
`test_own_bugs_are_not_disguised_as_embedding_errors` enforces this.

### 4. Benefits
Errors are actionable (they name the chunks), the root cause is preserved, and bugs are never
misfiled as infrastructure problems.

- **Why here:** a 200-second job that fails needs a message you can act on without rerunning it
  with extra logging.
- **What breaks:** skip-and-continue produces an index that silently lacks chunks; a catch-all
  sends debugging in the wrong direction.
- **Docs:** [Python tutorial: exception chaining](https://docs.python.org/3/tutorial/errors.html#exception-chaining) ·
  [PEP 3134 — Exception Chaining](https://peps.python.org/pep-3134/) ·
  [ollama-python `ResponseError`](https://github.com/ollama/ollama-python)

### Micro-exercises
1. In 5B, replace `from exc` with `from None` and compare the traceback. *Hint:* the
   `ConnectionError` disappears.
2. In 5B, remove `from exc` entirely. What sentence joins the two tracebacks now?
3. In 5C, add `KeyError` to the catch-by-type tuple. What have you just hidden?
4. Stop the Ollama container (`docker compose stop ollama`) and run
   `python -m src.embedding.embedder`. Is the error from `verify_model` or `EmbeddingError`, and
   why? *Hint:* which runs first? Restart it afterwards.
5. Design (on paper) a *bisection* strategy: after a 400 on a batch of 16, how many extra
   requests does it take in the worst case to find one bad chunk? *Hint:* log₂16.

**Internals.** `ollama.Client` uses `httpx` underneath. HTTP error statuses become
`ollama.ResponseError` (with `.status_code` and `.error`), while an unreachable server is turned
into Python's built-in `ConnectionError`.
**Checkpoint:** explain the difference between `raise X from exc`, `raise X` inside `except`, and
`raise X from None`.

> **Still open:** Question 1 from P3-M3 (what `embed_batch` has done when a *later* batch fails)
> is intentionally not answered here. Work it out from `embed_batch`'s loop before checking it
> with exercise 4 of C6.

---

## C6 — Protocols and fakes that encode their input

### 1. Definition + analogy

**What it is.** Two related testing ideas.

**(a) Structural typing with `typing.Protocol`.** Normally a type says "must *be* an X"
(inherit from X). A **Protocol** says "must *have* these methods with these signatures", nothing
more. This is also called *duck typing* ("if it walks like a duck…"), but checked by mypy
before running.
`EmbedClient` requires exactly one method: `embed(model, input, truncate) -> Any`. The real
`ollama.Client` satisfies it without knowing it exists, and so does a ten-line test fake.
That's what makes **dependency injection** (passing the client in) cheap.

**(b) Fakes that encode their input.** A *fake* is a working stand-in for a real dependency. A
*lazy* fake returns the same vector for everything, and tests using it can't tell whether vector
*i* went to chunk *i*. A **fake whose output encodes its input** can. Ours returns a one-hot
vector (all zeros, a single `1.0`) whose hot position is the input text's length. From any
returned vector, a test can recover which text produced it. It is also a valid unit vector, so it
passes validation like a real one.

**Key terms.**
- **Fake** vs **mock** vs **spy:** a *fake* has simple working behaviour; a *mock* is
  pre-programmed with expected calls and asserts on them; a *spy* wraps a real object and records
  calls (C3).
- **One-hot vector:** a vector with exactly one 1 and the rest 0s. Its norm is 1.

**Analogy.** A Protocol is a job advert listing required skills, not required schools. An
encoding fake is a practice exam where every answer contains the question number, so you can see
immediately whether answers were handed in out of order.

**For a 14-year-old.** The code doesn't care *who* the helper is, only that it can do the one job
asked. And for testing, we use a helper whose answers are labelled, so mix-ups are obvious.

### 2. Why (vs the traditional approach)
Without injection, tests must either run a real Ollama (slow, flaky, impossible in CI) or
monkeypatch module internals (fragile). Without an encoding fake, order bugs pass every test.

### 3. Minimal code

#### Example 6A — a Protocol is satisfied by shape, and mypy checks it
`c6a_protocol.py`
```python
# Structural typing: a class satisfies a Protocol by having the right method -- no inheritance.
from typing import Any, Protocol


class EmbedClient(Protocol):
    def embed(self, model: str = ..., input: list[str] = ..., truncate: bool | None = ...) -> Any: ...


class GoodFake:                      # does NOT inherit from EmbedClient
    def embed(self, model: str = "", input: list[str] = [], truncate: bool | None = None) -> Any:
        return {"embeddings": [[1.0] for _ in input]}


class WrongFake:                     # method named differently
    def encode(self, texts: list[str]) -> Any:
        return [[1.0] for _ in texts]


def use(client: EmbedClient) -> int:
    return len(client.embed(model="m", input=["a", "b"], truncate=False)["embeddings"])


print("GoodFake works at runtime:", use(GoodFake()))
try:
    use(WrongFake())                 # mypy flags this line BEFORE running
except AttributeError as e:
    print("WrongFake fails at runtime:", e)
```
Real output (running it):
```
GoodFake works at runtime: 2
WrongFake fails at runtime: 'WrongFake' object has no attribute 'embed'
```
Real output (`.venv/Scripts/python -m mypy "practice codes/phase 3/m3_embedding_deep_dive/c6a_protocol.py"`):
```
c6a_protocol.py:25: error: Argument 1 to "use" has incompatible type "WrongFake"; expected "EmbedClient"  [arg-type]
Found 1 error in 1 file (checked 1 source file)
```
**How to read it.**
- `GoodFake` never mentions `EmbedClient`, but it has a matching `embed` method, so it is
  accepted.
- `WrongFake` fails in two places: **at runtime** (`AttributeError`, only when that line runs) and
  **statically** (mypy flags line 25 without running anything). The static check is the point:
  the mistake is caught in the editor or CI, not in production.
- The real `ollama.Client.embed` has *more* parameters (`options`, `keep_alive`, `dimensions`), and
  it still satisfies the Protocol, because extra optional parameters don't break compatibility.

#### Example 6B — only an encoding fake catches an order bug
`c6b_fake_encodes_input.py`
```python
# A useful fake returns vectors that ENCODE their input, so tests can see which text made which vector.
from src.embedding.embedder import Embedder
from src.ingestion.pipeline import read_chunks


def one_hot(position: int) -> list[float]:
    v = [0.0] * 768
    v[position % 768] = 1.0            # unit length, so it passes validation
    return v


class HonestFake:
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": [one_hot(len(t)) for t in input]}


class ShufflingFake(HonestFake):
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": list(reversed(super().embed(model, input, truncate)["embeddings"]))}


class ConstantFake:                    # the lazy fake: same vector for everything
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": [one_hot(0) for _ in input]}


chunks = read_chunks()[:3]
prefix = len("search_document: ")
expected = [(prefix + len(c.text)) % 768 for c in chunks]   # same wrap-around as one_hot
print("expected hot positions:", expected)
for fake in (HonestFake(), ShufflingFake(), ConstantFake()):
    result = Embedder(fake, batch_size=3, verify=lambda url, model: "d").embed_batch(chunks)
    got = [e.vector.index(1.0) for e in result]
    print(f"{type(fake).__name__:<13} hot positions {got} -> order check {'PASS' if got == expected else 'FAIL'}")
```
Real output:
```
expected hot positions: [356, 392, 120]
HonestFake    hot positions [356, 392, 120] -> order check PASS
ShufflingFake hot positions [120, 392, 356] -> order check FAIL
ConstantFake  hot positions [0, 0, 0] -> order check FAIL
```
**How to read it.**
- The expected position for each chunk is `(17 + len(text)) % 768`. The fake sees the *prefixed*
  text, so this also proves the prefix was applied.
- `HonestFake` passes. `ShufflingFake` simulates a server returning vectors in the wrong order,
  and the test catches it. Note that the middle chunk still matches (392), because reversing 3
  items keeps the middle one in place. An order test with only 2 or 3 items can miss some bugs.
- `ConstantFake` gives the same vector for everything. With a fake like that, **every** order bug
  would pass a naive test that only checks `len(result) == 3`. That's why the real test
  (`test_returns_one_embedding_per_chunk_in_input_order`) uses the encoding approach.

### 4. Benefits
The test suite runs in about a second with no server; order, prefix and batching behaviour are all
observable; and mypy verifies that fakes and the real client share the interface.

- **What breaks:** if `ollama.Client.embed` renames a parameter in a future version, mypy on
  `build_ollama_client` flags it (it flagged the return type during P3-M3).
- **Docs:** [PEP 544 — Protocols](https://peps.python.org/pep-0544/) ·
  [mypy: Protocols and structural subtyping](https://mypy.readthedocs.io/en/stable/protocols.html) ·
  [Martin Fowler, "Mocks Aren't Stubs"](https://martinfowler.com/articles/mocksArentStubs.html)

### Micro-exercises
1. Add a `SwapFirstTwoFake` to 6B and check which positions differ.
2. Change `ConstantFake` to return `[0.0]*768`. What happens now, and which check fires?
   *Hint:* norm.
3. Remove `truncate` from `GoodFake.embed` and run mypy on 6A. *Hint:* the call passes `truncate=`.
4. Build a fake that raises on its 2nd call and use it with `batch_size=2` on 5 chunks. Before
   running, write down what you expect `embed_batch` to have done. (This checks your answer to the
   open Question 1.)
5. Explain why the one-hot position uses `% 768`. *Hint:* chunk lengths go up to 5,983.

**Internals.** mypy compares the fake's method signature to the Protocol's: same name, compatible
parameter names/types, compatible return type. At runtime, Protocols do nothing unless decorated
with `@runtime_checkable`.
**Checkpoint:** explain why a fake returning identical vectors is dangerous for this module.

---

## C7 — The contract round-trip to disk

### 1. Definition + analogy

**What it is.** Stages that run as separate commands must pass data through files.
**Serialisation** turns an in-memory object into text (JSON); **deserialisation** turns it back.
A *round-trip* is doing both and getting an equal object. For chunks:

```
DocumentChunk ──dataclasses.asdict──► dict ──json.dumps──► text file        (write_chunks, P2)
text file ──json.loads──► dict ──DocumentChunk(**record)──► DocumentChunk   (read_chunks, P3-M3)
```

- `dataclasses.asdict` converts the dataclass (recursively) into plain dicts/lists.
- JSON keeps `str`, `int`, `float`, `bool`, `list`, `dict`, `null`; our chunk uses only those,
  so nothing is lost. A tuple would come back as a list, and a `datetime` wouldn't serialise at
  all.
- `DocumentChunk(**record)` *unpacks* the dict as keyword arguments. An extra or missing key raises
  `TypeError`, so a file written by a different schema version fails loudly.

**File selection is part of the contract.** `data/chunks/` holds chunk files *and* `report.json`.
The loader must pick exactly the chunk files. Two tempting globs are wrong in opposite directions,
shown in example 7.

**Why a separate read step at all?** It decouples stages: embedding can be rerun, retuned or
debugged without re-chunking (which re-embeds every sentence through `SemanticChunker`).

**Analogy.** Packing a flat-pack wardrobe (serialise) and assembling it in another house
(deserialise). The instructions must match the parts, and you must not accidentally unpack the
neighbour's box that was delivered to the same doorstep (the report file).

**For a 14-year-old.** Save your game to a file, load it tomorrow, and it should be exactly the
same game. Also, don't try to load the settings file as if it were a save file.

### 2. Why (vs the traditional approach)
Notebook pipelines keep everything in memory and re-run from the top, which gets slower and more
fragile as stages get more expensive. Stages connected by files can be run, cached and inspected
independently.

### 3. Minimal code

#### Example 7 — round-trip step by step, plus both glob pitfalls
`c7_round_trip.py`
```python
# Contract on disk: dataclass -> dict -> JSON -> dict -> dataclass, and two glob pitfalls.
import dataclasses
import json
import tempfile
from pathlib import Path

from src.ingestion.pipeline import REPORT_FILENAME, read_chunks, write_chunks
from src.ingestion.schemas import DocumentChunk

chunk = read_chunks()[0]

# Step 1: dataclass -> plain dict (nested dicts included)
record = dataclasses.asdict(chunk)
print("1. asdict keys:", list(record))

# Step 2: dict -> JSON text -> dict (what write_chunks / read_chunks do)
text = json.dumps(record)
back = json.loads(text)
print("2. JSON is", len(text), "chars; types after reload:", type(back["chunk_index"]).__name__, type(back["metadata"]).__name__)

# Step 3: dict -> dataclass via keyword unpacking, and equality by value
rebuilt = DocumentChunk(**back)
print("3. rebuilt == original:", rebuilt == chunk)

# Step 4: an unknown key is rejected, so a schema drift in the file is loud
try:
    DocumentChunk(**{**back, "embedding": [0.1]})
except TypeError as e:
    print("4. extra key ->", e)

# Step 5: the two glob pitfalls read_chunks avoids
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    write_chunks([chunk], d / "company__about.md.json")
    write_chunks([dataclasses.replace(chunk, source_path="company/memo.txt")], d / "company__memo.txt.json")
    (d / REPORT_FILENAME).write_text(json.dumps({"chunks_written": 2}), encoding="utf-8")

    print("5a. glob('*.md.json') finds", len(list(d.glob("*.md.json"))), "file(s) -> the .txt chunks would be lost")
    try:
        [DocumentChunk(**r) for p in d.glob("*.json") for r in json.loads(p.read_text("utf-8"))]
    except TypeError as e:
        print("5b. glob('*.json') including the report ->", type(e).__name__, e)
    print("5c. read_chunks finds", len(read_chunks(d)), "chunks")
```
Real output:
```
1. asdict keys: ['chunk_id', 'doc_id', 'source_path', 'category', 'title', 'chunk_index', 'text', 'char_count', 'approx_token_count', 'metadata']
2. JSON is 1407 chars; types after reload: int dict
3. rebuilt == original: True
4. extra key -> DocumentChunk.__init__() got an unexpected keyword argument 'embedding'
5a. glob('*.md.json') finds 1 file(s) -> the .txt chunks would be lost
5b. glob('*.json') including the report -> TypeError src.ingestion.schemas.DocumentChunk() argument after ** must be a mapping, not str
5c. read_chunks finds 2 chunks
```
**How to read it.**
- **Steps 1–3:** the 10 fields survive the trip with their types (`int` stays `int`, `metadata`
  stays a `dict`), and the rebuilt object is *equal* to the original.
- **Step 4:** a file containing a field the class doesn't know is refused, naming the field.
- **5a (too narrow):** `*.md.json` misses `company__memo.txt.json`. The loader supports `.txt`
  and `.pdf`, so those chunks would silently never be embedded. This bug was caught while
  implementing P3-M3.
- **5b (too wide):** `*.json` includes `report.json`, a *dict* rather than a list. Iterating a
  dict yields its *keys* (strings), so `DocumentChunk(**"chunks_written")` fails. That exact
  `TypeError` also happened earlier in this project, when counting chunks by hand.
- **5c:** `read_chunks` takes every `*.json` **except the report, by name**, and finds both.

### 4. Benefits
Stages are decoupled, the schema is enforced on load, and the file selection is correct for every
supported source type.

- **What breaks:** the narrow glob silently drops data; the wide glob crashes; a schema change
  without re-running P2 fails loudly on load (step 4), which is the good outcome.
- **Docs:** [`dataclasses.asdict`](https://docs.python.org/3/library/dataclasses.html#dataclasses.asdict) ·
  [`json` module](https://docs.python.org/3/library/json.html) ·
  [`pathlib.Path.glob`](https://docs.python.org/3/library/pathlib.html#pathlib.Path.glob)

### Micro-exercises
1. Remove a key (e.g. `title`) from `back` and rebuild. What error appears?
2. Add a `tuple` field to a toy dataclass and round-trip it. Is it equal afterwards? *Hint:* JSON
   has no tuples.
3. Write a test for 5a using a `.pdf.json` file name.
4. Count chunks per source extension in the real `data/chunks/`. Are there any non-`.md` sources
   today?
5. Propose a manifest-based design (a file listing chunk files) and one advantage over name-based
   exclusion. *Hint:* decision D-2026-09-17-10's flip condition.

**Internals.** `Path.glob("*.json")` matches file names in one directory (no recursion), in
arbitrary filesystem order, which is why `read_chunks` wraps it in `sorted(...)` for reproducible
chunk order.
**Checkpoint:** explain both glob failures and which one is worse.

---

## C8 — Mutation testing

### 1. Definition + analogy

**What it is.** Normal tests ask *"does the code work?"* **Mutation testing** asks *"would the
tests notice if the code were broken?"* You deliberately introduce a small bug (a **mutant**),
such as deleting a check, flipping a condition or changing a constant, and run the tests:
- if a test **fails**, the mutant is **killed**: good, the tests protect that line;
- if all tests **pass**, the mutant **survived**: that line is effectively untested, however high
  line coverage looks.

Line *coverage* only proves a line **ran** during tests. Mutation testing proves its **behaviour
was checked**. A validation function can be 100 % covered by a happy-path test that never feeds it
bad input.

Tools such as **mutmut** or **cosmic-ray** generate mutants automatically. For P3-M3 we did it by
hand for the 9 safeguards that matter most.

**Analogy.** Testing a smoke alarm by actually making smoke, not by checking it's plugged in.

**For a 14-year-old.** To check that your spell-checker works, you type a spelling mistake on
purpose. If it doesn't underline it, the checker isn't really checking.

### 2. Why (vs the traditional approach)
Coverage percentages are easy to game and say nothing about assertions. Safety checks (like C4's)
are exactly the code that happy-path tests never exercise.

### 3. Minimal code

#### Example 8A — a weak test vs a strong test against one mutant
`c8_mutation.py`
```python
# Mutation testing in miniature: break the code on purpose, check a test notices.
import math


def validate(vector, dim=3):
    if len(vector) != dim:
        raise ValueError("wrong dimension")
    if not all(math.isfinite(x) for x in vector):
        raise ValueError("NaN")


def mutant_no_nan_check(vector, dim=3):     # someone "simplifies" the function
    if len(vector) != dim:
        raise ValueError("wrong dimension")


def weak_test(fn):
    """Only checks the happy path."""
    fn([0.0, 0.6, 0.8])
    return True


def strong_test(fn):
    """Also checks that bad input is REJECTED."""
    fn([0.0, 0.6, 0.8])
    try:
        fn([math.nan, 0.6, 0.8])
    except ValueError:
        return True
    return False


for test in (weak_test, strong_test):
    original = test(validate)
    mutant = test(mutant_no_nan_check)
    verdict = "catches the mutant" if original and not mutant else "MISSES the mutant"
    print(f"{test.__name__:<11}: original passes={original}, mutant passes={mutant} -> {verdict}")
```
Real output:
```
weak_test  : original passes=True, mutant passes=True -> MISSES the mutant
strong_test: original passes=True, mutant passes=False -> catches the mutant
```
**How to read it.**
- Both tests pass on the real function, and both give it **full line coverage** of the happy path.
- Only `strong_test` fails on the mutant, because only it feeds bad input and expects rejection.
  A test for a safety check must include the *unsafe* case.

#### Example 8B — the real P3-M3 mutation run
Each safeguard in `src/embedding/embedder.py` was replaced, one at a time, and
`tests/embedding/test_embedder.py` was run (file restored byte-exact after each):

| Mutant (what was broken) | Result |
|---|---|
| `truncate=False` → `truncate=None` | 1 failed (killed) |
| prefix removed from input | 2 failed (killed) |
| count check → `if False:` | 1 failed (killed) |
| dimension check → `if False:` | 1 failed (killed) |
| NaN check → `if False:` | 1 failed (killed) |
| norm check → `if False:` | 1 failed (killed) |
| vectors reversed | 1 failed (killed) |
| `verify_model` skipped | 1 failed (killed) |
| catch-by-type → `except Exception` | 1 failed (killed) |

**How to read it.** 9 of 9 mutants killed. "Prefix removed" killed 2 tests, because both the
prefix test and the order test depend on the prefixed length (6B). A lesson learned while doing
it: the first attempt rewrote the file with Windows line endings. Always restore mutated files
**byte-exact** (`read_bytes` / `write_bytes`) and verify with `cmp`.

### 4. Benefits
Real confidence that safety checks are tested; it finds useless or missing assertions; and it is a
strong interview talking point ("coverage says what ran, mutation says what's checked").

- **What breaks without it:** a refactor can remove a check while all tests stay green.
- **Docs:** [mutmut](https://mutmut.readthedocs.io/) ·
  [Wikipedia: Mutation testing](https://en.wikipedia.org/wiki/Mutation_testing) ·
  Petrović & Ivanković, "State of Mutation Testing at Google" (ICSE-SEIP 2018)

### Micro-exercises
1. In 8A, write `mutant_no_dim_check` and check whether `strong_test` kills it. *Hint:* it doesn't
   test a wrong dimension.
2. Delete `test_nan_raises` temporarily; which mutant from 8B would now survive?
3. Mutate `NORM_TOLERANCE = 1e-3` to `1.0`. Does any test fail? Should one?
4. `pip install mutmut` in a scratch venv and run it on `src/embedding/embedder.py`; compare with
   the hand table.
5. Explain why 100 % line coverage and 0 killed mutants can happen together.

**Internals.** Mutation tools parse the code into a syntax tree, apply operators (swap `<`/`<=`,
replace constants, delete statements), and rerun the test suite per mutant.
**Checkpoint:** define "killed" and "survived" and say which is the good outcome.

---

## C9 — Brute-force nearest-neighbour search (first look at retrieval)

### 1. Definition + analogy

**What it is.** Retrieval asks: *which stored vectors are closest to the query vector?* The
simplest exact method is **brute force**: compare the query with **every** stored vector and sort.
With vectors stacked as rows of a matrix `M` (shape `N × 768`) and a query `q` (length 768):

```
scores = M @ q          # N dot products in one matrix multiplication
order  = argsort(-scores)   # indices of rows, highest score first
top_k  = order[:k]
```

Because every vector is unit length, each dot product *is* the cosine similarity (doc 12, C7).

- **Cost:** N × 768 multiplications per query. For our 223 chunks that is ~171,000, nothing.
  For 100 million chunks it's far too slow, which is why Qdrant uses an **approximate** index
  (HNSW, doc 11) that visits only a small part of the vectors. It trades a little accuracy
  (recall) for enormous speed.
- **Query side:** the question gets `search_query: `, while documents got `search_document: `
  (doc 12, C5).
- **Top-k:** RAG usually keeps the best *k* (e.g. 5) chunks to put in the LLM prompt.

This is exactly what P3-M4/Phase 4 will ask Qdrant to do, so seeing it by hand first shows
what the database is really computing.

**Analogy.** Finding the three people in a room whose taste in music is closest to yours by asking
*everyone* (brute force), versus asking a well-connected friend who points you to the right corner
of the room (approximate index).

**For a 14-year-old.** Give every page and your question a "meaning arrow". The best pages are
the ones whose arrows point most nearly the same way as your question's.

### 2. Why (vs the traditional approach)
Keyword search matches exact words: "founded" finds "founded". Embedding search matches meaning:
"Who started the company?" can find "founded by". But it can also rank a vaguely related chunk
first, as seen below. That's why later phases add hybrid search and reranking.

### 3. Minimal code

#### Example 9 — search the company chunks by hand (live)
`c9_retrieval_sanity.py`
```python
# From Embeddings to a first search: brute-force nearest neighbours with a matrix product.
import numpy as np
import ollama
from src.embedding.embedder import Embedder
from src.embedding.lock import as_query
from src.ingestion.pipeline import read_chunks

url = "http://localhost:11434"
client = ollama.Client(host=url)
subset = [c for c in read_chunks() if c.category == "company"]        # 7 chunks, fast
embeddings = Embedder(client, ollama_url=url).embed_batch(subset)

matrix = np.array([e.vector for e in embeddings])                     # shape (7, 768)
print("matrix shape:", matrix.shape)

for question in ["Who founded the company?", "Where are the offices?", "What are the company values?"]:
    q = np.array(client.embed(model="nomic-embed-text", input=[as_query(question)])["embeddings"][0])
    scores = matrix @ q                                                  # 7 dot products in one step
    order = np.argsort(-scores)                                          # indices, best first
    print(question)
    for i in order[:2]:
        chunk = embeddings[i].chunk
        print(f"   {scores[i]:.3f}  {chunk.chunk_id:<22} {chunk.text[:70]!r}")

about = [e.chunk for e in embeddings if e.chunk.chunk_id == "company/about.md_0"][0]
print("company/about.md_0 is", about.char_count, "chars; it covers:", about.text[:200].replace("\n", " "), "...")
```
Real output:
```
matrix shape: (7, 768)
Who founded the company?
   0.728  company/about.md_0     '# About Insurellm\n\nInsurellm was founded by Avery Lancaster in 2015 as'
   0.594  company/overview.md_0  '# Overview of Insurellm\n\nInsurellm is an innovative insurance tech fir'
Where are the offices?
   0.620  company/careers.md_1   'Insurellm is an equal opportunity employer. We celebrate diversity and'
   0.606  company/about.md_0     '# About Insurellm\n\nInsurellm was founded by Avery Lancaster in 2015 as'
What are the company values?
   0.665  company/careers.md_1   'Insurellm is an equal opportunity employer. We celebrate diversity and'
   0.641  company/culture.md_1   'We foster a culture where diverse perspectives are valued, knowledge i'
company/about.md_0 is 1107 chars; it covers: # About Insurellm  Insurellm was founded by Avery Lancaster in 2015 as an insurance tech startup designed to disrupt an industry in need of innovative products. Its first product was Markellm, the mar ...
```
**How to read it.**
- **`matrix @ q`** computes all 7 similarities at once; `argsort(-scores)` sorts descending (the
  minus sign flips ascending order).
- **"Who founded the company?"** works well: the About chunk wins clearly (0.728 vs 0.594). The
  query says "founded", the chunk says "founded by".
- **"What are the company values?"** is reasonable: careers and culture chunks both talk about
  values.
- **"Where are the offices?"** is a real retrieval miss: the top hit is an equal-opportunity
  paragraph (0.620). The answer **does exist**. Checked with a regex: `about.md_0` says
  *"San Francisco headquarters along with small satellite offices in … New York, Austin,
  Chicago…"*, and `overview.md_0` also mentions offices. `about.md_0` came only **second**
  (0.606). A likely reason: `about.md_0` is 1,107 chars mixing founding history, products,
  restructuring *and* offices, so its single vector is an average of all those topics, and the
  office sentence is a small part of it. `overview.md_0` (2,848 chars) is even more diluted and
  isn't in the top 2. This is the chunk-size problem from P2 (69 % of chunks over the 500-char
  target) showing up as a retrieval failure.
- **Scores cluster between ~0.59 and 0.73.** Absolute values mean little. What matters is the gap
  between the top hit and the rest, and whether the top hit actually answers the question.
- Together with the P3-M3 observation ("Who is the CEO?" ranked an HR note first over 40 chunks),
  this is the first concrete evidence for why RAG systems add **hybrid (keyword + vector) search
  and reranking**.

### 4. Benefits
You see exactly what a vector database computes, get a quick sanity check that embeddings are
meaningful, and have a baseline to compare Qdrant's results against in P3-M4.

- **What breaks:** forgetting `as_query` on the question (doc 12, C5); comparing against vectors
  from a different model (doc 12, C7); misaligned storage (4A).
- **Docs:** [Qdrant: search](https://qdrant.tech/documentation/concepts/search/) ·
  [Malkov & Yashunin 2018, HNSW (arXiv:1603.09320)](https://arxiv.org/abs/1603.09320) ·
  [Pinecone: What is similarity search](https://www.pinecone.io/learn/what-is-similarity-search/) ·
  [Anthropic: Contextual Retrieval (hybrid + reranking)](https://www.anthropic.com/news/contextual-retrieval)

### Micro-exercises
1. Search all 223 chunks instead of 7 (≈ 3.5 minutes to embed). Does "Who founded the company?"
   still rank `about.md_0` first?
2. Split `about.md_0` by hand into 3 smaller pieces (history / restructuring / offices), embed
   them, and rerun "Where are the offices?". Does the office piece now rank first? *Hint:* this
   tests the "diluted vector" explanation instead of assuming it.
3. Embed the query **without** `as_query` and compare the scores and order.
4. Compute the gap `scores[order[0]] - scores[order[1]]` for each question and relate it to how
   good the top hit is.
5. Time `matrix @ q` for a random 100,000 × 768 matrix with numpy. Extrapolate to 100 million.
   *Hint:* this is why HNSW exists.

**Internals.** numpy's `@` calls an optimised BLAS routine (matrix–vector multiplication) that uses
SIMD CPU instructions, much faster than a Python loop over 768 numbers.
**Checkpoint:** explain why brute force is exact but doesn't scale, and what Qdrant's HNSW trades
to fix that.

---

## Validation

Run these from the project root; each line should match.

```bash
.venv/Scripts/python -m pytest tests/embedding -q -p no:warnings
# 22 passed   (7 lock tests + 15 embedder tests)
.venv/Scripts/python -m pytest tests -q -p no:warnings
# 130 passed
.venv/Scripts/python -m mypy src
# Success: no issues found in 16 source files
.venv/Scripts/python -m src.embedding.embedder          # live, ~3.5 minutes
# OK: embedded 223 chunks in ~200s (batch=16, dim=768, digest=0a109f422b47)
```

Self-check questions (answer without looking):
1. Which four checks does `_validate` run, and which failure does each catch?
2. Where exactly are the prefix, `truncate=False` and the digest check enforced?
3. Why does `Embedding.vector` use a tuple?
4. Why does an `EmbeddingError` list several chunk IDs rather than one?

## Visualization

**The data flow through one call** (batch_size = 16, 223 chunks → 14 requests):

```
chunks[0:16]    ─► ["search_document: …" ×16] ─► POST /api/embed ─► 16 vectors ─► _validate ─► 16 Embeddings
chunks[16:32]   ─► ["search_document: …" ×16] ─► POST /api/embed ─► 16 vectors ─► _validate ─► 16 Embeddings
   …                                                                                    (14 requests total)
chunks[208:223] ─► ["search_document: …" ×15] ─► POST /api/embed ─► 15 vectors ─► _validate ─► 15 Embeddings
                                                        │                              │
                                              400 / connection error          count/dim/NaN/norm fails
                                                        └──────────► EmbeddingError(chunk_ids of that batch)
```

**What one `Embedding` looks like**
```
Embedding
├── chunk: DocumentChunk(chunk_id="company/about.md_0", text="# About Insurellm…", …)
├── vector: (-0.0103, 0.0254, -0.2221, … 768 numbers, norm 1.0)
├── model: "nomic-embed-text"
├── model_digest: "0a109f422b47e3a3…"
└── document_prefix: "search_document: "
```

**Tools to see it yourself**
- **Spy client** (example 3A) to watch every request.
- **Ollama logs:** `docker logs -f advancerag-ollama-1` while the CLI runs shows each `/api/embed`
  call and its duration.
- **Similarity heatmap:** `M @ M.T` for the 7 company chunks, plotted with
  `matplotlib.pyplot.imshow`. Bright off-diagonal cells are chunks with overlapping meaning.
- **2-D projection:** `sklearn.decomposition.PCA(n_components=2).fit_transform(M)` for all 223
  chunks, coloured by `category`. Categories should form loose clusters.
- **TensorFlow Embedding Projector** (https://projector.tensorflow.org): export vectors and chunk
  IDs as TSV and explore interactively.

## End-to-end "watch it work" exercise

Do these in order; write down what you observe at each step.

1. **Prove the preconditions.** `python -m src.embedding.lock` → `OK … matches pin`.
2. **Watch the wire.** Run `c3_choke_point.py`. Confirm the prefix and `truncate=False` on every
   request, and that the caller passed raw text.
3. **Break the order on purpose.** In a copy of `c6b_fake_encodes_input.py`, add a fake that swaps
   only the first two vectors. Which chunks FAIL?
4. **Break validation on purpose.** Run `c4c_four_checks.py`, then add an `inf` case and a
   zero-vector case. Note which message fires for each.
5. **Break the server on purpose.** Run `c5a_batch_fails_whole.py`. Then run
   `docker compose stop ollama`, run `python -m src.embedding.embedder`, read the error, and
   restart with `docker compose start ollama`. Decide whether the message is good enough, and write
   what you'd improve.
6. **Run it for real.** `python -m src.embedding.embedder`. While it runs, watch
   `docker logs -f advancerag-ollama-1` in a second terminal and count the `/api/embed` lines (14
   expected).
7. **Use the vectors.** Run `c9_retrieval_sanity.py`, then extend it to all 223 chunks and try
   5 questions of your own. Record each question with its top hit, and whether that hit really
   answers it.
8. **Log it.** Add a line to `LEARNING_LOG.md`: the most surprising result, and one retrieval
   failure you want the later phases (hybrid search, reranking) to fix.

## Canonical sources
- Ollama API `/api/embed` (batched input, `truncate`): https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings
- ollama-python client: https://github.com/ollama/ollama-python
- nomic-embed-text model card (task prefixes): https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- Sentence-Transformers `encode` (batch_size): https://sbert.net/docs/package_reference/sentence_transformer/SentenceTransformer.html
- Python dataclasses: https://docs.python.org/3/library/dataclasses.html
- PEP 618 (`zip(strict=True)`): https://peps.python.org/pep-0618/
- PEP 3134 (exception chaining): https://peps.python.org/pep-3134/
- PEP 544 (Protocols): https://peps.python.org/pep-0544/ · mypy protocols: https://mypy.readthedocs.io/en/stable/protocols.html
- Martin Fowler, "Mocks Aren't Stubs": https://martinfowler.com/articles/mocksArentStubs.html
- Mutation testing — mutmut: https://mutmut.readthedocs.io/ · Petrović & Ivanković, "State of Mutation Testing at Google" (ICSE-SEIP 2018)
- HNSW paper: https://arxiv.org/abs/1603.09320 · Qdrant search: https://qdrant.tech/documentation/concepts/search/
- Anthropic, Contextual Retrieval: https://www.anthropic.com/news/contextual-retrieval
