"""Tests for HybridRetriever and its fusion function.

Three layers, cheapest first:
* pure: `fuse_rankings` on hand-built lists, no services and no fakes;
* hermetic: `HybridRetriever` with fake sub-retrievers, plus one test using a REAL
  vector store on in-memory Qdrant with a toy embedding model;
* live (`-m live`): the real `chunks` collection, the real graph and the real embedding
  model together -- the first tests in this project that need Neo4j, Qdrant and Ollama at once.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

from src.embedding.embedder import Embedder, build_ollama_client
from src.graph.setup import build_driver
from src.ingestion.schemas import DocumentChunk
from src.retrieval.graph_retriever import GraphRetriever
from src.retrieval.graph_store import GraphStore
from src.retrieval.hybrid_retriever import HybridRetriever, fuse_rankings
from src.vectorstore.ingest import run_ingestion
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore, ScoredChunk
from tests.support import (
    OLLAMA_URL,
    QDRANT_URL,
    BagOfWordsClient,
    live_services_down,
    make_chunk,
    neo4j_down,
    pinned_digest,
    write_corpus,
)

RRF = 60
# the smoothing constant these tests expect the module to use by default


def chunk(name):
    return make_chunk(name, 0, f"text of {name}")
    # builds a tiny chunk whose chunk_id is "<name>_0"


def scored(*names):
    return [
        ScoredChunk(chunk=chunk(name), score=1.0 - 0.1 * position)
        for position, name in enumerate(names)
    ]
    # builds a vector-style result list, best first, with falling fake cosine scores


def plain(*names):
    return [chunk(name) for name in names]
    # builds a graph-style result list, best first, with no scores


def ids(fused):
    return [item.chunk.chunk_id for item in fused]
    # lists the chunk ids of a fused result in order


def recomputed_score(item, vector_weight=1.0, graph_weight=1.0):
    total = 0.0
    # starts the sum at zero before adding either list's contribution
    if item.vector_rank is not None:
        # only adds the vector contribution if vector search ranked this chunk
        total += vector_weight / (RRF + item.vector_rank)
        # adds the weighted reciprocal of the vector rank
    if item.graph_rank is not None:
        # only adds the graph contribution if graph retrieval ranked this chunk
        total += graph_weight / (RRF + item.graph_rank)
        # adds the weighted reciprocal of the graph rank
    return total
    # hands back the score recomputed from the ranks alone


# --- pure: fuse_rankings ---------------------------------------------------------------------


def test_a_chunk_found_by_both_lists_outranks_chunks_found_by_only_one():
    fused = fuse_rankings(
        scored("A", "B", "C"), plain("D", "B", "E"), vector_weight=1, graph_weight=1
    )
    # fuses two lists that share exactly one chunk, B, at rank 2 in each
    assert ids(fused)[0] == "B_0"
    # requires the shared chunk to come first even though neither list ranked it first
    assert fused[0].score == pytest.approx(2 / (RRF + 2))
    # requires its score to be the two reciprocal-rank contributions added together


def test_the_fused_score_is_the_weighted_sum_of_reciprocal_ranks():
    fused = fuse_rankings(scored("X", "A"), plain("A"), vector_weight=2, graph_weight=3)
    # fuses lists where A is at vector rank 2 and graph rank 1, with unequal weights
    a_item = next(item for item in fused if item.chunk.chunk_id == "A_0")
    # picks out A's fused result
    assert a_item.score == pytest.approx(2 / (RRF + 2) + 3 / (RRF + 1))
    # requires 2/(60+2) from the vector list plus 3/(60+1) from the graph list


def test_weights_decide_between_chunks_that_only_one_retriever_found():
    vector, graph = scored("A"), plain("B")
    # sets up A found only by vector search and B found only by graph retrieval
    graph_heavy = fuse_rankings(vector, graph, vector_weight=1, graph_weight=3)
    # fuses with the graph weighted three times as heavily
    assert ids(graph_heavy) == ["B_0", "A_0"]
    # requires the graph-only chunk to win
    vector_heavy = fuse_rankings(vector, graph, vector_weight=3, graph_weight=1)
    # fuses with the vector weighted three times as heavily
    assert ids(vector_heavy) == ["A_0", "B_0"]
    # requires the vector-only chunk to win


def test_equal_scores_are_broken_by_vector_rank_first():
    fused = fuse_rankings(scored("A"), plain("B"), vector_weight=1, graph_weight=1)
    # fuses two chunks that each sit at rank 1 in a different list, so their scores tie
    assert fused[0].score == pytest.approx(fused[1].score)
    # confirms the scores really are tied
    assert ids(fused) == ["A_0", "B_0"]
    # requires the chunk with a vector rank to come before the chunk without one


def test_a_zero_weight_silences_that_list_but_its_chunks_still_appear_last():
    fused = fuse_rankings(scored("A", "B"), plain("C"), vector_weight=1, graph_weight=0)
    # fuses with the graph list given no weight at all
    assert ids(fused) == ["A_0", "B_0", "C_0"]
    # requires the vector order first, then the silenced graph-only chunk
    assert fused[-1].score == 0
    # requires the silenced chunk to score exactly zero


def test_ranks_and_the_raw_vector_score_are_recorded():
    fused = fuse_rankings(scored("A", "B"), plain("B", "C"), vector_weight=1, graph_weight=1)
    # fuses lists where B is in both, A only in vector, and C only in graph
    by_id = {item.chunk.chunk_id: item for item in fused}
    # indexes the fused results by chunk id for easy lookup
    shared = by_id["B_0"]
    # picks out the chunk both lists found
    assert (shared.vector_rank, shared.graph_rank) == (2, 1)
    # requires both of its ranks to be recorded
    assert shared.vector_score == pytest.approx(0.9)
    # requires its raw cosine similarity to be carried through
    graph_only = by_id["C_0"]
    # picks out the chunk only graph retrieval found
    assert (graph_only.vector_rank, graph_only.graph_rank, graph_only.vector_score) == (
        None,
        2,
        None,
    )
    # requires the missing vector information to be None rather than invented


def test_a_chunk_repeated_inside_one_list_counts_once_at_its_first_rank():
    fused = fuse_rankings(scored("A", "A", "B"), [], vector_weight=1, graph_weight=1)
    # fuses a vector list that repeats A, with no graph results
    assert ids(fused) == ["A_0", "B_0"]
    # requires each chunk to appear once in the output
    assert fused[0].score == pytest.approx(1 / (RRF + 1))
    # requires A to score only its first-rank contribution, not the repeat's as well


def test_a_chunk_repeated_inside_the_graph_list_counts_once_at_its_first_rank():
    fused = fuse_rankings([], plain("A", "A", "B"), vector_weight=1, graph_weight=1)
    # fuses a graph list that repeats A, with no vector results -- the mirror image of the
    # test above, exercising the graph list's OWN duplicate-skipping branch, not the vector one
    assert ids(fused) == ["A_0", "B_0"]
    # requires each chunk to appear once in the output
    assert fused[0].score == pytest.approx(1 / (RRF + 1))
    # requires A to score only its first-rank graph contribution, not the repeat's as well


def test_an_empty_list_contributes_nothing_and_the_other_list_order_survives():
    vector_only = fuse_rankings(scored("A", "B", "C"), [], vector_weight=1, graph_weight=1)
    # fuses with an empty graph list
    assert ids(vector_only) == ["A_0", "B_0", "C_0"]
    # requires the vector order to come through unchanged
    graph_only = fuse_rankings([], plain("A", "B", "C"), vector_weight=1, graph_weight=1)
    # fuses with an empty vector list
    assert ids(graph_only) == ["A_0", "B_0", "C_0"]
    # requires the graph order to come through unchanged
    assert fuse_rankings([], [], vector_weight=1, graph_weight=1) == []
    # requires two empty lists to fuse to an empty result


@pytest.mark.parametrize("bad", [-1, -0.5, float("nan"), float("inf"), True, "1", None])
# tries a negative, a NaN, an infinity, a bool, a string and None as a weight
def test_unusable_weights_are_rejected(bad):
    with pytest.raises(ValueError, match="finite number"):
        # expects a ValueError that names what a weight must be
        fuse_rankings([], [], vector_weight=bad, graph_weight=1)
        # passes the bad value as the vector weight
    with pytest.raises(ValueError, match="finite number"):
        # expects the same error for the other weight
        fuse_rankings([], [], vector_weight=1, graph_weight=bad)
        # passes the bad value as the graph weight


def test_two_zero_weights_are_rejected():
    with pytest.raises(ValueError, match="cannot both be 0"):
        # expects the special error for the one combination that scores everything zero
        fuse_rankings([], [], vector_weight=0, graph_weight=0)
        # passes two weights that are each valid alone


def test_a_negative_smoothing_constant_is_rejected():
    with pytest.raises(ValueError, match="rrf_c"):
        # expects an error that names the bad argument
        fuse_rankings([], [], vector_weight=1, graph_weight=1, rrf_c=-1)
        # passes a negative smoothing constant


# --- hermetic: HybridRetriever with fake sub-retrievers --------------------------------------


class FakeVector:
    def __init__(self, hits, calls):
        self.hits = hits
        # keeps the canned scored chunks this fake will return
        self.calls = calls
        # keeps the shared list that records every call made to any fake

    def search_with_scores(self, query_text, k):
        self.calls.append(("vector", query_text, k))
        # records that vector search was asked, and for how many candidates
        return self.hits
        # returns the canned scored chunks


class FakeGraph:
    def __init__(self, hits, calls):
        self.hits = hits
        # keeps the canned chunks this fake will return
        self.calls = calls
        # keeps the shared list that records every call made to any fake

    def retrieve(self, query, k):
        self.calls.append(("graph", query, k))
        # records that graph retrieval was asked, and for how many candidates
        return self.hits
        # returns the canned chunks


class DownVector:
    def search_with_scores(self, query_text, k):
        raise RuntimeError("qdrant down")
        # simulates the vector service being unreachable


class DownGraph:
    def retrieve(self, query, k):
        raise RuntimeError("neo4j down")
        # simulates the graph service being unreachable


def build(vector_hits=(), graph_hits=(), **options):
    calls = []
    # makes the shared list both fakes will record their calls into
    hybrid = HybridRetriever(
        FakeVector(list(vector_hits), calls), FakeGraph(list(graph_hits), calls), **options
    )
    # wires a HybridRetriever to the two fakes, passing on any constructor options
    return hybrid, calls
    # hands back the retriever and the call log


def test_each_retriever_is_asked_for_three_times_k_and_vector_goes_first():
    hybrid, calls = build(scored(*"ABCDEF"), plain(*"GHIJKL"))
    # builds a retriever whose two fakes each hold six candidates
    result = hybrid.retrieve("q", 2)
    # asks for the top 2 fused chunks
    assert calls == [("vector", "q", 6), ("graph", "q", 6)]
    # requires both retrievers to be asked for 3 x 2 = 6, vector before graph
    assert len(result) == 2
    # requires the fused list to be cut back to k


def test_the_candidate_multiplier_is_configurable():
    hybrid, calls = build(candidate_multiplier=5)
    # builds a retriever that asks for five times k
    hybrid.retrieve("q", 2)
    # runs one retrieval so the fakes record their calls
    assert calls == [("vector", "q", 10), ("graph", "q", 10)]
    # requires both retrievers to be asked for 5 x 2 = 10


def test_constructor_default_weights_apply_and_per_call_weights_override_them():
    hybrid, _ = build(scored("A"), plain("B"), default_graph_weight=3)
    # builds a retriever whose default graph weight is three times the vector weight
    assert [c.chunk_id for c in hybrid.retrieve("q", 2)] == ["B_0", "A_0"]
    # requires the defaults to put the graph-only chunk first
    assert [c.chunk_id for c in hybrid.retrieve("q", 2, 1, 1)] == ["A_0", "B_0"]
    # requires both weights passed in the call to replace the defaults
    assert [c.chunk_id for c in hybrid.retrieve("q", 2, graph_weight=1)] == ["A_0", "B_0"]
    # requires a single weight passed in the call to replace only its own default


def test_an_empty_graph_result_degrades_to_the_vector_order():
    hybrid, _ = build(scored("A", "B", "C"), [])
    # builds a retriever whose graph side finds nothing
    assert [c.chunk_id for c in hybrid.retrieve("q", 3)] == ["A_0", "B_0", "C_0"]
    # requires the result to be exactly the vector order


@pytest.mark.parametrize("failing", ["vector", "graph"])
# runs the test once with each side failing
def test_an_error_from_either_retriever_propagates(failing):
    vector = DownVector() if failing == "vector" else FakeVector(scored("A"), [])
    # makes the vector side fail only in the first case
    graph = DownGraph() if failing == "graph" else FakeGraph(plain("B"), [])
    # makes the graph side fail only in the second case
    hybrid = HybridRetriever(vector, graph)
    # wires the retriever to one failing and one working side
    with pytest.raises(RuntimeError, match="down"):
        # expects the failure to come out of retrieve rather than be swallowed
        hybrid.retrieve("q", 2)
        # runs a retrieval that touches both sides


@pytest.mark.parametrize(
    ("k", "vector_weight", "graph_weight"),
    [(0, 1, 1), (-1, 1, 1), (True, 1, 1), (2.5, 1, 1), (2, -1, 1), (2, 1, float("nan")), (2, 0, 0)],
)
# tries a bad k, a bad weight, and two zero weights
def test_bad_arguments_are_rejected_before_either_retriever_is_called(
    k, vector_weight, graph_weight
):
    hybrid, calls = build(scored("A"), plain("B"))
    # builds a retriever whose fakes record every call
    with pytest.raises(ValueError):
        # expects the bad argument to be refused
        hybrid.retrieve("q", k, vector_weight, graph_weight)
        # runs a retrieval with the bad argument
    assert calls == []
    # requires that neither retriever was contacted at all


def test_bad_constructor_arguments_are_rejected():
    vector, graph = FakeVector([], []), FakeGraph([], [])
    # makes two harmless fakes to pass to each attempted construction
    with pytest.raises(ValueError, match="candidate_multiplier"):
        # expects a multiplier of zero to be refused
        HybridRetriever(vector, graph, candidate_multiplier=0)
        # asks for zero times k candidates
    with pytest.raises(ValueError, match="cannot both be 0"):
        # expects two zero default weights to be refused
        HybridRetriever(vector, graph, default_vector_weight=0, default_graph_weight=0)
        # gives both retrievers no weight by default
    with pytest.raises(ValueError, match="rrf_c"):
        # expects a negative smoothing constant to be refused
        HybridRetriever(vector, graph, rrf_c=-1)
        # passes a negative constant


def test_retrieve_returns_the_same_chunks_as_retrieve_with_scores():
    hybrid, _ = build(scored("A", "B"), plain("B", "C"))
    # builds a retriever with overlapping candidate lists
    chunks = hybrid.retrieve("q", 3)
    # runs the plain method
    fused = hybrid.retrieve_with_scores("q", 3)
    # runs the method that keeps scores and ranks
    assert [c.chunk_id for c in chunks] == ids(fused)
    # requires both methods to give the same chunks in the same order
    assert all(isinstance(c, DocumentChunk) for c in chunks)
    # requires the plain method to return real DocumentChunk objects


# --- hermetic: a REAL vector store (in-memory Qdrant, toy embeddings) with a fake graph ------

CORPUS = {
    "company/about.md": [
        "Acme was founded in 2015 by Ada Lovelace.",
        "Acme employs thirty people across two offices.",
    ],
    "company/culture.md": ["Our values are curiosity, honesty and kindness."],
    "products/widget.md": [
        "The widget ships in blue and red colours.",
        "Widget pricing starts at ten dollars per unit.",
    ],
}
# the same small toy corpus tests/test_retrieval.py uses


@pytest.fixture
def real_vector_store(tmp_path):
    client = QdrantClient(":memory:")
    # starts a real Qdrant that lives only in this process
    model = BagOfWordsClient()
    # makes the toy embedding model, which needs no Ollama
    chunks_dir = tmp_path / "chunks"
    # picks a temporary folder for the chunk files
    write_corpus(chunks_dir, CORPUS)
    # writes the toy corpus in the layout the ingestion job reads
    run_ingestion(
        client,
        lambda: Embedder(model, verify=pinned_digest),
        collection="chunks",
        chunks_dir=chunks_dir,
        log_path=tmp_path / "log.json",
    )
    # runs the real ingestion job to embed and store every toy chunk
    return QdrantVectorStore(client, Embedder(model, verify=pinned_digest), collection="chunks")
    # hands back a real vector store over that in-memory collection


def test_a_real_vector_store_feeds_the_fusion(real_vector_store):
    widget = real_vector_store.search("What is the widget pricing?", k=1)[0]
    # takes a real chunk to play the part of graph retrieval's only answer
    hybrid = HybridRetriever(real_vector_store, FakeGraph([widget], []))
    # pairs the real vector store with a fake graph that returns just that chunk
    fused = hybrid.retrieve_with_scores("Who founded Acme?", 3)
    # asks a question about the company, not the widget
    assert len(fused) == 3
    # requires k results
    assert all(item.vector_score is not None for item in fused)
    # requires every result to carry a real cosine score from the real store
    assert fused[0].chunk.chunk_id == widget.chunk_id
    # requires the chunk both retrievers found to come first, ahead of vector's own top hit
    assert (fused[0].graph_rank, fused[0].vector_rank >= 2) == (1, True)
    # requires it to be graph rank 1 and to have been ranked below vector's top hit
    assert fused[1].chunk.chunk_id == "company/about.md_0"
    # requires vector search's own top hit to come second
    assert fused[1].graph_rank is None
    # requires that chunk to show graph retrieval never saw it
    for item in fused:
        # checks every fused result against the formula
        assert item.score == pytest.approx(recomputed_score(item))
        # requires the stored score to equal the sum recomputed from the ranks


# --- live: the real collection, the real graph and the real embedding model ------------------

KNOWN_ANSWERS = [
    ("Who founded Insurellm?", {"company/about.md_0"}),
    ("What are the company values?", {"company/careers.md_1", "company/culture.md_1"}),
    ("Which product is for life insurance?", {"products/Lifellm.md_0"}),
]
# the three questions tests/test_retrieval.py already knows vector search answers


@pytest.fixture(scope="module")
def live():
    reason = live_services_down() or neo4j_down()
    # checks whether Qdrant, Ollama or Neo4j is unreachable
    if reason:
        # covers the case where at least one service is down
        pytest.skip(reason)
        # skips every live test, saying which service is missing
    embedder = Embedder(build_ollama_client(OLLAMA_URL), ollama_url=OLLAMA_URL)
    # builds the real query embedder, which checks the pinned model is present
    vector = QdrantVectorStore(build_qdrant_client(QDRANT_URL), embedder, collection="chunks")
    # opens the real, full collection built by the P3 ingestion job
    if vector.count() == 0:
        # covers a fresh checkout where nothing has been indexed
        pytest.skip("the 'chunks' collection is empty; run `python -m src.vectorstore.ingest`")
        # skips instead of failing, saying how to fix it
    with build_driver() as driver:
        # opens the real graph connection for the length of the module
        store = GraphStore(driver)
        # wraps the driver in the graph store
        if not any(store.all_entity_names().values()):
            # covers a database where the P4 ingestion job has not run
            pytest.skip("the graph is empty; run `python -m src.graph.ingest`")
            # skips instead of failing, saying how to fix it
        graph = GraphRetriever(store)
        # builds the real graph retriever over the real graph
        yield SimpleNamespace(vector=vector, graph=graph, hybrid=HybridRetriever(vector, graph))
        # hands the tests all three, with the hybrid using its default settings


@pytest.mark.live
@pytest.mark.parametrize(("question", "acceptable"), KNOWN_ANSWERS)
# runs once per known-answer question
def test_live_known_answers_still_rank_in_the_hybrid_top_3(live, question, acceptable):
    found = [c.chunk_id for c in live.hybrid.retrieve(question, 3)]
    # asks the hybrid for its top 3
    assert acceptable & set(found), found
    # requires an accepted chunk among them, so adding the graph did not cost a known answer


@pytest.mark.live
def test_live_ceo_question_that_vector_search_misses_is_found_by_the_hybrid(live):
    question = "Who is the CEO of Insurellm?"
    # the question tests/test_retrieval.py records as a known vector-only miss
    accepted = {"employees/Avery Lancaster.md_0", "company/about.md_0"}
    # the chunks that count as answering it
    vector_top_3 = {c.chunk_id for c in live.vector.search(question, 3)}
    # asks vector search alone for its top 3
    assert not accepted & vector_top_3
    # confirms the miss: vector search alone does not surface the CEO's record
    hybrid_top_3 = {c.chunk_id for c in live.hybrid.retrieve(question, 3)}
    # asks the hybrid for its top 3
    assert accepted & hybrid_top_3, hybrid_top_3
    # requires the hybrid to surface it; graph retrieval supplies it, and among its tied hits
    # company/about.md_0 happens to sort first by chunk_id (see notes/step-5.2-hybrid-retriever.md)


@pytest.mark.live
@pytest.mark.xfail(
    strict=True,
    reason="Known miss at k=3: each retriever is asked for 3*k = 9 candidates, too few to reach "
    "vector search's first Carllm product page (its rank 22), and graph retrieval lists product "
    "pages after contract and employee chunks (chunk_id tie-break). Found at k=10, see the next "
    "test and notes/step-5.2-hybrid-retriever.md.",
)
def test_live_carllm_product_question_at_k3_is_still_a_miss(live):
    found = [c.chunk_id for c in live.hybrid.retrieve("What does the Carllm product do?", 3)]
    # asks the hybrid for its top 3
    assert any(chunk_id.startswith("products/Carllm.md") for chunk_id in found), found
    # requires a Carllm product page among them, which does not currently happen


@pytest.mark.live
def test_live_carllm_product_question_is_found_once_the_candidate_pool_is_deeper(live):
    found = [c.chunk_id for c in live.hybrid.retrieve("What does the Carllm product do?", 10)]
    # asks for 10, which makes each retriever supply 30 candidates instead of 9
    assert any(chunk_id.startswith("products/Carllm.md") for chunk_id in found[:3]), found
    # requires a Carllm product page within the first three results


@pytest.mark.live
def test_live_a_question_naming_no_entity_gives_exactly_the_vector_order(live):
    question = "What are the company values?"
    # a question that names no entity the graph knows
    hybrid_ids = [c.chunk_id for c in live.hybrid.retrieve(question, 5)]
    # asks the hybrid, whose graph side will find nothing
    vector_ids = [c.chunk_id for c in live.vector.search(question, 5)]
    # asks vector search alone
    assert hybrid_ids == vector_ids
    # requires the two to match exactly, so an empty graph side costs nothing


@pytest.mark.live
def test_live_every_fused_score_equals_its_recomputed_reciprocal_rank_sum(live):
    fused = live.hybrid.retrieve_with_scores(
        "Does DriveSmart Insurance license Carllm?", 5, 1.0, 2.0
    )
    # runs a question that names two entities, with the graph weighted double
    for item in fused:
        # checks every fused result
        assert item.score == pytest.approx(recomputed_score(item, 1.0, 2.0))
        # requires its score to equal the sum recomputed from its two ranks
    scores = [item.score for item in fused]
    # collects the scores in result order
    assert scores == sorted(scores, reverse=True)
    # requires the results to be ordered best score first


@pytest.mark.live
def test_live_results_are_identical_on_a_second_call(live):
    first = [c.chunk_id for c in live.hybrid.retrieve("Who works on Carllm?", 5)]
    # runs a question once
    second = [c.chunk_id for c in live.hybrid.retrieve("Who works on Carllm?", 5)]
    # runs the same question again
    assert first == second
    # requires the same chunks in the same order
