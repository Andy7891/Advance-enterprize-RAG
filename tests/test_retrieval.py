"""Retrieval tests across components: chunks -> ingestion -> Qdrant -> search.

Two kinds, deliberately kept apart:

* Hermetic (default): the real Embedder, store and ingestion job, with a toy
  bag-of-words model and in-memory Qdrant. They run anywhere, including CI.
* Live (`-m live`): the real nomic-embed-text model and the real Qdrant server, on a
  scratch collection built from real chunks. They skip, with the reason, when the
  services are not running.

Per-component unit tests live in tests/embedding and tests/vectorstore; these check
that the components produce the right answers together.
"""

from __future__ import annotations

import functools
import json
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

import src.embedding.embedder as embedder_module
import src.embedding.lock as lock_module
import src.ingestion.pipeline as pipeline_module
import src.vectorstore.ingest as ingest_module
import src.vectorstore.setup as vector_setup
import src.vectorstore.store as store_module
from src.core.config import settings
from src.embedding.embedder import Embedder
from src.embedding.lock import fetch_tags
from src.vectorstore.ingest import cli_args, run_from_args, run_ingestion
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore
from tests.support import (
    OLLAMA_URL,
    QDRANT_URL,
    BagOfWordsClient,
    live_services_down,
    pinned_digest,
    write_corpus,
)

COLLECTION = "chunks"

CORPUS = {
    "company/about.md": [
        "Acme was founded in 2015 by Ada Lovelace.",
        "Acme employs thirty people across two offices.",
    ],
    "company/culture.md": ["Our values are curiosity, honesty and kindness."],
    "products/widget.md": [
        "The widget ships in blue and red colours.",
        "Widget pricing starts at ten dollars per unit.",
    ],
}


def toy_embedder_factory(model: BagOfWordsClient):
    return lambda: Embedder(model, verify=pinned_digest)


@pytest.fixture
def indexed(tmp_path):
    """The toy corpus ingested into in-memory Qdrant through the real job."""
    client = QdrantClient(":memory:")
    model = BagOfWordsClient()
    chunks_dir = tmp_path / "chunks"
    write_corpus(chunks_dir, CORPUS)

    def ingest(**kwargs):
        return run_ingestion(
            client,
            toy_embedder_factory(model),
            collection=COLLECTION,
            chunks_dir=chunks_dir,
            log_path=tmp_path / "log.json",
            **kwargs,
        )

    ingest()
    store = QdrantVectorStore(client, Embedder(model, verify=pinned_digest), collection=COLLECTION)
    return SimpleNamespace(
        client=client, model=model, store=store, chunks_dir=chunks_dir, ingest=ingest
    )


# --- the toy model itself ----------------------------------------------------------


def test_toy_model_is_deterministic_normalised_and_word_based():
    model = BagOfWordsClient()
    founded = model.vector("Acme was founded in 2015")
    assert founded == BagOfWordsClient().vector("Acme was founded in 2015")
    assert sum(x * x for x in founded) == pytest.approx(1.0)

    def cosine(a, b):
        return sum(x * y for x, y in zip(model.vector(a), model.vector(b)))

    # Sharing a topic word must score higher than sharing nothing, or every flow
    # test below would pass or fail by accident.
    assert cosine("who founded acme", "Acme was founded in 2015") > cosine(
        "who founded acme", "The widget ships in blue"
    )


# --- hermetic end-to-end retrieval ----------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Who founded Acme?", "company/about.md_0"),
        ("What are our values?", "company/culture.md_0"),
        ("What is the widget pricing?", "products/widget.md_1"),
        ("Which colours does the widget ship in?", "products/widget.md_0"),
    ],
)
def test_ingested_corpus_answers_questions(indexed, question, expected):
    # INTERVIEW: asserted by rank, not by score. Scores depend on the model and
    # shift for harmless reasons; "the answering chunk comes first" is the property
    # retrieval is actually for.
    assert indexed.store.search(question, k=3)[0].chunk_id == expected


def test_search_returns_complete_chunks_with_descending_scores(indexed):
    results = indexed.store.search_with_scores("Who founded Acme?", k=5)

    assert len(results) == 5
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    top = results[0].chunk
    assert (top.text, top.category, top.chunk_index) == (
        CORPUS["company/about.md"][0],
        "company",
        0,
    )


def test_both_sides_reach_the_model_with_their_own_prefix(indexed):
    indexed.model.inputs.clear()
    indexed.store.search("Who founded Acme?", k=1)
    assert indexed.model.inputs == [f"{settings.embed_query_prefix}Who founded Acme?"]

    indexed.model.inputs.clear()
    indexed.ingest()
    assert indexed.model.inputs, "re-ingesting must embed the documents again"
    assert all(text.startswith(settings.embed_document_prefix) for text in indexed.model.inputs)


def test_edited_document_is_found_by_its_new_text_after_reingest(indexed):
    write_corpus(
        indexed.chunks_dir,
        {**CORPUS, "company/about.md": ["Acme was founded in 1999 by Grace Hopper."]},
    )
    indexed.ingest()

    top = indexed.store.search("Who is Grace Hopper?", k=1)[0]
    assert top.chunk_id == "company/about.md_0" and "Grace Hopper" in top.text
    # The document shrank from 2 chunks to 1: its old second chunk must be gone.
    stored = indexed.store.stored_chunk_ids_by_document()["doc-company/about.md"]
    assert stored == {"company/about.md_0"}


def test_deleted_document_disappears_from_search(indexed):
    (indexed.chunks_dir / "products__widget.md.json").unlink()
    indexed.ingest()

    ids = [c.chunk_id for c in indexed.store.search("widget pricing colours", k=10)]
    assert not any(chunk_id.startswith("products/widget.md") for chunk_id in ids)


# --- command-line entry points, run in-process ---------------------------------------


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    """Route every CLI's clients to in-memory Qdrant and the toy model."""
    client = QdrantClient(":memory:")
    model = BagOfWordsClient()
    toy_embedder = functools.partial(Embedder, verify=pinned_digest)
    monkeypatch.setattr(ingest_module, "build_qdrant_client", lambda url: client)
    monkeypatch.setattr(ingest_module, "Embedder", toy_embedder)
    monkeypatch.setattr(store_module, "build_qdrant_client", lambda url: client)
    monkeypatch.setattr(embedder_module, "Embedder", toy_embedder)
    monkeypatch.setattr(embedder_module, "build_ollama_client", lambda url: model)
    chunks_dir = tmp_path / "chunks"
    write_corpus(chunks_dir, CORPUS)
    return SimpleNamespace(client=client, chunks_dir=chunks_dir, log=tmp_path / "log.json")


def run_main(monkeypatch, main, *argv):
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    try:
        main()
    except SystemExit as exit_:
        return exit_.code
    return 0


def test_ingest_cli_returns_0_and_writes_the_log(cli_env, capsys):
    args = cli_args(["--chunks-dir", str(cli_env.chunks_dir), "--log", str(cli_env.log)])
    assert run_from_args(args) == 0
    assert "SUCCEEDED: 5 chunks from 3 documents" in capsys.readouterr().out
    assert json.loads(cli_env.log.read_text(encoding="utf-8"))["verified"] is True


def test_ingest_cli_returns_1_and_still_writes_the_log(cli_env, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    args = cli_args(["--chunks-dir", str(empty), "--log", str(cli_env.log)])
    assert run_from_args(args) == 1
    assert "failed in load" in capsys.readouterr().out
    assert json.loads(cli_env.log.read_text(encoding="utf-8"))["status"] == "failed"


def test_store_cli_index_then_search(cli_env, monkeypatch, capsys):
    index_argv = ["index", "--chunks-dir", str(cli_env.chunks_dir), "--log", str(cli_env.log)]
    assert run_main(monkeypatch, store_module.main, *index_argv) == 0

    assert run_main(monkeypatch, store_module.main, "search", "Who founded Acme?", "-k", "2") == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("1. ") and "company/about.md_0" in line for line in lines)


def test_store_cli_health_exit_codes(cli_env, monkeypatch, capsys):
    assert run_main(monkeypatch, store_module.main, "health") == 1
    cli_env.client.create_collection(
        COLLECTION, vectors_config=VectorParams(size=settings.embed_dim, distance=Distance.COSINE)
    )
    assert run_main(monkeypatch, store_module.main, "health") == 0
    assert capsys.readouterr().out.splitlines() == ["NOT ready", "ready"]


def test_embedder_cli_reports_every_chunk(cli_env, monkeypatch, capsys):
    from src.ingestion.pipeline import read_chunks

    monkeypatch.setattr(pipeline_module, "read_chunks", lambda: read_chunks(cli_env.chunks_dir))
    assert run_main(monkeypatch, embedder_module.main) == 0
    assert "OK: embedded 5 chunks" in capsys.readouterr().out


def test_setup_and_lock_clis(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(vector_setup, "verify_model", pinned_digest)
    monkeypatch.setattr(vector_setup, "build_qdrant_client", lambda url: cli_env.client)
    monkeypatch.setattr(lock_module, "verify_model", lambda url: settings.embed_model_digest)

    assert run_main(monkeypatch, vector_setup.main) == 0
    assert run_main(monkeypatch, lock_module.main) == 0
    out = capsys.readouterr().out
    assert "collection 'chunks' created" in out
    assert f"digest {settings.embed_model_digest[:12]} matches pin" in out


# --- live: the real model and the real server ------------------------------------------

LIVE_FILES = [
    "company__about.md.json",
    "company__careers.md.json",
    "company__culture.md.json",
    "products__Carllm.md.json",
    "products__Lifellm.md.json",
    "products__Markellm.md.json",
    "contracts__Contract with TechDrive Insurance for Carllm.md.json",
    "employees__Avery Lancaster.md.json",
]
REAL_CHUNKS = Path("data/chunks")

# Checked once at import: each check is a network call with a timeout, and repeating
# it per test would make a run with the services down slow for no benefit.
_SERVICES_DOWN = live_services_down()
_LIVE_MARKS = [
    pytest.mark.live,
    pytest.mark.skipif(_SERVICES_DOWN is not None, reason=_SERVICES_DOWN or ""),
    pytest.mark.skipif(
        not all((REAL_CHUNKS / name).exists() for name in LIVE_FILES),
        reason="data/chunks is missing the live subset; run `python -m src.ingestion.pipeline`",
    ),
]


def live_test(function):
    """Mark a test as live and skip it, with the reason, when it cannot run here."""
    for mark in _LIVE_MARKS:
        function = mark(function)
    return function


@pytest.fixture(scope="session")
def live_index(tmp_path_factory):
    """A scratch collection built once per session from 8 real documents.

    Uniquely named so parallel or crashed runs never collide with each other or
    with the real `chunks` collection, and deleted afterwards.
    """
    work = tmp_path_factory.mktemp("live")
    chunks_dir = work / "chunks"
    chunks_dir.mkdir()
    for name in LIVE_FILES:
        shutil.copy(REAL_CHUNKS / name, chunks_dir / name)
    (chunks_dir / "report.json").write_text(json.dumps({"documents_failed": 0}), encoding="utf-8")

    client = build_qdrant_client(QDRANT_URL)
    collection = f"test_live_{uuid.uuid4().hex[:8]}"

    def make_embedder():
        return Embedder(embedder_module.build_ollama_client(OLLAMA_URL), ollama_url=OLLAMA_URL)

    try:
        report = run_ingestion(
            client,
            make_embedder,
            collection=collection,
            chunks_dir=chunks_dir,
            log_path=work / "log.json",
        )
        store = QdrantVectorStore(client, make_embedder(), collection=collection)
        yield SimpleNamespace(client=client, store=store, report=report, collection=collection)
    finally:
        # INTERVIEW: cleanup in finally, so a failing test cannot leave a scratch
        # collection behind on a shared server.
        if client.collection_exists(collection):
            client.delete_collection(collection)


def top_ids(live_index, question: str, k: int = 3) -> list[str]:
    return [chunk.chunk_id for chunk in live_index.store.search(question, k=k)]


@live_test
def test_live_ingestion_is_verified(live_index):
    report = live_index.report
    assert (report.status, report.verified) == ("succeeded", True)
    assert report.documents_on_disk == len(LIVE_FILES)
    assert report.points_after == report.chunks_on_disk


@live_test
@pytest.mark.parametrize(
    ("question", "acceptable"),
    [
        ("Who founded Insurellm?", {"company/about.md_0"}),
        ("What are the company values?", {"company/careers.md_1", "company/culture.md_1"}),
        ("Which product is for life insurance?", {"products/Lifellm.md_0"}),
    ],
)
def test_live_known_answers_rank_in_top_3(live_index, question, acceptable):
    assert acceptable & set(top_ids(live_index, question)), top_ids(live_index, question)


@live_test
@pytest.mark.xfail(
    strict=True,
    reason="Known dense-retrieval miss (docs/14 C11): product names are not separated by "
    "embeddings; expected to pass once hybrid keyword + vector search exists.",
)
def test_live_named_product_question_finds_its_product_page(live_index):
    ids = top_ids(live_index, "What does the Carllm product do?")
    assert any(chunk_id.startswith("products/Carllm.md") for chunk_id in ids), ids


@live_test
@pytest.mark.xfail(
    strict=True,
    reason="Known miss: 'CEO' questions rank product and contract chunks above the CEO's "
    "own record; expected to pass once hybrid search or reranking exists.",
)
def test_live_ceo_question_finds_the_ceo(live_index):
    ids = top_ids(live_index, "Who is the CEO of Insurellm?")
    assert {"employees/Avery Lancaster.md_0", "company/about.md_0"} & set(ids), ids


@live_test
def test_live_search_is_deterministic(live_index):
    first = live_index.store.search_with_scores("Who founded Insurellm?", k=5)
    second = live_index.store.search_with_scores("Who founded Insurellm?", k=5)
    assert [(r.chunk.chunk_id, r.score) for r in first] == [
        (r.chunk.chunk_id, r.score) for r in second
    ]


@live_test
def test_live_collection_is_ready_with_payload_indexes(live_index):
    assert live_index.store.health_check() is True
    # The in-memory client ignores payload indexes, so only the server can prove
    # the delete guard and category filters are indexed.
    schema = live_index.client.get_collection(live_index.collection).payload_schema
    assert {"doc_id", "category"} <= set(schema)


@live_test
def test_live_server_rejects_ids_the_in_memory_client_accepts(live_index):
    point = PointStruct(id=-1, vector=[1.0, 0.0])
    memory = QdrantClient(":memory:")
    memory.create_collection("c", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    memory.upsert("c", points=[point])  # accepted locally

    name = f"test_live_ids_{uuid.uuid4().hex[:8]}"
    client = live_index.client
    client.create_collection(name, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    try:
        with pytest.raises(UnexpectedResponse):
            client.upsert(name, points=[point])
    finally:
        client.delete_collection(name)


@live_test
def test_live_client_negotiates_gzip_only(live_index):
    seen = []
    http = live_index.client._client.openapi_client.client._client
    http.event_hooks["response"].append(
        lambda r: seen.append(
            (r.request.headers["accept-encoding"], r.headers.get("content-encoding"))
        )
    )
    live_index.client.count(live_index.collection)
    assert seen[-1] == ("gzip", "gzip")


@live_test
def test_live_ollama_serves_the_pinned_model():
    tags = fetch_tags(OLLAMA_URL)["models"]
    served = {m["name"]: m["digest"] for m in tags}
    assert served.get(f"{settings.embed_model}:latest") == settings.embed_model_digest
