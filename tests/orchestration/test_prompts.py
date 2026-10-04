"""Tests for src/orchestration/prompts.py: the budget algorithm, the numbered-source-list
building, and the two published constants (SYSTEM_PROMPT, REFUSAL_TEXT), all previously only
a design in docs/prompts.md and now real, tested code."""

from __future__ import annotations

from src.ingestion.schemas import DocumentChunk
from src.orchestration.prompts import (
    CONTEXT_WINDOW_TOKENS,
    REFUSAL_TEXT,
    SYSTEM_PROMPT,
    apply_budget,
    build_user_prompt,
    fit_context,
    mechanical_source_list,
    numbered_sources,
)
from src.retrieval.pipeline import RankedChunk


def chunk(name: str, tokens: int, text: str | None = None) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=f"{name}_0",
        doc_id=f"doc-{name}",
        source_path=f"{name}.md",
        category="test",
        title=name,
        chunk_index=0,
        text=text if text is not None else f"text of {name}",
        char_count=10,
        approx_token_count=tokens,
    )
    # a minimal, directly-constructed chunk with a controllable token count


def ranked(name: str, tokens: int, rank: int, text: str | None = None) -> RankedChunk:
    return RankedChunk(
        chunk=chunk(name, tokens, text),
        final_rank=rank,
        relevance_score=1.0,
        hybrid_score=1.0,
        hybrid_rank=rank,
        vector_rank=rank,
        graph_rank=None,
        vector_score=1.0,
    )
    # a minimal, directly-constructed RankedChunk for controlled budget/prompt tests


# --- apply_budget / fit_context (the shared, now-dynamic budget algorithm, P6-M5) -----------


def test_context_window_tokens_is_the_real_measured_limit():
    assert CONTEXT_WINDOW_TOKENS == 4096
    # pins the published constant itself (docs/prompts.md section 1), so a silent change
    # fails loudly


def test_apply_budget_keeps_everything_that_fits():
    chunks = [ranked("a", 100, 1), ranked("b", 200, 2)]
    # two small chunks, well under a 1000-token budget
    assert apply_budget(chunks, prompt="", max_tokens=3095) == chunks
    # budget = 4096 - count_tokens_approx("") - 3095 = 4096 - 1 - 3095 = 1000


def test_apply_budget_skips_a_chunk_that_does_not_fit_but_keeps_a_smaller_one_behind_it():
    huge = ranked("huge", 1265, 1)
    mid = ranked("mid", 909, 2)
    small = ranked("small", 613, 3)
    # the real measured percentiles from docs/prompts.md section 4
    survivors = apply_budget([huge, mid, small], prompt="", max_tokens=2095)
    # budget = 4096 - 1 - 2095 = 2000, the same figure docs/prompts.md section 4 worked out by hand
    assert survivors == [huge, small]
    # requires "mid" to be skipped (1265+909=2174 > 2000) but "small" recovered behind it
    # (1265+613=1878 <= 2000) -- the exact result docs/prompts.md section 4 reports


def test_apply_budget_always_keeps_the_first_chunk_even_if_it_alone_exceeds_the_budget():
    oversized = ranked("oversized", 5000, 1)
    # a single chunk far bigger than the entire context window
    assert apply_budget([oversized], prompt="", max_tokens=2095) == [oversized]
    # requires it to be kept anyway -- a caller must never get zero sources


def test_apply_budget_shrinks_as_the_real_prompt_grows():
    chunks = [ranked("a", 3000, 1), ranked("b", 900, 2)]
    # together, 3900 tokens -- P6-M5's whole point: the SAME chunks, a DIFFERENT real prompt
    short_prompt_survivors = apply_budget(chunks, prompt="hi", max_tokens=100)
    # budget = 4096 - count_tokens_approx("hi")=1 - 100 = 3995 -- both fit (3000+900=3900)
    assert short_prompt_survivors == chunks
    long_prompt = "x" * 4000
    # count_tokens_approx(long_prompt) = 4000 // 4 = 1000 -- a genuinely bigger real prompt
    long_prompt_survivors = apply_budget(chunks, prompt=long_prompt, max_tokens=100)
    # budget = 4096 - 1000 - 100 = 2996 -- "b" no longer fits behind "a" (3000+900=3900 > 2996)
    assert long_prompt_survivors == [chunks[0]]
    # proves the SAME chunks get a DIFFERENT result purely because the measured prompt grew --
    # the fixed CONTEXT_BUDGET_TOKENS=2000 this replaced could never have shown this


# --- fit_context (DocumentChunk-in, DocumentChunk-out version of the same algorithm) --------


def test_fit_context_matches_apply_budgets_own_keep_skip_recover_behavior():
    huge = chunk("huge", 1265)
    mid = chunk("mid", 909)
    small = chunk("small", 613)
    # the exact same real measured percentiles, as plain DocumentChunks this time
    survivors = fit_context([huge, mid, small], prompt="", max_tokens=2095)
    # budget = 4096 - 1 - 2095 = 2000, identical to apply_budget's own equivalent test
    assert survivors == [huge, small]
    # requires the exact same keep/skip/recover behavior fit_context and apply_budget share


def test_fit_context_always_keeps_the_first_chunk_even_if_it_alone_exceeds_the_budget():
    oversized = chunk("oversized", 5000)
    assert fit_context([oversized], prompt="", max_tokens=2095) == [oversized]
    # requires it to be kept anyway -- a caller must never get zero sources


def test_fit_context_returns_plain_document_chunks_not_ranked_chunks():
    a = chunk("a", 10)
    result = fit_context([a], prompt="", max_tokens=100)
    assert result == [a]
    assert isinstance(result[0], DocumentChunk)
    # requires the P6-M5 deliverable's own literal type: DocumentChunk in, DocumentChunk out


# --- numbered_sources / build_user_prompt / mechanical_source_list -------------------------


def test_numbered_sources_numbers_by_position_not_by_final_rank():
    # simulates a budget that already dropped the original rank-2 chunk
    survivors = [ranked("first", 10, rank=1), ranked("third", 10, rank=3)]
    numbered = numbered_sources(survivors)
    assert [n for n, _ in numbered] == [1, 2]
    # requires 1, 2 -- never 1, 3 -- so citation numbers the model sees are always contiguous


def test_build_user_prompt_matches_the_published_template():
    chunks = [ranked("Office Policy", 10, 1, text="The office is closed on holidays.")]
    prompt = build_user_prompt("When is the office closed?", chunks)
    assert prompt == (
        "Sources:\n"
        "[1] Office Policy (Office Policy.md):\n"
        "The office is closed on holidays.\n\n"
        "Question: When is the office closed?\n\n"
        "Answer:"
    )
    # requires the exact section 3 layout: numbered sources, question, "Answer:" last


def test_build_user_prompt_numbers_multiple_sources_in_order():
    chunks = [ranked("a", 10, 1, text="text a"), ranked("b", 10, 2, text="text b")]
    prompt = build_user_prompt("q?", chunks)
    assert "[1] a (a.md):\ntext a" in prompt
    assert "[2] b (b.md):\ntext b" in prompt
    # requires both entries present, correctly numbered by position


def test_mechanical_source_list_omits_chunk_text():
    chunks = [ranked("a", 10, 1, text="a very long chunk body that should not reappear")]
    listing = mechanical_source_list(chunks)
    assert listing == "Sources:\n[1] a (a.md)"
    # requires only title and source_path -- never the chunk's own text -- in the mechanical list


def test_mechanical_source_list_and_build_user_prompt_number_survivors_identically():
    chunks = [ranked("a", 10, 1, text="x"), ranked("b", 10, 2, text="y")]
    prompt = build_user_prompt("q?", chunks)
    listing = mechanical_source_list(chunks)
    assert "[1] a (a.md)" in prompt and "[1] a (a.md)" in listing
    assert "[2] b (b.md)" in prompt and "[2] b (b.md)" in listing
    # requires the numbers shown to the model to always match the numbers shown to the reader


# --- SYSTEM_PROMPT / REFUSAL_TEXT -----------------------------------------------------------


def test_refusal_text_appears_verbatim_inside_the_system_prompt():
    assert f'"{REFUSAL_TEXT}"' in SYSTEM_PROMPT
    # requires the two to be single-sourced -- they can never silently drift apart


def test_system_prompt_contains_all_four_rules_and_the_worked_example():
    assert "1. Answer using ONLY" in SYSTEM_PROMPT
    assert "2. After any sentence or claim" in SYSTEM_PROMPT
    assert "3. If two or more sources disagree" in SYSTEM_PROMPT
    assert "4. Be concise." in SYSTEM_PROMPT
    assert "Example:" in SYSTEM_PROMPT and "[2] appears to be the more recent figure" in (
        SYSTEM_PROMPT
    )
    # pins the published design's exact content, so an accidental edit fails loudly
