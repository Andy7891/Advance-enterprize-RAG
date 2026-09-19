# Phase 3, Microstep 6 — Unit & integration tests for the vector store

**Date:** 2026-09-19

## Audit first: 61 unit tests already existed
Before writing anything: `tests/embedding` and `tests/vectorstore` already had 61 tests
(lock 7, embedder 17, setup 8, store 16, ingest 12) with 70–79% line coverage. What was missing:
1. Any test of the components **together**: chunks → ingestion → store → search.
2. Any test against the **real Qdrant server or real model**. Deep dive 14 found server/in-memory
   differences no hermetic test can see.
3. Any record of **retrieval behaviour**, what works and what doesn't.
4. The command-line `main()` functions, which were most of the uncovered lines.

## What was built
- **`tests/support.py`**: `make_chunk`, `write_corpus`, `BagOfWordsClient` (toy embedding model),
  `pinned_digest`, `live_services_down()`.
- **`tests/test_retrieval.py`**, 26 tests:
  - **Hermetic (15):**
    - the toy model's properties;
    - 4 questions answered through ingestion → store → search;
    - complete chunks with descending scores;
    - query and document prefixes reaching the model;
    - an edited document found by its new text (with its stale second chunk gone);
    - a deleted document disappearing from search;
    - all 5 CLIs run in-process with exit codes.
  - **Live (11, `@live_test`):**
    - a verified ingestion into a scratch collection;
    - 3 known answers in the top 3;
    - 2 known misses as `xfail(strict=True)` (the Carllm product question, the CEO question);
    - identical scores on repeat;
    - readiness plus payload indexes on the server;
    - the server rejecting id −1 while in-memory accepts it;
    - gzip negotiation;
    - Ollama serving the pinned digest.
- **`pyproject.toml`:** registered the `live` marker.
- **CI:** `pytest tests/ -v -rs -m "not tiktoken and not live" ...`.

## Verified, not assumed
```
full suite, services up     : 185 passed, 2 xfailed (the known misses)
CI-equivalent run           : 174 passed, 13 deselected (11 live + 2 tiktoken)
coverage embedding+vectorst.: embedder 78->96%, lock 75->91%, ingest 79->98%, setup 70->95%, store 78->98%
services down               : Qdrant stopped -> 15 passed, 11 skipped, each skip naming the missing service
live scratch collection     : 20 chunks from 8 documents, ~21 s embedding, deleted afterwards (no test_live_* left)
mutation tests (hermetic)   : 7 mutants (query prefix, delete-by-doc_id guard, orphan delete, search limit,
                              CLI rank numbering, CLI exit code on failure, health exit code) -> all killed
strict xfail semantics      : a strict xfail that passes -> "XPASS(strict)" -> the run fails; non-strict -> green
```
**Probing before asserting found a second retrieval miss.** "Who is the CEO of Insurellm?" ranks
neither Avery Lancaster's HR record nor `about.md` in the top 3 of the live subset. It's now a strict
xfail alongside Carllm.

**Two slips, both caught and fixed:**
1. Importing `src.vectorstore.setup as setup_module` produced 24 errors: pytest treats a module-level
   `setup_module` as an xunit setup hook. Renamed the alias to `vector_setup`.
2. During mutation testing, the final restore of `store.py` failed with a Windows file error, leaving a
   mutant in place for a moment. It was restored from backup and checked byte-exact, and `git diff`
   confirmed no source change.

## Why this approach
| Choice | Over | Because |
|---|---|---|
| hermetic + live | hermetic only | only live tests see the real server and model |
| strict xfail for known misses | no quality checks | documents the misses, and fails loudly once they're fixed |
| auto-skip with reason | env-var switch | runs whenever services are up; says why when they aren't |
| scratch collection | real `chunks` | tests can't damage or depend on the real index |
| bag-of-words toy model | one-hot fake | real questions work without a model server |
| rank asserts | score asserts | robust to harmless score changes |

## Key concepts
- 🟡 **Test pyramid:** many unit tests, fewer integration tests, few live/end-to-end tests.
- 🟡 **Hermetic vs live test:** no network and always the same result, vs real services that may be absent.
- 🔴 **Known-answer retrieval test:** a question whose answering chunk is known, asserted by rank.
- 🟡 **`xfail(strict=True)`:** an executable record of a known bug that fails once the bug is fixed.
- 🟡 **Fixture scope `session`:** expensive setup done once for all tests that need it.
- 🟡 **Contract test:** checks that a stand-in behaves like the real dependency, or records where it doesn't.
- ⚪ **Monkeypatching module globals:** replacing a name where it is *looked up*, not where it's defined.
- ⚪ **pytest xunit names:** `setup_module`, `teardown_module` etc. are hooks, so avoid them as variable names.

## Honest limitations
1. Live tests never run in CI (Windows runner, no service containers).
2. Known answers were chosen from questions that already work, so they can't reveal *new* weaknesses. That
   needs a labelled evaluation set.
3. The live subset (8 documents) behaves differently from the full index: rankings can differ.
4. In-process CLI tests swap out client construction, so wrong URL flags wouldn't be caught.
5. The toy model has no synonyms: hermetic tests passing says nothing about semantic quality.
6. The older test files still duplicate helpers that now exist in `tests/support.py`.

## How this connects
**Previous:** P3-M1…M5 built collection setup, model lock, embedding, store and the ingestion job, each
with unit tests. **This step:** tests them together, against the real services, and records current
retrieval behaviour. **Next:** retrieval quality. The two strict xfails are the acceptance tests for
hybrid search or reranking, and a labelled question set would turn "known answers" into a real metric.

## How to do this yourself
1. List existing tests and measure coverage before writing new ones (`pytest --cov`).
2. Find what no test covers: components together, real services, behaviour, entry points.
3. Build a small deterministic fake that keeps the property you need (word overlap → similarity).
4. Write flow tests through the public API with that fake.
5. Register a marker for live tests and skip them with a reason when services are down.
6. Build live data in a uniquely named scratch resource, and delete it in `finally`.
7. Probe real results **before** writing assertions; assert by rank.
8. Turn known failures into `xfail(strict=True)` with a reason that says what would fix them.
9. Mutation-test the new tests against source mutants, restoring files byte-exact.
10. Make CI's selection explicit (`-m "not live"`, `-rs`).

## Practice exercises
1. Stop Ollama only and run `pytest tests/test_retrieval.py -rs`. Which tests skip, and what reason do they give?
2. Add a hermetic question the toy model gets *wrong* because of a synonym ("How much does a widget cost?"). Mark it xfail and explain why.
3. Add a fourth live known answer after probing it first with `store search`.
4. Make one strict xfail pass by editing its assertion, and watch the run fail with `XPASS(strict)`.
5. Parametrise `test_store.py`'s `client` fixture over in-memory and live server (D7-B) for one test.
6. Write a Linux CI job with a Qdrant service container that runs only the Qdrant-only live tests.
7. Migrate `tests/vectorstore/test_ingest.py` to use `tests/support.py` helpers.
8. Measure how long the live session fixture takes, and reduce the subset to hit 10 seconds.
9. Add a live test that `query_points` with `category == products` returns only product chunks.
10. Write 10 labelled questions and compute hit@3 over the full `chunks` collection: the start of Phase 4 evaluation.
