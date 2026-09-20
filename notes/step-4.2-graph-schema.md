# Phase 4, Microstep 2 — Decide the graph model and enforce it with constraints

**Date:** 2026-09-20

## Audit first
The P4-M1 notes said the schema "waits until extraction decides the entities". The corpus is small enough
(76 documents) that it can decide them directly, so I measured it before choosing anything:

| Finding | Number | Consequence for the model |
|---|---|---|
| Documents | 76: 32 contracts, 32 HR records, 8 product pages, 4 company pages | Entities are enumerable: a fixed model is possible |
| Contracts carrying a `Contract Number` / `Contract ID` | 19 of 32 (13 have none) | A contract number cannot be a contract's identity |
| Contract header layouts before `## Terms` | 8 different; 11 contracts have no header at all | Contract metadata needs per-template parsing later |
| Contracts with an unfilled `[Date]` / `[Name]` placeholder | 7 | Dates and names can be genuinely absent |
| Contract Parties block: legal name vs title name | differs in 11 of 12 (`DriveSmart Insurance` / `DriveSmart Insurance Corp.`) | Company key is the short name; legal name is a property |
| Contract titles that append a tagline to the product | 2 (`Rellm: AI-Powered ...`) | Resolve products against the closed list of 8, don't split titles |
| Employee locations | 11 different strings for 9 cities (`San Francisco, California` and `San Francisco, CA`; 5 `Remote (Based in ...)`) | Locations must be normalised before they are matched |
| Employees naming a product in their role bullets | 6 (Carllm, Homellm, Rellm, two each) | A real employee-to-product relationship exists |
| Signature blocks (dominant layout) | 34 in 17 contracts | People appear in contracts, so the model must place them |
| **Insurellm-side signatories on the HR roster** | **0 of 17** (Jennifer Rodriguez signs as "Chief Executive Officer" 7 times, Michael Torres 5, Sarah Chen 4) | The HR record and About page say Avery Lancaster is CEO: the corpus contradicts itself |
| Client-side signatories who share a name with an employee | 2 (Robert Chen at Fortress, Marcus Johnson at Rapid Claims) | Merging people by name would create false facts |
| Names one word away from a roster name | Sarah Chen / Alex Chen / Robert Chen / Sarah Williams; Jennifer Rodriguez / Carlos Rodriguez / Jennifer Adams | No fuzzy matching on people |
| Explicit reporting lines in HR records | 1 (`James Wilson: Reports directly to CEO`) | Too sparse, and the target is a role, so no `REPORTS_TO` |
| Distinct pricing-tier names in contracts | 9 (product pages use others) | `tier` is a property, not a node |

## What was built
- **`src/graph/schema.py`** — the model as code:
  - `NODE_TYPES`: 8 labels in two layers. Provenance: `Document`, `Chunk`. Domain: `Company`, `Product`,
    `Contract`, `Employee`, `Contact`, `Location`. Each has a `key` (its identity) and a one-line meaning.
  - `RELATIONSHIP_TYPES`: 10 types: `HAS_CHUNK`, `MENTIONS`, `DESCRIBED_IN`, `FOR_PRODUCT`, `WITH_CLIENT`,
    `SIGNED_BY`, `WORKS_ON`, `BASED_IN`, `HAS_OFFICE`, `FOUNDED`, with allowed endpoints and properties.
  - `apply_schema()`: one `CREATE CONSTRAINT ... IF NOT EXISTS` per node type (auto-commit), waits for the
    backing indexes, then verifies. Raises `SchemaMismatch` if a rule is still not in force.
  - `check_schema()`: read-only; matches constraints by *definition*, lists foreign constraints as
    `unexpected` (informational).
  - CLI: `python -m src.graph.schema` (apply) and `--check` (verify only); exit 0/1; reuses the P4-M1
    readiness check, so a wrong password or stopped container gets its specific message.
- **`docs/architecture.md`** — two tables (nodes, relationships) between markers, plus what Community can and
  cannot enforce.
- **`tests/graph/test_schema.py`** — 44 new tests.
- **`pyproject.toml`** — the `live` marker description now includes Neo4j.
- The graph itself: **8 uniqueness constraints, 0 nodes.** Nothing is loaded in this step.

## Verified, not assumed
Probed on the live server (5.26.0 Community) on a throwaway label before any code was written:
```
CREATE ... IF NOT EXISTS, run twice      : constraints_added 1, then 0
same NAME, different definition          : silent no-op (0 added); the old constraint is unchanged
same definition, another name            : silent no-op
IS NOT NULL / IS :: STRING / NODE KEY    : refused: "Property existence constraint requires Neo4j Enterprise Edition"
composite (name, organization)           : accepted; same name at another organisation allowed; exact pair -> ConstraintError
composite with a null part               : two 'Ann' nodes with no organisation were both accepted
MERGE with a null key                    : SemanticError; CREATE with a null key is accepted
schema DDL after a write in one tx       : ForbiddenDueToTransactionType
backing index of a uniqueness constraint : one RANGE index each, ONLINE
```
Then on the real graph:
```
python -m src.graph.schema --check   (empty graph) : SCHEMA NOT IN FORCE: 0/8, exit 1, each missing constraint named
python -m src.graph.schema                         : 8 created, 0 already present
python -m src.graph.schema  (again)                : 0 created, 8 already present
python -m src.graph.schema --check                 : schema verified: 8/8, exit 0
tests, graph      : 58 passed (17 of them live), none skipped
tests, full suite : 234 passed, 11 skipped (Qdrant and Ollama were stopped; each skip names them)
tests, CI mode    : 211 passed, 34 deselected; ruff, ruff format, mypy clean
coverage          : schema.py 99% (the uncovered line is the `python -m` entry point)
mutation tests    : 11 mutants (no IF NOT EXISTS, name-only matching, no verification, created/present swapped,
                    identifiers unvalidated, unexpected hidden, no index wait, --check writing, composite order
                    reversed, constraint name ignoring the key, dropped mismatch) -> all killed
after the tests   : 8 real constraints, 0 nodes, no scratch constraints or labels left
```

**One mistake, and what it taught.** I first concluded the HR records contain no reporting lines, from a
search for `reports? to`. That pattern cannot match "Reports directly to CEO"; a broader search found it
(James Wilson). I corrected it before it reached a decision. When a search says "none", run a second,
differently shaped one before believing it.

## Why this approach
| Choice | Over | Because |
|---|---|---|
| closed model from the audit | LLM-proposed types per chunk | every category is templated; constraints only guard labels fixed in advance |
| IDs in the graph, text in Qdrant | copying text into nodes | one owner of the text; results join on `chunk_id` |
| `Employee` and `Contact` | one `Person` | the corpus has namesakes and two different CEOs |
| natural keys (`doc_id`, `name`, `(name, organization)`) | generated ids, contract numbers | idempotent `MERGE`; 13 contracts have no number |
| `IF NOT EXISTS` + verify by definition | `IF NOT EXISTS` alone | a same-name, different-definition constraint is silently skipped |
| no auto-repair on mismatch | drop and recreate | dropping a constraint is a human decision |
| tests run on a fake *and* the real server | fake only | the fake encodes beliefs about Neo4j; running the same tests on both keeps them true |
| sweep scratch data by name prefix | remembering what was created | a crashed run is cleaned up by the next one |

## Key concepts
- 🔴 **Node label and relationship type:** the vocabulary of the graph. A closed vocabulary is what makes
  constraints and later queries dependable.
- 🔴 **Uniqueness constraint + `MERGE`:** the constraint makes "one node per identity" a database rule;
  `MERGE (n:Label {key: $k})` then finds-or-creates, so re-running a load never duplicates.
- 🔴 **Identity vs properties:** the key decides *which* node; everything else describes it. Choosing a key
  that is not always present (a contract number) is the classic modelling mistake.
- 🟡 **Composite key:** `(name, organization)` says "this pair is unique"; the same name elsewhere is a
  different node. It does not protect a node that lacks one of the parts.
- 🟡 **`IF NOT EXISTS` semantics:** a no-op if the *name* exists (whatever its definition) or an equivalent
  constraint exists under another name. Success does not mean "the rule you wanted is in force".
- 🟡 **Community vs Enterprise:** uniqueness constraints work; existence, type and node-key constraints do not.
  Required properties become the loader's job.
- ⚪ **Backing index:** each uniqueness constraint owns a range index, so a lookup by key is indexed for free.
- ⚪ **Auto-commit DDL:** schema changes cannot follow a write in the same transaction, so each runs alone.

## Honest limitations
1. **Nothing is loaded.** The constraints are proven to enforce; that the *model answers the questions that
   motivated it* (the Carllm and CEO retrieval misses) is untested until extraction and graph retrieval exist.
2. **The corpus contradicts itself about the CEO.** HR and About say Avery Lancaster; 7 signature blocks say
   Jennifer Rodriguez. The model keeps both (`Employee` vs `Contact`) but does not decide which is true. The
   retriever and its evaluation have to, and the expected answer for that test should be confirmed.
3. **Relationship endpoints are not enforced.** Community cannot say "`WORKS_ON` goes from `Employee` to
   `Product`". `RELATIONSHIP_TYPES` is the reference; nothing checks it yet.
4. **A key with a missing part is not protected** (observed). The loader must `MERGE` with complete keys.
5. **The counts are for this corpus today.** 17 signature blocks is a lower bound: only the dominant layout
   was parsed, and 7 contracts have placeholders.
6. **The doc guard checks names, keys, endpoints and properties, not the prose meanings.**
7. **Entity resolution rules are decided but not implemented:** exact full-name match plus organisation
   context, never fuzzy matching on people. That belongs to extraction.

## How this connects
**Previous:** P4-M1 delivered an authenticated, verified, empty Neo4j. **This step:** what goes in it and the
rules that keep it consistent. **Next (P4-M3+):** extract entities and relationships from the 76 documents into
this model (parsers for the templated categories, checked against the audit counts above), load with `MERGE`
and complete keys, and link entities back to `chunk_id` so graph and vector results can be combined.

## How to do this yourself
1. Count before you model: for each category, which fields are present in *every* document, which are not, and
   in how many layouts. Script it; don't eyeball.
2. Look for the traps that break identity: the same name meaning two things, two spellings of one thing, a
   field missing from a third of the files.
3. Pick each label's key from what is always present. Write down what you deliberately left out and why.
4. Probe the database on a throwaway label: what does `IF NOT EXISTS` do on a conflict, what is refused, what
   happens to nulls? Record the observed behaviour.
5. Write the model as data (labels, keys, relationship endpoints) and generate the DDL from it.
6. Make `apply` idempotent and make it verify by definition; add a read-only `--check`.
7. Test the same scenarios against a fake and the real server; sweep scratch data by name prefix.
8. Compare your documentation tables to the model in a test.
9. Read: Neo4j constraints reference, Cypher `MERGE`, graph data modelling guidelines.

## Practice exercises
1. Run `python -m src.graph.schema --check`, then `DROP CONSTRAINT company_name_unique` in the Browser and run
   it again. What does it say? Re-apply.
2. In the Browser create `(:Company {name:'A'})` twice with `CREATE`, then with `MERGE`. Which one errors?
3. Create a constraint named `company_name_unique` on `Company.legal_name` first, then run the apply. Read the
   message, drop it and apply again.
4. Try `CREATE CONSTRAINT x FOR (n:Company) REQUIRE n.name IS NOT NULL` and read the refusal.
5. Create two `Contact` nodes with the same name and different organisations, then the same pair twice.
6. Create two `Contact` nodes with a name and *no* organisation. Why did the constraint allow it, and what in
   the loader would have to prevent it?
7. Add a `Department` label to `NODE_TYPES` and run the tests. Which test fails, and what does it tell you to
   update?
8. Reverse the order of the `Contact` key and predict what `--check` reports.
9. Write the Cypher for "who works in Austin?" and "which clients have a Carllm contract?" against this model.
10. From the audit, list one more fact you could put in the graph and argue for or against it in the style of
    `decision.md`.
