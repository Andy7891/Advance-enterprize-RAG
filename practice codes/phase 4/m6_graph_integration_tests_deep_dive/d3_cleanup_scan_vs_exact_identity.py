# Minimal demo of C4: cleaning up by SCANNING for a name pattern can crash or over-match
# on data you didn't anticipate; cleaning up by the EXACT identity you wrote cannot.
# Live (read-only: MATCH and RETURN, no DETACH DELETE) against the real Neo4j.
from neo4j.exceptions import ClientError

from src.graph.setup import build_driver

with build_driver() as driver:
    with driver.session(database="neo4j") as session:
        print("Attempt 1: a text-scan sweep, the naive cleanup approach")
        try:
            # This is the REAL query I tried by hand while checking this project's own
            # cleanup: scan every node's every property for one that starts with a prefix.
            session.run(
                "MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH $prefix) "
                "RETURN count(n) AS c",
                prefix="M6Fixture",
            ).single()
            print("  scan succeeded")
        except ClientError as exc:
            # toString() refuses a LIST property (a Company's `aliases`, from P4-M4's
            # dedup): the scan crashes on the first node it meets that has one, whatever
            # that node's actual name is -- proof a scan's blast radius includes data it
            # was never meant to touch.
            print(f"  scan CRASHED: {exc.code}")
            print(f"  {exc.message}")

        print("\nAttempt 2: exact-identity lookup, this project's actual cleanup approach")
        # Look up ONE specific node by its exact label and key -- no scanning, no risk of
        # ever touching a node this code didn't itself create.
        result = session.run(
            "MATCH (n:Company {name: $name}) RETURN count(n) AS c", name="M6Fixture Client"
        ).single()
        print(f"  exact lookup succeeded, found: {result['c']} node(s) (0 is fine -- cleanup already ran)")
