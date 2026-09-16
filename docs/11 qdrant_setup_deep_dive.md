# Qdrant Setup Deep Dive: Reverse-Engineering Phase 3, Microstep 1

A self-contained learning path for
[`src/vectorstore/setup.py`](../src/vectorstore/setup.py),
[`tests/vectorstore/test_setup.py`](../tests/vectorstore/test_setup.py) and the `qdrant`
service in [`docker-compose.yml`](../docker-compose.yml) — validated against the running
server, not just described.

**How to use this doc:** run each exercise before reading its checkpoint. Five exercises per
concept, easiest → hardest, with hints for complete beginners.

**Verified facts this doc is built on:**

```
nomic-embed-text output size (measured via Ollama)   : 768
collection 'chunks'                                   : status green, size 768, Cosine, 0 points
survives `docker compose restart qdrant`              : yes
on disk inside the container                          : /qdrant/storage/collections/chunks/config.json
HNSW index defaults                                   : m=16, ef_construct=100, full_scan_threshold=10000
Qdrant with Cosine stores [3, 4] as                   : [0.6, 0.8]   <- normalised on insert
query [1,0] vs a=[1,0.1], b=[5,5]  cosine picks       : a (0.995 vs 0.707)
                                   dot product picks  : b (5.0 vs 1.0)   <- different winner
named-vectors collection config type                  : dict (has no .size)
```

---

## 0. What this step does, in plain English

Phase 2 turned 76 documents into 214 chunks of text. Phase 3 is about *finding* the right
chunk when someone asks a question. You can't search meaning with Ctrl+F — "What does
Insurellm pay new hires?" shares almost no words with a paragraph titled "Compensation
Structure." So each chunk will be turned into a list of 768 numbers (an **embedding**) that
captures its meaning, and a **vector database** will find the lists closest to the question's
list.

This microstep builds only the *empty container* for those numbers. Think of it as building a
library's shelves before any books arrive — but with one rule that makes it matter: **the
shelf size can never change once built.** Build shelves for 384-page books, and the 768-page
books will never fit. You'd have to tear the shelves down and re-shelve everything.

That is why this small step needed care: measure the real book size first, build shelves to
match, and make sure nobody quietly builds the wrong shelves later.

**Concept map:**

```
What Qdrant is:                    vector database + collections + points
What goes in it:                   embeddings and their dimension (768)
What "close" means:                distance metrics (cosine vs dot vs euclid)
Why order of decisions matters:    config that is immutable at creation
Safe to run twice:                 idempotent setup (create-if-missing, verify-if-present)
Refusing to guess:                 fail-loud custom exception, no auto-repair
Two shapes of collection:          named vs unnamed vectors (and check ordering)
Testing without a server:          in-memory Qdrant client
Keeping data across restarts:      Docker named volumes, healthchecks, REST inspection
What makes search fast:            the HNSW index
```

---

## 1. Concept-by-concept

---

### 1.1 Vector database, collections and points

**1. Definition (plain English):** a vector database stores lists of numbers (vectors) and
answers one question extremely fast: "which stored vectors are closest to this one?" Data is
organised into **collections** (like tables), each holding **points** — a point is an `id`, a
`vector`, and an optional `payload` (any JSON, e.g. the chunk text). Analogy: a map of a city
where every building is a pin. Asking "what's near me?" is instant, because the map is built
for exactly that question.

**2. The "Why" — vs. the traditional approach:** a traditional database (Postgres, SQLite)
answers "find rows where `category = 'contracts'`" beautifully, but "find the 5 rows whose
768-number vector is nearest to this one" would mean computing a distance to *every* row —
fine at 214, painful at 2 million. A vector database indexes vectors specifically so nearest-
neighbour search stays fast as data grows, and keeps the payload next to the vector so a hit
comes back with its text and metadata attached.

**3. Minimal code example:**
```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

client = QdrantClient(":memory:")
client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
client.upsert("demo", points=[
    PointStruct(id=1, vector=[1.0, 0.0], payload={"text": "cats"}),
    PointStruct(id=2, vector=[0.0, 1.0], payload={"text": "tax law"}),
])
print(client.query_points("demo", query=[0.9, 0.1], limit=1).points[0].payload)  # {'text': 'cats'}
```

**4. Main benefits:** (a) nearest-neighbour search that stays fast at scale; (b) vector and
payload stored together, so results are immediately usable; (c) payload filtering (e.g. only
`category = contracts`) combined with similarity in one query.

**What breaks without it:** you'd load every vector into Python and compute distances in a
loop — workable for 214 chunks, unusable for a real corpus, and with no persistence.

**Canonical reading:** [Qdrant — Concepts overview](https://qdrant.tech/documentation/concepts/), [Collections](https://qdrant.tech/documentation/concepts/collections/), [Points](https://qdrant.tech/documentation/concepts/points/).

**5 micro-exercises:**
1. **(5 min)** Run the example above. Change the query to `[0.1, 0.9]` — predict the winner first.
2. **(5 min)** Open `http://localhost:6333/dashboard`, find `chunks`. How many points does it have, and why?
3. **(10 min)** Add a third point `[0.7, 0.7]` with payload `{"text":"both"}` and query `[1,0]` with `limit=3`. Print ids with scores. Is the ordering what you expected?
4. **(10 min)** List collections over plain HTTP: `curl localhost:6333/collections`. Compare with `client.get_collections()` in Python — same data, two interfaces.
   *Hint:* the Python client is a wrapper around this REST API.
5. **(15 min)** Store a payload with `{"category":"contracts"}` on one point and `{"category":"company"}` on another; query with a filter so only contracts can match. Look up `models.Filter` in the Points docs.

**How it works internally:** each collection is split into **segments** (verified: `chunks`
already has 4, even when empty). Each segment holds vectors, payloads and an index. Writes go
to a write-ahead log first, so a crash doesn't lose acknowledged data; background optimisers
later merge small segments and build indexes.

**Checkpoint:** explain what a point is made of, and why keeping the payload beside the vector
matters for a RAG system specifically.

---

### 1.2 Embeddings and their dimension

**1. Definition:** an embedding is a fixed-length list of numbers a model produces for a piece
of text, arranged so texts with similar meaning get similar lists. The **dimension** is the
length of that list. `nomic-embed-text` always outputs 768 numbers. Analogy: describing every
song using the same 768 sliders (tempo, mood, loudness...). Two songs can only be compared if
both are described with the *same* sliders.

**2. The "Why" — vs. the traditional approach:** the traditional move is to read a number off
a tutorial or spec. This project's spec said "384 or 1536". Neither was true for the model we
actually run — measured, it's **768**. A dimension taken from documentation is a claim; a
dimension taken from one real embedding is a fact.

**3. Minimal code example** — the actual measurement:
```bash
curl -s localhost:11434/api/embed -d '{"model":"nomic-embed-text","input":"hello"}' \
  | python -c "import sys,json; print(len(json.load(sys.stdin)['embeddings'][0]))"
# 768
```

**4. Main benefits:** (a) the collection matches reality, not a doc; (b) one model now defines
both chunk boundaries (Phase 2) and retrieval similarity (Phase 3); (c) the check takes one
command.

**What breaks if dimension is wrong:** Qdrant rejects every vector at upsert time — verified
by `test_wrong_size_vector_is_rejected_at_upsert`. Nothing fails at setup; it fails later,
after embedding work.

**Canonical reading:** [Ollama embeddings API](https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings), [nomic-embed-text model card](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5).

**5 micro-exercises:**
1. **(5 min)** Run the curl above. Then embed a 2-word and a 200-word text — does the length change?
2. **(5 min)** Print the first 5 numbers of two embeddings for "dog" and "puppy". Can you see meaning in the raw numbers? (You shouldn't — it's distributed.)
3. **(10 min)** Compute cosine similarity for ("dog","puppy") vs ("dog","invoice") with numpy. Which is higher?
4. **(10 min)** `ollama pull all-minilm` and measure its dimension. Now you know the source of the spec's "384".
5. **(15 min)** Embed one real chunk from `data/chunks/company__about.md.json` and the question "When was Insurellm founded?" — compute their similarity. Then try a question about something unrelated.

**How it works internally:** the model reads tokens, passes them through transformer layers,
and pools the final token representations into one vector of fixed width. That width is baked
into the model's architecture — which is why it never varies with input length.

**Checkpoint:** explain why a 3-word query and a 3,000-word chunk produce vectors of the same
length, and why that is what makes comparing them possible.

---

### 1.3 Distance metrics — what "similar" means

**1. Definition:** the distance metric is the formula a collection uses to score closeness.
**Cosine** measures the *angle* between vectors (ignores length). **Dot product** multiplies
them (length matters). **Euclidean** is straight-line distance. Analogy: two arrows on a
compass. Cosine asks "are they pointing the same way?" Dot product also asks "and how long is
each arrow?"

**2. The "Why" — vs. the traditional approach:** the naive assumption is "they're all basically
the same." They aren't, and here is a verified counter-example with unnormalised vectors:

**3. Minimal code example** (real output):
```python
import numpy as np
q = np.array([1.0, 0.0])
a = np.array([1.0, 0.1])   # nearly the same direction, short
b = np.array([5.0, 5.0])   # 45 degrees away, but long
cos = lambda x, y: x @ y / np.linalg.norm(x) / np.linalg.norm(y)
print(cos(q, a), cos(q, b))   # 0.995  0.707  -> cosine picks a
print(q @ a, q @ b)           # 1.0    5.0    -> dot product picks b
```
Same data, **different winner**. Dot product rewards length, not meaning. Also verified:
Qdrant with `Distance.COSINE` **normalises vectors when you insert them** — `[3, 4]` is stored
as `[0.6, 0.8]` — so it can then use fast dot product internally.

**4. Main benefits (of cosine here):** (a) matches how `nomic-embed-text` is trained; (b)
consistent with how the Phase 2 chunker already measured sentence similarity; (c) immune to
vectors that happen to be longer.

**What breaks with the wrong metric:** no errors, just quietly worse rankings — the most
dangerous kind of failure, because every search "works."

**Canonical reading:** [Qdrant — Distance metrics](https://qdrant.tech/documentation/concepts/search/#metrics); [Pinecone — Vector similarity explained](https://www.pinecone.io/learn/vector-similarity/).

**5 micro-exercises:**
1. **(5 min)** Run the example; confirm the two winners differ.
2. **(10 min)** Normalise `a` and `b` (divide by their length) and recompute dot product. Do cosine and dot now agree?
3. **(10 min)** Reproduce the normalisation in Qdrant: insert `[3,4]` into a cosine collection and `retrieve(..., with_vectors=True)`. Then do the same in a `Distance.DOT` collection — is it normalised there?
4. **(10 min)** Create three `:memory:` collections (COSINE, DOT, EUCLID), insert the same 3 points, run the same query, and compare rankings.
5. **(15 min)** Check whether real `nomic-embed-text` embeddings are already unit length (`np.linalg.norm(vec)`). What does the answer mean for choosing cosine vs dot for this model?

**How it works internally:** because Qdrant normalises on insert for cosine collections,
scoring at query time reduces to a dot product on unit vectors — cosine accuracy at dot-product
speed. That's why the collection's metric is set once, at creation: stored vectors have
already been transformed to suit it.

**Checkpoint:** explain why choosing the wrong metric produces no error message, and why that
makes it more dangerous than a crash.

---

### 1.4 Configuration that is immutable at creation

**1. Definition:** some settings can't be changed once a collection exists — vector size and
distance are among them. Analogy: pouring a concrete foundation. You can repaint the house
any time; you cannot widen the foundation without demolishing.

**2. The "Why" — vs. the traditional approach:** the traditional workflow is "create it now,
adjust later." The spec followed that: create the collection in M1, pick the model in M2. But
since size can't change, that order forces a guess — and a wrong guess means dropping the
collection and re-embedding everything. The fix is to reorder the decisions: **choose and
measure the model first**, then create.

**3. Minimal code example:**
```python
VECTOR_SIZE = 768          # measured from the model, decided BEFORE creation
DISTANCE = Distance.COSINE

client.create_collection(
    collection_name="chunks",
    vectors_config=VectorParams(size=VECTOR_SIZE, distance=DISTANCE),
)
# There is no update call that changes `size` afterwards.
```

**4. Main benefits:** (a) no wasted re-embedding; (b) the constants live in one module, with the
reason next to them; (c) it forces the model decision to be explicit and recorded (D-2026-09-16-13/14).

**What breaks if you guess:** a 384-dim collection accepts setup fine, then rejects every
768-dim vector at upsert — after the ~9-minute embedding run.

**Canonical reading:** [Qdrant — Update collection parameters](https://qdrant.tech/documentation/concepts/collections/#update-collection-parameters) (note which settings are and aren't listed as updatable).

**5 micro-exercises:**
1. **(5 min)** Read the "update collection parameters" docs and list what CAN be changed after creation.
2. **(10 min)** Try `client.update_collection("chunks", vectors_config=...)` with a new size on an in-memory copy. What happens?
3. **(10 min)** Write out the steps needed to switch this project from 768 to 384 dims. Count how many are expensive.
4. **(10 min)** Find another "immutable once created" decision in this project (hint: chunk ids, D-2026-09-15-01).
5. **(15 min)** Look at the spec for P3-M2 onward and hunt for any other step whose order assumes something can change later.

**How it works internally:** vectors are stored in fixed-width blocks and the index is built
around that width and metric. Changing either would invalidate every stored vector and the
entire index, which is why Qdrant makes you rebuild rather than pretend to migrate.

**Checkpoint:** explain the bug in "create collection in M1, pick model in M2" in one sentence.

---

### 1.5 Idempotent setup — create if missing, verify if present

**1. Definition:** idempotent means running it twice leaves things the same as running it
once. `ensure_collection()` creates the collection only if it doesn't exist; otherwise it
*checks* it. Analogy: a hotel check-in desk. The first visit gives you a room; asking again
doesn't give you a second room — it confirms the one you have.

**2. The "Why" — vs. the traditional approach:** a plain `create_collection` fails the second
time ("already exists"); a "delete then create" always succeeds but **wipes every stored
vector**. Neither is safe to put in a setup script that people re-run. Verified: running the
real script twice printed `created`, then `already present, config verified`, and
`test_rerun_preserves_existing_points` proves stored points survive.

**3. Minimal code example:**
```python
def ensure_collection(client, name, size, distance) -> bool:
    if not client.collection_exists(name):
        client.create_collection(name, vectors_config=VectorParams(size=size, distance=distance))
        return True             # created
    ...verify config...
    return False                # already there, and correct
```

**4. Main benefits:** (a) safe to run on every deploy or startup; (b) never destroys data; (c)
the return value tells the caller which case happened.

**What breaks with delete-then-create:** running setup after embedding silently empties the
index. Search keeps "working" — it just returns nothing.

**Canonical reading:** [Wikipedia — Idempotence](https://en.wikipedia.org/wiki/Idempotence); [Qdrant — collection_exists](https://qdrant.tech/documentation/concepts/collections/#check-collection-existence).

**5 micro-exercises:**
1. **(5 min)** Run `python -m src.vectorstore.setup` twice; read both messages.
2. **(10 min)** Upsert one point into the real `chunks` via curl, re-run setup, and confirm `points_count` is still 1. Delete the point afterwards.
3. **(10 min)** Write a non-idempotent version (always `create_collection`) and run it twice in `:memory:`. Read the error.
4. **(10 min)** Write a "delete then create" version, upsert a point, re-run, and watch the point vanish.
5. **(15 min)** Two processes run setup at the same instant. Is there a race between `collection_exists` and `create_collection`? What would happen, and does it matter here?

**How it works internally:** `collection_exists` is a cheap metadata lookup; the branch then
either issues a create request or a `get_collection` read. No data is touched on the verify
path — only configuration is read.

**Checkpoint:** explain why "delete then create" is technically idempotent in *shape* but
disastrous in *effect*.

---

### 1.6 Fail loud — a custom exception, and no auto-repair

**1. Definition:** when the existing collection doesn't match, the code raises
`CollectionConfigMismatch` with a message naming the actual and expected values, and refuses
to fix it. Analogy: a pharmacist who notices the prescription dose doesn't match the label and
*stops*, rather than quietly picking one.

**2. The "Why" — vs. the traditional approach:** the two traditional options are "ignore it"
(reuse the wrong collection, fail later at upsert) or "fix it automatically" (drop and
recreate — deleting everything). The first hides the problem; the second destroys data
without asking. Raising early, with a clear message, is the only option that is both safe and
honest. A *custom* exception type lets callers catch exactly this case.

**3. Minimal code example:**
```python
class CollectionConfigMismatch(RuntimeError): ...

if vectors.size != size or vectors.distance != distance:
    raise CollectionConfigMismatch(
        f"collection {name!r} has size={vectors.size}, distance={vectors.distance}; "
        f"expected size={size}, distance={distance}. Delete it manually to rebuild."
    )
```

**4. Main benefits:** (a) problems surface at setup, before expensive work; (b) the message says
what's wrong *and* what to do; (c) data deletion stays a human decision.

**What breaks with auto-repair:** one mistyped constant in a future edit would silently wipe the
entire index on the next deploy.

**Canonical reading:** [Python docs — user-defined exceptions](https://docs.python.org/3/tutorial/errors.html#user-defined-exceptions); the general "fail fast" principle, [Jim Shore — Fail Fast (IEEE Software)](https://martinfowler.com/ieeeSoftware/failFast.pdf).

**5 micro-exercises:**
1. **(5 min)** Run `test_wrong_size_raises_rather_than_reusing` and read its `match=`.
2. **(10 min)** In `:memory:`, create `chunks` with size 384 and call `ensure_collection`. Read the full message — does it tell you how to fix it?
3. **(10 min)** Catch the exception specifically (`except CollectionConfigMismatch`) and print a friendlier message. Why is a specific type better than catching `RuntimeError`?
4. **(10 min)** Change only the distance (DOT) and confirm a different part of the message fires.
5. **(15 min)** Argue for a dev-only `--recreate` flag. What guard would you add so it can never run against the real collection?

**How it works internally:** subclassing `RuntimeError` makes it a normal exception for anyone
catching broadly, while its own class lets `except CollectionConfigMismatch` target just this
case. The f-string embeds live values, so the message is always accurate for the specific
mismatch.

**Checkpoint:** explain why refusing to auto-repair is a *safety* decision, not laziness.

---

### 1.7 Named vs unnamed vectors — and why the check order matters

**1. Definition:** a collection can hold one unnamed vector per point (config is a
`VectorParams`), or several *named* vectors per point (config is a `dict` like
`{"dense": VectorParams(...)}`). This pipeline writes exactly one. Analogy: a form with one
"photo" box versus a form with "passport photo" and "profile photo" boxes. Different forms,
different handling.

**2. The "Why" — vs. the traditional approach:** the naive check reads `.size` straight away.
But the two shapes have different types. Verified: for a named-vector collection the config is
a **`dict`**, which has no `.size`. So the type check must come *before* reading size — it
narrows "could be either shape" down to "definitely `VectorParams`."

**3. Minimal code example:**
```python
vectors = client.get_collection(name).config.params.vectors
if not isinstance(vectors, VectorParams):          # type check first
    raise CollectionConfigMismatch("... uses named vectors ...")
if vectors.size != size or vectors.distance != distance:   # safe to read .size now
    raise CollectionConfigMismatch(...)
```

**4. Main benefits:** (a) a clear error instead of an obscure crash; (b) `mypy` understands
`isinstance` narrowing, so `.size` type-checks; (c) rejects collections built by something else.

**What breaks if you reorder:** run exercise 2 and see for yourself.

**Canonical reading:** [Qdrant — Named vectors](https://qdrant.tech/documentation/concepts/vectors/#named-vectors); [mypy — type narrowing](https://mypy.readthedocs.io/en/stable/type_narrowing.html).

**5 micro-exercises:**
1. **(5 min)** Create a named-vector collection in `:memory:` and print `type(...config.params.vectors)`.
2. **(10 min)** Temporarily swap the two `if` blocks in a scratch copy and call `ensure_collection` on that collection. What exception do you get, and is its message helpful?
3. **(10 min)** Remove the `isinstance` check and run `mypy src/`. What does mypy say about `.size`?
4. **(10 min)** Upsert a point with two named vectors and query using just one of them.
5. **(15 min)** When would named vectors be genuinely useful in RAG? (Hint: hybrid dense + sparse search.)

**How it works internally:** the client parses Qdrant's JSON config into either a `VectorParams`
model or a `dict[str, VectorParams]`, depending on the shape the server returns.
`isinstance` checks the runtime type; mypy uses the same check to narrow the declared union
type for the lines after it.

**Checkpoint:** describe the two possible shapes of the vectors config, and say what a caller
would see if the size check ran first. (Answer from exercise 2 — not from memory.)

---

### 1.8 In-memory Qdrant for tests

**1. Definition:** `QdrantClient(":memory:")` runs Qdrant's collection logic inside your
Python process, with no server. Analogy: a flight simulator — the real controls and
instruments, without leaving the ground.

**2. The "Why" — vs. the traditional approach:** testing against the Docker Qdrant means CI
needs Docker, tests share state across runs, and a stopped container fails the suite for
reasons unrelated to code. In-memory mode gives each test a fresh, empty Qdrant in
milliseconds (8 tests in ~1.6s), fully offline.

**3. Minimal code example:**
```python
@pytest.fixture
def client() -> QdrantClient:
    return QdrantClient(":memory:")        # fresh, isolated, no network

def test_creates_collection_when_missing(client):
    assert ensure_collection(client) is True
```

**4. Main benefits:** (a) offline and fast; (b) perfect isolation per test; (c) same client API
as production, so the code under test is unchanged.

**What breaks / the honest limit:** in-memory mode is a *local implementation*, not the server.
Server-only behaviour (persistence, HNSW indexing, clustering, some performance traits) isn't
exercised. That's why this step also verified against the real server manually.

**Canonical reading:** [qdrant-client — local mode](https://github.com/qdrant/qdrant-client#local-mode).

**5 micro-exercises:**
1. **(5 min)** Run `pytest tests/vectorstore -v --durations=3`.
2. **(10 min)** Swap the fixture to `QdrantClient(url="http://localhost:6333")` and run — why do tests now interfere with each other and the real `chunks`? **Revert.**
3. **(10 min)** Try `QdrantClient(path="./tmp_qdrant")` — local mode persisted to disk. Restart Python and check the collection is still there. Delete the folder afterwards.
4. **(10 min)** Write a test marked `@pytest.mark.qdrant` that talks to the real server, and skip it when the server is unreachable.
5. **(15 min)** Find one Qdrant feature the docs say local mode doesn't support. Would any current test be fooled by it?

**How it works internally:** local mode implements storage and brute-force search in pure
Python/numpy, behind the same client methods. Your code calls `client.create_collection(...)`
either way; only the backend differs.

**Checkpoint:** state one thing in-memory tests prove, and one thing they cannot prove.

---

### 1.9 Docker persistence, health and inspection

**1. Definition:** the `qdrant` service stores data in a **named volume** (`qdrant_data`)
mounted at `/qdrant/storage`, has a **healthcheck**, and exposes REST on 6333 and gRPC on
6334. Analogy: the container is a rented office you can move out of any time; the volume is a
storage unit that stays put when you move.

**2. The "Why" — vs. the traditional approach:** by default a container's files disappear with
the container. Without the volume, a `docker compose down` would erase the collection and every
vector. Verified: after `docker compose restart qdrant` the collection was still there, and it
physically lives at `/qdrant/storage/collections/chunks/config.json`.

**3. Minimal code example:**
```yaml
qdrant:
  image: qdrant/qdrant:v1.19.0
  ports: ["6333:6333", "6334:6334"]
  volumes: ["qdrant_data:/qdrant/storage"]
  healthcheck:
    test: ["CMD-SHELL", "bash -c 'exec 3<>/dev/tcp/localhost/6333' || exit 1"]
```
```bash
curl localhost:6333/healthz                  # "healthz check passed"
curl localhost:6333/collections/chunks       # full config + counts
MSYS_NO_PATHCONV=1 docker exec advancerag-qdrant-1 ls /qdrant/storage/collections
```
**Windows Git Bash gotcha, verified:** without `MSYS_NO_PATHCONV=1`, Git Bash rewrites
`/qdrant/...` into `C:/Program Files/Git/qdrant/...` and the command fails.

**4. Main benefits:** (a) data survives restarts and image upgrades; (b) other services can wait
for "healthy" instead of guessing; (c) REST makes everything inspectable with plain `curl`.

**What breaks:** remove the volume line and every `down` loses the index. `docker compose down -v`
does the same deliberately — that `-v` means "delete volumes."

**Canonical reading:** [Docker — volumes](https://docs.docker.com/engine/storage/volumes/); [Qdrant — installation with Docker](https://qdrant.tech/documentation/guides/installation/); [Qdrant — HTTP API reference](https://api.qdrant.tech/).

**5 micro-exercises:**
1. **(5 min)** `docker compose ps` — find qdrant's health status.
2. **(5 min)** `docker volume inspect advancerag_qdrant_data` — where does Docker keep it?
3. **(10 min)** `docker compose restart qdrant`, then rerun setup — confirm "already present."
4. **(10 min)** `docker compose down -v`, `up -d qdrant`, rerun setup — confirm "created." (Only do this while the collection is empty.)
5. **(15 min)** `docker stats advancerag-qdrant-1` while running the setup script in a loop — how much memory does an empty Qdrant use?

**How it works internally:** the named volume is a directory managed by Docker outside the
container's writable layer; the mount makes it appear at `/qdrant/storage`. The healthcheck
opens a raw TCP connection to 6333 via bash's `/dev/tcp` — no curl needed inside the image.

**Checkpoint:** explain the difference between `docker compose down` and `docker compose down -v`
for this service.

---

### 1.10 The HNSW index — why search stays fast

**1. Definition:** HNSW (Hierarchical Navigable Small World) is a graph index. Each vector links
to a few neighbours; searching hops through the graph toward the query instead of comparing
against everything. Analogy: finding a house by asking locals for directions — each hop gets
closer — rather than knocking on every door in the country.

**2. The "Why" — vs. the traditional approach:** exhaustive search is exact but its cost grows
with every vector added. HNSW trades a tiny chance of missing the true nearest neighbour for
huge speed. Verified defaults on `chunks`: `m=16` (links per node), `ef_construct=100`
(build-time search width), and `full_scan_threshold=10000` — **below about 10,000 vectors,
Qdrant just does exact brute force**, so with our 214 chunks, results are exact.

**3. Minimal code example:**
```bash
curl -s localhost:6333/collections/chunks | python -c \
  "import sys,json; print(json.load(sys.stdin)['result']['config']['hnsw_config'])"
# {'m': 16, 'ef_construct': 100, 'full_scan_threshold': 10000, ...}
```

**4. Main benefits:** (a) near-constant search time as data grows; (b) tunable accuracy/speed;
(c) at our scale, no accuracy trade-off at all (full scan).

**What breaks if tuned badly:** very low `m`/`ef` makes search fast but misses relevant chunks —
silent recall loss, again with no errors.

**Canonical reading:** [Malkov & Yashunin — the HNSW paper (2016)](https://arxiv.org/abs/1603.09320); [Qdrant — Indexing](https://qdrant.tech/documentation/concepts/indexing/); [Pinecone — HNSW explained](https://www.pinecone.io/learn/series/faiss/hnsw/).

**5 micro-exercises:**
1. **(5 min)** Run the curl above and note all four HNSW values.
2. **(10 min)** Read `indexed_vectors_count` on the empty collection (0). After M3, check it again — will it be 214 or 0? (Hint: `full_scan_threshold`.)
3. **(10 min)** In `:memory:`, time exhaustive search over 10k, 50k and 100k random 768-dim vectors with numpy. Watch cost grow linearly.
4. **(10 min)** Read the "small world" section of the HNSW paper's introduction and explain the six-degrees analogy in your own words.
5. **(15 min)** Look up `hnsw_ef` as a *search* parameter in Qdrant. How is it different from `ef_construct`?

**How it works internally:** HNSW builds layered graphs — sparse long-range links at the top,
dense local links at the bottom. A search starts at the top layer, greedily moves to the
closest neighbour, descends a layer, and repeats, reaching the right region in roughly
logarithmic steps.

**Checkpoint:** explain why our 214-chunk index currently returns *exact* results even though
HNSW is an approximate method.

---

## 2. Validating it's actually working

| What to check | Command | "Working" looks like |
|---|---|---|
| Server alive | `curl localhost:6333/healthz` | `healthz check passed` |
| Container healthy | `docker compose ps` | qdrant `(healthy)` |
| Collection config | `curl localhost:6333/collections/chunks` | `status: green`, `size: 768`, `distance: Cosine` |
| Setup is idempotent | run `python -m src.vectorstore.setup` twice | `created`, then `already present, config verified` |
| Persistence | `docker compose restart qdrant` then setup | still `already present` |
| On-disk storage | `MSYS_NO_PATHCONV=1 docker exec advancerag-qdrant-1 ls /qdrant/storage/collections` | `chunks` |
| Model dimension matches | Ollama `/api/embed` then `len()` | `768` |
| Offline tests | `pytest tests/vectorstore -q` | 8 passed, no Docker needed |

**Failure-mode drills** (predict, then run; use `:memory:` or an empty collection):
1. Create `chunks` with size 384, run setup → expect `CollectionConfigMismatch` naming both sizes.
2. Stop Qdrant (`docker compose stop qdrant`), run setup → which line fails, and is the error a connection error rather than a collection error? Start it again.
3. Upsert a 384-dim point into the 768 collection → rejected at upsert, not at setup.

---

## 3. Visualizing it

```
 Phase 2 output                    Phase 3 (this step builds the right-hand box, empty)
 data/chunks/*.json  (214)
       │ (M3, later)
       ▼
  Ollama :11434  nomic-embed-text ──► 768 numbers per chunk
                                              │
                                              ▼
 ┌─────────────────────── docker network ───────────────────────┐
 │  qdrant :6333 REST / :6334 gRPC      healthcheck: TCP 6333   │
 │   └── collection "chunks"                                    │
 │         vectors: size=768, distance=Cosine   (fixed forever) │
 │         hnsw: m=16, ef_construct=100, full_scan<10000        │
 │         segments: 4, points: 0                               │
 └───────────────────────────┬──────────────────────────────────┘
                             │ mounted at /qdrant/storage
                     named volume  qdrant_data  (survives restart)

 setup.py:  collection_exists?
              ├─ no  ─► create(768, Cosine) ─► return True
              └─ yes ─► get config
                          ├─ dict (named vectors) ─► raise Mismatch
                          ├─ size/distance differ ─► raise Mismatch
                          └─ matches ─────────────► return False
```

**Tools:** the Qdrant **dashboard** at `http://localhost:6333/dashboard` (browse collections,
points, run searches visually); `curl` + `python -m json.tool` for raw REST;
`docker compose ps` / `docker stats` for container health and memory.

**End-to-end "watch it work":**
1. `docker compose up -d qdrant ollama` → `docker compose ps` until qdrant is `healthy`.
2. Measure the model: `curl localhost:11434/api/embed ...` → `768`.
3. Open the dashboard in a browser; note `chunks` and its point count.
4. `python -m src.vectorstore.setup` → `already present, config verified`.
5. Upsert one test point matching the config:
   ```bash
   curl -X PUT localhost:6333/collections/chunks/points -H "Content-Type: application/json" \
     -d "{\"points\":[{\"id\":999,\"vector\":$(python -c 'print([0.1]*768)'),\"payload\":{\"text\":\"test\"}}]}"
   ```
   Refresh the dashboard — points is now 1.
6. `docker compose restart qdrant`, refresh — the point survived (volume).
7. Rerun setup — still `already present`, and the point is **still there** (idempotent, non-destructive).
8. Clean up: `curl -X POST localhost:6333/collections/chunks/points/delete -H "Content-Type: application/json" -d '{"points":[999]}'` → points back to 0.

If you can predict each result before running it — and say which concept explains it — you
understand this step, not just the command that ran it.
