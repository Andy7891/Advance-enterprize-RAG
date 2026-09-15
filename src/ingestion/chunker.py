"""Semantic chunking: LoadedDocument -> list[DocumentChunk].

Chunking is the highest-leverage decision in a RAG pipeline: embeddings,
reranking and prompting all operate on whatever boundaries this module
chooses, and a bad boundary caps retrieval quality permanently.

This implementation uses SEMANTIC chunking (Kamradt's "5 Levels of Text
Splitting", level 4): instead of cutting at fixed character counts, it
embeds each sentence, measures the cosine distance between consecutive
sentences, and cuts where meaning shifts. See docs/decision.md for why this
was chosen over recursive character splitting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from langchain_experimental.text_splitter import SemanticChunker

from src.ingestion.loader import LoadedDocument
from src.ingestion.schemas import DocumentChunk, make_chunk

logger = logging.getLogger(__name__)

# Constrained to the four values SemanticChunker actually accepts, so a typo
# like "standard_devation" fails type-checking instead of at runtime, mid-ingest.
BreakpointThresholdType = Literal["percentile", "standard_deviation", "interquartile", "gradient"]


class Embedder(Protocol):
    """Structural type matching LangChain's Embeddings interface.

    Declared as a Protocol rather than importing LangChain's base class so
    tests can inject a trivial deterministic fake without any LangChain
    machinery, network access, or a running Ollama — which is what keeps the
    test suite fast and offline (a real embedder would make CI depend on a
    model server being up).
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    """Tunable chunking parameters, separated from the Chunker itself.

    Kept as its own frozen object so an experiment can hold several configs
    side by side and diff their output on the same corpus — which is how you
    actually tune chunking, rather than editing constants in place and losing
    the comparison.
    """

    breakpoint_threshold_type: BreakpointThresholdType = "standard_deviation"
    """Cut only where sentence-to-sentence distance is a statistical outlier
    for THIS document. Unlike 'percentile', this cuts nothing in a genuinely
    uniform document instead of inventing boundaries that aren't there — at
    the cost of wildly varying chunk counts between documents."""

    breakpoint_threshold_amount: float = 1.5
    """How many standard deviations above the mean counts as a breakpoint.
    Lower cuts more aggressively. 1.5 is a starting point to tune against
    your own corpus, not a derived optimum."""

    target_chunk_chars: int = 500
    """A SOFT cap, not a guarantee. Semantic chunking derives boundaries from
    meaning, so it cannot honour a hard size limit without overriding the very
    signal it exists to follow. Chunks exceeding this are logged, not split,
    so an over-large chunk is visible rather than silently mangled."""

    min_chunk_chars: int = 100
    """Chunks below this are merged BACKWARDS into their predecessor. A
    30-character fragment embeds to near-noise and pollutes retrieval with
    context-free hits; merging keeps the content reachable instead of
    dropping it."""

    buffer_size: int = 1
    """How many neighbouring sentences are grouped before embedding. >1
    smooths out single odd sentences at the cost of blurring genuine sharp
    topic changes."""


class Chunker:
    """Turns one LoadedDocument into an ordered list of DocumentChunks."""

    def __init__(self, embedder: Embedder, config: ChunkerConfig | None = None) -> None:
        # Embedder is injected rather than constructed here so the choice of
        # provider (Ollama today, Cohere later) never becomes hardcoded into
        # the chunking logic, and tests can pass a fake.
        self._embedder = embedder
        self._config = config or ChunkerConfig()
        self._splitter = SemanticChunker(
            embeddings=embedder,  # type: ignore[arg-type]
            breakpoint_threshold_type=self._config.breakpoint_threshold_type,
            breakpoint_threshold_amount=self._config.breakpoint_threshold_amount,
            buffer_size=self._config.buffer_size,
            # Without this, offsets back into the original text are lost and a
            # UI could never highlight which span an answer actually came from.
            add_start_index=True,
        )

    def chunk(self, document: LoadedDocument, data_root: Path) -> list[DocumentChunk]:
        """Split one document into DocumentChunks, in reading order."""
        # An empty document would make the embedder fail on a zero-length
        # input; returning early is cheaper and clearer than defending
        # against that deeper inside the splitter.
        if not document.text.strip():
            logger.warning("Skipping %s: no text content", document.source_path)
            return []

        lc_documents = self._splitter.create_documents([document.text])

        # Offsets are recovered before merging, because merging changes
        # indices but must preserve each surviving chunk's ORIGINAL span.
        spans = [
            (
                doc.page_content,
                doc.metadata.get("start_index", 0),
                doc.metadata.get("start_index", 0) + len(doc.page_content),
            )
            for doc in lc_documents
        ]

        spans = self._merge_undersized(spans)
        self._warn_oversized(spans, document.source_path)

        source_path = (data_root / document.source_path).resolve()
        return [
            make_chunk(
                source_path=source_path,
                data_root=data_root,
                title=document.title,
                chunk_index=index,
                text=text,
                start_char=start,
                end_char=end,
            )
            # Index is assigned only AFTER merging, so chunk_ids are always
            # a contiguous 0..N-1 run with no gaps where a merge removed one.
            for index, (text, start, end) in enumerate(spans)
        ]

    def _merge_undersized(self, spans: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
        """Fold too-small chunks backwards into their predecessor."""
        merged: list[tuple[str, int, int]] = []
        for text, start, end in spans:
            # The first chunk has no predecessor to merge into, so it is kept
            # regardless of size rather than dropped (which would lose content).
            if merged and len(text) < self._config.min_chunk_chars:
                prev_text, prev_start, _ = merged[-1]
                # The merged span runs from the earlier chunk's start to this
                # one's end, so the offset pair still describes one contiguous
                # region of the source document.
                merged[-1] = (prev_text + " " + text, prev_start, end)
                continue
            merged.append((text, start, end))
        return merged

    def _warn_oversized(self, spans: list[tuple[str, int, int]], source: str) -> None:
        # Logged rather than raised: an over-large chunk is a tuning signal,
        # not a correctness failure, and aborting ingestion over it would be
        # far more disruptive than embedding one bulky chunk.
        for index, (text, _, _) in enumerate(spans):
            if len(text) > self._config.target_chunk_chars:
                logger.info(
                    "Chunk %d of %s is %d chars (target %d) — consider lowering "
                    "breakpoint_threshold_amount",
                    index,
                    source,
                    len(text),
                    self._config.target_chunk_chars,
                )

    def chunk_all(self, documents: list[LoadedDocument], data_root: Path) -> list[DocumentChunk]:
        """Chunk many documents, preserving per-document chunk ordering."""
        chunks: list[DocumentChunk] = []
        for document in documents:
            chunks.extend(self.chunk(document, data_root))
        return chunks


def build_ollama_embedder(base_url: str, model: str = "nomic-embed-text") -> Embedder:
    """Construct the production embedder.

    Isolated in a factory so importing this module never opens a network
    connection — which is what lets the test suite import Chunker freely
    without a running Ollama.
    """
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(base_url=base_url, model=model)
