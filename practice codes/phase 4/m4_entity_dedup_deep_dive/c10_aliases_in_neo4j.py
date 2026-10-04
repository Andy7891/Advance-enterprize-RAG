# C10: what happens to `aliases` when the result is written to Neo4j (needs: docker compose up -d neo4j).
import uuid                                                                  # a unique prefix, so the cleanup is exact
from src.graph.dedup import deduplicate_entities                             # the merge
from src.graph.records import GraphNode                                      # the record type
from src.graph.setup import build_driver                                     # the project's driver factory
from src.retrieval.graph_store import GraphStore                             # the P4-M3 store

prefix = f"__demo_{uuid.uuid4().hex[:6]}_"                                   # every demo name starts with this


def merged(*names):                                                          # dedupe clean names, then prefix the RESULT (prefixes would break matching)
    node = deduplicate_entities([GraphNode("Employee", {"name": n}) for n in names])[0]  # one merged node
    return GraphNode("Employee", {"name": prefix + node.key["name"]}, dict(node.properties))  # same node, prefixed key


def stored(session):                                                         # read the aliases now in the database
    return session.run("MATCH (e:Employee) WHERE e.name STARTS WITH $p RETURN e.aliases AS a", p=prefix).single()["a"]  # one row


with build_driver() as driver, driver.session() as session:                  # open a connection and a session
    try:                                                                     # cleanup below must always run
        store = GraphStore(driver)                                           # the store (checks the constraints exist)
        store.upsert_node(merged("Alice", "Alice Johnson"))                  # batch 1 knows the alias "Alice"
        print("after batch 1:", stored(session))                             # ['Alice']
        store.upsert_node(merged("Ms. Johnson", "Alice Johnson"))            # batch 2 knows a DIFFERENT alias, "Ms. Johnson"
        print("after batch 2:", stored(session), "<- 'Alice' is gone: SET += replaced the list")  # ['Ms. Johnson']
        old = set(stored(session))                                           # the fix: read what is stored first...
        new = merged("Alice", "Alice Johnson")                               # ...merge in the alias a third batch knows...
        store.upsert_node(GraphNode("Employee", dict(new.key), {"aliases": sorted(old | set(new.properties["aliases"]))}))  # ...write the union
        print("after union  :", stored(session))                             # ['Alice', 'Ms. Johnson']: nothing lost
    finally:                                                                 # always clean up
        session.run("MATCH (e:Employee) WHERE e.name STARTS WITH $p DETACH DELETE e", p=prefix).consume()  # remove only this demo's node
