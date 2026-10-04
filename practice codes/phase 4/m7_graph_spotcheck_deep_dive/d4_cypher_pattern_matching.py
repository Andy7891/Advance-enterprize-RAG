# Minimal demo of C3: a Cypher MATCH pattern describes a SHAPE (nodes and the relationships
# between them) and Neo4j finds every place in the graph that shape occurs. A tiny, isolated
# scratch subgraph, written and deleted by exact identity (the M6/M7 project convention).
from src.graph.setup import build_driver

with build_driver() as driver:
    with driver.session(database="neo4j") as session:
        print("Step 1: write a tiny 3-node scratch subgraph")
        session.run(
            # MERGE finds-or-creates each node by its one identifying property
            "MERGE (a:M7FixtureThing {name: 'Alice'}) "
            "MERGE (b:M7FixtureThing {name: 'Bob'}) "
            "MERGE (c:M7FixtureThing {name: 'Carol'}) "
            # MERGE again, this time for the relationships connecting them
            "MERGE (a)-[:M7FIXTURE_KNOWS]->(b) "
            "MERGE (b)-[:M7FIXTURE_KNOWS]->(c)"
        ).consume()
        print("  written: Alice -> Bob -> Carol")

        print("\nStep 2: a pattern that matches ONE direct relationship")
        for row in session.run(
            # this pattern matches exactly: some node, one KNOWS edge, another node
            "MATCH (x:M7FixtureThing)-[:M7FIXTURE_KNOWS]->(y:M7FixtureThing) "
            "RETURN x.name AS x, y.name AS y"
        ):
            print(" ", row["x"], "->", row["y"])

        print("\nStep 3: a pattern that matches TWO hops, through an intermediate node")
        for row in session.run(
            # the *1..2 means "one OR two relationships in a row", any direction, matched as a chain
            "MATCH (x:M7FixtureThing)-[:M7FIXTURE_KNOWS*1..2]->(z:M7FixtureThing) "
            "WHERE x.name = 'Alice' "
            "RETURN DISTINCT z.name AS reachable_from_alice"
        ):
            print(" ", row["reachable_from_alice"])

        print("\nStep 4: clean up by exact identity -- delete only what step 1 created")
        session.run(
            "MATCH (n:M7FixtureThing) DETACH DELETE n"
        ).consume()
        print("  cleaned up")
