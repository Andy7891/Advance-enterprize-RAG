"""Tests for POST /chat: request validation, SSE framing, and the real RAGPipeline.stream()
integration. `TestClient(app)` used without `with` never triggers `lifespan` (confirmed by
tests/test_health.py already relying on this), so these tests set `app.state.rag` directly --
a real `RAGPipeline` wired to fakes, touching no real service at all.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.ingestion.schemas import DocumentChunk
from src.orchestration.rag import RAGPipeline
from src.retrieval.pipeline import RankedChunk, RetrievalResult


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
    # a minimal, directly-constructed chunk, mirroring tests/test_rag.py's own local helper


def ranked(name: str, *, rank: int = 1) -> RankedChunk:
    return RankedChunk(
        chunk=chunk(name),
        final_rank=rank,
        relevance_score=1.0,
        hybrid_score=1.0,
        hybrid_rank=rank,
        vector_rank=rank,
        graph_rank=None,
        vector_score=1.0,
    )


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
        return RetrievalResult(query=query, results=tuple(self.results))


class FakeLLM:
    """Answers stream() from canned fragments, so no real Ollama is needed."""

    def __init__(self, *, chunks=("An answer.",), raises=None):
        self.chunks = chunks
        # the fragments a stream() call will yield, in order
        self.raises = raises
        # an exception stream() should raise partway through, or None

    def generate(self, prompt, temperature, max_tokens, *, system=None):
        return "".join(self.chunks)
        # query() isn't exercised by this endpoint, but LLM's own Protocol requires it

    def stream(self, prompt, temperature, *, system=None):
        yield from self.chunks
        # yields every canned fragment successfully first...
        if self.raises is not None:
            raise self.raises
            # ...then fails, simulating a real connection dying partway through a real stream


def build_client(*, retriever=None, llm=None, **pipeline_kwargs):
    retriever = retriever if retriever is not None else FakeRetriever(results=[ranked("a")])
    # a retriever that finds one chunk by default, unless the test overrides it
    llm = llm if llm is not None else FakeLLM()
    # an LLM that answers with one canned fragment by default, unless the test overrides it
    app.state.rag = RAGPipeline(retriever, llm, **pipeline_kwargs)
    # sets the SAME attribute lifespan would normally set, bypassing it entirely
    return TestClient(app), retriever, llm


# --- request validation -----------------------------------------------------------------------


def test_chat_accepts_a_valid_request_and_streams_sse():
    client, _, _ = build_client()
    response = client.post("/chat", json={"query": "Who founded Acme?", "k": 3})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # requires the real SSE media type, not a plain JSON or text response


def test_chat_rejects_a_missing_query():
    client, _, _ = build_client()
    response = client.post("/chat", json={"k": 3})
    assert response.status_code == 422
    # requires FastAPI's own automatic validation to catch the missing required field


def test_chat_rejects_an_empty_query():
    client, _, _ = build_client()
    response = client.post("/chat", json={"query": "", "k": 3})
    assert response.status_code == 422
    # requires ChatRequest's min_length=1 constraint to reject an empty string


def test_chat_rejects_a_zero_or_negative_k():
    client, _, _ = build_client()
    assert client.post("/chat", json={"query": "hi", "k": 0}).status_code == 422
    assert client.post("/chat", json={"query": "hi", "k": -1}).status_code == 422
    # requires ChatRequest's gt=0 constraint to reject both, before RAGPipeline is ever touched


def test_chat_rejects_a_non_integer_k():
    client, _, _ = build_client()
    response = client.post("/chat", json={"query": "hi", "k": "three"})
    assert response.status_code == 422
    # requires Pydantic's own type validation to reject a string that isn't a valid int


def test_chat_defaults_k_to_3_when_omitted():
    client, retriever, _ = build_client()
    client.post("/chat", json={"query": "Who founded Acme?"})
    assert retriever.calls[0] == ("Who founded Acme?", 3)
    # requires ChatRequest's own default (k=3) to reach the real retriever


# --- SSE framing -------------------------------------------------------------------------------


def test_chat_streams_one_sse_event_per_real_fragment_then_done():
    client, _, _ = build_client(llm=FakeLLM(chunks=["Hello", ", world", "!"]))
    response = client.post("/chat", json={"query": "hi", "k": 1})
    body = response.text
    assert "data: Hello\n\n" in body
    assert "data: , world\n\n" in body
    assert "data: !\n\n" in body
    # requires the model's own real fragments to each arrive as their own "data: ..." event
    assert body.endswith("data: [DONE]\n\n")
    # requires the explicit end-of-stream marker to be the final thing sent


def test_chat_frames_multiline_fragments_with_a_data_prefix_per_line():
    client, _, _ = build_client(llm=FakeLLM(chunks=["ok"]))
    response = client.post("/chat", json={"query": "hi", "k": 1})
    body = response.text
    # the mechanical source list is stream()'s own final fragment, "\n\nSources:\n[1] a (a.md)"
    # -- contains real newlines, which SSE requires a fresh "data: " prefix on every line of
    assert "data: \ndata: \ndata: Sources:\ndata: [1] a (a.md)\n\n" in body
    # requires EVERY line of that multi-line fragment to carry its own "data: " prefix -- a
    # single un-prefixed blank line would break real SSE parsing for this one event


def test_chat_passes_the_requested_k_through_to_real_retrieval():
    client, retriever, _ = build_client()
    client.post("/chat", json={"query": "Who founded Acme?", "k": 7})
    assert retriever.calls[0] == ("Who founded Acme?", 7)
    # requires the per-request k to reach the real retriever, not just the pipeline's own default


def test_chat_on_empty_retrieval_streams_only_the_refusal_text():
    from src.orchestration.prompts import REFUSAL_TEXT

    client, _, llm_unused = build_client(retriever=FakeRetriever(results=[]))
    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.text == f"data: {REFUSAL_TEXT}\n\ndata: [DONE]\n\n"
    # requires the real, wired-up empty-retrieval short-circuit to reach the client as one
    # real SSE event, still correctly followed by the [DONE] marker


# --- error handling mid-stream -------------------------------------------------------------


def test_chat_reports_a_failure_partway_through_as_an_error_event_not_a_crash():
    client, _, _ = build_client(llm=FakeLLM(chunks=["partial"], raises=RuntimeError("ollama down")))
    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.status_code == 200
    # requires the response to have started successfully -- headers were already sent
    assert "data: partial\n\n" in response.text
    # requires the fragment that streamed successfully before the failure to still reach the client
    assert response.text.endswith("data: [ERROR] ollama down\n\n")
    # requires the failure to reach the client as a real, readable event, not a silent reset
