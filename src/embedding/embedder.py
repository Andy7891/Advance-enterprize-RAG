"""Embedding for storage and search: list[DocumentChunk] -> list[Embedding], and
question -> query vector.

This is the only path by which text becomes vectors, so it is where
the P3-M2 guarantees are enforced rather than merely configured: the pinned model
is verified, the document prefix is applied, over-length input is refused
instead of truncated, and every vector is checked before anything downstream
can store it.
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx
import ollama

from src.core.config import settings
from src.embedding.lock import as_document, as_query, verify_model
from src.ingestion.schemas import DocumentChunk, Embedding

logger = logging.getLogger(__name__)

# Loose enough for float32 rounding, tight enough that a model that does not
# normalise (typical norms are far from 1) is caught.
NORM_TOLERANCE = 1e-3


class EmbeddingError(RuntimeError):
    """A batch could not be embedded, or produced vectors that cannot be trusted."""


class EmbedClient(Protocol):
    """The one method of `ollama.Client` this module relies on.

    A Protocol so tests inject a fake with no server — and so the dependency on
    the ollama package is exactly one method wide.
    """

    # Returns Any because ollama's EmbedResponse is a pydantic model that only
    # supports ["embeddings"] access, not a Mapping — and a test fake returns a
    # plain dict. Subscripting is the whole contract either way.
    def embed(
        self, model: str = ..., input: str | Sequence[str] = ..., truncate: bool | None = ...
    ) -> Any: ...


class Embedder:
    """Embeds DocumentChunks with the pinned model, in batches."""

    def __init__(
        self,
        client: EmbedClient,
        *,
        ollama_url: str = settings.ollama_url,
        model: str = settings.embed_model,
        dim: int = settings.embed_dim,
        batch_size: int = settings.embed_batch_size,
        verify: Callable[..., str] = verify_model,
    ) -> None:
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        # INTERVIEW: verification happens at construction, so an Embedder that
        # exists is proof the served weights match the pin. A check inside
        # embed_batch could be skipped by a future code path; a constructor can't.
        self._digest = verify(ollama_url, model=model)
        self._client = client
        self._model = model
        self._dim = dim
        self._batch_size = batch_size

    def embed_batch(self, chunks: Sequence[DocumentChunk]) -> list[Embedding]:
        """Return one Embedding per chunk, in the same order as `chunks`."""
        embeddings: list[Embedding] = []
        for start in range(0, len(chunks), self._batch_size):
            batch = chunks[start : start + self._batch_size]
            vectors = self._embed_one_batch(batch)
            embeddings.extend(
                Embedding(
                    chunk=chunk,
                    vector=tuple(vector),
                    model=self._model,
                    model_digest=self._digest,
                    document_prefix=settings.embed_document_prefix,
                )
                # strict=True is a second line of defence behind the count check:
                # zip would otherwise stop at the shorter list without a word.
                for chunk, vector in zip(batch, vectors, strict=True)
            )
            logger.info("Embedded %d/%d chunks", len(embeddings), len(chunks))
        return embeddings

    def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed a search question with the query-side prefix."""
        # INTERVIEW: the same class embeds both sides so the model check, the
        # truncation policy and the validation can never differ between stored
        # vectors and the questions compared against them — only the prefix does.
        (vector,) = self._embed_texts(["<query>"], [as_query(text)])
        return tuple(vector)

    def _embed_one_batch(self, batch: Sequence[DocumentChunk]) -> list[list[float]]:
        return self._embed_texts(
            [chunk.chunk_id for chunk in batch],
            # INTERVIEW: the document prefix is applied here, not by callers,
            # because a stored vector without it is wrong in a way no later
            # step can detect.
            [as_document(chunk.text) for chunk in batch],
        )

    def _embed_texts(self, ids: list[str], texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.embed(
                model=self._model,
                input=texts,
                # INTERVIEW: Ollama truncates over-length input by default and
                # returns a normal-looking vector for text it never read.
                # Refusing turns silent recall loss into an error.
                truncate=False,
            )
        # Caught by type, so a bug in our own code still crashes as itself instead
        # of being reported as an embedding failure. Re-raised with the chunk IDs,
        # because the server's message ("input length exceeds...") names none.
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError) as exc:
            raise EmbeddingError(f"embedding failed for {ids}: {exc}") from exc

        vectors = list(response["embeddings"])
        self._validate(ids, vectors)
        return vectors

    def _validate(self, ids: list[str], vectors: list[list[float]]) -> None:
        # INTERVIEW: every failure below would otherwise reach Qdrant as a
        # plausible-looking vector — misalignment in particular gives each chunk
        # a neighbour's meaning with no error anywhere.
        if len(vectors) != len(ids):
            raise EmbeddingError(f"sent {len(ids)} texts but received {len(vectors)} vectors")
        for chunk_id, vector in zip(ids, vectors):
            if len(vector) != self._dim:
                raise EmbeddingError(
                    f"{chunk_id}: vector has {len(vector)} dims, expected {self._dim} "
                    "(is a different model being served?)"
                )
            if not all(math.isfinite(x) for x in vector):
                raise EmbeddingError(f"{chunk_id}: vector contains NaN or infinity")
            norm = math.sqrt(sum(x * x for x in vector))
            if abs(norm - 1.0) > NORM_TOLERANCE:
                # Cosine search tolerates any norm, but nomic-embed-text always
                # returns unit vectors, so a different norm means a different or
                # misbehaving model rather than a harmless variation.
                raise EmbeddingError(f"{chunk_id}: vector norm is {norm:.4f}, expected 1.0")


def build_ollama_client(ollama_url: str) -> EmbedClient:
    return ollama.Client(host=ollama_url)


def main() -> None:
    """CLI: python -m src.embedding.embedder — embed the chunks on disk and report."""
    parser = argparse.ArgumentParser(description="Embed chunks written by the ingestion pipeline.")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--batch-size", type=int, default=settings.embed_batch_size)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx logs every request at INFO, which buries the per-batch progress lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Imported here, not at module level, so the embedder itself does not depend
    # on the chunking stack (LangChain, SemanticChunker) just to be imported.
    from src.ingestion.pipeline import read_chunks

    chunks = read_chunks()
    if not chunks:
        raise SystemExit("no chunks found; run `python -m src.ingestion.pipeline` first")

    embedder = Embedder(
        build_ollama_client(args.ollama_url),
        ollama_url=args.ollama_url,
        batch_size=args.batch_size,
    )
    started = time.perf_counter()
    embeddings = embedder.embed_batch(chunks)
    elapsed = time.perf_counter() - started

    print(
        f"OK: embedded {len(embeddings)} chunks in {elapsed:.1f}s "
        f"(batch={args.batch_size}, dim={len(embeddings[0].vector)}, "
        f"digest={embeddings[0].model_digest[:12]})"
    )


if __name__ == "__main__":
    main()
