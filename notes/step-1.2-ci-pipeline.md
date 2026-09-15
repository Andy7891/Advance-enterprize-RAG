# Step 1.2 — CI pipeline: `.github/workflows/ci.yml` (lint, test, build)

**Date:** 2026-08-19

### What was built
- Rewrote [.github/workflows/ci.yml](.github/workflows/ci.yml) with three separate jobs
  (`lint`, `test`, `build`), replacing the old single `build:` job that only ran tests with
  an "optional" no-op lint placeholder.
  - `lint`: installs `.[dev]`, then TODOs for `ruff check`, `ruff format --check`, `mypy`.
  - `test`: installs `.[dev]`, then a TODO for `pytest tests/`.
  - `build`: `docker build -f infra/docker/Dockerfile -t advance-rag:ci .` — proves the
    Dockerfile from Step 1.1 still builds on every push, catching drift between
    `pyproject.toml` and the image.
  All three run as independent jobs (parallel by default in GitHub Actions), each on
  `windows-latest` (kept from the original file, per your call — Docker builds run inside
  Docker Desktop's Linux-container mode on that runner, which does work, just with more
  overhead than a native Linux runner).
- Added `mypy==1.19.0` to `pyproject.toml`'s `dev` extras, plus `[tool.ruff]` /
  `[tool.ruff.lint]` / `[tool.mypy]` config sections (line length, target Python version,
  which ruff rule sets are active, mypy's strictness).
- Added [tests/test_health.py](tests/test_health.py) — a single smoke test hitting `/health`
  via FastAPI's `TestClient`. Necessary, not decorative: `pytest` exits with code 5 ("no
  tests ran") when zero tests exist, which would make the `test` job fail on *every* push
  until real tests exist — this unblocks that immediately.

### Why this approach (trade-offs vs alternatives)
- **Ruff (lint + format) + mypy (types)**, not your originally-specified `black` + `flake8`
  + `mypy`: `ruff` is a single Rust-based tool that reimplements both `flake8`'s linting
  rules and `black`'s formatting — using all three together would mean two tools
  (`ruff`+`black`, or `ruff`+`flake8`) doing overlapping work. We'd already pinned `ruff`
  alone in Step 1.1's dev deps, so this keeps that decision consistent rather than bolting
  on two more tools. `mypy` stays separate because nothing else in the stack does type
  checking — ruff explicitly does not check types, only style/correctness patterns.
- **Three separate jobs**, not one job with sequential steps: independent jobs run in
  parallel on separate runners by default, so a lint failure doesn't block you from seeing
  whether tests also failed — you get all the feedback from one push, not gated one-error-
  at-a-time.
- **`ruff format --check` (non-mutating) in CI**, not `ruff format` (which rewrites files):
  CI should *verify* formatting, never silently rewrite your code — a mutating step in CI
  would need a separate commit-back mechanism and hides formatting drift instead of failing
  loudly on it. You still run plain `ruff format .` locally to actually fix things.
- **Kept `windows-latest`** (your explicit choice) over switching to `ubuntu-latest`: matches
  your local dev OS, at the cost of slightly slower/more expensive Docker builds compared to
  a native Linux runner.

### Key concepts used
- **Lint vs format vs type-check vs test** — four genuinely different questions about code
  correctness: lint = "does this violate style/error-prone patterns," format = "is
  whitespace/quoting consistent," type-check = "do the types I annotated actually line up,"
  test = "does the code produce the outputs I expect."
- **CI job independence/parallelism** — separate `jobs:` in a GitHub Actions workflow run on
  separate, isolated runners simultaneously by default (no shared state) unless you add a
  `needs:` dependency between them.
- **`--check`/non-mutating verification flags** — a general pattern (not just ruff/black):
  CI should assert a property holds, never "fix" it silently and push the fix — that hides
  problems and produces surprise commits.
- **Build validation as its own CI concern** — a `docker build` that never runs the
  container, just proves the image *can* be built; distinct from the actual `docker compose
  up` integration test we did manually in Step 1.1.

### Real things verified before handing this off (not just "should work")
Ran every TODO'd command locally against the actual project first, so the scaffold is
provably achievable rather than hypothetical:
- `ruff check src/ tests/` → `All checks passed!`
- `ruff format --check src/ tests/` → **failed as expected** — 3 files (all our recently
  created ones) would be reformatted (missing blank line after module docstrings). Left
  unfixed on purpose — running `ruff format .` yourself to clear this is part of the
  exercise, and it's a realistic first CI failure you'll actually hit.
- `mypy src/` → `Success: no issues found in 5 source files`
- `pytest tests/ -v` → 1 passed
- `docker build -f infra/docker/Dockerfile -t advance-rag:ci .` → built successfully

### How this connects to the pipeline
**Previous:** Step 1.1 gave us a pinned environment (`pyproject.toml`) and a working
containerized stack (`Dockerfile` + `docker-compose.yml`), verified by hand.
**This step:** automates re-verifying "does the environment still install, does the code
still pass style/type checks, do the tests still pass, does the image still build" on every
push — so drift gets caught by GitHub, not by you remembering to check manually.
**Next:** once real ingestion/retrieval code exists, `tests/` grows real unit tests (chunking
correctness, retrieval relevance) and RAGAS-based evaluation tests, and this same `test` job
picks them up automatically — no CI changes needed, just more files under `tests/`.

### How to do this yourself (reproduce without me)
1. Decide your lint/format/type-check tool(s) — check what's already pinned in your
   `pyproject.toml`'s dev group before adding new ones (avoid redundant tools doing the same
   job, like both black and ruff's formatter).
2. For each tool, run it locally first (`ruff check .`, `ruff format --check .`, `mypy src/`)
   and read what it reports on your actual code — don't write the CI step blind.
3. In `.github/workflows/ci.yml`, give each concern (lint, test, build) its own `jobs:` key
   so they run independently. Each job needs its own `actions/checkout@v4` and
   `actions/setup-python@v5` — jobs don't share filesystem state.
4. Use `cache: 'pip'` on `actions/setup-python` to speed up repeat runs (caches downloaded
   wheels between workflow runs, keyed off your dependency files).
5. For the Docker validation job, remember `-f` needs the Dockerfile's *actual* path (check
   where you put it), and the build context (last positional argument) needs to be wherever
   your Dockerfile's `COPY` instructions expect to find files relative to.
6. Push to a branch and open a PR (or push directly if solo) — watch the Actions tab on
   GitHub to see all three jobs run and report pass/fail independently.

### Practice exercises
1. Intentionally break formatting (`ruff format .` then manually add inconsistent spacing to
   one file) and push — watch the `lint` job fail while `test` and `build` still pass
   independently, proving job isolation.
2. Add a `needs: lint` key to the `test` job so tests only run after lint passes — push a
   deliberately-broken lint commit and confirm `test` shows as skipped, not failed.
