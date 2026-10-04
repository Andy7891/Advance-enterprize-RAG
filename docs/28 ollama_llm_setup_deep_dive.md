# 28 — Local LLM Setup (Ollama): Deep Dive (P6-M1)

> The demo outputs below were produced on 2026-09-29/30 by running the scripts in
> [`practice codes/phase 6/m1_ollama_llm_setup_deep_dive/`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/)
> against the real [`src/generation/setup.py`](../src/generation/setup.py). `d2` runs a real,
> read-only `docker stats` command; the others need no Docker, no Ollama and no network at all.
> Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 6/m1_ollama_llm_setup_deep_dive/d0_readiness_probe.py"
> ```

---

## 0. Plain-language summary

This file's whole job is answering one question honestly: **is the local LLM actually ready to
answer, or does it just look ready?** Along the way, building and verifying it surfaced two real
infrastructure problems this doc uses as its main teaching material — a URL that only makes sense
inside Docker's own network, and a shared memory budget that a cold model load can blow past.

```
python -m src.generation.setup
  │
  ▼
build_client(url)                    <- C2: which URL even reaches Ollama from THIS process?
  (ollama.Client, timeout bound)     <- C4: how long is this allowed to take before giving up?
  │
  ▼
health_check(client, model)
  ├─ client.list()                  <- C5: only needs 2 methods -> a fake can stand in for tests
  ├─ "is the model even listed?"    <- C1: presence, the CHEAP half of the question
  ├─ client.generate(probe prompt)  <- C1: one real call, the EXPENSIVE, PROOF half
  └─ GenerationStatus(ready, ...)
  │
  ▼
"ready: ..." or "NOT ready: ..."     <- exit 0 / exit 1
```

Five concepts, in the order the code uses them:

| # | Concept | Interview-important? | Where in the code |
|---|---|---|---|
| C1 | Readiness probes: "listed" is not "working" | 🔴 very high | the two-part `health_check`, lines 55-97 |
| C2 | Host vs. container networking (Docker service names) | 🔴 very high | the `--ollama-url` default, lines 103-113 |
| C3 | Shared resource budgets under a VM-backed runtime | 🔴 very high | not in the code at all -- found by running it |
| C4 | Bounded timeouts on network calls | 🟡 high | `PROBE_TIMEOUT_SECONDS`, lines 24, 46-50 |
| C5 | Protocol-typed client injection | 🟡 medium | `GenerationClient`, lines 36-43 |

---

## C1 — Readiness probes: "listed" is not "working"

### Do this first
Run [`d0_readiness_probe.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d0_readiness_probe.py).
**Predict first:** all three names are in the phone directory. Which ones do you expect to
actually answer when called? Write your guess before running it.

### 1. Definition + analogy
**What it is.** A "readiness probe" is a check that proves a dependency can really do its job
right now, by actually making it do a small version of that job — not by checking that it merely
*exists* or is *listed somewhere*. For an LLM specifically: `ollama list` (or `GenerationClient
.list()`) only reports which model **files** are on disk. It says nothing about whether the model
can actually be loaded into memory and produce an answer — that can fail from a corrupt download,
not enough RAM, or a dozen other reasons a directory listing can't see.

**Analogy.** A company phone directory lists three employees with phone numbers. Being *listed*
proves nothing about whether the line is connected, whether someone is at their desk, or whether
they'll actually pick up. The only way to know someone can really take a call is to call them.

### For a 14-year-old
Imagine your school has an official list of "students who play chess," posted on a noticeboard.
Three names are on it: Alice, Bob and Carol. If you just read the noticeboard, all three look
equally ready to play. But what the noticeboard can't tell you is: Bob quit chess club last year
and nobody updated the list. Carol is on the list but she's actually terrified of playing in front
of people and will freeze up the second a real game starts. Only Alice will actually sit down,
make moves, and finish a real game if you challenge her right now.

If you were organizing a real chess tournament and you only looked at the noticeboard, you'd sign
up all three — and then be surprised, mid-tournament, when two of your "players" can't actually
play. The only way to know for sure is to make each of them play one real, quick practice move
before the tournament starts. That one practice move is annoying and costs a little time, but it's
the ONLY thing that tells you the truth. The noticeboard is fast and free to check; the practice
move is slower and costs something, but it's the only one that's actually reliable.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the noticeboard listing "students who play chess" | `client.list()`, checked at `pulled_names` (line 68) |
| a name still on the list after quitting | Ollama reporting a model as pulled even if its files are corrupt |
| making someone play one real practice move | `client.generate(model=model, prompt=PROBE_PROMPT, ...)` (line 80) |
| "the only way to know for sure" | the whole reason `health_check` doesn't stop at the presence check |
| Bob and Carol looking fine on paper | `GenerationStatus(ready=False, ...)` even though the model WAS listed |
| Alice actually finishing a real game | `GenerationStatus(ready=True, ..., sample_reply=...)` |

### 2. Why here specifically
`health_check` exists specifically because P6-M1's task said "Ollama running with model...
loaded" — and "loaded" is exactly the claim a presence check cannot prove. A generation model is
the first dependency in this whole project that *produces new output* rather than just storing or
searching existing data; "is it working" for a database means "can it answer a query," but for an
LLM, "can it answer" is a MUCH heavier, MUCH more failure-prone operation (loading gigabytes of
weights into RAM, running real computation) than checking a file exists.

### What breaks if this changes
- **Skip the real `generate()` call, return `ready=True` once the model is listed:** the CLI
  would report "ready" for a model that is present on disk but fails on first real use — exactly
  the gap this step's whole `main()` output ("ready: ... answered in ...s") is built to close.
- **Skip the presence check, always call `generate()` directly:** every unpulled-model check
  would waste time attempting a real generation call that Ollama would reject anyway, and the
  error message would be Ollama's raw API error instead of `problem`'s clear
  `` `ollama pull <model>` `` instruction.
- **Cache a "ready" result from one earlier check:** a model that was ready five minutes ago could
  have since had its container restarted, its disk corrupted, or been evicted from memory — proven
  freshness matters, which is why `health_check` runs both checks every single time it's called.

### [Interview relevance: very high — "how do you check a dependency is healthy" comes up in almost every systems-design and SRE-adjacent AI-engineering interview, and "presence vs. functional check" is the exact distinction interviewers are listening for]

**Interview question:** *"How would you write a health check for a service, and what's the
difference between 'the process is running' and 'the service actually works'?"*
**A strong answer covers:** a liveness check only proves a process exists (a listening port, a
process ID); a readiness/functional check proves the service can do real work (query a database,
generate a token, answer a request); for anything that loads large state into memory (an LLM, a
big index), presence checks can pass while the real thing still fails to load; a good check
exercises the smallest REAL operation that would also fail for the reasons you actually care
about, and returns quickly so it can be run often.

### 3. Minimal code that explains the concept
The complete script, [`d0_readiness_probe.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d0_readiness_probe.py) (run from the project root):
```python
# Minimal demo of C1: a "phone book" (listed) versus actually CALLING the number (working).
# Two of the three "employees" listed as available cannot actually take the call.
DIRECTORY = {  # a company's phone directory -- who is LISTED as reachable
    "Alice": "555-0101",  # a real, working line
    "Bob": "555-0102",  # a disconnected line -- still listed, though
    "Carol": "555-0103",  # a line that rings forever and nobody answers
}

WHO_ACTUALLY_ANSWERS = {"Alice"}  # the ground truth: only Alice will really pick up


def is_listed(name):
    return name in DIRECTORY  # checks the directory only -- no call is made


def actually_works(name):
    if name not in DIRECTORY:  # covers a name that isn't even in the directory
        return False, "not listed at all"  # reports why, without dialing anything
    if name not in WHO_ACTUALLY_ANSWERS:  # covers a listed name that won't really answer
        return False, "listed, but the call did not go through"  # reports the real outcome
    return True, "answered"  # reports a real, successful call


print("Checking the directory alone (what `ollama list` / GenerationClient.list() gives you):")
for name in DIRECTORY:  # goes through every listed name
    print(f"  {name:<6} listed: {is_listed(name)}")  # prints ONLY presence, nothing more

print("\nActually calling each one (what health_check's real generate() call gives you):")
for name in DIRECTORY:  # goes through every listed name again
    works, reason = actually_works(name)  # this time, really "dials" and checks
    print(f"  {name:<6} works: {works!s:<5} ({reason})")  # prints the real, only-provable-by-trying result
# Bob and Carol are BOTH listed, but only Alice is actually ready -- being in the
# directory never proves the call will be answered, only that a number exists for it.
```
Real output:
```
Checking the directory alone (what `ollama list` / GenerationClient.list() gives you):
  Alice  listed: True
  Bob    listed: True
  Carol  listed: True

Actually calling each one (what health_check's real generate() call gives you):
  Alice  works: True  (answered)
  Bob    works: False (listed, but the call did not go through)
  Carol  works: False (listed, but the call did not go through)
```

### The exact code I wrote (`src/generation/setup.py`)
```python
# src/generation/setup.py, lines 53-74, verbatim
 53 | def health_check(client: GenerationClient, model: str = settings.llm_model) -> GenerationStatus:
 54 |     """Report whether the local LLM is usable. Never raises: the reason is in `problem`."""
 55 |     try:
 56 |         # asks Ollama which models are currently pulled
 57 |         listed = client.list()  # 🎯 the CHEAP check: only reads a list Ollama already has
 58 |     except ConnectionError as exc:
 59 |         # covers Ollama being unreachable at all (container down, wrong URL)
 60 |         return GenerationStatus(ready=False, problem=f"cannot reach Ollama: {exc}")
 61 |         # reports the connection failure without raising it further
 62 | 
 63 |     # Ollama reports bare names with an explicit tag ("mistral:latest"), so an
 64 |     # untagged configured name has to match either form -- the same rule
 65 |     # src/embedding/lock.py already uses for the embedding model's own name.
 66 |     wanted = {model, f"{model}:latest"} if ":" not in model else {model}
 67 |     # builds both spellings a configured, untagged model name could be reported as
 68 |     pulled_names = {entry.model for entry in listed.models}
 69 |     # collects every model name Ollama actually has pulled right now
 70 |     if not wanted & pulled_names:  # 🎯 presence alone decided "not pulled" -- no generate() call spent on this
 71 |         # covers the case where the configured model has never been pulled
 72 |         problem = f"model {model!r} is not pulled; run `ollama pull {model}`"
 73 |         # names the exact command that fixes it
 74 |         return GenerationStatus(ready=False, problem=problem)
```
```python
# src/generation/setup.py, lines 76-98, verbatim
 76 |     started = time.perf_counter()
 77 |     # marks the start of the one real generation call this check makes
 78 |     try:
 79 |         # sends one small, real prompt to the model and waits for a real reply
 80 |         reply = client.generate(model=model, prompt=PROBE_PROMPT, stream=False)  # 🎯 the EXPENSIVE, PROOF check: one real inference call
 81 |     except (ConnectionError, ollama.ResponseError) as exc:
 82 |         # covers a model that is listed but fails to actually load or answer
 83 |         problem = f"{model!r} is pulled but did not respond: {exc}"
 84 |         # reports that mismatch explicitly, rather than treating "listed" as "working"
 85 |         return GenerationStatus(ready=False, model=model, problem=problem)
 86 |     elapsed = time.perf_counter() - started
 87 |     # measures how long the real model actually took to answer
 88 | 
 89 |     text = (reply.response or "").strip()  # 🎯 a model can answer and still say nothing useful
 90 |     # reads the generated text, treating a missing field the same as an empty one
 91 |     if not text:
 92 |         # covers a model that responded but produced nothing
 93 |         problem = f"{model!r} returned an empty response"
 94 |         # reports that as not ready, rather than silently accepting a blank answer
 95 |         return GenerationStatus(ready=False, model=model, problem=problem)
 96 | 
 97 |     return GenerationStatus(ready=True, model=model, response_seconds=elapsed, sample_reply=text)  # 🎯 only this path returns ready=True, and only with real, measured evidence
 98 |     # reports success, together with the real measured latency and the real reply
```
**🎯 Interview-relevant lines:** 70 (short-circuits before spending a real generation call on a
model that was never even pulled) and 80 (the one line in this entire file that actually proves
anything — everything before it is bookkeeping, everything after it is reporting what line 80
found).

### Canonical docs
- [Kubernetes: liveness, readiness and startup probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
  — the standard, widely-used vocabulary this concept borrows ("readiness probe" is literally
  their term).
- [Ollama API reference](https://github.com/ollama/ollama/blob/main/docs/api.md) — `/api/tags`
  (presence) vs `/api/generate` (the real thing).

### Micro-exercises
1. In `d0`, add a fourth name, "Dave," who is NOT in `DIRECTORY` at all. Predict what
   `is_listed("Dave")` and `actually_works("Dave")` return before running it.
2. Change `WHO_ACTUALLY_ANSWERS` to include all three names. Run it again — what does this
   simulate about a healthier system?
3. In a scratch copy of `src/generation/setup.py`, comment out lines 76-97 (the whole real-call
   block) and just `return GenerationStatus(ready=True, model=model)` right after the presence
   check. Run `python -m src.generation.setup` for real. Does it still say "ready"? What
   information is now MISSING from that message compared to before?
4. Stop Ollama's container (`docker compose stop ollama`) but leave the model "pulled" (its data
   is on a Docker volume, so it survives). Run the real CLI. Which of the two checks fails, and
   what does the message say?
5. Design, on paper, a readiness probe for a DIFFERENT kind of dependency this project already
   has (pick Qdrant or Neo4j). What's the cheap "presence" check, and what's the one real,
   minimal operation that would prove it actually works?

**How it works internally.** There is no framework or library doing anything special here —
`health_check` is just an ordinary function that happens to make TWO real network calls in
sequence, each one only running if the previous one succeeded, and each one translating a real
failure into a `GenerationStatus` value instead of letting an exception escape. "Probe" describes
the INTENT of the second call, not any special mechanism.

### How to validate this yourself
Run `python -m src.generation.setup` for real right now, and read the printed line — does it
include a measured `response_seconds` and a real `sample_reply`? Those two fields only exist
because a REAL call happened; a presence-only check couldn't have produced them. Then read
`tests/generation/test_setup.py::test_not_ready_when_a_pulled_model_fails_to_actually_respond`,
which builds a fake client that IS listed but raises on `generate()` — proving the code
distinguishes "listed" from "working," not just asserting it.

### How to visualize this
Draw two boxes in a row: "Ask the directory" (fast, cheap, arrow labelled `list()`) then "Actually
call" (slow, real, arrow labelled `generate()`). Only draw the "ready ✅" outcome AFTER both boxes,
never after the first one alone.

**You should now be able to explain:** why "the model is pulled" and "the model works" are
different claims, and which single line in this file is the only one that can tell them apart.

---

## C2 — Host vs. container networking

### Do this first
Run [`d1_host_vs_container_networking.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d1_host_vs_container_networking.py).
**Predict first:** this project's `docker-compose.yml` sets `OLLAMA_URL: "http://ollama:11434"`
for its `api` container. Will that same address work if you type it into THIS host machine's own
Python?

### 1. Definition + analogy
**What it is.** Docker Compose gives every container running together its own private network,
and on that network, each service can be reached by its **service name** (`ollama`, `neo4j`,
`qdrant`) — Docker runs its own internal DNS that only containers on that network can see. Your
host machine (where you actually run Python scripts, tests, and CLIs from) was never invited onto
that private network, so those names mean nothing to it. `localhost` always means "this machine
itself," from any vantage point, which is why it's the one name that works from the host.

**Analogy.** A large office building has an internal phone extension system: dial "204" and you
reach the finance department, but that only works from a phone *inside the building*. If you're
calling from your own home phone, "204" means nothing — you need the building's real, external
phone number instead.

### For a 14-year-old
Imagine a big office building where every employee's desk phone can reach any other desk just by
dialing a short 3-digit extension, like "204" for the finance department. This works great — but
ONLY for phones that are actually plugged into the building's own internal phone system. Now
imagine you're calling from your own phone at home, and someone tells you "just dial 204 to reach
finance." You dial it... and nothing happens, because your home phone was never connected to that
building's internal system in the first place. "204" isn't a real, universal phone number — it
only means something to phones already inside that one building's network. To reach finance from
home, you'd need their REAL external number instead, something like a full phone number that
works from anywhere.

Docker does exactly this: when `docker-compose.yml` starts `ollama`, `neo4j` and `qdrant`
together, Docker builds them their OWN private "office building" (an internal network), and gives
each one a short "extension" — its service name, like `ollama`. Any container plugged into that
same building (like this project's own `api` container, when it eventually runs) can dial "ollama"
and reach it instantly. But your own computer, running Python scripts directly (not inside that
building), was never plugged into that internal system — it's calling from "home." For your host
machine, the ONLY number that always works is `localhost`, which just means "whichever phone I'm
holding right now" — it works from home, it works in the office, it works anywhere, because it
never depends on being inside any particular building's private system.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the internal 3-digit extension "204" | the Docker Compose service name `ollama` |
| the building's own private phone system | Docker's internal network + DNS |
| an employee's desk phone, inside the building | a container also defined in `docker-compose.yml` (e.g. the future `api` service) |
| your home phone, never plugged into that system | this host machine, running `python -m src.generation.setup` directly |
| "204 means nothing from home" | `settings.ollama_url` (`"http://ollama:11434"`) failing to resolve from the host |
| "home" always reaches whoever holds the phone | `localhost` always means "this machine," from any vantage point |
| dialing the wrong number and getting silence, not a clear error | the real, exact error this project hit: `getaddrinfo failed` |

### 2. Why here specifically
Every script in this project (the CLI, every test, every deep-dive demo) runs on the HOST, never
inside a container — this project has no code that runs inside Docker yet. `Settings.ollama_url`'s
default (`"http://ollama:11434"`) was written for the OTHER case (code running inside Docker,
like the `api` service's environment block already sets up), so anything run from the host must
use a different address. `.env` solves this for `NEO4J_URI` (it's overridden to
`bolt://localhost:7687`) but was never given an equivalent override for `OLLAMA_URL`.

### What breaks if this changes
- **This project's own real bug:** `src/generation/setup.py`'s first version defaulted its CLI's
  `--ollama-url` to `settings.ollama_url` (mirroring `src/graph/setup.py`'s pattern for Neo4j).
  Running it for real failed immediately: `cannot reach Ollama: Failed to connect...`. The fix was
  hardcoding `"http://localhost:11434"` directly in the CLI, matching
  `src/embedding/embedder.py`'s and `src/embedding/lock.py`'s own, already-correct CLIs.
- **Trusting `settings.ollama_url` inside a live test:** the same bug, and it hid itself — my
  first two live tests used `build_client(settings.ollama_url)` too, so they failed for the exact
  same reason, and being WRONG in the same way as the code they were testing meant they couldn't
  catch it.
- **Hardcoding `localhost` INSIDE a container instead:** the opposite mistake — code that runs
  inside Docker (like a future `api` service) must use the service name, not `localhost`, because
  `localhost` inside a container means that container itself, not the host or any sibling service.

### [Interview relevance: very high — Docker networking (bridge networks, service discovery by name, the host-vs-container boundary) is one of the most common practical gaps between "works in my scripts" and "works when actually deployed," and a frequent AI-infra interview topic]

**Interview question:** *"Your Python script can't reach a service by the hostname your
`docker-compose.yml` uses for it. What's happening, and how do you fix it?"*
**A strong answer covers:** Docker Compose service names only resolve on that compose project's
own internal network, via Docker's built-in DNS; a process running on the host (not itself a
container on that network) can't resolve them; the fix is either running your script inside a
container attached to the same network, or using the address the host CAN reach the service by
(`localhost` plus the published port, from `docker-compose.yml`'s `ports:` mapping); a
config/settings layer often needs two different defaults for "runs on host" vs. "runs in a
container," and conflating them is an easy, recurring mistake (as this project's own history
shows).

### 3. Minimal code that explains the concept
The complete script, [`d1_host_vs_container_networking.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d1_host_vs_container_networking.py) (run from the project root):
```python
# Minimal demo of C2: a hostname that only exists INSIDE Docker's own network fails to
# resolve from this host process, while "localhost" (this machine itself) always works.
import socket  # the standard library's own name-resolution and connection tool

socket.setdefaulttimeout(2)  # refuses to let a resolution attempt hang this demo

CANDIDATES = [  # two ways settings.ollama_url or a CLI flag could name the same server
    ("ollama", "the Docker Compose SERVICE NAME -- only meaningful inside Docker's network"),
    ("localhost", "THIS machine, from THIS machine's own point of view"),
]

for host, meaning in CANDIDATES:  # tries to resolve each hostname in turn
    print(f'trying "{host}"  ({meaning})')  # explains what is about to be attempted
    try:
        address = socket.getaddrinfo(host, 11434)[0][4]  # asks the OS to resolve this name
        print(f"  -> RESOLVES to {address}")  # prints the real IP address it found
    except OSError as error:  # covers the OS being unable to find that name at all
        print(f"  -> FAILS to resolve: {error}")  # prints the REAL error this host gives

# `ollama` is a name Docker's own internal DNS invents for containers on its network --
# it means nothing to this host process, which never joined that network. `localhost`
# means "this machine" everywhere, which is why src/embedding/embedder.py's CLI (and now
# src/generation/setup.py's CLI) hardcode it as their default instead of trusting a
# setting whose default value was written for code running INSIDE a container.
```
Real output:
```
trying "ollama"  (the Docker Compose SERVICE NAME -- only meaningful inside Docker's network)
  -> FAILS to resolve: [Errno 11001] getaddrinfo failed
trying "localhost"  (THIS machine, from THIS machine's own point of view)
  -> RESOLVES to ('::1', 11434, 0, 0)
```
`[Errno 11001] getaddrinfo failed` is this exact machine's real error for "that hostname does not
exist anywhere I can look it up" — not a timeout, not a refused connection, a flat "I have never
heard of `ollama`."

### The exact code I wrote (`src/generation/setup.py`)
```python
# src/generation/setup.py, lines 101-117, verbatim
101 | def main(argv: list[str] | None = None) -> int:
102 |     """CLI: python -m src.generation.setup -- exit 0 if the local LLM is ready, 1 if not."""
103 |     parser = argparse.ArgumentParser(description="Check the local Ollama generation model is ready")
104 |     # builds this CLI's argument parser
105 |     # Hardcoded, like src/embedding/embedder.py and src/embedding/lock.py's own CLIs --
106 |     # settings.ollama_url defaults to "http://ollama:11434" (the Docker Compose service  # 🎯 explains WHY, referencing the two sibling CLIs that already got this right
107 |     # name), which only resolves from inside another container. Unlike NEO4J_URI, .env
108 |     # has no OLLAMA_URL override for a host-run script, so the CLI supplies one directly.
109 |     parser.add_argument("--ollama-url", default="http://localhost:11434")  # 🎯 hardcoded, host-reachable, unconditionally
110 |     parser.add_argument("--model", default=None, help="defaults to LLM_MODEL from .env")
111 |     args = parser.parse_args(argv)
112 | 
113 |     ollama_url = args.ollama_url
114 |     # uses the flag's own host-reachable default unless the caller overrode it
115 |     model = args.model or settings.llm_model
116 |     # picks the model name the caller gave, or the configured default
117 |     client = build_client(ollama_url)
```
**🎯 Interview-relevant lines:** line 109 looks like the simplest line in the whole file, and it's
the one that took a real, live failure to get right the first time.

### Canonical docs
- [Docker docs: networking overview](https://docs.docker.com/engine/network/) — bridge networks
  and container-to-container communication.
- [Docker Compose docs: networking](https://docs.docker.com/compose/how-tos/networking/) — how
  Compose gives every project its own network and resolves service names by DNS.

### Micro-exercises
1. In `d1`, add a third candidate: `("neo4j", "another Compose service name")`. Predict, then run:
   does it resolve from this host?
2. Run `docker compose exec ollama getent hosts ollama` (or, if `getent` isn't in that image, `cat
   /etc/hosts`) — from INSIDE the `ollama` container. Does `ollama` resolve there? What does that
   prove about where this name is meaningful?
3. Open `docker-compose.yml` and find the `ports:` section under the `ollama` service. What does
   `"11434:11434"` mean, and why is THAT the reason `localhost:11434` works from the host at all?
4. Temporarily change `src/core/config.py`'s `ollama_url` default to `"http://localhost:11434"`
   and re-run the FULL test suite. Does anything fail? What does that tell you about which code
   paths actually depend on the container-only default versus which don't?
5. Find the `NEO4J_URI` line in `.env`. Write, in your own words, why an equivalent `OLLAMA_URL`
   line was never added — and whether adding one now would have prevented this step's bug.

**How it works internally.** `socket.getaddrinfo` is the same low-level name-resolution call every
networking library in Python eventually goes through. Docker's internal DNS server only answers
queries that arrive FROM containers on its own virtual network (implemented as a Linux bridge
network under the hood); a query from the host's own network stack never reaches it, so the OS
falls back to normal DNS/hosts-file resolution, which has never heard of `ollama` either, hence
the clean "does not exist" error rather than a slow timeout.

### How to validate this yourself
`docker network inspect` the project's own network (find its name with `docker network ls | grep
advance`) and read the `Containers` section — every container listed there is exactly what can
resolve every OTHER container's service name. Anything not listed (your host shell, this Python
process) cannot. Compare that list to the output of `d1`'s own resolution attempt.

### How to visualize this
Draw a large rectangle labelled "Docker's private network" containing three small boxes: `ollama`,
`neo4j`, `qdrant`, each with arrows connecting them to each other (they can all reach each other by
name). Draw your host machine as a SEPARATE box entirely OUTSIDE the rectangle, with only one thin
arrow going IN — through the specific published ports (`11434`, `7687`, `6333`) — labelled
`localhost:<port>`, and no arrow at all using the service names.

**You should now be able to explain:** why a Docker Compose service name works from a sibling
container but not from your own terminal, and which exact address this project's own CLIs use
instead, and why.

---

## C3 — Shared resource budgets under a VM-backed runtime

### Do this first
Run [`d2_docker_memory_budget.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d2_docker_memory_budget.py).
**Predict first:** this host machine has 15.7 GB of RAM. What memory LIMIT do you expect
`docker stats` to report for a single container?

### 1. Definition + analogy
**What it is.** Docker Desktop on Windows doesn't run containers directly on Windows — it runs a
real Linux virtual machine (via WSL2) and every container lives inside THAT VM. The VM itself has
its OWN memory ceiling, set by Docker Desktop (roughly half the host's RAM by default, unless a
`.wslconfig` file says otherwise) — and every container sharing that VM competes for space inside
that one, smaller budget, not the host's full amount.

**Analogy.** Renting a shared studio apartment with two roommates is not the same as having the
whole house to yourself, even if the house itself is huge — the apartment's own four walls are
what actually limits how much furniture fits, no matter how big the house around it is.

### For a 14-year-old
Imagine your family's house has a huge amount of storage space overall. But you and your two
siblings share ONE storage closet in the hallway, and that closet has a hard physical size — it
doesn't matter how big the rest of the house is, only what fits in THAT closet. If you bring home
a giant box (say, new sports equipment) and cram it into the closet, there might not be room left
for your siblings' stuff anymore — even though there's tons of free space elsewhere in the house
that closet just can't reach.

Docker Desktop on Windows works exactly like that shared closet. Your computer might have 16 GB of
memory total, but Docker Desktop doesn't give every container access to all of it — it builds one
shared "closet" (a virtual Linux computer running inside Windows, called WSL2) with its OWN fixed
size, usually around half your computer's total memory, and EVERY container (Ollama, Neo4j,
Qdrant, all three) has to fit their stuff into that ONE shared closet together. When Ollama loaded
a big AI model, it was like shoving a giant box into the closet — and there wasn't room left for
Neo4j's and Qdrant's things, so the "landlord" (an automatic memory-management system called the
OOM killer) came in and literally threw two of the smaller boxes (Neo4j and Qdrant) out to make
room, without asking anyone first.

**Mapping the story back to the code:**
| In the story | In the real infrastructure |
|---|---|
| the whole house's total storage | this host machine's real RAM (15.7 GB) |
| the one shared hallway closet | Docker Desktop's WSL2 virtual machine |
| the closet's own fixed size, regardless of the house | the VM's own memory limit (~7.6 GB, `docker stats` confirms it) |
| the giant box that barely fits | Mistral loaded into RAM inside the `ollama` container (5-6 GB) |
| the landlord throwing out smaller boxes without asking | the Linux kernel's OOM killer choosing victims when the VM runs out of memory |
| the two smaller boxes that got thrown out | `advancerag-neo4j-1` and `advancerag-qdrant-1`, both `Exited (255)`, no shutdown log |
| checking the closet's real size before buying a giant box | running `docker stats` BEFORE assuming a model "fits" |

### 2. Why here specifically
This is the one concept in this deep dive that isn't a line of code at all — it's a fact about the
infrastructure this project's `docker-compose.yml` runs on, discovered only by actually running
the readiness check for real and watching two unrelated containers go down. Every previous phase
of this project ran Neo4j, Qdrant and Ollama together without incident, because `nomic-embed-text`
(the only model pulled until this step) is only 0.27 GB — tiny enough that the shared VM budget
was never seriously tested. A 4.4 GB generation model was the first thing in this whole project
large enough to actually hit that ceiling.

### What breaks if this changes
- **Assume host RAM (15.7 GB) is the real budget:** exactly the mistake made in this step's first
  pass — "fits in 15.7 GB" is true and irrelevant, because nothing in this setup ever gets access
  to the full 15.7 GB at once.
- **Check memory only at steady state, after everything has settled:** would have shown Ollama at
  a stable, survivable 65-80% and missed the real cause — the crash happened during the brief,
  HIGHER peak of the cold model load itself, not the calmer state afterward.
- **Raise the VM's own memory ceiling (`.wslconfig`):** would genuinely fix the room problem, but
  it's a change to the HOST's own system configuration, not this project's code — deliberately
  left as an open choice for the user rather than made unilaterally.

### [Interview relevance: very high — "how much memory does your model actually need, and where does it actually run" is a central, practical question for anyone deploying local or self-hosted LLMs, and "the container's limit isn't the host's limit" is a common, expensive misunderstanding in real deployments]

**Interview question:** *"You're deploying a self-hosted LLM in Docker and it works fine alone,
but crashes other services when all three run together. What would you check?"*
**A strong answer covers:** the actual memory ceiling of the container RUNTIME itself (not just
the host's specs) — on Docker Desktop for Windows/Mac that's a VM with its own budget; measure
with `docker stats`, not assumptions; check the PEAK during model loading, not just steady state,
since loading can transiently need more than the settled amount; know that when a shared memory
budget is exceeded, the OOM killer may kill a DIFFERENT process than the actual memory hog; and
name the real fixes: a smaller/quantized model, raising the VM's memory allocation, or giving the
heavy service its own dedicated resources.

### 3. Minimal code that explains the concept
The complete script, [`d2_docker_memory_budget.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d2_docker_memory_budget.py) (run from the project root):
```python
# Minimal demo of C3: Docker Desktop's WSL2 VM has ITS OWN memory ceiling, separate from
# (and smaller than) this host machine's real RAM -- read live with `docker stats`, the
# exact command that first surfaced this during P6-M1.
import subprocess  # lets this script run a real, read-only docker command

CONTAINERS = ["advancerag-ollama-1", "advancerag-neo4j-1", "advancerag-qdrant-1"]
# the three services this project's docker-compose.yml defines

result = subprocess.run(  # runs the real `docker stats` command, once, no streaming
    ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}", *CONTAINERS],
    capture_output=True,
    text=True,
)
print("LIVE right now (docker stats --no-stream):")  # heading for the real, current reading
print(result.stdout or "(docker not reachable, or these containers are not running)")  # the real output

print("\nRECORDED during this project's P6-M1 (from decision.md's own correction note):")
# a frozen, documented reading from the actual incident -- not re-run here on purpose,
# since reproducing the crash on demand isn't something a demo script should risk doing
INCIDENT_READING = {
    "before Mistral was pulled": {"ollama": "121 MiB / 7.6 GiB (1.6%)"},
    "warm, after the crash and recovery": {
        "ollama": "5.97 GiB / 7.6 GiB (78.5%)",
        "neo4j": "894 MiB / 7.6 GiB (11.5%)",
        "qdrant": "139 MiB / 7.6 GiB (1.8%)",
    },
}
for moment, readings in INCIDENT_READING.items():  # walks the two recorded snapshots
    print(f"  {moment}:")  # names which moment this snapshot is from
    for name, usage in readings.items():  # walks each container's reading at that moment
        print(f"    {name:<8} {usage}")  # prints the recorded real usage

print(
    "\nThe crash happened BETWEEN these two snapshots -- during Mistral's cold load, "
    "briefly higher than either steady-state reading, which pushed the shared ~7.6 GiB "
    "VM budget over the edge and took Neo4j and Qdrant down with it (see notes/step-6.1)."
)
```
Real output:
```
LIVE right now (docker stats --no-stream):
advancerag-ollama-1	5.181GiB / 7.599GiB	68.18%
advancerag-neo4j-1	990.3MiB / 7.599GiB	12.73%
advancerag-qdrant-1	94.75MiB / 7.599GiB	1.22%


RECORDED during this project's P6-M1 (from decision.md's own correction note):
  before Mistral was pulled:
    ollama   121 MiB / 7.6 GiB (1.6%)
  warm, after the crash and recovery:
    ollama   5.97 GiB / 7.6 GiB (78.5%)
    neo4j    894 MiB / 7.6 GiB (11.5%)
    qdrant   139 MiB / 7.6 GiB (1.8%)

The crash happened BETWEEN these two snapshots -- during Mistral's cold load, briefly higher than either steady-state reading, which pushed the shared ~7.6 GiB VM budget over the edge and took Neo4j and Qdrant down with it (see notes/step-6.1).
```
The "LIVE right now" numbers are whatever this machine reports at the moment you run it — they
will differ from the frozen "RECORDED" incident numbers, which are kept exactly as measured during
the real crash so the comparison stays honest.

### The exact code
There is no source line to quote — this finding came from running commands, not from reading or
writing Python. The commands themselves, exactly as run during the real incident:
```bash
docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"
docker ps -a --filter name=advancerag --format "{{.Names}}\t{{.Status}}"
docker logs advancerag-neo4j-1 --tail 15
docker logs advancerag-qdrant-1 --tail 15
```
**🎯 Interview-relevant:** the honest answer to "how did you find this" is "I ran the real check,
watched real containers exit, and read real logs" — not "I calculated it in advance." Saying that
plainly, with the actual commands, is itself a strong interview signal.

### Canonical docs
- [Docker Desktop docs: WSL2 resource limits](https://docs.docker.com/desktop/settings-and-maintenance/settings/#resources)
  — how Docker Desktop's WSL2 backend allocates memory, and how `.wslconfig` overrides it.
- [Microsoft docs: `.wslconfig` reference](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#configuration-setting-for-wslconfig)
  — the exact file and settings that would raise this budget.
- Linux kernel docs, [OOM killer](https://www.kernel.org/doc/gorman/html/understand/understand016.html)
  — how a memory-constrained Linux system (the WSL2 VM, here) chooses what to kill.

### Micro-exercises
1. Run `d2` right now and compare the LIVE numbers to the RECORDED incident numbers. Is Ollama's
   memory usage closer to the "before" or the "warm, after" snapshot?
2. Run `docker stats` (without `--no-stream`) in one terminal, then in another run
   `python -m src.generation.setup`. Watch Ollama's memory number live during the call — does it
   change at all for a WARM call (model already loaded)?
3. Find this machine's total RAM (already known: 15.7 GB) and the VM budget `docker stats` shows
   (~7.6 GB). Compute the percentage. Is it closer to Docker Desktop's documented default of "half
   the host," or something else?
4. Read `docker logs advancerag-neo4j-1 --tail 20` yourself right now. Do you see any explicit
   "shutting down" or "received SIGTERM" message anywhere? What does an ABSENCE of such a message
   suggest about how a process ends?
5. On paper, calculate: if Ollama needs ~6 GB warm, Neo4j configured up to ~1.5 GB, and Qdrant a
   few hundred MB, how much spare margin does that leave inside a 7.6 GB budget? Is "it currently
   fits" the same claim as "it will always fit," given real-world variance?

**How it works internally.** WSL2 runs a real, lightweight Linux kernel inside a Hyper-V virtual
machine; Docker Desktop configures that VM's own memory allocation (via `.wslconfig`, or a default
if none exists) completely independently of whatever the Windows host itself has. Every container
Docker runs is a Linux process INSIDE that one VM, all sharing that VM's single Linux kernel and
memory manager — which is exactly why the VM's own kernel-level OOM killer, not Windows', is what
acts when total memory demand exceeds the VM's ceiling, and why it can pick ANY process in that VM
as a victim, not necessarily the one that caused the pressure.

### How to validate this yourself
`docker stats` (shown above) is the primary tool. `docker inspect <container> --format
"{{.HostConfig.Memory}}"` shows whether a container has its OWN explicit memory limit set (this
project's `docker-compose.yml` sets none, so every container's limit IS the shared VM's total).
`docker ps -a` with `--format "{{.Names}}\t{{.Status}}"` shows `Exited (255)` for a killed
container versus a clean exit code for a deliberately stopped one. `docker logs <container> --tail
N` — a graceful shutdown logs its own shutdown sequence; an OOM kill just stops mid-stream, as
happened here.

### How to visualize this
Draw three nested rectangles: the outermost is "this Windows host, 15.7 GB." Inside it, a smaller
rectangle labelled "Docker Desktop's WSL2 VM, ~7.6 GB." Inside THAT, three small boxes for
`ollama`, `neo4j`, `qdrant`, sized roughly proportional to their real measured memory (Ollama's box
should look 5-6x bigger than Qdrant's). Shade the WSL2 rectangle to show it's nowhere near the
outer one's size — that gap is the entire lesson.

**You should now be able to explain:** why "fits in my computer's RAM" is not the same claim as
"fits in Docker's budget," and what evidence (not assumption) would tell you the difference.

---

## C4 — Bounded timeouts on network calls

### Do this first
Run [`d3_bounded_timeout.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d3_bounded_timeout.py).
**Predict first:** a toy server always answers, but only after 3 seconds. One caller gives up
after 1.5 seconds; another is willing to wait 10. Which one gets a real answer?

### 1. Definition + analogy
**What it is.** A "timeout" is a promise a caller makes to itself: "I will wait AT MOST this long
for an answer, and if it doesn't come, I will stop waiting and report a failure, rather than
waiting indefinitely." `PROBE_TIMEOUT_SECONDS = 120.0` bounds every request `build_client`'s
`ollama.Client` makes — a hung or wedged Ollama fails this check in about two minutes, not never.

**Analogy.** Calling a friend and deciding in advance "I'll let it ring for 30 seconds, then hang
up and try texting instead" — versus letting the phone ring forever on the off chance they answer.

### For a 14-year-old
Imagine you call a friend's phone. You could let it ring literally forever, just in case they
eventually pick up — but that's a bad plan, because you'd never know if they were just running
late to the phone, or if their phone was broken and would NEVER be answered. Either way, you'd be
stuck holding the phone to your ear indefinitely, unable to do anything else, with no way to tell
those two situations apart.

A smarter plan: decide IN ADVANCE how long you're willing to wait — say, 30 seconds — and if there's
no answer by then, hang up and assume something's wrong (busy, broken, not home), and go try
something else (text them, call someone else). You might occasionally hang up JUST before they
would have picked up — that's the real cost of a timeout — but in exchange, you're NEVER stuck
waiting forever, and you find out something is wrong in a predictable amount of time instead of an
unknown one.

`PROBE_TIMEOUT_SECONDS = 120.0` is exactly this decision, made in advance: "I'll give the model two
minutes to answer. If Ollama is completely wedged (not just slow), I'd rather know that in two
minutes than have this health check hang forever."

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| deciding "I'll wait 30 seconds, then hang up" | `PROBE_TIMEOUT_SECONDS = 120.0`, line 24 |
| the phone ringing without an answer | Ollama accepting a connection but never finishing a response |
| hanging up and assuming something's wrong | `httpx.TimeoutException` (surfaced through `ollama`'s client) |
| letting it ring forever, just in case | NOT setting a timeout at all — the rejected alternative |
| "I might hang up right before they'd have answered" | a slow-but-fine Ollama being reported as a failure if the bound is set too low |
| applying the SAME 30-second rule to every call you make that day | `timeout=PROBE_TIMEOUT_SECONDS` applied to the WHOLE client (`build_client`, line 48), not just one call |

### 2. Why here specifically
Generation is the slowest operation this project has ever performed (this step's own measurement:
68.8 seconds cold, dominated by loading a 4.4 GB model). A readiness check that hangs indefinitely
on a truly broken Ollama would be worse than useless — it would look identical to "still loading, be
patient" from the outside, with no way to tell the two apart. 120 seconds is deliberately generous
(comfortably above the measured 68.8 second cold load) while still being a real, finite bound.

### What breaks if this changes
- **No timeout at all** (the SDK's own default, if `build_client` didn't set one): a genuinely
  wedged Ollama container would make this check — and anything that calls it, like a future CI
  step — hang forever, with no distinction between "slow" and "broken."
- **Too short a timeout** (say, 5 seconds): the measured 68.8-second cold load would ALWAYS be
  reported as a failure, even though the model is perfectly healthy — just not warm yet.
- **A timeout only on `generate()`, not on `list()`** (an earlier possible design): the presence
  check itself could still hang forever if Ollama's HTTP layer is wedged before generation is ever
  attempted; `build_client`'s timeout applies to the whole client precisely to avoid this gap.

### [Interview relevance: high — timeouts, and specifically "what should your timeout budget be for a call that might legitimately take a long time (cold model load, cold cache, cold start)," come up constantly in production ML-serving interviews]

**Interview question:** *"How do you choose a timeout for a call to a service whose response time
varies a lot, like a model that's sometimes cold and sometimes warm?"*
**A strong answer covers:** measure the real worst-case (here: cold load, 68.8s) before picking a
number; set the bound comfortably above that measured worst case, not an arbitrary guess; know the
cost of getting it wrong in either direction (too short: false failures on legitimately slow but
healthy calls; too long / none: hangs that look identical to "just slow"); and that a health check
specifically should fail within a bounded, predictable window, since something else (a CI job, an
on-call alert) is usually waiting on ITS answer too.

### 3. Minimal code that explains the concept
The complete script, [`d3_bounded_timeout.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d3_bounded_timeout.py) (run from the project root):
```python
# Minimal demo of C4: a bounded timeout fails FAST and predictably; no timeout means
# waiting however long the other side takes -- shown here with a server that answers
# late on purpose, not never, so this demo stays short and safe to run.
import threading  # runs the toy slow server in the background while the demo continues
import time  # measures real elapsed time for both attempts
from http.server import BaseHTTPRequestHandler, HTTPServer  # a real, correct HTTP server

import httpx  # the same HTTP library the project's own Ollama client sits on top of

SLOW_SERVER_DELAY_SECONDS = 3  # how long the toy server waits before finally replying


class SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(SLOW_SERVER_DELAY_SECONDS)  # simulates a service that is just slow, not dead
        try:
            self.send_response(200)  # answers, eventually, with a normal HTTP 200
            self.end_headers()  # finishes the headers before writing the body
            self.wfile.write(b"OK")  # writes the real response body
        except (ConnectionAbortedError, ConnectionResetError):
            print("  (server side: tried to answer, but the bounded client had already left)")
            # this is the EXPECTED other half of the bounded-timeout story: the server was
            # never told to stop working, it just found nobody listening when it finished

    def log_message(self, *args):
        pass  # silences the server's own per-request log lines for this demo


def start_slow_server():
    server = HTTPServer(("127.0.0.1", 0), SlowHandler)  # a real local HTTP server, any free port
    threading.Thread(target=server.handle_request, daemon=True).start()  # serves exactly one request
    return f"http://127.0.0.1:{server.server_port}/"  # hands back this one server's own address


started = time.perf_counter()  # starts the bounded attempt's stopwatch
try:
    httpx.get(start_slow_server(), timeout=1.5)  # a SHORT, bounded timeout, its OWN fresh server
except httpx.TimeoutException as error:
    print(f"bounded  (timeout=1.5s): FAILED after {time.perf_counter() - started:.2f}s -- {type(error).__name__}")
    # fails fast and predictably, well before the server would ever have answered

started = time.perf_counter()  # starts the patient attempt's stopwatch
response = httpx.get(start_slow_server(), timeout=10.0)  # a longer timeout, a SEPARATE fresh server
print(f"patient  (timeout=10s):  SUCCEEDED after {time.perf_counter() - started:.2f}s -- {response.text}")
# succeeds, but only because this caller was willing to wait as long as it took

# PROBE_TIMEOUT_SECONDS = 120.0 in the real code picks a value in between: long enough for
# a real model to answer, short enough that a truly hung Ollama fails in minutes, not forever.
```
Real output:
```
bounded  (timeout=1.5s): FAILED after 1.69s -- ReadTimeout
  (server side: tried to answer, but the bounded client had already left)
patient  (timeout=10s):  SUCCEEDED after 3.02s -- OK
```
The bounded caller fails at ~1.5s, right at its own limit — not whenever the server happened to be
ready. The patient caller succeeds at ~3.0s — exactly when the (still slow, but not broken) server
finally answered.

### The exact code I wrote (`src/generation/setup.py`)
```python
# src/generation/setup.py, lines 20-24, verbatim
 20 | # A short, closed-ended prompt: enough to prove the model loads and answers coherently,
 21 | # without waiting on a long generation just to check readiness.
 22 | PROBE_PROMPT = "Reply with exactly one word: OK"
 23 | # how long the probe call may take before this check gives up and reports not ready
 24 | PROBE_TIMEOUT_SECONDS = 120.0  # 🎯 the ONE number that bounds every request this client will ever make
```
```python
# src/generation/setup.py, lines 46-50, verbatim
 46 | def build_client(ollama_url: str) -> ollama.Client:
 47 |     """A real Ollama client for the given URL, with a bounded probe timeout."""
 48 |     return ollama.Client(host=ollama_url, timeout=PROBE_TIMEOUT_SECONDS)  # 🎯 applied to the whole client at construction, not per-call
 49 |     # PROBE_TIMEOUT_SECONDS bounds every request this client makes, not just generate();
 50 |     # a hung Ollama container fails this check in ~2 minutes instead of hanging forever
```
**🎯 Interview-relevant lines:** line 48 is the entire feature — one keyword argument. What makes
it a real decision (not just a default left alone) is the comment explaining WHY 120 was chosen,
tied to a real measurement, not a guess.

### Canonical docs
- [httpx docs: timeouts](https://www.python-httpx.org/advanced/timeouts/) — the library
  `ollama.Client` is built on, and the exact timeout semantics it inherits.
- [Ollama Python SDK source](https://github.com/ollama/ollama-python) — confirms `timeout` is
  passed straight through to its underlying `httpx.Client`.

### Micro-exercises
1. In `d3`, change `SLOW_SERVER_DELAY_SECONDS` to `1` (faster than the bounded client's 1.5s
   timeout). Predict, then run: does the bounded call now succeed?
2. Change the bounded call's `timeout=1.5` to `timeout=0.1`. Run it. How much faster does it fail?
3. In a scratch copy of `src/generation/setup.py`, change `PROBE_TIMEOUT_SECONDS` to `2.0` and run
   `python -m src.generation.setup` for real against the (warm) Mistral. Does it still succeed?
   Now try it right after restarting the Ollama container (forcing a cold load) — what happens?
4. Read `httpx`'s own timeout docs and name the FOUR different timeout types it actually supports
   (connect, read, write, pool). Which one is actually responsible for `d3`'s failure?
5. Design, on paper, what should happen if `health_check`'s `generate()` call times out versus if
   it gets a real error response (like a 500). Are these currently handled the same way in the
   code? Should they be?

**How it works internally.** `httpx` (which `ollama.Client` uses internally) starts an internal
clock the moment a request begins, and if the full round trip hasn't completed by the configured
number of seconds, it actively cancels the in-flight network operation and raises
`httpx.TimeoutException` — the OS-level socket read is interrupted, not merely ignored, so no
background work is left silently running after the exception is raised.

### How to validate this yourself
Run `d3` and watch the wall-clock time printed next to each result — `time.perf_counter()` proves
the bound was actually respected, not just that SOME error eventually appeared. Against the real
project: run `python -m src.generation.setup` right after a fresh `docker compose restart ollama`
(forcing a cold load) and time it with the shell's own `time` command — confirm it completes well
under 120 seconds, with margin to spare.

### How to visualize this
A simple timeline: draw a horizontal line from 0 to 120 seconds. Mark the measured cold-load time
(68.8s) as a dot on that line, well before the 120s mark. The GAP between the dot and 120 is the
safety margin this decision deliberately bought.

**You should now be able to explain:** what a bounded timeout trades away (occasionally giving up
on something that was about to succeed) for what it buys (never hanging forever), and why 120
seconds specifically was chosen here rather than left as a guess.

---

## C5 — Protocol-typed client injection

### Do this first
Run [`d4_protocol_injection.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d4_protocol_injection.py).
**Predict first:** `FakeOllama` never mentions `GenerationClient` anywhere in its own definition.
Will `probe(FakeOllama(), ...)` work anyway?

### 1. Definition + analogy
**What it is.** `GenerationClient` describes only the TWO methods `health_check` actually calls
(`list`, `generate`) — nothing about the real `ollama.Client`'s dozen other methods. Any object
with those two methods, in that shape, can stand in for the real client, with no inheritance and
no relationship to `ollama.Client` at all. This is the same structural-typing idea used throughout
this project's retrieval code (`HybridSearcher`, `Reranking`), applied here to a service client
instead of another in-project class.

**Analogy.** A job posting that says "must be able to answer the phone and take a message" doesn't
care whether the applicant has ever worked in an office before — only that they can do those two
specific things.

### For a 14-year-old
Imagine a small shop needs someone to cover the front desk for an afternoon, and the only two
things that job actually requires are: answer the phone, and write down a message if the caller
asks. The shop owner doesn't care if the person is a professional receptionist, a substitute
teacher on their day off, or a robot someone's cousin built for a school project — as long as
whoever shows up can do those exact two things, they're qualified for the job. The shop owner never
wrote a rule saying "you must be an OFFICIAL Receptionist" — they only care about the two abilities.

That's exactly what `GenerationClient` says about anything handed to `health_check`: "I only need
you to be able to `list()` and `generate()`, in this exact shape. I genuinely don't care if you're
the real `ollama.Client` talking to a real server, or a tiny fake object a test built in three
lines that just returns made-up answers instantly." Both get hired for the job.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "must answer the phone and take a message" | `GenerationClient`'s two method signatures |
| the professional receptionist | the real `ollama.Client` |
| the substitute filling in for one afternoon | `FakeClient` in `tests/generation/test_setup.py` |
| "I don't care about your résumé, just these two skills" | no inheritance, no shared base class required |
| the shop owner never checking anyone's actual job title | `health_check`'s parameter is typed `client: GenerationClient`, never `client: ollama.Client` |

### 2. Why here specifically
`health_check` needed to be testable without a real Ollama server running — every one of this
project's 12 hermetic generation tests builds a small `FakeClient` instead of talking to a real
service. Protocol typing is what lets `mypy` confirm those fakes are legitimate stand-ins for the
real thing, purely by checking their method shapes, with zero coupling to `ollama.Client` itself.

### What breaks if this changes
- **Type the parameter as the concrete `ollama.Client`:** every hermetic test's `FakeClient` would
  structurally "shouldn't" satisfy that type hint (even though Python would still run it fine at
  runtime) — inviting a future edit to add real-client-specific logic that would silently break
  every fake.
- **This project's own real mismatch, found by mypy:** the first version of `GenerationClient`
  declared `generate(..., stream: bool = False)`. The REAL `ollama.Client.generate` is
  `@overload`-decorated, dispatching on `stream: Literal[False]` vs `Literal[True]` — a plain
  `bool` didn't match either overload precisely, and `mypy` refused to compile it. The fix was
  declaring `stream: Literal[False] = False` in the Protocol, matching the exact discriminant type
  the real overload uses (see docs/27 C1 for the earlier, related lesson that *extra* optional
  parameters are fine — this is the sharper case where the *type* of a SHARED parameter must
  match exactly).

### [Interview relevance: medium-high — "how do you test code that calls an external API without hitting the real API" is a standard question, and Protocol-typed injection is Python's own answer to it]

**Interview question:** *"How would you unit-test a function that calls a third-party SDK client,
without making real network calls in your tests?"*
**A strong answer covers:** depend on an abstraction (a `Protocol`, or an abstract base class) that
names only the methods actually used, not the whole SDK surface; inject the client as a parameter
rather than constructing it inside the function; tests supply a small, fast fake satisfying that
same shape; a real integration/live test still exists separately, gated to skip when the real
service isn't available; and — a sharper point — matching an SDK's `@overload`-decorated methods
exactly sometimes requires matching literal types precisely, not just "close enough" typing.

### 3. Minimal code that explains the concept
The complete script, [`d4_protocol_injection.py`](../practice%20codes/phase%206/m1_ollama_llm_setup_deep_dive/d4_protocol_injection.py) (run from the project root):
```python
# Minimal demo of C5: GenerationClient describes only the TWO methods health_check uses,
# so a tiny fake can stand in for the real ollama.Client -- and the fake's generate() must
# match the real one's exact stream=Literal[False] typing, not just "some bool".
from types import SimpleNamespace  # builds tiny stand-in objects with named fields
from typing import Literal, Protocol  # the tools this file's own Protocol is built from


class GenerationClient(Protocol):  # the EXACT shape src/generation/setup.py declares
    def list(self): ...  # only asks: can you list what is pulled?

    def generate(self, model: str, prompt: str, *, stream: Literal[False] = False): ...
    # only asks: can you generate, called this exact way?


class FakeOllama:  # never inherits from GenerationClient, never mentions it
    def list(self):
        model = SimpleNamespace(model="mistral:latest")  # a fake "model" entry
        return SimpleNamespace(models=[model])  # shaped just enough like ollama.ListResponse

    def generate(self, model, prompt, *, stream=False):
        return SimpleNamespace(response=f"(fake reply to: {prompt!r})")  # a fake, instant reply


def probe(client: GenerationClient, model: str) -> str:  # only trusts the Protocol's checklist
    names = {entry.model for entry in client.list().models}  # reads what the client says is pulled
    if model not in names:  # covers the model never being listed
        return f"{model} is not listed"  # reports without ever calling generate()
    reply = client.generate(model=model, prompt="hi", stream=False)  # calls the SAME way health_check does
    return reply.response  # hands back whatever the client answered


print(probe(FakeOllama(), "mistral:latest"))  # a FakeOllama satisfies the Protocol structurally
print(probe(FakeOllama(), "llama3.2:latest"))  # a model this fake never listed

# Try this by hand: change FakeOllama.generate's signature to `stream: bool = False`
# (plain bool, not Literal[False]) and run `mypy` on this file. It still passes here,
# because THIS Protocol also declares plain-ish typing -- the real project's Protocol is
# checked against ollama.Client's own @overload-decorated generate(), which is stricter;
# see docs/28 C5 for the exact mismatch mypy caught on the real class.
```
Real output:
```
(fake reply to: 'hi')
llama3.2:latest is not listed
```
`mypy` on this demo file itself reports no errors — its own `GenerationClient` copy deliberately
uses looser typing than the real project's, precisely to keep this demo focused on the *structural
typing* idea; the exact-`Literal` lesson is shown separately, against the real file, below.

### The exact code I wrote (`src/generation/setup.py`)
```python
# src/generation/setup.py, lines 36-43, verbatim
 36 | class GenerationClient(Protocol):
 37 |     """The part of `ollama.Client` the health check uses, so tests can pass a fake."""
 38 | 
 39 |     def list(self) -> ollama.ListResponse: ...  # 🎯 only this
 40 | 
 41 |     def generate(  # 🎯 and this -- nothing else about ollama.Client matters to this file
 42 |         self, model: str, prompt: str, *, stream: Literal[False] = False  # 🎯 the exact discriminant type the real, @overload-decorated method needs
 43 |     ) -> ollama.GenerateResponse: ...
```
And the real mismatch mypy caught while building this file (the ORIGINAL, rejected line, and the
fix that replaced it -- re-verified by reverting the fix and re-running mypy, below):
```python
# rejected: mypy error -- "Argument 1 to health_check has incompatible type Client; expected GenerationClient"
def generate(self, model: str, prompt: str, *, stream: bool = False) -> ollama.GenerateResponse: ...

# fixed: matches the real, @overload-decorated method's own discriminant type exactly
def generate(
    self, model: str, prompt: str, *, stream: Literal[False] = False
) -> ollama.GenerateResponse: ...
```
The real, complete `mypy` output against the rejected version, captured by actually reverting the
fix and re-running the type checker (not paraphrased):
```
src\generation\setup.py:119: error: Argument 1 to "health_check" has incompatible type "Client"; expected "GenerationClient"  [arg-type]
src\generation\setup.py:119: note: Following member(s) of "Client" have conflicts:
src\generation\setup.py:119: note:     Expected:
src\generation\setup.py:119: note:         def generate(self, model: str, prompt: str, *, stream: bool = ...) -> GenerateResponse
src\generation\setup.py:119: note:     Got:
src\generation\setup.py:119: note:         @overload
src\generation\setup.py:119: note:         def generate(self, model: str = ..., prompt: str = ..., ..., stream: Literal[False] = ..., ...) -> GenerateResponse
src\generation\setup.py:119: note:         @overload
src\generation\setup.py:119: note:         def generate(self, model: str = ..., prompt: str = ..., ..., stream: Literal[True] = ..., ...) -> Iterator[GenerateResponse]
```
**🎯 Interview-relevant lines:** the diff between those two versions IS the interview-relevant
fact: "extra optional parameters are fine" (the P5-M4 lesson) is a DIFFERENT rule from "a shared
parameter's exact type must match an overload's discriminant" (this step's own, sharper lesson).

### Canonical docs
- Python docs, [`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
- Python docs, [`typing.overload`](https://docs.python.org/3/library/typing.html#typing.overload)
  and [`typing.Literal`](https://docs.python.org/3/library/typing.html#typing.Literal) — the two
  features whose interaction caused this step's real mypy error.

### Micro-exercises
1. In `d4`, add a `FakeOllamaMissingGenerate` class with only a `list()` method (no `generate()`
   at all). Pass it to `probe(...)`. Predict the error, then run it — is it a `mypy` error, a
   Python `AttributeError`, or both, depending on whether you type-check first?
2. Change `FakeOllama.generate`'s signature to `def generate(self, model, prompt, stream=False)`
   (no `*`, making `stream` positional-or-keyword instead of keyword-only). Run `mypy` on the demo
   file. Does it still pass? Why might keyword-only matter here?
3. Copy the REAL `GenerationClient` from `src/generation/setup.py` into a scratch file, along with
   a fake whose `generate` uses plain `stream: bool = False`. Run `mypy` on it and reproduce the
   real error this step hit.
4. Read `tests/generation/test_setup.py`'s `FakeClient` class. List every method the real
   `ollama.Client` has that `FakeClient` does NOT implement. Why doesn't that matter here?
5. Design, on paper, a `GenerationClient` variant that also supports STREAMING (`stream=True`,
   returning an iterator instead of one response). What would the Protocol's `generate` method
   signature need to become to type both cases correctly?

**How it works internally.** `@overload` tells `mypy` "this one function actually behaves like
several different functions depending on its arguments' exact types" — at runtime Python only ever
sees the LAST, un-decorated implementation, but `mypy` uses the overload signatures to pick which
return type applies for a given call. When `GenerationClient` declares a Protocol method, `mypy`
checks whether the REAL implementation (here, `ollama.Client.generate`, overloaded) is compatible
with EVERY way the Protocol's signature could be called — and a plain `bool` parameter can't
prove it always lands on the `Literal[False]` overload specifically, while `Literal[False]` can.

### How to validate this yourself
Run `mypy src/generation/setup.py` — it passes clean today. Then, in a scratch copy, revert the
`Literal[False]` back to plain `bool` and run `mypy` again — watch the exact error reappear,
confirming this file's current state is not accidental.

### How to visualize this
Draw `GenerationClient` as a two-item checklist card. Draw three arrows pointing INTO that same
card: from `ollama.Client` (the real thing), from `FakeClient` (the test), and from `FakeOllama`
(this demo) — three unrelated shapes, all satisfying one small, shared checklist.

**You should now be able to explain:** why this file's tests never need a real Ollama server, and
the exact typing rule (`Literal[False]`, not `bool`) that made the real Protocol match the real
SDK's overloaded method.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **Predict, then trace.** Before running anything, write down what you expect
   `python -m src.generation.setup` to print, including roughly how long it will take, based on
   whether Mistral is currently warm or was just restarted.
2. **Watch it work, layer by layer.** Open three terminals.
   - Terminal 1: `docker stats` (no `--no-stream`, so it keeps updating live).
   - Terminal 2: `docker logs -f advancerag-ollama-1` (follows the server's own log in real time).
   - Terminal 3: run `python -m src.generation.setup`.
   Watch Terminal 3's process start, see the request land in Terminal 2's log, and see Terminal
   1's memory number for `ollama` respond (or not, if the model is already warm).
3. **Break C1 on purpose.** In a scratch copy, make `PROBE_PROMPT` an empty string (`""`). Run the
   real CLI. Does Ollama accept an empty prompt? Does `health_check` still correctly decide
   ready/not-ready either way?
4. **Break C2 on purpose.** Run `python -m src.generation.setup --ollama-url http://ollama:11434`
   from your host terminal. Read the real error. Does it match `d1`'s prediction exactly?
5. **Break C3 on purpose, safely.** Run `docker compose restart ollama` right before running the
   readiness check, while watching `docker stats` for all three containers in another terminal.
   Does memory spike the way it did during the real incident? (It may or may not reproduce — that
   uncertainty is itself part of the lesson: a resource-budget bug is not always deterministic.)
6. **Break C4 on purpose.** In a scratch copy, set `PROBE_TIMEOUT_SECONDS = 0.01`. Run the real
   CLI against the real, running Ollama. Read the exact error message — does it clearly explain
   WHY it failed, or does it look like a generic connection problem?
7. **Break C5 on purpose.** Write a fake client with `generate` missing the `*` (making `stream`
   positional), pass it through `health_check` directly in a scratch script (no test framework),
   and see whether it still works at runtime despite mypy possibly objecting.
8. **Put it back together.** Undo every change from steps 3-7, re-run
   `pytest tests/generation/ -v -p no:cacheprovider`, and confirm all 14 tests pass again.

If your predictions in steps 1, 3, 4 and 6 matched what actually happened, you understand not just
*that* this file works, but *why* each of its five decisions exists.
