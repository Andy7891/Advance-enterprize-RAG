# C7: apply_schema step by step -- idempotent creation, the counters, the silent no-op, and DDL after a write.
# Live Neo4j on throwaway labels, self-cleaning.
import time                                                              # to time the wait for indexes
import uuid                                                              # to make labels no other data can have

from neo4j.exceptions import Neo4jError                                  # base class of every error the server reports

from src.graph.schema import NodeType, SchemaMismatch, apply_schema, constraint_statement  # the code under study
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
single = NodeType(f"Demo{suffix}", ("name",), "domain", "demo")          # a one-property key, like Company
composite = NodeType(f"DemoC{suffix}", ("name", "organization"), "domain", "demo")  # a two-property key, like Contact
types = (single, composite)                                              # the demo's tiny "model"
tx_label = f"DemoT{suffix}"                                              # a third label, for the transaction experiment

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        first = apply_schema(driver, types)                              # 1. apply the tiny model to an empty label set
        print("1. first apply   : created", len(first.created), "present", len(first.present))  # both are new
        second = apply_schema(driver, types)                             # 2. apply it again
        print("2. second apply  : created", len(second.created), "present", len(second.present))  # nothing new: safe to re-run

        raw = session.run(constraint_statement(single)).consume().counters.constraints_added  # 3. the server's own counter for one statement
        print("3. counters      : constraints_added =", raw, "(0 = a no-op; this is how 'created' vs 'present' is known)")  # 0

        session.run(f"DROP CONSTRAINT {single.constraint_name}").consume()  # 4. remove the rule, then plant an IMPOSTOR under the same name
        session.run(                                                     # the impostor: same name, but it protects another property
            f"CREATE CONSTRAINT {single.constraint_name} IF NOT EXISTS FOR (n:{single.label}) REQUIRE n.other IS UNIQUE"  # a different rule
        ).consume()                                                      # wait for it
        noop = session.run(constraint_statement(single)).consume().counters.constraints_added  # now ask for the REAL rule under that name
        print("4. name taken    : constraints_added =", noop, "-> IF NOT EXISTS silently did nothing")  # 0, no error
        try:                                                             # apply_schema must not be fooled by that silence
            apply_schema(driver, (single,))                              # it verifies by definition after creating
        except SchemaMismatch as exc:                                    # so it raises
            print("   apply_schema  : SchemaMismatch |", exc)            # naming both definitions
        session.run(f"DROP CONSTRAINT {single.constraint_name}").consume()  # remove the impostor
        healed = apply_schema(driver, (single,))                         # and apply again
        print("   after dropping: created", len(healed.created), "-> the real rule is now in force")  # 1

        try:                                                             # 5. schema change after a write in ONE transaction
            with driver.session() as other, other.begin_transaction() as tx:  # an explicit transaction on its own session
                tx.run(f"CREATE (:{tx_label} {{name: 'x'}})").consume()  # a write ...
                tx.run(f"CREATE CONSTRAINT demo_tx_{suffix} IF NOT EXISTS FOR (n:{tx_label}) REQUIRE n.name IS UNIQUE").consume()  # ... then DDL
                tx.commit()                                              # never reached
        except Neo4jError as exc:                                        # the server refuses
            print("5. DDL after a write in one transaction:", type(exc).__name__, "|", exc.code)  # why apply_schema runs each DDL on its own

        started = time.perf_counter()                                    # 6. time the wait for indexes
        session.run("CALL db.awaitIndexes($seconds)", seconds=60).consume()  # blocks until every index is ONLINE (or 60 s pass)
        print(f"6. awaitIndexes  : returned after {(time.perf_counter() - started) * 1000:.0f} ms (empty labels, so immediate)")  # fast here
    finally:                                                             # always clean up, even after an error above
        for name in (single.constraint_name, composite.constraint_name, f"demo_tx_{suffix}"):  # every constraint the demo may have made
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop it if it exists
        removed = session.run(                                           # delete any nodes with a demo label
            f"MATCH (n) WHERE n:{single.label} OR n:{composite.label} OR n:{tx_label} DETACH DELETE n RETURN count(n) AS c"  # DETACH removes relationships too
        ).single()["c"]                                                  # how many were deleted
        print("7. cleanup       :", removed, "nodes deleted; constraints dropped")  # nothing left behind
