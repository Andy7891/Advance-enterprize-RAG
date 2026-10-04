"""Tests for src/ui/client.py (P8-M1): everything the chat page needs from the API.

The client is the first in-repo CLIENT of the API, so these tests are the consumer's side of the
contract in docs/api-contract.md: the SSE reading rules (S2, S6), the terminal events (S3), the
source list (S4), the error envelope (E1-E4), and the long silences (G6). No network is used: the
HTTP layer is `httpx.MockTransport`, and one section runs the REAL FastAPI app in-process so the
client is also checked against the server's own framing, not just against bytes written here.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.orchestration.llm import GENERATION_TIMEOUT_SECONDS
from src.orchestration.rag import RAGPipeline
from src.retrieval.pipeline import RankedChunk, RetrievalResult
from src.ui import client
from tests.support import make_chunk
from tests.ui.support import BASE, SSE_HEADERS, Chunks, api_returning, envelope, frame, stream_of


def collect(stream: client.AnswerStream) -> list[str]:
    return list(stream)


# --- the SSE reader --------------------------------------------------------------------------

AWKWARD_FRAGMENTS = [
    # (what the model produced, what the client must rebuild) -- the same cases as clause S6
    pytest.param(" A", " A", id="leading-space"),
    pytest.param("multi\nline", "multi\nline", id="lf"),
    pytest.param("unicode: Réponse ✓ 你好", "unicode: Réponse ✓ 你好", id="unicode"),
    pytest.param("crlf\r\nfragment", "crlf\nfragment", id="crlf"),
    pytest.param("bare\rcarriage", "bare\ncarriage", id="lone-cr"),
    pytest.param("inject\revent: error", "inject\nevent: error", id="cr-then-a-field-name"),
    pytest.param("a\r\rdata: forged", "a\n\ndata: forged", id="cr-pair-forges-an-event"),
    pytest.param("ends-with-lf\n", "ends-with-lf\n", id="trailing-lf"),
    pytest.param("sep: after", "sep: after", id="u2028-is-not-a-line-end"),
    pytest.param("v\x0bf\x0cn\x85s\x1ce", "v\x0bf\x0cn\x85s\x1ce", id="other-separators"),
]


@pytest.mark.parametrize(("fragment", "rebuilt"), AWKWARD_FRAGMENTS)
def test_every_awkward_fragment_is_rebuilt_exactly(fragment, rebuilt):
    assert list(client.iter_sse_data([frame(fragment, "[DONE]")])) == [rebuilt, "[DONE]"]
    # requires the reader to follow the SSE line rules, not str.splitlines(): iter_lines() on
    # these very bodies loses the text after U+2028, \x0b and \x85 without any error


@pytest.mark.parametrize(("fragment", "rebuilt"), AWKWARD_FRAGMENTS)
def test_delivery_in_single_bytes_changes_nothing(fragment, rebuilt):
    body = frame(fragment, "[DONE]")
    one_byte_at_a_time = [body[i : i + 1] for i in range(len(body))]
    assert list(client.iter_sse_data(one_byte_at_a_time)) == [rebuilt, "[DONE]"]
    # requires a multi-byte character, and a CR separated from its LF, to survive being split
    # across network chunks


def test_a_crlf_split_between_two_chunks_is_one_line_end():
    chunks = [b"data: a\r", b"\ndata: b\r\n\r\n"]
    assert list(client.iter_sse_data(chunks)) == ["a\nb"]
    # requires a trailing CR to wait for the next chunk, which may begin with its LF


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(b"data: a\n\ndata: b\n\n", ["a", "b"], id="two-events"),
        pytest.param(b"data: x\ndata: y\n\n", ["x\ny"], id="data-lines-are-joined-with-lf"),
        pytest.param(b"data:  two\n\n", [" two"], id="only-one-leading-space-is-removed"),
        pytest.param(b"data:none\n\n", ["none"], id="no-space-after-the-colon"),
        pytest.param(b"data\n\n", [""], id="a-bare-field-name-is-an-empty-value"),
        pytest.param(b": comment\ndata: a\n\n", ["a"], id="comments-are-ignored"),
        pytest.param(b"event: x\nid: 1\nretry: 5\ndata: a\n\n", ["a"], id="other-fields-ignored"),
        pytest.param(b"data: a\n\ndata: unfinished\n", ["a"], id="an-unfinished-event-is-dropped"),
        pytest.param(b"data: a\r\rdata: b\r\r", ["a", "b"], id="a-lone-cr-ends-a-line"),
        pytest.param(b"data: a\r\rdata: b\r", ["a"], id="a-final-cr-ends-a-line-not-the-event"),
        pytest.param(b"data: \xff ok\n\n", ["� ok"], id="invalid-utf8-is-replaced-not-fatal"),
        pytest.param(b"data: a\r\n\r\ndata: b\r\n\r\n", ["a", "b"], id="crlf-ends-a-line"),
        pytest.param(b"\n\n\n", [], id="blank-lines-alone-make-no-event"),
        pytest.param(b"", [], id="an-empty-body"),
    ],
)
def test_the_reader_follows_the_sse_rules(body, expected):
    assert list(client.iter_sse_data([body])) == expected


# --- the source list -------------------------------------------------------------------------


def test_the_source_list_is_parsed_into_numbered_entries():
    data = (
        "\n\nSources:\n[1] About Insurellm (company/about.md)"
        "\n[2] Avery Lancaster (employees/Avery Lancaster.md)"
    )
    assert client.parse_sources(data) == [
        client.Source(1, "About Insurellm", "company/about.md"),
        client.Source(2, "Avery Lancaster", "employees/Avery Lancaster.md"),
    ]


def test_a_title_containing_parentheses_keeps_them():
    data = "\n\nSources:\n[1] Contract (Draft) v2 (contracts/draft.md)"
    assert client.parse_sources(data) == [
        client.Source(1, "Contract (Draft) v2", "contracts/draft.md")
    ]
    # requires the LAST parenthesised group to be the path, as the API writes `title (path)`


def test_a_source_line_in_an_unexpected_format_is_skipped_not_fatal():
    data = "\n\nSources:\n[1] Good (a.md)\nsomething else entirely\n[3] Also good (c.md)"
    assert [source.number for source in client.parse_sources(data)] == [1, 3]


def test_a_source_has_a_readable_label():
    assert client.Source(2, "Title", "dir/file.md").label == "[2] Title (dir/file.md)"


# --- reading an answer -----------------------------------------------------------------------


def test_a_completed_answer_yields_its_text_and_keeps_its_sources_apart():
    http = api_returning(
        stream_of("Acme ", "was founded", "\n\nSources:\n[1] About (company/about.md)")
    )
    stream = client.ask(BASE, "Who founded Acme?", 3, http_client=http)
    assert collect(stream) == ["Acme ", "was founded"]
    # requires the source list to be held back from the text fragments
    assert stream.text == "Acme was founded"
    assert stream.sources == [client.Source(1, "About", "company/about.md")]
    assert stream.complete is True
    assert stream.error is None


def test_an_answer_with_no_sources_is_still_complete():
    refusal = "I don't have enough information in the provided documents to answer that."
    stream = client.ask(BASE, "q", 3, http_client=api_returning(stream_of(refusal)))
    assert collect(stream) == [refusal]
    assert stream.sources == []
    assert stream.complete is True


def test_an_in_band_error_keeps_the_partial_text_and_reports_the_message():
    http = api_returning(stream_of("Partial ", "answer ", ending="[ERROR] timed out"))
    stream = client.ask(BASE, "q", 3, http_client=http)
    assert collect(stream) == ["Partial ", "answer "]
    assert stream.text == "Partial answer "
    assert stream.error == "timed out"
    assert stream.complete is False
    # requires a failure after the 200 to be reported, without discarding what already arrived


def test_an_in_band_error_alone_is_a_stream_with_no_text():
    http = api_returning(stream_of(ending="[ERROR] qdrant down"))
    stream = client.ask(BASE, "q", 3, http_client=http)
    assert collect(stream) == []
    assert stream.error == "qdrant down"


def test_a_stream_that_ends_without_a_terminal_event_is_a_failure():
    http = api_returning(stream_of("half an ans", ending=None))
    stream = client.ask(BASE, "q", 3, http_client=http)
    assert collect(stream) == ["half an ans"]
    assert stream.complete is False
    assert stream.error == client.TRUNCATED_MESSAGE
    # requires contract rule 4: no terminal event means a dropped connection, whatever the status


def test_nothing_after_done_is_read():
    body = frame("a", "[DONE]", "late")
    stream = client.ask(
        BASE,
        "q",
        3,
        http_client=api_returning(httpx.Response(200, headers=SSE_HEADERS, content=body)),
    )
    assert collect(stream) == ["a"]


def test_the_connection_is_closed_when_the_answer_ends_and_when_it_is_abandoned():
    finished = Chunks([frame("a", "[DONE]")])
    stream = client.ask(
        BASE,
        "q",
        3,
        http_client=api_returning(httpx.Response(200, headers=SSE_HEADERS, stream=finished)),
    )
    collect(stream)
    assert finished.closed is True

    abandoned = Chunks([frame("a"), frame("b"), frame("c")])
    stream = client.ask(
        BASE,
        "q",
        3,
        http_client=api_returning(httpx.Response(200, headers=SSE_HEADERS, stream=abandoned)),
    )
    fragments = iter(stream)
    # held in a variable: a discarded generator would be closed at once by CPython, which would
    # hide exactly the difference between "closed by the owner" and "closed by chance"
    next(fragments)
    assert abandoned.closed is False
    stream.close()
    assert abandoned.closed is True
    # requires an explicit close() to release the connection at once, not whenever a garbage
    # collection happens to run (the lesson of gap G1 in docs/api-contract.md)
    stream.close()
    # requires close() to be safe to call again


def test_a_dropped_connection_mid_answer_is_reported_with_the_text_so_far():
    broken = Chunks([frame("Acme ", "was")], then=httpx.RemoteProtocolError("peer closed"))
    stream = client.ask(
        BASE,
        "q",
        3,
        http_client=api_returning(httpx.Response(200, headers=SSE_HEADERS, stream=broken)),
    )
    seen = []
    with pytest.raises(client.ChatError) as problem:
        for fragment in stream:
            seen.append(fragment)
    assert seen == ["Acme ", "was"]
    assert stream.text == "Acme was"
    assert problem.value.kind == "connection_lost"
    assert broken.closed is True


def test_a_silence_longer_than_the_read_timeout_is_reported_as_a_timeout():
    silent = Chunks([], then=httpx.ReadTimeout("no bytes"))
    stream = client.ask(
        BASE,
        "q",
        3,
        http_client=api_returning(httpx.Response(200, headers=SSE_HEADERS, stream=silent)),
    )
    with pytest.raises(client.ChatError) as problem:
        collect(stream)
    assert problem.value.kind == "timeout"
    assert str(int(client.READ_TIMEOUT_SECONDS)) in problem.value.message


# --- the request -----------------------------------------------------------------------------


def test_the_question_is_posted_as_json_to_the_chat_route():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return stream_of("ok")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    collect(client.ask(BASE + "/", "Who founded Insurellm?", 2, http_client=http))
    request = seen["request"]
    assert (request.method, str(request.url)) == ("POST", "http://api.test/chat")
    # requires exactly /chat: a trailing slash would be a 307 redirect (clause E6)
    assert json.loads(request.content) == {"query": "Who founded Insurellm?", "k": 2}
    assert request.headers["accept"] == "text/event-stream"


def test_an_injected_http_client_is_left_open_for_its_owner():
    http = api_returning(stream_of("ok"))
    collect(client.ask(BASE, "q", 3, http_client=http))
    assert http.is_closed is False


def test_the_default_client_waits_longer_than_the_api_does():
    default = client.new_http_client()
    try:
        assert default.timeout.read == client.READ_TIMEOUT_SECONDS
        assert default.timeout.connect == client.CONNECT_TIMEOUT_SECONDS
    finally:
        default.close()
    assert client.READ_TIMEOUT_SECONDS > GENERATION_TIMEOUT_SECONDS
    # requires the page to give up AFTER the API does, so the API's own in-band
    # "[ERROR] timed out" arrives first instead of the page's generic message


# --- errors before any stream ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "kind", "must_mention"),
    [
        pytest.param(
            envelope(400, "invalid_json", "the request body is not valid JSON"),
            "bad_request",
            "not valid JSON",
            id="400",
        ),
        pytest.param(
            envelope(422, "validation_error", "body.k: Input should be greater than 0"),
            "bad_request",
            "body.k",
            id="422",
        ),
        pytest.param(
            envelope(500, "internal_error", "an unexpected error occurred"),
            "server",
            "internal error",
            id="500",
        ),
        pytest.param(
            envelope(504, "upstream_timeout", "the request timed out"),
            "gateway_timeout",
            "timed out",
            id="504",
        ),
        pytest.param(
            httpx.Response(404, json={"error": {"code": "not_found", "message": "Not Found"}}),
            "unexpected",
            "404",
            id="404",
        ),
        pytest.param(
            httpx.Response(502, text="<html>Bad gateway</html>"),
            "unexpected",
            "502",
            id="a-proxy-page-with-no-envelope",
        ),
    ],
)
def test_an_http_error_becomes_a_chat_error_before_any_streaming(response, kind, must_mention):
    with pytest.raises(client.ChatError) as problem:
        client.ask(BASE, "q", 3, http_client=api_returning(response))
    assert problem.value.kind == kind
    assert must_mention in problem.value.message


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")],
    ids=["refused", "connect-timeout"],
)
def test_an_unreachable_api_names_the_address_it_tried(failure):
    with pytest.raises(client.ChatError) as problem:
        client.ask(BASE, "q", 3, http_client=api_returning(failure))
    assert problem.value.kind == "unreachable"
    assert BASE in problem.value.message


# --- /health ---------------------------------------------------------------------------------


def test_a_healthy_api_is_reported_ok():
    health = client.check_health(
        BASE, http_client=api_returning(httpx.Response(200, json={"status": "ok"}))
    )
    assert health.ok is True


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(httpx.ConnectError("refused"), id="refused"),
        pytest.param(httpx.Response(503, text="down"), id="503"),
        pytest.param(httpx.Response(200, text="not json"), id="not-json"),
        pytest.param(httpx.Response(200, json={"status": "degraded"}), id="not-ok"),
    ],
)
def test_anything_but_a_clean_ok_is_reported_down_with_a_reason(response):
    health = client.check_health(BASE, http_client=api_returning(response))
    assert health.ok is False
    assert health.detail


# --- wording ---------------------------------------------------------------------------------


def test_a_generation_timeout_gets_advice_and_any_other_error_is_passed_through():
    assert "smaller k" in client.explain_stream_error("timed out")
    assert "qdrant down" in client.explain_stream_error("qdrant down")
    assert client.explain_stream_error(client.TRUNCATED_MESSAGE) == client.TRUNCATED_MESSAGE
    # requires the page's own sentence to be shown as it is, not wrapped in "cut off: ..."


# --- against the REAL API, in-process --------------------------------------------------------


class ScriptedRetriever:
    def retrieve(self, query, k):
        chunks = tuple(
            RankedChunk(
                chunk=make_chunk(f"doc{rank}.md", 0, f"text {rank}"),
                final_rank=rank,
                relevance_score=1.0,
                hybrid_score=1.0,
                hybrid_rank=rank,
                vector_rank=rank,
                graph_rank=None,
                vector_score=1.0,
            )
            for rank in range(1, k + 1)
        )
        return RetrievalResult(query=query, results=chunks)


class ScriptedLLM:
    def __init__(self, fragments):
        self.fragments = fragments

    def generate(self, prompt, temperature, max_tokens, *, system=None):
        return "".join(self.fragments)

    def stream(self, prompt, temperature, *, system=None):
        yield from self.fragments


_ABSENT = object()


@pytest.fixture
def real_api():
    """The real FastAPI app answering through an httpx client, with a scripted model."""
    previous = getattr(app.state, "rag", _ABSENT)
    # remembered exactly as found -- including "not there at all" -- because every test in the
    # suite shares the one module-level app

    def serve(fragments: list[str]) -> httpx.Client:
        app.state.rag = RAGPipeline(ScriptedRetriever(), ScriptedLLM(fragments))
        return TestClient(app, base_url="http://testserver")

    yield serve
    if previous is _ABSENT:
        if hasattr(app.state, "rag"):
            del app.state.rag
    else:
        app.state.rag = previous


@pytest.mark.parametrize(("fragment", "rebuilt"), AWKWARD_FRAGMENTS)
def test_the_client_agrees_with_the_real_servers_own_framing(fragment, rebuilt, real_api):
    stream = client.ask("http://testserver", "hi", 2, http_client=real_api([fragment]))
    assert collect(stream) == [rebuilt]
    # requires the REAL server's output, for text the model could really emit, to be rebuilt
    # exactly: this is the consumer's half of the contract P7-M7 wrote down
    assert stream.complete is True
    assert [source.number for source in stream.sources] == [1, 2]
    assert stream.sources[0].path == "doc1.md"


def test_a_real_validation_error_reaches_the_client_as_a_chat_error(real_api):
    http = real_api(["x"])
    with pytest.raises(client.ChatError) as problem:
        client.ask("http://testserver", "hi", 0, http_client=http)
    assert problem.value.kind == "bad_request"
    assert "body.k" in problem.value.message
    # requires the REAL 422 envelope's message, which names the failing field, to be shown
