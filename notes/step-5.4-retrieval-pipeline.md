# Phase 5, Microstep 4 — Assemble Complete Retrieval Pipeline

**Date:** 2026-09-29
**Core learning: Yes — pipeline composition and output-contract design.** Nothing new is invented
at the retrieval-logic level (RRF fusion and cross-encoder reranking already exist); what's new is
deciding how two independently-correct stages combine into one answer, and what that answer promises
its caller.

> **Status: built and hermetically verified; the full live path still needs your Cohere key.**
> `COHERE_API_KEY` in `.env` is still empty, so `RetrievalPipeline` has never returned a real,
> Cohere-reranked answer. Two live tests run today with no key at all (a fake "identity" reranker
> stands in, proving the real `HybridRetriever` wiring); the two full-pipeline live tests skip until
> a key exists.

## The gap this step closes
P5-M2 built `HybridRetriever` and P5-M3 built `Reranker`. Using them together meant a caller had to:
know the pool-size math that makes reranking useful at all (P5-M3's "a reranker can only reorder what
it's given" finding), call two classes in the right order, and manually read `.chunk` off two
different result types. `RetrievalPipeline` is the one place that composition happens, and
`RetrievalResult` is the one object a caller (a future prompt-construction step) needs.

```
query ─► HybridRetriever.retrieve_with_scores(query, pool_size) ─► Reranker.rerank_with_scores(query, pool, k) ─► RetrievalResult
         (pool_size = max(k * multiplier, floor))
```

## What was built
- **[`src/retrieval/pipeline.py`](../src/retrieval/pipeline.py)**
  - `RetrievalResult(query: str, results: tuple[RankedChunk, ...])` — frozen dataclass, matching
    `DocumentChunk`/`FusedChunk`/`RerankedChunk`'s existing style in this codebase.
  - `RankedChunk(chunk, final_rank, relevance_score, hybrid_score, hybrid_rank, vector_rank,
    graph_rank, vector_score)` — one row per result, carrying every stage's own verdict.
  - `RetrievalPipeline(hybrid, reranker, *, pool_multiplier=3, pool_floor=20)`, one public method:
    `retrieve(query: str, k: int) -> RetrievalResult`, exactly the spec'd signature (no bonus
    parameters this time — unlike P5-M2/M3, the task named this exact signature, so all tuning lives
    at the constructor).
  - `HybridSearcher` / `Reranking` Protocols requiring `retrieve_with_scores` / `rerank_with_scores` —
    the score-carrying methods, not the plain ones, because the rich `RetrievalResult` needs the
    lineage only those expose.
- **[`tests/retrieval/test_pipeline.py`](../tests/retrieval/test_pipeline.py)**, 36 tests: 30 hermetic
  with fakes, 1 hermetic against a REAL `HybridRetriever` (in-memory Qdrant, toy embedder) paired with
  a fake reranker, and 5 live (3 need no Cohere key and pass today; 2 need one and skip until you add
  it).
- No existing file changed.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Pool size | `max(k * 3, 20)`, constructor-configurable | `k * multiplier` alone; a fixed constant | not validated against an eval set, and NOT monotonic: P5-M3 measured the CEO answer's pool rank get worse (10→3, 20→15) as the pool grew, so the floor guards against starvation, not against poor recall in general |
| D2 Result shape | Rich, lineage-preserving `RetrievalResult`/`RankedChunk`, as frozen dataclasses | Minimal `{query, chunks}`; Pydantic `BaseModel` | needs `retrieve_with_scores`/`rerank_with_scores`, not the plain methods; a caller wanting JSON must serialize it themselves |
| D3 Module shape | One new file, `src/retrieval/pipeline.py` | Split `schemas.py` + `pipeline.py`, mirroring `ingestion/` | no existing file touched, but the result type lives next to the class that builds it, not with the project's other "Schema" file |
| D4 Errors | Propagate unchanged from either stage | Wrap in a `RetrievalStageError`; log then re-raise | with the Cohere key empty, every real call raises today; no hybrid-only fallback exists |
| D5 Validation | A local `_check_k`/`_check_query`/`_check_positive_int`, duplicating each sibling module's own copy | A shared `src/retrieval/validation.py` | a fourth copy of near-identical code now exists in the package |
| D6 Factory | None — pure dependency injection only | A `from_settings()` that also opens/owns a Neo4j driver | no one-line way to build the real pipeline; a caller must wire Qdrant/Neo4j/Ollama/(Cohere) themselves |
| D7 Acceptance | Hermetic fakes + a real-hybrid hermetic test + a no-Cohere live test + a Cohere-gated live test, no outcome claims yet | Hermetic only | the full live path is unverified until a key exists |

## Verified, not assumed
```
this step's tests    : 36 (30 hermetic-fake + 1 hermetic-real-hybrid + 5 live: 3 need no Cohere key
                        and pass; 2 need a Cohere key and skip)
mutation check        : 10 deliberate breakages of pipeline.py, each caught by a test, none survived
whole suite, live      : 664 passed, 6 skipped (2 pipeline + 4 pre-existing reranker tests, all
                        gated on the same missing Cohere key), 3 xfailed  (~102s)
CI mode               : 589 passed, 84 deselected  (-m "not tiktoken and not live")
ruff, format, mypy    : clean
```

**What the real-hybrid-stage live tests prove without any Cohere key.** `IdentityReranker` (a tiny
test-only class that keeps the hybrid's own order, defined in the test file, not shipped in
`src/`) stands in for `Reranker`. Wiring a real `HybridRetriever` (real Qdrant + Neo4j + Ollama)
through `RetrievalPipeline` with that stand-in confirms: two known-answer questions still return an
accepted chunk through the full class, and the pool-size arithmetic (`max(k*3, 20)`) is really being
sent to the real hybrid stage, not just a fake one. This is exactly D7's "one live test that only
exercises the hybrid stage" promise.

## Findings
1. **The task's own signature meant no bonus parameters this time.** P5-M2 and P5-M3 both added
   optional keyword arguments beyond their literal spec (`vector_weight=None`, `model=DEFAULT_MODEL`)
   because their specs were open ("weighting strategy (configurable)"). This task specifies
   `retrieve(query: str, k: int) -> RetrievalResult` exactly, so pool sizing is constructor-only. A
   caller who wants different fusion weights or a different rerank model configures the injected
   `HybridRetriever`/`Reranker` directly — `RetrievalPipeline` doesn't re-expose what its dependencies
   already make configurable.
2. **Protocols with fewer parameters than the real method are fine.** `HybridSearcher.retrieve_with_scores(self, query, k)` has two fewer parameters than the real `HybridRetriever.retrieve_with_scores`
   (which also takes optional `vector_weight`/`graph_weight`). mypy accepts this: a concrete method
   with *extra optional* parameters is a valid implementation of a Protocol that calls it with fewer.
3. **`RetrievalResult.results` is a tuple, not a list**, following this codebase's own documented
   rule (`EmbeddedChunk.vector: tuple[float, ...]` in `src/ingestion/schemas.py`): a frozen dataclass
   should be immutable all the way down, and a `list` field would let a caller mutate it in place.
4. **The pipeline trusts its dependencies' own edge-case handling rather than re-implementing it.**
   An empty hybrid pool is still passed to the reranker (empty list and all); `Reranker` already
   short-circuits on an empty list with no request sent (P5-M3 D7). Nothing in `RetrievalPipeline`
   special-cases this — it was cheaper and more correct to trust the layer that already owns it.

## Key concepts
🔴 = core to RAG and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Pipeline composition:** deciding what flows between two already-correct stages (here: the pool
  size) is itself a design decision with measurable consequences, not a detail.
- 🔴 **Output contract / data-boundary design:** what `RetrievalResult` promises — the full lineage of
  every score and rank — determines what a future consumer (prompt construction, evaluation, a UI)
  can do without re-deriving information that was already computed and then thrown away.
- 🟡 **Protocol variance for methods with extra optional parameters:** a narrower Protocol is
  satisfied by a wider concrete implementation, which is what let two already-configurable classes
  plug in without changing their signatures.
- 🟡 **Trusting composed layers instead of re-validating their invariants:** the empty-pool case is
  handled once, in `Reranker`, not duplicated in `RetrievalPipeline`.
- ⚪ Dependency injection, local validation helpers, a frozen dataclass result type — plumbing.

## Honest limitations
1. **Never run against the real Cohere API end to end.** The two full-pipeline live tests are written
   and will run the moment a key exists; until then, "the pipeline works" means "the composition is
   correct," not "the answers are good."
2. **The pool floor (20) and multiplier (3) are unvalidated defaults**, carried over from P5-M3's own
   test constant, not chosen from an evaluation set.
3. **No fallback if reranking fails.** A Cohere outage, rate limit, or missing key makes `retrieve()`
   raise; there is no degrade-to-hybrid-only path (a deliberate choice, D4, consistent with every
   prior error-handling decision in this project).
4. **Inherits every limitation of its two dependencies** — exact-name entity linking, RRF's
   rank-only fusion, the alphabetical tie-break bias, Cohere's rate limits.

## What is still open (needs you)
Add `COHERE_API_KEY` to `.env` yourself (I cannot create accounts or enter keys), then run:
```bash
.venv/Scripts/python -m pytest tests/retrieval/test_pipeline.py -m live -s -p no:cacheprovider
```
That runs the two hybrid-only live tests (already passing) plus the two full-pipeline live tests,
and prints a report of the five labelled questions' final top-3 through the whole system. Send me
that output and I'll add the real numbers here, the same way P5-M3's report was designed to work.

## How this connects
**Previous:** P5-M2 (fusion) and P5-M3 (reranking) each solved one problem in isolation and were each
tested and documented on their own. **This step:** the first place both exist inside one call.
**Next (not yet specified to me):** a prompt-construction / generation step almost certainly consumes
`RetrievalResult` next — Phase 5's title is "Retrieval & Reranking," and this looks like its last
microstep, so Phase 6 is likely generation. Worth raising with you directly (see below) rather than
assuming.

## How to do this yourself
1. Before composing two classes, list exactly what a caller currently has to know to use them
   together correctly (here: the pool-size relationship) — that's the composition's real job.
2. Decide whether your output type should compress information (minimal) or preserve it (rich +
   lineage); check what already exists (`_with_scores` methods) before deciding, since building a
   rich contract from ones that already return scores costs little extra code.
3. Follow the codebase's own conventions for the new type (dataclass vs Pydantic, tuple vs list on a
   frozen object) rather than picking whatever's more familiar to you.
4. Write the Protocols the composing class actually calls, not the full concrete interface — mypy
   will tell you if a narrower Protocol doesn't structurally match.
5. Test with fakes for logic, one real-but-cheap dependency for a sanity check, and live tests last,
   gated to skip honestly when a resource (a key, a service) isn't available.
6. Mutation-test the new code: break each behaviour on purpose and confirm a test catches it, before
   trusting the suite.

## Practice exercises
1. Trace `pipeline.retrieve("Who founded Insurellm?", 3)` by hand: what `pool_size` is requested,
   and (from `notes/step-5.3`'s pool table) is the known answer inside a pool that size?
2. Set `pool_floor=3` and `pool_multiplier=1` and re-run the CEO question through a real pipeline
   (with `IdentityReranker`, no Cohere needed). Does the pool now miss the answer entirely?
3. Add a `pool_size` property or method that computes `max(k*multiplier, floor)` without calling
   anything, and write a test that checks it directly instead of through `calls[0]`.
4. Build a `LoggingReranker` wrapper (implements `Reranking`, logs then delegates to a real
   `Reranker`) and inject it instead of the real one. What does this prove you can do because of D6?
5. What would break if `RankedChunk.hybrid_rank` used `rank` (the final position) instead of
   `item.input_rank` (the pool position)? The mutation check already caught this — explain why in
   your own words.
6. Remove the `tuple(...)` around `results` in a scratch copy and try `answer.results.append(x)`
   from outside the class. What happens with a `list`, and why does the frozen dataclass not stop it?
7. Write a `RetrievalStageError` (D4's rejected option) and change `retrieve()` to raise it, chaining
   the original error with `from`. Which existing tests would need to change?
8. Given `RetrievalResult`, sketch (on paper) the smallest prompt-construction function that could
   consume it — what fields would it actually use, and which would it ignore?
9. Design an evaluation harness (on paper) that runs `pipeline.retrieve(q, k)` over 20+ labelled
   questions and computes hit@k. Which of `RankedChunk`'s fields would you need for a partial-credit
   metric like MRR versus a strict hit@k?
10. Once you have a Cohere key: run the live report, then flip `pool_floor` to 50 and re-run. Does
    the CEO question's answer change, consistent with the P5-M3 finding that the direct answer only
    enters the pool at N=50?

## Sources
- Lewis et al. (2020), [Retrieval-Augmented Generation](https://arxiv.org/abs/2005.11401),
  arXiv:2005.11401 — the architecture this whole pipeline implements one piece of.
- LangChain, [contextual compression / rerank retriever pattern](https://python.langchain.com/docs/how_to/contextual_compression/)
  and LlamaIndex, [node postprocessors](https://docs.llamaindex.ai/en/stable/module_guides/querying/node_postprocessors/) —
  prior art for composing a retriever and a reranker behind one interface (both links redirected to
  overview pages when checked; treat as pointers, not deep links).
- [notes/step-5.2-hybrid-retriever.md](step-5.2-hybrid-retriever.md) and
  [notes/step-5.3-reranker.md](step-5.3-reranker.md) — the two stages this step composes, including
  the pool-size measurements D1's floor is based on.
