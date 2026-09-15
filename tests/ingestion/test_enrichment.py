from pathlib import Path

import pytest

from src.ingestion.enrichment import (
    TiktokenCounter,
    build_tiktoken_counter,
    enrich_with_token_counts,
)
from src.ingestion.schemas import make_chunk

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


class FakeCounter:
    """Deterministic stand-in so the suite never loads tiktoken's encoding,
    which would make CI depend on a network fetch on a fresh runner.
    """

    def count(self, text: str) -> int:
        return len(text.split())


def _chunk(text: str = "Alpha one. Alpha two.", index: int = 0):
    return make_chunk(
        source_path=DATA_ROOT / "company" / "about.md",
        data_root=DATA_ROOT,
        title="About Insurellm",
        chunk_index=index,
        text=text,
        start_char=0,
        end_char=len(text),
    )


def test_populates_metadata_tokens_approx():
    enriched = enrich_with_token_counts([_chunk()], FakeCounter())
    assert enriched[0].metadata["tokens_approx"] == 4


def test_field_and_metadata_hold_the_same_number():
    enriched = enrich_with_token_counts([_chunk()], FakeCounter())
    chunk = enriched[0]
    assert chunk.approx_token_count == chunk.metadata["tokens_approx"]


def test_overwrites_the_char_based_estimate():
    original = _chunk()
    heuristic = original.approx_token_count
    enriched = enrich_with_token_counts([original], FakeCounter())
    # The whole point of this pass: the creation-time estimate is replaced.
    assert enriched[0].approx_token_count != heuristic
    assert enriched[0].approx_token_count == 4


def test_tokens_approx_is_an_integer():
    enriched = enrich_with_token_counts([_chunk()], FakeCounter())
    assert isinstance(enriched[0].metadata["tokens_approx"], int)


def test_original_chunks_are_not_modified():
    original = _chunk()
    before_field = original.approx_token_count
    before_metadata = dict(original.metadata)

    enrich_with_token_counts([original], FakeCounter())

    assert original.approx_token_count == before_field
    assert original.metadata == before_metadata
    assert "tokens_approx" not in original.metadata


def test_existing_metadata_is_preserved():
    enriched = enrich_with_token_counts([_chunk()], FakeCounter())
    chunk = enriched[0]
    assert chunk.metadata["start_char"] == 0
    assert "end_char" in chunk.metadata


def test_identity_fields_are_untouched():
    original = _chunk()
    enriched = enrich_with_token_counts([original], FakeCounter())[0]
    assert enriched.chunk_id == original.chunk_id
    assert enriched.doc_id == original.doc_id
    assert enriched.chunk_index == original.chunk_index
    assert enriched.text == original.text


def test_preserves_order_and_count():
    chunks = [_chunk("Alpha one.", 0), _chunk("Beta two three.", 1)]
    enriched = enrich_with_token_counts(chunks, FakeCounter())
    assert [c.chunk_id for c in enriched] == [c.chunk_id for c in chunks]
    assert [c.metadata["tokens_approx"] for c in enriched] == [2, 3]


def test_empty_input_returns_empty_list():
    assert enrich_with_token_counts([], FakeCounter()) == []


def test_enrichment_is_idempotent():
    once = enrich_with_token_counts([_chunk()], FakeCounter())
    twice = enrich_with_token_counts(once, FakeCounter())
    assert once[0].approx_token_count == twice[0].approx_token_count
    assert once[0].metadata == twice[0].metadata


def test_bad_encoding_name_fails_at_construction_not_mid_run():
    # Failing loudly at wiring time is the deliberate choice (decision.md
    # D-2026-09-15-17) -- this asserts we did not add a silent fallback.
    with pytest.raises(Exception):
        TiktokenCounter("not_a_real_encoding")


@pytest.mark.tiktoken
def test_real_tiktoken_counts_differ_from_the_heuristic():
    # Marked separately: this one genuinely loads the encoding, so it needs a
    # warm cache or network. Deselect with: pytest -m "not tiktoken"
    counter = build_tiktoken_counter()
    original = _chunk("Alpha one. Alpha two.")
    enriched = enrich_with_token_counts([original], counter)[0]
    assert enriched.metadata["tokens_approx"] > 0
    assert enriched.approx_token_count == enriched.metadata["tokens_approx"]
