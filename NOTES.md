# Project Notes

Running log of what was built, why, and how to reproduce it. Newest entries at the bottom.

---

## Step 1.1 — Project Initialization: `pyproject.toml` with pinned dependencies

**Date:** 2026-08-19

### What was built
- Rewrote `pyproject.toml` as the single source of truth for dependencies, using PEP 621
  (`[project]` table) with real, pinned, resolvable versions.
- Split dependencies into two groups:
  - `dependencies` (core, always installed): LangChain, Neo4j driver, Qdrant client, Cohere,
    FastAPI + Uvicorn, Pydantic, Streamlit, dotenv, httpx, requests.
  - `[project.optional-dependencies].dev`: pytest, pytest-asyncio, ragas, ruff, docker SDK.
    Installed via `pip install -e ".[dev]"` — not pulled in for a plain runtime install.
- Regenerated `requirements.txt` as a plain mirror of the core deps (some CI/Docker steps
  read `requirements.txt` directly rather than resolving `pyproject.toml`).
- Fixed `.github/workflows/ci.yml` to use Python 3.11 (was pinned to 3.10, out of sync).
- Recreated `.venv` using Python 3.11.3 (previous venv was 3.10.11, incompatible with the
  new `requires-python = ">=3.11,<3.12"` constraint).

### Why this approach (and what was wrong before)
The repo already had a `pyproject.toml`, but it had several issues:
- `[tool.poetry.scripts]` was present even though the build backend was plain `setuptools`,
  not Poetry — that section was dead/inert.
- Both `qdrant-client` and `pinecone-client` were listed, even though we're only using Qdrant
  (per the stack decision) — unnecessary dependency surface.
- `httpx==0.24.1` was stale (current is `0.28.1`).
- No dev/test dependency group — installing the app for runtime use would also install
  pytest, ragas, etc., which nobody deploying the app actually needs.

I initially *suspected* several version numbers (`fastapi==0.141.1`, `streamlit==1.61.1`,
`cohere==7.0.8`, etc.) were hallucinated, because they looked implausibly high compared to
what I remembered. I was wrong — I checked with `pip index versions <pkg>` and all of them
are real, current releases; my own training data was just stale. **Lesson embedded in the
process itself: don't trust a version number from memory — verify against the package index.**

**Alternatives considered:**
- `requirements.txt` only, no `pyproject.toml` — simpler, but no project metadata, no
  dependency groups, considered legacy for new projects.
- Poetry / PDM — nicer resolver UX and lockfiles, but adds a required tool; plain pip +
  `pyproject.toml` is more transparent for a learning project and installs anywhere with
  just `pip`.
- `uv` — much faster installer, also reads `pyproject.toml` directly — worth trying as a
  drop-in replacement for `pip install` later; doesn't change what's written in the file.

### Key concepts used
- **`pyproject.toml` (PEP 518/621)** — a single, standard config file for project metadata,
  build system, and dependencies, replacing the old `setup.py` + `requirements.txt` split.
- **Pinned versions (`==`)** — exact versions so `pip install` produces the *same*
  environment on any machine, at any point in the future, instead of drifting as packages
  release new versions.
- **Optional dependency groups (`[project.optional-dependencies]`)** — a way to separate
  "needed to run the app" from "needed to develop/test the app," so a production install
  stays lean.
- **Editable install (`pip install -e .`)** — installs the package in a mode where source
  edits take effect immediately without reinstalling, standard for active development.

### How this connects to the pipeline
This is step 1.1: environment setup. Nothing here does RAG work yet — it's the foundation
every later step (ingestion, embeddings, retrieval, API, UI) depends on being reproducible.
**Previous:** nothing — this is the first step.
**Next:** 1.2 will likely set up `docker-compose.yml` to run Qdrant + Neo4j locally, and
wire environment variables (`.env` + `pydantic-settings`) for API keys.

### How to do this yourself (reproduce without me)
1. Decide core vs dev dependencies for your stack.
2. For each package, look up the real current version:
   ```bash
   pip index versions <package-name>
   ```
   (This is an experimental pip command but works for checking what's actually on PyPI.)
3. Write `pyproject.toml` with a `[build-system]` table (setuptools is the simplest default),
   a `[project]` table with `dependencies = [...]`, and
   `[project.optional-dependencies]` for dev-only tools.
4. Check your installed Python version against `requires-python` in the file
   (`python --version`); if they don't match, either adjust the constraint or install the
   right Python version (on Windows, `py -0` lists installed versions, `py -3.11` runs a
   specific one).
5. Create the venv: `py -3.11 -m venv .venv`, activate it
   (`.venv\Scripts\activate` on Windows), then `pip install -e ".[dev]"`.
6. Keep `requirements.txt` in sync manually if any tooling (CI, Docker) reads it directly
   instead of `pyproject.toml`.

### What actually happened when installing (real dependency conflicts)
Pinning exact versions for *your own* dependencies doesn't protect you from conflicts with
what those dependencies themselves require. Two showed up on the first install attempt:

1. `pydantic-settings==2.7.1` conflicted with `langchain-community==0.3.30`, which requires
   `pydantic-settings>=2.10.1`. Fixed by bumping to the real current release, `2.15.0`.
2. `pytest-asyncio==0.25.2` requires `pytest<9`, but we'd pinned `pytest==9.1.1`. Fixed by
   bumping `pytest-asyncio` to the current release, `1.4.0`, which supports pytest 9.

Both were caught immediately by `pip`'s resolver (`ResolutionImpossible` errors that name
the exact conflicting packages) rather than failing silently later — this is the resolver
doing its job. Final environment: **Python 3.11.3**, all 89 packages installed cleanly,
verified with `python -c "import langchain, fastapi, qdrant_client, neo4j, cohere,
streamlit, pydantic_settings, pytest, ragas"`.

### Practice exercises
1. Try `pip install -e ".[dev]"` vs `pip install .` in two separate venvs and run
   `pip list` in each — see exactly which packages the `dev` extra pulled in.
2. Deliberately mistype a version (e.g. `fastapi==0.999.0`) and run `pip install -e .` —
   read the resolver error message carefully; this is the failure mode pinning protects
   you from catching *late* instead of *immediately*.

---

## Step 1.1 (cont'd) — Dockerfile + docker-compose orchestration

**Date:** 2026-08-19

### What was built
- [infra/docker/Dockerfile](infra/docker/Dockerfile) — multi-stage build for the FastAPI
  app: `builder` stage installs the package into an isolated prefix, `runtime` stage copies
  only the installed packages + source, runs as a non-root user, exposes port 8000.
- [docker-compose.yml](docker-compose.yml) — orchestrates four services:
  - `qdrant` (`qdrant/qdrant:v1.19.0`, version-matched to our pinned `qdrant-client`)
  - `neo4j` (`neo4j:5.26.0`, an LTS release)
  - `ollama` (`ollama/ollama:0.20.0`, CPU-only — no GPU passthrough configured)
  - `api` (built from the Dockerfile above)
  All stateful services get named volumes; all services get healthchecks; `api` uses
  `depends_on: condition: service_healthy` so it never starts before its dependencies can
  actually accept connections.
- [.dockerignore](.dockerignore) — keeps `.venv`, `__pycache__`, tests, docs, data etc. out
  of the build context (faster builds, no accidental secrets in the image).
- [.env.example](.env.example) — documents the env vars compose reads (`NEO4J_USER`,
  `NEO4J_PASSWORD`, `COHERE_API_KEY`); copy to `.env` and fill in for real use.
- Minimal FastAPI scaffold to make the Dockerfile buildable and testable:
  [src/api/main.py](src/api/main.py) (a `/health` endpoint only — no RAG endpoints yet,
  those come in later steps) and [src/core/config.py](src/core/config.py) (a
  `pydantic-settings` `Settings` class reading service URLs from env vars, defaulting to
  the Docker Compose service names).
- [README.md](README.md) — had to create a minimal placeholder because `pyproject.toml`
  declared `readme = "README.md"` but the file didn't exist, which would have broken
  `pip install .` inside the Docker build entirely.

### Why this approach (trade-offs vs alternatives)
- **Multi-stage Dockerfile** over a single-stage build: keeps build tools (pip's compiled
  wheel cache, etc.) out of the final image — smaller image, smaller attack surface.
  Trade-off: slightly more complex Dockerfile syntax to learn.
- **docker-compose** over Kubernetes/Helm: this is a portfolio project meant to run with
  one command on a reviewer's laptop; Kubernetes would be solving a scaling problem that
  doesn't exist here.
- **Ollama in-compose, CPU-only** (your choice) over GPU passthrough or host-native: keeps
  the "one command, works everywhere" property intact. Trade-off: CPU inference is slow —
  fine for demoing the pipeline works, not for production-grade throughput. Revisit with
  GPU passthrough (`nvidia-container-toolkit`) later if you have a compatible GPU.
- **Healthchecks + `depends_on: service_healthy`** over a fixed `sleep` or no ordering at
  all: without this, `api` can start and try to connect to Qdrant/Neo4j before they're
  ready to accept connections — a classic source of "works on second run, fails on first
  boot" bugs in RAG tutorials.

### Key concepts used
- **Multi-stage Docker build** — using multiple `FROM` stages in one Dockerfile so you can
  build with heavy tools then ship only the runtime artifacts.
- **Build context & layer caching** — Docker caches each instruction's result; copying
  `pyproject.toml` before `src/` means code-only changes don't force a full dependency
  reinstall.
- **Compose service networking** — containers on the same compose network resolve each
  other by *service name* (e.g. `http://qdrant:6333`), never `localhost`, because each
  container has its own network namespace.
- **Named volumes** — persist stateful service data (`qdrant_data`, `neo4j_data`,
  `ollama_data`) across `docker compose down`/`up` cycles; without them, all vector/graph
  data is lost every restart.
- **Healthcheck + `depends_on: condition: service_healthy`** — compose-level readiness
  gating, distinct from just "container process started."

### Real bug hit and fixed while testing
The first `docker compose up` attempt left `ollama` stuck `unhealthy` forever, blocking
`api` from starting (`depends_on` correctly refused to start it). The Ollama container logs
showed the server was actually running fine (`Listening on [::]:11434`) — the problem was
the healthcheck command itself: `wget -q --spider ...` failed because the `ollama/ollama`
image ships **no `wget` and no `curl`** (`docker exec ... which wget` → not found).
Fixed by switching the healthcheck to `ollama list`, which uses the CLI binary that's
guaranteed to exist in the image and talks to the local server directly. **Lesson: verify
what tools actually exist inside a base image before writing a healthcheck against it —
don't assume every image has `wget`/`curl`.**

After the fix, a full `docker compose up -d` brought all four services to `healthy`/`running`,
and `curl http://localhost:8000/health` returned
`{"status":"ok","qdrant_url":"http://qdrant:6333","neo4j_uri":"bolt://neo4j:7687","ollama_url":"http://ollama:11434"}`
— confirming the API resolved its dependencies through compose's internal DNS correctly.

### How this connects to the pipeline
**Previous:** 1.1 pinned `pyproject.toml` gave us a reproducible Python environment.
**This step:** turns that environment into a containerized, orchestrated stack — the
literal infrastructure every later step (ingestion into Qdrant/Neo4j, retrieval, the real
API endpoints, the UI) will run inside.
**Next:** likely wiring real ingestion code (chunking → embeddings → Qdrant) and a Neo4j
GraphRAG ingestion path, now that there's a running database to write into.

### How to do this yourself (reproduce without me)
1. Write a Dockerfile with two `FROM` stages: a `builder` that does
   `pip install --prefix=/install .`, and a `runtime` stage that does
   `COPY --from=builder /install /usr/local`. Add `USER appuser` for non-root.
2. For each service in `docker-compose.yml`, decide: does it need a `build:` (your own
   code) or just an `image:` (an existing public image)? For public images, check available
   tags with `docker manifest inspect <image>:<tag>` before you commit to a version.
3. Add a named `volumes:` entry for anything stateful (databases). Mount it at whatever path
   that image's docs say it stores data (check the image's Docker Hub page).
4. Add a `healthcheck:` to every service — but first `docker exec -it <container> sh` into
   a running instance and check what tools actually exist (`which wget`, `which curl`)
   before writing the check command.
5. Use `depends_on: <service>: condition: service_healthy` on any service that needs
   another to be *ready*, not just *started*.
6. Test end-to-end: `docker compose build`, `docker compose up -d`, `docker compose ps`
   (check all say `healthy`), then hit whatever your app exposes
   (`curl http://localhost:<port>/health`) to prove the internal service-name networking
   actually resolves. Always `docker compose down` when done to free the volumes/network.

### Quick Validation:

bash
# After completing microstep
docker-compose config  # Validates YAML syntax
docker-compose build --dry-run  # Check build steps (don't build yet)




### Practice exercises
1. `docker exec -it <container-name> sh` into the running `qdrant` container and try to
   curl `neo4j:7474` from inside it — see compose's internal DNS resolve a sibling service
   by name, something `localhost` could never do.
2. Deliberately remove the `depends_on: condition: service_healthy` block from `api` and
   run `docker compose up` a few times — try to reproduce the race condition where `api`
   starts before Qdrant is ready (more likely on a fresh/cold volume pull).

---

## Step 1.2 — CI pipeline: `.github/workflows/ci.yml` (lint, test, build)

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
