"""API tests (P7-M6): the seams the per-concern API test files cannot see.

tests/test_chat.py, test_errors.py, test_logging.py, test_openapi.py and test_health.py each
unit-test ONE concern of src/api/main.py against fakes (35 tests, 88% line coverage). This file
follows the tests/test_rag.py precedent instead -- a top-level cross-component file -- and
covers only what those files do not prove:

* the public route table, and what the framework itself answers for paths, methods and bodies
  the app never declared (404 / 405 / redirects / malformed bodies / boundary values);
* the real retrieval stack behind real HTTP, and the real `lifespan` that wires it up;
* what a TestClient cannot observe: chunks arriving incrementally, and a slow stream not
  blocking other requests (a tiny raw-ASGI harness, plus negative controls proving it can fail);
* the JSON log lines an operator actually sees;
* (P7-M7) the SSE stream as a spec-compliant client reads it -- through a reference parser that
  has its own tests -- including every kind of line ending a fragment can carry;
* (P7-M7) what /openapi.json DECLARES against what the handlers really send, including a guard
  against schemas that would accept anything;
* (P7-M7) what happens when a client walks away mid-stream;
* known, unhardened gaps, written as strict xfails so fixing one forces the marker's removal;
* a small live tier (`-m live`) against the real services.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import re
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import jsonschema
import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

import src.api.main as api
from src.api.main import app
from src.core.config import settings
from src.embedding.embedder import Embedder
from src.orchestration.prompts import REFUSAL_TEXT
from src.orchestration.rag import RAGPipeline
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.pipeline import RankedChunk, RetrievalPipeline, RetrievalResult
from src.retrieval.reranker import IdentityReranker
from src.vectorstore.ingest import run_ingestion
from src.vectorstore.store import QdrantVectorStore
from tests.support import (
    BagOfWordsClient,
    make_chunk,
    neo4j_down,
    pinned_digest,
    reachable,
    write_corpus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TEST_ONLY_PREFIX = "/_test"
# every test-only route in this suite lives under this prefix (tests/test_errors.py and
# tests/test_logging.py already follow it), because they all register onto the ONE shared,
# module-level `app` -- see public_routes() for why that matters

client = TestClient(app, raise_server_exceptions=False)
# raise_server_exceptions=False for the same reason tests/test_errors.py needed it: the
# bare-Exception path re-raises after sending its own 500 (decision.md D-2026-09-30-23)


@app.get(f"{TEST_ONLY_PREFIX}/api-raises-unexpected")
def _raises_unexpected() -> None:
    raise RuntimeError("boom")


# --- helpers ---------------------------------------------------------------------------------


def ranked(source: str, rank: int = 1) -> RankedChunk:
    """A minimal RankedChunk for `source`, built from tests.support's shared chunk factory."""
    return RankedChunk(
        chunk=make_chunk(source, 0, f"text of {source}"),
        final_rank=rank,
        relevance_score=1.0,
        hybrid_score=1.0,
        hybrid_rank=rank,
        vector_rank=rank,
        graph_rank=None,
        vector_score=1.0,
    )


class StubRetriever:
    """Answers retrieve() with canned results (or raises), recording every call."""

    def __init__(self, results=(), raises=None):
        self.results = results
        # the RankedChunks retrieve() will hand back, best first
        self.raises = raises
        # an exception retrieve() should raise instead of answering, or None
        self.calls: list[tuple[str, int]] = []
        # every (query, k) this stub received, in order

    def retrieve(self, query, k):
        self.calls.append((query, k))
        # recorded first, so a call that goes on to raise is still visible to the test
        if self.raises is not None:
            raise self.raises
        return RetrievalResult(query=query, results=tuple(self.results))


class StubLLM:
    """Streams canned fragments (optionally slowly), then optionally fails, recording calls."""

    def __init__(self, fragments=("An answer.",), *, delay=0.0, raises=None):
        self.fragments = fragments
        # what stream() yields, in order
        self.delay = delay
        # seconds of real, BLOCKING sleep before each fragment, like a blocking network read
        self.raises = raises
        # an exception raised after the last fragment, or None
        self.calls: list[dict] = []
        # the prompt and system text of every stream() call
        self.produced = 0
        # how many fragments stream() has handed out so far, across all calls
        self.closed = False
        # True once a stream() generator has been closed -- finished, failed or abandoned

    def generate(self, prompt, temperature, max_tokens, *, system=None):
        return "".join(self.fragments)

    def stream(self, prompt, temperature, *, system=None):
        self.calls.append({"prompt": prompt, "system": system})
        try:
            for fragment in self.fragments:
                if self.delay:
                    time.sleep(self.delay)
                self.produced += 1
                yield fragment
            if self.raises is not None:
                raise self.raises
        finally:
            self.closed = True
            # runs however the generator ends -- the same hook a real model client uses to
            # release its HTTP connection to the model server


class NoOpGraph:
    """A graph retriever that never recognises any entity, as the toy corpus has none."""

    def retrieve(self, query, k):
        return []


class FakeDriver:
    """Stands in for the Neo4j driver, counting how often it is closed."""

    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


SSE_LINE_END = re.compile(r"\r\n|\r|\n")
# the three line terminators the SSE grammar defines, and the ONLY three: str.splitlines() would
# also split on U+2028, \x0b, \x0c and more, none of which SSE treats as a line end


def sse_stream(body: str) -> list[tuple[str, str]]:
    """Parses a raw SSE body the way a browser's EventSource does: (event type, data) pairs.

    Written from the WHATWG "event stream interpretation" algorithm, not from this server's own
    framing, so a framing bug cannot hide behind a parser that shares it. Two simplifications,
    both safe because this server only ever sends `data:` lines: `id` and `retry` are ignored,
    and a leading byte-order mark is not stripped.
    """
    events: list[tuple[str, str]] = []
    data_lines: list[str] = []
    event_type = ""
    for line in SSE_LINE_END.split(body)[:-1]:
        # [:-1] drops what follows the LAST line end -- an empty string, or a partial line that
        # never got its terminator -- because that remainder is not a complete line, and
        # treating it as a blank one would dispatch an event the stream never finished
        if line == "":
            # a blank line ends the event being built, which only counts if it has a data line
            if data_lines:
                events.append((event_type or "message", "\n".join(data_lines)))
            data_lines, event_type = [], ""
        elif line.startswith(":"):
            # a comment line, ignored
            continue
        else:
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            # exactly ONE leading space after the colon is dropped, never more
            if field == "data":
                data_lines.append(value)
            elif field == "event":
                event_type = value
    return events
    # an event still open when the body ends (no closing blank line) is dropped, as in a browser


def sse_events(body: str) -> list[str]:
    """Just the data of every event in a raw SSE body, in order."""
    return [data for _, data in sse_stream(body)]


def terminal_positions(events: list[str]) -> list[int]:
    """The index of every terminal event ([DONE] or [ERROR] ...) in a parsed stream."""
    return [i for i, event in enumerate(events) if event == "[DONE]" or event.startswith("[ERROR]")]


def public_routes() -> dict[str, set[str]]:
    """The app's route table as {path: methods}, minus the test-only routes other files add.

    Every test file registers its throwaway routes onto the single module-level `app` (merely
    importing tests/test_errors.py adds three), so the raw `app.routes` depends on which files
    pytest happened to collect.
    """
    table: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or methods is None or path.startswith(TEST_ONLY_PREFIX):
            continue
        table.setdefault(path, set()).update(methods)
    return table


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
# the same small toy corpus tests/test_rag.py and tests/test_retrieval.py use


def build_index(tmp_path) -> QdrantClient:
    """An in-memory Qdrant filled by the REAL ingestion job from the toy corpus."""
    qdrant = QdrantClient(":memory:")
    chunks_dir = tmp_path / "chunks"
    write_corpus(chunks_dir, CORPUS)
    run_ingestion(
        qdrant,
        lambda: Embedder(BagOfWordsClient(), verify=pinned_digest),
        collection="chunks",
        chunks_dir=chunks_dir,
        log_path=tmp_path / "log.json",
    )
    return qdrant


_ABSENT = object()


@pytest.fixture
def app_state_guard():
    """Puts `app.state.rag` back exactly as found -- every test shares one module-level app."""
    previous = getattr(app.state, "rag", _ABSENT)
    yield
    if previous is _ABSENT:
        if hasattr(app.state, "rag"):
            del app.state.rag
    else:
        app.state.rag = previous


@pytest.fixture
def pipeline_on_app(app_state_guard):
    """A function that installs a real RAGPipeline over the given parts on the shared app."""

    def install(retriever, llm, **pipeline_kwargs):
        app.state.rag = RAGPipeline(retriever, llm, **pipeline_kwargs)
        # sets the SAME attribute lifespan would set, bypassing lifespan entirely

    return install


@pytest.fixture
def real_retrieval(tmp_path):
    """A real RetrievalPipeline over a real, ingested, in-memory index (graph finds nothing)."""
    vector = QdrantVectorStore(
        build_index(tmp_path),
        Embedder(BagOfWordsClient(), verify=pinned_digest),
        collection="chunks",
    )
    return RetrievalPipeline(HybridRetriever(vector, NoOpGraph()), IdentityReranker())


# --- the public contract ---------------------------------------------------------------------


def test_the_public_route_table_is_exactly_the_documented_one():
    assert public_routes() == {
        "/health": {"GET"},
        "/chat": {"POST"},
        "/docs": {"GET", "HEAD"},
        "/docs/oauth2-redirect": {"GET", "HEAD"},
        "/redoc": {"GET", "HEAD"},
        "/openapi.json": {"GET", "HEAD"},
    }
    # requires nothing public beyond /health, /chat and FastAPI's own docs routes -- an
    # accidentally committed debug route (anything not under /_test) would fail right here


# --- what the framework answers for things the app never declared ---------------------------


def test_an_unknown_path_is_a_404_in_the_error_envelope():
    response = client.get("/nonexistent")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}
    # requires routing's own 404 to use the app's one error shape, not FastAPI's {"detail": ...}


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/chat", "POST"),
        ("PUT", "/chat", "POST"),
        ("DELETE", "/chat", "POST"),
        ("OPTIONS", "/chat", "POST"),
        ("POST", "/health", "GET"),
    ],
)
def test_a_wrong_method_is_a_405_envelope_that_still_names_the_allowed_method(
    method, path, allowed
):
    response = client.request(method, path)
    assert response.status_code == 405
    assert response.headers["allow"] == allowed
    # requires the handler to keep the Allow header Starlette attached to its HTTPException
    assert response.json() == {
        "error": {"code": "method_not_allowed", "message": "Method Not Allowed"}
    }


def test_a_trailing_slash_is_redirected_with_a_307_that_preserves_the_method():
    response = client.post("/chat/", json={"query": "hi", "k": 1}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/chat")
    # requires a method-preserving redirect, not a 301/302 that would turn the POST into a GET


# --- request bodies and boundary values ------------------------------------------------------


@pytest.mark.parametrize(
    "request_kwargs",
    [
        pytest.param(
            {"content": b'{"query": "hi", "k": 1}', "headers": {"content-type": "text/plain"}},
            id="valid-json-labelled-text-plain",
        ),
        pytest.param({"content": b'{"query": "hi", "k": 1}'}, id="valid-json-no-content-type"),
        pytest.param({"json": ["not", "an", "object"]}, id="json-array"),
        pytest.param({"content": b""}, id="empty-body"),
    ],
)
def test_a_body_that_is_not_a_labelled_json_object_is_a_422_envelope(request_kwargs):
    response = client.post("/chat", **request_kwargs)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"].startswith("body")
    # requires the failure to be reported against the whole body, not against a missing field


@pytest.mark.parametrize("bad_query", [None, ["a"], 5], ids=["null", "list", "number"])
def test_a_query_that_is_not_a_string_is_a_422(bad_query):
    response = client.post("/chat", json={"query": bad_query, "k": 1})
    assert response.status_code == 422
    assert "body.query" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "blank", ["   ", "\n\t ", "  "], ids=["spaces", "newline-and-tab", "unicode-spaces"]
)
def test_a_whitespace_only_query_is_a_422_before_anything_streams(blank, pipeline_on_app):
    retriever = StubRetriever(results=[ranked("a.md")])
    pipeline_on_app(retriever, StubLLM())
    response = client.post("/chat", json={"query": blank, "k": 1})
    assert response.status_code == 422
    assert (
        response.json()["error"]["message"] == "body.query: String should have at least 1 character"
    )
    # requires the same message "" gets, not a 200 stream carrying an in-band [ERROR]
    assert retriever.calls == []
    # requires the pipeline never to have been reached


def test_surrounding_whitespace_is_trimmed_before_retrieval(pipeline_on_app):
    retriever = StubRetriever(results=[ranked("a.md")])
    pipeline_on_app(retriever, StubLLM())
    response = client.post("/chat", json={"query": "  Who founded Acme?\n", "k": 1})
    assert response.status_code == 200
    assert retriever.calls == [("Who founded Acme?", 1)]
    # requires retrieval to see the trimmed question, not the padded one the client sent


@pytest.mark.parametrize(
    ("sent", "seen"), [(1, 1), (2.0, 2), ("3", 3)], ids=["int", "integral-float", "numeric-string"]
)
def test_k_accepts_integral_numbers_and_numeric_strings(sent, seen, pipeline_on_app):
    retriever = StubRetriever(results=[ranked("a.md")])
    pipeline_on_app(retriever, StubLLM())
    response = client.post("/chat", json={"query": "hi", "k": sent})
    assert response.status_code == 200
    assert retriever.calls == [("hi", seen)]
    # requires Pydantic's lax coercion to hand retrieval a real int in every case


@pytest.mark.parametrize("bad_k", [2.5, None, [1]], ids=["fractional-float", "null", "list"])
def test_k_rejects_fractional_null_and_list_values(bad_k):
    response = client.post("/chat", json={"query": "hi", "k": bad_k})
    assert response.status_code == 422
    assert "body.k" in response.json()["error"]["message"]


def test_unknown_request_fields_are_ignored(pipeline_on_app):
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM())
    response = client.post("/chat", json={"query": "hi", "k": 1, "temperature": 5})
    assert response.status_code == 200
    # requires an unmodelled field (here a would-be temperature override) to be dropped
    # silently rather than rejected or applied


def test_unicode_survives_the_round_trip_and_the_stream_declares_utf8(pipeline_on_app):
    retriever = StubRetriever(results=[ranked("a.md")])
    pipeline_on_app(retriever, StubLLM(fragments=["Réponse ✓ ", "你好"]))
    question = "¿Quién fundó Insurellm? 🚀"
    response = client.post("/chat", json={"query": question, "k": 1})
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert retriever.calls == [(question, 1)]
    # requires the multi-byte question to reach retrieval character for character
    assert sse_events(response.text)[:2] == ["Réponse ✓ ", "你好"]
    # requires the multi-byte answer fragments to reach the client intact


# --- known, unhardened gaps: the DESIRED behaviour, as strict xfails -------------------------
# Strict, so the day one of these is fixed the test XPASSes, the suite fails, and the marker
# has to be removed deliberately. None contradicts a documented rule (notes/step-7.2 already
# lists "no request size limits"); they are missing hardening, not broken promises.


@pytest.mark.xfail(
    strict=True,
    reason="gap: ChatRequest.k has no upper bound, so k=10**9 reaches the retriever unchanged",
)
def test_an_absurdly_large_k_is_rejected(pipeline_on_app):
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM())
    response = client.post("/chat", json={"query": "hi", "k": 10**9})
    assert response.status_code == 422


@pytest.mark.xfail(
    strict=True,
    reason="gap: ChatRequest.query has no maximum length, so 100,000 characters are accepted",
)
def test_an_enormous_query_is_rejected(pipeline_on_app):
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM())
    response = client.post("/chat", json={"query": "q" * 100_000, "k": 1})
    assert response.status_code == 422


@pytest.mark.xfail(
    strict=True,
    reason="gap: the SSE response sets neither Cache-Control nor X-Accel-Buffering, so a "
    "proxy in front of it is free to cache or buffer the stream",
)
def test_the_sse_response_is_safe_to_put_behind_a_proxy(pipeline_on_app):
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM())
    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.headers.get("cache-control") == "no-cache"
    assert response.headers.get("x-accel-buffering") == "no"


@pytest.mark.xfail(
    strict=True, reason="gap: /health is GET-only, so a HEAD liveness probe gets a 405"
)
def test_health_answers_a_head_probe():
    assert client.head("/health").status_code == 200


@pytest.mark.xfail(
    strict=True,
    reason="gap G1: when a client leaves mid-stream, the generator chain holding the model "
    "server's connection is closed only when the cyclic garbage collector runs, not at once",
)
def test_a_client_that_walks_away_gets_the_model_stream_closed_at_once(pipeline_on_app):
    llm = StubLLM([f"c{i}" for i in range(50)], delay=0.05)
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), llm)
    gc.disable()
    # the fix must not lean on a cyclic-GC pass, so none may happen while the stream hangs
    try:
        _, closed_at_once = asyncio.run(walk_away(llm))
    finally:
        gc.enable()
        gc.collect()
        # releases the dangling chain so it cannot outlive this test
    assert closed_at_once


# --- the SSE protocol, across every way a stream can end -------------------------------------


def test_the_sse_parser_rebuilds_multiline_events_and_ignores_the_trailing_blank():
    body = "data: Hello\n\ndata: \ndata: \ndata: Sources:\ndata: [1] a.md\n\ndata: [DONE]\n\n"
    assert sse_events(body) == ["Hello", "\n\nSources:\n[1] a.md", "[DONE]"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("data: a\r\rdata: b\r\r", [("message", "a"), ("message", "b")], id="lone-cr"),
        pytest.param("data: a\r\ndata: b\r\n\r\n", [("message", "a\nb")], id="crlf-is-one-end"),
        pytest.param("data: a b\n\n", [("message", "a b")], id="u2028-is-no-line-end"),
        pytest.param("data: a\n\ndata: open\n", [("message", "a")], id="unfinished-is-dropped"),
        pytest.param(": a comment\ndata: a\n\n", [("message", "a")], id="comment"),
        pytest.param("data:  two\n\n", [("message", " two")], id="one-leading-space-removed"),
        pytest.param("data:none\n\n", [("message", "none")], id="no-space-after-colon"),
        pytest.param("event: error\ndata: x\n\n", [("error", "x")], id="event-type"),
        pytest.param("data\n\n", [("message", "")], id="bare-field-name-is-empty-value"),
    ],
)
def test_the_reference_sse_parser_follows_the_spec(body, expected):
    assert sse_stream(body) == expected
    # pins the parser the framing tests below rely on to the WHATWG rules it was written from


@pytest.mark.parametrize(
    ("fragment", "client_sees"),
    [
        pytest.param("bare\rcarriage", "bare\ncarriage", id="lone-cr"),
        pytest.param("crlf\r\nfragment", "crlf\nfragment", id="crlf"),
        pytest.param("ends-with-cr\r", "ends-with-cr\n", id="trailing-cr"),
        pytest.param("mixed\rx\ny\r\nz", "mixed\nx\ny\nz", id="all-three-line-ends"),
        pytest.param("inject\revent: error", "inject\nevent: error", id="cr-then-a-field-name"),
        pytest.param("a\r\rdata: forged", "a\n\ndata: forged", id="cr-pair-forges-an-event"),
        pytest.param("sep: after", "sep: after", id="u2028-is-not-a-line-end"),
        pytest.param("v\x0bf\x0cn\x85s\x1ce", "v\x0bf\x0cn\x85s\x1ce", id="other-separators"),
        pytest.param("ends-with-lf\n", "ends-with-lf\n", id="trailing-lf"),
    ],
)
def test_a_line_ending_inside_a_fragment_can_neither_split_nor_forge_an_sse_event(
    fragment, client_sees, pipeline_on_app
):
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM(fragments=[fragment]))
    response = client.post("/chat", json={"query": "hi", "k": 1})
    events = sse_stream(response.text)
    assert [kind for kind, _ in events] == ["message"] * 3
    # requires exactly the fragment, the source list and [DONE], all plain messages: no fragment
    # may end its own event early, start another one, or smuggle in an `event:` field
    assert events[0] == ("message", client_sees)
    # requires the fragment to arrive with every line ending (CR, CRLF or LF) rebuilt as the one
    # newline an SSE data field can carry -- and every other character left exactly as it was
    assert events[-1] == ("message", "[DONE]")
    assert "\r" not in response.text
    # requires the server's own framing to use bare LF only, whatever the model emitted


@pytest.mark.parametrize(
    "make_parts",
    [
        pytest.param(lambda: (StubRetriever(results=[ranked("a.md")]), StubLLM()), id="answered"),
        pytest.param(lambda: (StubRetriever(results=[]), StubLLM()), id="nothing-retrieved"),
        pytest.param(
            lambda: (
                StubRetriever(results=[ranked("a.md")]),
                StubLLM(raises=RuntimeError("ollama down")),
            ),
            id="generation-fails-midstream",
        ),
        pytest.param(
            lambda: (StubRetriever(raises=ConnectionError("qdrant down")), StubLLM()),
            id="retrieval-fails-before-any-fragment",
        ),
    ],
)
def test_every_stream_ends_with_exactly_one_terminal_event_and_it_is_last(
    make_parts, pipeline_on_app
):
    retriever, llm = make_parts()
    pipeline_on_app(retriever, llm)
    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.status_code == 200
    events = sse_events(response.text)
    assert terminal_positions(events) == [len(events) - 1]
    # requires one [DONE] or one [ERROR ...] -- never both, never none -- and nothing after it


def test_a_retrieval_failure_is_reported_in_band_and_the_model_is_never_called(pipeline_on_app):
    llm = StubLLM()
    pipeline_on_app(StubRetriever(raises=ConnectionError("qdrant down")), llm)
    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.status_code == 200
    # requires the headers to have gone out before the failure, so the status could not change
    assert response.text == "data: [ERROR] qdrant down\n\n"
    assert llm.calls == []


# --- the declared contract against the observed one (P7-M7) ----------------------------------


def declared_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema itself, with a top-level `$ref` followed to the component it names."""
    if "$ref" not in schema:
        return schema
    return app.openapi()["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]


def validate_against(instance: Any, schema: dict[str, Any]) -> None:
    """Validates `instance` against a schema taken from /openapi.json, following its `$ref`s.

    jsonschema resolves a `#/components/schemas/X` pointer against the document the schema sits
    in, so the OpenAPI document's `components` are copied in beside it -- without them an
    ErrorResponse that points at ErrorBody could not be followed.
    """
    jsonschema.validate(instance, {**schema, "components": app.openapi()["components"]})


def assert_matches_the_declared_contract(response, path: str, method: str) -> None:
    """The declared-vs-observed check: /openapi.json promises a status, a media type and a body
    schema for this response; requires the observed response to keep every part of the promise.
    """
    declared = app.openapi()["paths"][path][method]["responses"].get(str(response.status_code))
    assert declared is not None, f"{response.status_code} is not declared for {method} {path}"
    media_type = response.headers["content-type"].split(";")[0]
    assert media_type in declared.get("content", {}), f"{media_type} is not a declared media type"
    if media_type != "application/json":
        return
        # a text/event-stream body is checked by the protocol tests, not by a JSON schema
    schema = declared["content"][media_type].get("schema")
    assert schema is not None, "a JSON response is declared with an example but no schema"
    validate_against(response.json(), schema)
    required = declared_schema(schema).get("required")
    assert required, "a schema with no required property accepts any object, however wrong"
    assert set(required) <= set(response.json())
    # requires every property the schema calls required to be present, not merely tolerated


def raise_timeout(*args, **kwargs):
    raise TimeoutError("the upstream took too long")


def provoke_chat_failure(status: int, monkeypatch, pipeline_on_app):
    """Provokes the REAL handler behind `status` on POST /chat and returns what it sent."""
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM())
    # a working pipeline first, so that nothing except the one provoked failure can go wrong
    if status == 400:
        return client.post("/chat", content=b"{bad", headers={"content-type": "application/json"})
    if status == 422:
        return client.post("/chat", json={"query": "hi", "k": 0})
    if status == 500:
        del app.state.rag
        # no pipeline, as if lifespan never ran: the route itself raises an AttributeError
        return client.post("/chat", json={"query": "hi", "k": 1})
    monkeypatch.setattr(api, "_sse_events", raise_timeout)
    # raised inside the route, before any response has started -- the only place a 504 can come
    # from today, which is why the schema can only be checked against a provoked one
    return client.post("/chat", json={"query": "hi", "k": 1})


@pytest.mark.parametrize("status", [400, 422, 500, 504])
def test_every_error_chat_declares_matches_what_its_handlers_really_send(
    status, monkeypatch, pipeline_on_app
):
    response = provoke_chat_failure(status, monkeypatch, pipeline_on_app)
    assert response.status_code == status
    assert_matches_the_declared_contract(response, "/chat", "post")
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    # requires the exact envelope documented in docs/api-usage.md, nothing extra


def test_the_declared_success_response_is_the_observed_one(pipeline_on_app):
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM())
    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.status_code == 200
    assert_matches_the_declared_contract(response, "/chat", "post")


def test_health_matches_its_declared_schema():
    response = client.get("/health")
    assert response.status_code == 200
    assert_matches_the_declared_contract(response, "/health", "get")


def test_every_declared_json_example_validates_against_its_own_declared_schema():
    responses = app.openapi()["paths"]["/chat"]["post"]["responses"]
    checked = []
    for status, declared in responses.items():
        media = declared.get("content", {}).get("application/json")
        if media is None:
            continue
        assert "schema" in media, f"{status} declares an example but no schema"
        assert "example" in media, f"{status} declares a schema but no example"
        validate_against(media["example"], media["schema"])
        checked.append(status)
    assert checked == ["400", "422", "500", "504"]
    # requires the documentation examples to be real instances of the schema beside them, so
    # an example can never drift from the shape it illustrates


# --- the real retrieval stack behind real HTTP -----------------------------------------------


def test_a_real_question_streams_a_real_grounded_source_list(real_retrieval, pipeline_on_app):
    pipeline_on_app(real_retrieval, StubLLM(fragments=["Ada Lovelace founded Acme [1]."]))
    response = client.post("/chat", json={"query": "Who founded Acme?", "k": 2})
    events = sse_events(response.text)
    assert events[0] == "Ada Lovelace founded Acme [1]."
    assert "[1] company/about.md" in events[-2]
    # requires the mechanical source list to name the REAL file real retrieval ranked first --
    # a stub could never have produced this text
    assert events[-1] == "[DONE]"


@pytest.mark.parametrize("k", [1, 3])
def test_the_requested_k_decides_how_many_sources_are_cited(k, real_retrieval, pipeline_on_app):
    pipeline_on_app(real_retrieval, StubLLM())
    response = client.post("/chat", json={"query": "Who founded Acme?", "k": k})
    sources = sse_events(response.text)[-2]
    assert len(re.findall(r"^\[\d+\]", sources, flags=re.MULTILINE)) == k
    # requires k to travel from the JSON body, through real retrieval, to the cited sources


def test_the_real_prompt_reaches_the_llm_built_from_the_trimmed_query(
    real_retrieval, pipeline_on_app
):
    llm = StubLLM()
    pipeline_on_app(real_retrieval, llm)
    client.post("/chat", json={"query": "  Who founded Acme?  ", "k": 1})
    call = llm.calls[0]
    assert "ONLY the numbered source excerpts" in call["system"]
    # requires the real, published system prompt
    assert "Question: Who founded Acme?" in call["prompt"]
    # requires the trimmed question, not the padded one the client sent
    assert "Acme was founded in 2015 by Ada Lovelace." in call["prompt"]
    # requires the text of the real chunk that real retrieval found


def test_a_genuinely_empty_index_streams_the_real_refusal(pipeline_on_app):
    vector = QdrantVectorStore(
        QdrantClient(":memory:"),
        Embedder(BagOfWordsClient(), verify=pinned_digest),
        collection="chunks",
    )
    vector.ensure_collection()
    # a real, valid, searchable collection holding zero points
    llm = StubLLM()
    retrieval = RetrievalPipeline(HybridRetriever(vector, NoOpGraph()), IdentityReranker())
    pipeline_on_app(retrieval, llm)
    response = client.post("/chat", json={"query": "Who founded Acme?", "k": 3})
    assert sse_events(response.text) == [REFUSAL_TEXT, "[DONE]"]
    assert llm.calls == []
    # requires the model never to be asked about a question retrieval found nothing for


# --- the real lifespan, with only its network-touching calls replaced ------------------------


@pytest.fixture
def hermetic_lifespan(monkeypatch, tmp_path, app_state_guard):
    """The REAL lifespan(), with only the calls that touch a network service swapped out.

    Replaced: the Ollama and Qdrant client builders, Embedder (its constructor verifies the
    model digest against Ollama), build_driver, GraphStore (its constructor checks the Neo4j
    schema), GraphRetriever (its constructor loads every entity name), build_llm and the Cohere
    key. Everything else lifespan builds is the real class, over a real in-memory Qdrant that
    the real ingestion job filled.
    """
    qdrant = build_index(tmp_path)
    seen = SimpleNamespace(
        urls={},
        rerankers=[],
        driver=FakeDriver(),
        llm=StubLLM(fragments=["Ada Lovelace [1]."]),
    )
    real_pipeline_class = RetrievalPipeline
    # captured BEFORE patching, so the recording subclass below extends the real class

    def fake_build_ollama_client(url):
        seen.urls["embedder_ollama"] = url
        return object()

    def fake_embedder(client, ollama_url):
        return Embedder(BagOfWordsClient(), verify=pinned_digest)

    def fake_build_qdrant_client(url):
        seen.urls["qdrant"] = url
        return qdrant

    def fake_build_llm(url):
        seen.urls["llm_ollama"] = url
        return seen.llm

    class RecordingPipeline(real_pipeline_class):
        def __init__(self, hybrid, reranker, **kwargs):
            seen.rerankers.append(reranker)
            super().__init__(hybrid, reranker, **kwargs)

    monkeypatch.setattr(api, "build_ollama_client", fake_build_ollama_client)
    monkeypatch.setattr(api, "Embedder", fake_embedder)
    monkeypatch.setattr(api, "build_qdrant_client", fake_build_qdrant_client)
    monkeypatch.setattr(api, "build_driver", lambda: seen.driver)
    monkeypatch.setattr(api, "GraphStore", lambda driver: object())
    monkeypatch.setattr(api, "GraphRetriever", lambda store: NoOpGraph())
    monkeypatch.setattr(api, "build_llm", fake_build_llm)
    monkeypatch.setattr(api, "RetrievalPipeline", RecordingPipeline)
    monkeypatch.setattr(api.settings, "cohere_api_key", "")
    if hasattr(app.state, "rag"):
        del app.state.rag
        # every test starts from "nothing built yet", so a pipeline left by another test
        # cannot make a startup assertion pass for the wrong reason
    return seen


def test_startup_builds_a_real_pipeline_that_answers_a_real_question_over_http(
    hermetic_lifespan,
):
    with TestClient(app) as lifespan_client:
        assert isinstance(app.state.rag, RAGPipeline)
        # requires startup to have stored a real RAGPipeline on app.state
        response = lifespan_client.post("/chat", json={"query": "Who founded Acme?", "k": 1})
    events = sse_events(response.text)
    assert events[0] == "Ada Lovelace [1]."
    assert "[1] company/about.md" in events[-2]
    assert events[-1] == "[DONE]"


def test_startup_wires_every_client_to_the_configured_service_urls(hermetic_lifespan):
    with TestClient(app):
        pass
    assert hermetic_lifespan.urls == {
        "embedder_ollama": api.settings.ollama_url,
        "qdrant": api.settings.qdrant_url,
        "llm_ollama": api.settings.ollama_url,
    }
    # requires each builder to receive the URL from settings, not a hard-coded or swapped one


def test_shutdown_closes_the_neo4j_driver_exactly_once(hermetic_lifespan):
    with TestClient(app):
        assert hermetic_lifespan.driver.close_calls == 0
        # requires the driver to stay open for as long as the app is serving
    assert hermetic_lifespan.driver.close_calls == 1


def test_without_a_cohere_key_the_pipeline_uses_the_identity_reranker(hermetic_lifespan):
    with TestClient(app):
        pass
    assert [type(reranker) for reranker in hermetic_lifespan.rerankers] == [IdentityReranker]


def test_with_a_cohere_key_the_pipeline_uses_the_real_reranker_factory(
    hermetic_lifespan, monkeypatch
):
    class MarkerReranker(IdentityReranker):
        pass

    marker = MarkerReranker()
    monkeypatch.setattr(api.settings, "cohere_api_key", "test-key")
    monkeypatch.setattr(api.Reranker, "from_settings", lambda: marker)
    with TestClient(app):
        pass
    assert hermetic_lifespan.rerankers == [marker]
    # requires a configured key to route through Reranker.from_settings() rather than the stub


def test_a_startup_failure_after_the_driver_exists_still_closes_it(hermetic_lifespan, monkeypatch):
    def schema_mismatch(driver):
        raise RuntimeError("graph schema is not in force")

    monkeypatch.setattr(api, "GraphStore", schema_mismatch)
    with pytest.raises(RuntimeError, match="graph schema is not in force"):
        with TestClient(app):
            pass
    assert hermetic_lifespan.driver.close_calls == 1
    # requires the already-built driver to be released even though startup never finished
    assert not hasattr(app.state, "rag")
    # requires no half-built pipeline to be left behind


# --- what a TestClient cannot see: incremental delivery and concurrency ----------------------


class Sent(NamedTuple):
    at: float
    # the time.perf_counter() reading at the moment the app called send()
    message: dict[str, Any]
    # the raw ASGI message it sent


async def asgi_request(target_app, method, path, json_body=None, leave_after=None) -> list[Sent]:
    """Drives `target_app` directly -- no HTTP client -- and timestamps every message it sends.

    TestClient and httpx's ASGITransport both run the app to completion and hand back ONE
    buffered body, so neither can show when a chunk actually left the app. With `leave_after`
    the client disconnects once it has received that many response body messages.
    """
    body = json.dumps(json_body).encode() if json_body is not None else b""
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        # spec 2.3 is what uvicorn 0.35 advertises, which makes StreamingResponse watch for a
        # client disconnect through receive() -- the exact code path production runs
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    sent: list[Sent] = []
    finished = asyncio.Event()
    request_delivered = False

    async def receive():
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        # a real server blocks here until the client goes away; returning at once would make
        # StreamingResponse's disconnect listener spin forever
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(Sent(time.perf_counter(), message))
        bodies = sum(1 for item in sent if item.message["type"] == "http.response.body")
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            finished.set()
        if leave_after is not None and bodies >= leave_after:
            finished.set()
            # the client has seen enough and goes away mid-stream: receive() now reports it

    await target_app(scope, receive, send)
    return sent


async def overlap(target_app, slow, fast, head_start=0.1) -> tuple[float, float]:
    """Starts `slow`, then `fast` after `head_start`; returns when each finished (seconds)."""
    started = time.perf_counter()
    slow_task = asyncio.create_task(asgi_request(target_app, *slow))
    await asyncio.sleep(head_start)
    fast_sent = await asgi_request(target_app, *fast)
    slow_sent = await slow_task
    return fast_sent[-1].at - started, slow_sent[-1].at - started


def test_sse_fragments_reach_the_client_as_they_are_generated(pipeline_on_app):
    delay = 0.2
    fragments = ["c0", "c1", "c2", "c3"]
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), StubLLM(fragments, delay=delay))
    sent = asyncio.run(asgi_request(app, "POST", "/chat", {"query": "hi", "k": 1}))

    start_at = next(s.at for s in sent if s.message["type"] == "http.response.start")
    bodies = [s for s in sent if s.message["type"] == "http.response.body"]
    fragment_times = [s.at for s in bodies if s.message["body"].startswith(b"data: c")]
    assert len(fragment_times) == len(fragments)

    assert fragment_times[0] - start_at >= delay * 0.5
    # requires the response headers to leave BEFORE the first fragment exists -- which is why
    # a mid-stream failure can no longer change the status code
    gaps = [later - earlier for earlier, later in zip(fragment_times, fragment_times[1:])]
    assert all(gap >= delay * 0.5 for gap in gaps)
    # requires the fragments to arrive spread across the generation, not as one burst at the end
    assert all(s.message["more_body"] is True for s in bodies[:-1])
    assert bodies[-1].message["more_body"] is False
    # requires every body message but the last to promise more, and the last to end the stream


def test_a_slow_chat_stream_does_not_block_other_requests(pipeline_on_app):
    llm = StubLLM(["a", "b", "c", "d"], delay=0.25)
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), llm)
    slow = ("POST", "/chat", {"query": "hi", "k": 1})
    fast_done, slow_done = asyncio.run(overlap(app, slow, ("GET", "/health")))
    assert slow_done >= 0.9
    # sanity: the stream really spent ~1s in blocking sleeps, so the two requests overlapped
    assert fast_done < slow_done * 0.4
    # requires /health to have finished long before /chat did: the sync generator runs in a
    # worker thread, so its blocking calls never stall the event loop other requests share


async def walk_away(llm) -> tuple[int, bool]:
    """A /chat stream whose client leaves after two events; returns (fragments the model had
    produced, whether its stream had been closed) at the moment the response finished."""
    await asgi_request(app, "POST", "/chat", {"query": "hi", "k": 1}, leave_after=2)
    return llm.produced, llm.closed


def test_a_client_that_walks_away_stops_the_model_being_asked_for_more(pipeline_on_app):
    llm = StubLLM([f"c{i}" for i in range(50)], delay=0.05)
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), llm)

    async def scenario():
        produced, _ = await walk_away(llm)
        await asyncio.sleep(0.4)
        # long enough for eight more fragments, had the model still been asked for them
        return produced, llm.produced

    produced, later = asyncio.run(scenario())
    assert later == produced
    assert produced <= 4
    # requires the model to be asked for nothing once the client has gone: at most the two events
    # it received plus the one fragment already being generated at that moment


def test_the_abandoned_model_stream_is_closed_once_the_cyclic_gc_runs(pipeline_on_app):
    llm = StubLLM([f"c{i}" for i in range(50)], delay=0.05)
    pipeline_on_app(StubRetriever(results=[ranked("a.md")]), llm)
    gc.disable()
    # no garbage collection may run by itself while the abandoned stream is left hanging
    try:
        asyncio.run(walk_away(llm))
    finally:
        gc.enable()
    gc.collect()
    assert llm.closed
    # the positive control for the strict xfail test_a_client_that_walks_away_gets_the_model_
    # stream_closed_at_once: it proves the chain really is only waiting for the cyclic GC, so
    # that xfail cannot be failing because of a flaw in this file's own plumbing


def build_misbehaving_app() -> FastAPI:
    """Two deliberately wrong handlers, used only to prove the two checks above can fail."""
    bad = FastAPI()

    @bad.get("/blocks-the-loop")
    async def blocks_the_loop():
        time.sleep(0.5)
        # a blocking call made directly on the event loop -- the bug the concurrency check guards
        return {"ok": True}

    @bad.get("/fast")
    async def fast():
        return {"ok": True}

    @bad.get("/buffers-the-stream")
    def buffers_the_stream():
        pieces = []
        for i in range(4):
            time.sleep(0.1)
            pieces.append(f"c{i}")
        return Response("".join(pieces), media_type="text/event-stream")
        # collects everything first and returns one body -- the bug the streaming check guards

    return bad


def test_the_concurrency_check_would_catch_a_handler_that_blocks_the_event_loop():
    fast_done, slow_done = asyncio.run(
        overlap(build_misbehaving_app(), ("GET", "/blocks-the-loop"), ("GET", "/fast"))
    )
    assert fast_done >= slow_done * 0.9
    # requires the quick request to have been stuck behind the blocking one -- exactly the
    # symptom test_a_slow_chat_stream_does_not_block_other_requests asserts is absent


def test_the_streaming_check_would_catch_a_handler_that_buffers_everything():
    sent = asyncio.run(asgi_request(build_misbehaving_app(), "GET", "/buffers-the-stream"))
    bodies = [s for s in sent if s.message["type"] == "http.response.body"]
    start_at = next(s.at for s in sent if s.message["type"] == "http.response.start")
    assert len(bodies) == 1
    assert start_at >= 0.35
    # requires the whole ~0.4s to pass BEFORE the response even starts, and the body to arrive
    # in one piece -- the opposite of what test_sse_fragments_reach_the_client_... asserts


# --- the log lines an operator actually sees -------------------------------------------------


@pytest.fixture
def json_log_lines():
    """Attaches the app's REAL _JsonFormatter to a stream and returns a reader of its lines.

    Needed because logging.basicConfig() in main.py is a silent no-op under pytest (the root
    logger already carries pytest's own handlers when the module is imported), so without this
    the suite never sees a single byte of what an operator would.
    """
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(api._JsonFormatter())
    api_logger = logging.getLogger("src.api.main")
    previous_level = api_logger.level
    api_logger.addHandler(handler)
    api_logger.setLevel(logging.INFO)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    api_logger.removeHandler(handler)
    api_logger.setLevel(previous_level)


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("GET", "/health", 200), ("GET", "/nonexistent", 404), ("PUT", "/chat", 405)],
)
def test_every_request_leaves_one_parseable_json_access_line(method, path, status, json_log_lines):
    client.request(method, path)
    lines = [line for line in json_log_lines() if line.get("path") == path]
    assert len(lines) == 1
    line = lines[0]
    assert {"timestamp", "level", "logger", "message", "status_code", "duration_ms"} <= line.keys()
    assert (line["method"], line["status_code"], line["level"]) == (method, status, "INFO")
    # requires even framework-generated errors to be logged with their real status


def test_an_unhandled_error_logs_its_real_traceback_in_a_json_field(json_log_lines):
    client.get(f"{TEST_ONLY_PREFIX}/api-raises-unexpected")
    errors = [line for line in json_log_lines() if line["level"] == "ERROR"]
    assert len(errors) == 1
    assert "Traceback" in errors[0]["exception"]
    assert "RuntimeError: boom" in errors[0]["exception"]
    # requires the formatter's `exception` branch -- run by no other test -- to carry the real
    # traceback as ONE json field, so the record stays a single parseable line


def test_in_a_clean_process_the_json_handler_really_is_attached_to_the_root_logger():
    code = "import logging, src.api.main as m; print(m._handler in logging.getLogger().handlers)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True"
    # requires basicConfig to take effect when nothing else has configured logging first --
    # which is the real production situation, and the one no in-process test can reproduce


# --- live: the real services (`pytest -m live`; skips itself when they are down) -------------


@pytest.fixture(scope="module")
def live_client():
    """The real lifespan against the real services named in settings (needs the .env overrides)."""
    problems = [
        f"{name} not reachable at {url}"
        for name, url in (
            ("Qdrant", f"{settings.qdrant_url}/readyz"),
            ("Ollama", f"{settings.ollama_url}/api/version"),
        )
        if not reachable(url)
    ]
    neo4j_problem = neo4j_down()
    if neo4j_problem:
        problems.append(neo4j_problem)
    if problems:
        pytest.skip("; ".join(problems))
    with TestClient(app) as live:
        yield live


@pytest.mark.live
def test_live_startup_builds_a_real_pipeline_and_health_reports_the_configured_urls(live_client):
    assert isinstance(app.state.rag, RAGPipeline)
    assert live_client.get("/health").json() == {
        "status": "ok",
        "qdrant_url": settings.qdrant_url,
        "neo4j_uri": settings.neo4j_uri,
        "ollama_url": settings.ollama_url,
    }


@pytest.mark.live
def test_live_chat_obeys_the_stream_protocol_whether_or_not_generation_completes(
    live_client, capsys
):
    response = live_client.post("/chat", json={"query": "Who founded Insurellm?", "k": 3})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    assert terminal_positions(events) == [len(events) - 1]
    # requires exactly one terminal event, and nothing after it, whichever way the stream ended
    last = events[-1]
    if last == "[DONE]":
        outcome = "completed"
        assert any("Sources:" in event for event in events)
        # requires the mechanical source list to have arrived with a completed answer
    else:
        outcome = f"ended with {last}"
        assert "timed out" in last, f"a failure other than the documented host ceiling: {last}"
        # tolerates ONLY this host's documented generation-throughput ceiling
        # (notes/step-6.7); any other [ERROR] -- Qdrant down, a bad collection -- fails here
    with capsys.disabled():
        print(f"\nlive /chat stream: {outcome} ({len(events)} events)")
