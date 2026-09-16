# 12 — Embedding Model Lock: Deep Dive (P3-M2)

> Every number in this document was produced by running code on 2026-09-16 against
> Ollama **0.20.0** serving `nomic-embed-text` (digest `0a109f422b47…`). Commands to
> reproduce are in each exercise. If your numbers differ, that is a finding, not a typo.

---

## 0. Plain-language summary

An embedding model turns text into a list of 768 numbers. Retrieval works only if
**every** vector in Qdrant and **every** query vector come from *the same model, the same
version, and the same input format*. P3-M2 makes that guarantee explicit:

1. **Chunks must fit** the model's input limit (context window) → hard cap in the chunker.
2. **The model must not change silently** → pin the digest, verify at startup.
3. **Inputs must be formatted the way the model was trained** → task prefixes.
4. **All of the above live in one place** → `Settings` in `src/core/config.py`.

### Concepts map

```
                  src/core/config.py  (C6 single source of truth)
                 /          |               |                 \
   embed_max_chunk_tokens  embed_model_digest  prefixes        embed_dim
          |                    |                 |                 |
   chunker._split_oversized  lock.verify_model  lock.as_query/   setup.py VECTOR_SIZE
   (C1 context window,       (C3 name≠version,  as_document      (C7 embedding space)
    C2 hard cap vs soft)      C4 fail loud + DI) (C5 asymmetric)
          \___________________________|_______________/
                                      v
                  P3-M3: embed 223 chunks → upsert to Qdrant "chunks"
```

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | Context window | **Core RAG** |
| C2 | Hard cap vs soft target (greedy packing) | **Core RAG** |
| C3 | Model name ≠ model version (digest pinning) | **Core RAG / MLOps** |
| C4 | Fail-loud startup check + dependency injection | Engineering (interview-heavy) |
| C5 | Asymmetric task prefixes | **Core RAG** |
| C6 | Single source of truth config | Boilerplate (but classic interview topic) |
| C7 | Embedding-space compatibility & unit-norm vectors | **Core RAG** |

---

## ⚠️ Correction found while verifying this document

The P3-M2 notes say Ollama **rejects** over-long input rather than truncating. Testing
showed that is only half true on Ollama 0.20.0:

```
3000 × "policy" (default)         -> OK, prompt_eval_count=2048   ← silently TRUNCATED
3000 × "policy" (truncate=false)  -> HTTP 400 input length exceeds the context length
largest real doc, 14,592 chars    -> HTTP 400 (both default and truncate=false)
first 6,000 chars of it           -> OK, prompt_eval_count=1177
```

`/api/embed` has a `truncate` option that defaults to `true`. Some inputs get cut to 2048
tokens *without any error*; our real text was rejected anyway. **Lesson: the cap is doubly
necessary.** Rejection crashes ingestion; silent truncation is worse, because the tail of
the chunk is never embedded and nobody finds out. P3-M3 should send `truncate: false`, so
that any overflow fails loudly. (Source: Ollama API docs, `POST /api/embed`,
https://github.com/ollama/ollama/blob/main/docs/api.md)

---

## C1 — Context window

**1. Definition + analogy.** The context window is the maximum number of *tokens* a model
reads in one go. For nomic-embed-text it is **2048**. Analogy: a photocopier's glass. A
page larger than the glass either jams the machine (rejection) or gets copied with its
bottom cut off (truncation).
*For a 14-year-old:* the model can only "read" about 2,000 word-pieces at once. Hand it
more and it either refuses or quietly stops reading.

**2. Why (vs traditional).** Keyword search (BM25) indexes a document of any length.
Neural encoders use attention, whose cost grows with the square of the input length, so
the length is fixed at training time.

**3. Minimal code.**
```python
import json, urllib.request
body = {"model": "nomic-embed-text", "input": "policy " * 3000, "truncate": False}
req = urllib.request.Request("http://localhost:11434/api/embed",
      data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
urllib.request.urlopen(req)   # -> HTTPError 400: input length exceeds the context length
```

**4. Benefits of respecting it.** No crashed ingestion, no invisible lost text, and every
vector represents the whole chunk.

- **Why here:** P2 only ever embedded *single sentences* (for semantic breakpoints), so the
  whole chunks were never sent until the P3-M2 audit: **1/214 rejected**.
- **What breaks:** P3-M3 crashes mid-upsert, or (with truncation) retrieval can never
  surface facts from the tail of long chunks.
- **Docs:** `ollama show nomic-embed-text` (context length); nomic-embed-text model card
  https://huggingface.co/nomic-ai/nomic-embed-text-v1.5

### Micro-exercises
1. Run `docker exec advancerag-ollama-1 ollama show nomic-embed-text`. Find the context and
   embedding length. *Hint:* look under "Model".
2. Send `"policy " * 3000` with the default settings and read `prompt_eval_count`.
   *Hint:* what number would prove truncation?
3. Repeat with `"truncate": false`. *Hint:* expect HTTP 400.
4. Binary-search the word count at which `truncate:false` starts failing. *Hint:* is
   "policy " one token or two?
5. Take the first 6,000 chars of the largest `data/` file and compare its tiktoken
   `cl100k_base` count with Ollama's `prompt_eval_count`. *Hint:* measured 1231 vs 1177, so
   different tokenizers give different counts.

**How it works internally.** Ollama tokenizes with the model's own BERT WordPiece
vocabulary, then compares the token count to `num_ctx`. With `truncate=true` it keeps the
first N tokens; otherwise it returns 400.
**Checkpoint:** you can explain why "2048 tokens" is not "2048 × 4 characters".

---

## C2 — Hard cap vs soft target (greedy packing)

**1. Definition + analogy.** A *soft target* is a preference, like "chunks around 500
chars", enforced by logging. A *hard cap* is a limit, "never above 6,000 chars", enforced
by changing the data. Analogy: a speed limit sign (soft) versus a height barrier at a
car park (hard).
*For a 14-year-old:* "try to keep it short" is advice; "it must fit in the box" is a rule.

**2. Why (vs traditional).** Fixed-size splitters (e.g. `RecursiveCharacterTextSplitter`)
cap everything but cut through meaning. Semantic chunking respects meaning but has no
upper bound. We keep semantic splits and add a cap as a safety net that only fires when
needed.

**3. Minimal code** (the real functions):
```python
from src.ingestion.chunker import Chunker
Chunker._pack("A"*50 + ". " + "B"*50 + ". " + "C"*50 + ".", 110)   # -> piece lengths [103, 51]
Chunker._pack_words("aaa bbb ccc ddd", 7)                        # -> ['aaa bbb', 'ccc ddd']
```
Order in `Chunker.chunk()`: `_recover_spans` → `_merge_undersized` → **`_split_oversized`**
→ `_warn_oversized`. The split runs last so that nothing after it can make a chunk bigger.

**4. Benefits.** It is guaranteed to hold, sentence boundaries are kept where possible,
the offsets are still exact, and it measurably changed the data: chunks over 6,000 chars
went 8 → 0 and the largest went 13,599 → 5,983.

- **Why 6,000 chars?** 1500 tokens × 4 chars/token. That leaves about 27% headroom under
  2048 because chars/4 is only an estimate (C1 exercise 5).
- **What breaks:** swap merge and split, and a merge can glue a 5,900-char piece to a
  small neighbour, pushing it over the cap again. Remove the cap and 3 tests fail
  (mutation-verified).
- **Docs:** LangChain text splitters
  https://python.langchain.com/docs/concepts/text_splitters/

### Micro-exercises
1. Call `_pack` on a 3-sentence string with a limit that fits exactly 2 sentences.
   *Hint:* the joining space counts.
2. Call `_pack_words` with a single word longer than the limit. *Hint:* read
   `or not current`. Why is it there?
3. Set `embed_max_chunk_tokens=500` via env (`EMBED_MAX_CHUNK_TOKENS=500`), rerun the
   pipeline and count chunks. *Hint:* write to a scratch output directory.
4. Comment out the `_split_oversized` line and run
   `pytest tests/ingestion/test_oversize_cap.py`. *Hint:* expect 3 failures, then restore
   the line.
5. For one split chunk, check `source_text[start:end].split() == text.split()`.
   *Hint:* that is the contract test's criterion.

**Internals.** Split into sentences with `(?<=[.?!])\s+`. Any sentence longer than the cap
falls back to words. Greedily fill pieces up to the limit. Locate each piece with
`_locate` starting from the previous piece's end, so repeated sentences map to the right
place.
**Checkpoint:** explain why a greedy packer never produces an empty piece and never
exceeds the limit (except for a single word longer than the limit).

---

## C3 — Model name ≠ model version (digest pinning)

**1. Definition + analogy.** `nomic-embed-text:latest` is a *tag*: a label that can be
moved to different weights. The **digest** is a SHA-256 of the actual model content.
Analogy: "the latest edition of the dictionary" versus the ISBN of one printing.
*For a 14-year-old:* a nickname can be given to someone else; a fingerprint can't.

**2. Why (vs traditional).** With libraries you pin `==1.2.3` in `requirements.txt`, and
the package manager enforces it. Ollama has no lockfile, so nothing stops a
`docker compose pull` or `ollama pull` from changing the weights under you. We store the
digest ourselves.

**3. Minimal code.**
```python
import json, urllib.request
tags = json.load(urllib.request.urlopen("http://localhost:11434/api/tags"))["models"]
[(m["name"], m["digest"][:12]) for m in tags]  # -> [('nomic-embed-text:latest', '0a109f422b47'), ...]
```

**4. Benefits.** Reproducible vectors and detected drift. The upgrade becomes a deliberate
act, documented in `docs/architecture.md`.

- **What breaks without it:** after a re-pull, new query vectors are compared against old
  document vectors. Scores still come back and look plausible, but rankings silently get
  worse. No exception is ever raised.
- **Docs:** Ollama API `GET /api/tags`; OCI content digests
  https://github.com/opencontainers/image-spec/blob/main/descriptor.md#digests

### Micro-exercises
1. `curl http://localhost:11434/api/tags` and find the full digest. *Hint:* compare it to
   `settings.embed_model_digest`.
2. Why does `verify_model` accept both `nomic-embed-text` and `nomic-embed-text:latest`?
   *Hint:* look at what `/api/tags` returns.
3. Pull a second model (e.g. `all-minilm`) and confirm its digest differs. *Hint:* also
   compare `ollama show` dims.
4. Write down which files you'd change and which data you'd delete to upgrade the model.
   *Hint:* `docs/architecture.md` has the procedure; compare it with your answer.
5. Explain why the error message says "re-embed the collection **before** updating the
   pin". *Hint:* what if you updated the pin first?

**Internals.** Ollama stores models as OCI-style layers. The manifest digest identifies the
exact blob set, so identical weights give an identical digest on any machine.
**Checkpoint:** name one scenario where the tag stays the same but the vectors change.

---

## C4 — Fail-loud startup verification + dependency injection

**1. Definition + analogy.** Check the preconditions *before* doing work, and stop with a
clear error if they don't hold. Analogy: a pilot's pre-flight checklist, done on the
ground rather than in the air.
*For a 14-year-old:* check the batteries before the trip, not halfway up the mountain.

**2. Why (vs traditional).** The usual pattern is "try it and see". For embeddings,
"see" never happens, because a wrong model returns valid-looking numbers. The failure has
to be *manufactured* by an explicit check.

**3. Minimal code** (verified live):
```python
from src.embedding.lock import verify_model, ModelVersionMismatch
verify_model("http://localhost:11434")[:12]            # '0a109f422b47'
verify_model("http://localhost:11434", expected_digest="deadbeef")
# ModelVersionMismatch: 'nomic-embed-text' digest is 0a109f422b47, pinned deadbeef.
#   The model changed; re-embed the collection before updating the pin.
verify_model("http://localhost:11434", model="all-minilm")
# ModelVersionMismatch: model 'all-minilm' is not available ...; run `ollama pull all-minilm`

# Dependency injection: tests pass a fake instead of hitting the network
verify_model("x", expected_digest="abc", fetch=lambda url: {"models": [{"name": "nomic-embed-text:latest", "digest": "abc"}]})
```

**4. Benefits.** Errors appear in seconds with an actionable fix. Tests run offline (7
lock tests need no Ollama), and the check can be reused by the pipeline, the Qdrant setup,
and later the API.

- **Interview line:** "Silent failures must be converted into loud ones at a boundary.
  The digest check is that boundary for model drift."
- **What breaks:** skip the check in one entry point (say, a future query API) and that
  path can drift alone, so queries and documents end up in different spaces.
- **Docs:** pytest monkeypatch https://docs.pytest.org/en/stable/how-to/monkeypatch.html

### Micro-exercises
1. `EMBED_MODEL_DIGEST=bad .venv/Scripts/python -m src.embedding.lock`. *Hint:* pydantic
   reads env vars.
2. Stop Ollama and run the lock CLI. What error do you get, and is it clear? *Hint:* it's a
   `URLError`, not `ModelVersionMismatch`. Is that acceptable?
3. Read `tests/ingestion/test_cli.py` and find where `verify_model` is replaced. *Hint:* why
   patch `pipeline.verify_model`, not `lock.verify_model`?
4. Write a test for a tagged model name like `nomic-embed-text:v1.5`. *Hint:* the `wanted`
   set.
5. List every entry point that embeds text today. Does each call `verify_model`?
   *Hint:* `grep -rn verify_model src`.

**Internals.** `fetch` defaults to `fetch_tags`, but default arguments are bound at
definition time, so passing `fetch=` swaps the network dependency for any callable.
**Checkpoint:** explain exercise 3's answer (patch where the name is *looked up*).

---

## C5 — Asymmetric task prefixes

**1. Definition + analogy.** nomic-embed-text was trained with an instruction at the start
of each input: `search_query: ` for questions and `search_document: ` for passages.
Analogy: labelling envelopes "question" and "answer" so the sorter knows how to file them.
*For a 14-year-old:* the model learned "this is a question" and "this is a page to search"
as different jobs; the prefix tells it which job to do.

**2. Why (vs traditional).** Symmetric models (e.g. `all-MiniLM`) embed everything the same
way. But questions and answers look different ("How many vacation days?" versus "Employees
receive 20 days…"), so asymmetric training teaches the model to map them close together.

**3. Minimal code + a measured result:**
```python
from src.embedding.lock import as_query, as_document
as_query("x"), as_document("x")   # ('search_query: x', 'search_document: x')
```
One hand-built query ("How many vacation days do employees get?") against a relevant and
an irrelevant sentence:
```
prefixes off: relevant 0.680  irrelevant 0.409  gap 0.272
prefixes on : relevant 0.722  irrelevant 0.431  gap 0.291
```
Earlier, two real corpus queries showed the *opposite* (gaps 0.169→0.124 and
0.107→0.083). **Three queries prove nothing either way.** That is why P3-M2 left the
prefixes on per the model card, as configurable, and marked a proper evaluation as pending.

**4. Benefits.** Matches the model's training distribution; the model card reports better
retrieval with prefixes. It's configurable, so an evaluation can switch it off.

- **What breaks:** prefixes are *baked into stored vectors*. Change the document prefix and
  you must re-embed everything. Forgetting `as_query` at query time is another silent
  mismatch.
- **Docs:** https://huggingface.co/nomic-ai/nomic-embed-text-v1.5 (section "Task instruction
  prefixes"); Nussbaum et al. 2024, *Nomic Embed* https://arxiv.org/abs/2402.01613

### Micro-exercises
1. Reproduce the off/on table above. *Hint:* cosine of the two vectors, or just the dot
   product since they are unit-norm.
2. Try a query with `search_document:` as its prefix. *Hint:* does the gap shrink?
3. Build 5 queries with known relevant chunks and compare recall@3 on and off.
   *Hint:* that's exercise 4 in the notes, started small.
4. Find where P3-M3 must call `as_document` and where Phase 4 must call `as_query`.
   *Hint:* grep for `embed_query`/`embed_documents`.
5. Explain why the prefix is in config but **also** needs to be recorded next to the
   collection. *Hint:* config can change; stored vectors can't.

**Internals.** The prefix is just text: it's tokenized and passed through the encoder like
any other tokens, and it shifts the pooled output vector toward the "query" or "document"
region the model learned.
**Checkpoint:** explain why the scores went up in both directions, and why only the *gap*
matters for ranking.

---

## C6 — Single source of truth config

**1. Definition + analogy.** Every value appears in exactly one place and everything else
reads it from there. Analogy: one master clock in a station, not every platform setting
its own.
*For a 14-year-old:* if your address is written in 3 notebooks, you'll forget to update
one when you move.

**2. Why (vs traditional).** Before P3-M2, `"nomic-embed-text"` was in `chunker.py` and
`pipeline.py`, and `768` was in `setup.py`, with no link between them. pydantic-settings
adds typing plus env-var and `.env` overrides for free (12-factor config).

**3. Minimal code.**
```python
from src.core.config import settings
settings.embed_dim, settings.embed_max_chunk_tokens * 4, settings.embed_context_tokens
# (768, 6000, 2048)
```
```bash
EMBED_DIM=1024 .venv/Scripts/python -c "from src.core.config import settings; print(settings.embed_dim)"
```

**4. Benefits.** One edit per change, overridable per environment, testable invariants
(`test_lock.py` asserts `embed_max_chunk_tokens < embed_context_tokens`).

- **What breaks:** config is a single source, but it is *not* a single source of truth for
  data that already exists. Change `embed_dim` to 1024 and the existing 768-dim collection
  won't match; `ensure_collection` raises `CollectionConfigMismatch` (P3-M1).
- **Docs:** https://docs.pydantic.dev/latest/concepts/pydantic_settings/ ;
  https://12factor.net/config

### Micro-exercises
1. `grep -rn "768\|nomic-embed-text" src` and confirm only `config.py` holds them.
   *Hint:* docstrings may mention them. Is that OK?
2. Override `QDRANT_COLLECTION=scratch` and run `python -m src.vectorstore.setup`.
   *Hint:* delete the scratch collection afterwards.
3. Put an invalid `EMBED_DIM=abc` in the env. *Hint:* pydantic's ValidationError.
4. Why is `setup.py`'s `VECTOR_SIZE = settings.embed_dim` evaluated at import time, and when
   does that matter? *Hint:* tests that monkeypatch settings.
5. Write the test from notes exercise 9 (no hardcoded model constants in `src/`).

**Internals.** `Settings()` reads, in priority order: init kwargs > env vars > `.env` file
> class defaults, validating types as it goes.
**Checkpoint:** name a value that should *not* be overridable by env var, and why.

---

## C7 — Embedding-space compatibility & unit-norm vectors

**1. Definition + analogy.** Vectors are comparable only if they were made by the same
model, version, and input format. They then live in one "space" where closeness means
similar meaning. Analogy: GPS coordinates. Two sets of coordinates are comparable only if
they use the same map projection.
*For a 14-year-old:* two maps of the same city with different scales. Measuring distance
across them gives nonsense.

**2. Why (vs traditional).** A keyword index is readable and portable. A vector is
meaningless outside its model. You can't mix, migrate, or "fix up" vectors, only
regenerate them.

**3. Minimal code** (verified):
```python
v = embed("hello")          # via /api/embed
len(v), round(sum(x*x for x in v) ** 0.5, 4)   # (768, 1.0)  -> unit length
```
Because the vectors are unit-norm, cosine = dot product. That's why the Qdrant cosine
collection (P3-M1) and plain dot products agree.

**4. Benefits of guarding it.** Rankings stay meaningful; the dimension check catches
cross-model mixing (768 ≠ 384). The digest check catches same-dimension drift, which the
dimension check can never catch.

- **Interview line:** "A dimension check catches the wrong model; only a version pin catches
  the right model name with different weights."
- **Docs:** Qdrant distance metrics
  https://qdrant.tech/documentation/concepts/search/#metrics

### Micro-exercises
1. Verify the norm of 3 different embeddings is 1.0. *Hint:* short, long, and prefixed
   text.
2. Compare cosine and dot product for two vectors. *Hint:* they should be equal to within
   ~1e-6.
3. Embed the same text twice and check the vectors are identical. *Hint:* determinism on
   CPU.
4. Embed with and without a prefix and compute the cosine. *Hint:* high but not 1.0, so
   they're different vectors.
5. Explain which guard (dimension check, digest check, or prefix config) catches each case:
   wrong model, re-pulled model, changed prefix. *Hint:* one case has no automated guard
   yet.

**Checkpoint:** answer exercise 5 without looking. (The prefix change is the unguarded one.
P3-M3 should store the prefix and digest in collection metadata or the payload.)

---

## Validation

Run all of these; every line should match.

```bash
.venv/Scripts/python -m src.embedding.lock
# OK: nomic-embed-text digest 0a109f422b47 matches pin (dim=768, context=2048)
.venv/Scripts/python -m pytest tests/embedding tests/ingestion/test_oversize_cap.py -q
# 14 passed  (report.json in data/chunks is skipped by the *.md.json glob)
.venv/Scripts/python -m pytest tests/ -q
# 115 passed (contract tests skip if data/chunks is empty)
```

Data check after running the pipeline:
```python
import json, glob
chunks = [c for p in glob.glob("data/chunks/*.md.json") for c in json.load(open(p, encoding="utf-8"))]
len(chunks), max(c["char_count"] for c in chunks), sum(c["char_count"] > 500 for c in chunks)
# (223, 5983, 156)
```

## Visualization

```
 raw doc ──SemanticChunker──> spans ──merge small──> spans ──split > 6000──> spans
                                                                     │
                     config.embed_max_chunk_tokens ×4 ───────────────┘
                                                                     v
   startup: /api/tags ──digest==pin?──no──> ModelVersionMismatch (stop)
                              │yes
                              v
   as_document(text) ──/api/embed (truncate:false)──> 768-d unit vector ──> Qdrant "chunks"
   as_query(question) ──/api/embed──────────────────> 768-d unit vector ──> cosine search
```

Tools to *see* it:
- **Chunk-size histogram:** matplotlib `plt.hist([c["char_count"] for c in chunks], bins=40)`
  with `axvline(500)` and `axvline(6000)`. You'll see the long tail stopping at the cap.
- **Qdrant dashboard:** http://localhost:6333/dashboard (after P3-M3) for points and payloads.
- **Embedding projector:** https://projector.tensorflow.org. Export vectors as TSV to see the
  prefix on/off clusters.

## End-to-end "watch it work" exercise

1. `python -m src.embedding.lock` → OK.
2. Set `EMBED_MODEL_DIGEST=0000` in your shell, run `python -m src.ingestion.pipeline`, and
   watch it stop **before** chunking. Unset it.
3. Set `EMBED_MAX_CHUNK_TOKENS=250` (1,000-char cap), run the pipeline into a scratch
   output, and read the warnings `Chunk in … is N chars (> 1000); splitting`. Count chunks
   against 223.
4. Restore the default and rerun: 223 chunks, max 5,983.
5. Take the largest chunk, then embed it with `truncate:false` twice: once as is (OK), once
   doubled (400). You've just watched the exact failure P3-M2 prevents.
6. Write in LEARNING_LOG.md what surprised you, e.g. the silent truncation in the correction
   section above.

## Canonical sources
- Ollama API (`/api/embed`, `/api/tags`, `truncate`): https://github.com/ollama/ollama/blob/main/docs/api.md
- nomic-embed-text model card: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- Nomic Embed paper: https://arxiv.org/abs/2402.01613
- pydantic-settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- Qdrant distance metrics: https://qdrant.tech/documentation/concepts/search/#metrics
- LangChain text splitters: https://python.langchain.com/docs/concepts/text_splitters/
