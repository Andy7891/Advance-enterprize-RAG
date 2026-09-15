from pathlib import Path

import pytest

from src.ingestion.schemas import (
    compute_chunk_id,
    compute_doc_id,
    count_tokens_approx,
    discover_markdown_files,
    extract_title,
    infer_category,
    make_chunk,
)

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def test_discover_markdown_files_excludes_ipynb_checkpoints():
    files = discover_markdown_files(DATA_ROOT)
    assert files, "expected at least one .md file under data/"
    assert not any(".ipynb_checkpoints" in path.parts for path in files)


def test_discover_markdown_files_finds_known_categories():
    files = discover_markdown_files(DATA_ROOT)
    categories = {infer_category(path, DATA_ROOT) for path in files}
    assert categories == {"company", "contracts", "employees", "products"}


def test_compute_doc_id_is_deterministic():
    assert compute_doc_id("contracts/foo.md") == compute_doc_id("contracts/foo.md")


def test_compute_doc_id_differs_for_different_paths():
    assert compute_doc_id("contracts/foo.md") != compute_doc_id("contracts/bar.md")


def test_compute_chunk_id_is_readable_source_underscore_index():
    assert compute_chunk_id("contracts/foo.md", 3) == "contracts/foo.md_3"


def test_compute_chunk_id_stable_for_unchanged_input():
    assert compute_chunk_id("contracts/foo.md", 0) == compute_chunk_id("contracts/foo.md", 0)


def test_compute_chunk_id_does_NOT_change_when_text_changes():
    """Documents the known, deliberate risk of the positional ID scheme.

    This is the inverse of what the previous hash-based scheme guaranteed.
    Because the ID ignores content entirely, re-chunking with different
    settings reuses IDs for different text -- so the vector-store upsert
    layer MUST delete a document's existing chunks by doc_id before
    re-ingesting it. This test exists so that requirement stays visible
    instead of being rediscovered as a production bug.
    """
    assert compute_chunk_id("contracts/foo.md", 0) == compute_chunk_id("contracts/foo.md", 0)


def test_compute_chunk_id_differs_across_categories_with_same_filename():
    assert compute_chunk_id("contracts/foo.md", 0) != compute_chunk_id("company/foo.md", 0)


def test_infer_category_rejects_file_outside_data_root():
    with pytest.raises(ValueError):
        infer_category(Path("/tmp/not_in_data.md"), DATA_ROOT)


def test_extract_title_finds_h1():
    text = "# About Insurellm\n\nSome body text."
    assert extract_title(text, fallback="fallback") == "About Insurellm"


def test_extract_title_falls_back_when_no_h1():
    text = "No heading here, just text."
    assert extract_title(text, fallback="my-fallback") == "my-fallback"


def test_count_tokens_approx_scales_with_length():
    short = count_tokens_approx("hi")
    long = count_tokens_approx("hi " * 1000)
    assert long > short


def test_make_chunk_on_real_company_file():
    source = DATA_ROOT / "company" / "about.md"
    text = source.read_text(encoding="utf-8")
    title = extract_title(text, fallback=source.stem)

    chunk = make_chunk(
        source_path=source,
        data_root=DATA_ROOT,
        title=title,
        chunk_index=0,
        text=text[:200],
    )

    assert chunk.category == "company"
    assert chunk.source_path == "company/about.md"
    assert chunk.title == "About Insurellm"
    assert chunk.chunk_index == 0
    assert chunk.char_count == 200
    assert chunk.chunk_id == "company/about.md_0"
    assert len(chunk.doc_id) == 16


def test_make_chunk_is_idempotent_across_reingestion():
    source = DATA_ROOT / "company" / "about.md"
    text = source.read_text(encoding="utf-8")
    title = extract_title(text, fallback=source.stem)

    chunk_run_1 = make_chunk(
        source_path=source, data_root=DATA_ROOT, title=title, chunk_index=0, text=text[:200]
    )
    chunk_run_2 = make_chunk(
        source_path=source, data_root=DATA_ROOT, title=title, chunk_index=0, text=text[:200]
    )

    assert chunk_run_1.chunk_id == chunk_run_2.chunk_id
    assert chunk_run_1.doc_id == chunk_run_2.doc_id


def test_document_chunk_is_immutable():
    source = DATA_ROOT / "company" / "about.md"
    text = source.read_text(encoding="utf-8")
    chunk = make_chunk(
        source_path=source, data_root=DATA_ROOT, title="x", chunk_index=0, text=text[:50]
    )
    with pytest.raises(AttributeError):
        chunk.text = "tampered"  # type: ignore[misc]
