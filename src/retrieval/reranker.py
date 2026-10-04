"""Reranking: take the short list of candidate chunks that retrieval produced, score each
one by reading the question and the chunk text together, and keep the best k.

Retrieval (vector, graph, or the hybrid of both) compares texts one at a time or by
position. A reranker is a cross-encoder: one model reads the question and one chunk as a
single input and returns a relevance score, which is slower but far more precise. It can
only reorder what it is given, so the caller decides how many candidates to hand over.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import cohere

from src.core.config import Settings, settings
from src.ingestion.schemas import DocumentChunk

if TYPE_CHECKING:
    import httpx

DEFAULT_MODEL = "rerank-v3.5"
# the Cohere rerank model used unless the caller names another
REQUEST_TIMEOUT_SECONDS = 30.0
# how long one rerank request may take before the client gives up, instead of the SDK's 300


class RerankResult(Protocol):
    @property
    def index(self) -> int: ...

    # the position, in the documents that were sent, of the document this result scores

    @property
    def relevance_score(self) -> float: ...

    # how relevant the model judged that document to the query, higher is better


class RerankResponse(Protocol):
    @property
    def results(self) -> Sequence[RerankResult]: ...

    # the scored documents, best first, at most as many as were asked for


class RerankClient(Protocol):
    def rerank(
        self, *, model: str, query: str, documents: Sequence[str], top_n: int
    ) -> RerankResponse: ...

    # anything that can score documents against a query, such as cohere.ClientV2


@dataclass(frozen=True, slots=True)
class RerankedChunk:
    """One reranked result: the chunk, the model's relevance score, and where it came in."""

    chunk: DocumentChunk
    # the chunk itself, exactly as the caller passed it in
    relevance_score: float
    # the reranker's score for this chunk, meaningful only next to scores from the same model
    input_rank: int
    # the chunk's 1-based position in the list the caller passed in, before reranking


def build_cohere_client(
    api_key: str, *, httpx_client: httpx.Client | None = None
) -> cohere.ClientV2:
    """A Cohere client that fails fast: no hidden retries and a short timeout."""
    if not api_key.strip():
        # covers a key that was never filled in, so the failure names the fix
        raise ValueError(
            "COHERE_API_KEY is empty: create a key at dashboard.cohere.com and add it to .env"
        )
        # names the setting to fill in rather than failing later with an opaque 401
    return cohere.ClientV2(
        api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
        httpx_client=httpx_client,
    )
    # builds the client with the SDK's own retry-on-429/5xx turned off, so errors surface at once


def _check_k(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        # rejects anything that is not a plain positive integer
        raise ValueError(f"k must be a positive integer, got {k!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        # rejects a value that is not a string, or is empty or only whitespace
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
        # names the argument and the value so the caller can see what was wrong


def _unique_chunks(chunks: Sequence[DocumentChunk]) -> tuple[list[DocumentChunk], list[int]]:
    unique: list[DocumentChunk] = []
    # keeps each distinct chunk, in the order it first appeared
    input_ranks: list[int] = []
    # keeps each kept chunk's 1-based position in the list the caller passed in
    seen: set[str] = set()
    # keeps the chunk ids already kept
    for rank, chunk in enumerate(chunks, start=1):
        # walks the caller's list in order, numbering positions from 1
        if chunk.chunk_id in seen:
            # spots a chunk that already appeared earlier in the list
            continue
            # skips it so one chunk cannot take two places in the result
        seen.add(chunk.chunk_id)
        # remembers this chunk id
        unique.append(chunk)
        # keeps the chunk
        input_ranks.append(rank)
        # keeps the position it came in at
    return unique, input_ranks
    # hands back the distinct chunks and their original positions


class Reranker:
    """Reorders candidate chunks by how well each one answers the question."""

    def __init__(self, client: RerankClient, *, model: str = DEFAULT_MODEL) -> None:
        _check_text("model", model)
        # refuses an empty model name at construction time
        self._client = client
        # keeps the client every rerank request will be sent through
        self._model = model
        # keeps the model name sent with every request

    @classmethod
    def from_settings(
        cls, config: Settings | None = None, *, model: str = DEFAULT_MODEL
    ) -> Reranker:
        """A Reranker using the Cohere key from `.env`."""
        config = config or settings
        # falls back to the app-wide settings when none are passed
        return cls(build_cohere_client(config.cohere_api_key), model=model)
        # builds the client from the configured key and wraps it in a Reranker

    def rerank(self, query: str, chunks: Sequence[DocumentChunk], k: int) -> list[DocumentChunk]:
        """The k chunks that best answer `query`, best first."""
        return [item.chunk for item in self.rerank_with_scores(query, chunks, k)]
        # drops the scores and positions, leaving only the chunks in reranked order

    def rerank_with_scores(
        self, query: str, chunks: Sequence[DocumentChunk], k: int
    ) -> list[RerankedChunk]:
        """The k best results, each with its relevance score and its position before reranking."""
        _check_k(k)
        # refuses a k that is not a positive integer before any request is sent
        _check_text("query", query)
        # refuses an empty question before any request is sent
        candidates, input_ranks = _unique_chunks(chunks)
        # drops repeats so a chunk cannot appear twice in the result
        if not candidates:
            # covers an empty candidate list, where there is nothing to score
            return []
            # answers without spending a request
        response = self._client.rerank(
            model=self._model,
            query=query,
            documents=[chunk.text for chunk in candidates],
            top_n=min(k, len(candidates)),
        )
        # asks the model to score every candidate's text against the query and return the best few
        ranked: list[RerankedChunk] = []
        # collects one RerankedChunk per result the model returned
        for result in response.results:
            # walks the model's results, which name candidates by their position
            if not 0 <= result.index < len(candidates):
                # catches a position outside the list that was sent
                raise ValueError(f"reranker returned an unknown document index {result.index}")
                # refuses it, because a negative index would silently pick the wrong chunk
            ranked.append(
                RerankedChunk(
                    chunk=candidates[result.index],
                    relevance_score=result.relevance_score,
                    input_rank=input_ranks[result.index],
                )
            )
            # pairs the model's score with the chunk it belongs to and where that chunk came in
        ranked.sort(key=lambda item: (-item.relevance_score, item.input_rank))
        # orders by score, then by original position so equal scores are deterministic
        return ranked[:k]
        # keeps only the k best results


class IdentityReranker:
    """A reranker stand-in that keeps the earlier retrieval stage's own order, unscored --
    needs no Cohere key. Used when `COHERE_API_KEY` isn't configured, so a real pipeline can
    still run end to end without a paid key, at the cost of losing the real reranking step."""

    def rerank(self, query: str, chunks: Sequence[DocumentChunk], k: int) -> list[DocumentChunk]:
        """The first k chunks, unchanged -- no real reranking happens."""
        return [item.chunk for item in self.rerank_with_scores(query, chunks, k)]
        # drops the scores and positions, matching Reranker.rerank's own return shape

    def rerank_with_scores(
        self, query: str, chunks: Sequence[DocumentChunk], k: int
    ) -> list[RerankedChunk]:
        """The first k chunks, each given a falling fake score so ties break by input order."""
        _check_k(k)
        # refuses a k that is not a positive integer, matching Reranker's own validation
        _check_text("query", query)
        # refuses an empty question, matching Reranker's own validation, even though this
        # stand-in never actually sends the query anywhere -- keeps the two classes'
        # observable behavior consistent for any caller that validates by trying both
        return [
            RerankedChunk(chunk=chunk, relevance_score=1.0 - 0.01 * i, input_rank=i + 1)
            for i, chunk in enumerate(chunks[:k])
        ]
        # keeps the first k chunks in the order they arrived, with falling fake scores
