"""GraphStore: write nodes and edges into the Neo4j entity graph, and ask what is
connected to an entity.

It sits between the extractor, which builds `GraphNode` and `GraphEdge` records, and a
retriever that will use the graph for the entity questions vector search missed in P3
("the Carllm product", "the CEO"). A graph hit is entities and edges, not text:
reaching the passages means following MENTIONS to a chunk_id and asking Qdrant for it,
which is a later step.

The store never opens or closes the driver, so the caller decides its lifetime and
tests can hand in a fake.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from neo4j import ManagedTransaction

from src.graph.records import GraphEdge, GraphNode, NodeRef
from src.graph.schema import NODE_TYPES, RELATIONSHIP_TYPES, SchemaMismatch, check_schema
from src.graph.setup import GraphDriver

T = TypeVar("T")

# Every extra hop multiplies the paths Neo4j has to enumerate, and on this graph a
# fourth hop already reaches most of it: "connected" stops meaning anything.
MAX_HOPS = 3
DEFAULT_LIMIT = 50

_KEY_BY_LABEL = {node.label: node.key for node in NODE_TYPES}
_PROVENANCE_LABELS = frozenset(node.label for node in NODE_TYPES if node.layer == "provenance")

# A relationship is provenance if either end is a Document or a Chunk. Those record
# WHERE a fact was read; following them from an entity returns every passage that
# merely mentions it, and makes two entities named in one chunk look 2 hops apart.
DOMAIN_RELATIONSHIPS = tuple(
    rel.name for rel in RELATIONSHIP_TYPES if not _PROVENANCE_LABELS & {*rel.sources, *rel.targets}
)
ALL_RELATIONSHIPS = tuple(rel.name for rel in RELATIONSHIP_TYPES)

# Only labels whose identity includes a name can be looked up by name. Contract,
# Document and Chunk are reached through their neighbours instead.
NAMED_LABELS = tuple(node.label for node in NODE_TYPES if "name" in node.key)


class MissingEndpoint(LookupError):
    """An edge was written before one of its two nodes exists."""


def _key_pattern(label: str, prefix: str) -> str:
    # The property names come from the model, never from data, so splicing them is
    # safe; the VALUES are always parameters.
    return ", ".join(f"{name}: ${prefix}_{name}" for name in _KEY_BY_LABEL[label])


def _key_params(key: Mapping[str, str], prefix: str) -> dict[str, str]:
    return {f"{prefix}_{name}": value for name, value in key.items()}


def _lookup(labels: Sequence[str]) -> str:
    # INTERVIEW: a label cannot be a query parameter, so labels are spliced in, which
    # is safe only because they come from the model. One MATCH per label, joined by
    # UNION, so each is an index seek on that label's constraint; a label-less
    # `MATCH (n {name: $name})` would scan every node in the graph.
    return " UNION ".join(f"MATCH (e:{label} {{name: $name}}) RETURN e" for label in labels)


def _sort_key(variable: str) -> str:
    # Nodes have different identity properties, and ORDER BY must be total or the
    # same question could return its edges in a different order on the next run.
    return (
        f"coalesce({variable}.name, {variable}.title, {variable}.doc_id, {variable}.chunk_id, '')"
    )


def _node_ref(labels: Sequence[str], properties: Mapping[str, Any]) -> NodeRef:
    for label in labels:
        key_names = _KEY_BY_LABEL.get(label)
        if key_names and all(name in properties for name in key_names):
            return NodeRef(label, {name: properties[name] for name in key_names})
    # Failing loudly: a node from outside the model means someone wrote to this graph
    # by hand, and hiding it would make a retrieval answer silently incomplete.
    raise ValueError(f"a node with labels {list(labels)} is not part of the graph model")


def _check_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit must be a positive integer, got {limit!r}")


class GraphStore:
    """Reads and writes the entity graph through an injected Neo4j driver."""

    def __init__(
        self, driver: GraphDriver, *, database: str = "neo4j", verify_schema: bool = True
    ) -> None:
        self._driver = driver
        self._database = database
        if verify_schema:
            # Without the constraints MERGE still runs, but it is no longer atomic
            # under concurrent writers and every lookup becomes a scan. Refusing here
            # is cheaper than finding duplicates later.
            status = check_schema(driver, database=database)
            if not status.ok:
                raise SchemaMismatch(
                    "graph schema is not in force: "
                    + "; ".join(status.problems)
                    + " (run: python -m src.graph.schema)"
                )

    # --- writes ---------------------------------------------------------------------

    def upsert_node(self, node: GraphNode) -> None:
        """Create the node, or add these properties to the one that already exists."""
        # INTERVIEW: MERGE on the full key finds-or-creates exactly one node, and the
        # uniqueness constraint from P4-M2 makes that atomic and index-backed.
        # `SET n += map` adds and overwrites the properties given but never deletes
        # the others, so two documents that each know part of an entity can both
        # write it. `SET n = map` would wipe the key itself.
        query = f"MERGE (n:{node.label} {{{_key_pattern(node.label, 'k')}}}) SET n += $props"
        params = {**_key_params(node.key, "k"), "props": dict(node.properties)}
        self._write(lambda tx: tx.run(query, params).consume())

    def upsert_edge(self, edge: GraphEdge) -> None:
        """Create the relationship between two nodes that already exist, or update its properties.

        Raises MissingEndpoint if either node is absent.
        """
        source, target = edge.source, edge.target
        # INTERVIEW: the endpoints are MATCHed, not MERGEd. MERGE would quietly create
        # a stub node for a typo ("Carlllm") and split one product into two, which no
        # constraint can catch. The price is load order: nodes before edges.
        query = (
            f"MATCH (a:{source.label} {{{_key_pattern(source.label, 'a')}}}) "
            f"MATCH (b:{target.label} {{{_key_pattern(target.label, 'b')}}}) "
            f"MERGE (a)-[r:{edge.type}]->(b) SET r += $props "
            # Aggregating makes zero matches return one row holding 0, not no rows.
            "RETURN count(r) AS written"
        )
        params = {
            **_key_params(source.key, "a"),
            **_key_params(target.key, "b"),
            "props": dict(edge.properties),
        }

        def work(tx: ManagedTransaction) -> None:
            if tx.run(query, params).single(strict=True)["written"] == 0:
                raise MissingEndpoint(self._explain_missing(tx, edge, params))

        self._write(work)

    def _explain_missing(
        self, tx: ManagedTransaction, edge: GraphEdge, params: dict[str, Any]
    ) -> str:
        found = tx.run(
            f"OPTIONAL MATCH (a:{edge.source.label} {{{_key_pattern(edge.source.label, 'a')}}}) "
            f"OPTIONAL MATCH (b:{edge.target.label} {{{_key_pattern(edge.target.label, 'b')}}}) "
            "RETURN a IS NOT NULL AS source_found, b IS NOT NULL AS target_found",
            params,
        ).single(strict=True)
        missing = [
            str(ref)
            for ref, present in (
                (edge.source, found["source_found"]),
                (edge.target, found["target_found"]),
            )
            if not present
        ]
        if not missing:
            # Both nodes exist now, so one was created between the two queries.
            return f"cannot write {edge.type}: an endpoint was created while writing; retry"
        return f"cannot write {edge.type}: {' and '.join(missing)} not found; upsert the node first"

    # --- reads ----------------------------------------------------------------------

    def search_related(
        self,
        entity_name: str,
        *,
        label: str | None = None,
        include_provenance: bool = False,
        limit: int = DEFAULT_LIMIT,
    ) -> list[GraphEdge]:
        """Every edge attached to the entity, in either direction.

        Matches the exact `name`. A name can belong to several nodes (an Employee and a
        Contact can share one), so all of them are searched unless `label` narrows it.
        Provenance edges (MENTIONS and friends) are left out unless asked for.
        """
        name = _clean_name(entity_name)
        _check_limit(limit)
        lookup = _lookup(_labels_to_search(label))
        relationships = ALL_RELATIONSHIPS if include_provenance else DOMAIN_RELATIONSHIPS
        query = (
            f"CALL {{ {lookup} }} "
            # The pattern has no arrow because "connected to" means both directions;
            # each edge still reports its real source and target.
            "MATCH (e)-[r]-() WHERE type(r) IN $types "
            # Two namesakes joined to each other would otherwise return the same edge twice.
            "WITH DISTINCT r "
            "WITH r, startNode(r) AS s, endNode(r) AS t "
            "RETURN type(r) AS rel_type, labels(s) AS s_labels, properties(s) AS s_props, "
            "labels(t) AS t_labels, properties(t) AS t_props, properties(r) AS r_props "
            f"ORDER BY rel_type, {_sort_key('s_props')}, {_sort_key('t_props')} LIMIT $limit"
        )
        rows = self._read(query, {"name": name, "types": list(relationships), "limit": limit})
        return [
            GraphEdge(
                row["rel_type"],
                _node_ref(row["s_labels"], row["s_props"]),
                _node_ref(row["t_labels"], row["t_props"]),
                row["r_props"],
            )
            for row in rows
        ]

    def multi_hop_search(
        self, start_entity: str, hops: int, *, label: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> list[str]:
        """Names of the entities within `hops` steps of the start, nearest first.

        Follows domain relationships in either direction, from every node with that
        name. The start itself is never returned. A node with no name, such as a
        Contract, is walked THROUGH but not returned, so "which clients have a Carllm
        contract" still works; a Contract with a `title` property is returned by title.
        """
        if isinstance(hops, bool) or not isinstance(hops, int) or not 1 <= hops <= MAX_HOPS:
            raise ValueError(f"hops must be an integer from 1 to {MAX_HOPS}, got {hops!r}")
        name = _clean_name(start_entity)
        _check_limit(limit)
        lookup = _lookup(_labels_to_search(label))
        # INTERVIEW: a variable-length pattern enumerates PATHS, not nodes, so cost
        # grows with the number of routes between entities, not with the number of
        # entities. The 3-hop cap and domain-only edges keep it small here; the exit
        # is APOC's path expander, which visits each node once. Hops is spliced in as
        # an integer because a length bound cannot be a parameter.
        types = "|".join(DOMAIN_RELATIONSHIPS)
        query = (
            f"CALL {{ {lookup} }} "
            "WITH collect(e) AS starts UNWIND starts AS s "
            f"MATCH p = (s)-[:{types}*1..{hops}]-(t) WHERE NOT t IN starts "
            # The shortest route wins: a node reachable at 1 and at 3 hops is at 1.
            "WITH t, min(length(p)) AS distance "
            "WITH coalesce(t.name, t.title) AS entity, min(distance) AS distance "
            "WHERE entity IS NOT NULL "
            "RETURN entity, distance ORDER BY distance, entity LIMIT $limit"
        )
        return [row["entity"] for row in self._read(query, {"name": name, "limit": limit})]

    def all_entity_names(self) -> dict[str, list[str]]:
        """Every distinct `name` currently in the graph, grouped by label."""
        query = " UNION ALL ".join(
            # builds one "MATCH this label, return its name" clause per named label
            f"MATCH (e:{label}) RETURN '{label}' AS label, e.name AS name"
            for label in NAMED_LABELS
        )
        rows = self._read(query, {})
        # runs the combined query and collects every row it returns
        result: dict[str, list[str]] = {label: [] for label in NAMED_LABELS}
        # starts every label with an empty list, even a label with zero nodes
        for row in rows:
            # goes through each returned row one at a time
            result[row["label"]].append(row["name"])
            # adds that row's name under that row's own label
        return result
        # gives back the finished label -> names mapping

    def chunks_of_document(self, doc_id: str) -> list[str]:
        """Every `chunk_id` belonging to this document, via `HAS_CHUNK`."""
        query = (
            "MATCH (d:Document {doc_id: $doc_id})-[:HAS_CHUNK]->(c:Chunk) "
            "RETURN c.chunk_id AS chunk_id"
        )
        # names the query that follows HAS_CHUNK from the document to its chunks
        rows = self._read(query, {"doc_id": doc_id})
        # runs that query for the one document id passed in
        return [row["chunk_id"] for row in rows]
        # pulls just the chunk_id string out of every returned row

    # --- transactions ---------------------------------------------------------------

    def _write(self, work: Callable[[ManagedTransaction], T]) -> T:
        with self._driver.session(database=self._database) as session:
            # INTERVIEW: execute_write runs `work` in a managed transaction and RETRIES
            # it on transient failures (a deadlock, a leader change). That is only safe
            # because every write here is idempotent: MERGE finds whatever an earlier,
            # half-finished attempt already made.
            return session.execute_write(work)

    def _read(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        def work(tx: ManagedTransaction) -> list[dict[str, Any]]:
            # Rows are copied out inside the transaction: a Result cannot be read
            # once its transaction has closed.
            return [record.data() for record in tx.run(query, params)]

        with self._driver.session(database=self._database) as session:
            return session.execute_read(work)


def _clean_name(name: str) -> str:
    # Keys can never carry surrounding whitespace (records refuse it), so a query
    # that does could only ever miss; trimming turns a typo into a hit.
    cleaned = name.strip() if isinstance(name, str) else ""
    if not cleaned:
        raise ValueError("an entity name must be a non-empty string")
    return cleaned


def _labels_to_search(label: str | None) -> tuple[str, ...]:
    if label is None:
        return NAMED_LABELS
    if label not in NAMED_LABELS:
        raise ValueError(f"cannot look up {label!r} by name; labels with a name are {NAMED_LABELS}")
    return (label,)
