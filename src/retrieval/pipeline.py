"""Retrieval pipeline: chain the hybrid retriever and the reranker behind one call, so a
caller gets one ranked, explainable answer instead of wiring two classes together and
knowing the pool-size math that makes reranking work at all.

Retrieval (vector + graph, fused by rank) finds a candidate pool; reranking (a
cross-encoder) reorders that pool by reading the question and each chunk together. Neither
piece knows the other exists -- this module is the one place that does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from src.ingestion.schemas import DocumentChunk
from src.retrieval.hybrid_retriever import FusedChunk
from src.retrieval.reranker import RerankedChunk

DEFAULT_POOL_MULTIPLIER = 3
# how many times k the hybrid stage is asked for, before the pool floor is applied
DEFAULT_POOL_FLOOR = 20
# the smallest candidate pool ever requested, regardless of how small k is


class HybridSearcher(Protocol):
    def retrieve_with_scores(self, query: str, k: int) -> list[FusedChunk]: ...

    # anything that can return a fused, scored ranking for a question, such as HybridRetriever


class Reranking(Protocol):
    def rerank_with_scores(
        self, query: str, chunks: Sequence[DocumentChunk], k: int
    ) -> list[RerankedChunk]: ...

    # anything that can reorder a candidate list by relevance, such as Reranker


@dataclass(frozen=True, slots=True)
class RankedChunk:
    """One final result: the chunk, its place in the answer, and every stage's own verdict."""

    chunk: DocumentChunk
    # the chunk itself, exactly as the reranker returned it
    final_rank: int
    # this chunk's 1-based position in the pipeline's own final answer
    relevance_score: float
    # the reranker's score for this chunk, meaningful only next to scores from the same call
    hybrid_score: float
    # the fused vector+graph score the hybrid stage gave this chunk, before reranking
    hybrid_rank: int
    # this chunk's 1-based position in the pool the hybrid stage handed to the reranker
    vector_rank: int | None
    # this chunk's 1-based position in the vector list, or None if vector search missed it
    graph_rank: int | None
    # this chunk's 1-based position in the graph list, or None if graph retrieval missed it
    vector_score: float | None
    # the raw cosine similarity vector search gave it, or None if vector search missed it


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The answer to one query: the question itself, plus every chunk that answers it, ranked."""

    query: str
    # the question this result answers, exactly as it was asked
    results: tuple[RankedChunk, ...]
    # every returned chunk, best first; a tuple so the frozen result is immutable all the way down


def _check_k(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        # rejects anything that is not a plain positive integer
        raise ValueError(f"k must be a positive integer, got {k!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip():
        # rejects a value that is not a string, or is empty or only whitespace
        raise ValueError(f"query must be a non-empty string, got {query!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        # rejects anything that is not a plain integer of at least 1
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
        # names the argument and the value so the caller can see what was wrong


class RetrievalPipeline:
    """Chains hybrid retrieval and reranking into one call: a query in, a ranked answer out."""

    def __init__(
        self,
        hybrid: HybridSearcher,
        reranker: Reranking,
        *,
        pool_multiplier: int = DEFAULT_POOL_MULTIPLIER,
        pool_floor: int = DEFAULT_POOL_FLOOR,
    ) -> None:
        _check_positive_int("pool_multiplier", pool_multiplier)
        # refuses a multiplier that would ask the hybrid stage for nothing
        _check_positive_int("pool_floor", pool_floor)
        # refuses a floor that would ask the hybrid stage for nothing
        self._hybrid = hybrid
        # keeps the hybrid retriever every query is sent through first
        self._reranker = reranker
        # keeps the reranker every hybrid pool is sent through second
        self._pool_multiplier = pool_multiplier
        # keeps how many times k the hybrid stage is asked for
        self._pool_floor = pool_floor
        # keeps the smallest pool ever requested, regardless of k

    def retrieve(self, query: str, k: int) -> RetrievalResult:
        """The k best chunks for `query`: hybrid-retrieved, then reranked, best first."""
        _check_k(k)
        # refuses a k that is not a positive integer before either stage is called
        _check_query(query)
        # refuses an empty question before either stage is called

        pool_size = max(k * self._pool_multiplier, self._pool_floor)
        # works out how many candidates the hybrid stage should hand to the reranker
        pool = self._hybrid.retrieve_with_scores(query, pool_size)
        # asks the hybrid stage for its top `pool_size` fused, scored candidates
        hybrid_by_id = {item.chunk.chunk_id: item for item in pool}
        # indexes the pool by chunk_id, so each reranked chunk's hybrid data can be found again

        chunks = [item.chunk for item in pool]
        # keeps only the chunk objects, in hybrid order, for the reranker to score
        reranked = self._reranker.rerank_with_scores(query, chunks, k)
        # asks the reranker to pick and order the best k chunks of the pool

        results = tuple(
            RankedChunk(
                chunk=item.chunk,
                final_rank=rank,
                relevance_score=item.relevance_score,
                hybrid_score=hybrid_by_id[item.chunk.chunk_id].score,
                hybrid_rank=item.input_rank,
                vector_rank=hybrid_by_id[item.chunk.chunk_id].vector_rank,
                graph_rank=hybrid_by_id[item.chunk.chunk_id].graph_rank,
                vector_score=hybrid_by_id[item.chunk.chunk_id].vector_score,
            )
            for rank, item in enumerate(reranked, start=1)
        )
        # builds one RankedChunk per reranked result, joining each one back to its hybrid record
        return RetrievalResult(query=query, results=results)
        # returns the whole answer as one object: the question and its ranked chunks
