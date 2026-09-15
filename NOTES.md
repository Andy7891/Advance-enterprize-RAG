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

---

## Step 2.1 — `DocumentChunk` data contract: `src/ingestion/schemas.py`

**Date:** 2026-08-20

### What was built
- [src/ingestion/schemas.py](src/ingestion/schemas.py) — `DocumentChunk`, a frozen
  (immutable) dataclass, plus the utility functions that construct it from a real markdown
  file on disk:
  - `compute_doc_id(source_path)` — deterministic hash of a file's path (relative to
    `data/`), stable across re-ingestion of the same file.
  - `compute_chunk_id(doc_id, chunk_index, text)` — deterministic hash of a chunk's
    position + content; changes if content or chunking strategy changes, stays the same
    otherwise.
  - `infer_category(source_path, data_root)` — reads the top-level `data/` subfolder
    (`company`/`contracts`/`employees`/`products`) as the chunk's category, erroring loudly
    if a file has no category subfolder.
  - `extract_title(text, fallback)` — pulls the first `# H1` heading, falls back to the
    filename stem.
  - `count_tokens_approx(text)` — cheap `len(text) // 4` estimate, not an exact tokenizer
    count (see trade-off below).
  - `discover_markdown_files(data_root)` — walks `data/` for `*.md`, explicitly excluding
    `.ipynb_checkpoints`/`.git`/`__pycache__` noise directories.
  - `make_chunk(...)` — the single call site that assembles a fully-populated,
    correctly-ID'd `DocumentChunk`; all chunk construction should go through this, not
    hand-rolled elsewhere, so the ID logic can never drift between call sites.
- [tests/ingestion/test_schemas.py](tests/ingestion/test_schemas.py) — 14 tests, run against
  the project's **real** `data/` folder (82 actual `.md` files across 4 categories), not
  synthetic fixtures — including tests that the `.ipynb_checkpoints` folders under
  `data/company/`, `data/employees/`, etc. get correctly excluded, and that re-running
  `make_chunk` on the same input twice produces identical IDs (idempotency, proven not just
  asserted).

### Why this approach (trade-offs vs alternatives)
- **Deterministic, content-derived IDs instead of `uuid4()`:** re-ingesting an unchanged file
  reproduces the exact same `chunk_id`/`doc_id`, so a future Qdrant upsert overwrites the
  existing point instead of creating a duplicate — this is what makes ingestion *idempotent*,
  safe to re-run without a separate manual dedup step. `uuid4()` would be simpler code but
  silently double the index on every re-run.
- **`doc_id` derived from PATH, `chunk_id` derived from CONTENT:** a deliberate split. If
  `doc_id` were content-based, editing a contract file would produce an unrelated new
  `doc_id`, orphaning the file's old chunks in the vector store with nothing to identify and
  delete them by. Path-based `doc_id` means "same file" always maps to "same document,"
  regardless of edits — content changes are tracked at the finer-grained `chunk_id` level
  instead.
- **Frozen (`frozen=True, slots=True`) dataclass**, not a plain mutable one: `chunk_id` is
  mathematically derived from `doc_id + chunk_index + text`. A mutable dataclass would let
  other code edit `.text` post-construction and leave `.chunk_id` silently wrong — pointing
  at content that no longer matches. Freezing turns that into a `FrozenInstanceError` at the
  point of the mistake, not a silent data-integrity bug discovered much later during
  retrieval debugging. `slots=True` is a minor, free efficiency win (less per-instance
  memory) with no real downside here.
- **`count_tokens_approx` uses a `len(text) // 4` heuristic, not `tiktoken`:** a real,
  considered trade-off, not laziness. Exact token counts need a specific tokenizer, and
  `tiktoken` (OpenAI's) both couples this schema module to one provider AND can require a
  one-time network fetch of encoding data on first use in some environments — a poor fit for
  a project meant to build/test offline and stay provider-agnostic (this stack already uses
  Cohere for reranking and Ollama for local inference — never intended to be OpenAI-only).
  The approximation is accurate enough to catch "this chunk is way too big," not precise
  enough for exact context-window budgeting — acceptable now, revisit once actual chunk-size
  tuning starts (see practice exercises).
- **`infer_category` raises loudly instead of defaulting to `"unknown"`:** every chunk MUST
  have a real category for the filtering/hybrid-search use case category exists for in the
  first place — a silent `"unknown"` fallback would let a misplaced file quietly degrade
  search quality instead of failing ingestion immediately where it's obvious and cheap to fix.

### Key concepts used
- **Data contract** — a shared shape (here, `DocumentChunk`) that every producer and
  consumer in a pipeline agrees to, so they can be built/tested independently without
  coordinating on internals.
- **Idempotency** — running an operation multiple times with the same input produces the
  same result as running it once; here, achieved via deterministic ID derivation rather than
  random IDs.
- **Content-addressed / deterministic hashing** — deriving an identifier from a hash of the
  content/inputs themselves, rather than generating a random one, so identical inputs always
  produce identical IDs.
- **Immutability (`frozen` dataclass)** — preventing an object's fields from being modified
  after construction, used here to protect a derived-field invariant (`chunk_id` matching
  `text`) from ever silently breaking.

### How this connects to the pipeline
**Previous:** Steps 1.1/1.2 built the environment, containers, and CI — infrastructure with
no actual RAG logic yet. **This step** is the first real ingestion-pipeline code: the shape
every chunk will have from here on.
**Next:** the actual **chunking strategy** (splitting each markdown file's text into
`DocumentChunk`s of reasonable size/overlap, likely `src/ingestion/chunking.py`) — this is
flagged as a genuine core RAG concept to spend real time on, not boilerplate: chunk size and
overlap directly determine retrieval quality (too large → irrelevant context dilutes the
answer; too small → answers lose necessary surrounding context). After that: embedding the
chunks and upserting into Qdrant, using exactly the `DocumentChunk` shape defined here.

### How to do this yourself (reproduce without me)
1. Look at your actual data source structure first (`find data -type f -name "*.md"`) before
   designing any schema — the category/folder convention here came directly from how your
   `data/` folder was already organized, not invented in the abstract.
2. Decide what a "chunk" needs to carry to support every later use case you can already
   foresee: citation (source path), filtering (category), debugging chunk boundaries
   (chunk_index, char_count), and idempotent re-ingestion (a deterministic ID) — design the
   dataclass fields backward from those use cases, not just "text + source."
3. For any ID field, ask: "if I run this twice on the same input, do I want the same ID or a
   different one?" That answer determines whether to hash content, hash a path, or use a
   random UUID — get this wrong and you'll be debugging silent duplicate-or-orphan bugs in
   your vector store much later.
4. Use `dataclasses.dataclass(frozen=True)` for any object with fields that are
   mathematically derived from other fields — it converts a whole class of "field went stale"
   bugs into an immediate, loud error.
5. Write tests against your REAL data directory, not just synthetic strings — a real 82-file,
   4-category knowledge base surfaces edge cases (like `.ipynb_checkpoints` pollution) that
   hand-written test fixtures would never think to include.
6. Run `pytest`, `ruff check`, `ruff format --check`, and `mypy` locally before considering
   the module done — all four check different things (correctness, style, formatting,
   types), and this step's CI pipeline (Step 1.2) will run all four on every push anyway.

### Practice exercises
1. Swap `count_tokens_approx`'s heuristic for real `tiktoken` counts
   (`tiktoken.get_encoding("cl100k_base").encode(text)`) and measure the actual error
   percentage across a sample of your real `data/*.md` files — is char/4 an overestimate or
   underestimate for this specific corpus?
2. Deliberately edit `data/company/about.md`'s content (even by one character) and re-run
   `make_chunk` on it with the same `chunk_index` — confirm `doc_id` stays identical but
   `chunk_id` changes, proving the path-vs-content ID split works as designed.
3. Try removing `frozen=True` from `DocumentChunk`, then rerun
   `test_document_chunk_is_immutable` — watch it fail, and read exactly what `pytest.raises`
   was expecting vs what happened.
4. Add a fifth data category folder (e.g. `data/policies/`) with one dummy `.md` file, rerun
   `test_discover_markdown_files_finds_known_categories`, and update the test's expected set
   — practice the workflow of "data changed, tests need updating too."
5. Call `infer_category` on a file placed directly in `data/` (no subfolder) and confirm it
   raises `ValueError` with a message that would actually help someone debug it.
6. Try hashing the FULL sha256 hexdigest (64 chars) instead of the truncated `[:16]` we use —
   compute the theoretical collision probability difference for a knowledge base of ~10,000
   chunks and decide whether 16 characters was actually a safe truncation.
7. Write a quick script that calls `discover_markdown_files` and `extract_title` across all
   82 real files, and print any file where the extracted title looks wrong (e.g. no H1
   found, so it fell back to the filename) — a real data-quality pass over your own corpus.
8. Add a `content_hash` field to `DocumentChunk` that's independent of `chunk_id` (e.g. hash
   of `text` alone) — think through why you might want this as a SEPARATE field from
   `chunk_id` even though `chunk_id` already incorporates the text (hint: think about
   detecting "did the CONTENT change" vs "did the ID change," across a chunking-strategy
   change that shifts every chunk_index).
9. Benchmark `compute_doc_id`/`compute_chunk_id` against all 82 real files' worth of
   (eventual) chunks — at roughly how many chunks would SHA-256 hashing become a measurable
   bottleneck in ingestion, if ever?
10. Intentionally break one thing at a time (comment out the `.ipynb_checkpoints` exclusion,
    remove the `category subfolder` check, etc.) and predict which specific test in
    `test_schemas.py` will fail before running it — then confirm.

**Correction from this step, worth noting:** earlier chat summaries said "82 real markdown
files" — that count includes `.ipynb_checkpoints/` duplicates. The real, de-duplicated
count (what `discover_markdown_files`/`discover_documents` actually return) is **76**. Fixed
in Step 2.2's tests below; if you still have "82" anywhere in your own notes, it's the raw
on-disk count including checkpoint noise, not the real corpus size.

---

## Phase 2, Step 2.2 — Document loader: `src/ingestion/loader.py`

**Date:** 2026-08-21

### What was built
- [src/ingestion/loader.py](src/ingestion/loader.py):
  - `LoadedDocument` — a frozen dataclass holding one file's raw extracted text +
    minimal metadata (`source_path`, `category`, `title`, `format`, `char_count`).
    Deliberately simpler than `DocumentChunk` (Step 2.1) — no `chunk_id`/`doc_id`/
    `chunk_index`, since this document hasn't been chunked yet; those identity fields only
    make sense once a chunking strategy exists.
  - `discover_documents(data_root)` — like Step 2.1's `discover_markdown_files` but for all
    three supported extensions, reusing the same `EXCLUDED_DIR_NAMES` exclusion set (see
    below — this constant was promoted from private to shared).
  - `load_document(source_path, data_root)` — loads one file, dispatching to the right
    extraction function by extension.
  - `load_all_documents(data_root, skip_errors=False)` — loads every supported file;
    `skip_errors` controls whether one bad file aborts the whole run or gets collected as a
    reported failure (see trade-off below).
  - `_read_text_file` — reads `.txt`/`.md`, with a UTF-8 → Latin-1 fallback for
    mis-encoded files (see real bug class below).
  - `_read_pdf_file` — extracts text from `.pdf` via `pypdf`, page by page, joined with
    blank lines.
- Added `pypdf==6.16.1` to `pyproject.toml`'s core `dependencies` (not `dev` — PDF loading
  is a runtime capability of the actual application, not a dev-only tool) and mirrored it
  into `requirements.txt`, per Step 1.1's sync convention.
- Promoted `schemas.py`'s `_EXCLUDED_DIR_NAMES` (private, underscore-prefixed) to
  `EXCLUDED_DIR_NAMES` (public) — `loader.py` needed the exact same exclusion set, and
  importing a private name across module boundaries is a real code smell (nothing stops the
  two lists from drifting if they'd stayed separately defined; sharing one guarantees they
  can't).
- [tests/ingestion/test_loader.py](tests/ingestion/test_loader.py) — 9 tests, including
  extraction from a REAL, hand-constructed, byte-valid single-page PDF (built with a correct
  xref table and trailer — pypdf's lenient-recovery parsing does NOT cover every malformed
  PDF, discovered by hitting a real "Stream has ended unexpectedly" error on my first,
  invalid attempt at the fixture and fixing it properly rather than working around it), a
  UTF-8-fallback test using a deliberately non-UTF-8 byte sequence, and a full run against
  the real 76-file `data/` corpus.

### Why this approach (trade-offs vs alternatives)
- **`pypdf` over `pdfplumber`/`PyMuPDF`/`unstructured`:** pure Python (no compiled/system
  dependencies to manage in Docker), permissive BSD license (PyMuPDF is AGPL — a real
  licensing consideration for a project that might be open-sourced or distributed),
  reasonably good on typical text-based PDFs (contracts, reports). Trade-off: weaker on
  complex multi-column layouts/tables than `pdfplumber`, and zero OCR capability for
  scanned/image-only PDFs — both explicitly documented as limitations in the code, not
  silently assumed away.
- **`skip_errors` as an explicit caller choice, not a fixed policy:** whether one bad file
  should abort a whole ingestion run or be skipped-and-reported is a genuine judgment call
  that depends on context (a small curated knowledge base vs a large messy one) — the
  module refuses to decide this for you; `load_all_documents(data_root)` fails fast by
  default (safer default: don't silently ingest an incomplete corpus), and
  `skip_errors=True` opts into best-effort collection when that's the right call.
- **UTF-8 → Latin-1 fallback, not UTF-8-or-crash:** a real, easy-to-miss bug class —
  not every text file on disk is actually UTF-8 (files saved by older tools or on different
  locales are often Latin-1/cp1252). Latin-1 never raises `UnicodeDecodeError` (every byte
  value is a valid Latin-1 code point), making it a safe last-resort that keeps one
  mis-encoded file from aborting an entire ingestion run — at the cost of possibly
  mis-decoding some characters if the file was actually some OTHER non-UTF-8 encoding.
- **Promoting `EXCLUDED_DIR_NAMES` to shared/public instead of duplicating the exclusion
  set in `loader.py`:** two independently-maintained copies of "which folders are noise"
  WILL eventually drift (someone adds a new noise-folder type to one and forgets the
  other) — one shared constant makes that impossible by construction.

### Key concepts used
- **Format dispatch** — routing to different logic based on a file's type (here, extension),
  a simple but foundational pattern for any multi-format ingestion pipeline.
- **PDF text extraction (lossy, layout-dependent)** — flagged as a genuine core RAG concept:
  unlike plain text, PDFs store positioned glyphs, not paragraphs; extraction quality varies
  by library and by the PDF's own internal structure (single vs multi-column, embedded text
  layer vs scanned image) — see [pypdf docs](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) and the practice exercises for how to actually verify extraction quality rather than assume it worked.
- **Encoding fallback** — trying a strict decode first, falling back to a permissive one,
  a general pattern for handling real-world files that don't perfectly match your primary
  assumption.
- **Fail-fast vs best-effort error handling** — a genuine design axis (not a "correct
  answer") for any batch-processing pipeline: does one bad input abort everything, or does
  the pipeline degrade gracefully and report failures separately?

### Real bugs hit and fixed while building this (not just "should work")
1. **Wrong file count in my own test assumption:** I initially asserted the real data corpus
   has 82 files, matching an earlier (imprecise) chat summary — running the code against
   the actual `data/` folder immediately proved that wrong (76, once `.ipynb_checkpoints`
   duplicates are correctly excluded). Fixed the TEST, not the code — the code was already
   correct; my assumption was stale. This is exactly why every deep-dive doc in this project
   pushes "run it against real data and check the actual number," not "trust what you
   remember from three steps ago."
2. **Invalid hand-crafted test PDF:** my first attempt at a minimal PDF fixture (raw bytes,
   no xref table, no `%%EOF`) failed with `PdfReadError: Stream has ended unexpectedly` —
   pypdf's lenient recovery mode does NOT successfully parse every malformed PDF. Fixed by
   building the xref table and trailer with byte-accurate offsets computed programmatically
   (see `_make_minimal_pdf` in the test file), verified in a scratch script before porting
   into the real test — proof that even a "fake" test PDF needs a genuinely valid internal
   structure, which is itself a small, concrete lesson in what PDF parsers actually require.

### How this connects to the pipeline
**Previous:** Step 2.1 defined `DocumentChunk`, the eventual output shape. **This step**
produces the raw text `DocumentChunk`s will be built FROM — `LoadedDocument.text` is exactly
what a chunking function will take as input.
**Next:** the actual **chunking strategy** (Phase 2's next microstep) — splitting each
`LoadedDocument.text` into appropriately-sized, possibly-overlapping pieces, then calling
Step 2.1's `make_chunk()` for each piece using `LoadedDocument.source_path`/`category`/
`title` to carry that metadata through. This is where chunk size/overlap decisions (a real
core RAG concept) get made — this loader intentionally does zero chunking itself.

### How to do this yourself (reproduce without me)
1. Look at what file formats actually exist (or will realistically exist) in your knowledge
   base before choosing extraction libraries — don't build PDF support speculatively if you
   have zero PDFs; don't skip it if you know contracts/reports will arrive as PDFs later.
2. For any new PDF library, test it against a REAL PDF from your own domain (or a hand-built
   minimal one, done correctly) before trusting it — read the library's own docs for known
   limitations (multi-column layout, tables, scanned/image-only files) rather than assuming
   "no exception raised" means "extraction succeeded well."
3. Decide, explicitly, your fail-fast vs best-effort policy for batch loading — don't let it
   default silently one way or the other; make it a parameter callers choose, if both are
   legitimate for different call sites.
4. For any "read this file as text" operation, ask what happens if the file ISN'T in your
   assumed encoding — test with an intentionally mis-encoded file, not just well-formed ones.
5. Before introducing a second module that needs the same filtering/exclusion logic as an
   existing one, promote the shared logic/constant to a public, shared location instead of
   copy-pasting it — copies drift, one source of truth can't.
6. Run the real test suite, `ruff check`, `ruff format --check`, and `mypy` before calling
   the module done — exactly as this step did, catching the file-count assumption error and
   the invalid-PDF-fixture bug along the way rather than after the fact.

### Practice exercises
1. Add a 4th format (e.g. `.docx` via `python-docx`) — extend `SUPPORTED_EXTENSIONS`,
   add a branch in `_extract_text`, and write a real test for it, following the exact
   pattern `.pdf` support used here.
2. Drop a REAL scanned/image-only PDF (no embedded text layer — e.g. export a page as an
   image and wrap it in a PDF) into a scratch folder and run `load_document` on it — observe
   that it "succeeds" (no exception) but `char_count` comes back near-zero — a live
   demonstration of pypdf's core limitation, not a hypothetical one.
3. Feed `load_all_documents` a folder containing one file with Windows CRLF line endings and
   one with Unix LF — does `char_count` treat `\r\n` as one character or two? Does this
   matter for later chunking?
4. Deliberately corrupt one real `.md` file's bytes (truncate it mid-file) and run
   `load_all_documents(data_root, skip_errors=True)` against a copy of `data/` containing
   it — confirm it's NOT collected as an error (since truncated text is still valid UTF-8
   text, just incomplete) — a good illustration of a failure mode this loader does NOT
   currently detect at all.
5. Multi-column PDF test: find or construct a 2-column PDF and compare pypdf's extracted
   text order against the visual reading order — is text from column 1 fully extracted
   before column 2 starts, or interleaved line-by-line?
6. Benchmark `load_all_documents` against the real 76-file corpus — how long does it take,
   and would it still be acceptably fast at 10x or 100x the file count?
7. Add a `page_count` field to `LoadedDocument` for PDFs specifically (via
   `len(reader.pages)`) — think about whether this belongs as a first-class field or in
   `metadata`-equivalent territory (note `LoadedDocument` currently has no metadata escape
   hatch at all, unlike `DocumentChunk` — is that a gap worth fixing?).
8. Change `skip_errors=True`'s behavior to also log a SUMMARY count at the end
   ("Loaded 74/76 documents, 2 failed") — a small but realistic operational-visibility
   improvement for a real ingestion run.
9. Try loading a `.md` file that's actually binary garbage (e.g. copy a `.pdf` and rename its
   extension to `.md`) — does the UTF-8/Latin-1 fallback logic handle it gracefully, crash,
   or silently produce nonsense text? Decide if this is a gap worth guarding against.
10. Write a property-style test: for every real file in `data/`, assert
    `load_document(...).char_count > 0` — does this hold for all 76 files, and would it
    still hold if a genuinely empty `.md` file were added?

---

## Fix — `ModuleNotFoundError: No module named 'src'` in Jupyter

**Date:** 2026-08-21

### What happened
Running `from src.ingestion.loader import ...` in a Jupyter notebook raised
`ModuleNotFoundError: No module named 'src'`, even though the exact same import works fine
from a terminal (`.venv/Scripts/python.exe -c "from src.ingestion... "`).

### Root cause
`src` is only importable inside THIS project's `.venv` (registered there via
`pip install -e .` back in Step 1.1). Checking the venv directly
(`pip show ipykernel jupyter`) showed neither package was installed at all — meaning
whatever notebook kernel was in use belonged to a COMPLETELY DIFFERENT Python environment
(e.g. a system-wide Python, Anaconda, or a different venv) that never had this project
installed into it. The notebook's `import` was running in an environment that has never
heard of this project, not the one all the terminal commands in this whole learning log have
been using.

### Fix
1. Added `ipykernel==7.3.0` to `pyproject.toml`'s `dev` extras and reinstalled
   (`pip install -e ".[dev]"`).
2. Registered a NAMED kernel pointing at this exact venv:
   ```bash
   .venv/Scripts/python.exe -m ipykernel install --user --name=advance-rag --display-name "Advance RAG (.venv)"
   ```
3. **You must then select "Advance RAG (.venv)" as the kernel** in your notebook (Jupyter's
   kernel picker, or VS Code's kernel selector in the top-right of a notebook file) — this
   step doesn't happen automatically; registering a kernel just makes it AVAILABLE to
   choose, it doesn't switch any already-open notebook to use it.
4. Verified the fix from the terminal (not the notebook, to isolate variables):
   `from src.ingestion.loader import discover_documents` succeeded and returned 76, and
   the full test suite (23 tests) + `ruff check` + `mypy` still pass after the
   `pyproject.toml` change.

### Key concept
**A Jupyter kernel IS a Python environment/interpreter, chosen independently per notebook**
— installing a package into a venv from your terminal does NOT make it available to a
notebook unless that notebook's kernel points at that exact same venv. This is a very common
first-time gotcha: terminal and notebook can silently be running completely different
Pythons even when opened from the "same" project folder.

### How to do this yourself (reproduce without me)
1. From inside the project's venv, run
   `python -m pip show ipykernel` — if it errors "package not found," that confirms the venv
   itself can't run notebooks yet.
2. Install it: add `ipykernel` to your dev dependencies, reinstall.
3. Register a kernel: `python -m ipykernel install --user --name=<short-name> --display-name "<Human Readable Name>"`.
4. In your notebook UI, open the kernel picker and select the new display name explicitly —
   don't assume it's already selected.
5. Sanity-check with a trivial cell: `import sys; print(sys.executable)` — this should print
   a path INSIDE your project's `.venv`, not a system Python or a different environment. If
   it doesn't, the kernel picker selection didn't take effect.

---

## Fix #2 — `ModuleNotFoundError: No module named 'src'` PERSISTED after correct kernel

**Date:** 2026-08-21

### What happened
After Fix #1 (registering and selecting the correct `.venv` kernel — confirmed via
`sys.executable` pointing at the right venv), `from src.ingestion.schemas import ...` STILL
raised `ModuleNotFoundError: No module named 'src'`. This meant Fix #1's diagnosis, while
correct as far as it went, wasn't the WHOLE story — there was a second, independent bug
underneath it.

### Root cause — a real bug in `pyproject.toml`, not a notebook/kernel issue at all
`[tool.setuptools.packages.find] where = ["src"]` tells setuptools: "packages live INSIDE
`src/`; treat `src/` itself as invisible, and install whatever's inside it (`ingestion`,
`api`, `core`) as top-level packages." I verified this directly:
```python
import ingestion  # NO "src." prefix
print(ingestion.__file__)  # -> .../src/ingestion/__init__.py  -- this actually worked!
```
So the package that `pip install -e .` genuinely registered was named `ingestion` (bare),
NOT `src.ingestion`. Every `from src.xxx import ...` statement in this entire codebase
(written since Step 1.2) had ONLY ever "worked" as an accident of `sys.path` — when a
terminal command runs with the current working directory AT the repo root, Python
implicitly adds that cwd to `sys.path`, and the plain `src/` folder (which has an
`__init__.py`, from Step 1.2) becomes importable purely as a side effect of THAT, completely
independent of the real `pip install -e .` package registration. I proved this directly by
reproducing the exact failure from a terminal:
```bash
cd "practice codes" && ../.venv/Scripts/python.exe -c "from src.ingestion.loader import discover_documents"
# ModuleNotFoundError: No module named 'src'   <- reproduced outside the notebook entirely
```
This confirms it was NEVER a kernel/notebook-specific problem — any process (notebook OR
terminal) run from a directory other than the repo root would hit this exact same error.
Fix #1 (the kernel registration) was still a legitimate, necessary fix on its own — the
venv genuinely lacked `ipykernel` — but it was fixing a DIFFERENT problem than the one
actually causing this specific error message.

### Fix
Changed the package-discovery config so `src` itself becomes the real, properly-registered
top-level package — matching what every file in this codebase (and prior NOTES.md entries)
already assumes:
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["src", "src.*"]
```
Reinstalled with `pip install -e ".[dev]" --force-reinstall --no-deps`, then verified:
- `from src.ingestion.loader import discover_documents` now works from `practice codes/`
  (a real subdirectory, NOT the repo root) — the actual bug is fixed, not just relocated.
- The old accidental bare `import ingestion` (no `src.` prefix) now correctly FAILS — proof
  the package registration changed to match our code's real import convention instead of
  the standard "src-layout" convention we were never actually following consistently.
- Full test suite (23 tests), `ruff check`, and `mypy` all still pass unchanged.
- **Not yet re-verified inside Docker** — Docker Desktop wasn't running at the time of this
  fix. The Dockerfile's `pip install --prefix=/install .` uses the same
  `pyproject.toml`/setuptools mechanism, so it should behave identically, but this is a
  claim based on the mechanism being environment-agnostic, not a directly re-run Docker
  build — worth confirming with `docker compose build api` next time Docker Desktop is up.

### Key concept
**"src layout" is a real, named Python packaging convention** — but it specifically expects
NO `__init__.py` directly in `src/`, and code to import bare package names (`from
ingestion import ...`, not `from src.ingestion import ...`). This project has an `src/
__init__.py` (added early on, Step 1.2, as unremarkable-seeming boilerplate) AND every
import statement uses the `src.` prefix — a real, internally-consistent convention, just
NOT the standard src-layout one. `where = ["src"]` in `pyproject.toml` was leftover
configuration matching the STANDARD convention, silently mismatched against what the actual
codebase does. This is a good example of how two individually-reasonable-looking choices
(an `src/__init__.py`, and a standard-looking `packages.find` config) can combine into a bug
that only surfaces once something (a notebook in a subdirectory) breaks the ONE
undocumented assumption — "always run from the repo root" — holding it together.

### How to do this yourself (reproduce without me)
1. When `from src.x import y` works from a terminal at the repo root but fails from
   anywhere else (a notebook, a different cwd), suspect the package registration itself,
   not just the environment/kernel — the two are separate possible causes and BOTH may be
   real simultaneously (as they were here).
2. Directly test what your editable install actually registered:
   `python -c "import <suspected-bare-name>; print(<suspected-bare-name>.__file__)"` — if a
   bare, unprefixed import unexpectedly succeeds, your `packages.find` config doesn't match
   your code's actual import convention.
3. Reproduce the failure from a plain terminal, `cd`'d into a non-root subdirectory — this
   isolates whether the bug is notebook-specific or a genuine packaging config issue that
   would affect ANY process run from that location.
4. Fix `[tool.setuptools.packages.find]` to match whichever convention your code ACTUALLY
   uses (`src.x` prefixed imports vs bare `x` imports) — don't change decades of existing
   import statements across a whole codebase to match a config file; change the config to
   match the code that already exists and is already tested.
5. Reinstall (`pip install -e . --force-reinstall --no-deps` is enough — only the package
   metadata/registration changed, not the dependency list) and re-verify from a non-root
   directory specifically, not just the repo root (which would have looked fine even with
   the bug still present, per the accidental-cwd-import behavior described above).

---

## Phase 2, Microstep 2 — Semantic chunking: `src/ingestion/chunker.py`

**Date:** 2026-09-15

### Pre-flight audit (what we'd forgotten)
Before building, an audit of the repo found real gaps, all now closed:
- **No `.gitignore`, and 32,509 `.venv/` files were committed to git.** Untracked
  forward-only (the Initial commit was already pushed to GitHub, so a history rewrite would
  have required force-pushing published `main` — see decision.md D-2026-09-15-12).
- **CI was green while asserting nothing** — all five `ci.yml` steps still `echo`'d TODO
  placeholders. Now they run `ruff check`, `ruff format --check`, `mypy`, `pytest`, and
  `docker build` for real.
- Three files failing `ruff format --check`, and a stray `xxxx` in `src/api/main.py`'s
  docstring — both fixed.
- The Docker build had never been re-verified after the Fix #2 packaging change. Now
  confirmed passing.

### What was built
- [src/ingestion/chunker.py](src/ingestion/chunker.py):
  - `Embedder` — a `Protocol` matching LangChain's embeddings interface, so tests inject a
    deterministic fake and CI never needs a live model server.
  - `ChunkerConfig` — frozen config (`breakpoint_threshold_type`,
    `breakpoint_threshold_amount`, `target_chunk_chars`, `min_chunk_chars`, `buffer_size`).
  - `Chunker` — wraps `SemanticChunker`, recovers char offsets, merges undersized chunks
    backwards, warns on oversized ones, and emits `DocumentChunk`s via `make_chunk`.
  - `build_ollama_embedder()` — factory kept separate so importing the module opens no
    network connection.
- **Contract 1 amended** in [schemas.py](src/ingestion/schemas.py): `compute_chunk_id` now
  takes `(source_path, chunk_index)` and returns `"company/about.md_3"`; `make_chunk`
  accepts optional `start_char`/`end_char` that land in `metadata`.
- [tests/ingestion/test_chunker.py](tests/ingestion/test_chunker.py) — 13 tests using a fake
  embedder that maps keywords to vectors, so topic switches produce real cosine distance.
- Dependencies: `langchain-experimental==0.4.2`, `langchain-ollama==1.1.0`, and
  `langchain-community` bumped `0.3.30 → 0.4.2` (required by langchain-experimental).

### Key concepts
- **Semantic chunking** — cutting text where *meaning* shifts (measured by cosine distance
  between consecutive sentence embeddings) rather than at fixed character counts.
- **Breakpoint threshold** — the rule deciding which distances count as boundaries; we use
  `standard_deviation` (mean + 1.5σ), so uniform documents aren't force-split.
- **Soft vs hard size cap** — semantic boundaries can't honour a hard limit without
  overriding the signal they exist to follow, so `target_chunk_chars` logs rather than cuts.
- **Dependency inversion via Protocol** — depending on an interface, not a concrete
  embedder, which is what makes the tests offline and fast.

### Honest risks carried by this design
1. **Positional `chunk_id` has no content sensitivity.** Re-chunking with different settings
   reuses IDs for different text. If a run produces *fewer* chunks than before, surplus old
   chunks linger in the vector store forever and can still be retrieved. **The upsert layer
   MUST delete all chunks for a `doc_id` before re-ingesting.** A test
   (`test_compute_chunk_id_does_NOT_change_when_text_changes`) documents this deliberately.
2. **`langchain-experimental` is officially sunset**, and `langchain-community` is being
   sunset too — both emit `DeprecationWarning` on import. A core pipeline stage now depends
   on retired packages (decision.md D-2026-09-15-05).
3. **Ollama on CPU is slow.** A full 76-document run embeds thousands of sentences.

### How this connects to the pipeline
**Previous:** `loader.py` produced `LoadedDocument`s (raw text + metadata).
**This step:** turns that text into `DocumentChunk`s — the unit everything downstream
retrieves. **Next:** embedding those chunks and upserting into Qdrant, where the
delete-by-doc_id guard above becomes mandatory, not optional.

### How to do this yourself
1. Choose a chunking strategy deliberately; read
   [Pinecone's chunking guide](https://www.pinecone.io/learn/chunking-strategies/) and
   [Kamradt's 5 Levels of Text Splitting](https://github.com/FullStackRetrieval-com/RetrievalTutorials/blob/main/tutorials/LevelsOfTextSplitting/5_Levels_Of_Text_Splitting.ipynb).
2. Before adopting any library, run `pip install --dry-run` to see what it upgrades, and
   import it once to check for deprecation warnings — that is how the sunset status of
   `langchain-experimental` surfaced here.
3. Inject the embedder rather than constructing it inside the chunker, so tests can fake it.
4. Write the fake embedder to encode *topic* (keyword → vector), so cosine distance behaves
   realistically without a model.
5. Whenever an ID scheme drops a guarantee, write a test asserting the *new* behaviour so
   the lost guarantee stays visible instead of being rediscovered in production.

### Practice exercises
1. Run the chunker on `data/company/about.md` with `breakpoint_threshold_amount` at 0.5, 1.5
   and 3.0 — plot chunk count against threshold and find where it stops changing.
2. Switch `breakpoint_threshold_type` to `"percentile"` and compare chunk-count variance
   across 10 documents versus `standard_deviation`.
3. Feed a deliberately uniform document (the same sentence 50 times) and confirm
   `standard_deviation` produces exactly one chunk — the documented failure mode.
4. Set `min_chunk_chars=10_000` and verify everything merges to a single chunk; then reason
   about why the *first* chunk is never dropped.
5. Print `start_char`/`end_char` for every chunk of one file and use them to slice the
   original text — confirm the slices reconstruct the document (modulo merge whitespace).
6. Time `chunk()` with the fake embedder vs real Ollama on one document; compute the
   per-sentence embedding cost and extrapolate to all 76 files.
7. Write a chunk whose text contains no sentence-ending punctuation at all and see how
   `sentence_split_regex` handles it.
8. Add a `strategy="recursive"` branch using `RecursiveCharacterTextSplitter` and diff its
   boundaries against the semantic ones on the same document.
9. Deliberately re-chunk a file with two different configs and inspect the resulting
   `chunk_id`s — reproduce the orphaned-chunk risk from Risk #1 on paper.
10. Replace `nomic-embed-text` with a different Ollama embedding model and check whether the
    chunk boundaries move — evidence for how model-specific your tuning actually is.
