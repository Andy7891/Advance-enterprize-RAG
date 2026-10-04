# 21 — Graph Ingestion: Deep Dive (P4-M5)

> Every output block in this document was produced on 2026-09-22 by running the code: against a
> live Neo4j **5.26.0 community** + APOC (`docker compose up -d neo4j`), the real `data/chunks/`
> (223 chunks, 76 documents), and the real `data/graph_ingestion_log.json` from the P4-M5 run.
> All scripts are in
> [`practice codes/phase 4/m5_graph_ingestion_deep_dive/`](../practice%20codes/phase%204/m5_graph_ingestion_deep_dive/).
> Run any of them from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 4/m5_graph_ingestion_deep_dive/d1_described_in_home_category.py"
> ```
>
> Scripts marked **(live)** need `docker compose up -d neo4j`. They write and delete exactly one
> throwaway `__test_`-prefixed node; none of them touch the real corpus data already loaded.

---

## 0. Plain-language summary

By the end of P4-M4b you could turn *one chunk* of text into graph records (nodes and edges), with
rules, in memory, touching no database. P4-M5 answers a different question: **how do you turn 223
separate, small answers into ONE graph that's actually correct, and how do you know it's correct
enough to build a query engine on top of?**

That's harder than "loop over the chunks and call `store.upsert_node`". Every chunk's extraction
names entities that OTHER chunks also name (the word "Insurellm" appears in 40 chunks); some of
those references belong to the entity's own page and some don't; a re-run must not corrupt what's
already there; and once you've written 656 edges, "no exception was raised" is not the same thing
as "the graph is right". `src/graph/ingest.py` turns loading into a **procedure with a policy, a
validation gate, and a receipt**:

```
python -m src.graph.ingest [--rebuild]
   │
   ├─ preflight   Neo4j up? schema in force? graph already owned by this job? (refuse unless --rebuild)  (C1)
   ├─ load        read all 223 chunks from data/chunks
   ├─ extract     GraphExtractor over every chunk -> a write-plan (nodes, edges, DESCRIBED_IN, MENTIONS) (C3)
   ├─ resolve     ONE corpus-wide resolve_entities call; remap every edge through the alias map
   ├─ validate    every edge's two endpoints checked against the exact node set about to be written      (C2)
   ├─ write       store.upsert_node for every node, then store.upsert_edge for every edge
   └─ verify      read Neo4j back; compare against what Python thinks it wrote                            (C4)
   │
   └─ always: data/graph_ingestion_log.json (status, per-stage seconds, counts, the CEO note)             (C5)
```

A bug in the `extract` stage — caught only by running this against a real database, never by a
hermetic test — is the spine of this document (C3).

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | Idempotent ingestion & an explicit re-run policy (`MERGE` + `--rebuild`) | 🔴 **Core RAG / MLOps** |
| C2 | Validate the whole plan before any write (fail-fast on a graph, not a value) | 🟡 Engineering (interview-heavy) |
| C3 | "A node was created" is not "this is its home page" — reference vs. authorship | 🔴 **Core RAG** |
| C4 | Verification reads the system of record, not the write loop's own tally | 🔴 **Core RAG / MLOps** |
| C5 | Recording a contradiction instead of resolving it | 🔴 **Core RAG** (interview-heavy) |

> **Open question** (not answered here): `_verify` compares COUNTS by label and by type. Two runs
> could write the same *number* of `DESCRIBED_IN` edges while pointing at different entities and
> still pass. C4's exercises push on exactly this gap.

---

## C1 — Idempotent ingestion & an explicit re-run policy

### Do this first
Before reading on: run `python -m src.graph.ingest` twice in a row against a graph that already
has data (don't pass `--rebuild` the second time). **Predict** what happens before you run it, then
run it and compare. (Or run
[`d0_merge_is_not_a_rerun_policy.py`](../practice%20codes/phase%204/m5_graph_ingestion_deep_dive/d0_merge_is_not_a_rerun_policy.py)
first for a pure-Python, no-database version of the same idea.)
> *Hint:* look at [`src/graph/ingest.py:119-127`](../src/graph/ingest.py#L119-L127) — what does the
> job count before it writes anything?

### 1. Definition + analogy
**What it is.** An operation is **idempotent** if doing it twice has the same effect as doing it
once. Neo4j's `MERGE` (used by `GraphStore.upsert_node`/`upsert_edge`, built in P4-M3) is what makes
a SINGLE write idempotent: `MERGE (n:Company {name: 'Insurellm'})` finds the existing node instead
of creating a second one. But idempotent writes are not the same thing as a **safe re-run policy**
for the whole job. `MERGE` only ever *adds*; it never removes a node that an earlier run wrote and a
later run's rules no longer produce. If you delete a product page from `data/`, or fix a bug in an
extractor rule so it stops emitting some node, the OLD node from the last run is never cleaned up by
MERGE alone — it just sits there, silently wrong.

That's why `run_ingestion` adds a second layer on top of MERGE: it counts how many nodes the graph
already holds under its 8 managed labels ([`_managed_node_count`](../src/graph/ingest.py#L401-L406)),
and if that count is non-zero, it **refuses to write anything** unless you pass `--rebuild`
([`ingest.py:119-127`](../src/graph/ingest.py#L119-L127)). `--rebuild` then `DETACH DELETE`s exactly
those 8 labels ([`_wipe_managed_labels`](../src/graph/ingest.py#L409-L412)) before loading — a full,
explicit, deliberate replace, never a silent partial merge into unknown prior state.

**Key terms.**
- **Idempotent operation:** same effect no matter how many times you run it.
- **`MERGE`:** Cypher's find-or-create. Safe for re-running the SAME input; blind to input that
  should now be GONE.
- **Ownership:** "this job owns exactly these 8 labels" is what makes a full wipe safe — a database
  could hold other labels from someone else's experiment, and `--rebuild` never touches them.

**Analogy.** Repainting your bedroom wall the same colour twice looks identical either time — that's
idempotent. But if you'd also *taken down a poster* between coats, repainting doesn't put the poster
back or notice it's gone; the wall just silently doesn't match what you meant it to look like
anymore. Getting back to "what I actually want" needs someone to say "strip it and start over," on
purpose — that's `--rebuild`.

### For a 14-year-old
Imagine a classroom noticeboard that a robot keeps up to date. Every morning it walks in with today's
list of notices and pins up any that aren't there yet. If yesterday's notice about the school play is
still on the list today, pinning it again does nothing new — it's already there. That's the safe,
repeatable part.

But say someone TAKES a notice off the list entirely (the play got cancelled). The robot never looks
at what's ALREADY on the board and asks "should this still be here?" — it only ever adds. So the old
notice stays up forever, quietly wrong, unless a teacher tells the robot "clear the whole board and
start fresh from today's list" — that's `--rebuild`. And you'd want the robot to REFUSE to just start
pinning things on a board that already has notices from an unknown source, until someone gives that
explicit order, because it can't tell "yesterday's notices, correctly still true" from "some other
class's board it wandered into by mistake."

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the classroom noticeboard | the Neo4j graph database |
| a notice pinned to the board | one node or edge under this job's 8 managed labels |
| the robot pinning notices each morning | `run_ingestion`'s write stage, calling `store.upsert_node`/`upsert_edge` (`MERGE`) |
| "pinning an already-there notice does nothing new" | `MERGE`'s idempotency — writing the same node twice leaves one node |
| a notice taken off the list, but the robot never notices | a document deleted from `data/`, or a rule that no longer emits a node — `MERGE` never deletes anything on its own |
| the teacher saying "clear the board, start fresh" | the `--rebuild` flag |
| the robot refusing to pin onto a board of unknown origin | `_managed_node_count` + the `IngestionAborted` refusal ([`ingest.py:119-127`](../src/graph/ingest.py#L119-L127)) |

### 2. Why here specifically
This corpus is small (223 chunks) and static during a single learning session, so the "input changed,
old data must go" problem is easy to trigger by hand: I ran the job, then deliberately ran it again
without `--rebuild` to prove the refusal, then with `--rebuild` twice to prove it reproduces the
identical graph. A production version of this job, ingesting a corpus that changes daily, would need
this policy question answered on day one, not discovered the first time someone deletes a document.

### 3. Minimal code
`d0_merge_is_not_a_rerun_policy.py` — a dict standing in for Neo4j, showing exactly where `MERGE`
stops being enough on its own:
```python
# Minimal demo of C1: Neo4j's MERGE makes ONE write idempotent, but it is not a re-run
# POLICY for a whole job -- it only ever adds, so it can never notice that something the
# last run wrote should now be gone. Pure Python, no database: a dict stands in for Neo4j.
graph = {}  # stand-in for "what Neo4j holds": key -> properties, exactly like a real MERGE target


def merge(label, key, props):
    # This is exactly what GraphStore.upsert_node's Cypher does: MERGE (n:label {key})
    # SET n += props -- find the node by its key, or create it, then add the properties.
    identity = (label, key)  # a node's real-world identity: its label plus its unique key
    existing = graph.get(identity, {})  # look up whatever is already stored under this identity
    graph[identity] = {**existing, **props}  # keep old fields, let new ones override -- SET n +=


print("Run 1: load two products")  # the FIRST ingestion run, as if from a fresh corpus
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # first product, first time seen
merge("Product", "Rellm", {"tagline": "Real Estate AI"})  # second product, first time seen
print("  graph now:", sorted(graph))  # show every identity currently stored

print("\nRun 2: re-run the SAME two products (nothing changed in the source)")  # idempotency test
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # same key, same properties again
merge("Product", "Rellm", {"tagline": "Real Estate AI"})  # same key, same properties again
print("  graph now:", sorted(graph))  # still exactly two entries: MERGE did not duplicate anything

print("\nRun 3: the corpus changed -- Rellm's product page was DELETED")  # simulate a real edit
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # Carllm is still on disk, so it's re-merged
# Notice: there is no line here for Rellm. A real ingestion job would simply stop calling
# merge() for a product that is no longer on disk -- MERGE never gets a chance to delete it.
print("  graph now:", sorted(graph))  # Rellm is STILL there: MERGE alone can only add, never remove

print("\nThe fix our job uses: an explicit --rebuild that wipes ownership labels FIRST")
managed_labels = {"Product"}  # the job's own record of which labels it is responsible for


def rebuild(graph, managed_labels):
    # This mirrors _wipe_managed_labels: DETACH DELETE every node under labels this job owns,
    # before loading anything, so a deleted source document cannot leave a stale node behind.
    for identity in list(graph):  # iterate over a COPY of the keys, since we're deleting while looping
        if identity[0] in managed_labels:  # only touch labels this job owns, never a stranger's data
            del graph[identity]  # the DETACH DELETE equivalent: remove the stale entry entirely


rebuild(graph, managed_labels)  # wipe every Product node before reloading today's corpus
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # reload only what is ACTUALLY on disk now
print("  graph after --rebuild + reload:", sorted(graph))  # Rellm is finally, correctly, gone
```
Real output:
```
Run 1: load two products
  graph now: [('Product', 'Carllm'), ('Product', 'Rellm')]

Run 2: re-run the SAME two products (nothing changed in the source)
  graph now: [('Product', 'Carllm'), ('Product', 'Rellm')]

Run 3: the corpus changed -- Rellm's product page was DELETED
  graph now: [('Product', 'Carllm'), ('Product', 'Rellm')]

The fix our job uses: an explicit --rebuild that wipes ownership labels FIRST
  graph after --rebuild + reload: [('Product', 'Carllm')]
```
Run 3 is the whole lesson in one line of output: Rellm is gone from the source, but `merge()` alone
leaves it in the graph. Only the explicit `rebuild()` step removes it.

### The exact code I wrote (`src/graph/ingest.py`)
```python
# lines 119-127
existing = _managed_node_count(driver, database)      # count nodes under this job's 8 labels, right now
if existing and not rebuild:                          # non-empty AND the caller didn't ask to replace it
    raise IngestionAborted(                            # 🎯 INTERVIEW: refuse rather than silently merge
        f"the graph already holds {existing} node(s) under this job's labels; "
        "rerun with --rebuild to delete and reload them, or point at an empty "
        "database"
    )
if existing:                                          # non-empty AND --rebuild was passed
    _wipe_managed_labels(driver, database)             # DETACH DELETE exactly the 8 owned labels

# lines 401-406
def _managed_node_count(driver: GraphDriver, database: str) -> int:
    with driver.session(database=database) as session:            # one short-lived session
        return session.run(                                       # a single count query
            "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN count(n) AS c",
            labels=list(MANAGED_LABELS),                           # the 8 labels, never a stray one
        ).single()["c"]                                            # the count, and nothing else

# lines 409-412
def _wipe_managed_labels(driver: GraphDriver, database: str) -> None:
    with driver.session(database=database) as session:            # one session for the whole wipe
        for label in MANAGED_LABELS:                               # exactly the 8 labels this job owns
            session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()  # 🎯 INTERVIEW: DETACH removes edges too
```
**🎯 Interview-relevant lines:** the `raise IngestionAborted` (refuse instead of silently merging into
unknown prior state) and `DETACH DELETE` inside `_wipe_managed_labels` (one statement removes a node
AND every relationship touching it — the reason the wipe needs no separate edge-cleanup step).

### 4. What breaks if this changes
- **Remove the `existing` check, keep only `MERGE`:** running the job twice, once as an experiment
  and once for real, quietly doubles nothing (MERGE prevents duplicates) but also means whatever
  garbage the experiment wrote is now permanently mixed into the "real" graph, forever indistinguishable
  from real data.
- **Make `--rebuild` the default:** every accidental re-run silently destroys and rebuilds the whole
  graph. Fine for a demo corpus; a genuine data-loss risk at real scale.
- **Widen the wipe to `MATCH (n) DETACH DELETE n`** (all labels, not just the 8 managed ones): a
  teammate's unrelated experiment data disappears the next time someone runs this job with
  `--rebuild`.

### [Interview relevance: high — "how do you make an ingestion job safe to re-run?" is a standard data-engineering question]

### Canonical docs
- Neo4j Cypher manual: [`MERGE`](https://neo4j.com/docs/cypher-manual/current/clauses/merge/)
- Fowler, M., **Idempotent Receiver** (enterprise integration patterns)
- [`src/vectorstore/ingest.py`](../src/vectorstore/ingest.py)'s `count_incompatible`/`--rebuild` — the
  same idea, applied to embeddings instead of a graph (P3-M5, doc 15 C5)

### Micro-exercises (absolute-beginner, learn by doing)
1. Run `python -m src.graph.ingest` against an already-loaded graph, no flag. Read the error message.
   Which exact number does it report, and where in the code does that number come from?
   *Hint:* [`ingest.py:119-125`](../src/graph/ingest.py#L119-L125); the number comes from a single
   Cypher `count(n)`.
2. Run it again with `--rebuild`. Time how long `preflight` takes this time versus a run against an
   empty graph. Where did the extra time go? *Hint:* look at `stage_seconds["preflight"]` in the log,
   and count how many `DETACH DELETE` statements `_wipe_managed_labels` issues.
3. Open Neo4j Browser (`http://localhost:7474`) and manually create one extra node with a label NOT
   in the model, e.g. `CREATE (:Scratch {note: 'mine'})`. Run the job with `--rebuild`. Is your scratch
   node still there afterwards? Why?
4. In a scratch copy of `ingest.py`, change `_managed_node_count`'s query to count only `Company`
   nodes instead of all 8 labels. Load the graph, then delete every `Company` node by hand but leave
   the rest. Does the job now let you write without `--rebuild`? What state does the graph end up in?
5. Predict, then check: if you run the job with `--rebuild` on an EMPTY graph (nothing to wipe), does
   `_wipe_managed_labels` still get called? Read [`ingest.py:126-127`](../src/graph/ingest.py#L126-L127)
   before you check.

**How it works internally.** `MERGE (n:Company {name: $name}) SET n += $props` is two operations
fused by Cypher: an index seek on the uniqueness constraint (from P4-M2) to look for a matching node,
and a `CREATE` only if none was found — both happen inside one atomic operation, so two concurrent
writers can't both "not find" the node and both create one. `DETACH DELETE` removes a node AND every
relationship touching it in one statement, which is why the wipe doesn't need a separate step for
edges.

### How to validate this yourself
```bash
# 1. See the refusal
.venv/Scripts/python -m src.graph.ingest
# 2. See the rebuild wipe + reload, then confirm counts are identical to before
.venv/Scripts/python -m src.graph.ingest --rebuild
```
Compare `nodes_written_by_label` in the two runs' log files — they should be byte-identical maps.

### How to visualize this
Open Neo4j Browser and run `MATCH (n) RETURN labels(n)[0] AS label, count(n) ORDER BY label` before
and after a `--rebuild` run. A bar chart of the "before" counts, then the identical bars "after,"
*is* the idempotency proof, on screen.

**You should now be able to explain:** why `MERGE` alone is not a re-run policy, what exact condition
triggers `IngestionAborted`, and what `--rebuild` does and does not touch.

---

## C2 — Validate the whole plan before any write

### Do this first
Run [`d2_validate_before_write.py`](../practice%20codes/phase%204/m5_graph_ingestion_deep_dive/d2_validate_before_write.py)
(live). **Before** you run it: predict which of the two approaches (validate first, or write straight
through) leaves a partial write behind when it fails, and why.

### 1. Definition + analogy
**What it is.** `_validate` ([`ingest.py:340-347`](../src/graph/ingest.py#L340-L347)) checks, for
every edge about to be written, that BOTH its endpoints appear in the exact set of nodes about to be
written — entirely in Python, entirely in memory, before a single network call is made. If anything
is missing, `run_ingestion` raises `ValidationFailed` and stops; nothing has been sent to Neo4j yet.

The alternative already existed and already worked, technically: `GraphStore.upsert_edge` (P4-M3)
`MATCH`es both endpoints rather than `MERGE`ing them, and raises `MissingEndpoint` with a clear
message if one is absent. So why add a SECOND check that does, in a sense, the same job? Because of
**when** each one finds the problem. `MissingEndpoint` fires only once the write loop reaches the
bad edge — by which point every node and edge BEFORE it in the list is already committed to Neo4j.
`_validate` finds the exact same problem before the write stage even starts.

**Key terms.**
- **Fail fast:** find a problem at the cheapest, earliest point possible (see doc 15, C2, for the
  same idea applied to preflight checks).
- **Partial write:** some of a multi-step change happened, some didn't, and nothing undoes the part
  that did.
- **Precondition vs. runtime guard:** `_validate` checks a precondition of the WHOLE plan;
  `MissingEndpoint` is a runtime guard on ONE write.

**Analogy.** A furniture kit with a page of assembly instructions and a bag of screws. You could
just start building and discover screw #14 is missing when you get there (four shelves already
built, held together, now stuck) — or you could count every screw against the instructions BEFORE
picking up a screwdriver. Same missing screw, wildly different cost of finding out.

### For a 14-year-old
You're building a giant tower out of numbered building blocks from a set. Each block's sticker says
which OTHER block it sits on top of. You could just start stacking, block by block, and find out
block #47 needs a block #12 that was never in the box — except by then you've already glued blocks
1 through 46 together, and now you've got a half-tower with a mistake baked in partway up. OR: before
gluing anything, you could lay out every block and check every sticker against the pile — "does #12
actually exist in this box?" — and find the SAME missing block in two seconds, before any glue is
used. Nothing's stuck together wrong; you just... don't build it yet, and go find the missing piece.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| a building block | a `GraphNode` or `GraphEdge` in the write plan |
| a block's sticker naming another block | an edge's `source`/`target` `NodeRef` |
| gluing a block into the tower | `store.upsert_node`/`upsert_edge` actually writing to Neo4j |
| discovering block #12 is missing WHILE gluing block #47 | `MissingEndpoint`, raised mid-`_write`, after earlier nodes/edges are already committed |
| the half-tower, stuck together with a mistake in it | a partial write left in Neo4j: some entities merged, some edges dangling |
| laying out every block and checking every sticker before gluing anything | `_validate(nodes_to_write, edges_to_write)`, run entirely in memory before `stage("write")` |
| finding the missing block in two seconds, no glue used | `_validate` returning its list of problems in ~0.02ms, with zero network calls |

### 2. Why here specifically
`_validate` is genuinely redundant with `MissingEndpoint` on a CORRECT plan — it only earns its
keep when the plan has a bug (in `_plan_writes`, in `resolve_entities`, in `remap_edges`). That's
exactly the situation that matters most: this job's own code has a bug, not the input data. A
partial graph — some entities merged, some edges dangling — is a much worse debugging starting point
than a clean refusal that lists every problem at once.

### 3. Minimal code
`d2_validate_before_write.py` (live) — the exact same broken plan, checked two ways:
```python
# Same broken plan, two ways to find out: `_validate` catches it before any write (fast,
# in-process); writing it straight to Neo4j catches it only when the store reaches the bad
# edge, after everything before it is already committed. Live: writes and deletes exactly
# one throwaway __test_ node, nothing from the real corpus.
import time  # for timing the in-process check, in milliseconds

from src.graph.ingest import _validate  # the real precondition check this project ships
from src.graph.records import GraphEdge, GraphNode, NodeRef  # the same record types the real job uses
from src.graph.setup import build_driver  # opens a real driver to the configured Neo4j
from src.retrieval.graph_store import GraphStore, MissingEndpoint  # P4-M3's store and its runtime guard

COMPANY = GraphNode("Company", {"name": "__test_dd21_co"})  # a throwaway node, clearly test-only by name
LOCATION_THAT_WILL_NOT_BE_WRITTEN = NodeRef("Location", {"name": "__test_dd21_nowhere"})  # never in the plan
BAD_EDGE = GraphEdge(  # an edge whose target is deliberately missing from the plan
    "HAS_OFFICE", COMPANY.ref, LOCATION_THAT_WILL_NOT_BE_WRITTEN, {"headquarters": False}
)

plan_nodes = [COMPANY]  # the plan's node list: only the Company, on purpose
plan_edges = [BAD_EDGE]  # the plan's edge list: one edge whose target is not above

print("1. _validate(), in-process, no network call:")
started = time.perf_counter()  # start a clock, to show how cheap this check is
problems = _validate(plan_nodes, plan_edges)  # the exact function run_ingestion calls before any write
elapsed = time.perf_counter() - started  # stop the clock
print(f"   found {len(problems)} problem(s) in {elapsed * 1000:.2f}ms: {problems[0]}")  # the refusal, fast
print("   -- nothing was sent to Neo4j; the plan is simply refused.")

print("\n2. writing the SAME plan straight to Neo4j, no validate step first:")
with build_driver() as driver:  # open a real connection to the configured Neo4j
    store = GraphStore(driver, verify_schema=True)  # the real store, schema-checked like production
    store.upsert_node(COMPANY)  # this one succeeds and is now committed to the real database
    print("   upsert_node(Company) -> committed")
    try:
        store.upsert_edge(BAD_EDGE)  # this MATCHes both endpoints; Location was never written
    except MissingEndpoint as exc:  # the store's own runtime guard fires here, not before
        print(f"   upsert_edge(HAS_OFFICE) -> MissingEndpoint: {exc}")
        print("   -- the Company node is STILL THERE: a partial write, caught late instead of never made.")

    # Clean up the one throwaway node so this script leaves no trace on the real graph.
    with driver.session(database="neo4j") as session:  # a plain session, bypassing GraphStore
        session.run(  # delete exactly the one node this script created, and nothing else
            "MATCH (n:Company {name: $name}) DETACH DELETE n", name=COMPANY.key["name"]
        ).consume()
    print("   cleanup: throwaway __test_ node deleted")
```
Real output:
```
1. _validate(), in-process, no network call:
   found 1 problem(s) in 0.02ms: HAS_OFFICE: target Location(name='__test_dd21_nowhere') is not among the nodes to be written
   -- nothing was sent to Neo4j; the plan is simply refused.

2. writing the SAME plan straight to Neo4j, no validate step first:
   upsert_node(Company) -> committed
   upsert_edge(HAS_OFFICE) -> MissingEndpoint: cannot write HAS_OFFICE: Location(name='__test_dd21_nowhere') not found; upsert the node first
   -- the Company node is STILL THERE: a partial write, caught late instead of never made.
   cleanup: throwaway __test_ node deleted
```
0.02 milliseconds versus a real committed (if throwaway) node in Neo4j — same bad edge, radically
different cost of finding out.

### The exact code I wrote (`src/graph/ingest.py`)
```python
# lines 336-347
def _identity(ref: NodeRef) -> _Identity:
    return ref.label, tuple(sorted(ref.key.items()))          # a hashable (label, sorted key) tuple


def _validate(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> list[str]:
    known = {_identity(node.ref) for node in nodes}           # every identity about to be WRITTEN, as a set
    problems = []                                             # collect every problem, don't stop at the first
    for edge in edges:                                        # check EVERY edge in the plan
        for ref, role in ((edge.source, "source"), (edge.target, "target")):  # both ends of each edge
            if _identity(ref) not in known:                   # 🎯 INTERVIEW: an O(1) set lookup, not a scan
                problems.append(f"{edge.type}: {role} {ref} is not among the nodes to be written")
    return problems                                           # empty list means the plan is safe to write

# lines 166-172 (run_ingestion's validate stage)
problems = _validate(nodes_to_write, edges_to_write)          # run the check against THIS run's exact plan
if problems:                                                  # any problem at all -- even just one
    raise ValidationFailed(                                   # 🎯 INTERVIEW: raised BEFORE the write stage runs
        f"{len(problems)} edge(s) name an endpoint that will not be written: "
        + "; ".join(problems[:20])                            # show up to 20, so one huge failure is still readable
        + (" ..." if len(problems) > 20 else "")
    )
```
**🎯 Interview-relevant lines:** the set-membership check in `_validate` (O(1) per edge instead of an
O(n) scan, which is what makes checking hundreds of edges cheap) and the fact that `ValidationFailed`
is raised *before* `stage("write")` ever runs — the whole point of putting validation in its own
stage, ahead of the one that mutates the database.

### 4. What breaks if this changes
- **Remove `_validate`, keep only `MissingEndpoint`:** a bug in `remap_edges` that drops a node the
  plan still references an edge to would only be discovered mid-write, after however many nodes and
  edges came before it in the list are already committed — and the next run would then need
  `--rebuild` to get back to a clean state, because the graph is now in a state no single run ever
  intended to produce.
- **Validate only the FIRST bad edge, then stop:** you'd fix one problem, re-run, hit the next one,
  fix that, re-run again — instead of seeing every problem in one pass (`_validate` collects all of
  them: [`ingest.py:342-346`](../src/graph/ingest.py#L342-L346)).

### [Interview relevance: high — "fail fast" and "validate before you mutate state" are core reliability-engineering habits, asked about directly in systems-design interviews]

### Canonical docs
- Shore, J., ["Fail Fast"](https://www.martinfowler.com/ieeeSoftware/failFast.pdf), IEEE Software 2004
- [`src/retrieval/graph_store.py`](../src/retrieval/graph_store.py)'s `MissingEndpoint` (P4-M3) — the
  runtime guard this validation step exists in front of

### Micro-exercises
1. Run d2 and note the two timings: how long did `_validate` take versus how long did it take to
   reach `MissingEndpoint` on the live write? *Hint:* `_validate` never touches the network.
2. In a scratch script, build a plan with TWO bad edges (two different missing targets). Call
   `_validate` and count how many problems it returns. Does it stop at the first one?
3. Read [`ingest.py:166-172`](../src/graph/ingest.py#L166-L172). What happens to the error message if
   there are 25 problems instead of 2? *Hint:* look for the `[:20]` slice.
4. Comment out the `validate` stage entirely in a scratch copy of `ingest.py` and re-run d2's Part 2
   scenario through the full `run_ingestion` pipeline (not just the store directly). Which stage does
   the failure now surface in?
5. `_validate` builds a Python `set` of every node identity, then checks every edge against it. For
   443 nodes and 656 edges, roughly how many identity comparisons does that cost, and why is a `set`
   the right structure instead of a list?

**How it works internally.** `_identity(ref)` ([`ingest.py:336-337`](../src/graph/ingest.py#L336-L337))
turns a `NodeRef` into a hashable tuple `(label, sorted key items)`, so two references to "the same"
node — built at different points in the code, from different chunks — compare equal even though
they're different Python objects. That's what lets a `set` membership check work at all: without a
stable, hashable identity, you'd need an O(n) scan per edge instead of an O(1) lookup.

### How to validate this yourself
The live comparison IS the validation: [`d2_validate_before_write.py`](../practice%20codes/phase%204/m5_graph_ingestion_deep_dive/d2_validate_before_write.py)'s
output shows `_validate` finding the problem in 0.02ms with zero network calls, versus the live store
call that leaves a real (if throwaway) node committed before it fails.

### How to visualize this
In Neo4j Browser, after intentionally breaking a plan and letting it fail mid-write (as in d2's part
2, before cleanup), run `MATCH (n) WHERE n.name STARTS WITH '__test_' RETURN n` — you'll see the one
orphaned `Company` node with no edges, sitting there, proof of exactly the kind of half-write
`_validate` exists to prevent in the real run.

**You should now be able to explain:** the difference between a precondition check and a runtime
guard, and why checking a whole plan up front costs almost nothing compared to what a partial write
would cost to notice and repair.

---

## C3 — "A node was created" is not "this is its home page"

### Do this first
Run [`d1_described_in_home_category.py`](../practice%20codes/phase%204/m5_graph_ingestion_deep_dive/d1_described_in_home_category.py).
**Before** running it, predict: does `DESCRIBED_IN` fire once per entity, or could the same `Product`
end up "described in" more than one document? Look at
[`src/graph/extractor.py:296-300`](../src/graph/extractor.py#L296-L300) — a contract chunk creates a
`Product` node too, as the target of `FOR_PRODUCT`. Now predict what a rule of "emit `DESCRIBED_IN`
whenever a home-page-labelled node appears" would do with that.

### 1. Definition + analogy
**What it is.** The graph model says `DESCRIBED_IN` connects a `Product`, `Contract` or `Employee` to
**the Document that is its own page** — a product's product page, a contract's own file, an
employee's own HR record (`schema.py`'s `RelationshipType("DESCRIBED_IN", ...)`). But the *extractor*
creates a node of one of those labels any time the RULES need to reference one, which is not the
same thing as "this chunk is that entity's home page." Two examples, both real, both in this corpus:

- A contract's title, "Contract with Acme for Carllm", makes the extractor emit a `Product` node
  named `Carllm` as the target of the `FOR_PRODUCT` edge
  ([`extractor.py:296-300`](../src/graph/extractor.py#L296-L300)). That happens on the CONTRACT's
  chunk, in the `contracts` category — not on Carllm's own product page.
- An About page saying "Insurellm was founded by Avery Lancaster" makes the extractor emit an
  `Employee` node for Avery Lancaster as the source of `FOUNDED`
  ([`extractor.py:236-243`](../src/graph/extractor.py#L236-L243)). That happens on the COMPANY page,
  in the `company` category — not on Avery Lancaster's own HR record.

The first version of `_plan_writes` wrote `DESCRIBED_IN` for any chunk that produced a node with one
of the three home-page labels, full stop. On the real corpus that wrote **111** `DESCRIBED_IN`
edges. Running the job against real Neo4j and comparing against the audit's expectation (72) is what
caught it — not any hermetic test, because a hermetic test built the same way the code was written
makes the same mistake the code did. The fix, `_HOME_CATEGORY`
([`ingest.py:207-213`](../src/graph/ingest.py#L207-L213)), checks the CHUNK's own `category` against
the entity's home category, not just the node's label:

```python
_HOME_CATEGORY = {"Product": "products", "Contract": "contracts", "Employee": "employees"}
...
if _HOME_CATEGORY.get(node.label) == chunk.category:
    described_in[...] = GraphEdge("DESCRIBED_IN", node.ref, document)
```

**Key terms.**
- **Reference vs. authorship.** A chunk can NAME an entity (a reference) without being ABOUT that
  entity (authorship). Only authorship should produce `DESCRIBED_IN`.
- **A node's identity vs. the context it was built in.** `GraphNode("Product", {"name": "Carllm"})`
  looks identical whether it came from Carllm's own page or from a contract that merely licenses
  Carllm — the fix has to look at the SURROUNDING context (the chunk), not the node itself.

**Analogy.** If a newspaper article about a football match mentions a player's name, that article is
not the player's Wikipedia page. Both "contain" the player's name; only one is ABOUT the player.
Confusing "this text mentions X" with "this text is X's own record" is exactly the bug here, at the
scale of a whole knowledge graph.

### For a 14-year-old
Imagine a filing cabinet where every folder is supposed to be ONE person's own file — their actual
birth certificate, their actual report card. Now imagine a rule that says "if a person's name is
WRITTEN ANYWHERE in a folder, file a copy of their name-tag as belonging to that folder." A totally
unrelated folder — say, a school newsletter that happens to mention "congratulations to Alex on
winning the science fair" — would now ALSO count as one of Alex's own folders, even though it's the
newsletter's folder, not Alex's. If seven different newsletters all mention the school's most popular
product, that product would end up with SEVEN "home folders" instead of the one page that's actually
about it. The fix is simple once you see it: only count a name-tag as "this is their own folder" when
you're actually standing in THAT PERSON's folder, not just any folder that happens to say their name.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the filing cabinet | `data/chunks/` — the whole corpus of documents |
| one folder | one document (a `Chunk`'s `chunk.category` and `chunk.doc_id`) |
| a person's OWN folder | the entity's true home page — its product page, its own contract file, its own HR record |
| the name-tag rule ("written anywhere = belongs here") | the FIRST, buggy `DESCRIBED_IN` rule: label alone, no category check |
| the newsletter mentioning Alex, filed as "Alex's folder" | a contract chunk naming a `Product` (`FOR_PRODUCT`'s target), or an About page naming an `Employee` (`FOUNDED`'s source) |
| a product with SEVEN "home folders" | Carllm/Rellm/Homellm each wrongly `DESCRIBED_IN` 7 documents (measured, real corpus) |
| "only count it when you're standing in THEIR folder" | the fix: `_HOME_CATEGORY.get(node.label) == chunk.category` ([`ingest.py:230`](../src/graph/ingest.py#L230)) |

### 2. Why here specifically
`DESCRIBED_IN` is the edge a future query-time step would follow to answer "show me Carllm's own
product page" — if it points at the wrong documents (or too many), that query returns garbage
silently, with no error anywhere. This is exactly the kind of bug that "the code runs, the tests
pass, the numbers even look plausible" hides: 111 is not an absurd number for 72 entities across 76
documents; it just happens to be wrong.

### 3. Minimal code
`d1_described_in_home_category.py` — the naive rule and the fixed rule, run side by side on the real
223-chunk corpus:
```python
# The real bug this step found: DESCRIBED_IN must check the chunk's own CATEGORY, not just
# the node's LABEL. Runs on the real 223-chunk corpus, no database needed (pure Python).
from src.graph.extractor import GraphExtractor  # the P4-M4b rule-based extractor, unmodified
from src.graph.ingest import _HOME_CATEGORY, _plan_writes  # the real constant and function this bug lived in
from src.ingestion.pipeline import read_chunks  # reads the 223 real chunks written by P2

chunks = read_chunks()  # every chunk from data/chunks/*.json, in the job's real input order
extractor = GraphExtractor.from_chunks(chunks)  # builds the closed lists (products/roster/cities) from the corpus itself
extractions = extractor.extract_all(chunks)  # runs the extractor over every chunk, with the lookahead


def entity_id(node):
    # A hashable identity for a GraphNode: its label plus its sorted key items, so two
    # references to "the same" node compare equal even if they're different Python objects.
    return node.label, tuple(sorted(node.key.items()))


def naive_described_in(chunks, extractions):
    # The FIRST version of this rule: "the node's label is one DESCRIBED_IN allows as a
    # source", with no check on which chunk created it. This is the version that shipped
    # with the bug.
    home_labels = set(_HOME_CATEGORY)  # {"Product", "Contract", "Employee"} -- the 3 labels DESCRIBED_IN allows
    seen = {}  # every (entity, document) pair the naive rule would turn into an edge
    for chunk, extraction in zip(chunks, extractions, strict=True):  # walk chunk and its extraction together
        for node in extraction.nodes:  # every node THIS chunk's extraction produced
            if node.label in home_labels:  # the naive check: label alone, nothing about the chunk's category
                seen[(entity_id(node), chunk.doc_id)] = True  # record: this chunk "described" this entity
    return len(seen)  # total DESCRIBED_IN edges the naive rule would write


def fixed_described_in(chunks, extractions):
    # The FIXED version: the real _plan_writes function, which checks chunk.category too.
    return len(_plan_writes(chunks, extractions).described_in)  # ask the real code for the real count


naive = naive_described_in(chunks, extractions)  # run the naive (buggy) version
fixed = fixed_described_in(chunks, extractions)  # run the fixed (shipped) version
print(f"naive  (label only)          : {naive} DESCRIBED_IN edges")  # what the first version wrote
print(f"fixed  (label AND category)  : {fixed} DESCRIBED_IN edges")  # what actually ships today
print(f"difference                   : {naive - fixed} edges that were never really a home page")
```
Real output:
```
naive  (label only)          : 111 DESCRIBED_IN edges
fixed  (label AND category)  : 72 DESCRIBED_IN edges
difference                   : 39 edges that were never really a home page
```
(The full script also prints the per-entity breakdown shown under "How to validate this yourself"
below.)

### The exact code I wrote (`src/graph/ingest.py`)
```python
# lines 207-213
# Which data/ category is an entity's OWN page, for each label DESCRIBED_IN allows as a
# source (schema.py: Product, Contract, Employee -- a Company or Location has no single home
# page). Keyed on category, not just label: a contract chunk also creates a Product node as
# the FOR_PRODUCT target and an About-page chunk creates an Employee node as a FOUNDED
# source, and neither of those is that entity's own page -- only a chunk of the matching
# category is.
_HOME_CATEGORY = {"Product": "products", "Contract": "contracts", "Employee": "employees"}

# lines 225-232 (inside _plan_writes's per-chunk loop)
for node in extraction.nodes:                                      # every node this chunk's extraction produced
    key = (node.label, tuple(sorted(node.key.items())))            # this node's identity
    earlier = domain_nodes.get(key)                                # has an earlier chunk already produced it?
    props = {**(dict(earlier.properties) if earlier else {}), **node.properties}  # merge properties, latest wins
    domain_nodes[key] = GraphNode(node.label, node.key, props)     # one entry per identity, however many chunks named it
    if _HOME_CATEGORY.get(node.label) == chunk.category:           # 🎯 INTERVIEW: THE FIX -- category, not just label
        described_key = (*key, chunk.doc_id)                       # dedupe key: (entity identity, this document)
        described_in[described_key] = GraphEdge("DESCRIBED_IN", node.ref, document)  # one edge per (entity, doc)
```
**🎯 Interview-relevant line:** `_HOME_CATEGORY.get(node.label) == chunk.category` — the ENTIRE fix is
one boolean condition. Everything else in the loop (building the node, merging properties, dedup by
`(entity, document)`) was already correct; only this one check was missing, and it's the kind of bug
that produces a plausible-looking wrong number instead of a crash. Being able to explain *why* a
one-line fix like this needed a live database to find, not a hermetic test, is the real interview
story here.

### 4. What breaks if this changes
- **Revert to label-only matching:** `DESCRIBED_IN` over-counts by 39 edges on this corpus alone
  (measured, see below), and would silently over-count differently on any other corpus, in a way that
  degrades exactly the query this edge exists to answer.
- **Make the category check TOO strict** (e.g. require `chunk_index == 0` as well as category): the
  live corpus test would start failing, because a Contract's `DESCRIBED_IN` is legitimately written
  from a LATER chunk too, when the signature block is what creates the Contract node
  ([`extractor.py:312-315`](../src/graph/extractor.py#L312-L315)) — the fix has to collapse duplicates
  by (entity, document) across chunks of the SAME document, which `_plan_writes`'s dict keying already
  does (see `described_key = (*key, chunk.doc_id)` at
  [`ingest.py:230-232`](../src/graph/ingest.py#L230-L232)).

### [Interview relevance: high — "reference extraction" vs. "entity's canonical record" is a classic knowledge-graph correctness pitfall, and this specific bug — caught only against real data — is a good story for "tell me about a bug you found"]

### Canonical docs
- [Neo4j: modelling provenance / source relationships](https://neo4j.com/developer/guide-data-modeling/)
- Edge et al. (2024), ["From Local to Global: A Graph RAG Approach..."](https://arxiv.org/abs/2404.16130),
  arXiv:2404.16130 — a knowledge graph's value depends on entities pointing at the RIGHT source text

### Micro-exercises
1. Run d1. Read the printed table of entities the naive rule over-counted. Which label is affected
   the most, and can you explain WHY from the extractor's rules (`extractor.py`'s `_contract` method)
   without looking at the answer first?
2. Open `data/chunks/contracts__Contract with Acme...` — wait, there's no such real file; instead pick
   any real contract chunk file under `data/chunks/contracts__*.md_0.json` and find the line that
   names a product. Which extractor rule and which line number produces that `Product` node?
   *Hint:* `extractor.py`'s `_contract` method, the `FOR_PRODUCT` edge.
3. Would the SAME bug class affect `MENTIONS` edges? Read
   [`extractor.py:358-374`](../src/graph/extractor.py#L358-L374) (`_mentions`) and decide: is a
   mention supposed to be restricted to an entity's own page, or is "any chunk that names it" exactly
   the right rule for `MENTIONS`? Why is the answer different from `DESCRIBED_IN`?
4. Add a NEW rule to a scratch copy of the extractor that creates a `Location` node as a side effect
   on some unrelated category of chunk. Does `_HOME_CATEGORY` need to change to accommodate it? Why
   or why not — check the schema's `DESCRIBED_IN` `sources` tuple.
5. Modify `_HOME_CATEGORY` in a scratch copy to `{"Product": "products"}` only (drop Contract and
   Employee). Run the hermetic tests
   (`tests/graph/test_ingest.py::test_described_in_ignores_an_employee_named_only_as_a_founder`).
   Which one fails, and what does the failure message tell you was expected versus produced?

**How it works internally.** `_plan_writes` iterates chunks in the SAME loop that builds every other
part of the write plan ([`ingest.py:223-255`](../src/graph/ingest.py#L223-L255)); the `category` it
checks is `chunk.category`, a field already present on every `DocumentChunk` from P2 (the top-level
`data/` subfolder). No extra data is fetched or computed — the fix uses information that was already
sitting right there in the loop variable, just never checked.

### How to validate this yourself
```bash
.venv/Scripts/python "practice codes/phase 4/m5_graph_ingestion_deep_dive/d1_described_in_home_category.py"
```
Real output:
```
naive  (label only)          : 111 DESCRIBED_IN edges
fixed  (label AND category)  : 72 DESCRIBED_IN edges
difference                   : 39 edges that were never really a home page

entities the naive rule described in MORE THAN ONE document (9 of them):
  Product    Rellm                naive=7 fixed=1
  Product    Carllm               naive=7 fixed=1
  Product    Homellm              naive=7 fixed=1
  Product    Healthllm            naive=5 fixed=1
  Product    Bizllm               naive=5 fixed=1
  Product    Markellm             naive=5 fixed=1
  Product    Lifellm              naive=5 fixed=1
  Product    Claimllm             naive=5 fixed=1
  Employee   Avery Lancaster      naive=2 fixed=1
```
72 matches the independently-known audit expectation (32 contracts + 32 employees + 8 products);
111 does not — that mismatch is what a live run against real Neo4j surfaces and a hermetic test,
built from the same assumptions as the bug, does not.

### How to visualize this
In Neo4j Browser: `MATCH (p:Product {name: 'Carllm'})-[:DESCRIBED_IN]->(d:Document) RETURN p, d` —
with the fix, exactly one `Document` node comes back. Before the fix, this query returned seven,
one per contract that licenses Carllm, which you can reproduce by reverting `_HOME_CATEGORY`'s check
to label-only in a scratch copy and re-running `--rebuild`.

**You should now be able to explain:** the difference between an entity being NAMED in a chunk and a
chunk being ABOUT that entity, why this bug was invisible to every hermetic test, and why running the
real pipeline against a real database at least once is not optional.

---

## C4 — Verification reads the system of record, not the write loop

### Do this first
Run [`d3_verify_reads_the_database.py`](../practice%20codes/phase%204/m5_graph_ingestion_deep_dive/d3_verify_reads_the_database.py).
Before running it: predict whether "every `store.upsert_node()` call returned without raising an
exception" is enough to prove the graph holds exactly what you intended to write.

### 1. Definition + analogy
**What it is.** `_write` ([`ingest.py:353-373`](../src/graph/ingest.py#L353-L373)) tallies as it
goes: one write call, one increment to `report.nodes_written_by_label[node.label]`. That tally is
Python's OPINION of what it sent. `_verify` ([`ingest.py:376-398`](../src/graph/ingest.py#L376-L398))
then asks Neo4j directly — a fresh `MATCH (n:{label}) RETURN count(n)` per label, per type — and
compares the two. If they disagree, it raises `VerificationFailed`, even though every individual
write call along the way succeeded with no exception.

Why would they ever disagree, if every call succeeded? Because `MERGE` can legitimately collapse two
DIFFERENT write calls into ONE stored node, if their keys happen to match — which is exactly what
you WANT when it's intentional (the whole reason `MERGE` exists), and exactly what you want to be
TOLD about when it's a bug (two entities that should have stayed separate, colliding because
`resolve_entities` or `remap_edges` made a mistake). "No exception" cannot distinguish those two
cases; only counting what's actually there, afterwards, can.

**Key terms.**
- **System of record:** the place that holds the actual, authoritative state — here, Neo4j itself,
  not Python's in-memory bookkeeping about what it MEANT to send.
- **Write-then-verify:** doing the write, then independently checking the result, rather than trusting
  the absence of an error as proof of correctness.

**Analogy.** You mail 10 letters and get no "delivery failed" notices back. That is NOT the same as
confirming 10 letters arrived — maybe two went to the same mailbox because you copied an address
wrong. Calling the recipient and counting what they actually received is the only way to know.

### For a 14-year-old
You're doing a scavenger hunt where you're supposed to hide 5 different prizes in 5 different spots
in the garden, and you tick off each one on your clipboard as you place it. At the end, your
clipboard says "5 hidden, all good!" But what if, without noticing, you hid two prizes in almost the
same spot — like under the same bush — so really there are only 4 findable hiding spots, with one
bush having two prizes stacked and one spot empty? Your CLIPBOARD would still proudly say "5 done,"
because from your clipboard's point of view, you did place 5 things. The only way to actually know is
to go back afterwards and COUNT the hiding spots for real, instead of trusting your own checklist.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| one prize | one node or edge in the write plan |
| hiding a prize | one `store.upsert_node`/`upsert_edge` call inside `_write` |
| ticking it off on the clipboard | `report.nodes_written_by_label[node.label] += 1` — Python's own tally of CALLS made |
| "clipboard says 5, all good!" | `_write` finishing with no exception raised |
| two prizes stacked under the same bush | two write calls whose keys collide, so `MERGE` stores them as ONE node |
| the garden really only having 4 findable spots | `db_count` — what `_counts_by_label`/`_counts_by_type` actually finds in Neo4j |
| going back and counting the hiding spots for real | `_verify` reading Neo4j back and comparing it against the clipboard |

### 2. Why here specifically
`resolve_entities` and `remap_edges` are the two places a genuine key collision could sneak in — they
decide which nodes and edges are "the same" and rewrite references accordingly. Both are already
well-tested in isolation (P4-M4), but `_write`/`_verify` is the last line of defense against a bug in
how THIS job composes them, and it's cheap: 18 extra Cypher queries (8 labels + 10 relationship
types) against a graph this size.

### 3. Minimal code
`d3_verify_reads_the_database.py` — a dict standing in for Neo4j's `MERGE`, showing exactly how a
key collision hides behind "every call succeeded":
```python
# Why `_verify` re-reads Neo4j instead of trusting the write loop's own tally. A tiny
# in-memory stand-in for Neo4j's MERGE (a dict keyed by node identity) shows the gap: every
# individual upsert can succeed, and the write loop's COUNT can still be wrong.
class FakeGraph:
    """MERGE semantics: writing the same key twice still leaves ONE node."""

    def __init__(self):
        self.nodes = {}  # identity -> properties, exactly what a real graph would hold

    def upsert_node(self, key, props):
        # This is MERGE ... SET n += props: find-or-create by key, then add these properties
        # to whatever is already there -- never a second node for the same key.
        self.nodes[key] = {**self.nodes.get(key, {}), **props}  # old fields kept, new ones override


def write_all(graph, planned_nodes):
    """What `_write` does: call upsert_node once per planned node, tally as it goes."""
    written = 0  # Python's own count of write CALLS, exactly like report.nodes_written_by_label
    for key, props in planned_nodes:  # walk the plan's node list, in order
        graph.upsert_node(key, props)  # send this one node to the "database"
        written += 1  # +1 per CALL made, not per distinct key that ends up stored
    return written  # this is the number _write reports as "written", before any verification


planned = [  # a small, deliberately-broken plan: two entries share one key
    (("Company", "Acme Insurance"), {"legal_name": "Acme Insurance Co."}),  # entry 1: key "Acme Insurance"
    (("Company", "Acme Insurance"), {"legal_name": "Acme Insurance Corporation"}),  # entry 2: SAME key
    (("Company", "Globex"), {}),  # entry 3: a normal, unrelated node
]

graph = FakeGraph()  # a fresh stand-in database
written_count = write_all(graph, planned)  # run the write loop exactly as _write would
db_count = len(graph.nodes)  # a FRESH read of what the "database" actually holds, like _verify does

print(f"write loop's own tally (report.nodes_written_by_label) : {written_count}")  # Python's opinion
print(f"a FRESH read of the database (what _verify actually does) : {db_count}")  # the ground truth
```
Real output:
```
write loop's own tally (report.nodes_written_by_label) : 3
a FRESH read of the database (what _verify actually does) : 2
MERGE result for the collided key: {'legal_name': 'Acme Insurance Corporation'}

_verify would raise VerificationFailed: wrote 3, database holds 2
```
Three write CALLS, two nodes actually stored. Every call returned normally; the mismatch only shows
up when something goes back and counts.

### The exact code I wrote (`src/graph/ingest.py`)
```python
# lines 362-373 (_write)
by_label, by_type = report.nodes_written_by_label, report.edges_written_by_type
for node in nodes:
    store.upsert_node(node)                                   # send it; MERGE decides create-or-find
    by_label[node.label] = by_label.get(node.label, 0) + 1    # 🎯 +1 per CALL -- not per distinct key stored
for edge in edges:
    try:
        store.upsert_edge(edge)
    except MissingEndpoint as exc:
        raise ValidationFailed(str(exc)) from exc
    by_type[edge.type] = by_type.get(edge.type, 0) + 1        # same shape: +1 per call

# lines 376-398 (_verify)
def _verify(driver: GraphDriver, database: str, report: IngestionReport) -> None:
    report.nodes_in_db_by_label = _counts_by_label(driver, database)   # 🎯 a FRESH read, not the tally above
    report.edges_in_db_by_type = _counts_by_type(driver, database)     # same, for relationship types
    node_mismatch = {
        label: (report.nodes_written_by_label.get(label, 0), report.nodes_in_db_by_label[label])
        for label in MANAGED_LABELS
        if report.nodes_written_by_label.get(label, 0) != report.nodes_in_db_by_label[label]
    }                                                                  # every label where the two disagree
    edge_mismatch = {
        name: (report.edges_written_by_type.get(name, 0), report.edges_in_db_by_type.get(name, 0))
        for name in _RELATIONSHIP_NAMES
        if report.edges_written_by_type.get(name, 0) != report.edges_in_db_by_type.get(name, 0)
    }
    if node_mismatch or edge_mismatch:                                 # 🎯 INTERVIEW: this is the whole check
        raise VerificationFailed(
            f"Neo4j does not match what was written: node counts {node_mismatch}, "
            f"edge counts {edge_mismatch}"
        )
    report.verified = True
```
**🎯 Interview-relevant lines:** `by_label[node.label] = by_label.get(node.label, 0) + 1` counts write
*calls*, and `_counts_by_label`/`_counts_by_type` inside `_verify` are a completely independent read
of Neo4j. The whole concept is that these two numbers come from two DIFFERENT sources of truth
(Python's memory vs. the database itself) — being able to say that sentence out loud is the
interview answer to "how do you know your pipeline actually did what it meant to."

### 4. What breaks if this changes
- **Remove `_verify` entirely:** a bug anywhere upstream that causes an unintended merge — two
  different clients silently becoming one `Company` node — succeeds with `status: "succeeded"` in the
  log, and nobody finds out until a much later query returns a suspiciously connected answer.
- **Compare only TOTAL counts, not per-label/per-type:** 33 `Company` + 1 extra somewhere and 1 fewer
  `Employee` elsewhere could cancel out to the same grand total and still pass — this is exactly why
  `_verify` compares per-label and per-type dictionaries, not one number (see the Open Question above:
  it's still not a full identity check, just a finer-grained count check).

### [Interview relevance: high — "how do you know your data pipeline actually did what you think it did" is asked constantly for MLOps/data-engineering roles]

### Canonical docs
- [`src/vectorstore/ingest.py`](../src/vectorstore/ingest.py)'s own `_verify` (P3-M5, doc 15 C7) — the
  same pattern, comparing exact chunk-id SETS instead of counts (a stronger check than this job's,
  see the exercises)
- Neo4j Cypher manual: [aggregating `count()`](https://neo4j.com/docs/cypher-manual/current/functions/aggregating/#functions-count)

### Micro-exercises
1. Run d3. Change the `planned` list so the two colliding entries have DIFFERENT keys (no collision).
   Does `_verify`'s logic (written count == database count) still hold?
2. Read [`ingest.py:379-388`](../src/graph/ingest.py#L379-L388). Are `node_mismatch` and
   `edge_mismatch` checked independently, or does one only run if the other is empty? What does that
   tell you about how many problems a single failed run can report at once?
3. `src/vectorstore/ingest.py`'s `_verify` compares exact ID SETS (`missing`, `unexpected`), not just
   counts (see doc 15, C7). Sketch — in words, no code needed — what a set-based `_verify` would look
   like for this graph job, and what extra bug class it would catch that a count comparison misses.
   *Hint:* the "Open question" at the top of this document is your answer key.
4. In `d3`, make `written_count == db_count` by accident (pick planned nodes with no collision) and
   confirm the script prints "counts agree; _verify would pass" — then re-introduce the collision and
   watch the message change. Time both runs; is the check itself ever the bottleneck?
5. `_counts_by_label`/`_counts_by_type` run one query PER label/type (18 total). Rewrite one of them,
   on paper, as a single Cypher query using `UNWIND` and `apoc.cypher.run` that returns all label
   counts in one round trip. What's the tradeoff against the current 8-separate-queries approach?

**How it works internally.** Each of the 8 `_counts_by_label` queries and 10 `_counts_by_type`
queries is `MATCH (n:{label}) RETURN count(n)` or `MATCH ()-[r:{type}]->() RETURN count(r)` — Neo4j
answers a bare label/type count from its label/relationship-type index almost instantly, which is why
18 extra queries added under a second to the real run's total time (`verify` took 0.05–0.53s across
my runs, see the log).

### How to validate this yourself
```bash
.venv/Scripts/python "practice codes/phase 4/m5_graph_ingestion_deep_dive/d3_verify_reads_the_database.py"
```
Real output:
```
write loop's own tally (report.nodes_written_by_label) : 3
a FRESH read of the database (what _verify actually does) : 2
MERGE result for the collided key: {'legal_name': 'Acme Insurance Corporation'}

_verify would raise VerificationFailed: wrote 3, database holds 2
```
And on the real corpus, after a clean `--rebuild` run, `report.verified == True` and every entry in
`nodes_in_db_by_label`/`edges_in_db_by_type` in `data/graph_ingestion_log.json` matches its
`*_written_*` counterpart exactly.

### How to visualize this
`docker exec -it advancerag-neo4j-1 cypher-shell -u neo4j -p <password>` and run
`MATCH (n) RETURN labels(n)[0] AS label, count(n) ORDER BY label DESC;` — compare that output, by
eye, against the `nodes_written_by_label` block printed at the end of a `python -m src.graph.ingest`
run. They should match exactly; if you ever see them differ, that's `VerificationFailed` in action.

**You should now be able to explain:** why "no exception was raised" is insufficient evidence of
correctness for anything that writes through its own merge/dedup logic, and what specific bug class
per-label/per-type count verification catches (and what it still cannot catch — see the Open
Question).

---

## C5 — Recording a contradiction instead of resolving it

### Do this first
Before reading on, look at [`ingest.py:301-330`](../src/graph/ingest.py#L301-L330) (`_ceo_note`) and
predict: does it CHANGE either the HR record's `job_title` or any `SIGNED_BY` edge's `title` property
to make them agree? Then run
[`d4_ceo_contradiction.py`](../practice%20codes/phase%204/m5_graph_ingestion_deep_dive/d4_ceo_contradiction.py)
(or `python -m src.graph.ingest --rebuild`) and read the printed note.

### 1. Definition + analogy
**What it is.** The corpus states who the CEO is in two places that disagree: the HR record says
Avery Lancaster is `"Co-Founder & Chief Executive Officer (CEO)"`; seven contracts are signed by
Jennifer Rodriguez with the title `"Chief Executive Officer"`. Both facts are extracted, both are
written to the graph, UNCHANGED, exactly as the source documents say them. `_ceo_note` computes and
LOGS the disagreement — a sentence in `data/graph_ingestion_log.json`'s `notes` field — but does not
pick a winner, correct either record, or drop either edge.

This is a design choice, not an oversight: deciding "who is really the CEO" needs either a human with
outside knowledge (maybe Jennifer Rodriguez signs as CEO because she's Acting CEO for that division,
or the HR record is stale) or a retrieval-time policy (surface both, with their sources, and let the
person asking decide). Neither of those is something an ingestion job — whose whole job is to be a
faithful, mechanical copy of what the source documents say — should be doing silently.

**Key terms.**
- **Provenance over correction:** record WHERE a fact came from and let a conflict be visible, rather
  than silently picking one "truth" and discarding the evidence for the other.
- **A knowledge graph is a model of what the DOCUMENTS say, not of what is TRUE.** Those are
  different things whenever the documents themselves disagree.

**Analogy.** A court stenographer transcribes exactly what two witnesses say, even when their
accounts contradict each other. The transcript recording BOTH statements, word for word, is more
useful to the judge than a "corrected" transcript where the stenographer quietly decided who was
right and only wrote that one down.

### For a 14-year-old
Imagine you're building a fact-file about your school from two sources: the school website says the
Head Boy is Sam, but the yearbook — printed a few months later — lists a DIFFERENT name as Head Boy,
because Sam graduated early and someone else took over. If your fact-file just silently believes
whichever source you read last, anyone using it has no idea there was ever a change, or a
disagreement, or which source is more current. A BETTER fact-file writes both down and says "the
website says X, the yearbook says Y — here's where each came from, you decide which matters for what
you're asking." That's what `_ceo_note` is doing: not fixing the disagreement, just refusing to hide
that it exists.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the fact-file about the school | the knowledge graph itself |
| the school website's claim | the HR record's `Employee.job_title` for Avery Lancaster |
| the yearbook's claim | the `SIGNED_BY.title` property on the 7 contracts Jennifer Rodriguez signs |
| "the fact-file writes both down and says where each came from" | `_ceo_note` reading both facts and building one sentence, without editing either |
| deciding which name is "really" Head Boy | left to a human or a later retrieval-time policy — never decided inside `_ceo_note` |
| the fact-file, unedited, still holding both claims | both the `Employee` node and all 7 `SIGNED_BY` edges are still written to Neo4j exactly as extracted |

### 2. Why here specifically
This graph feeds a future retrieval system whose whole point is answering questions FROM these
documents. If ingestion silently "fixed" the CEO discrepancy by picking one, a question like "who
signs Insurellm's contracts as CEO?" would get an answer that contradicts what the HR record actually
says, with no way for anyone downstream to know a decision was ever made on their behalf.

### 3. Minimal code
`d4_ceo_contradiction.py` — the REAL `_ceo_note` function, imported unmodified, run against a small
made-up set of nodes and edges (so the concept is visible without needing all 46 real signatures):
```python
# Minimal demo of C5: `_ceo_note` is called on the REAL function from ingest.py, with a
# small, made-up set of nodes/edges standing in for the real corpus. It shows the note
# RECORDS the disagreement -- it never edits either fact to make them agree.
from src.graph.ingest import _ceo_note  # the exact function this project ships, imported unmodified
from src.graph.records import GraphEdge, GraphNode, NodeRef  # the same record types the real job uses

hr_record = GraphNode(  # one Employee node, as if extracted from an HR file
    "Employee",  # the label the extractor gives every roster member
    {"name": "Avery Lancaster"},  # the key: roster members are identified by name alone
    {"job_title": "Co-Founder & Chief Executive Officer (CEO)"},  # the fact this source states
)

jennifer = NodeRef("Contact", {"name": "Jennifer Rodriguez", "organization": "Insurellm"})  # a signatory
other_signatory = NodeRef("Contact", {"name": "Priya Nair", "organization": "Insurellm"})  # a DIFFERENT one

edges = [  # a small set of SIGNED_BY edges, standing in for the 46 the real corpus produces
    GraphEdge(  # first contract Jennifer signs as CEO
        "SIGNED_BY",
        NodeRef("Contract", {"doc_id": "doc-1"}),  # the contract's own identity (its doc_id)
        jennifer,  # who signed it
        {"title": "Chief Executive Officer"},  # the title as THAT contract writes it
    ),
    GraphEdge(  # second contract, same signatory, same conflicting title
        "SIGNED_BY", NodeRef("Contract", {"doc_id": "doc-2"}), jennifer, {"title": "Chief Executive Officer"}
    ),
    GraphEdge(  # a third contract, a DIFFERENT person, a DIFFERENT title -- should not be counted
        "SIGNED_BY", NodeRef("Contract", {"doc_id": "doc-3"}), other_signatory, {"title": "VP Sales"}
    ),
]

note = _ceo_note([hr_record], edges)  # call the REAL function exactly as run_ingestion does
print(note)  # print the sentence that goes into data/graph_ingestion_log.json's "notes" field

print("\nBoth facts, unedited, are still what would be WRITTEN to the graph:")
print(" ", hr_record.label, hr_record.key, "->", dict(hr_record.properties))  # the HR fact, untouched
for edge in edges:  # every SIGNED_BY edge, untouched
    print(" ", edge.type, edge.source.key, "->", edge.target.key, dict(edge.properties))
```
Real output:
```
CEO contradiction: HR records Avery Lancaster as 'Co-Founder & Chief Executive Officer (CEO)', while Jennifer Rodriguez signs 2 contract(s) as ['Chief Executive Officer']. Both are loaded as written.

Both facts, unedited, are still what would be WRITTEN to the graph:
  Employee {'name': 'Avery Lancaster'} -> {'job_title': 'Co-Founder & Chief Executive Officer (CEO)'}
  SIGNED_BY {'doc_id': 'doc-1'} -> {'name': 'Jennifer Rodriguez', 'organization': 'Insurellm'} {'title': 'Chief Executive Officer'}
  SIGNED_BY {'doc_id': 'doc-2'} -> {'name': 'Jennifer Rodriguez', 'organization': 'Insurellm'} {'title': 'Chief Executive Officer'}
  SIGNED_BY {'doc_id': 'doc-3'} -> {'name': 'Priya Nair', 'organization': 'Insurellm'} {'title': 'VP Sales'}
```
The unrelated third signatory (Priya Nair, "VP Sales") is correctly left out of the count — the
function only reports the specific two names it's told to compare, exactly as written below.

### The exact code I wrote (`src/graph/ingest.py`)
```python
# lines 301-330
def _ceo_note(nodes: Sequence[GraphNode], domain_edges: Sequence[GraphEdge]) -> str:
    lancaster = next(  # find the ONE Employee node named Avery Lancaster, if it exists
        (n for n in nodes if n.label == "Employee" and n.key.get("name") == "Avery Lancaster"),
        None,
    )
    hr_title = lancaster.properties.get("job_title") if lancaster else None  # 🎯 READ only, never SET
    signed_as_ceo = sorted(
        {
            edge.properties.get("title", "")
            for edge in domain_edges
            if edge.type == "SIGNED_BY"
            and edge.target.key.get("name") == "Jennifer Rodriguez"
            and "chief executive" in str(edge.properties.get("title", "")).casefold()
        }
    )                                                          # every DISTINCT wording she signs with
    count = sum(                                               # how many contracts, not just how many wordings
        1
        for edge in domain_edges
        if edge.type == "SIGNED_BY"
        and edge.target.key.get("name") == "Jennifer Rodriguez"
        and "chief executive" in str(edge.properties.get("title", "")).casefold()
    )
    return (                                                    # 🎯 INTERVIEW: build a SENTENCE, change NOTHING
        f"CEO contradiction: HR records Avery Lancaster as {hr_title!r}, while Jennifer "
        f"Rodriguez signs {count} contract(s) as {signed_as_ceo!r}. Both are loaded as written."
    )
```
**🎯 Interview-relevant lines:** `hr_title = lancaster.properties.get(...)` and every line inside
`signed_as_ceo`/`count` — every single one of them is a READ. Nothing in this function ever calls
`GraphNode(...)` to construct a corrected record, or mutates `edge.properties`. The function's entire
job is to look at two facts and describe the disagreement in a string; deciding which fact is "right"
is explicitly left to whoever reads the log or queries the graph later.

### 4. What breaks if this changes
- **Pick the HR record as authoritative and drop the contradicting `SIGNED_BY` titles:** the graph
  now lies about what seven real contracts actually say, and the evidence for correcting that later
  (if it turns out Jennifer Rodriguez really was Acting CEO) is gone.
- **Remove `_ceo_note` entirely, keep loading both facts:** the graph is still technically correct
  (both facts loaded as written), but the CONTRADICTION becomes invisible — nobody building the next
  step (an evaluation set, a retrieval policy) would know to design for it without re-discovering it
  themselves.

### [Interview relevance: high — "how do you handle conflicting facts in a knowledge base" is a direct, common RAG/knowledge-graph interview question, and "record, don't silently resolve" is the answer worth having ready]

### Canonical docs
- Edge et al. (2024), ["From Local to Global..."](https://arxiv.org/abs/2404.16130) — community/entity
  summarization has to grapple with the same source-conflict problem at scale
- [W3C PROV-O](https://www.w3.org/TR/prov-o/) — the standard vocabulary for provenance ("this fact
  came from this source"), the general pattern `_ceo_note` is a minimal, hand-written instance of

### Micro-exercises
1. Run `python -m src.graph.ingest --rebuild` and read the note in the printed summary and in
   `data/graph_ingestion_log.json`. Which two properties does it read to build the sentence?
   *Hint:* `Employee.job_title` and `SIGNED_BY.title`.
2. Find a THIRD contradicting fact in the corpus by hand (open a few HR records and contracts and
   compare titles/roles). Is there one? If not, why might CEO be the only one the corpus was designed
   to have?
3. `_ceo_note` hard-codes the names "Avery Lancaster" and "Jennifer Rodriguez". What would a GENERAL
   version look like — one that finds ANY two SIGNED_BY signatories both claiming "Chief Executive
   Officer" for the same company, without naming them in advance? Sketch it; you don't need to
   implement it.
4. Change `_ceo_note` in a scratch copy to instead RAISE an exception when it finds a contradiction,
   aborting the run. What would that mean for whether this corpus could ever be ingested at all?
5. Where should the DECISION about which CEO fact to trust actually live, in a system that answers
   user questions — in this ingestion job, in the retrieval/ranking step, or in the UI that presents
   the answer? Argue for one.

**How it works internally.** `_ceo_note` runs entirely on the ALREADY-RESOLVED nodes and edges
([`ingest.py:157`](../src/graph/ingest.py#L157): called with `resolution.nodes, domain_edges` after
`resolve_entities`/`remap_edges` have already run), so it sees exactly what will actually be written —
not the pre-merge extraction, which could still contain spelling variants that haven't been unified
yet.

### How to validate this yourself
```bash
.venv/Scripts/python -m src.graph.ingest --rebuild
```
Real output (the note line):
```
note: CEO contradiction: HR records Avery Lancaster as 'Co-Founder & Chief Executive Officer (CEO)', while Jennifer Rodriguez signs 7 contract(s) as ['Chief Executive Officer']. Both are loaded as written.
```
Then confirm BOTH facts are actually in the graph, unedited, by querying Neo4j directly (below).

### How to visualize this
In Neo4j Browser: `MATCH (e:Employee {name: 'Avery Lancaster'}) RETURN e.job_title` and
`MATCH (c:Contact {name: 'Jennifer Rodriguez'})<-[r:SIGNED_BY]-(:Contract) RETURN DISTINCT r.title,
count(*)` — run both, side by side, and see the disagreement sitting plainly in the data, exactly as
the log described it.

**You should now be able to explain:** why an ingestion job should generally record a conflict
rather than silently resolve it, and where in this codebase that decision actually gets made (at
`resolve_entities`'s merge step for SPELLING variants of one entity, versus here, for two DIFFERENT
entities each correctly claiming the same fact).

---

## Consolidated end-to-end exercise: watch the whole thing work

This ties every concept above together into one sequence: start clean, trigger a run, and observe the
effect at every layer — the log, the terminal, and the database itself.

1. **Start clean.** `docker compose up -d neo4j`, wait for it to be healthy
   (`python -m src.graph.setup` should print `ready: ...`), then confirm the schema is in force:
   `python -m src.graph.schema --check` should print `schema verified: 8/8 constraints`.
2. **Trigger the run.** `python -m src.graph.ingest --rebuild` (or without `--rebuild` if the graph
   is genuinely empty). Watch the terminal: seven `INFO stage ... finished in ...s` lines, in order —
   this is C1's staged pipeline live.
3. **See it in the log.** Open `data/graph_ingestion_log.json`. Find `stage_seconds` (which stage was
   slowest — almost certainly `write`, not `extract`, unlike the P3-M5 vector job where `embed`
   dominated), `nodes_written_by_label`, `edges_written_by_type`, and the CEO `notes` entry (C5).
4. **See it in the terminal's own summary.** The `SUCCEEDED: ...` line and the two dictionaries
   printed after it are `summarise(report)` — compare them, by eye, against the log file. They should
   describe the exact same run.
5. **See it in Neo4j itself.** Open `http://localhost:7474`, log in, and run:
   ```cypher
   MATCH (n) RETURN labels(n)[0] AS label, count(n) AS total ORDER BY total DESC
   ```
   Compare every number against `nodes_in_db_by_label` in the log (C4) — they must match exactly, or
   `report.verified` would have been `False` and the run would have raised `VerificationFailed`
   before you ever got to look.
6. **Follow one entity all the way through.** Pick a product, e.g. `Carllm`:
   ```cypher
   MATCH (p:Product {name: 'Carllm'})-[:DESCRIBED_IN]->(d:Document) RETURN p, d
   ```
   Exactly one `Document` should come back (C3). Now follow its mentions back to actual text:
   ```cypher
   MATCH (c:Chunk)-[:MENTIONS]->(p:Product {name: 'Carllm'}) RETURN c.chunk_id LIMIT 5
   ```
   Open one of those `chunk_id`s in `data/chunks/` and confirm, by reading, that the chunk really does
   name Carllm — you've just walked the entire bridge this graph exists to build: entity → its own
   page, and entity → every passage that mentions it.
7. **Break something on purpose and watch it get caught.** Re-run `python -m src.graph.ingest`
   *without* `--rebuild` against the now-loaded graph. Read the `IngestionAborted` message (C1) and
   confirm in Neo4j Browser that nothing changed — the node counts from step 5 are identical.

If every one of those six checks matches what you predicted, you understand this step. If any of them
surprises you, that surprise is worth chasing down before moving on to query-time retrieval, which
will build directly on top of everything this graph now holds.
