"""Ingestion orchestration: discover -> load -> chunk -> enrich -> write to disk.

This module owns no domain logic of its own. Every real decision (how to read a
file, where to cut it, how to count its tokens) already lives in loader.py,
chunker.py and enrichment.py; what is added here is the part that turns those
pieces into something operable: an order to run them in, a policy for what
happens when one document fails, and a report describing what actually
happened rather than leaving it to be inferred from the filesystem.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import statistics
from pathlib import Path

from src.core.config import settings
from src.embedding.lock import verify_model
from src.ingestion.chunker import Chunker, ChunkerConfig, SentenceEmbedder, build_ollama_embedder
from src.ingestion.enrichment import (
    TokenCounter,
    build_tiktoken_counter,
    enrich_with_token_counts,
)
from src.ingestion.loader import DocumentLoadError, discover_documents, load_document
from src.ingestion.schemas import DocumentChunk

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("data")
DEFAULT_OUTPUT_DIR = Path("data/chunks")
REPORT_FILENAME = "report.json"


@dataclasses.dataclass(frozen=True, slots=True)
class TokenDistribution:
    """Spread of token counts, not just an average.

    An average alone hides the shape that actually matters for retrieval: a
    mean of 250 tokens is produced both by a healthy corpus and by one made of
    50-token fragments and 900-token walls of text, and only the second will
    embed badly. The quartiles make that difference visible.
    """

    minimum: int
    p25: int
    median: int
    p75: int
    maximum: int
    mean: float


@dataclasses.dataclass(frozen=True, slots=True)
class DocumentFailure:
    source_path: str
    error: str


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineReport:
    """What the run actually did, as data rather than as console scrollback."""

    documents_processed: int
    documents_failed: int
    chunks_written: int
    avg_chunk_chars: float
    token_distribution: TokenDistribution | None
    failures: list[DocumentFailure]


def output_filename(source_path: str) -> str:
    """Flatten 'company/about.md' into 'company__about.md.json'.

    Keeping the category in the name rather than recreating the directory tree
    means the output directory can be listed, sorted and diffed in one view,
    and a single `rm data/chunks/*.json` is enough to reset the whole dataset.
    """
    return f"{source_path.replace('/', '__')}.json"


def summarise(chunks: list[DocumentChunk], failures: list[DocumentFailure]) -> PipelineReport:
    """Build the report from the chunks that were actually produced."""
    # Counting distinct source paths rather than incrementing a loop variable
    # keeps this honest: a document that produced zero chunks is not counted as
    # processed, which is exactly the case worth noticing.
    documents = {chunk.source_path for chunk in chunks}

    if not chunks:
        return PipelineReport(
            documents_processed=len(documents),
            documents_failed=len(failures),
            chunks_written=0,
            avg_chunk_chars=0.0,
            token_distribution=None,
            failures=failures,
        )

    char_counts = [chunk.char_count for chunk in chunks]
    token_counts = sorted(chunk.approx_token_count for chunk in chunks)

    return PipelineReport(
        documents_processed=len(documents),
        documents_failed=len(failures),
        chunks_written=len(chunks),
        avg_chunk_chars=round(statistics.mean(char_counts), 1),
        token_distribution=_distribution(token_counts),
        failures=failures,
    )


def _distribution(sorted_counts: list[int]) -> TokenDistribution:
    # statistics.quantiles needs at least two data points, and a corpus small
    # enough to produce one chunk is exactly when someone is debugging and can
    # least afford a crash in the reporting code.
    if len(sorted_counts) < 2:
        only = sorted_counts[0]
        return TokenDistribution(only, only, only, only, only, float(only))

    # method="inclusive" because this describes the chunks that actually exist,
    # not an estimate of some larger population they were sampled from. The
    # default ("exclusive") extrapolates beyond the observed range on small
    # inputs: for two chunks of 6 and 10 tokens it reports p25=5 and p75=11,
    # both impossible values for a dataset whose min is 6 and max is 10.
    p25, median, p75 = statistics.quantiles(sorted_counts, n=4, method="inclusive")
    return TokenDistribution(
        minimum=sorted_counts[0],
        p25=int(p25),
        median=int(median),
        p75=int(p75),
        maximum=sorted_counts[-1],
        mean=round(statistics.mean(sorted_counts), 1),
    )


def write_chunks(chunks: list[DocumentChunk], destination: Path) -> None:
    """Serialise one document's chunks to a single JSON file."""
    payload = [dataclasses.asdict(chunk) for chunk in chunks]
    # indent=2 because these files are read by humans far more often than by
    # machines during development; the size cost is irrelevant at this scale.
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_chunks(chunks_dir: Path = DEFAULT_OUTPUT_DIR) -> list[DocumentChunk]:
    """Load every chunk written by `write_chunks`, in a stable order.

    Lets embedding run as its own stage from the files on disk, so changing the
    embedding step never forces a re-chunk (which re-embeds every sentence).
    """
    chunks: list[DocumentChunk] = []
    # Excluding the report by name rather than globbing "*.md.json": sources can
    # also be .txt or .pdf, and a narrower glob would drop their chunks silently.
    for path in sorted(p for p in chunks_dir.glob("*.json") if p.name != REPORT_FILENAME):
        chunks.extend(DocumentChunk(**record) for record in json.loads(path.read_text("utf-8")))
    return chunks


def run_pipeline(
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    embedder: SentenceEmbedder,
    counter: TokenCounter,
    config: ChunkerConfig | None = None,
) -> PipelineReport:
    """Run the full ingestion pass and return a report of what happened.

    Every document is re-chunked on every run, overwriting whatever was there
    before. The alternative — skipping documents whose output already exists —
    is fast but silently wrong the moment chunking parameters change: the
    skipped files keep boundaries produced by the OLD configuration, leaving a
    dataset that mixes two chunking strategies with no way to tell which chunk
    came from which.

    A document that fails is recorded and skipped rather than aborting the run,
    so a single unreadable file cannot discard the embedding work already done
    for everything before it. The cost of that choice is real and worth stating:
    the resulting dataset LOOKS complete, so `documents_failed` in the report is
    the only thing standing between a partial corpus and silent recall loss.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    chunker = Chunker(embedder, config)
    all_chunks: list[DocumentChunk] = []
    failures: list[DocumentFailure] = []

    for source_path in discover_documents(data_root):
        try:
            document = load_document(source_path, data_root)
            chunks = chunker.chunk(document, data_root)
            # Enrichment returns NEW chunks, so the result must be reassigned;
            # discarding it would leave the char/4 estimates in place and make
            # the token distribution below a report about the wrong numbers.
            chunks = enrich_with_token_counts(chunks, counter)

            write_chunks(chunks, output_dir / output_filename(document.source_path))
            all_chunks.extend(chunks)

        except (DocumentLoadError, OSError, ValueError) as exc:
            # Catching by type rather than bare Exception so a genuine bug in
            # our own code still surfaces as a crash instead of being filed
            # away as "one document failed".
            logger.warning("Skipping %s: %s", source_path, exc)
            failures.append(DocumentFailure(source_path=str(source_path), error=str(exc)))

    report = summarise(all_chunks, failures)
    (output_dir / REPORT_FILENAME).write_text(
        json.dumps(dataclasses.asdict(report), indent=2), encoding="utf-8"
    )
    return report


def format_report(report: PipelineReport) -> str:
    """Render the report for a human reading a terminal."""
    lines = [
        "Ingestion complete",
        f"  documents processed : {report.documents_processed}",
        f"  documents failed    : {report.documents_failed}",
        f"  chunks written      : {report.chunks_written}",
        f"  avg chunk chars     : {report.avg_chunk_chars}",
    ]

    if report.token_distribution is not None:
        d = report.token_distribution
        lines.append(
            f"  tokens  min={d.minimum} p25={d.p25} median={d.median} "
            f"p75={d.p75} max={d.maximum} mean={d.mean}"
        )

    # Failures are listed individually rather than only counted, because a
    # number alone gives the reader nothing to act on.
    for failure in report.failures:
        lines.append(f"  FAILED {failure.source_path}: {failure.error}")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point: python -m src.ingestion.pipeline"""
    parser = argparse.ArgumentParser(description="Chunk and enrich the document corpus.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--embed-model", default=settings.embed_model)
    parser.add_argument(
        "--threshold",
        type=float,
        default=ChunkerConfig().breakpoint_threshold_amount,
        help="Lower cuts more aggressively; see ChunkerConfig.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Checked before any chunking: the chunker embeds sentences with this model,
    # so a silently swapped model would change chunk boundaries for the whole run.
    verify_model(args.ollama_url, model=args.embed_model)

    # Both dependencies are constructed here, at the outermost layer, and passed
    # down — so run_pipeline itself never reaches out to a network or a model
    # server, and a test can drive the entire pipeline with fakes.
    report = run_pipeline(
        data_root=args.data_root,
        output_dir=args.output_dir,
        embedder=build_ollama_embedder(args.ollama_url, args.embed_model),
        counter=build_tiktoken_counter(),
        config=ChunkerConfig(
            breakpoint_threshold_amount=args.threshold,
            max_chunk_tokens=settings.embed_max_chunk_tokens,
        ),
    )

    print(format_report(report))


if __name__ == "__main__":
    main()
