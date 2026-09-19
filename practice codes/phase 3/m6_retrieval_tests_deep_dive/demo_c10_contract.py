# Run with: pytest demo_c10_contract.py -q -rs -p no:cacheprovider
# One contract, two implementations: the same tests run against the fake AND the real thing.
import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.vectorstore.setup import build_qdrant_client
from tests.support import live_services_down

DOWN = live_services_down()


@pytest.fixture(params=["in-memory", "server"])
def client(request):
    if request.param == "in-memory":
        yield QdrantClient(":memory:"), "c"
        return
    if DOWN:
        pytest.skip(DOWN)
    server = build_qdrant_client("http://localhost:6333")
    name = f"demo_contract_{uuid.uuid4().hex[:8]}"
    try:
        yield server, name
    finally:
        if server.collection_exists(name):
            server.delete_collection(name)


def make(client_and_name):
    client, name = client_and_name
    client.create_collection(name, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    return client, name


def test_contract_upsert_then_count(client):
    c, name = make(client)
    c.upsert(name, wait=True, points=[PointStruct(id=1, vector=[1.0, 0.0])])
    assert c.count(name).count == 1


def test_contract_negative_ids_are_rejected(client):
    c, name = make(client)
    with pytest.raises((UnexpectedResponse, ValueError)):
        c.upsert(name, wait=True, points=[PointStruct(id=-1, vector=[1.0, 0.0])])
