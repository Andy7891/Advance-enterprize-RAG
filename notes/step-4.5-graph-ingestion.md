# Phase 4, Microstep 5 — Ingest all chunks into the knowledge graph

**Date:** 2026-09-22

## What was built
- **`src/graph/ingest.py`** (standard library plus the project's own graph modules; no new extraction or merging logic):
  - `run_ingestion(driver, *, chunks_dir, log_path, database, rebuild) -> IngestionReport`: the whole job, one call;
  - stages, in order: `preflight -> load -> extract -> resolve -> validate -> write -> verify`, each timed;
  - `python -m src.graph.ingest [--rebuild]`: the CLI, mirroring `src/vectorstore/ingest.py`'s shape.
- **Tests:** `tests/graph/test_ingest.py`, 14 tests: 13 hermetic (the pure planning functions, plus a fake driver that checks what preflight sends, never what Neo4j does) and 1 live, against the real 223-chunk corpus and a real Neo4j, skipped when either is unavailable.
- **Docs:** decisions D-2026-09-22-01 to -09, `docs/architecture.md` (Graph ingestion), README row, learning log rows.
- Composition, not new logic: extraction is [P4-M4b's `GraphExtractor`](step-4.4b-graph-extractor.md), merging is [P4-M4's `resolve_entities`/`remap_edges`](step-4.4-entity-deduplication.md), writing is [P4-M3's `GraphStore.upsert_node`/`upsert_edge`](../docs/19%20graph_store_deep_dive.md), used exactly as built. This step adds the order to run them in, the re-run policy, and the report.

What each stage does:

| Stage | Does | Aborts if |
|---|---|---|
| preflight | Neo4j reachable? schema in force? count nodes under the job's 8 labels | non-empty, unless `--rebuild` (which deletes exactly those 8 labels first) |
| load | `read_chunks(chunks_dir)` | the directory has none |
| extract | `GraphExtractor.from_chunks(chunks).extract_all(chunks)`, then a write-plan built from every `Extraction` | never (an unknown category only warns) |
| resolve | one corpus-wide `resolve_entities` call, roster + client names pinned as `known_names`; `remap_edges` (and the same remap for `DESCRIBED_IN`/`MENTIONS`) | never |
| validate | every edge's two endpoints checked against the exact set of nodes about to be written | any edge names one that will not be written — nothing is written |
| write | `store.upsert_node` for every node, then `store.upsert_edge` for every edge | a `MissingEndpoint` validate should already have ruled out |
| verify | counts by label and by type, read back from Neo4j, compared with what Python wrote | they differ |

## Your choices (accepted earlier) and how each one failed or could fail
| Decision | Chosen | Rejected | Failure mode (observed where marked) |
|---|---|---|---|
| D6 Resolution scope | one corpus-wide `resolve_entities` call | resolve per category, merge alias maps after | two calls could pick two different canonical spellings for a name only one batch saw |
| D7 Provenance | `Document`/`Chunk`, `HAS_CHUNK` for all 223, `DESCRIBED_IN` from the entity's own category, `MENTIONS` from `Extraction.mentions` | `DESCRIBED_IN` from any chunk the node appeared in | **observed:** 111 edges instead of 72 — a contract chunk's `Product` reference and an About page's `Employee` reference are not those entities' own pages |
| D8 Re-run policy | refuse a non-empty graph unless `--rebuild`, which wipes exactly the 8 labels | merge into whatever is there; a separate `--wipe` flag | a stale node from a retired extractor rule would never be removed by MERGE alone |
| D9 Validation | every edge endpoint checked before any write; on any problem, write nothing | let `MissingEndpoint` catch it mid-run | a partial graph: some entities merged, some edges dangling, discovered only after Neo4j already has some of it |
| D10 Store reuse | `GraphStore.upsert_node`/`upsert_edge`, unchanged | a new batched `UNWIND` upsert | 442 records one at a time took 16s locally — acceptable now, not forever |
| D11 Logging | `data/graph_ingestion_log.json`, overwritten; stage seconds, counts written vs read back, ambiguities, conflicts, the CEO note | trust "no exception" | an upsert can legitimately collapse two writes into fewer rows than Python counted; only a fresh read catches that |
| D12 The CEO fact | both loaded as written; the contradiction recorded, not resolved | pick one as correct | which is "right" is a retrieval-time or a human question, not this job's |

## Verified, not assumed
```
tests, ingestion    : 14 passed (13 hermetic, 1 live; live skips if Neo4j is down or data/chunks is empty)
real run            : 223 chunks from 76 documents, 0.06s extraction, 0.02s resolve, 16s write, 0.05s verify
nodes written       : Company 33, Contact 29, Contract 32, Employee 32, Location 9, Product 8, Document 76, Chunk 223
edges written       : HAS_CHUNK 223, DESCRIBED_IN 72, FOUNDED 1, HAS_OFFICE 5, WITH_CLIENT 32, FOR_PRODUCT 32,
                      SIGNED_BY 46, BASED_IN 32, WORKS_ON 6, MENTIONS 207 (656 total, matching the audit exactly)
ambiguities         : 0        conflicts: 0        extraction warnings: 0
verified            : True (written counts equal a fresh read of Neo4j, by label and by type)
rebuild idempotency : run twice with --rebuild, identical counts both times
refusal             : a run without --rebuild against the now non-empty graph raises IngestionAborted, no writes sent
CEO note            : HR records Avery Lancaster as 'Co-Founder & Chief Executive Officer (CEO)', while Jennifer
                      Rodriguez signs 7 contracts as 'Chief Executive Officer'. Both are loaded as written.
full suite          : 481 passed, 53 deselected in CI mode (not counting the live test just run)
ruff, format, mypy  : the three CI commands are clean
```

**What the checks taught.** One real bug, found only by running the job against a real database, not by any hermetic test:

**"Created a node" is not "described its own page."** The extractor emits a `Product` node wherever one is a `FOR_PRODUCT` edge's target — every
contract chunk that names it — and an `Employee` node wherever one is a `FOUNDED` source — the About page too, when it says who co-founded the
company. Neither of those chunks is that entity's own page. The first version of `DESCRIBED_IN` used the node's label alone (`Product`, `Contract`,
`Employee`) as the test for "is this chunk a home page," and it wrote 111 edges on the real corpus instead of 72: `Homellm` was "described in" 7
documents and Avery Lancaster in 2. Every hermetic test still passed, because the fake driver only checks what a query SAYS, not what MERGE actually
DOES to it — a test built the same way the bug was written would have made the same mistake. The fix checks the chunk's own `category` against the
entity's home category (`Product` only from a `products` chunk, `Employee` only from an `employees` chunk, `Contract` only from a `contracts` chunk),
and it is now a regression test (`test_described_in_ignores_a_product_named_only_as_a_contract_target`) as well as the reason the live test exists at
all: only reading a real Neo4j back caught the wrong number.

## Key concepts
🔴 = core to RAG and worth being able to explain; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Orchestration versus logic:** this module contains almost no domain logic (extraction, merging and writing are all reused unchanged); what it
  owns is sequencing, idempotency and the re-run policy — a distinction worth naming, because it is what makes each earlier step testable alone.
- 🔴 **Idempotent ingestion / upsert:** writing with `MERGE` (find-or-create) means re-running the SAME input twice is safe; the harder question — a
  CHANGED input, or a rule that stopped firing — is what `--rebuild` exists to answer, because MERGE alone never removes anything.
- 🔴 **Validate before you write:** checking every edge's endpoints against the planned node set, before any write, turns a plan bug into a caught
  error instead of a partially-written graph.
- 🔴 **Verification reads the system of record, not the log of intentions.** "The loop finished with no exception" is not evidence the graph is
  correct; a fresh count from Neo4j, compared with what Python thinks it sent, is.
- 🟡 **Provenance modelling:** `Document`/`Chunk`/`HAS_CHUNK`/`DESCRIBED_IN`/`MENTIONS` is what lets a later query start from an entity and land back
  on the exact chunk of text that mentioned it — the bridge this whole knowledge graph exists to build to the vector store.
- 🟡 **A single point of truth for "known" names:** `known_names` for `resolve_entities` is read from the plan's own extracted nodes, not re-derived
  by a second, independent parse — one rule set decides what a client or an employee is called, not two that could drift apart.
- 🟡 **Staged pipelines with a timed, typed report:** the `preflight -> load -> extract -> resolve -> validate -> write -> verify` shape and the
  `IngestionReport` dataclass mirror `src/vectorstore/ingest.py` on purpose — one pattern, two ingestion jobs.
- ⚪ **`_StageTimer`, `write_log`, `cli_args`/`run_from_args`:** plumbing copied from the P3-M5 vector ingestion job almost unchanged.

Interview markers (`# INTERVIEW:` in `src/graph/ingest.py`): the preflight ordering comment (cheap checks before the expensive stage), the
`GraphStore.upsert_edge` MATCH-not-MERGE note, and the `_verify` comment on why "no exception" is not proof of correctness.

## Honest limitations
1. **`--rebuild` owns the WHOLE graph, not a scoped subset.** There is no way to reload one document's worth of data without wiping and reloading
   everything; a larger corpus would need an incremental design this job does not have.
2. **Verification compares counts, not identities.** Two mismatched pairs of the same size (72 written as X, 72 read back as Y where some X's are
   missing and some Y's are extra but the totals happen to match) would pass `_verify` undetected; only the exact-count mismatch case is caught, the
   same limitation `src/vectorstore/ingest.py`'s own `_verify` chose to accept for its point counts before it added a set comparison.
3. **The CEO contradiction is detected by one hand-written rule (`_ceo_note`), not a general mechanism.** A different pair of conflicting facts
   elsewhere in the corpus would go unnoticed unless someone wrote a rule for it too.
4. **Write speed is one record at a time.** 16 seconds for 442 nodes/edges is fine here; nothing about this design scales past a few thousand.
5. **It is only as current as `data/chunks/` and the live Neo4j it is pointed at.** Nothing here watches for either changing after the run.

## How this connects
**Previous:** P4-M2 fixed what may exist; P4-M3 wrote it; P4-M4 merged spellings; P4-M4b turned chunk text into records. **This step:** is the first
time all four meet — chunks in, a queryable graph out. **Next:** query-time entity linking (recognising an entity name in a user's question),
fusion with the P3 vector results (a graph hit gives entities and edges; `MENTIONS` gives the `chunk_id` back to Qdrant for the actual passage), and
an evaluation set that has to decide what to do with the CEO contradiction this step recorded but did not resolve.

## How to do this yourself
1. Before writing an orchestrator, ask what is ALREADY built and testable alone (here: extraction, merging, writing) — the orchestrator's job is
   sequencing and policy, not re-implementing any of them.
2. List every failure mode a re-run could hit (empty target, partly-loaded target, changed input, changed rules) and decide, in writing, what
   happens for each BEFORE coding the happy path.
3. Write the validate-before-write step even when the pieces underneath it look like they can't fail; a plan bug is not the same class of bug as a
   bad input, and it is much cheaper to catch before any write than after.
4. Never trust "it ran without an error" as proof of correctness for anything that writes to a system with its own merge/upsert semantics you did
   not write yourself — read the result back and compare it against what you intended to send.
5. Run the real thing against a real database at least once before trusting a hermetic test suite for an ingestion job; a fake driver can only
   check what you asked it to check, and it was a fake that could not have caught this step's own bug.
6. Read: the Neo4j Python driver manual on `MERGE` and transaction functions; Fowler, *Idempotent Receiver* pattern; Kleppmann, *Designing
   Data-Intensive Applications*, ch. 11 (stream processing / re-processing) for the general re-run-safety problem this job is a small instance of.

## Practice exercises
1. Run `python -m src.graph.ingest` against a fresh Neo4j and read the printed summary. Which stage took the longest, and why would extraction be
   fast while write is slow?
2. Delete `src/graph/ingest.py`'s `_HOME_CATEGORY` check (make `DESCRIBED_IN` fire from any node with a home-page label) and re-run the live test.
   Which assertion fails, and by how much?
3. Open `data/graph_ingestion_log.json` after a run. Find the CEO note, the per-stage seconds, and the counts by label. Which fields would you add
   if you had to debug a run that failed at `write`?
4. Run the job twice in a row without `--rebuild`. Read the `IngestionAborted` message. What exact number does it report, and where does that
   number come from in the code?
5. Change `_ceo_note` to also report a THIRD kind of conflicting fact you invent (make one up in a scratch copy of the corpus) and add a test.
6. Trace one `MENTIONS` edge from the log's counts back to an actual Cypher row: open Neo4j Browser and run
   `MATCH (c:Chunk)-[:MENTIONS]->(e) RETURN c.chunk_id, labels(e), coalesce(e.name, e.doc_id) LIMIT 10`. Pick one row and find the exact sentence in
   `data/chunks/` that caused it.
7. Break `_validate` on purpose (comment out the target check) and construct an edge whose target does not exist. What happens now instead of a
   clean abort?
8. Remove one of the 8 constraints with `python -m src.graph.schema --check` (read only, then look at what dropping one manually would let happen)
   and reason about which of this job's writes would silently duplicate a node if the constraint were gone.
9. Time the `write` stage with 10 chunks versus all 223 (use `--chunks-dir` pointed at a small copy). Does write time scale linearly with node/edge
   count, or is there a fixed cost per run?
10. Ask: if `resolve_entities` merged two clients that should have stayed separate, at which stage of this job would that first become visible —
    `resolve`, `validate`, `write`, or `verify` — and which of the four would actually catch it?

## Sources
- Neo4j Python driver manual: sessions, transaction functions (`execute_write`/`execute_read`), and `MERGE` semantics under concurrent writers.
- Fowler, M., "Idempotent Receiver" pattern (enterprise integration patterns) — the general shape of D8's re-run policy.
- Kleppmann, M., *Designing Data-Intensive Applications*, chapter 11 — re-processing and the idempotence of a data pipeline, the same problem this
  job solves at a much smaller scale.
- [src/vectorstore/ingest.py](../src/vectorstore/ingest.py) — the P3-M5 job this one's stage/report/CLI shape was copied from.
- Edge et al. (2024), "From Local to Global: A Graph RAG Approach to Query-Focused Summarization", arXiv:2404.16130 — what a graph built this way is
  eventually for.
