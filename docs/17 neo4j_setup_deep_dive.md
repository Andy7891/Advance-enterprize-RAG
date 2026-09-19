# 17 — Neo4j Setup: Deep Dive (P4-M1)

> Every output block in this document was produced on 2026-09-19 by running the code against the live
> Neo4j **5.26.0 Community** container (`advancerag-neo4j-1`, fresh volume initialised from `.env`).
> Demos that write to the graph use a unique throwaway label and delete everything they created.
> Nothing prints the real password.
>
> For every concept you get:
> 1. a **definition** (what, key terms, how it fits, limits, analogy) and a **14-year-old** version;
> 2. a **minimal demo**, in which **every line has a comment saying what it does**, with its real output;
> 3. the **exact project code** built in P4-M1, as an **annotated copy**. Each line keeps its real line number,
>    and a `#▸` comment above it says what that line does. (The files themselves keep their shorter WHY-only
>    comments, following the project's code rule; the line-by-line annotation lives here.)
>
> Demos are in [`practice codes/phase 4/m1_neo4j_deep_dive/`](../practice%20codes/phase%204/m1_neo4j_deep_dive/). Run from the project root:
> ```bash
> .venv/Scripts/python "practice codes/phase 4/m1_neo4j_deep_dive/c1_property_graph.py"
> ```
> In Git Bash, prefix scripts that call `docker exec` with `MSYS_NO_PATHCONV=1` (the path-rewriting quirk from P1).

---

## 0. Plain-language summary

Phase 3 built **vector** retrieval, which finds text *similar* to a question. Its tests recorded two misses about
**specific things and how they relate** ("the Carllm product", "the CEO"). A **graph database** stores exactly that
kind of fact: `(Avery Lancaster)-[:CEO_OF]->(Insurellm)`. P4-M1 doesn't put any data in yet. It makes the database
**run correctly, securely and verifiably**:

```
.env  (gitignored secret)                       docker-compose.yml  (neo4j service)
 NEO4J_PASSWORD=<32 chars>  ─── compose ──►  NEO4J_AUTH=neo4j/<password>   (applied only on FIRST start of the volume)  C2 C3
 NEO4J_URI=bolt://localhost:7687             NEO4J_PLUGINS=["apoc"]                                                     C9
        │                                   NEO4J_server_memory_* = heap 512m-1G, page cache 512m                     C4 C8
        │                                   healthcheck: cypher-shell ... RETURN 1  (logs in)                         C5
        ▼                                                    │
 src/core/config.py  Settings ──► src/graph/setup.py         ▼
                                  build_driver() ──Bolt :7687──► Neo4j 5.26 ◄──HTTP :7474── Browser               C1 C6
                                  health_check(): connectivity, auth, version 5, APOC  ──► GraphStatus            C7
                                  CLI: python -m src.graph.setup  → exit 0 / 1
 tests/graph/test_setup.py: fake driver (hermetic) + read-only live tests; one database only (Community)           C10
```

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | The property graph model and Cypher | 🔴 **Core RAG** (the foundation of GraphRAG) |
| C2 | Neo4j in Docker: ports, volume, first-start-only settings | 🟡 Engineering |
| C3 | Secrets and configuration: `.env`, compose interpolation, pydantic-settings | 🟡 Engineering (interview-heavy) |
| C4 | How the Neo4j image turns `NEO4J_*` variables into config (and the failure it caused) | ⚪ Boilerplate (but it broke the start-up) |
| C5 | A healthcheck that logs in: `cypher-shell`, `$$`, shell parameter expansion | 🟡 Engineering |
| C6 | The Python driver: lazy driver, connectivity check, sessions, results, parameters | 🟡 Engineering |
| C7 | Error taxonomy and a health check that never raises | 🟡 Engineering |
| C8 | Memory: heap vs page cache | ⚪ Boilerplate (operations) |
| C9 | APOC: what the plugin adds, Core vs Extended | ⚪ Boilerplate (tooling) |
| C10 | Testing a single-database server: fakes, live tests, label isolation | 🟡 Engineering |

> **A note on the open question.** C6 and C7 walk through `build_driver`, `health_check` and `main` line by line,
> so they fully answer the P4-M1 Question 1 (the wrong-password path). A new control-flow question is at the end
> of this document instead.

---

## C1 — The property graph model and Cypher

### 1. Definition + analogy

**What it is.** Neo4j stores data as a **labelled property graph** with three building blocks:

| Part | What it is | Example | Written in Cypher as |
|---|---|---|---|
| **Node** | a thing | a person, a company, a product, a chunk of text | `(p)` |
| **Label** | a node's type (a node can have several) | `:Person`, `:Company` | `(p:Person)` |
| **Relationship** | a **typed, directed** connection between two nodes | `CEO_OF`, `SELLS` | `(p)-[:CEO_OF]->(c)` |
| **Property** | a key–value pair on a node **or** a relationship | `name: 'Avery'`, `since: 2015` | `{name: 'Avery'}` |

**Cypher** is the query language, and it's written as **ASCII-art patterns**: round brackets are nodes, arrows are
relationships. You describe the *shape* you want and Neo4j finds every place in the graph that has that shape:
```cypher
MATCH (p:Person)-[r:CEO_OF]->(c:Company)   // find this pattern
RETURN p.name, c.name, r.since             // return these values from each match
```
The main clauses: `CREATE` (make new nodes and relationships), `MATCH` (find a pattern), `RETURN` (choose output),
`WHERE` (filter), `DETACH DELETE` (delete nodes plus their relationships), `SHOW ...` (inspect settings, procedures,
databases), and `CALL` (run a procedure).

**Why this matters for RAG.** A vector index answers "what text is *like* this?". A graph answers "what is *connected*
to this, and how?". That makes graphs good at:
- **exact entity questions** ("who is the CEO?" means follow `CEO_OF`), where Phase 3's vector search failed;
- **multi-hop questions** ("which products does the company whose CEO is Avery sell?" means two hops);
- **explainability**: the path itself is the evidence.

GraphRAG uses both: the graph finds entities and connections, and vector search finds the passages that describe them.

**Key terms.** *Hop*: one relationship traversed. *Pattern*: a small graph shape written in Cypher. *Direction*: every
relationship has one; a query can respect it (`->`) or ignore it (`-`). *DETACH DELETE*: Neo4j refuses to delete a node
that still has relationships unless told to delete those too.

**Analogy.** A spider diagram on a whiteboard: circles with names (nodes, labelled by colour), arrows with verbs written
on them (relationships), and sticky notes on either (properties). Cypher is how you ask "find every red circle with an
arrow saying CEO_OF pointing to a blue circle".

### For a 14-year-old

Think of a map of your friends on a big poster.
- Each **circle** is a person, a club or a game. Those are **nodes**.
- Each circle has a **colour sticker** saying what kind of thing it is: "Person" (red), "Club" (blue). Those are **labels**.
- **Arrows** join circles, and each arrow has a word on it: "Alex **captains** Football Club", "Sam **plays** Minecraft".
  The arrow points one way, because Alex captains the club, not the other way round. Those are **relationships**.
- Little notes stuck on circles or arrows add details: Alex's age, *since when* Alex has been captain. Those are **properties**.

Now you can ask questions by **drawing a small picture** of what you want: "show me every red circle with a *captains*
arrow pointing to a blue circle". The computer finds every place on the poster where that picture fits. That's Cypher.

Why is this useful for our project? When someone asks "Who is the CEO?", instead of hunting through pages of text for
similar words (which went wrong in Phase 3), the computer can simply follow the **CEO_OF** arrow.

### 2. Why (vs the traditional approach)
In a relational database, "the company's CEO's products" needs joins across several tables and gets harder with every
hop. In a graph, each hop is just another arrow in the pattern, and following an arrow is a direct pointer lookup.

### 3a. Minimal demo — build a tiny graph, query it, clean up
`c1_property_graph.py`
```python
# C1: the property graph model and basic Cypher, on the LIVE Neo4j (self-cleaning).
import uuid                                                   # to make a label no other data can have

from src.graph.setup import build_driver                      # the project's driver factory (reads .env)

label = f"Demo_{uuid.uuid4().hex[:8]}"                        # a unique label isolates this demo's nodes

with build_driver() as driver, driver.session() as session:   # open a connection pool, then one session
    try:
        # CREATE: two nodes (with labels + properties) and one typed, directed relationship with a property
        session.run(
            f"CREATE (p:Person:{label} {{name: 'Avery Lancaster', born: 1985}})"
            f"-[:CEO_OF {{since: 2015}}]->"
            f"(c:Company:{label} {{name: 'Insurellm', founded: 2015}})"
        ).consume()                                           # consume() waits for the write to finish
        # CREATE a second company-product relationship, reusing the company node found by MATCH
        session.run(
            f"MATCH (c:Company:{label} {{name: 'Insurellm'}}) "
            f"CREATE (c)-[:SELLS]->(:Product:{label} {{name: 'Carllm', line: 'auto'}})"
        ).consume()

        # MATCH a pattern: who is CEO of which company, and since when?
        record = session.run(
            f"MATCH (p:Person:{label})-[r:CEO_OF]->(c:Company) RETURN p.name AS person, c.name AS company, r.since AS since"
        ).single()                                            # exactly one row expected
        print("1. CEO pattern      :", record.data())         # .data() turns a Record into a dict

        # Two hops: which products does the company whose CEO is Avery sell?
        rows = session.run(
            f"MATCH (:Person:{label} {{name: 'Avery Lancaster'}})-[:CEO_OF]->(c)-[:SELLS]->(prod) "
            "RETURN c.name AS company, prod.name AS product"
        ).data()                                              # all rows as a list of dicts
        print("2. two-hop pattern  :", rows)

        # Direction matters: the same pattern written backwards finds nothing
        backwards = session.run(f"MATCH (p:Person:{label})<-[:CEO_OF]-(c) RETURN count(*) AS n").single()["n"]
        print("3. reversed arrow   :", backwards, "matches")

        # Labels, relationship types and property keys that now exist for this demo
        schema = session.run(
            f"MATCH (n:{label}) RETURN labels(n) AS labels, keys(n) AS properties ORDER BY n.name"
        ).data()
        print("4. nodes            :", schema)
    finally:
        # DETACH DELETE removes the nodes AND their relationships; plain DELETE would refuse
        removed = session.run(f"MATCH (n:{label}) DETACH DELETE n RETURN count(n) AS n").single()["n"]
        print("5. cleanup          :", removed, "nodes deleted; label", label, "left nothing behind")
```
Real output:
```
1. CEO pattern      : {'person': 'Avery Lancaster', 'company': 'Insurellm', 'since': 2015}
2. two-hop pattern  : [{'company': 'Insurellm', 'product': 'Carllm'}]
3. reversed arrow   : 0 matches
4. nodes            : [{'labels': ['Person', 'Demo_2178ab49'], 'properties': ['born', 'name']}, {'labels': ['Demo_2178ab49', 'Product'], 'properties': ['name', 'line']}, {'labels': ['Demo_2178ab49', 'Company'], 'properties': ['name', 'founded']}]
5. cleanup          : 3 nodes deleted; label Demo_2178ab49 left nothing behind
```
**How to read it.**
- **Line 1:** the pattern `(Person)-[:CEO_OF]->(Company)` returns exactly the question P3 couldn't answer: the CEO,
  the company, and a property *on the relationship* (`since: 2015`).
- **Line 2:** two arrows in one pattern is a two-hop query. Its answer (Insurellm sells Carllm) connects the two P3 misses.
- **Line 3:** the same arrow reversed matches nothing, because direction is part of the data.
- **Line 4:** every node has two labels (its type plus the demo's unique label); `keys(n)` lists its properties.
- **Line 5:** `DETACH DELETE` removed all 3 nodes and their 2 relationships. The `finally` makes sure cleanup runs even
  if a query above fails.
- Why `{{ }}`: inside a Python f-string, `{{` prints a literal `{`, which Cypher needs for property maps.

### 3b. Exact project code — the Cypher that P4-M1 runs (annotated copy of `src/graph/setup.py`)
```python
#▸ L69: open a session on the named database ("neo4j") and close it automatically at the end of the block
69          with driver.session(database=database) as session:
#▸ L70: send a Cypher query and keep the Result object it returns
70              component = session.run(
#▸ L71: CALL a built-in procedure that lists the server's components; YIELD picks two of its output columns
71                  "CALL dbms.components() YIELD versions, edition "
#▸ L72: take the first version string from the list and name the columns "version" and "edition"
72                  "RETURN versions[0] AS version, edition"
#▸ L73: expect exactly one row and return it as a Record (indexable like a dict)
73              ).single()
...
#▸ L109: call the APOC function apoc.version(); if APOC is loaded it returns e.g. "5.26.0" in the "version" column
109         return session.run("RETURN apoc.version() AS version").single()["version"]
```
**How it fits.** P4-M1 only **reads** from the graph (no data yet): one `CALL` of a system procedure and one function
call. `CREATE`/`MATCH` on real entities starts in the next microsteps.

### 4. Benefits
Entity and relationship questions become exact pattern lookups; multi-hop questions are one query; the path explains the answer.

- **What breaks:** getting relationship direction wrong (silently empty results); plain `DELETE` on connected nodes
  (refused); building Cypher with string formatting from user input (injection, see C6).
- **Docs:** [Cypher manual](https://neo4j.com/docs/cypher-manual/current/) ·
  [Graph database concepts](https://neo4j.com/docs/getting-started/graph-database/) ·
  [Edge et al., GraphRAG (arXiv:2404.16130)](https://arxiv.org/abs/2404.16130)

### Micro-exercises
1. In the demo, write the two-hop query *without* direction (`-[:CEO_OF]-`). Does it still find Carllm?
2. Add a second person who is `CFO_OF` the company, and query "all people with any role at Insurellm".
3. Replace `DETACH DELETE` with `DELETE` in the cleanup. Read the error, then fix it.
4. Draw (on paper) the graph for three facts from `data/company/about.md`.
5. Run the demo's `CREATE` in the Browser (http://localhost:7474) and look at the graph picture.

**Internals.** Neo4j stores each relationship as a record with direct pointers to its start and end nodes and to the
next relationship of each node (index-free adjacency), so following an arrow costs the same however big the graph is.
**Checkpoint:** name the four building blocks of a property graph and write a two-hop Cypher pattern.

---

## C2 — Neo4j in Docker: ports, volume, and first-start-only settings

### 1. Definition + analogy

**What it is.** The `neo4j` service in `docker-compose.yml` runs Neo4j from the official **image** `neo4j:5.26.0`
(a **long-term-support** release in the 5.x line).

**Ports.** Neo4j listens on two ports, mapped from the container to your machine:
- **7474, HTTP:** the Neo4j **Browser** (a web UI for running Cypher and seeing graphs) and an HTTP query API.
- **7687, Bolt:** Neo4j's compact **binary protocol**, which the Python driver, `cypher-shell` and LangChain use. It's
  faster and supports streaming results.

**The volume.** Everything Neo4j stores (graph files, transaction logs, and the **user accounts** in the `system`
database) lives under `/data` inside the container. A Docker **named volume**, `neo4j_data`, is mounted there, so the
data **outlives the container**: stop it, delete it, recreate it, and the data is still there.

**First-start-only settings.** Some settings only matter while an **empty** volume is being turned into a database.
`NEO4J_AUTH=user/password` is one of them. On first start the image runs `neo4j-admin dbms set-initial-password`, which
writes the password into `/data/dbms/auth.ini`. On every later start the image *still* runs that command, but it has no
effect, because the account already exists. The container log says so, verbatim, on every start. This is why P4-M1
found the P1-era volume still using `changeme` and **rejecting** the new `.env` password: the new value was ignored,
silently, except for that one log line. The fixes are either **reset the volume** (what we did, since it was empty) or
**change the password inside the database** with `ALTER CURRENT USER SET PASSWORD FROM 'old' TO 'new'`.

**Analogy.** Setting up a new phone: you choose the lock-screen PIN **during first setup**. Later, writing a new PIN on a
sticky note and putting it on the phone changes nothing. You have to go into Settings and change it there.

### For a 14-year-old

Imagine a school locker with a **combination lock** you set yourself.
- The first day, the locker is empty and you choose the code: 1-2-3-4. That's the **first start**.
- A week later you decide your code should be 9-8-7-6, and you write it on your hand. Does the locker now open with
  9-8-7-6? **No!** The lock remembers 1-2-3-4. Writing a new code on your hand doesn't reprogram the lock.
- To really change it, you open the locker with the **old** code and **reset** it from the inside. Or, if the locker is
  empty anyway, you ask the caretaker to give you a **brand-new** locker and set the new code on day one.

Our database is the locker, the password in `.env` is the code written on your hand, and the "data volume" is the
locker's memory. Because the old locker was **empty** (nothing stored yet), we got a brand-new one, and it took the
new code on its first day.

And the two **doors** of the building: door **7474** is for people with a web browser (you can look around), door **7687**
is the fast service entrance that programs use.

### 2. Why (vs the traditional approach)
Installing Neo4j directly on Windows means Java versions, services and paths per machine. The container gives every
developer the same pinned version, and the named volume keeps data separate from the throwaway container.

### 3a. Minimal demo — evidence of the first-start-only rule
`c2_first_start_only.py`
```python
# C2: NEO4J_AUTH is applied only when the data volume is FIRST created. Evidence from the live container.
import subprocess                                             # to call the docker CLI

CONTAINER = "advancerag-neo4j-1"                              # the compose container name


def sh(cmd):                                                  # run a shell command inside the container
    return subprocess.run(["docker", "exec", CONTAINER, "sh", "-c", cmd],
                          capture_output=True, text=True).stdout.strip()


# 1. The volume mounted at /data: the auth file lives in the "system" database area
print("1. /data contents      :", sh("ls /data").split())
print("   auth file present   :", sh("ls /data/dbms 2>/dev/null") or "(none)")

# 2. When was the volume initialised, and how often has the container started since?
created = subprocess.run(["docker", "volume", "inspect", "advancerag_neo4j_data", "--format", "{{.CreatedAt}}"],
                         capture_output=True, text=True).stdout.strip()
print("2. volume created at   :", created)

# 3. The entrypoint tries to set the initial password on EVERY start; the log explains why it has no effect
logs = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True)
lines = [l for l in (logs.stdout + logs.stderr).splitlines() if "only take effect" in l]
print("3. entrypoint message  :", lines[-1] if lines else "(not found)")
print("   seen", len(lines), "time(s) in this container's log")
```
Real output:
```
1. /data contents      : ['databases', 'dbms', 'server_id', 'transactions']
   auth file present   : auth.ini
2. volume created at   : 2026-09-19T11:39:15Z
3. entrypoint message  : Changed password for user 'neo4j'. IMPORTANT: this change will only take effect if performed before the database is started for the first time.
   seen 1 time(s) in this container's log
```
**How to read it.**
- **Line 1:** `/data` holds the databases, transaction logs and `dbms/auth.ini`, where the user accounts live. Because it's
  on the volume, the accounts survive container recreation.
- **Line 2:** this volume was created at 11:39 UTC on 2026-09-19, the fresh one after the reset. The P1 volume dated from
  2026-08-19.
- **Line 3:** the entrypoint's own warning. It printed "Changed password" (sounds like success) and then, in the same line,
  "only take effect … before … the first time". On an old volume, that message is the only hint that `.env` was ignored.

### 3b. Exact project code — the service definition (annotated copy of `docker-compose.yml`)
```yaml
#▸ L15: declare a service named "neo4j"; other containers reach it by this name (bolt://neo4j:7687)
15    neo4j:
#▸ L16: run the official Neo4j image, pinned to the exact 5.26.0 LTS release
16      image: neo4j:5.26.0
#▸ L17: start the list of host-to-container port mappings
17      ports:
#▸ L18: host port 7474 -> container 7474: the Browser UI and HTTP API
18        - "7474:7474" # Browser UI
#▸ L19: host port 7687 -> container 7687: the Bolt protocol the Python driver uses
19        - "7687:7687" # Bolt protocol
#▸ L20: start the container's environment variables
20      environment:
#▸ L24: initial "user/password"; user defaults to neo4j, password is REQUIRED from .env (see C3); first start only
24        NEO4J_AUTH: "${NEO4J_USER:-neo4j}/${NEO4J_PASSWORD:?set NEO4J_PASSWORD in .env (see .env.example)}"
...
#▸ L36: start the list of volume mounts
36      volumes:
#▸ L37: mount the named volume neo4j_data at /data, where Neo4j keeps graph files AND user accounts
37        - neo4j_data:/data
```
**How it fits.** Lines 15–19 and 37 existed since P1. Line 24 is P4-M1's change (required password), and the comment
on lines 21–23 records the first-start-only rule so the next person doesn't fall into it.

### 4. Benefits
A pinned, reproducible server; data that survives container restarts; one explicit place where the password rule is written down.

- **What breaks:** changing `.env` on an existing volume (password silently ignored); `docker compose down -v` (deletes the
  volume, and with it every node *and* the accounts); unpinned image tags (a new major version on the next pull).
- **Docs:** [Neo4j Docker: authentication](https://neo4j.com/docs/operations-manual/current/docker/introduction/#docker-auth) ·
  [Ports](https://neo4j.com/docs/operations-manual/current/configuration/ports/) ·
  [Docker volumes](https://docs.docker.com/engine/storage/volumes/)

### Micro-exercises
1. Run `docker volume inspect advancerag_neo4j_data` and find where Docker keeps it.
2. In the Browser, run `ALTER CURRENT USER SET PASSWORD FROM '<old>' TO '<new>'`, update `.env`, and confirm
   `python -m src.graph.setup` is still ready.
3. Stop and remove the container (`docker compose rm -sf neo4j`), start it again, and prove the graph survived.
4. Why would `docker compose down -v` be dangerous here? What exactly would it delete?
5. Find the line in the container log that shows APOC being installed.

**Internals.** The image's entrypoint script (`docker-entrypoint.sh`) turns environment variables into configuration,
installs plugins, runs `neo4j-admin dbms set-initial-password` if `NEO4J_AUTH` is set, then starts the server process.
**Checkpoint:** explain why a new password in `.env` didn't work against the old volume, and name both fixes.

---

## C3 — Secrets and configuration: `.env`, compose interpolation, pydantic-settings

### 1. Definition + analogy

**What it is.** A **secret** is configuration that grants access: passwords, API keys. Secrets must be (1) available to the
programs that need them, (2) **never** in version control, and (3) fail **loudly** when missing, never replaced by a guess.
Three pieces work together.

**(a) `.env`, the local secrets file.** A plain `KEY=value` file in the project root. It's **gitignored** (`.gitignore` line
20), so it never reaches GitHub. `.env.example` is the committed **template**: same keys, empty or placeholder values, and
instructions. New developers copy it (`cp .env.example .env`) and fill it in.

**(b) Docker Compose reads `.env` for interpolation.** In `docker-compose.yml`, `${VAR}` is replaced by the value of `VAR`
from the shell environment or `.env`. Two modifiers matter:
- `${VAR:-default}` uses `default` if `VAR` is unset or empty: fine for harmless values like the user name `neo4j`;
- `${VAR:?message}` **stops Compose with `message`** if `VAR` is unset or empty: right for secrets.
P4-M1 replaced `${NEO4J_PASSWORD:-changeme}` with `${NEO4J_PASSWORD:?...}`. Before, a missing `.env` meant "run with the
famous password". Now it means "refuse to start and say why".

**(c) pydantic-settings reads `.env` for Python.** `Settings` in `src/core/config.py` looks up each field, highest
priority first: **explicit argument > environment variable > `.env` file > class default**. That lets the same code work
in two places:
- **on your machine:** `.env` says `NEO4J_URI=bolt://localhost:7687`;
- **inside Docker:** compose sets the environment variable `NEO4J_URI=bolt://neo4j:7687` (the service name), which **beats** `.env`.
The class default for the password is now `""` (empty): without `.env` the driver fails with an authentication error,
never with a working well-known password.

**Analogy.** A house key. The spare lives in a locked box at home (`.env`), never taped to the front door (the Git repository).
A note on the fridge says "the spare key is in the blue box" (`.env.example`), without being the key.

### For a 14-year-old

You and your friends have a secret club with a password to get into the treehouse.
- You write the password on a card and keep it **in your pocket** (that's `.env`). You **never** write it on the club's
  public noticeboard (that's GitHub, where everyone can see).
- On the noticeboard you only pin a note saying "**Password: ask a member. It must be at least 8 letters.**" (that's
  `.env.example`). It tells new members *what* they need, without giving it away.
- The treehouse guard (Docker Compose) has a rule: **if nobody gives a password, nobody gets in and the guard says why.**
  The old rule was "if nobody gives a password, just use 'changeme'", which is like leaving the door open with a sign
  that says the password is "changeme".

One more thing: when you're **inside** the treehouse you call the guard "Bob" (the service name `neo4j`), but from your
**house** you have to call him on the phone at "localhost". Our settings know both names and use the right one in each place.

### 2. Why (vs the traditional approach)
Common shortcuts are hard-coding the password in code, committing a `.env`, or a default like `changeme` "for local dev".
All three end up in production sooner or later. Secret scanners find thousands of such passwords on GitHub every day.

### 3a. Minimal demo — compose refusal, what compose substitutes, pydantic priority, gitignore
`c3_env_and_settings.py`
```python
# C3: where credentials come from -- .env, compose interpolation, pydantic-settings. Never prints the password.
import os                                                      # to set/unset environment variables
import shutil                                                  # to move .env aside temporarily
import subprocess                                              # to call `docker compose config`
from pathlib import Path                                       # file paths

# 1. Compose interpolation: ${VAR:-default} substitutes a default, ${VAR:?msg} stops with an error
env = {**os.environ}                                           # copy of the current environment
env.pop("NEO4J_PASSWORD", None)                                # make sure the shell does not supply it
shutil.move(".env", ".env.demo_backup")                        # hide .env so compose cannot read it
try:
    r = subprocess.run(["docker", "compose", "config", "--quiet"], capture_output=True, text=True, env=env)
    print("1. compose without .env :", "exit", r.returncode, "|", r.stderr.strip().splitlines()[-1][:110])
finally:
    shutil.move(".env.demo_backup", ".env")                    # always put .env back
r = subprocess.run(["docker", "compose", "config", "--quiet"], capture_output=True, text=True)
print("   compose with .env    :", "exit", r.returncode)

# 2. What compose substituted into the neo4j service (password masked)
out = subprocess.run(["docker", "compose", "config"], capture_output=True, text=True).stdout
auth_line = next(l.strip() for l in out.splitlines() if "NEO4J_AUTH" in l)
user, _, password = auth_line.split(": ", 1)[1].partition("/")  # "neo4j/<password>" -> user, password
print("2. NEO4J_AUTH resolved  :", f"{user}/<{len(password)} characters>")

# 3. pydantic-settings priority: environment variable > .env file > class default
from src.core.config import Settings                           # the project's settings class

print("3. from .env            :", Settings().neo4j_uri)      # .env sets bolt://localhost:7687
os.environ["NEO4J_URI"] = "bolt://from-env-var:7687"            # a real env var beats .env
print("   env var overrides    :", Settings().neo4j_uri)
del os.environ["NEO4J_URI"]
print("   class default        :", Settings(_env_file=None).neo4j_uri, "(when no .env is read)")
print("   default password     :", repr(Settings(_env_file=None).neo4j_password), "(empty, no well-known value)")

# 4. .env must never be committed
r = subprocess.run(["git", "check-ignore", "-v", ".env"], capture_output=True, text=True)
print("4. git check-ignore .env:", r.stdout.strip())
print("   .env tracked by git? :", bool(subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True).stdout.strip()))
print("   .env still present   :", Path(".env").exists())
```
Real output:
```
1. compose without .env : exit 1 | error while interpolating services.neo4j.environment.NEO4J_AUTH: required variable NEO4J_PASSWORD is missing a
   compose with .env    : exit 0
2. NEO4J_AUTH resolved  : neo4j/<32 characters>
3. from .env            : bolt://localhost:7687
   env var overrides    : bolt://from-env-var:7687
   class default        : bolt://neo4j:7687 (when no .env is read)
   default password     : '' (empty, no well-known value)
4. git check-ignore .env: .gitignore:20:.env	.env
   .env tracked by git? : False
   .env still present   : True
```
**How to read it.**
- **Line 1:** with `.env` hidden, Compose **refuses** (exit 1) and names the variable and the file to fix, even for
  `config`, before starting anything. With `.env` back: exit 0.
- **Line 2:** Compose built `NEO4J_AUTH` as `neo4j/<32 characters>`, the default user plus the generated password. The
  script only prints the length.
- **Line 3:** all three pydantic priority levels in a row: `.env` value, overridden by an environment variable, and the
  class default when no `.env` is read. The password default is the empty string.
- **Line 4:** `.env` is ignored by rule 20 of `.gitignore` and not tracked. The `finally` put it back after step 1.

### 3b. Exact project code — `.env.example`, the compose lines, and `Settings` (annotated copies)
`.env.example`:
```bash
#▸ L1: tell the reader what to do with this template
1  # Copy to .env and fill in. .env is gitignored.
#▸ L2: the database user name (Neo4j's built-in admin user)
2  NEO4J_USER=neo4j
#▸ L3-5: document that the password is required, its minimum length, and how to change it after first start
3  # Required: docker compose refuses to start without it. At least 8 characters.
4  # Only applied when the neo4j_data volume is first created; afterwards change it with
5  #   ALTER CURRENT USER SET PASSWORD FROM '<old>' TO '<new>'
#▸ L6: the password key with an EMPTY value -- the template never contains a real secret
6  NEO4J_PASSWORD=
#▸ L7: where scripts on your machine connect (localhost, not the Docker service name)
7  NEO4J_URI=bolt://localhost:7687
#▸ L8: the Cohere key for later reranking, empty until needed
8  COHERE_API_KEY=
```
`docker-compose.yml` (neo4j service line 24, api service line 111):
```yaml
#▸ L24: NEO4J_AUTH = "<user>/<password>"; ${NEO4J_USER:-neo4j} defaults the user, ${NEO4J_PASSWORD:?msg} REQUIRES the password
24        NEO4J_AUTH: "${NEO4J_USER:-neo4j}/${NEO4J_PASSWORD:?set NEO4J_PASSWORD in .env (see .env.example)}"
#▸ L111: the api container also gets the password, with the same "required, no fallback" rule
111       NEO4J_PASSWORD: "${NEO4J_PASSWORD:?set NEO4J_PASSWORD in .env (see .env.example)}"
```
`src/core/config.py`:
```python
#▸ L6: a settings class; pydantic fills its fields from env vars, .env, or the defaults below
6  class Settings(BaseSettings):
#▸ L7: read ".env" from the working directory, and ignore keys in it that aren't fields
7      model_config = SettingsConfigDict(env_file=".env", extra="ignore")
#▸ L14: default URI = the Docker service name; .env or an env var overrides it
14     neo4j_uri: str = "bolt://neo4j:7687"
#▸ L15: default user name
15     neo4j_user: str = "neo4j"
#▸ L18: default password is EMPTY, so a missing .env causes an auth error, not a login with a known password
18     neo4j_password: str = ""
```
**How it fits.** Compose (for the container) and pydantic (for Python on your machine) both read the **same** `.env`, so
the password exists in exactly one place.

### 4. Benefits
One secret, one place, never committed, and loud failure when missing.

- **What breaks:** losing `.env` (the graph's password is gone; an admin reset is needed); committing `.env`; `:?` makes
  **every** compose command fail without it, even `up qdrant` (intended, but surprising).
- **Docs:** [Compose variable interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/) ·
  [pydantic-settings: dotenv and priority](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#dotenv-env-support) ·
  [The Twelve-Factor App: config](https://12factor.net/config)

### Micro-exercises
1. Temporarily rename `.env` and run `docker compose up -d qdrant`. Why does even Qdrant refuse? Restore `.env`.
2. Set `NEO4J_URI` in your shell and run `python -m src.graph.setup`. Which URI does it print?
3. Why is `NEO4J_USER` defaulted (`:-`) but `NEO4J_PASSWORD` required (`:?`)?
4. Search the repo's history for "changeme": `git log -S changeme --oneline`. When did it stop being a default?
5. What would you change so the api container reads its password from a Docker secret file instead?

**Internals.** Compose parses `.env` before reading the YAML; `${VAR:?msg}` raises an interpolation error during parsing.
pydantic-settings builds each field's value from its sources in priority order, validating the type as it goes.
**Checkpoint:** give the pydantic priority order, and explain what `${VAR:?msg}` does differently from `${VAR:-default}`.

---

## C4 — How the Neo4j image turns `NEO4J_*` variables into config (and the failure it caused)

### 1. Definition + analogy

**What it is.** Neo4j's real configuration lives in a file, `conf/neo4j.conf`, with keys like
`server.memory.heap.max_size=1G`. Editing that file inside a container is awkward, so the image's start-up script offers a
shortcut: **every environment variable whose name starts with `NEO4J_` is converted into a `neo4j.conf` setting.** The
conversion rule:
1. drop the `NEO4J_` prefix;
2. every single underscore `_` becomes a dot `.`;
3. every double underscore `__` becomes a single underscore `_` (for setting names that contain one).

So `NEO4J_server_memory_heap_max__size` becomes `server.memory.heap.max_size`.

A few names are **special-cased** and not converted: `NEO4J_AUTH` (the initial password), `NEO4J_PLUGINS` (which plugins
to install), `NEO4J_ACCEPT_LICENSE_AGREEMENT`. Everything else is converted.

Neo4j 5 also has **strict configuration validation** on by default: an unknown setting name is an **error**, and the server
refuses to start. This catches typos like `server.memroy...`.

**The failure in P4-M1.** To let the healthcheck log in, I first added `NEO4J_USER` and `NEO4J_PASSWORD` to the container's
environment. The start-up script converted them into settings named `USER` and `PASSWORD`, strict validation found no such
settings, and the container **exited with code 1** before starting. The log line:
`Failed to read config: Unrecognized setting. No declared setting with name: PASSWORD`.
The fix was to not add our own `NEO4J_*` variables at all, and to take the credentials from `NEO4J_AUTH` in the healthcheck (C5).

**The general lesson:** a **vendor image owns its environment-variable namespace**. Before adding a variable to someone
else's image, check whether its prefix has a meaning there.

**Analogy.** An office's post room where every envelope marked "HR:" is automatically filed into the HR cabinet. You write
"HR: my lunch order" on an envelope, meaning it just for yourself, and the post room tries to file it, finds no HR folder
called "lunch order", and stops all post until someone fixes it.

### For a 14-year-old

Imagine a robot helper in your house with one strict rule: **any note starting with "ROBOT:" is an instruction for the robot.**
- "ROBOT: vacuum the kitchen" makes it vacuum the kitchen. Great, that's what the rule is for.
- You leave yourself a reminder: "ROBOT: password is 1234" (you just meant "note for the robot's healthcheck"). The robot
  reads it as an instruction, looks for a job called "password", can't find one, and **refuses to do anything at all**
  until the note is removed.

That's exactly what happened. The Neo4j "robot" treats every setting starting with `NEO4J_` as an instruction for itself. We
gave it `NEO4J_PASSWORD` (meant for our health check), it looked for a setting called "PASSWORD", didn't find one, and
wouldn't start. The fix: don't write "ROBOT:" notes unless they really are instructions for the robot.

### 2. Why (vs the traditional approach)
Mounting a hand-written `neo4j.conf` also works, but then two sources describe the configuration (the file and compose),
and the image's defaults have to be copied by hand. Environment variables keep everything in `docker-compose.yml`.

### 3a. Minimal demo — the naming rule, what the server wrote, and the failure reproduced safely
`c4_env_to_config_mapping.py`
```python
# C4: the Neo4j image turns NEO4J_* variables into neo4j.conf settings -- and rejects unknown ones.
import subprocess                                                # to run docker


def env_to_setting(name):                                        # the image's naming rule, re-implemented
    key = name[len("NEO4J_"):]                                   # drop the prefix
    key = key.replace("__", "\x00")                              # protect double underscores
    key = key.replace("_", ".")                                  # single underscore -> dot
    return key.replace("\x00", "_")                              # double underscore -> single underscore


for name in ("NEO4J_server_memory_heap_max__size", "NEO4J_server_memory_pagecache_size", "NEO4J_PASSWORD"):
    print(f"1. {name:<38} -> setting {env_to_setting(name)!r}")

# 2. What the running server actually loaded from our variables
out = subprocess.run(["docker", "exec", "advancerag-neo4j-1", "sh", "-c",
                      "grep -E '^server.memory.(heap|pagecache)' conf/neo4j.conf"],
                     capture_output=True, text=True).stdout.strip()
print("2. conf/neo4j.conf written by the entrypoint:")
for line in out.splitlines():
    print("     ", line)

# 3. A throwaway container (no volume, no ports) with one unknown NEO4J_* variable: it refuses to start
r = subprocess.run(["docker", "run", "--rm", "-e", "NEO4J_AUTH=none", "-e", "NEO4J_PASSWORD=x", "neo4j:5.26.0"],
                   capture_output=True, text=True, timeout=180)
failure = next((l for l in (r.stdout + r.stderr).splitlines() if "Unrecognized setting" in l), "(no error line)")
print("3. throwaway container exit code:", r.returncode)
print("   ", failure.replace("PASSWORD x", "PASSWORD <value>")[:150])
```
Real output:
```
1. NEO4J_server_memory_heap_max__size     -> setting 'server.memory.heap.max_size'
1. NEO4J_server_memory_pagecache_size     -> setting 'server.memory.pagecache.size'
1. NEO4J_PASSWORD                         -> setting 'PASSWORD'
2. conf/neo4j.conf written by the entrypoint:
      server.memory.pagecache.size=512m
      server.memory.heap.max_size=1G
      server.memory.heap.initial_size=512m
3. throwaway container exit code: 1
    Error: Unrecognized setting. No declared setting with name: PASSWORD. Cleanup the config or disable 'server.config.strict_validation.enabled' to conti
```
**How to read it.**
- **Line 1:** the rule turns our memory variables into real setting names, and turns `NEO4J_PASSWORD` into `PASSWORD`.
  In `heap_max__size`, the double underscore keeps the `_` inside `max_size`.
- **Line 2:** the start-up script wrote the three memory settings into `conf/neo4j.conf`. That's the file the server
  actually reads.
- **Line 3:** a throwaway container (`--rm`, no volume, no ports, `NEO4J_AUTH=none`) with one stray `NEO4J_PASSWORD` exits
  with **code 1** and exactly the error from P4-M1. Reproduced safely, without touching the real container.

### 3b. Exact project code — the variables we DO pass (annotated copy of `docker-compose.yml`)
```yaml
#▸ L25-27: warning for future editors: never add our own NEO4J_* variables (they become settings and break start-up)
25        # Do NOT add other NEO4J_* variables for our own use: the image turns every
26        # NEO4J_* variable into a neo4j.conf setting, and strict config validation
27        # then refuses to start on unknown ones (NEO4J_PASSWORD -> setting "PASSWORD").
#▸ L30: special-cased variable (not converted): install the APOC plugin from the jar bundled in the image
30        NEO4J_PLUGINS: '["apoc"]'
#▸ L33: converted to server.memory.heap.initial_size=512m (Java heap size at start-up)
33        NEO4J_server_memory_heap_initial__size: "512m"
#▸ L34: converted to server.memory.heap.max_size=1G (the Java heap can grow to 1 GiB)
34        NEO4J_server_memory_heap_max__size: "1G"
#▸ L35: converted to server.memory.pagecache.size=512m (RAM reserved for caching graph files)
35        NEO4J_server_memory_pagecache_size: "512m"
```
**How it fits.** Every `NEO4J_*` variable in the file is now either special-cased (`AUTH`, `PLUGINS`) or a real setting
(the memory lines). Lines 25–27 exist because of the failure.

### 4. Benefits
All configuration in one YAML file, typos caught at start-up by strict validation, and a written warning against the trap.

- **What breaks:** any stray `NEO4J_*` variable (start-up failure); a single underscore where a double was needed
  (a different, unknown setting, so again a start-up failure).
- **Docs:** [Neo4j Docker: configuration via environment variables](https://neo4j.com/docs/operations-manual/current/docker/configuration/) ·
  [`server.config.strict_validation.enabled`](https://neo4j.com/docs/operations-manual/current/configuration/configuration-settings/#config_server.config.strict_validation.enabled)

### Micro-exercises
1. Convert `NEO4J_db_transaction_timeout` by hand, then check the setting exists with `SHOW SETTINGS YIELD name WHERE name CONTAINS 'transaction.timeout'`.
2. What setting would `NEO4J_server_memory_heap_max_size` (single underscore) become? Would the server start?
3. Run the throwaway container again with `NEO4J_server_config_strict__validation_enabled=false` added. Does it start?
4. Which other `NEO4J_*` names does the entrypoint treat specially? (Look inside the image: `docker exec advancerag-neo4j-1 grep -n NEO4J_ /startup/docker-entrypoint.sh | head`.)
5. Design a naming convention for your own variables so this can't happen again.

**Internals.** The entrypoint loops over the environment, skips the special names, applies the rename rule, and appends
`key=value` lines to `conf/neo4j.conf` before starting Neo4j.
**Checkpoint:** convert `NEO4J_server_memory_pagecache_size` by hand, and explain why `NEO4J_PASSWORD` stopped the server.

---

## C5 — A healthcheck that logs in: `cypher-shell`, `$$`, shell parameter expansion

### 1. Definition + analogy

**What it is.** A Docker **healthcheck** is a command Docker runs **inside the container** every `interval`. Exit code 0
means healthy; anything else is a failure. After `retries` failures in a row the container is **unhealthy**. Compose uses this
for `depends_on: condition: service_healthy`: the `api` service waits until Neo4j is healthy.

**What "healthy" should mean.** The old check was `wget --spider http://localhost:7474`, which only proves the web server
answers. A Neo4j with a **wrong password** or a broken Bolt listener still passes it. The new check **logs in and runs a query**:
```
cypher-shell -a bolt://localhost:7687 -u <user> -p <password> 'RETURN 1'
```
`cypher-shell` is Neo4j's command-line client, included in the image. If Bolt and the credentials work, it prints `1` and
exits 0.

**Getting the credentials without new `NEO4J_*` variables (C4).** The container already has `NEO4J_AUTH="neo4j/<password>"`.
The shell can split it with **parameter expansion**:
- `${NEO4J_AUTH%%/*}` removes the **longest** match of `/*` from the **end**, leaving everything **before the first `/`**
  (the user);
- `${NEO4J_AUTH#*/}` removes the **shortest** match of `*/` from the **start**, leaving everything **after the first `/`**
  (the password, even if it contains `/` itself).

**`$$` in compose.** Compose interpolates `${...}` itself, while reading the YAML. Writing `$${NEO4J_AUTH%%/*}` tells
Compose "this is a literal `$`, not yours", so the text reaches the container unchanged and the **container's** shell
expands it at check time.

**`start_period: 30s`** gives Neo4j time to boot. Failures during those first 30 seconds don't count toward `retries`.

**Analogy.** Checking a shop is really open: not by seeing the lights are on (HTTP answers), but by walking in and buying
something (logging in and running a query).

### For a 14-year-old

The school nurse checks every 10 seconds whether our database is "healthy".
- **Old check:** knock on the door and see if the light is on. The light can be on while the person inside has lost their key
  and can't unlock anything. "Healthy!" would be a lie.
- **New check:** actually **use the key**: log in and ask the simplest question, "what is 1?". If it answers "1", it's truly working.

How does the nurse know the key? The password is written as `neo4j/secret` on one card, and the nurse needs to split it at
the slash into "neo4j" (user) and "secret" (password). The shell has two tiny tricks for that:
- "keep everything **before** the first slash", written `%%/*`;
- "keep everything **after** the first slash", written `#*/`.
Even if the password itself has a slash in it, only the **first** slash splits.

And `$$`? Docker Compose tries to fill in anything with a `$` itself. Doubling it (`$$`) is like writing "don't touch this, it's
for the nurse", so the nurse fills it in later, inside the container.

### 2. Why (vs the traditional approach)
Port or HTTP probes are the default in many examples because they're easy. They answer "is something listening?", not "can a
client use it?".

### 3a. Minimal demo — expansion, what Docker stored, the health log, a failing probe
`c5_healthcheck.py`
```python
# C5: the healthcheck -- shell parameter expansion, compose's $$ escaping, and Docker's health log.
import json                                                     # to parse docker inspect output
import subprocess                                               # to run docker

# 1. Shell parameter expansion, shown with a FAKE value (never the real password)
demo = subprocess.run(
    ["docker", "exec", "advancerag-neo4j-1", "sh", "-c",
     'X="neo4j/s3cr/et"; echo "user=${X%%/*}"; echo "password=${X#*/}"'],   # %%/* cuts from the first "/" on; #*/ cuts up to it
    capture_output=True, text=True).stdout.split()
print("1. with X='neo4j/s3cr/et'   :", demo, "<- only the FIRST '/' splits, so a '/' in a password survives")

# 2. What compose passes to Docker: $$ became a single $, so the CONTAINER'S shell expands it
out = subprocess.run(["docker", "inspect", "advancerag-neo4j-1", "--format", "{{json .Config.Healthcheck}}"],
                     capture_output=True, text=True).stdout
health = json.loads(out)                                        # the healthcheck config as stored by Docker
print("2. stored test command      :", health["Test"][1][:95], "...")
print("   interval/timeout/retries :", health["Interval"] // 10**9, "s /", health["Timeout"] // 10**9, "s /", health["Retries"])

# 3. The last health results Docker recorded
state = json.loads(subprocess.run(["docker", "inspect", "advancerag-neo4j-1", "--format", "{{json .State.Health}}"],
                                  capture_output=True, text=True).stdout)
print("3. status                   :", state["Status"], "| failing streak:", state["FailingStreak"])
for entry in state["Log"][-2:]:                                 # Docker keeps the last few probe results
    print("   probe exit", entry["ExitCode"], "| output:", entry["Output"].strip().replace("\n", " | ")[:60])

# 4. The same probe with a WRONG password: what "unhealthy" would look like
bad = subprocess.run(["docker", "exec", "advancerag-neo4j-1", "cypher-shell", "-a", "bolt://localhost:7687",
                      "-u", "neo4j", "-p", "definitely-wrong", "RETURN 1"], capture_output=True, text=True)
print("4. wrong password probe     : exit", bad.returncode, "|", (bad.stderr or bad.stdout).strip()[:90])
```
Real output:
```
1. with X='neo4j/s3cr/et'   : ['user=neo4j', 'password=s3cr/et'] <- only the FIRST '/' splits, so a '/' in a password survives
2. stored test command      : cypher-shell -a bolt://localhost:7687 -u "${NEO4J_AUTH%%/*}" -p "${NEO4J_AUTH#*/}" 'RETURN 1' | ...
   interval/timeout/retries : 10 s / 10 s / 10
3. status                   : healthy | failing streak: 0
   probe exit 0 | output: 1 | 1
   probe exit 0 | output: 1 | 1
4. wrong password probe     : exit 1 | The client is unauthorized due to authentication failure.
```
**How to read it.**
- **Line 1:** with a fake `neo4j/s3cr/et`, the user is `neo4j` and the password `s3cr/et`: the second `/` stays in the
  password, because `%%/*` and `#*/` both act on the **first** `/`.
- **Line 2:** Docker stored `${NEO4J_AUTH%%/*}` with **one** `$`. Compose turned `$$` into `$` and substituted nothing, so
  the password is never written into the container's configuration.
- **Line 3:** healthy, with no failures; each probe printed `1` (a header line and the value) and exited 0.
- **Line 4:** the same command with a wrong password exits 1 with the authentication message. That's what would turn the
  container "unhealthy", which the old HTTP probe could never detect.

### 3b. Exact project code — the healthcheck (annotated copy of `docker-compose.yml`)
```yaml
#▸ L38: start the healthcheck definition for the neo4j service
38      healthcheck:
#▸ L44: run through the container's shell: log in over Bolt with the user/password split from NEO4J_AUTH, run RETURN 1;
#▸      "|| exit 1" turns any failure into exit code 1 ($$ keeps compose from substituting)
44        test: ["CMD-SHELL", "cypher-shell -a bolt://localhost:7687 -u \"$${NEO4J_AUTH%%/*}\" -p \"$${NEO4J_AUTH#*/}\" 'RETURN 1' || exit 1"]
#▸ L45: run the probe every 10 seconds
45        interval: 10s
#▸ L46: a probe that takes longer than 10 seconds counts as a failure
46        timeout: 10s
#▸ L47: 10 failures in a row mark the container unhealthy
47        retries: 10
#▸ L48: failures in the first 30 seconds (while Neo4j boots) don't count
48        start_period: 30s
```

### 4. Benefits
"Healthy" means a client can log in and query, and dependent services start only then.

- **What breaks:** a wrong password in `.env` gives an "unhealthy" container but not the *reason* (use `python -m src.graph.setup`
  for that); the probe uses the admin credentials (Community Edition has no role-based users for a read-only probe).
- **Docs:** [Compose healthcheck](https://docs.docker.com/reference/compose-file/services/#healthcheck) ·
  [Compose `$$` escaping](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/) ·
  [POSIX shell parameter expansion](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_06_02) ·
  [cypher-shell](https://neo4j.com/docs/operations-manual/current/cypher-shell/)

### Micro-exercises
1. In demo step 1, try `X="neo4j"` (no slash). What do both expansions return, and why?
2. Replace `$$` with `$` in a copy of the compose file and run `docker compose config`. What happened to the command?
3. Watch health change live: `docker inspect -f '{{.State.Health.Status}}' advancerag-neo4j-1` during a restart.
4. What does `docker compose up -d api` wait for now that Neo4j has this healthcheck?
5. Write the same probe as a one-line Python script using the driver instead of `cypher-shell`.

**Internals.** `CMD-SHELL` means Docker runs `/bin/sh -c "<string>"` in the container, which is where the parameter expansion
happens. Docker keeps the last five probe results in `.State.Health.Log`.
**Checkpoint:** explain what `${NEO4J_AUTH#*/}` returns for `neo4j/a/b`, and why the compose file writes `$$`.

---

## C6 — The Python driver: lazy driver, connectivity check, sessions, results, parameters

### 1. Definition + analogy

**What it is.** The official `neo4j` Python package (version 6.2.0 here) talks Bolt to the server. Its main objects:

| Object | Created by | What it is | Cost |
|---|---|---|---|
| **Driver** | `GraphDatabase.driver(uri, auth=...)` | a long-lived **connection pool** plus configuration | creating it does **not** connect |
| **Session** | `driver.session(database=...)` | a short-lived context that borrows connections from the pool | cheap; use one per unit of work |
| **Result** | `session.run(query, **params)` | a stream of records | records arrive as you read them |
| **Record** | `result.single()` / iterating | one row, indexable by column name | — |
| **ResultSummary** | `result.consume()` | metadata: query type, timings, counters | — |

**Laziness.** `GraphDatabase.driver(...)` only stores the address and credentials. No network traffic happens until something
needs a connection. So a wrong address or password is **not** detected at construction. `verify_connectivity()` forces an
immediate connection: the Bolt **handshake** (agreeing a protocol version) plus **authentication**. That makes it the right
first call in a health check.

**Parameters.** `session.run("RETURN $value", value=x)` sends the query text and the value **separately**. The value can never be
interpreted as Cypher code, so it prevents **Cypher injection**. As a bonus the server can cache the query plan, since the text
doesn't change.

**The project's `build_driver()`** adds two decisions:
1. it reads `settings` **inside the function**, not in default arguments (default arguments are evaluated once, at import;
   deep dive 16 C8);
2. it sets `connection_timeout=5`, so an unreachable server fails in about 5 seconds, not the driver's default 30.

**Analogy.** A taxi company. The **driver object** is your account with the company (setting it up doesn't send a car). A
**session** is one booking. `verify_connectivity` is calling to confirm a car actually comes. A **parameter** is giving the
address on the booking form instead of shouting it into the dispatcher's instructions.

### For a 14-year-old

Talking to the database from Python is like using a walkie-talkie system:
1. **Buying the walkie-talkie** (`GraphDatabase.driver`): you now *have* one, but you haven't talked to anyone yet. Even if
   nobody is on the other end, buying it works fine.
2. **Checking the channel** (`verify_connectivity`): you press the button and say "can you hear me? my code word is …". Only
   **now** do you find out if someone's there and if your code word is right.
3. **A conversation** (`session`): you open a chat, ask questions, and close it when done.
4. **An answer** (`result` / `record`): the reply comes back, like "42".
5. **Parameters**: when you ask "what's 1 + x?", you say "x is 41" **separately**. Why? If someone tricky gave you a value like
   "41, and also delete everything", sending it separately means it's always treated as just a value, never as an order.

### 2. Why (vs the traditional approach)
Opening a new connection per query is slow; one global connection isn't safe across threads; building queries by string
formatting invites injection. The driver pattern (pool + sessions + parameters) solves all three.

### 3a. Minimal demo — each step timed
`c6_driver_lifecycle.py`
```python
# C6: the Python driver step by step -- lazy driver, connectivity check, session, result, summary.
import time                                                     # to time each step

from neo4j import GraphDatabase                                 # the official driver entry point
from neo4j.exceptions import ServiceUnavailable                 # raised when no server answers

from src.core.config import settings                            # URI / user / password from .env

# 1. Creating a driver does NOT connect: even a port where nothing listens raises no error here
nowhere = GraphDatabase.driver("bolt://localhost:7699", auth=("neo4j", "x"), connection_timeout=2)
print("1. driver to an empty port created without error:", type(nowhere).__name__)
try:
    nowhere.verify_connectivity()                               # the FIRST network contact happens here
except ServiceUnavailable as e:
    print("   verify_connectivity ->", type(e).__name__)
finally:
    nowhere.close()                                             # always release the pool

# 2. The real server: time each step
t = time.perf_counter()
driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
print(f"2. driver created           : {1000 * (time.perf_counter() - t):6.1f} ms (no network yet)")
t = time.perf_counter()
driver.verify_connectivity()                                    # opens a connection: Bolt handshake + login
print(f"   verify_connectivity      : {1000 * (time.perf_counter() - t):6.1f} ms (handshake + authentication)")
info = driver.get_server_info()                                 # what the server told us during the handshake
print("   server agent / protocol  :", info.agent, "/", info.protocol_version)

# 3. Session -> run -> Result -> Record -> summary
with driver.session(database="neo4j") as session:               # a session borrows connections from the pool
    result = session.run("RETURN 1 + $x AS answer", x=41)       # $x is a query PARAMETER, sent separately
    record = result.single()                                    # exactly one row -> one Record
    summary = result.consume()                                  # metadata about the query that just ran
    print("3. record                   :", record, "| record['answer'] =", record["answer"])
    print("   query type / server time :", summary.query_type, "/", summary.result_available_after, "ms")

# 4. Why parameters instead of string formatting: the value is data, never Cypher code
with driver.session() as session:
    tricky = "x' RETURN 'injected"
    echoed = session.run("RETURN $value AS v", value=tricky).single()["v"]
    print("4. parameter kept as data   :", repr(echoed))
driver.close()                                                  # closes every pooled connection
```
Real output:
```
1. driver to an empty port created without error: BoltDriver
   verify_connectivity -> ServiceUnavailable
2. driver created           :    0.2 ms (no network yet)
   verify_connectivity      :   52.4 ms (handshake + authentication)
   server agent / protocol  : Neo4j/5.26.0 / (5, 8)
3. record                   : <Record answer=42> | record['answer'] = 42
   query type / server time : r / 35 ms
4. parameter kept as data   : "x' RETURN 'injected"
```
**How to read it.**
- **Line 1:** a driver for a port with **nothing listening** was created without complaint (`BoltDriver`). Only
  `verify_connectivity` found the problem (`ServiceUnavailable`). Construction is lazy.
- **Line 2:** construction took 0.2 ms, with no network. `verify_connectivity` took 52 ms: a TCP connection, the Bolt version
  negotiation (the server picked **protocol 5.8**) and the login.
- **Line 3:** `$x` was sent separately; the result is one `Record` with `answer=42`. The summary says it was a read query (`r`)
  and the first result was ready after 35 ms (includes planning the new query).
- **Line 4:** a string that *looks* like Cypher came back unchanged. As a parameter it was only ever data.

### 3b. Exact project code — `build_driver` and `main` (annotated copy of `src/graph/setup.py`)
```python
#▸ L45: define build_driver; all three arguments are optional (None means "use settings")
45  def build_driver(
#▸ L46: the optional URI, user and password parameters
46      uri: str | None = None, user: str | None = None, password: str | None = None
#▸ L47: it returns a neo4j Driver (a connection pool; nothing is connected yet)
47  ) -> Driver:
...
#▸ L53: create the driver object -- lazy, so this line never touches the network
53      return GraphDatabase.driver(
#▸ L54: use the given URI, or else read settings.neo4j_uri NOW (at call time, not at import)
54          uri or settings.neo4j_uri,
#▸ L55: the (user, password) pair, each falling back to settings read at call time
55          auth=(user or settings.neo4j_user, password or settings.neo4j_password),
#▸ L58: give up on a connection attempt after 5 seconds instead of the default 30
58          connection_timeout=5,
#▸ L59: end of the driver() call
59      )
...
#▸ L115: define the CLI entry point; argv=None means "read sys.argv"; returns an exit code
115 def main(argv: list[str] | None = None) -> int:
#▸ L117: create the argument parser with a help description
117     parser = argparse.ArgumentParser(description="Check the Neo4j graph database is ready.")
#▸ L118: optional --uri; if absent, .env's NEO4J_URI is used
118     parser.add_argument("--uri", default=None, help="defaults to NEO4J_URI from .env")
#▸ L119: optional --user; if absent, .env's NEO4J_USER is used
119     parser.add_argument("--user", default=None, help="defaults to NEO4J_USER from .env")
#▸ L122: parse the arguments (an unknown flag such as --password makes argparse exit with code 2)
122     args = parser.parse_args(argv)
#▸ L124: decide the URI once, so the same value is used for connecting and for the printed message
124     uri = args.uri or settings.neo4j_uri
#▸ L125: build the driver and close it automatically when the block ends
125     with build_driver(uri, args.user) as driver:
#▸ L126: run the readiness check (C7); it returns a GraphStatus and never raises
126         status = health_check(driver)
#▸ L128: success branch
128     if status.ready:
#▸ L129-132: print one summary line with version, edition, URI, APOC version and the Browser address
129         print(
130             f"ready: Neo4j {status.version} {status.edition} at {uri}, "
131             f"APOC {status.apoc_version}, browser http://localhost:7474"
132         )
#▸ L133: exit code 0 = ready
133         return 0
#▸ L134: failure branch: print the reason held in status.problem
134     print(f"NOT ready: {status.problem}")
#▸ L135: exit code 1 = not ready
135     return 1
...
#▸ L138-139: when run as `python -m src.graph.setup`, call main() and exit the process with its return code
138 if __name__ == "__main__":
139     sys.exit(main())
```
Live, the CLI prints:
```
ready: Neo4j 5.26.0 community at bolt://localhost:7687, APOC 5.26.0, browser http://localhost:7474
```

### 4. Benefits
Connections are pooled and reused, problems surface at a known point (`verify_connectivity`), values are injection-safe,
and configuration is read when it's needed.

- **What breaks:** forgetting `close()` or `with` (leaked connections); assuming a created driver means a working one; string
  formatting user input into Cypher; default arguments that freeze settings at import.
- **Docs:** [Neo4j Python driver manual](https://neo4j.com/docs/python-manual/current/) ·
  [Driver API: `verify_connectivity`](https://neo4j.com/docs/api/python-driver/current/api.html#neo4j.Driver.verify_connectivity) ·
  [Query parameters](https://neo4j.com/docs/cypher-manual/current/syntax/parameters/)

### Micro-exercises
1. Time `verify_connectivity` twice in a row on the same driver. Is the second call faster? Why?
2. In step 3, print `summary.counters` for a `CREATE` query on a throwaway label (and clean up).
3. Build the same query with an f-string and the tricky value from step 4. What happens?
4. Set `connection_timeout=1` and point at a non-routable IP (`bolt://10.255.255.1:7687`). How long does it take?
5. Why does `main` compute `uri` once on line 124 rather than letting `build_driver` decide alone?

**Internals.** The driver keeps a pool of TCP connections per server address. A session acquires one when it runs a query and
returns it when the result is consumed. The Bolt handshake sends four candidate protocol versions and the server picks the
highest it supports.
**Checkpoint:** explain why `GraphDatabase.driver(...)` succeeded for an empty port, and what `verify_connectivity` adds.

---

## C7 — Error taxonomy and a health check that never raises

### 1. Definition + analogy

**What it is.** When something goes wrong, the driver raises an exception, and **which class** it raises tells you **where** the
problem is. The driver has two main families:

| Family | Meaning | Examples |
|---|---|---|
| **`Neo4jError`** | the **server** answered with an error (it carries a status `code` like `Neo.ClientError.Security.Unauthorized`) | `ClientError` (your request is wrong), with subclasses such as **`AuthError`** (credentials) and `CypherSyntaxError`; `TransientError` (retry may help) |
| **`DriverError`** | a **client-side / network** problem; the server may not have been reached at all | **`ServiceUnavailable`** (no server answered), session and pool errors |

Both inherit from `GqlError`, then Python's `Exception`. Because `AuthError` is a *subclass* of `ClientError`, **order matters**
in `except` clauses: the most specific class must come first, or a general branch would catch it.

**A health check that never raises.** `health_check()` is used by a CLI, by tests, and later maybe by an API readiness endpoint.
If it raised, every caller would have to wrap it. Instead it catches the expected failures and returns a
**`GraphStatus(ready, version, edition, apoc_version, problem)`**, where `problem` is a human-readable reason. It catches only
**driver and server** errors (`Neo4jError`, `DriverError`), not every `Exception`, so a genuine bug in our own code still crashes
loudly (the "catch by type" rule from doc 13 C5).

**Readiness beyond connectivity.** After connecting, it checks two more things P4-M1 depends on:
- **major version 5** (the compose pin; queries and procedures are written against 5.x);
- **APOC loaded** (C9).
Each unmet condition is added to `problems`, and `ready` is true only if the list is empty.

**Error messages that point at the fix.** For `AuthError`, the message doesn't just say "authentication failed". It names the
**Docker-specific cause** (C2: `NEO4J_AUTH` only applies to a fresh volume), because that's the likeliest reason in this project
and the least obvious.

**Analogy.** A car dashboard. Instead of the engine stopping with no explanation (raising), it shows a specific warning light
("low oil", "door open") and lets the driver decide. And the oil light doesn't just say "problem": its manual says "check the dipstick".

### For a 14-year-old

When a game fails to connect, there are very different reasons:
- **"Wrong password"**: the server heard you and said no. (`AuthError`)
- **"Server not found"**: nobody answered at all; maybe the server is off. (`ServiceUnavailable`)
- **"Invalid command"**: you typed something the game doesn't understand. (`ClientError`, e.g. a syntax error)

Knowing **which** one happened tells you what to do: retype your password, turn the server on, or fix your typing. Our health
check sorts the problem into the right box and writes a helpful note, like "wrong password, and here's the sneaky reason it
might be wrong in our setup".

It also never "throws a tantrum" (crashes the program). It always calmly returns a report card: **ready: yes or no**, plus
**what's wrong**. Only if *our own* code has a real bug does it let the program crash, because we want to notice those.

### 2. Why (vs the traditional approach)
Many health checks catch `Exception` and print "something failed". That hides our own bugs and gives the reader nothing to act on.

### 3a. Minimal demo — the exception family tree, and which class each failure raises
`c7_error_taxonomy.py`
```python
# C7: which exception each failure raises, and where it sits in the driver's exception hierarchy.
from neo4j import GraphDatabase                                  # driver factory
from neo4j.exceptions import (AuthError, ClientError, DriverError, Neo4jError,  # the classes we catch
                              ServiceUnavailable)

from src.core.config import settings                             # real credentials from .env

# 1. The family tree (method resolution order) of each exception class
for cls in (AuthError, ClientError, ServiceUnavailable):
    print(f"1. {cls.__name__:<18} ->", " > ".join(c.__name__ for c in cls.__mro__[1:5]))


def attempt(label, uri, password, query="RETURN 1"):
    driver = GraphDatabase.driver(uri, auth=(settings.neo4j_user, password), connection_timeout=3)
    try:
        driver.verify_connectivity()                             # connection + login
        with driver.session() as s:
            s.run(query).consume()                               # the query itself
        print(f"2. {label:<22} -> OK")
    except (Neo4jError, DriverError) as e:                       # every driver/server error ends up here
        code = getattr(e, "code", None)                          # server errors carry a Neo4j status code
        branch = next(c.__name__ for c in (AuthError, ServiceUnavailable, ClientError, Neo4jError, DriverError)
                      if isinstance(e, c))                       # the most specific class we catch
        print(f"2. {label:<22} -> {type(e).__name__:<18} caught as {branch:<18} code={code}")
    finally:
        driver.close()


attempt("everything correct", settings.neo4j_uri, settings.neo4j_password)
attempt("nothing on that port", "bolt://localhost:7699", settings.neo4j_password)
attempt("wrong password (once)", settings.neo4j_uri, "wrong-password")
attempt("unknown function", settings.neo4j_uri, settings.neo4j_password, "RETURN nosuch.fn() AS x")
attempt("Cypher syntax error", settings.neo4j_uri, settings.neo4j_password, "RETURN (")
```
Real output:
```
1. AuthError          -> ClientError > Neo4jError > GqlError > Exception
1. ClientError        -> Neo4jError > GqlError > Exception > BaseException
1. ServiceUnavailable -> DriverError > GqlError > Exception > BaseException
2. everything correct     -> OK
2. nothing on that port   -> ServiceUnavailable caught as ServiceUnavailable code=None
2. wrong password (once)  -> AuthError          caught as AuthError          code=Neo.ClientError.Security.Unauthorized
2. unknown function       -> CypherSyntaxError  caught as ClientError        code=Neo.ClientError.Statement.SyntaxError
2. Cypher syntax error    -> CypherSyntaxError  caught as ClientError        code=Neo.ClientError.Statement.SyntaxError
```
**How to read it.**
- **The family tree:** `AuthError` **is a** `ClientError`, which **is a** `Neo4jError`. `ServiceUnavailable` is on the other
  branch, a `DriverError`. Both share `GqlError`, the root for both families in driver 6.
- **Nothing on the port:** a *client-side* `ServiceUnavailable` with **no** server code, because no server was involved.
- **Wrong password:** the server answered, with `AuthError` and code `Neo.ClientError.Security.Unauthorized`. It's caught as
  `AuthError` only because that branch is checked *before* `ClientError`.
- **Unknown function / syntax error:** both are `CypherSyntaxError` (a `ClientError`). An unknown function like `apoc.version()`
  without APOC fails the same way, which is how `_apoc_version` detects a missing APOC.
- Only **one** wrong-password attempt is made on purpose: Neo4j temporarily locks a user after repeated failed logins.

### 3b. Exact project code — `GraphStatus`, `health_check`, `_apoc_version` (annotated copy of `src/graph/setup.py`)
```python
#▸ L23: the major version the queries are written for (matches the compose pin 5.26.0)
23  EXPECTED_MAJOR_VERSION = "5"
#▸ L26: an immutable result object (frozen=True: fields can't be changed after creation)
26  @dataclass(frozen=True)
#▸ L27: the report card returned by health_check
27  class GraphStatus:
#▸ L28: the overall verdict
28      ready: bool
#▸ L29: server version string, e.g. "5.26.0" (None if it couldn't be read)
29      version: str | None = None
#▸ L30: "community" or "enterprise"
30      edition: str | None = None
#▸ L31: APOC version, or None if APOC is absent
31      apoc_version: str | None = None
#▸ L32: human-readable reason when not ready
32      problem: str | None = None
...
#▸ L35: a structural type: any object with these two methods is accepted (the real Driver or a test fake)
35  class GraphDriver(Protocol):
#▸ L38: must be able to check connectivity
38      def verify_connectivity(self) -> None: ...
#▸ L42: must be able to open a session for a named database
42      def session(self, *, database: str | None = ...) -> Any: ...
...
#▸ L62: define the check; takes any GraphDriver, checks the "neo4j" database by default, returns a GraphStatus
62  def health_check(driver: GraphDriver, database: str = "neo4j") -> GraphStatus:
#▸ L64: everything that talks to the server goes inside one try block
64      try:
#▸ L68: connect + Bolt handshake + login; wrong password or no server fails HERE, before any query
68          driver.verify_connectivity()
#▸ L69-73: open a session and read the server's version and edition (see C1)
69          with driver.session(database=database) as session:
70              component = session.run(
71                  "CALL dbms.components() YIELD versions, edition "
72                  "RETURN versions[0] AS version, edition"
73              ).single()
#▸ L74: ask whether APOC is loaded (None if not)
74              apoc_version = _apoc_version(session)
#▸ L75: most specific case first: the server rejected the credentials
75      except AuthError as exc:
#▸ L77-84: return "not ready" with a message naming the code and the Docker-specific cause
77          return GraphStatus(
78              ready=False,
79              problem=(
80                  f"authentication failed ({exc.code}); check NEO4J_USER/NEO4J_PASSWORD in .env. "
81                  "NEO4J_AUTH only applies when the neo4j_data volume is first created, so a "
82                  "password changed in .env later is ignored by an existing volume"
83              ),
84          )
#▸ L85: nothing answered at the address
85      except ServiceUnavailable as exc:
#▸ L86-88: return "not ready" suggesting the container may be stopped
86          return GraphStatus(
87              ready=False, problem=f"cannot reach Neo4j ({exc}); is the container running?"
88          )
#▸ L89: any other server-side or driver-side error (but NOT arbitrary Python exceptions)
89      except (Neo4jError, DriverError) as exc:
#▸ L90: report its class name and message
90          return GraphStatus(ready=False, problem=f"{type(exc).__name__}: {exc}")
#▸ L92: reached only if nothing raised: take the two columns from the record
92      version, edition = component["version"], component["edition"]
#▸ L93: collect every unmet readiness condition
93      problems = []
#▸ L94: is the server on the expected major version ("5.")?
94      if not version.startswith(f"{EXPECTED_MAJOR_VERSION}."):
#▸ L95: if not, record why
95          problems.append(f"expected Neo4j {EXPECTED_MAJOR_VERSION}.x, server is {version}")
#▸ L96: is APOC missing?
96      if apoc_version is None:
#▸ L97: if so, record how to enable it
97          problems.append("APOC is not loaded; set NEO4J_PLUGINS='[\"apoc\"]' on the neo4j service")
#▸ L98: build the final report
98      return GraphStatus(
#▸ L99: ready only if there were no problems
99          ready=not problems,
#▸ L100-102: pass the observed values through for display
100         version=version,
101         edition=edition,
102         apoc_version=apoc_version,
#▸ L103: join all problems into one message, or None if there were none
103         problem="; ".join(problems) or None,
104     )
...
#▸ L107: helper: find APOC's version, or None
107 def _apoc_version(session: Any) -> str | None:
#▸ L108: try the APOC function
108     try:
#▸ L109: returns e.g. "5.26.0" when APOC is loaded
109         return session.run("RETURN apoc.version() AS version").single()["version"]
#▸ L110: without APOC the function is unknown -> a ClientError (CypherSyntaxError, as the demo showed)
110     except ClientError:
#▸ L112: treat that as "APOC absent" rather than an error
112         return None
```

### 4. Benefits
Each failure gets a precise, actionable message; callers never need `try` around the check; genuine bugs still surface.

- **What breaks:** catching `ClientError` before `AuthError` (auth failures reported generically); catching `Exception` (our bugs
  hidden); a check that only connects (a server without APOC would look "ready").
- **Docs:** [Python driver: exceptions](https://neo4j.com/docs/api/python-driver/current/api.html#errors) ·
  [Neo4j status codes](https://neo4j.com/docs/status-codes/current/)

### Micro-exercises
1. Swap the order of `except AuthError` and `except (Neo4jError, DriverError)` in a copy. Which hermetic test fails?
2. Add a check that `edition == "community"` (warn otherwise). Should it make `ready` false? Argue both sides.
3. Make three wrong-password attempts in a row, then a correct one. What happens, and for how long?
4. Why does `_apoc_version` catch `ClientError` and not `Neo4jError`?
5. What would `health_check` return if Neo4j were replaced by version 2025.x with APOC loaded?

**Internals.** The server sends a FAILURE message with a status code; the driver maps the code's classification (`ClientError`,
`TransientError`, `DatabaseError`) and specific codes (`Security.Unauthorized`) to Python classes. Network errors are raised by the
driver itself before any server message exists.
**Checkpoint:** name the two exception families and say which one a wrong password raises and which an unreachable server raises.

---

## C8 — Memory: heap vs page cache

### 1. Definition + analogy

**What it is.** Neo4j runs on the Java Virtual Machine (JVM) and uses memory in two main pools:
- **Heap:** memory for **running queries**: intermediate results, sorting, transaction state, and the query plan cache. It's
  managed by Java's garbage collector. Too small, and big queries fail with out-of-memory errors or long GC pauses.
- **Page cache:** memory that holds the **graph's store files** (nodes, relationships, properties, indexes) in RAM. Too small,
  and Neo4j reads from disk constantly. Ideally it's at least as large as the store files.

Without configuration, Neo4j **auto-sizes** both from the machine's total RAM. On this machine, 7.6 GB is **shared** with Ollama
(the embedding model) and Qdrant, so P4-M1 fixed them:
- heap **512 MiB initial, 1 GiB max**;
- page cache **512 MiB** (the empty store is under 1 MB, so this is plenty for a long time).

The container uses more than heap plus page cache (the JVM itself, threads, native memory): about 0.9–1 GiB at idle here.

**Analogy.** A workshop: the **workbench** (heap) is where you work on the current job; the **shelves next to it** (page cache)
hold the parts you use most, so you don't walk to the warehouse (disk) each time. A tiny bench limits how big a job you can do;
empty shelves mean constant trips.

### For a 14-year-old

Your brain while doing homework:
- your **desk** is where you actually work on the current problem: scrap paper, calculator, working out. That's the **heap**.
  A tiny desk means you can't do big problems.
- the **books open around you** so you don't go to the library every time you need a fact. That's the **page cache**. If all
  the books you need fit on your desk area, you never leave the room.

Our computer is shared with two other "students" (Ollama and Qdrant), so we gave Neo4j a **fixed-size desk and shelf**, so it
can't grab all the space from the others.

### 3a. Minimal demo — configured vs actual
`c8_memory.py`
```python
# C8: heap vs page cache -- what we configured, what the JVM reports, what the container uses.
import subprocess                                               # to call docker stats

from src.graph.setup import build_driver                        # the project's driver factory

with build_driver() as driver, driver.session() as session:
    # 1. The settings Neo4j loaded (set by our NEO4J_server_memory_* variables)
    rows = session.run(
        "SHOW SETTINGS YIELD name, value WHERE name IN "
        "['server.memory.heap.initial_size', 'server.memory.heap.max_size', 'server.memory.pagecache.size'] "
        "RETURN name, value ORDER BY name"
    ).data()
    for row in rows:
        print(f"1. {row['name']:<34} = {row['value']}")

    # 2. What the Java virtual machine inside the container reports about its heap right now
    heap = session.run(
        "CALL dbms.queryJmx('java.lang:type=Memory') YIELD attributes "
        "RETURN attributes.HeapMemoryUsage.value.properties AS heap"
    ).single()["heap"]
    mib = lambda b: f"{b / 2**20:,.0f} MiB"                     # bytes -> MiB for display
    print(f"2. JVM heap: used {mib(heap['used'])}, committed {mib(heap['committed'])}, max {mib(heap['max'])}")

# 3. How big the graph's store files are (what the page cache would hold). apoc.monitor.store is in
#    APOC *Extended*, not the Core jar we enabled, so the size is read from the filesystem instead.
store = subprocess.run(["docker", "exec", "advancerag-neo4j-1", "du", "-sh", "/data/databases/neo4j"],
                       capture_output=True, text=True).stdout.split()[0]
print(f"3. store files of database 'neo4j' on disk: {store}  (a 512 MiB page cache holds all of it)")

# 4. The container as a whole: heap + page cache + everything else the process uses
stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}: {{.MemUsage}}"],
                       capture_output=True, text=True).stdout.strip().splitlines()
for line in stats:
    print("4.", line)
```
Real output:
```
1. server.memory.heap.initial_size    = 512.00MiB
1. server.memory.heap.max_size        = 1.00GiB
1. server.memory.pagecache.size       = 512.00MiB
2. JVM heap: used 330 MiB, committed 512 MiB, max 1,024 MiB
3. store files of database 'neo4j' on disk: 740K  (a 512 MiB page cache holds all of it)
4. advancerag-neo4j-1: 1.006GiB / 7.599GiB
4. advancerag-qdrant-1: 53.38MiB / 7.599GiB
4. advancerag-ollama-1: 308.5MiB / 7.599GiB
```
**How to read it.**
- **Line 1:** the three settings from compose, in Neo4j's display format.
- **Line 2:** the JVM *itself* confirms it: 512 MiB committed (the initial size), 1,024 MiB maximum. 330 MiB is actually in
  use right now.
- **Line 3:** the whole database is 740 KB on disk (empty graph plus system files), about 700× smaller than the page cache.
  (The first version of this demo called `apoc.monitor.store`, which failed with `ProcedureNotFound`: it's in APOC
  **Extended**, not Core; see C9.)
- **Line 4:** the container uses about 1 GiB in total: heap, page cache (allocated as needed) and JVM overhead. Next to it,
  Qdrant (53 MiB) and Ollama (309 MiB with no model loaded) share the same 7.6 GB.

### 3b. Exact project code (annotated copy of `docker-compose.yml`)
```yaml
#▸ L31-32: explain why sizes are fixed rather than auto-sized
31        # Fixed sizes instead of auto-sizing from host RAM, which Ollama and Qdrant
32        # share on this machine; ample for a graph of this corpus's size.
#▸ L33: Java heap at start-up = 512 MiB  (becomes server.memory.heap.initial_size)
33        NEO4J_server_memory_heap_initial__size: "512m"
#▸ L34: Java heap may grow to at most 1 GiB  (server.memory.heap.max_size)
34        NEO4J_server_memory_heap_max__size: "1G"
#▸ L35: 512 MiB of RAM for caching store files  (server.memory.pagecache.size)
35        NEO4J_server_memory_pagecache_size: "512m"
```
The live test that pins them (`tests/graph/test_setup.py` lines 185–197) reads `SHOW SETTINGS` and asserts
`512.00MiB` / `1.00GiB` / `512.00MiB`, the display format measured on the live server (a first guess, `1.000GiB`, was wrong).

### 4. Benefits
Predictable memory next to the other services, and settings verified from the server itself.

- **What breaks:** a heap too small for future large queries (out-of-memory errors); a page cache smaller than the store once
  the graph grows (disk reads).
- **Docs:** [Neo4j memory configuration](https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/) ·
  [`neo4j-admin server memory-recommendation`](https://neo4j.com/docs/operations-manual/current/neo4j-admin-neo4j-cli/#neo4j-admin-commands)

### Micro-exercises
1. Run `docker exec advancerag-neo4j-1 neo4j-admin server memory-recommendation` and compare with our values.
2. Run demo step 2 before and after a heavy query (`UNWIND range(1, 1000000) AS i RETURN sum(i)`). How does `used` change?
3. Load a larger throwaway graph, then re-check the store size in step 3. When would 512 MiB stop being enough?
4. Why is `committed` 512 MiB even though only 330 MiB is used?
5. What happens to Ollama if Neo4j is allowed to auto-size on this 7.6 GB machine? Estimate from the defaults.

**Internals.** The page cache is off-heap native memory mapped to store files; the heap is managed by the JVM's garbage collector.
`dbms.queryJmx` reads the JVM's own management beans.
**Checkpoint:** explain the difference between heap and page cache, and which one should be at least the store size.

---

## C9 — APOC: what the plugin adds, Core vs Extended

### 1. Definition + analogy

**What it is.** **APOC** ("Awesome Procedures On Cypher") is Neo4j's standard library: hundreds of **procedures** (`CALL apoc.x(...)`)
and **functions** (`RETURN apoc.y(...)`) for things plain Cypher does awkwardly or not at all: text cleaning, collection helpers,
batch processing, data import/export, and **schema inspection** (`apoc.meta.data`, `apoc.meta.schema`).

**Why the project enables it.** Graph tooling reads the database schema through APOC. LangChain's `Neo4jGraph.refresh_schema()`, for
example, uses `apoc.meta.data` to learn which labels, relationships and properties exist, so an LLM can be shown the schema when
generating Cypher. Later GraphRAG steps will want the same.

**Core vs Extended.** Since Neo4j 5, APOC is split:
- **APOC Core:** the supported part, shipped **inside the Neo4j image** at `/var/lib/neo4j/labs/apoc-5.26.0-core.jar`. P4-M1
  enables it with `NEO4J_PLUGINS='["apoc"]'`; the start-up script **copies** that jar into `plugins/`, with no download.
- **APOC Extended:** community-maintained extras (for example `apoc.monitor.*`, many import connectors), shipped as a
  **separate jar** you'd have to download. The failed `apoc.monitor.store` call in C8 was an Extended procedure.

**Analogy.** A phone's built-in apps (plain Cypher) versus a trusted app pack that comes pre-installed but switched off (APOC Core),
versus third-party apps you'd have to download separately (APOC Extended).

### For a 14-year-old

Cypher is like a **basic calculator**: it does the main things well. APOC is like switching on the **scientific mode**: suddenly you
have hundreds of extra buttons for text, lists, dates and "tell me what's inside this database".

The basic scientific mode (**Core**) is already built into the calculator we bought; we just flipped the switch. There's also an
**extra add-on pack** (**Extended**) you'd have to download separately. When one of our tests tried an add-on button that wasn't
installed, the calculator said "I don't have that button", which is how we found out which pack it was in.

### 3a. Minimal demo — counting, using and inspecting with APOC (self-cleaning)
`c9_apoc.py`
```python
# C9: APOC -- what the plugin adds, and a schema read that GraphRAG tooling relies on (self-cleaning).
import uuid                                                      # unique label for isolation

from src.graph.setup import build_driver                         # the project's driver factory

label = f"Demo_{uuid.uuid4().hex[:8]}"                           # isolates this demo's nodes

with build_driver() as driver, driver.session() as session:
    # 1. How many procedures and functions the plugin registered
    procs = session.run("SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc.' RETURN count(*) AS n").single()["n"]
    funcs = session.run("SHOW FUNCTIONS YIELD name WHERE name STARTS WITH 'apoc.' RETURN count(*) AS n").single()["n"]
    print(f"1. APOC {session.run('RETURN apoc.version() AS v').single()['v']}: {procs} procedures, {funcs} functions")

    # 2. A few everyday APOC functions (plain Cypher cannot do these directly)
    print("2. apoc.text.clean   :", session.run("RETURN apoc.text.clean('Car-LLM  Product!') AS v").single()["v"])
    print("   apoc.coll.frequencies:", session.run(
        "RETURN apoc.coll.frequencies(['car','life','car']) AS v").single()["v"])

    try:
        # 3. Schema inspection: what labels, relationship types and properties exist (LangChain uses this)
        session.run(
            f"CREATE (:Person:{label} {{name:'Avery'}})-[:CEO_OF]->(:Company:{label} {{name:'Insurellm'}})"
        ).consume()
        meta = session.run(
            f"CALL apoc.meta.data({{includeLabels: ['Person', 'Company', '{label}']}}) "
            "YIELD label, property, type, elementType "
            "WHERE label IN ['Person', 'Company'] RETURN label, property, type, elementType ORDER BY label, property"
        ).data()
        print("3. apoc.meta.data (Person/Company only):")
        for row in meta:
            print("     ", row)
    finally:
        n = session.run(f"MATCH (n:{label}) DETACH DELETE n RETURN count(n) AS n").single()["n"]
        print("4. cleanup:", n, "nodes deleted")
```
Real output:
```
1. APOC 5.26.0: 192 procedures, 246 functions
2. apoc.text.clean   : carllmproduct
   apoc.coll.frequencies: [{'count': 2, 'item': 'car'}, {'count': 1, 'item': 'life'}]
3. apoc.meta.data (Person/Company only):
      {'label': 'Company', 'property': 'name', 'type': 'STRING', 'elementType': 'node'}
      {'label': 'Person', 'property': 'CEO_OF', 'type': 'RELATIONSHIP', 'elementType': 'node'}
      {'label': 'Person', 'property': 'name', 'type': 'STRING', 'elementType': 'node'}
4. cleanup: 2 nodes deleted
```
**How to read it.**
- **Line 1:** the Core jar added **192 procedures and 246 functions**, matching the version the health check reports.
- **Line 2:** `apoc.text.clean` lowercases and strips non-alphanumerics ("Car-LLM  Product!" becomes "carllmproduct"), useful for
  matching entity names like "Carllm" / "CarLLM" later. `apoc.coll.frequencies` counts list items.
- **Line 3:** `apoc.meta.data` reports, per label, each property and its type, and each outgoing relationship type
  (`Person` has `CEO_OF`). This is the schema summary graph tooling gives an LLM.
- **Line 4:** the two demo nodes were removed.

### 3b. Exact project code
`docker-compose.yml`:
```yaml
#▸ L28-29: explain where the jar comes from (bundled in the image) and why it's needed (graph tooling)
28        # The APOC Core jar ships in the image's labs folder, so this enables it
29        # without a download; graph tooling (e.g. LangChain schema reads) expects it.
#▸ L30: a JSON list of plugins; the entrypoint copies labs/apoc-*-core.jar into plugins/ at start-up
30        NEO4J_PLUGINS: '["apoc"]'
```
`src/graph/setup.py`, lines 96–97 (readiness) and 107–112 (detection) are annotated in C7. The container log confirms the install:
`Installing Plugin 'apoc' from /var/lib/neo4j/labs/apoc-*-core.jar to /var/lib/neo4j/plugins/apoc.jar`.

### 4. Benefits
Schema inspection and utility functions available with no download and no version mismatch (the jar is built for this exact Neo4j).

- **What breaks:** assuming an Extended procedure exists (`ProcedureNotFound`); a manually downloaded APOC jar of the wrong version
  (Neo4j refuses to start).
- **Docs:** [APOC Core documentation](https://neo4j.com/docs/apoc/current/) ·
  [APOC in Docker (`NEO4J_PLUGINS`)](https://neo4j.com/docs/operations-manual/current/docker/plugins/) ·
  [LangChain Neo4j integration](https://python.langchain.com/docs/integrations/graphs/neo4j_cypher/)

### Micro-exercises
1. `SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc.meta' RETURN name`. Which schema procedures exist?
2. Compare `apoc.meta.data` with `apoc.meta.schema` on the demo graph. How do the outputs differ?
3. Use `apoc.text.levenshteinDistance('Carllm', 'CarLLM')` to measure name similarity.
4. Remove `NEO4J_PLUGINS` in a copy of compose, restart, and read `python -m src.graph.setup`'s message. Restore it.
5. Find one APOC Extended procedure you might want later, and note what installing it would require.

**Internals.** Procedures and functions are Java methods annotated for Neo4j; at start-up Neo4j scans `plugins/*.jar` and registers
them. `SHOW PROCEDURES` lists everything registered.
**Checkpoint:** explain the difference between APOC Core and Extended, and how the project enables Core.

---

## C10 — Testing a single-database server: fakes, live tests, label isolation

### 1. Definition + analogy

**What it is.** P4-M1's tests reuse the P3-M6 patterns (deep dive 16), with one Neo4j-specific constraint.

**(a) A fake driver for hermetic tests.** `health_check` accepts any object with `verify_connectivity()` and `session(database=...)`
(the `GraphDriver` Protocol). The test file's `FakeDriver` can pretend to be healthy, lack APOC, run a different version, or raise
`AuthError`, `ServiceUnavailable` or `TransientError`, covering every branch of C7 with no server, in milliseconds.

**(b) Live tests that skip only when unreachable.** `neo4j_down()` checks whether port 7474 answers. If not, live tests **skip** with
the start command as the reason. But a reachable server that rejects the password is a real **failure**, not a skip: that's
exactly the bug these tests exist to catch. (Before the volume reset, three live tests failed for exactly that reason.)

**(c) Read-only live tests, and the one-database constraint.** **Neo4j Community Edition has a single user database** (`neo4j`)
plus `system`; `CREATE DATABASE` is an Enterprise feature. So tests can't get a scratch database the way Qdrant tests got scratch
*collections*. P4-M1's live tests only **read**. When later steps test **writes**, isolation will use a **unique label per test
run** (`TestRun_<uuid>`) with `DETACH DELETE` in a `finally`, the pattern this document's demos already use, or a throwaway
container.

**Analogy.** Practising a school play: rehearsals with a stand-in (the fake driver) cover every scene quickly; the dress rehearsal
on the real stage (live tests) checks the real thing. And because there's only **one** stage (one database), every group marks
their props with their own colour sticker (a unique label) and clears them away afterwards.

### For a 14-year-old

Testing our database connection is like testing a new phone charger.
- **Pretend tests** (the fake driver): you use a little tester box that can *pretend* to be a phone that's full, empty, broken,
  or the wrong type. You can try every situation in seconds without a real phone.
- **Real tests** (live tests): you plug in your actual phone. If your phone isn't around, you write "skipped: no phone
  available". But if the phone *is* there and the charger doesn't work, that's a real **fail**, not a skip.

And a special rule for our database: there's only **one** shared room to test in (Community Edition has one database). So when
tests put things in the room, each test labels its things with its own **unique sticker** and tidies up everything with that
sticker at the end, so nobody else's things get touched.

### 3a. Minimal demo — one database only, and label isolation
`c10_single_database_isolation.py`
```python
# C10: Community Edition has ONE user database -- so test isolation must use labels, not scratch databases.
import uuid                                                      # unique run id

from neo4j.exceptions import ClientError                         # raised for unsupported commands

from src.graph.setup import build_driver                         # the project's driver factory

with build_driver() as driver:
    # 1. The databases that exist, and what Community Edition says about creating another
    with driver.session(database="system") as system:           # admin commands run against "system"
        print("1. databases:", [r["name"] for r in system.run("SHOW DATABASES YIELD name")])
        try:
            system.run("CREATE DATABASE scratch_test").consume()
        except ClientError as e:
            print("   CREATE DATABASE ->", e.code, "|", e.message[:80])

    # 2. The isolation pattern the project will use instead: a unique label per test run
    run_label = f"TestRun_{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        try:
            s.run(f"UNWIND range(1, 3) AS i CREATE (:Chunk:{run_label} {{i: i}})").consume()
            mine = s.run(f"MATCH (n:{run_label}) RETURN count(n) AS n").single()["n"]
            everyone = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
            print(f"2. nodes with my label: {mine} | all nodes in the database: {everyone}")
        finally:
            s.run(f"MATCH (n:{run_label}) DETACH DELETE n").consume()
            left = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
            print("3. after cleanup, nodes in the database:", left)
```
Real output:
```
1. databases: ['neo4j', 'system']
   CREATE DATABASE -> Neo.ClientError.Statement.UnsupportedAdministrationCommand | Unsupported administration command: CREATE DATABASE scratch_test
2. nodes with my label: 3 | all nodes in the database: 3
3. after cleanup, nodes in the database: 0
```
**How to read it.**
- **Line 1:** only `neo4j` and `system` exist, and Community Edition rejects `CREATE DATABASE` with a specific status code.
- **Line 2:** `UNWIND range(1, 3)` created three nodes in one query; the unique label finds exactly those three.
- **Line 3:** the `finally` removed them. The shared database is back to 0 nodes.

### 3b. Exact project code — the fake driver and the live tests (annotated copy of `tests/graph/test_setup.py`)
```python
#▸ L18: a stand-in for a neo4j Result
18  class FakeResult:
#▸ L19-20: remember the record to hand back
19      def __init__(self, record):
20          self.record = record
#▸ L22-23: like Result.single(): return that one record
22      def single(self):
23          return self.record
#▸ L26: a stand-in for a neo4j Session
26  class FakeSession:
#▸ L27-28: which version and APOC version to pretend (apoc=None pretends APOC is missing)
27      def __init__(self, version="5.26.0", apoc="5.26.0"):
28          self.version, self.apoc = version, apoc
#▸ L30-34: context-manager methods, so `with driver.session(...) as session:` works
30      def __enter__(self):
31          return self
33      def __exit__(self, *exc):
34          return None
#▸ L36: like Session.run(): look at the query text and answer accordingly
36      def run(self, query, **parameters):
#▸ L37: the APOC query from _apoc_version
37          if "apoc.version" in query:
#▸ L38-39: pretending APOC is missing -> raise the same ClientError a real server raises
38              if self.apoc is None:
39                  raise ClientError("Unknown function 'apoc.version'")
#▸ L40: otherwise answer with the pretend APOC version
40              return FakeResult({"version": self.apoc})
#▸ L41: any other query (dbms.components) gets the pretend version and edition
41          return FakeResult({"version": self.version, "edition": "community"})
#▸ L44: a stand-in for a neo4j Driver
44  class FakeDriver:
#▸ L45-48: an optional exception to raise, the session settings, and a log of requested databases
45      def __init__(self, fail_with=None, **session_kwargs):
46          self.fail_with = fail_with
47          self.session_kwargs = session_kwargs
48          self.databases: list[str | None] = []
#▸ L50-52: like verify_connectivity(): raise the configured exception, if any
50      def verify_connectivity(self):
51          if self.fail_with is not None:
52              raise self.fail_with
#▸ L54-56: like session(database=...): record which database was asked for, return a fake session
54      def session(self, *, database=None):
55          self.databases.append(database)
56          return FakeSession(**self.session_kwargs)
...
#▸ L152: check reachability ONCE, at import (a network call with a 2-second timeout)
152 _NEO4J_DOWN = neo4j_down()
#▸ L153: a skip marker that applies only when Neo4j is unreachable, with the reason
153 live = pytest.mark.skipif(_NEO4J_DOWN is not None, reason=_NEO4J_DOWN or "")
#▸ L156-157: register as a "live" test and apply the skip condition
156 @pytest.mark.live
157 @live
#▸ L158: the real server must be ready with the credentials from .env
158 def test_live_graph_is_ready_with_the_configured_credentials():
#▸ L159-160: build a real driver (from settings) and run the real health check
159     with build_driver() as driver:
160         status = health_check(driver)
#▸ L161: ready, or fail showing the problem message
161     assert status.ready, status.problem
#▸ L162: a 5.x server with APOC present
162     assert status.version.startswith("5.") and status.apoc_version
...
#▸ L167: prove the fresh volume took the .env password, not the P1 default
167 def test_live_old_default_password_is_rejected():
#▸ L169-170: try the old default password against the real server
169     with build_driver(password="changeme") as driver:
170         status = health_check(driver)
#▸ L171: it must be refused with an authentication failure
171     assert not status.ready and "authentication failed" in status.problem
```
And `tests/support.py`, lines 128–136:
```python
#▸ L128: define the reachability check for Neo4j
128 def neo4j_down() -> str | None:
#▸ L134: only whether 7474 answers decides skipping (a wrong password is not a reason to skip)
134     if reachable(NEO4J_HTTP_URL):
#▸ L135: reachable -> no reason to skip
135         return None
#▸ L136: unreachable -> the skip reason, including the command that fixes it
136     return "Neo4j not reachable on :7474; start with `docker compose up -d neo4j`"
```
Verified in P4-M1: hermetic 10/10; live 4/4 after the reset (before it, 3 live tests failed on the old password, which is exactly
what they're for); 5 hand mutants of `setup.py` all killed.

### 4. Benefits
Every failure branch tested without a server; the real server verified whenever it runs; a clear plan for write isolation on a
single-database edition.

- **What breaks:** tests that write without a unique label (they collide, or delete other data); treating auth failure as "skip"
  (the key bug hides); a fake that drifts from the real driver (contract risk, deep dive 16 C10).
- **Docs:** [Neo4j editions](https://neo4j.com/docs/operations-manual/current/introduction/#_neo4j_editions) ·
  [Managing databases (Enterprise)](https://neo4j.com/docs/operations-manual/current/database-administration/) ·
  [Testcontainers for Neo4j](https://testcontainers-python.readthedocs.io/en/latest/modules/neo4j/README.html)

### Micro-exercises
1. Add a hermetic test for `FakeDriver(version="4.4.0")`. What problem message do you expect?
2. Write a live test that creates one node with a `TestRun_<uuid>` label and deletes it in `finally`.
3. Why does `neo4j_down()` check HTTP 7474 rather than logging in over Bolt?
4. Stop Neo4j and run `pytest tests/graph -rs`. Read the skip reasons.
5. Parametrise one hermetic test over `FakeDriver()` and the real driver (the contract-test idea). What must stay true for both?

**Internals.** `UNWIND` turns a list into rows, so `UNWIND range(1, 3) AS i CREATE ...` runs the `CREATE` three times in one
query. Neo4j 5 creates a node **token lookup index** by default (`SHOW INDEXES` lists it as type `LOOKUP`), so
`MATCH (n:TestRun_x)` finds nodes by label without scanning the whole graph.
**Checkpoint:** explain why Neo4j tests can't use scratch databases here, and describe the isolation pattern that replaces them.

---

## Validation

Run from the project root; each should match.

```bash
.venv/Scripts/python -m src.graph.setup
# ready: Neo4j 5.26.0 community at bolt://localhost:7687, APOC 5.26.0, browser http://localhost:7474
.venv/Scripts/python -m pytest tests/graph -q -p no:warnings -rs
# 14 passed
docker inspect -f '{{.State.Health.Status}}' advancerag-neo4j-1
# healthy
git check-ignore .env
# .env
```

Self-check questions (answer without looking):
1. Why was the new password rejected before the volume reset, and what are the two fixes?
2. What does `${NEO4J_PASSWORD:?msg}` do that `${NEO4J_PASSWORD:-changeme}` didn't?
3. Why did adding `NEO4J_PASSWORD` to the container stop Neo4j from starting?
4. What does `verify_connectivity()` do that `GraphDatabase.driver()` doesn't?
5. Why does `except AuthError` come before `except (Neo4jError, DriverError)`?

## Visualization

**Where each value comes from**
```
.env ──────────────┬─► docker compose ─► NEO4J_AUTH ─► (first start only) /data/dbms/auth.ini
 NEO4J_PASSWORD    │                   └─► healthcheck splits it: ${NEO4J_AUTH%%/*} / ${NEO4J_AUTH#*/}
 NEO4J_URI         └─► pydantic Settings ─► build_driver() ─► Bolt :7687 ─► verify_connectivity (login)
```

**The readiness decision in `health_check`**
```
verify_connectivity ──AuthError──────────► not ready: "authentication failed ... first created ..."
        │           ──ServiceUnavailable─► not ready: "cannot reach Neo4j ... container running?"
        │           ──other Neo4j/Driver─► not ready: "<ErrorClass>: <message>"
        ▼ ok
dbms.components ─► version 5.x?  ── no ─► problem
apoc.version()  ─► present?       ── no ─► problem
                          no problems ─► READY
```

**Tools to see it yourself**
- **Neo4j Browser:** http://localhost:7474, log in with the `.env` credentials, run `:sysinfo`, `SHOW SETTINGS`, and the C1 queries
  to see the graph drawn.
- **`docker inspect -f '{{json .State.Health}}' advancerag-neo4j-1`:** the last healthcheck results.
- **`docker logs advancerag-neo4j-1`:** plugin installation, the first-start password message, and start-up errors.
- **`cypher-shell`:** `docker exec -it advancerag-neo4j-1 cypher-shell -u neo4j`, an interactive Cypher prompt (type the password when asked).

## End-to-end "watch it work" exercise

1. **Check it's ready:** `python -m src.graph.setup`, then open the Browser and log in.
2. **See the password rule:** run `c2_first_start_only.py` and read the entrypoint message.
3. **See the config mapping:** run `c4_env_to_config_mapping.py` (it starts a throwaway container that exits on its own).
4. **Break it safely:** put a wrong password in `.env`, run `python -m src.graph.setup`, and read the message. Restore `.env`.
5. **Rotate the password properly:** in the Browser, `ALTER CURRENT USER SET PASSWORD FROM '<old>' TO '<new>'`, update `.env`,
   rerun the CLI and the live tests.
6. **Build and query a graph:** run `c1_property_graph.py`, then write your own two-hop query in the Browser (and clean up).
7. **Inspect the schema:** run `c9_apoc.py`, then try `apoc.meta.schema`.
8. **Log it:** add a `LEARNING_LOG.md` line with the one Docker behaviour that surprised you most.

## Canonical sources
- Neo4j: [Docker introduction and auth](https://neo4j.com/docs/operations-manual/current/docker/introduction/) · [Docker configuration](https://neo4j.com/docs/operations-manual/current/docker/configuration/) · [Docker plugins](https://neo4j.com/docs/operations-manual/current/docker/plugins/) · [Ports](https://neo4j.com/docs/operations-manual/current/configuration/ports/) · [Memory](https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/) · [Cypher manual](https://neo4j.com/docs/cypher-manual/current/) · [Status codes](https://neo4j.com/docs/status-codes/current/) · [cypher-shell](https://neo4j.com/docs/operations-manual/current/cypher-shell/)
- Python driver: [manual](https://neo4j.com/docs/python-manual/current/) · [API and errors](https://neo4j.com/docs/api/python-driver/current/api.html)
- APOC: [Core docs](https://neo4j.com/docs/apoc/current/)
- Docker / Compose: [variable interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/) · [healthcheck](https://docs.docker.com/reference/compose-file/services/#healthcheck) · [volumes](https://docs.docker.com/engine/storage/volumes/)
- Shell: [POSIX parameter expansion](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_06_02)
- Configuration: [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) · [Twelve-Factor config](https://12factor.net/config)
- GraphRAG: [Edge et al. 2024 (arXiv:2404.16130)](https://arxiv.org/abs/2404.16130) · [Peng et al. 2024 survey (arXiv:2408.08921)](https://arxiv.org/abs/2408.08921) · [Neo4j GraphRAG for Python](https://neo4j.com/docs/neo4j-graphrag-python/current/)
