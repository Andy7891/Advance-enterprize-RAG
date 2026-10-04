"""Tests for the app-wide error handlers (P7-M3): the 400/422 split, the structured
{"error": {"code", "message"}} envelope every error response shares, the 500/504
catch-alls, and a regression proving /chat's own mid-stream failures are still handled
by _sse_events's own try/except -- untouched by these new, app-wide handlers.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from src.api.main import app
from src.ingestion.schemas import DocumentChunk
from src.orchestration.rag import RAGPipeline
from src.retrieval.pipeline import RankedChunk, RetrievalResult

# Three routes that exist only so the global 500/504 handlers have something real to
# catch -- neither /health nor /chat has a synchronous path of its own that can fail
# this way (confirmed while designing this step: /health only reads static settings,
# and /chat's own real work all happens inside _sse_events, after headers are sent), so
# a small test-only route is the honest way to exercise these handlers at all.


@app.get("/_test/raises-unexpected")
def _raises_unexpected():
    raise RuntimeError("something nobody anticipated")


@app.get("/_test/raises-timeout")
def _raises_timeout():
    raise TimeoutError("took too long")


@app.get("/_test/raises-httpx-timeout")
def _raises_httpx_timeout():
    raise httpx.ConnectTimeout("connect took too long")


client = TestClient(app, raise_server_exceptions=False)
# raise_server_exceptions=False, not TestClient(app)'s own default of True -- verified while
# writing these tests: a handler registered for the literal Exception class is routed through
# Starlette's ServerErrorMiddleware, which re-raises the original exception AFTER sending the
# handler's own response (so a real server's own error logging still sees it); TestClient's
# default surfaces that re-raise back into the calling test even though a correct response was
# already produced. Handlers for specific classes (RequestValidationError, TimeoutError) go
# through ExceptionMiddleware instead, which does not re-raise -- only the 500 test actually
# needed this, but it is harmless for the others since their responses were already clean


# --- structured envelope + the 400/422 split ---------------------------------------------------


def test_malformed_json_body_returns_400_with_the_structured_envelope():
    response = client.post(
        "/chat", content=b"{not valid json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "invalid_json", "message": "the request body is not valid JSON"}
    }
    # requires the dedicated 400 path, not FastAPI's own default of 422 for everything


def test_a_missing_required_field_still_returns_422_not_400():
    response = client.post("/chat", json={"k": 3})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "query" in body["error"]["message"]
    # requires a well-formed-but-invalid body to keep the semantically correct 422


def test_multiple_field_errors_are_joined_into_one_message():
    response = client.post("/chat", json={"k": 0})
    body = response.json()
    assert "query" in body["error"]["message"]
    assert "k" in body["error"]["message"]
    # requires BOTH the missing query and the bad k to reach the one joined message


# --- the 500 catch-all ---------------------------------------------------------------------------


def test_an_unexpected_exception_returns_a_structured_500():
    response = client.get("/_test/raises-unexpected")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "an unexpected error occurred"}
    }
    # requires the real exception's own message to stay OUT of the response body


# --- the 504 catch-all ---------------------------------------------------------------------------


def test_a_timeout_error_returns_a_structured_504():
    response = client.get("/_test/raises-timeout")
    assert response.status_code == 504
    assert response.json() == {"error": {"code": "upstream_timeout", "message": "took too long"}}


def test_an_httpx_timeout_exception_also_returns_a_structured_504():
    response = client.get("/_test/raises-httpx-timeout")
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"
    # requires BOTH registered timeout types (builtin TimeoutError, httpx's own) to share a handler


# --- /health's new response_model ----------------------------------------------------------------


def test_health_still_returns_its_documented_shape():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "qdrant_url", "neo4j_uri", "ollama_url"}
    # requires response_model to still describe exactly the fields already returned


# --- the documented streaming limitation, proven as a regression --------------------------------


def _chunk() -> DocumentChunk:
    return DocumentChunk(
        chunk_id="a_0",
        doc_id="doc-a",
        source_path="a.md",
        category="test",
        title="a",
        chunk_index=0,
        text="text of a",
        char_count=10,
        approx_token_count=50,
    )
    # a minimal, directly-constructed chunk, mirroring tests/test_chat.py's own local helper


class _FakeRetriever:
    """Always finds one chunk, so RAGPipeline.stream() reaches real generation."""

    def retrieve(self, query, k):
        return RetrievalResult(
            query=query,
            results=(
                RankedChunk(
                    chunk=_chunk(),
                    final_rank=1,
                    relevance_score=1.0,
                    hybrid_score=1.0,
                    hybrid_rank=1,
                    vector_rank=1,
                    graph_rank=None,
                    vector_score=1.0,
                ),
            ),
        )


class _FakeLLMThatTimesOut:
    """Streams one real fragment, then fails with the exact type the new 504 handler catches."""

    def generate(self, prompt, temperature, max_tokens, *, system=None):
        return "unused"
        # query() isn't exercised by this endpoint, but LLM's own Protocol requires it

    def stream(self, prompt, temperature, *, system=None):
        yield "partial"
        # yields one real fragment successfully first...
        raise TimeoutError("ollama took too long")
        # ...then fails with TimeoutError -- the same type _handle_timeout is registered for


def test_chat_mid_stream_timeout_is_still_handled_in_stream_not_by_the_new_504_handler():
    app.state.rag = RAGPipeline(_FakeRetriever(), _FakeLLMThatTimesOut())
    # sets the SAME attribute lifespan would normally set, bypassing it entirely
    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.status_code == 200
    # proves the new 504 handler did NOT intercept this -- a case it actually caught would
    # have returned 504 with the structured envelope, not a 200 SSE stream
    assert response.text.endswith("data: [ERROR] ollama took too long\n\n")
    # proves the failure still reached the client via _sse_events's own existing try/except,
    # exactly as P7-M2 built it -- confirming the limitation documented in _handle_timeout
    # is real, not just a claim in a comment
