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
from qdrant_client.models import Distance, VectorParams

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
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=size, distance=distance),
        )
        return True

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
    return False


def main() -> None:
    """CLI entry point: python -m src.vectorstore.setup"""
    parser = argparse.ArgumentParser(description="Create/verify the Qdrant chunks collection.")
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    args = parser.parse_args()

    # A correctly-sized collection is only half the contract; the model that will
    # fill it must be the pinned one too, or the size check proves nothing.
    verify_model(args.ollama_url)

    client = QdrantClient(url=args.url)
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
