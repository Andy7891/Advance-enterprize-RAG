"""Tests for P7-M5: the real, generated OpenAPI schema is honest and complete -- not just that
`/docs` loads, but that it correctly describes what this app actually does, including the real
SSE content type and the real error shapes P7-M3 added (which FastAPI has no way to know about
on its own).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_docs_redoc_and_openapi_json_all_load():
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    # the literal "auto-generated OpenAPI docs at /docs" output this step verifies, not assumes


def test_the_app_has_a_real_top_level_description():
    schema = app.openapi()
    assert len(schema["info"]["description"]) > 20
    # requires more than FastAPI's own bare title -- a real sentence explaining what this API is


def test_chats_200_response_is_documented_as_sse_not_json():
    schema = app.openapi()
    content = schema["paths"]["/chat"]["post"]["responses"]["200"]["content"]
    assert "text/event-stream" in content
    assert "application/json" not in content
    # requires response_class=StreamingResponse to have suppressed FastAPI's own spurious
    # default application/json entry -- confirmed empirically this was needed before adding it


def test_chats_documented_error_statuses_include_the_real_ones_p7m3_added():
    responses = app.openapi()["paths"]["/chat"]["post"]["responses"]
    assert set(responses) == {"200", "400", "422", "500", "504"}
    # requires 400/500/504 (invisible in the schema before P7-M5) alongside 422, which P7-M7 now
    # declares itself with the real error envelope (FastAPI's own default 422 documented a body
    # this app stopped sending in P7-M3) -- every one of these is a real response _handle_* produces


def test_chat_requests_query_field_has_a_real_example():
    schema = app.openapi()["components"]["schemas"]["ChatRequest"]
    assert schema["properties"]["query"]["examples"] == ["Who founded Insurellm?"]
    # requires a REAL example value, not a blank "Try it out" prefill


def test_health_responses_fields_are_all_described():
    schema = app.openapi()["components"]["schemas"]["HealthResponse"]
    for field in ("status", "qdrant_url", "neo4j_uri", "ollama_url"):
        assert schema["properties"][field]["description"]
        # requires a real, non-empty description on every field, not just a bare name


def test_both_routes_are_tagged():
    paths = app.openapi()["paths"]
    assert paths["/health"]["get"]["tags"] == ["health"]
    assert paths["/chat"]["post"]["tags"] == ["chat"]
