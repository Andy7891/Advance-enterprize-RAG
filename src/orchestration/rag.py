"""End-to-end RAG pipeline: retrieval, prompt construction, and generation behind one class,
so a caller asks a plain question and gets a grounded, cited answer without knowing Qdrant,
Neo4j, Cohere, or Ollama exist underneath.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Protocol

from src.orchestration.prompts import (
    REFUSAL_TEXT,
    SYSTEM_PROMPT,
    apply_budget,
    build_user_prompt,
    mechanical_source_list,
)
from src.retrieval.pipeline import RankedChunk, RetrievalResult

DEFAULT_K = 3
# how many chunks RetrievalPipeline is asked for, matching docs/prompts.md's own worked examples
DEFAULT_TEMPERATURE = 0.1
# within docs/prompts.md section 9's recommended 0.0-0.2 range for grounded, factual answers
DEFAULT_MAX_TOKENS = 400
# matches section 4's own budget arithmetic, which assumed 400 for the answer itself


class Retriever(Protocol):
    def retrieve(self, query: str, k: int) -> RetrievalResult: ...

    # anything that can return a ranked answer for a question, such as RetrievalPipeline


class LLM(Protocol):
    def generate(
        self, prompt: str, temperature: float, max_tokens: int, *, system: str | None = None
    ) -> str: ...

    # anything that can answer a prompt in one call, such as LocalLLM

    def stream(
        self, prompt: str, temperature: float, *, system: str | None = None
    ) -> Iterator[str]: ...

    # anything that can stream an answer to a prompt, such as LocalLLM


def _check_question(question: str) -> None:
    if not isinstance(question, str) or not question.strip():
        # rejects a value that is not a string, or is empty or only whitespace
        raise ValueError(f"question must be a non-empty string, got {question!r}")
        # names the argument and the value so the caller can see what was wrong


def _check_k(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        # rejects anything that is not a plain positive integer
        raise ValueError(f"k must be a positive integer, got {k!r}")
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


class RAGPipeline:
    """Answers one question end to end: retrieve, budget the context, build the prompts,
    generate -- with the mechanical citation list always appended, win or lose."""

    def __init__(
        self,
        retriever: Retriever,
        llm: LLM,
        *,
        k: int = DEFAULT_K,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        _check_k(k)
        # refuses a bad k before it is ever used
        _check_temperature(temperature)
        # refuses a bad temperature before it is ever used
        _check_max_tokens(max_tokens)
        # refuses a bad max_tokens before it is ever used
        self._retriever = retriever
        # keeps the retriever every query is sent through first
        self._llm = llm
        # keeps the LLM every prompt is sent through second
        self._k = k
        # keeps how many chunks each retrieval asks for
        self._temperature = temperature
        # keeps the temperature every generate()/stream() call uses
        self._max_tokens = max_tokens
        # keeps the max_tokens every generate() call uses (stream() has no such cap)

    def _prepare(self, question: str, k: int | None = None) -> tuple[str, list[RankedChunk]] | None:
        """Retrieves and budgets `question`'s context; None means nothing was retrieved."""
        _check_question(question)
        # refuses an empty question before any retrieval happens
        resolved_k = self._k if k is None else k
        # "is None", not "or" -- an explicitly-passed k=0 must still be rejected below,
        # not silently replaced by self._k the way "k or self._k" would (hybrid_retriever.py's
        # own documented anti-pattern, already repeated and caught twice in this project)
        _check_k(resolved_k)
        # refuses a bad per-call k before it is ever used
        result = self._retriever.retrieve(question, resolved_k)
        # asks the retriever for this question's best k chunks -- this call's own k if given,
        # otherwise the k this pipeline was constructed with
        if not result.results:
            # covers a question that matched nothing at all
            return None
            # signals the caller to short-circuit, rather than ask the model about nothing
        shell = SYSTEM_PROMPT + "\n\n" + build_user_prompt(question, [])
        # the real system prompt plus the real question, sources section left empty -- exactly
        # the non-chunk portion of the final prompt apply_budget needs to measure against (P6-M5)
        kept = apply_budget(result.results, shell, self._max_tokens)
        # drops whichever lowest-ranked chunks don't fit what's left of the real context window
        user_prompt = build_user_prompt(question, kept)
        # builds the numbered-sources-then-question prompt from what survived the budget
        return user_prompt, kept
        # hands back both the prompt and the chunks, so the caller can cite them afterward

    def query(self, question: str, *, k: int | None = None) -> str:
        """One complete, grounded answer to `question`, with sources always attached.

        `k` overrides this pipeline's own configured k for this one call, if given."""
        prepared = self._prepare(question, k)
        # runs retrieval and budgeting once, shared by both outcomes below
        if prepared is None:
            # covers a question retrieval found nothing at all for
            return REFUSAL_TEXT
            # answers honestly without ever calling the model on an empty context
        user_prompt, kept = prepared
        # unpacks the prompt and the chunks it was built from
        answer = self._llm.generate(
            user_prompt, self._temperature, self._max_tokens, system=SYSTEM_PROMPT
        )
        # asks the model for one real, complete answer, grounded by the system prompt's rules
        return answer.strip() + "\n\n" + mechanical_source_list(kept)
        # appends the reliable source list regardless of the model's own inline citations

    def stream(self, question: str, *, k: int | None = None) -> Iterator[str]:
        """`question`'s answer, yielded one real fragment at a time, sources appended last.

        `k` overrides this pipeline's own configured k for this one call, if given."""
        prepared = self._prepare(question, k)
        # runs retrieval and budgeting once, shared by both outcomes below
        if prepared is None:
            # covers a question retrieval found nothing at all for
            yield REFUSAL_TEXT
            # answers honestly without ever calling the model on an empty context
            return
            # ends the generator immediately -- there is nothing else to yield
        user_prompt, kept = prepared
        # unpacks the prompt and the chunks it was built from
        for fragment in self._llm.stream(user_prompt, self._temperature, system=SYSTEM_PROMPT):
            # walks every real fragment the model streams back, in order
            yield fragment
            # hands this one fragment to the caller immediately, not buffered
        yield "\n\n" + mechanical_source_list(kept)
        # appends the reliable source list as one final chunk, once streaming is done
