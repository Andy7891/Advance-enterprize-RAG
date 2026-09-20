"""Tests for the graph records: each is built against the model in schema.py, so a bad
record must fail at construction, with a message that names what is wrong."""

from __future__ import annotations

import dataclasses

import pytest

from src.graph.records import GraphEdge, GraphNode, InvalidRecord, NodeRef
from src.graph.schema import NODE_TYPES, RELATIONSHIP_TYPES

CARLLM = NodeRef("Product", {"name": "Carllm"})
DRIVESMART = NodeRef("Company", {"name": "DriveSmart Insurance"})
CONTRACT = NodeRef("Contract", {"doc_id": "e1d50d989d7d0276"})


def _key_for(label: str) -> dict[str, str]:
    node = next(n for n in NODE_TYPES if n.label == label)
    return {name: f"{name}-value" for name in node.key}


# --- nodes ---------------------------------------------------------------------------------


def test_a_node_carries_its_identity_and_its_properties():
    node = GraphNode(
        "Company", {"name": "DriveSmart Insurance"}, {"legal_name": "DriveSmart Insurance Corp."}
    )
    assert node.label == "Company"
    assert node.key == {"name": "DriveSmart Insurance"}
    assert node.properties == {"legal_name": "DriveSmart Insurance Corp."}


def test_a_node_reference_carries_identity_only():
    node = GraphNode(
        "Contact", {"name": "Robert Chen", "organization": "Fortress"}, {"title": "CUO"}
    )
    assert node.ref == NodeRef("Contact", {"name": "Robert Chen", "organization": "Fortress"})


def test_an_unknown_label_is_rejected_and_the_message_lists_the_model():
    with pytest.raises(InvalidRecord, match="unknown label 'Person'") as error:
        GraphNode("Person", {"name": "Avery Lancaster"})
    assert "Employee" in str(error.value)


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Contact", {"name": "Robert Chen"}),  # a composite key with a missing part
        ("Company", {"name": "Acme", "organization": "Acme"}),  # a part the model does not have
        ("Company", {}),  # no key at all
        ("Contract", {"name": "Contract with DriveSmart"}),  # the wrong property for this label
    ],
)
def test_the_key_must_be_exactly_the_models_key(label, key):
    # Community Edition cannot enforce this in the database, so this is the guard.
    with pytest.raises(InvalidRecord, match="is identified by"):
        GraphNode(label, key)


@pytest.mark.parametrize("value", ["", "   ", " Carllm", "Carllm ", 5, None])
def test_key_values_must_be_clean_non_empty_strings(value):
    with pytest.raises(InvalidRecord, match="key 'name'"):
        GraphNode("Product", {"name": value})


def test_properties_may_not_repeat_the_identity():
    with pytest.raises(InvalidRecord, match="part of the identity"):
        GraphNode("Product", {"name": "Carllm"}, {"name": "Carllm 2"})


@pytest.mark.parametrize(
    "value",
    [None, {"a": 1}, object(), (1, 2), [1, "two"], [1, True], [[1]], [None]],
    ids=["None", "dict", "object", "tuple", "mixed", "int-and-bool", "nested", "list-of-None"],
)
def test_properties_reject_values_neo4j_cannot_store(value):
    with pytest.raises(InvalidRecord, match="omit the property instead of passing None"):
        GraphNode("Product", {"name": "Carllm"}, {"line": value})


def test_properties_accept_every_type_neo4j_can_store():
    properties = {"a": "text", "b": 3, "c": 2.5, "d": True, "e": ["x", "y"], "f": [1, 2], "g": []}
    assert dict(GraphNode("Product", {"name": "Carllm"}, properties).properties) == properties


def test_a_record_cannot_be_changed_after_it_was_validated():
    key, properties = {"name": "Carllm"}, {"line": "auto"}
    node = GraphNode("Product", key, properties)

    with pytest.raises(dataclasses.FrozenInstanceError):
        node.label = "Company"  # type: ignore[misc]
    with pytest.raises(TypeError):
        node.properties["line"] = "home"  # type: ignore[index]
    # The caller's own dicts are copied, so editing them later cannot bypass validation.
    key["name"] = "  "
    properties["line"] = object()
    assert node.key == {"name": "Carllm"} and node.properties == {"line": "auto"}


def test_records_compare_by_value():
    assert GraphNode("Product", {"name": "Carllm"}) == GraphNode("Product", {"name": "Carllm"})
    assert GraphNode("Product", {"name": "Carllm"}) != GraphNode("Product", {"name": "Homellm"})
    assert GraphNode("Product", {"name": "Carllm"}, {"line": "auto"}) != GraphNode(
        "Product", {"name": "Carllm"}
    )


def test_a_node_reference_prints_the_way_a_person_would_read_it():
    assert str(NodeRef("Contact", {"name": "Robert Chen", "organization": "Fortress"})) == (
        "Contact(name='Robert Chen', organization='Fortress')"
    )


# --- edges ---------------------------------------------------------------------------------


def test_a_valid_edge_keeps_its_endpoints_and_declared_properties():
    edge = GraphEdge(
        "SIGNED_BY",
        CONTRACT,
        NodeRef("Contact", {"name": "Jennifer Rodriguez", "organization": "Insurellm"}),
        {"title": "Chief Executive Officer"},
    )
    assert edge.type == "SIGNED_BY"
    assert edge.source == CONTRACT
    assert edge.properties == {"title": "Chief Executive Officer"}


def test_provenance_edges_are_ordinary_records():
    # The extractor links chunks to entities with the same record types.
    chunk = NodeRef("Chunk", {"chunk_id": "products/Carllm.md_0"})
    assert GraphEdge("MENTIONS", chunk, CARLLM).source.label == "Chunk"


def test_an_unknown_relationship_is_rejected_and_the_message_lists_the_model():
    with pytest.raises(InvalidRecord, match="unknown relationship 'CEO_OF'") as error:
        GraphEdge("CEO_OF", CARLLM, DRIVESMART)
    assert "WITH_CLIENT" in str(error.value)


@pytest.mark.parametrize(
    ("type_", "source", "target", "wrong"),
    [
        ("WITH_CLIENT", CONTRACT, CARLLM, "target"),  # a client is a Company, not a Product
        ("FOR_PRODUCT", CARLLM, CONTRACT, "source"),  # right labels, wrong direction
        ("MENTIONS", DRIVESMART, CARLLM, "source"),  # only a Chunk can mention
    ],
)
def test_an_edge_must_join_the_kinds_of_node_the_model_allows(type_, source, target, wrong):
    with pytest.raises(InvalidRecord, match=f"{wrong} must be one of"):
        GraphEdge(type_, source, target)


def test_edge_properties_must_be_declared_for_that_relationship():
    with pytest.raises(InvalidRecord, match=r"undeclared properties \['titel'\]"):
        GraphEdge(
            "SIGNED_BY",
            CONTRACT,
            NodeRef("Contact", {"name": "A", "organization": "B"}),
            {"titel": "CEO"},
        )
    with pytest.raises(InvalidRecord, match="undeclared properties"):
        GraphEdge("FOR_PRODUCT", CONTRACT, CARLLM, {"anything": 1})


def test_edge_endpoints_must_be_references_and_the_error_says_how_to_get_one():
    node = GraphNode("Product", {"name": "Carllm"})
    with pytest.raises(InvalidRecord, match=r"must be a NodeRef \(use node.ref\)"):
        GraphEdge("FOR_PRODUCT", CONTRACT, node)  # type: ignore[arg-type]


@pytest.mark.parametrize("relationship", RELATIONSHIP_TYPES, ids=lambda r: r.name)
def test_every_relationship_in_the_model_can_be_built(relationship):
    # Records and the registry must agree: this fails the day a relationship is added
    # to schema.py that the records cannot express.
    source = NodeRef(relationship.sources[0], _key_for(relationship.sources[0]))
    target = NodeRef(relationship.targets[0], _key_for(relationship.targets[0]))
    properties = {name: "x" for name in relationship.properties}
    assert GraphEdge(relationship.name, source, target, properties).type == relationship.name
