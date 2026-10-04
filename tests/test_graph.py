"""Graph tests across components: chunks -> extraction -> resolution -> Neo4j -> query.

Two kinds, deliberately kept apart, the same split as tests/test_retrieval.py:

* Hermetic (default): the real GraphExtractor and resolve_entities/remap_edges, checked
  against the write-plan they build together. No database: Neo4j has no in-process mode
  the way Qdrant has QdrantClient(":memory:"), so nothing here can be both "real" and
  free the way P3's hermetic layer is.
* Live (`-m live`): the real GraphStore against a real Neo4j, on a small fixture corpus
  whose every entity name starts with FIXTURE ("M6Fixture"), so nothing here can collide
  with a real corpus name -- and cleanup deletes each node this file wrote by its exact
  identity, never by a text scan. Skips itself, with the reason, when Neo4j is down.

Per-component unit tests live in tests/graph (extraction: 62 tests, dedup: 75 tests) and
tests/retrieval/test_graph_store.py (storage and query, against hand-built records: 40
tests); tests/graph/test_ingest.py already proves the full 223-chunk corpus loads with
the right COUNTS. None of them prove a query against data the real extractor produced
returns the right ANSWER -- which is the one gap P4-M5's own DESCRIBED_IN bug exposed
(every unit test passed; only a live run caught it). This file closes that gap, and nothing
else: it does not re-verify what the files above already cover.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from src.graph.dedup import remap_edges, resolve_entities
from src.graph.extractor import GraphExtractor
from src.graph.records import GraphEdge, GraphNode
from src.graph.setup import build_driver
from src.ingestion.schemas import DocumentChunk, compute_chunk_id, compute_doc_id
from src.retrieval.graph_store import GraphStore
from tests.support import neo4j_down

# A prefix no real corpus entity uses, kept as a normal capitalised word (not "__test_")
# because it has to survive the extractor's own regexes, which expect city/founder names
# shaped like real proper nouns. Every fixture entity's name starts with it.
FIXTURE = "M6Fixture"

PRODUCT = f"{FIXTURE}Llm"
EMPLOYEE = f"{FIXTURE} Employee"
CLIENT = f"{FIXTURE} Client"
CLIENT_LEGAL_NAME = f"{CLIENT} Corp."
CITY = f"{FIXTURE}ville, {FIXTURE} State"
VENDOR_SIGNATORY = f"{FIXTURE} VendorSigner"
CLIENT_SIGNATORY = f"{FIXTURE} ClientSigner"
CONTRACT_TITLE = f"Contract with {CLIENT} for {PRODUCT}"


# --- the fixture corpus: real markdown shapes, fictional entities -----------------------------


def _chunk(category: str, path: str, text: str, *, index: int = 0) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=compute_chunk_id(path, index),
        doc_id=compute_doc_id(path),
        source_path=path,
        category=category,
        title=PurePosixPath(path).stem,
        chunk_index=index,
        text=text,
        char_count=len(text),
        approx_token_count=max(1, len(text) // 4),
    )


def _fixture_chunks() -> list[DocumentChunk]:
    product_page = _chunk(
        "products", f"products/{PRODUCT}.md", f"# {PRODUCT}\n\nA fixture product."
    )

    employee_page = _chunk(
        "employees",
        f"employees/{EMPLOYEE}.md",
        f"# HR Record\n\n# {EMPLOYEE}\n\n"
        f"- **Job Title**: {FIXTURE} Engineer\n"
        f"- **Location**: {CITY}\n\n"
        f"Builds the {PRODUCT} platform.",
    )

    contract_page = _chunk(
        "contracts",
        f"contracts/{CONTRACT_TITLE}.md",
        f"# {CONTRACT_TITLE}\n\n"
        f"**Parties:**\n- Insurellm, Inc.\n- {CLIENT_LEGAL_NAME}\n\n"
        "**Signatures:**\n\n"
        f"**{VENDOR_SIGNATORY}**\n**Title**: VP Sales\n**Insurellm, Inc.**\n"
        "**Date**: January 1, 2025\n\n"
        f"**{CLIENT_SIGNATORY}**\n**Title**: General Counsel\n**{CLIENT}**\n"
        "**Date**: January 1, 2025\n",
    )

    return [product_page, employee_page, contract_page]


@pytest.fixture(scope="module")
def plan() -> tuple[list[GraphNode], list[GraphEdge]]:
    """The real pipeline's first half: chunks -> GraphExtractor -> resolve_entities.

    Module-scoped and computed once: it's pure and deterministic, and both the hermetic
    assertions and the live write below need the exact same plan.
    """
    chunks = _fixture_chunks()
    extractor = GraphExtractor.from_chunks(chunks)  # closed lists read from THESE 3 files only
    extractions = extractor.extract_all(chunks)
    nodes = [node for extraction in extractions for node in extraction.nodes]
    edges = [edge for extraction in extractions for edge in extraction.edges]

    resolution = resolve_entities(
        nodes, known_names={"Employee": (EMPLOYEE,), "Company": (CLIENT,)}
    )
    edges = remap_edges(edges, resolution.alias_map)
    return resolution.nodes, edges


def _find(nodes: list[GraphNode], label: str, **key: str) -> GraphNode:
    (found,) = [
        n for n in nodes if n.label == label and n.key == key
    ]  # exactly one, or fail loudly
    return found


def _edges(edges: list[GraphEdge], type_: str) -> list[GraphEdge]:
    return sorted((e for e in edges if e.type == type_), key=repr)


# --- hermetic: does extraction + resolution build the RIGHT plan? -----------------------------


def test_extraction_and_resolution_produce_the_expected_entities(plan):
    nodes, _ = plan

    employee = _find(nodes, "Employee", name=EMPLOYEE)
    assert employee.properties["job_title"] == f"{FIXTURE} Engineer"

    product = _find(nodes, "Product", name=PRODUCT)
    assert dict(product.properties) == {}  # a Product carries no properties in this model

    client = _find(nodes, "Company", name=CLIENT)
    assert client.properties["legal_name"] == CLIENT_LEGAL_NAME

    location = _find(nodes, "Location", name=CITY)
    assert dict(location.properties) == {}

    # Two signatories, two Contacts, on opposite sides of the one contract.
    vendor_contact = _find(nodes, "Contact", name=VENDOR_SIGNATORY, organization="Insurellm")
    client_contact = _find(nodes, "Contact", name=CLIENT_SIGNATORY, organization=CLIENT)
    assert {vendor_contact.key["name"], client_contact.key["name"]} == {
        VENDOR_SIGNATORY,
        CLIENT_SIGNATORY,
    }


def test_extraction_and_resolution_produce_the_expected_relationships(plan):
    _, edges = plan

    (works_on,) = _edges(edges, "WORKS_ON")
    assert (works_on.source.key["name"], works_on.target.key["name"]) == (EMPLOYEE, PRODUCT)

    (based_in,) = _edges(edges, "BASED_IN")
    assert based_in.target.key["name"] == CITY
    assert based_in.properties["remote"] is False

    (with_client,) = _edges(edges, "WITH_CLIENT")
    assert with_client.target.key["name"] == CLIENT

    (for_product,) = _edges(edges, "FOR_PRODUCT")
    assert for_product.target.key["name"] == PRODUCT

    signed_by = _edges(edges, "SIGNED_BY")
    signers = {(e.source.label, e.target.key["name"], e.properties["title"]) for e in signed_by}
    assert signers == {
        ("Contract", VENDOR_SIGNATORY, "VP Sales"),
        ("Contract", CLIENT_SIGNATORY, "General Counsel"),
    }
    # Both signatories sign the SAME contract -- their source doc_ids must be identical.
    assert signed_by[0].source.key == signed_by[1].source.key


def test_resolution_finds_no_ambiguity_or_conflict_in_a_clean_fixture(plan):
    # A sanity check on the fixture itself: every fixture name is used consistently, so
    # resolve_entities should have nothing to merge, refuse or disagree about. If this
    # ever fails, the fixture -- not the pipeline -- is what changed.
    chunks = _fixture_chunks()
    extractor = GraphExtractor.from_chunks(chunks)
    nodes = [n for e in extractor.extract_all(chunks) for n in e.nodes]
    resolution = resolve_entities(
        nodes, known_names={"Employee": (EMPLOYEE,), "Company": (CLIENT,)}
    )
    assert (resolution.ambiguous, resolution.conflicts) == ((), ())


# --- live: does Neo4j, written through GraphStore, answer real questions? ---------------------


@pytest.fixture(scope="module")
def live_store(plan):
    reason = neo4j_down()
    if reason:
        pytest.skip(reason)
    nodes, edges = plan

    with build_driver() as driver:
        store = GraphStore(driver, verify_schema=True)
        for node in nodes:
            store.upsert_node(node)
        for edge in edges:
            store.upsert_edge(edge)
        try:
            yield store
        finally:
            # Cleanup by EXACT identity, not a name scan: every node this fixture wrote
            # is already known from `nodes`, so nothing else in the graph can be touched,
            # whatever the real corpus does or doesn't currently hold.
            with driver.session(database="neo4j") as session:
                for node in nodes:
                    pattern = ", ".join(f"{k}: ${k}" for k in node.key)
                    session.run(f"MATCH (n:{node.label} {{{pattern}}}) DETACH DELETE n", **node.key)


@pytest.mark.live
def test_live_graph_answers_who_works_on_the_product(live_store):
    assert PRODUCT in live_store.multi_hop_search(EMPLOYEE, hops=1)


@pytest.mark.live
def test_live_graph_connects_the_client_to_the_product_through_its_contract(live_store):
    # No edge goes directly from Company to Product: WITH_CLIENT and FOR_PRODUCT both
    # start at the Contract in between. multi_hop_search must walk THROUGH it to connect
    # the two -- the reason a graph answers this and a single Cypher MATCH on one
    # relationship type could not.
    assert PRODUCT not in live_store.multi_hop_search(CLIENT, hops=1)
    assert PRODUCT in live_store.multi_hop_search(CLIENT, hops=2)


@pytest.mark.live
def test_live_search_related_fans_out_to_every_kind_of_connection(live_store):
    edges = live_store.search_related(PRODUCT, label="Product")
    kinds = {(e.type, e.source.label) for e in edges}
    # Both the employee who builds it and the contract that licenses it come back
    # together, from one call -- the cross-cutting answer this graph exists to give.
    assert ("WORKS_ON", "Employee") in kinds
    assert ("FOR_PRODUCT", "Contract") in kinds


@pytest.mark.live
def test_live_signed_by_keeps_its_title_property_through_the_round_trip(live_store):
    edges = live_store.search_related(VENDOR_SIGNATORY, label="Contact")
    (signed_by,) = [e for e in edges if e.type == "SIGNED_BY"]
    assert signed_by.properties["title"] == "VP Sales"
