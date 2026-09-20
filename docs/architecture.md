# Architecture

## Embedding model (locked P3-M2, 2026-09-16)

| Property | Value | How it was established |
|---|---|---|
| Model | `nomic-embed-text` (served by Ollama) | Chosen P3-M1; already used for semantic chunking |
| Pinned digest | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` | Read from Ollama `/api/tags` |
| Dimension | 768 | Measured by embedding a real string |
| Context length | 2048 model tokens | `ollama show nomic-embed-text` |
| Distance | Cosine | Model is trained for it; vectors measured at norm 1.0 |
| Query prefix | `search_query: ` | nomic model card |
| Document prefix | `search_document: ` | nomic model card |
| Hard chunk cap | 1500 tokens, counted as chars÷4 (6000 chars) | Set below the measured rejection point |
| Qdrant collection | `chunks` | P3-M1 |

**Single source of truth:** `Settings` in [`src/core/config.py`](../src/core/config.py).
Everything that depends on the model — the chunker's embedder default, the pipeline CLI,
and the Qdrant collection size — reads from it. Any value can be overridden via `.env`.

### Why this model

- **One model for both boundaries and retrieval.** The chunker already embeds sentences with
  `nomic-embed-text` to find topic shifts, so the same notion of "similar" defines both where
  chunks are cut and what a query retrieves.
- **Local, free, offline.** It runs in the Ollama container already defined in
  `docker-compose.yml`; no API key, no per-call cost.
- **Rejected:** `all-MiniLM-L6-v2` (384 dims — needs sentence-transformers and torch, and a
  different model from the chunker's); OpenAI `text-embedding-3-small` (1536 dims — API key,
  cost, network dependency, a third provider).

### Measured evidence behind the lock

**Context limit is a hard failure, not a quality issue.** Embedding every chunk from the
first pipeline run against the live model:

```
chunks rejected: 1 / 214     error: "the input length exceeds the context length"
rejected         13,599 chars / 2,788 tiktoken tokens
largest accepted 10,530 chars / 2,119 tiktoken tokens
```
The original pipeline never hit this because the chunker only embeds single sentences.
Fix: a hard cap in the chunker that re-splits any chunk over 6,000 characters at sentence
boundaries (words as a last resort), applied after merging so it is always the final word.

**Task prefixes were kept on the model card's recommendation, not on measurement.** A
2-query spot check:

```
                            no prefixes            with prefixes
When was Insurellm founded? relevant .600 irrel .431  relevant .650 irrel .526
                            gap .169                  gap .124
What does Carllm do?        gap .107                  gap .083
```
Prefixes raised every score but narrowed the relevant/irrelevant gap in both cases. Two
queries is not evidence either way. The prefix is **configurable** and must be decided by a
proper retrieval evaluation. Changing it later requires re-embedding the collection, because
the document prefix is baked into every stored vector.

### Invariants that must never drift

1. **Digest.** `src/embedding/lock.py` compares the served model's digest to the pin and
   refuses to run on a mismatch, in both `python -m src.ingestion.pipeline` and
   `python -m src.vectorstore.setup`. A re-pull that changes the weights stops the pipeline
   instead of silently mixing vector spaces.
2. **Dimension.** The Qdrant collection is created from `settings.embed_dim`, and
   `ensure_collection` refuses a collection with any other size.
3. **Prefixes.** Documents are stored with `as_document()`, queries embedded with
   `as_query()`. Mixing prefixed and unprefixed vectors in one collection is a silent error.

### Changing the model (the full procedure)

Every step is required; skipping any leaves old and new vectors in different spaces.

1. Pull the new model and measure its dimension and context length.
2. Update `embed_model`, `embed_model_digest`, `embed_dim`, `embed_context_tokens`,
   `embed_max_chunk_tokens` and prefixes in `src/core/config.py`.
3. Delete the Qdrant `chunks` collection.
4. Re-run the ingestion pipeline (chunk boundaries depend on the model too).
5. Recreate the collection and re-embed everything.

## Embedding pipeline (P3-M3, 2026-09-17)

`src/embedding/embedder.py` is the only path from chunk text to stored vectors, and enforces
the locks above rather than trusting callers:

| Guarantee | Where |
|---|---|
| Served model digest == pin | `Embedder.__init__` (calls `verify_model`) |
| `search_document: ` on every chunk | `Embedder._embed_one_batch` |
| Over-length input refused, never truncated | `truncate=False` on every request |
| Count, 768 dims, finite, unit norm | `Embedder._validate`, per batch |
| Provenance on every vector | `Embedding.model`, `.model_digest`, `.document_prefix` |

Measured: 223 chunks in 199.9s at batch size 16 on CPU; batched vectors identical to single. Batch size does not change speed on this CPU once the model is warm (~1.16 chunks/s for sizes 1-32).
Run: `python -m src.embedding.embedder`.

## Vector store (P3-M4, 2026-09-17)

`src/vectorstore/store.py` — `QdrantVectorStore(client, query_embedder)`.

| Concern | How |
|---|---|
| Point ID | `uuid5(POINT_ID_NAMESPACE, chunk_id)` — never change the namespace |
| Payload | every `DocumentChunk` field + `model`, `model_digest`, `document_prefix` |
| Re-ingest | per document: delete where `doc_id` matches, then upsert (`wait=True`); partial documents refused |
| Provenance | embeddings with a different digest or prefix refused before any write |
| Indexes | keyword on `doc_id`, `category` (created by `ensure_collection`) |
| Query | `Embedder.embed_query` (`search_query: ` prefix) -> `query_points(limit=k)` |
| Health | readiness: reachable AND collection exists with 768 / cosine |
| Client | `build_qdrant_client` pins `Accept-Encoding: gzip` (see D-2026-09-17-23) |

Live (223 points): index 187s embed + 4.8s upsert; `query_points` ~18 ms; no HNSW index is built at
this size (669 KB < 10,000 KB thresholds), so search is exact — verified equal to numpy brute force.
Run: `python -m src.vectorstore.store {index,search "question" -k 5,health}`.

## Ingestion job (P3-M5, 2026-09-17)

`python -m src.vectorstore.ingest` (alias: `python -m src.vectorstore.store index`) runs
`run_ingestion`: **preflight -> load -> embed -> upsert -> orphans -> verify**, and always writes
`data/ingestion_log.json` (gitignored).

| Guarantee | How |
|---|---|
| Model is the pinned one | Embedder built in preflight (digest check) |
| No silent vector-space change | preflight counts stored points with a different digest/prefix; refuses unless `--rebuild` |
| Embedding failure leaves index untouched | all chunks embedded before any write |
| Deleted documents leave the index | orphans deleted, unless `report.json` shows chunking failures |
| Index equals disk | exact `chunk_id` set comparison and zero incompatible points |
| Failures are recorded | report written in `finally`, error re-raised, exit code 1 |

Live: 223 chunks, embed 197.7 s, upsert 4.8 s, all other stages < 0.2 s; refusal in 0.2 s.

## Graph database (P4-M1, 2026-09-19)

| Setting | Value | Why |
|---|---|---|
| Image | `neo4j:5.26.0` (Community, LTS) | pinned; verified with Python driver 6.2.0 |
| Ports | 7474 HTTP/Browser, 7687 Bolt | drivers use Bolt |
| Credentials | `NEO4J_USER` / `NEO4J_PASSWORD` in gitignored `.env`; compose requires the password | no well-known fallback |
| First start only | `NEO4J_AUTH` applies only when `neo4j_data` is created; later: `ALTER CURRENT USER SET PASSWORD` | the trap observed in P4-M1 |
| Plugins | APOC Core (bundled jar) | expected by graph tooling |
| Memory | heap 512m-1G, page cache 512m | shares 7.6 GB with Ollama and Qdrant |
| Healthcheck | `cypher-shell ... 'RETURN 1'` (credentials split from `NEO4J_AUTH`) | healthy = Bolt + auth work |
| Readiness in code | `python -m src.graph.setup` | connectivity, auth, major version 5, APOC |

Do not add custom `NEO4J_*` variables to the container: the image maps them to `neo4j.conf` settings
and strict validation refuses to start on unknown ones.

## Graph model (P4-M2, 2026-09-20)

The authoritative definition is `NODE_TYPES` and `RELATIONSHIP_TYPES` in
[src/graph/schema.py](../src/graph/schema.py); the tables describe it for humans and
`tests/graph/test_schema.py` asserts they agree. Apply and verify it with
`python -m src.graph.schema` (`--check` verifies without writing).

The graph holds **IDs, not text**. Chunk text stays in Qdrant, so a graph result and a vector result
meet on `chunk_id` (Qdrant's point id is `point_id(chunk_id)`).

<!-- graph-nodes:start -->

| Node label | Layer | Identity (unique) | Meaning |
|---|---|---|---|
| `Document` | provenance | `doc_id` | one source file; `doc_id` is the SHA-256 of its path (as in the chunk schema) |
| `Chunk` | provenance | `chunk_id` | one chunk, `{source_path}_{chunk_index}`; holds no text |
| `Company` | domain | `name` | Insurellm and its clients; short name from the contract title, legal name is a property |
| `Product` | domain | `name` | one of the 8 products, from the closed list in `data/products/` |
| `Contract` | domain | `doc_id` | one contract, identified by its document (contract numbers are missing from many) |
| `Employee` | domain | `name` | a person on the HR roster; never someone who only appears in a contract |
| `Contact` | domain | `name`, `organization` | a person named in a contract; identity needs the organisation because namesakes exist |
| `Location` | domain | `name` | a city as `City, State` with the full state name |

<!-- graph-nodes:end -->

<!-- graph-relationships:start -->

| Relationship | From → To | Properties | Meaning |
|---|---|---|---|
| `HAS_CHUNK` | Document → Chunk | | the document was split into this chunk |
| `MENTIONS` | Chunk → Company, Product, Contract, Employee, Contact, Location | | the chunk names this entity; the bridge from a graph hit to a `chunk_id` |
| `DESCRIBED_IN` | Product, Contract, Employee → Document | | the entity's own page (product page, contract, HR record) |
| `FOR_PRODUCT` | Contract → Product | | the product a contract licenses |
| `WITH_CLIENT` | Contract → Company | | the client; Insurellm is always the other party, so it is not stored |
| `SIGNED_BY` | Contract → Contact | `title` | a signatory, with the title as written in that contract |
| `WORKS_ON` | Employee → Product | | the employee's role description names this product |
| `BASED_IN` | Employee → Location | `remote` | where the employee works from |
| `HAS_OFFICE` | Company → Location | `headquarters` | an office; `headquarters` marks the head office |
| `FOUNDED` | Employee → Company | | co-founder, stated on the About page and in the HR record |

<!-- graph-relationships:end -->

What the database can and cannot enforce (Community Edition, observed on 5.26.0):

| Rule | Enforced by |
|---|---|
| One node per identity | uniqueness constraints (8, created by `apply_schema`); each has a backing range index, so `MERGE` on the key is an index lookup |
| A key property is present | **not** the database (existence constraints need Enterprise): the loader must `MERGE` with complete keys. `MERGE` raises on a null key; `CREATE` does not |
| Two people with the same name are different people | the `Employee` / `Contact` split, and `organization` inside the `Contact` key |
| Relationship endpoints have the right labels | **not** the database: `RELATIONSHIP_TYPES` is the reference, and the loader must use it |

Left out on purpose, with the reason: salary and date of birth (sensitive, and Community has no
role-based access); performance reviews and prior employers (single-document facts vector search
already reaches); pricing tiers as nodes (contracts name 9 different tiers, so `tier` is a
property); `WORKS_AT` and `PROVIDED_BY` (constant edges: every employee works at Insurellm, Insurellm
is on every contract); `REPORTS_TO` (one explicit statement in 32 HR records).
