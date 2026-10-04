"""Everything the chat page needs from the API, with no Streamlit in it (P8-M1).

This is the first in-repo CLIENT of the API, so it is written against docs/api-contract.md: the
response is a Server-Sent Events stream read by the WHATWG rules (S2, S6), it ends with exactly one
terminal event, `[DONE]` or `[ERROR] <message>` (S3), a completed answer ends with its numbered
source list (S4), and anything that goes wrong before the stream starts is a JSON error envelope
(E1-E4). Keeping this out of app.py means it can be tested without a Streamlit runtime.
"""

from __future__ import annotations

import codecs
import itertools
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

import httpx

CONNECT_TIMEOUT_SECONDS = 5.0
# how long to wait for the API to accept a connection; it runs on this host or this network
READ_TIMEOUT_SECONDS = 330.0
# how long the API may stay SILENT before the page gives up. The API itself gives up on the model
# after GENERATION_TIMEOUT_SECONDS (300, src/orchestration/llm.py) and reports it as an in-band
# `[ERROR] timed out`, so waiting a little longer lets that message arrive first --
# tests/ui/test_client.py enforces the ordering. The API sends nothing at all before the model's
# first fragment (gap G6), and on this CPU-only host that wait is minutes.
HEALTH_TIMEOUT_SECONDS = 2.0
# /health answers in milliseconds, so a short wait is enough to call the API down

TRUNCATED_MESSAGE = "The connection ended before the answer was finished."
SOURCES_PREFIX = "\n\nSources:\n"
DONE = "[DONE]"
ERROR_PREFIX = "[ERROR]"

_LINE_END = re.compile(r"\r\n|\r|\n")
# the three line terminators the SSE grammar defines, CRLF first so it counts as ONE: not
# str.splitlines(), which also splits on U+2028, \x0b, \x85 and more (httpx's iter_lines() does too)
_SOURCE_LINE = re.compile(r"\[(\d+)\] (.+) \((.+)\)")
# `[n] title (path)`, as the API writes it; the LAST parenthesised group is the path

ErrorKind = Literal[
    "unreachable",
    "timeout",
    "connection_lost",
    "bad_request",
    "server",
    "gateway_timeout",
    "unexpected",
]


class ChatError(Exception):
    """Something went wrong talking to the API; `message` is written for the person at the page."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        # what sort of failure this is, so a caller can branch without parsing the message
        self.message = message
        # a full sentence for the page to show


@dataclass(frozen=True)
class Source:
    """One entry of the source list that ends a completed answer."""

    number: int
    title: str
    path: str

    @property
    def label(self) -> str:
        return f"[{self.number}] {self.title} ({self.path})"
        # the same shape the API sends, ready to show


@dataclass(frozen=True)
class Health:
    ok: bool
    detail: str


def iter_sse_data(chunks: Iterable[bytes]) -> Iterator[str]:
    """The data of every complete SSE event in `chunks`, in order.

    Follows the WHATWG "event stream interpretation": lines end with CRLF, CR or LF; a blank line
    ends an event; `data:` lines are joined with LF after removing exactly one leading space;
    comment lines and every other field are ignored; an event the stream never finished is dropped.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    # turns bytes into text without breaking a multi-byte character that a network chunk cut in two,
    # and without ever raising on bytes that are not valid UTF-8
    buffer = ""
    # text received but not yet cut into lines
    data_lines: list[str] = []
    # the `data:` lines of the event being built
    for chunk in itertools.chain(chunks, [None]):
        # None marks the end of the stream: one last pass with nothing more to wait for
        at_end = chunk is None
        buffer += decoder.decode(b"" if chunk is None else chunk, final=at_end)
        # adds this chunk's text to what the last chunk left over
        held = "\r" if buffer.endswith("\r") and not at_end else ""
        # a trailing CR may be the first half of a CRLF whose LF is in the next chunk -- unless
        # the stream has ended, when it is simply a line end
        lines = _LINE_END.split(buffer[: len(buffer) - len(held)])
        # cuts the text into lines on CRLF, CR or LF, and on nothing else
        buffer = lines.pop() + held
        # the last piece is an unfinished line: it waits for the next chunk
        for line in lines:
            if line == "":
                if data_lines:
                    yield "\n".join(data_lines)
                    # a blank line completes the event, which is handed over
                data_lines = []
            elif not line.startswith(":"):
                name, _, value = line.partition(":")
                if name == "data":
                    data_lines.append(value.removeprefix(" "))
                    # exactly one space after the colon is dropped, never more


def parse_sources(data: str) -> list[Source]:
    """The entries of the source-list event; a line in any other shape is skipped, not fatal."""
    sources = []
    for line in data.removeprefix(SOURCES_PREFIX).split("\n"):
        match = _SOURCE_LINE.fullmatch(line)
        if match:
            sources.append(Source(int(match.group(1)), match.group(2), match.group(3)))
    return sources


def explain_stream_error(message: str) -> str:
    """The text for the page when a stream ended with `[ERROR] <message>` or just stopped."""
    if message == TRUNCATED_MESSAGE:
        return message
        # already a full sentence written for the page
    if "timed out" in message:
        return (
            "The model did not answer within the time limit. On this CPU-only setup that happens "
            "when the prompt is long; try a smaller k, or ask again."
        )
    return f"The answer was cut off: {message}"


class AnswerStream:
    """One answer being read from the API.

    Iterate it ONCE to receive the answer text fragment by fragment, then read what the stream left
    behind: `text` (everything received so far, also after a failure), `sources`, `complete` (the
    API said `[DONE]`) and `error` (the API said `[ERROR]`, or the stream just stopped).
    Call close() when done with it, including after an interruption: it releases the connection
    at once instead of waiting for a garbage collection (gap G1, docs/api-contract.md).
    """

    def __init__(self, response: httpx.Response, owned_client: httpx.Client | None) -> None:
        self._response = response
        # the open HTTP response whose body is the event stream
        self._owned_client = owned_client
        # the client to close along with it, or None when the caller supplied (and owns) the client
        self.text = ""
        self.sources: list[Source] = []
        self.error: str | None = None
        self.complete = False

    def __iter__(self) -> Iterator[str]:
        try:
            for data in iter_sse_data(self._response.iter_bytes()):
                if data == DONE:
                    self.complete = True
                    return
                    # the successful end: nothing after it is read
                if data.startswith(ERROR_PREFIX):
                    self.error = data.removeprefix(ERROR_PREFIX).strip()
                    return
                    # the failed end, reported inside the 200
                if data.startswith(SOURCES_PREFIX):
                    self.sources = parse_sources(data)
                    continue
                    # the source list is kept apart from the answer text
                self.text += data
                yield data
                # hands one fragment of answer text to the page
            self.error = TRUNCATED_MESSAGE
            # the body ended with no terminal event: a dropped connection, whatever the status said
        except httpx.ReadTimeout as silence:
            raise ChatError(
                "timeout",
                f"The API sent nothing for {READ_TIMEOUT_SECONDS:.0f} s, so the page gave up.",
            ) from silence
        except httpx.TransportError as lost:
            raise ChatError(
                "connection_lost", "The connection to the API dropped mid-answer."
            ) from lost
        finally:
            self.close()
            # runs on a normal end, a failure, and when the generator is closed early

    def close(self) -> None:
        """Releases the connection. Safe to call more than once."""
        self._response.close()
        if self._owned_client is not None:
            self._owned_client.close()


def new_http_client() -> httpx.Client:
    """A client that waits long enough for a CPU-only model (see READ_TIMEOUT_SECONDS)."""
    return httpx.Client(
        timeout=httpx.Timeout(10.0, connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS)
    )


def ask(
    base_url: str, query: str, k: int, *, http_client: httpx.Client | None = None
) -> AnswerStream:
    """Sends the question and returns the open answer stream.

    The request goes out now and the response status is checked now, so a refused connection or an
    HTTP error (400, 422, 500, 504, ...) is raised here as a ChatError, before any streaming starts.
    `http_client` is for tests; a client passed in is left open for its owner.
    """
    owned = http_client is None
    http = new_http_client() if http_client is None else http_client
    try:
        request = http.build_request(
            "POST",
            base_url.rstrip("/") + "/chat",
            json={"query": query, "k": k},
            headers={"Accept": "text/event-stream"},
        )
        response = http.send(request, stream=True)
        # sends the question and returns as soon as the headers arrive, leaving the body unread
    except (httpx.ConnectError, httpx.ConnectTimeout) as refused:
        if owned:
            http.close()
        raise ChatError(
            "unreachable",
            f"Cannot reach the API at {base_url}. Is it running? (uvicorn src.api.main:app)",
        ) from refused
    except httpx.TimeoutException as silence:
        if owned:
            http.close()
        raise ChatError("timeout", "The API did not answer in time.") from silence
    except httpx.TransportError as lost:
        if owned:
            http.close()
        raise ChatError("connection_lost", f"The connection to {base_url} failed: {lost}") from lost
    if response.status_code != 200:
        problem = _problem_from(response)
        response.close()
        if owned:
            http.close()
        raise problem
    return AnswerStream(response, http if owned else None)


def _problem_from(response: httpx.Response) -> ChatError:
    """The ChatError for a non-200 answer, using the API's error envelope when there is one."""
    response.read()
    status = response.status_code
    try:
        message = str(response.json()["error"]["message"])
        # the envelope every error of this API uses: {"error": {"code": ..., "message": ...}}
    except (ValueError, KeyError, TypeError):
        message = ""
        # not an envelope -- for example an HTML error page from a proxy in front of the API
    if status in (400, 422):
        return ChatError(
            "bad_request", f"The API rejected the question: {message or f'HTTP {status}'}"
        )
    if status == 500:
        return ChatError(
            "server", "The API had an internal error (HTTP 500); its log has the details."
        )
    if status == 504:
        return ChatError("gateway_timeout", f"The API reported a timeout: {message or 'HTTP 504'}")
    return ChatError("unexpected", f"Unexpected answer from the API (HTTP {status}).")


def check_health(base_url: str, *, http_client: httpx.Client | None = None) -> Health:
    """Asks GET /health whether the API is up; never raises."""
    owned = http_client is None
    http = httpx.Client(timeout=HEALTH_TIMEOUT_SECONDS) if http_client is None else http_client
    try:
        response = http.get(base_url.rstrip("/") + "/health")
    except httpx.HTTPError as failure:
        return Health(False, f"cannot connect ({type(failure).__name__})")
    finally:
        if owned:
            http.close()
    if response.status_code != 200:
        return Health(False, f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return Health(False, "the answer was not JSON")
    if not isinstance(body, dict) or body.get("status") != "ok":
        return Health(False, "the status is not ok")
    return Health(True, "ok")
