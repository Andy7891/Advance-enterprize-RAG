"""Shared helpers for retrieval tests: chunk factories, a toy embedding model, and
checks for whether the live services are reachable.

A plain module rather than conftest fixtures: these are functions and classes that
tests call with arguments, and importing them explicitly keeps it obvious where
each one comes from.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from pathlib import Path

from src.core.config import settings
from src.ingestion.pipeline import REPORT_FILENAME, write_chunks
from src.ingestion.schemas import DocumentChunk

QDRANT_URL = "http://localhost:6333"
OLLAMA_URL = "http://localhost:11434"
NEO4J_HTTP_URL = "http://localhost:7474"


def make_chunk(source: str, index: int, text: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=f"{source}_{index}",
        doc_id=f"doc-{source}",
        source_path=source,
        category=source.split("/")[0],
        title=source,
        chunk_index=index,
        text=text,
        char_count=len(text),
        approx_token_count=max(1, len(text) // 4),
        metadata={"start_char": 0, "end_char": len(text), "tokens_approx": max(1, len(text) // 4)},
    )


def write_corpus(
    chunks_dir: Path, documents: dict[str, list[str]], documents_failed: int = 0
) -> None:
    """Write one chunk file per document, in the layout the ingestion job reads."""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for source, texts in documents.items():
        write_chunks(
            [make_chunk(source, i, text) for i, text in enumerate(texts)],
            chunks_dir / f"{source.replace('/', '__')}.json",
        )
    (chunks_dir / REPORT_FILENAME).write_text(
        json.dumps({"documents_failed": documents_failed}), encoding="utf-8"
    )


class BagOfWordsClient:
    """A toy embedding model with the same `embed` shape as `ollama.Client`.

    Each word is hashed into one of the model's dimensions and counted, and the
    vector is scaled to length 1. Texts sharing words point in similar directions,
    so a question really does find the chunk that uses its words -- which is enough
    to test the retrieval flow end to end without a model server. It knows nothing
    about synonyms: "cost" and "price" are unrelated to it.
    """

    # The task prefixes and question words carry no topic for a word-count model;
    # counting them would make every query look similar to every document.
    IGNORED = {
        "search", "query", "document", "what", "who", "which", "how", "does", "do", "is",
        "are", "the", "a", "an", "of", "in", "to", "and", "for", "by", "our", "per",
    }  # fmt: skip

    def __init__(self, dim: int = settings.embed_dim) -> None:
        self.dim = dim
        self.inputs: list[str] = []

    def embed(self, model="", input=(), truncate=None):
        texts = [input] if isinstance(input, str) else list(input)
        self.inputs.extend(texts)
        return {"embeddings": [self.vector(text) for text in texts]}

    def vector(self, text: str) -> list[float]:
        counts = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in self.IGNORED:
                continue
            # hashlib, not hash(): Python randomises str hashes per process, which
            # would give a different "model" on every test run.
            counts[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(c * c for c in counts))
        if norm == 0:
            # A text with no counted words still needs a valid unit vector.
            counts[0], norm = 1.0, 1.0
        return [c / norm for c in counts]


def pinned_digest(url: str, model: str | None = None) -> str:
    """A stand-in for verify_model that accepts without a network call."""
    return settings.embed_model_digest


def reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def live_services_down() -> str | None:
    """None if Qdrant and Ollama both answer, otherwise a reason to skip."""
    missing = [
        name
        for name, url in (
            ("Qdrant", f"{QDRANT_URL}/readyz"),
            ("Ollama", f"{OLLAMA_URL}/api/version"),
        )
        if not reachable(url)
    ]
    if missing:
        services = " and ".join(missing)
        return f"{services} not reachable; start with `docker compose up -d qdrant ollama`"
    return None


def neo4j_down() -> str | None:
    """None if the Neo4j HTTP endpoint answers, otherwise a reason to skip.

    Only reachability decides skipping: a reachable server that rejects the
    configured password is a real failure, not a missing service.
    """
    if reachable(NEO4J_HTTP_URL):
        return None
    return "Neo4j not reachable on :7474; start with `docker compose up -d neo4j`"
