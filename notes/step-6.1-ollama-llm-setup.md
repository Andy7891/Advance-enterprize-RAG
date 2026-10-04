# Phase 6, Microstep 1 — Set Up Local LLM (Ollama)

**Date:** 2026-09-29
**Core learning: Partially.** Pulling and running a local model is mostly infrastructure
(⚪), matching [P4-M1](step-4.1-neo4j-setup.md)'s shape. The core RAG idea this step
introduces is narrower but real (🔴): a generation model is a fundamentally different kind
of dependency than every service before it — it doesn't just store or search data, it
*produces* new text, and "is it loaded" isn't provable without actually asking it to answer.

## The gap this step closes
Every step so far has been retrieval: find and rank existing chunks. Nothing in this project
has ever generated a new sentence. Phase 6 is "Core Orchestration" — turning a question plus
retrieved chunks into an answer — and that needs an LLM to exist and respond first.

Checked before building anything: `docker-compose.yml`'s `ollama` service was already running
(used since Phase 3 for embeddings), but the only model ever pulled was `nomic-embed-text`
(0.27 GB, an embedding model, not a generation one). No `src/generation/` package existed. No
`Settings` field named a generation model. This step's real, narrow job was: pull a
generation-capable model, and prove — not assume — it actually answers.

## What was built
- **`mistral` pulled** into the existing Ollama container (7B parameters, Q4 GGUF
  quantization, ~4.4 GB on disk).
- **[`src/generation/setup.py`](../src/generation/setup.py)**, mirroring
  [`src/graph/setup.py`](../src/graph/setup.py)'s exact shape:
  - `GenerationStatus` (frozen dataclass): `ready`, `model`, `response_seconds`,
    `sample_reply`, `problem`.
  - `health_check(client, model)`: verifies Ollama is reachable, the model is pulled, **and**
    runs one real, tiny generation call (`PROBE_PROMPT = "Reply with exactly one word: OK"`) —
    a model can be *listed* without actually being able to *load and answer*.
  - `build_client(ollama_url)`: a real `ollama.Client`, with a bounded 120 s timeout so a hung
    Ollama container fails this check in minutes, not indefinitely.
  - `python -m src.generation.setup` CLI, exit 0 if ready (prints the real measured response
    time and sample reply), exit 1 with a specific problem otherwise.
- **`Settings.llm_model: str = "mistral"`** in [`src/core/config.py`](../src/core/config.py) —
  deliberately *no* digest-lock field (see D-2026-09-29-14).
- **[`tests/generation/test_setup.py`](../tests/generation/test_setup.py)**: 14 tests — 12
  hermetic (a fake `ollama.Client`, built from the SDK's own response types) and 2 live.
- **`tests/support.py`**: added `ollama_down()`, mirroring `neo4j_down()` — the existing
  `live_services_down()` requires Qdrant too, which generation tests don't need.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Model | `mistral` (7B, ~4.4 GB) | `llama3.2` (3B, faster); `phi3.5` (3.8B) | CPU-only generation is slow (measured below); the fix is re-pulling a smaller model, not a code change |
| D2 Client | raw `ollama` package | `langchain_ollama.ChatOllama` (already pinned, unused so far for chat) | mirrors the embedding side's own established preference; a second calling convention would exist alongside it if this had gone the other way |
| D3 Version pinning | presence check only, no digest lock | a hard lock mirroring `embed_model_digest` | a silently drifted generation model degrades answer quality without anyone being told — accepted, because the stakes aren't the embedding case's silent data corruption |
| D4 Scope | a readiness-check module + CLI, no generation/prompt class yet | full generation class now; nothing beyond a manual `ollama pull` | matches this project's microstep granularity; a presence-only check would call a broken model "ready" |
| D5 Location | new `src/generation/` package | folded into `src/embedding/` | groups by concern, not by "talks to the same server" |

## Verified, not assumed
```
this step's tests    : 14 (12 hermetic + 2 live), all pass
mutation check         : 6 deliberate breakages of setup.py, each caught by a test, none survived
                         (one of the 6 is the exact bug class described in Finding 1 below)
model pulled           : mistral:latest, digest 6577803aa9a0..., 4.37 GB on disk (real `ollama list`)
live readiness check   : ready=True, model=mistral, sample_reply="Understood."
response time, cold    : 68.8s total (Ollama's own log: "llama runner started in 66.26 seconds" --
                         almost all of it is loading 4.4 GB into RAM, not generating)
response time, warm    : 0.9-1.0s, measured over 4 consecutive calls after the model was loaded
Docker memory budget   : measured 7.6 GB (Docker Desktop's WSL2 VM, no .wslconfig override), not
                         this host's full 15.7 GB
ruff, format, mypy     : clean
CI mode                : 604 passed, 86 deselected
whole suite (services up, after recovering from Finding 1 below) : 681 passed, 6 skipped, 3 xfailed
```

## Findings
1. **Loading Mistral crashed Neo4j and Qdrant. This is the most important finding of this
   step, found only by actually running the live check, not by reasoning about disk/RAM
   numbers in advance.** `docker stats` before this step showed the `ollama` container's own
   memory *limit* as ~7.6 GB — Docker Desktop's WSL2 VM, with no `.wslconfig` override,
   defaults to roughly half this host's 15.7 GB RAM. I noted that as a "tighter constraint than
   host RAM" and, in my first pass, wrote that pulling `mistral` was "verified to still work."
   That was wrong: the first real generation call's cold model-load (66.26s, per Ollama's own
   log) coincided with `advancerag-neo4j-1` and `advancerag-qdrant-1` both exiting with code
   255 and no graceful-shutdown log entries — the signature of the VM's OOM killer, not a bug
   in either service. Restarting both restored a healthy state, and the full suite (681 passed)
   confirms nothing in this project's *code* is at fault — this is a real, disclosed
   *infrastructure* constraint, corrected in `decision.md` D-2026-09-29-12 rather than quietly
   edited away. I cannot fix this myself: raising Docker's WSL2 memory allocation is a host
   `.wslconfig` change, which falls under this session's standing rule against modifying system
   settings — it needs your decision (see "What is still open").
2. **A model being "pulled" and a model "working" are different claims, and the difference is
   real, not theoretical.** Ollama's `list()` reports a model as soon as its weights are on
   disk; nothing about that proves it can actually be loaded into RAM and produce output. This
   step's health check makes exactly one real generation call to close that gap, and a
   mutation test (replacing the real call with a canned "OK") proves the test suite would
   catch it if that check were ever silently skipped.
3. **The CLI's own `--ollama-url` default was wrong on the first pass, and the live tests hid
   it until I ran the CLI for real.** I wrote `settings.ollama_url` (which defaults to
   `"http://ollama:11434"`, the Docker Compose service name) as the fallback, copying
   `src/graph/setup.py`'s pattern — but unlike `NEO4J_URI`, `.env` never overrides `OLLAMA_URL`
   for host use. `src/embedding/embedder.py`'s and `src/embedding/lock.py`'s own CLIs already
   solve this correctly by hardcoding `"http://localhost:11434"` directly in the argparse
   default; I had copied the wrong sibling precedent. Caught only by actually running
   `python -m src.generation.setup` from the host — the hermetic tests, which monkeypatch
   `build_client` entirely, couldn't have caught this, and my first two live tests had the
   *same* bug (`build_client(settings.ollama_url)` instead of the host-reachable constant every
   other live test in this project uses), so they didn't catch it either until run for real.
   Fixed in both `setup.py` and the two live tests; a new hermetic test now pins the correct
   default explicitly.
4. **mypy caught a real, non-obvious typing mismatch**: the installed `ollama` package's
   `generate()` is `@overload`-dispatched on `stream: Literal[False]` vs `Literal[True]`. A
   Protocol declaring plain `stream: bool = False` failed to match it; the fix was declaring
   `stream: Literal[False] = False` exactly. A sharper version of the P5-M4 lesson that extra
   optional parameters are fine — here the *type* of a shared parameter had to match an
   overload's exact discriminant.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **"Listed" is not "working":** the central, RAG-specific idea in this step — a model
  present on disk can still fail to load (OOM, corruption) or fail to answer; only a real
  inference call proves readiness.
- 🔴 **Quantization:** Mistral 7B's raw weights would be ~14 GB (float16); the Q4 GGUF build
  Ollama serves is ~4.4 GB — roughly 4 bits per parameter instead of 16, trading some precision
  for a model that fits in consumer RAM at all.
- 🟡 **Instruct-tuned vs base models:** Ollama's default `mistral` tag is the instruct-tuned
  variant — fine-tuned to follow a question/instruction, not just continue arbitrary text,
  which is what RAG generation actually needs.
- 🟡 **A container's memory limit can differ from the host's, and the risky moment is the
  transient peak, not the steady state:** Docker Desktop's WSL2 VM has its own, smaller budget;
  a warm Mistral settled at 65-80% of it, but the crash happened during the brief, higher spike
  of the cold model load — checking only steady-state usage would have missed it.
- ⚪ Readiness-check dataclass, CLI shape, Protocol-typed client injection for testing —
  plumbing, directly copied from P4-M1's own established pattern.

## Honest limitations
1. **Running all three services (Ollama with Mistral loaded, Neo4j, Qdrant) simultaneously is
   tight on this machine and crashed two of them once already (Finding 1).** Cold model loads
   are the riskiest moment; a warm model settles around 5-6 GB, about 65-80% of the VM's ~7.6 GB
   budget, leaving thin margin.
2. **CPU-only inference.** No usable GPU on this machine; warm latency (~1s for a one-word
   reply) is fine, but a real multi-sentence answer will take meaningfully longer — untested at
   that length yet.
3. **No prompt construction, no `RetrievalResult` integration yet.** This step only proves the
   model itself works; turning retrieved chunks into a grounded answer is deliberately left for
   a later microstep (D4).
4. **No digest lock (D3).** A silent model swap behind the `mistral` tag would change answer
   quality with no automated warning — accepted for now, revisit if reproducibility becomes a
   real requirement (e.g., a regression-tested eval harness).

## What is still open (needs you)
Finding 1 is a real, unresolved operational risk, not something I fixed. Three options, and I
haven't picked one for you:
1. **Accept it.** Keep working as now, and if Neo4j/Qdrant exit unexpectedly again, restart them
   (`docker compose up -d neo4j qdrant`) — mildly annoying, zero setup cost.
2. **Switch to a smaller model** (`llama3.2`, 2.0 GB, or `phi3.5`, 2.2 GB) — leaves 2-2.5 GB more
   headroom, at some generation-quality cost. One command (`ollama pull llama3.2`) plus changing
   `Settings.llm_model`.
3. **Raise Docker Desktop's WSL2 memory budget** via a `.wslconfig` file (e.g.,
   `[wsl2]\nmemory=12GB`) and a WSL restart (`wsl --shutdown`, then reopen Docker Desktop). This is
   a host-level system setting change — I won't make it myself; if you want this option, you'd do
   it directly.

## How this connects
**Previous:** Phase 5 ended with `RetrievalPipeline.retrieve(query, k) -> RetrievalResult` — a
ranked, explainable set of source chunks, and nothing to turn them into an answer. **This
step:** proves a local LLM exists and can generate text at all. **Next (not yet specified to
me):** almost certainly prompt construction — reading a `RetrievalResult` and the original
question, building a grounded prompt, and calling this same model to answer it.

## How to do this yourself
1. Check what's already running before assuming a "set up X" task starts from nothing —
   `docker ps`, `curl .../api/tags`, and reading `docker-compose.yml` took two minutes and
   changed the whole scope of this step.
2. Pick a model by real, published size against your actual hardware, not by name recognition
   — `curl` a model's library page, don't guess.
3. Measure a container's *actual* resource ceiling (`docker stats`) before assuming host
   specs are what matters — a VM-backed setup like Docker Desktop's WSL2 can have its own,
   smaller budget — and check it during the riskiest transient moment (a cold model load), not
   only at steady state, which alone looked fine here and still missed the real crash.
4. Treat "is it running" and "does it work" as different questions for anything that performs
   inference, not just anything that stores data — a presence check is not a readiness check.
5. When wrapping an SDK method that's `@overload`-decorated, match the exact discriminant type
   in your own Protocol (`Literal[False]`, not `bool`) — let mypy's error message guide you to
   the precise mismatch rather than loosening the type to make the error disappear.

## Practice exercises
1. Run `python -m src.generation.setup` yourself and read its printed output. Then stop the
   Ollama container (`docker compose stop ollama`) and run it again — compare the two messages.
2. Pull a second, smaller model (`docker exec advancerag-ollama-1 ollama pull llama3.2`) and
   run `python -m src.generation.setup --model llama3.2`. Compare `response_seconds` to
   Mistral's.
3. Change `PROBE_PROMPT` to something the model is likely to get "wrong" in an interesting way
   (e.g., a simple arithmetic question) and read `sample_reply` — does this change your sense
   of what "ready" should mean?
4. Temporarily set `PROBE_TIMEOUT_SECONDS` to `0.01` in a scratch copy and run the live test.
   What error do you get, and does the message make the cause obvious?
5. With Neo4j and Qdrant both up and idle, stop Ollama (`docker compose stop ollama`), then run
   `docker compose up -d ollama` and immediately run the readiness check while watching
   `docker stats` for all three containers in another terminal. Do you see the memory spike that
   crashed two containers during this step, or does it stay under budget this time? (Real
   outcomes can vary between runs — that's the point of checking rather than assuming either way.)
6. Break the mutation "real generate() call skipped, always OK" back in on purpose (replace the
   real call with a canned response) and run the FULL test file, not just one test — which
   specific test fails, and why that one rather than another?
7. Look up Mistral 7B's real parameter count and compute, by hand, roughly how many bytes an
   unquantized float16 copy would need. Compare to the measured 4.4 GB Q4 download.
8. Design (on paper) what a digest lock for the generation model WOULD look like if D3 had
   gone the other way, using `src/embedding/lock.py` as a template. What's the one line that
   would need to change from "raise" to "warn"?
9. Read the [Ollama API docs](https://github.com/ollama/ollama/blob/main/docs/api.md) for
   `/api/generate` vs `/api/chat`. Which does `PROBE_PROMPT` use, and what would change in
   `health_check` if you switched to the other?
10. Add a second Protocol method requirement to `GenerationClient` (e.g., `ollama.Client.show`)
    and try to satisfy it with a fake that's missing it. Read mypy's error, then delete the
    method call from your fake's class body and read Python's runtime error if you skip mypy
    and just run the test anyway.

## Sources
- Jiang et al. (2023), [Mistral 7B](https://arxiv.org/abs/2310.06825), arXiv:2310.06825 — the
  model architecture and training behind the weights this step pulled.
- [GGUF format overview](https://huggingface.co/docs/hub/gguf) — the quantized format Ollama
  serves models in, and what "Q4" means for size vs. precision.
- [Ollama model library: mistral](https://ollama.com/library/mistral) — the real, published tag
  and size checked before choosing.
- [Ollama API reference](https://github.com/ollama/ollama/blob/main/docs/api.md) — `/api/generate`,
  `/api/chat`, `/api/tags`, the endpoints `health_check` and `build_client` sit on top of.
- [notes/step-4.1-neo4j-setup.md](step-4.1-neo4j-setup.md) — the readiness-check shape this
  step's `src/generation/setup.py` deliberately mirrors.
