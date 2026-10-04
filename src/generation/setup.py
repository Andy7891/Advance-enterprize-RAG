"""Ollama generation setup check: is a local LLM actually loaded and able to answer?

Readiness here means more than "the model is listed": Ollama can report a model in
`ollama list` that still fails at the first real request (a corrupt pull, or not enough
RAM to load it), so this check also runs one tiny, real generation call.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Literal, Protocol

import ollama

from src.core.config import settings

# A short, closed-ended prompt: enough to prove the model loads and answers coherently,
# without waiting on a long generation just to check readiness.
PROBE_PROMPT = "Reply with exactly one word: OK"
# how long the probe call may take before this check gives up and reports not ready
PROBE_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class GenerationStatus:
    ready: bool
    model: str | None = None
    response_seconds: float | None = None
    sample_reply: str | None = None
    problem: str | None = None


class GenerationClient(Protocol):
    """The part of `ollama.Client` the health check uses, so tests can pass a fake."""

    def list(self) -> ollama.ListResponse: ...

    def generate(
        self, model: str, prompt: str, *, stream: Literal[False] = False
    ) -> ollama.GenerateResponse: ...


def build_client(ollama_url: str) -> ollama.Client:
    """A real Ollama client for the given URL, with a bounded probe timeout."""
    return ollama.Client(host=ollama_url, timeout=PROBE_TIMEOUT_SECONDS)
    # PROBE_TIMEOUT_SECONDS bounds every request this client makes, not just generate();
    # a hung Ollama container fails this check in ~2 minutes instead of hanging forever


def health_check(client: GenerationClient, model: str = settings.llm_model) -> GenerationStatus:
    """Report whether the local LLM is usable. Never raises: the reason is in `problem`."""
    try:
        # asks Ollama which models are currently pulled
        listed = client.list()
    except ConnectionError as exc:
        # covers Ollama being unreachable at all (container down, wrong URL)
        return GenerationStatus(ready=False, problem=f"cannot reach Ollama: {exc}")
        # reports the connection failure without raising it further

    # Ollama reports bare names with an explicit tag ("mistral:latest"), so an
    # untagged configured name has to match either form -- the same rule
    # src/embedding/lock.py already uses for the embedding model's own name.
    wanted = {model, f"{model}:latest"} if ":" not in model else {model}
    # builds both spellings a configured, untagged model name could be reported as
    pulled_names = {entry.model for entry in listed.models}
    # collects every model name Ollama actually has pulled right now
    if not wanted & pulled_names:
        # covers the case where the configured model has never been pulled
        problem = f"model {model!r} is not pulled; run `ollama pull {model}`"
        # names the exact command that fixes it
        return GenerationStatus(ready=False, problem=problem)

    started = time.perf_counter()
    # marks the start of the one real generation call this check makes
    try:
        # sends one small, real prompt to the model and waits for a real reply
        reply = client.generate(model=model, prompt=PROBE_PROMPT, stream=False)
    except (ConnectionError, ollama.ResponseError) as exc:
        # covers a model that is listed but fails to actually load or answer
        problem = f"{model!r} is pulled but did not respond: {exc}"
        # reports that mismatch explicitly, rather than treating "listed" as "working"
        return GenerationStatus(ready=False, model=model, problem=problem)
    elapsed = time.perf_counter() - started
    # measures how long the real model actually took to answer

    text = (reply.response or "").strip()
    # reads the generated text, treating a missing field the same as an empty one
    if not text:
        # covers a model that responded but produced nothing
        problem = f"{model!r} returned an empty response"
        # reports that as not ready, rather than silently accepting a blank answer
        return GenerationStatus(ready=False, model=model, problem=problem)

    return GenerationStatus(ready=True, model=model, response_seconds=elapsed, sample_reply=text)
    # reports success, together with the real measured latency and the real reply


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m src.generation.setup -- exit 0 if the local LLM is ready, 1 if not."""
    parser = argparse.ArgumentParser(description="Check the local Ollama generation model is ready")
    # builds this CLI's argument parser
    # Hardcoded, like src/embedding/embedder.py and src/embedding/lock.py's own CLIs --
    # settings.ollama_url defaults to "http://ollama:11434" (the Docker Compose service
    # name), which only resolves from inside another container. Unlike NEO4J_URI, .env
    # has no OLLAMA_URL override for a host-run script, so the CLI supplies one directly.
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default=None, help="defaults to LLM_MODEL from .env")
    args = parser.parse_args(argv)

    ollama_url = args.ollama_url
    # uses the flag's own host-reachable default unless the caller overrode it
    model = args.model or settings.llm_model
    # picks the model name the caller gave, or the configured default
    client = build_client(ollama_url)
    # builds one real client for this one check
    status = health_check(client, model)
    # runs the actual readiness check

    if status.ready:
        print(
            f"ready: {status.model} at {ollama_url} answered in {status.response_seconds:.1f}s "
            f'-- sample reply: "{status.sample_reply}"'
        )
        # prints a one-line, human-readable confirmation, including real measured timing
        return 0
        # signals success to the shell
    print(f"NOT ready: {status.problem}")
    # prints exactly what went wrong
    return 1
    # signals failure to the shell


if __name__ == "__main__":
    sys.exit(main())
