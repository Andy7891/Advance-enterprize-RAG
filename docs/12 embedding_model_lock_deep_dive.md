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

**1. Definition + analogy.**

*What it is.* A model never sees your text as letters. First, a **tokenizer** cuts the text
into **tokens**: whole common words ("hello"), pieces of rarer words ("embed" + "##ding"),
punctuation, and a few special markers the model adds itself. For nomic-embed-text these
are a start marker `[CLS]` and an end marker `[SEP]`. Each token becomes a row of numbers,
and the model mixes all rows together to produce one 768-number vector for the whole
input.

The **context window** is the maximum number of tokens that mixing step can take:
**2048** for nomic-embed-text. The limit is fixed when the model is built. The model has
learned *position information* for positions 0–2047 only, so it has literally no way to
represent token 2049.

*Three facts that follow from this:*
1. **The limit is in tokens, not characters or words.** `"hello"` is 5 characters but 3
   tokens (`[CLS] hello [SEP]`). Normal English averages about 4 characters per token, but
   numbers, codes, and non-English text use more tokens per character.
2. **Something has to give when input is too long.** Ollama either *refuses* (HTTP 400)
   or *truncates*: it keeps the first 2048 tokens and silently discards the rest. With
   `/api/embed`, the `truncate` option decides, and it defaults to `true`.
3. **Truncation produces a vector that looks perfectly normal.** It has 768 numbers and
   unit length. Nothing about it tells you that the second half of the chunk was never
   read.

*Analogy:* a photocopier's glass. A page larger than the glass either jams the machine
(rejection, which is annoying but you notice) or comes out with its bottom cut off
(truncation, and you file the copy thinking it's complete).

*For a 14-year-old:* the model can only "read" about 2,000 word-pieces at once. Hand it
more and it either refuses, or quietly stops reading and pretends it read everything.

**2. Why (vs traditional).** Keyword search (BM25) indexes a document of any length.
Neural encoders use attention, whose cost grows with the square of the input length, so
the length is fixed at training time.

**3. Minimal code: watch tokens get counted, the tail get discarded, and the refusal.**

Save as `scratch_c1.py` in the project root, with Ollama running, and run
`.venv/Scripts/python scratch_c1.py`.
```python
import json, math, urllib.request, urllib.error

URL = "http://localhost:11434/api/embed"

def embed(text, truncate=True):
    """Send text to Ollama; return (vector, tokens_read). Raises HTTPError if refused."""
    body = {"model": "nomic-embed-text", "input": text, "truncate": truncate}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    data = json.load(urllib.request.urlopen(req))
    # prompt_eval_count = how many tokens the model ACTUALLY read
    return data["embeddings"][0], data["prompt_eval_count"]

def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))

# Step 1: the limit is counted in TOKENS, not characters or words.
for text in ["hello", "hello world", "Employees receive 20 days of paid annual leave."]:
    _, n = embed(text)
    print(f"step1 {len(text):>3} chars -> {n} tokens  : {text!r}")

# Step 2: go past 2048 tokens with the DEFAULT setting (truncate=True).
v2500, n2500 = embed("policy " * 2500)
v5000, n5000 = embed("policy " * 5000)
print("step2 2500 words read", n2500, "tokens; 5000 words read", n5000, "tokens")

# Step 3: proof the tail was thrown away: 2500 and 5000 words give the SAME vector.
print("step3 cosine(2500 words, 5000 words) =", round(cosine(v2500, v5000), 6))

# Step 4: same input with truncate=False -> the model refuses instead.
try:
    embed("policy " * 2500, truncate=False)
except urllib.error.HTTPError as e:
    print("step4 truncate=False ->", e.code, e.read().decode())
```
Real output (2026-09-16):
```
step1   5 chars -> 3 tokens  : 'hello'
step1  11 chars -> 4 tokens  : 'hello world'
step1  47 chars -> 11 tokens  : 'Employees receive 20 days of paid annual leave.'
step2 2500 words read 2048 tokens; 5000 words read 2048 tokens
step3 cosine(2500 words, 5000 words) = 1.0
step4 truncate=False -> 400 {"error":"the input length exceeds the context length"}
```
*How to read it:*
- **Step 1:** `hello` → 3 tokens proves the two hidden markers `[CLS]`/`[SEP]` are
  counted. 47 chars → 11 tokens is about 4.3 chars/token, which is where our "chars / 4"
  estimate comes from.
- **Step 2:** both inputs stop at **exactly 2048**, the context window. Everything past
  it was never read.
- **Step 3:** cosine **1.0** means 2,500 words and 5,000 words produced *identical*
  vectors. The extra 2,500 words had zero effect. This is silent truncation, caught in the
  act.
- **Step 4:** with `truncate: false`, the same input becomes a loud error. That is what
  P3-M3 should use.

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

**1. Definition + analogy.**

*What it is.* Our chunker has **two different size rules**, and they exist for different
reasons:

| | Soft target (`target_chunk_chars = 500`) | Hard cap (`max_chunk_tokens = 1500` → 6,000 chars) |
|---|---|---|
| Protects | retrieval *quality* (small, focused chunks match queries better) | the pipeline's *correctness* (the model must accept the input) |
| When violated | a log line: "consider lowering breakpoint_threshold_amount" | the chunk is **rewritten**: split into smaller pieces |
| Can it be broken? | yes, 156 of 223 chunks break it | no, 0 chunks break it, by construction |

Why not make 500 the hard rule? Semantic chunking puts boundaries where the *meaning*
changes. Forcing a cut every 500 chars would override that meaning signal everywhere. A
cap at 6,000 overrides it only in the rare case where the alternative is failure: 8
chunks out of 223.

*How the hard cap splits (greedy packing).* An oversized chunk is cut into **sentences**
(at `.`, `?`, `!` followed by whitespace). Sentences are then added one by one into a
"current piece". Before each addition we ask: *would this still fit?* If yes, add it. If
no, close the current piece and start a new one with this sentence. "Greedy" means it
never looks ahead or rebalances; it just fills each piece as full as possible. If a single
sentence is itself over the cap (a table, a huge list), the same packing runs on **words**
instead.

*Keeping offsets exact.* Every chunk stores `start_char`/`end_char`: where it lives in the
original file. After a split, each piece needs its *own* offsets. The chunker searches for
each piece in the source text, starting from where the previous piece ended. It matches
words with flexible whitespace, because the source may have blank lines between sentences
that the chunk text doesn't.

*Why the order matters.* `_merge_undersized` glues small chunks onto their neighbour and
can make a chunk *bigger*. So the cap must run **after** it; whatever step runs last is
the only one whose guarantee holds at the end.

*Analogy:* a speed-limit sign (soft: you can exceed it, and you get a warning) versus a
height barrier at a car-park entrance (hard: nothing taller physically gets in). Greedy
packing is loading boxes into a van: put items in until the next one won't fit, then
start a new van.

*For a 14-year-old:* "try to keep it short" is advice; "it must fit in the box" is a rule.
When something doesn't fit, we cut it between sentences and put the pieces in separate
boxes, each labelled with where it came from.

**2. Why (vs traditional).** Fixed-size splitters (e.g. `RecursiveCharacterTextSplitter`)
cap everything but cut through meaning. Semantic chunking respects meaning but has no
upper bound. We keep semantic splits and add a cap as a safety net that only fires when
needed.

**3. Minimal code: re-implement the packer by hand, check it against the real one, then watch offsets get recomputed.**

Save as `scratch_c2.py` in the project root and run
`.venv/Scripts/python scratch_c2.py` (no Ollama needed).
```python
import re
from src.ingestion.chunker import Chunker, ChunkerConfig

LIMIT = 60  # tiny cap so the behaviour is visible; production is 1500 tokens * 4 = 6000 chars

text = ("Leave is 20 days. Sick leave is separate. "
        "Unused leave carries over up to five days per calendar year. Ask HR.")
print("input length", len(text), "limit", LIMIT)

# Step 1: split into sentences at . ? ! followed by whitespace (lookbehind keeps the punctuation).
sentences = [s for s in re.split(r"(?<=[.?!])\s+", text) if s]
for s in sentences:
    print(f"step1 sentence {len(s):>2} chars: {s!r}")

# Step 2: greedy packing: keep appending while it still fits, otherwise close the piece.
pieces, current = [], ""
for unit in sentences:
    candidate = f"{current} {unit}" if current else unit
    if len(candidate) <= LIMIT:
        print(f"step2 add         -> piece now {len(candidate)} chars")
        current = candidate
    else:
        print(f"step2 close piece ({len(current)} chars); adding would make {len(candidate)} > {LIMIT}")
        pieces.append(current)
        current = unit
pieces.append(current)
print("step2 my pieces  :", [len(p) for p in pieces])
print("step2 real _pack :", [len(p) for p in Chunker._pack(text, LIMIT)])

# Step 3: a single sentence longer than the cap has no sentence boundary -> fall back to words.
print("step3 word fallback:", [len(p) for p in Chunker._pack(("word " * 20).strip(), 30)])

# Step 4: the real _split_oversized also recomputes each piece's offsets in the SOURCE text.
source = "Intro.\n\n" + text.replace("days. Sick", "days.\n\nSick")   # source has blank lines
start = source.index("Leave")
chunker = Chunker.__new__(Chunker)            # skip SemanticChunker setup; only the cap is used
chunker._config = ChunkerConfig(max_chunk_tokens=LIMIT // 4)
for piece, s, e in chunker._split_oversized([(text, start, len(source))], source, "demo.md"):
    print(f"step4 [{s:>3},{e:>3}] words match source: {source[s:e].split() == piece.split()}  {piece!r}")
```
Real output:
```
Chunk in demo.md is 110 chars (> 60); splitting to fit the embedding model
input length 110 limit 60
step1 sentence 17 chars: 'Leave is 20 days.'
step1 sentence 23 chars: 'Sick leave is separate.'
step1 sentence 60 chars: 'Unused leave carries over up to five days per calendar year.'
step1 sentence  7 chars: 'Ask HR.'
step2 add         -> piece now 17 chars
step2 add         -> piece now 41 chars
step2 close piece (41 chars); adding would make 102 > 60
step2 close piece (60 chars); adding would make 68 > 60
step2 my pieces  : [41, 60, 7]
step2 real _pack : [41, 60, 7]
step3 word fallback: [29, 29, 29, 9]
step4 [  8, 50] words match source: True  'Leave is 20 days. Sick leave is separate.'
step4 [ 51,111] words match source: True  'Unused leave carries over up to five days per calendar year.'
step4 [112,119] words match source: True  'Ask HR.'
```
*How to read it:*
- **Step 1:** the regex `(?<=[.?!])\s+` splits on the *whitespace after* punctuation.
  The lookbehind `(?<=…)` checks for the `.` without consuming it, so each sentence
  keeps its full stop.
- **Step 2:** follow the decisions. 17 → 41 chars fits. Adding the 60-char sentence would
  make 41 + 1 space + 60 = **102**, so piece 1 is closed at 41. The 60-char sentence
  exactly fills piece 2. Adding "Ask HR." would make 68, so it starts piece 3. The
  hand-written version and the real `Chunker._pack` agree: `[41, 60, 7]`.
- **Step 3:** 20 × "word" is one sentence with no punctuation, so it is packed by words
  into pieces of ≤30 chars.
- **Step 4:** the source has `"Intro.\n\n"` (8 chars) in front and a blank line inside.
  Piece 1 is found at **[8, 50]**: 42 chars of source for 41 chars of chunk text, because
  the source contains `\n\n` where the chunk has one space. Pieces 2 and 3 are searched
  from where piece 1 ended. "words match source: True" is exactly the contract-test rule.
  The first line of output is the real warning the pipeline logs whenever the cap fires.

Order in `Chunker.chunk()`: `_recover_spans` → `_merge_undersized` → **`_split_oversized`**
→ `_warn_oversized`.

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

**1. Definition + analogy.**

*What it is.* An Ollama model is not one file. It's a set of **layers**: the weights
(~274 MB for nomic-embed-text), a license, and default parameters. A small JSON file
called the **manifest** lists every layer by its SHA-256 hash.

- The **name + tag** (`nomic-embed-text:latest`) is a *pointer* to a manifest.
  Pointers can be moved. When the publisher uploads new weights and moves `latest` to
  them, your next `ollama pull nomic-embed-text` downloads the new ones, under the same
  name.
- The **digest** is the SHA-256 hash of the manifest file itself. A hash is a fingerprint
  of content: change one byte anywhere, and the hash is completely different. Since the
  manifest contains the hashes of the weights, a change to the weights changes the
  manifest, which changes the digest. The chain is: weights → layer hash → manifest →
  digest.

So "which model?" has two different answers. The *name* tells you what the model is
called; only the *digest* tells you exactly which weights you have. **Pinning** means
writing the digest into our config and treating any other digest as a different model.

*Why this matters so much for embeddings specifically:* with a chat model, new weights just
give slightly different answers. With embeddings, you've *stored* 223 vectors made by
the old weights. Queries embedded by new weights are compared against them, and the
numbers don't line up (see C7).

*Analogy:* "the latest edition of the Oxford Dictionary" (name + tag: which book that
means changes over time) versus the ISBN of one specific printing (digest: always exactly
one book). If your index cards cite page numbers, they only work with the exact printing.

*For a 14-year-old:* a nickname can be given to someone else; a fingerprint can't.

**2. Why (vs traditional).** With libraries you pin `==1.2.3` in `requirements.txt`, and
the package manager enforces it. Ollama has no lockfile, so nothing stops a
`docker compose pull` or `ollama pull` from changing the weights under you. We store the
digest ourselves.

**3. Minimal code: trace the digest back to the weights and compare it with the pin.**

Save as `scratch_c3.py` and run `.venv/Scripts/python scratch_c3.py` (needs the Ollama
container running).
```python
import hashlib, json, subprocess, urllib.request
from src.core.config import settings

# Step 1: ask Ollama which models it has. A name is a label; the digest identifies content.
tags = json.load(urllib.request.urlopen("http://localhost:11434/api/tags"))["models"]
for m in tags:
    print(f"step1 name={m['name']:<26} digest={m['digest'][:12]}  size={m['size']:,} bytes")

# Step 2: where does the digest come from? It is the SHA-256 of the model's manifest file,
# a small JSON listing the sha256 of every layer (weights, license, params...).
manifest = subprocess.run(
    ["docker", "exec", "advancerag-ollama-1", "cat",
     "/root/.ollama/models/manifests/registry.ollama.ai/library/nomic-embed-text/latest"],
    capture_output=True).stdout
layers = json.loads(manifest)["layers"]
for layer in layers:
    print(f"step2 layer {layer['mediaType']:<36} sha256={layer['digest'][7:19]} size={layer['size']:,}")
print("step2 sha256(manifest) =", hashlib.sha256(manifest).hexdigest()[:12])

# Step 3: compare what is served with the pin stored in config.
served = next(m for m in tags if m["name"] == "nomic-embed-text:latest")
print("step3 served digest :", served["digest"][:12])
print("step3 pinned digest :", settings.embed_model_digest[:12])
print("step3 same weights? :", served["digest"] == settings.embed_model_digest)
```
Real output:
```
step1 name=nomic-embed-text:latest    digest=0a109f422b47  size=274,302,450 bytes
step2 layer application/vnd.ollama.image.model   sha256=970aa74c0a90 size=274,290,656
step2 layer application/vnd.ollama.image.license sha256=c71d239df917 size=11,357
step2 layer application/vnd.ollama.image.params  sha256=ce4a164fc046 size=17
step2 sha256(manifest) = 0a109f422b47
step3 served digest : 0a109f422b47
step3 pinned digest : 0a109f422b47
step3 same weights? : True
```
*How to read it:*
- **Step 1:** the API gives us a name (movable) and a digest (fixed).
- **Step 2:** the manifest lists 3 layers. The 274,290,656-byte `image.model` layer *is*
  the weights, and its own hash is `970aa74c0a90`. Hashing the manifest ourselves gives
  `0a109f422b47`, **the same value Ollama reports as the digest**. You have now verified
  the chain: if the weights change, their layer hash changes, so the manifest changes, so
  the digest changes.
- **Step 3:** this equality check is the entire core of `verify_model`. Try editing
  `embed_model_digest` in your `.env` and running it again.

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

**1. Definition + analogy.**

*What it is.* Two ideas working together.

**(a) Fail-loud startup verification.** Bugs come in two kinds:
- **Loud failures:** an exception, a crash, an HTTP 400. Annoying, but you *know*.
- **Silent failures:** everything "works", yet the result is wrong. A wrong embedding model
  is a silent failure. It still returns 768 unit-length numbers, Qdrant still returns
  top-5 results with scores like 0.6, and nothing ever raises.

You cannot catch a silent failure by "trying and seeing", because there is nothing to see.
The only defence is to **turn it into a loud failure on purpose**: before doing any work,
check the preconditions and raise an exception with a clear message if one is not met.
`verify_model` does this. It gets the list of models from Ollama and, before a single
chunk is processed, handles three situations:

| Situation | Result |
|---|---|
| model present, digest == pin | return the digest; work continues |
| model present, digest ≠ pin | `ModelVersionMismatch`: "re-embed before updating the pin" |
| model missing | `ModelVersionMismatch`: "run `ollama pull …`" |

"At startup" matters because the check is cheap (one HTTP call), while discovering the
problem later is expensive (half a collection written with the wrong model).

**(b) Dependency injection (DI).** `verify_model` needs data from Ollama, but it does not
*hard-code* how to get it. The fetching function is a **parameter**,
`fetch: TagsFetcher = fetch_tags`. Production uses the default, which does a real HTTP
call; a test passes a two-line fake that returns whatever dictionary the test wants. The
function's logic, "compare what's served with the pin", is then testable without Ollama,
without a network, and in milliseconds. "Injection" just means the dependency is handed in
from outside instead of created inside.

A related Python detail tests trip over: `from src.embedding.lock import verify_model`
inside `pipeline.py` copies a **reference** into `pipeline`'s own namespace. Replacing
`lock.verify_model` afterwards doesn't touch that copy. You must patch
`pipeline.verify_model`, the name that is actually looked up when the code runs.

*Analogy:* a pilot's pre-flight checklist, done on the ground rather than discovering a
problem in the air. DI is like a car's diagnostic port: the mechanic plugs in a test
device instead of driving the car around to find out what's wrong.

*For a 14-year-old:* check the batteries before the trip, not halfway up the mountain. And
build the torch so you can test it with a test battery, not only on a real hike.

**2. Why (vs traditional).** The usual pattern is "try it and see". For embeddings,
"see" never happens, because a wrong model returns valid-looking numbers. The failure has
to be *manufactured* by an explicit check.

**3. Minimal code: drive all three branches with fakes (no Ollama), then see the patching trap.**

Save as `scratch_c4.py` and run `.venv/Scripts/python scratch_c4.py`.
```python
from src.embedding.lock import verify_model, ModelVersionMismatch

PIN = "abc123"

# Step 1: fake Ollama answers. No network is needed, because `fetch` is a parameter.
def ollama_serving(digest):
    return lambda url: {"models": [{"name": "nomic-embed-text:latest", "digest": digest}]}

def ollama_empty(url):
    return {"models": []}

# Step 2: walk all three branches of verify_model.
cases = [("pinned model served", ollama_serving(PIN)),
         ("model was re-pulled", ollama_serving("fff999")),
         ("model never pulled ", ollama_empty)]
for label, fake in cases:
    try:
        result = verify_model("http://fake", model="nomic-embed-text", expected_digest=PIN, fetch=fake)
        print(f"step2 {label} -> OK, returned {result!r}")
    except ModelVersionMismatch as e:
        print(f"step2 {label} -> STOP: {e}")

# Step 3: why tests patch pipeline.verify_model and not lock.verify_model.
import src.embedding.lock as lock
import src.ingestion.pipeline as pipeline
original = lock.verify_model
lock.verify_model = lambda *a, **k: "patched"
print("step3 patched lock.verify_model; pipeline.verify_model is still the original:",
      pipeline.verify_model is original)
```
Real output:
```
step2 pinned model served -> OK, returned 'abc123'
step2 model was re-pulled -> STOP: 'nomic-embed-text' digest is fff999, pinned abc123. The model changed; re-embed the collection before updating the pin.
step2 model never pulled  -> STOP: model 'nomic-embed-text' is not available at http://fake; run `ollama pull nomic-embed-text`
step3 patched lock.verify_model; pipeline.verify_model is still the original: True
```
*How to read it:*
- **Step 1:** each fake is a function with the same shape as the real `fetch_tags`: it
  takes a URL and returns `{"models": [...]}`. That shared shape (the `TagsFetcher`
  type) is the only thing `verify_model` depends on.
- **Step 2:** one function, three situations, three outcomes, all in milliseconds with
  no Ollama. Note that the fake serves `nomic-embed-text:latest` while we asked for
  `nomic-embed-text`. It still matched, because `verify_model` accepts the implicit
  `:latest` tag. Each error message tells the reader *what to do next*, which is what
  separates a useful loud failure from a crash.
- **Step 3:** after replacing `lock.verify_model`, `pipeline.verify_model` is **still the
  original function** (`True`). `pipeline` has its own reference, created at import time.
  That's why `tests/ingestion/test_cli.py` patches `pipeline.verify_model`.

Against the live server, the same function with the real pin returns `'0a109f422b47…'`, and
with `expected_digest="deadbeef"` it raises
`'nomic-embed-text' digest is 0a109f422b47, pinned deadbeef. …` (verified).

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

**1. Definition + analogy.**

*What it is.* In retrieval we compare two very different kinds of text:
- a **query**: short, often a question, with words like "how many" and "do employees";
- a **document chunk**: long, a statement, with the answer phrased differently ("receive
  20 days of paid annual leave").

A **symmetric** model embeds both the same way and hopes they land close together. An
**asymmetric** model is *trained* knowing which side each text is on. nomic-embed-text
learned this with **task prefixes**: during training, every question started with
`search_query: ` and every passage with `search_document: `. The model learned that
text after `search_query:` should be placed near the passages that *answer* it, not near
passages that merely look similar.

Three consequences:
1. **The prefix is ordinary text.** There's no special API flag; the string is literally
   glued on the front. It is tokenized, costs tokens, and changes the output vector.
2. **You must use the same prefixes the model was trained with.** Otherwise you're
   feeding it an input format it never saw in training. The model card lists them.
3. **The prefix becomes part of every stored vector.** Documents embedded with
   `search_document:` can only be fairly compared with queries embedded with
   `search_query:`. Change either later, and every stored vector must be regenerated.

The model also defines `clustering:` and `classification:` prefixes for other tasks; RAG
uses only the two search ones.

*Analogy:* a library where each card is stamped "QUESTION" or "BOOK". The librarian was
trained to file QUESTION cards next to the books that answer them. Without the stamp,
cards get filed next to things that merely *sound* similar, e.g. "vacation photos" next to
"vacation days".

*For a 14-year-old:* the model learned "this is a question" and "this is a page to search"
as different jobs. The prefix is a sticky note telling it which job to do.

**2. Why (vs traditional).** Symmetric models (e.g. `all-MiniLM`) embed everything the same
way. But questions and answers look different ("How many vacation days?" versus "Employees
receive 20 days…"), so asymmetric training teaches the model to map them close together.

**3. Minimal code: show that the prefix is real text that changes the vector, then rank passages with and without it.**

Save as `scratch_c5.py` and run `.venv/Scripts/python scratch_c5.py` (needs Ollama).
```python
import json, urllib.request
from src.embedding.lock import as_query, as_document

def embed(text):
    body = {"model": "nomic-embed-text", "input": text}
    req = urllib.request.Request("http://localhost:11434/api/embed", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req))
    return d["embeddings"][0], d["prompt_eval_count"]

def dot(a, b):  # vectors are unit length, so dot product == cosine similarity
    return sum(x * y for x, y in zip(a, b))

# Step 1: a prefix is plain text. It costs tokens and changes the vector.
raw, n_raw = embed("vacation days")
pre, n_pre = embed(as_query("vacation days"))
print(f"step1 tokens without prefix={n_raw}, with={n_pre}; cosine(raw, prefixed)={dot(raw, pre):.3f}")

# Step 2: rank 4 passages for one question, prefixes off vs on.
question = "How many vacation days do employees get?"
passages = ["Employees receive 20 days of paid annual leave per year.",
            "Unused leave can be carried over for up to five days.",
            "The Acme widget ships in blue and red.",
            "Vacation photos from the company retreat are on the intranet."]
for label, q_fmt, d_fmt in [("off", str, str), ("on ", as_query, as_document)]:
    qv, _ = embed(q_fmt(question))
    scores = sorted(((dot(qv, embed(d_fmt(p))[0]), p) for p in passages), reverse=True)
    print(f"step2 prefixes {label}:")
    for s, p in scores:
        print(f"        {s:.3f}  {p}")
```
Real output:
```
step1 tokens without prefix=4, with=8; cosine(raw, prefixed)=0.950
step2 prefixes off:
        0.680  Employees receive 20 days of paid annual leave per year.
        0.665  Unused leave can be carried over for up to five days.
        0.468  Vacation photos from the company retreat are on the intranet.
        0.409  The Acme widget ships in blue and red.
step2 prefixes on :
        0.722  Employees receive 20 days of paid annual leave per year.
        0.655  Unused leave can be carried over for up to five days.
        0.497  Vacation photos from the company retreat are on the intranet.
        0.431  The Acme widget ships in blue and red.
```
*How to read it:*
- **Step 1:** `as_query` is just an f-string, but the model reads 4 extra tokens for
  `search_query: ` and produces a *different* vector: cosine 0.950, not 1.0.
  That is why prefixed and unprefixed vectors must never be mixed in one collection.
- **Step 2, off:** the true answer (0.680) barely beats the carry-over rule (0.665), a gap
  of only **0.015**.
- **Step 2, on:** the true answer (0.722) leads the runner-up (0.655) by **0.067**, about
  4× more separation. The ranking order is the same; the *confidence* of the ranking
  improved.
- Notice that *all* scores moved, including the widget sentence. Absolute scores aren't
  comparable across settings; only the **gaps and the order** matter.
- **Caveat:** earlier, two real corpus queries showed gaps *narrowing* with prefixes
  (0.169→0.124, 0.107→0.083). A handful of queries proves nothing either way, which is why
  prefixes stay on per the model card, configurable, with a proper evaluation pending.

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

**1. Definition + analogy.**

*What it is.* Some values are **coupled**: they must agree, or the system breaks.
In this project:
- `embed_model` decides `embed_dim` (nomic → 768), which must equal the Qdrant
  collection's vector size.
- `embed_model` decides `embed_context_tokens` (2048), which bounds
  `embed_max_chunk_tokens` (1500).
- `embed_model` decides the digest and the prefixes.

If each of those is typed separately in the file that uses it, changing one leaves the
others stale. Before P3-M2, that was the situation: the model name was typed in
`chunker.py` and `pipeline.py`, and `768` in `setup.py`. Nothing connected them.

**Single source of truth** means each value is *defined once*, in `Settings` in
`src/core/config.py`, and every other module *reads* it (`settings.embed_dim`) instead of
repeating it. Coupled values sit next to each other, so the coupling is visible, and a
test can assert it (`embed_max_chunk_tokens < embed_context_tokens`).

*How pydantic-settings fills in a value.* When `Settings()` is created, each field is
looked up in several sources, highest priority first:
1. a keyword argument, `Settings(embed_dim=384)` (mostly used in tests);
2. an **environment variable** with the same name, case-insensitive (`EMBED_DIM=1024`);
3. the `.env` file;
4. the **default** written in the class.

Whatever it finds is **validated and converted** to the declared type. Environment
variables are always strings, so `"1024"` becomes the int `1024`, and `"abc"` is refused
immediately with a `ValidationError`, at startup instead of deep inside Qdrant later.

*One trap:* `VECTOR_SIZE = settings.embed_dim` at the top of `setup.py` **copies** the
value once, when the module is imported. Changing `settings.embed_dim` afterwards
doesn't update that copy. The source is single, but a copy can still go stale.

*Analogy:* one master clock in a railway station, with every platform display showing its
time, versus each platform having its own clock that someone has to set by hand.

*For a 14-year-old:* if your address is written in 3 notebooks, you'll forget to update
one when you move. Write it once, and have everyone look it up.

**2. Why (vs traditional).** Before P3-M2, `"nomic-embed-text"` was in `chunker.py` and
`pipeline.py`, and `768` was in `setup.py`, with no link between them. pydantic-settings
adds typing plus env-var and `.env` overrides for free (12-factor config).

**3. Minimal code: watch the priority order, type conversion, validation, and the import-time trap.**

Save as `scratch_c6.py` and run `.venv/Scripts/python scratch_c6.py` (no services needed).
```python
import os
from pydantic import ValidationError
from pydantic_settings import BaseSettings

class Demo(BaseSettings):
    embed_dim: int = 768           # the class default is the lowest-priority source

# Step 1: nothing set -> class default
print("step1 default             :", Demo().embed_dim)
# Step 2: environment variable beats the default (matched case-insensitively)
os.environ["EMBED_DIM"] = "1024"
print("step2 env EMBED_DIM=1024  :", Demo().embed_dim, type(Demo().embed_dim).__name__)
# Step 3: an explicit keyword argument beats the environment
print("step3 Demo(embed_dim=384) :", Demo(embed_dim=384).embed_dim)
# Step 4: values are validated; junk is rejected at startup, not deep inside Qdrant later
os.environ["EMBED_DIM"] = "abc"
try:
    Demo()
except ValidationError as e:
    print("step4 env EMBED_DIM=abc   -> ValidationError:", e.errors()[0]["msg"])
del os.environ["EMBED_DIM"]

# Step 5: import-time copy pitfall. setup.VECTOR_SIZE copied the value once, when imported.
from src.core.config import settings
import src.vectorstore.setup as setup
settings.embed_dim = 1024
print("step5 settings.embed_dim =", settings.embed_dim, "| setup.VECTOR_SIZE =", setup.VECTOR_SIZE)
```
Real output:
```
step1 default             : 768
step2 env EMBED_DIM=1024  : 1024 int
step3 Demo(embed_dim=384) : 384
step4 env EMBED_DIM=abc   -> ValidationError: Input should be a valid integer, unable to parse string as an integer
step5 settings.embed_dim = 1024 | setup.VECTOR_SIZE = 768
```
*How to read it:*
- **Steps 1–3** show the priority ladder: default (768) < environment (1024) < keyword
  (384). This is how you run the same code with different settings in dev, CI, and
  Docker without editing files.
- **Step 2** also prints `int`: the environment gave the *string* `"1024"`, and pydantic
  converted it because the field is declared `int`.
- **Step 4:** a bad value stops the program at the moment settings load, with a message
  naming the problem. Without validation, `"abc"` would reach Qdrant's
  `create_collection` and fail there with a far less obvious error.
- **Step 5:** `settings` now says 1024, but `setup.VECTOR_SIZE` still says 768, because it
  was copied at import. To see a new value, it must be set *before* import (env var) or
  read at call time (`settings.embed_dim` inside the function).

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

**1. Definition + analogy.**

*What it is.* An embedding is a point in a 768-dimensional space. Think of each of the
768 numbers as a coordinate, like x and y on a graph but with 768 axes. The model places
texts so that **similar meanings end up close together**. "Closeness" is measured with
**cosine similarity**: the cosine of the angle between two vectors. It ranges from +1
(same direction), through 0 (unrelated, at right angles), to −1 (opposite).

*The key fact:* the axes have **no fixed meaning**. Axis 17 doesn't mean "about holidays".
The meaning of every axis is an accident of one particular training run. So:
- Vectors from the **same model, version, and prefixes** share one coordinate system, and
  comparing them is meaningful.
- Vectors from a **different model** usually have a different number of axes (e.g. 384
  vs 768). The maths refuses, so this failure is *loud*.
- Vectors from a **new version with the same dimension** have 768 axes that mean
  *something else*. The maths happily computes a number, but it's nonsense. This
  failure is *silent*, and it's the one that digest pinning (C3) exists to catch.

*Unit norm.* nomic-embed-text returns vectors of **length 1** (the square root of the
sum of the squares is 1). Cosine similarity is defined as
`dot(a, b) / (|a| × |b|)`, and when both lengths are 1 that becomes just `dot(a, b)`.
Qdrant also normalises vectors in a cosine collection on insert (P3-M1), so both sides
agree.

*Analogy:* GPS coordinates. "51.5, −0.12" is London only if you know which coordinate
system was used. Plot old coordinates on a map drawn with a different system, and every
point lands somewhere confidently wrong, with no error.

*For a 14-year-old:* two treasure maps of the same island, but one is rotated. Each map
works fine by itself. Use directions from one on the other, and you'll dig in the wrong
place, and you'll never know you used the wrong map.

**2. Why (vs traditional).** A keyword index is readable and portable. A vector is
meaningless outside its model. You can't mix, migrate, or "fix up" vectors, only
regenerate them.

**3. Minimal code: prove cosine equals dot, then simulate a model update and watch the ranking collapse.**

Save as `scratch_c7.py` and run `.venv/Scripts/python scratch_c7.py` (needs Ollama).
```python
import json, urllib.request
import numpy as np
from src.embedding.lock import as_query, as_document

def embed(text):
    body = {"model": "nomic-embed-text", "input": text}
    req = urllib.request.Request("http://localhost:11434/api/embed", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return np.array(json.load(urllib.request.urlopen(req))["embeddings"][0])

passages = ["Employees receive 20 days of paid annual leave per year.",
            "The Acme widget ships in blue and red.",
            "Contracts renew automatically every twelve months."]
docs = np.array([embed(as_document(p)) for p in passages])   # shape (3, 768)
q = embed(as_query("How much holiday do staff get?"))         # shape (768,)

# Step 1: unit length -> cosine similarity equals the plain dot product.
print("step1 norms :", np.round(np.linalg.norm(docs, axis=1), 4))
cos = docs @ q / (np.linalg.norm(docs, axis=1) * np.linalg.norm(q))
print("step1 cosine:", np.round(cos, 3), "| dot:", np.round(docs @ q, 3))

# Step 2: simulate a model UPDATE that keeps 768 dims: a random rotation of the whole space.
rng = np.random.default_rng(0)
rotation, _ = np.linalg.qr(rng.normal(size=(768, 768)))
new_docs = docs @ rotation.T          # documents re-embedded with the "new model"
new_q = rotation @ q                  # query embedded with the "new model"
print("step2 new query vs NEW docs:", np.round(new_docs @ new_q, 3), "<- same scores as step1")
print("step2 new query vs OLD docs:", np.round(docs @ new_q, 3), "<- noise, ranking lost")
print("step2 dimensions still match:", docs.shape[1] == new_q.shape[0])

# Step 3: a different dimension (another model family) fails loudly instead.
try:
    docs @ np.zeros(384)
except ValueError as e:
    print("step3 768 vs 384 ->", str(e)[:70])
```
Real output:
```
step1 norms : [1. 1. 1.]
step1 cosine: [0.614 0.41  0.47 ] | dot: [0.614 0.41  0.47 ]
step2 new query vs NEW docs: [0.614 0.41  0.47 ] <- same scores as step1
step2 new query vs OLD docs: [-0.035 -0.055  0.007] <- noise, ranking lost
step2 dimensions still match: True
step3 768 vs 384 -> matmul: Input operand 1 has a mismatch in its core dimension 0, with g
```
*How to read it:*
- **Step 1:** every norm is 1.0, so the cosine formula and the plain dot product print
  identical numbers. The holiday passage wins (0.614), even though the query says
  "holiday"/"staff" and the passage says "annual leave"/"employees". That's the meaning
  space working.
- **Step 2:** a **rotation** turns the whole space without changing any angles inside it.
  It's a simplified stand-in for "new weights with the same dimension" (a real new model
  changes more than a rotation, but the point is the same). If *everything* is
  re-embedded with the new model, the scores are identical (0.614, 0.41, 0.47). Mix a
  new-model query with old-model documents, and the scores collapse to about 0 (−0.035,
  −0.055, 0.007): the right answer is now ranked *second*, and the numbers are noise.
- **"dimensions still match: True"** is the scary line: a dimension check would pass this
  broken setup. Only the digest pin (C3) catches it.
- **Step 3:** a 384-dim vector against 768-dim documents raises immediately. A different
  model family is the *easy* case.

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
