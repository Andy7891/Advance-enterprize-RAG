"""Smoke test: proves pytest + the API package import correctly in CI.
Real ingestion/retrieval tests will replace/join this once that logic exists.
"""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health_endpoint_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
