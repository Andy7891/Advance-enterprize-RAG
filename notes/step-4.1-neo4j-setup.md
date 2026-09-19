# Phase 4, Microstep 1 — Set up Neo4j

**Date:** 2026-09-19

## Audit first
The `neo4j` service had existed since P1 (`neo4j:5.26.0`), but:
- **`.env` didn't exist.** Compose and `config.py` both fell back to `changeme`.
- **The data volume was initialised on 2026-08-19** with `changeme`. `NEO4J_AUTH` applies only when a
  volume is first created, so a new password in `.env` would have been silently ignored. The container
  log says so on every start.
- The graph was empty (0 nodes), APOC wasn't enabled, memory was auto-sized, and the healthcheck only
  probed HTTP.

## What was built
- **`.env`** (gitignored), with a generated 32-character password (never printed),
  `NEO4J_URI=bolt://localhost:7687` for scripts on the host, and `NEO4J_USER`. **`.env.example`**
  documents the required password and how to change it later.
- **Volume reset:** the empty P1 volume was deleted and re-initialised from `.env`. The deletion was
  blocked for the assistant by the permission system; you ran it.
- **`docker-compose.yml`, neo4j service:**
  - the password is required (`${NEO4J_PASSWORD:?...}`);
  - `NEO4J_PLUGINS='["apoc"]'`;
  - heap 512m–1G and page cache 512m;
  - a `cypher-shell 'RETURN 1'` healthcheck that logs in, with credentials split out of `NEO4J_AUTH`.
- **`docker-compose.yml`, api service:** the password is required there too.
- **`src/core/config.py`:** the default password is now empty (no well-known fallback).
- **`src/graph/setup.py`:**
  - `build_driver()` reads settings at call time, with a 5 s connection timeout;
  - `health_check()` never raises, and returns `GraphStatus(ready, version, edition, apoc_version, problem)`;
  - CLI: `python -m src.graph.setup` (exit 0/1; no `--password` flag).
- **Tests:** `tests/graph/test_setup.py` has 10 hermetic tests and 4 live tests;
  `tests/support.py` gains `neo4j_down()`.

## Verified, not assumed
```
old volume + new .env password   : authentication failed (the trap, observed live before the reset)
fresh volume                      : container healthy (the healthcheck logs in over Bolt)
CLI                               : "ready: Neo4j 5.26.0 community at bolt://localhost:7687, APOC 5.26.0" (exit 0)
Browser                           : http://localhost:7474/browser/ -> HTTP 200
old default password `changeme`   : rejected (live test)
APOC                              : 192 procedures
memory (SHOW SETTINGS)            : heap 512.00MiB..1.00GiB, pagecache 512.00MiB; container ~900 MiB
compose without NEO4J_PASSWORD    : refuses to start ("required variable NEO4J_PASSWORD is missing a value")
tests                             : graph 14 passed; full suite with services up 199 passed, 2 xfailed
mutation tests                    : 5 mutants (connectivity skipped, APOC check, version check,
                                    auth error handling, settings frozen at import) -> all killed
```

**One mistake, and what it taught.** I first passed `NEO4J_USER` / `NEO4J_PASSWORD` to the container
so the healthcheck could use them. **Neo4j refused to start:** the image turns *every* `NEO4J_*`
environment variable into a `neo4j.conf` setting, and strict validation rejected the unknown setting
`PASSWORD`. The fix was to take the credentials out of `NEO4J_AUTH` in the healthcheck with shell
parameter expansion (`${NEO4J_AUTH%%/*}`, `${NEO4J_AUTH#*/}`).

## Why this approach
| Choice | Over | Because |
|---|---|---|
| reset the empty volume | in-place password change | exercises the fresh-clone path; nothing to lose |
| required password | `changeme` fallback | a missing secret should fail, not default |
| 5.26 LTS | 2025.x | already pinned, supported, driver-verified |
| APOC from bundled jar | no plugins | one line, no download, expected by graph tooling |
| fixed memory | auto-sizing | predictable next to Ollama/Qdrant |
| login healthcheck | HTTP probe | "healthy" should mean usable |

## Key concepts
- 🔴 **Property graph:** nodes with labels, typed directed relationships, and properties on both.
- 🔴 **GraphRAG:** retrieval that uses entities and relationships from a graph alongside vector search.
- 🟡 **Bolt vs HTTP:** Bolt (7687) is the binary protocol drivers use; HTTP (7474) serves the Browser and Query API.
- 🟡 **Initial password vs changing it:** `NEO4J_AUTH` applies only to a fresh volume; later changes need `ALTER CURRENT USER`.
- 🟡 **Heap vs page cache:** Java memory for queries vs RAM caching of the graph's store files.
- ⚪ **`NEO4J_*` → config mapping:** the Docker image turns these variables into `neo4j.conf` settings.
- ⚪ **Shell parameter expansion:** `${VAR%%/*}` / `${VAR#*/}` split a string on its first `/`.
- ⚪ **APOC:** the standard Neo4j procedure library.

## Honest limitations
1. The healthcheck uses the admin credentials; a dedicated read-only probe user would be safer
   (Community Edition has no role-based access control, though).
2. Community Edition has **one** user database, so later graph tests that write need isolation by label
   or a throwaway container.
3. Losing `.env` means losing access to the graph (an admin reset would be needed).
4. The live tests are read-only; writes are untested until the schema steps.
5. Three unnamed Docker volumes from earlier containers remain; they're harmless, and were left alone.

## How this connects
**Previous:** Phase 3 built vector retrieval and recorded two retrieval misses about specific entities
(the Carllm product, the CEO). **This step:** a running, authenticated, verified graph database.
**Next (P4-M2+):** decide the entity and relationship model, extract entities from chunks, load them with
constraints, and link graph nodes back to chunk IDs so graph and vector results can be combined.

## How to do this yourself
1. Check whether a data volume already exists (`docker volume ls`), and what's in it, before setting a
   password.
2. Create `.env` from `.env.example` with a strong password, and make sure `.env` is gitignored.
3. Make Compose require the password (`${VAR:?message}`).
4. Enable APOC with `NEO4J_PLUGINS` and set explicit memory with `NEO4J_server_memory_*`.
5. Write a healthcheck that logs in (`cypher-shell ... 'RETURN 1'`), and don't add your own `NEO4J_*`
   variables to the container.
6. Start it and wait for `healthy`; open http://localhost:7474 and log in.
7. Check from Python: `driver.verify_connectivity()`, `CALL dbms.components()`, `RETURN apoc.version()`.
8. Confirm the old default password is rejected.
9. Read the docs: Neo4j Docker configuration, memory configuration, APOC installation.

## Practice exercises
1. Change the password with `ALTER CURRENT USER SET PASSWORD FROM '<old>' TO '<new>'` in the Browser,
   update `.env`, and rerun `python -m src.graph.setup`.
2. Put a wrong password in `.env` and read the CLI message. Is it enough to find the cause?
3. Add `NEO4J_FOO=bar` to the service and watch the container fail; read the log line.
4. Remove `NEO4J_PLUGINS` and check what `health_check` reports.
5. Run `neo4j-admin server memory-recommendation` inside the container and compare with the chosen values.
6. Create two nodes and a relationship in the Browser (`CREATE (:Person {name:'Avery'})-[:CEO_OF]->(:Company {name:'Insurellm'})`),
   query them back, then delete them.
7. Time `health_check` against a stopped container: does the 5 s timeout hold?
8. Write a live test that creates a node with a unique label and deletes it in `finally`.
9. Call `CALL apoc.meta.schema()` after exercise 6 and read the output.
10. Sketch the entity and relationship types you'd extract from `data/company/about.md`.
