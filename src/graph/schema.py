"""The graph model as code: which node labels exist, what identifies each one, which
relationships may connect them, and the constraints that make that identity real.

Two layers, joined by chunk IDs:

- provenance -- `Document` and `Chunk`: where text came from. They hold IDs only.
  Chunk text stays in Qdrant, its single owner, so a graph hit and a vector hit meet
  on `chunk_id` (Qdrant's point id is derived from it by `vectorstore.store.point_id`).
- domain -- `Company`, `Product`, `Contract`, `Employee`, `Contact`, `Location`: the
  things the documents talk about.

The model is closed on purpose. It was derived from an audit of the real corpus (see
notes/step-4.2-graph-schema.md), and it is what makes constraints meaningful: an
open-ended extractor would invent labels faster than a constraint could guard them.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Literal

from src.core.config import settings
from src.graph.setup import GraphDriver, build_driver, health_check

# In constraint DDL a label or property name cannot be a parameter or a dynamic expression
# (observed on 5.26: both are syntax errors), so they are spliced into the statement text.
# Restricting them to plain identifiers keeps that safe even if a model is ever built from
# something other than this file's constants. The constraint name is spliced too, for one
# uniform rule, although 5.26 would accept it as a parameter.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# `CREATE CONSTRAINT` returns once the constraint exists, but over existing data its
# backing index can still be populating. Waiting makes "applied" mean "enforced".
AWAIT_INDEXES_SECONDS = 60


@dataclass(frozen=True)
class NodeType:
    label: str
    # The properties that together identify one node. Every one must be set on every
    # node: Community Edition cannot enforce that (decision D-2026-09-20-05), so the
    # loader has to, by MERGEing with complete keys.
    key: tuple[str, ...]
    layer: Literal["provenance", "domain"]
    meaning: str

    @property
    def constraint_name(self) -> str:
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", self.label).lower()
        return f"{snake}_{'_'.join(self.key)}_unique"


@dataclass(frozen=True)
class RelationshipType:
    name: str
    sources: tuple[str, ...]
    targets: tuple[str, ...]
    meaning: str
    properties: tuple[str, ...] = ()


NODE_TYPES: tuple[NodeType, ...] = (
    NodeType(
        "Document",
        ("doc_id",),
        "provenance",
        "One source file under data/. doc_id is the 16-character SHA-256 of its path, the "
        "same value the chunk schema carries.",
    ),
    NodeType(
        "Chunk",
        ("chunk_id",),
        "provenance",
        "One chunk of a document, identified as '{source_path}_{chunk_index}'. Holds no "
        "text: the text lives in Qdrant.",
    ),
    NodeType(
        "Company",
        ("name",),
        "domain",
        "Insurellm and its clients. name is the short form used in contract titles; the "
        "legal name that appears in the contract body is a property.",
    ),
    NodeType(
        "Product",
        ("name",),
        "domain",
        "One of the eight products. Names come from the closed list of files in "
        "data/products/, because contract titles sometimes append a tagline.",
    ),
    NodeType(
        "Contract",
        ("doc_id",),
        "domain",
        "One contract, identified by its source document: contract numbers are missing "
        "from many contracts, so a number cannot be the identity.",
    ),
    NodeType(
        "Employee",
        ("name",),
        "domain",
        "A person on the HR roster (data/employees/). Nobody who merely appears in a "
        "contract is an Employee.",
    ),
    NodeType(
        "Contact",
        ("name", "organization"),
        "domain",
        "A person named in a contract, such as a signatory. Identity is name plus "
        "organisation, because namesakes exist: a client's 'Robert Chen' is not Insurellm's.",
    ),
    NodeType(
        "Location",
        ("name",),
        "domain",
        "A city, normalised to 'City, State' with the full state name, because the "
        "corpus writes San Francisco both ways.",
    ),
)

DOMAIN_LABELS: tuple[str, ...] = tuple(n.label for n in NODE_TYPES if n.layer == "domain")

RELATIONSHIP_TYPES: tuple[RelationshipType, ...] = (
    RelationshipType(
        "HAS_CHUNK", ("Document",), ("Chunk",), "The document was split into this chunk."
    ),
    RelationshipType(
        "MENTIONS",
        ("Chunk",),
        DOMAIN_LABELS,
        "The chunk's text names this entity. The bridge from a graph hit to the vector "
        "store: follow it to a chunk_id.",
    ),
    RelationshipType(
        "DESCRIBED_IN",
        ("Product", "Contract", "Employee"),
        ("Document",),
        "The entity's own page: its product page, its contract, its HR record.",
    ),
    RelationshipType(
        "FOR_PRODUCT", ("Contract",), ("Product",), "The product a contract licenses."
    ),
    RelationshipType(
        "WITH_CLIENT",
        ("Contract",),
        ("Company",),
        "The client on a contract. Insurellm is always the other party, so it is not stored.",
    ),
    RelationshipType(
        "SIGNED_BY",
        ("Contract",),
        ("Contact",),
        "A person who signed the contract. title is the title as written in that contract.",
        properties=("title",),
    ),
    RelationshipType(
        "WORKS_ON",
        ("Employee",),
        ("Product",),
        "The employee's role description names this product.",
    ),
    RelationshipType(
        "BASED_IN",
        ("Employee",),
        ("Location",),
        "Where the employee works from. remote is true for remote employees.",
        properties=("remote",),
    ),
    RelationshipType(
        "HAS_OFFICE",
        ("Company",),
        ("Location",),
        "A company office. headquarters marks the head office.",
        properties=("headquarters",),
    ),
    RelationshipType(
        "FOUNDED",
        ("Employee",),
        ("Company",),
        "The employee co-founded the company (stated in both the About page and the HR record).",
    ),
)


class SchemaMismatch(RuntimeError):
    """The database's constraints differ from this model in a way `IF NOT EXISTS` hides.

    `CREATE CONSTRAINT ... IF NOT EXISTS` is a silent no-op when a constraint of the
    same name exists with a different definition (observed on the live server), so
    creating alone would report success while the expected rule is not enforced.
    """


@dataclass(frozen=True)
class SchemaReport:
    created: tuple[str, ...]
    present: tuple[str, ...]


@dataclass(frozen=True)
class SchemaStatus:
    # One sentence per expected constraint that is not in force.
    problems: tuple[str, ...] = ()
    # Constraints in the database that this model does not define. Informational: a
    # developer's experiment should not make the schema check fail.
    unexpected: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{value!r} is not a plain identifier and cannot be used in DDL")
    return value


def constraint_statement(node: NodeType) -> str:
    """The idempotent DDL that makes `node.key` unique for `node.label`."""
    properties = [f"n.{_identifier(p)}" for p in node.key]
    target = properties[0] if len(properties) == 1 else f"({', '.join(properties)})"
    return (
        f"CREATE CONSTRAINT {_identifier(node.constraint_name)} IF NOT EXISTS "
        f"FOR (n:{_identifier(node.label)}) REQUIRE {target} IS UNIQUE"
    )


def apply_schema(
    driver: GraphDriver,
    node_types: tuple[NodeType, ...] = NODE_TYPES,
    database: str = "neo4j",
) -> SchemaReport:
    """Create every missing constraint, then verify the result. Safe to re-run.

    Raises SchemaMismatch if, afterwards, an expected constraint is still not in force.
    """
    created: list[str] = []
    present: list[str] = []
    # Each statement runs on its own (auto-commit): Neo4j forbids schema changes in a
    # transaction that has already written (observed: ForbiddenDueToTransactionType).
    with driver.session(database=database) as session:
        for node in node_types:
            counters = session.run(constraint_statement(node)).consume().counters
            # constraints_added is 1 for a new constraint and 0 for a no-op; a no-op is
            # only trusted after check_schema compares the definitions below.
            (created if counters.constraints_added else present).append(node.constraint_name)
        session.run("CALL db.awaitIndexes($seconds)", seconds=AWAIT_INDEXES_SECONDS).consume()

    status = check_schema(driver, node_types, database)
    if not status.ok:
        raise SchemaMismatch("; ".join(status.problems))
    return SchemaReport(created=tuple(created), present=tuple(present))


def check_schema(
    driver: GraphDriver,
    node_types: tuple[NodeType, ...] = NODE_TYPES,
    database: str = "neo4j",
) -> SchemaStatus:
    """Compare the database's constraints with the model. Read-only.

    Constraints are matched by definition, not by name: the same rule under another
    name enforces exactly the same thing (also a silent no-op for IF NOT EXISTS).
    """
    with driver.session(database=database) as session:
        rows = list(
            session.run(
                "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties "
                "RETURN name, type, entityType, labelsOrTypes, properties"
            )
        )

    by_definition = {
        (
            row["type"],
            row["entityType"],
            tuple(row["labelsOrTypes"]),
            tuple(row["properties"]),
        ): row["name"]
        for row in rows
    }
    by_name = {row["name"]: row for row in rows}

    problems, expected_definitions = [], set()
    for node in node_types:
        definition = ("UNIQUENESS", "NODE", (node.label,), node.key)
        expected_definitions.add(definition)
        if definition in by_definition:
            continue
        found = by_name.get(node.constraint_name)
        wanted = f"UNIQUENESS on {node.label}({', '.join(node.key)})"
        if found is None:
            problems.append(f"missing {node.constraint_name}: {wanted}")
        else:
            problems.append(
                f"{node.constraint_name} exists but is {found['type']} on "
                f"{'/'.join(found['labelsOrTypes'])}({', '.join(found['properties'])}), "
                f"not {wanted}; drop it to let the model recreate it"
            )

    unexpected = sorted(
        name for definition, name in by_definition.items() if definition not in expected_definitions
    )
    return SchemaStatus(problems=tuple(problems), unexpected=tuple(unexpected))


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m src.graph.schema [--check] -- exit 0 if the schema is in force."""
    parser = argparse.ArgumentParser(description="Apply and verify the graph schema constraints.")
    parser.add_argument("--check", action="store_true", help="verify only; never write")
    parser.add_argument("--uri", default=None, help="defaults to NEO4J_URI from .env")
    parser.add_argument("--user", default=None, help="defaults to NEO4J_USER from .env")
    args = parser.parse_args(argv)

    uri = args.uri or settings.neo4j_uri
    with build_driver(uri, args.user) as driver:
        # Reuse the P4-M1 readiness check so a wrong password or a stopped container
        # gets its specific explanation instead of a stack trace.
        status = health_check(driver)
        if not status.ready:
            print(f"NOT ready: {status.problem}")
            return 1

        if args.check:
            result = check_schema(driver)
            headline = "schema verified" if result.ok else "SCHEMA NOT IN FORCE"
            in_force = len(NODE_TYPES) - len(result.problems)
            print(f"{headline}: {in_force}/{len(NODE_TYPES)} constraints")
            for problem in result.problems:
                print(f"  - {problem}")
            _print_unexpected(result.unexpected)
            return 0 if result.ok else 1

        try:
            report = apply_schema(driver)
        except SchemaMismatch as exc:
            print(f"SCHEMA MISMATCH: {exc}")
            return 1
        print(
            f"schema applied: {len(NODE_TYPES)} constraints in force "
            f"({len(report.created)} created, {len(report.present)} already present)"
        )
        _print_unexpected(check_schema(driver).unexpected)
        return 0


def _print_unexpected(names: tuple[str, ...]) -> None:
    if names:
        print(f"note: {len(names)} constraint(s) are not part of the model: {', '.join(names)}")


if __name__ == "__main__":
    sys.exit(main())
