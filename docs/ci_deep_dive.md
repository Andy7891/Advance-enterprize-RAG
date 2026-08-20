# CI Deep Dive: Reverse-Engineering `ci.yml` + the ruff/mypy Config

This is a self-contained learning path for Step 1.2:
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) and the
`[tool.ruff]`/`[tool.mypy]` sections of [`pyproject.toml`](../pyproject.toml).

**How to use this doc:** for each concept, attempt the exercise yourself — actually run the
commands, actually push a branch where a push is called for — form your own explanation,
*then* read the checkpoint. Don't peek first.

---

## 0. High-level summary + concept map

`ci.yml` defines a GitHub Actions **workflow**: a set of automated jobs GitHub runs on its
own servers whenever you `push` or open a `pull_request` against `main`/`master`. It has
three **jobs** (`lint`, `test`, `build`), each independent, each proving a different claim
about the state of the repo: "the code is styled/typed correctly," "the tests pass," "the
Docker image still builds." `pyproject.toml`'s `[tool.ruff]`/`[tool.mypy]` sections
configure *how strict* two of those checks are.

Think of your GitHub repository as a factory manufacturing a car, and this whole process as the quality control inspector.🚗 The Analogy Breakdownci.yml (The Quality Control Checklist): This is a master checklist. It says, "Every time a new car design is submitted, we must run three specific tests before allowing it on the road."GitHub Actions Workflow (The Automated Factory Line): This is the automated system that executes the checklist. Nobody has to manually test the car; the factory line does it automatically in the cloud.push / pull_request (The Triggers): This is the action of submitting your work. Whenever you save new code (push) or ask to merge it into the main project (pull_request), the factory line automatically starts.The 3 Jobs (The Independent Test Stations):lint Station: Checks the look and structure. Is the car painted correctly? Are the screws tight? (Checks if code looks neat and has no obvious typos).test Station: Checks the functionality. Does the engine start? Do the brakes work? (Runs code tests).build Station: Checks assembly. Can we successfully package this car into a box to ship it? (Creates the Docker image).pyproject.toml (The Factory Rulebook): This file sets the exact rules for the stations. For example, it tells the lint station exactly how picky to be about the spacing, or how strict to be about code rules (ruff and mypy).🛠️ Summary of the RelationshipYou send code to GitHub.ci.yml wakes up and starts three automated test stations (lint, test, build).These stations use the rules written in pyproject.toml to decide if your code passes or fails.

Concept map, in dependency order:

```
Workflow structure:  triggers (on:) → jobs → runners (runs-on) → steps
Step mechanics:       actions (uses:) vs raw commands (run:) → the checkout action
                       → setup-python + dependency caching → editable installs in CI
Tool-specific:         ruff check vs ruff format --check vs mypy — three different questions
                       → non-mutating "--check" flags as a general CI pattern
Job relationships:     job isolation (no shared filesystem) → job parallelism →
                       optional `needs:` for sequencing
Config-as-code:        [tool.ruff]/[tool.mypy] in pyproject.toml — tool config that
                       travels with the repo instead of living only in CI YAML
```
Understand "workflow structure" before "tool-specific" — the exercises assume that order.

---

## 1. Concept-by-concept

---

### 1.1 Triggers (`on: push` / `on: pull_request`)

**Definition:** the `on:` block declares which GitHub events cause this workflow to run.

**Why here:** `push` to `main`/`master` catches direct commits (including yours, right now,
working solo); `pull_request` targeting those branches means checks run *before* a merge, on
whatever branch is proposing the change — this is what lets GitHub show a red/green check
on a PR before you click merge.

**What breaks if changed:** delete the `pull_request:` block entirely — pushing directly to
`main` still triggers CI, but opening a PR from a feature branch would show no checks at
all, defeating the "catch problems before merge" purpose.

**Canonical reading:** [GitHub Actions: events that trigger workflows](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows)

**Micro-exercise (10 min):**
Create a new branch, push it (don't open a PR yet), and check the Actions tab on GitHub —
does anything run? Then open a PR from that branch — check again.

**Checkpoint:**
*Why does pushing a branch with no PR open sometimes NOT trigger the workflow, even though
`on: push` has no branch filter restricting it — wait, look again at the actual YAML: does
`push:` here have a branch filter?*

<details><summary>Reveal</summary>
Yes — `branches: [ main, master ]` under `push:` means the push trigger only fires for
commits landing directly ON those branches, not on an arbitrary feature branch. Pushing a
feature branch by itself triggers nothing; only opening a PR *against* main/master triggers
the separate `pull_request` event, which has its own `branches:` filter (meaning "target"
branch, not source). This distinction — "push filter is about which branch received a
commit" vs "PR filter is about which branch the PR targets" — is a common source of "why
didn't my CI run" confusion.
</details>

---

### 1.2 Jobs (`jobs: lint: / test: / build:`)

**Definition:** a job is a group of steps that all execute together, sequentially, on one
runner instance. Multiple jobs in one workflow run in parallel by default, on separate
runner instances.

**Why here:** `lint`, `test`, `build` are three genuinely independent questions about the
repo's state — none needs the others' output to run its own checks. Splitting them means
GitHub can schedule all three simultaneously (faster wall-clock feedback) and report each
pass/fail separately in the PR UI, instead of one monolithic "CI" status that doesn't tell
you *which* thing failed without opening logs.

**What breaks if changed:** merge all three into one `ci:` job with all steps sequential —
functionally the checks still all run, but now a lint failure (which by default aborts the
rest of that job's steps) would *prevent* the test step from running at all, hiding whether
tests would have passed or failed. You'd also lose parallelism — total wall-clock time
becomes the *sum* of all three jobs instead of the *max*.

**Canonical reading:** [GitHub Actions: jobs](https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow)

**Micro-exercise (10 min):**
In the Actions tab after a push, look at the visual graph GitHub draws for the workflow run
— are `lint`, `test`, `build` shown side-by-side or in a chain? Note their individual start
timestamps in the logs — do they overlap?

**Checkpoint:**
*If `lint` takes 90 seconds and `test` takes 60 seconds and `build` takes 45 seconds, and
they run as three separate jobs with no `needs:`, roughly how long until the whole workflow
shows as complete — 195 seconds, or something closer to 90?*

<details><summary>Reveal</summary>
Closer to 90 seconds (plus a little scheduling overhead) — since they run on independent
runners in parallel, total time is dominated by the *slowest* job, not the sum of all three.
This is the entire point of separating them.
</details>

---

### 1.3 Runners (`runs-on: windows-latest`)

**Definition:** the OS/environment GitHub provisions a fresh VM from, to execute a job's
steps — a clean machine every single run, discarded afterward.

**Why here:** kept as `windows-latest` deliberately (a call you made) to match local dev OS,
even though Linux runners are more typical for Python+Docker CI (faster boot, Docker runs
natively rather than through Docker Desktop's Linux-VM layer).

**What breaks if changed:** switch `build`'s `runs-on` to `ubuntu-latest` while `lint`/`test`
stay on `windows-latest` — nothing breaks, actually; each job can independently choose its
own runner OS. This is worth trying explicitly since it's non-obvious that jobs don't need
matching runners.

**Canonical reading:** [GitHub Actions: runners](https://docs.github.com/en/actions/using-github-hosted-runners/about-github-hosted-runners)

**Micro-exercise (10 min):**
Temporarily change just the `build` job's `runs-on` to `ubuntu-latest`, push, and compare
that job's total duration in the Actions log against a previous run's `build` job duration
on `windows-latest`.

**Checkpoint:**
*Is there any requirement that all jobs in one workflow use the same runner OS?*

<details><summary>Reveal</summary>
No — `runs-on` is a per-job setting. Each job gets its own fresh, independent VM, so you can
freely mix `ubuntu-latest`, `windows-latest`, `macos-latest` across jobs in the same
workflow file if each job's needs differ.
</details>

---

### 1.4 Steps: `uses:` (actions) vs `run:` (raw commands)

**Definition:** a step either calls a reusable, packaged **action** (`uses: owner/repo@ref`)
or runs shell commands directly (`run: |`) on the runner's native shell.

**Why here:** `actions/checkout@v4` and `actions/setup-python@v5` are official GitHub
actions doing non-trivial setup work (cloning the right commit with correct git config;
downloading/caching/activating a specific Python version) that would take many fragile
manual shell lines to replicate — `uses:` steps are for "someone already solved this
generic problem well." `run:` steps are for the project-specific commands only you know
(`pip install -e ".[dev]"`, eventually `ruff check`, etc.).

**What breaks if changed:** delete `actions/checkout@v4` entirely — every subsequent step
runs against an *empty* runner filesystem (no repo checked out at all); `pip install -e
".[dev]"` would fail immediately with "no such file: pyproject.toml."

**Canonical reading:** [GitHub Actions: workflow syntax — jobs.<job_id>.steps](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#jobssteps)

**Micro-exercise (10 min) — prove checkout is load-bearing:**
Duplicate the `test` job as a throwaway `test_broken` job, remove its `actions/checkout@v4`
step, push to a scratch branch, and read the exact failure message from the
`pip install -e ".[dev]"` step.

**Checkpoint:**
*Why does removing `setup-python` (but keeping `checkout`) NOT necessarily fail immediately
— what would you expect to happen instead, given GitHub-hosted runners come with some
default tools pre-installed?*

<details><summary>Reveal</summary>
GitHub's runner images ship with several Python versions pre-installed as a baseline, so
`python`/`pip` commands would likely still resolve to *some* version — just not
deterministically pinned to 3.11 the way `setup-python` with `python-version: '3.11'`
guarantees. This is a subtler bug than a hard failure: things "work" until a runner image
update changes the default Python version out from under you.
</details>

---

### 1.5 `actions/setup-python` + `cache: 'pip'`

**Definition:** downloads/activates a specific Python version on the runner; `cache: 'pip'`
additionally caches pip's downloaded packages between workflow runs, keyed by a hash of your
dependency files.

**Why here:** `python-version: '3.11'` matches the `requires-python` constraint pinned in
`pyproject.toml` back in Step 1.1 — CI testing against a different Python version than your
local dev/Docker environment would be testing something you don't actually ship. `cache:
'pip'` exists purely for speed: without it, every single workflow run re-downloads the
entire LangChain dependency tree (dozens of packages) from PyPI from scratch.

**What breaks if changed:** set `python-version: '3.10'` instead — recall `pyproject.toml`
declares `requires-python = ">=3.11,<3.12"`; `pip install -e .` would fail on that runner
with a Python-version-incompatible error, *before* your code is even tested — a good example
of a config mismatch between two files catching itself automatically.

**Canonical reading:** [actions/setup-python README](https://github.com/actions/setup-python) (see "caching packages" section)

**Micro-exercise (10 min):**
Change `python-version: '3.11'` to `'3.10'` on a scratch branch, push, and read the exact
pip/setuptools error in the Actions log.

**Checkpoint:**
*What does `cache: 'pip'` actually use as the cache key — does it cache forever regardless
of what changes?*

<details><summary>Reveal</summary>
No — it's keyed off a hash of your dependency declaration files (for a `pyproject.toml`-based
project, effectively that file's content). Change a single pinned version and the cache key
changes, so the *next* run downloads fresh (cache miss) and then saves a new cache entry for
future runs. It's not a permanent cache — it invalidates exactly when your dependencies do.
</details>

---

### 1.6 Editable install in CI (`pip install -e ".[dev]"`)

**Definition:** `-e` installs the local package in "editable" mode (a link to source, not a
copied build artifact); `.[dev]` means "this project, plus its `dev` optional-dependency
group."

**Why here:** CI needs the exact same `dev` tools (ruff, mypy, pytest) Step 1.1 defined —
using `.[dev]` instead of hand-listing `pip install ruff mypy pytest` keeps one single
source of truth (`pyproject.toml`) instead of two lists that can drift out of sync. `-e`
(editable) doesn't matter much for correctness in CI (nothing else imports this package as
a dependency here), but it's the same command you'd run locally, which is deliberately
consistent.

**What breaks if changed:** run plain `pip install .` (no `[dev]`) — `pytest`, `ruff`, and
`mypy` wouldn't be installed at all, and the very next step would fail with "ruff: command
not found."

**Canonical reading:** [pip docs: editable installs](https://pip.pypa.io/en/stable/topics/local-project-installs/#editable-installs) and [PEP 621: optional-dependencies](https://peps.python.org/pep-0621/#dependencies-optional-dependencies)

**Micro-exercise (5 min):**
Temporarily change `pip install -e ".[dev]"` to `pip install -e .` in the `lint` job only,
push to a scratch branch, watch the `Ruff lint + format check` (or whatever step you've
filled in) step fail with a "command not found"-style error.

**Checkpoint:**
*Why does this same mistake NOT break the `build` job?*

<details><summary>Reveal</summary>
Because the `build` job never installs Python dependencies at all — it only runs `docker
build`, which installs everything fresh *inside* the Docker build process (recall Step
1.1's Dockerfile `RUN pip install ...`), completely independent of whatever `pip install`
commands run directly on the GitHub Actions runner in other jobs. The `build` job and the
`lint`/`test` jobs don't share any Python environment — proof of job isolation from section
1.2, concretely.
</details>

---

### 1.7 `ruff check` vs `ruff format --check` — two different questions

**Definition:** `ruff check` is **linting** — static analysis for bugs/style violations
(unused imports, undefined names, etc.); `ruff format --check` is **formatting
verification** — reports whether files' whitespace/quoting/line-breaking would change under
Ruff's formatter, without changing them.

**Why here:** these are genuinely different concerns even though one tool now does both.
`ruff check` can pass on a file that's still "ugly" (inconsistent quote style, extra blank
lines) as long as it has no actual style-rule violations from the `select = ["E", "F",
"W"]` set; `ruff format --check` is a much stricter, opinionated, all-or-nothing
"does this match the canonical formatting" check.

**What breaks if changed:** run only `ruff check` and skip `ruff format --check` entirely —
you'd still catch real bugs (unused imports, etc.) but lose the guarantee that everyone's
code looks consistent — two contributors could write functionally-identical, `ruff
check`-passing code with wildly different formatting, and CI wouldn't flag it.

**Canonical reading:** [Ruff docs: linter vs formatter](https://docs.astral.sh/ruff/formatter/) and [Ruff rules reference](https://docs.astral.sh/ruff/rules/)

**Micro-exercise (10 min) — feel the difference directly:**
1. In a scratch `.py` file, write `import os` and never use `os` — run `ruff check` on it
   (should flag F401 unused import) and separately `ruff format --check` on it (should pass
   fine — unused imports don't affect formatting).
2. Now write a syntactically fine but oddly-formatted file (e.g., `x=1;y  =   2`) — run
   `ruff check` (may or may not flag anything depending on selected rules) vs `ruff format
   --check` (will flag it).

**Checkpoint:**
*Give one example of a file that would fail `ruff check` but pass `ruff format --check`,
and one that would do the reverse.*

<details><summary>Reveal</summary>
Fails check, passes format: a perfectly-formatted file with an unused import (F401) —
formatting is fine, but there's a real lint violation. Fails format, passes check: a
correctly-logic'd file with inconsistent quote styles or extra blank lines but no actual
lint-rule violations in your `select` set — nothing "wrong" code-wise, just not
canonically formatted.
</details>

---

### 1.8 `mypy` — static type checking (a third, separate question)

**Definition:** analyzes type annotations (`def f(x: int) -> str`) without running the
code, and reports where inferred/declared types don't line up.

**Why here — and why it's config'd `strict = false`:** none of `ruff check` or `ruff format`
look at types at all — a function that returns the wrong type, or calls another function
with mismatched argument types, sails through both completely clean. `strict = false` in
`pyproject.toml` is a deliberate, temporary leniency: strict mode requires type hints
basically everywhere, and this codebase is only a few files old — flipping it on now would
produce a wall of errors about *missing* annotations rather than genuine type bugs, drowning
out signal. `ignore_missing_imports = true` separately suppresses a different, unrelated
noise source: third-party packages that ship no type stubs.

**What breaks if changed:** flip `strict = true` right now and run `mypy src/` — expect a
burst of new errors, almost entirely "missing type annotation" complaints rather than actual
type mismatches, because most functions in this early codebase have partial or no
annotations yet.

**Canonical reading:** [mypy docs: getting started](https://mypy.readthedocs.io/en/stable/getting_started.html) and [mypy: strict mode](https://mypy.readthedocs.io/en/stable/existing_code.html#introduce-stricter-options)

**Micro-exercise (10 min) — see mypy catch something ruff can't:**
```python
# scratch_type_bug.py
def get_port(settings_value: str) -> int:
    return settings_value  # returns a str where int was promised
```
Run `ruff check scratch_type_bug.py` (passes clean) then `mypy scratch_type_bug.py`
(should report a return-type mismatch).

**Checkpoint:**
*Why doesn't `ruff check` catch this obviously-wrong return type, when it catches other
"obviously wrong" things like unused variables?*

<details><summary>Reveal</summary>
Ruff's checks are pattern-based static analysis (AST inspection for known bad patterns) —
it doesn't build or reason about a full type system. Catching "this function's return type
annotation doesn't match what it actually returns" requires type inference across the whole
call graph, which is specifically what a dedicated type checker like mypy does and a linter
like ruff, by design, does not attempt.
</details>

---

### 1.9 Non-mutating `--check`/verify flags as a general CI pattern

**Definition:** many formatting/fixing tools ship two modes: one that *reports* what would
change (`--check`, `--dry-run`), and one that actually rewrites files.

**Why here:** every check in this workflow (`ruff format --check`, implicitly `ruff check`
without `--fix`) is deliberately the non-mutating variant. CI's job is to *verify* a
property holds and fail loudly if not — never to silently "fix" your code and let a broken
commit merge with different content than what you pushed.

**What breaks if changed:** swap `ruff format --check` for plain `ruff format` (mutating) in
the CI step — the step would "succeed" (files get reformatted, exit code 0) even on
previously-unformatted code, giving you a false-green check that hides the fact your
committed code wasn't actually formatted correctly — and the runner's rewritten files never
even make it back into your repo (the runner is thrown away after the job), so you'd get a
green check that's lying about the state of your actual commit.

**Canonical reading:** [Ruff CLI docs: format command](https://docs.astral.sh/ruff/formatter/#ci-integration) — explicitly documents the CI `--check` pattern.

**Micro-exercise (5 min) — prove the false-green trap:**
On a scratch branch, deliberately mis-format a file, then locally run plain `ruff format
<file>` (mutating) followed immediately by `ruff format --check <file>` — see the second
command now pass (because the first one already fixed it, right there on your machine,
which is fine to do *locally* but is exactly what you must NOT rely on in CI).

**Checkpoint:**
*If your CI step used mutating `ruff format` instead of `--check`, and it "fixed" a file
during the run, does that fix make it into your git history at all?*

<details><summary>Reveal</summary>
No — the runner's filesystem is thrown away after the job finishes. Any file changes made
during a CI run are invisible to your repo unless a separate step explicitly commits and
pushes them back (a pattern some workflows do use deliberately, but it's not what's
happening here, and doing it accidentally via a mutating command is the exact anti-pattern
this concept warns against).
</details>

---

### 1.10 `docker build` as a CI validation job (not an integration test)

**Definition:** the `build` job runs `docker build` against the Dockerfile from Step 1.1,
purely to confirm the image *can* be constructed — it never runs the resulting container.

**Why here:** this catches a specific, real class of bug: someone changes `pyproject.toml`
(bumps a version, adds a package) without noticing the Dockerfile's `pip install` step now
fails, or restructures `src/` in a way that breaks a `COPY` path — bugs that `lint`/`test`
jobs, which never touch Docker at all, would completely miss.

**What breaks if changed:** delete this job — `lint` and `test` could both stay green while
the Dockerfile is silently broken (e.g., a typo'd package version in `pyproject.toml` that
somehow still resolves locally via cache but fails on a clean `docker build`) — you'd only
discover it the next time you or a reviewer manually tried `docker compose build`.

**Canonical reading:** [Docker docs: docker build CLI reference](https://docs.docker.com/reference/cli/docker/buildx/build/)

**Micro-exercise (10 min) — reproduce a Dockerfile break that lint/test wouldn't catch:**
On a scratch branch, add a nonexistent package to `pyproject.toml`'s `dependencies`
(e.g., `"totally-fake-package-xyz==1.0.0"`), push, and check: does `lint` job fail? Does
`test` job fail? Does `build` job fail?

**Checkpoint:**
*Why would `lint` and `test` most likely still pass green in that scenario, even though the
project is now genuinely broken for anyone trying to build the Docker image?*

<details><summary>Reveal</summary>
Because `lint` and `test` only run `pip install -e ".[dev]"` against packages already cached
by `setup-python`'s `cache: 'pip'` from a previous successful run, or freshly resolve from
PyPI directly on the runner — if the fake package genuinely doesn't exist on PyPI, actually
BOTH would fail too (pip install fails for everyone identically, cache or not). Retry the
exercise with a real-but-broken scenario instead: pin an existing package to a version that
conflicts with another dependency (recreating Step 1.1's `pydantic-settings` conflict on
purpose) — THEN you'd see `lint`/`test` fail at the `pip install` step exactly like `build`
would fail at its own `pip install` inside the Dockerfile — proving all three jobs actually
share the same underlying fragility to a bad pin, just discovered independently, in
parallel, rather than gating each other.
</details>

---

### 1.11 `[tool.ruff]` / `[tool.mypy]` in `pyproject.toml` — config as code

**Definition:** tool configuration embedded in the same file as your dependencies, rather
than scattered across separate config files (`.flake8`, `setup.cfg`, `mypy.ini`) or, worse,
hardcoded as CLI flags inside the CI YAML itself.

**Why here:** `line-length = 100`, `target-version = "py311"`, `select = ["E", "F", "W"]`
for ruff, and `strict = false` / `ignore_missing_imports = true` for mypy are settings you
want identical whether you run these tools locally in your terminal, in a pre-commit hook
(future step), or in CI — putting them in `pyproject.toml` means one source of truth instead
of duplicating `--line-length 100` as a flag in three different places that could drift.

**What breaks if changed:** delete `[tool.ruff]` entirely — `ruff check`/`ruff format` still
run, just against Ruff's own built-in defaults (which differ from what we chose — e.g.
default line length is 88, not 100) — nothing errors, but local runs and CI runs both
silently follow different assumptions than what NOTES.md documents, if you'd separately told
yourself "we use 100."

**Canonical reading:** [Ruff docs: configuration discovery](https://docs.astral.sh/ruff/configuration/) and [mypy docs: config file](https://mypy.readthedocs.io/en/stable/config_file.html)

**Micro-exercise (5 min):**
Comment out the entire `[tool.ruff]` and `[tool.ruff.lint]` blocks in `pyproject.toml`, run
`ruff check src/` locally, and diff the output against a run with the config restored — do
different violations get flagged?

**Checkpoint:**
*If you ran `ruff format --check` from a completely different directory outside this repo,
against a copy of one of our source files, would it use our `line-length = 100` setting?*

<details><summary>Reveal</summary>
No — Ruff discovers config by walking up from the current/target file looking for a
`pyproject.toml` (or `ruff.toml`) in an ancestor directory. Outside this repo entirely,
there's no such file to find, so it silently falls back to Ruff's built-in defaults. This is
worth internalizing: tool config is *discovered contextually*, not "installed" globally with
the tool itself.
</details>

---

## 2. Validating it's ACTUALLY working (not just "no errors")

Local validation (run these yourself, right now, in the project root):

| What to check | Command | What "working" looks like |
|---|---|---|
| Ruff finds real violations | `ruff check src/ tests/` | Currently `All checks passed!` — verify it CAN report a problem: temporarily add `import os` unused to any file and rerun |
| Ruff format check is meaningful | `ruff format --check src/ tests/` | Currently reports 3 files would reformat — run `ruff format src/ tests/` (mutating) then rerun `--check`, confirm it now passes |
| mypy is actually type-checking, not just running | `mypy src/` | Currently `Success: no issues found in 5 source files` — use the `scratch_type_bug.py` exercise from 1.8 to prove it catches real errors |
| pytest collects the right tests | `pytest tests/ -v --collect-only` | Lists `test_health_endpoint_returns_ok` — add a second dummy test function and rerun to confirm collection scales |
| Docker build reproduces what CI would do | `docker build -f infra/docker/Dockerfile -t advance-rag:ci .` | Exits 0, final line `naming to docker.io/library/advance-rag:ci` |

Validating the **GitHub Actions run itself** (not just the local equivalent):

1. **Read the raw logs, not just the green checkmark.** Open a workflow run on GitHub → click
   a job → expand each step. Confirm the actual command text that ran (after your TODOs are
   filled in) matches what you intended — a `run:` step with a typo can still exit 0 if the
   typo happens to be a valid no-op command.
2. **Check the job summary duration and compare to local timing** — if a step takes
   dramatically longer in CI than locally, that's often a sign `cache: 'pip'` isn't hitting
   (cold cache) or the runner is under-provisioned relative to your machine.
3. **Deliberately fail each job once, on a scratch branch, and read GitHub's PR-level status
   check UI** — this is what a collaborator (or future-you on a PR) actually sees; confirm
   it's legible enough to know *what* failed without opening logs.
4. **Verify job isolation for real:** add a temporary `run: echo "marker" > /tmp/proof.txt`
   step to `lint`, and a temporary `run: cat /tmp/proof.txt` step to `test` — confirm `test`
   fails to find the file, proving jobs truly don't share filesystem state even within the
   same workflow run.

**Failure-mode drills** (predict the outcome before running):

1. Push a commit where `pyproject.toml` and `requirements.txt` (from Step 1.1) have drifted
   — bump a version in one but not the other — does any current job catch this
   inconsistency? (Answer: no — worth noting as a real gap, since none of `lint`/`test`/
   `build` currently reads `requirements.txt` at all.)
2. Introduce a merge conflict marker (`<<<<<<< HEAD`) into a Python file by hand and push —
   which job fails first, and with what kind of error (syntax error, not a lint/type
   violation)?
3. Rename `tests/test_health.py` to `tests/test_health.txt` (wrong extension) and push —
   does `pytest` silently collect 0 tests (exit 5, real failure) or error more loudly?

---

## 3. Visualizing it working

**Static structure, as this workflow actually runs it** (not a generic CI diagram):

```
git push / PR opened
        │
        ▼
   GitHub Actions picks up .github/workflows/ci.yml
        │
        ├─────────────────┬─────────────────┐
        ▼                 ▼                 ▼
   ┌─────────┐       ┌─────────┐       ┌─────────┐
   │  lint   │       │  test   │       │  build  │      (3 separate runner VMs,
   │windows- │       │windows- │       │windows- │       started in parallel,
   │ latest  │       │ latest  │       │ latest  │       no shared filesystem)
   └────┬────┘       └────┬────┘       └────┬────┘
        │                 │                 │
   checkout          checkout          checkout
        │                 │                 │
   setup-python      setup-python      (no python setup —
   (3.11, pip         (3.11, pip        docker build installs
    cache)             cache)            its own deps INSIDE
        │                 │              the image build)
   pip install -e    pip install -e          │
   ".[dev]"          ".[dev]"           docker build -f
        │                 │             infra/docker/Dockerfile
   ruff check        pytest tests/           │
   ruff format             │             (validates Step 1.1's
   --check                 │              Dockerfile still builds)
        │                 │                 │
   mypy src/               │                 │
        │                 │                 │
        ▼                 ▼                 ▼
   pass/fail          pass/fail          pass/fail
   (independent, shown separately on the commit/PR)
```

**Tools worth using for deeper visibility:**
- **The Actions tab's visual graph** — click any workflow run; GitHub draws the job
  dependency graph automatically (flat/parallel here, since we have no `needs:`). Compare
  this to what it would look like if you complete practice exercise #2 from the previous
  turn (adding `needs: lint` to `test`) — the graph literally redraws to show a chain
  instead of three parallel lanes.
- **`gh run view --log` (GitHub CLI)** — if you have `gh` installed, `gh run list` then `gh
  run view <run-id> --log` pulls the full raw log locally, useful for grepping through a
  long run without clicking through the web UI.
- **Re-running a single job** — GitHub's UI lets you re-run just `lint` (say) without
  re-running `test`/`build` — a concrete, visible demonstration that they're genuinely
  independent, not just independently-configured-but-secretly-coupled.

**One end-to-end "watch it work" exercise, tying validation + visualization together:**

1. Fill in all four TODOs in `ci.yml` for real (`ruff check src/ tests/`, `ruff format
   --check src/ tests/`, `mypy src/`, `pytest tests/ -v`, and the `docker build` command).
2. Run every single one of those commands **locally first** and confirm they pass (fix the
   3 formatting violations with `ruff format src/ tests/` if you haven't already).
3. Commit and push to a scratch branch, then open a PR against `main` (even if you plan to
   merge it yourself) — this is what surfaces the PR-level status-check UI, not just the
   Actions tab.
4. Watch the PR page: you should see three separate check entries appear
   (`lint`, `test`, `build`), each transitioning from a yellow "in progress" dot to a green
   check, **independently and at different times** — visual, undeniable proof of the
   parallelism from section 1.2.
5. Click into the `lint` job's log while it's mid-run (or after) and find the exact line
   where `ruff format --check` prints which files it examined — cross-reference that file
   list against `src/` and `tests/`'s actual contents, confirming the tool touched what you
   expect and nothing outside those directories.
6. Now deliberately break something small (add one unused import) in a new commit on the
   same branch, push again, and watch only `lint` go red while `test` and `build` — from
   the *same* commit — stay green. That's the single clearest possible demonstration that
   these are three separate, independent claims about your code, not one monolithic "CI
   passed" signal.

If you can narrate, from memory, exactly which job would catch each of these bugs — a
type mismatch, an unused import, a failing unit test, a broken Dockerfile, a Python-version
mismatch — you've internalized this step for real, not just "it went green."
