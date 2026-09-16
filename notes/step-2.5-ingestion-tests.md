# Phase 2, Microstep 5 — Unit tests for ingestion

**Date:** 2026-09-16

## Pre-flight audit: the step was already ~90% done

The spec asked to "write `tests/test_ingestion.py` with tests for loader, chunker, schema."
Those tests already existed — 66 of them, at **93% coverage**:

| file | tests |
|---|---|
| `tests/ingestion/test_chunker.py` | 17 |
| `tests/ingestion/test_schemas.py` | 15 |
| `tests/ingestion/test_enrichment.py` | 12 |
| `tests/ingestion/test_pipeline.py` | 12 |
| `tests/ingestion/test_loader.py` | 9 |
| `tests/test_health.py` | 1 |

So the useful version of this step was not "write tests" but **"find out which tests are
missing, using evidence rather than intuition."**

## The finding that justifies the whole step

Running `pytest --cov=src --cov-report=term-missing` showed `chunker.py:218-223` — the merge
body of `_merge_undersized` — was **never executed**, despite
`test_undersized_chunks_are_merged_backwards` existing and passing.

Verified why: that test sets `min_chunk_chars=10_000` and asserts `len(chunks) == 1`. But the
splitter returns **one chunk before merging ever runs**, so `if merged and ...` is never true.
The assertion is true, the test is green, and the code it claims to test never executes.

**That is the core lesson of this microstep.** A green test proves one assertion held. It does
not prove the code ran. Coverage is what distinguishes the two.
(Background reading: [Martin Fowler — Test Coverage](https://martinfowler.com/bliki/TestCoverage.html),
whose point is that coverage is good at finding *untested* code and bad as a quality target.)

## What was built

- **`tests/conftest.py`** — shared fixtures, with two deliberately distinct data sources:
  `fixture_corpus` (committed, controlled) and `real_corpus` (the actual `data/`, kept for
  canary tests). Also hosts `FakeEmbedder` and `FakeCounter`.
- **`tests/fixtures/corpus/`** — 3 small markdown files across 2 categories, fully under the
  test suite's control.
- **`tests/ingestion/test_coverage_gaps.py`** — 11 tests for paths proven unexecuted:
  the merge body (4 tests, including consecutive-tiny-chunk collapsing), `_locate`'s two
  `None` branches, the offset-recovery fallback warning, `infer_category`'s missing-category
  `ValueError`, and `TiktokenCounter.encoding_name`.
- **`tests/ingestion/test_cli.py`** — 5 tests for `pipeline.main()`, which had zero coverage.
  They verify *wiring* (do CLI flags reach `run_pipeline` correctly?), not whether Ollama
  works, by monkeypatching both dependency builders.
- **CI** now runs `pytest -m "not tiktoken" --cov=src --cov-report=term-missing`.
- **`pytest-cov==7.1.0` pinned** — it was installed ad hoc during the audit, which is exactly
  the unpinned-dependency trap P2-M3 was about.

## Result

**93% → 99% coverage**, 66 → 82 tests. The 3 remaining uncovered statements are legitimately
untestable offline:

- `chunker.py:257-259` — `build_ollama_embedder`'s body, which needs a live model server.
- `pipeline.py:256` — the `if __name__ == "__main__":` line, never executed under pytest.

CI's exact command (`-m "not tiktoken"`) reports 98%, because the two tiktoken-marked tests
are deselected there.

## Key concepts

- **Code coverage** — which lines actually executed during a test run; finds untested code,
  but a high number does not prove tests are good.
- **Vacuous test** — one that passes without exercising the behaviour it claims to test.
- **Fixture** — reusable test setup provided by pytest via `conftest.py`, requested by naming
  it as a test argument.
- **`monkeypatch`** — pytest's built-in way to temporarily replace an attribute/function for
  one test, automatically undone afterwards.
- **Test double (fake)** — a simple stand-in (here `FakeEmbedder`/`FakeCounter`) that makes
  tests fast and offline.
- **`caplog`** — pytest fixture capturing log output, so a warning can be asserted on.

## Honest limitations

1. `_merge_undersized` and `_locate` are **private methods**; testing them directly couples
   the tests to internal names that no public contract promises.
2. Coverage is reported but **not enforced**, so it can silently drift downward.
3. **Line coverage is not branch coverage** — a line can execute without every condition on
   it being exercised.

## How this connects

**Previous:** M1–M4 built loader, chunker, enrichment and the pipeline, each with tests
written alongside. **This step:** verified those tests actually exercise the code, and filled
the gaps evidence revealed. **Next:** embedding + Qdrant upsert, where the delete-by-`doc_id`
guard is the first thing that needs a test written *before* the code.

## How to do this yourself

1. `pip install pytest-cov`, then `pytest --cov=src --cov-report=term-missing`. Pin it.
2. Read the `Missing` column as a list of line numbers, and open each one — don't assume
   what's there.
3. For each uncovered line, ask "is this untestable, or untested?" Only the second needs work.
4. When a test covering a behaviour exists but the behaviour's lines are uncovered, that test
   is vacuous — investigate why it passes before writing a new one.
5. Prefer calling a function directly over constructing elaborate inputs, when the goal is
   guaranteeing a specific branch runs.
6. Use `monkeypatch` to replace dependency *builders* rather than the dependencies, so the
   wiring under test stays real.

## Practice exercises

1. Revert `test_coverage_gaps.py`, rerun with `--cov`, and confirm the 25 missing lines
   reappear — see the before/after for yourself.
2. Delete the body of `_merge_undersized` (make it `return spans`) and run the OLD test
   only — confirm it still passes, proving it was vacuous.
3. Run with `--cov-branch` instead of plain coverage — does 99% line coverage still look
   like 99%?
4. Add `--cov-fail-under=99` locally and delete one test; watch the run fail.
5. Write a deliberately vacuous test of your own (assert something true but unrelated) and
   confirm coverage does not move.
6. Use `pytest --cov=src --cov-report=html` and open `htmlcov/index.html` — click into
   `chunker.py` and see uncovered lines highlighted in red.
7. Find which test covers `loader.py`'s Latin-1 fallback by running
   `pytest --cov=src -k latin1 --cov-report=term-missing`.
8. Move one test from `test_coverage_gaps.py` into its module's own test file — does
   anything break? What does that say about how pytest discovers tests?
9. Make `fixture_corpus` return a nonexistent path and see which tests fail vs error.
10. Time `pytest tests/` against `pytest tests/ --cov=src` — what does measuring coverage
    actually cost?
