# C3: a uniqueness constraint + MERGE make "one node per identity" a database rule. Live Neo4j, self-cleaning.
import uuid                                                              # to make a label no other data can have

from neo4j.exceptions import ConstraintError, Neo4jError                 # duplicate-key error, and the base class of all server errors

from src.graph.schema import NodeType, constraint_statement             # the project's model class and its DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                    # a unique label isolates this demo's nodes
node = NodeType(label, ("name",), "domain", "demo")                      # a model entry for the throwaway label, identified by name


def operators(plan: dict):                                               # walk a PROFILE plan and yield every operator's name
    yield plan["operatorType"]                                           # this operator
    for child in plan.get("children", []):                               # then each of its inputs
        yield from operators(child)                                      # recursively


with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        session.run(f"CREATE (:{label} {{name: 'Acme'}})").consume()     # 1. no constraint yet: a first Acme
        session.run(f"CREATE (:{label} {{name: 'Acme'}})").consume()     #    and a second one, silently accepted
        count = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]  # how many nodes exist now
        print("1. CREATE twice, no constraint :", count, "nodes")        # 2: nothing stopped the duplicate

        try:                                                             # 2. try to add the rule over data that already breaks it
            session.run(constraint_statement(node)).consume()            # the project's own DDL for this node type
        except Neo4jError as exc:                                        # the server refuses to create a rule the data violates
            print("2. constraint over duplicates:", type(exc).__name__, "|", " ".join(str(exc).split())[:120])  # what it says

        session.run(f"MATCH (n:{label}) WITH n LIMIT 1 DETACH DELETE n").consume()  # 3. remove one of the duplicates
        added = session.run(constraint_statement(node)).consume().counters.constraints_added  # now the DDL succeeds; count what it added
        print("3. constraint created          :", added, "-> named", node.constraint_name)  # the name comes from NodeType.constraint_name

        try:                                                             # 4. now a duplicate must be refused
            session.run(f"CREATE (:{label} {{name: 'Acme'}})").consume()  # a second Acme again
        except ConstraintError as exc:                                   # the specific error class for a duplicate key
            print("4. CREATE a duplicate         :", type(exc).__name__, "|", " ".join(str(exc).split())[:110])  # refused

        first = session.run(f"MERGE (n:{label} {{name: 'Zed'}})").consume().counters.nodes_created   # 5. MERGE = find, or create if absent
        second = session.run(f"MERGE (n:{label} {{name: 'Zed'}})").consume().counters.nodes_created  # the same MERGE again
        print("5. MERGE twice, nodes created  :", first, "then", second)  # 1 then 0: re-running a load never duplicates

        index = session.run(                                             # 6. the constraint owns an index
            "SHOW INDEXES YIELD name, type, state, owningConstraint WHERE owningConstraint = $c RETURN type, state",  # look it up by owner
            c=node.constraint_name,                                      # the constraint's name is the parameter
        ).single()                                                       # exactly one index expected
        print("6. backing index               :", index.data())         # a RANGE index, ONLINE

        plan = session.run(f"PROFILE MATCH (n:{label} {{name: 'Zed'}}) RETURN n").consume().profile  # 7. run a lookup and keep the executed plan
        print("7. lookup by key uses          :", [o for o in operators(plan) if "Seek" in o or "Scan" in o])  # an index seek, not a scan
    finally:                                                             # always clean up, even after an error above
        session.run(f"DROP CONSTRAINT {node.constraint_name} IF EXISTS").consume()  # remove the demo's constraint
        removed = session.run(f"MATCH (n:{label}) DETACH DELETE n RETURN count(n) AS c").single()["c"]  # delete the demo's nodes
        print("8. cleanup                     :", removed, "nodes deleted; constraint dropped; label", label, "left nothing behind")  # done
