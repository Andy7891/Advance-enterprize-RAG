"""Tests for RetrievalPipeline.

Four layers, cheapest first:
* hermetic with fake stages: pool-size math, call order, field mapping, validation, errors;
* hermetic with a REAL HybridRetriever (in-memory Qdrant, toy embedder) feeding a fake
  reranker, proving the field mapping works against the real FusedChunk type;
* hermetic with a REAL HybridRetriever AND a REAL Reranker (the real Cohere SDK, its HTTP
  transport stubbed) -- proves the actual seam between two real classes, not two fakes;
* live (`-m live`): one test needs only Qdrant/Ollama/Neo4j (a fake reranker stands in, so
  no Cohere key is needed); the rest need COHERE_API_KEY too and skip without it.
"""

from __future__ import annotations

import json

import httpx
import pytest
from cohere.errors import TooManyRequestsError
from cohere.v2.types.v2rerank_response import V2RerankResponse
from cohere.v2.types.v2rerank_response_results_item import V2RerankResponseResultsItem
from qdrant_client import QdrantClient

from src.embedding.embedder import Embedder, build_ollama_client
from src.graph.setup import build_driver
from src.retrieval.graph_retriever import GraphRetriever
from src.retrieval.graph_store import GraphStore
from src.retrieval.hybrid_retriever import FusedChunk, HybridRetriever
from src.retrieval.pipeline import RetrievalPipeline, RetrievalResult
from src.retrieval.reranker import Reranker, RerankedChunk, build_cohere_client
from src.vectorstore.ingest import run_ingestion
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore
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


def chunk(name):
    return make_chunk(name, 0, f"text of {name}")
    # builds a tiny chunk whose chunk_id is "<name>_0"


def fused(name, score, *, vector_rank=None, graph_rank=None, vector_score=None):
    return FusedChunk(
        chunk=chunk(name),
        score=score,
        vector_rank=vector_rank,
        graph_rank=graph_rank,
        vector_score=vector_score,
    )
    # builds one hybrid-stage result exactly as HybridRetriever.retrieve_with_scores would


def reranked(name, relevance_score, input_rank):
    return RerankedChunk(chunk=chunk(name), relevance_score=relevance_score, input_rank=input_rank)
    # builds one rerank-stage result exactly as Reranker.rerank_with_scores would


def ids(items):
    return [getattr(item, "chunk", item).chunk_id for item in items]
    # lists the chunk ids of chunks or of ranked/reranked results, in order


# --- hermetic: RetrievalPipeline with fake stages ----------------------------------------------


class FakeHybrid:
    """Answers every call with the same canned pool, and records what it was asked."""

    def __init__(self, pool, calls):
        self.pool = pool
        # keeps the canned fused, scored chunks this fake will return
        self.calls = calls
        # keeps the shared list every fake in a test records its calls into

    def retrieve_with_scores(self, query, k):
        self.calls.append(("hybrid", query, k))
        # records that the hybrid stage was asked, and for how many candidates
        return self.pool
        # returns the canned pool


class FakeReranker:
    """Answers every call with the same canned result, and records what it was asked."""

    def __init__(self, result, calls):
        self.result = result
        # keeps the canned reranked chunks this fake will return
        self.calls = calls
        # keeps the shared list every fake in a test records its calls into

    def rerank_with_scores(self, query, chunks, k):
        self.calls.append(("rerank", query, ids(chunks), k))
        # records the question, the chunk ids it was handed, and k
        return self.result
        # returns the canned result


class DownHybrid:
    def retrieve_with_scores(self, query, k):
        raise RuntimeError("qdrant down")
        # simulates the hybrid stage's underlying services being unreachable


class DownReranker:
    def rerank_with_scores(self, query, chunks, k):
        raise RuntimeError("cohere down")
        # simulates the reranker's underlying service being unreachable


def build(pool=(), result=(), **options):
    calls = []
    # makes the shared list both fakes will record their calls into
    hybrid = FakeHybrid(list(pool), calls)
    # builds the fake hybrid stage, sharing the call log
    reranker = FakeReranker(list(result), calls)
    # builds the fake reranker, sharing the same call log
    pipeline = RetrievalPipeline(hybrid, reranker, **options)
    # wires a RetrievalPipeline to the two fakes, passing on any constructor options
    return pipeline, calls
    # hands back the pipeline and the call log


def test_the_default_pool_size_is_k_times_the_multiplier_once_above_the_floor():
    pipeline, calls = build()
    # builds a pipeline with the default multiplier (3) and floor (20)
    pipeline.retrieve("q", 10)
    # asks for 10, so k * multiplier (30) is already above the floor (20)
    assert calls[0] == ("hybrid", "q", 30)
    # requires the hybrid stage to have been asked for exactly 30 candidates


def test_the_pool_floor_applies_when_k_times_the_multiplier_is_smaller():
    pipeline, calls = build()
    # builds a pipeline with the default multiplier (3) and floor (20)
    pipeline.retrieve("q", 2)
    # asks for 2, so k * multiplier (6) is below the floor (20)
    assert calls[0] == ("hybrid", "q", 20)
    # requires the floor, not the multiplier, to have decided the pool size


def test_a_custom_multiplier_and_floor_are_used():
    pipeline, calls = build(pool_multiplier=5, pool_floor=3)
    # builds a pipeline with a custom multiplier and a floor smaller than any k tried
    pipeline.retrieve("q", 1)
    # asks for 1: k * multiplier (5) is already above the floor (3)
    assert calls[0] == ("hybrid", "q", 5)
    # requires the custom multiplier to have decided the pool size


def test_hybrid_runs_before_the_reranker():
    pipeline, calls = build(pool=[fused("A", 0.5)], result=[reranked("A", 0.9, 1)])
    # builds a pipeline whose two fakes both have something to return
    pipeline.retrieve("q", 1)
    # runs one query
    assert [call[0] for call in calls] == ["hybrid", "rerank"]
    # requires the hybrid stage to be called first, the reranker second


def test_the_reranker_receives_the_pool_chunks_in_hybrid_order():
    pool = [fused("B", 0.9), fused("A", 0.5), fused("C", 0.1)]
    # builds a pool that is NOT alphabetical, to prove order is preserved, not re-sorted
    pipeline, calls = build(pool=pool, result=[reranked("A", 0.9, 2)])
    # builds a pipeline over that pool
    pipeline.retrieve("q", 1)
    # runs one query
    assert calls[1] == ("rerank", "q", ["B_0", "A_0", "C_0"], 1)
    # requires the reranker to receive exactly the pool's own chunks, in the pool's own order


def test_the_final_result_carries_every_stage_score_and_rank():
    pool = [
        fused("A", 0.111, vector_rank=1, graph_rank=None, vector_score=0.9),
        fused("B", 0.222, vector_rank=None, graph_rank=1, vector_score=None),
    ]
    # builds a two-chunk pool, one vector-only, one graph-only
    result = [reranked("B", 0.77, input_rank=2), reranked("A", 0.33, input_rank=1)]
    # has the reranker promote B (the graph-only chunk) ahead of A
    pipeline, _ = build(pool=pool, result=result)
    # builds a pipeline over that pool and that reranked order
    answer = pipeline.retrieve("who?", 2)
    # runs the query
    assert isinstance(answer, RetrievalResult)
    # requires a RetrievalResult, as the task's signature specifies
    assert answer.query == "who?"
    # requires the original question to be carried on the result
    first, second = answer.results
    # unpacks the two ranked chunks
    assert (first.chunk.chunk_id, first.final_rank, first.relevance_score) == ("B_0", 1, 0.77)
    # requires B to be first, ranked 1, with its own relevance score
    assert (first.hybrid_score, first.hybrid_rank) == (0.222, 2)
    # requires B's hybrid score and its position in the pool (2nd) to be carried over
    assert (first.vector_rank, first.graph_rank, first.vector_score) == (None, 1, None)
    # requires B's per-list ranks to match exactly what the hybrid stage gave it
    assert (second.chunk.chunk_id, second.final_rank, second.hybrid_rank) == ("A_0", 2, 1)
    # requires A to be second, ranked 2, with its own pool position (1st) carried over


def test_final_rank_is_1_based_and_follows_the_reranked_order():
    pool = [fused("A", 0.5), fused("B", 0.5), fused("C", 0.5)]
    # builds a three-chunk pool
    result = [reranked("C", 0.9, 3), reranked("A", 0.8, 1), reranked("B", 0.1, 2)]
    # has the reranker return C, A, B in that order
    pipeline, _ = build(pool=pool, result=result)
    # builds a pipeline over that pool and that reranked order
    answer = pipeline.retrieve("q", 3)
    # runs the query
    assert [(item.chunk.chunk_id, item.final_rank) for item in answer.results] == [
        ("C_0", 1),
        ("A_0", 2),
        ("B_0", 3),
    ]
    # requires final_rank to number the RERANKED order, 1st to last


def test_results_is_a_tuple_not_a_list():
    pipeline, _ = build(pool=[fused("A", 0.5)], result=[reranked("A", 0.5, 1)])
    # builds a pipeline with one chunk in both stages
    answer = pipeline.retrieve("q", 1)
    # runs the query
    assert isinstance(answer.results, tuple)
    # requires an immutable tuple, matching this project's frozen-dataclass convention


def test_an_empty_hybrid_pool_still_calls_the_reranker_with_nothing():
    pipeline, calls = build(pool=[], result=[])
    # builds a pipeline whose hybrid stage finds nothing at all
    answer = pipeline.retrieve("q", 3)
    # runs the query
    assert calls[1] == ("rerank", "q", [], 3)
    # requires the reranker to still be called, with an empty chunk list (its own job to
    # short-circuit, not the pipeline's -- see Reranker.rerank_with_scores)
    assert answer.results == ()
    # requires an empty result, not an error


@pytest.mark.parametrize("bad", [0, -1, 1.5, "3", True, None])
# runs once per unusable k
def test_a_bad_k_is_rejected_before_either_stage_is_called(bad):
    pipeline, calls = build(pool=[fused("A", 0.5)], result=[reranked("A", 0.5, 1)])
    # builds a pipeline that would otherwise happily answer
    with pytest.raises(ValueError, match="positive integer"):
        # expects a clear error naming k
        pipeline.retrieve("q", bad)
        # asks with the unusable k
    assert calls == []
    # requires that neither the hybrid stage nor the reranker was called


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
# runs once per unusable query
def test_a_blank_query_is_rejected_before_either_stage_is_called(bad):
    pipeline, calls = build(pool=[fused("A", 0.5)], result=[reranked("A", 0.5, 1)])
    # builds a pipeline that would otherwise happily answer
    with pytest.raises(ValueError, match="non-empty string"):
        # expects a clear error naming query
        pipeline.retrieve(bad, 1)
        # asks with the unusable query
    assert calls == []
    # requires that neither the hybrid stage nor the reranker was called


def test_an_error_from_the_hybrid_stage_propagates_and_the_reranker_is_never_called():
    calls = []
    # keeps a record the reranker fake will append to, if it is ever called at all
    pipeline = RetrievalPipeline(DownHybrid(), FakeReranker([], calls))
    # pairs a broken hybrid stage with a reranker that would otherwise succeed
    with pytest.raises(RuntimeError, match="qdrant down"):
        # expects the very same error, unchanged
        pipeline.retrieve("q", 1)
        # runs a query
    assert calls == []
    # requires the reranker to never have been reached


def test_an_error_from_the_reranker_propagates():
    pipeline = RetrievalPipeline(FakeHybrid([fused("A", 0.5)], []), DownReranker())
    # pairs a working hybrid stage with a broken reranker
    with pytest.raises(RuntimeError, match="cohere down"):
        # expects the very same error, unchanged
        pipeline.retrieve("q", 1)
        # runs a query


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
# runs once per unusable constructor argument
def test_a_bad_pool_multiplier_is_rejected_at_construction(bad):
    with pytest.raises(ValueError, match="positive integer"):
        # expects the constructor itself to refuse it
        RetrievalPipeline(FakeHybrid([], []), FakeReranker([], []), pool_multiplier=bad)
        # builds a pipeline with an unusable multiplier


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
# runs once per unusable constructor argument
def test_a_bad_pool_floor_is_rejected_at_construction(bad):
    with pytest.raises(ValueError, match="positive integer"):
        # expects the constructor itself to refuse it
        RetrievalPipeline(FakeHybrid([], []), FakeReranker([], []), pool_floor=bad)
        # builds a pipeline with an unusable floor


def test_a_chunk_the_reranker_drops_is_absent_from_the_result():
    pool = [fused("A", 0.5), fused("B", 0.5), fused("C", 0.5)]
    # builds a three-chunk pool
    result = [reranked("B", 0.9, 2)]
    # has the reranker keep only B, as it would when asked for k=1
    pipeline, _ = build(pool=pool, result=result)
    # builds a pipeline over that pool and that reranked result
    answer = pipeline.retrieve("q", 1)
    # runs the query
    assert ids(answer.results) == ["B_0"]
    # requires only the chunk the reranker actually kept


# --- hermetic: a REAL HybridRetriever (in-memory Qdrant, toy embeddings) with a fake reranker ---

CORPUS = {
    "company/about.md": [
        "Acme was founded in 2015 by Ada Lovelace.",
        "Acme employs thirty people across two offices.",
    ],
    "products/widget.md": [
        "The widget ships in blue and red colours.",
        "Widget pricing starts at ten dollars per unit.",
    ],
}
# the same small toy corpus tests/test_retrieval.py and test_hybrid_retriever.py use


class NoOpGraph:
    def retrieve(self, query, k):
        return []
        # simulates a graph that never recognises any entity, as the toy corpus has none


class IdentityReranker:
    """A reranker stand-in that keeps the hybrid's own order -- needs no Cohere key."""

    def rerank_with_scores(self, query, chunks, k):
        return [
            RerankedChunk(chunk=c, relevance_score=1.0 - 0.01 * i, input_rank=i + 1)
            for i, c in enumerate(chunks[:k])
        ]
        # keeps the first k chunks in the order they arrived, with falling fake scores


@pytest.fixture
def real_hybrid(tmp_path):
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
    vector = QdrantVectorStore(client, Embedder(model, verify=pinned_digest), collection="chunks")
    # builds a real vector store over that in-memory collection
    return HybridRetriever(vector, NoOpGraph())
    # hands back a real HybridRetriever, wired to a graph that never finds anything


def test_a_real_hybrid_retriever_feeds_the_pipeline(real_hybrid):
    pipeline = RetrievalPipeline(real_hybrid, IdentityReranker(), pool_floor=4)
    # pairs the real hybrid stage with a reranker that just keeps its order
    answer = pipeline.retrieve("Who founded Acme?", 2)
    # asks a question the toy corpus can answer
    assert answer.results[0].chunk.chunk_id == "company/about.md_0"
    # requires the founding chunk to come first
    assert answer.results[0].vector_rank == 1
    # requires the REAL hybrid stage's own vector rank to have been carried through
    assert answer.results[0].graph_rank is None
    # requires the toy corpus's entity-less graph result to show as no graph rank
    assert all(item.hybrid_score > 0 for item in answer.results)
    # requires a real, positive fused score on every result


# --- hermetic: a REAL HybridRetriever AND a REAL Reranker (Cohere SDK, HTTP stubbed) -----------


def cohere_reply(*pairs):
    body = V2RerankResponse(
        id="test",
        results=[V2RerankResponseResultsItem(index=i, relevance_score=s) for i, s in pairs],
    )
    # builds a response using the SDK's own types, so its shape cannot drift from the real one
    return httpx.Response(
        200, content=body.model_dump_json(), headers={"content-type": "application/json"}
    )
    # wraps it as an HTTP 200 carrying JSON, exactly as the real API would


def sdk_reranker(handler):
    transport = httpx.MockTransport(handler)
    # makes an HTTP layer that answers with the handler instead of using the network
    client = build_cohere_client("test-key", httpx_client=httpx.Client(transport=transport))
    # builds the REAL Cohere client on top of it, through the same factory production uses
    return Reranker(client)
    # wraps it in a REAL Reranker


def reversing_handler(request):
    documents = json.loads(request.content)["documents"]
    # reads the exact documents the REAL Reranker sent in the REAL request body
    count = len(documents)
    # counts how many documents were sent
    order = list(reversed(range(count)))
    # decides to rank the LAST document sent as the best, the first as the worst
    pairs = [(index, 1.0 - rank / count) for rank, index in enumerate(order)]
    # builds (document position, falling score) pairs matching that reversed order
    return cohere_reply(*pairs)
    # answers as the real API would, having genuinely reversed the real request


def test_a_real_hybrid_retriever_and_a_real_reranker_compose_correctly(real_hybrid):
    pipeline = RetrievalPipeline(
        real_hybrid, sdk_reranker(reversing_handler), pool_multiplier=1, pool_floor=4
    )
    # pairs the REAL hybrid stage with a REAL Reranker whose HTTP layer is stubbed
    pool = real_hybrid.retrieve_with_scores("Who founded Acme?", 4)
    # asks the SAME real hybrid stage, separately, for the pool the pipeline will also compute
    # (pool_multiplier=1, pool_floor=4 makes the pool exactly the toy corpus's 4 chunks)
    answer = pipeline.retrieve("Who founded Acme?", 4)
    # runs the same question through the full, real, composed pipeline
    assert [item.chunk.chunk_id for item in answer.results] == [
        item.chunk.chunk_id for item in reversed(pool)
    ]
    # requires the REAL reranker's REAL (stubbed) response to have reversed the REAL hybrid order
    first, last = answer.results[0], answer.results[-1]
    # takes the first and last results for a detailed lineage check
    assert (first.chunk.chunk_id, first.hybrid_rank, first.hybrid_score) == (
        pool[-1].chunk.chunk_id,
        4,
        pool[-1].score,
    )
    # requires the top result to be the pool's LAST chunk, with its REAL hybrid rank and score
    assert first.relevance_score == pytest.approx(1.0)
    # requires the top result's relevance score to be the REAL Reranker's own top score
    assert (last.chunk.chunk_id, last.hybrid_rank, last.hybrid_score) == (
        pool[0].chunk.chunk_id,
        1,
        pool[0].score,
    )
    # requires the bottom result to be the pool's FIRST chunk, with ITS real hybrid rank and score
    assert first.vector_rank == pool[-1].vector_rank and last.vector_rank == pool[0].vector_rank
    # requires the vector ranks to have travelled through both real classes unchanged


def test_a_real_cohere_error_propagates_unchanged_through_the_real_pipeline(real_hybrid):
    def rate_limited(request):
        return httpx.Response(429, json={"message": "rate limited"})
        # simulates Cohere's OWN rate-limit response, not a made-up generic error

    pipeline = RetrievalPipeline(real_hybrid, sdk_reranker(rate_limited), pool_floor=4)
    # pairs the REAL hybrid stage with a REAL Reranker that will hit a real 429
    with pytest.raises(TooManyRequestsError):
        # expects the SDK's OWN exception type, not a generic RuntimeError from a fake
        pipeline.retrieve("Who founded Acme?", 4)
        # runs the question through the full, real, composed pipeline


# --- live: the real hybrid stage; a fake reranker stands in, so no Cohere key is needed --------


@pytest.fixture(scope="module")
def live_hybrid_only():
    reason = live_services_down() or neo4j_down()
    # checks whether Qdrant, Ollama or Neo4j is unreachable
    if reason:
        # covers the case where at least one service is down
        pytest.skip(reason)
        # skips every test in this fixture's tests, saying which service is missing
    embedder = Embedder(build_ollama_client(OLLAMA_URL), ollama_url=OLLAMA_URL)
    # builds the real query embedder
    vector = QdrantVectorStore(build_qdrant_client(QDRANT_URL), embedder, collection="chunks")
    # opens the real, full collection
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
        hybrid = HybridRetriever(vector, GraphRetriever(store))
        # builds the real hybrid retriever
        yield RetrievalPipeline(hybrid, IdentityReranker())
        # hands the tests a pipeline needing no Cohere key at all


@pytest.mark.live
@pytest.mark.parametrize(
    ("question", "acceptable"),
    [
        ("Who founded Insurellm?", {"company/about.md_0"}),
        ("Which product is for life insurance?", {"products/Lifellm.md_0"}),
    ],
)
# runs once per known-answer question
def test_live_pipeline_composes_the_real_hybrid_stage_with_no_cohere_key(
    live_hybrid_only, question, acceptable
):
    answer = live_hybrid_only.retrieve(question, 3)
    # asks the pipeline for its top 3, using the identity reranker
    assert acceptable & {item.chunk.chunk_id for item in answer.results}, answer.results
    # requires an accepted chunk among them


@pytest.mark.live
def test_live_the_pool_size_requested_from_the_real_hybrid_stage_is_correct(live_hybrid_only):
    answer = live_hybrid_only.retrieve("Who founded Insurellm?", 2)
    # asks for k=2 candidates
    assert len(answer.results) == 2
    # requires exactly k results back (the identity reranker keeps chunks[:k] of a pool of
    # max(2*3, 20) = 20, and the real collection holds far more than 20 chunks)


# --- live: the full pipeline with the real Cohere API ------------------------------------------


@pytest.fixture(scope="module")
def live_full():
    reason = live_services_down() or neo4j_down()
    # checks whether Qdrant, Ollama or Neo4j is unreachable
    if reason:
        # covers the case where at least one service is down
        pytest.skip(reason)
        # skips every test in this fixture's tests, saying which service is missing
    from src.core.config import settings

    if not settings.cohere_api_key.strip():
        # covers a checkout where no Cohere key has been added yet
        pytest.skip("COHERE_API_KEY is empty; add a Cohere key to .env to run the full pipeline")
        # skips instead of failing, saying how to fix it
    embedder = Embedder(build_ollama_client(OLLAMA_URL), ollama_url=OLLAMA_URL)
    # builds the real query embedder
    vector = QdrantVectorStore(build_qdrant_client(QDRANT_URL), embedder, collection="chunks")
    # opens the real, full collection
    with build_driver() as driver:
        # opens the real graph connection for the length of the module
        hybrid = HybridRetriever(vector, GraphRetriever(GraphStore(driver)))
        # builds the real hybrid retriever
        yield RetrievalPipeline(hybrid, Reranker.from_settings())
        # hands the tests a fully real pipeline, hybrid AND reranker


@pytest.mark.live
def test_live_full_pipeline_returns_a_well_formed_result(live_full):
    answer = live_full.retrieve("Who founded Insurellm?", 3)
    # asks the full, real pipeline for its top 3
    assert isinstance(answer, RetrievalResult)
    # requires the exact return type the task specifies
    assert len(answer.results) == 3
    # requires exactly k results
    assert [item.final_rank for item in answer.results] == [1, 2, 3]
    # requires final_rank to number the answer 1, 2, 3
    assert all(0.0 <= item.relevance_score <= 1.0 for item in answer.results)
    # requires every relevance score to be on Cohere's 0-to-1 scale


@pytest.mark.live
def test_live_full_pipeline_report_on_the_five_labelled_questions(live_full, capsys):
    questions = [
        "Who founded Insurellm?",
        "What are the company values?",
        "Which product is for life insurance?",
        "What does the Carllm product do?",
        "Who is the CEO of Insurellm?",
    ]
    # the five questions tests/test_retrieval.py labelled
    rows = []
    # will hold one printed line per question
    for question in questions:
        # goes through each question in turn
        answer = live_full.retrieve(question, 3)
        # runs the full pipeline
        rows.append(f"{question:<40} -> {[item.chunk.chunk_id for item in answer.results]}")
        # records the final top-3 chunk ids, to read, not to assert an outcome from
    with capsys.disabled():
        # lets the report show even when pytest is capturing output
        print("\nRetrievalPipeline.retrieve(question, 3), full pipeline:\n" + "\n".join(rows))
        # prints the report; no outcome is asserted, matching notes/step-5.3's honesty rule
    assert len(rows) == len(questions)
    # requires that every question was run
