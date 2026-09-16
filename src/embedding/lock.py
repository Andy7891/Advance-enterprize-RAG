"""Embedding model lock: verify the running model is the pinned one, and apply
the task prefixes it was trained with.

Why this exists: vectors from two different models (or two versions of one
model) live in unrelated spaces. Mixing them raises no error — similarity scores
still come back — so the only protection is refusing to run when the model
serving embeddings is not the one the stored vectors were made with.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Callable

from src.core.config import settings

TagsFetcher = Callable[[str], dict]


class ModelVersionMismatch(RuntimeError):
    """The model Ollama is serving is not the pinned model."""


def fetch_tags(ollama_url: str) -> dict:
    with urllib.request.urlopen(f"{ollama_url.rstrip('/')}/api/tags", timeout=10) as response:
        return json.load(response)


def verify_model(
    ollama_url: str,
    model: str = settings.embed_model,
    expected_digest: str = settings.embed_model_digest,
    fetch: TagsFetcher = fetch_tags,
) -> str:
    """Return the digest if the served model matches; raise otherwise.

    `fetch` is injectable so the check can be tested without a running Ollama.
    """
    tags = fetch(ollama_url)["models"]
    # Ollama reports bare names with an explicit tag ("nomic-embed-text:latest"),
    # so an untagged configured name has to match either form.
    wanted = {model, f"{model}:latest"} if ":" not in model else {model}
    served = next((m for m in tags if m["name"] in wanted), None)

    if served is None:
        raise ModelVersionMismatch(
            f"model {model!r} is not available at {ollama_url}; run `ollama pull {model}`"
        )
    if served["digest"] != expected_digest:
        # Deliberately no automatic acceptance of the new digest: upgrading the
        # model invalidates every stored vector, so it must be a human decision.
        raise ModelVersionMismatch(
            f"{model!r} digest is {served['digest'][:12]}, pinned {expected_digest[:12]}. "
            "The model changed; re-embed the collection before updating the pin."
        )
    return served["digest"]


def as_query(text: str) -> str:
    return f"{settings.embed_query_prefix}{text}"


def as_document(text: str) -> str:
    return f"{settings.embed_document_prefix}{text}"


def main() -> None:
    """CLI: python -m src.embedding.lock"""
    parser = argparse.ArgumentParser(description="Verify the pinned embedding model.")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    args = parser.parse_args()
    digest = verify_model(args.ollama_url)
    print(
        f"OK: {settings.embed_model} digest {digest[:12]} matches pin "
        f"(dim={settings.embed_dim}, context={settings.embed_context_tokens})"
    )


if __name__ == "__main__":
    main()
