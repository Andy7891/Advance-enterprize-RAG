"""FastAPI entrypoint. RAG endpoints (ingest/query) are added in later steps."""

import json
import logging
import re
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.config import settings
from src.embedding.embedder import Embedder, build_ollama_client
from src.graph.setup import build_driver
from src.orchestration.llm import build_llm
from src.orchestration.rag import RAGPipeline
from src.retrieval.graph_retriever import GraphRetriever
from src.retrieval.graph_store import GraphStore
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.pipeline import RetrievalPipeline
from src.retrieval.reranker import IdentityReranker, Reranker
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

logger = logging.getLogger(__name__)

_LOG_RECORD_RESERVED_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
}
# every attribute a plain LogRecord already has, computed from a real instance rather than
# hand-copied from documentation -- used below to separate a log call's own extra={} fields
# from the record's own built-in ones


class _JsonFormatter(logging.Formatter):
    """Formats every log record this app emits as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # merges any extra={...} fields a log call attached (e.g. method/path/duration_ms)
        for key, value in vars(record).items():
            if key not in _LOG_RECORD_RESERVED_FIELDS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
            # covers logger.exception(...) calls (_handle_unexpected_error), unchanged since P7-M3
        return json.dumps(payload, default=str)
        # default=str covers any extra value that is not natively JSON-serializable


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])
# every logger.*() call project-wide that reaches the root logger -- including P7-M3's own
# existing logger.warning/.exception calls, unchanged -- now emits this same JSON shape.
# Verified directly (not assumed) that this survives real `uvicorn src.api.main:app` startup:
# uvicorn configures its OWN named loggers (uvicorn, uvicorn.access) independently rather
# than overwriting the root logger's handlers.

SLOW_REQUEST_MS = 5000.0
# a request logged at WARNING instead of INFO once it crosses this bound -- chosen well under
# this project's own repeatedly-measured real generation latency (108-300+s, P6-M6/M7/P7-M2),
# so a genuinely slow /chat request is clearly flagged rather than lost among fast ones


class RequestLoggingMiddleware:
    """Raw ASGI middleware, not FastAPI's own @app.middleware("http"): verified directly that
    the latter's call_next returns almost immediately for a StreamingResponse (long before a
    real stream finishes), which would log /chat's own latency as near-instant. Wrapping
    `send` directly and watching for the real final response message measures the REAL, full
    duration instead."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        # the next layer in the ASGI chain, exactly as any ASGI middleware wraps its app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # covers lifespan (and any future websocket) scopes -- nothing to log as a request
            await self._app(scope, receive, send)
            return
        started = time.perf_counter()
        # the one, real wall-clock start point this whole request is measured from
        status_holder = {"status": 500}
        # defaults to 500, only overwritten below on a NORMAL completion -- for the bare
        # Exception path (verified: it propagates THROUGH this middleware as a raised
        # exception, not a normal send, since ServerErrorMiddleware sits OUTSIDE any
        # app.add_middleware(...) layer and sends its own response on its own send), this
        # default is what gets logged, matching _handle_unexpected_error's real status_code=500

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                # captures the REAL status a specific handler (400/422/504) chose
            await send(message)

        try:
            await self._app(scope, receive, wrapped_send)
        except Exception:
            self._log(scope, status_holder["status"], started)
            raise
            # re-raises so ServerErrorMiddleware (outside this middleware) still sends its
            # own response and still does its own re-raise for the real server's error log
        else:
            self._log(scope, status_holder["status"], started)

    def _log(self, scope: Scope, status: int, started: float) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        # the real, full request duration -- including a streamed response's entire body
        level = logging.WARNING if duration_ms >= SLOW_REQUEST_MS else logging.INFO
        # escalates severity for a genuinely slow request, instead of uniform INFO for all
        logger.log(
            level,
            f"{scope['method']} {scope['path']} {status} {duration_ms}ms",
            extra={
                "method": scope["method"],
                "path": scope["path"],
                "status_code": status,
                "duration_ms": duration_ms,
            },
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Builds the real RAGPipeline once, at startup, and tears down its Neo4j driver at
    shutdown -- real clients/drivers are expensive and meant to be long-lived, not rebuilt
    on every request (see docs/34's own C1/C4 for why the app object and its dependencies
    are kept separate from how/when they're actually constructed)."""
    embedder = Embedder(build_ollama_client(settings.ollama_url), ollama_url=settings.ollama_url)
    # the real query embedder, talking to the configured Ollama instance
    vector = QdrantVectorStore(
        build_qdrant_client(settings.qdrant_url), embedder, collection="chunks"
    )
    # the real vector store, over the already-ingested "chunks" collection
    driver = build_driver()
    # the real Neo4j driver, reading its own connection settings at call time
    try:
        # everything from here on can fail (GraphStore checks the schema, GraphRetriever loads
        # every entity name), and the driver above already exists -- so it must be closed on
        # that path too, not only after a successful yield
        store = GraphStore(driver)
        hybrid = HybridRetriever(vector, GraphRetriever(store))
        # the real hybrid retriever: vector search + graph traversal, fused by rank

        reranker = (
            Reranker.from_settings() if settings.cohere_api_key.strip() else IdentityReranker()
        )
        # a real Cohere reranker when a key is configured, else a no-key stand-in -- mirrors
        # this project's own established live-test fallback (tests/retrieval/test_pipeline.py),
        # so the API can run and demo without requiring a paid key

        retrieval = RetrievalPipeline(hybrid, reranker)
        llm = build_llm(settings.ollama_url)
        # the real LocalLLM, with its own generous GENERATION_TIMEOUT_SECONDS (not the probe's)
        app.state.rag = RAGPipeline(retrieval, llm)
        # stored on app.state so every request reuses the SAME long-lived pipeline, not a new one

        yield
        # the app serves requests for as long as it's running, between this yield and the finally
    finally:
        driver.close()
        # releases the real Neo4j connection on shutdown -- and when startup itself failed


app = FastAPI(
    title="Advance RAG",
    version="0.1.0",
    description=(
        "A retrieval-augmented generation API over a hybrid vector+graph index. Answers are "
        "grounded in this project's own ingested corpus, with real, numbered source citations "
        "always appended -- see /chat's own description for the real streaming response shape."
    ),
    lifespan=lifespan,
)
app.add_middleware(RequestLoggingMiddleware)
# outside ExceptionMiddleware, inside ServerErrorMiddleware -- see RequestLoggingMiddleware's
# own docstring/comments for exactly what that placement means for each error path


class HealthResponse(BaseModel):
    """The real shape /health promises callers -- now enforced, not just assembled by hand."""

    status: str = Field(
        ..., description='Always "ok" -- this endpoint has no failure path of its own.'
    )
    qdrant_url: str = Field(
        ..., description="The Qdrant URL this running instance is configured to use."
    )
    neo4j_uri: str = Field(
        ..., description="The Neo4j URI this running instance is configured to use."
    )
    ollama_url: str = Field(
        ..., description="The Ollama URL this running instance is configured to use."
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Liveness check + confirms which backing services this instance is configured for."""
    return HealthResponse(
        status="ok",
        qdrant_url=settings.qdrant_url,
        neo4j_uri=settings.neo4j_uri,
        ollama_url=settings.ollama_url,
    )


class ErrorBody(BaseModel):
    """The `error` object inside every error response this API returns."""

    code: str = Field(
        ...,
        description=(
            "A stable, machine-readable code to branch on: `invalid_json` (400), "
            "`validation_error` (422), `not_found` (404), `method_not_allowed` (405), "
            "`http_error` (any other HTTP error the framework raises), `upstream_timeout` (504) "
            "or `internal_error` (500)."
        ),
    )
    message: str = Field(
        ..., description="A human-readable explanation, for logs and developers -- not to match on."
    )


class ErrorResponse(BaseModel):
    """The one JSON shape every error response in this app returns, regardless of cause.

    This model is both what /openapi.json declares for each error status and what builds the
    real body (see _error_envelope), so the documented shape and the sent one share one source.
    """

    error: ErrorBody = Field(..., description="What went wrong.")


def _error_envelope(code: str, message: str) -> dict:
    """The one JSON shape every error response in this app returns, regardless of cause."""
    return ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump()
    # built through the declared model, not a hand-written dict, so the two cannot drift apart


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Splits FastAPI's own default (422 for everything) into a real 400 vs 422 distinction:
    a body that fails to even parse as JSON is malformed (400); a well-formed body that fails
    a field's own rules -- a missing query, a k that isn't > 0 -- stays 422, the semantically
    correct code for "understood, but invalid" (verified empirically: FastAPI defaults BOTH
    cases to 422 today, confirmed via a live TestClient call before writing this handler)."""
    errors = exc.errors()
    if errors and all(error["type"] == "json_invalid" for error in errors):
        # every reported error is a parse failure -- the request itself is malformed
        return JSONResponse(
            status_code=400,
            content=_error_envelope("invalid_json", "the request body is not valid JSON"),
        )
    message = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)
    # joins every field-level problem into one readable string, e.g. "body.k: Input should..."
    return JSONResponse(status_code=422, content=_error_envelope("validation_error", message))


_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}
# the machine-readable `code` for the two routing errors Starlette raises on its own; any other
# HTTPException status falls back to the generic "http_error"


@app.exception_handler(StarletteHTTPException)
async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Puts the errors the FRAMEWORK raises (an unknown path's 404, a wrong method's 405) into
    the same envelope as every error this app raises itself. Registered for Starlette's
    HTTPException, not FastAPI's subclass of it: routing raises the Starlette one, so a handler
    for FastAPI's would never see a 404. Replaces FastAPI's default {"detail": ...} handler,
    and keeps exc.headers, which is where a 405's `Allow` header lives."""
    code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    # unknown statuses still get the envelope, just with the generic code
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope(code, str(exc.detail)),
        headers=exc.headers,
    )


@app.exception_handler(TimeoutError)
@app.exception_handler(httpx.TimeoutException)
async def _handle_timeout(request: Request, exc: Exception) -> JSONResponse:
    """Covers a real backing-service call that took too long. Only reachable for a timeout
    raised before any response has started -- a timeout inside /chat's own real work (query
    embedding, Qdrant, Neo4j, Ollama generation) happens inside _sse_events's generator,
    after headers are already sent, so it reaches that generator's own try/except instead
    (see its comment) and is reported as an in-stream [ERROR] event, not this handler."""
    logger.warning("upstream timeout on %s: %s", request.url.path, exc)
    # logged so a real, silent-otherwise hang is always visible to whoever runs this service
    return JSONResponse(
        status_code=504,
        content=_error_envelope("upstream_timeout", str(exc) or "the request timed out"),
    )


@app.exception_handler(Exception)
async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Catches anything not already handled above, in a path operation's own synchronous
    body -- the same streaming/headers-already-sent limitation as _handle_timeout applies
    here too, for the exact same reason."""
    logger.exception("unhandled error on %s", request.url.path)
    # logs the real traceback -- the response itself deliberately hides the details from
    # the caller, so an internal bug never leaks implementation specifics over the wire
    return JSONResponse(
        status_code=500,
        content=_error_envelope("internal_error", "an unexpected error occurred"),
    )


class ChatRequest(BaseModel):
    """The real request shape this endpoint validates before ever touching RAGPipeline."""

    model_config = ConfigDict(str_strip_whitespace=True)
    # trims surrounding whitespace BEFORE min_length is checked, so a blank query fails exactly
    # like "" does (a 422) instead of passing here, failing inside RAGPipeline's own stricter
    # check, and surfacing as a 200 stream carrying an in-band [ERROR] (verified: "   " did)

    query: str = Field(
        ...,
        min_length=1,
        description="The question to ask the RAG pipeline.",
        examples=["Who founded Insurellm?"],
        # one of this project's own established, reliably-retrieving labelled questions
        # (notes/step-6.6-rag-integration-tests.md) -- chosen so Swagger UI's own "Try it
        # out" prefill actually works well, not a question this project already knows misses
    )
    k: int = Field(default=3, gt=0, description="How many chunks to retrieve and ground on.")


CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "A real-time Server-Sent Events stream: one `data: <fragment>\\n\\n` event per real "
            "generated fragment, ending with `data: [DONE]\\n\\n` -- or, if a failure happens "
            "partway through (after this 200 has already started), `data: [ERROR] <message>\\n\\n` "
            "as the stream's final event instead of an HTTP error status (see docs/35, docs/36 for "
            "why: headers are already sent by the time a mid-stream failure can occur). A "
            "fragment that itself contains line breaks (CR, CRLF or LF) is sent as several "
            "`data:` lines, which an SSE client rejoins with a single `\\n` -- so a `\\r` or "
            "`\\r\\n` in the model's text reaches the client as `\\n`."
        ),
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "example": (
                    "data: Insurellm was founded by Avery Lancaster.\n\n"
                    "data: \ndata: \ndata: Sources:\ndata: [1] company/about.md (about.md)\n\n"
                    "data: [DONE]\n\n"
                ),
            }
        },
    },
    400: {
        "model": ErrorResponse,
        "description": "The request body is not valid JSON (see _handle_validation_error).",
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "invalid_json",
                        "message": "the request body is not valid JSON",
                    }
                }
            }
        },
    },
    422: {
        "model": ErrorResponse,
        "description": (
            "The body is valid JSON but breaks a field's own rules: a missing or blank `query`, "
            "a `k` that is not a whole number greater than 0, or a body that is not a JSON "
            "object (see _handle_validation_error). `message` lists every problem found, each "
            "as `body.<field>: <reason>`, separated by `; `."
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "validation_error",
                        "message": "body.k: Input should be greater than 0",
                    }
                }
            }
        },
    },
    500: {
        "model": ErrorResponse,
        "description": "An unexpected, unhandled error (see _handle_unexpected_error).",
        "content": {
            "application/json": {
                "example": {
                    "error": {"code": "internal_error", "message": "an unexpected error occurred"}
                }
            }
        },
    },
    504: {
        "model": ErrorResponse,
        "description": (
            "A backing-service call took too long, BEFORE any response had started (see "
            "_handle_timeout). A timeout during /chat's own real work -- query embedding, "
            "retrieval, generation -- happens AFTER this 200 response has already started, so it "
            "can never actually produce this status; it is reported as an in-band `[ERROR]` SSE "
            "event within the 200 response instead (see docs/36 C2/C4 for the full, verified "
            "reason this status is documented here but not reachable from this endpoint today)."
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": {"code": "upstream_timeout", "message": "the request timed out"}
                }
            }
        },
    },
}
# 422 IS declared here, with the real envelope: FastAPI's own default 422 documentation
# describes its stock {"detail": [...]} body, which this app has not sent since P7-M3 replaced
# the handler -- and a declared 422 entry is what makes FastAPI skip adding that default one.
# (This comment used to claim the default was "already correct"; P7-M7 checked the declared
# schema against the real body and found it was not.)


_SSE_LINE_END = re.compile(r"\r\n|\r|\n")
# the three line terminators the SSE grammar defines, CRLF first so it counts as ONE break --
# and only these three: str.splitlines() would also split on U+2028, \x0b, \x0c and more, which
# SSE does not treat as line ends, and it would silently drop a fragment's trailing newline


def _sse_events(rag: RAGPipeline, query: str, k: int) -> Iterator[str]:
    """Turns RAGPipeline.stream()'s real fragments into real Server-Sent Events text.

    A plain (sync) generator on purpose: Starlette's StreamingResponse automatically runs a
    sync generator's iteration in a worker thread (iterate_in_threadpool), so this long,
    blocking, real network call never blocks the async event loop other requests share."""
    try:
        for fragment in rag.stream(query, k=k):
            # walks every real fragment RAGPipeline.stream() yields, in order
            payload = "\ndata: ".join(_SSE_LINE_END.split(fragment))
            # SSE requires every LINE of multi-line data to carry its own "data: " prefix --
            # the mechanical source list (stream()'s own final chunk) contains real newlines.
            # Splits on all three SSE line ends, not just "\n": a lone "\r" left inside a data
            # line ends that line for the client, so whatever followed it would be parsed as a
            # field of its own (verified: "x\revent: error" forged an event type)
            yield f"data: {payload}\n\n"
            # one real SSE event per fragment: "data: <text>", then a blank line
        yield "data: [DONE]\n\n"
        # a final, explicit marker so a client knows the stream ended normally, not just closed
    except Exception as exc:
        # covers a real failure partway through an already-started stream (Qdrant/Neo4j/Ollama
        # down, a timeout, ...) -- HTTP headers are already sent by this point, so raising here
        # would just reset the connection with no informative content ever reaching the client
        yield f"data: [ERROR] {exc}\n\n"
        # reports the failure AS an event, the only way a client can still see it mid-stream


@app.post("/chat", response_class=StreamingResponse, responses=CHAT_RESPONSES, tags=["chat"])
# response_class=StreamingResponse, not just the function's own "-> StreamingResponse" return
# hint: verified by reading fastapi/openapi/utils.py directly (get_openapi_path, ~line 330) that
# FastAPI's OpenAPI generation reads route.response_class (defaulting to JSONResponse for every
# route unless overridden here), not the function's return annotation -- without this, FastAPI
# unconditionally adds a spurious "application/json": {"schema": {}} entry alongside the real
# text/event-stream one, confirmed empirically before this line was added
async def chat(request: ChatRequest, http_request: Request) -> StreamingResponse:
    """Answers `request.query` as a real-time Server-Sent Events stream."""
    rag: RAGPipeline = http_request.app.state.rag
    # the single, long-lived RAGPipeline this app built once at startup
    return StreamingResponse(
        _sse_events(rag, request.query, request.k), media_type="text/event-stream"
    )
