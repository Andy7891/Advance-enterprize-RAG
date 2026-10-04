# Phase 4, Microstep 7 — Integration Checkpoint: Graph Spot-Check Report

**Date:** 2026-09-22
**Core learning:** No — validation. Documented anyway because *what* was checked, and *why it isn't
redundant with P4-M5/M6*, is itself worth recording.

## What this checkpoint is, and isn't
P4-M5's `_verify` stage and P4-M6's `tests/test_graph.py` already prove, mechanically, that Neo4j
holds what the extraction/ingestion code *computed*. This checkpoint asks a different question: does
the graph match what the **raw source documents actually say**, checked by reading them directly and
independently of any extractor code? That's the one class of mistake no automated test here can catch
— a misunderstanding shared between the extractor and any test written against the extractor's own
assumptions. Six checks, spanning every entity category, chosen to sample breadth rather than
re-verify the whole 223-chunk corpus (already done, exhaustively, by M5).

**Ground truth for every check below was read directly from `data/*.md` source files, before any
Neo4j query was run** — never from `Extraction` objects, never from a prior audit number.

## Findings

### Check 1 — An employee: Rachel Martinez
**Source** ([`data/employees/Rachel Martinez.md`](../data/employees/Rachel%20Martinez.md)): Job Title
"Product Manager"; Location "San Francisco, California"; current role "Leads product strategy for
**Carllm**"; an *older* role (2017–2019) "Supported product development for **Marketllm**
marketplace."

```cypher
// find the employee, then everything it's WORKS_ON and BASED_IN to
MATCH (e:Employee {name: 'Rachel Martinez'})
OPTIONAL MATCH (e)-[w:WORKS_ON]->(p:Product)
OPTIONAL MATCH (e)-[b:BASED_IN]->(l:Location)
RETURN e.job_title, collect(DISTINCT p.name) AS works_on, l.name AS location, b.remote
```

| Field | Expected (from source) | Actual (Neo4j) | Match? |
|---|---|---|---|
| `job_title` | "Product Manager" | "Product Manager" | ✅ |
| `location` | "San Francisco, California" | "San Francisco, California" | ✅ |
| `remote` | not stated as remote | `False` | ✅ |
| `works_on` | Carllm (current role) | `["Carllm"]` | ✅ |

**A finding, not a bug:** the older role names "**Marketllm**" — the real product is spelled
"**Markellm**" (compare the two letter by letter: `Market-llm` vs `Marke-llm`). The extractor matches
product names against a *closed list* read from `data/products/`, so a misspelling simply never
matches — no edge, no warning, nothing looks wrong. The graph is behaving exactly as designed
(P4-M4b: rules read only the exact wordings they were written for); the *source document* itself has
an inconsistency that this design silently drops. Worth knowing, not worth "fixing" the extractor for
one typo.

### Check 2 — A contract: DriveSmart Insurance for Carllm
**Source** ([`data/contracts/Contract with DriveSmart Insurance for Carllm.md`](../data/contracts/Contract%20with%20DriveSmart%20Insurance%20for%20Carllm.md)):
Parties "Insurellm, Inc." and "DriveSmart Insurance Corp."; signed by "**Jennifer Rodriguez**, Title:
Chief Executive Officer, Insurellm, Inc." and "**Steven Brooks**, Title: President & Chief Operating
Officer, DriveSmart Insurance Corp."

```cypher
// the contract, its client's legal name, its product, and both signatories
MATCH (c:Contract)-[:WITH_CLIENT]->(client:Company {name: 'DriveSmart Insurance'})
OPTIONAL MATCH (c)-[:FOR_PRODUCT]->(p:Product)
OPTIONAL MATCH (c)-[s:SIGNED_BY]->(contact:Contact)
RETURN c.title, client.legal_name, p.name,
       collect({name: contact.name, org: contact.organization, title: s.title}) AS signatories
```

| Field | Expected (from source) | Actual (Neo4j) | Match? |
|---|---|---|---|
| `legal_name` | "DriveSmart Insurance Corp." | "DriveSmart Insurance Corp." | ✅ |
| `product` | Carllm | "Carllm" | ✅ |
| signatory 1 | Jennifer Rodriguez / CEO / **Insurellm** | `{'name': 'Jennifer Rodriguez', 'org': 'Insurellm', 'title': 'Chief Executive Officer'}` | ✅ |
| signatory 2 | Steven Brooks / President & COO / **DriveSmart Insurance Corp.** | `{'name': 'Steven Brooks', 'org': 'DriveSmart Insurance', 'title': 'President & Chief Operating Officer'}` | ✅ (org correctly shortened to the client's own key, dropping "Corp." — P4-M4's `_party` rule) |

### Check 3 — The company page: founder and offices
**Source** ([`about.md`](../data/company/about.md)): "founded by **Avery Lancaster** in 2015";
"maintains its **San Francisco** headquarters along with small satellite offices ... **New York,
Austin, Chicago, and Denver**." ([`overview.md`](../data/company/overview.md), independently: "offices
in **San Francisco (HQ)**, **New York**, **Austin**, **Chicago**, and **Denver**" — the same five
cities, worded twice, exactly the "one fact two wordings" case P4-M4b's rules were built to handle.)

```cypher
// the company, who founded it, and every office with its headquarters flag
MATCH (i:Company {name: 'Insurellm'})
OPTIONAL MATCH (f:Employee)-[:FOUNDED]->(i)
OPTIONAL MATCH (i)-[o:HAS_OFFICE]->(l:Location)
RETURN collect(DISTINCT f.name) AS founders, collect({city: l.name, hq: o.headquarters}) AS offices
```

| Field | Expected (from source) | Actual (Neo4j) | Match? |
|---|---|---|---|
| founder | Avery Lancaster | `["Avery Lancaster"]` | ✅ |
| offices | 5 cities, San Francisco is HQ | 5 cities returned, `San Francisco, California` the only `hq: True` | ✅ |

### Check 4 — The known CEO contradiction
**Source, independently read for both sides:**
- [`Avery Lancaster.md`](../data/employees/Avery%20Lancaster.md): "**Job Title**: Co-Founder & Chief
  Executive Officer (CEO)".
- The DriveSmart contract (Check 2, above): "**Jennifer Rodriguez** ... Title: Chief Executive
  Officer ... Insurellm, Inc." — read directly, not taken on the extractor's word.

```cypher
// side A: what HR says
MATCH (e:Employee {name: 'Avery Lancaster'}) RETURN e.job_title

// side B: every contract Jennifer Rodriguez signs, and the title she signs with
MATCH (contract:Contract)-[s:SIGNED_BY]->(c:Contact {name: 'Jennifer Rodriguez'})
RETURN contract.title, s.title ORDER BY contract.title
```

| Check | Expected | Actual | Match? |
|---|---|---|---|
| HR's claim | "Co-Founder & Chief Executive Officer (CEO)" | "Co-Founder & Chief Executive Officer (CEO)" | ✅ |
| One of Jennifer's contracts (DriveSmart, read directly in Check 2) | signs as "Chief Executive Officer" | signs as "Chief Executive Officer" | ✅ |
| Total contracts Jennifer signs as CEO | not independently counted (would mean reading all 32) | **7**, listed by name below | — (see note) |

Jennifer Rodriguez's 7 contracts, from Neo4j: Continental Commercial Group (Bizllm), DriveSmart
Insurance (Carllm), GlobalRe Partners (Rellm), Metropolitan Life Group (Lifellm), National Claims
Network (Claimllm), SafeHaven Property Insurance (Homellm), United Healthcare Alliance (Healthllm).
I independently verified one of these seven (DriveSmart) against its own source file; the other six
are trusted from the graph, consistent with a *spot check* (sampling one in depth) rather than an
exhaustive re-read of 31 more contract files.

### Check 5 — Multi-hop: connected with no direct edge
**Claim to check:** Rachel Martinez (Check 1) has no direct relationship of any kind to DriveSmart
Insurance (Check 2) — yet the graph should still connect them, because Rachel works on Carllm and
DriveSmart licenses Carllm.

```cypher
// is there ANY direct edge between the two, in either direction?
MATCH (e:Employee {name:'Rachel Martinez'})-[]-(client:Company {name:'DriveSmart Insurance'})
RETURN count(*) AS direct_edges

// who else licenses the product Rachel works on?
MATCH (e:Employee {name:'Rachel Martinez'})-[:WORKS_ON]->(p:Product)
      <-[:FOR_PRODUCT]-(c:Contract)-[:WITH_CLIENT]->(client:Company)
RETURN p.name, collect(DISTINCT client.name) AS clients_licensing_it
```

| Check | Expected | Actual | Match? |
|---|---|---|---|
| direct edge Rachel ↔ DriveSmart Insurance | none | `0` | ✅ |
| clients reachable through Carllm | at least DriveSmart Insurance | `['Velocity Auto Solutions', 'TechDrive Insurance', 'Roadway Insurance Inc.', 'DriveSmart Insurance']` | ✅ |

Confirmed: zero direct edges, yet Rachel is two hops from DriveSmart Insurance
(`Employee -WORKS_ON-> Product <-FOR_PRODUCT- Contract -WITH_CLIENT-> Company`) — exactly the kind of
answer a graph gives that a row in a spreadsheet cannot.

### Check 6 — Provenance: does HAS_CHUNK match the real files on disk?
**Source, read directly:** `data/chunks/contracts__Contract with DriveSmart Insurance for Carllm.md.json`
holds **4** chunk objects (`_0` through `_3`), independent of any Neo4j query.

```cypher
// every chunk this job says belongs to this document
MATCH (d:Document {doc_id: 'e1d50d989d7d0276'})-[:HAS_CHUNK]->(c:Chunk)
RETURN count(c) AS chunk_count, collect(c.chunk_id) AS chunk_ids
```

| Field | Expected (from disk) | Actual (Neo4j) | Match? |
|---|---|---|---|
| chunk count | 4 | `4` | ✅ |
| chunk IDs | `_0, _1, _2, _3` | same 4, exactly | ✅ |

## The one real discrepancy found — and where it actually lives
While cross-checking Check 5's numbers, `overview.md`'s own prose claims a per-product contract
breakdown: *"Auto Insurance (Carllm): 3 contracts... Bizllm: 7... Claimllm: 7... Lifellm: 6...
Healthllm: 6... Homellm: 4... Markellm: 2... Rellm: 2."* That sums to **37** — not the 32 the same page
(and `about.md`) also state as the company's total.

Querying the real per-product breakdown from the graph:
```cypher
MATCH (c:Contract)-[:FOR_PRODUCT]->(p:Product) RETURN p.name, count(c) ORDER BY p.name
```
gives a clean **4 contracts per product, every product, 8 × 4 = 32** — nothing like the uneven
7/7/6/6/3/4/2/2 the prose describes.

**This is not a graph or extraction bug.** `overview.md` and `about.md` are free-text narrative pages;
their per-category contract counts are not part of the graph model and were never meant to be
extracted (P4-M4b's own stated limitation: "free-text facts are not extracted"). The graph faithfully
reflects the actual 32 contract *files* that exist, which is the thing it was built to reflect. The
discrepancy is in the corpus's own authored narrative text, most likely because those summary pages
and the actual contract files were generated independently and never reconciled. It's exactly the kind
of thing this checkpoint exists to surface: a fact no automated pipeline test would ever flag, because
the pipeline was never asked to check prose against itself — only a human, reading both independently,
would notice the two don't agree.

## Sign-off
6 of 6 structural checks match source-document ground truth exactly. 1 finding recorded (the
`Marketllm`/`Markellm` spelling miss — an accepted, by-design limitation, not a defect) and 1
discrepancy recorded (the corpus's own narrative summary numbers vs. its real file count — a source
data quality note, not a graph defect). **The graph is a faithful, correctly-linked representation of
what the source documents actually contain**, checked independently of the code that built it.

## How to do this yourself
1. Pick an entity you haven't looked at yet (an employee, a contract, a product) and read its **source
   `.md` file directly** — write down what you'd expect the graph to say, before running any query.
2. Open Neo4j Browser (`http://localhost:7474`), log in (`neo4j` / your `NEO4J_PASSWORD`), and run a
   `MATCH` for that exact entity. Compare what comes back against what you wrote down, not against
   what you remember the extractor being told.
3. Prefer checks that cross TWO entities you've independently verified (like Check 5) over checks of
   one entity alone — that's what catches a wiring mistake between correct pieces (the same idea as
   P4-M6, C1).
4. When something doesn't match, work out WHERE the mismatch actually lives before calling it a bug:
   the extraction rule, the source document itself, or (as in this checkpoint) a narrative claim that
   was never meant to be structured data at all.
5. Record what you checked and what you found — even (especially) when everything matches. A
   checkpoint that isn't written down can't be pointed to later as evidence the graph was ever
   verified.

## Key concepts
🟡 = useful engineering (this step has no 🔴 core-RAG concept — it's a validation checkpoint).
- 🟡 **Independent ground truth:** verifying a system against a source that had no part in building
  it, so a shared blind spot in the system can't hide from the check.
- 🟡 **Spot-checking vs. exhaustive verification:** sampling a handful of cases across every category,
  deliberately not re-proving what an exhaustive automated check (P4-M5) already proved.
- ⚪ **Locating a discrepancy correctly:** telling apart "the pipeline is wrong," "the rule has a known
  limitation," and "the source data itself is inconsistent" — three different findings that call for
  three different responses (fix code, accept and document, flag the data).

## Practice exercises
1. Pick a different employee (not Rachel Martinez), read their HR file directly, and run Check 1's
   query against them. Does everything match? Try someone whose HR record mentions TWO products.
2. Pick a contract for a DIFFERENT product and repeat Check 2. Which signature layout does it use —
   A, B, or C (see `docs/21`'s extractor deep dive)? Does the organisation-resolution logic still work?
3. Find a THIRD wording of the "offices" fact somewhere else in `data/company/` (there may not be
   one) — if there isn't, why do you think exactly two pages state it, no more?
4. Re-run Check 4b yourself and pick a SECOND contract (not DriveSmart) from Jennifer Rodriguez's list
   of 7. Read its source file directly and confirm the signature block says what the graph claims.
5. Design a Check 5-style multi-hop claim of your own: pick two entities with no plausible direct
   edge, predict the shortest path between them, then verify it with a live query.
6. Find a chunked document with MORE than 4 chunks (search `data/chunks/*.json` for larger arrays) and
   repeat Check 6 against it.
7. Try to find a SECOND narrative-vs-structure discrepancy like the overview.md contract-count one —
   read `about.md` and `overview.md` fully and look for any other number that doesn't add up.
8. Deliberately pick a company page rather than a contract or employee record — what NEW category of
   fact can you check that Checks 1-6 didn't touch (hint: look at what `_company_page` extracts that
   nothing above exercised)?
9. Open Neo4j Browser yourself, log in, and run `MATCH p = (e:Employee {name:'Rachel Martinez'})-[*1..2]-(comp:Company) RETURN p` — does the rendered graph diagram match what Check 5's text output told you?
10. Write one NEW check of your own, in the same table format (Checked / Expected from source /
    Actual in Neo4j / Match?), for an entity or relationship this report never touched.

## How this connects
**Previous:** P4-M5 built and machine-verified the graph; P4-M6 proved real extractor output survives
storage and query correctly on a fixture. **This step:** the human check neither of those can be —
reading the actual source and confirming the graph agrees, catching the ONE thing a shared blind spot
could hide. **Next:** query-time entity linking and fusion with the P3 vector results; this checkpoint
is the last one before the graph is treated as trustworthy enough to build a retrieval feature on.

## Sources
- [`data/employees/Rachel Martinez.md`](../data/employees/Rachel%20Martinez.md),
  [`data/contracts/Contract with DriveSmart Insurance for Carllm.md`](../data/contracts/Contract%20with%20DriveSmart%20Insurance%20for%20Carllm.md),
  [`data/company/about.md`](../data/company/about.md), [`data/company/overview.md`](../data/company/overview.md),
  [`data/employees/Avery Lancaster.md`](../data/employees/Avery%20Lancaster.md) — every source file read
  directly for this checkpoint.
- [notes/step-2.6-schema-signoff.md](step-2.6-schema-signoff.md) — this project's P2 precedent for a
  checkpoint-shaped step (see decision.md, Phase 2 Microstep 6).
