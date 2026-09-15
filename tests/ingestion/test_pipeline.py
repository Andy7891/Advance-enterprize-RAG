import json
from pathlib import Path

import pytest

from src.ingestion.chunker import ChunkerConfig
from src.ingestion.pipeline import (
    REPORT_FILENAME,
    DocumentFailure,
    format_report,
    output_filename,
    run_pipeline,
    summarise,
)
from src.ingestion.schemas import make_chunk
from tests.ingestion.test_chunker import FakeEmbedder
from tests.ingestion.test_enrichment import FakeCounter

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A tiny real-on-disk corpus, so the pipeline walks actual files."""
    root = tmp_path / "data"
    (root / "company").mkdir(parents=True)
    (root / "products").mkdir(parents=True)
    (root / "company" / "about.md").write_text(
        "# About\n\nAlpha one. Alpha two. Beta one. Beta two.", encoding="utf-8"
    )
    (root / "products" / "widget.md").write_text(
        "# Widget\n\nGamma one. Gamma two.", encoding="utf-8"
    )
    return root


def _run(corpus: Path, output: Path, **kwargs):
    return run_pipeline(
        data_root=corpus,
        output_dir=output,
        embedder=FakeEmbedder(),
        counter=FakeCounter(),
        config=ChunkerConfig(min_chunk_chars=1),
        **kwargs,
    )


def test_output_filename_flattens_category_into_the_name():
    assert output_filename("company/about.md") == "company__about.md.json"


def test_writes_one_json_file_per_document(corpus, tmp_path):
    out = tmp_path / "chunks"
    _run(corpus, out)
    written = {p.name for p in out.glob("*.json")} - {REPORT_FILENAME}
    assert written == {"company__about.md.json", "products__widget.md.json"}


def test_written_json_round_trips_to_real_chunk_fields(corpus, tmp_path):
    out = tmp_path / "chunks"
    _run(corpus, out)
    payload = json.loads((out / "company__about.md.json").read_text(encoding="utf-8"))
    assert payload
    first = payload[0]
    assert first["source_path"] == "company/about.md"
    assert first["category"] == "company"
    assert "tokens_approx" in first["metadata"]


def test_enrichment_actually_ran(corpus, tmp_path):
    # The whole point of chaining enrichment in: saved chunks must carry the
    # counter's number, not the char/4 estimate make_chunk starts with.
    out = tmp_path / "chunks"
    _run(corpus, out)
    payload = json.loads((out / "products__widget.md.json").read_text(encoding="utf-8"))
    for chunk in payload:
        assert chunk["approx_token_count"] == chunk["metadata"]["tokens_approx"]
        assert chunk["approx_token_count"] == len(chunk["text"].split())


def test_report_counts_documents_and_chunks(corpus, tmp_path):
    report = _run(corpus, tmp_path / "chunks")
    assert report.documents_processed == 2
    assert report.documents_failed == 0
    assert report.chunks_written > 0


def test_report_file_is_written_and_parseable(corpus, tmp_path):
    out = tmp_path / "chunks"
    report = _run(corpus, out)
    on_disk = json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["chunks_written"] == report.chunks_written
    assert on_disk["token_distribution"]["median"] >= 0


def test_token_distribution_is_ordered(corpus, tmp_path):
    report = _run(corpus, tmp_path / "chunks")
    d = report.token_distribution
    assert d is not None
    assert d.minimum <= d.p25 <= d.median <= d.p75 <= d.maximum


def test_rerun_overwrites_rather_than_appending(corpus, tmp_path):
    out = tmp_path / "chunks"
    first = _run(corpus, out)
    second = _run(corpus, out)
    # Same inputs and same config must produce the same dataset, not a growing
    # one -- the property that makes the overwrite policy safe to re-run.
    assert first.chunks_written == second.chunks_written
    assert len(list(out.glob("*.json"))) == 3  # 2 documents + report


def test_a_failing_document_is_skipped_and_recorded(corpus, tmp_path):
    (corpus / "company" / "broken.pdf").write_bytes(b"not a real pdf")
    report = _run(corpus, tmp_path / "chunks")

    assert report.documents_failed == 1
    assert report.failures[0].source_path.endswith("broken.pdf")
    # The healthy documents must still have been processed.
    assert report.documents_processed == 2


def test_empty_corpus_produces_an_empty_report(tmp_path):
    empty = tmp_path / "data"
    (empty / "company").mkdir(parents=True)
    report = _run(empty, tmp_path / "chunks")
    assert report.chunks_written == 0
    assert report.token_distribution is None


def test_summarise_handles_a_single_chunk():
    chunk = make_chunk(
        source_path=DATA_ROOT / "company" / "about.md",
        data_root=DATA_ROOT,
        title="t",
        chunk_index=0,
        text="hello world",
    )
    report = summarise([chunk], [])
    d = report.token_distribution
    assert d is not None
    assert d.minimum == d.maximum == d.median


def test_format_report_lists_failures_individually():
    report = summarise([], [DocumentFailure(source_path="a/b.pdf", error="boom")])
    rendered = format_report(report)
    assert "a/b.pdf" in rendered
    assert "boom" in rendered
