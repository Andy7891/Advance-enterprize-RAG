"""Qdrant collection setup: make sure the `chunks` collection exists with the
exact vector configuration the embedding model requires.

Vector size and distance are fixed at creation time in Qdrant — they cannot be
altered on an existing collection. That makes this the one place where a wrong
value is expensive: every vector written afterwards inherits it, and the only
fix is dropping the collection and re-embedding the whole corpus.
"""

from __future__ import annotations

import argparse

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

from src.core.config import settings
from src.embedding.lock import verify_model

COLLECTION_NAME = settings.qdrant_collection

# Read from the model lock rather than typed here, so the collection size can
# never drift from the model that fills it (decision D-2026-09-16-20).
VECTOR_SIZE = settings.embed_dim

# Cosine because nomic-embed-text is trained for it and the chunker already
# measures sentence similarity the same way; dot product would only be
# equivalent if every vector were guaranteed normalised.
DISTANCE = Distance.COSINE

# Fields that queries filter on. doc_id serves the delete-before-reupsert guard,
# category serves filtered search. Without an index Qdrant still filters, but by
# scanning every payload, which grows with the corpus rather than with the result.
PAYLOAD_INDEXES = {"doc_id": PayloadSchemaType.KEYWORD, "category": PayloadSchemaType.KEYWORD}


def build_qdrant_client(url: str) -> QdrantClient:
    """The client every CLI uses to talk to a Qdrant server.

    Accept-Encoding is pinned to gzip because httpx advertises zstd whenever the
    zstandard package is importable (here only as a dependency of langsmith), and
    one live index run failed with "zstd decompressor error: Allocation error" on
    a 100-byte delete response. It could not be reproduced in 200 retries, so the
    root cause is unknown; gzip removes that decoder from the path entirely.
    """
    return QdrantClient(url=url, headers={"Accept-Encoding": "gzip"})


class CollectionConfigMismatch(RuntimeError):
    """An existing collection's vector config differs from what this code expects.

    Raised instead of silently reusing the collection, because inserting 768-dim
    vectors into a collection built for another size fails only at upsert time —
    after the expensive embedding work is already done.
    """


def ensure_collection(
    client: QdrantClient,
    name: str = COLLECTION_NAME,
    size: int = VECTOR_SIZE,
    distance: Distance = DISTANCE,
) -> bool:
    """Create the collection if missing; verify its config if present.

    Returns True if the collection was created, False if it already existed and
    matched — so a caller can report which happened, and re-running is safe.
    """
    created = not client.collection_exists(name)
    if created:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=size, distance=distance),
        )
    else:
        check_collection_config(client, name, size, distance)

    # Run on every call, not only at creation, so a collection made before the
    # indexes existed gains them. Re-creating an existing index is a no-op.
    for field_name, schema in PAYLOAD_INDEXES.items():
        client.create_payload_index(name, field_name, schema, wait=True)
    return created


def check_collection_config(
    client: QdrantClient,
    name: str = COLLECTION_NAME,
    size: int = VECTOR_SIZE,
    distance: Distance = DISTANCE,
) -> None:
    """Raise CollectionConfigMismatch unless an existing collection matches.

    Separate from ensure_collection so a read-only caller (a health check) can
    verify the collection without ever creating one.
    """
    vectors = client.get_collection(name).config.params.vectors
    # A collection can hold several NAMED vectors (a dict) instead of one
    # unnamed vector; this pipeline only ever writes one, so anything else
    # means the collection was built by something other than this code.
    if not isinstance(vectors, VectorParams):
        raise CollectionConfigMismatch(
            f"collection {name!r} uses named vectors; expected a single unnamed vector"
        )

    if vectors.size != size or vectors.distance != distance:
        # Deliberately no auto-repair: recreating would destroy every stored
        # vector, which should only ever be a human decision.
        raise CollectionConfigMismatch(
            f"collection {name!r} has size={vectors.size}, distance={vectors.distance}; "
            f"expected size={size}, distance={distance}. Delete it manually to rebuild."
        )


def main() -> None:
    """CLI entry point: python -m src.vectorstore.setup"""
    parser = argparse.ArgumentParser(description="Create/verify the Qdrant chunks collection.")
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    args = parser.parse_args()

    # A correctly-sized collection is only half the contract; the model that will
    # fill it must be the pinned one too, or the size check proves nothing.
    verify_model(args.ollama_url)

    client = build_qdrant_client(args.url)
    # get_collections doubles as a connectivity check, so an unreachable Qdrant
    # fails here with a connection error rather than inside collection logic.
    client.get_collections()

    created = ensure_collection(client)
    state = "created" if created else "already present, config verified"
    print(
        f"Qdrant at {args.url}: collection {COLLECTION_NAME!r} {state} "
        f"(size={VECTOR_SIZE}, distance={DISTANCE.value})"
    )


if __name__ == "__main__":
    main()
