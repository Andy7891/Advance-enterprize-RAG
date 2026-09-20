# C8: check_schema -- compare what the database enforces with what the model expects, by DEFINITION.
# Live Neo4j on throwaway labels, self-cleaning.
import uuid                                                              # to make labels no other data can have

from src.graph.schema import NodeType, apply_schema, check_schema       # the code under study
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
single = NodeType(f"Demo{suffix}", ("name",), "domain", "demo")          # a one-property key
composite = NodeType(f"DemoC{suffix}", ("name", "organization"), "domain", "demo")  # a two-property key
types = (single, composite)                                              # the demo's tiny "model"
alias, extra = f"demo_alias_{suffix}", f"demo_extra_{suffix}"            # names for two hand-made constraints


def ddl(session, name: str, label: str, prop: str) -> None:              # helper: create a one-property uniqueness constraint by hand
    session.run(f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE").consume()  # plain DDL


with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        before = check_schema(driver, types)                             # 1. nothing applied yet
        print("1. before apply :", "ok" if before.ok else "NOT ok")      # the model is not in force
        for problem in before.problems:                                  # one sentence per missing constraint
            print("     -", problem)                                      # names the missing constraint and its definition

        apply_schema(driver, types)                                      # apply the tiny model
        row = session.run(                                               # 2. what the server reports for one constraint
            "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties WHERE name = $n RETURN *",  # the five columns check_schema reads
            n=composite.constraint_name,                                 # look up the composite one
        ).single()                                                       # exactly one row
        print("2. one SHOW CONSTRAINTS row:", row.data())                # type, entityType, labels, properties (in creation order)
        print("   after apply  :", "ok" if check_schema(driver, types).ok else "NOT ok")  # in force

        ddl(session, extra, single.label, "other")                       # 3. an unrelated rule that someone added by hand
        status = check_schema(driver, types)                             # check again
        print("3. foreign rule :", "ok" if status.ok else "NOT ok", "| reported as unexpected:", extra in status.unexpected)  # informational only
        real = [n for n in status.unexpected if not n.startswith("demo")]  # this demo's model is tiny, so the 8 REAL constraints also look foreign
        print("   (the", len(real), "real project constraints are listed as unexpected too: this model only has 2 types)")  # explains the long list

        session.run(f"DROP CONSTRAINT {single.constraint_name}").consume()  # 4. remove the real rule and add the same RULE under another name
        ddl(session, alias, single.label, "name")                        # equivalent definition, different name
        status = check_schema(driver, types)                             # check again
        print("4. same rule, other name:", "ok" if status.ok else "NOT ok", "| alias flagged as foreign:", alias in status.unexpected)  # matched by definition

        session.run(f"DROP CONSTRAINT {alias}").consume()                # 5. remove the alias and plant an IMPOSTOR under the expected name
        ddl(session, single.constraint_name, single.label, "legal_name")  # right name, wrong property
        status = check_schema(driver, types)                             # check again
        print("5. impostor     :", "ok" if status.ok else "NOT ok")      # the rule is not really in force
        for problem in status.problems:                                  # the explanation
            print("     -", problem)                                     # names what exists and what was expected
    finally:                                                             # always clean up, even after an error above
        for name in (single.constraint_name, composite.constraint_name, alias, extra):  # every constraint the demo may have made
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop it if it exists
        print("6. cleanup      : demo constraints dropped (no nodes were ever created)")  # nothing left behind
