"""Qdrant vector store: store Embeddings as points, retrieve chunks by question.

Every stored point is (id, vector, payload). The id makes writes idempotent, the
vector makes the point findable, and the payload is what search hands back — it
is the text a language model will eventually read.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ApiException
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue, PointStruct

from src.core.config import settings
from src.ingestion.schemas import DocumentChunk, Embedding
from src.vectorstore.setup import (
    COLLECTION_NAME,
    CollectionConfigMismatch,
    build_qdrant_client,
    check_collection_config,
    ensure_collection,
)

logger = logging.getLogger(__name__)

# INTERVIEW: never change this value. Point IDs are derived from it, so a new
# namespace gives every chunk a new ID and the next upsert duplicates the whole
# collection instead of overwriting it.
POINT_ID_NAMESPACE = uuid.UUID("f46fd59c-cfad-4523-9397-d36d7de5de4e")

CHUNK_FIELDS = tuple(field.name for field in dataclasses.fields(DocumentChunk))


class ProvenanceMismatch(ValueError):
    """An Embedding was made by a different model version or prefix than the store expects."""


class IncompleteDocument(ValueError):
    """An upsert did not contain every chunk of a document."""


class QueryEmbedder(Protocol):
    def embed_query(self, text: str) -> Sequence[float]: ...


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: DocumentChunk
    score: float
    """Cosine similarity to the question. Kept because thresholds, evaluation and
    reranking all need it, and it cannot be recovered once discarded."""


def point_id(chunk_id: str) -> str:
    """Deterministic UUID for a chunk.

    Qdrant accepts only unsigned integers or UUIDs as point IDs, so the readable
    chunk_id is hashed into a UUIDv5: the same chunk_id always yields the same
    point, which is what makes re-running an upsert overwrite instead of duplicate.
    """
    return str(uuid.uuid5(POINT_ID_NAMESPACE, chunk_id))


def to_payload(embedding: Embedding) -> dict[str, Any]:
    payload = dataclasses.asdict(embedding.chunk)
    # Stored on every point so a collection holding vectors from two model
    # versions can be found with a filter, long after the run that wrote them.
    payload["model"] = embedding.model
    payload["model_digest"] = embedding.model_digest
    payload["document_prefix"] = embedding.document_prefix
    return payload


def chunk_from_payload(payload: dict[str, Any]) -> DocumentChunk:
    # Selecting the chunk's own fields drops the provenance keys, which belong to
    # the point, not to the chunk.
    return DocumentChunk(**{name: payload[name] for name in CHUNK_FIELDS})


class QdrantVectorStore:
    """Stores Embeddings in one Qdrant collection and retrieves chunks by question."""

    def __init__(
        self,
        client: QdrantClient,
        query_embedder: QueryEmbedder,
        *,
        collection: str = COLLECTION_NAME,
        dim: int = settings.embed_dim,
        model_digest: str = settings.embed_model_digest,
        document_prefix: str = settings.embed_document_prefix,
    ) -> None:
        self._client = client
        self._query_embedder = query_embedder
        self._collection = collection
        self._dim = dim
        self._model_digest = model_digest
        self._document_prefix = document_prefix

    def health_check(self) -> bool:
        """True only if Qdrant answers AND the collection can serve upsert and search."""
        try:
            # INTERVIEW: readiness, not liveness. A running Qdrant with a missing
            # or wrongly sized collection is "up" but would fail every request.
            if not self._client.collection_exists(self._collection):
                logger.warning("collection %r does not exist", self._collection)
                return False
            check_collection_config(self._client, self._collection, size=self._dim)
        except (ApiException, CollectionConfigMismatch) as exc:
            # A health check that raises forces every caller to wrap it; the
            # reason is logged so False is still diagnosable.
            logger.warning("Qdrant not ready: %s", exc)
            return False
        return True

    def upsert(self, embeddings: Sequence[Embedding]) -> None:
        """Store embeddings, replacing everything previously stored for their documents.

        Each call must contain ALL chunks of every document it touches: a
        document's old points are deleted first, so a partial list would silently
        remove the chunks that were left out.
        """
        # Both checks run over the whole input before anything is written, so a
        # rejected call leaves the collection exactly as it was.
        self._check_provenance(embeddings)
        by_document = self._group_complete_documents(embeddings)

        for doc_id, group in by_document.items():
            # INTERVIEW: chunk IDs are positional, so re-chunking a document to
            # fewer chunks would leave its old surplus points searchable forever.
            # Deleting by doc_id first is the guard noted since P2-M1.
            same_document = FieldCondition(key="doc_id", match=MatchValue(value=doc_id))
            self._client.delete(
                collection_name=self._collection,
                points_selector=FilterSelector(filter=Filter(must=[same_document])),
                wait=True,
            )
            self._client.upsert(
                collection_name=self._collection,
                points=[
                    PointStruct(
                        id=point_id(e.chunk.chunk_id), vector=list(e.vector), payload=to_payload(e)
                    )
                    for e in group
                ],
                # wait=True returns only once points are searchable; without it a
                # search straight after indexing can silently miss them.
                wait=True,
            )
        logger.info("Upserted %d points across %d documents", len(embeddings), len(by_document))

    def search(self, query_text: str, k: int) -> list[DocumentChunk]:
        """Top-k chunks for a question, best first."""
        return [result.chunk for result in self.search_with_scores(query_text, k)]

    def search_with_scores(self, query_text: str, k: int) -> list[ScoredChunk]:
        """Top-k chunks with their cosine similarity, best first."""
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        # INTERVIEW: the question is embedded with the query prefix by the same
        # pinned model that embedded the documents; comparing against vectors from
        # any other model or prefix returns confident, meaningless scores.
        vector = self._query_embedder.embed_query(query_text)
        response = self._client.query_points(
            collection_name=self._collection,
            query=list(vector),
            limit=k,
            with_payload=True,
        )
        return [
            ScoredChunk(chunk=chunk_from_payload(point.payload or {}), score=point.score)
            for point in response.points
        ]

    def count(self) -> int:
        return self._client.count(collection_name=self._collection, exact=True).count

    def _check_provenance(self, embeddings: Sequence[Embedding]) -> None:
        for e in embeddings:
            if e.model_digest != self._model_digest or e.document_prefix != self._document_prefix:
                # INTERVIEW: the startup check stops the WRONG model embedding; this
                # stops embeddings made earlier by another model (a stale list, an
                # old script) from being mixed into the collection.
                raise ProvenanceMismatch(
                    f"{e.chunk.chunk_id}: made with digest {e.model_digest[:12]} and prefix "
                    f"{e.document_prefix!r}; this store expects {self._model_digest[:12]} and "
                    f"{self._document_prefix!r}"
                )

    @staticmethod
    def _group_complete_documents(embeddings: Sequence[Embedding]) -> dict[str, list[Embedding]]:
        by_document: dict[str, list[Embedding]] = {}
        for e in embeddings:
            by_document.setdefault(e.chunk.doc_id, []).append(e)

        for doc_id, group in by_document.items():
            indexes = sorted(e.chunk.chunk_index for e in group)
            # Contiguous 0..N-1 is the only evidence available that the list holds
            # the whole document; it cannot detect a document missing its final
            # chunks, which is why callers pass full read_chunks() output.
            if indexes != list(range(len(group))):
                raise IncompleteDocument(
                    f"document {group[0].chunk.source_path} ({doc_id}) has chunk indexes "
                    f"{indexes}; upsert needs every chunk, because the document's existing "
                    "points are deleted first"
                )
        return by_document


def main() -> None:
    """CLI: python -m src.vectorstore.store {index,search,health}"""
    parser = argparse.ArgumentParser(description="Index chunks into Qdrant and search them.")
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("index", help="embed data/chunks and upsert into Qdrant")
    search_parser = commands.add_parser("search", help="retrieve top-k chunks for a question")
    search_parser.add_argument("question")
    search_parser.add_argument("-k", type=int, default=5)
    commands.add_parser("health", help="exit 0 if the collection is ready")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    client = build_qdrant_client(args.url)

    if args.command == "health":
        # A health check must not depend on Ollama, so it gets an embedder that is
        # never called instead of one that verifies the model over the network.
        ready = QdrantVectorStore(client, _NoQueries()).health_check()
        print("ready" if ready else "NOT ready")
        raise SystemExit(0 if ready else 1)

    # Imported here so importing this module (tests, notebooks) never pulls in the
    # embedding stack or triggers a live model check.
    from src.embedding.embedder import Embedder, build_ollama_client

    embedder = Embedder(build_ollama_client(args.ollama_url), ollama_url=args.ollama_url)
    store = QdrantVectorStore(client, embedder)

    if args.command == "index":
        from src.ingestion.pipeline import read_chunks

        chunks = read_chunks()
        if not chunks:
            raise SystemExit("no chunks found; run `python -m src.ingestion.pipeline` first")
        ensure_collection(client)
        started = time.perf_counter()
        embeddings = embedder.embed_batch(chunks)
        embedded_at = time.perf_counter()
        store.upsert(embeddings)
        print(
            f"OK: {len(embeddings)} chunks embedded in {embedded_at - started:.1f}s, "
            f"upserted in {time.perf_counter() - embedded_at:.1f}s; collection now holds "
            f"{store.count()} points"
        )
        return

    for rank, result in enumerate(store.search_with_scores(args.question, args.k), start=1):
        snippet = " ".join(result.chunk.text.split())[:110]
        print(f"{rank}. {result.score:.3f}  {result.chunk.chunk_id}")
        print(f"   {snippet}")


class _NoQueries:
    def embed_query(self, text: str) -> Sequence[float]:
        raise RuntimeError("health checks never embed")


if __name__ == "__main__":
    main()
