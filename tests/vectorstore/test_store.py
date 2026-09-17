"""Tests for QdrantVectorStore against the real in-memory Qdrant client, so upsert,
filtered delete and query_points behave as the server does — minus payload
indexes, which local mode ignores and are checked live instead."""

import dataclasses
import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException
from qdrant_client.models import Distance, VectorParams

from src.core.config import settings
from src.ingestion.schemas import DocumentChunk, Embedding
from src.vectorstore.setup import ensure_collection
from src.vectorstore.store import (
    IncompleteDocument,
    ProvenanceMismatch,
    QdrantVectorStore,
    chunk_from_payload,
    point_id,
)

DIM = settings.embed_dim
COLLECTION = "test_chunks"


def one_hot(position: int) -> tuple[float, ...]:
    vector = [0.0] * DIM
    vector[position] = 1.0
    return tuple(vector)


def make_embedding(source: str, index: int, position: int, **overrides) -> Embedding:
    text = f"{source} chunk {index}"
    chunk = DocumentChunk(
        chunk_id=f"{source}_{index}",
        doc_id=f"doc-{source}",
        source_path=source,
        category=source.split("/")[0],
        title=source,
        chunk_index=index,
        text=text,
        char_count=len(text),
        approx_token_count=3,
        metadata={"start_char": 0, "end_char": len(text), "tokens_approx": 3},
    )
    fields = {
        "chunk": chunk,
        "vector": one_hot(position),
        "model": settings.embed_model,
        "model_digest": settings.embed_model_digest,
        "document_prefix": settings.embed_document_prefix,
    }
    fields.update(overrides)
    return Embedding(**fields)


class FakeQueryEmbedder:
    """Returns a chosen one-hot vector, so a test decides exactly which point is nearest."""

    def __init__(self, position: int = 0):
        self.position = position
        self.questions: list[str] = []

    def embed_query(self, text: str):
        self.questions.append(text)
        return one_hot(self.position)


@pytest.fixture
def client():
    client = QdrantClient(":memory:")
    ensure_collection(client, name=COLLECTION)
    return client


def make_store(client, embedder=None):
    return QdrantVectorStore(client, embedder or FakeQueryEmbedder(), collection=COLLECTION)


def chunk_ids_in(client) -> list[str]:
    points, _ = client.scroll(COLLECTION, limit=100, with_payload=True)
    return sorted(p.payload["chunk_id"] for p in points)


# --- upsert -------------------------------------------------------------------


def test_upsert_then_search_returns_the_nearest_chunk_first(client):
    embeddings = [make_embedding("company/about.md", i, position=i) for i in range(3)]
    store = make_store(client, FakeQueryEmbedder(position=1))
    store.upsert(embeddings)

    results = store.search_with_scores("who founded it?", k=3)

    assert results[0].chunk == embeddings[1].chunk
    assert results[0].score == pytest.approx(1.0)
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_search_returns_full_chunks_equal_to_what_was_stored(client):
    embedding = make_embedding("company/about.md", 0, position=0)
    store = make_store(client)
    store.upsert([embedding])
    # Equality proves every field, including the nested metadata dict, survived
    # the payload round trip.
    assert store.search("anything", k=1) == [embedding.chunk]


def test_search_passes_the_raw_question_to_the_query_embedder(client):
    embedder = FakeQueryEmbedder()
    store = make_store(client, embedder)
    store.upsert([make_embedding("company/about.md", 0, position=0)])
    store.search("Who is the CEO?", k=1)
    # The store must not add a prefix itself; the embedder owns that, and adding
    # it twice would put queries in a different space from documents.
    assert embedder.questions == ["Who is the CEO?"]


def test_k_larger_than_collection_returns_everything(client):
    store = make_store(client)
    store.upsert([make_embedding("company/about.md", i, position=i) for i in range(2)])
    assert len(store.search("q", k=10)) == 2


def test_k_below_one_is_rejected(client):
    with pytest.raises(ValueError, match="k must be >= 1"):
        make_store(client).search("q", k=0)


def test_reupserting_the_same_embeddings_does_not_duplicate(client):
    store = make_store(client)
    embeddings = [make_embedding("company/about.md", i, position=i) for i in range(3)]
    store.upsert(embeddings)
    store.upsert(embeddings)
    assert store.count() == 3


def test_rechunking_to_fewer_chunks_removes_stale_points(client):
    store = make_store(client)
    store.upsert([make_embedding("company/about.md", i, position=i) for i in range(4)])
    store.upsert([make_embedding("company/about.md", i, position=i) for i in range(2)])
    # Without the delete-by-doc_id guard, _2 and _3 would survive and stay searchable.
    assert chunk_ids_in(client) == ["company/about.md_0", "company/about.md_1"]


def test_reupserting_one_document_leaves_other_documents_untouched(client):
    store = make_store(client)
    store.upsert(
        [make_embedding("company/about.md", 0, position=0)]
        + [make_embedding("products/widget.md", i, position=10 + i) for i in range(2)]
    )
    store.upsert([make_embedding("company/about.md", 0, position=0)])
    assert chunk_ids_in(client) == [
        "company/about.md_0",
        "products/widget.md_0",
        "products/widget.md_1",
    ]


def test_point_ids_are_deterministic_uuids(client):
    first = point_id("company/about.md_3")
    assert first == point_id("company/about.md_3")
    assert first != point_id("company/about.md_4")
    assert uuid.UUID(first).version == 5

    store = make_store(client)
    store.upsert([make_embedding("company/about.md", 0, position=0)])
    (point,), _ = client.scroll(COLLECTION, limit=1)
    assert point.id == point_id("company/about.md_0")


def test_payload_records_provenance(client):
    store = make_store(client)
    store.upsert([make_embedding("company/about.md", 0, position=0)])
    (point,), _ = client.scroll(COLLECTION, limit=1, with_payload=True)
    assert point.payload["model"] == settings.embed_model
    assert point.payload["model_digest"] == settings.embed_model_digest
    assert point.payload["document_prefix"] == settings.embed_document_prefix
    # Provenance keys must not leak into the reconstructed chunk.
    assert (
        dataclasses.asdict(chunk_from_payload(point.payload)).keys()
        == dataclasses.asdict(make_embedding("company/about.md", 0, position=0).chunk).keys()
    )


@pytest.mark.parametrize(
    "override", [{"model_digest": "f" * 64}, {"document_prefix": ""}], ids=["digest", "prefix"]
)
def test_provenance_mismatch_is_rejected_before_anything_is_written(client, override):
    store = make_store(client)
    good = make_embedding("company/about.md", 0, position=0)
    stale = make_embedding("products/widget.md", 0, position=1, **override)
    with pytest.raises(ProvenanceMismatch):
        store.upsert([good, stale])
    assert store.count() == 0


def test_incomplete_document_is_rejected_before_deleting_anything(client):
    store = make_store(client)
    full = [make_embedding("company/about.md", i, position=i) for i in range(3)]
    store.upsert(full)

    with pytest.raises(IncompleteDocument, match=r"\[0, 2\]"):
        store.upsert([full[0], full[2]])
    # The guard fired before the delete, so the stored document is intact.
    assert store.count() == 3


# --- health_check ---------------------------------------------------------------


def test_health_check_true_for_matching_collection(client):
    assert make_store(client).health_check() is True


def test_health_check_false_when_collection_missing():
    assert make_store(QdrantClient(":memory:")).health_check() is False


def test_health_check_false_when_collection_has_wrong_size():
    client = QdrantClient(":memory:")
    client.create_collection(
        COLLECTION, vectors_config=VectorParams(size=384, distance=Distance.COSINE)
    )
    assert make_store(client).health_check() is False


def test_health_check_false_instead_of_raising_when_qdrant_unreachable():
    class DownClient:
        def collection_exists(self, name):
            raise ResponseHandlingException(ConnectionError("connection refused"))

    assert make_store(DownClient()).health_check() is False
