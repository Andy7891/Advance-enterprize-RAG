"""Shared pytest fixtures.

Two deliberately separate sources of test data live here, and knowing which a
test uses matters:

- `fixture_corpus` — small, committed files under tests/fixtures/corpus/ that
  this test suite fully controls. Use this by default, so a test never breaks
  because someone edited a real document.
- `real_corpus` — the actual data/ directory. Kept for a handful of canary
  tests, because a controlled fixture can only contain problems we already
  thought of; the real corpus is what surfaced the .ipynb_checkpoints
  duplication nobody predicted.
"""

from pathlib import Path

import pytest

FIXTURE_CORPUS = Path(__file__).resolve().parent / "fixtures" / "corpus"
REAL_CORPUS = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def fixture_corpus() -> Path:
    """A tiny, fully-controlled corpus: 2 categories, 3 markdown files."""
    return FIXTURE_CORPUS


@pytest.fixture
def real_corpus() -> Path:
    """The project's actual data/ directory, for canary tests only."""
    return REAL_CORPUS


class FakeEmbedder:
    """Maps text to a vector by which keyword it contains, so sentences about
    the same topic sit close together and a topic switch produces a genuinely
    large cosine distance — the signal SemanticChunker looks for, with no
    model server involved.
    """

    TOPICS = ("acme", "beta", "gamma")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [1.0 if topic in lowered else 0.0 for topic in self.TOPICS]
        # A non-zero fallback keeps cosine distance defined for text matching
        # no topic at all; an all-zero vector would divide by zero.
        return vector if any(vector) else [0.33] * len(self.TOPICS)


class FakeCounter:
    """Word count standing in for a real tokenizer, so the suite never loads
    tiktoken's encoding file (which needs a warm cache or network).
    """

    def count(self, text: str) -> int:
        return len(text.split())


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_counter() -> FakeCounter:
    return FakeCounter()
