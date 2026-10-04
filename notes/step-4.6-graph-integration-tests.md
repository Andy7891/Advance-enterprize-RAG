# Phase 4, Microstep 6 — Unit & Integration Tests for the Graph

**Date:** 2026-09-22
**Core learning:** No — this step is testing craft, not a new RAG concept. It's documented anyway
because the *decision* of what to test (and what NOT to re-test) is a real engineering skill.

## The gap-analysis that came first
The task asked for `tests/test_graph.py` with "extraction, storage, and query tests." Before writing
anything, I counted what already existed: 62 extraction tests (`tests/graph/test_extractor.py`), 75
dedup tests (`tests/graph/test_dedup.py`), 40 storage-and-query tests
(`tests/retrieval/test_graph_store.py`), and 14 integration tests on the real 223-chunk corpus
(`tests/graph/test_ingest.py`) — 191 tests total, already covering exactly the three things named.
Writing a new file that re-tested the same things the same way would have been pure duplication.

The real gap was narrower: none of those 191 tests ever ran a **query** against data the **real**
extractor produced. `test_graph_store.py`'s query tests use hand-built `GraphNode`/`GraphEdge`
records; `test_ingest.py`'s live test checks **counts** after loading, never that a question against
the result gets the right **answer**. This project already has a template for exactly this situation:
[`tests/test_retrieval.py`](../tests/test_retrieval.py), P3's own cross-component file, with the
docstring "per-component unit tests live in tests/embedding and tests/vectorstore; these check that
the components produce the right answers together." `tests/test_graph.py` is that file's graph
sibling.

## What was built
- **[`tests/test_graph.py`](../tests/test_graph.py)**, 7 tests:
  - **3 hermetic** (no database): a 3-file synthetic fixture (one product, one employee, one
    contract), in the real corpus's exact markdown shapes, run through the real `GraphExtractor` and
    `resolve_entities`/`remap_edges`. They assert the write-**plan** has the right nodes, edges and
    properties, and that this clean fixture produces zero ambiguities or conflicts.
  - **4 live** (skip if Neo4j is down): the same plan written through the real `GraphStore`, then
    queried with `search_related`/`multi_hop_search` for realistic questions — "who works on this
    product," "does the client connect to the product through its contract," "what's connected to
    this product from every direction," "does an edge's property survive the round trip."
- Every fixture entity's name starts with `M6Fixture`, so the ~9 nodes this file writes can never be
  mistaken for anything in the real corpus; cleanup deletes each one by its own exact `(label, key)`
  match, using the same node list the test wrote from — never a text scan.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Scope | new integration coverage only | duplicate unit tests into the new file | someone expecting a full unit-test home here won't find one |
| D2 Hermetic vs. live | live for storage/query; hermetic for extraction/resolution only | a fake-driver hermetic layer for storage/query | this file contributes 0 tests when no Neo4j is reachable |
| D3 Test data | a small synthetic fixture in real markdown shapes | a slice of the real corpus's own files | if the corpus format ever drifts from this fixture's shapes, the fixture won't notice |
| D4 Isolation | `M6Fixture`-prefixed names, deleted by exact identity | a `STARTS WITH` scan across the database | none really — this is strictly safer, at the cost of up to 9 delete statements instead of 1 |
| D5 Location | `tests/test_graph.py`, top-level | `tests/graph/test_integration.py` | a naming/discoverability preference only |
| D6 Assertions | 4 realistic questions | exhaustively re-verify every `GraphStore` method | narrower coverage than a full re-test, but targets exactly the identified gap |

## Verified, not assumed
```
hermetic tests : 3 passed (plan shape: entities, relationships, zero ambiguity/conflict)
live tests     : 4 passed against real Neo4j 5.26.0
cleanup        : managed node count before == after (442); 0 leftover M6Fixture nodes
full suite     : 484 passed, 57 deselected in CI mode (was 481, 53)
ruff, mypy     : clean
```
**What building this taught.** Designing the fixture surfaced a real constraint I hadn't planned for:
the extractor's own regexes require city/founder-style names to look like real capitalised proper
nouns (`[A-Z][\w.'-]*`), so a literal `__test_` prefix (the convention `test_graph_store.py` uses for
hand-built records) would have silently broken the very rules this fixture exists to exercise.
`M6Fixture` — still a normal-looking capitalised word — was the fix. Also, real names in a fixture
written to a live, shared database turned out to be unsafe for cleanup: deleting "the test's Employee
node" by name would delete the REAL corpus's node of the same name, since a fixture using real names
would `MERGE` straight into it. That's why D3 chose synthetic names in real *shapes*, not real names.

## Key concepts
🔴 = core to RAG and worth understanding deeply; 🟡 = useful engineering; ⚪ = boilerplate.
- 🟡 **Test pyramid — unit vs. integration vs. end-to-end:** a unit test checks one function; an
  integration test checks that several correct units, wired together, still do the right thing. The
  M5 `DESCRIBED_IN` bug is the proof they're not redundant: 191 unit tests passed while the composed
  system was wrong.
- 🟡 **Hermetic vs. live testing:** hermetic tests need no external service and run everywhere
  (including CI with nothing running); live tests exercise the real thing but skip themselves when
  it's unavailable. Which parts of a system CAN be hermetic depends on whether the datastore has an
  in-process mode (Qdrant does; Neo4j, in this project, does not).
- 🟡 **Fixture isolation on shared, mutable state:** when there's only one database (Neo4j Community
  Edition) and tests share it with real data, isolation has to be a *naming convention* plus
  *exact-identity cleanup*, not a fresh instance per test.
- ⚪ **Gap analysis before writing tests:** counting what already exists before adding more of it.
  Not a RAG concept, but the actual first step of this whole task.

## Honest limitations
1. **The fixture is small and clean on purpose** — one product, one employee, one contract, no
   ambiguity. It proves the WIRING works; it does not stress-test resolve_entities' merge logic
   (that's `test_dedup.py`'s 75 tests) or the extractor's edge cases (that's `test_extractor.py`'s 62).
2. **`multi_hop_search`'s "walked through, not returned" claim for nameless nodes isn't exercised
   here** — this fixture's `Contract` node has a `title` property, so it IS returned by
   `multi_hop_search`, just not asserted on. A node truly without `name` or `title` isn't part of this
   fixture.
3. **Cleanup assumes the test doesn't crash mid-write.** A hard interrupt before the `finally` block
   runs would leave `M6Fixture`-named nodes in the graph until the next successful run (which
   overwrites them harmlessly via `MERGE`, then still cleans them up).

## How this connects
**Previous:** P4-M2 built the model; P4-M3 the store; P4-M4 dedup; P4-M4b extraction; P4-M5 the
ingestion job that composes all four for the real corpus. **This step:** proves that composition
answers real questions, on a small, disposable fixture, without needing the full 223-chunk corpus or
risking the real corpus's data. **Next:** query-time entity linking and fusion with the P3 vector
results — the first real *user* of `search_related`/`multi_hop_search` outside a test.

## How to do this yourself
1. Before writing a test file a task asks for, count what tests already exist for the same components
   (`grep -c "^def test_" <file>` per file). If coverage already exists, find the SPECIFIC gap instead
   of duplicating it.
2. Ask what your datastore's hermetic options actually are. If it has no in-process mode, say so
   explicitly and split hermetic (whatever needs no database) from live (whatever does), rather than
   faking the database and pretending the result carries the same guarantee.
3. When a live test needs to write to a shared, non-disposable resource, design the fixture's naming
   BEFORE writing any assertions: every entity your test creates must be identifiable by something
   nothing real would ever produce.
4. Prefer deleting by the exact identity you wrote, over deleting by a pattern scan — a scan can
   over-match (or, as I found by hand, crash on a property type you didn't expect).
5. Pick a small number of assertions that answer the actual question ("does this compose correctly")
   rather than exhaustively re-verifying components already tested elsewhere.

## Practice exercises
1. Run `pytest tests/test_graph.py -v -m "not live"` and then `-m live`. Which 3 run in the first
   command and which 4 in the second, and why exactly those?
2. Break the fixture on purpose: change `CONTRACT_TITLE`'s product name so it no longer matches
   `PRODUCT` exactly. Which hermetic test fails, and what does its message say was expected?
3. Add a fourth signatory to the contract fixture, on the SAME side as an existing one (same
   organisation). Does `test_extraction_and_resolution_produce_the_expected_relationships` still pass
   unmodified? Why or why not?
4. Temporarily rename `FIXTURE` to `"__test_"` (an underscore prefix) and run the hermetic tests.
   Which one fails, and which extractor regex is responsible?
5. Delete the `finally:` block's cleanup code in a scratch copy and run the live tests. Confirm the
   fixture nodes are still in Neo4j afterwards with a Cypher query, then write them back out by hand.
6. Add a live test asserting `search_related(CLIENT, label="Company")` returns the `WITH_CLIENT` edge.
   Does it also return anything you didn't expect? Why (hint: which other edges touch `Company`)?
7. Change the fixture's `CITY` to remove the comma (`"M6Fixtureville M6Fixture State"`). Does
   `_split_location` in `dedup.py` still parse it the same way? What changes?
8. Time the hermetic tests versus the live ones. What accounts for the difference, given the fixture
   is only 3 chunks either way?
9. Read `tests/test_retrieval.py`'s docstring and compare it, sentence by sentence, with this file's.
   Which sentences are near-identical, and why is that a GOOD sign rather than lazy copying?
10. Suppose Neo4j gained a `:memory:`-style embedded mode tomorrow. Which of this file's 4 live tests
    could move to the hermetic section unchanged, and which would still need to stay live (hint: does
    any of them test something specific to the REAL server, like the schema constraint)?

## Sources
- [`tests/test_retrieval.py`](../tests/test_retrieval.py) — the P3 cross-component file this one
  mirrors, including its own hermetic/live split and cleanup-in-`finally` convention.
- [`tests/retrieval/test_graph_store.py`](../tests/retrieval/test_graph_store.py) — the existing
  fake-driver + live convention for `GraphStore`, reused here for the live fixture's cleanup style.
- Fowler, M., ["TestPyramid"](https://martinfowler.com/bliki/TestPyramid.html) — unit vs. integration
  vs. end-to-end, and why more of the pyramid should be at the bottom, not the top.
- Cohn, M., *Succeeding with Agile*, ch. 15 — the original test-pyramid diagram this file's split
  follows in spirit (many fast/cheap checks, few slow/real ones).
