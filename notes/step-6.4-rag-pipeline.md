# Phase 6, Microstep 4 — Implement RAG Pipeline

**Date:** 2026-09-29
**Core learning: Yes — orchestration.** This is the step where Phases 3-6 stop being separate,
individually-tested pieces and become one thing a caller can actually use: a question in, a
grounded, cited answer out.

## The gap this step closes
`RetrievalPipeline.retrieve()` (P5) returns ranked chunks. `LocalLLM.generate()`/`.stream()`
(P6-M2) can call the model. `docs/prompts.md` (P6-M3) designed the exact strings to put between
them. Nothing connected all three — asking a real question required a caller to do the
retrieval, the budgeting, the prompt-building, and the generation call themselves, by hand, every
time. This step is that connection, and nothing else.

## What was built
- **[`src/orchestration/prompts.py`](../src/orchestration/prompts.py)** (new): `SYSTEM_PROMPT` and
  `REFUSAL_TEXT` verbatim from `docs/prompts.md` §2 (single-sourced, so they can never drift
  apart), `apply_budget` (the real §4 greedy best-fit algorithm), `numbered_sources` /
  `build_user_prompt` (§3's template) / `mechanical_source_list` (§5's citation fallback) — the
  design turned into real, independently-tested code for the first time.
- **[`src/orchestration/rag.py`](../src/orchestration/rag.py)** (new): `RAGPipeline` with
  `Retriever`/`LLM` Protocols (so tests can pass fakes, matching every prior retrieval-layer
  class), `query(question) -> str`, `stream(question) -> Iterator[str]`.
- **[`src/orchestration/llm.py`](../src/orchestration/llm.py)** (changed): `generate`/`.stream`
  gained the `system: str | None = None` parameter `docs/prompts.md` §6 pre-specified — the one
  piece P6-M3 deliberately left unbuilt.
- 32 hermetic tests (`tests/orchestration/test_prompts.py`, `tests/orchestration/test_rag.py`) + 2
  live tests (real Qdrant/Neo4j/Ollama, a fake reranker so no Cohere key is needed) + 6 new tests
  in `test_llm.py` for the `system` parameter, including one live test proving `system` measurably
  changes real output (a French-language system prompt produced a French reply).

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Prompt logic location | New `src/orchestration/prompts.py` module | Inline inside `RAGPipeline` | none realistic — matches this project's one-module-per-responsibility pattern throughout |
| D2 `system` support | Build the pre-specified `LocalLLM` parameter now | Keep the D1b string-concatenation fallback | couples to Mistral's measured template; unverified for a different model's serving layer |
| D3 The disclosed few-shot bias | Ship the published worked example as-is, backstopped by the mechanical fallback | Revise the example now, as part of this task | a real query hitting an unresolvable disagreement can get a confidently wrong, unflagged answer — mitigated, not eliminated |
| D4 Empty retrieval | Short-circuit to the exact refusal text, no model call | Always call the model, trust Rule 1 | none realistic for the common case; the alternative depends on compliance already measured (docs/31) to be imperfect |
| D5 Testing | Hermetic (fake Retriever/LLM) + a small no-Cohere-key live fixture | Hermetic-only | none realistic — the sixth time live testing has been kept in this project, and it found a real bug again |
| D6 Errors | Propagate unchanged from `query`/`stream` | Catch and return a fallback string | none realistic — the seventh consecutive time this exact choice has been made project-wide |

## Verified, not assumed
```
apply_budget          : re-verified against the real measured percentiles (1265, 909, 613, 204) --
                         kept [1265, 613], total 1878/2000, identical to docs/prompts.md section 4
system parameter       : live call with system="Answer only in French" produced a real French
                         reply -- proof it reaches and changes the real model, not just accepted
live pipeline          : real HybridRetriever + real GraphRetriever + real Ollama answered "Who
                         founded Insurellm?" correctly, with the mechanical Sources: list attached,
                         via both query() and stream()
mypy                   : clean across src/ (37 files) after one documented type: ignore for a real
                         upstream stub imprecision in the installed `ollama` package (see Findings)
```

## Findings
1. **A real `system` parameter, once wired through, measurably changes real model output.** Not
   just "accepted without a type error" — a live test asked a real question with
   `system="Answer only in French, in one short sentence."` and got back an actual French reply,
   the first live proof this project's whole system/user design (P6-M3) does what it claims.
2. **The installed `ollama` package's own overload stubs are internally inconsistent.** Its two
   `@overload` dispatch signatures for `Client.generate` declare `system: str = ''` (not
   `Optional`), while the shared runtime implementation actually used declares
   `system: Optional[str] = None`. mypy caught this the moment `LLMClient`'s Protocol (correctly
   typed `str | None`) was checked against the real class. Fixed with one documented
   `# type: ignore[arg-type]` at the single call site (`build_llm`), rather than weakening the
   Protocol's own, correct typing.
3. **Live testing found a real bug in a different, already-shipped module — the sixth time this
   exact pattern has repeated in this project.** Running `RAGPipeline`'s live tests with
   Qdrant+Neo4j+Ollama all up together produced an intermittent `httpx.ReadTimeout`. Tracing it
   showed `src/embedding/embedder.py`'s `build_ollama_client` has no timeout at all, unlike
   `src/generation/setup.py`'s deliberate 120-second bound. A retry with the CPU idle passed
   cleanly, confirming this is a real, reproducible gap under genuine concurrent load, not a bug in
   the new pipeline code. Flagged as a separate follow-up task, not fixed inline — mirroring how
   P6-M2 flagged P6-M1's own frozen-default bug rather than scope-creeping into it.
4. **This step is the first time the disclosed few-shot risk (decision.md D-2026-09-29-25) ships
   in code that will actually be used, not just a design document.** Shipped as-is deliberately
   (D3 above); the mechanical citation fallback exists specifically so no part of this pipeline's
   correctness depends on the model's own disagreement-flagging being reliable.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Orchestration / composition:** `RAGPipeline` does no retrieval or generation work itself —
  it is the one place that knows both `Retriever` and `LLM` exist, and nothing else needs to.
- 🔴 **The mechanical citation fallback, now real code:** `mechanical_source_list` is appended to
  every answer, win or lose, so citation reliability never depends entirely on model compliance —
  the exact insurance policy this project's whole citation strategy (D-2026-09-29-19) was built on.
- 🔴 **Grounding via short-circuit:** the empty-retrieval case is answered with certainty by code,
  not by hoping the model follows Rule 1 — the strongest possible form of "grounded."
- 🟡 **The greedy best-fit budget, now real and tested:** previously only traced by hand against
  measured percentiles (P6-M3); now a real function with real hermetic tests proving the
  skip-and-continue behavior.
- 🟡 **Ollama's native `system` field, now really wired through the whole stack:** from
  `docs/prompts.md`'s measured discovery, to a specified-but-unbuilt parameter, to real code, to a
  live-verified behavioral change — one idea traced across three microsteps.
- ⚪ The validation helpers (`_check_k`, `_check_question`, etc.) — boilerplate, mirror existing
  patterns in `pipeline.py` and `llm.py` exactly.

## Honest limitations
1. **The few-shot "picks a winner" bias is now live in code that will actually be used**, not just
   a design-document risk. A real query hitting a genuinely unresolvable disagreement could get a
   confidently wrong, unflagged answer from the model's own text — mitigated by the mechanical
   fallback (a reader always sees the real source list), not eliminated.
2. **No evaluation harness yet scores `RAGPipeline`'s real end-to-end answers** against this
   project's five labelled acceptance questions systematically — only two were spot-checked live,
   informally, inside this step's own tests. A natural next-step candidate.
3. **`system`'s real behavior has only been verified against Mistral specifically** — a different
   model's serving layer might not honor it the same way (already named in D-2026-09-29-27's flip
   condition).
4. **Live testing surfaced, but did not fix, a real timeout bug in a sibling module**
   (`src/embedding/embedder.py`) — flagged separately, not blocking this step's own correctness.

## How this connects
**Previous:** P6-M1 made a model reachable; P6-M2 made it callable through a stable interface;
P6-M3 designed the exact words to send it. **This step:** all three, plus P5's retrieval, wired
into one class a caller can actually use. **Next (not yet specified):** almost certainly a
user-facing surface (CLI or API) over `RAGPipeline`, and/or the evaluation harness Honest
Limitation 2 names.

## How to do this yourself
1. When a design doc says "this is left for a later microstep," treat that sentence as a literal
   task description for whenever you get there, not a vague gesture — `docs/prompts.md` §6's exact
   code diff became this step's D-2026-09-29-27 almost unchanged.
2. Single-source any string that must match itself elsewhere (`REFUSAL_TEXT` used both inside
   `SYSTEM_PROMPT` and as `RAGPipeline`'s own short-circuit value) — an f-string embedding a shared
   constant costs nothing and makes drift structurally impossible, not just unlikely.
3. When a Protocol you fully control is correctly typed and a third-party library's own stubs
   don't quite match, don't weaken your own type — document why with a scoped `type: ignore` at
   the one real boundary.
4. Test orchestration logic with fakes for the pieces you already tested elsewhere (`Retriever`,
   `LLM`) — a hermetic RAGPipeline test should never need a real Qdrant just to prove the budget
   was applied correctly.
5. When live testing hits a real, unrelated bug in a different module, resist the urge to fix it
   inline — flag it, and keep the current step's scope intact.

## Practice exercises
1. Trace `RAGPipeline.query()` by hand for a 2-chunk retrieval result where one chunk is 1900
   tokens and the other is 500. Which survives the budget? Check your answer against
   `test_query_applies_the_context_budget_before_building_the_prompt`.
2. Rewrite `_prepare`'s empty-retrieval check to ALSO short-circuit when every retrieved chunk has
   an `approx_token_count` of zero (a different kind of "nothing useful"). Is this a good idea?
   What would `mechanical_source_list` show in that case if you didn't?
3. Add a new hermetic test asserting `stream()`'s error-propagation behavior happens BEFORE any
   fragment is yielded, not after a few fragments already reached the caller.
4. Using `FakeLLM`, build a test where `generate()` returns an answer that ALREADY contains a
   `Sources:` block coincidentally. What does `RAGPipeline.query()` do with it? Is that the right
   behavior?
5. Read `decision.md` D-2026-09-29-25 and D-2026-09-29-28 back to back. Write, in your own words,
   the argument for revising the worked example NOW versus leaving it for a dedicated step. Which
   do you find more convincing, and why?
6. `IdentityReranker` (this step's live-test fake) keeps the hybrid stage's own order. Compare its
   real, live top-3 for "Who founded Insurellm?" to what a real `Reranker` (Cohere) would return
   for the same question, using `tests/retrieval/test_pipeline.py`'s own live fixtures as a guide.
7. Add a `k` override to `RAGPipeline` that DOES vary per call (breaking from the literal
   `query(question: str) -> str` signature). What would the task's own exact signature requirement
   say about whether this is a good idea?
8. Using `mechanical_source_list` and `build_user_prompt` directly (no `RAGPipeline` needed),
   reproduce §7's real worked example from `docs/prompts.md` and confirm the two numbered lists
   match exactly.
9. Design (on paper) a test that would catch a regression where `query()` forgot to pass
   `system=SYSTEM_PROMPT` to `generate()`. Does one of the existing tests already catch this?
10. Investigate `src/embedding/embedder.py`'s missing timeout yourself (the flagged follow-up
    task) — what value would you choose, and would you share `PROBE_TIMEOUT_SECONDS` from
    `src/generation/setup.py` or define a separate constant? Argue for one.

## Sources
- Lewis et al. (2020), [RAG](https://arxiv.org/abs/2005.11401), arXiv:2005.11401.
- [docs/prompts.md](../docs/prompts.md) — the design this step implements, section by section.
- [docs/31 few_shot_tie_investigation_deep_dive.md](<../docs/31 few_shot_tie_investigation_deep_dive.md>) —
  the measured risk this step ships with, disclosed not fixed.
- [notes/step-6.3-prompt-templates.md](step-6.3-prompt-templates.md) — the design this step is a
  direct continuation of.
- [tests/retrieval/test_pipeline.py](../tests/retrieval/test_pipeline.py) — the no-Cohere-key live
  fixture pattern this step's own live test reuses.
