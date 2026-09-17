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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from langchain_core.documents import Document
from langchain_experimental.text_splitter import SemanticChunker

from src.core.config import settings
from src.ingestion.loader import LoadedDocument
from src.ingestion.schemas import DocumentChunk, make_chunk

logger = logging.getLogger(__name__)

# Constrained to the four values SemanticChunker actually accepts, so a typo
# like "standard_devation" fails type-checking instead of at runtime, mid-ingest.
BreakpointThresholdType = Literal["percentile", "standard_deviation", "interquartile", "gradient"]


class SentenceEmbedder(Protocol):
    """Structural type matching LangChain's Embeddings interface.

    Named for what the chunker uses it for — embedding individual sentences to
    find topic boundaries — so it is not confused with
    `src.embedding.embedder.Embedder`, which embeds whole chunks for storage
    with prefixes, pinning and validation this interface knows nothing about.

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

    max_chunk_tokens: int = 1500
    """A HARD cap, unlike target_chunk_chars. Semantic boundaries follow
    meaning and can produce a chunk larger than the embedding model accepts --
    measured: a 13,599-char chunk was rejected by nomic-embed-text's 2048-token
    context. Anything over this is re-split at sentence boundaries. Counted with
    the char/4 estimate, which overestimates, so the real margin is wider."""


class Chunker:
    """Turns one LoadedDocument into an ordered list of DocumentChunks."""

    def __init__(self, embedder: SentenceEmbedder, config: ChunkerConfig | None = None) -> None:
        # The embedder is injected rather than constructed here so the choice of
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
        spans = self._recover_spans(lc_documents, document.text, document.source_path)

        spans = self._merge_undersized(spans)
        # The cap runs AFTER merging, so a merge can never push a chunk back over
        # the model's limit -- the guarantee has to be the final step to hold.
        spans = self._split_oversized(spans, document.text, document.source_path)
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

    def _recover_spans(
        self, lc_documents: list[Document], source_text: str, source_name: str
    ) -> list[tuple[str, int, int]]:
        """Locate each chunk's true character span in the original text.

        SemanticChunker's own `start_index` metadata cannot be trusted past
        the first chunk. It rebuilds each chunk by re-joining sentences with a
        single " ", which is shorter than the source wherever sentences were
        separated by a blank line or multiple spaces, and then advances its
        internal position tracker by the REBUILT length — so every offset
        after the first such mismatch drifts further from reality.

        A plain `str.find` is not enough either: that same whitespace
        normalisation means the rebuilt chunk often is not verbatim present in
        the source at all. Matching on the chunk's words with `\\s+` between
        them tolerates any whitespace difference, so the recovered span covers
        the REAL source region — including the blank lines the rebuilt text
        dropped. Consequence worth knowing: `source_text[start:end]` equals the
        chunk only after whitespace normalisation, never byte-for-byte.
        """
        spans: list[tuple[str, int, int]] = []
        search_from = 0

        for doc in lc_documents:
            text = doc.page_content
            span = self._locate(source_text, text, search_from)

            if span is None:
                # Falling back keeps a whole ingestion run alive rather than
                # failing it over one unlocatable chunk, but the offsets are
                # then the untrustworthy estimates described above — so this
                # is logged loudly rather than passing silently.
                logger.warning(
                    "Could not locate chunk verbatim in %s; falling back to "
                    "SemanticChunker's start_index estimate (offsets may be wrong)",
                    source_name,
                )
                start = doc.metadata.get("start_index", 0)
                end = start + len(text)
            else:
                start, end = span
                # Advancing past this chunk stops a later, identical chunk from
                # matching an earlier position and reporting a span that runs
                # backwards relative to its predecessor.
                search_from = end

            spans.append((text, start, end))

        return spans

    @staticmethod
    def _locate(source_text: str, chunk_text: str, search_from: int) -> tuple[int, int] | None:
        """Find chunk_text in source_text, tolerating whitespace differences."""
        # The exact match is tried first purely because it is far cheaper than
        # building and running a regex, and it succeeds for any chunk whose
        # internal whitespace the splitter happened not to alter.
        exact = source_text.find(chunk_text, search_from)
        if exact != -1:
            return exact, exact + len(chunk_text)

        words = [re.escape(word) for word in chunk_text.split()]
        if not words:
            return None

        match = re.compile(r"\s+".join(words)).search(source_text, search_from)
        if match is None:
            return None
        return match.start(), match.end()

    def _split_oversized(
        self, spans: list[tuple[str, int, int]], source_text: str, source_name: str
    ) -> list[tuple[str, int, int]]:
        """Re-split any span exceeding max_chunk_tokens, keeping offsets exact."""
        limit_chars = self._config.max_chunk_tokens * 4
        result: list[tuple[str, int, int]] = []

        for text, start, end in spans:
            if len(text) <= limit_chars:
                result.append((text, start, end))
                continue

            logger.warning(
                "Chunk in %s is %d chars (> %d); splitting to fit the embedding model",
                source_name,
                len(text),
                limit_chars,
            )
            search_from = start
            for piece in self._pack(text, limit_chars):
                # Located within the original span so each piece's offsets point
                # at its own region, not at an identical sentence elsewhere.
                span = self._locate(source_text, piece, search_from)
                piece_start, piece_end = span if span else (search_from, search_from + len(piece))
                result.append((piece, piece_start, piece_end))
                search_from = piece_end

        return result

    @staticmethod
    def _pack(text: str, limit_chars: int) -> list[str]:
        """Greedily pack sentences into pieces no longer than limit_chars."""
        # Same boundary rule as SemanticChunker, so a forced split still lands
        # between sentences wherever the text allows it.
        sentences = [s for s in re.split(r"(?<=[.?!])\s+", text) if s]
        units: list[str] = []
        for sentence in sentences:
            if len(sentence) <= limit_chars:
                units.append(sentence)
                continue
            # A single sentence longer than the cap (a table, a run-on clause)
            # has no sentence boundary to use, so words are the last resort.
            units.extend(Chunker._pack_words(sentence, limit_chars))

        pieces: list[str] = []
        current = ""
        for unit in units:
            candidate = f"{current} {unit}" if current else unit
            if len(candidate) <= limit_chars:
                current = candidate
            else:
                pieces.append(current)
                current = unit
        if current:
            pieces.append(current)
        return pieces

    @staticmethod
    def _pack_words(sentence: str, limit_chars: int) -> list[str]:
        pieces: list[str] = []
        current = ""
        for word in sentence.split():
            candidate = f"{current} {word}" if current else word
            if len(candidate) <= limit_chars or not current:
                current = candidate
            else:
                pieces.append(current)
                current = word
        if current:
            pieces.append(current)
        return pieces

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


def build_ollama_embedder(base_url: str, model: str = settings.embed_model) -> SentenceEmbedder:
    """Construct the production embedder.

    Isolated in a factory so importing this module never opens a network
    connection — which is what lets the test suite import Chunker freely
    without a running Ollama.
    """
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(base_url=base_url, model=model)
