"""Local LLM wrapper: hide which library talks to Ollama behind one small interface, so
later Phase 6 steps call `generate`/`stream` without knowing or caring what sits underneath.

Retrieval (Phases 3-5) only ever fetched text that already existed. This is the first class
in the whole project that PRODUCES new text -- every call here is a live, unpredictable
inference request to a real model, not a lookup against something already stored.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from typing import Any, Literal, Protocol, overload

import ollama

from src.core.config import settings

GENERATION_TIMEOUT_SECONDS = 300.0
# a real ~400-token RAGPipeline answer measured at 108.3s on this project's own dev host
# (CPU-only Mistral-7B-Q4, 2026-09-30) -- src/generation/setup.py's PROBE_TIMEOUT_SECONDS
# (120s) was sized for a tiny one-word readiness reply, not a real generation call, and left
# almost no margin (real live tests intermittently exceeded it); this is LocalLLM's own,
# separate, more generous bound for real generation specifically -- the probe keeps its own


class LLMClient(Protocol):
    """The part of `ollama.Client` LocalLLM uses, so tests can pass a fake."""

    @overload
    def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        options: Mapping[str, Any],
        stream: Literal[False] = False,
    ) -> ollama.GenerateResponse: ...

    # the non-streaming shape: one call in, one complete answer back

    @overload
    def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        options: Mapping[str, Any],
        stream: Literal[True],
    ) -> Iterator[ollama.GenerateResponse]: ...

    # the streaming shape: one call in, a whole sequence of fragments back


def _check_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        # rejects a value that is not a string, or is empty or only whitespace
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_temperature(temperature: float) -> None:
    if isinstance(temperature, bool) or not isinstance(temperature, int | float):
        # rejects anything that is not a plain int or float, including True and False
        raise ValueError(f"temperature must be a finite number >= 0, got {temperature!r}")
        # names the argument and the value so the caller can see what was wrong
    if not math.isfinite(temperature) or temperature < 0:
        # rejects NaN, infinity, and any negative value
        raise ValueError(f"temperature must be a finite number >= 0, got {temperature!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_max_tokens(max_tokens: int) -> None:
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        # rejects anything that is not a plain positive integer
        raise ValueError(f"max_tokens must be a positive integer, got {max_tokens!r}")
        # names the argument and the value so the caller can see what was wrong


class LocalLLM:
    """Generates text with a local Ollama model, hiding the client library underneath."""

    def __init__(self, client: LLMClient, *, model: str | None = None) -> None:
        resolved_model = settings.llm_model if model is None else model
        # reads the configured default at construction time, never at import time;
        # "is None", not "or" -- an explicitly-passed "" must still be rejected below,
        # not silently replaced by the default the way "" or settings.llm_model would
        _check_text("model", resolved_model)
        # refuses a blank model name before it is ever used
        self._client = client
        # keeps the client every generate()/stream() call is sent through
        self._model = resolved_model
        # keeps the model name every call uses

    def generate(
        self, prompt: str, temperature: float, max_tokens: int, *, system: str | None = None
    ) -> str:
        """One complete answer to `prompt`, produced by a single real call."""
        _check_text("prompt", prompt)
        # refuses an empty prompt before any call is made
        _check_temperature(temperature)
        # refuses a bad temperature before any call is made
        _check_max_tokens(max_tokens)
        # refuses a bad max_tokens before any call is made
        if system is not None:
            # covers the case where a caller actually wants a system prompt applied
            _check_text("system", system)
            # refuses a blank system string, the same standard prompt is held to

        response = self._client.generate(
            model=self._model,
            prompt=prompt,
            system=system,
            options={"temperature": temperature, "num_predict": max_tokens},
            stream=False,
        )
        # asks the model for one complete, real answer; Ollama's own template wraps system
        # and prompt as [INST] {system}\n\n{prompt}[/INST] when system is not None
        return response.response or ""
        # returns the generated text, or an empty string if that field was ever missing

    def stream(
        self, prompt: str, temperature: float, *, system: str | None = None
    ) -> Iterator[str]:
        """`prompt`'s answer, yielded one real fragment at a time as it is generated."""
        _check_text("prompt", prompt)
        # refuses an empty prompt before any call is made
        _check_temperature(temperature)
        # refuses a bad temperature before any call is made
        if system is not None:
            # covers the case where a caller actually wants a system prompt applied
            _check_text("system", system)
            # refuses a blank system string, the same standard prompt is held to

        chunks = self._client.generate(
            model=self._model,
            prompt=prompt,
            system=system,
            options={"temperature": temperature},
            stream=True,
        )
        # asks the model to start streaming its answer, one real fragment at a time
        for chunk in chunks:
            # walks every fragment Ollama sends, in the exact order it sends them
            if chunk.response:
                # skips the final fragment, which only carries done=True and no text
                yield chunk.response
                # hands this one fragment to the caller immediately, not buffered


def build_llm(ollama_url: str, *, model: str | None = None) -> LocalLLM:
    """A real LocalLLM for the given URL, timed out generously enough for a real answer."""
    client = ollama.Client(host=ollama_url, timeout=GENERATION_TIMEOUT_SECONDS)
    # NOT src.generation.setup's build_client: that one is bound to PROBE_TIMEOUT_SECONDS
    # (120s), sized for a tiny one-word readiness reply, not a real ~400-token answer
    return LocalLLM(client, model=model)  # type: ignore[arg-type]
    # ollama's own two dispatch overloads declare `system: str = ''` (not `Optional[str]`,
    # unlike their shared runtime implementation, which does accept None) and give the
    # `stream=True` overload a default -- both are the installed package's own stub
    # imprecision, not a real incompatibility: passing system=None to a real ollama.Client
    # already works correctly (tests/orchestration/test_llm.py's live tests call it that way)
