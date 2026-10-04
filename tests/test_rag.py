"""RAG pipeline tests across every component: retrieval -> budgeting -> prompting -> generation.

Two kinds, deliberately kept apart:

* Hermetic (default): a real RetrievalPipeline (real HybridRetriever, an in-memory Qdrant with
  the toy BagOfWordsClient embedder, a graph that never finds anything), real prompts.py, and a
  real RAGPipeline -- but a fake LLM standing in for Ollama, since the point here is proving the
  real retrieval+budget+prompt seam works, not re-testing generation quality.
* Live (`-m live`): the real corpus, real Qdrant, real Neo4j, and real Ollama, with a reranker
  stand-in so no Cohere key is needed (mirrors tests/retrieval/test_pipeline.py's own fixture).

RAGPipeline's OWN orchestration logic (call order, validation, error propagation, the budget
short-circuit) is already unit-tested in tests/orchestration/test_rag.py against fully-faked
dependencies; this file checks the SEAMS between already-unit-tested real components instead.
"""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from src.embedding.embedder import Embedder, build_ollama_client
from src.graph.setup import build_driver
from src.orchestration.llm import build_llm
from src.orchestration.prompts import REFUSAL_TEXT
from src.orchestration.rag import RAGPipeline
from src.retrieval.graph_retriever import GraphRetriever
from src.retrieval.graph_store import GraphStore
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.pipeline import RetrievalPipeline
from src.retrieval.reranker import RerankedChunk
from src.vectorstore.ingest import run_ingestion
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore
from tests.support import (
    OLLAMA_URL,
    QDRANT_URL,
    BagOfWordsClient,
    live_services_down,
    neo4j_down,
    pinned_digest,
    write_corpus,
)

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
# the same small toy corpus tests/test_retrieval.py and tests/retrieval/test_pipeline.py use


class NoOpGraph:
    def retrieve(self, query, k):
        return []
        # simulates a graph that never recognises any entity, as the toy corpus has none


class IdentityReranker:
    """A reranker stand-in that keeps the hybrid's own order -- needs no Cohere key."""

    def rerank_with_scores(self, query, chunks, k):
        return [
            RerankedChunk(chunk=c, relevance_score=1.0 - 0.01 * i, input_rank=i + 1)
            for i, c in enumerate(chunks[:k])
        ]
        # keeps the first k chunks in the order they arrived, with falling fake scores


class FakeLLM:
    """Answers generate()/stream() from one canned reply -- no real Ollama needed."""

    def __init__(self, reply="A canned answer [1]."):
        self.reply = reply
        # the text every generate()/stream() call will answer with
        self.generate_calls: list[dict] = []
        # records every generate() call this fake received, in order
        self.stream_calls: list[dict] = []
        # records every stream() call this fake received, in order

    def generate(self, prompt, temperature, max_tokens, *, system=None):
        self.generate_calls.append({"prompt": prompt, "system": system})
        # keeps the exact real prompt and real system text this call actually received
        return self.reply

    def stream(self, prompt, temperature, *, system=None):
        self.stream_calls.append({"prompt": prompt, "system": system})
        # keeps the exact real prompt and real system text this call actually received
        yield self.reply


# --- hermetic: real retrieval + real prompts.py + real RAGPipeline, only the LLM is fake -------


@pytest.fixture
def indexed_rag(tmp_path):
    """The toy corpus ingested for real, wired into a real RAGPipeline with a fake LLM."""
    client = QdrantClient(":memory:")
    # starts a real Qdrant that lives only in this process
    model = BagOfWordsClient()
    # makes the toy embedding model, which needs no Ollama
    chunks_dir = tmp_path / "chunks"
    write_corpus(chunks_dir, CORPUS)
    # writes the toy corpus in the layout the real ingestion job reads
    run_ingestion(
        client,
        lambda: Embedder(model, verify=pinned_digest),
        collection="chunks",
        chunks_dir=chunks_dir,
        log_path=tmp_path / "log.json",
    )
    # runs the REAL ingestion job to embed and store every toy chunk
    vector = QdrantVectorStore(client, Embedder(model, verify=pinned_digest), collection="chunks")
    hybrid = HybridRetriever(vector, NoOpGraph())
    retrieval = RetrievalPipeline(hybrid, IdentityReranker())
    # a real RetrievalPipeline, wired to real (toy-backed) retrieval, needing no Cohere key
    llm = FakeLLM()
    return RAGPipeline(retrieval, llm), llm
    # hands back a real RAGPipeline plus the fake LLM, so a test can inspect what it received


def test_a_real_question_flows_through_real_retrieval_into_a_real_source_list(indexed_rag):
    rag, llm = indexed_rag
    answer = rag.query("Who founded Acme?")
    # asks a real question the toy corpus can really answer
    assert llm.generate_calls, "the real retrieval seam must have found something to ground on"
    assert answer.startswith("A canned answer [1].")
    # requires the fake LLM's own canned text to still be the start of the real answer
    assert "Sources:" in answer and "company/about.md" in answer
    # requires the mechanical source list to name the REAL file real retrieval actually found --
    # a fake LLM could never have produced this text; it only comes from real RankedChunk data


def test_the_real_system_prompt_and_a_real_numbered_user_prompt_reach_the_llm(indexed_rag):
    rag, llm = indexed_rag
    rag.query("Who founded Acme?")
    call = llm.generate_calls[0]
    assert call["system"] is not None and "ONLY the numbered source excerpts" in call["system"]
    # requires the real, published SYSTEM_PROMPT, not a placeholder or empty string
    assert "Question: Who founded Acme?" in call["prompt"] and "[1]" in call["prompt"]
    # requires a real, numbered user prompt built from the real retrieved chunk


def test_a_genuinely_empty_index_hits_the_real_refusal_path_end_to_end():
    client = QdrantClient(":memory:")
    model = BagOfWordsClient()
    vector = QdrantVectorStore(client, Embedder(model, verify=pinned_digest), collection="chunks")
    vector.ensure_collection()
    # creates a real, valid, searchable Qdrant collection with zero points -- run_ingestion
    # itself deliberately refuses to run against zero chunks, so this bypasses the job and
    # builds the empty collection directly, the one honest way to get a genuinely empty index
    hybrid = HybridRetriever(vector, NoOpGraph())
    retrieval = RetrievalPipeline(hybrid, IdentityReranker())
    llm = FakeLLM()
    rag = RAGPipeline(retrieval, llm)

    assert rag.query("Who founded Acme?") == REFUSAL_TEXT
    # requires the REAL refusal string, produced by the REAL, wired-up empty-retrieval path
    assert llm.generate_calls == []
    # requires the model to never have been called for a genuinely empty real index


# --- live: the real corpus, real Neo4j/Qdrant, and real Ollama; a fake reranker stands in ------

FIVE_LABELLED_QUESTIONS = [
    "Who founded Insurellm?",
    "What are the company values?",
    "Which product is for life insurance?",
    "What does the Carllm product do?",
    "Who is the CEO of Insurellm?",
]
# the same five questions tests/retrieval/test_pipeline.py's own live report uses

KNOWN_RELIABLE = [
    ("Who founded Insurellm?", "avery lancaster"),
    ("Which product is for life insurance?", "lifellm"),
    ("What are the company values?", "innovation"),
]
# the subset of the five already known (Phase 5) to retrieve well at k=3; the other two
# (Carllm, CEO) are documented retrieval misses (notes/step-5.2, step-5.3) -- reported below,
# not asserted, matching this project's own established honesty rule for known weak spots


@pytest.fixture(scope="module")
def live_rag():
    reason = live_services_down() or neo4j_down()
    # checks whether Qdrant, Ollama or Neo4j is unreachable
    if reason:
        pytest.skip(reason)
        # skips every test in this fixture's tests, saying which service is missing
    embedder = Embedder(build_ollama_client(OLLAMA_URL), ollama_url=OLLAMA_URL)
    vector = QdrantVectorStore(build_qdrant_client(QDRANT_URL), embedder, collection="chunks")
    if vector.count() == 0:
        pytest.skip("the 'chunks' collection is empty; run `python -m src.vectorstore.ingest`")
        # skips instead of failing, saying how to fix it
    with build_driver() as driver:
        store = GraphStore(driver)
        if not any(store.all_entity_names().values()):
            pytest.skip("the graph is empty; run `python -m src.graph.ingest`")
        hybrid = HybridRetriever(vector, GraphRetriever(store))
        retrieval = RetrievalPipeline(hybrid, IdentityReranker())
        # a real RetrievalPipeline needing no Cohere key at all
        llm = build_llm(OLLAMA_URL)
        # a real LocalLLM over the real, already-pulled model
        yield RAGPipeline(retrieval, llm)
        # hands the tests a fully real RAGPipeline


@pytest.mark.live
def test_live_report_on_the_five_labelled_questions(live_rag, capsys):
    rows = []
    # will hold one printed line per question
    for question in FIVE_LABELLED_QUESTIONS:
        answer = live_rag.query(question)
        assert "Sources:" in answer
        # requires the mechanical fallback to have run for every question, pass or fail otherwise
        rows.append(f"{question:<40} -> {answer}")
        # records the real answer, to read, not to assert a specific outcome from
    with capsys.disabled():
        # lets the report show even when pytest is capturing output
        print("\nRAGPipeline.query(question), full pipeline:\n" + "\n".join(rows))
    assert len(rows) == len(FIVE_LABELLED_QUESTIONS)
    # requires that every question was run


@pytest.mark.live
@pytest.mark.parametrize(("question", "acceptable_fact"), KNOWN_RELIABLE)
# runs once per question already known to retrieve well at k=3
def test_live_known_reliable_questions_produce_correct_answers(live_rag, question, acceptable_fact):
    answer = live_rag.query(question)
    assert acceptable_fact in answer.lower(), answer
    # requires the real, correct fact to appear in the real, generated answer


@pytest.mark.live
def test_live_stream_also_works_through_the_real_integrated_stack(live_rag):
    fragments = list(live_rag.stream("Who founded Insurellm?"))
    # asks the same reliable question, via the streaming path this time
    assert len(fragments) > 1
    # requires the answer to have arrived as more than one streamed piece
    full_text = "".join(fragments)
    assert "avery lancaster" in full_text.lower()
    # requires the real, correct fact to appear in the reassembled answer
    assert "Sources:" in full_text
    # requires the mechanical source list to have arrived as part of the real stream
