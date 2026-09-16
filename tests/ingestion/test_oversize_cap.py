"""The hard token cap: no chunk may exceed what the embedding model accepts."""

from pathlib import Path

from src.ingestion.chunker import Chunker, ChunkerConfig
from src.ingestion.loader import LoadedDocument


def _doc(text: str) -> LoadedDocument:
    return LoadedDocument(
        source_path="company/big.md",
        category="company",
        title="t",
        format="md",
        text=text,
        char_count=len(text),
    )


def test_oversized_span_is_split_under_the_cap(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(max_chunk_tokens=10))  # 40 chars
    text = " ".join(f"Acme sentence number {i}." for i in range(20))
    pieces = chunker._split_oversized([(text, 0, len(text))], text, "t")
    assert len(pieces) > 1
    assert all(len(p[0]) <= 40 for p in pieces)


def test_split_keeps_every_word(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(max_chunk_tokens=10))
    text = " ".join(f"Acme sentence number {i}." for i in range(20))
    pieces = chunker._split_oversized([(text, 0, len(text))], text, "t")
    assert " ".join(p[0] for p in pieces).split() == text.split()


def test_split_prefers_sentence_boundaries(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(max_chunk_tokens=10))
    text = " ".join(f"Acme sentence number {i}." for i in range(20))
    pieces = chunker._split_oversized([(text, 0, len(text))], text, "t")
    assert all(p[0].endswith(".") for p in pieces)


def test_split_pieces_have_correct_offsets(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(max_chunk_tokens=10))
    text = " ".join(f"Acme sentence number {i}." for i in range(20))
    for piece, start, end in chunker._split_oversized([(text, 0, len(text))], text, "t"):
        assert text[start:end].split() == piece.split()


def test_single_giant_sentence_falls_back_to_words(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(max_chunk_tokens=5))  # 20 chars
    text = "word " * 30  # no sentence punctuation at all
    pieces = chunker._split_oversized([(text.strip(), 0, len(text))], text, "t")
    assert all(len(p[0]) <= 20 for p in pieces)


def test_small_spans_are_untouched(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(max_chunk_tokens=100))
    span = [("short text.", 0, 11)]
    assert chunker._split_oversized(span, "short text.", "t") == span


def test_end_to_end_no_chunk_exceeds_cap(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(max_chunk_tokens=10, min_chunk_chars=1))
    text = " ".join(f"Acme fact {i} is important." for i in range(40))
    chunks = chunker.chunk(_doc(text), Path("data"))
    assert all(c.char_count <= 40 for c in chunks)
