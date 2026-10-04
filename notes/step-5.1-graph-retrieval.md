# Phase 5, Microstep 1 — Graph-Based Retrieval

**Date:** 2026-09-23
**Core learning: Yes — multi-hop traversal.** This is the first step in the project whose entire
point is a core RAG technique: turning a graph of facts into an answer to a free-text question.

> **Corrected 2026-09-24, while building P5-M2.** Two defects in this step's ranking were found and
> fixed, and one claim in the first version of this note was wrong.
> 1. **Ties were not reproducible.** Tied chunks came out in `set` iteration order, which changes with
>    `PYTHONHASHSEED`; the same question returned a different order on each run. Fixed with a final
>    `chunk_id` sort key. Side effect: alphabetical order ranks `company/` before `contracts/` before
>    `employees/` before `products/`.
> 2. **"Convergence" was diluted.** A query entity was credited for reaching a chunk at any distance,
>    so a Company and the Product it licenses (two hops apart) tied on every shared chunk. Fixed: a
>    query entity now counts only for chunks it reaches at that chunk's *nearest* distance.
> 3. **The wrong claim:** "the DriveSmart contract moves from 3rd to 1st" was an insertion-order
>    artifact, not evidence. It has been replaced below by a result that fails when the rule is removed.
>
> Details: `decision.md` D-2026-09-24-01 and -02, `docs/24`, and [step 5.2](step-5.2-hybrid-retriever.md).

## The gap this step closes
Every `GraphStore` query method needs an *exact, known* entity name — `search_related`/`multi_hop_search`
both run `MATCH (e:Label {name: $name})`. Nothing built so far turns a question like *"Who works on
Carllm?"* into the fact that `"Carllm"` is a real `Product` node, and nothing turns the chunk ids a
graph traversal finds back into real chunk text (the graph's `Chunk` nodes hold ids only, by design —
P4-M2). `GraphRetriever` is the bridge: **entity linking** (question → known names), **multi-hop
traversal** (names → connected entities), and **chunk hydration** (chunk ids → real `DocumentChunk`s).

## What was built
- **[`src/retrieval/graph_retriever.py`](../src/retrieval/graph_retriever.py)**: `GraphRetriever(store, *,
  chunks_dir=..., hops=2)`, one public method: `retrieve(query: str, k: int) -> list[DocumentChunk]`.
- **Two small additions to [`src/retrieval/graph_store.py`](../src/retrieval/graph_store.py)**:
  `all_entity_names()` (every name currently in the graph, grouped by label) and
  `chunks_of_document(doc_id)` (every chunk id belonging to one document, via `HAS_CHUNK`) — reusable
  graph primitives, not retrieval-specific logic.
- **Tests:** `tests/retrieval/test_graph_retriever.py`, 14 tests: 8 hermetic (the pure text-matching
  helpers, plus two ranking tests against a fake store), 6 live (read-only against the real corpus
  already loaded by P4-M5 — this retriever never writes, so no fixture or cleanup is needed, unlike
  every other live graph test in this project). It was 12 (6 + 6) when first written; the two
  hermetic ranking tests were added by the 2026-09-24 corrections.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Entity linking | whole-word match against the graph's own 111 current names | fuzzy matching; NER/embeddings | a paraphrase ("the car insurance product") finds nothing — only near-exact name mentions are recognised |
| D2 Chunk hydration | `read_chunks()` from disk, filtered to found ids | fetch from Qdrant by point id | the graph's chunk ids and disk's chunk ids could drift if chunks are ever regenerated without a joint re-ingest |
| D3 Related chunks | `MENTIONS` union the entity's home-document chunks (via a new `chunks_of_document`) | `MENTIONS` only | a chunk relevant to an entity but naming it nowhere, in a document that isn't the entity's own home page either, is still missed |
| D4 Traversal | `multi_hop_search(hops=2)` per matched entity, unioned | 1 hop only; the full 3-hop cap | a very "central" entity (Insurellm itself) can pull in a broad, loosely-related set at hop 2 |
| D5 Ranking | hop distance, then agreement (query entities reaching the chunk at its nearest distance), then `chunk_id` | no ranking; a centrality-weighted score | ties at the same hop distance and agreement are broken alphabetically by `chunk_id`: reproducible, but not a relevance signal, and it puts `products/` last among ties. (The first version counted agreement at any distance and left ties in `set` order; see the correction box.) |
| D6 No entity found | return `[]` | a lower-confidence fallback; raise | a caller expecting graph retrieval to "always find something" gets silently nothing (by design) |
| D7 Name freshness | query Neo4j once per instance, cache | re-derive from chunks; hard-code | stale only if the graph changes after the instance is built and before it's rebuilt |
| D8 Module shape | new file, two `GraphStore` primitives | `retrieve()` folded into `GraphStore` | one more file to keep in sync with `GraphStore`'s query surface |

## Verified, not assumed
```
tests               : 14 passed (8 hermetic, 6 live), re-run 2026-09-24 after the corrections
known names         : 111 (33 Company, 32 Employee, 8 Product, 29 Contact, 9 Location)
construction         : ~1.6s (one query + driver warm-up), once per GraphRetriever instance
"What does Carllm do?"                        -> products/Carllm.md_2, 3 licensing contracts,
                                                  employees/Tyler Brooks.md_0                (0.2-3.7s)
"Who works on Carllm?"                        -> the same set (an employee reached via MENTIONS
                                                  at hop 0, since his HR record names Carllm directly)
"Does Velocity Auto Solutions license Carllm?"-> the Velocity contract chunk is FIRST; asking about
                                                  Carllm alone puts it behind three other Carllm
                                                  contracts (alphabetical order among ties). Both
                                                  entities mention it directly, so agreement counts.
"What is the weather today?"                  -> [] in 0.001s -- no entity recognised, no query sent
same question, 3 hash seeds -> identical order (it differed before the chunk_id tie-break)
```
The earlier version of this block claimed the DriveSmart contract "moves from 3rd to 1st". That was an
insertion-order artifact and is withdrawn (see the correction box at the top).

**What building this taught.** `multi_hop_search` computes a per-node minimum hop distance
internally (`min(length(p)) AS distance`) but only ever returns the entity *names*, sorted
nearest-first — the distance itself never leaves the query. Ranking by hop distance meant calling it
once per hop level (1, then 2) and taking the set difference, rather than changing its return type
and risking the 40 tests already built against `list[str]`. This is a real, general lesson: an
existing function's return SHAPE can silently discard information a new caller needs, and the fix is
often "call it more than once and diff," not "change the function."

## Key concepts
🔴 = core to RAG and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Query-time entity linking:** recognising that a piece of free text names a specific, known
  entity — the bridge between an unstructured question and a structured graph, and a prerequisite for
  any GraphRAG system, not just this one.
- 🔴 **Multi-hop graph traversal for retrieval:** answering a question using facts CONNECTED to the
  named entity, not just facts that repeat its name — the actual value proposition of a knowledge
  graph over a plain keyword or vector index (Edge et al. 2024).
- 🔴 **Chunk hydration / provenance bridging:** a graph hit is entities and edges, not text; reaching
  real content means following `MENTIONS`/`DESCRIBED_IN` back to a `chunk_id` and resolving that id
  against wherever the actual text lives (here, `data/chunks/`; in P3, Qdrant).
- 🟡 **Diffing successive query results to recover missing information:** `multi_hop_search(hops=1)`
  vs `multi_hop_search(hops=2)`, subtracted, to learn what a function's own return shape hid.
- 🟡 **Ranking by structural signals, not similarity scores:** hop distance and multi-entity
  agreement (counted only at a chunk's nearest distance) are graph-native relevance signals with no
  embedding or cosine similarity involved.
- 🟡 **Complete sort keys:** a sort that leaves ties lets the input order decide, and input order from
  a `set` of strings changes between runs. End the key with something unique (`chunk_id`).
- ⚪ **Caching a small lookup table at construction time:** `all_entity_names()` called once, not per
  query — ordinary object-oriented plumbing.

## Honest limitations
1. **Exact-ish name matching only.** "The car insurance platform" will never link to `Carllm`; this
   retriever's recall is bounded by how literally a question names things.
2. **No fusion yet.** A question naming no entity returns `[]` — by design, but this retriever alone
   cannot answer such a question; that needs combining it with P3's vector search (not yet built).
3. **Hops is fixed at 2 for the whole corpus.** No per-query adjustment, and no measurement yet of
   whether 2 is the right number for every kind of question this corpus could be asked.
4. **Ranking has no numeric score**, only an ordering — nothing here could be combined with a vector
   search's cosine scores without first deciding how to put the two on a comparable scale (a fusion
   step's own problem, deliberately deferred).
5. **`read_chunks()` re-reads the whole corpus into memory on every `GraphRetriever` construction.**
   Fine at 223 chunks; would need revisiting at real scale.
6. **Hub entities give weak rankings.** A question naming "Insurellm" reaches hundreds of chunks that
   tie on distance and agreement, so the order among them is just alphabetical `chunk_id`. That is
   reproducible but arbitrary, and it puts `products/` last among ties. (Found while building P5-M2:
   it is why the CEO answer, `company/about.md_0`, happens to be graph rank 1.)

## How this connects
**Previous:** P4 built, deduplicated, ingested and validated the knowledge graph; nothing before this
step could turn a QUESTION into a graph query. **This step:** the first consumer of the graph that
looks like retrieval. **Next:** P3's vector retrieval and this step's graph retrieval need to be
fused into one ranked list (RAG-Fusion, reciprocal rank fusion, or a simpler union-and-rerank) —
Phase 5's remaining microsteps, plus reranking with a cross-encoder, both build directly on having two
independent retrieval signals to combine.

## How to do this yourself
1. Before building a retriever, list every method the underlying store already exposes and what each
   one REQUIRES as input — the gap between "what a user will type" and "what these methods need" is
   exactly what a retriever has to bridge.
2. Check whether a function you plan to call already computes something useful that it doesn't
   return (read its actual query/implementation, not just its docstring) before assuming you need to
   duplicate that computation yourself.
3. Design entity linking around your corpus's actual vocabulary size: a small, closed vocabulary
   (dozens to low hundreds of names) makes exact/whole-word matching viable; a large or open
   vocabulary would need embeddings or NER instead.
4. Decide your "no match" behaviour explicitly and test it — an empty result is not a bug, but only
   if it's a deliberate, documented contract rather than an accident.
5. Verify ranking behaviour with a case designed to distinguish it from "no ranking at all": construct
   a query where two different signals should disagree about the top result, and confirm your ranking
   actually uses the signal you think it does. **Then prove the case can fail:** assert the "before"
   state inside the test (the chunk is NOT first without the signal) or break the rule on purpose and
   watch the test go red. My first version of this test skipped that, and passed by accident.

## Practice exercises
1. Run `GraphRetriever.retrieve("Who founded Insurellm?", k=5)` against the real graph. Which chunks
   come back, and which relationship type (`FOUNDED`) got you there?
2. Change `hops` to 1 when constructing a `GraphRetriever` and re-run "Who works on Carllm?". Does the
   employee chunk still appear? Why or why not, given his HR record mentions Carllm directly?
3. Change `hops` to 3 and try a query naming "Insurellm" itself. How many more chunks come back
   compared to hops=2? Is the extra recall worth the extra breadth, in your judgment?
4. Write a query naming THREE known entities at once (e.g. a product, an employee, and a company).
   Does the agreement-based ranking correctly promote a chunk that all three reach at its nearest
   distance above one that only two do? Watch for entities that are two hops apart: they reach each
   other's chunks, but not at the *nearest* distance.
5. Find a real question this retriever answers badly (recall D1's limitation: try a paraphrase like
   "the auto insurance product" instead of "Carllm"). Confirm it returns `[]` and explain exactly why.
6. Add a `print` inside `_entities_by_hop` showing `within_hop` at each hop level for one query. Trace
   by hand which entities are "new" at hop 2 versus already present at hop 1.
7. Time `GraphRetriever(store)` construction versus a single `retrieve()` call. Which dominates, and
   why does that match `all_entity_names()` being called once but `search_related`/`multi_hop_search`
   being called once per matched entity, per hop?
8. Modify `_chunk_ids_for` to ALSO follow `SIGNED_BY` in a way that would double-count a chunk already
   found via `MENTIONS`. Does the `set` type in the return value protect against that, or would you
   need to change something else?
9. Design (on paper, no code) what `retrieve()` would need to change to accept an OPTIONAL label
   filter (e.g. "only look for Product entities in this query"). Which internal method's signature
   would that touch first?
10. Read `docs/19`'s `multi_hop_search` deep dive again with this step in mind. Which of ITS "what
    would break" scenarios turned out to matter for `GraphRetriever` specifically?

## Sources
- Edge et al. (2024), ["From Local to Global: A Graph RAG Approach to Query-Focused
  Summarization"](https://arxiv.org/abs/2404.16130), arXiv:2404.16130 — the paper this whole graph
  branch of the project has been building toward; multi-hop retrieval is exactly its core claim.
- Microsoft, ["GraphRAG"](https://microsoft.github.io/graphrag/) — a production reference
  implementation of entity linking + graph traversal for retrieval.
- [Neo4j Cypher manual: variable-length relationships](https://neo4j.com/docs/cypher-manual/current/patterns/reference/#variable-length-relationships) —
  the mechanism `multi_hop_search` (and therefore this retriever) is built on.
- [docs/19 graph_store_deep_dive.md](19%20graph_store_deep_dive.md) — `search_related`/`multi_hop_search`'s
  own implementation deep dive, the two primitives this step composes.
