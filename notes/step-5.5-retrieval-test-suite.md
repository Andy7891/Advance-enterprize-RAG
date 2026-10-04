# Phase 5, Microstep 5 — Unit & Integration Tests for Retrieval

**Date:** 2026-09-29
**Core learning: Yes — the test pyramid, and coverage as a signal, not a target.** The real lesson
of this step wasn't writing tests; almost all of them already existed. It was figuring out *which*
tests were still missing, using measurement (coverage) instead of assumption.

## The gap this step closes
P5-M2, P5-M3 and P5-M4 each shipped mutation-checked unit tests alongside their own code. Before
writing anything new, I counted what already existed:

```
tests/retrieval/test_graph_retriever.py    14 tests   100% coverage of graph_retriever.py
tests/retrieval/test_graph_store.py        54 tests
tests/retrieval/test_hybrid_retriever.py   42 tests    99% coverage of hybrid_retriever.py
tests/retrieval/test_reranker.py           40 tests   100% coverage of reranker.py
tests/retrieval/test_pipeline.py           36 tests   100% coverage of pipeline.py
tests/test_retrieval.py (P3, vector-only)  26 tests
------------------------------------------------------------------------------------
212 tests, 99% line coverage of src/retrieval/ — before this step touched anything
```

This is the same shape as [P4-M6](step-4.6-graph-integration-tests.md): a task that reads like "now
go write the tests" when the tests mostly already exist. The real gap wasn't volume, it was found by
running `--cov-report=term-missing` and reading what it actually flagged, plus asking "what have we
proven separately that we've never proven *together*?"

## What was built
Three small, targeted additions — no new files, matching D1(a):
- **[`tests/retrieval/test_hybrid_retriever.py`](../tests/retrieval/test_hybrid_retriever.py)**: one
  new test, `test_a_chunk_repeated_inside_the_graph_list_counts_once_at_its_first_rank`, the exact
  mirror of an existing vector-list test, but for the **graph** list's own duplicate-skipping branch
  (`fuse_rankings`, line 132), which coverage showed had never run in any test.
- **[`tests/retrieval/test_pipeline.py`](../tests/retrieval/test_pipeline.py)**: two new tests, using
  the REAL Cohere SDK over a stubbed HTTP transport (`httpx.MockTransport`, exactly P5-M3's own
  technique) paired with the REAL `HybridRetriever` (in-memory Qdrant) — no fakes on either side:
  - `test_a_real_hybrid_retriever_and_a_real_reranker_compose_correctly`: a stubbed reranker that
    genuinely reverses whatever real pool it's sent, checked against a real, separately-fetched pool.
  - `test_a_real_cohere_error_propagates_unchanged_through_the_real_pipeline`: a stubbed HTTP 429,
    asserting `cohere.errors.TooManyRequestsError` (the SDK's real exception type) survives the trip
    through the real `RetrievalPipeline`, not a generic `RuntimeError` from a fake.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Where new tests live | Extend `tests/retrieval/test_pipeline.py` | A new `tests/test_retrieval_integration.py`, mirroring P4-M6's `tests/test_graph.py` | `test_pipeline.py` already IS retrieval's integration point; a new file would relocate structure that already exists, not add missing structure |
| D2 Scope of the new real+real test | Two targeted tests (lineage + a real SDK error type) | A larger family (one test per field, per error type) | most of that ground is already covered separately for each half; the marginal, unproven value was specifically the *seam* |
| D3 "Passing test suite" bar | CI mode + live mode + an informational coverage report, no new CI gate | A hard `--cov-fail-under` gate added to `pyproject.toml` | a coverage gate is a bigger, unrequested process change, and choosing a threshold would need its own justification |

## Verified, not assumed
```
this step's new tests : 3 (1 hybrid_retriever, 2 pipeline), each confirmed to catch its OWN targeted
                         mutation in isolation (not just as part of the whole suite)
src/retrieval coverage : 99% -> 100% (the one missing line, hybrid_retriever.py:132, is now covered)
whole suite, live      : 667 passed (+3), 6 skipped, 3 xfailed  (~106s, services up)
CI mode                : 592 passed (+3), 84 deselected
ruff, format, mypy     : clean
```

## Findings
1. **Coverage found a real gap that reasoning alone had missed.** `fuse_rankings` has two nearly
   identical duplicate-skipping branches — one for the vector list, one for the graph list — added
   in the same commit, reviewed together, and only ONE of them was ever exercised by a test. This is
   exactly why [test coverage is a signal, not a target](https://martinfowler.com/bliki/TestCoverage.html):
   99% looked "close enough" until the *specific* missing 1% turned out to be a real, meaningful gap,
   not noise.
2. **A "real" test on one side isn't the same as a "real" test on both sides.** `test_pipeline.py`
   already had a real `HybridRetriever` (with a fake reranker) and `test_reranker.py` already had the
   real Cohere SDK (stubbed, with fake chunks). Neither proved the two real result types
   (`FusedChunk`, `RerankedChunk`) actually join correctly when BOTH sides are real — only that each
   half behaves correctly on its own.
3. **A fake's error is not proof a real error propagates the same way.** The existing
   `test_an_error_from_the_reranker_propagates` test uses a fake that raises `RuntimeError("cohere
   down")`. That proves `RetrievalPipeline` doesn't swallow *an* exception — it says nothing about
   whether a *specific* Cohere exception type (`TooManyRequestsError`) survives unchanged, which
   matters if a caller ever wants to catch it specifically (e.g., to retry only on rate limits).
4. **The two P3-era `xfail`s were checked, not assumed, to still be correct.** They test
   `QdrantVectorStore` alone via a `live_index` fixture that never touches `HybridRetriever` — vector
   search alone genuinely still misses both questions, so they're correctly left untouched.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **The test pyramid:** many cheap unit tests at the base (already built, per component), a few
  integration tests proving composition (this step's addition), live/acceptance tests at the very
  top (still gated on a Cohere key).
- 🔴 **Coverage as a necessary-but-insufficient signal:** a covered line only proves it *ran*, not
  that it's *correct* — but an *uncovered* line is unambiguous proof that nothing has ever checked it
  at all, which is exactly the asymmetry that made this step's one real finding possible.
- 🟡 **Testing a real SDK against a stubbed transport, on both sides of a composition:** reuses
  P5-M3's own technique (`httpx.MockTransport`, response objects built from the SDK's real types) but
  applied at the seam between two real classes instead of around one class in isolation.
- ⚪ Test placement and file organization — a real decision (D1), but not a deep concept.

## Honest limitations
1. **Still blocked on a real Cohere key** for the third microstep running. The full live,
   outcome-asserting acceptance test for the composed pipeline (deferred in P5-M3 and P5-M4) still
   doesn't exist, and I'm not writing predicted outcomes to fill that gap — see "what's still open".
2. **The new real+real tests use a toy, hand-built corpus** (2 documents, 4 chunks), not the real
   223-chunk collection — enough to prove the composition is wired correctly, not to say anything
   about answer quality on real questions.
3. **No mutation check was re-run across all five retrieval files together** — only the three new
   tests were mutation-checked in isolation; each file's own prior mutation check (from its own
   microstep) still stands independently.

## What is still open (needs you)
Same item as P5-M3 and P5-M4: add `COHERE_API_KEY` to `.env` yourself, then the two full-pipeline
live tests in `test_pipeline.py` (currently skipped) will run and print a real report. I'm not asking
a fourth time in this same way — if getting a key isn't a priority right now, say so and I'll treat
"no Cohere access" as a standing constraint for planning purposes instead of re-raising it each step.

## How this connects
**Previous:** P5-M2/M3/M4 each proved their own class correct in isolation. **This step:** the first
time two of those classes were proven correct *together*, for real, without a live service. **Next
(not yet specified to me):** likely Phase 6 (generation), which will consume `RetrievalResult`.

## How to do this yourself
1. Before writing a single new test, run coverage on the code the task names and read the `Missing`
   column literally — don't assume "well-tested" modules have nothing left uncovered.
2. Ask, for anything with multiple independently-tested pieces: "has any test exercised these pieces
   *together*, using the real type each side produces, or only fakes on each side separately?"
3. When a fake stands in for something with a rich failure taxonomy (like an SDK's exception
   hierarchy), ask whether "an error propagates" is really the same claim as "*this specific* error
   propagates unchanged" — usually it isn't.
4. Mutation-test new tests in isolation (run just that one test against the mutated code), not only
   as part of the whole file, so you know the new test is pulling its own weight.
5. Resist the urge to add a coverage gate just because you measured coverage once — measuring and
   gating are different decisions with different costs.

## Practice exercises
1. Run `pytest tests/retrieval/ tests/test_retrieval.py --cov=src/retrieval --cov-report=term-missing`
   yourself and confirm the `Missing` column is now empty everywhere.
2. Temporarily re-introduce the original bug (skip the graph-list dedup line) and re-run just the new
   test in isolation — confirm it fails with a specific, readable assertion, not a crash.
3. In `reversing_handler` (in the new pipeline test), change the reversal to a *rotation* (shift by
   one position) instead of a full reverse. Update the test's assertions to match and re-run.
4. Add a THIRD real+real test: stub the HTTP layer to return a 500 instead of a 429, and confirm
   `cohere.errors.InternalServerError` (not `TooManyRequestsError`) is what propagates.
5. Find a coverage tool report for a project you didn't write (any open-source Python repo with
   `--cov-report=html`) and look for a single, oddly-specific uncovered line inside an otherwise
   fully-covered function. What does that pattern usually mean?
6. Write a test proving the REVERSE composition gap doesn't exist: that a real `Reranker`'s output,
   fed a real `HybridRetriever`'s pool, produces `RankedChunk.relevance_score` values that are
   genuinely on Cohere's documented 0-to-1 scale, using the stubbed transport (no network).
7. Read Martin Fowler's [TestPyramid](https://martinfowler.com/bliki/TestPyramid.html) article, then
   sketch (on paper) where each of this project's 212+ retrieval tests sits on that pyramid.
8. Pick one existing hermetic test in `test_pipeline.py` and rewrite it as a live test (or vice
   versa). What did you gain, and what did you lose, by moving it?
9. Design a coverage GATE for `src/retrieval/` (the rejected D3(b)) — pick a threshold, write the
   `pyproject.toml` change, and argue for or against actually adding it.
10. Ask: is there an equally narrow, measurement-found gap like this one hiding in `src/graph/` or
    `src/ingestion/`? Run coverage there and see.

## Sources
- Martin Fowler, [TestPyramid](https://martinfowler.com/bliki/TestPyramid.html) — the shape this
  step's decisions were organized around.
- Martin Fowler, [TestCoverage](https://martinfowler.com/bliki/TestCoverage.html) — why 99% coverage
  can hide exactly the kind of gap this step found, and why coverage alone was treated as a signal to
  investigate, not a target to hit.
- httpx docs, [Mock transports](https://www.python-httpx.org/advanced/transports/#mock-transports) —
  the mechanism reused from P5-M3 to test a real SDK on both sides of a composition.
- [notes/step-4.6-graph-integration-tests.md](step-4.6-graph-integration-tests.md) — the precedent
  this step's whole "count first, find the narrow real gap" approach follows.
