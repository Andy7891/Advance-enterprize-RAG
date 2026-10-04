"""Hybrid retrieval: run vector search and graph retrieval on the same question, then
merge their two ranked lists into one with weighted Reciprocal Rank Fusion (RRF).

Vector search gives each chunk a cosine score; graph retrieval gives only an ordering.
RRF sidesteps that mismatch by using ranks alone: every list adds
`weight / (rrf_c + rank)` to each chunk it contains, and the sums decide the order.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from src.ingestion.schemas import DocumentChunk
from src.vectorstore.store import ScoredChunk

RRF_C = 60
# the rank-smoothing constant from the original RRF paper
CANDIDATE_MULTIPLIER = 3
# how many times k each retriever is asked for before the lists are fused
DEFAULT_WEIGHT = 1.0
# the weight each retriever gets when the caller does not choose one


class VectorSearcher(Protocol):
    def search_with_scores(self, query_text: str, k: int) -> list[ScoredChunk]: ...

    # anything that can return scored chunks for a question, such as QdrantVectorStore


class GraphSearcher(Protocol):
    def retrieve(self, query: str, k: int) -> list[DocumentChunk]: ...

    # anything that can return ranked chunks for a question, such as GraphRetriever


@dataclass(frozen=True, slots=True)
class FusedChunk:
    """One fused result: the chunk, its fused score, and where each retriever ranked it."""

    chunk: DocumentChunk
    # the chunk itself, taken from whichever list contained it first
    score: float
    # the summed weighted reciprocal-rank contribution from both lists
    vector_rank: int | None
    # the chunk's 1-based position in the vector list, or None if vector search missed it
    graph_rank: int | None
    # the chunk's 1-based position in the graph list, or None if graph retrieval missed it
    vector_score: float | None
    # the raw cosine similarity vector search gave it, or None if vector search missed it


def _check_number(name: str, value: float, *, minimum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        # rejects anything that is not a plain int or float, including True and False
        raise ValueError(f"{name} must be a finite number >= {minimum}, got {value!r}")
        # names the argument and the value so the caller can see what was wrong
    if not math.isfinite(value) or value < minimum:
        # rejects NaN, infinity, and anything below the allowed minimum
        raise ValueError(f"{name} must be a finite number >= {minimum}, got {value!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        # rejects anything that is not a plain integer of at least 1
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_weights(vector_weight: float, graph_weight: float) -> None:
    _check_number("vector_weight", vector_weight, minimum=0)
    # confirms the vector weight is a finite number that is not negative
    _check_number("graph_weight", graph_weight, minimum=0)
    # confirms the graph weight is a finite number that is not negative
    if vector_weight == 0 and graph_weight == 0:
        # catches the one combination that would give every chunk a score of zero
        raise ValueError("vector_weight and graph_weight cannot both be 0")
        # explains why two individually valid weights were still rejected


def fuse_rankings(
    vector_hits: Sequence[ScoredChunk],
    graph_hits: Sequence[DocumentChunk],
    *,
    vector_weight: float,
    graph_weight: float,
    rrf_c: float = RRF_C,
) -> list[FusedChunk]:
    """Merge a scored vector ranking and a plain graph ranking, best chunk first."""
    _check_weights(vector_weight, graph_weight)
    # refuses unusable weights before any arithmetic happens
    _check_number("rrf_c", rrf_c, minimum=0)
    # refuses a negative or non-numeric smoothing constant

    chunks: dict[str, DocumentChunk] = {}
    # keeps one DocumentChunk per chunk_id, whichever list produced it first
    scores: dict[str, float] = {}
    # keeps the running fused score for each chunk_id
    vector_ranks: dict[str, int] = {}
    # keeps each chunk_id's 1-based position in the vector list
    graph_ranks: dict[str, int] = {}
    # keeps each chunk_id's 1-based position in the graph list
    vector_scores: dict[str, float] = {}
    # keeps each chunk_id's raw cosine similarity from the vector list

    for rank, hit in enumerate(vector_hits, start=1):
        # walks the vector list from best to worst, numbering positions from 1
        chunk_id = hit.chunk.chunk_id
        # reads the id that identifies this chunk in both lists
        if chunk_id in vector_ranks:
            # spots a chunk that already appeared earlier in this same list
            continue
            # skips it so a repeat cannot be counted twice
        vector_ranks[chunk_id] = rank
        # records where the vector list ranked this chunk
        vector_scores[chunk_id] = hit.score
        # records the cosine similarity the vector list gave this chunk
        chunks.setdefault(chunk_id, hit.chunk)
        # remembers the chunk object if no earlier list has supplied it
        scores[chunk_id] = scores.get(chunk_id, 0.0) + vector_weight / (rrf_c + rank)
        # adds the vector list's weighted reciprocal-rank contribution

    for rank, chunk in enumerate(graph_hits, start=1):
        # walks the graph list from best to worst, numbering positions from 1
        chunk_id = chunk.chunk_id
        # reads the id that identifies this chunk in both lists
        if chunk_id in graph_ranks:
            # spots a chunk that already appeared earlier in this same list
            continue
            # skips it so a repeat cannot be counted twice
        graph_ranks[chunk_id] = rank
        # records where the graph list ranked this chunk
        chunks.setdefault(chunk_id, chunk)
        # remembers the chunk object if no earlier list has supplied it
        scores[chunk_id] = scores.get(chunk_id, 0.0) + graph_weight / (rrf_c + rank)
        # adds the graph list's weighted reciprocal-rank contribution

    fused = [
        FusedChunk(
            chunk=chunks[chunk_id],
            score=scores[chunk_id],
            vector_rank=vector_ranks.get(chunk_id),
            graph_rank=graph_ranks.get(chunk_id),
            vector_score=vector_scores.get(chunk_id),
        )
        for chunk_id in chunks
    ]
    # builds one FusedChunk per distinct chunk_id seen in either list

    fused.sort(
        key=lambda item: (
            -item.score,
            math.inf if item.vector_rank is None else item.vector_rank,
            math.inf if item.graph_rank is None else item.graph_rank,
        )
    )
    # orders by fused score, then by vector rank, then by graph rank, so ties are deterministic
    return fused
    # hands back every fused chunk, best first


class HybridRetriever:
    """Runs vector search and graph retrieval on one question and fuses the two rankings."""

    def __init__(
        self,
        vector_store: VectorSearcher,
        graph_retriever: GraphSearcher,
        *,
        default_vector_weight: float = DEFAULT_WEIGHT,
        default_graph_weight: float = DEFAULT_WEIGHT,
        rrf_c: float = RRF_C,
        candidate_multiplier: int = CANDIDATE_MULTIPLIER,
    ) -> None:
        _check_weights(default_vector_weight, default_graph_weight)
        # refuses unusable default weights at construction time
        _check_number("rrf_c", rrf_c, minimum=0)
        # refuses a negative or non-numeric smoothing constant at construction time
        _check_positive_int("candidate_multiplier", candidate_multiplier)
        # refuses a multiplier that would ask each retriever for nothing
        self._vector_store = vector_store
        # keeps the vector retriever that every question will be sent to
        self._graph_retriever = graph_retriever
        # keeps the graph retriever that every question will be sent to
        self._default_vector_weight = default_vector_weight
        # keeps the vector weight used when a call does not pass one
        self._default_graph_weight = default_graph_weight
        # keeps the graph weight used when a call does not pass one
        self._rrf_c = rrf_c
        # keeps the smoothing constant used in every fusion
        self._candidate_multiplier = candidate_multiplier
        # keeps how many times k each retriever is asked for

    def retrieve(
        self,
        query: str,
        k: int,
        vector_weight: float | None = None,
        graph_weight: float | None = None,
    ) -> list[DocumentChunk]:
        """The k best chunks after fusing both rankings, best first."""
        fused = self.retrieve_with_scores(query, k, vector_weight, graph_weight)
        # runs the full fusion, keeping the scores and ranks for a moment
        return [item.chunk for item in fused]
        # drops the scores and ranks, leaving only the chunks in fused order

    def retrieve_with_scores(
        self,
        query: str,
        k: int,
        vector_weight: float | None = None,
        graph_weight: float | None = None,
    ) -> list[FusedChunk]:
        """The k best fused results, each with its fused score and per-retriever ranks."""
        _check_positive_int("k", k)
        # refuses a k that is not a positive integer before any retriever is called
        use_vector = self._default_vector_weight if vector_weight is None else vector_weight
        # picks the caller's vector weight, or the default when none was passed
        use_graph = self._default_graph_weight if graph_weight is None else graph_weight
        # picks the caller's graph weight, or the default when none was passed
        _check_weights(use_vector, use_graph)
        # refuses unusable weights before any retriever is called

        depth = k * self._candidate_multiplier
        # works out how many candidates each retriever is asked for
        vector_hits = self._vector_store.search_with_scores(query, depth)
        # asks vector search for its top candidates, with cosine scores
        graph_hits = self._graph_retriever.retrieve(query, depth)
        # asks graph retrieval for its top candidates, in ranked order

        fused = fuse_rankings(
            vector_hits,
            graph_hits,
            vector_weight=use_vector,
            graph_weight=use_graph,
            rrf_c=self._rrf_c,
        )
        # merges the two candidate lists into one fused ranking
        return fused[:k]
        # keeps only the k best fused results
