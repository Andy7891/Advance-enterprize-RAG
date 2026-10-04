# Phase 6, Microstep 7 — Integration Checkpoint: Verify End-to-End RAG

**Date:** 2026-09-30
**Core learning: No (validation).** Manual spot-check of the real, complete `RAGPipeline`
against 8 real questions, using production defaults end to end (real Qdrant, real Neo4j, real
Ollama, `IdentityReranker` standing in for Cohere — the same no-key configuration this project's
own live tests have used since Phase 5).

## What was run
[`RAGPipeline.query()`](../src/orchestration/rag.py) with real defaults (`k=3`, `temperature=0.1`,
`max_tokens=400`), against:
- The 5 questions this project has used as its benchmark since Phase 5.
- 3 new ones chosen to exercise paths not yet spot-checked end to end: a clearly out-of-scope
  product question (refusal path), a rephrasing of the documented CEO weak spot, and a compound
  two-part question.

A resumable runner script wrote one JSON line per completed question as it went, so a crash
mid-run (a real, accepted risk — see below) would lose at most the one in-flight question.

## Results

| # | Question | Outcome | Time | Verdict |
|---|---|---|---|---|
| 1 | Who founded Insurellm? | **Timed out** (304.8s) | — | Inconclusive — this exact question passed live in P6-M6's own test run earlier today; consistent with the disclosed "right at the throughput ceiling" finding (decision.md D-2026-09-30-12), not a new problem |
| 2 | What are the company values? | Answered | 178.2s | **Weak — see Finding 1 below** |
| 3 | Which product is for life insurance? | Answered | 137.5s | ✅ Correct |
| 4 | What does the Carllm product do? | Answered | 251.4s | ✅ Correct — see Finding 2 |
| 5 | Who is the CEO of Insurellm? | Answered | 44.4s | ✅ Correctly refused — see Finding 3 |
| 6 | Does Insurellm offer pet insurance? (new) | **Timed out** (300.9s) | — | Inconclusive |
| 7 | Who leads Insurellm as its chief executive? (new) | **Timed out** (302.2s) | — | Inconclusive |
| 8 | Who founded Insurellm, and which product is used for life insurance? (new, compound) | Answered | 293.8s | ✅ Correct, both parts |

**5 of 8 completed; 3 of 8 timed out even at the 300s bound P6-M6 just raised it to.** No OOM
crash this run (the accepted risk from P6-M1/P6-M7's own decision going in), but the timeout rate
is itself the headline finding — see below.

## The real answers, verbatim

**Q2 — What are the company values?**
> The provided sources do not explicitly state the company values. However, they suggest a
> commitment to growth and development [1], work ethic, positive attitude, willingness to go
> above and beyond, fostering team spirit [2], and equal opportunity employment and celebrating
> diversity [3].
>
> Sources:
> [1] HR Record (employees/Jordan K. Bishop.md)
> [2] HR Record (employees/Alex Harper.md)
> [3] Careers at Insurellm (company/careers.md)

**Q3 — Which product is for life insurance?**
> The product that is for life insurance is Lifellm [1].
>
> Sources:
> [1] Product Summary (products/Lifellm.md)
> [2] About Insurellm (company/about.md)
> [3] Contract with Evergreen Life Insurance for Lifellm (contracts/Contract with Evergreen Life Insurance for Lifellm.md)

**Q4 — What does the Carllm product do?**
> The Carllm product is an auto insurance solution developed by Insurellm that utilizes
> artificial intelligence (AI) to streamline the way insurance companies offer coverage to their
> customers [3]. It offers features such as AI-Powered Risk Assessment, Instant Quoting,
> Customizable Coverage Plans, Fraud Detection, Customer Insights Dashboard, Mobile Integration,
> and Automated Customer Support [2]. The product aims to enhance customer satisfaction and
> retention by providing personalized auto insurance solutions while minimizing costs [3].
>
> Sources:
> [1] Contract with TechDrive Insurance for Carllm (contracts/Contract with TechDrive Insurance for Carllm.md)
> [2] Contract with TechDrive Insurance for Carllm (contracts/Contract with TechDrive Insurance for Carllm.md)
> [3] Product Summary (products/Carllm.md)

**Q5 — Who is the CEO of Insurellm?**
> The sources provided do not contain information about the CEO of Insurellm.
>
> Sources:
> [1] Careers at Insurellm (company/careers.md)
> [2] HR Record (employees/Jordan K. Bishop.md)
> [3] HR Record (employees/Alex Harper.md)

**Q8 — Who founded Insurellm, and which product is used for life insurance? (compound)**
> Insurellm was founded by Avery Lancaster [3]. The product used for life insurance at Insurellm
> is Lifellm [2].
>
> Sources:
> [1] About Insurellm (company/about.md)
> [2] About Insurellm (company/about.md)
> [3] Avery Lancaster (employees/Avery Lancaster.md)

## Findings

1. **New, previously-undocumented retrieval gap: "company values" misses the actual Core Values
   section.** `data/company/culture.md` has a real "## Core Values" section (Innovation First,
   Customer Obsession, Integrity & Transparency, Collaborative Excellence) — the exact content
   this question is asking for. Retrieval at `k=3` with `IdentityReranker` instead surfaced two
   individual employees' HR records and the careers page, never `culture.md`. The model answered
   honestly, grounded only in what it was given (correctly hedging "do not explicitly state") —
   grounding (C1) worked exactly as designed — but the *retrieval* itself missed the best chunk.
   **This directly contradicts `tests/test_rag.py`'s own `KNOWN_RELIABLE` list**, which assumed
   this question reliably retrieves well enough to check for `"innovation"` in the answer; this
   real run's answer never contains that word. Flagged as a follow-up: `KNOWN_RELIABLE` should
   drop this question (or the corpus/retrieval config needs its own fix) — not corrected inline
   here, since it's a different file's own concern, not this checkpoint's job to silently patch.

2. **A genuinely positive surprise: the Carllm question (a documented Phase 5 retrieval miss)
   produced a correct, detailed, well-cited answer at the full pipeline level.** The original
   miss (`tests/retrieval/test_pipeline.py`'s own `xfail`) was measured against dense vector
   retrieval alone; by the time hybrid retrieval + the full `RAGPipeline` prompt/grounding design
   are applied together, this specific question comes out correct. Worth noting as real evidence
   that later pipeline stages can compensate for an earlier stage's own documented weakness — not
   a fix to the underlying miss, but a genuine, measured mitigation.

3. **The CEO question (the other documented Phase 5 miss) correctly refused rather than
   hallucinating.** This is the grounding design (P6-M3 C1, `docs/30`) working exactly as
   intended on a genuinely hard, real case: retrieval didn't find the CEO's actual record, and
   instead of confidently naming the wrong person (or the right person for the wrong reason), the
   model said so honestly. This is the SAFE failure mode this whole design exists to produce.

4. **3 of 8 real questions (37.5%) could not complete within 300 seconds.** This is not a new
   discovery (decision.md D-2026-09-30-11/-12 already disclosed this host's real CPU-only
   throughput ceiling), but this checkpoint is the first time it's been measured against a
   *representative, varied* question set rather than the same 5 benchmark questions repeatedly.
   A 37.5% timeout rate at `max_tokens=400` is a real, material usability constraint — worth
   weighing seriously before this pipeline is exposed behind any interactive interface (a direct,
   practical concern for the upcoming Phase 7 FastAPI work, where a synchronous request timing out
   at 5 real minutes would be a genuinely bad user experience).

## Honest overall verdict
**Grounding and citation correctness: strong.** Every completed answer was either correct and
well-cited, or an honest, correctly-grounded refusal — zero hallucinations observed across 5 real
completions, including on two questions with documented retrieval weaknesses. **Retrieval recall:
mixed** — reliable for named products and the founder, unreliable for "company values" specifically
(a new finding), already known-weak for the CEO's own record. **Latency/reliability: the weakest
link** — over a third of real questions did not complete in a reasonable time on this dev machine,
which would need addressing (a faster host, a smaller/quantized model, or an async job pattern
instead of a synchronous request) before this pipeline could sit behind a real, interactive API.

## Follow-ups this checkpoint surfaced (not fixed here)
- `tests/test_rag.py`'s `KNOWN_RELIABLE` list should drop "What are the company values?" (Finding 1).
- Phase 7's FastAPI work should treat generation latency as a first-class design constraint, not
  an afterthought, given a measured 37.5% real timeout rate at 300s on this dev machine.

## How this connects
**Previous:** P6-M4/M5/M6 built, generalized, and integration-tested `RAGPipeline`. **This step:**
the first time the complete system was exercised manually, end to end, against a deliberately
varied question set rather than the same repeated benchmark — and it surfaced one genuinely new
retrieval finding plus a material latency concern neither automated test suite was positioned to
reveal on its own. **Next:** Phase 7 (FastAPI backend), now informed by this checkpoint's latency
finding.

## Sources
- [tests/retrieval/test_pipeline.py](../tests/retrieval/test_pipeline.py), notes/step-5.2,
  notes/step-5.3 — the original Carllm/CEO retrieval misses this checkpoint re-measured at the
  full-pipeline level.
- [decision.md](../decision.md) D-2026-09-30-11/-12 — the generation-timeout finding this
  checkpoint's own timeout rate is consistent with.
- [notes/step-6.6-rag-integration-tests.md](step-6.6-rag-integration-tests.md) — `KNOWN_RELIABLE`,
  the list this checkpoint's Finding 1 contradicts.
