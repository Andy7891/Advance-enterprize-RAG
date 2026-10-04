"""Tests for RAGPipeline: hermetic tests with fake retrieval/generation for the orchestration
logic itself, plus a live test against the real corpus, real Neo4j/Qdrant, and real Ollama
(a fake reranker stands in, so no Cohere key is needed -- mirrors tests/retrieval/test_pipeline.py's
own no-Cohere live fixture)."""

from __future__ import annotations

import pytest

from src.embedding.embedder import Embedder, build_ollama_client
from src.graph.setup import build_driver
from src.ingestion.schemas import DocumentChunk
from src.orchestration.llm import build_llm
from src.orchestration.prompts import REFUSAL_TEXT, SYSTEM_PROMPT
from src.orchestration.rag import RAGPipeline
from src.retrieval.graph_retriever import GraphRetriever
from src.retrieval.graph_store import GraphStore
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.pipeline import RankedChunk, RetrievalPipeline, RetrievalResult
from src.retrieval.reranker import RerankedChunk
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore
from tests.support import OLLAMA_URL, QDRANT_URL, live_services_down, neo4j_down, ollama_down


def chunk(name: str, tokens: int = 50) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=f"{name}_0",
        doc_id=f"doc-{name}",
        source_path=f"{name}.md",
        category="test",
        title=name,
        chunk_index=0,
        text=f"text of {name}",
        char_count=10,
        approx_token_count=tokens,
    )
    # a minimal, directly-constructed chunk with a controllable token count


def ranked(name: str, *, tokens: int = 50, rank: int = 1) -> RankedChunk:
    return RankedChunk(
        chunk=chunk(name, tokens),
        final_rank=rank,
        relevance_score=1.0,
        hybrid_score=1.0,
        hybrid_rank=rank,
        vector_rank=rank,
        graph_rank=None,
        vector_score=1.0,
    )
    # a minimal, directly-constructed RankedChunk for controlled orchestration tests


class FakeRetriever:
    """Answers retrieve() with a canned RetrievalResult, so no real service is needed."""

    def __init__(self, *, results=(), raises=None):
        self.results = results
        # the RankedChunks retrieve() will hand back, best first
        self.raises = raises
        # an exception retrieve() should raise instead of answering, or None
        self.calls: list[tuple[str, int]] = []
        # records every (query, k) this fake received, in order

    def retrieve(self, query, k):
        self.calls.append((query, k))
        # keeps the record before doing anything else, even if this call is about to raise
        if self.raises is not None:
            raise self.raises
            # simulates the underlying retrieval service failing
        return RetrievalResult(query=query, results=tuple(self.results))
        # answers with a real RetrievalResult, as RetrievalPipeline would


class FakeLLM:
    """Answers generate()/stream() from canned values, so no real Ollama is needed."""

    def __init__(self, *, reply="An answer.", chunks=None, raises=None):
        self.reply = reply
        # the text a generate() call will return
        self.chunks = chunks if chunks is not None else [reply]
        # the fragments a stream() call will yield, in order
        self.raises = raises
        # an exception generate()/stream() should raise instead of answering, or None
        self.generate_calls: list[dict] = []
        # records every generate() call this fake received, in order
        self.stream_calls: list[dict] = []
        # records every stream() call this fake received, in order

    def generate(self, prompt, temperature, max_tokens, *, system=None):
        self.generate_calls.append(
            {
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system": system,
            }
        )
        # keeps the record before doing anything else, even if this call is about to raise
        if self.raises is not None:
            raise self.raises
            # simulates the underlying model failing
        return self.reply
        # answers with the canned reply

    def stream(self, prompt, temperature, *, system=None):
        self.stream_calls.append({"prompt": prompt, "temperature": temperature, "system": system})
        # keeps the record before doing anything else, even if this call is about to raise
        if self.raises is not None:
            raise self.raises
            # simulates the underlying model failing
        yield from self.chunks
        # yields every canned fragment, in order


def build(*, retriever=None, llm=None, **pipeline_kwargs):
    retriever = retriever if retriever is not None else FakeRetriever(results=[ranked("a")])
    # a retriever that finds one chunk by default, unless the test overrides it
    llm = llm if llm is not None else FakeLLM()
    # an LLM that answers with one canned reply by default, unless the test overrides it
    pipeline = RAGPipeline(retriever, llm, **pipeline_kwargs)
    # wires a RAGPipeline to both fakes
    return pipeline, retriever, llm
    # hands back all three, so a test can inspect what each fake actually received


# --- query() ---------------------------------------------------------------------------------


def test_query_returns_the_models_answer_with_sources_appended():
    pipeline, _, _ = build(llm=FakeLLM(reply="Paris is the capital [1]."))
    # a fake LLM that answers with a real-looking, already-cited sentence
    result = pipeline.query("What is the capital of France?")
    assert result == "Paris is the capital [1].\n\nSources:\n[1] a (a.md)"
    # requires the model's own text, then a blank line, then the mechanical source list


def test_query_sends_the_system_prompt_and_configured_temperature_and_max_tokens():
    pipeline, _, llm = build(temperature=0.3, max_tokens=222)
    # a pipeline built with specific, known parameters
    pipeline.query("Any question?")
    # makes one call
    call = llm.generate_calls[0]
    assert call["system"] == SYSTEM_PROMPT
    # requires the real system prompt to have been sent, unchanged
    assert (call["temperature"], call["max_tokens"]) == (0.3, 222)
    # requires the configured temperature and max_tokens to have been sent, unchanged


def test_query_sends_the_numbered_sources_and_the_question_in_the_prompt():
    pipeline, _, llm = build(retriever=FakeRetriever(results=[ranked("handbook")]))
    # a retriever that finds one specific, named chunk
    pipeline.query("How many vacation days?")
    # makes one call
    prompt = llm.generate_calls[0]["prompt"]
    assert "[1] handbook (handbook.md):" in prompt
    # requires the numbered source entry to be in the prompt sent to the model
    assert "Question: How many vacation days?" in prompt
    # requires the real question to be in the prompt sent to the model
    assert prompt.rstrip().endswith("Answer:")
    # requires "Answer:" to be the final token, as docs/prompts.md section 3 specifies


def test_query_retrieves_the_configured_k():
    pipeline, retriever, _ = build(k=7)
    # a pipeline built with a specific k
    pipeline.query("Any question?")
    # makes one call
    assert retriever.calls[0] == ("Any question?", 7)
    # requires retrieve() to have been called with the exact question and configured k


def test_query_accepts_a_per_call_k_overriding_the_configured_one():
    pipeline, retriever, _ = build(k=7)
    # a pipeline configured with k=7
    pipeline.query("Any question?", k=2)
    # makes one call, overriding k for just this call
    assert retriever.calls[0] == ("Any question?", 2)
    # requires the per-call k, not the configured one, to have reached the real retriever


def test_query_falls_back_to_the_configured_k_when_none_is_given():
    pipeline, retriever, _ = build(k=7)
    pipeline.query("Any question?")
    # makes one call, no per-call k at all
    assert retriever.calls[0] == ("Any question?", 7)
    # requires the configured k to still apply -- matches "is None", not "or", semantics


def test_query_rejects_a_bad_per_call_k():
    pipeline, retriever, llm = build()
    # a pipeline that would otherwise happily answer
    with pytest.raises(ValueError, match="k"):
        # expects a clear error naming k
        pipeline.query("Any question?", k=0)
        # calls with an unusable per-call k
    assert retriever.calls == []
    # requires that retrieval never ran
    assert llm.generate_calls == []
    # requires that the model was never called


def test_stream_accepts_a_per_call_k_overriding_the_configured_one():
    pipeline, retriever, _ = build(k=7)
    list(pipeline.stream("Any question?", k=2))
    # runs the generator to completion, overriding k for just this call
    assert retriever.calls[0] == ("Any question?", 2)
    # requires the per-call k, not the configured one, to have reached the real retriever


def test_query_on_empty_retrieval_returns_the_refusal_text_without_calling_the_model():
    pipeline, _, llm = build(retriever=FakeRetriever(results=[]))
    # a retriever that finds nothing at all
    assert pipeline.query("An unanswerable question?") == REFUSAL_TEXT
    # requires the exact refusal string, unchanged
    assert llm.generate_calls == []
    # requires that the model was never called for a known-empty context


def test_query_applies_the_context_budget_before_building_the_prompt():
    huge = ranked("huge", tokens=3000, rank=1)
    # a chunk that alone eats most of the real, dynamically-computed context budget
    small = ranked("small", tokens=50, rank=2)
    # a chunk that fits comfortably alongside "huge" in what's left
    too_big = ranked("too_big", tokens=600, rank=3)
    # a chunk that would push the total over what's left, so it should be dropped
    pipeline, _, llm = build(retriever=FakeRetriever(results=[huge, small, too_big]))
    pipeline.query("Any question?")
    # makes one call
    prompt = llm.generate_calls[0]["prompt"]
    assert "[1] huge" in prompt and "[2] small" in prompt
    # requires both chunks that fit the budget to be in the prompt
    assert "too_big" not in prompt
    # requires the chunk that doesn't fit the budget to have been dropped


def test_query_rejects_an_empty_question_before_any_retrieval():
    pipeline, retriever, llm = build()
    # a pipeline that would otherwise happily answer
    with pytest.raises(ValueError, match="question"):
        # expects a clear error naming question
        pipeline.query("   ")
        # calls with an unusable question
    assert retriever.calls == []
    # requires that retrieval never ran
    assert llm.generate_calls == []
    # requires that the model was never called


def test_an_error_from_the_retriever_propagates_unchanged_from_query():
    pipeline, _, _ = build(retriever=FakeRetriever(raises=RuntimeError("qdrant down")))
    # a retriever that fails on every call
    with pytest.raises(RuntimeError, match="qdrant down"):
        # expects the very same error, unchanged
        pipeline.query("Any question?")
        # calls the failing retriever


def test_an_error_from_the_llm_propagates_unchanged_from_query():
    pipeline, _, _ = build(llm=FakeLLM(raises=RuntimeError("ollama down")))
    # an LLM that fails on every call
    with pytest.raises(RuntimeError, match="ollama down"):
        # expects the very same error, unchanged
        pipeline.query("Any question?")
        # calls the failing LLM


# --- stream() --------------------------------------------------------------------------------


def test_stream_yields_the_models_fragments_then_the_sources_last():
    pipeline, _, _ = build(llm=FakeLLM(chunks=["Paris", " is", " the capital", " [1]."]))
    # a fake LLM that streams a short, already-cited sentence, fragment by fragment
    fragments = list(pipeline.stream("What is the capital of France?"))
    assert fragments == ["Paris", " is", " the capital", " [1].", "\n\nSources:\n[1] a (a.md)"]
    # requires every real fragment in order, then one final chunk: the mechanical source list


def test_stream_on_empty_retrieval_yields_only_the_refusal_text():
    pipeline, _, llm = build(retriever=FakeRetriever(results=[]))
    # a retriever that finds nothing at all
    assert list(pipeline.stream("An unanswerable question?")) == [REFUSAL_TEXT]
    # requires exactly one yielded value: the exact refusal string
    assert llm.stream_calls == []
    # requires that the model was never called for a known-empty context


def test_stream_sends_the_system_prompt_and_configured_temperature():
    pipeline, _, llm = build(temperature=0.4)
    # a pipeline built with a specific temperature
    list(pipeline.stream("Any question?"))
    # runs the generator to completion
    call = llm.stream_calls[0]
    assert (call["system"], call["temperature"]) == (SYSTEM_PROMPT, 0.4)
    # requires the real system prompt and configured temperature to have been sent, unchanged


def test_stream_rejects_an_empty_question_before_any_retrieval():
    pipeline, retriever, llm = build()
    # a pipeline that would otherwise happily answer
    with pytest.raises(ValueError, match="question"):
        # expects a clear error naming question
        next(pipeline.stream(""))
        # a generator's body does not run until the first next(), so this forces it to
    assert retriever.calls == []
    # requires that retrieval never ran
    assert llm.stream_calls == []
    # requires that the model was never called


def test_an_error_from_the_retriever_propagates_unchanged_from_stream():
    pipeline, _, _ = build(retriever=FakeRetriever(raises=RuntimeError("qdrant down")))
    # a retriever that fails on every call
    with pytest.raises(RuntimeError, match="qdrant down"):
        # expects the very same error, unchanged
        next(pipeline.stream("Any question?"))
        # forces the generator's body to actually run


def test_an_error_from_the_llm_propagates_unchanged_from_stream():
    pipeline, _, _ = build(llm=FakeLLM(raises=RuntimeError("ollama down")))
    # an LLM that fails on every call
    with pytest.raises(RuntimeError, match="ollama down"):
        # expects the very same error, unchanged
        next(pipeline.stream("Any question?"))
        # forces the generator's body to actually run


# --- construction ------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "3"])
# runs once per unusable k
def test_construction_rejects_a_bad_k(bad):
    with pytest.raises(ValueError, match="k"):
        # expects a clear error naming k
        build(k=bad)
        # builds with an unusable k


@pytest.mark.parametrize("bad", [-1, -0.1, float("nan"), float("inf"), True, "0.5"])
# runs once per unusable temperature
def test_construction_rejects_a_bad_temperature(bad):
    with pytest.raises(ValueError, match="temperature"):
        # expects a clear error naming temperature
        build(temperature=bad)
        # builds with an unusable temperature


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "400"])
# runs once per unusable max_tokens
def test_construction_rejects_a_bad_max_tokens(bad):
    with pytest.raises(ValueError, match="max_tokens"):
        # expects a clear error naming max_tokens
        build(max_tokens=bad)
        # builds with an unusable max_tokens


def test_construction_defaults_match_the_published_recommendations():
    pipeline, retriever, llm = build()
    # a pipeline built with no overrides at all
    pipeline.query("Any question?")
    # makes one call
    assert retriever.calls[0][1] == 3
    # requires the default k to match docs/prompts.md's own worked examples
    call = llm.generate_calls[0]
    assert call["temperature"] == 0.1 and call["max_tokens"] == 400
    # requires the defaults to sit inside section 9's recommended 0.0-0.2/300-500 ranges


# --- live: the real corpus, real Neo4j/Qdrant, and real Ollama; a fake reranker stands in ------


class IdentityReranker:
    """A reranker stand-in that keeps the hybrid's own order -- needs no Cohere key."""

    def rerank_with_scores(self, query, chunks, k):
        return [
            RerankedChunk(chunk=c, relevance_score=1.0 - 0.01 * i, input_rank=i + 1)
            for i, c in enumerate(chunks[:k])
        ]
        # keeps the first k chunks in the order they arrived, with falling fake scores


@pytest.fixture(scope="module")
def live_rag():
    reason = live_services_down() or neo4j_down() or ollama_down()
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
        retrieval = RetrievalPipeline(hybrid, IdentityReranker())
        # a real retrieval pipeline needing no Cohere key at all
        llm = build_llm(OLLAMA_URL)
        # a real LocalLLM over the real, already-pulled model
        yield RAGPipeline(retrieval, llm)
        # hands the tests a fully real RAGPipeline


@pytest.mark.live
def test_live_query_answers_a_real_labelled_question(live_rag):
    answer = live_rag.query("Who founded Insurellm?")
    # asks a real question this corpus's retrieval is known to answer well
    assert "avery lancaster" in answer.lower()
    # requires the real, correct fact to appear somewhere in the grounded answer
    assert "Sources:" in answer
    # requires the mechanical citation fallback to have been appended, as designed


@pytest.mark.live
def test_live_stream_reassembles_into_the_same_shape_as_query(live_rag):
    fragments = list(live_rag.stream("Who founded Insurellm?"))
    # asks the same real question, via the streaming path this time
    assert len(fragments) > 1
    # requires the answer to have arrived as more than one streamed piece
    full_text = "".join(fragments)
    assert "avery lancaster" in full_text.lower()
    # requires the real, correct fact to appear in the reassembled answer
    assert full_text.rstrip().endswith(")")
    # requires the mechanical source list (ending in "(path.md)") to be the final content
    assert fragments[-1].startswith("\n\nSources:")
    # requires the mechanical source list to have arrived as its own final fragment
