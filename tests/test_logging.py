"""Tests for P7-M4's request/response logging: one structured log record per request, with
the REAL, full duration (not just time-to-first-byte) and the real final status code --
including the two error paths P7-M3 already proved behave differently (a specific handler vs.
the bare Exception catch-all).
"""

from __future__ import annotations

import logging
import time

from fastapi.testclient import TestClient

from src.api.main import app, logger
from src.ingestion.schemas import DocumentChunk
from src.orchestration.rag import RAGPipeline
from src.retrieval.pipeline import RankedChunk, RetrievalResult

client = TestClient(app, raise_server_exceptions=False)
# raise_server_exceptions=False for the same reason tests/test_errors.py needed it: the bare
# Exception path re-raises after sending its response (see decision.md D-2026-09-30-23)


def _request_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if hasattr(r, "status_code") and hasattr(r, "duration_ms")]
    # picks out only RequestLoggingMiddleware's own log records, by the extra={} fields
    # only it attaches -- ignores any other record (e.g. P7-M3's own logger.warning calls)


# --- basic shape -----------------------------------------------------------------------------


def test_a_normal_request_logs_one_record_with_the_real_method_path_and_status(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    client.get("/health")
    records = _request_records(caplog.records)
    assert len(records) == 1
    # requires exactly one log line for exactly one request -- not zero, not duplicated
    assert records[0].method == "GET"
    assert records[0].path == "/health"
    assert records[0].status_code == 200
    assert records[0].levelno == logging.INFO
    # requires a normal, fast request to log at INFO, not WARNING


def test_duration_ms_is_a_real_positive_measurement(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    client.get("/health")
    record = _request_records(caplog.records)[0]
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms > 0.0
    # requires a real, measured elapsed time, not a placeholder like 0 or None


# --- the core claim this design exists to prove -----------------------------------------------


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


class _FakeRetriever:
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


class _SlowFakeLLM:
    """Sleeps for a real, measurable time across several fragments -- standing in for a
    real, slow RAGPipeline.stream() call, without needing Ollama or 5+ real seconds."""

    def __init__(self, sleep_per_chunk: float, chunks: int = 3):
        self._sleep_per_chunk = sleep_per_chunk
        self._chunks = chunks

    def generate(self, prompt, temperature, max_tokens, *, system=None):
        return "unused"

    def stream(self, prompt, temperature, *, system=None):
        for i in range(self._chunks):
            time.sleep(self._sleep_per_chunk)
            # a real, blocking sleep -- runs inside _sse_events's own iterate_in_threadpool
            yield f"chunk-{i}"


def test_a_streaming_response_logs_its_real_full_duration_not_time_to_first_byte(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    total_sleep = 0.3
    app.state.rag = RAGPipeline(_FakeRetriever(), _SlowFakeLLM(sleep_per_chunk=total_sleep / 3))
    # sets the SAME attribute lifespan would normally set, bypassing it entirely

    response = client.post("/chat", json={"query": "hi", "k": 1})
    assert response.status_code == 200

    record = _request_records(caplog.records)[-1]
    assert record.path == "/chat"
    assert record.duration_ms >= total_sleep * 1000 * 0.8
    # requires the logged duration to reflect the FULL ~300ms stream, not the near-instant
    # time at which the StreamingResponse object was first returned -- proving this
    # middleware does NOT fall into the same trap @app.middleware("http") was verified to
    # (empirically measured at 0.005s for an equivalent case before this step was designed)


# --- both error paths P7-M3 proved behave differently -----------------------------------------


def test_a_request_handled_by_a_specific_exception_handler_logs_its_real_status(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    response = client.post("/chat", json={"k": 0})
    # a validation failure -- handled by _handle_validation_error, a SPECIFIC registration
    assert response.status_code == 422

    record = _request_records(caplog.records)[-1]
    assert record.status_code == 422
    # requires the REAL status a specific handler chose, not the 500 fallback default


def test_the_bare_exception_path_still_gets_logged_via_the_middlewares_own_try_except(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)

    @app.get("/_test/logging-raises-unexpected")
    def _raises() -> None:
        raise RuntimeError("unanticipated")

    response = client.get("/_test/logging-raises-unexpected")
    assert response.status_code == 500
    # confirms _handle_unexpected_error (P7-M3) still runs -- this request is NOT swallowed

    record = _request_records(caplog.records)[-1]
    assert record.path == "/_test/logging-raises-unexpected"
    assert record.status_code == 500
    # requires this middleware's own try/except around `await self._app(...)` to have run --
    # verified while designing this step that WITHOUT it, this exact case produces NO log
    # line at all, since the exception propagates through wrapped_send instead of a normal
    # completion, and ServerErrorMiddleware (outside this middleware) never calls THIS send


# --- severity escalation -----------------------------------------------------------------------


def test_a_request_slower_than_the_threshold_logs_at_warning_not_info(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=logger.name)
    monkeypatch.setattr("src.api.main.SLOW_REQUEST_MS", 50.0)
    # lowers the real 5000ms threshold so this test stays fast while still proving the rule
    app.state.rag = RAGPipeline(_FakeRetriever(), _SlowFakeLLM(sleep_per_chunk=0.03, chunks=3))

    client.post("/chat", json={"query": "hi", "k": 1})

    record = _request_records(caplog.records)[-1]
    assert record.levelno == logging.WARNING
    # requires a request that crosses the (lowered) threshold to escalate severity


def test_a_request_faster_than_the_threshold_stays_at_info(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=logger.name)
    monkeypatch.setattr("src.api.main.SLOW_REQUEST_MS", 50_000.0)
    # raises the threshold so even a normal request can never accidentally cross it
    client.get("/health")

    record = _request_records(caplog.records)[-1]
    assert record.levelno == logging.INFO


# --- the real JSON formatter --------------------------------------------------------------------


def test_the_json_formatter_produces_valid_parseable_json_with_the_expected_fields():
    import json

    from src.api.main import _JsonFormatter

    record = logging.LogRecord(
        name="src.api.main",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="GET /health 200 1.2ms",
        args=(),
        exc_info=None,
    )
    record.method = "GET"
    record.path = "/health"
    record.status_code = 200
    record.duration_ms = 1.2

    parsed = json.loads(_JsonFormatter().format(record))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "src.api.main"
    assert parsed["message"] == "GET /health 200 1.2ms"
    assert parsed["method"] == "GET"
    assert parsed["path"] == "/health"
    assert parsed["status_code"] == 200
    assert parsed["duration_ms"] == 1.2
    assert "timestamp" in parsed
    # requires every extra={} field AND every standard field to survive into real JSON
