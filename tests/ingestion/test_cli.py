"""Tests for pipeline.main() — the CLI entry point, previously uncovered.

main() is the one function that constructs real dependencies (Ollama, tiktoken),
so testing it means intercepting those constructors. What is actually being
verified here is the WIRING: that command-line flags reach run_pipeline as the
right arguments. Whether Ollama itself works is not this test's business.
"""

import sys
from pathlib import Path

import pytest

from src.ingestion import pipeline
from tests.conftest import FakeCounter, FakeEmbedder


@pytest.fixture
def cli_run(monkeypatch, fixture_corpus, tmp_path):
    """Run main() with fakes swapped in, capturing what run_pipeline received."""
    captured: dict = {}
    real_run_pipeline = pipeline.run_pipeline

    def spy(**kwargs):
        captured.update(kwargs)
        return real_run_pipeline(**kwargs)

    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    # Both builders are replaced so no network or encoding file is touched;
    # the lambdas accept and ignore whatever arguments main() passes them.
    monkeypatch.setattr(pipeline, "build_ollama_embedder", lambda *a, **k: FakeEmbedder())
    monkeypatch.setattr(pipeline, "build_tiktoken_counter", lambda *a, **k: FakeCounter())
    # main() verifies the pinned model against a live Ollama before chunking;
    # that check has its own tests in tests/embedding/.
    monkeypatch.setattr(pipeline, "verify_model", lambda *a, **k: "fake-digest")

    def run(*extra_args: str) -> dict:
        argv = [
            "pipeline",
            "--data-root",
            str(fixture_corpus),
            "--output-dir",
            str(tmp_path / "out"),
            *extra_args,
        ]
        monkeypatch.setattr(sys, "argv", argv)
        pipeline.main()
        return captured

    return run


def test_cli_runs_end_to_end_and_writes_output(cli_run, tmp_path, capsys):
    cli_run()

    written = list((tmp_path / "out").glob("*.json"))
    assert written
    # main() prints the human summary; a silent successful run would leave an
    # operator with no idea whether anything happened.
    assert "Ingestion complete" in capsys.readouterr().out


def test_cli_passes_paths_through_as_path_objects(cli_run, fixture_corpus):
    captured = cli_run()

    # argparse's type=Path conversion is what makes this true; without it these
    # would arrive as plain strings and fail deeper inside the pipeline.
    assert captured["data_root"] == fixture_corpus
    assert isinstance(captured["output_dir"], Path)


def test_cli_threshold_flag_reaches_the_chunker_config(cli_run):
    captured = cli_run("--threshold", "0.25")

    assert captured["config"].breakpoint_threshold_amount == 0.25


def test_cli_uses_the_default_threshold_when_flag_is_absent(cli_run):
    from src.ingestion.chunker import ChunkerConfig

    captured = cli_run()

    assert captured["config"].breakpoint_threshold_amount == (
        ChunkerConfig().breakpoint_threshold_amount
    )


def test_cli_rejects_a_non_numeric_threshold(cli_run):
    # argparse converts and validates before any of our code runs, so a bad
    # value can never reach the chunker.
    with pytest.raises(SystemExit):
        cli_run("--threshold", "not-a-number")
