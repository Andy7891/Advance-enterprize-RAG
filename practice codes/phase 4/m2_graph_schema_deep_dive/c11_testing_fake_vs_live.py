# C11: the testing ideas behind tests/graph/test_schema.py -- one scenario on a fake AND the real server, a sweep, a doc guard, a mutant.
# Live Neo4j on throwaway "Scratch" labels (the prefix the project's sweep looks for), self-cleaning.
import sys                                                               # to add the project root to Python's import path
import uuid                                                              # to make labels no other data can have
from pathlib import Path                                                 # object-style file paths

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))             # only `src` is installed as a package; this makes `tests` importable too

from src.graph.schema import NODE_TYPES, NodeType, SchemaMismatch, apply_schema, check_schema  # the code under test
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)
from tests.graph.test_schema import FakeSchemaDriver, _sweep_scratch, _table  # the project's own fake, sweep helper and doc-table reader

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
types = (                                                                # a tiny model on labels that start with "Scratch"
    NodeType(f"Scratch{suffix}", ("name",), "domain", "demo"),           # a one-property key
    NodeType(f"ScratchC{suffix}", ("name", "organization"), "domain", "demo"),  # a two-property key
)                                                                        # end of the tiny model


def scenario(driver) -> dict:                                            # ONE scenario, run unchanged against any driver
    seen = {}                                                            # what we observed
    first = apply_schema(driver, types)                                  # apply twice ...
    second = apply_schema(driver, types)                                 # ... to see created vs present
    seen["created first/second"] = (len(first.created), len(second.created))  # (2, 0) if idempotent
    with driver.session() as session:                                    # now plant an impostor under the first constraint's name
        session.run(f"DROP CONSTRAINT {types[0].constraint_name} IF EXISTS").consume()  # remove the real rule
        session.run(f"CREATE CONSTRAINT {types[0].constraint_name} IF NOT EXISTS FOR (n:{types[0].label}) REQUIRE n.other IS UNIQUE").consume()  # wrong rule
    try:                                                                 # apply must notice
        apply_schema(driver, (types[0],))                                # verify-by-definition should raise
        seen["impostor"] = "ACCEPTED (bug)"                              # if we get here, the check is broken
    except SchemaMismatch:                                               # the expected outcome
        seen["impostor"] = "SchemaMismatch"                              # noticed
    return seen                                                          # hand the observations back


fake = FakeSchemaDriver()                                                # the in-memory stand-in from the test file
with build_driver() as live:                                             # the real server
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        _sweep_scratch(live)                                             # start from a clean slate
        on_fake, on_live = scenario(fake), scenario(live)                # 1. run the SAME scenario on both
        print("1. fake :", on_fake)                                      # what the fake did
        print("   live :", on_live)                                      # what Neo4j did
        print("   identical:", on_fake == on_live, "-> the fake still tells the truth about Neo4j")  # the point of running both

        with live.session() as session:                                  # 2. simulate a test run that crashed before cleaning up
            session.run(f"CREATE CONSTRAINT scratch_leftover_{suffix} IF NOT EXISTS FOR (n:ScratchLeft{suffix}) REQUIRE n.name IS UNIQUE").consume()  # a leftover rule
            session.run(f"CREATE (:ScratchLeft{suffix} {{name: 'orphan'}})").consume()  # and a leftover node
            left = session.run("SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'scratch' RETURN count(*) AS c").single()["c"]  # how many scratch rules exist
            print("2. leftovers before the sweep:", left, "scratch constraints")  # the crashed run's debris
            _sweep_scratch(live)                                         # the next run starts by sweeping, by name prefix
            left = session.run("SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'scratch' RETURN count(*) AS c").single()["c"]  # count again
            nodes = session.run("MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Scratch') RETURN count(n) AS c").single()["c"]  # and nodes
            print("   after the sweep           :", left, "constraints,", nodes, "nodes")  # repaired
    finally:                                                             # always clean up, even after an error above
        _sweep_scratch(live)                                             # the sweep IS the cleanup

documented = {                                                           # 3. the doc guard: read the table in docs/architecture.md
    cells[0].strip("`"): (cells[1], tuple(k.strip("` ") for k in cells[2].split(",")))  # label -> (layer, key)
    for cells in _table("graph-nodes")                                   # each data row of the node table
}                                                                        # end of the dictionary
model = {n.label: (n.layer, n.key) for n in NODE_TYPES}                  # the same facts, from the code
print("3. doc table == model          :", documented == model)           # True: the documentation matches the code
drifted = {**model, "Department": ("domain", ("name",))}                 # pretend someone added a label in code but not in the doc
print("   after adding 'Department'   :", documented == drifted, "| only in code:", set(drifted) - set(documented))  # the guard would fail


def mutant_ok(driver, node: NodeType) -> bool:                           # 4. a MUTANT: check by NAME only (a plausible mistake)
    with driver.session() as session:                                    # ask the database what constraints exist
        names = {r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name RETURN name")}  # just their names
    return node.constraint_name in names                                 # "a constraint with that name exists" -- ignores its definition


trap = FakeSchemaDriver()                                                # use the fake so nothing touches the real database
trap.constraints[types[0].constraint_name] = ("UNIQUENESS", "NODE", (types[0].label,), ("other",))  # the impostor: right name, wrong property
print("4. real check_schema says ok   :", check_schema(trap, (types[0],)).ok)   # False: it compares definitions
print("   mutant (name only) says ok  :", mutant_ok(trap, types[0]), "<- wrong; test_same_name_with_a_different_definition... fails on this mutant")  # True: it would slip through
