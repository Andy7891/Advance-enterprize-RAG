# Phase 6, Microstep 5 — Implement Context Window Management

**Date:** 2026-09-30
**Core learning: Yes — token budgeting.** This step turns a fixed, hand-derived approximation
(P6-M4's `CONTEXT_BUDGET_TOKENS = 2000`) into a real computation, measured fresh from the actual
prompt and answer-length of every single call, rather than assumed once and hard-coded.

## The gap this step closes
`apply_budget` (P6-M4) already dropped chunks that didn't fit — but against a FIXED number, 2000,
worked out by hand once by assuming a 386-token system prompt and a 400-token answer. Those
assumptions don't hold for every question (a longer question costs more tokens) or every caller
(a different `max_tokens` changes how much room is really left). This step asks: given the ACTUAL
prompt and the ACTUAL `max_tokens` for this specific call, how many chunks really fit?

## What was built
- **`fit_context(chunks: Sequence[DocumentChunk], prompt: str, max_tokens: int) ->
  list[DocumentChunk]`** ([src/orchestration/prompts.py](../src/orchestration/prompts.py)) — the
  task's own literal deliverable. Computes `budget = CONTEXT_WINDOW_TOKENS (4096) -
  count_tokens_approx(prompt) - max_tokens`, then the same greedy best-fit walk P6-M4 designed.
- **`apply_budget`** refactored to compute its own budget the same dynamic way, sharing a new
  private `_greedy_best_fit` helper with `fit_context` instead of duplicating the algorithm for
  `RankedChunk` vs. `DocumentChunk`.
- **`RAGPipeline._prepare`** ([src/orchestration/rag.py](../src/orchestration/rag.py)) now builds
  the `prompt` passed to `apply_budget` as `SYSTEM_PROMPT + "\n\n" + build_user_prompt(question,
  [])` — the real system prompt and the real question, sources section left empty — reusing the
  existing template builder rather than writing a new "shell" function.
- `CONTEXT_BUDGET_TOKENS = 2000` removed entirely (obsolete once the number is computed, not
  assumed). `CONTEXT_WINDOW_TOKENS = 4096` added as the one fixed constant that remains — the
  real, measured served context limit, a property of the deployed model, not of any one call.
- Tests: `tests/orchestration/test_prompts.py` gained `fit_context` tests and a new test proving
  the SAME chunks survive differently depending purely on the real prompt's measured length;
  `tests/orchestration/test_rag.py`'s budget-integration test was re-numbered against the new,
  larger real default budget (~3295 tokens, not 2000). 96 hermetic tests total across
  `tests/orchestration/`; live tests re-run against the real corpus, Neo4j, Qdrant, and Ollama.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 What `max_tokens` means | The reserved answer length; `CONTEXT_WINDOW_TOKENS=4096` is a module constant | `max_tokens` = the context limit itself, no answer room reserved; or a 4th explicit parameter | matches this project's universal `max_tokens` vocabulary everywhere else; the 4096 figure doesn't vary per call today |
| D2 Chunk type | Plain `DocumentChunk`, matching the task's literal signature | `RankedChunk`, matching `apply_budget`'s existing shape | none realistic — `fit_context` needs no ranking metadata at all |
| D3 Refactor `apply_budget`? | Yes — shares `_greedy_best_fit` with `fit_context`, computes its budget the same dynamic way | Leave `apply_budget`/`RAGPipeline` untouched, ship `fit_context` standalone | removes a known approximation; the two functions would otherwise duplicate the same algorithm forever |
| D4 Location | `src/orchestration/prompts.py`, alongside `apply_budget` | A new `src/orchestration/context.py` | none realistic — same concern, same file, now literally shared code |
| D5 Token counting | Keep `count_tokens_approx` (chars/4) | Switch to a real tokenizer for this function specifically | matches `DocumentChunk.approx_token_count`'s own already-established method; this project's own repeatedly-reconfirmed decision |

## Verified, not assumed
```
SYSTEM_PROMPT real token count         : 386 (count_tokens_approx, matches docs/prompts.md)
shell (system + real question, empty
  Sources: section) real token count   : 398-402, depending on the question's own length
real available budget at RAGPipeline's
  defaults (max_tokens=400)            : ~3294-3298 -- NOT the old fixed 2000
fit_context/apply_budget agreement     : identical keep/skip/recover result on the same real
                                          measured percentiles (1265, 909, 613) at budget=2000,
                                          proving the shared _greedy_best_fit is genuinely shared
mypy                                    : clean across src/ (37 files)
full test suite                         : 694 non-live passed; live RAGPipeline tests blocked by
                                          a host-level Ollama stall, not a code issue -- see
                                          Finding 4
```

## Findings
1. **Generalizing a fixed constant into a real computation quietly removed a safety margin nobody
   re-derived.** The old `CONTEXT_BUDGET_TOKENS = 2000` was deliberately conservative — ~1260
   tokens of margin below the theoretical ~3260 available, reserved specifically because
   `approx_token_count` can underestimate real tokenization. The new dynamic computation (measured:
   ~3295 tokens available at defaults) uses the full theoretical figure with ZERO such cushion. This
   wasn't one of the decisions presented before implementation, so it's disclosed as its own finding
   (`decision.md` D-2026-09-30-06) rather than quietly re-adding an undiscussed margin.
2. **Reusing `build_user_prompt(question, [])` avoided writing a new "shell" function.**
   `RAGPipeline` needed to measure "everything in the final prompt except the chunks" BEFORE it
   knows which chunks survive — a chicken-and-egg problem. Calling the real, existing template
   builder with an empty source list produces exactly that shell for free, and it can never drift
   out of sync with the real template, since it literally IS the real template.
3. **The task's own three listed inputs (chunks, LLM context limit, prompt) didn't map cleanly
   onto its own three-parameter signature** (`chunks`, `prompt`, `max_tokens` — no separate
   `context_limit` parameter). Resolved by reading `max_tokens` as this project's own established
   meaning (the answer's cap) and treating the context limit as a fixed, already-measured constant
   rather than a per-call input — a genuine ambiguity, flagged and resolved as D1 before any code
   was written, not silently assumed.
4. **Live verification of the `RAGPipeline` integration was blocked by a real, isolated
   infrastructure stall, not a code defect.** After the refactor, `test_live_query_...` and
   `test_live_stream_...` both timed out at exactly 120.0s (`PROBE_TIMEOUT_SECONDS`). Restarting
   the Ollama container did not fix it. Isolated it by calling `LocalLLM.generate` directly, with
   no `RAGPipeline`/retrieval involved at all — even a trivial `max_tokens=10` factual question
   timed out identically, and `docker top` showed a single `ollama runner` process pegged at 99%
   CPU for 38+ minutes, never completing. This is the same class of WSL2 resource constraint
   already disclosed in `notes/step-6.1-ollama-llm-setup.md` (a ~7.6GB VM budget, previously seen
   crashing Neo4j/Qdrant under load) manifesting as a stuck generation runner instead this time.
   Not fixed or worked around here — retried twice, isolated to rule out this step's own code, and
   disclosed rather than chased further. The 96 hermetic tests (including exact arithmetic checks
   against the real, measured `SYSTEM_PROMPT`/shell token counts) are the evidence this step's own
   logic is correct; re-running the live suite once the host is quiet again is the way to confirm

   **Correction (P6-M6, same day):** "stuck generation runner" was the wrong diagnosis. A direct
   call with a 300s timeout completed in 108.3s — the process was never hung, it was genuinely
   computing the whole time (its CPU TIME exceeded the wall-clock time elapsed, meaning multiple
   cores were saturated continuously). The real cause: `PROBE_TIMEOUT_SECONDS` (120s,
   `src/generation/setup.py`) was sized for a tiny one-word readiness reply and reused by
   `build_llm` for real ~400-token generation calls, leaving almost no margin on this host's real
   CPU-only throughput. Fixed in P6-M6 with a separate `GENERATION_TIMEOUT_SECONDS = 300.0` in
   `src/orchestration/llm.py` — see `decision.md` D-2026-09-30-11 and
   `notes/step-6.6-rag-integration-tests.md`. Restarting Docker Desktop (tried twice) never fixed
   this, which in hindsight was itself a clue the diagnosis was wrong — a genuinely stuck process
   would have been cleared by a restart; a throughput ceiling would not, and wasn't.
   the full integration, not something this session could force to happen on demand.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Static approximation vs. dynamic computation:** the same underlying question ("how many
  chunks fit?") answered two different ways across two microsteps — a number worked out once by
  hand, versus one measured fresh from real inputs every call. The second is strictly more
  correct, but isn't automatically safer (see Finding 1).
- 🔴 **Extracting a shared algorithm behind two differently-typed public functions:** `_greedy_best_fit`
  operates on a generic `Sequence[_T]` + a parallel list of token counts, letting `fit_context`
  (`DocumentChunk`) and `apply_budget` (`RankedChunk`) share one tested implementation without
  either knowing about the other's type.
- 🟡 **Reusing an existing function for a partial measurement:** `build_user_prompt(question, [])`
  as a "shell" is a small, generalizable trick — call the real thing with a trivial/empty input to
  measure a PART of what it would produce, instead of writing a second, parallel builder.
- ⚪ The exact 4096/398-402/3295 numbers themselves — specific measurements, not concepts.

## Honest limitations
1. **No safety margin in the new computation** (Finding 1) — a real risk if `approx_token_count`'s
   known estimation error is ever large enough to cause an actual overflow; not yet observed, and
   deliberately not pre-emptively fixed without a decision gate (D-2026-09-30-06).
2. **The "shell" (`SYSTEM_PROMPT` + empty-sources user prompt) is a close but not perfect measure**
   of the true non-chunk prompt cost — the literal string `"Sources:\n\n\n"` differs by a few
   characters from what the real prompt will contain once real sources are numbered in, though the
   difference is negligible (a handful of tokens at most).
3. **Still no real tokenizer** — `count_tokens_approx`'s chars/4 estimate is unchanged, carried
   forward from Phase 2, with the same long-standing, repeatedly-reconfirmed flip condition.
4. **The live `RAGPipeline` integration (real retrieval + real generation, end to end) is not
   currently re-verified** — blocked by the host-level Ollama stall in Finding 4, isolated to rule
   out this step's own code but not itself resolved. The pure budgeting logic is thoroughly
   verified (96 hermetic tests); the full pipeline's live behavior should be re-checked once the
   host's Ollama runner is behaving normally again.

## How this connects
**Previous:** P6-M4 built `RAGPipeline` around a fixed, approximate budget. **This step:** the
budget becomes a real, per-call computation, with the exact same greedy algorithm now serving two
call sites instead of one. **Next (not yet specified):** likely an evaluation harness scoring real
end-to-end answers (already named as an honest limitation in P6-M4's own notes), or revisiting the
disclosed margin/few-shot risks once real usage data exists to inform a fix.

## How to do this yourself
1. When a task's own inputs list doesn't map cleanly onto its own given function signature, name
   the mismatch explicitly and pick a resolution consistent with the rest of the codebase's
   existing vocabulary — don't silently guess and hope it's right.
2. When generalizing a fixed constant into a computed value, explicitly ask what implicit
   assumptions or safety margins the constant was carrying, and decide on purpose whether to keep,
   drop, or resize them — don't let a margin silently disappear as a side effect.
3. Before writing a new helper function to measure "part of" something a real function already
   builds, check whether calling the real function with a trivial/empty input already gives you
   that measurement for free.
4. When two functions need the same algorithm over two different types, extract a small,
   type-generic private helper rather than duplicating the loop — but keep the PUBLIC functions
   distinctly typed and named for their own callers.

## Practice exercises
1. By hand, compute `count_tokens_approx("Sources:\n\n\nQuestion: What is Carllm?\n\nAnswer:")`.
   Then compute the real available budget at `max_tokens=300` using `CONTEXT_WINDOW_TOKENS=386`
   for a hypothetical system prompt of that size. Check your arithmetic against `fit_context`'s
   real behavior on a matching scenario.
2. Modify `test_apply_budget_shrinks_as_the_real_prompt_grows` (in
   `tests/orchestration/test_prompts.py`) to use THREE chunks instead of two, choosing sizes so
   that the long prompt drops two chunks instead of one. Trace your expected result by hand first.
3. Add a `margin: int = 0` parameter to `fit_context`, subtracted from the computed budget before
   the greedy walk. Re-run `test_query_applies_the_context_budget_before_building_the_prompt`
   with a large margin — does it still pass? What would need to change if it didn't?
4. Using `_greedy_best_fit` directly (not `fit_context` or `apply_budget`), construct a scenario
   with plain strings instead of chunks (e.g., `items=["a", "b", "c"]`, `token_counts=[10, 5, 3]`)
   and confirm the shared algorithm works identically regardless of item type.
5. Investigate Finding 3 further: read `docs/prompts.md` section 4's own arithmetic once more and
   write, in one paragraph, whether you think a 4th `context_limit` parameter (D1's rejected
   option) would have been a clearer design than treating 4096 as an internal constant. Argue the
   case you find LESS convincing, on purpose.

## Sources
- `docs/prompts.md` [section 1](../docs/prompts.md) — the real, measured 4096-token context window
  this whole step's arithmetic is built on.
- `docs/prompts.md` [section 4](../docs/prompts.md#4-context-token-budget-measured-not-guessed) —
  the fixed 2000-token approximation this step replaces with a real computation.
- [notes/step-6.4-rag-pipeline.md](step-6.4-rag-pipeline.md) — the step this one directly builds on
  and partially supersedes.
- [tests/orchestration/test_prompts.py](../tests/orchestration/test_prompts.py) — the real,
  hermetic tests proving the dynamic behavior, including the same-chunks-different-prompt case.
