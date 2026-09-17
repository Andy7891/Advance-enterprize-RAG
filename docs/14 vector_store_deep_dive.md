# 14 — Vector Store Client: Deep Dive (P3-M4)

> Every output block in this document was produced on 2026-09-17 by running the code against the
> live Qdrant **v1.19.0** server (collection `chunks`, 223 points), the in-memory Qdrant client, and
> Ollama **0.20.0** serving `nomic-embed-text`. All scripts are in
> [`practice codes/phase 3/m4_vector_store_deep_dive/`](../practice%20codes/phase%203/m4_vector_store_deep_dive/).
> Run any of them from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 3/m4_vector_store_deep_dive/c1a_point_anatomy.py"
> ```
>
> Scripts marked **(live)** need `docker compose up -d qdrant ollama` and an indexed collection
> (`python -m src.vectorstore.store index`). Scripts that create scratch collections delete them
> before exiting.

---

## 0. Plain-language summary

P3-M3 gave us 223 `Embedding` objects that only existed while a Python process was running.
P3-M4 puts them somewhere **permanent and searchable**. Qdrant stores them, and
`QdrantVectorStore` is the one class through which the rest of the system reads and writes:

```
WRITE PATH                                              READ PATH
list[Embedding]                                         "What does Carllm do?"
   │ upsert()                                              │ search() / search_with_scores()
   │  1 refuse wrong provenance          (C9)              │  1 embed_query: "search_query: ..." (C5)
   │  2 refuse incomplete documents      (C3)              │  2 query_points(limit=k)            (C5, C6)
   │  3 per document:                                      │  3 payload -> DocumentChunk         (C1)
   │     delete where doc_id == X        (C3, C4)          ▼
   │     upsert points, wait=True        (C2, C7)       top-k chunks (+ scores)
   ▼
Qdrant collection "chunks"  — each point = id (C2) + 768-d vector + payload (C1)
   ▲
health_check(): reachable AND collection is 768/cosine  (C8)
HTTP between client and server: gzip only               (C10)
```

And the most important lesson from the live system: **it runs correctly, and it still retrieves
the wrong thing for some questions** (C11).

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | A point: id + vector + payload; three ways to read points | 🔴 **Core RAG** |
| C2 | Point IDs and idempotent upsert (UUIDv5) | 🔴 **Core RAG** (every re-index depends on it) |
| C3 | Stale chunks and the delete-by-filter guard; filter logic | 🔴 **Core RAG** |
| C4 | Payload indexes and filtered search | 🔴 **Core RAG** (metadata filtering) |
| C5 | The query path: query embedding and `query_points` | 🔴 **Core RAG** (the R in RAG) |
| C6 | Exact vs approximate search (HNSW, recall) | 🔴 **Core RAG** (classic interview topic) |
| C7 | `wait=True`: write acknowledgement vs visibility | 🟡 Engineering |
| C8 | Health: liveness vs readiness | 🟡 Engineering (interview-heavy) |
| C9 | Provenance at the storage boundary | 🟡 Engineering / MLOps |
| C10 | HTTP content negotiation (the gzip pin) | ⚪ Boilerplate (but it broke a live run) |
| C11 | Retrieval failure analysis: why "Carllm" was missed | 🔴 **Core RAG** (motivates hybrid search) |

---

## C1 — A point: id + vector + payload

### 1. Definition + analogy

**What it is.** Qdrant stores everything as **points** inside a **collection**. A collection is a
named group of points that all share one vector configuration (for us: 768 numbers, cosine
distance, fixed at creation in P3-M1). A point has exactly three parts:

| Part | What it holds for us | What it's for |
|---|---|---|
| **id** | a UUID computed from `chunk_id` (C2) | identity: lets a write *replace* a point instead of adding another |
| **vector** | the 768 numbers from `nomic-embed-text` | similarity: what search compares against the question |
| **payload** | a JSON object: all 10 `DocumentChunk` fields + `model`, `model_digest`, `document_prefix` | content and metadata: what search *returns*, and what filters look at |

Key idea: **the vector finds the point, the payload is what you get.** The vector is useless to
a human or an LLM. The text the LLM will eventually read, and the source path cited to the user,
all come from the payload. That's why we store the full chunk rather than just an ID (decision
D-2026-09-17-14).

**Payload is schemaless JSON.** Qdrant doesn't check it against any schema. You can store strings,
integers, floats, booleans, lists and nested objects (our `metadata` dict). Types survive the trip:
an `int` comes back as an `int`. The *contract* for what's in it is enforced by our code
(`to_payload` / `chunk_from_payload`), not by Qdrant.

**Three different ways to read points.** It's easy to confuse them:
- **retrieve** by ID ("give me exactly these points"): no similarity, no ranking.
- **scroll** ("list points, page by page"), optionally filtered: no similarity, no ranking.
- **query_points** by vector ("give me the k most similar"): ranked by score.

Only the third one is *search*. The other two are for inspection, maintenance and debugging.

**Analogy.** A library book. The **id** is its unique catalogue number. The **vector** is its exact
position on a "subject map" where similar books sit close together. The **payload** is the book
itself plus the sticker inside the cover (author, shelf, date printed, which printing press).
You find it through the map, but you *read* the book.

**For a 14-year-old.** Each saved chunk is a card with a serial number, a secret list of 768
numbers that says what it's about, and the actual text written on the back. Searching uses the
numbers; reading uses the back of the card.

### 2. Why (vs the traditional approach)
In a keyword search engine or a SQL table, the text itself is what gets indexed and searched. In
a vector database, the searchable thing (the vector) and the readable thing (the payload) are
separate. If you forget to put the text in the payload, search "works" but returns nothing you
can show.

### 3. Minimal code

#### Example 1A — take one real stored point apart (live)
`c1a_point_anatomy.py`
```python
# Anatomy of a point: fetch ONE real stored point and look at its three parts.
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import chunk_from_payload, point_id

client = build_qdrant_client("http://localhost:6333")
wanted = point_id("company/about.md_0")          # we can compute the ID without asking Qdrant

# retrieve = "give me these IDs" (no similarity involved)
(point,) = client.retrieve("chunks", ids=[wanted], with_payload=True, with_vectors=True)

print("PART 1  id      :", point.id, "| type:", type(point.id).__name__)
print("PART 2  vector  :", len(point.vector), "numbers, first 4 =", [round(x, 4) for x in point.vector[:4]])
print("        norm    :", round(sum(x * x for x in point.vector) ** 0.5, 4))
print("PART 3  payload : keys =", list(point.payload))
for key in ("chunk_id", "doc_id", "category", "chunk_index", "char_count", "model", "model_digest", "document_prefix"):
    value = point.payload[key]
    print(f"        {key:<16} = {value!r:.60}   ({type(value).__name__})")
print("        metadata         =", point.payload["metadata"])
print("        text[:70]        =", repr(point.payload["text"][:70]))

chunk = chunk_from_payload(point.payload)
print("rebuilt DocumentChunk:", type(chunk).__name__, chunk.chunk_id, "| provenance keys dropped:",
      not hasattr(chunk, "model_digest"))
```
Real output:
```
PART 1  id      : 0617944f-3f01-5b3d-825c-913ba229efa6 | type: str
PART 2  vector  : 768 numbers, first 4 = [-0.0103, 0.0254, -0.2221, 0.004]
        norm    : 1.0
PART 3  payload : keys = ['chunk_id', 'doc_id', 'source_path', 'category', 'title', 'chunk_index', 'text', 'char_count', 'approx_token_count', 'metadata', 'model', 'model_digest', 'document_prefix']
        chunk_id         = 'company/about.md_0'   (str)
        doc_id           = '9a3e2eda0cb7770f'   (str)
        category         = 'company'   (str)
        chunk_index      = 0   (int)
        char_count       = 1107   (int)
        model            = 'nomic-embed-text'   (str)
        model_digest     = '0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c4   (str)
        document_prefix  = 'search_document: '   (str)
        metadata         = {'start_char': 0, 'end_char': 1109, 'tokens_approx': 225}
        text[:70]        = '# About Insurellm\n\nInsurellm was founded by Avery Lancaster in 2015 as'
rebuilt DocumentChunk: DocumentChunk company/about.md_0 | provenance keys dropped: True
```
**How to read it.**
- **We never asked Qdrant for the ID.** `point_id("company/about.md_0")` computed it locally, and
  `retrieve` found the point. Deterministic IDs make points *addressable* from the data alone (C2).
- **The vector** is exactly what P3-M3 produced: first numbers `-0.0103, 0.0254, -0.2221` (the same
  values doc 13 printed from the embedder) and norm `1.0`. Storage didn't alter it.
- **The payload** has 13 keys: the 10 chunk fields plus 3 provenance fields. `chunk_index` and
  `char_count` came back as `int`, and `metadata` came back as a nested dict. JSON types survived.
- `metadata.end_char` is 1109 while `char_count` is 1107, because offsets point into the *source*
  file, which has extra whitespace (the P2-M6 "word-level, not byte-level" rule).
- `chunk_from_payload` rebuilds a `DocumentChunk` using only the 10 chunk fields. Provenance
  belongs to the *point*, not the *chunk*, so it's dropped.

#### Example 1B — retrieve vs scroll vs query_points (in-memory)
`c1b_retrieve_vs_query.py`
```python
# Three ways to read points: by ID (retrieve), by listing (scroll), by similarity (query_points).
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

client = QdrantClient(":memory:")
client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
client.upsert("demo", wait=True, points=[
    PointStruct(id=1, vector=[1.0, 0.0], payload={"text": "east"}),
    PointStruct(id=2, vector=[0.0, 1.0], payload={"text": "north"}),
    PointStruct(id=3, vector=[0.7, 0.7], payload={"text": "north-east"}),
])

print("retrieve(ids=[2])      ->", [(p.id, p.payload["text"]) for p in client.retrieve("demo", ids=[2])])

points, next_page = client.scroll("demo", limit=2)          # paging through everything, no ranking
print("scroll(limit=2)        ->", [(p.id, p.payload["text"]) for p in points], "| next page starts at id", next_page)

hits = client.query_points("demo", query=[0.9, 0.1], limit=3).points   # ranked by cosine similarity
print("query_points([0.9,0.1])->", [(p.id, p.payload["text"], round(p.score, 3)) for p in hits])

# with_payload / with_vectors control what travels back over the network
bare = client.query_points("demo", query=[0.9, 0.1], limit=1, with_payload=False).points[0]
print("with_payload=False     ->", "id", bare.id, "| payload", bare.payload, "| vector", bare.vector)
```
Real output:
```
retrieve(ids=[2])      -> [(2, 'north')]
scroll(limit=2)        -> [(1, 'east'), (2, 'north')] | next page starts at id 3
query_points([0.9,0.1])-> [(1, 'east', 0.994), (3, 'north-east', 0.781), (2, 'north', 0.11)]
with_payload=False     -> id 1 | payload None | vector None
```
**How to read it.**
- These 2-D vectors are compass directions, so you can *see* cosine similarity: the query `[0.9, 0.1]`
  points almost due east.
- **retrieve** returns point 2 simply because we asked for ID 2. No score.
- **scroll** returns points in ID order, 2 per page, plus `next_page = 3` (a cursor to continue from).
  This is how the verification scripts read all 223 points.
- **query_points** ranks by angle: east 0.994, north-east 0.781, north 0.11. Only this call has a
  `score`.
- `with_payload=False` returns just IDs and scores. Payloads (our chunk text, ~1–6 KB each) are the
  bulk of a response, so leave them out when you only need IDs.

### 4. Benefits
The point model separates *finding* (vector) from *returning* (payload), so one call gives an LLM
ready-to-use text plus citation data.

- **What breaks:** a payload without text (search finds chunks you can't show); a payload with a
  different key name than `chunk_from_payload` expects (`KeyError` on every search).
- **Docs:** [Qdrant: Points](https://qdrant.tech/documentation/concepts/points/) ·
  [Qdrant: Payload](https://qdrant.tech/documentation/concepts/payload/) ·
  [Qdrant: Collections](https://qdrant.tech/documentation/concepts/collections/)

### Micro-exercises
1. In 1A, retrieve `products/Carllm.md_0` instead and print its `char_count` and first heading.
2. In 1B, query with `[0.0, -1.0]` (south). Predict the three scores' signs first. *Hint:* cosine
   can be negative.
3. Scroll the live `chunks` collection with `limit=100` and follow `next_page` until it's `None`.
   How many pages?
4. Retrieve with `with_payload=["chunk_id", "category"]` and compare the response size to the full
   payload. *Hint:* payload selectors.
5. Why does `chunk_from_payload` select fields explicitly instead of doing `DocumentChunk(**payload)`?
   *Hint:* try it.

**Internals.** Qdrant stores vectors in a vector storage segment and payloads in a separate payload
storage (ours is `on_disk_payload: true`, from the collection config). A query first finds point IDs
via the vectors, then loads only those points' payloads.
**Checkpoint:** name the three parts of a point and which one each of retrieve/scroll/query uses to
select points.

---

## C2 — Point IDs and idempotent upsert

### 1. Definition + analogy

**What it is.** **Upsert** = "update or insert": for each point sent, if a point with that **ID**
already exists, *replace* it entirely (vector and payload); otherwise *insert* it. Everything
therefore depends on how IDs are chosen.

**Idempotent** means doing an operation twice has the same effect as doing it once. Indexing must
be idempotent, because you *will* rerun it after a crash, a chunking change or a model change. With
upsert, idempotency holds only if **the same chunk always gets the same ID**.

**What Qdrant accepts as an ID:** an **unsigned integer** (0, 1, 2 …) or a **UUID** (a 128-bit
identifier written as `xxxxxxxx-xxxx-Mxxx-Nxxx-xxxxxxxxxxxx`). Our readable `chunk_id`
(`company/about.md_3`) is neither, so we convert it.

**UUID versions** differ in *how* the 128 bits are produced:
- **UUIDv4:** random. A new value every call, which is useless for identity.
- **UUIDv5:** computed from a **namespace UUID + a name string** using SHA-1 hashing. Same namespace
  and name give the same UUID, on any machine, forever. Different name, completely different UUID.

The recipe (RFC 9562 §5.5):
1. Compute SHA-1 over the namespace's 16 bytes followed by the name's bytes (20-byte digest).
2. Keep the first 16 bytes.
3. Overwrite 4 bits with the **version** (`5`) and 2 bits with the **variant** (`10`), so anyone can
   tell what kind of UUID it is.

**The namespace** is a fixed UUID we chose once (`POINT_ID_NAMESPACE`). It separates "chunk IDs of
this project" from any other use of uuid5 with the same names. Changing it changes *every* ID, and
the next index run would duplicate the whole collection. That's why the code says *never change
this value*.

**Analogy.** A person's national ID number derived from their birth certificate details: re-issue
the card and you get the same number, so records merge instead of duplicating. A random ticket
number (UUIDv4) would create a "new person" every time.

**For a 14-year-old.** We don't give each chunk a random name tag. We make the tag by scrambling
the chunk's name in a fixed way, so the same chunk always gets the same tag, and saving it again
replaces the old card instead of adding a second one.

### 2. Why (vs the traditional approach)
Many tutorials call `add_documents(...)` or upsert with random UUIDs. It looks fine on the first
run, and every rerun silently doubles the index. Search then returns the same chunk several times
in the top-k, crowding out other results.

### 3. Minimal code

#### Example 2A — build a UUIDv5 by hand (no server)
`c2a_uuid5_mechanics.py`
```python
# How a UUIDv5 is made from a name, step by step, and why it is deterministic.
import hashlib
import uuid

from src.vectorstore.store import POINT_ID_NAMESPACE, point_id

name = "company/about.md_3"

# Step 1: SHA-1 over (namespace bytes + name bytes)
digest = hashlib.sha1(POINT_ID_NAMESPACE.bytes + name.encode("utf-8")).digest()
print("1. sha1(namespace + name) =", digest.hex())

# Step 2: keep the first 16 bytes, then overwrite the version and variant bits
b = bytearray(digest[:16])
b[6] = (b[6] & 0x0F) | 0x50      # high nibble of byte 6 = 5  -> "version 5"
b[8] = (b[8] & 0x3F) | 0x80      # top two bits of byte 8 = 10 -> RFC variant
by_hand = uuid.UUID(bytes=bytes(b))
print("2. by hand                =", by_hand)
print("3. uuid.uuid5             =", uuid.uuid5(POINT_ID_NAMESPACE, name))
print("4. store.point_id         =", point_id(name))
print("   version field          =", by_hand.version, "(the '5' at the start of the third group)")

# Same name -> same ID; one character different -> unrelated ID; different namespace -> different ID
print("5. same name again        =", point_id(name) == point_id("company/about.md_3"))
print("6. '..._4' instead        =", point_id("company/about.md_4"))
print("7. other namespace        =", uuid.uuid5(uuid.NAMESPACE_URL, name))
print("8. uuid4 twice            =", uuid.uuid4(), uuid.uuid4(), "<- random every call")
```
Real output:
```
1. sha1(namespace + name) = 03d53aa6e559c8ba2f58deda2735962815a3ff5f
2. by hand                = 03d53aa6-e559-58ba-af58-deda27359628
3. uuid.uuid5             = 03d53aa6-e559-58ba-af58-deda27359628
4. store.point_id         = 03d53aa6-e559-58ba-af58-deda27359628
   version field          = 5 (the '5' at the start of the third group)
5. same name again        = True
6. '..._4' instead        = 0789479e-3938-584c-843f-0b88772c62a3
7. other namespace        = a00548c5-f9c6-5c8f-b301-00913e11cd9d
8. uuid4 twice            = ae399a72-2aac-4328-95e2-4c22848d328c 1e6ed871-2602-45b6-9c5d-6de054f23538 <- random every call
```
**How to read it.**
- **Line 1 vs line 2:** the hash starts `03d53aa6 e559 c8ba af58…`. In the UUID, the third group
  changed from `c8ba` to **`5**8ba` (version bits) and the fourth group's first hex digit is now in
  the `8`–`b` range (variant bits). Everything else is the hash, copied.
- **Lines 2–4 are identical:** our hand-made version, Python's `uuid5`, and the store's `point_id`
  agree exactly. There's no magic, just a hash with 6 bits overwritten.
- **Line 6:** changing `_3` to `_4` produces a completely unrelated UUID. Hashes don't keep "near"
  inputs near.
- **Line 7:** the same name under a different namespace gives a different ID. That's why the
  namespace is frozen.
- **Line 8:** uuid4 changes on every call, so it can't identify anything across runs.

#### Example 2B — idempotent vs duplicating upsert, and which IDs are legal (in-memory + live)
`c2b_idempotent_upsert.py`
```python
# Upsert = insert or replace BY ID. Same ID -> overwrite. New ID -> another point.
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from qdrant_client.http.exceptions import UnexpectedResponse

client = QdrantClient(":memory:")
client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
NS = uuid.UUID("f46fd59c-cfad-4523-9397-d36d7de5de4e")


def run(label, make_id, runs=3):
    client.delete_collection("demo")
    client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    for run_number in range(1, runs + 1):
        client.upsert("demo", wait=True, points=[
            PointStruct(id=make_id(chunk_id), vector=[1.0, 0.0], payload={"chunk_id": chunk_id, "run": run_number})
            for chunk_id in ("about.md_0", "about.md_1")
        ])
    points, _ = client.scroll("demo", limit=100)
    print(f"{label:<22} after {runs} runs of 2 chunks -> {len(points)} points; runs stored: {sorted(p.payload['run'] for p in points)}")


run("uuid5(chunk_id)", lambda cid: str(uuid.uuid5(NS, cid)))
run("uuid4() random", lambda cid: str(uuid.uuid4()))

# What Qdrant accepts as an ID -- asked of BOTH the in-memory client and the real server
from src.vectorstore.setup import build_qdrant_client

server = build_qdrant_client("http://localhost:6333")
if server.collection_exists("dd14_id_scratch"):
    server.delete_collection("dd14_id_scratch")
server.create_collection("dd14_id_scratch", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
for candidate in (7, str(uuid.uuid5(NS, "x")), "about.md_0", -1):
    verdicts = []
    for label, target, name in (("in-memory", client, "demo"), ("server", server, "dd14_id_scratch")):
        try:
            target.upsert(name, wait=True, points=[PointStruct(id=candidate, vector=[1.0, 0.0])])
            verdicts.append(f"{label}: accepted")
        except (ValueError, UnexpectedResponse) as e:
            verdicts.append(f"{label}: rejected ({type(e).__name__})")
    print(f"id {candidate!r:<40} -> " + " | ".join(verdicts))
server.delete_collection("dd14_id_scratch")
```
Real output:
```
uuid5(chunk_id)        after 3 runs of 2 chunks -> 2 points; runs stored: [3, 3]
uuid4() random         after 3 runs of 2 chunks -> 6 points; runs stored: [1, 1, 2, 2, 3, 3]
id 7                                        -> in-memory: accepted | server: accepted
id '4c14de6b-7626-5ddc-a36b-7d7dab0e2b53'   -> in-memory: accepted | server: accepted
id 'about.md_0'                             -> in-memory: rejected (ValueError) | server: rejected (UnexpectedResponse)
id -1                                       -> in-memory: accepted | server: rejected (UnexpectedResponse)
```
**How to read it.**
- **uuid5:** 3 runs still leave **2 points**, and both hold `run: 3`. Each run *replaced* the
  previous point's payload. That is idempotency.
- **uuid4:** 3 runs leave **6 points**: every run added copies. Search would return
  `about.md_0` three times.
- **Legal IDs:** an integer and a UUID string work everywhere; a plain string like `about.md_0` is
  rejected by both.
- **The last line is a real trap:** `-1` is *accepted by the in-memory client* but *rejected by the
  server* (HTTP 400; server IDs must be unsigned). The in-memory client is a very good test double,
  but not a perfect copy of the server. Anything that depends on server-only rules must be checked
  live. (C4 shows a second difference.)

### 4. Benefits
Reruns are safe, points are addressable by name, and the collection size equals the number of
chunks, not the number of runs.

- **What breaks:** random IDs duplicate the corpus on every rerun; changing `POINT_ID_NAMESPACE`
  duplicates it once. An interesting finding from P3-M4's mutation test: with random IDs, the
  delete-by-doc_id guard (C3) *hid* the duplication, so only the dedicated ID test caught it.
- **Docs:** [RFC 9562 §5.5 (UUIDv5)](https://www.rfc-editor.org/rfc/rfc9562#section-5.5) ·
  [Python `uuid`](https://docs.python.org/3/library/uuid.html) ·
  [Qdrant: point IDs](https://qdrant.tech/documentation/concepts/points/#point-ids)

### Micro-exercises
1. In 2A, change one letter of the name and count how many hex digits of the UUID stay the same.
   *Hint:* about 1 in 16 by chance.
2. Replace SHA-1 with SHA-256 in the hand-made version. Does it still equal `uuid.uuid5`? Why not?
3. In 2B, make runs 1–2 use uuid5 and run 3 use a different namespace. How many points now?
4. Why is `id=7` a poor choice for chunks even though it's legal? *Hint:* what is chunk 7 after
   re-chunking?
5. Explain the uuid4 row using the delete guard: if `QdrantVectorStore.upsert` were used instead of
   raw `client.upsert`, would the count still be 6?

**Internals.** Qdrant keeps an ID → internal offset map. An upsert for an existing ID writes a new
version of the point and marks the old one deleted, and a background optimizer later reclaims the
space (the `version` field seen in C5 increases with each write).
**Checkpoint:** explain why a deterministic ID alone is not enough when chunks are re-cut (then read
C3).

---

## C3 — Stale chunks and the delete-by-filter guard

### 1. Definition + analogy

**What it is.** Our IDs come from `chunk_id = "<source_path>_<chunk_index>"`, which is a
**position**, not content. That's fine while chunking stays the same. It breaks when a document is
**re-chunked** into a *different number* of pieces:

- Old run: 4 chunks → IDs for `_0, _1, _2, _3`.
- New run: 2 chunks → IDs for `_0, _1`.
- Upsert replaces `_0` and `_1` with new text, but **nothing ever touches `_2` and `_3`**. They stay
  in the collection with their *old* text, and they are **stale**: still searchable, still citable,
  no longer true to the source.

This is the danger `compute_chunk_id`'s docstring has warned about since P2-M1.

**The guard.** Before writing a document's new points, **delete every point that belongs to that
document**, then upsert. Qdrant can delete by **filter** (a condition on payload fields) instead of
by listing IDs, which matters because we don't know the old IDs. The filter is:

```python
Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
```

`doc_id` is a hash of the *path*, stable across re-chunking, so it's exactly the right grouping key.

**Filter logic in Qdrant** is boolean logic over payload fields:
- `must` = AND (every condition must hold)
- `should` = OR (at least one must hold)
- `must_not` = NOT (none may hold)

A condition is a key plus a test: `match` (equal to a value), `MatchAny` (one of several values),
`MatchExcept` (none of these), `range` (numeric comparisons), and more.

**The guard's own danger.** "Delete everything for this document, then write what I was given" is
only safe if *what I was given* is the **whole** document. Pass a partial list and the chunks left
out are deleted. So `upsert` refuses any document whose chunk indexes aren't exactly `0..N-1`
(`IncompleteDocument`). That catches *gaps* but can't catch a document missing its *last* chunks
(example 3C shows that honestly).

**Analogy.** Updating a chapter in a binder. Page numbers are positions. If the new chapter has 2
pages instead of 4, slotting in new pages 1–2 leaves old pages 3–4 behind. The fix: remove the
whole chapter first, then insert the new pages. But if someone hands you only page 2 of the new
chapter, removing the whole chapter first would lose pages 1 and 3.

**For a 14-year-old.** When a document is re-cut into fewer pieces, the leftover old pieces don't
disappear by themselves. So we throw away all of that document's old pieces before saving the new
ones, and we refuse to do that if the new pieces are obviously incomplete.

### 2. Why (vs the traditional approach)
Common alternatives: **ignore it** (the index slowly fills with stale text), **rebuild the whole
collection** each time (search is empty during the rebuild, and one bad document takes everything
down), or **content-hash IDs** (new text gets new IDs, but old IDs still linger unless deleted).
Deleting by a stable document key is the standard remedy.

### 3. Minimal code

#### Example 3A — stale points appear, then the guard removes them (in-memory)
`c3a_stale_chunks.py`
```python
# Why re-chunking leaves stale points, and how delete-by-doc_id removes them.
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, FieldCondition, Filter, FilterSelector, MatchValue,
                                  PointStruct, VectorParams)

NS = uuid.UUID("f46fd59c-cfad-4523-9397-d36d7de5de4e")


def points_for(texts, doc_id="doc-about"):
    # chunk_id is positional: "<source>_<index>", exactly like compute_chunk_id
    return [PointStruct(id=str(uuid.uuid5(NS, f"about.md_{i}")), vector=[1.0, float(i)],
                        payload={"doc_id": doc_id, "chunk_id": f"about.md_{i}", "text": t})
            for i, t in enumerate(texts)]


def show(client, label):
    points, _ = client.scroll("demo", limit=100, with_payload=True)
    rows = sorted((p.payload["chunk_id"], p.payload["text"]) for p in points)
    print(f"{label:<44} {len(rows)} points: {rows}")


old_run = ["Founded 2015", "Offices in SF", "Restructured 2021", "Products list"]   # 4 chunks
new_run = ["Founded 2015. Offices in SF", "Restructured 2021. Products list"]      # re-chunked to 2

for guard in (False, True):
    client = QdrantClient(":memory:")
    client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    client.upsert("demo", points=points_for(old_run), wait=True)
    print(f"--- guard {'ON ' if guard else 'OFF'} ---")
    show(client, "after first ingest")
    if guard:
        client.delete("demo", wait=True, points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value="doc-about"))])))
        show(client, "after delete where doc_id == 'doc-about'")
    client.upsert("demo", points=points_for(new_run), wait=True)
    show(client, "after re-ingest with 2 chunks")
```
Real output:
```
--- guard OFF ---
after first ingest                           4 points: [('about.md_0', 'Founded 2015'), ('about.md_1', 'Offices in SF'), ('about.md_2', 'Restructured 2021'), ('about.md_3', 'Products list')]
after re-ingest with 2 chunks                4 points: [('about.md_0', 'Founded 2015. Offices in SF'), ('about.md_1', 'Restructured 2021. Products list'), ('about.md_2', 'Restructured 2021'), ('about.md_3', 'Products list')]
--- guard ON  ---
after first ingest                           4 points: [('about.md_0', 'Founded 2015'), ('about.md_1', 'Offices in SF'), ('about.md_2', 'Restructured 2021'), ('about.md_3', 'Products list')]
after delete where doc_id == 'doc-about'     0 points: []
after re-ingest with 2 chunks                2 points: [('about.md_0', 'Founded 2015. Offices in SF'), ('about.md_1', 'Restructured 2021. Products list')]
```
**How to read it.**
- **Guard OFF:** after re-ingesting 2 chunks, there are still **4 points**. `_0` and `_1` got the
  new merged text, but `_2` ("Restructured 2021") and `_3` ("Products list") are stale leftovers.
  The same sentence now exists **twice** in the index, once current and once stale.
- **Guard ON:** the filtered delete removes all 4 points of `doc-about` without knowing their IDs;
  the re-ingest then leaves exactly the 2 current chunks.
- This is the same experiment P3-M4 ran live on a scratch collection (2 → re-upsert 1 → 1).

#### Example 3B — filter logic: must, should, must_not, match-any, range (in-memory)
`c3b_filter_anatomy.py`
```python
# A Qdrant Filter is boolean logic over payload fields: must (AND), should (OR), must_not (NOT).
from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, FieldCondition, Filter, MatchAny, MatchValue, PointStruct,
                                  Range, VectorParams)

client = QdrantClient(":memory:")
client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
rows = [
    (1, "company", "doc-about", 0), (2, "company", "doc-about", 1), (3, "products", "doc-carllm", 0),
    (4, "products", "doc-carllm", 1), (5, "contracts", "doc-techdrive", 0),
]
client.upsert("demo", wait=True, points=[
    PointStruct(id=i, vector=[1.0, 0.0], payload={"category": c, "doc_id": d, "chunk_index": k})
    for i, c, d, k in rows
])


def ids(f):
    points, _ = client.scroll("demo", scroll_filter=f, limit=100)
    return sorted(p.id for p in points)


eq = lambda key, value: FieldCondition(key=key, match=MatchValue(value=value))
print("must  doc_id == doc-about                  ->", ids(Filter(must=[eq("doc_id", "doc-about")])))
print("must  category == products AND index == 0  ->", ids(Filter(must=[eq("category", "products"), eq("chunk_index", 0)])))
print("should category company OR contracts       ->", ids(Filter(should=[eq("category", "company"), eq("category", "contracts")])))
print("match any of [company, contracts]          ->", ids(Filter(must=[FieldCondition(key="category", match=MatchAny(any=["company", "contracts"]))])))
print("must_not category == products              ->", ids(Filter(must_not=[eq("category", "products")])))
print("range chunk_index >= 1                     ->", ids(Filter(must=[FieldCondition(key="chunk_index", range=Range(gte=1))])))
print("typo'd key 'docid' (no such field)         ->", ids(Filter(must=[eq("docid", "doc-about")])), "<- matches nothing, no error")
```
Real output:
```
must  doc_id == doc-about                  -> [1, 2]
must  category == products AND index == 0  -> [3]
should category company OR contracts       -> [1, 2, 5]
match any of [company, contracts]          -> [1, 2, 5]
must_not category == products              -> [1, 2, 5]
range chunk_index >= 1                     -> [2, 4]
typo'd key 'docid' (no such field)         -> [] <- matches nothing, no error
```
**How to read it.**
- Five points in three documents. Each line is one filter and the IDs it selects.
- **`must` with two conditions** is AND: only point 3 is both a product and index 0.
- **`should`** (OR) and **`MatchAny`** give the same set here; `MatchAny` is the compact form for
  "field is one of these values".
- **`must_not`** is the complement: everything except products.
- **The last line is a quiet danger:** a misspelt key (`docid`) matches **nothing** and raises
  **no error**. In a *delete* guard that means "delete nothing", so stale chunks would silently stay.
  That's why the P3-M4 mutation test "wrong delete field" matters: a test re-chunks and checks the
  leftovers are gone.

#### Example 3C — the guard's own danger, and the partial-list check (in-memory)
`c3c_partial_upsert_danger.py`
```python
# The delete guard's own danger: a PARTIAL document list deletes the chunks you left out.
from qdrant_client import QdrantClient

from src.core.config import settings
from src.ingestion.schemas import DocumentChunk, Embedding
from src.vectorstore.setup import ensure_collection
from src.vectorstore.store import IncompleteDocument, QdrantVectorStore


def emb(i):
    text = f"about chunk {i}"
    chunk = DocumentChunk(chunk_id=f"company/about.md_{i}", doc_id="doc-about", source_path="company/about.md",
                          category="company", title="About", chunk_index=i, text=text, char_count=len(text),
                          approx_token_count=3, metadata={})
    vector = [0.0] * 768
    vector[i] = 1.0
    return Embedding(chunk, tuple(vector), settings.embed_model, settings.embed_model_digest,
                     settings.embed_document_prefix)


class NoQueries:
    def embed_query(self, text):
        raise AssertionError


client = QdrantClient(":memory:")
ensure_collection(client, name="demo")
store = QdrantVectorStore(client, NoQueries(), collection="demo")

full = [emb(i) for i in range(3)]
store.upsert(full)
print("stored document with chunks 0,1,2 ->", store.count(), "points")

# Someone "fixes" just chunk 1 and upserts only that one.
# Chunk index 1 alone is not 0..N-1, so it is refused BEFORE the delete runs.
try:
    store.upsert([full[1]])
except IncompleteDocument as e:
    print("upsert([chunk 1]) ->", type(e).__name__ + ":", str(e)[:95], "...")
print("points still stored ->", store.count())

# The known gap: chunks 0 and 1 without chunk 2 LOOK complete (0..N-1), so they are accepted.
store.upsert(full[:2])
print("upsert([chunk 0, chunk 1]) accepted -> points now", store.count(), "<- chunk 2 was deleted")
```
Real output:
```
stored document with chunks 0,1,2 -> 3 points
upsert([chunk 1]) -> IncompleteDocument: document company/about.md (doc-about) has chunk indexes [1]; upsert needs every chunk, because  ...
points still stored -> 3
upsert([chunk 0, chunk 1]) accepted -> points now 2 <- chunk 2 was deleted
```
**How to read it.**
- **Refused:** a list containing only chunk 1 has indexes `[1]`, not `[0]`, so it's rejected, and
  all 3 stored points survive.
- **Accepted, and data lost:** chunks 0 and 1 have indexes `[0, 1]`, which *is* a valid `0..N-1`
  sequence. The store can't tell "a 2-chunk document" from "a 3-chunk document missing its end", so
  it deletes the document and writes 2 points. Chunk 2 is gone.
- The honest conclusion (also in the P3-M4 notes): the guard makes the common mistake impossible, not
  every mistake. Always upsert *complete* documents, as `read_chunks()` → `embed_batch` does.

### 4. Benefits
Re-chunking can't leave ghosts, the delete doesn't need to know old IDs, and one document's
re-index never touches another's points.

- **What breaks:** no guard → stale duplicates; wrong filter key → silent no-op; partial lists →
  lost chunks.
- **Docs:** [Qdrant: Filtering](https://qdrant.tech/documentation/concepts/filtering/) ·
  [Qdrant: delete points](https://qdrant.tech/documentation/concepts/points/#delete-points)

### Micro-exercises
1. In 3A, re-ingest with **6** chunks instead of 2. Is the guard still needed? *Hint:* nothing is
   left over, but it's still harmless.
2. In 3B, write a filter for "products OR index ≥ 1, but not doc-carllm chunk 1".
3. In 3B, count with `client.count("demo", count_filter=...)` instead of scroll.
4. Extend `IncompleteDocument` (in a copy) with an `expected_counts: dict[doc_id, int]` argument
   and make 3C's last call fail.
5. Why is filtering on `doc_id` safer than on `source_path` for this guard? *Hint:* path
   normalisation in `compute_doc_id`.

**Internals.** A filtered delete first evaluates the filter against the payload (using a payload
index if one exists, C4), collects the matching point IDs, then marks those points deleted. It's
one server-side operation, not one call per point.
**Checkpoint:** explain why positional chunk IDs make the guard necessary, and what content-based IDs
would and wouldn't fix.

---

## C4 — Payload indexes and filtered search

### 1. Definition + analogy

**What it is.** Filters (C3) work on *any* payload field, with or without preparation. Without
preparation, Qdrant must check the field in **every point's payload**, so the cost grows with the
collection. A **payload index** is a lookup structure for one field, like the index at the back of
a book: for a `keyword` index, "value → list of point IDs". With it, a filter on that field jumps
straight to matching points.

Index types match data types: `keyword` (exact strings, like `doc_id`, `category`), `integer`,
`float`, `bool`, `datetime`, `text` (full-text tokens), `geo`, `uuid`. We index:
- **`doc_id`**, used by the delete guard on every document re-index;
- **`category`**, which we'll use for filtered search ("only products").

`ensure_collection` creates both every time it runs. Re-creating an existing index is a no-op, so an
older collection gains them automatically.

**Filtered search** combines a similarity query with a filter: "the k most similar points
*among those where category == products*". Qdrant applies the filter *during* the search, not after
it, so you still get k results if enough points match. Filtering *after* taking the top-k could
return fewer or none.

**An important testing fact:** the **in-memory client ignores payload indexes**. It accepts the call
and does nothing. Filters still work there, just unindexed. So index *existence* can only be
verified on a real server.

**Analogy.** A shop's stock room. Without a label index, finding all "products" boxes means opening
every box. With a label index, a sheet says "products: shelves 4, 9, 17". Filtered search is
"show me the 3 items closest to this photo, but only from the products shelves".

**For a 14-year-old.** An index is a cheat-sheet saying which cards have which label, so we don't
have to flip every card to find the "products" ones.

### 2. Why (vs the traditional approach)
Keeping metadata in a separate SQL database and joining after vector search is common but awkward:
two systems, two consistency problems, and post-filtering loses results. Storing filterable metadata
in the vector database's payload lets one query do both.

### 3. Minimal code

#### Example 4A — indexes on the live server, and filtered search with a real question (live)
`c4a_payload_index_live.py`
```python
# Payload indexes on the LIVE server vs the in-memory client, and a filtered search with real data.
import json
import urllib.request

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PayloadSchemaType

from src.vectorstore.setup import build_qdrant_client

URL = "http://localhost:6333"
info = json.load(urllib.request.urlopen(f"{URL}/collections/chunks"))["result"]
print("1. live payload_schema:")
for field, spec in info["payload_schema"].items():
    print(f"     {field:<9} type={spec['data_type']:<8} points indexed={spec['points']}")

local = QdrantClient(":memory:")
local.create_collection("demo", vectors_config={"size": 2, "distance": "Cosine"})
local.create_payload_index("demo", "doc_id", PayloadSchemaType.KEYWORD)
print("2. in-memory payload_schema after create_payload_index:", local.get_collection("demo").payload_schema,
      "<- local mode accepts the call and ignores it")

# Filtered counting and searching on the real collection
client = build_qdrant_client(URL)
for category in ("company", "contracts", "employees", "products"):
    f = Filter(must=[FieldCondition(key="category", match=MatchValue(value=category))])
    print(f"3. count where category == {category:<10} -> {client.count('chunks', count_filter=f, exact=True).count}")

# Filtered SEARCH with a real question: does restricting to one category change what comes back?
from src.embedding.embedder import Embedder, build_ollama_client

embedder = Embedder(build_ollama_client("http://localhost:11434"), ollama_url="http://localhost:11434")
question_vector = list(embedder.embed_query("What does the Carllm product do?"))
only_products = Filter(must=[FieldCondition(key="category", match=MatchValue(value="products"))])
for label, f in [("no filter", None), ("category == products", only_products)]:
    hits = client.query_points("chunks", query=question_vector, query_filter=f, limit=3).points
    print(f"4. 'What does the Carllm product do?', {label:<21}:",
          [(h.payload["chunk_id"], round(h.score, 3)) for h in hits])
```
Real output:
```
1. live payload_schema:
     doc_id    type=keyword  points indexed=223
     category  type=keyword  points indexed=223
2. in-memory payload_schema after create_payload_index: {} <- local mode accepts the call and ignores it
3. count where category == company    -> 7
3. count where category == contracts  -> 119
3. count where category == employees  -> 65
3. count where category == products   -> 32
4. 'What does the Carllm product do?', no filter            : [('products/Lifellm.md_2', 0.677), ('contracts/Contract with TechDrive Insurance for Carllm.md_1', 0.668), ('contracts/Contract with Belvedere Insurance for Markellm.md_2', 0.663)]
4. 'What does the Carllm product do?', category == products : [('products/Lifellm.md_2', 0.677), ('products/Lifellm.md_1', 0.661), ('products/Carllm.md_0', 0.627)]
```
**How to read it.**
- **Line 1:** the live server has both `keyword` indexes, each covering all 223 points.
- **Line 2:** the in-memory client reports `{}` after the same call. Tests pass either way, so the
  index is a *live-only* guarantee.
- **Line 3:** the corpus is lopsided. **119 of 223 chunks (53 %) are contracts**, and only 32 are
  product pages. Keep this in mind for C11.
- **Line 4, the most interesting:** without a filter, two of the top 3 are contracts, and the Carllm
  product page is nowhere. With `category == products`, the contracts are excluded **before ranking**,
  and `products/Carllm.md_0` rises to **#3** (0.627). The filter helped, but the *Lifellm* page still
  outranks the *Carllm* page for a question naming Carllm. A filter narrows the search space; it
  doesn't fix what the vectors consider similar.

### 4. Benefits
Filters stay fast as the corpus grows, the delete guard doesn't scan every payload, and metadata
filtering is available for later retrieval strategies (per-category search, access control).

- **What breaks:** an index on the wrong type (e.g. `integer` for a string field) doesn't speed up the
  filter; relying on in-memory tests to prove indexes exist.
- **Docs:** [Qdrant: Indexing, payload index](https://qdrant.tech/documentation/concepts/indexing/#payload-index) ·
  [Qdrant: Filtered search / filterable HNSW](https://qdrant.tech/articles/filtrable-hnsw/)

### Micro-exercises
1. Filter to `category == contracts` and ask "Who founded Insurellm?". What comes back, and why is it
   wrong by construction?
2. Add a `MatchAny(any=["products", "company"])` filter to the Carllm question.
3. Open http://localhost:6333/dashboard, pick `chunks`, and find the payload schema in the UI.
4. Create a scratch collection *without* indexes, and time 1,000 filtered counts vs `chunks`. Is there
   a measurable difference at 223 points? *Hint:* probably not, which is itself the lesson about scale.
5. Why does `category` use `keyword` rather than `text`? *Hint:* exact match vs tokenised words.

**Internals.** For filtered vector search, Qdrant's planner estimates how many points match the filter
(using the payload index's counts) and chooses a strategy: for very selective filters it scores just
the matching points directly; otherwise it searches the vector index while skipping non-matching
points.
**Checkpoint:** explain why filtering *during* search returns more useful results than filtering the
top-k afterwards.

---

## C5 — The query path: query embedding and `query_points`

### 1. Definition + analogy

**What it is.** `search_with_scores(question, k)` does three things:

1. **Question → vector.** `Embedder.embed_query(question)` sends `"search_query: " + question` to the
   same pinned model with `truncate=False`, and validates the result (768 numbers, finite, unit
   length). The store *never* adds a prefix itself. Doing it in two places is how a double or missing
   prefix creeps in.
2. **Vector → ranked points.** `client.query_points(collection, query=vector, limit=k,
   with_payload=True)` returns a `QueryResponse` whose `.points` is a list of **`ScoredPoint`**s,
   sorted best first. Each has `id`, `version` (how many writes the point has seen), `score` (cosine
   similarity for our collection), `payload`, and `vector` (None unless requested).
3. **Payload → `DocumentChunk`.** `chunk_from_payload` rebuilds the chunk; the score goes into
   `ScoredChunk`. `search()` (the spec's signature) just drops the scores.

**Why the prefix matters here specifically.** Documents were embedded as
`"search_document: …"`. nomic-embed-text was trained so that `search_query:` vectors land near the
`search_document:` vectors that *answer* them. Omit the query prefix, or use the document prefix on a
question, and the question is placed as if it were a document. It then matches passages that *look
like* it (contracts full of product names) rather than passages that *answer* it. Example 5B measures
this.

**`query_points` vs `search`.** Older qdrant-client versions had `client.search(...)`. In 1.19 it no
longer exists (checked in P3-M4); `query_points` is the unified Query API, which also handles hybrid,
multi-stage and recommendation queries later.

**Analogy.** Asking a librarian: (1) you phrase your question in the form they're trained to
understand, (2) they walk to the closest shelves and bring back the k best books with a confidence
rating, (3) you read the books, not the shelf coordinates.

**For a 14-year-old.** Turn the question into its own list of 768 numbers (with the "this is a
question" sticker), find the saved cards whose numbers point the same way, and read what's written on
them.

### 2. Why (vs the traditional approach)
Keyword search sends the question text itself to the index. Vector search needs a *model call* before
the database is even contacted, so query latency and correctness depend on the embedder as much as on
the database.

### 3. Minimal code

#### Example 5A — the query path one step at a time (live)
`c5a_query_path.py`
```python
# The full query path, one step at a time, against the live model and collection.
import time

from src.embedding.embedder import Embedder, build_ollama_client
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore, chunk_from_payload

question = "Who founded Insurellm?"
embedder = Embedder(build_ollama_client("http://localhost:11434"), ollama_url="http://localhost:11434")
client = build_qdrant_client("http://localhost:6333")

# Step 1: question -> vector (the embedder adds "search_query: " itself)
t = time.perf_counter()
vector = embedder.embed_query(question)
t_embed = time.perf_counter() - t
print(f"1. embed_query     -> {len(vector)} numbers in {t_embed * 1000:.0f} ms, first 3 {[round(x, 4) for x in vector[:3]]}")

# Step 2: vector -> ranked points (what search_with_scores sends)
t = time.perf_counter()
response = client.query_points("chunks", query=list(vector), limit=3, with_payload=True)
t_query = time.perf_counter() - t
print(f"2. query_points    -> {type(response).__name__} with {len(response.points)} {type(response.points[0]).__name__}s in {t_query * 1000:.0f} ms")
top = response.points[0]
print(f"   ScoredPoint fields: id={top.id[:8]}..., version={top.version}, score={top.score:.3f}, payload keys={len(top.payload)}, vector={top.vector}")

# Step 3: payload -> DocumentChunk (what the caller receives)
for rank, point in enumerate(response.points, start=1):
    chunk = chunk_from_payload(point.payload)
    print(f"3. rank {rank}: score {point.score:.3f}  {chunk.chunk_id:<32} {chunk.text[:55]!r}")

# Step 4: the same through the store in one call
store = QdrantVectorStore(client, embedder)
print("4. store.search    ->", [c.chunk_id for c in store.search(question, k=3)])
```
Real output:
```
1. embed_query     -> 768 numbers in 56 ms, first 3 [0.0127, -0.0054, -0.1937]
2. query_points    -> QueryResponse with 3 ScoredPoints in 22 ms
   ScoredPoint fields: id=0617944f..., version=228, score=0.664, payload keys=13, vector=None
3. rank 1: score 0.664  company/about.md_0               '# About Insurellm\n\nInsurellm was founded by Avery Lanca'
3. rank 2: score 0.603  employees/Emily Tran.md_0        '# HR Record\n\n# Emily Tran\n\n## Summary\n- **Date of Birth'
3. rank 3: score 0.594  employees/Avery Lancaster.md_0   '# Avery Lancaster\n\n## Summary\n- **Date of Birth**: Marc'
4. store.search    -> ['company/about.md_0', 'employees/Emily Tran.md_0', 'employees/Avery Lancaster.md_0']
```
**How to read it.**
- **Step 1 (56 ms)** is embedding one short question; **step 2 (22 ms)** is the database. Even at this
  tiny scale, the model call is the larger half of search latency.
- **`id=0617944f…`** is the same point C1 retrieved by computing `point_id("company/about.md_0")`.
  **`version=228`** is Qdrant's internal version number for the point, which grows as the collection
  receives writes; it has nothing to do with the chunk's content.
- **`vector=None`:** vectors aren't returned unless `with_vectors=True`, which saves ~768 × 8 bytes per
  result.
- **Ranks:** the About page (which names the founder) is first. Rank 3 is Avery Lancaster's HR record,
  also relevant. Rank 2 (Emily Tran's HR record) is a false positive: HR records share structure and
  vocabulary.
- **Step 4** gives the same IDs in one call. The store is exactly steps 1–3.

#### Example 5B — what a missing or wrong query prefix does (live)
`c5b_forgotten_query_prefix.py`
```python
# What happens if the query side forgets its prefix (or uses the document prefix)?
import ollama

from src.vectorstore.setup import build_qdrant_client

client = build_qdrant_client("http://localhost:6333")
ollama_client = ollama.Client(host="http://localhost:11434")
question = "Who founded Insurellm?"

for label, text in [("search_query: (correct)", f"search_query: {question}"),
                    ("no prefix", question),
                    ("search_document: (wrong side)", f"search_document: {question}")]:
    vector = ollama_client.embed(model="nomic-embed-text", input=[text], truncate=False)["embeddings"][0]
    hits = client.query_points("chunks", query=vector, limit=3).points
    print(f"{label:<30}", " | ".join(f"{h.payload['chunk_id']} {h.score:.3f}" for h in hits))
```
Real output:
```
search_query: (correct)        company/about.md_0 0.664 | employees/Emily Tran.md_0 0.603 | employees/Avery Lancaster.md_0 0.594
no prefix                      company/about.md_0 0.653 | contracts/Contract with Pinnacle Insurance Co. for Homellm.md_0 0.614 | contracts/Contract with BrightWay Solutions for Markellm.md_0 0.593
search_document: (wrong side)  contracts/Contract with Greenstone Insurance for Homellm.md_0 0.709 | contracts/Contract with Belvedere Insurance for Markellm.md_0 0.705 | contracts/Contract with Pinnacle Insurance Co. for Homellm.md_0 0.681
```
**How to read it.**
- **Correct prefix:** About page first, then two HR records, all people/company content.
- **No prefix:** the About page is still first, but ranks 2–3 become **contracts**, which say
  "Insurellm" many times but say nothing about who founded it.
- **Document prefix on a question:** the About page **drops out of the top 3 entirely**, and all three
  hits are contracts, with **higher** scores (0.709) than the correct setup's best (0.664). The
  question now looks like a document, so it matches documents that *look similar*. **A higher score is
  not a better answer.** This is exactly the silent failure `embed_query` prevents by owning the prefix.

### 4. Benefits
One call from question to chunks, the query side can't drift from the document side, and scores are
kept for thresholds and evaluation.

- **What breaks:** forgetting the prefix (5B); comparing against a collection embedded by another model
  (doc 12, C7); using `client.search` from older tutorials (`AttributeError` in 1.19).
- **Docs:** [Qdrant: Query API / search](https://qdrant.tech/documentation/concepts/search/) ·
  [nomic-embed-text task prefixes](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) ·
  [qdrant-client](https://github.com/qdrant/qdrant-client)

### Micro-exercises
1. In 5A, time step 1 ten times. Is the first call slower? *Hint:* model warm-up (doc 13 correction).
2. In 5B, add a fourth variant: `"clustering: " + question`. Where does it land?
3. Run 5B with "What does the Carllm product do?". Does the correct prefix still win clearly?
4. Call `query_points(..., score_threshold=0.65)`. How many results remain for each 5B variant?
5. Why does `search_with_scores` convert the tuple from `embed_query` with `list(vector)`? *Hint:* JSON
   serialisation of the request.

**Internals.** `query_points` sends `POST /collections/chunks/points/query` with the vector as JSON.
The server normalises it (cosine collection), scores candidates (a full scan here, C6), keeps a size-k
heap of the best, then loads payloads for just those k.
**Checkpoint:** explain why the wrong-side prefix produced *higher* scores yet *worse* results.

---

## C6 — Exact vs approximate search (HNSW)

### 1. Definition + analogy

**What it is.** To find the k nearest vectors *exactly*, you compare the query with **every** stored
vector: **brute force**, as in doc 13's C9. Cost grows linearly: 223 × 768 multiplications is nothing,
but 100 million × 768 per query is far too slow.

**Approximate nearest neighbour (ANN)** search trades a little accuracy for a lot of speed. Qdrant uses
**HNSW** (Hierarchical Navigable Small World):
- Each vector becomes a node in a **graph**, linked to a few of its near neighbours.
- There are several **layers**: sparse upper layers with long-range links for fast travel, and a dense
  bottom layer with every node.
- A search starts at the top, greedily hops toward the query, drops down a layer, and repeats,
  visiting only a small fraction of all points.

**The knobs:**
| Setting | When | Effect |
|---|---|---|
| `m` | graph build | links per node. More = better recall, more memory (ours: 16) |
| `ef_construct` | graph build | how hard the builder searches for good links (ours: 100) |
| `hnsw_ef` | each query | how many candidates a search explores. More = better recall, slower |
| `exact=True` | each query | skip the graph, brute-force this query |

**Recall** measures approximation quality: of the true top-k (from exact search), what fraction did
ANN return? Recall@10 = 0.98 means 98 % of the true top-10 were found.

**When does Qdrant actually build HNSW?** Two thresholds, both in **kilobytes of vector data**:
- `indexing_threshold` (optimizer, ours 10,000 KB): below this, segments aren't HNSW-indexed at all.
- `full_scan_threshold` (HNSW, ours 10,000 KB): below this, queries use a full scan even if an index
  exists.

Our 223 vectors take 223 × 768 × 4 bytes ≈ **669 KB**, far below both. So in the real `chunks`
collection **there is no graph, every search is exact, and `indexed_vectors_count` is 0**. P3-M4
verified Qdrant's top-5 equals numpy brute force. At roughly 3,300+ chunks, approximation begins.

**Analogy.** Finding the closest café. Exact: measure the distance to every café in the country.
HNSW: take the motorway to the right city (upper layer), then main roads to the right district, then
walk the streets (bottom layer). You almost always find the closest one, occasionally a very close
second.

**For a 14-year-old.** Checking every card is perfect but slow. HNSW is like asking friends who know
friends: you jump quickly toward the right crowd and only compare with the people there. You nearly
always find the best match.

### 2. Why (vs the traditional approach)
Tree indexes (like B-trees for SQL, or k-d trees) stop working well in hundreds of dimensions, the
"curse of dimensionality". Graph-based ANN like HNSW is the current standard in vector databases
because it keeps high recall at high dimension.

### 3. Minimal code

#### Example 6A — force an HNSW index on the real vectors and measure recall (live)
`c6a_exact_vs_hnsw.py`
```python
# Exact vs approximate search on the LIVE server.
# Copies the 223 stored points into a scratch collection whose thresholds force an HNSW index,
# then compares approximate results with exact ones. The scratch collection is deleted at the end.
import json
import time
import urllib.request

from qdrant_client.models import (Distance, HnswConfigDiff, OptimizersConfigDiff, PointStruct, SearchParams,
                                  VectorParams)

from src.vectorstore.setup import build_qdrant_client

URL = "http://localhost:6333"
client = build_qdrant_client(URL)
real = json.load(urllib.request.urlopen(f"{URL}/collections/chunks"))["result"]
print("1. real 'chunks': points", real["points_count"], "| indexed_vectors", real["indexed_vectors_count"],
      "| full_scan_threshold", real["config"]["hnsw_config"]["full_scan_threshold"], "KB",
      "| indexing_threshold", real["config"]["optimizer_config"]["indexing_threshold"], "KB")
print("   vector data size ~", round(real["points_count"] * 768 * 4 / 1024), "KB -> below both thresholds -> exact scan")

points, _ = client.scroll("chunks", limit=500, with_payload=["chunk_id"], with_vectors=True)
scratch = "dd14_hnsw_scratch"
if client.collection_exists(scratch):
    client.delete_collection(scratch)
client.create_collection(
    scratch,
    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
    # m=4 and ef_construct=8 build a deliberately SPARSE graph, so approximation errors are visible
    hnsw_config=HnswConfigDiff(m=4, ef_construct=8, full_scan_threshold=10),   # server minimum is 10 KB; still far below 669 KB
    optimizers_config=OptimizersConfigDiff(indexing_threshold=10),
)
client.upsert(scratch, wait=True, points=[PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points])

for _ in range(60):                                   # the optimizer builds HNSW in the background
    info = client.get_collection(scratch)
    if info.indexed_vectors_count and info.indexed_vectors_count >= len(points) and str(info.status) .endswith("green"):
        break
    time.sleep(1)
print("2. scratch: points", info.points_count, "| indexed_vectors", info.indexed_vectors_count, "| status", info.status)

total_hits = total_possible = 0
for probe in points[::20]:                            # 12 stored vectors used as queries
    exact = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(exact=True)).points
    approx = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(hnsw_ef=10)).points
    overlap = len({p.id for p in exact} & {p.id for p in approx})
    total_hits += overlap
    total_possible += 10
print(f"3. recall@10 of HNSW (m=4, ef=10) vs exact over 12 queries: {total_hits}/{total_possible} = {total_hits / total_possible:.2f}")

total_hits = 0
for probe in points[::20]:
    exact = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(exact=True)).points
    approx = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(hnsw_ef=200)).points
    total_hits += len({p.id for p in exact} & {p.id for p in approx})
print(f"4. same graph, hnsw_ef=200 (search wider)            : {total_hits}/{total_possible} = {total_hits / total_possible:.2f}")

client.delete_collection(scratch)
print("5. scratch collection deleted:", not client.collection_exists(scratch))
```
Real output:
```
1. real 'chunks': points 223 | indexed_vectors 0 | full_scan_threshold 10000 KB | indexing_threshold 10000 KB
   vector data size ~ 669 KB -> below both thresholds -> exact scan
2. scratch: points 223 | indexed_vectors 223 | status green
3. recall@10 of HNSW (m=4, ef=10) vs exact over 12 queries: 118/120 = 0.98
4. same graph, hnsw_ef=200 (search wider)            : 120/120 = 1.00
5. scratch collection deleted: True
```
**How to read it.**
- **Line 1:** the real collection has **0 indexed vectors**: no graph exists, so every production
  search is exact. The P3-M4 claim, now shown directly.
- **Creating the scratch collection:** the first attempt used `full_scan_threshold=1` and the
  **server rejected it** (`value 1 invalid, must be 10 or larger`). 10 KB is still far below 669 KB,
  so a graph gets built. (Another server rule the in-memory client doesn't have.)
- **Line 2:** the background optimizer built HNSW over all 223 vectors.
- **Line 3:** with a deliberately weak graph (`m=4`, `ef_construct=8`) and a narrow search
  (`hnsw_ef=10`), approximate search missed **2 of the 120** true top-10 results: recall 0.98.
- **Line 4:** *the same graph*, but exploring more candidates per query (`hnsw_ef=200`), finds all of
  them. `hnsw_ef` is the query-time dial between speed and recall.
- Why test with stored vectors as queries? They're real vectors from our model, so the neighbourhoods
  are realistic without spending time embedding new questions.

### 4. Benefits
Knowing the regime you're in: at our size results are exact, so retrieval problems are **model or
chunking problems, not index problems** (important for C11). At scale you have explicit knobs and a
way to measure what they cost.

- **What breaks:** tuning `m`/`ef` blindly without measuring recall; blaming HNSW for misses that
  exact search also makes.
- **Docs:** [Malkov & Yashunin, HNSW (arXiv:1603.09320)](https://arxiv.org/abs/1603.09320) ·
  [Qdrant: vector index](https://qdrant.tech/documentation/concepts/indexing/#vector-index) ·
  [Qdrant: optimizer / indexing_threshold](https://qdrant.tech/documentation/concepts/optimizer/) ·
  [ANN-Benchmarks](https://ann-benchmarks.com/)

### Micro-exercises
1. In 6A, set `hnsw_ef=4`. How low does recall go?
2. Build the scratch graph with `m=16, ef_construct=100` (the real defaults). What is recall at
   `hnsw_ef=10`?
3. Time 100 exact vs 100 approximate queries on the scratch collection. Is HNSW faster at 223 points?
   *Hint:* graph overhead can lose to a tiny scan.
4. Compute how many chunks would exceed `indexing_threshold` for 768-dim float32 vectors.
5. Explain why recall is measured against *exact search*, not against human relevance labels.

**Internals.** HNSW search keeps a candidate priority queue of size `ef`. It repeatedly expands the
closest unexplored node's neighbours and stops when no candidate can improve the current best `ef`.
Larger `ef` means more of the graph is explored before stopping.
**Checkpoint:** state what `m`, `ef_construct` and `hnsw_ef` each trade, and why our production
searches are exact.

---

## C7 — `wait=True`: write acknowledgement vs visibility

### 1. Definition + analogy

**What it is.** A Qdrant write goes through stages: the server **receives** it, records it in its
**write-ahead log (WAL)** so it survives a crash, then **applies** it to the segments where searches
look. The `wait` flag decides *when the call returns*:

- **`wait=False`**: returns as soon as the write is accepted into the WAL. Status
  **`acknowledged`**. The points may **not yet be visible** to count or search.
- **`wait=True`**: returns only after the write is applied. Status **`completed`**. The points are
  visible immediately.

This is a small example of a general distributed-systems idea: **acknowledged is not the same as
visible** (eventual vs read-after-write consistency). `wait=False` is faster for bulk loads when
nothing reads until later; `wait=True` makes "write, then immediately search" correct. That's our
index-then-verify workflow, and the per-document delete-then-upsert order depends on it.

**Analogy.** Posting a parcel. `wait=False` is the counter clerk saying "received"; the parcel isn't
on the shelf at the destination yet. `wait=True` is waiting for the "delivered" notification before
telling someone to go and pick it up.

**For a 14-year-old.** "Got it" isn't the same as "done". With `wait=True` we wait for "done" before
looking for what we saved.

### 2. Why (vs the traditional approach)
Most SQL databases return from `COMMIT` only once the data is readable, so developers expect that.
Many search and vector systems default to asynchronous indexing, so the expectation silently fails.

### 3. Minimal code

#### Example 7 — 20,000 points with and without waiting (live)
`c7_wait_flag.py`
```python
# wait=True vs wait=False on the LIVE server: what the call returns and when points become visible.
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.vectorstore.setup import build_qdrant_client

client = build_qdrant_client("http://localhost:6333")
name = "dd14_wait_scratch"
if client.collection_exists(name):
    client.delete_collection(name)
client.create_collection(name, vectors_config=VectorParams(size=4, distance=Distance.COSINE))

batch = lambda start: [PointStruct(id=i, vector=[1.0, 0.0, 0.0, float(i % 7)]) for i in range(start, start + 20000)]

result = client.upsert(name, points=batch(0), wait=False)
seen_immediately = client.count(name, exact=True).count
print(f"wait=False -> status={result.status.value:<12} count right after the call: {seen_immediately}")

result = client.upsert(name, points=batch(20000), wait=True)
seen_immediately = client.count(name, exact=True).count
print(f"wait=True  -> status={result.status.value:<12} count right after the call: {seen_immediately}")

client.delete_collection(name)
```
Real output:
```
wait=False -> status=acknowledged count right after the call: 9408
wait=True  -> status=completed    count right after the call: 40000
```
**How to read it.**
- **`wait=False`:** the call returned `acknowledged` while the server had applied only **9,408 of
  20,000** points. A count, or a search, at that moment sees less than half the data, with no error.
- **`wait=True`:** the call returned `completed`, and the count is exactly **40,000** (both batches).
- The batch has 20,000 points so the gap is big enough to catch. With our 3–5 chunks per document the
  window would be milliseconds, but it's the same race.
- `QdrantVectorStore.upsert` and its delete both use `wait=True`, so when `index` prints
  `collection now holds 223 points`, that number is real.

### 4. Benefits
Read-after-write correctness for indexing and verification, and a delete that has definitely finished
before the replacement points arrive.

- **What breaks:** `wait=False` + immediate search = silently missing results; `wait=False` on the
  delete could let it race the following upsert.
- **Docs:** [Qdrant: points, `wait` parameter](https://qdrant.tech/documentation/concepts/points/#upload-points) ·
  [Qdrant: storage and WAL](https://qdrant.tech/documentation/concepts/storage/) ·
  [Werner Vogels, "Eventually Consistent"](https://www.allthingsdistributed.com/2008/12/eventually_consistent.html)

### Micro-exercises
1. Poll `count` every 50 ms after the `wait=False` upsert until it reaches 20,000. How long did it take?
2. Repeat with 2,000 points. Do you still catch a partial count?
3. What would happen to `QdrantVectorStore.upsert` if only the *delete* used `wait=False`?
4. Measure the total time of 20,000 points with `wait=True` vs `wait=False` plus polling.
5. Name one situation in a real product where `wait=False` is the right choice.

**Internals.** The WAL is an append-only file. `wait=False` returns after appending; a background
update worker applies WAL entries to segments. `wait=True` blocks the HTTP response until that worker
confirms the operation's ID is applied.
**Checkpoint:** explain the difference between `acknowledged` and `completed` in one sentence each.

---

## C8 — Health: liveness vs readiness

### 1. Definition + analogy

**What it is.** "Is it healthy?" has two different meanings, and systems like Kubernetes probe them
separately:
- **Liveness:** *is the process alive?* If not, restart it. Qdrant's `/livez` (and `/healthz`) answer
  this.
- **Readiness:** *can it do its job right now?* If not, don't send it traffic yet. Qdrant's `/readyz`
  checks its own shards are loaded.

Neither server endpoint knows what *our application* needs. A perfectly alive and ready Qdrant with
**no `chunks` collection**, or a `chunks` collection of the **wrong size**, would fail every
upsert and search. So `QdrantVectorStore.health_check()` is an **application-level readiness check**:
1. Qdrant answers (`collection_exists` succeeds);
2. the collection exists;
3. its config is a single unnamed vector of 768 dimensions with cosine distance (reusing
   `check_collection_config` from setup, which never creates anything).

It **returns a bool and never raises**. A health check that throws forces every caller (a CLI, a load
balancer probe, a startup script) to wrap it. Failures are **logged** with the reason, so `False` is
still diagnosable. It catches only the expected failure types: `ApiException` (connection or HTTP
errors from qdrant-client) and `CollectionConfigMismatch`.

**Analogy.** A restaurant. Liveness: the lights are on and someone answers the door. Readiness: the
kitchen is staffed, the oven is hot, and the ingredients for *today's menu* are in stock. Only
readiness means you can serve dinner.

**For a 14-year-old.** "Is the shop open?" isn't the same as "does the shop have what I came to buy?"
Our check asks the second question.

### 2. Why (vs the traditional approach)
A common shortcut is "ping the server": it returns healthy while every real request fails, which is
the worst kind of green dashboard.

### 3. Minimal code

#### Example 8 — four situations, three kinds of answer (live + in-memory)
`c8_health.py`
```python
# Liveness vs readiness: four situations, what the server endpoints say, what health_check says.
import logging
import subprocess
import sys
import urllib.error
import urllib.request

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

logging.basicConfig(level=logging.WARNING, format="   log: %(message)s", stream=sys.stdout)


class NoQueries:
    def embed_query(self, text):
        raise AssertionError("never called")


def endpoint(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return f"{r.status} {r.read().decode()[:25]}"
    except (urllib.error.URLError, OSError) as e:
        return f"unreachable ({type(e).__name__})"


print("A. server endpoints on :6333 ->", "/livez:", endpoint("http://localhost:6333/livez"),
      "| /readyz:", endpoint("http://localhost:6333/readyz"))
print("   server endpoints on :6399 ->", "/livez:", endpoint("http://localhost:6399/livez"))

live = build_qdrant_client("http://localhost:6333")
cases = [
    ("real collection 'chunks'", QdrantVectorStore(live, NoQueries())),
    ("collection that does not exist", QdrantVectorStore(live, NoQueries(), collection="no_such_collection")),
    ("wrong size (384) collection", None),
    ("nothing listening on :6399", QdrantVectorStore(build_qdrant_client("http://localhost:6399"), NoQueries())),
]
for label, store in cases:
    if store is None:
        memory = QdrantClient(":memory:")
        memory.create_collection("chunks", vectors_config=VectorParams(size=384, distance=Distance.COSINE))
        store = QdrantVectorStore(memory, NoQueries())
    print(f"B. health_check, {label:<32} ->", store.health_check())

exit_code = subprocess.run([sys.executable, "-m", "src.vectorstore.store", "health"], capture_output=True, text=True)
print("C. CLI `store health` exit code:", exit_code.returncode, "| stdout:", exit_code.stdout.strip())
```
Real output:
```
A. server endpoints on :6333 -> /livez: 200 healthz check passed | /readyz: 200 all shards are ready
   server endpoints on :6399 -> /livez: unreachable (URLError)
B. health_check, real collection 'chunks'         -> True
   log: collection 'no_such_collection' does not exist
B. health_check, collection that does not exist   -> False
   log: Qdrant not ready: collection 'chunks' has size=384, distance=Cosine; expected size=768, distance=Cosine. Delete it manually to rebuild.
B. health_check, wrong size (384) collection      -> False
   log: Qdrant not ready: [WinError 10061] No connection could be made because the target machine actively refused it
B. health_check, nothing listening on :6399       -> False
C. CLI `store health` exit code: 0 | stdout: ready
```
**How to read it.**
- **A:** Qdrant's own probes say *alive* and *ready*. They'd say the same if `chunks` didn't exist.
- **B, four situations:** only the real, correctly configured collection gives `True`. Each `False`
  comes with a **log line naming the reason**: missing collection, size 384 vs 768, connection
  refused. None of them raised.
- **The "no_such_collection" case runs against the live, fully healthy server.** Server liveness and
  readiness are both fine, yet the application isn't ready. That's the whole point of D8-B.
- **C:** the CLI turns the bool into an **exit code** (0 = ready, 1 = not). Shell scripts, Docker
  `HEALTHCHECK` and CI can use exit codes without parsing text.
- The `NoQueries` embedder is never called: a health check shouldn't depend on Ollama being up.

### 4. Benefits
A single trustworthy "can we serve?" signal, safe to call anywhere, with a logged reason on failure.

- **What breaks:** a liveness-only check hides a missing collection; a check that raises crashes the
  caller it was meant to protect; a check needing Ollama reports Qdrant unhealthy when only Ollama is
  down.
- **Docs:** [Kubernetes: liveness, readiness, startup probes](https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/) ·
  [Qdrant: monitoring / health endpoints](https://qdrant.tech/documentation/guides/monitoring/)

### Micro-exercises
1. Stop Qdrant (`docker compose stop qdrant`), run `python -m src.vectorstore.store health`, check
   `echo $?`, then start it again.
2. Add a `docker-compose.yml` healthcheck for the API service using this CLI (on paper, or in a branch).
3. Extend the health check (in a copy) to also report `points_count` without changing True/False.
4. What should the health check return for a collection with 768 dims but **dot** distance?
   *Hint:* `check_collection_config`.
5. Why catch `ApiException` rather than `Exception`? *Hint:* doc 13, C5.

**Internals.** `collection_exists` is `GET /collections/chunks/exists`; the config check is
`GET /collections/chunks`. A refused connection surfaces as `ResponseHandlingException`, a subclass
of `ApiException`.
**Checkpoint:** give one situation where liveness is true but readiness is false for our store.

---

## C9 — Provenance at the storage boundary

### 1. Definition + analogy

**What it is.** *Provenance* is the record of where data came from. Every stored point carries
`model`, `model_digest` and `document_prefix` (doc 13, C2). P3-M4 uses that at two moments:

**(a) Prevention at write time.** `upsert` compares each embedding's digest and prefix with what this
store expects (from `settings`) and raises `ProvenanceMismatch` if any differs. This closes a gap left
by earlier guards:
- the P3-M2 startup check stops the **wrong model from embedding now**;
- but a list of `Embedding`s made **earlier**, by an old model or with prefixes switched off, could
  still be passed to `upsert`. This check stops those from entering the collection.

**(b) Detection at read time.** Because provenance is in the payload, it's filterable like any other
field. "How many points were NOT made by the pinned digest?" is a single `count` with a `must_not`
filter. After a model upgrade, that same query tells you exactly what still needs re-embedding.

**Analogy.** A pharmacy. (a) The receiving desk refuses a delivery whose batch number doesn't match
the approved supplier. (b) Every box on the shelf keeps its batch number, so if a batch is recalled,
you can find every affected box.

**For a 14-year-old.** Each saved card is stamped with which calculator made it. The shelf refuses
cards from the wrong calculator, and you can always count how many cards have the wrong stamp.

### 2. Why (vs the traditional approach)
Most pipelines keep model version information only in logs or config. After a partial re-index, the
vector store itself can't tell you which vectors are old. The only fix is re-embedding everything to
be safe.

### 3. Minimal code

#### Example 9 — refuse stale embeddings, then audit the live collection (in-memory + live)
`c9_provenance.py`
```python
# Provenance at the storage boundary: refuse stale embeddings, and query stored provenance.
from collections import Counter

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchExcept, MatchValue

from src.core.config import settings
from src.ingestion.schemas import DocumentChunk, Embedding
from src.vectorstore.setup import build_qdrant_client, ensure_collection
from src.vectorstore.store import ProvenanceMismatch, QdrantVectorStore


def emb(digest, prefix):
    chunk = DocumentChunk(chunk_id="company/about.md_0", doc_id="doc-about", source_path="company/about.md",
                          category="company", title="About", chunk_index=0, text="x", char_count=1,
                          approx_token_count=1, metadata={})
    return Embedding(chunk, tuple([1.0] + [0.0] * 767), settings.embed_model, digest, prefix)


class NoQueries:
    def embed_query(self, text):
        raise AssertionError


store = QdrantVectorStore(QdrantClient(":memory:"), NoQueries(), collection="demo")
ensure_collection(store._client, name="demo")
for label, e in [("current model and prefix", emb(settings.embed_model_digest, settings.embed_document_prefix)),
                 ("an OLD model digest", emb("9" * 64, settings.embed_document_prefix)),
                 ("prefix switched off", emb(settings.embed_model_digest, ""))]:
    try:
        store.upsert([e])
        print(f"1. upsert with {label:<26} -> stored (count {store.count()})")
    except ProvenanceMismatch as err:
        print(f"1. upsert with {label:<26} -> refused: {err}")

# On the live collection, provenance is queryable like any other payload field
live = build_qdrant_client("http://localhost:6333")
points, _ = live.scroll("chunks", limit=500, with_payload=["model_digest", "document_prefix"])
print("2. live provenance groups:", dict(Counter((p.payload["model_digest"][:12], p.payload["document_prefix"]) for p in points)))
stale = Filter(must_not=[FieldCondition(key="model_digest", match=MatchValue(value=settings.embed_model_digest))])
print("3. live points NOT made by the pinned digest:", live.count("chunks", count_filter=stale, exact=True).count)
other_prefix = Filter(must=[FieldCondition(key="document_prefix", match=MatchExcept(**{"except": [settings.embed_document_prefix]}))])
print("4. live points with any other document prefix:", live.count("chunks", count_filter=other_prefix, exact=True).count)
```
Real output:
```
1. upsert with current model and prefix   -> stored (count 1)
1. upsert with an OLD model digest        -> refused: company/about.md_0: made with digest 999999999999 and prefix 'search_document: '; this store expects 0a109f422b47 and 'search_document: '
1. upsert with prefix switched off        -> refused: company/about.md_0: made with digest 0a109f422b47 and prefix ''; this store expects 0a109f422b47 and 'search_document: '
2. live provenance groups: {('0a109f422b47', 'search_document: '): 223}
3. live points NOT made by the pinned digest: 0
4. live points with any other document prefix: 0
```
**How to read it.**
- **1:** matching provenance is stored. An old digest and an empty prefix are both refused, and each
  message states *both* what the embedding was made with and what the store expects, which is
  everything needed to understand the mismatch.
- **2:** all 223 live points form **one** provenance group. The collection is consistent.
- **3–4:** the audit as filters. `must_not` + `MatchValue` asks "digest is not the pinned one";
  `MatchExcept` asks "prefix is none of these". Both return **0**. (`except` is a Python keyword, hence
  the `**{"except": [...]}` form.)
- After a future model change, query 3 becomes your re-embedding to-do count.

### 4. Benefits
Stale vectors can't be introduced by accident, and a mixed collection is detectable with one query.

- **What breaks:** a deliberate model upgrade fails at `upsert` until `settings` is updated (intended,
  but surprising), and provenance can't catch writes made by other tools that bypass the store.
- **⚠️ A gap found while writing this document (verified on a scratch collection):** the check compares
  embeddings with `settings`, and the Embedder *also* reads `settings`. Run with
  `EMBED_DOCUMENT_PREFIX=""` and both sides agree on the new, wrong prefix, so `upsert` **accepts** it
  (stored `document_prefix = ''`). A config change therefore bypasses the guard, and `store index` would
  silently rebuild the collection in a different vector space. The fix is D-2026-09-17-20's rejected
  option C: compare against the provenance **already stored** in the collection (e.g. the query in
  example 9, line 3) before writing. Not implemented yet; recorded as an open item.
- **Docs:** [W3C PROV overview](https://www.w3.org/TR/prov-overview/) ·
  [Qdrant: filtering conditions (`match`, `except`)](https://qdrant.tech/documentation/concepts/filtering/#match-except)

### Micro-exercises
1. Write the query "points whose `model` is not `nomic-embed-text`".
2. Add an `embedded_at` timestamp to the payload (in a copy) and filter points older than a date with
   a `datetime` range.
3. Predict, then check in a **scratch** collection: if `EMBED_DOCUMENT_PREFIX` is changed in the
   environment, does `store index` refuse, or silently re-index everything with the new prefix?
   *Hint:* both the Embedder and the store read `settings`. **Don't run this against `chunks`.**
4. Why does the provenance check run over the **whole** input list before writing anything? Write down
   your answer (it's related to open Question 1, so don't look it up; reason from the code).
5. Design a "re-embed only stale points" job using query 3 plus `read_chunks()`.

**Internals.** `_check_provenance` is a plain Python loop comparing strings; it costs microseconds per
embedding. The live audit uses a server-side filtered `count(exact=True)`.
**Checkpoint:** explain what the startup digest check prevents that this check doesn't, and vice versa.

---

## C10 — HTTP content negotiation (the gzip pin)

### 1. Definition + analogy

**What it is.** qdrant-client talks to the server over **HTTP**. HTTP bodies can be **compressed** to
save bandwidth, negotiated in two headers:
- the client says what it can decode: `Accept-Encoding: gzip, deflate, zstd`;
- the server picks one and labels the response: `Content-Encoding: zstd`.

The client then **decodes** the body before the application sees it. **gzip** is old and universal;
**zstd** (Zstandard) is newer and usually compresses more and faster.

**How zstd got involved.** `httpx` (qdrant-client's HTTP library) automatically adds `zstd` to
`Accept-Encoding` **if the `zstandard` Python package happens to be installed**. In this project it's
installed only as a dependency of `langsmith`. So a package we never chose changed how every Qdrant
response was encoded.

**What went wrong.** During the first live `index` run, one delete response (about 100 bytes) failed to
decode: `zstd decompressor error: Allocation error : not enough memory`. It could not be reproduced in
200 repeated deletes, an idle-connection test, or a full rerun. **Root cause unknown.** The mitigation
(D-2026-09-17-23): `build_qdrant_client` sends `Accept-Encoding: gzip`, so the server never sends zstd
and the failing decoder is never used. It's a workaround that removes a code path, not a fix, and the
docs say so.

**Analogy.** Two people agreeing which zip format to use for email attachments. The recipient lists the
formats they can open, and the sender picks one. One day, opening a 7z attachment crashed; you couldn't
make it crash again, so you now ask for plain zip only.

**For a 14-year-old.** The computer and the database agree on a way to squash messages so they travel
faster. One squashing method broke once for no clear reason, so we switched to a different, very common
one.

### 2. Why (vs the traditional approach)
Most people never think about response encoding, which is exactly why a transitive dependency could
change it unnoticed.

### 3. Minimal code

#### Example 10 — what each Accept-Encoding costs on the wire (live)
`c10_compression.py`
```python
# HTTP content negotiation: the client lists encodings it can decode, the server picks one.
import httpx

URL = "http://localhost:6333/collections/chunks/points/scroll"
body = {"limit": 50, "with_payload": True, "with_vector": False}

print("httpx default Accept-Encoding here:", httpx.Client().headers["accept-encoding"])
for accept in ("identity", "gzip", "zstd", "gzip, deflate, zstd"):
    with httpx.Client() as client:
        with client.stream("POST", URL, json=body, headers={"Accept-Encoding": accept}) as response:
            wire = b"".join(response.iter_raw())            # bytes as sent over the network
        decoded = client.post(URL, json=body, headers={"Accept-Encoding": accept}).content   # after decoding
    print(f"Accept-Encoding: {accept:<20} -> server chose {response.headers.get('content-encoding', 'none'):<5}"
          f" | on the wire {len(wire):>6} bytes | decoded {len(decoded):>6} bytes")

from src.vectorstore.setup import build_qdrant_client

seen = []
qdrant = build_qdrant_client("http://localhost:6333")
qdrant._client.openapi_client.client._client.event_hooks["response"].append(
    lambda r: seen.append((r.request.headers["accept-encoding"], r.headers.get("content-encoding"))))
qdrant.count("chunks")
print("build_qdrant_client request/response encodings:", seen)
```
Real output:
```
httpx default Accept-Encoding here: gzip, deflate, zstd
Accept-Encoding: identity             -> server chose none  | on the wire 104069 bytes | decoded 104069 bytes
Accept-Encoding: gzip                 -> server chose gzip  | on the wire  44813 bytes | decoded 104069 bytes
Accept-Encoding: zstd                 -> server chose zstd  | on the wire  29723 bytes | decoded 104069 bytes
Accept-Encoding: gzip, deflate, zstd  -> server chose zstd  | on the wire  29723 bytes | decoded 104069 bytes
build_qdrant_client request/response encodings: [('gzip', 'gzip')]
```
**How to read it.**
- **Line 1:** in this environment httpx advertises `zstd` by default, because `zstandard` is installed.
- **Uncompressed**, 50 payloads are **104 KB**. gzip shrinks that to **45 KB**, zstd to **30 KB**.
  Decoded size is the same in every case: compression is invisible to the application when it works.
- **With the default header**, the server prefers **zstd**, the path that failed once.
- **The last line** hooks into `build_qdrant_client`'s HTTP client and shows the request asked for
  `gzip` and the response was `gzip`. The pin works.
- **Cost of the workaround:** ~50 % more bytes than zstd (45 vs 30 KB here). On localhost that's
  irrelevant; across a slow network it would be a real trade-off worth revisiting.

### 4. Benefits
A known, universally supported decoding path; a dependency accident can't change transport behaviour;
and the unknown is documented rather than hidden.

- **What breaks:** removing the pin brings back the unexplained failure mode; a remote Qdrant over a
  slow link pays more bandwidth.
- **Docs:** [MDN: Accept-Encoding](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Encoding) ·
  [RFC 8878 (Zstandard)](https://www.rfc-editor.org/rfc/rfc8878) ·
  [httpx: compression](https://www.python-httpx.org/compatibility/#compression)

### Micro-exercises
1. Run `pip show zstandard` and `pip show langsmith`. Confirm which package requires it.
2. Repeat Example 10 with `limit: 223`. Does the gzip/zstd ratio change?
3. Add `with_vector: true` to the body. How well do 768 floats compress compared with text?
4. Search the httpx and python-zstandard issue trackers for "Allocation error". Record what you find
   in `LEARNING_LOG.md`.
5. Write a stress script (1,000 queries after a 200 s idle period with an Ollama embedding run in the
   same process) to try to reproduce the failure without the pin.

**Internals.** httpx picks a decoder class per `Content-Encoding` value and streams the body through
it. zstd frames written by a streaming compressor may not declare their content size, so the decoder
must stream-decompress rather than one-shot decompress (the P3-M4 investigation showed a one-shot
`decompress` fails on exactly such a frame: "could not determine content size in frame header").
**Checkpoint:** explain why installing an unrelated package changed our Qdrant traffic.

---

## C11 — Retrieval failure analysis: why "Carllm" was missed

### 1. Definition + analogy

**What it is.** P3-M4 ended with a working store that gave a poor answer: for *"What does the Carllm
product do?"*, no `products/Carllm.md` chunk appeared in the top 10, although that page says "Carllm"
many times. **Failure analysis** means measuring *where* and *why* retrieval fails, instead of guessing
or tuning at random. The questions to ask, in order:

1. **Is it the index?** No. At our size search is **exact** (C6), so Qdrant returns the true nearest
   vectors. The problem is what the *vectors* consider near.
2. **Where did the right chunks actually rank?** Rank all 223 and find them.
3. **How separated are the scores?** If rank 1 and rank 50 score almost the same, the model isn't
   confidently distinguishing anything.
4. **Is there a signal the vectors ignore?** An exact name like "Carllm" is a strong *keyword* signal.
   Dense embeddings compress meaning into 768 numbers and treat "Carllm", "Lifellm" and "Markellm" as
   near-synonyms: all AI insurance products with similar descriptions.
5. **Is the right chunk diluted?** A long chunk covering many sub-topics gets one averaged vector.

**The concepts behind it.**
- **Dense retrieval** (embeddings) captures *semantic similarity* and handles paraphrase ("founded" ≈
  "started"), but is weak on **exact identifiers**: product names, codes, rare words.
- **Sparse / lexical retrieval** (keyword scoring such as **BM25**) matches the exact words the user
  typed, weighting rare words heavily. "Carllm" is rare, so BM25 would rank the Carllm page first.
- **Hybrid search** runs both and **fuses** the rankings (e.g. Reciprocal Rank Fusion). This is the
  standard remedy, and the direct motivation for the next phase.
- **Corpus imbalance:** 119 of 223 chunks are contracts that repeat product names. They crowd the
  results for any product question.

**Analogy.** A music-streaming app that recommends by "vibe". Ask for a specific song by name and it
plays three songs that *sound* similar. Great for "something like this", bad for "this exact thing".
Keyword search is the search box that matches the title.

**For a 14-year-old.** The meaning-search thinks all the "-llm" insurance products are basically the
same thing, so asking about Carllm gets you Lifellm. A word-search that just looks for "Carllm" would
have found it straight away. Good systems use both.

### 2. Why (vs the traditional approach)
Classic search engines were purely lexical and failed on paraphrase. Pure vector search fixes paraphrase
and breaks on names. Neither is enough alone, which is why production RAG systems increasingly combine
them.

### 3. Minimal code

#### Example 11 — measure the miss five ways (live)
`c11_failure_analysis.py`
```python
# Why did "What does the Carllm product do?" miss products/Carllm.md? Measure instead of guessing.
import re

from src.embedding.embedder import Embedder, build_ollama_client
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

question = "What does the Carllm product do?"
embedder = Embedder(build_ollama_client("http://localhost:11434"), ollama_url="http://localhost:11434")
client = build_qdrant_client("http://localhost:6333")
store = QdrantVectorStore(client, embedder)

# 1. Where do the Carllm product chunks rank among ALL 223?
ranked = store.search_with_scores(question, k=223)
positions = {r.chunk.chunk_id: (i + 1, r.score) for i, r in enumerate(ranked)}
for cid in ("products/Carllm.md_0", "products/Carllm.md_1", "products/Carllm.md_2"):
    rank, score = positions[cid]
    print(f"1. {cid:<22} rank {rank:>3}/223  score {score:.3f}  (top-1 score {ranked[0].score:.3f})")

# 2. How close together are the scores? A flat score curve means weak separation.
print("2. scores at ranks 1, 5, 10, 25, 50, 100:", [round(ranked[i - 1].score, 3) for i in (1, 5, 10, 25, 50, 100)])

# 3. How much of the corpus mentions "Carllm" at all? (a keyword signal the vectors don't use)
mentions = sorted(((len(re.findall(r"carllm", r.chunk.text, re.I)), r.chunk.chunk_id) for r in ranked), reverse=True)
print("3. chunks mentioning 'Carllm':", sum(1 for n, _ in mentions if n), "| top by count:", mentions[:4])

# 4. What is the Carllm page's first chunk actually about? (dilution check)
page = next(r.chunk for r in ranked if r.chunk.chunk_id == "products/Carllm.md_0")
print(f"4. products/Carllm.md_0 is {page.char_count} chars; headings: {re.findall(r'^#+ .+', page.text, re.M)[:6]}")

# 5. A crude keyword ranking (count of query words) vs the vector ranking
words = [w for w in re.findall(r"\w+", question.lower()) if len(w) > 3]
keyword_top = sorted(ranked, key=lambda r: -sum(r.chunk.text.lower().count(w) for w in words))[:3]
print("5. query words:", words, "| keyword top-3:", [r.chunk.chunk_id for r in keyword_top])
print("   vector top-3:", [r.chunk.chunk_id for r in ranked[:3]])
```
Real output:
```
1. products/Carllm.md_0   rank  22/223  score 0.627  (top-1 score 0.677)
1. products/Carllm.md_1   rank 219/223  score 0.488  (top-1 score 0.677)
1. products/Carllm.md_2   rank 133/223  score 0.557  (top-1 score 0.677)
2. scores at ranks 1, 5, 10, 25, 50, 100: [0.677, 0.661, 0.652, 0.625, 0.608, 0.577]
3. chunks mentioning 'Carllm': 18 | top by count: [(11, 'products/Carllm.md_0'), (4, 'products/Carllm.md_2'), (4, 'contracts/Contract with TechDrive Insurance for Carllm.md_1'), (4, 'contracts/Contract with Roadway Insurance Inc. for Carllm.md_3')]
4. products/Carllm.md_0 is 2409 chars; headings: ['# Product Summary', '# Carllm', '## Summary']
5. query words: ['what', 'does', 'carllm', 'product'] | keyword top-3: ['products/Carllm.md_0', 'company/careers.md_0', 'employees/Rachel Martinez.md_0']
   vector top-3: ['products/Lifellm.md_2', 'contracts/Contract with TechDrive Insurance for Carllm.md_1', 'contracts/Contract with Belvedere Insurance for Markellm.md_2']
```
**How to read it.**
- **1. Ranks:** the best Carllm page chunk is **#22** (score 0.627). With `k=5` or `k=10` it can never
  reach the LLM. Chunk `_1` is almost last (#219): it's a 313-char fragment with little meaning of its
  own.
- **2. A flat score curve:** rank 1 is 0.677 and rank 25 is 0.625, a gap of only **0.052** across 25
  places. Tiny differences decide the order, so small biases (shared vocabulary in contracts) win.
- **3. The keyword signal is strong:** 18 chunks mention "Carllm", and **`products/Carllm.md_0` mentions
  it 11 times**, more than any other chunk. The vectors ignore this.
- **4. Dilution:** that chunk is 2,409 characters under the headings "Product Summary / Carllm /
  Summary", a broad overview whose vector is an average of many features.
- **5. Even a crude keyword count** (just counting the words "what", "does", "carllm", "product") puts
  `products/Carllm.md_0` **first**. Its ranks 2–3 are junk (the words "what"/"does"/"product" are
  common), which is why real keyword scoring (BM25) down-weights common words. The two top-3 lists
  **fail in opposite ways**: vectors understand topic but not names; keywords find names but not
  meaning. That's the case for **hybrid search**.

### 4. Benefits
A measured diagnosis instead of a hunch, a clear next step (hybrid search / filtering / chunk sizing),
and a baseline to compare improvements against.

- **What breaks if skipped:** tuning HNSW (irrelevant at this size), swapping embedding models blindly,
  or raising k until the context is noise.
- **Docs:** [Robertson & Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond" (2009)](https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf) ·
  [Cormack et al., "Reciprocal Rank Fusion" (SIGIR 2009)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) ·
  [Qdrant: hybrid queries](https://qdrant.tech/documentation/concepts/hybrid-queries/) ·
  [Anthropic: Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) ·
  [Thakur et al., BEIR benchmark (arXiv:2104.08663)](https://arxiv.org/abs/2104.08663), where BM25 remains a strong baseline

### Micro-exercises
1. Rerun Example 11 for "Where are the offices?" and find where `company/about.md_0` ranks.
2. Replace the crude keyword count with `rank_bm25` (`pip install rank-bm25` in a scratch venv) and
   compare the top 3.
3. Implement Reciprocal Rank Fusion by hand: `score = Σ 1 / (60 + rank)` over the vector and keyword
   rankings. Where does Carllm land?
4. Split `products/Carllm.md_0` into its sections, embed them, and check whether one section now ranks
   near the top.
5. Write 10 questions with the chunk that should answer each, and measure hit@5 for vector search. Keep
   the script: it's your baseline for Phase 4.

**Internals.** A dense embedding model maps text into a space trained to put *related* passages
together. Product names that appear in similar contexts ("AI-powered insurance platform for …") get
similar representations, so the name itself contributes little to the vector's direction.
**Checkpoint:** explain why the Carllm miss is *not* an HNSW problem, and name two different remedies
with what each fixes.

---

## Validation

Run these from the project root; each line should match.

```bash
.venv/Scripts/python -m pytest tests/vectorstore tests/embedding -q -p no:warnings
# 49 passed
.venv/Scripts/python -m pytest tests -q -p no:warnings
# 149 passed
.venv/Scripts/python -m src.vectorstore.store health
# ready          (exit code 0)
curl -s http://localhost:6333/collections/chunks
# ... "points_count":223 ... "payload_schema":{"doc_id":{"data_type":"keyword",...},"category":{...}}
.venv/Scripts/python -m src.vectorstore.store search "Who founded Insurellm?" -k 3
# 1. 0.664  company/about.md_0 ...
```

Self-check questions (answer without looking):
1. What are the three parts of a point, and which one does search return to the LLM?
2. Why does `uuid5(chunk_id)` make re-indexing idempotent, and what does it *not* protect against?
3. What does `wait=False` return, and what can go wrong right after it?
4. Name two ways the in-memory client differs from the server (from this document).
5. Why is the Carllm miss not caused by HNSW?

## Visualization

**One document's re-index, step by step**
```
upsert([about_0, about_1, widget_0])      (all inputs valid)
  │
  ├─ input checks (provenance, completeness) — see open Question 1
  │
  ├─ doc "about":   DELETE where doc_id=about (wait) ──► UPSERT about_0, about_1 (wait)
  └─ doc "widget":  DELETE where doc_id=widget (wait) ─► UPSERT widget_0 (wait)
```
*(What happens when an input check fails, and in what order, is open Question 1. Draw that branch
yourself from `upsert` in `src/vectorstore/store.py`.)*

**What a search touches**
```
question ─► Ollama (56 ms) ─► 768 numbers ─► Qdrant full scan of 223 vectors (22 ms) ─► top-k ids
                                                                                    └─► payloads ─► DocumentChunks
```

**Tools to see it yourself**
- **Qdrant dashboard:** http://localhost:6333/dashboard. Browse points, filter by `category`, inspect
  payloads, and see the collection's config and payload schema.
- **Score curve plot:** run C11 and plot `[r.score for r in ranked]` with matplotlib. A flat curve is
  weak separation.
- **Rank table:** for 10 questions, print where the expected chunk ranks (vector vs keyword). This is
  the start of a retrieval evaluation.
- **2-D map:** scroll all vectors (`with_vectors=True`), project with
  `sklearn.decomposition.PCA(n_components=2)`, colour by `category`, and highlight the `*llm.md` product
  chunks to see how close the products sit.

## End-to-end "watch it work" exercise

Do these in order and write down what you observe at each step.

1. **Ready?** `python -m src.vectorstore.store health` → `ready`. Stop Qdrant, repeat (`NOT ready`, exit
   code 1), start it again.
2. **Look at a point.** Run `c1a_point_anatomy.py`. Find its ID in the dashboard.
3. **Prove idempotency.** Note `points_count`, run `python -m src.vectorstore.store index` (~3 min),
   confirm the count is still 223.
4. **See stale chunks.** Run `c3a_stale_chunks.py`. Then explain, in your own words, why guard OFF
   leaves 4 points.
5. **Break the prefix.** Run `c5b_forgotten_query_prefix.py`. Record which variant gave the highest
   score and whether it was the best answer.
6. **See approximation.** Run `c6a_exact_vs_hnsw.py`, then change `hnsw_ef` to 4 and rerun.
7. **See the race.** Run `c7_wait_flag.py` twice. Does the `wait=False` count change between runs?
8. **Diagnose a miss.** Run `c11_failure_analysis.py`, then do micro-exercise C11-3 (Reciprocal Rank
   Fusion by hand). Did Carllm move into the top 5?
9. **Log it.** Add a `LEARNING_LOG.md` line: the most surprising result, and the retrieval failure you
   most want Phase 4 to fix.

## Canonical sources
- Qdrant docs: [Points](https://qdrant.tech/documentation/concepts/points/) · [Payload](https://qdrant.tech/documentation/concepts/payload/) · [Filtering](https://qdrant.tech/documentation/concepts/filtering/) · [Indexing](https://qdrant.tech/documentation/concepts/indexing/) · [Search / Query API](https://qdrant.tech/documentation/concepts/search/) · [Optimizer](https://qdrant.tech/documentation/concepts/optimizer/) · [Storage](https://qdrant.tech/documentation/concepts/storage/) · [Hybrid queries](https://qdrant.tech/documentation/concepts/hybrid-queries/) · [Monitoring](https://qdrant.tech/documentation/guides/monitoring/)
- RFC 9562 (UUIDs, incl. v5): https://www.rfc-editor.org/rfc/rfc9562
- Malkov & Yashunin, HNSW: https://arxiv.org/abs/1603.09320 · ANN-Benchmarks: https://ann-benchmarks.com/
- Kubernetes probes: https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/
- Werner Vogels, "Eventually Consistent": https://www.allthingsdistributed.com/2008/12/eventually_consistent.html
- MDN Accept-Encoding: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Encoding · RFC 8878 (Zstandard): https://www.rfc-editor.org/rfc/rfc8878
- BM25: Robertson & Zaragoza (2009) · Reciprocal Rank Fusion: Cormack, Clarke & Büttcher (SIGIR 2009) · BEIR: https://arxiv.org/abs/2104.08663
- Anthropic, Contextual Retrieval: https://www.anthropic.com/news/contextual-retrieval
- nomic-embed-text model card: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
