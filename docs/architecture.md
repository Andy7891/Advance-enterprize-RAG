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

## Graph store (P4-M3, 2026-09-20)

Two modules: [src/graph/records.py](../src/graph/records.py) defines what may be written, and
[src/retrieval/graph_store.py](../src/retrieval/graph_store.py) writes and reads it.

| Record | Holds | Checked when built |
|---|---|---|
| `NodeRef(label, key)` | a node's identity, nothing else | label is in the model; `key` is exactly the model's key; values are clean non-empty strings |
| `GraphNode(label, key, properties)` | identity plus what describes it | the above, plus properties are storable, not `None`, and do not repeat the key |
| `GraphEdge(type, source, target, properties)` | a relationship between two `NodeRef`s | type is in the model; endpoints have the labels the model allows; properties are declared for that type |

| `GraphStore` method | Does | Notes |
|---|---|---|
| `upsert_node(node)` | `MERGE` on the full key, `SET n += props` | never deletes properties; safe to repeat |
| `upsert_edge(edge)` | `MATCH` both endpoints, `MERGE` the edge, `SET r += props` | raises `MissingEndpoint`, naming the absent node; nodes must be loaded first |
| `search_related(name, *, label, include_provenance, limit)` | every edge attached to the entity, either direction | exact `name`; all namesakes unless `label` narrows it; domain edges unless asked; ordered |
| `multi_hop_search(start, hops, *, label, limit)` | names within `hops` steps, nearest first | 1 to 3 hops; domain edges; never the start; walks through nameless nodes |

Constructing a `GraphStore` runs `check_schema` and refuses a graph whose constraints are not in force.

What reads deliberately do **not** do: return chunk text or `chunk_id`s (a graph hit is entities and
edges; reaching passages means following `MENTIONS`, a later step); find a node with no `name`
(`Contract`, `Document` and `Chunk` are reached through neighbours); match case-insensitively; walk
provenance edges, which would make two entities named in one chunk look 2 hops apart.

## Entity deduplication (P4-M4, 2026-09-21)

[src/graph/dedup.py](../src/graph/dedup.py) decides which extracted nodes are the same real-world entity.
It is pure: it takes `GraphNode`s and returns `GraphNode`s, and the caller writes them through `GraphStore`.

| Function | Returns | Notes |
|---|---|---|
| `deduplicate_entities(nodes, *, known_names=None)` | `List[GraphNode]` | one node per entity; every other spelling is under `aliases` |
| `resolve_entities(nodes, *, known_names=None)` | `Resolution(nodes, alias_map, ambiguous, conflicts)` | the same merge, plus what was refused and what disagreed |
| `remap_edges(edges, alias_map)` | `List[GraphEdge]` | points edges at canonical nodes; edges that become identical collapse into one |

The strategy, in the order it runs:

1. **Scope.** A node is compared only with nodes of its own label, and a `Contact` only with contacts of the
   same organisation. The corpus has two people called Robert Chen (an `Employee` and a client's officer), so a
   name match across labels is never evidence.
2. **Normalise** each name with its label's rules (table below). Names with the same words are one entity.
3. **Subset match.** A shorter form joins a longer one only if *exactly one* longer form contains all its words.
   If several do, it is *ambiguous*: it stays a separate node and is reported. The step repeats until nothing
   changes, so the result does not depend on input order and a second run changes nothing.
4. **Canonical name:** a name from the caller's `known_names` if one matches, otherwise the label's rule.
5. **Merge properties:** the canonical node's value wins, gaps are filled from the others, lists are united,
   and every disagreement is returned as a `Conflict`.
6. **Alias map:** each spelling that disappeared maps to the node that replaced it, for `remap_edges`.

| Label | Two names are equal when | A shorter name joins a longer one when | Canonical name |
|---|---|---|---|
| `Employee`, `Contact` | same words after lower-casing, deleting apostrophes and dropping leading honorifics; an initial is a word | exactly one longer name holds all its words (one word is enough) | the fullest spelling seen, honorific removed |
| `Company` | same words after dropping a leading "the" and trailing legal suffixes (Inc, LLC, Ltd, Corp, Co, ...) | as above, but the short name needs two or more words | the spelling with the fewest words, legal suffix removed; built, so a suffix or case variant never changes the key |
| `Location` | same words after a state abbreviation becomes the full name | never | `City, FullState` |
| `Product`, `Contract`, `Document`, `Chunk` | identical key | never | the key as given |

Deliberately **not** done: fuzzy similarity (Sarah Chen is one word from Alex Chen and Robert Chen); merging across
labels; reading "Chen, Alex" as "Alex Chen"; merging across batches ("exactly one" means exactly one among the nodes
passed in); query-time matching (the store still finds a node only by its exact `name`). Nothing here removes a
node from the graph: a spelling merged away by a later batch stays there until the ingestion job deletes it.

## Graph extraction (P4-M4b, 2026-09-21)

[src/graph/extractor.py](../src/graph/extractor.py) turns one chunk of text into graph records with rules. It reads chunks written by P2
(`data/chunks/*.json`, through `read_chunks`), touches no database and merges nothing: several chunks emit the same node, and
[P4-M4](../src/graph/dedup.py) unifies them.

| Call | Returns | Notes |
|---|---|---|
| `GraphExtractor.from_chunks(chunks)` | an extractor | reads the closed lists (products, HR roster, cities) from the corpus |
| `extract(chunk, *, lookahead="")` | `Extraction(nodes, edges, mentions, warnings)` | one chunk; every edge's endpoints are among its nodes |
| `extract_all(chunks)` | `list[Extraction]` | gives each chunk the first 400 characters of its successor in the same document |

| Category | Read from | Yields |
|---|---|---|
| contracts | the H1 title (client, product against the closed list); the Parties list; three signature layouts | `Contract`, client `Company` (+`legal_name`), `Product`, `Contact`; `WITH_CLIENT`, `FOR_PRODUCT`, `SIGNED_BY` (title as written) |
| employees | the HR summary; product names; "co-founded Insurellm" | `Employee` (+`job_title`), `Location`, `Product`; `BASED_IN` (`remote`), `WORKS_ON`, `FOUNDED` |
| company pages | "founded by <roster name>"; two wordings of the office list | `Company` Insurellm, `Employee`, `Location`; `FOUNDED`, `HAS_OFFICE` (`headquarters` always True or False) |
| products | the file name | `Product` |

Two rules carry most of the risk. **A fact belongs to the chunk where it starts:** the chunker cut one signature block in half, so each chunk also sees a
bounded piece of the next one, but keeps only what starts inside its own text. **A contract has two parties:** a signature side that is not Insurellm's is
the client, so a contact's organisation equals a `Company` key.

`mentions` lists the entities whose name appears in the chunk's own text (a `Contract` is never named); P4-M5 turns them into `MENTIONS` edges.
Not extracted: anything the model has no room for (salary, date of birth, performance, reporting lines), and any wording the rules were not written for,
which yields nothing and no warning: the tests compare the result with the audit's counts for that reason.

## Graph ingestion (P4-M5, 2026-09-22)

[src/graph/ingest.py](../src/graph/ingest.py) is the job that turns the 223 chunks into the graph a retriever will query: read every chunk, run
[the extractor](../src/graph/extractor.py) over all of it, merge spellings with [`resolve_entities`](../src/graph/dedup.py), and write the result through
[`GraphStore`](../src/retrieval/graph_store.py). It adds no extraction or merging logic of its own; what it adds is the order to run the three in, a
re-run policy, and a report of what actually happened.

    preflight -> load -> extract -> resolve -> validate -> write -> verify

| Stage | Does | Aborts if |
|---|---|---|
| preflight | checks Neo4j is reachable and the schema is in force (`GraphStore(..., verify_schema=True)`); counts nodes under the job's 8 labels | the graph already holds any, unless `--rebuild`, which `DETACH DELETE`s exactly those labels first |
| load | `read_chunks(chunks_dir)` | the directory has none |
| extract | `GraphExtractor.from_chunks(chunks).extract_all(chunks)`, then a write-plan is built from every `Extraction`: `Document`/`Chunk` nodes, `HAS_CHUNK` for every chunk, `DESCRIBED_IN` only from a chunk whose own category is that entity's home page, `MENTIONS` from `Extraction.mentions` | never (a category with no rule only warns) |
| resolve | one corpus-wide `resolve_entities` call, with the client names and roster names already in the plan pinned as `known_names`; `remap_edges` and the same remap for `DESCRIBED_IN`/`MENTIONS` | never |
| validate | every edge's endpoints checked against the exact set of nodes about to be written | any edge names one that will not be written -- nothing is written |
| write | `store.upsert_node` for every node, then `store.upsert_edge` for every edge | a `MissingEndpoint` the validate stage should have already ruled out |
| verify | counts by label and by type are read back from Neo4j and compared with what Python wrote | they differ |

Why `DESCRIBED_IN` checks the chunk's own category and not just the node's label: the extractor creates a `Product` node wherever one is a
`FOR_PRODUCT` edge's target (every contract chunk that names it) and an `Employee` node wherever one is a `FOUNDED` source (the About page too).
Neither of those chunks is that entity's own page. The first version of this job used the label alone and wrote 111 `DESCRIBED_IN` edges on the
real corpus instead of 72 -- caught only by running it against Neo4j, not by any hermetic test (decision.md D-2026-09-22-02/09).

On the real corpus: 143 domain nodes (33 `Company`, 29 `Contact`, 32 `Contract`, 32 `Employee`, 9 `Location`, 8 `Product`), 76 `Document`, 223
`Chunk`; 154 domain edges and 502 provenance edges (223 `HAS_CHUNK`, 72 `DESCRIBED_IN`, 207 `MENTIONS`). `--rebuild` is idempotent: re-running it
against the same corpus reproduces the same counts exactly. The run is logged to `data/graph_ingestion_log.json`, including a note on the CEO
contradiction the corpus states two ways (HR: Avery Lancaster; 7 contracts signed by Jennifer Rodriguez) -- both are loaded as written, unresolved,
because deciding which is right is a retrieval-time question.

## Graph-based retrieval (P5-M1, 2026-09-23)

[src/retrieval/graph_retriever.py](../src/retrieval/graph_retriever.py) answers `GraphRetriever.retrieve(query, k) -> list[DocumentChunk]`.
`GraphStore`'s query methods (`search_related`, `multi_hop_search`) all need an exact, known entity name; a question is free text, not a name. The
retriever is the bridge: entity linking, traversal, and chunk hydration, composed from primitives that already existed.

| Stage | Does |
|---|---|
| construct | `store.all_entity_names()` once, caching every name currently in the graph by label (111 on the real corpus: 33 Company, 32 Employee, 8 Product, 29 Contact, 9 Location); `read_chunks()` once, indexed by `chunk_id` |
| link | whole-word, case-insensitive match of the query against every cached name -- the same regex shape `extractor.py`'s `_mentions` already proved correct |
| traverse | per matched entity, `multi_hop_search` at hops 1 and 2, diffed against each other to recover which entities are newly reached at each distance (the method itself never returns a distance, only nearest-first names) |
| collect | per entity reached at any hop, `search_related(..., include_provenance=True)`: `MENTIONS` edges give chunk ids directly; a `DESCRIBED_IN` edge's target document is expanded to every one of its chunks via the new `GraphStore.chunks_of_document` |
| rank | chunks sorted by `(nearest hop distance any matched entity reached them at, -count of matched entities that reach them at that same nearest distance, chunk_id)`; the final `chunk_id` makes equal chunks come out in the same order every run |
| hydrate | the top `k` chunk ids looked up in the cached `read_chunks()` index and returned as real `DocumentChunk`s |

No known entity in the query returns `[]` immediately, before any graph query -- graph retrieval is one signal, meant to be fused with vector
search later, and "names nothing this graph knows" is an ordinary outcome, not an error. Two small, reusable reads were added to `GraphStore`
itself (`all_entity_names`, `chunks_of_document`) rather than living as raw Cypher inside the retriever, keeping the same layering P3 already
has between `QdrantVectorStore` (primitive) and whatever composes search on top of it.

Verified live: "What does Carllm do?" returns the product's own page and a licensing contract; "Who works on Carllm?" reaches an employee through
the graph; naming both "Velocity Auto Solutions" and "Carllm" in one question promotes the one contract chunk both entities mention directly to
first place (it is not first when only Carllm is named), with no new scoring model.

**Corrected 2026-09-24, while building P5-M2.** The first version credited a matched entity for reaching a chunk at any distance, so entities with
overlapping neighbourhoods (a company and the product it licenses, two hops apart) tied every shared chunk and the alphabet decided; the earlier
"DriveSmart contract moves from 3rd to 1st" claim was an insertion-order artifact. It also left ties in `set` iteration order, which changes with
`PYTHONHASHSEED`, so the same question returned a different order on each run. Both are fixed (see `decision.md` D-2026-09-24-01 and -02).
Side effect of the alphabetical tie-break: `company/` sorts before `contracts/`, `employees/` and `products/`, so product pages come last among ties.

## Hybrid retrieval (P5-M2, 2026-09-24)

[src/retrieval/hybrid_retriever.py](../src/retrieval/hybrid_retriever.py) answers `HybridRetriever.retrieve(query, k, vector_weight, graph_weight) ->
list[DocumentChunk]`. Vector search (P3) finds meaning but is weak on names (the first Carllm product page is at vector rank 22; the CEO's chunk is
outside the top 40); graph retrieval (P5-M1) finds named entities and their neighbours but returns `[]` for a question naming none. The two lists
have different scales (cosine scores versus an unscored order), so they are merged by **weighted Reciprocal Rank Fusion**: every list adds
`weight / (rrf_c + rank)` to each chunk it contains, and the sums decide the order.

| Stage | Does |
|---|---|
| validate | `k` must be a positive integer; weights must be finite, non-negative and not both zero; checked before either retriever is called |
| vector | `search_with_scores(query, 3 * k)` on any object satisfying the `VectorSearcher` Protocol (a `QdrantVectorStore`) |
| graph | `retrieve(query, 3 * k)` on any object satisfying the `GraphSearcher` Protocol (a `GraphRetriever`); a question naming no entity gives `[]` and adds nothing |
| fuse | `fuse_rankings`, a pure function: 1-based ranks, `weight / (60 + rank)` per list, a chunk repeated inside one list counted once, ties broken by vector rank then graph rank |
| cut | the fused list is cut to `k`; `retrieve` returns the chunks, `retrieve_with_scores` returns `FusedChunk`s (chunk, fused score, vector rank, graph rank, raw cosine) |

Defaults: weights 1.0/1.0 (only the ratio matters), `rrf_c = 60`, candidate multiplier 3; all configurable, weights also per call. An error from either
retriever propagates; nothing is caught. The two calls run one after the other, so latency is their sum (median 85 ms + 141 ms, about 230 ms
hybrid, on this machine).

On the five labelled questions: the three known answers stay in the top 3; the CEO question, which vector search misses, gets an accepted chunk at rank 2 at k=3 (the accepted About page names the founder, not the CEO; the chunk stating the title is outside the top 20);
the Carllm product-page question is still a miss at k=3 (recorded as a strict `xfail`) and is found at rank 3 at k=10. Because each retriever is asked
for `3 * k`, results depend on `k`: `retrieve(q, 3)` is not `retrieve(q, 10)[:3]`. Five questions demonstrate the effect but cannot tune weights, `c`
or depth. See [notes/step-5.2-hybrid-retriever.md](../notes/step-5.2-hybrid-retriever.md) for the sweep and the limitations.

## Reranking (P5-M3, 2026-09-25)

[src/retrieval/reranker.py](../src/retrieval/reranker.py) answers `Reranker.rerank(query, chunks, k) -> list[DocumentChunk]`. Retrieval (vector, graph, or the
hybrid of both) orders candidates by comparing texts independently or by position; a reranker is a cross-encoder that reads the question and one chunk
together and returns a relevance score, which is more precise but costs one model pass per candidate, so it is applied to a short list only. It can only
reorder what it is given, so the caller decides the pool size (tests and the live report use `hybrid.retrieve(q, 20)` and keep 3).

| Stage | Does |
|---|---|
| validate | `k` a positive integer, the question a non-empty string; both checked before any request |
| de-duplicate | a chunk id repeated in the input is kept once, at its first position (`input_rank` counts positions in the list as given) |
| short-circuit | an empty list returns `[]` without calling the API |
| score | one request to Cohere `rerank-v3.5` with `query`, the chunk **texts** (nothing else, not the title) and `top_n = min(k, len)` |
| order | by relevance score, then by original position; the response is cut to `k`; an out-of-range document index raises |

`rerank_with_scores` returns `RerankedChunk(chunk, relevance_score, input_rank)`; `rerank` returns just the chunks. The client is injected (a Protocol), and
`Reranker.from_settings()` builds the real one from `COHERE_API_KEY` in `.env` through `build_cohere_client`, which **turns off the SDK's default retries**
(it silently retries 408/409/429/5xx twice with backoff) and sets a 30 s timeout, so an error surfaces at once and nothing is swallowed. Trial keys allow
10 rerank requests per minute and 1,000 calls per month; chunk text leaves the machine on every call.

Tested hermetically with a fake client and with the real SDK over a stubbed HTTP transport (request body, bearer header, response parsing, exactly one
request per failure); ten deliberate breakages of the module were each caught. Measured without Cohere on the real corpus: all five labelled answers are
inside a 20-candidate hybrid pool, but their position depends on the pool size (the CEO answer is at hybrid rank 2, 3, 15, 21, 9 for N = 3, 10, 20, 30, 50),
and the CEO label is lenient (the accepted About page names the founder; the chunk stating the title enters the list only at N=50, rank 17).
**The reranker has not yet been run against the real Cohere API** (no key in `.env`); see [notes/step-5.3-reranker.md](../notes/step-5.3-reranker.md).

## Retrieval pipeline (P5-M4, 2026-09-29)

[src/retrieval/pipeline.py](../src/retrieval/pipeline.py) answers `RetrievalPipeline.retrieve(query, k) -> RetrievalResult` -- exactly the spec'd
signature, with no bonus parameters (unlike P5-M2/M3, whose specs asked for configurability, this task's spec is exact, so all tuning lives in the
constructor). It chains the two previous stages: `HybridRetriever.retrieve_with_scores` finds a candidate pool, `Reranker.rerank_with_scores`
reorders it, and the result carries every stage's own verdict on every chunk.

| Stage | Does |
|---|---|
| validate | `k` a positive integer, the question a non-empty string; both checked before either stage is called |
| pool | `pool_size = max(k * pool_multiplier, pool_floor)`, defaults 3 and 20 (constructor-configurable) |
| retrieve | `hybrid.retrieve_with_scores(query, pool_size)` -- the fused, scored candidates |
| rerank | `reranker.rerank_with_scores(query, chunks, k)` -- the pool's chunks, reordered to the best k |
| join | each reranked chunk is matched back to its own hybrid record by `chunk_id`, producing one `RankedChunk` per result |

`RetrievalResult(query, results: tuple[RankedChunk, ...])` and `RankedChunk(chunk, final_rank, relevance_score, hybrid_score, hybrid_rank, vector_rank,
graph_rank, vector_score)` are frozen dataclasses, matching `DocumentChunk`/`FusedChunk`/`RerankedChunk`'s existing style; `results` is a tuple, not a
list, following the same immutability rule `EmbeddedChunk.vector: tuple[float, ...]` already uses. Both dependencies are Protocol-typed and injected --
`RetrievalPipeline` has no `from_settings()` factory (unlike `Reranker`), because a Neo4j driver has a lifecycle to manage that a pure factory would
need to own.

Tested with fakes (pool-size arithmetic, call order, field mapping, validation, error propagation -- ten deliberate breakages of the module were each
caught) and with a REAL `HybridRetriever` (in-memory Qdrant) paired with a fake reranker. Two live tests need no Cohere key at all (a test-only
`IdentityReranker` stands in for `Reranker`, proving the real hybrid wiring); two more are written and skip until `COHERE_API_KEY` exists.
**The full pipeline has not yet been run against the real Cohere API**; see [notes/step-5.4-retrieval-pipeline.md](../notes/step-5.4-retrieval-pipeline.md).

## Retrieval test suite (P5-M5, 2026-09-29)

Before writing anything, coverage was run on the 212 tests already covering `src/retrieval/` (P5-M1 through P5-M4, each mutation-checked at the time
it was built): 99% line coverage. The one real gap it found -- `fuse_rankings`'s graph-list duplicate-skipping branch
([src/retrieval/hybrid_retriever.py:132](../src/retrieval/hybrid_retriever.py)) had never run in any test, though its sibling vector-list branch had --
was closed with one new test in `tests/retrieval/test_hybrid_retriever.py`.

The other gap was structural, not a coverage number: every existing "real" pipeline test paired a real class with a fake on the other side
(`HybridRetriever` real + `Reranker` fake, or the real Cohere SDK stubbed + fake chunks). Two new tests in
[tests/retrieval/test_pipeline.py](../tests/retrieval/test_pipeline.py) put a real `HybridRetriever` (in-memory Qdrant) and a real `Reranker` (the
actual Cohere SDK, its HTTP transport stubbed with `httpx.MockTransport`, no network) on both sides of one `RetrievalPipeline` at once: one proves the
full `RankedChunk` lineage is correct when both sides are real, the other proves a real Cohere exception type
(`cohere.errors.TooManyRequestsError`) -- not a generic fake error -- survives the trip unchanged.

`src/retrieval/` is now at 100% line coverage. No new file was added -- both additions extended existing test files, since `test_pipeline.py` already
serves as retrieval's own integration point. See [notes/step-5.5-retrieval-test-suite.md](../notes/step-5.5-retrieval-test-suite.md).

## Local LLM setup (P6-M1, 2026-09-29)

`docker-compose.yml`'s `ollama` service (already running since P3, for embeddings) now also serves `mistral` (7B, Q4 GGUF, 4.37 GB), pulled via
`docker exec advancerag-ollama-1 ollama pull mistral`. [src/generation/setup.py](../src/generation/setup.py) mirrors
[src/graph/setup.py](../src/graph/setup.py)'s exact shape: `health_check(client, model)` verifies Ollama is reachable, the model is pulled, **and**
runs one real, tiny generation call (`PROBE_PROMPT`) -- a model can be listed without being able to load or answer. `Settings.llm_model = "mistral"`
carries no digest lock, unlike `embed_model_digest`: a drifted generation model degrades answer quality observably, not silently, so a hard lock
wasn't judged worth its cost here.

`build_client`'s CLI default is `"http://localhost:11434"`, hardcoded directly (matching `src/embedding/embedder.py`'s and
`src/embedding/lock.py`'s own CLIs) -- `Settings.ollama_url` itself defaults to `"http://ollama:11434"`, the Docker Compose service name, which only
resolves from inside another container; `.env` overrides `NEO4J_URI` for host use but has no equivalent `OLLAMA_URL` override.

**Measured, not assumed:** warm generation is fast (0.9-1.0 s for a short reply); the first call after a cold start is not (68.8 s, of which 66.3 s is
Ollama's own model-load time, per its server log). Docker Desktop's WSL2 VM has its own memory budget (~7.6 GB measured via `docker stats`, roughly
half this host's 15.7 GB RAM, no `.wslconfig` override) -- Mistral loaded settles around 65-80% of it. Loading it once coincided with
`advancerag-neo4j-1` and `advancerag-qdrant-1` both exiting (code 255, no graceful-shutdown log entries -- the VM's OOM killer), which running the
full suite after restarting both confirmed was an infrastructure event, not a bug in this project's code (681 passed). Mitigating this (accept the
risk, use a smaller model, or raise the WSL2 memory budget) is a decision left open for the user; the last option is a host system-setting change
outside what this session modifies on its own. See [notes/step-6.1-ollama-llm-setup.md](../notes/step-6.1-ollama-llm-setup.md).

## LLM wrapper (P6-M2, 2026-09-30)

[src/orchestration/llm.py](../src/orchestration/llm.py) answers `LocalLLM.generate(prompt, temperature, max_tokens) -> str` and
`LocalLLM.stream(prompt, temperature) -> Iterator[str]`, wrapping the raw `ollama` package (reusing P6-M1's own `build_client`, since this is the
same concern -- generation -- not a different one). `max_tokens` maps to Ollama's own `options.num_predict`; `temperature` passes straight through.
`LLMClient` is a Protocol with two `@overload`-decorated `generate` signatures, matching `ollama.Client.generate`'s own overload shape
(`stream: Literal[False]` -> one response, `stream: Literal[True]` -> an iterator) exactly, so both the real client and a hermetic fake type-check
cleanly.

| Stage | Does |
|---|---|
| validate | `prompt` non-empty, `temperature` finite >= 0 (no upper bound), `max_tokens` a positive integer (only for `generate`) -- all before any call |
| `generate` | one real, non-streaming call; `options={"temperature": ..., "num_predict": max_tokens}`, `stream=False` |
| `stream` | one real, streaming call; `options={"temperature": ...}` only (no length cap, matching the spec exactly), `stream=True`; the final empty `done=True` fragment is never yielded |

`model` is resolved from `settings.llm_model` inside `__init__` (`settings.llm_model if model is None else model`), never as a parameter default
expression -- a parameter default would freeze the value at import time, the exact anti-pattern `src/graph/setup.py`'s `build_driver` docstring
already warns against. **Finding, not yet fixed:** `src/generation/setup.py`'s own `health_check(client, model: str = settings.llm_model)` has this
exact bug, latent (nothing currently overrides `settings.llm_model` after import) -- flagged as a separate background task rather than patched
inline here.

Tested with a hermetic fake client (33 tests, 9 deliberate breakages of the module each caught -- including the real `model or settings.llm_model`
bug this step's own first draft had, caught by its own test within seconds of being written) and 2 live tests against the real, already-pulled
`mistral`. One live test's original assertion ("reply with exactly one word: PONG") failed against the real model -- the second time this exact
local model has been observed not to follow literal one-word instructions even at `temperature=0` (P6-M1's own probe got "Understood." instead of
"OK") -- replaced with a factual question the model answers correctly and consistently. See
[notes/step-6.2-llm-wrapper.md](../notes/step-6.2-llm-wrapper.md).

## Prompt template design (P6-M3, 2026-09-29, design only)

[docs/prompts.md](prompts.md) is this step's actual deliverable -- a system prompt and a user prompt template, no code. Measured directly from the
real, installed model (`docker exec ... ollama show mistral --template`): Ollama's own `/api/generate` template wraps a passed `system` string as
`[INST] {system}

{prompt}[/INST]`, Mistral's real trained instruction format, applied automatically. The design targets that mechanism, which
means using it for real needs one small, specified, not-yet-built change to `LocalLLM` (a new keyword-only `system: str | None = None` parameter).

| Part | Does |
|---|---|
| system prompt (386 measured tokens) | strict grounding + an exact refusal string, stackable `[N]` inline citations, explicit contradiction handling, a one-shot worked example demonstrating both citations and disagreement at once |
| user prompt template | `Sources:` (numbered `RankedChunk`s: title, source_path, text) then `Question:` then `Answer:` |
| context budget | 2000 approx-tokens of retrieved source text, greedy best-fit in rank order -- verified against real measured chunk-size percentiles (max=1265, p95=909, p90=613, median=204), leaving ~1260 tokens of margin below Ollama's real, measured 4096-token served context window (superseded by a dynamic computation in P6-M5, below) |

The citation strategy is a deliberate hybrid (inline best-effort + a mechanical fallback the design specifies but does not build), grounded directly
in P6-M2's own measured finding that this model does not reliably follow strict format instructions even at `temperature=0`. The contradiction rule
is grounded in this project's own documented data discrepancy (`decision.md` D-2026-09-22-07); the worked example for it was checked against the real
corpus and one wrong citation (a guessed contract file) was found and corrected before publishing. See
[notes/step-6.3-prompt-templates.md](../notes/step-6.3-prompt-templates.md).

## RAG pipeline (P6-M4, 2026-09-29)

[src/orchestration/rag.py](../src/orchestration/rag.py) is the first class that actually wires Phases 3-6 together:
`RAGPipeline.query(question: str) -> str` and `.stream(question: str) -> Iterator[str]` retrieve, budget, prompt, and generate, with no other module
needing to know Qdrant, Neo4j, Cohere, or Ollama exist. [src/orchestration/prompts.py](../src/orchestration/prompts.py) turns P6-M3's design into
real, tested code for the first time: `SYSTEM_PROMPT` and `REFUSAL_TEXT` are verbatim from `docs/prompts.md` (single-sourced, so they can never drift
apart), `apply_budget` is the exact greedy best-fit algorithm from section 4, and `mechanical_source_list` is section 5's citation fallback, always
appended to the model's own output regardless of its inline citations.

| Stage | Does |
|---|---|
| retrieve | `RetrievalPipeline.retrieve(question, k)`; an empty result short-circuits straight to `REFUSAL_TEXT`, never calling the model |
| budget | `apply_budget` drops the lowest-ranked chunks that don't fit 2000 approx-tokens, always keeping at least the first |
| prompt | `build_user_prompt` numbers survivors by position (never original `final_rank`, so citation numbers stay contiguous) |
| generate | `LLM.generate`/`.stream` called with `system=SYSTEM_PROMPT`; the mechanical source list is appended to the raw answer (or yielded as a final chunk, for `stream`) unconditionally |

`LocalLLM.generate`/`.stream` ([src/orchestration/llm.py](../src/orchestration/llm.py)) gained the `system: str | None = None` parameter
`docs/prompts.md` section 6 pre-specified — the one piece P6-M3 deliberately left unbuilt. Verified live: a real system prompt ("answer only in
French") measurably changed a real reply, not just passed through without error. A live run of the full pipeline (real Qdrant, Neo4j, and Ollama,
a fake reranker so no Cohere key is needed) found a real, separate bug in an already-shipped module —
[src/embedding/embedder.py](../src/embedding/embedder.py)'s `build_ollama_client` has no request timeout, unlike `src/generation/setup.py`'s
deliberate 120-second bound — flagged as a follow-up task rather than fixed inline. The known "picks a winner" risk in the published few-shot example
(`decision.md` D-2026-09-29-25, found via [docs/31](<31 few_shot_tie_investigation_deep_dive.md>)) is used as-published here, backstopped by the
mechanical citation fallback. See [notes/step-6.4-rag-pipeline.md](../notes/step-6.4-rag-pipeline.md).

## Context window management (P6-M5, 2026-09-30)

P6-M4's `apply_budget` used a *fixed* budget (`CONTEXT_BUDGET_TOKENS = 2000`), hand-derived once from an assumed system-prompt size (386) and
answer length (400). This step generalizes that into a real computation: [`src/orchestration/prompts.py`](../src/orchestration/prompts.py) gained
`CONTEXT_WINDOW_TOKENS = 4096` (the real, measured served limit) and `fit_context(chunks: Sequence[DocumentChunk], prompt: str, max_tokens: int) ->
list[DocumentChunk]` — the task's own literal deliverable, operating on plain `DocumentChunk`s. `apply_budget` was refactored to share the same
underlying `_greedy_best_fit` helper and compute its own budget the same way (`4096 - count_tokens_approx(prompt) - max_tokens`), rather than
duplicating the algorithm for `RankedChunk`. `RAGPipeline._prepare` now builds the `prompt` passed to `apply_budget` as
`SYSTEM_PROMPT + "\n\n" + build_user_prompt(question, [])` — reusing the existing template builder with an empty source list to get exactly the
real, non-chunk portion of the final prompt, rather than writing a new "shell" builder.

**A disclosed trade-off, not silently absorbed:** the old fixed 2000 carried ~1260 tokens of deliberate margin below the theoretical ~3260 available,
specifically because `approx_token_count` is an estimate that can run higher than real tokenization. The new dynamic computation uses the full
theoretical figure with no such cushion (~3295 tokens available at `RAGPipeline`'s real defaults) — a real behavior change, recorded as
`decision.md` D-2026-09-30-06 rather than quietly re-introducing an undiscussed safety buffer. See
[notes/step-6.5-context-window-management.md](../notes/step-6.5-context-window-management.md).

## RAG integration tests (P6-M6, 2026-09-30, core learning: no)

New top-level [`tests/test_rag.py`](../tests/test_rag.py) (not nested, matching [`tests/test_retrieval.py`](../tests/test_retrieval.py)'s own
established split) proves the real seams across Phases 3-6 hold, distinct from `tests/orchestration/test_rag.py`'s existing unit coverage of
`RAGPipeline` against fully-faked dependencies. Hermetic tier: real `RetrievalPipeline` + real `prompts.py` + real `RAGPipeline`, only the `LLM`
faked — 3/3 passing. Live tier mirrors `tests/retrieval/test_pipeline.py`'s own no-Cohere-key fixture, reporting all five of this project's labelled
acceptance questions and strictly checking the three already known (Phase 5) to retrieve well.

**A real bug found and fixed along the way:** live tests initially failed 5/5 with a 120-second timeout, first suspected to be a repeat of P6-M5's
"stuck Ollama runner" finding — but a full Docker Desktop restart (twice) didn't fix it, which was itself the clue the diagnosis was wrong. A direct
call with a 300-second timeout completed in 108.3s, proving the process was never hung, just genuinely CPU-bound the whole time on this host's real
throughput. Root cause: [`build_llm`](../src/orchestration/llm.py) was reusing `src/generation/setup.py`'s `PROBE_TIMEOUT_SECONDS` (120s, sized for
a tiny one-word readiness reply, P6-M1) for real ~400-token generation calls. Fixed with a separate `GENERATION_TIMEOUT_SECONDS = 300.0`, specific
to real generation. After the fix: 2/5 live tests pass (up from 0/5); the remaining 3 are accepted as a disclosed host CPU-throughput ceiling, not
chased further (`decision.md` D-2026-09-30-11/-12). See [notes/step-6.6-rag-integration-tests.md](../notes/step-6.6-rag-integration-tests.md).

## Integration checkpoint (P6-M7, 2026-09-30, core learning: no)

A manual spot-check of the complete, real `RAGPipeline` against 8 real questions — the 5 established Phase-5 benchmark questions plus 3 new ones
(an out-of-scope refusal test, a CEO rephrasing, a compound question) — using production defaults end to end (real Qdrant, Neo4j, Ollama;
`IdentityReranker`, no Cohere key). 5 of 8 completed; 3 timed out even at the 300s bound P6-M6 just raised it to, a measured 37.5% real timeout rate
that is itself the checkpoint's headline finding — grounding and citation correctness were strong (zero hallucinations across every completed
answer, including safe handling of both documented Phase-5 misses), but this is the first time latency was measured against a varied, realistic
question set rather than the same repeated benchmark. It also surfaced a genuinely new retrieval gap: "What are the company values?" retrieved two
employees' HR records and the careers page, never `data/company/culture.md`'s actual Core Values section — directly contradicting P6-M6's own
`KNOWN_RELIABLE` assumption, flagged as a follow-up (`task_a1bdb8a8`) rather than fixed inline here. See
[notes/step-6.7-integration-checkpoint.md](../notes/step-6.7-integration-checkpoint.md).

## FastAPI app skeleton (P7-M1, 2026-09-30)

[`src/api/main.py`](../src/api/main.py) and its `/health` endpoint already existed before this session, already tested by
[`tests/test_health.py`](../tests/test_health.py), and already exceeded the task's own "hello-world" bar — a real, informative liveness check
reporting the instance's own configured `qdrant_url`/`neo4j_uri`/`ollama_url`, not a bare `{"status": "ok"}`. Confirmed rather than rebuilt; a full
deep dive was built instead, covering the ASGI app object, path operation decorators, automatic response serialization, the app-vs-server
relationship (ASGI, and why `uvicorn` is a separate process from `app`), and the health-check design pattern itself. See
[docs/34 fastapi_skeleton_deep_dive.md](<34 fastapi_skeleton_deep_dive.md>).

## Chat endpoint with streaming (P7-M2, 2026-09-30)

`POST /chat` in `src/api/main.py` — the first real RAG endpoint. A Pydantic `ChatRequest` (`query: str`, `k: int = 3`) gives automatic request
validation; the real `RAGPipeline` (real Qdrant/Neo4j/Ollama, a real Cohere `Reranker` when `COHERE_API_KEY` is configured else the newly-promoted
[`IdentityReranker`](../src/retrieval/reranker.py) — previously duplicated as a local test fixture in three separate files) is built exactly once,
at startup, via FastAPI's `lifespan` context manager, and stored on `app.state` — never rebuilt per request. The response streams
`RAGPipeline.stream()`'s real fragments as hand-rolled Server-Sent Events (`data: ...\n\n`, correctly re-prefixing each line of the mechanical
source list's own multi-line final chunk, ending with `data: [DONE]\n\n`).

**A real architectural question, verified rather than assumed:** `RAGPipeline.stream()` is fully synchronous, but FastAPI path operations run on an
async event loop — would a long (100-300+s, per this project's own measured throughput) blocking generator freeze every other concurrent request?
Reading Starlette's own `StreamingResponse` source directly showed the answer is no: a plain sync generator is automatically wrapped in
`iterate_in_threadpool`, which runs each `next()` call in a worker thread via `anyio.to_thread.run_sync` — no code changes needed.

**A deliberate departure from this project's usual error-handling pattern:** every prior decision in this project has let errors propagate
unchanged (7 consecutive times). Here, once SSE headers are sent, a raised exception can no longer become a clean HTTP status code — so a mid-stream
failure is instead caught and sent as a final `data: [ERROR] ...` event. Verified live, not just hermetically: hitting the real endpoint produced a
genuine ~300s generation timeout (the same already-disclosed host throughput ceiling from P6-M5/M6/M7), and it arrived at the client as a real,
readable SSE error event, not a silent connection reset. `RAGPipeline.query`/`.stream` also gained an optional per-call `k`, overriding the
instance's own configured one, mirroring `RetrievalPipeline.retrieve(query, k)`'s own per-call design. See
[notes/step-7.2-chat-endpoint.md](../notes/step-7.2-chat-endpoint.md).

## Validation & error handling (P7-M3, 2026-09-30)

Every error response in `src/api/main.py` now shares one structured envelope, `{"error": {"code": "...", "message": "..."}}`, produced by a small
`_error_envelope` helper and four app-wide `@app.exception_handler(...)` registrations: a `RequestValidationError` handler that splits FastAPI's
own default (422 for everything) into a real 400 — a body that fails to parse as JSON at all — versus 422 — a well-formed body that fails a
field's own rule, with every field error joined into one message; a `TimeoutError`/`httpx.TimeoutException` handler returning a structured 504;
and an `Exception` catch-all returning a structured, detail-hiding 500, with the real traceback logged server-side via `logger.exception`.
`/health` gained a real `HealthResponse` model (`response_model=HealthResponse`), closing the one genuine gap in "response validation" — it
previously returned a bare `dict`.

**A structural limit, proven before writing any handler:** reading `StreamingResponse.stream_response` directly (`starlette/responses.py`) shows
the 200 status line is sent *before* the generator is ever touched — meaning nothing `_sse_events` raises (a Qdrant/Neo4j failure during
retrieval, an Ollama timeout during generation) can ever reach the new 500/504 handlers; `/chat`'s own mid-stream failures stay exactly what
P7-M2 built (the in-stream `data: [ERROR] ...` event). The handlers were built anyway — correct, idiomatic FastAPI, immediately useful for the
non-streaming `/ingest`/`/query` endpoints this file's own docstring already flags as coming — with the `/chat` blind spot stated directly in
`_handle_timeout`'s and `_handle_unexpected_error`'s own docstrings, not left implicit.

**A real, live-path gap found and closed along the way:** [`src/embedding/embedder.py`](../src/embedding/embedder.py)'s `build_ollama_client` had
no `timeout` kwarg at all, which `ollama.Client` defaults to `timeout=None` — confirmed directly (`httpx.Client(timeout=None).timeout`) to mean
no timeout whatsoever, not httpx's own 5-second default. Every `/chat` request embeds its query through exactly this client, as the first step
of retrieval. Fixed with a measured `EMBED_TIMEOUT_SECONDS = 60.0` (real `embed_query`: 0.701s; a real 16-text `embed_batch` call: 1.005s, both
against this project's own warm Ollama), mirroring `LocalLLM`'s own `GENERATION_TIMEOUT_SECONDS` pattern. See
[notes/step-7.3-error-handling.md](../notes/step-7.3-error-handling.md).

## Logging & observability (P7-M4, 2026-09-30)

`src/api/main.py` gained its first-ever logging configuration: `_JsonFormatter` (a hand-written `logging.Formatter` subclass) emits one
JSON object per log line -- timestamp, level, logger name, message, every `extra={}` field a call attaches, plus the real traceback when
`exc_info` is set -- attached via `logging.basicConfig` at module level, mirroring the exact pattern this project's other 5 entry points
already use. `RequestLoggingMiddleware`, a raw ASGI middleware (not FastAPI's `@app.middleware("http")`), logs one structured line per
request (`method`, `path`, `status_code`, `duration_ms`), escalating to `WARNING` once `duration_ms` crosses `SLOW_REQUEST_MS = 5000.0`.

**A middleware choice driven by direct measurement, not framework convention:** `@app.middleware("http")` (`BaseHTTPMiddleware`) is the
first result in nearly every FastAPI logging tutorial -- and measured directly to return from `call_next` after 0.005s for a ~0.95s real
streaming route, timing only how long it took to construct the `StreamingResponse` object, not how long the real stream took to finish.
A raw ASGI middleware, wrapping `send` directly and watching for the real final response message, measured the true 0.906s instead --
the only approach that correctly times `/chat`, the one endpoint whose real latency is this project's own repeatedly-documented concern.

**A bug predicted and fixed before it shipped, using a P7-M3 finding directly:** P7-M3 (see this file's own P7-M3 section) proved a
handler registered for the bare `Exception` class routes through `ServerErrorMiddleware`, which sits *outside* any `app.add_middleware(...)`
layer. That fact alone predicted the new logging middleware would never observe that one error path as a normal completion -- confirmed
directly: the exception propagates *through* the middleware's own call instead of a `send` message, requiring its own `try`/`except`
around the inner `await self._app(...)` call (logging, then re-raising so `ServerErrorMiddleware` still does its own job) to log that
case at all. See [notes/step-7.4-logging-observability.md](../notes/step-7.4-logging-observability.md).

## API documentation & testing (P7-M5, 2026-10-01)

`/docs` (Swagger UI) and `/redoc` were already auto-generated by FastAPI -- this step verified them by reading the real, generated
`app.openapi()` schema directly rather than assuming it was already accurate, and closed what it found. `/chat`'s `200` response was an
empty, actively-wrong `application/json` schema (FastAPI's own default for a response with no `response_model`); it now correctly
documents the real `text/event-stream` content type, with a real captured example event sequence. `400`, `500`, and `504` -- real,
tested response shapes P7-M3 already built -- were completely absent from the schema (only `200` and FastAPI's own default `422`
appeared); `CHAT_RESPONSES`, a module-level dict in `src/api/main.py`, now documents all of them with real examples matching
`_error_envelope`'s actual shape, including an explicit note on `504` explaining that `/chat`'s own real work can never actually
produce it (see this file's own P7-M3 section). `ChatRequest.query` gained a real example value (`"Who founded Insurellm?"`, one of
this project's own already-established reliable questions), `HealthResponse`'s four fields gained real descriptions, and both routes
gained `tags`.

**A bug found in the fix itself, not assumed away:** adding `responses={200: {"content": {"text/event-stream": ...}}}` alone did not
replace FastAPI's own spurious default entry -- it merged alongside it, confirmed by re-fetching the real schema and seeing both
content types still listed for one response. Reading `fastapi/openapi/utils.py`'s own `get_openapi_path` directly showed the real
cause: FastAPI's automatic-schema generation reads the route's configured `response_class` (defaulting to `JSONResponse` for every
route, regardless of the function's own return-type hint), not `responses=` at all. `response_class=StreamingResponse` on the
`/chat` decorator was the real fix, confirmed by re-checking the schema a second time afterward.

**[`docs/api-usage.md`](<api-usage.md>)** (new): every example -- `/health`, a full real `/chat` streaming response, 400, 422, and
500/504 (via a temporary debug-only route, since `/chat` itself cannot produce those two statuses for real) -- was run against the
live app and its real output captured, not hand-written. See [notes/step-7.5-api-documentation.md](../notes/step-7.5-api-documentation.md).

## API tests (P7-M6, 2026-10-03)

[`tests/test_api.py`](../tests/test_api.py) is a top-level cross-component file, following the `tests/test_rag.py` precedent: the five per-concern API test files
(35 tests, 88% line coverage of `src/api/main.py`) each unit-test one concern against fakes, and this file asserts only what they cannot see. Its sections:
the public route table; what the framework answers for paths, methods and bodies the app never declared; the real retrieval stack behind real HTTP; the real
`lifespan` with only its network-touching calls replaced; incremental delivery and concurrency; the JSON log lines an operator actually sees; strict-`xfail`
tests for known unhardened gaps; and a small `-m live` tier. 59 test items: 53 hermetic passing tests, 4 strict xfails, 2 live.

**Static gates first.** Running the three CI gates (`ruff check`, `ruff format --check`, `mypy src/`) for the first time since P7-M2 found all three red -- 15 E501,
three unformatted files, three mypy errors in `main.py` -- so they were fixed before any new test was written: `ruff format` (proved AST-identical, layout only),
`starlette.types` annotations on `RequestLoggingMiddleware`, an E501 per-file-ignore for the published `SYSTEM_PROMPT` text, and a direct `starlette==1.3.1` pin.

**Streaming is observed below the test client.** `TestClient` and httpx's `ASGITransport` both run the app to completion and return one buffered body, so neither
can show when a chunk left the app. A ~25-line raw-ASGI harness (`asgi_request`) calls the app with `scope`/`receive`/`send` and timestamps every message: the
response headers leave before the first fragment, fragments are spaced by the model's own delay, and `/health` answers in ~0.1 s while a ~1 s `/chat` stream is
still running -- docs/35 C4's claim, previously verified only on a toy app. Two committed negative controls (a loop-blocking handler, a buffering handler) prove
the harness can fail, and a mutation check against the real `_sse_events` showed each streaming test fails for its own bug and not the other's.

**Three production fixes the tests forced**, each written as a failing test first (12 red, then green): framework-raised `404`/`405` now use the same error
envelope as every other error (a Starlette `HTTPException` handler, keeping `Allow`) -- the earlier "every error shares one envelope" claim had been an overclaim;
a whitespace-only `query` is now a `422` instead of a `200` stream carrying an in-band `[ERROR]` (`str_strip_whitespace`); and `lifespan` now closes the Neo4j
driver when startup fails after it was built (`try`/`finally`). Four gaps that contradict no documented rule -- unbounded `k`, unbounded query length, no SSE
cache headers, no `HEAD /health` -- are strict xfails of the desired behavior rather than fixes. See [notes/step-7.6-api-tests.md](../notes/step-7.6-api-tests.md).

## API contract checkpoint (P7-M7, 2026-10-03)

[`docs/api-contract.md`](api-contract.md) is the API's contract written clause by clause, and **every clause names the check that
enforces it**. Three layers check it, so one blind spot cannot hide a defect: the hermetic tests in `tests/test_api.py`; a frontend simulator
(`practice codes/phase 7/m7_api_contract_checkpoint/check_api_contract.py`, 23 clauses) that drives a *running* server over HTTP, once against the
real app with a scripted model (`stub_server.py` swaps only the lifespan, so every route, handler, middleware and the SSE framing are the real ones)
and once against the real Qdrant + Neo4j + Ollama stack, where one real generation is shared between the stream clauses and only a `timed out`
is tolerated; and a real browser page (`web/frontend_sim.html`: `fetch` + `ReadableStream`, its own SSE parser, a Stop button, `EventSource`,
same-origin and cross-origin). The checker is itself shown able to fail: `run_mutants.py` switches on one deliberate bug at a time and requires
exactly the right clauses to go red (nine results as expected, baseline included).

**Two defects in claims I had made.** SSE has three line terminators and `_sse_events` handled one, so a lone `\r` in model text corrupted the
stream and could forge an event (`"inject\revent: error"` became an `event: error` field); confirmed with two independent parsers before the fix, which
now splits on exactly `\r\n`, `\r` and `\n` (`str.splitlines()` would have been wrong too). And `/openapi.json` declared `/chat`'s 422 with FastAPI's
stock schema, a body this app stopped sending in P7-M3: one `ErrorResponse` model now both declares 400/422/500/504 and builds the real error bodies,
and the declared-vs-observed check refuses a schema that requires nothing, because such a schema validates any body.

**Gaps found, pinned, not fixed** (the contract-scope decision fixes only what contradicts something documented). The main one, **G1**: when a client
walks away mid-stream the server stops asking the model for fragments, but the generator chain holding the connection to Ollama is closed only when
the cyclic garbage collector runs. It is pinned by a strict `xfail`, its positive control and a `GAP` verdict that becomes `FAIL` the day it closes; a
candidate fix (a `BackgroundTask` that closes the generator) is verified on mutants of the stub and in-process (its real-stack effect is not yet measured; see the contract). Also
recorded there: no CORS by decision (a real browser confirms a page on another origin is blocked, and `EventSource` cannot call a `POST` route), no
heartbeat during the model's long first-fragment silence, and the four P7-M6 xfails. See [notes/step-7.7-api-contract-checkpoint.md](../notes/step-7.7-api-contract-checkpoint.md).

## Chat UI (P8-M1, 2026-10-03)

[`src/ui/app.py`](../src/ui/app.py) is the Streamlit chat page and [`src/ui/client.py`](../src/ui/client.py) is everything that talks to the API (no Streamlit in it):
a hand-written incremental SSE reader following the WHATWG rules, `ask()` which sends the question and raises a `ChatError` for a refused connection or a
400/422/500/504 *before* any streaming starts, and `AnswerStream`, which yields the answer text fragment by fragment, holds the source list back, and records
`complete`/`error` and the text received so far. It is the first in-repo client of the API, written against [`docs/api-contract.md`](api-contract.md); its tests
replay that contract's awkward-fragment cases and also run it against the real FastAPI app in-process. Run with `streamlit run src/ui/app.py` (API address:
`Settings.api_url`, env `API_URL`, default `http://localhost:8000`).

**Streamlit re-executes the whole file on every interaction**, so a question takes two runs: the first records it in a plain `Chat` object kept in session state and
reruns with the input, the `k` slider and *New chat* disabled; the second shows a status with a running clock until the first word (minutes on this CPU-only host),
streams the answer with `st.write_stream`, saves the reply, unlocks and reruns once more. The lock is deliberate: an interrupt is honoured only at the next `st.*`
call, an abandoned request keeps the model working for nobody (gaps G1 and G8) and the next question would queue behind it. **Inside a `finally`, every `st.*` call
and every `st.session_state` access raises again while Streamlit is stopping a script** (measured), which is why the state that cleanup must change is a plain
object; the first version, which used session-state keys, would have left the page locked and sent a `None` question to the API after a Stop.

**Model text is untrusted.** `as_plain_text()` backslash-escapes every ASCII punctuation character before anything goes through Streamlit's markdown renderer, so the
corpus's dollar amounts do not become maths, an image link does not make the browser fetch a URL, and directives do not format text; it works fragment by fragment.
`.streamlit/config.toml` makes Streamlit listen on 127.0.0.1 only (its default is every interface, with no login in front of minutes of model work) and switches
usage statistics off. Checked three ways: 104 tests in `tests/ui/` (76 client, 28 page under `AppTest`), each mutation-checked; a real browser against the P7-M7 stub
API (`practice codes/phase 8/m1_streamlit_chat_ui/ui_stub.py`); and once against the real stack. Known limits: no Stop button yet, and Streamlit's own Stop leaves the
widgets drawn disabled until a rerun (press `r`). See [notes/step-8.1-streamlit-chat-ui.md](../notes/step-8.1-streamlit-chat-ui.md).

## UI configuration (P8-M3, 2026-10-04)

The chat page finds the API through one setting, `Settings.api_url` (`API_URL`). The value comes from the first of: an environment variable, a line in `.env` in the
**current folder**, the default `http://localhost:8000` -- measured in fresh processes and pinned by `tests/ui/test_endpoint.py` in temporary folders, including the
pitfall that a process started from another folder silently ignores `.env`. Settings are read once per process, so editing `.env` needs a restart of `streamlit run`
(a browser reload starts a new session in the same process; measured). Streamlit's own mechanisms were measured and rejected: `config.toml` raises `RuntimeError:
Config key ... not defined` for a custom key, and `st.secrets` raises `StreamlitSecretNotFoundError` when no `secrets.toml` exists, even for `.get` with a default.

[`src/ui/endpoint.py`](../src/ui/endpoint.py) checks the value before the page draws anything: `normalize_api_url` requires `http://` or `https://`, a host name and a usable
port, refuses spaces, a query or fragment, a password in the address and an address that already ends in `/chat` or `/health`, and tidies padding, trailing slashes and an
upper-case scheme. On a bad value the page shows one error that names the fix and stops (no sidebar, no input); before this step `localhost:8000` was reported as
"Cannot reach the API at … (cannot connect (UnsupportedProtocol))" and as a lost connection, three layers from the typo. The check lives in the page's own package rather than in `Settings`,
which the API and the scripts share and which must not refuse to start over a setting only the UI uses. A value containing `@`, `?` or `#` is never repeated in a message, and every message reminds the reader to restart `streamlit run` (settings are read once). Timeouts,
the `k` bounds and the question cap stay constants: they are coupled to code and to measurements of this host, not to the deployment. A future compose `ui` service would
set `API_URL: "http://api:8000"` and, because the image copies only `src/`, would listen on all interfaces inside the container, moving the "local only" promise of
D-2026-10-03-32 to the port mapping. See [notes/step-8.3-ui-configuration.md](../notes/step-8.3-ui-configuration.md) and the Configuration section of the
[README](../README.md).
