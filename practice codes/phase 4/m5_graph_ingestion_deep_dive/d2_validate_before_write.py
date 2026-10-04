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
