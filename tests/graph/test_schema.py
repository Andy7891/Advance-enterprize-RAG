"""Tests for the graph schema: the model's own consistency, the constraint logic against
a fake, and the same scenarios replayed on the real Neo4j.

The fake and the real server run the SAME scenario tests (the `schema_db` fixture has
two parameters). That is what keeps the fake honest: it encodes behaviour observed on a
live 5.26 Community server, and every scenario that passes on the fake must also pass
there. Live parameters skip themselves, with the reason, when Neo4j is not running.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from neo4j.exceptions import ClientError, ConstraintError, DatabaseError

import src.graph.schema as graph_schema
from src.graph.schema import (
    DOMAIN_LABELS,
    NODE_TYPES,
    RELATIONSHIP_TYPES,
    NodeType,
    SchemaMismatch,
    apply_schema,
    check_schema,
    constraint_statement,
)
from src.graph.setup import GraphStatus, build_driver
from tests.support import neo4j_down

ARCHITECTURE = Path(__file__).resolve().parents[2] / "docs" / "architecture.md"


# --- a fake of the constraint behaviour observed on the live server ----------------------


class FakeCounters:
    def __init__(self, constraints_added: int) -> None:
        self.constraints_added = constraints_added


class FakeSummary:
    def __init__(self, constraints_added: int) -> None:
        self.counters = FakeCounters(constraints_added)


class FakeResult:
    def __init__(self, rows=(), added: int = 0) -> None:
        self.rows, self.added = list(rows), added

    def __iter__(self):
        return iter(self.rows)

    def consume(self) -> FakeSummary:
        return FakeSummary(self.added)


class FakeSession:
    def __init__(self, driver: FakeSchemaDriver) -> None:
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def run(self, query: str, **parameters):
        return self.driver.execute(query)


class FakeSchemaDriver:
    """Just the constraint behaviour this module relies on, each point observed live:

    - `CREATE ... IF NOT EXISTS` reports constraints_added 1 for a new constraint, 0 for a no-op;
    - it is a silent no-op when the NAME is taken, even by a different definition;
    - it is also a silent no-op when an equivalent constraint exists under another name.
    """

    CREATE = re.compile(
        r"CREATE CONSTRAINT (\w+) IF NOT EXISTS FOR \(n:(\w+)\) REQUIRE (.+) IS UNIQUE"
    )

    def __init__(self) -> None:
        self.constraints: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {}
        self.statements: list[str] = []
        self.databases: list[str | None] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def verify_connectivity(self) -> None:
        return None

    def session(self, *, database=None):
        self.databases.append(database)
        return FakeSession(self)

    def execute(self, query: str) -> FakeResult:
        self.statements.append(query)
        if query.startswith("CREATE CONSTRAINT"):
            match = self.CREATE.fullmatch(query)
            assert match, f"the fake only models single/composite uniqueness DDL, got: {query}"
            name, label, properties = match.groups()
            keys = tuple(p.strip().removeprefix("n.") for p in properties.strip("()").split(","))
            definition = ("UNIQUENESS", "NODE", (label,), keys)
            if name in self.constraints or definition in self.constraints.values():
                return FakeResult()
            self.constraints[name] = definition
            return FakeResult(added=1)
        if query.startswith("SHOW CONSTRAINTS"):
            return FakeResult(
                {
                    "name": name,
                    "type": kind,
                    "entityType": entity,
                    "labelsOrTypes": list(labels),
                    "properties": list(properties),
                }
                for name, (kind, entity, labels, properties) in self.constraints.items()
            )
        if query.startswith("CALL db.awaitIndexes"):
            return FakeResult()
        if match := re.fullmatch(r"DROP CONSTRAINT (\w+) IF EXISTS", query):
            self.constraints.pop(match.group(1), None)
            return FakeResult()
        raise AssertionError(f"the fake does not model: {query}")


# --- fixtures: the same scenarios on the fake and on the real server ------------------------

_NEO4J_DOWN = neo4j_down()
live = pytest.mark.skipif(_NEO4J_DOWN is not None, reason=_NEO4J_DOWN or "")


def _run(driver, query: str, **parameters) -> None:
    with driver.session() as session:
        session.run(query, **parameters).consume()


def _sweep_scratch(driver) -> None:
    """Remove everything earlier (possibly crashed) test runs left behind.

    Scratch things are found by NAME PREFIX rather than remembered in memory, so a run
    that died before its own cleanup is repaired by the next one. Real labels never
    start with "Scratch", and real constraints never start with "scratch".
    """
    with driver.session() as session:
        names = [
            record["name"]
            for record in session.run(
                "SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'scratch' RETURN name"
            )
        ]
        for name in names:
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()
        session.run(
            "MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Scratch') DETACH DELETE n"
        ).consume()


@pytest.fixture
def live_db():
    """A real driver, with scratch leftovers swept before and after the test."""
    with build_driver() as driver:
        _sweep_scratch(driver)
        try:
            yield driver
        finally:
            _sweep_scratch(driver)


@pytest.fixture(params=["fake", pytest.param("live", marks=[pytest.mark.live, live])])
def schema_db(request):
    if request.param == "fake":
        return FakeSchemaDriver()
    return request.getfixturevalue("live_db")


@pytest.fixture
def scratch() -> tuple[NodeType, NodeType]:
    """One single-key and one composite-key node type on labels no real data uses."""
    token = uuid.uuid4().hex[:8]
    return (
        NodeType(f"Scratch{token}", ("name",), "domain", "throwaway"),
        NodeType(f"ScratchC{token}", ("name", "organization"), "domain", "throwaway"),
    )


# --- the model is internally consistent ------------------------------------------------------


def test_every_node_type_has_a_key_and_the_names_are_distinct():
    labels = [n.label for n in NODE_TYPES]
    assert len(labels) == len(set(labels))
    assert all(n.key for n in NODE_TYPES)
    names = [n.constraint_name for n in NODE_TYPES]
    assert len(names) == len(set(names)), "two node types would fight over one constraint name"


def test_constraint_names_are_stable_and_readable():
    # The name is what SHOW CONSTRAINTS and error messages show, so it is part of the interface.
    by_label = {n.label: n.constraint_name for n in NODE_TYPES}
    assert by_label["Document"] == "document_doc_id_unique"
    assert by_label["Contact"] == "contact_name_organization_unique"
    assert NodeType("JobTitle", ("name",), "domain", "").constraint_name == "job_title_name_unique"


def test_every_relationship_connects_labels_that_exist():
    labels = {n.label for n in NODE_TYPES}
    for rel in RELATIONSHIP_TYPES:
        assert rel.sources and rel.targets, rel.name
        assert set(rel.sources) <= labels, f"{rel.name} starts at an undefined label"
        assert set(rel.targets) <= labels, f"{rel.name} ends at an undefined label"
        assert re.fullmatch(r"[A-Z]+(_[A-Z]+)*", rel.name), f"{rel.name} is not UPPER_SNAKE"


def test_provenance_layer_only_connects_through_the_documented_bridges():
    # Chunks are the meeting point with the vector store: only a Document owns them, and
    # only MENTIONS points from them. Anything else would let graph data drift away from
    # the chunk_id that Qdrant is keyed on.
    chunk_edges = {
        r.name for r in RELATIONSHIP_TYPES if "Chunk" in r.sources or "Chunk" in r.targets
    }
    assert chunk_edges == {"HAS_CHUNK", "MENTIONS"}
    mentions = next(r for r in RELATIONSHIP_TYPES if r.name == "MENTIONS")
    assert mentions.sources == ("Chunk",)
    assert mentions.targets == DOMAIN_LABELS
    assert set(DOMAIN_LABELS) == {
        "Company",
        "Product",
        "Contract",
        "Employee",
        "Contact",
        "Location",
    }


def test_employees_and_contacts_are_different_labels():
    # The corpus has a client-side "Robert Chen" and an Insurellm employee "Robert Chen",
    # and names the Insurellm CEO differently in contracts than in the HR record. One
    # shared label keyed on name would merge them; two labels make that impossible.
    labels = {n.label: n for n in NODE_TYPES}
    assert labels["Employee"].key == ("name",)
    assert labels["Contact"].key == ("name", "organization")


# --- DDL generation --------------------------------------------------------------------------


def test_constraint_statements_are_idempotent_ddl():
    single = NodeType("Company", ("name",), "domain", "")
    composite = NodeType("Contact", ("name", "organization"), "domain", "")
    assert constraint_statement(single) == (
        "CREATE CONSTRAINT company_name_unique IF NOT EXISTS "
        "FOR (n:Company) REQUIRE n.name IS UNIQUE"
    )
    assert constraint_statement(composite) == (
        "CREATE CONSTRAINT contact_name_organization_unique IF NOT EXISTS "
        "FOR (n:Contact) REQUIRE (n.name, n.organization) IS UNIQUE"
    )


@pytest.mark.parametrize(
    "bad",
    [
        NodeType("Company) DETACH DELETE n //", ("name",), "domain", ""),
        NodeType("Company", ("name; DROP DATABASE neo4j",), "domain", ""),
        NodeType("Two Words", ("name",), "domain", ""),
        NodeType("1Company", ("name",), "domain", ""),
    ],
)
def test_identifiers_that_are_not_plain_names_never_reach_the_database(bad):
    # A label or property name in constraint DDL cannot be a parameter, so it is spliced
    # into the text; that is only safe while nothing but plain identifiers can get through.
    driver = FakeSchemaDriver()
    with pytest.raises(ValueError, match="not a plain identifier"):
        apply_schema(driver, (bad,))
    assert driver.statements == []


# --- apply / check: the same scenarios on the fake and on the real server -------------------


def test_first_apply_creates_everything_and_the_second_creates_nothing(schema_db, scratch):
    first = apply_schema(schema_db, scratch)
    assert first.created == tuple(n.constraint_name for n in scratch)
    assert first.present == ()

    second = apply_schema(schema_db, scratch)
    # Reported from the server's own counters, not from a guess: the second run really
    # was a no-op, so re-running the setup is safe.
    assert second.created == ()
    assert second.present == tuple(n.constraint_name for n in scratch)


def test_check_is_ok_after_apply_and_names_every_missing_constraint_before(schema_db, scratch):
    before = check_schema(schema_db, scratch)
    assert not before.ok
    assert before.problems == tuple(
        f"missing {n.constraint_name}: UNIQUENESS on {n.label}({', '.join(n.key)})" for n in scratch
    )

    apply_schema(schema_db, scratch)
    assert check_schema(schema_db, scratch).ok


def test_same_name_with_a_different_definition_is_reported_not_hidden(schema_db, scratch):
    single = scratch[0]
    # A constraint that already uses the expected NAME, but protects another property.
    _run(
        schema_db,
        f"CREATE CONSTRAINT {single.constraint_name} IF NOT EXISTS "
        f"FOR (n:{single.label}) REQUIRE n.other IS UNIQUE",
    )

    # IF NOT EXISTS would report success here. Verifying by definition does not.
    with pytest.raises(SchemaMismatch) as error:
        apply_schema(schema_db, (single,))
    assert single.constraint_name in str(error.value)
    assert f"{single.label}(other)" in str(error.value)
    assert f"{single.label}(name)" in str(error.value)


def test_the_same_rule_under_another_name_counts_as_present(schema_db, scratch):
    single = scratch[0]
    alias = f"scratch_alias_{uuid.uuid4().hex[:8]}"
    _run(
        schema_db,
        f"CREATE CONSTRAINT {alias} IF NOT EXISTS FOR (n:{single.label}) REQUIRE n.name IS UNIQUE",
    )

    report = apply_schema(schema_db, (single,))

    # Nothing was created (the rule already held), yet the model is satisfied, and the
    # alias is not flagged as foreign: it IS the model's rule.
    assert report.created == ()
    status = check_schema(schema_db, (single,))
    assert status.ok and alias not in status.unexpected


def test_unrelated_constraints_are_reported_but_do_not_fail_the_check(schema_db, scratch):
    single = scratch[0]
    extra = f"scratch_extra_{uuid.uuid4().hex[:8]}"
    _run(
        schema_db,
        f"CREATE CONSTRAINT {extra} IF NOT EXISTS "
        f"FOR (n:{single.label}) REQUIRE n.something_else IS UNIQUE",
    )
    apply_schema(schema_db, (single,))

    status = check_schema(schema_db, (single,))

    assert status.ok, "an experiment in the Browser must not break the schema check"
    assert extra in status.unexpected


# --- apply / check: behaviour that only the fake can observe ------------------------------


def test_apply_waits_for_indexes_before_it_verifies(scratch):
    driver = FakeSchemaDriver()
    apply_schema(driver, scratch)

    def position(prefix: str) -> list[int]:
        return [i for i, s in enumerate(driver.statements) if s.startswith(prefix)]

    creates = position("CREATE CONSTRAINT")
    (wait,) = position("CALL db.awaitIndexes")
    (verify,) = position("SHOW CONSTRAINTS")
    # Created is not online: the wait comes after every CREATE and before verification.
    assert len(creates) == len(scratch) and max(creates) < wait < verify


def test_check_is_read_only_and_uses_the_requested_database(scratch):
    driver = FakeSchemaDriver()
    check_schema(driver, scratch, database="graph2")
    assert driver.databases == ["graph2"]
    assert all(s.startswith("SHOW CONSTRAINTS") for s in driver.statements)


def test_a_constraint_of_another_type_under_the_expected_name_is_described(scratch):
    single = scratch[0]
    driver = FakeSchemaDriver()
    driver.constraints[single.constraint_name] = (
        "NODE KEY",
        "NODE",
        (single.label,),
        ("name", "id"),
    )

    status = check_schema(driver, (single,))

    assert not status.ok
    assert "NODE KEY on" in status.problems[0] and "drop it" in status.problems[0]


# --- the CLI -----------------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch):
    driver = FakeSchemaDriver()
    monkeypatch.setattr(graph_schema, "build_driver", lambda uri=None, user=None: driver)
    monkeypatch.setattr(graph_schema, "health_check", lambda d: GraphStatus(ready=True))
    return driver


def test_cli_applies_the_whole_model_and_reports_what_it_did(cli, capsys):
    assert graph_schema.main([]) == 0
    assert len(cli.constraints) == len(NODE_TYPES)
    assert f"{len(NODE_TYPES)} created, 0 already present" in capsys.readouterr().out

    assert graph_schema.main([]) == 0
    assert f"0 created, {len(NODE_TYPES)} already present" in capsys.readouterr().out


def test_cli_check_never_writes_and_exits_1_when_constraints_are_missing(cli, capsys):
    assert graph_schema.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert out.startswith(f"SCHEMA NOT IN FORCE: 0/{len(NODE_TYPES)} constraints")
    assert "missing company_name_unique" in out
    assert all(s.startswith("SHOW CONSTRAINTS") for s in cli.statements)
    assert cli.constraints == {}


def test_cli_check_passes_once_the_schema_is_applied(cli, capsys):
    graph_schema.main([])
    capsys.readouterr()
    assert graph_schema.main(["--check"]) == 0
    assert capsys.readouterr().out.startswith(
        f"schema verified: {len(NODE_TYPES)}/{len(NODE_TYPES)} constraints"
    )


def test_cli_reports_a_mismatch_and_exits_1(cli, capsys):
    cli.constraints["company_name_unique"] = ("UNIQUENESS", "NODE", ("Company",), ("legal_name",))
    assert graph_schema.main([]) == 1
    out = capsys.readouterr().out
    assert out.startswith("SCHEMA MISMATCH:") and "Company(legal_name)" in out


def test_cli_stops_at_the_readiness_problem_without_touching_the_schema(cli, monkeypatch, capsys):
    monkeypatch.setattr(
        graph_schema,
        "health_check",
        lambda d: GraphStatus(ready=False, problem="cannot reach Neo4j"),
    )
    assert graph_schema.main([]) == 1
    assert capsys.readouterr().out == "NOT ready: cannot reach Neo4j\n"
    assert cli.statements == []


def test_cli_lists_constraints_that_are_not_part_of_the_model(cli, capsys):
    cli.constraints["my_experiment"] = ("UNIQUENESS", "NODE", ("Scratch",), ("x",))
    assert graph_schema.main([]) == 0
    assert (
        "note: 1 constraint(s) are not part of the model: my_experiment" in capsys.readouterr().out
    )


def test_cli_has_no_password_flag():
    with pytest.raises(SystemExit) as exit_:
        graph_schema.main(["--password", "secret"])
    assert exit_.value.code == 2


# --- the documentation cannot drift from the model ---------------------------------------------


def _table(marker: str) -> list[list[str]]:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = re.search(rf"<!-- {marker}:start -->(.*?)<!-- {marker}:end -->", text, re.DOTALL)
    assert section, f"docs/architecture.md is missing the {marker} markers"
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.group(1).splitlines()
        if line.startswith("|")
    ]
    # Data rows start with a backticked name; this skips the header and separator rows.
    return [cells for cells in rows if cells[0].startswith("`")]


def _names(cell: str) -> tuple[str, ...]:
    return tuple(part.strip().strip("`") for part in cell.split(",") if part.strip())


def test_architecture_doc_node_table_matches_the_model():
    documented = {
        cells[0].strip("`"): (cells[1], _names(cells[2])) for cells in _table("graph-nodes")
    }
    assert documented == {n.label: (n.layer, n.key) for n in NODE_TYPES}


def test_architecture_doc_relationship_table_matches_the_model():
    documented = {}
    for cells in _table("graph-relationships"):
        left, right = cells[1].split("→")
        documented[cells[0].strip("`")] = (_names(left), _names(right), _names(cells[2]))
    assert documented == {r.name: (r.sources, r.targets, r.properties) for r in RELATIONSHIP_TYPES}


# --- live: the real schema on the real server --------------------------------------------------

TEST_PREFIX = "__test_"


def _delete_test_nodes(driver) -> None:
    """Remove nodes the enforcement tests made, on real labels, by their value prefix."""
    with driver.session() as session:
        for node in NODE_TYPES:
            session.run(
                f"MATCH (n:{node.label}) WHERE n.{node.key[0]} STARTS WITH $prefix DETACH DELETE n",
                prefix=TEST_PREFIX,
            ).consume()


@pytest.fixture
def real_schema():
    """The real model applied to the real server, and the real labels swept before/after."""
    with build_driver() as driver:
        apply_schema(driver)
        _delete_test_nodes(driver)
        try:
            yield driver
        finally:
            _delete_test_nodes(driver)


@pytest.mark.live
@live
def test_live_real_schema_is_in_force_and_reapplying_changes_nothing(real_schema):
    again = apply_schema(real_schema)
    assert again.created == ()
    assert len(again.present) == len(NODE_TYPES)
    assert check_schema(real_schema).ok
    # And the CLI agrees, through its own real driver.
    assert graph_schema.main(["--check"]) == 0


@pytest.mark.live
@live
@pytest.mark.parametrize("node", NODE_TYPES, ids=lambda n: n.label)
def test_live_every_real_constraint_rejects_a_duplicate_key(real_schema, node):
    token = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    properties = {name: f"{token}_{name}" for name in node.key}
    query = f"CREATE (:{node.label} $properties)"

    _run(real_schema, query, properties=properties)
    with pytest.raises(ConstraintError):
        _run(real_schema, query, properties=properties)


@pytest.mark.live
@live
def test_live_namesakes_are_kept_apart_by_organisation_and_by_label(real_schema):
    token = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    contact = "CREATE (:Contact {name: $name, organization: $org})"

    _run(real_schema, contact, name=f"{token}_Robert Chen", org="Fortress")
    # Same name at another organisation: a different person, allowed.
    _run(real_schema, contact, name=f"{token}_Robert Chen", org="Insurellm")
    # The same name as an Employee is a different label, so it cannot collide either.
    _run(real_schema, "CREATE (:Employee {name: $name})", name=f"{token}_Robert Chen")
    # The identical (name, organisation) pair is the one thing refused.
    with pytest.raises(ConstraintError):
        _run(real_schema, contact, name=f"{token}_Robert Chen", org="Fortress")


# --- live: server behaviour the design depends on (a change here means revisiting decisions) ---


@pytest.mark.live
@live
def test_live_community_edition_cannot_enforce_required_properties(live_db, scratch):
    with pytest.raises(DatabaseError) as error:
        _run(
            live_db,
            f"CREATE CONSTRAINT scratch_notnull_{uuid.uuid4().hex[:8]} IF NOT EXISTS "
            f"FOR (n:{scratch[0].label}) REQUIRE n.name IS NOT NULL",
        )
    # If this ever succeeds the server is Enterprise, and the loader-side validation
    # decision (D-2026-09-20-05) should be revisited.
    assert "requires Neo4j Enterprise Edition" in str(error.value)


@pytest.mark.live
@live
def test_live_merge_refuses_a_null_key_but_create_silently_accepts_it(live_db, scratch):
    single, composite = scratch
    apply_schema(live_db, scratch)

    with pytest.raises(ClientError) as merge_error:
        _run(live_db, f"MERGE (:{single.label} {{name: $value}})", value=None)
    assert merge_error.value.code == "Neo.ClientError.Statement.SemanticError"

    # CREATE has no such guard, and a uniqueness constraint ignores a node that lacks a
    # key property. Two keyless "duplicates" therefore coexist: the loader must use
    # MERGE with complete keys, because the database will not catch the gap.
    keyless = f"CREATE (:{composite.label} {{name: 'Ann'}}), (:{composite.label} {{name: 'Ann'}})"
    _run(live_db, keyless)
