"""Tests for the ingestion job: real in-memory Qdrant, a fake embedding client, and
a temporary chunks directory, so every stage runs for real except the model."""

import dataclasses
import hashlib
import json

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src.core.config import settings
from src.embedding.embedder import Embedder, EmbeddingError
from src.ingestion.pipeline import REPORT_FILENAME, write_chunks
from src.ingestion.schemas import DocumentChunk, Embedding
from src.vectorstore.ingest import IngestionAborted, VerificationFailed, run_ingestion
from src.vectorstore.setup import ensure_collection
from src.vectorstore.store import QdrantVectorStore

COLLECTION = "test_chunks"
DIM = settings.embed_dim


class HashingClient:
    """Deterministic unit vectors derived from the text, counting every request."""

    def __init__(self, fail=False):
        self.requests = 0
        self.fail = fail

    def embed(self, model="", input=(), truncate=None):
        self.requests += 1
        if self.fail:
            import ollama

            raise ollama.ResponseError("the input length exceeds the context length", 400)
        vectors = []
        for text in input:
            vector = [0.0] * DIM
            vector[int(hashlib.sha256(text.encode()).hexdigest(), 16) % DIM] = 1.0
            vectors.append(vector)
        return {"embeddings": vectors}


def chunk(source: str, index: int) -> DocumentChunk:
    text = f"{source} chunk {index}"
    return DocumentChunk(
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


def write_corpus(chunks_dir, documents: dict[str, int], documents_failed: int = 0) -> None:
    for source, count in documents.items():
        write_chunks(
            [chunk(source, i) for i in range(count)],
            chunks_dir / f"{source.replace('/', '__')}.json",
        )
    (chunks_dir / REPORT_FILENAME).write_text(
        json.dumps({"documents_failed": documents_failed}), encoding="utf-8"
    )


@pytest.fixture
def env(tmp_path):
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    client = QdrantClient(":memory:")
    embed_client = HashingClient()

    def ingest(**kwargs):
        return run_ingestion(
            client,
            lambda: Embedder(embed_client, verify=lambda url, model: settings.embed_model_digest),
            collection=COLLECTION,
            chunks_dir=chunks_dir,
            log_path=tmp_path / "ingestion_log.json",
            **kwargs,
        )

    def log():
        return json.loads((tmp_path / "ingestion_log.json").read_text(encoding="utf-8"))

    return dataclasses.make_dataclass(
        "Env", ["chunks_dir", "client", "embed_client", "ingest", "log"]
    )(chunks_dir, client, embed_client, ingest, log)


def stored_chunk_ids(client) -> list[str]:
    points, _ = client.scroll(COLLECTION, limit=1000, with_payload=["chunk_id"])
    return sorted(p.payload["chunk_id"] for p in points)


# --- success path -----------------------------------------------------------------


def test_successful_run_indexes_everything_and_logs_it(env):
    write_corpus(env.chunks_dir, {"company/about.md": 3, "products/widget.md": 2})

    report = env.ingest()

    assert report.status == "succeeded" and report.verified
    assert stored_chunk_ids(env.client) == [
        "company/about.md_0",
        "company/about.md_1",
        "company/about.md_2",
        "products/widget.md_0",
        "products/widget.md_1",
    ]
    log = env.log()
    assert log["status"] == "succeeded"
    assert (log["documents_on_disk"], log["chunks_on_disk"]) == (2, 5)
    assert (log["points_before"], log["points_after"]) == (0, 5)
    assert list(log["stage_seconds"]) == [
        "preflight",
        "load",
        "embed",
        "upsert",
        "orphans",
        "verify",
    ]
    assert log["model_digest"] == settings.embed_model_digest
    assert log["started_at"].endswith("+00:00") and log["finished_at"]


def test_rerun_is_idempotent(env):
    write_corpus(env.chunks_dir, {"company/about.md": 3})
    env.ingest()
    report = env.ingest()
    assert (report.points_before, report.points_after) == (3, 3)


# --- orphans ----------------------------------------------------------------------


def test_documents_removed_from_disk_are_removed_from_the_index(env):
    write_corpus(env.chunks_dir, {"company/about.md": 2, "products/widget.md": 1})
    env.ingest()
    (env.chunks_dir / "products__widget.md.json").unlink()

    report = env.ingest()

    assert report.orphan_documents_removed == 1
    assert stored_chunk_ids(env.client) == ["company/about.md_0", "company/about.md_1"]
    assert report.verified


def test_orphans_are_kept_when_the_chunking_run_had_failures(env):
    write_corpus(env.chunks_dir, {"company/about.md": 2, "products/widget.md": 1})
    env.ingest()
    (env.chunks_dir / "products__widget.md.json").unlink()
    write_corpus(env.chunks_dir, {"company/about.md": 2}, documents_failed=1)

    report = env.ingest()

    # The missing document may only have failed to chunk, so it must survive.
    assert "products/widget.md_0" in stored_chunk_ids(env.client)
    assert (report.orphan_documents_removed, report.orphan_documents_kept) == (0, 1)
    assert report.status == "succeeded" and report.verified
    assert "kept 1 orphan document" in env.log()["warnings"][0]


# --- refusals and failures ----------------------------------------------------------


def test_empty_chunks_directory_aborts_and_logs_the_stage(env):
    with pytest.raises(IngestionAborted, match="no chunks"):
        env.ingest()
    log = env.log()
    assert (log["status"], log["failed_stage"]) == ("failed", "load")
    assert env.embed_client.requests == 0


def test_incompatible_stored_points_abort_before_any_embedding(env):
    write_corpus(env.chunks_dir, {"company/about.md": 2})
    env.ingest()
    requests_after_first_run = env.embed_client.requests
    stale = Embedding(
        chunk("company/old.md", 0), tuple([1.0] + [0.0] * (DIM - 1)), "m", "9" * 64, "old: "
    )
    QdrantVectorStore(
        env.client, None, collection=COLLECTION, model_digest="9" * 64, document_prefix="old: "
    ).upsert([stale])

    with pytest.raises(IngestionAborted, match="1 stored points were made with a different"):
        env.ingest()

    assert env.embed_client.requests == requests_after_first_run
    log = env.log()
    assert (log["failed_stage"], log["incompatible_points_before"]) == ("preflight", 1)


def test_rebuild_replaces_incompatible_points(env):
    write_corpus(env.chunks_dir, {"company/about.md": 2})
    stale = Embedding(
        chunk("company/old.md", 0), tuple([1.0] + [0.0] * (DIM - 1)), "m", "9" * 64, ""
    )
    env.ingest()
    QdrantVectorStore(
        env.client, None, collection=COLLECTION, model_digest="9" * 64, document_prefix=""
    ).upsert([stale])

    report = env.ingest(rebuild=True)

    # The stale document is not on disk, so it is removed as an orphan, and
    # verification confirms nothing incompatible remains.
    assert report.status == "succeeded" and report.verified
    assert report.orphan_documents_removed == 1
    assert stored_chunk_ids(env.client) == ["company/about.md_0", "company/about.md_1"]


def test_rebuild_cannot_verify_while_an_incompatible_orphan_is_kept(env):
    write_corpus(env.chunks_dir, {"company/about.md": 2}, documents_failed=1)
    ensure_collection(env.client, name=COLLECTION)
    stale = Embedding(
        chunk("company/old.md", 0), tuple([1.0] + [0.0] * (DIM - 1)), "m", "9" * 64, ""
    )
    QdrantVectorStore(
        env.client, None, collection=COLLECTION, model_digest="9" * 64, document_prefix=""
    ).upsert([stale])

    # The chunking failures keep the stale document as a protected orphan, so the
    # collection still spans two vector spaces and must not be reported as verified.
    with pytest.raises(VerificationFailed, match="1 incompatible"):
        env.ingest(rebuild=True)
    assert (env.log()["failed_stage"], env.log()["orphan_documents_kept"]) == ("verify", 1)


def test_embedding_failure_leaves_the_collection_untouched(env):
    write_corpus(env.chunks_dir, {"company/about.md": 2})
    env.ingest()
    before = stored_chunk_ids(env.client)
    env.embed_client.fail = True

    with pytest.raises(EmbeddingError):
        env.ingest()

    assert stored_chunk_ids(env.client) == before
    assert env.log()["failed_stage"] == "embed"


def test_model_check_failure_is_logged_at_preflight(env, tmp_path):
    write_corpus(env.chunks_dir, {"company/about.md": 1})

    def wrong_model():
        raise RuntimeError("digest mismatch")

    with pytest.raises(RuntimeError, match="digest mismatch"):
        run_ingestion(
            env.client,
            wrong_model,
            collection=COLLECTION,
            chunks_dir=env.chunks_dir,
            log_path=tmp_path / "ingestion_log.json",
        )
    assert env.log()["failed_stage"] == "preflight"


def test_wrongly_sized_collection_fails_preflight(env):
    write_corpus(env.chunks_dir, {"company/about.md": 1})
    env.client.create_collection(
        COLLECTION, vectors_config=VectorParams(size=384, distance=Distance.COSINE)
    )
    with pytest.raises(Exception, match="size=384"):
        env.ingest()
    assert env.log()["failed_stage"] == "preflight"


def test_verification_catches_a_document_that_was_not_written(env, monkeypatch):
    write_corpus(env.chunks_dir, {"company/about.md": 2, "products/widget.md": 1})
    real_upsert = QdrantVectorStore.upsert

    def drops_widget(self, embeddings):
        real_upsert(self, [e for e in embeddings if e.chunk.source_path != "products/widget.md"])

    monkeypatch.setattr(QdrantVectorStore, "upsert", drops_widget)

    with pytest.raises(VerificationFailed, match="1 missing"):
        env.ingest()
    log = env.log()
    assert (log["failed_stage"], log["verified"]) == ("verify", False)
