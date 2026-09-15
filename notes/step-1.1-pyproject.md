# Step 1.1 — Project Initialization: `pyproject.toml` with pinned dependencies

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
