"""Tests for the embedding model lock, using an injected tags fetcher so no
Ollama server is needed."""

import pytest

from src.core.config import settings
from src.embedding.lock import ModelVersionMismatch, as_document, as_query, verify_model

PINNED = settings.embed_model_digest


def tags(*models):
    return lambda _url: {"models": [{"name": n, "digest": d} for n, d in models]}


def test_matching_digest_passes():
    assert verify_model("x", fetch=tags(("nomic-embed-text:latest", PINNED))) == PINNED


def test_untagged_config_name_matches_latest_tag():
    # Ollama always reports an explicit tag, so "nomic-embed-text" in config must
    # still find "nomic-embed-text:latest".
    verify_model("x", model="nomic-embed-text", fetch=tags(("nomic-embed-text:latest", PINNED)))


def test_changed_digest_raises():
    with pytest.raises(ModelVersionMismatch, match="model changed"):
        verify_model("x", fetch=tags(("nomic-embed-text:latest", "f" * 64)))


def test_missing_model_raises_with_pull_hint():
    with pytest.raises(ModelVersionMismatch, match="ollama pull"):
        verify_model("x", fetch=tags(("some-other-model:latest", PINNED)))


def test_similarly_named_model_is_not_accepted():
    # Prefix matching would wrongly accept a different model.
    with pytest.raises(ModelVersionMismatch):
        verify_model("x", fetch=tags(("nomic-embed-text-v2:latest", PINNED)))


def test_prefixes_are_applied_asymmetrically():
    assert as_query("q") == "search_query: q"
    assert as_document("d") == "search_document: d"


def test_config_values_are_mutually_consistent():
    # The chunk cap must sit under the model's context, counted pessimistically.
    assert settings.embed_max_chunk_tokens < settings.embed_context_tokens
    assert settings.embed_dim == 768
