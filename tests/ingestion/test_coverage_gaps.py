"""Tests for code paths that had ZERO coverage before P2-M5.

Each test here exists because `pytest --cov` proved the lines it exercises were
never executed by the 66 tests that already passed. They are grouped in one
file rather than scattered, so the "what coverage found" story stays readable;
they would normally live beside their module's other tests.

The most important one is `test_merge_branch_actually_executes`: before this
file existed, `test_undersized_chunks_are_merged_backwards` passed while the
merge code never ran at all.
"""

from pathlib import Path

import pytest
from langchain_core.documents import Document

from src.ingestion.chunker import Chunker, ChunkerConfig
from src.ingestion.enrichment import TiktokenCounter
from src.ingestion.loader import LoadedDocument
from src.ingestion.schemas import infer_category

# --------------------------------------------------------------------------
# chunker.py — _merge_undersized's merge body (was lines 218-223, uncovered)
# --------------------------------------------------------------------------


def test_merge_branch_actually_executes(fake_embedder):
    """Crafted spans guarantee the merge branch runs.

    Driving this through the real splitter is what failed silently before: the
    splitter returned a single chunk, so `if merged and ...` was never true and
    the assertion `len(chunks) == 1` held for a reason unrelated to merging.
    Calling the method directly removes that ambiguity entirely.
    """
    chunker = Chunker(fake_embedder, ChunkerConfig(min_chunk_chars=5))

    merged = chunker._merge_undersized([("Long enough text", 0, 16), ("Hi", 16, 18)])

    assert len(merged) == 1
    assert merged[0][0] == "Long enough text Hi"


def test_merge_preserves_the_outer_span_boundaries(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(min_chunk_chars=5))

    merged = chunker._merge_undersized([("Long enough text", 10, 26), ("Hi", 26, 28)])

    # The merged span must run from the FIRST chunk's start to the LAST one's
    # end, or the offsets would no longer describe one contiguous region.
    assert merged[0][1] == 10
    assert merged[0][2] == 28


def test_first_chunk_is_never_merged_away_even_when_tiny(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(min_chunk_chars=10_000))

    merged = chunker._merge_undersized([("Hi", 0, 2), ("Also short", 2, 12)])

    # With nothing before it to merge into, dropping the first chunk would
    # silently lose content -- so it survives regardless of size.
    assert merged[0][0].startswith("Hi")
    assert len(merged) == 1


def test_consecutive_tiny_chunks_collapse_into_one(fake_embedder):
    chunker = Chunker(fake_embedder, ChunkerConfig(min_chunk_chars=100))

    merged = chunker._merge_undersized([("First", 0, 5), ("a", 5, 6), ("b", 6, 7)])

    # Each tiny chunk merges into whatever is CURRENTLY last, so three become
    # one rather than the second and third pairing off separately.
    assert len(merged) == 1
    assert merged[0][0] == "First a b"


# --------------------------------------------------------------------------
# chunker.py — _locate's two failure branches (was lines 204, 208, uncovered)
# --------------------------------------------------------------------------


def test_locate_returns_none_for_text_with_no_words(fake_embedder):
    chunker = Chunker(fake_embedder)

    # Whitespace-only text produces an empty word list, which would otherwise
    # build the regex `""` and match at position 0 of anything.
    assert chunker._locate("some source text", "   ", 0) is None


def test_locate_returns_none_when_text_is_genuinely_absent(fake_embedder):
    chunker = Chunker(fake_embedder)

    assert chunker._locate("the source document", "completely different words", 0) is None


def test_locate_finds_text_despite_differing_whitespace(fake_embedder):
    chunker = Chunker(fake_embedder)

    span = chunker._locate("Alpha one.\n\nBeta two.", "Alpha one. Beta two.", 0)

    # The regex path must succeed where a plain str.find fails -- this is the
    # exact case the offset-drift fix exists for.
    assert span == (0, 21)


# --------------------------------------------------------------------------
# chunker.py — the fallback warning path (was lines 174-180, uncovered)
# --------------------------------------------------------------------------


def test_unlocatable_chunk_falls_back_and_warns(fake_embedder, monkeypatch, caplog):
    """The safety net for a chunk that cannot be found in the source.

    Triggering this needs a chunk containing characters absent from the source,
    which the real splitter never produces -- so the splitter's output is
    replaced wholesale. Without this test the fallback was pure theory.
    """
    chunker = Chunker(fake_embedder, ChunkerConfig(min_chunk_chars=1))

    monkeypatch.setattr(
        chunker._splitter,
        "create_documents",
        lambda texts: [Document(page_content="ZZZ NOT IN SOURCE", metadata={"start_index": 7})],
    )

    document = LoadedDocument(
        source_path="company/about.md",
        category="company",
        title="t",
        format="md",
        text="Alpha one. Beta two.",
        char_count=20,
    )

    chunks = chunker.chunk(document, Path("data"))

    assert "Could not locate chunk verbatim" in caplog.text
    # Ingestion continues rather than aborting, using the splitter's own
    # (untrustworthy) estimate so the run still produces output.
    assert chunks[0].metadata["start_char"] == 7


# --------------------------------------------------------------------------
# schemas.py — infer_category's missing-category guard (was line 142)
# --------------------------------------------------------------------------


def test_infer_category_rejects_a_file_with_no_category_folder(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    loose_file = data_root / "orphan.md"
    loose_file.write_text("no category folder above me", encoding="utf-8")

    with pytest.raises(ValueError, match="no category subfolder"):
        infer_category(loose_file, data_root)


def test_infer_category_error_names_the_offending_file(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    loose_file = data_root / "orphan.md"
    loose_file.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        infer_category(loose_file, data_root)

    # An error that does not say WHICH file is wrong forces a manual hunt
    # through 76 documents.
    assert "orphan.md" in str(exc_info.value)


# --------------------------------------------------------------------------
# enrichment.py — the encoding_name property (was line 57, uncovered)
# --------------------------------------------------------------------------


@pytest.mark.tiktoken
def test_counter_reports_which_encoding_it_used():
    counter = TiktokenCounter("cl100k_base")

    # Worth asserting because a saved dataset's token counts are only
    # interpretable if you know which tokenizer produced them.
    assert counter.encoding_name == "cl100k_base"
