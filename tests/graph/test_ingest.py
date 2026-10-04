"""Tests for the graph ingestion job (P4-M5).

Split the same way as `tests/retrieval/test_graph_store.py`: a fake driver checks the
ORCHESTRATION -- what preflight decides, what --rebuild sends, that a bad plan is refused
before any write -- and the live test (skipped when Neo4j is down) checks what Neo4j then
DOES, against the real corpus, and pins the exact counts this job is known to produce.

The pure planning functions (`_plan_writes`, `_known_names`, `_remap_refs`, `_ceo_note`,
`_validate`) need neither a fake nor a live database: they are plain functions from chunks
and records to more records, and are tested directly.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from neo4j.exceptions import ServiceUnavailable

from src.graph.dedup import AliasMap
from src.graph.extractor import GraphExtractor
from src.graph.ingest import (
    MANAGED_LABELS,
    IngestionAborted,
    _ceo_note,
    _known_names,
    _managed_node_count,
    _plan_writes,
    _remap_refs,
    _validate,
    run_ingestion,
)
from src.graph.records import GraphEdge, GraphNode, NodeRef
from src.graph.schema import NODE_TYPES
from src.graph.setup import build_driver
from src.ingestion.schemas import DocumentChunk, compute_chunk_id, compute_doc_id
from tests.support import neo4j_down

# --- fixtures shared by the planning tests ----------------------------------------------------

EX = GraphExtractor(
    products=["Carllm"],
    employees=["Avery Lancaster"],
    cities={"San Francisco": "San Francisco, California"},
)


def make(category: str, path: str, text: str, *, index: int = 0, title: str | None = None):
    return DocumentChunk(
        chunk_id=compute_chunk_id(path, index),
        doc_id=compute_doc_id(path),
        source_path=path,
        category=category,
        title=title or PurePosixPath(path).stem,
        chunk_index=index,
        text=text,
        char_count=len(text),
        approx_token_count=0,
    )


def contract(title: str, text: str, *, index: int = 0):
    return make("contracts", f"contracts/{title}.md", text, index=index)


def employee(name: str, text: str, *, index: int = 0):
    body = f"# HR Record\n\n# {name}\n\n{text}"
    return make("employees", f"employees/{name}.md", body, index=index)


def product_page(name: str, text: str, *, index: int = 0):
    return make("products", f"products/{name}.md", text, index=index)


def company_page(name: str, text: str, *, index: int = 0):
    return make("company", f"company/{name}.md", text, index=index)


# --- _plan_writes: DESCRIBED_IN must come from the entity's OWN page, nothing else -----------


def test_described_in_ignores_a_product_named_only_as_a_contract_target():
    title = "Contract with Acme for Carllm"
    contract_chunk = contract(title, f"# {title}\n\nTerms.")
    product_chunk = product_page("Carllm", "# Carllm\n\nOur flagship product.")
    chunks = [contract_chunk, product_chunk]
    extractions = EX.extract_all(chunks)

    plan = _plan_writes(chunks, extractions)

    # Regression for the bug this suite caught on the real corpus: the contract chunk also
    # creates a Product node (as the FOR_PRODUCT target), and that must NOT count as Carllm's
    # own page -- only the products/Carllm.md chunk does.
    described = [e for e in plan.described_in if e.source.label == "Product"]
    assert len(described) == 1
    assert described[0].target.key == {"doc_id": product_chunk.doc_id}


def test_described_in_ignores_an_employee_named_only_as_a_founder():
    about = company_page("about", "Insurellm was founded by Avery Lancaster in 2015.")
    hr = employee("Avery Lancaster", "- **Job Title**: CEO\n- **Location**: San Francisco, CA\n")
    chunks = [about, hr]
    extractions = EX.extract_all(chunks)

    plan = _plan_writes(chunks, extractions)

    described = [e for e in plan.described_in if e.source.label == "Employee"]
    assert len(described) == 1
    assert described[0].target.key == {"doc_id": hr.doc_id}


def test_described_in_collapses_multiple_chunks_of_the_same_document_into_one_edge():
    title = "Contract with Acme for Carllm"
    signatures = (
        "**Signatures:**\n\n_____________\n**Nora Bishop**\n**Title**: VP Sales\n"
        "**Insurellm, Inc.**\n**Date**: January 1, 2025\n"
    )
    chunks = [
        contract(title, f"# {title}\n\nTerms.", index=0),
        contract(title, signatures, index=1),
    ]
    extractions = EX.extract_all(chunks)

    plan = _plan_writes(chunks, extractions)

    described = [e for e in plan.described_in if e.source.label == "Contract"]
    assert len(described) == 1


def test_has_chunk_covers_every_chunk_exactly_once():
    title = "Contract with Acme for Carllm"
    chunks = [
        contract(title, f"# {title}\n\nTerms.", index=0),
        contract(title, "More.", index=1),
    ]
    plan = _plan_writes(chunks, EX.extract_all(chunks))
    assert {e.target.key["chunk_id"] for e in plan.has_chunk} == {c.chunk_id for c in chunks}


# --- _known_names, _remap_refs, _ceo_note, _validate: pure and infra-free --------------------


def test_known_names_pulls_client_and_employee_names_from_the_planned_nodes():
    nodes = [
        GraphNode("Company", {"name": "Insurellm"}),
        GraphNode("Company", {"name": "Acme Corp"}),
        GraphNode("Employee", {"name": "Avery Lancaster"}),
        GraphNode("Product", {"name": "Carllm"}),
    ]
    assert _known_names(nodes) == {
        "Company": ("Insurellm", "Acme Corp"),
        "Employee": ("Avery Lancaster",),
    }


def test_remap_refs_resolves_through_the_alias_map_and_dedupes():
    alias_map = AliasMap(
        {("Company", (("name", "Acme Corp Inc"),)): NodeRef("Company", {"name": "Acme Corp"})}
    )
    chunk_ref = NodeRef("Chunk", {"chunk_id": "c_0"})
    edges = [
        GraphEdge("MENTIONS", chunk_ref, NodeRef("Company", {"name": "Acme Corp Inc"})),
        GraphEdge("MENTIONS", chunk_ref, NodeRef("Company", {"name": "Acme Corp"})),
    ]

    remapped = _remap_refs(edges, alias_map)

    assert len(remapped) == 1
    assert remapped[0].target.key == {"name": "Acme Corp"}


def test_ceo_note_reports_both_conflicting_facts():
    nodes = [GraphNode("Employee", {"name": "Avery Lancaster"}, {"job_title": "CEO"})]
    jennifer = NodeRef("Contact", {"name": "Jennifer Rodriguez", "organization": "Insurellm"})
    other = NodeRef("Contact", {"name": "Someone Else", "organization": "Insurellm"})
    ceo = {"title": "Chief Executive Officer"}
    edges = [
        GraphEdge("SIGNED_BY", NodeRef("Contract", {"doc_id": "d1"}), jennifer, ceo),
        GraphEdge("SIGNED_BY", NodeRef("Contract", {"doc_id": "d2"}), jennifer, ceo),
        GraphEdge("SIGNED_BY", NodeRef("Contract", {"doc_id": "d3"}), other, {"title": "VP Sales"}),
    ]

    note = _ceo_note(nodes, edges)

    assert "'CEO'" in note
    assert "2 contract(s)" in note
    assert "Jennifer Rodriguez" in note


def test_validate_catches_an_edge_whose_endpoint_will_not_be_written():
    nodes = [GraphNode("Company", {"name": "Insurellm"})]
    edges = [
        GraphEdge(
            "HAS_OFFICE",
            NodeRef("Company", {"name": "Insurellm"}),
            NodeRef("Location", {"name": "Austin, Texas"}),
            {"headquarters": False},
        )
    ]

    problems = _validate(nodes, edges)

    assert len(problems) == 1
    assert "Location" in problems[0] and "target" in problems[0]


def test_validate_passes_when_every_endpoint_is_among_the_planned_nodes():
    insurellm = GraphNode("Company", {"name": "Insurellm"})
    austin = GraphNode("Location", {"name": "Austin, Texas"})
    edge = GraphEdge("HAS_OFFICE", insurellm.ref, austin.ref, {"headquarters": False})
    assert _validate([insurellm, austin], [edge]) == []


# --- orchestration: a fake driver checks what preflight decides, not what Neo4j does ---------


class FakeRecord(dict):
    def data(self):
        return dict(self)


class FakeResult:
    def __init__(self, rows):
        self.rows = [FakeRecord(row) for row in rows]

    def __iter__(self):
        return iter(self.rows)

    def single(self, strict=False):
        if strict and len(self.rows) != 1:
            raise AssertionError(f"expected exactly one row, got {len(self.rows)}")
        return self.rows[0] if self.rows else None

    def consume(self):
        return None


def _constraint_rows():
    return [
        {
            "name": node.constraint_name,
            "type": "UNIQUENESS",
            "entityType": "NODE",
            "labelsOrTypes": [node.label],
            "properties": list(node.key),
        }
        for node in NODE_TYPES
    ]


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def run(self, query, **params):
        self.driver.calls.append((query, dict(params)))
        return FakeResult(self.driver.reply(query))

    def execute_write(self, work):
        raise AssertionError("this test's chunks_dir is empty, so no write should be attempted")

    def execute_read(self, work):
        raise AssertionError("this test's chunks_dir is empty, so no read should be attempted")


class FakeIngestDriver:
    """Answers exactly the fixed queries `run_ingestion` sends during preflight."""

    def __init__(self, *, managed_count: int):
        self.calls: list[tuple[str, dict]] = []
        self._managed_count = managed_count

    def verify_connectivity(self) -> None:
        return None

    def session(self, *, database=None):
        return FakeSession(self)

    def reply(self, query: str):
        if "dbms.components" in query:
            return [{"version": "5.26.0", "edition": "community"}]
        if "apoc.version" in query:
            return [{"version": "5.26.0"}]
        if "SHOW CONSTRAINTS" in query:
            return _constraint_rows()
        if "any(l IN labels(n)" in query:
            return [{"c": self._managed_count}]
        if "DETACH DELETE" in query:
            return []
        raise AssertionError(f"unexpected query in this test: {query}")


class _UnreachableDriver:
    def verify_connectivity(self) -> None:
        raise ServiceUnavailable("no route to host")

    def session(self, *, database=None):
        raise AssertionError("verify_connectivity should fail before a session is opened")


def test_preflight_aborts_when_neo4j_is_not_reachable(tmp_path):
    with pytest.raises(IngestionAborted, match="not ready"):
        run_ingestion(_UnreachableDriver(), chunks_dir=tmp_path, log_path=tmp_path / "log.json")

    report = (tmp_path / "log.json").read_text(encoding="utf-8")
    assert '"status": "failed"' in report
    assert '"failed_stage": "preflight"' in report


def test_preflight_refuses_a_non_empty_graph_without_rebuild(tmp_path):
    driver = FakeIngestDriver(managed_count=442)

    with pytest.raises(IngestionAborted, match="442 node"):
        run_ingestion(driver, chunks_dir=tmp_path, log_path=tmp_path / "log.json", rebuild=False)

    assert not any("DETACH DELETE" in query for query, _ in driver.calls)


def test_rebuild_wipes_exactly_the_eight_managed_labels_before_loading(tmp_path):
    driver = FakeIngestDriver(managed_count=442)

    # The chunks_dir is empty, so the run aborts at "load" -- late enough to prove the wipe
    # already ran, without needing to fake the extract/resolve/write stages too.
    with pytest.raises(IngestionAborted, match="no chunks"):
        run_ingestion(driver, chunks_dir=tmp_path, log_path=tmp_path / "log.json", rebuild=True)

    wiped = [query for query, _ in driver.calls if "DETACH DELETE" in query]
    assert len(wiped) == len(MANAGED_LABELS) == 8
    assert {f"MATCH (n:{label}) DETACH DELETE n" for label in MANAGED_LABELS} == set(wiped)


def test_managed_node_count_reads_the_count_the_query_returns(tmp_path):
    driver = FakeIngestDriver(managed_count=17)
    assert _managed_node_count(driver, "neo4j") == 17


# --- live: what Neo4j actually ends up holding, on the real corpus ---------------------------

CHUNKS_DIR = Path("data/chunks")


@pytest.mark.live
def test_ingestion_on_the_real_corpus_matches_the_audited_counts(tmp_path):
    reason = neo4j_down()
    if reason:
        pytest.skip(reason)
    if not CHUNKS_DIR.exists() or not any(CHUNKS_DIR.glob("*.json")):
        pytest.skip("data/chunks is empty; run the P2 pipeline first")

    with build_driver() as driver:
        # This job owns the whole graph (D-2026-09-21, P4-M5, D8a): --rebuild replaces
        # whatever is there, including a previous test's or a previous manual run's data.
        log_path = tmp_path / "log.json"
        report = run_ingestion(driver, chunks_dir=CHUNKS_DIR, log_path=log_path, rebuild=True)

        assert report.status == "succeeded"
        assert report.verified
        assert report.chunks_on_disk == 223
        assert report.documents_on_disk == 76
        assert report.nodes_written_by_label == {
            "Company": 33,
            "Contact": 29,
            "Contract": 32,
            "Employee": 32,
            "Location": 9,
            "Product": 8,
            "Document": 76,
            "Chunk": 223,
        }
        assert report.edges_written_by_type == {
            "HAS_CHUNK": 223,
            "DESCRIBED_IN": 72,
            "FOUNDED": 1,
            "HAS_OFFICE": 5,
            "WITH_CLIENT": 32,
            "FOR_PRODUCT": 32,
            "SIGNED_BY": 46,
            "BASED_IN": 32,
            "WORKS_ON": 6,
            "MENTIONS": 207,
        }
        assert report.ambiguities == []
        assert report.conflicts == []
        assert "Jennifer Rodriguez signs 7 contract(s)" in report.notes[0]

        # Rerunning without --rebuild must refuse to touch what is already there.
        with pytest.raises(IngestionAborted, match="rerun with --rebuild"):
            other_log = tmp_path / "log2.json"
            run_ingestion(driver, chunks_dir=CHUNKS_DIR, log_path=other_log, rebuild=False)

        # Rerunning WITH --rebuild is idempotent: the same corpus produces the same graph.
        log_path_2 = tmp_path / "log3.json"
        again = run_ingestion(driver, chunks_dir=CHUNKS_DIR, log_path=log_path_2, rebuild=True)
        assert again.nodes_written_by_label == report.nodes_written_by_label
        assert again.edges_written_by_type == report.edges_written_by_type
