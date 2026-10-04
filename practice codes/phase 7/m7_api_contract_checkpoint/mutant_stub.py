"""stub_server.py with ONE deliberate bug switched on, chosen by the MUTANT environment variable.

Used by run_mutants.py to prove that check_api_contract.py can actually FAIL: a checker that has
never been seen to go red proves nothing when it stays green. Each mutant breaks the REAL app
(src.api.main) in one specific way, after import and before the first request.

One mutant is not a bug but a candidate FIX: "closes-abandoned-streams" is the repair for known
gap G1 (docs/api-contract.md), tried here so that the recommendation is verified, not guessed.

Run:  MUTANT=old-framing python -m uvicorn mutant_stub:app --port 8010   (see stub_server.py)
"""

import os
import re

from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from stub_server import app

import src.api.main as api


def old_framing() -> None:
    """The original defect: only LF counts as a line break, so a lone CR passes through."""
    api._SSE_LINE_END = re.compile("\n")


def splitlines_fix() -> None:
    """The obvious-but-wrong repair: str.splitlines() also splits on U+2028 and drops a newline."""

    class SplitLines:
        @staticmethod
        def split(text: str) -> list[str]:
            return text.splitlines() or [""]

    api._SSE_LINE_END = SplitLines()  # type: ignore[assignment]


def buffers_everything() -> None:
    """Collects the whole answer before sending any of it, instead of streaming."""
    original = api._sse_events

    def buffered(rag, query, k):
        events = list(original(rag, query, k))
        yield from events

    api._sse_events = buffered


def blocks_the_loop() -> None:
    """Iterates the blocking generator ON the event loop, instead of in a worker thread.

    An async iterable is handed to StreamingResponse as is, while a plain generator is moved to a
    thread -- so wrapping the generator in an `async def` makes every blocking read of the model
    stall every other request (docs/35, C4: why _sse_events is a plain `def`)."""
    original = api._sse_events

    async def on_the_loop(rag, query, k):
        for event in original(rag, query, k):
            yield event

    api._sse_events = on_the_loop


def default_http_errors() -> None:
    """Puts FastAPI's stock {"detail": ...} body back for 404 / 405."""
    app.exception_handlers[StarletteHTTPException] = http_exception_handler


def default_422_docs() -> None:
    """Stops declaring the 422 envelope, so FastAPI documents its own stock 422 again."""
    for route in app.routes:
        if getattr(route, "path", None) == "/chat":
            route.responses.pop(422, None)
    app.openapi_schema = None


def cors_on() -> None:
    """Allows every origin: the 'make the browser error go away' change."""
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )


def closes_abandoned_streams() -> None:
    """The candidate fix for G1: close the generator chain after the response ends, however it
    ended. Starlette runs a response's `background` task after the stream task is cancelled by a
    disconnect, and that cancellation waits for the in-flight next() to finish, so close() never
    races a running generator."""

    class ClosingStreamingResponse(api.StreamingResponse):
        def __init__(self, content, *args, **kwargs):
            super().__init__(content, *args, background=BackgroundTask(content.close), **kwargs)

    api.StreamingResponse = ClosingStreamingResponse


MUTANTS = {
    "old-framing": old_framing,
    "splitlines-fix": splitlines_fix,
    "buffers-everything": buffers_everything,
    "blocks-the-loop": blocks_the_loop,
    "default-http-errors": default_http_errors,
    "default-422-docs": default_422_docs,
    "cors-on": cors_on,
    "closes-abandoned-streams": closes_abandoned_streams,
}

_selected = os.environ.get("MUTANT", "")
if _selected:
    MUTANTS[_selected]()
