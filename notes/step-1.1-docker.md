# Step 1.1 (cont'd) — Dockerfile + docker-compose orchestration

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
