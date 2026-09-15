# Docker Deep Dive: Reverse-Engineering `Dockerfile` + `docker-compose.yml`

This is a self-contained learning path for the two files built in Step 1.1:
[`infra/docker/Dockerfile`](../infra/docker/Dockerfile) and [`docker-compose.yml`](../docker-compose.yml).

**How to use this doc:** for each concept, read the definition, attempt the exercise
yourself (actually run the commands — don't just read them), form your own explanation,
*then* read the "checkpoint" to check yourself. Don't skip to the checkpoint first.

---

## 0. High-level summary + concept map

`Dockerfile` describes how to **build one image** (the `api` service) in two stages.
`docker-compose.yml` describes how to **run four containers together** (`api`, `qdrant`,
`neo4j`, `ollama`) as one networked system with shared conventions for state and readiness.

Concept map, in dependency order (left unlocks understanding of what's to its right):

```
Dockerfile concepts:
  base image → WORKDIR → COPY → RUN (layers/caching) → multi-stage builds
  → non-root USER → EXPOSE → CMD

docker-compose concepts:
  services → images vs build → ports (host:container) → environment vars (+ .env)
  → volumes (named, persistent) → networking (service-name DNS) → healthcheck
  → depends_on (with condition)
```

Everything in section 2 below follows this order. Do not attempt the multi-stage build
exercise before the layer-caching one — it won't make sense yet.

---

## 1. Concept-by-concept

Each entry: **definition → why here specifically → what breaks if removed/changed →
canonical reading → micro-exercise → checkpoint (attempt first, then reveal)**.

---

### 1.1 Base image (`FROM python:3.11-slim`)

**Definition:** the starting filesystem + OS your image is built on top of.

**Why here:** we picked `python:3.11-slim` specifically to match the `requires-python
">=3.11,<3.12"` constraint we pinned in `pyproject.toml` in Step 1.1 — the Docker image's
Python version and your local venv's Python version are two *separate* things that must be
kept in sync deliberately. `slim` (not the ~1GB default `python:3.11`, not `alpine`) is a
size/compatibility trade-off: much smaller than the default, but unlike `alpine` it uses
glibc, which avoids a whole class of binary-wheel compatibility issues with packages like
`numpy`/`scipy` that we pull in transitively via LangChain.

**What breaks if changed:** switch to `python:3.11-alpine` and rebuild — some pinned
packages (numpy, scipy, tokenizers) either fail to install or take much longer because pip
has to compile from source instead of using a prebuilt wheel (alpine's musl libc isn't
`manylinux`-compatible).

**Canonical reading:** [Docker Hub: python official image](https://hub.docker.com/_/python) — read the "Image Variants" section.

**Micro-exercise (5 min):**
```dockerfile
# save as Dockerfile.test in a scratch folder
FROM python:3.11-alpine
RUN pip install --no-cache-dir numpy==2.4.6
```
Run `docker build -f Dockerfile.test -t alpine-test .` and watch what happens — does it
succeed, and if so, how much slower is it vs a `slim`-based build of the same package?

**Checkpoint — explain in your own words before reading on:**
*What's the actual trade-off between `slim` and `alpine`, in terms of what happens during
`pip install` for a package with C extensions?*

<details><summary>Reveal</summary>
`alpine` is smaller but uses musl libc, so most Python packages with compiled C extensions
(numpy, scipy, pydantic-core, etc.) have no prebuilt wheel for it — pip falls back to
compiling from source, which is slow and needs build tools installed. `slim` uses glibc
(Debian-based) so `manylinux` wheels — the vast majority of PyPI's prebuilt binaries — install
instantly.
</details>

---

### 1.2 `WORKDIR`

**Definition:** sets the working directory for all subsequent instructions (`COPY`, `RUN`,
`CMD`) in that stage; creates the directory if it doesn't exist.

**Why here:** `WORKDIR /build` in the builder stage and `WORKDIR /app` in the runtime stage
are *different, deliberately* — nothing from `/build` needs to exist in the final image
except what's explicitly copied out of it.

**What breaks if changed:** remove `WORKDIR` entirely and all `COPY` destinations become
relative to `/` (image root) — `COPY src ./src` would try to write to `/src` at the
filesystem root, mixing your app code in with system directories.

**Canonical reading:** [Dockerfile reference: WORKDIR](https://docs.docker.com/reference/dockerfile/#workdir)

**Micro-exercise (5 min):**
```dockerfile
FROM alpine
WORKDIR /app
RUN pwd > /tmp/where.txt
CMD ["cat", "/tmp/where.txt"]
```
Build and run it (`docker build -t wd-test . && docker run --rm wd-test`) — confirm it
prints `/app`. Then delete the `WORKDIR` line, rebuild, rerun — what does it print now?

**Checkpoint:**
*If you have two `WORKDIR` instructions in the same stage, what happens to the second one's
path if it's relative, e.g. `WORKDIR /app` then later `WORKDIR src`?*

<details><summary>Reveal</summary>
It's *appended* to the previous WORKDIR, not treated as image-root-relative — you'd end up
at `/app/src`. WORKDIR paths stack like `cd` calls, not like reassignment.
</details>

---

### 1.3 `COPY` and layer caching (the ordering trick)

**Definition:** `COPY` copies files from the build context into the image; each `COPY`/`RUN`
becomes its own cached layer, keyed by a hash of the instruction + the copied files' content.

**Why here — this is the single most load-bearing design choice in the file:**
```dockerfile
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ... --prefix=/install .
```
`pyproject.toml` is copied *before* `src/`, and the `pip install` layer comes *after* that.
Docker caches each layer by the hash of its inputs. If you only change application code in
`src/`, the `pyproject.toml` copy layer's hash is unchanged, so Docker reuses the cached
layer — but critically, **the `pip install` layer here still re-runs**, because it depends
on `src/` too (it's `pip install .`, which needs the source to build the wheel). This is
actually a subtlety worth noticing: our current ordering doesn't fully exploit caching,
because `pip install .` needs `src/` present. A stricter version would install dependencies
from `pyproject.toml` alone (no local source needed) before copying `src/` at all — see the
exercise below, which has you prove this to yourself.

**What breaks if changed:** reverse the order (`COPY src ./src` before
`COPY pyproject.toml ...`) — now *any* change to `pyproject.toml` alone still invalidates
the cache correctly, but you lose the intended optimization of "code-only changes skip
dependency reinstall," because the `RUN pip install` layer's cache key includes whichever
`COPY` happened most recently before it either way.

**Canonical reading:** [Docker docs: layer caching](https://docs.docker.com/build/cache/) and [best practices: order matters](https://docs.docker.com/build/building/best-practices/#leverage-build-cache)

**Micro-exercise (10 min) — prove the caching behavior to yourself:**
1. `docker compose build api` (baseline build).
2. Edit only `src/api/main.py` (change the health response text), rebuild, and **time it**
   (`Measure-Command` in PowerShell or just eyeball it) — note which steps say `CACHED`.
3. Now edit `pyproject.toml` (bump a comment or add a blank line) instead, rebuild, and
   compare — which layers get invalidated this time?

**Checkpoint:**
*Explain why editing `pyproject.toml` forces a slow rebuild but editing `src/api/main.py`
does NOT skip the `pip install` step either, in our current Dockerfile — even though in
theory it should be able to.*

<details><summary>Reveal</summary>
Because our `RUN pip install --prefix=/install .` runs `pip install .` (installing the local
package itself, not just its dependencies) — and that command needs `src/` to exist to build
the wheel. So the `pip install` layer's effective inputs include `src/`, meaning ANY code
change re-triggers it, even though most of that command's *time* is actually spent
downloading/installing third-party dependencies that didn't change. A more advanced version
would split this into two `RUN` steps: one that installs just `[project.dependencies]` from
a temporary requirements file (cacheable across code changes), and a second, fast step that
installs the local package on top. That's a real optimization you could implement as a
stretch exercise.
</details>

---

### 1.4 Multi-stage builds (`AS builder` / `AS runtime`, `COPY --from=builder`)

**Definition:** multiple `FROM` instructions in one Dockerfile, each starting a new,
independent build stage; a later stage can selectively copy artifacts from an earlier one
via `COPY --from=<stage>`.

**Why here:** the `builder` stage runs `pip install`, which needs `pip` itself, potentially
compiler toolchains for any package without a prebuilt wheel, and leaves behind pip's
package cache metadata. None of that needs to exist in the image that actually runs in
production — only the *installed packages* (`/install`) and your source code do. The
`runtime` stage starts fresh from the same slim base and cherry-picks only those two things.

**What breaks if removed:** collapse it into a single stage (just do everything in one
`FROM python:3.11-slim`, no second stage) — the final image still works identically at
runtime, but it's measurably larger and contains build-time artifacts (pip's cache, apt
metadata if you'd installed compilers) that serve no purpose after the build — pure bloat
and slightly increased attack surface.

**Canonical reading:** [Docker docs: multi-stage builds](https://docs.docker.com/build/building/multi-stage/)

**Micro-exercise (10 min):**
```dockerfile
# Stage A produces a file
FROM alpine AS producer
RUN echo "hello from builder" > /secret.txt

# Stage B only gets what we explicitly copy
FROM alpine AS consumer
COPY --from=producer /secret.txt /secret.txt
CMD ["cat", "/secret.txt"]
```
Build and run it. Then add a second file in the producer stage (`RUN echo "other" >
/other.txt`) but *don't* copy it in stage B — try `docker run --rm <image> cat /other.txt`
and observe the failure.

**Checkpoint:**
*If stage A downloads a 500MB dataset and stage B only copies a 10KB summary file out of
it, what's the final image size — does the 500MB "leak through" at all?*

<details><summary>Reveal</summary>
No — the final image only contains what was explicitly `COPY --from`'d. The 500MB exists
only in the intermediate builder-stage layer, which isn't part of the final image's layer
history (though it may linger in Docker's build cache on your machine until pruned via
`docker builder prune`).
</details>

---

### 1.5 `RUN` (in the `pip install` context)

**Definition:** executes a command during the build, and commits the resulting filesystem
changes as a new layer.

**Why here:** `RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir
--prefix=/install .` does two things chained with `&&` deliberately in *one* `RUN`
instruction rather than two: chaining avoids creating an extra layer, and `--no-cache-dir`
stops pip from writing its own download cache into the layer (which would otherwise bloat
the image for no runtime benefit — you already have this cache in your local dev venv).

**What breaks if changed:** split into two separate `RUN` lines instead of chaining with
`&&` — functionally the output is identical, but you get one extra layer in the image
history. Remove `--no-cache-dir` — the image gets noticeably larger for no functional gain
(pip's HTTP cache is dead weight once the install finishes).

**Canonical reading:** [Dockerfile reference: RUN](https://docs.docker.com/reference/dockerfile/#run) and [best practices: minimize layers](https://docs.docker.com/build/building/best-practices/#minimize-the-number-of-layers)

**Micro-exercise (5 min):**
```dockerfile
FROM python:3.11-slim AS with_cache
RUN pip install requests

FROM python:3.11-slim AS without_cache
RUN pip install --no-cache-dir requests
```
Build both as separate targets (`docker build --target with_cache -t t1 .` and `--target
without_cache -t t2 .`), then `docker images` and compare sizes.

**Checkpoint:**
*Roughly how much size difference did `--no-cache-dir` make for a small package like
`requests`? Would you expect the gap to be bigger or smaller for LangChain (which pulls
dozens of transitive dependencies)?*

<details><summary>Reveal</summary>
For a single small package the difference is modest (a few MB) but for a dependency tree
the size of LangChain's — dozens of packages, some with large wheels — the pip cache
duplication would be tens of MB of pure waste in the image. This is why our actual
Dockerfile uses `--no-cache-dir` on both `pip install` calls.
</details>

---

### 1.6 Non-root `USER`

**Definition:** sets which user ID subsequent instructions (and the running container's
main process) execute as; defaults to `root` if never set.

**Why here:** if `api`'s FastAPI process (or any of its dozens of dependencies, including
ones we don't directly control) has a vulnerability that lets an attacker execute arbitrary
code, running as `appuser` instead of `root` limits what that code can do to the container's
own filesystem — it's a real, measurable reduction in blast radius, standard practice for
any production container.

**What breaks if removed:** the container still runs identically — nothing about your app's
*functionality* depends on this. The difference only shows up in a security-testing
scenario (see the exercise).

**Canonical reading:** [Docker docs: USER](https://docs.docker.com/reference/dockerfile/#user) and [Docker security best practices](https://docs.docker.com/engine/security/#linux-kernel-capabilities)

**Micro-exercise (5 min, uses the real running stack):**
```bash
docker exec -it advancerag-api-1 whoami
docker exec -it advancerag-api-1 sh -c "touch /root/test 2>&1; echo exit:$?"
docker exec -it advancerag-api-1 sh -c "touch /home/appuser/test 2>&1; echo exit:$?"
```

**Checkpoint:**
*Why does `touch /root/test` fail specifically, when `appuser` clearly has write access to
its own home directory?*

<details><summary>Reveal</summary>
Standard Unix file permissions — `/root` is owned by `root` with mode restricting write
access to that owner only; `appuser` is a different, unprivileged user, so the kernel denies
the write regardless of being inside a container. Containers don't bypass normal Linux
permission checks — `USER` just controls which UID the process runs as, and that UID is
then subject to ordinary filesystem permissions.
</details>

---

### 1.7 `EXPOSE`

**Definition:** documentation-only metadata declaring which port the container's app
listens on — it does **not** actually publish/open that port to the host.

**Why here:** `EXPOSE 8000` documents that uvicorn binds to 8000 inside the container. The
actual host-accessible mapping is a separate concern, controlled entirely by
`docker-compose.yml`'s `ports: - "8000:8000"`.

**What breaks if removed:** nothing at runtime — `EXPOSE` has zero effect on whether the
port is reachable. It's purely informational (some tools like `docker run -P` use it to
auto-publish, but we don't use that flag here).

**Canonical reading:** [Dockerfile reference: EXPOSE](https://docs.docker.com/reference/dockerfile/#expose)

**Micro-exercise (5 min) — prove `EXPOSE` does nothing on its own:**
Temporarily delete `EXPOSE 8000` from the Dockerfile, rebuild, and `docker compose up -d` —
check `curl localhost:8000/health` still works.

**Checkpoint:**
*If `EXPOSE` doesn't actually open the port, what IS it useful for?*

<details><summary>Reveal</summary>
Documentation for humans and tooling reading the Dockerfile (e.g., someone using `docker
inspect` or `docker run -P` to auto-map all exposed ports to random host ports). The actual
host-port publishing is 100% controlled by `-p`/`--publish` on `docker run`, or `ports:` in
compose — `EXPOSE` is metadata, not configuration.
</details>

---

### 1.8 `CMD`

**Definition:** the default command run when a container starts from this image (can be
overridden at `docker run`/compose time).

**Why here:** `CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]`
uses **exec form** (JSON array), not shell form (`CMD uvicorn ...`) — exec form runs the
process directly as PID 1 without wrapping it in a shell, which matters for how the process
receives signals (see exercise). `--host 0.0.0.0` is critical and easy to get wrong: binding
to `127.0.0.1` (the default if you omit `--host`) would make uvicorn refuse connections from
outside the container itself, since the "loopback" interface inside a container is not the
same loopback the host or sibling containers see.

**What breaks if changed:** change `--host 0.0.0.0` to `--host 127.0.0.1` (or omit
`--host`) — the container starts fine, `docker compose ps` shows it running, but
`curl localhost:8000/health` from the host times out or connection-refuses, because uvicorn
is only listening on the container's internal loopback, unreachable from outside.

**Canonical reading:** [Dockerfile reference: CMD](https://docs.docker.com/reference/dockerfile/#cmd), [uvicorn docs: host binding](https://www.uvicorn.org/settings/)

**Micro-exercise (10 min) — reproduce the classic "works in logs, unreachable from host" bug:**
Temporarily change the Dockerfile's CMD to bind `--host 127.0.0.1`, rebuild, `docker compose
up -d api`, then:
```bash
docker compose logs api        # looks fine — uvicorn says "running"
curl http://localhost:8000/health   # fails
docker exec -it advancerag-api-1 curl http://127.0.0.1:8000/health  # works, from INSIDE
```

**Checkpoint:**
*Why does curling from inside the container succeed, but from the host, the exact same
`127.0.0.1:8000` fails?*

<details><summary>Reveal</summary>
`127.0.0.1` always means "this machine," but "this machine" is a different machine (network
namespace) depending on where you run the command — inside the container, `127.0.0.1` IS
the container. From the host, `127.0.0.1` is the host, and the port-forwarding rule set up
by `ports: "8000:8000"` forwards host traffic to the container's *external* interface
(`0.0.0.0` inside the container), not its loopback. This is the single most common
"container starts fine but I can't reach it" bug in Docker.
</details>

---

### 1.9 Compose `services:` block + `build:` vs `image:`

**Definition:** each top-level key under `services:` is one container definition; `image:`
pulls a prebuilt image from a registry, `build:` constructs one locally from a Dockerfile.

**Why here:** `qdrant`, `neo4j`, `ollama` use `image:` because we don't own or need to modify
their code — official maintained images exist. `api` uses `build:` with `context: .` (repo
root, so `COPY src ./src` in the Dockerfile can see it) and `dockerfile:
infra/docker/Dockerfile` (since we organized it in a subfolder, not the repo root).

**What breaks if changed:** set `context: infra/docker` instead of `context: .` — the build
fails immediately, because `COPY pyproject.toml README.md ./` and `COPY src ./src` in the
Dockerfile can no longer find those paths (they're not inside the `infra/docker/` context).

**Canonical reading:** [Compose spec: build](https://docs.docker.com/reference/compose-file/build/)

**Micro-exercise (5 min):**
Temporarily change `context: .` to `context: infra/docker` in `docker-compose.yml`, run
`docker compose build api`, and read the exact error message.

**Checkpoint:**
*What does the build context actually control, in one sentence?*

<details><summary>Reveal</summary>
It's the root directory sent to the Docker daemon that all `COPY`/`ADD` paths inside the
Dockerfile are resolved relative to — it has nothing to do with where the Dockerfile itself
lives (that's the separate `dockerfile:` key).
</details>

---

### 1.10 `ports:` — host:container mapping

**Definition:** `"HOST_PORT:CONTAINER_PORT"` — publishes a container's internal port to a
specific port on the host machine.

**Why here:** `"8000:8000"` for `api`, but note `qdrant` publishes **two** ports
(`6333` REST, `6334` gRPC) because the Qdrant client library can use either protocol —
we expose both so either connection style would work from the host.

**What breaks if changed:** two services both trying to map to the same host port
(e.g., change `neo4j`'s `7474` mapping to `8000:7474`) — `docker compose up` fails outright
with a port-already-allocated error, since only one process on the host can bind a given
port.

**Canonical reading:** [Compose spec: ports](https://docs.docker.com/reference/compose-file/services/#ports)

**Micro-exercise (5 min):**
Change `api`'s port mapping to `"9500:8000"`, `docker compose up -d api`, then try both
`curl localhost:8000/health` (should fail) and `curl localhost:9500/health` (should work).

**Checkpoint:**
*Inside the container, does uvicorn know or care that it's being accessed via host port
9500 instead of 8000?*

<details><summary>Reveal</summary>
No — uvicorn only ever sees container-internal port 8000: it never sees the host-side
number at all. The remapping happens entirely at the Docker networking layer (iptables/NAT
rules), invisible to the process inside.
</details>

---

### 1.11 `environment:` + `.env` substitution (`${VAR:-default}`)

**Definition:** `environment:` injects variables into the container's process environment;
`${VAR:-default}` is compose's shell-style variable substitution syntax, reading from a
`.env` file (or your shell) with a fallback if unset.

**Why here:** `QDRANT_URL`/`NEO4J_URI`/`OLLAMA_URL` are hardcoded to the compose *service
names* (`http://qdrant:6333`, etc.) — these never need to change since they rely on
compose's internal DNS. But `NEO4J_PASSWORD` and `COHERE_API_KEY` are secrets that
shouldn't be hardcoded, so they're pulled from `.env` with `${VAR:-default}`, matching the
`Settings` class in `src/core/config.py` that reads these same variable names.

**What breaks if changed:** typo a variable name on either side (e.g., `NEO4J_PASSWROD` in
compose vs `neo4j_password` expected in `config.py`) — nothing errors at build or startup
time; the app just silently falls back to `Settings`' own Python-level default instead of
your `.env` value. This is a *silent* failure mode, which is exactly why the validation
section below teaches you to check the actual runtime value, not just trust the file.

**Canonical reading:** [Compose spec: environment](https://docs.docker.com/reference/compose-file/services/#environment) and [Compose docs: env files](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)

**Micro-exercise (10 min):**
1. Copy `.env.example` to `.env`, set `NEO4J_PASSWORD=testpass123`.
2. `docker compose up -d`, then `docker exec advancerag-api-1 env | grep NEO4J`.
3. Delete `.env` entirely, `docker compose down && docker compose up -d`, check the env var
   again — did the default kick in?

**Checkpoint:**
*Where, precisely, does variable substitution happen — inside the container, or before the
container ever starts?*

<details><summary>Reveal</summary>
Before. Compose reads `.env` and resolves `${VAR:-default}` syntax on your *host* machine
while parsing `docker-compose.yml`, producing a final, fully-resolved environment block —
the container itself receives plain, already-substituted key-value pairs and has no
awareness that substitution syntax was ever involved.
</details>

---

### 1.12 Named `volumes:`

**Definition:** a Docker-managed persistent storage location, declared at the bottom-level
`volumes:` key and mounted into a container's filesystem at a given path via
`service.volumes: - volume_name:/container/path`.

**Why here:** `qdrant_data:/qdrant/storage`, `neo4j_data:/data`, `ollama_data:/root/.ollama`
— each mount path is dictated by *that specific image's* documented data directory (you'd
find these in each image's Docker Hub page, not something you invent). Without them, each
`docker compose down` + `up` cycle would start every database completely empty.

**What breaks if changed:** mount to the wrong path (e.g. `qdrant_data:/wrong/path`) — the
container still starts fine (no error), but Qdrant writes its actual data to some
ephemeral, non-persisted location inside the container instead, and you'd only discover data
loss the next time you restarted — another silent failure mode.

**Canonical reading:** [Docker docs: volumes](https://docs.docker.com/engine/storage/volumes/)

**Micro-exercise (10 min):**
1. `docker compose up -d`, create a Qdrant collection: `curl -X PUT
   localhost:6333/collections/test_collection -H "Content-Type: application/json" -d
   '{"vectors":{"size":4,"distance":"Dot"}}'`
2. `docker compose down` (no `-v`), then `docker compose up -d` again.
3. `curl localhost:6333/collections/test_collection` — still there?
4. Now `docker compose down -v`, `up -d` again, check the same URL.

**Checkpoint:**
*What Docker object actually holds the data — is it "inside" the container, or somewhere
else entirely?*

<details><summary>Reveal</summary>
Somewhere else — a named volume is a separate object Docker manages independently of any
container's lifecycle, physically stored on the host (find it with `docker volume inspect
advancerag_qdrant_data`, look at `Mountpoint`). The container just has that location
bind-mounted at a path inside its filesystem. Deleting the container never touches the
volume; only explicit volume deletion (`-v` flag, or `docker volume rm`) does.
</details>

---

### 1.13 Compose networking (service-name DNS)

**Definition:** Compose automatically creates a private network per project and registers
each service's name as a DNS hostname resolvable by every other container on that network.

**Why here:** `api`'s env vars use `http://qdrant:6333` — never an IP address, never
`localhost` — because container IPs are dynamic (reassigned on every recreate) while the
service name is stable. This is *the* reason Docker networking feels "magic" the first time
you see it.

**What breaks if changed:** use a hardcoded IP instead (e.g., discover Qdrant's current IP
via `docker inspect` and hardcode it into `QDRANT_URL`) — works until the next `docker
compose down && up`, at which point Qdrant gets a new IP and the hardcoded value silently
points nowhere.

**Canonical reading:** [Compose docs: networking](https://docs.docker.com/compose/how-tos/networking/)

**Micro-exercise (10 min):**
```bash
docker exec -it advancerag-api-1 sh -c "curl -s http://qdrant:6333 && echo OK"
docker exec -it advancerag-api-1 sh -c "curl -s http://localhost:6333 || echo FAILED-as-expected"
docker network inspect advancerag_default
```
Read the `Containers` section of the inspect output — note each container's actual internal
IP, and compare to what you just used by name.

**Checkpoint:**
*If you restarted just the `qdrant` container (`docker compose restart qdrant`), would its
internal IP address likely change? Would `api`'s config need to change as a result?*

<details><summary>Reveal</summary>
The IP might well change (compose doesn't guarantee IP stability across
recreate/restart in all cases). But `api`'s config never needs to change, because it never
referenced the IP — only the stable DNS name `qdrant`, which compose keeps pointing at
whatever the current IP is.
</details>

---

### 1.14 `healthcheck:`

**Definition:** a command run periodically *inside* a container to determine if it's ready
to serve traffic, separate from "is the process running at all."

**Why here — and the real bug this step hit:** every service needs a way for `api` to know
"not just started, but actually ready." Qdrant's healthcheck uses a raw TCP connection test
(`bash -c 'exec 3<>/dev/tcp/localhost/6333'`) because Qdrant's slim image doesn't ship
`curl`/`wget` either, but does have bash's `/dev/tcp` pseudo-device. Neo4j's uses `wget`
(its image does ship it). Ollama's uses `ollama list` — **this was changed from an original
`wget` attempt that failed**, because that image ships neither `wget` nor `curl`. Three
services, three different healthcheck strategies, each dictated by what tools actually
exist in that specific base image — never assume, always verify (see exercise).

**What breaks if changed:** revert Ollama's healthcheck to `wget -q --spider
http://localhost:11434` — the container's `ollama serve` process runs perfectly fine
(confirm with `docker logs`), but Compose reports it `unhealthy` forever because the
healthcheck *command itself* errors (`wget: not found`), and `api`'s
`depends_on: condition: service_healthy` means `api` never starts.

**Canonical reading:** [Compose spec: healthcheck](https://docs.docker.com/reference/compose-file/services/#healthcheck)

**Micro-exercise (10 min) — reproduce the exact bug this step hit:**
1. Temporarily edit `ollama`'s healthcheck test back to
   `["CMD-SHELL", "wget -q --spider http://localhost:11434 || exit 1"]`.
2. `docker compose up -d`, wait ~30s, `docker compose ps` — confirm `ollama` shows
   `unhealthy` and `api` never starts (stuck `Created`).
3. `docker logs advancerag-ollama-1` — confirm the actual server process logs show it's
   running fine (`Listening on [::]:11434`).
4. `docker exec advancerag-ollama-1 which wget` — confirm it's genuinely missing.
5. Revert to `ollama list` and confirm `api` starts normally again.

**Checkpoint:**
*Before writing a healthcheck for a new image you've never used, what's the one command you
should run first?*

<details><summary>Reveal</summary>
`docker run --rm -it <image> sh` (or `docker exec` if it's already running) followed by
`which wget`, `which curl`, or checking that image's own CLI tools — never assume a minimal
image ships general-purpose HTTP clients. Many production images strip them deliberately to
reduce attack surface.
</details>

---

### 1.15 `depends_on: condition: service_healthy`

**Definition:** delays starting a service until its dependency reports `healthy` (not just
"container process started") via that dependency's own `healthcheck:`.

**Why here:** `api` connects to Qdrant/Neo4j/Ollama on startup (implicitly, whenever the
first request comes in and code eventually calls those clients). If those databases haven't
finished their own internal startup sequence (Neo4j in particular takes several seconds to
initialize before accepting Bolt connections), `api` could start and immediately fail its
first real request — a classic race condition that's especially likely on a cold/fresh
volume, and especially likely to bite a first-time reviewer cloning your repo.

**What breaks if changed:** remove the `condition: service_healthy` and use plain
`depends_on: [qdrant, neo4j, ollama]` (list form, no condition) — compose only waits for the
containers to *start*, not to be ready; on a fresh `docker compose down -v && up`, you can
catch `api` racing Neo4j's slower boot sequence.

**Canonical reading:** [Compose spec: depends_on](https://docs.docker.com/reference/compose-file/services/#depends_on)

**Micro-exercise (10 min) — try to reproduce the race:**
1. Change `api`'s `depends_on` to plain list form (no `condition:`).
2. `docker compose down -v` (wipe all state, forces slowest possible cold start for Neo4j).
3. `docker compose up` (not `-d`, so you can watch logs live) and watch `api`'s logs closely
   right after it starts — look for any connection-refused/timeout messages.
4. This may not reproduce every single time (that's the nature of a race condition) — try
   it 2-3 times if the first attempt doesn't show it.
5. Revert to `condition: service_healthy` and confirm the race is gone.

**Checkpoint:**
*Why is this bug hard to catch during normal development, but more likely to bite a first-
time user cloning your repo?*

<details><summary>Reveal</summary>
During normal dev, your volumes already have data and images are already pulled/cached, so
every service boots fast and the race window is tiny or nonexistent — you might never
trigger it. A first-time clone starts from nothing: images must be pulled, Neo4j
initializes its data directory from scratch (slower), and the race window is much wider.
This is exactly the kind of bug that looks fine in your own testing but breaks for someone
else — which is why we tested with `docker compose down -v` explicitly in this step's
validation, not just a normal restart.
</details>

---

## 2. Validating it's ACTUALLY working (not just "no errors")

For each claim below, run the command yourself against the real running stack
(`docker compose up -d` first) and look at real output — don't take these on faith.

| What to check | Command | What "working" looks like |
|---|---|---|
| Container is alive | `docker compose ps` | `STATUS` shows `Up`, and `(healthy)` for anything with a healthcheck |
| Process-level logs | `docker compose logs -f api` | Uvicorn's startup banner, no tracebacks |
| Healthcheck raw history | `docker inspect advancerag-ollama-1 --format='{{json .State.Health}}'` | JSON with `"Status":"healthy"` and a `Log` array of past attempts with `ExitCode: 0` |
| Actual env vars inside the container | `docker exec advancerag-api-1 env \| sort` | Every var from `docker-compose.yml`'s `environment:` block present with the value you expect — **this is the only way to be sure `.env` substitution actually happened**, don't trust the YAML file alone |
| Mounted volumes | `docker inspect advancerag-qdrant-1 --format='{{json .Mounts}}'` | Shows `"Destination":"/qdrant/storage"` bound to a named volume, not an anonymous/missing mount |
| Exposed vs published ports | `docker port advancerag-api-1` | `8000/tcp -> 0.0.0.0:8000` — confirms the actual host mapping in effect right now, not just what's declared in the file |
| Live resource usage | `docker stats --no-stream` | Sanity-check nothing is spinning at 100% CPU or ballooning in memory at idle |
| Network membership | `docker network inspect advancerag_default` | All four containers listed under `Containers`, each with an IP on the same subnet |
| End-to-end request | `curl -s http://localhost:8000/health \| python -m json.tool` | Returns the exact URLs your `environment:` block declared — proves config actually flowed from file → container → app → HTTP response |

**Failure-mode drills** (do each on purpose, predict the outcome first, then observe):

1. **Kill a dependency mid-flight:** `docker compose stop qdrant` while `api` is running,
   then `curl localhost:8000/health` — does `/health` fail, or does it still return 200
   (because our current `/health` only echoes config, never actually pings Qdrant)? This
   reveals a real gap: our healthcheck endpoint doesn't currently verify live connectivity
   to dependencies — worth fixing once real ingestion/retrieval code exists.
2. **Remove a volume's data path:** `docker exec advancerag-qdrant-1 rm -rf
   /qdrant/storage/*` (destructive — only do this on your throwaway dev stack), then
   `docker compose restart qdrant` — confirm data is gone even though the volume object
   itself still exists.
3. **Misconfigure an env var:** set `NEO4J_PASSWORD=wrongpass` in `.env` after Neo4j has
   already initialized with a different password, restart just `api`, and try to imagine
   (or later, once real Neo4j client code exists, actually observe) what error surfaces —
   auth failures on the *client* side vs the *server* rejecting the connection are different
   failure signatures worth learning to distinguish.

---

## 3. Visualizing it working

**Static architecture, as this file actually builds it** (not a generic RAG diagram):

```
                         host machine
   ┌──────────────────────────────────────────────────────────┐
   │  localhost:8000   localhost:6333/6334  localhost:7474/7687  localhost:11434
   └──────┬────────────────┬──────────────────┬─────────────────────┬────────┘
          │                │                  │                     │
   ═══════▼════════════════▼══════════════════▼═════════════════════▼═══════
                    docker network: advancerag_default
   ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │    api     │──▶│   qdrant   │   │   neo4j    │   │   ollama   │
   │ (built img)│   │ v1.19.0    │   │ 5.26.0     │   │ 0.20.0     │
   │            │──────────────▶│            │   │            │
   │            │───────────────────────────▶│            │
   └────────────┘   └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
                          │                 │                 │
                    qdrant_data       neo4j_data        ollama_data
                     (volume)          (volume)          (volume)
```
Arrows from `api` show the connections implied by its `environment:` block
(`QDRANT_URL`, `NEO4J_URI`, `OLLAMA_URL`) — resolved via the service-name DNS inside
`advancerag_default`. `depends_on: service_healthy` means `api`'s container doesn't even
start until all three targets of those arrows report healthy.

**Tools worth installing for deeper visibility:**
- [`dive`](https://github.com/wagoodman/dive) — `dive advancerag-api` shows every layer of
  the built image, its size, and exactly which files each `RUN`/`COPY` added. This makes
  section 1.3/1.4's caching and multi-stage claims visually undeniable instead of abstract.
- `docker stats` — live CPU/mem/network I/O per container, updating in real time; run it in
  a separate terminal while you hit `/health` repeatedly and watch `api`'s numbers twitch.
- `ctop` (optional, third-party) — a `top`-like curses UI over `docker stats` if you want
  something friendlier than the raw table.

**One end-to-end "watch it work" exercise, tying validation + visualization together:**

1. Open **three terminals** side by side.
2. **Terminal 1:** `docker compose logs -f api` (watch API logs live).
3. **Terminal 2:** `docker stats` (watch live resource usage of all four containers).
4. **Terminal 3:** run `docker compose up -d` from a cold state
   (`docker compose down -v` first), then narrate out loud (or write down) what you observe
   in Terminal 1/2 as each service transitions `Created → Starting → Healthy`, and finally
   as `api` starts *after* the others go healthy — you should visually see the ordering
   enforced by `depends_on`.
5. Once everything is up, in Terminal 3: `curl -s http://localhost:8000/health | python -m
   json.tool` — watch Terminal 1 for the corresponding uvicorn access log line appearing the
   instant you send the request. That's you watching a single HTTP request cross from your
   shell, through the host's port mapping, into the container, through FastAPI's routing,
   and back out — with visible confirmation at both ends simultaneously.
6. Finally: `docker network inspect advancerag_default | python -m json.tool` in Terminal 3
   and find each container's IP — cross-reference against what `docker stats` (Terminal 2)
   is labeling each row with, so the abstract "network diagram" above maps onto concrete,
   currently-live IP addresses you can see with your own eyes.

If you can narrate every arrow in the architecture diagram above, using only commands from
section 2, without looking anything up — you've internalized this step for real, not just
"it ran without error."
