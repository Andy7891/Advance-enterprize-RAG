"""Fakes shared by the UI tests: an httpx layer that answers like the API, with no network."""

from __future__ import annotations

import re
from collections.abc import Iterator

import httpx

BASE = "http://api.test"
# the address every mocked request is sent to; nothing listens there

SSE_HEADERS = {"content-type": "text/event-stream; charset=utf-8"}
LINE_END = re.compile(r"\r\n|\r|\n")


def frame(*fragments: str) -> bytes:
    """The bytes the API sends for these text fragments: the server's own framing rule."""
    events = [
        "data: " + "\ndata: ".join(LINE_END.split(fragment)) + "\n\n" for fragment in fragments
    ]
    return "".join(events).encode("utf-8")


class Chunks(httpx.SyncByteStream):
    """A response body delivered in the given pieces, remembering whether it was closed."""

    def __init__(self, pieces: list[bytes], then: BaseException | None = None):
        self.pieces = pieces
        # the network chunks, in order
        self.then = then
        # an exception to raise after the last chunk, standing in for a dropped connection
        self.closed = False
        # True once httpx has closed this body

    def __iter__(self) -> Iterator[bytes]:
        yield from self.pieces
        if self.then is not None:
            raise self.then

    def close(self) -> None:
        self.closed = True


def api_returning(response: httpx.Response | Exception) -> httpx.Client:
    """An httpx client whose every request is answered with `response` (or raises it)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


def stream_of(*fragments: str, ending: str | None = "[DONE]") -> httpx.Response:
    """A 200 event stream carrying these fragments and then an optional terminal event."""
    pieces = list(fragments) + ([ending] if ending is not None else [])
    return httpx.Response(200, headers=SSE_HEADERS, content=frame(*pieces))


def envelope(status: int, code: str, message: str) -> httpx.Response:
    """An error response in the API's envelope shape."""
    return httpx.Response(status, json={"error": {"code": code, "message": message}})
