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


print("Scenario: the resolve stage has a bug and hands _write two DIFFERENT plan entries")
print("that happen to carry the SAME node key (e.g. a canonical-name collision it didn't merge).\n")

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
print(f"MERGE result for the collided key: {graph.nodes[('Company', 'Acme Insurance')]}")  # which value won

if written_count != db_count:  # exactly the comparison _verify makes, per label/type
    print(f"\n_verify would raise VerificationFailed: wrote {written_count}, database holds {db_count}")
else:
    print("\ncounts agree; _verify would pass")

print("\nEvery individual store.upsert_node() call above returned normally -- 'no exception'")
print("is exactly what a job WITHOUT a verify stage would see. Only counting what the database")
print("actually holds, and comparing it with what was intended, catches the collapse.")
