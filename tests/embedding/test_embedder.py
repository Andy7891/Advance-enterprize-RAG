"""Tests for batch embedding, driven by a fake client so no Ollama is needed."""

import dataclasses
import json
import math

import ollama
import pytest

from src.core.config import settings
from src.embedding.embedder import Embedder, EmbeddingError
from src.ingestion.pipeline import REPORT_FILENAME, read_chunks, write_chunks
from src.ingestion.schemas import DocumentChunk

DIM = settings.embed_dim


def make_chunk(index: int, text: str | None = None) -> DocumentChunk:
    text = text if text is not None else f"chunk number {index}"
    return DocumentChunk(
        chunk_id=f"company/about.md_{index}",
        doc_id="abc",
        source_path="company/about.md",
        category="company",
        title="About",
        chunk_index=index,
        text=text,
        char_count=len(text),
        approx_token_count=len(text) // 4,
        metadata={"start_char": 0, "end_char": len(text), "tokens_approx": 1},
    )


def unit_vector(position: int) -> list[float]:
    vector = [0.0] * DIM
    vector[position % DIM] = 1.0
    return vector


class FakeClient:
    """Returns a one-hot vector whose hot position encodes the input's length,
    so a test can tell exactly which text produced which vector."""

    def __init__(self, transform=None):
        self.calls: list[dict] = []
        self._transform = transform

    def embed(self, model="", input=(), truncate=None):
        self.calls.append({"model": model, "input": list(input), "truncate": truncate})
        vectors = [unit_vector(len(text)) for text in input]
        return {"embeddings": self._transform(vectors) if self._transform else vectors}


def make_embedder(client, batch_size=2, verify=lambda url, model: "digest-123"):
    return Embedder(client, batch_size=batch_size, verify=verify)


def test_returns_one_embedding_per_chunk_in_input_order():
    chunks = [make_chunk(i, "x" * (10 + i)) for i in range(5)]
    result = make_embedder(FakeClient()).embed_batch(chunks)

    assert [e.chunk for e in result] == chunks
    # The hot position proves each vector came from its own chunk's text, not a
    # neighbour's — the misalignment bug this contract exists to prevent.
    prefix_len = len(settings.embed_document_prefix)
    assert [e.vector.index(1.0) for e in result] == [prefix_len + 10 + i for i in range(5)]


def test_sends_batches_of_configured_size():
    client = FakeClient()
    make_embedder(client, batch_size=2).embed_batch([make_chunk(i) for i in range(5)])
    assert [len(call["input"]) for call in client.calls] == [2, 2, 1]


def test_applies_document_prefix_and_refuses_truncation():
    client = FakeClient()
    make_embedder(client).embed_batch([make_chunk(0, "hello")])
    call = client.calls[0]
    assert call["input"] == [f"{settings.embed_document_prefix}hello"]
    assert call["truncate"] is False
    assert call["model"] == settings.embed_model


def test_records_provenance_on_every_embedding():
    result = make_embedder(FakeClient()).embed_batch([make_chunk(0), make_chunk(1)])
    for embedding in result:
        assert embedding.model == settings.embed_model
        assert embedding.model_digest == "digest-123"
        assert embedding.document_prefix == settings.embed_document_prefix
        assert isinstance(embedding.vector, tuple)


def test_verifies_model_at_construction_before_any_embedding():
    client = FakeClient()

    def mismatch(url, model):
        raise RuntimeError("digest mismatch")

    with pytest.raises(RuntimeError, match="digest mismatch"):
        make_embedder(client, verify=mismatch)
    assert client.calls == []


def test_empty_input_makes_no_requests():
    client = FakeClient()
    assert make_embedder(client).embed_batch([]) == []
    assert client.calls == []


def test_rejects_non_positive_batch_size():
    with pytest.raises(ValueError, match="batch_size"):
        make_embedder(FakeClient(), batch_size=0)


def test_count_mismatch_raises():
    client = FakeClient(transform=lambda vectors: vectors[:-1])
    with pytest.raises(EmbeddingError, match="sent 2 texts but received 1"):
        make_embedder(client).embed_batch([make_chunk(0), make_chunk(1)])


def test_wrong_dimension_raises():
    client = FakeClient(transform=lambda vectors: [v[:384] for v in vectors])
    with pytest.raises(EmbeddingError, match="384 dims, expected 768"):
        make_embedder(client).embed_batch([make_chunk(0)])


def test_nan_raises():
    client = FakeClient(transform=lambda vectors: [[math.nan] + v[1:] for v in vectors])
    with pytest.raises(EmbeddingError, match="NaN"):
        make_embedder(client).embed_batch([make_chunk(0)])


def test_non_unit_norm_raises():
    client = FakeClient(transform=lambda vectors: [[x * 2 for x in v] for v in vectors])
    with pytest.raises(EmbeddingError, match="norm is 2.0000"):
        make_embedder(client).embed_batch([make_chunk(0)])


def test_server_error_names_every_chunk_in_the_failed_batch():
    class RejectingClient(FakeClient):
        def embed(self, model="", input=(), truncate=None):
            raise ollama.ResponseError("the input length exceeds the context length", 400)

    with pytest.raises(EmbeddingError) as excinfo:
        make_embedder(RejectingClient()).embed_batch([make_chunk(0), make_chunk(1)])
    assert "company/about.md_0" in str(excinfo.value)
    assert "company/about.md_1" in str(excinfo.value)
    assert "exceeds the context length" in str(excinfo.value)


def test_connection_failure_is_reported_as_embedding_error():
    class DownClient(FakeClient):
        def embed(self, model="", input=(), truncate=None):
            raise ConnectionError("Failed to connect to Ollama")

    with pytest.raises(EmbeddingError, match="Failed to connect"):
        make_embedder(DownClient()).embed_batch([make_chunk(0)])


def test_own_bugs_are_not_disguised_as_embedding_errors():
    class BuggyClient(FakeClient):
        def embed(self, model="", input=(), truncate=None):
            raise KeyError("embeddings")

    with pytest.raises(KeyError):
        make_embedder(BuggyClient()).embed_batch([make_chunk(0)])


def test_read_chunks_round_trips_and_skips_the_report(tmp_path):
    about = [make_chunk(0), make_chunk(1)]
    notes = [dataclasses.replace(make_chunk(0), source_path="company/notes.txt")]
    write_chunks(about, tmp_path / "company__about.md.json")
    write_chunks(notes, tmp_path / "company__notes.txt.json")
    (tmp_path / REPORT_FILENAME).write_text(json.dumps({"chunks_written": 3}), encoding="utf-8")

    loaded = read_chunks(tmp_path)

    # The .txt source proves the reader does not assume every source is markdown.
    assert loaded == about + notes
