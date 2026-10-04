"""Graph ingestion job: read every chunk, extract entities and relationships, merge the
spellings of each entity into one node, and write the result into Neo4j.

A run is a fixed sequence of stages. Each stage is timed, and whatever happens -- success
or failure at any stage -- the report is written to the log, because a graph nobody can
audit is a graph nobody can trust.

    preflight -> load -> extract -> resolve -> validate -> write -> verify

Composition, not new logic: extraction is P4-M4b's `GraphExtractor`, merging is P4-M4's
`resolve_entities`/`remap_edges`, and writing is P4-M3's `GraphStore.upsert_node/upsert_edge`
used exactly as they already are. What this module adds is the order to run them in, the
policy for re-running against a graph that already has data, and a report that says what
actually happened.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.graph.dedup import AliasMap, remap_edges, resolve_entities
from src.graph.extractor import INSURELLM, Extraction, GraphExtractor
from src.graph.records import GraphEdge, GraphNode, NodeRef
from src.graph.schema import NODE_TYPES, RELATIONSHIP_TYPES
from src.graph.setup import GraphDriver, build_driver, health_check
from src.ingestion.pipeline import DEFAULT_OUTPUT_DIR, read_chunks
from src.ingestion.schemas import DocumentChunk
from src.retrieval.graph_store import GraphStore, MissingEndpoint

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("data/graph_ingestion_log.json")

# The 8 node labels this job owns end to end. --rebuild deletes exactly these and nothing
# else: a database is allowed to hold other labels (a developer's experiment) untouched.
MANAGED_LABELS: tuple[str, ...] = tuple(node.label for node in NODE_TYPES)
_RELATIONSHIP_NAMES: tuple[str, ...] = tuple(rel.name for rel in RELATIONSHIP_TYPES)

_Identity = tuple[str, tuple[tuple[str, str], ...]]


class IngestionAborted(RuntimeError):
    """A precondition failed, so the run stopped before it could do damage."""


class ValidationFailed(RuntimeError):
    """An edge names an endpoint that is not among the nodes about to be written."""


class VerificationFailed(RuntimeError):
    """Neo4j, read back after writing, does not hold what Python just wrote."""


@dataclasses.dataclass
class IngestionReport:
    """Everything known about one run.

    Mutable and filled in stage by stage on purpose: when a stage fails, the fields
    gathered before it are exactly what is needed to diagnose it.
    """

    started_at: str
    chunks_dir: str
    rebuild: bool
    status: str = "running"
    failed_stage: str | None = None
    error: str | None = None
    finished_at: str | None = None
    stage_seconds: dict[str, float] = dataclasses.field(default_factory=dict)
    chunks_on_disk: int = 0
    documents_on_disk: int = 0
    extraction_warnings: list[str] = dataclasses.field(default_factory=list)
    nodes_extracted: int = 0
    edges_extracted: int = 0
    ambiguities: list[str] = dataclasses.field(default_factory=list)
    conflicts: list[str] = dataclasses.field(default_factory=list)
    nodes_written_by_label: dict[str, int] = dataclasses.field(default_factory=dict)
    edges_written_by_type: dict[str, int] = dataclasses.field(default_factory=dict)
    nodes_in_db_by_label: dict[str, int] = dataclasses.field(default_factory=dict)
    edges_in_db_by_type: dict[str, int] = dataclasses.field(default_factory=dict)
    verified: bool = False
    notes: list[str] = dataclasses.field(default_factory=list)


def run_ingestion(
    driver: GraphDriver,
    *,
    chunks_dir: Path = DEFAULT_OUTPUT_DIR,
    log_path: Path = DEFAULT_LOG_PATH,
    database: str = "neo4j",
    rebuild: bool = False,
) -> IngestionReport:
    """Extract, merge and write every chunk in `chunks_dir` into Neo4j, then log the run.

    Raises whatever stopped the run, after the log has been written.
    """
    report = IngestionReport(started_at=_now(), chunks_dir=str(chunks_dir), rebuild=rebuild)
    stage = _StageTimer(report)

    try:
        with stage("preflight"):
            status = health_check(driver, database=database)
            if not status.ready:
                raise IngestionAborted(f"Neo4j is not ready: {status.problem}")
            # GraphStore's constructor runs check_schema and raises SchemaMismatch itself;
            # letting that propagate gives the same message `python -m src.graph.schema`
            # would, instead of a second, competing explanation here.
            store = GraphStore(driver, database=database, verify_schema=True)
            existing = _managed_node_count(driver, database)
            if existing and not rebuild:
                raise IngestionAborted(
                    f"the graph already holds {existing} node(s) under this job's labels; "
                    "rerun with --rebuild to delete and reload them, or point at an empty "
                    "database"
                )
            if existing:
                _wipe_managed_labels(driver, database)

        with stage("load"):
            chunks = read_chunks(chunks_dir)
            if not chunks:
                raise IngestionAborted(f"no chunks in {chunks_dir}; run the P2 pipeline first")
            report.chunks_on_disk = len(chunks)
            report.documents_on_disk = len({chunk.doc_id for chunk in chunks})

        with stage("extract"):
            extractor = GraphExtractor.from_chunks(chunks)
            extractions = extractor.extract_all(chunks)
            plan = _plan_writes(chunks, extractions)
            report.extraction_warnings = plan.warnings
            report.nodes_extracted = len(plan.domain_nodes)
            report.edges_extracted = len(plan.domain_edges)

        with stage("resolve"):
            known_names = _known_names(plan.domain_nodes)
            resolution = resolve_entities(plan.domain_nodes, known_names=known_names)
            domain_edges = remap_edges(plan.domain_edges, resolution.alias_map)
            described_in = _remap_refs(plan.described_in, resolution.alias_map)
            mentions = _remap_refs(plan.mentions, resolution.alias_map)
            report.ambiguities = [
                f"{a.label} {a.mention!r} fits {list(a.candidates)}" for a in resolution.ambiguous
            ]
            report.conflicts = [
                f"{c.label} {c.name!r}.{c.property}: kept {c.kept!r}, dropped {c.dropped!r}"
                for c in resolution.conflicts
            ]
            report.notes.append(_ceo_note(resolution.nodes, domain_edges))

        with stage("validate"):
            nodes_to_write = [
                *resolution.nodes,
                *(GraphNode("Document", {"doc_id": d}) for d in plan.doc_ids),
                *(GraphNode("Chunk", {"chunk_id": c}) for c in plan.chunk_ids),
            ]
            edges_to_write = [*plan.has_chunk, *described_in, *domain_edges, *mentions]
            problems = _validate(nodes_to_write, edges_to_write)
            if problems:
                raise ValidationFailed(
                    f"{len(problems)} edge(s) name an endpoint that will not be written: "
                    + "; ".join(problems[:20])
                    + (" ..." if len(problems) > 20 else "")
                )

        with stage("write"):
            _write(store, nodes_to_write, edges_to_write, report)

        with stage("verify"):
            _verify(driver, database, report)

        report.status = "succeeded"
        return report
    except Exception as exc:
        report.status = "failed"
        report.failed_stage = stage.current
        report.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report.finished_at = _now()
        write_log(report, log_path)


# --- turning chunks into a write plan -------------------------------------------------------


@dataclasses.dataclass
class _WritePlan:
    domain_nodes: list[GraphNode]
    domain_edges: list[GraphEdge]
    described_in: list[GraphEdge]
    mentions: list[GraphEdge]
    has_chunk: list[GraphEdge]
    doc_ids: list[str]
    chunk_ids: list[str]
    warnings: list[str]


# Which data/ category is an entity's OWN page, for each label DESCRIBED_IN allows as a
# source (schema.py: Product, Contract, Employee -- a Company or Location has no single home
# page). Keyed on category, not just label: a contract chunk also creates a Product node as
# the FOR_PRODUCT target and an About-page chunk creates an Employee node as a FOUNDED
# source, and neither of those is that entity's own page -- only a chunk of the matching
# category is.
_HOME_CATEGORY = {"Product": "products", "Contract": "contracts", "Employee": "employees"}


def _plan_writes(chunks: Sequence[DocumentChunk], extractions: Sequence[Extraction]) -> _WritePlan:
    domain_nodes: dict[_Identity, GraphNode] = {}
    domain_edges: dict[tuple, GraphEdge] = {}
    described_in: dict[tuple, GraphEdge] = {}
    mentions: dict[tuple, GraphEdge] = {}
    warnings: list[str] = []

    for chunk, extraction in zip(chunks, extractions, strict=True):
        document = NodeRef("Document", {"doc_id": chunk.doc_id})
        for node in extraction.nodes:
            key = (node.label, tuple(sorted(node.key.items())))
            earlier = domain_nodes.get(key)
            props = {**(dict(earlier.properties) if earlier else {}), **node.properties}
            domain_nodes[key] = GraphNode(node.label, node.key, props)
            if _HOME_CATEGORY.get(node.label) == chunk.category:
                described_key = (*key, chunk.doc_id)
                described_in[described_key] = GraphEdge("DESCRIBED_IN", node.ref, document)
        for edge in extraction.edges:
            edge_key = (
                edge.type,
                (edge.source.label, tuple(sorted(edge.source.key.items()))),
                (edge.target.label, tuple(sorted(edge.target.key.items()))),
            )
            earlier_edge = domain_edges.get(edge_key)
            extra = {
                k: v
                for k, v in edge.properties.items()
                if not earlier_edge or k not in earlier_edge.properties
            }
            domain_edges[edge_key] = GraphEdge(
                edge.type,
                edge.source,
                edge.target,
                {**(dict(earlier_edge.properties) if earlier_edge else {}), **extra},
            )
        for ref in extraction.mentions:
            mentions[(chunk.chunk_id, ref.label, tuple(sorted(ref.key.items())))] = GraphEdge(
                "MENTIONS", NodeRef("Chunk", {"chunk_id": chunk.chunk_id}), ref
            )
        warnings.extend(f"{chunk.chunk_id}: {w}" for w in extraction.warnings)

    doc_ids = sorted({chunk.doc_id for chunk in chunks})
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    has_chunk = [
        GraphEdge(
            "HAS_CHUNK",
            NodeRef("Document", {"doc_id": chunk.doc_id}),
            NodeRef("Chunk", {"chunk_id": chunk.chunk_id}),
        )
        for chunk in chunks
    ]
    return _WritePlan(
        domain_nodes=list(domain_nodes.values()),
        domain_edges=list(domain_edges.values()),
        described_in=list(described_in.values()),
        mentions=list(mentions.values()),
        has_chunk=has_chunk,
        doc_ids=doc_ids,
        chunk_ids=chunk_ids,
        warnings=warnings,
    )


def _known_names(nodes: Sequence[GraphNode]) -> Mapping[str, tuple[str, ...]]:
    # INTERVIEW: known_names pins a spelling as canonical BEFORE resolve_entities picks one
    # by heuristic. The client names and the roster names are exactly the names the extractor
    # already wrote (from contract titles and from HR file stems, respectively), so reusing
    # them here -- rather than re-deriving from the corpus a second way -- means there is
    # only one place that decides what a client or an employee is called.
    clients = sorted(
        {n.key["name"] for n in nodes if n.label == "Company" and n.key["name"] != INSURELLM}
    )
    employees = sorted({n.key["name"] for n in nodes if n.label == "Employee"})
    return {"Company": (INSURELLM, *clients), "Employee": tuple(employees)}


def _remap_refs(edges: Sequence[GraphEdge], alias_map: AliasMap) -> list[GraphEdge]:
    remapped: dict[tuple, GraphEdge] = {}
    for edge in edges:
        source, target = alias_map.resolve(edge.source), alias_map.resolve(edge.target)
        key = (edge.type, _identity(source), _identity(target))
        remapped.setdefault(key, GraphEdge(edge.type, source, target, dict(edge.properties)))
    return list(remapped.values())


def _ceo_note(nodes: Sequence[GraphNode], domain_edges: Sequence[GraphEdge]) -> str:
    # D-2026-09-21 (P4-M5, D12a): the corpus states the CEO two ways and they disagree. Both
    # are loaded as written -- neither is corrected -- and the disagreement is recorded here
    # rather than silently resolved, because deciding which fact is right is a retrieval-time
    # or a human question, not an ingestion-time one.
    lancaster = next(
        (n for n in nodes if n.label == "Employee" and n.key.get("name") == "Avery Lancaster"),
        None,
    )
    hr_title = lancaster.properties.get("job_title") if lancaster else None
    signed_as_ceo = sorted(
        {
            edge.properties.get("title", "")
            for edge in domain_edges
            if edge.type == "SIGNED_BY"
            and edge.target.key.get("name") == "Jennifer Rodriguez"
            and "chief executive" in str(edge.properties.get("title", "")).casefold()
        }
    )
    count = sum(
        1
        for edge in domain_edges
        if edge.type == "SIGNED_BY"
        and edge.target.key.get("name") == "Jennifer Rodriguez"
        and "chief executive" in str(edge.properties.get("title", "")).casefold()
    )
    return (
        f"CEO contradiction: HR records Avery Lancaster as {hr_title!r}, while Jennifer "
        f"Rodriguez signs {count} contract(s) as {signed_as_ceo!r}. Both are loaded as written."
    )


# --- validation before any write -------------------------------------------------------------


def _identity(ref: NodeRef) -> _Identity:
    return ref.label, tuple(sorted(ref.key.items()))


def _validate(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> list[str]:
    known = {_identity(node.ref) for node in nodes}
    problems = []
    for edge in edges:
        for ref, role in ((edge.source, "source"), (edge.target, "target")):
            if _identity(ref) not in known:
                problems.append(f"{edge.type}: {role} {ref} is not among the nodes to be written")
    return problems


# --- writing and verifying -----------------------------------------------------------------


def _write(
    store: GraphStore,
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    report: IngestionReport,
) -> None:
    # Nodes before edges: GraphStore.upsert_edge MATCHes its endpoints rather than MERGEing
    # them (P4-M3, D-2026-09-20), so an edge written before its node raises MissingEndpoint --
    # the `validate` stage should have already caught every such case.
    by_label, by_type = report.nodes_written_by_label, report.edges_written_by_type
    for node in nodes:
        store.upsert_node(node)
        by_label[node.label] = by_label.get(node.label, 0) + 1
    for edge in edges:
        try:
            store.upsert_edge(edge)
        except MissingEndpoint as exc:
            # Validation passed, so reaching this is a bug in the plan, not bad input data;
            # it is re-raised with the same message the store already built.
            raise ValidationFailed(str(exc)) from exc
        by_type[edge.type] = by_type.get(edge.type, 0) + 1


def _verify(driver: GraphDriver, database: str, report: IngestionReport) -> None:
    report.nodes_in_db_by_label = _counts_by_label(driver, database)
    report.edges_in_db_by_type = _counts_by_type(driver, database)
    node_mismatch = {
        label: (report.nodes_written_by_label.get(label, 0), report.nodes_in_db_by_label[label])
        for label in MANAGED_LABELS
        if report.nodes_written_by_label.get(label, 0) != report.nodes_in_db_by_label[label]
    }
    edge_mismatch = {
        name: (report.edges_written_by_type.get(name, 0), report.edges_in_db_by_type.get(name, 0))
        for name in _RELATIONSHIP_NAMES
        if report.edges_written_by_type.get(name, 0) != report.edges_in_db_by_type.get(name, 0)
    }
    if node_mismatch or edge_mismatch:
        # INTERVIEW: "no exception during write" is not evidence the graph is right -- an
        # upsert can MERGE two writes into fewer rows than expected if a key collided
        # unexpectedly. Comparing counts Python thinks it wrote against what a fresh read
        # of Neo4j reports catches that even though every individual call succeeded.
        raise VerificationFailed(
            f"Neo4j does not match what was written: node counts {node_mismatch}, "
            f"edge counts {edge_mismatch}"
        )
    report.verified = True


def _managed_node_count(driver: GraphDriver, database: str) -> int:
    with driver.session(database=database) as session:
        return session.run(
            "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN count(n) AS c",
            labels=list(MANAGED_LABELS),
        ).single()["c"]


def _wipe_managed_labels(driver: GraphDriver, database: str) -> None:
    with driver.session(database=database) as session:
        for label in MANAGED_LABELS:
            session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()


def _counts_by_label(driver: GraphDriver, database: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with driver.session(database=database) as session:
        for label in MANAGED_LABELS:
            counts[label] = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
    return counts


def _counts_by_type(driver: GraphDriver, database: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with driver.session(database=database) as session:
        for name in _RELATIONSHIP_NAMES:
            query = f"MATCH ()-[r:{name}]->() RETURN count(r) AS c"
            counts[name] = session.run(query).single()["c"]
    return counts


# --- logging, CLI, plumbing (mirrors src/vectorstore/ingest.py) ------------------------------


def write_log(report: IngestionReport, log_path: Path = DEFAULT_LOG_PATH) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(dataclasses.asdict(report), indent=2), encoding="utf-8")


class _StageTimer:
    def __init__(self, report: IngestionReport) -> None:
        self._report = report
        self.current: str | None = None

    @contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        self.current = name
        started = time.perf_counter()
        try:
            yield
        finally:
            self._report.stage_seconds[name] = round(time.perf_counter() - started, 3)
        logger.info("stage %s finished in %.2fs", name, self._report.stage_seconds[name])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarise(report: IngestionReport) -> str:
    stages = ", ".join(f"{name} {seconds:.2f}s" for name, seconds in report.stage_seconds.items())
    lines = [
        f"{report.status.upper()}: {report.chunks_on_disk} chunks from "
        f"{report.documents_on_disk} documents -> Neo4j",
        f"  nodes written: {report.nodes_written_by_label}",
        f"  edges written: {report.edges_written_by_type}",
        f"  ambiguities {len(report.ambiguities)}, conflicts {len(report.conflicts)}, "
        f"warnings {len(report.extraction_warnings)}; verified={report.verified}",
        f"  stages: {stages}",
    ]
    lines += [f"  note: {note}" for note in report.notes]
    if report.error:
        lines.append(f"  failed in {report.failed_stage}: {report.error}")
    return "\n".join(lines)


def cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract entities/relationships from all chunks and load them into Neo4j."
    )
    parser.add_argument("--chunks-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--uri", default=None, help="defaults to NEO4J_URI from .env")
    parser.add_argument("--user", default=None, help="defaults to NEO4J_USER from .env")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="delete this job's 8 node labels first if the graph already holds any",
    )
    return parser.parse_args(argv)


def run_from_args(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("neo4j").setLevel(logging.WARNING)

    uri = args.uri or settings.neo4j_uri
    with build_driver(uri, args.user) as driver:
        try:
            report = run_ingestion(
                driver,
                chunks_dir=args.chunks_dir,
                log_path=args.log,
                database=args.database,
                rebuild=args.rebuild,
            )
        except Exception:
            logger.exception("graph ingestion failed")
            data: dict[str, Any] = json.loads(args.log.read_text(encoding="utf-8"))
            print(summarise(IngestionReport(**data)))
            print(f"  log: {args.log}")
            return 1
    print(summarise(report))
    print(f"  log: {args.log}")
    return 0


def main() -> None:
    """CLI: python -m src.graph.ingest [--rebuild]"""
    raise SystemExit(run_from_args(cli_args()))


if __name__ == "__main__":
    main()
