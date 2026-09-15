"""Token counting: replaces each chunk's character-based estimate with a real
tokenizer count.

Runs as a SEPARATE pass over already-chunked documents rather than inside
`make_chunk`, so chunk creation stays free of any tokenizer dependency and a
corpus can be re-counted with a different tokenizer without re-chunking it.

A caveat worth stating plainly: `cl100k_base` is OpenAI's tokenizer, while this
pipeline currently embeds with `nomic-embed-text` via Ollama, which tokenizes
differently. These counts are therefore a consistent, portable RULER for
comparing chunk sizes to each other — not a prediction of what the embedding
model itself will see. See decision.md, D-2026-09-15-16.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Protocol

from src.ingestion.schemas import DocumentChunk

logger = logging.getLogger(__name__)

DEFAULT_ENCODING = "cl100k_base"


class TokenCounter(Protocol):
    """Anything that can turn text into a token count.

    A Protocol rather than a concrete class for the same reason `Embedder` is
    one in chunker.py: tests inject a trivial deterministic counter, so the
    suite never needs tiktoken's encoding file — which would otherwise make CI
    depend on a network fetch the first time it runs on a fresh runner.
    """

    def count(self, text: str) -> int: ...


class TiktokenCounter:
    """Real token counts from OpenAI's tiktoken."""

    def __init__(self, encoding_name: str = DEFAULT_ENCODING) -> None:
        # Imported here rather than at module top so that importing this module
        # costs nothing for callers who only need the Protocol or a fake.
        import tiktoken

        # Loaded eagerly, in the constructor, so a missing or unfetchable
        # encoding fails at wiring time with an obvious traceback — rather than
        # midway through enriching a 76-document corpus, after the expensive
        # chunking work is already done.
        self._encoding = tiktoken.get_encoding(encoding_name)
        self._encoding_name = encoding_name

    @property
    def encoding_name(self) -> str:
        return self._encoding_name

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text))


def enrich_with_token_counts(
    chunks: list[DocumentChunk], counter: TokenCounter
) -> list[DocumentChunk]:
    """Return NEW chunks carrying real token counts.

    The input chunks are never modified. `DocumentChunk` is frozen, but its
    `metadata` dict is not — mutating it in place would work, and would quietly
    defeat the immutability the class was designed around, so a fresh dict is
    built instead.

    The count is written to BOTH `approx_token_count` and
    `metadata["tokens_approx"]` so the two can never disagree; a caller reading
    either one gets the same number.
    """
    enriched: list[DocumentChunk] = []

    for chunk in chunks:
        tokens = counter.count(chunk.text)
        enriched.append(
            dataclasses.replace(
                chunk,
                approx_token_count=tokens,
                # A new dict, not chunk.metadata mutated, so the caller's
                # original chunk is genuinely left untouched.
                metadata={**chunk.metadata, "tokens_approx": tokens},
            )
        )

    return enriched


def build_tiktoken_counter(encoding_name: str = DEFAULT_ENCODING) -> TokenCounter:
    """Construct the production counter.

    Separate factory for the same reason chunker.py has one: it keeps the
    encoding load (and its potential network fetch) out of import time, so
    nothing pays that cost just by importing this module.
    """
    return TiktokenCounter(encoding_name)
