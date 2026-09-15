from pathlib import Path

import pytest

from src.ingestion.chunker import Chunker, ChunkerConfig
from src.ingestion.loader import LoadedDocument, load_document

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


class FakeEmbedder:
    """Deterministic stand-in for a real embedding model.

    Maps text to a vector derived from which keyword it contains, so
    sentences about the same topic land close together and a topic switch
    produces a large cosine distance -- exactly the signal SemanticChunker
    looks for, without any network or model server.
    """

    TOPICS = ("alpha", "beta", "gamma")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [0.0] * len(self.TOPICS)
        for index, topic in enumerate(self.TOPICS):
            if topic in lowered:
                vector[index] = 1.0
        # A non-zero fallback keeps cosine distance defined for text that
        # matches no topic at all.
        if not any(vector):
            vector = [0.33] * len(self.TOPICS)
        return vector


def _doc(text: str, source_path: str = "company/about.md") -> LoadedDocument:
    return LoadedDocument(
        source_path=source_path,
        category="company",
        title="Test Doc",
        format="md",
        text=text,
        char_count=len(text),
    )


@pytest.fixture
def chunker() -> Chunker:
    return Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=1))


def test_empty_document_produces_no_chunks(chunker):
    assert chunker.chunk(_doc("   \n  "), DATA_ROOT) == []


def test_single_sentence_produces_one_chunk(chunker):
    chunks = chunker.chunk(_doc("Alpha is the first topic here."), DATA_ROOT)
    assert len(chunks) == 1
    assert chunks[0].text.strip() == "Alpha is the first topic here."


def test_chunk_ids_follow_source_underscore_index_scheme(chunker):
    text = "Alpha one. Alpha two. Beta one. Beta two. Gamma one. Gamma two."
    chunks = chunker.chunk(_doc(text), DATA_ROOT)
    for index, chunk in enumerate(chunks):
        assert chunk.chunk_id == f"company/about.md_{index}"


def test_chunk_indices_are_contiguous_from_zero(chunker):
    text = "Alpha one. Alpha two. Beta one. Beta two. Gamma one."
    chunks = chunker.chunk(_doc(text), DATA_ROOT)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_offsets_land_in_metadata_not_first_class_fields(chunker):
    chunks = chunker.chunk(_doc("Alpha one. Alpha two."), DATA_ROOT)
    chunk = chunks[0]
    assert "start_char" in chunk.metadata
    assert "end_char" in chunk.metadata
    assert not hasattr(chunk, "start_char")


def test_offsets_are_ordered_and_non_negative(chunker):
    text = "Alpha one. Alpha two. Beta one. Beta two. Gamma one. Gamma two."
    chunks = chunker.chunk(_doc(text), DATA_ROOT)
    for chunk in chunks:
        start = chunk.metadata["start_char"]
        end = chunk.metadata["end_char"]
        assert 0 <= start < end


def test_approx_token_count_is_populated(chunker):
    chunks = chunker.chunk(_doc("Alpha one. Alpha two."), DATA_ROOT)
    assert all(c.approx_token_count >= 1 for c in chunks)


def test_undersized_chunks_are_merged_backwards():
    # min_chunk_chars is set far above any produced chunk, so every chunk
    # after the first must fold into its predecessor.
    aggressive = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=10_000))
    text = "Alpha one. Alpha two. Beta one. Beta two. Gamma one. Gamma two."
    chunks = aggressive.chunk(_doc(text), DATA_ROOT)
    assert len(chunks) == 1


def test_merging_preserves_total_content(chunker):
    text = "Alpha one. Alpha two. Beta one. Beta two. Gamma one."
    merged = Chunker(FakeEmbedder(), ChunkerConfig(min_chunk_chars=10_000))
    one_chunk = merged.chunk(_doc(text), DATA_ROOT)
    many_chunks = chunker.chunk(_doc(text), DATA_ROOT)
    # Whitespace differs because merging joins with a space, so compare the
    # content with all whitespace removed.
    assert "".join(one_chunk[0].text.split()) == "".join(
        "".join(c.text.split()) for c in many_chunks
    )


def test_source_metadata_is_carried_through(chunker):
    chunks = chunker.chunk(_doc("Alpha one. Alpha two."), DATA_ROOT)
    chunk = chunks[0]
    assert chunk.source_path == "company/about.md"
    assert chunk.category == "company"
    assert chunk.title == "Test Doc"


def test_chunk_all_preserves_document_order(chunker):
    docs = [
        _doc("Alpha one. Alpha two.", "company/about.md"),
        _doc("Beta one. Beta two.", "company/careers.md"),
    ]
    chunks = chunker.chunk_all(docs, DATA_ROOT)
    sources = [c.source_path for c in chunks]
    assert sources.index("company/about.md") < sources.index("company/careers.md")


def test_chunking_is_repeatable_for_identical_input(chunker):
    text = "Alpha one. Alpha two. Beta one. Gamma one."
    first = chunker.chunk(_doc(text), DATA_ROOT)
    second = chunker.chunk(_doc(text), DATA_ROOT)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert [c.text for c in first] == [c.text for c in second]


def test_works_on_a_real_corpus_file(chunker):
    document = load_document(DATA_ROOT / "company" / "about.md", DATA_ROOT)
    chunks = chunker.chunk(document, DATA_ROOT)
    assert chunks
    assert all(c.source_path == "company/about.md" for c in chunks)
    assert all(c.text.strip() for c in chunks)
