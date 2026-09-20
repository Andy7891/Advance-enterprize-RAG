"""Tests for GraphStore: a fake driver checks what the store SENDS to Neo4j, and live tests
check what Neo4j then DOES, on the real labels with `__test_` values swept before and after
(the same isolation as the P4-M2 enforcement tests; Community Edition has one database).

Live tests skip themselves, with the reason, when Neo4j is not running."""

from __future__ import annotations

import uuid

import pytest

from src.graph.records import GraphEdge, GraphNode, NodeRef
from src.graph.schema import NODE_TYPES, RELATIONSHIP_TYPES, SchemaMismatch, apply_schema
from src.graph.setup import build_driver
from src.retrieval.graph_store import (
    ALL_RELATIONSHIPS,
    DOMAIN_RELATIONSHIPS,
    MAX_HOPS,
    NAMED_LABELS,
    GraphStore,
    MissingEndpoint,
)
from tests.support import neo4j_down

# --- a fake driver: it records what the store sends and replays canned rows -----------------


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


class FakeTx:
    def __init__(self, driver):
        self.driver = driver

    def run(self, query, parameters=None):
        self.driver.calls.append((query, dict(parameters or {})))
        return FakeResult(self.driver.replies.pop(0) if self.driver.replies else [])


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def run(self, query, **parameters):
        # check_schema reads through session.run, not through a transaction function.
        self.driver.schema_queries.append(query)
        return FakeResult(self.driver.constraint_rows)

    def execute_write(self, work):
        self.driver.modes.append("write")
        return work(FakeTx(self.driver))

    def execute_read(self, work):
        self.driver.modes.append("read")
        return work(FakeTx(self.driver))


def _model_rows():
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


class FakeStoreDriver:
    def __init__(self, replies=(), constraint_rows=None):
        self.replies = list(replies)
        self.constraint_rows = _model_rows() if constraint_rows is None else constraint_rows
        self.calls, self.modes, self.databases, self.schema_queries = [], [], [], []

    def session(self, *, database=None):
        self.databases.append(database)
        return FakeSession(self)


def _store(*replies):
    driver = FakeStoreDriver(replies)
    return GraphStore(driver, verify_schema=False), driver


PRODUCT = NodeRef("Product", {"name": "Carllm"})
CONTRACT = NodeRef("Contract", {"doc_id": "e1d50d989d7d0276"})


# --- the module's derived constants ----------------------------------------------------------


def test_provenance_relationships_are_exactly_those_touching_a_document_or_chunk():
    provenance = set(ALL_RELATIONSHIPS) - set(DOMAIN_RELATIONSHIPS)
    assert provenance == {"HAS_CHUNK", "MENTIONS", "DESCRIBED_IN"}
    assert len(ALL_RELATIONSHIPS) == len(RELATIONSHIP_TYPES)


def test_only_labels_identified_by_a_name_can_be_looked_up_by_name():
    assert set(NAMED_LABELS) == {"Company", "Product", "Employee", "Contact", "Location"}


# --- constructor -----------------------------------------------------------------------------


def test_the_store_refuses_a_graph_whose_constraints_are_not_in_force():
    with pytest.raises(SchemaMismatch, match=r"python -m src.graph.schema"):
        GraphStore(FakeStoreDriver(constraint_rows=[]))


def test_the_store_accepts_a_graph_with_the_model_in_force():
    assert GraphStore(FakeStoreDriver())


def test_schema_verification_can_be_skipped_and_then_asks_nothing():
    driver = FakeStoreDriver(constraint_rows=[])
    GraphStore(driver, verify_schema=False)
    assert driver.schema_queries == []


@pytest.mark.parametrize("database", ["neo4j", "graph2"])
def test_the_requested_database_is_used_for_every_session(database):
    driver = FakeStoreDriver()
    GraphStore(driver, database=database, verify_schema=False).upsert_node(
        GraphNode("Product", {"name": "Carllm"})
    )
    assert driver.databases == [database]


# --- writes: what is sent ----------------------------------------------------------------------


def test_upsert_node_merges_on_the_key_and_adds_the_properties():
    store, driver = _store()
    store.upsert_node(GraphNode("Company", {"name": "Acme"}, {"legal_name": "Acme Inc."}))

    ((query, params),) = driver.calls
    assert query == "MERGE (n:Company {name: $k_name}) SET n += $props"
    assert params == {"k_name": "Acme", "props": {"legal_name": "Acme Inc."}}
    # A managed transaction, so a transient failure is retried by the driver.
    assert driver.modes == ["write"]


def test_upsert_node_writes_every_part_of_a_composite_key_in_the_models_order():
    store, driver = _store()
    # The caller lists the key backwards; the query must not depend on that.
    store.upsert_node(GraphNode("Contact", {"organization": "Fortress", "name": "Robert Chen"}))

    ((query, params),) = driver.calls
    assert (
        query == "MERGE (n:Contact {name: $k_name, organization: $k_organization}) SET n += $props"
    )
    assert params["k_name"] == "Robert Chen" and params["k_organization"] == "Fortress"


def test_upsert_edge_matches_both_endpoints_and_merges_the_relationship():
    store, driver = _store([{"written": 1}])
    company = NodeRef("Company", {"name": "DriveSmart Insurance"})

    assert store.upsert_edge(GraphEdge("WITH_CLIENT", CONTRACT, company)) is None

    ((query, params),) = driver.calls
    assert query == (
        "MATCH (a:Contract {doc_id: $a_doc_id}) MATCH (b:Company {name: $b_name}) "
        "MERGE (a)-[r:WITH_CLIENT]->(b) SET r += $props RETURN count(r) AS written"
    )
    assert params == {"a_doc_id": "e1d50d989d7d0276", "b_name": "DriveSmart Insurance", "props": {}}


def test_upsert_edge_names_the_endpoint_that_does_not_exist():
    store, driver = _store([{"written": 0}], [{"source_found": True, "target_found": False}])

    with pytest.raises(MissingEndpoint) as error:
        store.upsert_edge(GraphEdge("FOR_PRODUCT", CONTRACT, PRODUCT))

    assert "Product(name='Carllm')" in str(error.value)
    assert "Contract(" not in str(error.value)
    assert "upsert the node first" in str(error.value)
    assert len(driver.calls) == 2


def test_upsert_edge_names_both_endpoints_when_both_are_missing():
    store, _ = _store([{"written": 0}], [{"source_found": False, "target_found": False}])
    with pytest.raises(MissingEndpoint, match=r"Contract\(.*\) and Product\("):
        store.upsert_edge(GraphEdge("FOR_PRODUCT", CONTRACT, PRODUCT))


def test_upsert_edge_says_so_when_the_endpoints_exist_after_all():
    # Possible only if a node was created between the two queries.
    store, _ = _store([{"written": 0}], [{"source_found": True, "target_found": True}])
    with pytest.raises(MissingEndpoint, match="created while writing"):
        store.upsert_edge(GraphEdge("FOR_PRODUCT", CONTRACT, PRODUCT))


# --- reads: what is sent and how the rows come back ----------------------------------------------


def _edge_row(
    rel="FOR_PRODUCT", s_labels=("Contract",), s_props=None, t_labels=("Product",), t_props=None
):
    return {
        "rel_type": rel,
        "s_labels": list(s_labels),
        "s_props": s_props or {"doc_id": "e1d50d989d7d0276", "title": "Contract for Carllm"},
        "t_labels": list(t_labels),
        "t_props": t_props or {"name": "Carllm", "line": "auto"},
        "r_props": {},
    }


def test_search_related_turns_rows_into_edges_with_their_real_direction():
    store, driver = _store([_edge_row()])

    (edge,) = store.search_related("Carllm")

    assert edge == GraphEdge("FOR_PRODUCT", CONTRACT, PRODUCT)
    assert driver.modes == ["read"]


def test_search_related_hides_provenance_edges_unless_asked():
    store, driver = _store([], [])
    store.search_related("Carllm")
    store.search_related("Carllm", include_provenance=True)

    default_types, all_types = (params["types"] for _, params in driver.calls)
    assert set(default_types) == set(DOMAIN_RELATIONSHIPS) and "MENTIONS" not in default_types
    assert set(all_types) == set(ALL_RELATIONSHIPS)


def test_search_related_looks_up_each_named_label_and_no_other():
    store, driver = _store([], [])
    store.search_related("Robert Chen")
    store.search_related("Robert Chen", label="Employee")

    everywhere, narrowed = (query for query, _ in driver.calls)
    for label in NAMED_LABELS:
        assert f"MATCH (e:{label} {{name: $name}})" in everywhere
    for label in ("Contract", "Document", "Chunk"):
        assert f"(e:{label} " not in everywhere
    assert "Employee" in narrowed and "Contact" not in narrowed


def test_search_related_passes_the_trimmed_name_and_the_limit():
    store, driver = _store([])
    store.search_related("  Carllm ", limit=7)
    assert driver.calls[0][1]["name"] == "Carllm" and driver.calls[0][1]["limit"] == 7


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"entity_name": ""}, "non-empty"),
        ({"entity_name": "   "}, "non-empty"),
        ({"entity_name": None}, "non-empty"),
        ({"entity_name": "x", "label": "Contract"}, "cannot look up 'Contract' by name"),
        ({"entity_name": "x", "label": "Person"}, "cannot look up 'Person' by name"),
        ({"entity_name": "x", "limit": 0}, "limit must be a positive integer"),
        ({"entity_name": "x", "limit": True}, "limit must be a positive integer"),
        ({"entity_name": "x", "limit": "3"}, "limit must be a positive integer"),
    ],
)
def test_search_related_rejects_arguments_that_cannot_mean_anything(kwargs, message):
    store, driver = _store()
    with pytest.raises(ValueError, match=message):
        store.search_related(**kwargs)
    assert driver.calls == []


def test_search_related_refuses_a_node_that_is_not_part_of_the_model():
    store, _ = _store([_edge_row(s_labels=["Demo123"], s_props={"x": 1})])
    with pytest.raises(ValueError, match=r"labels \['Demo123'\] is not part of the graph model"):
        store.search_related("Carllm")


def test_multi_hop_bounds_the_path_and_walks_only_domain_relationships():
    store, driver = _store([])
    store.multi_hop_search("Carllm", 2)

    query = driver.calls[0][0]
    assert "*1..2]" in query
    assert all(name in query for name in DOMAIN_RELATIONSHIPS)
    assert not any(name in query for name in ("MENTIONS", "HAS_CHUNK", "DESCRIBED_IN"))


def test_multi_hop_returns_the_names_in_the_order_the_database_gave_them():
    store, driver = _store(
        [
            {"entity": "Austin", "distance": 1},
            {"entity": "Carllm", "distance": 1},
            {"entity": "DriveSmart Insurance", "distance": 3},
        ]
    )
    assert store.multi_hop_search("Rachel Martinez", 3) == [
        "Austin",
        "Carllm",
        "DriveSmart Insurance",
    ]
    assert driver.modes == ["read"]


@pytest.mark.parametrize("hops", [0, -1, MAX_HOPS + 1, True, "2", 2.0, None])
def test_multi_hop_rejects_a_hop_count_outside_one_to_the_cap(hops):
    store, driver = _store()
    with pytest.raises(ValueError, match=f"hops must be an integer from 1 to {MAX_HOPS}"):
        store.multi_hop_search("Carllm", hops)
    assert driver.calls == []


def test_multi_hop_accepts_a_label_and_a_limit_like_search_related():
    store, driver = _store([])
    store.multi_hop_search("Robert Chen", 1, label="Employee", limit=5)
    query, params = driver.calls[0]
    assert "Contact" not in query and params == {"name": "Robert Chen", "limit": 5}


# --- live: the real Neo4j ----------------------------------------------------------------------

TEST_PREFIX = "__test_"
_NEO4J_DOWN = neo4j_down()
live = pytest.mark.skipif(_NEO4J_DOWN is not None, reason=_NEO4J_DOWN or "")


def _sweep(driver) -> None:
    with driver.session() as session:
        for node in NODE_TYPES:
            session.run(
                f"MATCH (n:{node.label}) WHERE n.{node.key[0]} STARTS WITH $prefix DETACH DELETE n",
                prefix=TEST_PREFIX,
            ).consume()


class Graph:
    """A tiny builder so each live test reads as the graph it is about."""

    def __init__(self, store: GraphStore, driver) -> None:
        self.store, self.driver = store, driver
        self.token = uuid.uuid4().hex[:8]

    def n(self, name: str) -> str:
        return f"{TEST_PREFIX}{self.token}_{name}"

    def add(self, label: str, properties=None, **key) -> NodeRef:
        key = {name: self.n(value) for name, value in key.items()}
        self.store.upsert_node(GraphNode(label, key, properties or {}))
        return NodeRef(label, key)

    def link(self, type_: str, source: NodeRef, target: NodeRef, **properties) -> None:
        self.store.upsert_edge(GraphEdge(type_, source, target, properties))

    def count(self, ref: NodeRef) -> int:
        pattern = ", ".join(f"{name}: $k_{name}" for name in ref.key)
        with self.driver.session() as session:
            return session.run(
                f"MATCH (n:{ref.label} {{{pattern}}}) RETURN count(n) AS c",
                {f"k_{name}": value for name, value in ref.key.items()},
            ).single()["c"]

    def edge_count(self, type_: str, source: NodeRef, target: NodeRef) -> int:
        with self.driver.session() as session:
            return session.run(
                f"MATCH (:{source.label} {{{next(iter(source.key))}: $s}})-[r:{type_}]->"
                f"(:{target.label} {{{next(iter(target.key))}: $t}}) RETURN count(r) AS c",
                s=next(iter(source.key.values())),
                t=next(iter(target.key.values())),
            ).single()["c"]


@pytest.fixture
def graph():
    with build_driver() as driver:
        apply_schema(driver)
        _sweep(driver)
        try:
            yield Graph(GraphStore(driver), driver)
        finally:
            _sweep(driver)


@pytest.mark.live
@live
def test_live_the_store_accepts_the_real_graph(graph):
    assert graph.store


@pytest.mark.live
@live
def test_live_upserting_the_same_node_twice_leaves_one_node(graph):
    ref = graph.add("Product", name="Carllm")
    graph.add("Product", name="Carllm")
    assert graph.count(ref) == 1


@pytest.mark.live
@live
def test_live_a_second_upsert_adds_properties_and_keeps_the_first_ones(graph):
    ref = graph.add("Product", {"line": "auto"}, name="Carllm")
    graph.add("Product", {"tier": "Enterprise", "launched": 2015}, name="Carllm")

    with graph.driver.session() as session:
        properties = session.run(
            "MATCH (n:Product {name: $n}) RETURN properties(n) AS p", n=ref.key["name"]
        ).single()["p"]
    assert properties == {
        "name": ref.key["name"],
        "line": "auto",
        "tier": "Enterprise",
        "launched": 2015,
    }


@pytest.mark.live
@live
def test_live_the_same_name_under_two_labels_is_two_nodes(graph):
    employee = graph.add("Employee", name="Robert Chen")
    contact = graph.add("Contact", name="Robert Chen", organization="Fortress")
    assert graph.count(employee) == 1 and graph.count(contact) == 1


@pytest.mark.live
@live
def test_live_an_edge_to_a_missing_node_fails_and_creates_no_ghost(graph):
    contract = graph.add("Contract", doc_id="k1")
    ghost = NodeRef("Product", {"name": graph.n("Carlllm")})

    with pytest.raises(MissingEndpoint, match=r"Product\(name='__test_.*Carlllm'\) not found"):
        graph.store.upsert_edge(GraphEdge("FOR_PRODUCT", contract, ghost))

    assert graph.count(ghost) == 0


@pytest.mark.live
@live
def test_live_an_edge_upsert_is_idempotent_and_updates_its_properties(graph):
    contract = graph.add("Contract", doc_id="k1")
    signer = graph.add("Contact", name="Jennifer Rodriguez", organization="Insurellm")

    graph.link("SIGNED_BY", contract, signer, title="CEO")
    graph.link("SIGNED_BY", contract, signer, title="Chief Executive Officer")

    assert graph.edge_count("SIGNED_BY", contract, signer) == 1
    (edge,) = graph.store.search_related(signer.key["name"])
    assert edge.properties == {"title": "Chief Executive Officer"}


def _carllm_world(graph):
    product = graph.add("Product", name="Carllm")
    contract = graph.add("Contract", {"title": graph.n("Contract for Carllm")}, doc_id="k1")
    client = graph.add("Company", name="DriveSmart")
    employee = graph.add("Employee", {"job_title": "Product Manager"}, name="Rachel")
    city = graph.add("Location", name="Austin")
    chunk = graph.add("Chunk", chunk_id="about_0")
    document = graph.add("Document", doc_id="products_carllm")
    graph.link("FOR_PRODUCT", contract, product)
    graph.link("WITH_CLIENT", contract, client)
    graph.link("WORKS_ON", employee, product)
    graph.link("BASED_IN", employee, city, remote=False)
    graph.link("MENTIONS", chunk, product)
    graph.link("DESCRIBED_IN", product, document)
    graph.link("HAS_CHUNK", document, chunk)
    return product, contract, client, employee, city, chunk, document


@pytest.mark.live
@live
def test_live_search_related_sees_both_directions_and_hides_provenance(graph):
    product, contract, client, employee, city, *_ = _carllm_world(graph)

    into_product = graph.store.search_related(product.key["name"])
    # Sorted by relationship type, and each edge keeps its real direction.
    assert into_product == [
        GraphEdge("FOR_PRODUCT", contract, product),
        GraphEdge("WORKS_ON", employee, product),
    ]

    out_of_employee = graph.store.search_related(employee.key["name"])
    assert out_of_employee == [
        GraphEdge("BASED_IN", employee, city, {"remote": False}),
        GraphEdge("WORKS_ON", employee, product),
    ]


@pytest.mark.live
@live
def test_live_search_related_can_include_provenance(graph):
    product, contract, _, employee, _, chunk, document = _carllm_world(graph)

    edges = graph.store.search_related(product.key["name"], include_provenance=True)

    assert [edge.type for edge in edges] == ["DESCRIBED_IN", "FOR_PRODUCT", "MENTIONS", "WORKS_ON"]
    assert edges[0] == GraphEdge("DESCRIBED_IN", product, document)
    assert edges[2] == GraphEdge("MENTIONS", chunk, product)


@pytest.mark.live
@live
def test_live_search_related_returns_nothing_for_an_unknown_name(graph):
    _carllm_world(graph)
    assert graph.store.search_related(graph.n("Carlllm")) == []


@pytest.mark.live
@live
def test_live_namesakes_are_all_searched_unless_a_label_narrows_it(graph):
    product = graph.add("Product", name="Carllm")
    contract = graph.add("Contract", doc_id="k1")
    employee = graph.add("Employee", name="Robert Chen")
    contact = graph.add("Contact", name="Robert Chen", organization="Fortress")
    graph.link("WORKS_ON", employee, product)
    graph.link("SIGNED_BY", contract, contact, title="Chief Underwriting Officer")
    name = employee.key["name"]

    assert {edge.type for edge in graph.store.search_related(name)} == {"WORKS_ON", "SIGNED_BY"}
    assert [e.type for e in graph.store.search_related(name, label="Employee")] == ["WORKS_ON"]
    assert [e.type for e in graph.store.search_related(name, label="Contact")] == ["SIGNED_BY"]


@pytest.mark.live
@live
def test_live_search_related_orders_ties_by_name_and_limits_after_ordering(graph):
    product = graph.add("Product", name="Carllm")
    # Inserted in an order that is neither alphabetical nor its reverse, so the two edges
    # tie on everything but the source's name and only a real tie-breaker can sort them.
    for name in ("Carol", "Alice", "Eve", "Bob", "Dave"):
        graph.link("WORKS_ON", graph.add("Employee", name=name), product)
    alphabetical = [graph.n(name) for name in ("Alice", "Bob", "Carol", "Dave", "Eve")]

    everything = graph.store.search_related(product.key["name"])
    first_two = graph.store.search_related(product.key["name"], limit=2)

    assert [edge.source.key["name"] for edge in everything] == alphabetical
    assert first_two == everything[:2]


def _chain(graph):
    """Employee -WORKS_ON-> Product <-FOR_PRODUCT- Contract -WITH_CLIENT-> Company."""
    employee = graph.add("Employee", name="Rachel")
    product = graph.add("Product", name="Carllm")
    contract = graph.add("Contract", {"title": graph.n("Contract for Carllm")}, doc_id="k1")
    client = graph.add("Company", name="DriveSmart")
    city = graph.add("Location", name="Austin")
    graph.link("WORKS_ON", employee, product)
    graph.link("FOR_PRODUCT", contract, product)
    graph.link("WITH_CLIENT", contract, client)
    graph.link("BASED_IN", employee, city, remote=False)
    return employee, product, contract, client, city


@pytest.mark.live
@live
def test_live_multi_hop_is_up_to_n_nearest_first_and_never_returns_the_start(graph):
    employee, *_ = _chain(graph)
    n = graph.n
    start = employee.key["name"]

    assert graph.store.multi_hop_search(start, 1) == [n("Austin"), n("Carllm")]
    assert graph.store.multi_hop_search(start, 2) == [
        n("Austin"),
        n("Carllm"),
        n("Contract for Carllm"),
    ]
    assert graph.store.multi_hop_search(start, 3) == [
        n("Austin"),
        n("Carllm"),
        n("Contract for Carllm"),
        n("DriveSmart"),
    ]


@pytest.mark.live
@live
def test_live_multi_hop_walks_through_nameless_nodes_but_returns_only_named_ones(graph):
    product = graph.add("Product", name="Carllm")
    nameless_contract = graph.add("Contract", doc_id="k2")
    client = graph.add("Company", name="Roadway")
    graph.link("FOR_PRODUCT", nameless_contract, product)
    graph.link("WITH_CLIENT", nameless_contract, client)

    assert graph.store.multi_hop_search(product.key["name"], 1) == []
    assert graph.store.multi_hop_search(product.key["name"], 2) == [client.key["name"]]


@pytest.mark.live
@live
def test_live_multi_hop_does_not_take_shortcuts_through_provenance(graph):
    product, *_ = _carllm_world(graph)
    stranger = graph.add("Employee", name="Michelle")
    # Michelle is named in the same chunk as Carllm and connected to it in no other way.
    graph.link("MENTIONS", NodeRef("Chunk", {"chunk_id": graph.n("about_0")}), stranger)

    assert graph.n("Michelle") not in graph.store.multi_hop_search(product.key["name"], MAX_HOPS)
    mentions = graph.store.search_related(product.key["name"], include_provenance=True)
    assert any(edge.type == "MENTIONS" for edge in mentions)


@pytest.mark.live
@live
def test_live_multi_hop_reports_each_entity_once_at_its_shortest_distance(graph):
    employee, product, _, _, city = _chain(graph)
    colleague = graph.add("Employee", name="Sam")
    graph.link("WORKS_ON", colleague, product)
    graph.link("BASED_IN", colleague, city, remote=True)
    n = graph.n

    # Carllm is 1 hop from Rachel directly and 3 hops via Austin and Sam: listed once, at 1.
    result = graph.store.multi_hop_search(employee.key["name"], 3)

    assert result.count(n("Carllm")) == 1
    # Distance 1, then distance 2 (alphabetical: the contract's title before Sam), then 3.
    assert result == [
        n("Austin"),
        n("Carllm"),
        n("Contract for Carllm"),
        n("Sam"),
        n("DriveSmart"),
    ]


@pytest.mark.live
@live
def test_live_multi_hop_starts_from_every_namesake_unless_a_label_narrows_it(graph):
    product = graph.add("Product", name="Homellm")
    contract = graph.add("Contract", {"title": graph.n("Contract for Fortress")}, doc_id="k1")
    employee = graph.add("Employee", name="Robert Chen")
    contact = graph.add("Contact", name="Robert Chen", organization="Fortress")
    graph.link("WORKS_ON", employee, product)
    graph.link("SIGNED_BY", contract, contact, title="Chief Underwriting Officer")
    name = employee.key["name"]

    assert graph.store.multi_hop_search(name, 1) == [
        graph.n("Contract for Fortress"),
        graph.n("Homellm"),
    ]
    assert graph.store.multi_hop_search(name, 1, label="Employee") == [graph.n("Homellm")]
    assert graph.store.multi_hop_search(name, 1, label="Contact") == [
        graph.n("Contract for Fortress")
    ]


@pytest.mark.live
@live
def test_live_an_edge_between_two_nodes_sharing_the_searched_name_is_returned_once(graph):
    # A company and a city can share a name; both match the lookup, and the edge joining
    # them would otherwise be found once from each side.
    company = graph.add("Company", name="Chicago")
    city = graph.add("Location", name="Chicago")
    graph.link("HAS_OFFICE", company, city, headquarters=True)

    assert len(graph.store.search_related(company.key["name"])) == 1


@pytest.mark.live
@live
def test_live_multi_hop_never_returns_the_start_even_when_a_route_leads_back_to_it(graph):
    # The founder, the company they founded and the head-office city they live in form a
    # triangle: three hops from the founder leads back to the founder.
    founder = graph.add("Employee", name="Avery")
    company = graph.add("Company", name="Insurellm")
    city = graph.add("Location", name="San Francisco")
    graph.link("FOUNDED", founder, company)
    graph.link("HAS_OFFICE", company, city, headquarters=True)
    graph.link("BASED_IN", founder, city, remote=False)

    assert graph.store.multi_hop_search(founder.key["name"], 3) == [
        graph.n("Insurellm"),
        graph.n("San Francisco"),
    ]
