"""The chunk data contract: what every chunk in data/chunks/ must satisfy.

Two layers are checked here, and the distinction matters:

- STRUCTURAL — are the right keys present, with the right types? This is what
  a JSON Schema would check, and it is the easy half.
- SEMANTIC — do the values agree with each other? A chunk can be perfectly
  well-formed and still claim char_count=500 for a 12-character string. No
  schema language catches that; only an invariant does.

These run against real pipeline output rather than fixtures on purpose: the
point is to verify what was actually produced, not what a fixture was written
to contain.
"""

import dataclasses
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from src.ingestion.loader import load_document
from src.ingestion.schemas import DocumentChunk

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"
DATA_ROOT = PROJECT_ROOT / "data"
README = PROJECT_ROOT / "README.md"

SCHEMA_FIELDS = {field.name for field in dataclasses.fields(DocumentChunk)}
EXPECTED_METADATA_KEYS = {"start_char", "end_char", "tokens_approx"}


@pytest.fixture(scope="module")
def chunks() -> list[dict]:
    """Every chunk from every file in data/chunks/.

    Skips rather than fails when the directory is empty, because data/chunks/
    is gitignored generated output — a fresh clone and every CI run legitimately
    has none. The skip reason is explicit so a green run that skipped is
    distinguishable from a green run that actually verified something.
    """
    files = sorted(path for path in CHUNKS_DIR.glob("*.json") if path.name != "report.json")
    if not files:
        pytest.skip(
            "no chunk output in data/chunks/ — run `python -m src.ingestion.pipeline` "
            "first; this contract is only enforceable against real output"
        )

    loaded: list[dict] = []
    for path in files:
        loaded.extend(json.loads(path.read_text(encoding="utf-8")))
    return loaded


# --------------------------------------------------------------------------
# STRUCTURAL — the layer a JSON Schema would cover
# --------------------------------------------------------------------------


def test_every_chunk_has_exactly_the_schema_fields(chunks):
    # Comparing sets in both directions catches a MISSING field and an EXTRA
    # one; asserting only `issubset` would let unexpected keys through.
    for chunk in chunks:
        assert set(chunk) == SCHEMA_FIELDS, f"{chunk.get('chunk_id')} has {set(chunk)}"


def test_field_types_match_the_dataclass(chunks):
    expected = {
        "chunk_id": str,
        "doc_id": str,
        "source_path": str,
        "category": str,
        "title": str,
        "chunk_index": int,
        "text": str,
        "char_count": int,
        "approx_token_count": int,
        "metadata": dict,
    }
    for chunk in chunks:
        for name, kind in expected.items():
            assert isinstance(chunk[name], kind), f"{chunk['chunk_id']}.{name}"


def test_metadata_keys_are_uniform_across_every_chunk(chunks):
    # metadata is a free-form dict, so nothing in the type system stops one
    # chunk from carrying different keys than another. Uniformity is a
    # property of the PIPELINE, and only an assertion like this defends it.
    for chunk in chunks:
        assert set(chunk["metadata"]) == EXPECTED_METADATA_KEYS


# --------------------------------------------------------------------------
# SEMANTIC — values agreeing with each other, which no schema language checks
# --------------------------------------------------------------------------


def test_char_count_matches_the_actual_text_length(chunks):
    for chunk in chunks:
        assert chunk["char_count"] == len(chunk["text"])


def test_both_token_counts_agree(chunks):
    # Two places hold the same number by design (decision D-2026-09-15-18);
    # a disagreement means enrichment's return value was dropped somewhere.
    for chunk in chunks:
        assert chunk["approx_token_count"] == chunk["metadata"]["tokens_approx"]


def test_chunk_id_follows_the_documented_scheme(chunks):
    for chunk in chunks:
        assert chunk["chunk_id"] == f"{chunk['source_path']}_{chunk['chunk_index']}"


def test_no_chunk_is_empty_or_whitespace_only(chunks):
    for chunk in chunks:
        assert chunk["text"].strip()


def test_offsets_are_ordered_and_non_negative(chunks):
    for chunk in chunks:
        start, end = chunk["metadata"]["start_char"], chunk["metadata"]["end_char"]
        assert 0 <= start < end


def test_chunk_indices_are_contiguous_within_each_document(chunks):
    by_document = defaultdict(list)
    for chunk in chunks:
        by_document[chunk["source_path"]].append(chunk["chunk_index"])

    # A gap would mean a chunk was lost between chunking and writing; a
    # duplicate would mean two chunks share an id and one will overwrite the
    # other on upsert.
    for source_path, indices in by_document.items():
        assert sorted(indices) == list(range(len(indices))), source_path


def test_offsets_point_at_the_real_source_text(chunks):
    """The strongest check: offsets must resolve against the actual document."""
    by_document = defaultdict(list)
    for chunk in chunks:
        by_document[chunk["source_path"]].append(chunk)

    for source_path, doc_chunks in by_document.items():
        document = load_document(DATA_ROOT / source_path, DATA_ROOT)
        for chunk in doc_chunks:
            start, end = chunk["metadata"]["start_char"], chunk["metadata"]["end_char"]
            assert end <= len(document.text), chunk["chunk_id"]
            # Word-level rather than byte-level equality, because the splitter
            # rebuilds text with single spaces while the source may contain
            # blank lines (decision D-2026-09-15-15).
            sliced = document.text[start:end]
            assert sliced.split() == chunk["text"].split(), chunk["chunk_id"]


# --------------------------------------------------------------------------
# DRIFT GUARD — stops the README's prose table from quietly becoming a lie
# --------------------------------------------------------------------------


def test_readme_schema_table_lists_exactly_the_dataclass_fields():
    """The README documents the schema for humans; the dataclass defines it for
    code. Two descriptions of one thing drift unless something compares them,
    and a wrong schema table is worse than none — a reader trusts it.
    """
    readme = README.read_text(encoding="utf-8")

    section = re.search(
        r"<!-- chunk-schema:start -->(.*?)<!-- chunk-schema:end -->", readme, re.DOTALL
    )
    assert section, "README is missing the chunk-schema markers"

    # Field names are the first cell of each table row, wrapped in backticks.
    documented = set(re.findall(r"^\|\s*`(\w+)`\s*\|", section.group(1), re.MULTILINE))

    assert documented == SCHEMA_FIELDS, (
        f"README table and DocumentChunk disagree — "
        f"only in README: {documented - SCHEMA_FIELDS}, "
        f"only in code: {SCHEMA_FIELDS - documented}"
    )
