# Phase 4, Microstep 3 — Graph records and the Neo4j store

**Date:** 2026-09-20

## A required step was missing
The task listed `GraphNode` / `GraphEdge` schemas and a `GraphExtractor` as inputs. A repo search found neither: what
had been built as "P4-M2" was the graph *model* and its constraints, and no record types existed. That was checked
before any code, and the order was agreed as **records, then the store, then the extractor** (D-2026-09-20-10). This
note covers the first two; the extractor is the next step and has its own decisions to make.

## What was built
- **`src/graph/records.py`:** `NodeRef` (label + key), `GraphNode` (+ properties), `GraphEdge` (type, source, target,
  properties) and `InvalidRecord`. Each is checked against the P4-M2 model when it is built and is read-only afterwards.
- **`src/retrieval/graph_store.py`:** `GraphStore(driver, *, database, verify_schema)`:
  - `upsert_node(node)` and `upsert_edge(edge)` (raises `MissingEndpoint` if a node is absent);
  - `search_related(name, *, label, include_provenance, limit)` returns `List[GraphEdge]`;
  - `multi_hop_search(start, hops, *, label, limit)` returns `List[str]`.
- **Tests:** `tests/graph/test_records.py` (44) and `tests/retrieval/test_graph_store.py` (54: 36 hermetic with a fake
  driver, 18 live on the real labels).
- **Docs:** decisions D-2026-09-20-10 to -17, `docs/architecture.md` (Graph store), README row, learning log.
- No new dependencies, and nothing is loaded: the graph still has its 8 constraints and no data.

## Your choices (my recommendations) and how each one fails
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| Order | records → store → extractor | extractor first; all at once | the record shape may need a tweak once real extraction shows what it needs |
| Record shape | one generic set, validated against the model at construction | a class per label; validate at write time | a buggy extractor raises mid-run instead of writing bad data, so it must catch per record |
| Edge endpoints | `NodeRef` (label + key) | whole nodes; names only | needs the key in hand; a bare name is ambiguous (Robert Chen) |
| Immutability | read-only views over copies; `None` and padded keys refused | mutable dicts; silently drop or trim | shallow: a list inside a record is still editable |
| Location, driver | `src/retrieval/graph_store.py`; injected driver; schema checked at construction | `src/graph/store.py`; own driver; auto-apply | a store in `retrieval/` sits oddly beside future retrievers |
| Writes | `execute_write`, `MERGE` + `SET +=`, strict endpoints | `session.run`; `SET =`; `MERGE`d endpoints | nodes must be loaded before edges |
| Lookup | exact `name`, all namesakes, domain edges, a limit | fuzzy; required label; provenance by default | `carllm` misses `Carllm`; Contracts cannot be found by name |
| Multi-hop | variable-length Cypher, at most 3 hops, `List[str]` | APOC expander; Python BFS | enumerates paths, so a dense hub graph is slow; namesakes collapse to one string |

## Verified, not assumed
```
tests, records      : 44 passed
tests, store        : 54 passed (36 hermetic, 18 live; none skipped)
mutation check      : 29 mutants -> all killed in one run against the final tests. 9 in the records (key part
                      missing, padded key, blank key, None, mixed list, property repeating the key, undeclared edge
                      property, wrong endpoint label, unfrozen mapping); 20 in the store (SET = for +=, MERGEd
                      endpoints, provenance as domain, arrow-only search, no DISTINCT, exactly-N hops, start
                      returned, longest route, null names, no hop cap, schema unchecked, name untrimmed, writes in a
                      read transaction, order not total, provenance walked, label filter ignored, LIMIT dropped,
                      node or edge properties never written, missing endpoint unreported)
full suite          : 332 passed, 11 skipped (Qdrant and Ollama were stopped; each skip names them)
CI mode             : 291 passed, 52 deselected (was 211 and 34)
ruff, format, mypy  : clean
real graph after    : 8 constraints, 0 nodes, no leftovers (the live tests sweep their own __test_ data)
```
**What the mutation check taught.** Two comments in the code described guards that no test covered: `DISTINCT` (a
company and a city that share a name would return their edge twice) and excluding the start from its own results
(a founder, their company and its head-office city form a triangle). Both now have a test. A third mutant, removing the
tie-breaker from `ORDER BY`, **survived**: my ordering test used three employees and they came back alphabetical by luck.
It now uses five, inserted in an order that is neither alphabetical nor its reverse.

**Three small mistakes, all caught before they mattered.** I derived the expected order in one multi-hop test by hand and
had `Sam` in the wrong place (the contract's title sorts before it); the fake driver lacked `consume()`, so four tests
failed while the store was right; and an error message said an endpoint "vanished" when the race is the opposite.

## Key concepts
- 🔴 **Multi-hop retrieval:** following relationships several steps from an entity to find things no single passage mentions.
- 🔴 **Entity ambiguity:** one name can belong to several nodes (`Employee` and `Contact` Robert Chen), so a lookup by name
  must return all of them or take a label; guessing one is a silent wrong answer.
- 🔴 **Domain vs provenance edges:** provenance edges record where a fact was read; walking them makes co-mentioned entities look
  adjacent, so hop counts must ignore them.
- 🟡 **Upsert:** write "create it, or update it" as one idempotent operation (`MERGE` on the key, `SET +=`).
- 🟡 **Managed transaction:** the driver runs your function and retries it on transient errors, which is safe only if it is idempotent.
- 🟡 **Referential integrity:** an edge may only join nodes that exist; here the store checks it, since Community cannot.
- 🟡 **Shallow immutability:** `frozen=True` stops re-assigning a field but not editing the mutable object inside it.
- ⚪ **Fake driver / injected dependency:** the store is handed its driver, so a test can hand it a recording stand-in.

Interview markers (`# INTERVIEW:` in the code): `_frozen` in records (shallow immutability), and in the store `_lookup` (why labels
are spliced but values are parameters, and why one `MATCH` per label), `upsert_node` (`MERGE` + `SET +=`), `upsert_edge` (strict
endpoints), `multi_hop_search` (path enumeration and its exit) and `_write` (retries and idempotency).

## Honest limitations
1. **Only synthetic data has touched this.** The tests prove the queries and rules; that the graph answers the Carllm and CEO questions
   that motivated it is untested until an extractor loads the corpus.
2. **`multi_hop_search` returns names, so namesakes collapse** into one string, and a node with no `name` (a Contract) appears only if
   it has a `title`.
3. **Hubs blur "N hops".** Two hops from an employee reaches everyone in the same city (a `Location` is a hub) and, through
   `HAS_OFFICE`, the company. Nothing excludes them yet.
4. **Path enumeration.** The 3-hop cap and domain-only edges keep it small; APOC's path expander is the exit.
5. **Exact, case-sensitive names.** Entity linking (a later step) has to normalise a question's words before calling.
6. **One transaction per upsert, no batch.** Fine for hundreds of nodes; the extractor may need a batch method.
7. **Node properties are free-form** (the model declares only keys); edge properties are closed to the declared ones.
8. **Reads raise on a node outside the model** rather than skip it, so a hand-made node in the graph breaks retrieval loudly.
9. **Neo4j may log "property key does not exist" for `title`** until a Contract with a title has been written.
10. **Nothing removes stale nodes.** Deleting what a re-run no longer produces belongs to the graph ingestion job.

## How this connects
**Previous:** P4-M1 gave a verified Neo4j; P4-M2 gave the model and its 8 constraints. **This step:** the types that carry data into that
model, and the read/write layer over it. **Next:** the extractor, which turns the 76 documents into these records (deterministic parsers for
the templated categories, checked against the P4-M2 audit numbers, since no chat model is available yet); then a graph ingestion job that
also writes `Chunk`/`MENTIONS`/`DESCRIBED_IN` and removes stale nodes; then entity linking (question → entity), fusion with vector results,
and an evaluation set (which needs the CEO ground truth: HR says Avery Lancaster, contracts say Jennifer Rodriguez).

## How to do this yourself
1. Write down what one record is: its identity (from your model's keys) and its properties. Decide where a bad record should fail.
2. In the Neo4j Browser, write each operation by hand first: `MERGE (n:L {key: 'k'}) SET n += {p: 1}`; an edge with `MATCH ... MATCH ... MERGE`;
   a `[*1..3]` pattern. Run `PROFILE` on the lookups.
3. Wrap each in a function using `session.execute_write` / `execute_read`; copy rows out inside the function.
4. Splice only what comes from the model (labels, relationship types, a hop bound) and pass every value as a parameter.
5. Test twice: a fake that records the query and parameters, and live tests on values with a prefix you sweep before and after.
6. Break the code on purpose (change `+=` to `=`, `MATCH` to `MERGE`, drop `DISTINCT`) and check a test fails each time.
7. Read: Neo4j Cypher manual (`MERGE`, variable-length patterns), Python driver manual (transactions), APOC docs (path expander),
   Edge et al. 2024 on GraphRAG (arXiv:2404.16130).

## Practice exercises
1. In the Browser, run the multi-hop query by hand on a three-node chain, then add a fourth node and a second route to it. Count the paths.
2. `PROFILE` `MATCH (e:Company {name: 'x'}) RETURN e` and the same without the label. Which operator changes?
3. Change `SET n += $props` to `SET n = $props` in a scratch copy and read which test fails and why.
4. Build a `GraphEdge` between the wrong labels and read the message. Is it enough to find the mistake without the traceback?
5. Try `node.properties["x"] = 1` and `node.properties["list"].append(1)` on a record. Which works, and why?
6. Write the Cypher for "which clients have a Carllm contract?" against this model, then check it agrees with `multi_hop_search("Carllm", 2)`.
7. Add a `Company` and a `Location` with the same name joined by `HAS_OFFICE` and call `search_related`. Why is the edge returned once?
8. Make a triangle (founder, company, head-office city) and call `multi_hop_search` with 3 hops. Why is the founder not in the result?
9. Replace the tie-breaker in `ORDER BY` with `rel_type` only and run the tests. Which one catches it, and would three employees have?
10. Sketch how you would exclude hubs from `multi_hop_search`, and say what real connection that could hide.
