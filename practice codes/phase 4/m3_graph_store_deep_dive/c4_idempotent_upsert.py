# C4: idempotent upsert -- MERGE on the key, SET += to add properties without deleting any.
import uuid                                                                  # to make a label nothing else uses

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                        # a throwaway label, so cleanup is exact


def show(session, note: str) -> None:                                        # print how many nodes exist and what the first one holds
    rows = session.run(f"MATCH (n:{label}) RETURN properties(n) AS p").value("p")  # every node's properties
    print(f"{note:<28} nodes={len(rows)} properties={rows[0] if rows else None}")  # one line per step


with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.run(f"MERGE (n:{label} {{name: 'Acme'}}) SET n += {{a: 1}}").consume()  # first write: creates the node
        session.run(f"MERGE (n:{label} {{name: 'Acme'}}) SET n += {{b: 2}}").consume()  # second write: finds it, adds b
        show(session, "MERGE twice, SET +=")                                 # one node, name + a + b: nothing lost
        session.run(f"MERGE (n:{label} {{name: 'Acme'}}) SET n = {{c: 3}}").consume()  # SET = replaces every property
        show(session, "then SET =")                                          # the key itself is gone
    finally:                                                                 # always clean up
        session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()          # remove the demo's nodes
