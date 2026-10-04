"""Turns a retrieved answer into the two prompt strings LocalLLM needs: the system prompt
(the rules docs/prompts.md section 2 designed) and the user prompt (the numbered sources and
question from section 3) -- plus the real, dynamically-computed context-window budget (P6-M5,
generalizing section 4's own fixed 2000-token approximation) and the mechanical citation
fallback from section 5, all as real, tested code.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from src.ingestion.schemas import DocumentChunk, count_tokens_approx
from src.retrieval.pipeline import RankedChunk

CONTEXT_WINDOW_TOKENS = 4096
# Ollama's real, measured served context window (docs/prompts.md section 1) -- a fixed
# property of the deployed model, not something that varies per call

_T = TypeVar("_T")
# the item type _greedy_best_fit keeps -- DocumentChunk for fit_context, RankedChunk for
# apply_budget, so the one algorithm below serves both public, differently-typed functions

REFUSAL_TEXT = "I don't have enough information in the provided documents to answer that."
# Rule 1's exact refusal string, single-sourced here so the system prompt below and
# RAGPipeline's empty-retrieval short-circuit can never drift apart from each other

SYSTEM_PROMPT = f"""You are a factual question-answering assistant for Insurellm, an insurance technology company. You answer questions using ONLY the numbered source excerpts provided in the user message below. You do not use any knowledge you have from training.

Rules:
1. Answer using ONLY the information in the numbered sources. If the sources do not contain enough information to answer, respond exactly: "{REFUSAL_TEXT}" Do not guess or use outside knowledge.
2. After any sentence or claim drawn from a source, cite it with the source's number in brackets, like [1]. If a claim draws on more than one source, cite all of them, like [1][3].
3. If two or more sources disagree with each other, say so explicitly (for example: "Source [1] states X, while source [2] states Y") instead of silently choosing one.
4. Be concise. Answer in plain prose, in as few sentences as the question requires. Do not repeat the question, and do not add disclaimers or summaries that were not asked for.

Example:
Sources:
[1] Company Handbook, Section 2 (company/handbook.md): Employees receive 15 days of paid vacation per year.
[2] HR Policy Update 2024 (company/hr_policy_2024.md): Effective 2024, vacation increased to 20 days per year for all staff.

Question: How many vacation days do employees get?

Answer: According to the most recent policy, employees receive 20 days of paid vacation per year [2]. An earlier document states 15 days [1]; the sources disagree, and [2] appears to be the more recent figure."""
# verbatim from docs/prompts.md section 2, unchanged -- including the known "picks a winner"
# risk measured in docs/31 and disclosed in decision.md D-2026-09-29-25 (D3(a): use as
# published, backstopped by the mechanical citation fallback below, not fixed here)


def _greedy_best_fit(items: Sequence[_T], token_counts: Sequence[int], budget: int) -> list[_T]:
    """The best-to-worst items of `items` whose matching `token_counts` fit `budget`; always
    keeps the first item even if it alone exceeds the budget, so a caller never gets zero back.

    Shared by `fit_context` and `apply_budget` -- P6-M5 generalized this from a single, fixed
    `budget` int (the old `CONTEXT_BUDGET_TOKENS = 2000`, docs/prompts.md section 4's own
    hand-computed approximation) to a budget computed fresh from the REAL prompt and max_tokens
    of each call, so this is the one place the actual keep/skip/recover logic still lives."""
    kept: list[_T] = []
    # will hold every item that survives the budget, in its original best-first order
    total = 0
    # the running total of approx-tokens committed so far
    for item, tokens in zip(items, token_counts):
        # walks the ranked list from best to worst, exactly as docs/prompts.md section 4 does
        if total + tokens <= budget or not kept:
            # fits within budget, OR this is the very first item -- never keep zero sources
            kept.append(item)
            # keeps this item
            total += tokens
            # adds its size to the running total
        # else: this one item doesn't fit right now -- it is skipped, but the loop keeps
        # checking the rest of the ranked list, since a smaller, lower-ranked item might fit
    return kept
    # hands back every item that survived, still in best-first order


def fit_context(
    chunks: Sequence[DocumentChunk], prompt: str, max_tokens: int
) -> list[DocumentChunk]:
    """The best-ranked chunks of `chunks` (already ordered best first) that still leave room,
    inside the real 4096-token context window, for `prompt` and up to `max_tokens` of answer."""
    budget = CONTEXT_WINDOW_TOKENS - count_tokens_approx(prompt) - max_tokens
    # everything left over once the fixed, non-chunk parts of the call are accounted for
    return _greedy_best_fit(list(chunks), [c.approx_token_count for c in chunks], budget)
    # the P6-M5 deliverable: DocumentChunk in, DocumentChunk out, no ranking fields needed


def apply_budget(chunks: Sequence[RankedChunk], prompt: str, max_tokens: int) -> list[RankedChunk]:
    """`RankedChunk`'s own version of `fit_context` -- the best-ranked chunks of `chunks` that
    fit alongside `prompt` and `max_tokens` of answer inside the real 4096-token context window."""
    budget = CONTEXT_WINDOW_TOKENS - count_tokens_approx(prompt) - max_tokens
    # everything left over once the fixed, non-chunk parts of the call are accounted for
    return _greedy_best_fit(list(chunks), [c.chunk.approx_token_count for c in chunks], budget)
    # RAGPipeline's own shape (RankedChunk), computed the exact same way as fit_context


def numbered_sources(chunks: Sequence[RankedChunk]) -> list[tuple[int, RankedChunk]]:
    """Assigns 1-based citation numbers to `chunks` by POSITION in the given order."""
    return list(enumerate(chunks, start=1))
    # numbers by position in the surviving list, never by original final_rank -- so a chunk
    # dropped by the budget never leaves a gap like [1] and [3] with no [2] in between


def build_user_prompt(question: str, chunks: Sequence[RankedChunk]) -> str:
    """The user prompt template from docs/prompts.md section 3: numbered sources, then the
    question, then "Answer:" as the final token before generation begins."""
    sources_text = "\n\n".join(
        f"[{i}] {chunk.chunk.title} ({chunk.chunk.source_path}):\n{chunk.chunk.text}"
        for i, chunk in numbered_sources(chunks)
    )
    # builds one numbered entry per surviving chunk, in the exact format section 3 specifies
    return f"Sources:\n{sources_text}\n\nQuestion: {question}\n\nAnswer:"
    # matches section 3's template exactly: sources, then question, then "Answer:" last


def mechanical_source_list(chunks: Sequence[RankedChunk]) -> str:
    """The mechanical half of the hybrid citation strategy (section 5): a reliable source
    list built from data, appended regardless of the model's own inline citations."""
    lines = [
        f"[{i}] {chunk.chunk.title} ({chunk.chunk.source_path})"
        for i, chunk in numbered_sources(chunks)
    ]
    # the same numbering as build_user_prompt, so citation numbers always match this list
    return "Sources:\n" + "\n".join(lines)
    # a plain, data-driven list -- correct regardless of how the model actually answered
