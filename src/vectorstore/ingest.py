"""Ingestion job: make the Qdrant collection match the chunks on disk, and record the run.

A run is a fixed sequence of stages. Each stage is timed, and whatever happens —
success or failure at any stage — the report is written to the log, because an
index nobody can inspect is an index nobody can trust.

    preflight -> load -> embed -> upsert -> orphans -> verify
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from src.core.config import settings
from src.embedding.embedder import Embedder
from src.ingestion.pipeline import DEFAULT_OUTPUT_DIR, REPORT_FILENAME, read_chunks
from src.vectorstore.setup import COLLECTION_NAME, build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("data/ingestion_log.json")


class IngestionAborted(RuntimeError):
    """A precondition failed, so the run stopped before it could do damage."""


class VerificationFailed(RuntimeError):
    """The collection does not match the chunks on disk after writing."""


@dataclasses.dataclass
class IngestionReport:
    """Everything known about one run.

    Mutable and filled in stage by stage on purpose: when a stage fails, the
    fields gathered before it are exactly what is needed to diagnose it.
    """

    started_at: str
    collection: str
    model: str
    model_digest: str
    document_prefix: str
    batch_size: int
    rebuild: bool
    status: str = "running"
    failed_stage: str | None = None
    error: str | None = None
    finished_at: str | None = None
    stage_seconds: dict[str, float] = dataclasses.field(default_factory=dict)
    documents_on_disk: int = 0
    chunks_on_disk: int = 0
    points_before: int | None = None
    incompatible_points_before: int | None = None
    points_after: int | None = None
    orphan_documents_removed: int = 0
    orphan_documents_kept: int = 0
    verified: bool = False
    warnings: list[str] = dataclasses.field(default_factory=list)


def run_ingestion(
    client: QdrantClient,
    make_embedder: Callable[[], Embedder],
    *,
    collection: str = COLLECTION_NAME,
    chunks_dir: Path = DEFAULT_OUTPUT_DIR,
    log_path: Path = DEFAULT_LOG_PATH,
    rebuild: bool = False,
) -> IngestionReport:
    """Embed every chunk in `chunks_dir` into `collection`, then write the run log.

    Raises whatever stopped the run, after the log has been written.
    """
    report = IngestionReport(
        started_at=_now(),
        collection=collection,
        model=settings.embed_model,
        model_digest=settings.embed_model_digest,
        document_prefix=settings.embed_document_prefix,
        batch_size=settings.embed_batch_size,
        rebuild=rebuild,
    )
    stage = _StageTimer(report)

    try:
        # INTERVIEW: every check that can fail in seconds runs before the embedding
        # stage, which takes minutes; discovering a stopped Qdrant or a wrong
        # model after that work is the expensive way to find the same problem.
        with stage("preflight"):
            embedder = make_embedder()
            store = QdrantVectorStore(client, embedder, collection=collection)
            # Creates the collection on a fresh server, and raises on an unreachable
            # server or a wrongly configured collection, so no separate readiness
            # check is needed here (mutation testing showed one could never fail).
            store.ensure_collection()
            report.points_before = store.count()
            report.incompatible_points_before = store.count_incompatible()
            if report.incompatible_points_before and not rebuild:
                # INTERVIEW: closes the gap where a changed setting makes new
                # embeddings and the upsert check agree with each other while the
                # stored vectors live in a different space. Rebuilding is allowed,
                # but only as an explicit decision.
                raise IngestionAborted(
                    f"{report.incompatible_points_before} stored points were made with a "
                    f"different model digest or document prefix than the current settings "
                    f"({settings.embed_model_digest[:12]}, "
                    f"{settings.embed_document_prefix!r}); rerun with --rebuild to replace them"
                )

        with stage("load"):
            chunks = read_chunks(chunks_dir)
            if not chunks:
                raise IngestionAborted(
                    f"no chunks in {chunks_dir}; run the ingestion pipeline first"
                )
            disk_ids_by_document: dict[str, set[str]] = {}
            for chunk in chunks:
                disk_ids_by_document.setdefault(chunk.doc_id, set()).add(chunk.chunk_id)
            report.chunks_on_disk = len(chunks)
            report.documents_on_disk = len(disk_ids_by_document)

        # Embedding everything before writing anything means an embedding failure
        # leaves the collection exactly as it was, not half re-indexed.
        with stage("embed"):
            embeddings = embedder.embed_batch(chunks)

        with stage("upsert"):
            store.upsert(embeddings)

        with stage("orphans"):
            kept = _remove_orphans(store, disk_ids_by_document, chunks_dir, report)

        with stage("verify"):
            _verify(store, disk_ids_by_document, kept, report)

        report.status = "succeeded"
        return report
    except Exception as exc:
        # Recorded for the log, then re-raised unchanged: the log explains a
        # failure, it must never swallow one.
        report.status = "failed"
        report.failed_stage = stage.current
        report.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report.finished_at = _now()
        write_log(report, log_path)


def write_log(report: IngestionReport, log_path: Path = DEFAULT_LOG_PATH) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(dataclasses.asdict(report), indent=2), encoding="utf-8")


def _remove_orphans(
    store: QdrantVectorStore,
    disk_ids_by_document: dict[str, set[str]],
    chunks_dir: Path,
    report: IngestionReport,
) -> set[str]:
    """Delete documents that are stored but no longer on disk. Returns doc_ids kept."""
    orphans = sorted(set(store.stored_chunk_ids_by_document()) - set(disk_ids_by_document))
    if not orphans:
        return set()

    failed_documents = _pipeline_failures(chunks_dir)
    if failed_documents:
        # INTERVIEW: a document missing from disk usually means it was deleted,
        # but after a partly failed chunking run it can simply mean it failed to
        # chunk. Deleting it then would remove a valid document from the index
        # because of an unrelated error, so orphans are kept until a clean run.
        report.orphan_documents_kept = len(orphans)
        report.warnings.append(
            f"kept {len(orphans)} orphan document(s): the chunking report records "
            f"{failed_documents} failed document(s), so their absence is not proof of deletion"
        )
        return set(orphans)

    store.delete_documents(orphans)
    report.orphan_documents_removed = len(orphans)
    return set()


def _pipeline_failures(chunks_dir: Path) -> int:
    report_path = chunks_dir / REPORT_FILENAME
    if not report_path.exists():
        return 0
    return int(json.loads(report_path.read_text(encoding="utf-8")).get("documents_failed", 0))


def _verify(
    store: QdrantVectorStore,
    disk_ids_by_document: dict[str, set[str]],
    kept_orphans: set[str],
    report: IngestionReport,
) -> None:
    # INTERVIEW: "no exception was raised" is not evidence the index is right.
    # Comparing the exact chunk_id sets catches a missing chunk and a stale one
    # even when they cancel out in a point count.
    stored = store.stored_chunk_ids_by_document()
    report.points_after = store.count()

    expected = set().union(*disk_ids_by_document.values())
    actual = set().union(*(ids for doc_id, ids in stored.items() if doc_id not in kept_orphans))
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    incompatible = store.count_incompatible()

    if missing or unexpected or incompatible:
        raise VerificationFailed(
            f"collection does not match disk: {len(missing)} missing "
            f"(e.g. {missing[:3]}), {len(unexpected)} unexpected (e.g. {unexpected[:3]}), "
            f"{incompatible} incompatible points"
        )
    report.verified = True


class _StageTimer:
    """Times named stages into the report and remembers which one is running."""

    def __init__(self, report: IngestionReport) -> None:
        self._report = report
        self.current: str | None = None

    @contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        self.current = name
        started = time.perf_counter()
        try:
            yield
        finally:
            # Recorded even when the stage fails, so the log shows how far a
            # failed stage got before it stopped.
            self._report.stage_seconds[name] = round(time.perf_counter() - started, 3)
        logger.info("stage %s finished in %.1fs", name, self._report.stage_seconds[name])


def _now() -> str:
    # UTC so logs from different machines and seasons compare without conversion.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarise(report: IngestionReport) -> str:
    stages = ", ".join(f"{name} {seconds:.1f}s" for name, seconds in report.stage_seconds.items())
    # A run that stopped before loading has no chunk counts, and "0 chunks" would
    # read as an empty chunks directory rather than as "never got that far".
    loaded = (
        f"{report.chunks_on_disk} chunks from {report.documents_on_disk} documents"
        if "load" in report.stage_seconds
        else "chunks not loaded"
    )
    lines = [
        f"{report.status.upper()}: {loaded} -> collection {report.collection!r}",
        f"  points {report.points_before} -> {report.points_after}; orphans removed "
        f"{report.orphan_documents_removed}, kept {report.orphan_documents_kept}; "
        f"verified={report.verified}",
        f"  stages: {stages}",
    ]
    lines += [f"  warning: {warning}" for warning in report.warnings]
    if report.error:
        lines.append(f"  failed in {report.failed_stage}: {report.error}")
    return "\n".join(lines)


def cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed all chunks into Qdrant and log the run.")
    add_ingestion_arguments(parser)
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    return parser.parse_args(argv)


def add_ingestion_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--collection", default=COLLECTION_NAME)
    parser.add_argument("--chunks-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="replace stored points made with a different model digest or prefix",
    )


def run_from_args(args: argparse.Namespace) -> int:
    """Shared by this module's CLI and `store index`; returns a process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from src.embedding.embedder import build_ollama_client

    def make_embedder() -> Embedder:
        return Embedder(build_ollama_client(args.ollama_url), ollama_url=args.ollama_url)

    try:
        report = run_ingestion(
            build_qdrant_client(args.url),
            make_embedder,
            collection=args.collection,
            chunks_dir=args.chunks_dir,
            log_path=args.log,
            rebuild=args.rebuild,
        )
    except Exception:
        # The traceback goes to stderr for debugging; the summary comes from the log
        # that was just written, and the non-zero exit is what schedulers act on.
        logger.exception("ingestion failed")
        report_data: dict[str, Any] = json.loads(args.log.read_text(encoding="utf-8"))
        print(summarise(IngestionReport(**report_data)))
        print(f"  log: {args.log}")
        return 1
    print(summarise(report))
    print(f"  log: {args.log}")
    return 0


def main() -> None:
    """CLI: python -m src.vectorstore.ingest [--rebuild]"""
    raise SystemExit(run_from_args(cli_args()))


if __name__ == "__main__":
    main()
