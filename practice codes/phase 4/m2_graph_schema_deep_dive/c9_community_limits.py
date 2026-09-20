# C9: what Neo4j Community Edition cannot enforce, so the loader must. Live Neo4j on throwaway labels, self-cleaning.
import re                                                                # to pull the key phrase out of an error message
import uuid                                                              # to make labels no other data can have

from neo4j.exceptions import ClientError, ConstraintError, Neo4jError    # error classes: any client error, duplicate key, any server error

from src.graph.schema import NodeType, constraint_statement             # the project's model class and DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
label, keyless = f"Demo{suffix}", f"DemoK{suffix}"                       # one label with a composite key, one for the null experiment
composite = NodeType(label, ("name", "organization"), "domain", "demo")  # a two-property key, like Contact
names = [composite.constraint_name, f"demo_nn_{suffix}", f"demo_ty_{suffix}", f"demo_nk_{suffix}", f"demo_kl_{suffix}"]  # constraints to drop at the end

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        edition = session.run("CALL dbms.components() YIELD edition RETURN edition").single()["edition"]  # 1. which edition is this?
        print("1. edition                     :", edition)               # community

        for tag, rule in [("NOT NULL", "n.name IS NOT NULL"), ("TYPE", "n.name IS :: STRING"), ("NODE KEY", "(n.name, n.organization) IS NODE KEY")]:  # the three refused kinds
            key = {"NOT NULL": names[1], "TYPE": names[2], "NODE KEY": names[3]}[tag]  # a constraint name for each attempt
            try:                                                         # the server may refuse
                session.run(f"CREATE CONSTRAINT {key} IF NOT EXISTS FOR (n:{label}) REQUIRE {rule}").consume()  # try to create it
                print(f"2. {tag:<9} constraint      : accepted (this would mean Enterprise!)")  # not expected here
            except Neo4jError as exc:                                    # refused
                why = re.search(r"([A-Za-z ]+requires Neo4j Enterprise Edition)", str(exc))  # the sentence that explains it
                print(f"2. {tag:<9} constraint      :", type(exc).__name__, "|", why[1].strip() if why else str(exc)[:80])  # class and reason

        session.run(constraint_statement(composite)).consume()           # 3. the composite UNIQUENESS constraint IS allowed
        session.run(f"CREATE (:{label} {{name: 'Bob', organization: 'X'}})").consume()  # a first Bob at X
        try:                                                             # the same pair again
            session.run(f"CREATE (:{label} {{name: 'Bob', organization: 'X'}})").consume()  # a duplicate pair
        except ConstraintError:                                          # refused, as designed
            print("3. duplicate (name, org) pair  : refused by the constraint")  # the rule works when both parts are present

        session.run(f"CREATE (:{label} {{name: 'Ann'}}), (:{label} {{name: 'Ann'}})").consume()  # 4. the same name twice, with NO organisation
        anns = session.run(f"MATCH (n:{label} {{name: 'Ann'}}) RETURN count(n) AS c").single()["c"]  # how many Anns exist
        print("4. two 'Ann' with no org       :", anns, "nodes accepted (a missing key part slips past the constraint)")  # the hole

        for title, query in [("MERGE null name", f"MERGE (:{keyless} {{name: $v}})"), ("MERGE null org ", f"MERGE (:{label} {{name: 'Ann', organization: $v}})")]:  # 5. MERGE with a null key part
            try:                                                         # MERGE should refuse
                session.run(query, v=None).consume()                     # try it with None (null)
                print(f"5. {title}             : accepted")              # not expected
            except ClientError as exc:                                   # refused
                print(f"5. {title}             :", exc.code.split(".")[-1], "(MERGE checks the key before writing)")  # SemanticError

        session.run(f"CREATE (n:{keyless}) SET n.name = $v", v=None).consume()  # 6. CREATE has no such check: a node with no name at all
        props = session.run(f"MATCH (n:{keyless}) RETURN keys(n) AS k").single()["k"]  # which properties did it end up with?
        print("6. CREATE with a null name     : node created, properties =", props)  # empty: a keyless node
    finally:                                                             # always clean up, even after an error above
        for name in names:                                               # every constraint the demo may have made
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop it if it exists
        removed = session.run(f"MATCH (n) WHERE n:{label} OR n:{keyless} DETACH DELETE n RETURN count(n) AS c").single()["c"]  # delete demo nodes
        print("7. cleanup                     :", removed, "nodes deleted; constraints dropped")  # nothing left behind
