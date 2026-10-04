"""Tests for GraphRetriever: hermetic tests for the pure text-matching helpers, live
tests (read-only, no fixture needed) for entity linking and multi-hop retrieval against
the real corpus already loaded by P4-M5.

GraphRetriever never writes to Neo4j, so the live tests need no cleanup and cannot
disturb the real corpus's data -- unlike every other live test in this project's graph
suite, which has to write and then sweep a throwaway fixture.
"""

from __future__ import annotations

import pytest

from src.graph.records import GraphEdge, NodeRef
from src.graph.setup import build_driver
from src.retrieval.graph_retriever import GraphRetriever, _check_k, _match_entities, _word_pattern
from src.retrieval.graph_store import GraphStore
from tests.support import neo4j_down, write_corpus

# --- hermetic: a fake store, to prove the ranking order is reproducible ----------------------


class FakeStore:
    """Answers the four GraphStore reads GraphRetriever makes, with canned data."""

    def __init__(self, entities, neighbours=None):
        self._entities = entities
        # keeps each entity's (label, ids of the chunks that mention it), keyed by name
        self._neighbours = neighbours or {}
        # keeps, per entity, the names first reached at each hop distance

    def all_entity_names(self):
        names = {}
        # starts an empty label -> names table
        for name, (label, _) in self._entities.items():
            # goes through every entity the fake graph holds
            names.setdefault(label, []).append(name)
            # files the entity's name under its own label
        return names
        # hands back the table GraphRetriever caches at construction

    def multi_hop_search(self, name, hops, *, label=None):
        by_distance = self._neighbours.get(name, {})
        # looks up the names first reached at each distance from this entity
        return [n for distance, found in by_distance.items() if distance <= hops for n in found]
        # returns every name within `hops` steps, as the real method does

    def search_related(self, name, *, label=None, include_provenance=False):
        entity_label, chunk_ids = self._entities[name]
        # looks up this entity's label and the chunks that mention it
        entity = NodeRef(entity_label, {"name": name})
        # builds the reference that every MENTIONS edge below points at
        return [
            GraphEdge("MENTIONS", NodeRef("Chunk", {"chunk_id": chunk_id}), entity)
            for chunk_id in chunk_ids
        ]
        # returns one MENTIONS edge per chunk id the fake graph says mentions this entity

    def chunks_of_document(self, doc_id):
        return []
        # says no entity has a home document, so DESCRIBED_IN never adds chunks


def corpus_of(tmp_path, letters):
    names = [f"docs/{letter}.md" for letter in letters]
    # names one document per letter
    write_corpus(tmp_path, {name: ["text"] for name in names})
    # writes one single-chunk document per name into a temporary chunks directory
    return [f"{name}_0" for name in names]
    # hands back the chunk id each of those documents has


def test_chunks_tied_on_distance_and_convergence_come_back_in_chunk_id_order(tmp_path):
    chunk_ids = corpus_of(tmp_path, "hgfedcba")
    # writes eight documents whose names are in reverse alphabetical order on purpose
    store = FakeStore({"Alpha": ("Product", chunk_ids)})
    # builds a fake graph where all eight chunks mention Alpha, at the same distance
    retriever = GraphRetriever(store, chunks_dir=tmp_path, hops=1)
    # builds a retriever over that fake graph
    returned = [chunk.chunk_id for chunk in retriever.retrieve("Tell me about Alpha", k=8)]
    # asks for all eight, which are perfectly tied on hop distance and convergence
    assert returned == sorted(chunk_ids)
    # requires the ties to come back in chunk_id order, never in Python's set-iteration order


def test_a_chunk_two_query_entities_both_reach_directly_outranks_one_only_one_reaches(tmp_path):
    chunk_ids = corpus_of(tmp_path, "abcd")
    # writes four documents, whose chunk ids sort a, b, c, d
    store = FakeStore(
        {"Alpha": ("Product", chunk_ids), "Beta": ("Company", [chunk_ids[3]])},
        neighbours={"Alpha": {2: ["Beta"]}, "Beta": {2: ["Alpha"]}},
    )
    # builds a fake graph where Alpha is mentioned by all four chunks, Beta only by d,
    # and the two entities are two hops apart, as a Company and its Product are
    retriever = GraphRetriever(store, chunks_dir=tmp_path, hops=2)
    # builds a retriever that follows two hops
    returned = [chunk.chunk_id for chunk in retriever.retrieve("Alpha and Beta", k=4)]
    # asks a question naming both entities
    assert returned[0] == chunk_ids[3]
    # requires chunk d, the only one BOTH entities mention directly, to come first, ahead of
    # a, b and c, which Beta reaches only by going through Alpha two hops away


# --- hermetic: the pure text-matching helpers, no database at all ----------------------------


def test_word_pattern_matches_whole_words_only():
    pattern = _word_pattern("Carllm")
    assert pattern.search("What does Carllm do?")
    assert not pattern.search("Carllmish is not a word")


def test_word_pattern_is_case_insensitive():
    assert _word_pattern("Carllm").search("what does carllm do")


def test_word_pattern_is_whitespace_tolerant_for_multi_word_names():
    pattern = _word_pattern("DriveSmart Insurance")
    assert pattern.search("Does DriveSmart   Insurance license Carllm?")


def test_match_entities_finds_every_known_name_present_in_the_query():
    names_by_label = {"Product": ["Carllm", "Homellm"], "Company": ["DriveSmart Insurance"]}
    found = _match_entities("Does DriveSmart Insurance license Carllm?", names_by_label)
    assert set(found) == {("Product", "Carllm"), ("Company", "DriveSmart Insurance")}


def test_match_entities_finds_nothing_for_an_unrelated_question():
    names_by_label = {"Product": ["Carllm"]}
    assert _match_entities("What is the weather today?", names_by_label) == []


def test_check_k_rejects_zero_negative_and_non_integers():
    for bad in (0, -1, 1.5, "3", True):
        with pytest.raises(ValueError, match="positive integer"):
            _check_k(bad)


# --- live: real entity linking and multi-hop retrieval, read-only, on the real corpus --------


@pytest.fixture(scope="module")
def retriever():
    reason = neo4j_down()
    if reason:
        pytest.skip(reason)
    with build_driver() as driver:
        store = GraphStore(driver)
        yield GraphRetriever(store)


@pytest.mark.live
def test_live_retrieve_returns_empty_list_for_a_question_naming_no_known_entity(retriever):
    assert retriever.retrieve("What is the weather today?", k=5) == []


@pytest.mark.live
def test_live_retrieve_finds_the_products_own_page_and_a_licensing_contract(retriever):
    # k is generous on purpose: the claim is about which chunks are reachable, not their order
    chunk_ids = {chunk.chunk_id for chunk in retriever.retrieve("What does Carllm do?", k=20)}
    assert "products/Carllm.md_2" in chunk_ids
    assert any(cid.startswith("contracts/Contract with") and "Carllm" in cid for cid in chunk_ids)


@pytest.mark.live
def test_live_retrieve_finds_an_employee_through_multi_hop_from_the_product(retriever):
    chunk_ids = {chunk.chunk_id for chunk in retriever.retrieve("Who works on Carllm?", k=20)}
    # Tyler Brooks's HR chunk mentions Carllm directly, so this is reachable at hop 0
    # already -- the claim is only that graph retrieval reaches employees from a product.
    assert any(cid.startswith("employees/") for cid in chunk_ids)


@pytest.mark.live
def test_live_retrieve_ranks_a_chunk_higher_when_two_query_entities_reach_it_directly(retriever):
    velocity = "contracts/Contract with Velocity Auto Solutions for Carllm.md_0"
    # the contract chunk that names both the client Velocity Auto Solutions and the product Carllm
    single = [chunk.chunk_id for chunk in retriever.retrieve("What does Carllm do?", k=20)]
    # asks about the product alone, where every Carllm chunk ties on distance and agreement
    both = [
        chunk.chunk_id
        for chunk in retriever.retrieve("Does Velocity Auto Solutions license Carllm?", k=20)
    ]
    # asks naming the client too, so this one chunk is reached directly by BOTH entities
    assert single.index(velocity) > 0
    # confirms it is not already first when only the product is named (by chunk_id order,
    # three other Carllm contracts sort ahead of it), so being first below is not luck
    assert both[0] == velocity
    # requires naming the second entity to promote it to first place


@pytest.mark.live
def test_live_retrieve_respects_k(retriever):
    assert len(retriever.retrieve("What does Carllm do?", k=1)) == 1
    assert len(retriever.retrieve("What does Carllm do?", k=3)) == 3


@pytest.mark.live
def test_live_retrieve_rejects_a_non_positive_k(retriever):
    with pytest.raises(ValueError, match="positive integer"):
        retriever.retrieve("Carllm", k=0)
