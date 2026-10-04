"""Graph-based retrieval: find the entities a question names, walk outward from them
through the knowledge graph, and return the chunks connected to what was found.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from src.ingestion.pipeline import DEFAULT_OUTPUT_DIR, read_chunks
from src.ingestion.schemas import DocumentChunk
from src.retrieval.graph_store import GraphStore

DEFAULT_HOPS = 2
# sets the default traversal distance every GraphRetriever uses unless told otherwise


def _check_k(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        # rejects anything that is not a plain positive integer
        raise ValueError(f"k must be a positive integer, got {k!r}")


def _word_pattern(name: str) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(word) for word in name.split())
    # rebuilds the name as a regex, one escaped word per gap, joined by whitespace
    return re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE)
    # compiles it as a whole-word, case-insensitive pattern


def _match_entities(query: str, names_by_label: dict[str, list[str]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    # starts an empty list that every matching (label, name) pair gets added to
    for label, names in names_by_label.items():
        # repeats once per label the graph model defines (Company, Product, ...)
        for name in names:
            # repeats once per known name under that label
            if _word_pattern(name).search(query):
                # checks whether that exact name appears as whole words in the query
                found.append((label, name))
                # records this label/name pair as one of the query's found entities
    return found
    # hands back every (label, name) pair the query text named


class GraphRetriever:
    """Finds entities in a question, then returns the chunks connected to them."""

    def __init__(
        self,
        store: GraphStore,
        *,
        chunks_dir: Path = DEFAULT_OUTPUT_DIR,
        hops: int = DEFAULT_HOPS,
    ) -> None:
        self._store = store
        # keeps the GraphStore this retriever will send every query through
        self._hops = hops
        # keeps how many hops outward this retriever is allowed to traverse
        self._names_by_label = store.all_entity_names()
        # asks the graph, once, for every entity name it currently holds
        self._chunks_by_id: dict[str, DocumentChunk] = {
            chunk.chunk_id: chunk for chunk in read_chunks(chunks_dir)
        }
        # loads every chunk from disk and indexes it by its own chunk_id

    def retrieve(self, query: str, k: int) -> list[DocumentChunk]:
        """Chunks connected to whichever known entities `query` names, nearest first."""
        _check_k(k)
        # confirms k is a usable positive integer before doing any work
        seeds = self._find_entities(query)
        # finds every (label, name) pair the query text actually names
        if not seeds:
            # covers the case where the query names no entity this graph knows
            return []
            # hands back an empty list rather than guessing or raising

        seed_hops: dict[str, dict[tuple[str, str], int]] = {}
        # tracks, for each chunk_id, the smallest hop distance each seed entity reached it at

        for label, name in seeds:
            # repeats the whole expansion once per entity the query named
            entities_by_hop = self._entities_by_hop(name, label)
            # groups every entity within range by its exact distance from this seed
            for hop, entities in entities_by_hop.items():
                # repeats once per hop distance, from 0 up to self._hops
                for entity_label, entity_name in entities:
                    # repeats once per entity found at this particular hop distance
                    for chunk_id in self._chunk_ids_for(entity_name, entity_label):
                        # repeats once per chunk connected to that entity
                        hops_by_seed = seed_hops.setdefault(chunk_id, {})
                        # finds, or starts, this chunk's record of which seeds reached it
                        hops_by_seed[(label, name)] = min(hops_by_seed.get((label, name), hop), hop)
                        # keeps the smallest hop distance at which THIS seed reached this chunk

        best_hop = {chunk_id: min(hops.values()) for chunk_id, hops in seed_hops.items()}
        # works out each chunk's nearest distance from any seed
        agreeing = {
            chunk_id: sum(1 for hop in hops.values() if hop == best_hop[chunk_id])
            for chunk_id, hops in seed_hops.items()
        }
        # counts, per chunk, the seeds that reach it at exactly that nearest distance
        ranked = sorted(
            best_hop,
            key=lambda chunk_id: (best_hop[chunk_id], -agreeing[chunk_id], chunk_id),
        )
        # sorts by hop distance, then agreeing seeds, then chunk_id so ties never vary
        top = ranked[:k]
        # keeps only the first k chunk_ids from the ranked list
        return [self._chunks_by_id[chunk_id] for chunk_id in top if chunk_id in self._chunks_by_id]
        # turns the surviving chunk_ids back into real DocumentChunk objects

    def _find_entities(self, query: str) -> list[tuple[str, str]]:
        return _match_entities(query, self._names_by_label)
        # delegates to the free function below, passing this instance's own cached names

    def _entities_by_hop(self, name: str, label: str) -> dict[int, list[tuple[str | None, str]]]:
        by_hop: dict[int, list[tuple[str | None, str]]] = {0: [(label, name)]}
        # starts hop 0 as the seed entity itself, the only one whose label is known
        previous_names: set[str] = set()
        # tracks every name returned by the previous, shorter multi_hop_search call
        for hop in range(1, self._hops + 1):
            # repeats once per hop distance from 1 up to the configured maximum
            within_hop = set(self._store.multi_hop_search(name, hop, label=label))
            # asks the graph for every entity within `hop` steps of the seed
            new_at_this_hop = within_hop - previous_names
            # keeps only the names that are NEW at this hop, not already closer
            by_hop[hop] = [(None, entity_name) for entity_name in sorted(new_at_this_hop)]
            # records this hop's new entities, with no label known for any of them
            previous_names = within_hop
            # remembers this hop's full result, to diff against the next, longer one
        return by_hop
        # hands back every hop distance mapped to the entities first reached there

    def _chunk_ids_for(self, name: str, label: str | None) -> Iterable[str]:
        chunk_ids: set[str] = set()
        # starts an empty set that every chunk_id connected to this entity gets added to
        edges = self._store.search_related(name, label=label, include_provenance=True)
        # asks the graph for every edge touching this entity, including MENTIONS
        for edge in edges:
            # repeats once per edge returned for this entity
            if edge.type == "MENTIONS":
                # checks whether this edge is a chunk directly naming the entity
                chunk_ids.add(edge.source.key["chunk_id"])
                # adds that chunk's id, since MENTIONS always points FROM a Chunk
            elif edge.type == "DESCRIBED_IN":
                # checks whether this edge points at the entity's own home document
                doc_id = edge.target.key["doc_id"]
                # reads that document's id off the edge's target
                chunk_ids.update(self._store.chunks_of_document(doc_id))
                # adds every chunk belonging to that whole document
        return chunk_ids
        # hands back every chunk_id connected to this one entity
