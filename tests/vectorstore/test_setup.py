"""Tests for Qdrant collection setup.

Run against qdrant-client's in-memory mode: the same client API and collection
logic, with no server, so these stay offline and fast in CI.
"""

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.vectorstore.setup import (
    COLLECTION_NAME,
    VECTOR_SIZE,
    CollectionConfigMismatch,
    ensure_collection,
)


@pytest.fixture
def client() -> QdrantClient:
    return QdrantClient(":memory:")


def test_creates_collection_when_missing(client):
    assert ensure_collection(client) is True
    assert client.collection_exists(COLLECTION_NAME)


def test_created_collection_has_the_expected_config(client):
    ensure_collection(client)
    vectors = client.get_collection(COLLECTION_NAME).config.params.vectors
    assert vectors.size == 768
    assert vectors.distance == Distance.COSINE


def test_rerunning_is_idempotent(client):
    assert ensure_collection(client) is True
    # The second call must neither fail nor recreate -- recreating would wipe data.
    assert ensure_collection(client) is False


def test_rerun_preserves_existing_points(client):
    ensure_collection(client)
    client.upsert(
        COLLECTION_NAME,
        points=[PointStruct(id=1, vector=[0.1] * VECTOR_SIZE, payload={"x": 1})],
    )
    ensure_collection(client)
    assert client.count(COLLECTION_NAME).count == 1


def test_wrong_size_raises_rather_than_reusing(client):
    client.create_collection(
        COLLECTION_NAME, vectors_config=VectorParams(size=384, distance=Distance.COSINE)
    )
    with pytest.raises(CollectionConfigMismatch, match="size=384"):
        ensure_collection(client)


def test_wrong_distance_raises(client):
    client.create_collection(
        COLLECTION_NAME, vectors_config=VectorParams(size=768, distance=Distance.DOT)
    )
    with pytest.raises(CollectionConfigMismatch, match="distance"):
        ensure_collection(client)


def test_named_vectors_collection_is_rejected(client):
    client.create_collection(
        COLLECTION_NAME,
        vectors_config={"dense": VectorParams(size=768, distance=Distance.COSINE)},
    )
    with pytest.raises(CollectionConfigMismatch, match="named vectors"):
        ensure_collection(client)


def test_wrong_size_vector_is_rejected_at_upsert(client):
    # Documents WHY size matters: Qdrant refuses a mismatched vector outright.
    ensure_collection(client)
    with pytest.raises(Exception):
        client.upsert(COLLECTION_NAME, points=[PointStruct(id=1, vector=[0.1] * 384, payload={})])
