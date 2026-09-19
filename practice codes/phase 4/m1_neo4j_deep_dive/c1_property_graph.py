# C1: the property graph model and basic Cypher, on the LIVE Neo4j (self-cleaning).
import uuid                                                   # to make a label no other data can have

from src.graph.setup import build_driver                      # the project's driver factory (reads .env)

label = f"Demo_{uuid.uuid4().hex[:8]}"                        # a unique label isolates this demo's nodes

with build_driver() as driver, driver.session() as session:   # open a connection pool, then one session
    try:
        # CREATE: two nodes (with labels + properties) and one typed, directed relationship with a property
        session.run(
            f"CREATE (p:Person:{label} {{name: 'Avery Lancaster', born: 1985}})"
            f"-[:CEO_OF {{since: 2015}}]->"
            f"(c:Company:{label} {{name: 'Insurellm', founded: 2015}})"
        ).consume()                                           # consume() waits for the write to finish
        # CREATE a second company-product relationship, reusing the company node found by MATCH
        session.run(
            f"MATCH (c:Company:{label} {{name: 'Insurellm'}}) "
            f"CREATE (c)-[:SELLS]->(:Product:{label} {{name: 'Carllm', line: 'auto'}})"
        ).consume()

        # MATCH a pattern: who is CEO of which company, and since when?
        record = session.run(
            f"MATCH (p:Person:{label})-[r:CEO_OF]->(c:Company) RETURN p.name AS person, c.name AS company, r.since AS since"
        ).single()                                            # exactly one row expected
        print("1. CEO pattern      :", record.data())         # .data() turns a Record into a dict

        # Two hops: which products does the company whose CEO is Avery sell?
        rows = session.run(
            f"MATCH (:Person:{label} {{name: 'Avery Lancaster'}})-[:CEO_OF]->(c)-[:SELLS]->(prod) "
            "RETURN c.name AS company, prod.name AS product"
        ).data()                                              # all rows as a list of dicts
        print("2. two-hop pattern  :", rows)

        # Direction matters: the same pattern written backwards finds nothing
        backwards = session.run(f"MATCH (p:Person:{label})<-[:CEO_OF]-(c) RETURN count(*) AS n").single()["n"]
        print("3. reversed arrow   :", backwards, "matches")

        # Labels, relationship types and property keys that now exist for this demo
        schema = session.run(
            f"MATCH (n:{label}) RETURN labels(n) AS labels, keys(n) AS properties ORDER BY n.name"
        ).data()
        print("4. nodes            :", schema)
    finally:
        # DETACH DELETE removes the nodes AND their relationships; plain DELETE would refuse
        removed = session.run(f"MATCH (n:{label}) DETACH DELETE n RETURN count(n) AS n").single()["n"]
        print("5. cleanup          :", removed, "nodes deleted; label", label, "left nothing behind")
