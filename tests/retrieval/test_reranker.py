"""Tests for the Reranker.

Three layers, cheapest first:
* hermetic with a fake client: ordering, top_n, edge cases, argument checks, errors;
* hermetic against the REAL Cohere SDK over a stubbed HTTP transport: proves the request the
  SDK sends and the response it parses match what Reranker expects, and that a 429 is not
  silently retried (the SDK retries twice by default);
* live (`-m live`): the real Cohere API on the five labelled questions. Needs COHERE_API_KEY
  in .env plus Qdrant, Ollama and Neo4j, and skips itself otherwise.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import httpx
import pytest
from cohere.errors import InternalServerError, TooManyRequestsError, UnauthorizedError
from cohere.v2.types.v2rerank_response import V2RerankResponse
from cohere.v2.types.v2rerank_response_results_item import V2RerankResponseResultsItem

from src.core.config import Settings, settings
from src.embedding.embedder import Embedder, build_ollama_client
from src.graph.setup import build_driver
from src.ingestion.schemas import DocumentChunk
from src.retrieval.graph_retriever import GraphRetriever
from src.retrieval.graph_store import GraphStore
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranker import Reranker, build_cohere_client
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore
from tests.support import (
    OLLAMA_URL,
    QDRANT_URL,
    live_services_down,
    make_chunk,
    neo4j_down,
)


def chunk(name, text=None):
    return make_chunk(name, 0, f"text of {name}" if text is None else text)
    # builds a tiny chunk whose chunk_id is "<name>_0" and whose text is "text of <name>"


def chunks_named(*names):
    return [chunk(name) for name in names]
    # builds one tiny chunk per name, in the order given


def ids(items):
    return [getattr(item, "chunk", item).chunk_id for item in items]
    # lists the chunk ids of chunks or of reranked results, in order


def scores_for(**by_name):
    return {f"text of {name}": score for name, score in by_name.items()}
    # turns {name: score} into the {document text: score} table FakeClient looks scores up in


class FakeClient:
    """Scores each document from a canned table and answers like the real API does."""

    def __init__(self, scores=None, order=None):
        self.scores = scores or {}
        # keeps the table of document text -> relevance score
        self.order = order
        # keeps an optional fixed list of document positions to answer with, best first
        self.calls = []
        # keeps every request made, so tests can inspect what was sent

    def rerank(self, *, model, query, documents, top_n):
        self.calls.append(
            {"model": model, "query": query, "documents": list(documents), "top_n": top_n}
        )

        # records the request exactly as it arrived
        def result(position):
            known = 0 <= position < len(documents)
            # checks whether this position points at a document that was really sent
            score = self.scores.get(documents[position], 0.0) if known else 0.0
            # looks the document's score up in the table, defaulting to zero
            return SimpleNamespace(index=position, relevance_score=score)
            # builds one result naming that position, even when it is impossible

        if self.order is not None:
            # covers tests that need a specific answer, ignoring top_n
            return SimpleNamespace(results=[result(position) for position in self.order])
            # answers with those positions in that exact order
        scored = sorted(
            (result(position) for position in range(len(documents))),
            key=lambda item: -item.relevance_score,
        )
        # scores every document and puts the highest score first, as the real API does
        return SimpleNamespace(results=scored[:top_n])
        # answers with at most top_n results


class DownClient:
    def __init__(self, error):
        self.error = error
        # keeps the exception this fake will raise

    def rerank(self, *, model, query, documents, top_n):
        raise self.error
        # simulates the service failing


# --- hermetic: Reranker with a fake client -----------------------------------------------------


def test_the_scores_the_client_returns_decide_the_order():
    client = FakeClient(scores_for(A=0.2, B=0.9, C=0.1, D=0.7))
    # scores B best, then D, then A, then C
    reranked = Reranker(client).rerank("q", chunks_named("A", "B", "C", "D"), 4)
    # reranks four chunks that arrive in the order A, B, C, D
    assert ids(reranked) == ["B_0", "D_0", "A_0", "C_0"]
    # requires the order to follow the scores, not the arrival order


def test_only_k_results_come_back_and_the_client_is_asked_for_exactly_k():
    client = FakeClient(scores_for(A=0.5, B=0.4, C=0.3, D=0.2, E=0.1))
    # scores five chunks in falling order
    reranked = Reranker(client).rerank("q", chunks_named(*"ABCDE"), 2)
    # asks for the best two of five
    assert ids(reranked) == ["A_0", "B_0"]
    # requires just the two best
    assert client.calls[0]["top_n"] == 2
    # requires the request to ask for two, so the service is not paid to score more back


def test_a_k_larger_than_the_list_returns_everything_reordered():
    client = FakeClient(scores_for(A=0.1, B=0.9, C=0.5))
    # scores three chunks
    reranked = Reranker(client).rerank("q", chunks_named("A", "B", "C"), 10)
    # asks for ten of the three available
    assert ids(reranked) == ["B_0", "C_0", "A_0"]
    # requires all three, best first
    assert client.calls[0]["top_n"] == 3
    # requires the request to ask for three, not ten


def test_an_empty_candidate_list_returns_nothing_and_sends_no_request():
    client = FakeClient()
    # builds a client that records requests
    assert Reranker(client).rerank("q", [], 3) == []
    # requires an empty list back
    assert client.calls == []
    # requires that no request was made, since there was nothing to score


def test_the_query_the_model_and_only_the_chunk_texts_are_sent_in_input_order():
    client = FakeClient()
    # builds a client that records requests
    candidates = [
        chunk("products/Carllm.md", "It handles claims."),
        chunk("company/about.md", "Founded in 2015."),
    ]
    # builds two chunks whose titles (their source paths) are not part of their text
    Reranker(client).rerank("What does Carllm do?", candidates, 2)
    # reranks them
    request = client.calls[0]
    # takes the one request that was sent
    assert request["query"] == "What does Carllm do?"
    # requires the question to be sent unchanged
    assert request["model"] == "rerank-v3.5"
    # requires the default model to be sent
    assert request["documents"] == ["It handles claims.", "Founded in 2015."]
    # requires the documents to be the chunk texts, in the order they were given
    assert not any("Carllm" in document for document in request["documents"])
    # requires the chunk title (which contains Carllm) not to be sent along with the text


def test_the_model_name_is_configurable():
    client = FakeClient()
    # builds a client that records requests
    Reranker(client, model="rerank-v4.0-fast").rerank("q", chunks_named("A"), 1)
    # reranks with a different model name
    assert client.calls[0]["model"] == "rerank-v4.0-fast"
    # requires that name to be the one sent


def test_rerank_returns_the_same_chunks_as_rerank_with_scores():
    client = FakeClient(scores_for(A=0.3, B=0.8, C=0.5))
    # scores three chunks
    reranker = Reranker(client)
    # builds one reranker for both calls
    pool = chunks_named("A", "B", "C")
    # builds the candidates
    plain = reranker.rerank("q", pool, 2)
    # asks for the plain result
    detailed = reranker.rerank_with_scores("q", pool, 2)
    # asks for the detailed result
    assert plain == [item.chunk for item in detailed]
    # requires the same chunks in the same order
    assert all(isinstance(item, DocumentChunk) for item in plain)
    # requires real DocumentChunk objects, as the task specifies


def test_scores_and_input_positions_are_recorded():
    client = FakeClient(scores_for(A=0.1, B=0.9, C=0.5))
    # scores three chunks
    detailed = Reranker(client).rerank_with_scores("q", chunks_named("A", "B", "C"), 3)
    # reranks them
    assert [(item.chunk.chunk_id, item.relevance_score, item.input_rank) for item in detailed] == [
        ("B_0", 0.9, 2),
        ("C_0", 0.5, 3),
        ("A_0", 0.1, 1),
    ]
    # requires each result to carry its own score and its 1-based position before reranking


def test_equal_scores_keep_the_original_input_order():
    client = FakeClient(order=[2, 0, 1])
    # makes a client that answers in a scrambled order with every score equal
    reranked = Reranker(client).rerank("q", chunks_named("A", "B", "C"), 3)
    # reranks three chunks
    assert ids(reranked) == ["A_0", "B_0", "C_0"]
    # requires ties to fall back on the order the chunks arrived in, whatever order the API used


def test_a_chunk_repeated_in_the_input_is_scored_once_at_its_first_position():
    client = FakeClient(scores_for(C=0.9))
    # scores only C, highly
    pool = chunks_named("A", "B", "A", "C")
    # builds a list where A appears twice
    detailed = Reranker(client).rerank_with_scores("q", pool, 4)
    # reranks it, asking for more than the distinct chunks available
    assert client.calls[0]["documents"] == ["text of A", "text of B", "text of C"]
    # requires each distinct chunk to be sent once
    assert [(item.chunk.chunk_id, item.input_rank) for item in detailed][0] == ("C_0", 4)
    # requires C to report its position in the list as given, counting the repeat
    assert ids(detailed).count("A_0") == 1
    # requires A to appear once in the result


def test_a_client_that_returns_more_than_asked_is_cut_to_k():
    client = FakeClient(scores_for(A=0.1, B=0.9, C=0.5), order=[0, 1, 2])
    # makes a client that ignores top_n and returns all three
    reranked = Reranker(client).rerank("q", chunks_named("A", "B", "C"), 1)
    # asks for one
    assert ids(reranked) == ["B_0"]
    # requires only the single best one


@pytest.mark.parametrize("error", [RuntimeError("cohere down"), ConnectionError("no network")])
# runs once per kind of failure
def test_an_error_from_the_client_propagates(error):
    with pytest.raises(type(error), match=str(error)):
        # expects the very same kind of error, unchanged
        Reranker(DownClient(error)).rerank("q", chunks_named("A", "B"), 1)
        # reranks with a client that fails


@pytest.mark.parametrize("bad", [0, -1, 1.5, "3", True, None])
# runs once per unusable k
def test_a_bad_k_is_rejected_before_any_request(bad):
    client = FakeClient()
    # builds a client that records requests
    with pytest.raises(ValueError, match="positive integer"):
        # expects a clear error
        Reranker(client).rerank("q", chunks_named("A"), bad)
        # reranks with the unusable k
    assert client.calls == []
    # requires that nothing was sent


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
# runs once per unusable query
def test_a_blank_query_is_rejected_before_any_request(bad):
    client = FakeClient()
    # builds a client that records requests
    with pytest.raises(ValueError, match="non-empty string"):
        # expects a clear error
        Reranker(client).rerank(bad, chunks_named("A"), 1)
        # reranks with the unusable query
    assert client.calls == []
    # requires that nothing was sent


def test_k_is_checked_even_when_there_are_no_candidates():
    with pytest.raises(ValueError, match="positive integer"):
        # expects the k check to run before the empty-list shortcut
        Reranker(FakeClient()).rerank("q", [], 0)
        # reranks nothing with an unusable k


@pytest.mark.parametrize("bad_index", [5, -1])
# runs once per impossible position
def test_an_impossible_document_index_from_the_client_is_refused(bad_index):
    client = FakeClient(order=[bad_index])
    # makes a client that answers with a position outside the two documents sent
    with pytest.raises(ValueError, match="unknown document index"):
        # expects a clear error, not a silently wrong chunk
        Reranker(client).rerank("q", chunks_named("A", "B"), 1)
        # reranks two chunks


def test_an_empty_model_name_is_rejected():
    with pytest.raises(ValueError, match="model"):
        # expects the constructor to refuse it
        Reranker(FakeClient(), model="  ")
        # builds a reranker with a blank model name


# --- hermetic: settings and the client factory (no network) -------------------------------------


def test_from_settings_refuses_an_empty_key_and_names_the_fix():
    with pytest.raises(ValueError, match="COHERE_API_KEY"):
        # expects the message to name the setting to fill in
        Reranker.from_settings(Settings(cohere_api_key=""))
        # builds a reranker from settings whose key is empty


def test_from_settings_builds_a_reranker_from_a_key_without_touching_the_network():
    reranker = Reranker.from_settings(Settings(cohere_api_key="test-key"))
    # builds a reranker from settings that carry a key
    assert isinstance(reranker, Reranker)
    # requires a real Reranker, built with no request sent


@pytest.mark.parametrize("blank", ["", "   "])
# runs once per blank key
def test_build_cohere_client_rejects_a_blank_key(blank):
    with pytest.raises(ValueError, match="COHERE_API_KEY"):
        # expects the same helpful message
        build_cohere_client(blank)
        # builds a client with a blank key


# --- hermetic: the REAL Cohere SDK over a stubbed HTTP transport --------------------------------


def cohere_reply(*pairs):
    body = V2RerankResponse(
        id="test",
        results=[V2RerankResponseResultsItem(index=i, relevance_score=s) for i, s in pairs],
    )
    # builds a response using the SDK's own types, so its shape cannot drift from the real one
    return httpx.Response(
        200, content=body.model_dump_json(), headers={"content-type": "application/json"}
    )
    # wraps it as an HTTP 200 carrying JSON


def sdk_reranker(handler):
    transport = httpx.MockTransport(handler)
    # makes an HTTP layer that answers with the handler instead of using the network
    client = build_cohere_client("test-key", httpx_client=httpx.Client(transport=transport))
    # builds the real Cohere client on top of it, through the same factory production uses
    return Reranker(client)
    # wraps it in a Reranker


def test_the_real_sdk_sends_the_expected_request_and_its_response_is_parsed():
    seen = []
    # keeps every request the SDK sends

    def handler(request):
        seen.append(request)
        # records the request
        return cohere_reply((1, 0.91), (0, 0.42))
        # answers that document 1 is best, then document 0

    detailed = sdk_reranker(handler).rerank_with_scores(
        "What does Carllm do?", chunks_named("A", "B", "C"), 2
    )
    # reranks three chunks, asking for two
    request = seen[0]
    # takes the single request that was sent
    body = json.loads(request.content)
    # reads the JSON body the SDK sent
    assert request.url.path.endswith("/v2/rerank")
    # requires the v2 rerank endpoint
    assert request.headers["authorization"] == "Bearer test-key"
    # requires the key to be sent as a bearer token
    assert (body["model"], body["query"], body["top_n"]) == (
        "rerank-v3.5",
        "What does Carllm do?",
        2,
    )
    # requires the model, the question and top_n to be in the body
    assert body["documents"] == ["text of A", "text of B", "text of C"]
    # requires the documents to be the chunk texts, in order
    assert [(item.chunk.chunk_id, item.relevance_score, item.input_rank) for item in detailed] == [
        ("B_0", 0.91, 2),
        ("A_0", 0.42, 1),
    ]
    # requires the SDK's parsed response to become correctly ranked, correctly scored chunks


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, TooManyRequestsError),
        (500, InternalServerError),
        (401, UnauthorizedError),
    ],
)
# runs once per failure status
def test_the_real_sdk_raises_after_exactly_one_request_with_no_hidden_retry(status, error):
    count = []
    # counts every request the SDK sends

    def handler(request):
        count.append(request)
        # counts this request
        return httpx.Response(status, json={"message": "nope"})
        # answers with the failure status

    with pytest.raises(error):
        # expects the SDK's own error for that status
        sdk_reranker(handler).rerank("q", chunks_named("A"), 1)
        # reranks one chunk
    assert len(count) == 1
    # requires a single request: the SDK's default of two silent retries is switched off


# --- live: the real hybrid retriever's candidates, then the real Cohere API ---------------------

POOL_SIZE = 20
# how many chunks the hybrid hands to the reranker
FINAL_K = 3
# how many chunks the reranker keeps, and the size of the hybrid list it is compared against
PACE_SECONDS = 6.5
# a pause between Cohere calls, keeping under the trial key's 10 requests per minute
CARLLM_QUESTION = "What does the Carllm product do?"
# the question whose acceptable answer is any Carllm product-page chunk
QUESTIONS = [
    "Who founded Insurellm?",
    "What are the company values?",
    "Which product is for life insurance?",
    CARLLM_QUESTION,
    "Who is the CEO of Insurellm?",
]
# the five questions tests/test_retrieval.py labelled
ACCEPTABLE = {
    "Who founded Insurellm?": {"company/about.md_0"},
    "What are the company values?": {"company/careers.md_1", "company/culture.md_1"},
    "Which product is for life insurance?": {"products/Lifellm.md_0"},
    "Who is the CEO of Insurellm?": {"employees/Avery Lancaster.md_0", "company/about.md_0"},
}
# the chunk ids each of the other four questions accepts (for the CEO question: the About page
# that names the founder, and the CEO's own record that states the title)


def first_rank(question, chunk_ids):
    for rank, chunk_id in enumerate(chunk_ids, start=1):
        # walks the list from best to worst, numbering positions from 1
        if question == CARLLM_QUESTION:
            # covers the one question accepted by a prefix, not a fixed set
            accepted = chunk_id.startswith("products/Carllm.md")
            # accepts any chunk of the Carllm product page
        else:
            accepted = chunk_id in ACCEPTABLE[question]
            # accepts any of the labelled chunk ids
        if accepted:
            # spots the first accepted chunk
            return rank
            # reports where it sits
    return None
    # reports that no accepted chunk is in the list


@pytest.fixture(scope="module")
def live_pools():
    reason = live_services_down() or neo4j_down()
    # checks whether Qdrant, Ollama or Neo4j is unreachable
    if reason:
        # covers the case where at least one service is down
        pytest.skip(reason)
        # skips every live test, saying which service is missing
    embedder = Embedder(build_ollama_client(OLLAMA_URL), ollama_url=OLLAMA_URL)
    # builds the real query embedder
    vector = QdrantVectorStore(build_qdrant_client(QDRANT_URL), embedder, collection="chunks")
    # opens the real, full collection
    pools = {}
    # will hold, per question, the hybrid's candidate list and its own top 3
    with build_driver() as driver:
        # opens the real graph connection for as long as the questions are being asked
        hybrid = HybridRetriever(vector, GraphRetriever(GraphStore(driver)))
        # builds the real hybrid retriever with its default settings
        for question in QUESTIONS:
            # goes through the five labelled questions in turn
            pools[question] = SimpleNamespace(
                pool=hybrid.retrieve(question, POOL_SIZE),
                today=hybrid.retrieve(question, FINAL_K),
            )
            # keeps the 20 candidates the reranker will get, and what the hybrid returns
            # today for k=3, which is the result the reranker is meant to improve on
    return pools
    # hands the candidate lists to the tests; no Cohere call has been made yet


@pytest.fixture(scope="module")
def reranked(live_pools):
    if not settings.cohere_api_key.strip():
        # covers a checkout where no Cohere key has been added yet
        pytest.skip(
            "COHERE_API_KEY is empty; add a Cohere key to .env to run the live rerank tests"
        )
        # skips instead of failing, saying how to fix it
    reranker = Reranker.from_settings()
    # builds the real reranker from the key in .env
    outcomes = {}
    # will hold the reranked top 3 for each question
    for position, question in enumerate(QUESTIONS):
        # goes through the five labelled questions in turn
        if position:
            # covers every question after the first
            time.sleep(PACE_SECONDS)
            # waits so the calls stay under the trial rate limit
        outcomes[question] = reranker.rerank_with_scores(
            question, live_pools[question].pool, FINAL_K
        )
        # asks Cohere to pick the best 3 of the 20 candidates
    return outcomes
    # hands the reranked results to the tests


@pytest.mark.live
def test_live_every_labelled_answer_is_inside_the_pool_the_reranker_receives(live_pools):
    for question, held in live_pools.items():
        # checks each of the five questions
        assert len(held.pool) == POOL_SIZE, question
        # requires a full pool of candidates
        assert first_rank(question, ids(held.pool)) is not None, question
        # requires an accepted chunk somewhere in it, because a reranker can only reorder what
        # it is given, so an answer outside the pool would be out of its reach


@pytest.mark.live
def test_live_each_question_gets_exactly_three_chunks_taken_from_the_pool(live_pools, reranked):
    for question, after in reranked.items():
        # checks each of the five questions
        assert len(after) == FINAL_K, question
        # requires exactly three results
        assert set(ids(after)) <= set(ids(live_pools[question].pool)), question
        # requires every result to be one of the offered chunks


@pytest.mark.live
def test_live_scores_are_between_zero_and_one_and_sorted_best_first(reranked):
    for question, after in reranked.items():
        # checks each of the five questions
        scores = [item.relevance_score for item in after]
        # lists the three relevance scores in result order
        assert all(0.0 <= score <= 1.0 for score in scores), question
        # requires each score to be on the 0-to-1 scale
        assert scores == sorted(scores, reverse=True), question
        # requires best first


@pytest.mark.live
def test_live_input_rank_is_the_chunks_position_in_the_pool(live_pools, reranked):
    for question, after in reranked.items():
        # checks each of the five questions
        pool_ids = ids(live_pools[question].pool)
        # lists the offered chunk ids in the order they were offered
        for item in after:
            # checks each reranked result
            assert pool_ids[item.input_rank - 1] == item.chunk.chunk_id, question
            # requires input_rank to point at that chunk's place in the offered list


@pytest.mark.live
def test_live_report_hit_at_3_for_the_hybrid_today_and_after_reranking(
    live_pools, reranked, capsys
):
    rows = []
    # will hold one printed line per question
    for question in QUESTIONS:
        # goes through the five questions
        today = first_rank(question, ids(live_pools[question].today))
        # asks where an accepted chunk sits in the hybrid's own top 3 today
        after = first_rank(question, ids(reranked[question]))
        # asks where it sits in the reranked top 3
        in_pool = first_rank(question, ids(live_pools[question].pool))
        # asks where it sat in the 20 candidates the reranker chose from
        rows.append(
            f"{question:<40} pool rank: {in_pool!s:<4} hybrid k=3: {today!s:<5} reranked: {after}"
        )
        # writes the comparison as one line ("None" means no accepted chunk in the top 3)
    with capsys.disabled():
        # lets the report show even when pytest is capturing output
        print(
            "\nfirst accepted chunk's rank (pool of %d -> top %d)\n%s"
            % (POOL_SIZE, FINAL_K, "\n".join(rows))
        )
        # prints the comparison, a measurement to read, not a claim to assert
    assert len(rows) == len(QUESTIONS)
    # requires that every question was measured
