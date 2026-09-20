"""Records for what goes INTO the graph: a node, a reference to a node, and an edge.

The extractor builds these and the store (`retrieval/graph_store.py`) writes them. Each
is checked against the model in `schema.py` the moment it is built, so a record that
exists is a record the store can write. A wrong label, a missing key part or an edge
between the wrong kinds of node fails here, next to the code that made the mistake,
instead of showing up later as a bad node in the database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from src.graph.schema import NODE_TYPES, RELATIONSHIP_TYPES

# What Neo4j can store as a property: primitives, or lists of ONE primitive type.
# Anything else is rejected here because the server's own error arrives later and
# names neither the record nor the property.
PropertyValue = str | int | float | bool | list[str] | list[int] | list[float] | list[bool]
_SCALARS = (str, int, float, bool)

_KEY_BY_LABEL = {node.label: node.key for node in NODE_TYPES}
_RELATIONSHIP_BY_NAME = {rel.name: rel for rel in RELATIONSHIP_TYPES}


class InvalidRecord(ValueError):
    """A node or edge does not fit the graph model."""


def _check_identity(label: str, key: Mapping[str, Any], record: str) -> None:
    key_names = _KEY_BY_LABEL.get(label)
    if key_names is None:
        raise InvalidRecord(
            f"{record}: unknown label {label!r}; the model has {sorted(_KEY_BY_LABEL)}"
        )
    # A missing key part cannot be caught by the database on Community Edition
    # (decision D-2026-09-20-05), so this is the guard the loader was promised.
    if set(key) != set(key_names):
        raise InvalidRecord(f"{record}: {label} is identified by {key_names}, got {tuple(key)}")
    for name, value in key.items():
        if not isinstance(value, str) or not value.strip():
            raise InvalidRecord(f"{record}: key {name!r} must be a non-empty string, got {value!r}")
        # "Carllm " would MERGE into a second, ghost node: a uniqueness constraint
        # treats it as a different value. Normalising is the extractor's job.
        if value != value.strip():
            raise InvalidRecord(
                f"{record}: key {name!r} has leading or trailing whitespace ({value!r}); "
                "normalise it before building the record"
            )


def _is_storable(value: Any) -> bool:
    if isinstance(value, _SCALARS):
        return True
    if isinstance(value, list):
        # type(), not isinstance(): bool is an int subclass, and Neo4j rejects a list
        # that mixes the two.
        kinds = {type(item) for item in value}
        return len(kinds) <= 1 and kinds <= set(_SCALARS)
    return False


def _check_properties(
    properties: Mapping[str, Any], reserved: Mapping[str, Any], record: str
) -> None:
    for name, value in properties.items():
        if not isinstance(name, str) or not name:
            raise InvalidRecord(f"{record}: property names must be non-empty strings, got {name!r}")
        if name in reserved:
            raise InvalidRecord(f"{record}: {name!r} is part of the identity, not a property")
        if not _is_storable(value):
            # None is called out because it is the likely mistake: `SET n += {x: null}`
            # would DELETE x, so "unknown" must be an omitted property, not a null.
            raise InvalidRecord(
                f"{record}: property {name!r} has unsupported value {value!r}; use str, int, "
                "float, bool or a list of one of them, and omit the property instead of "
                "passing None"
            )


def _frozen(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    # INTERVIEW: frozen=True only stops re-assigning a field. The dict inside would
    # still be editable, so a caller could change a record AFTER it was validated.
    # A read-only view over a private copy closes that. (Lists inside stay editable:
    # the guard is shallow, and copying them here only protects against the caller's
    # own later edits.)
    return MappingProxyType(
        {name: list(value) if isinstance(value, list) else value for name, value in mapping.items()}
    )


@dataclass(frozen=True, slots=True)
class NodeRef:
    """Identifies a node without describing it: its label and the properties of its key."""

    label: str
    key: Mapping[str, str]

    def __post_init__(self) -> None:
        _check_identity(self.label, self.key, "NodeRef")
        object.__setattr__(self, "key", _frozen(self.key))

    def __str__(self) -> str:
        return f"{self.label}({', '.join(f'{name}={value!r}' for name, value in self.key.items())})"


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One node to write: identity (label and key) plus the properties that describe it."""

    label: str
    key: Mapping[str, str]
    properties: Mapping[str, PropertyValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_identity(self.label, self.key, "GraphNode")
        _check_properties(self.properties, self.key, f"GraphNode {self.label}")
        object.__setattr__(self, "key", _frozen(self.key))
        object.__setattr__(self, "properties", _frozen(self.properties))

    @property
    def ref(self) -> NodeRef:
        return NodeRef(self.label, dict(self.key))


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One relationship to write, between two nodes named by reference."""

    type: str
    source: NodeRef
    target: NodeRef
    properties: Mapping[str, PropertyValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        rel = _RELATIONSHIP_BY_NAME.get(self.type)
        if rel is None:
            raise InvalidRecord(
                f"GraphEdge: unknown relationship {self.type!r}; the model has "
                f"{sorted(_RELATIONSHIP_BY_NAME)}"
            )
        for role, ref, allowed in (
            ("source", self.source, rel.sources),
            ("target", self.target, rel.targets),
        ):
            if not isinstance(ref, NodeRef):
                raise InvalidRecord(
                    f"GraphEdge {self.type}: {role} must be a NodeRef (use node.ref), "
                    f"got {type(ref).__name__}"
                )
            if ref.label not in allowed:
                raise InvalidRecord(
                    f"GraphEdge {self.type}: {role} must be one of {allowed}, got {ref.label}"
                )
        # A closed model: an edge property nobody declared is far more likely a typo
        # ("titel") than a new fact, and it would be written silently.
        undeclared = set(self.properties) - set(rel.properties)
        if undeclared:
            raise InvalidRecord(
                f"GraphEdge {self.type}: undeclared properties {sorted(undeclared)}; "
                f"the model declares {rel.properties}"
            )
        _check_properties(self.properties, {}, f"GraphEdge {self.type}")
        object.__setattr__(self, "properties", _frozen(self.properties))
