# Phase 4, Microstep 4 — Entity deduplication and linking

**Date:** 2026-09-21

## What the task assumed, and what was missing
The task's input is "multiple extractions referencing the same entity with different names". No extraction exists yet
(the extractor is still ahead), so there was no output to deduplicate. It also left four things out that a correct
deduplicator needs: what happens to the **edges** that pointed at a spelling that disappears, how a merged-away
**alias can still be found** (the store matches an exact `name` only, and `SET +=` replaces a list), whether
"Alice" → "Alice Johnson" contradicts the earlier rule "never fuzzy on people" (D-2026-09-20-03), and how anyone would
know the merge is **right**. All four were raised before any code. The order question (extractor first, a thin extractor
slice, or deduplication first) went unanswered, so the recommendation was taken — deduplication first, as a pure function,
on a gold set built from the corpus audit — and said so (D-2026-09-21-01).

## What was built
- **`src/graph/dedup.py`** (standard library only, pure functions over the P4-M3 records):
  - `deduplicate_entities(nodes, *, known_names=None) -> List[GraphNode]`, the specified function;
  - `resolve_entities(...) -> Resolution(nodes, alias_map, ambiguous, conflicts)`, the same merge plus what was refused and what disagreed;
  - `remap_edges(edges, alias_map) -> List[GraphEdge]`, which points edges at canonical nodes and collapses edges that became identical.
- **Tests:** `tests/graph/test_dedup.py`, 109 tests: 104 hermetic, 3 on the real corpus (they skip when `data/` is absent) and 2 live through `GraphStore`. It holds the labelled gold sets, the alias map and edge remapping, and a seeded random check of order-independence, idempotence and no information lost.
- **Docs:** decisions D-2026-09-21-01 to -12, `docs/architecture.md` (Entity deduplication: the strategy, as the task asked), README row, learning log.
- No new dependency, and **nothing is written to the graph**: the module returns nodes; the caller loads them.

The task's own example, run as written:
```
Alice, alice, Alice Johnson  ->  one Employee "Alice Johnson", aliases ["Alice", "alice"]
+ Alice Smith                ->  "Alice" is left alone and reported: it fits two people
```

## Your choices (my recommendations) and how each one fails
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| Order | deduplication first, as a pure function | extractor first; a thin extractor slice first | checked only against the corpus's own variants, never against real extraction output |
| D1 Linking | ingest-time only | also query-time; query-time only | a question that says "Ms. Lancaster" still finds nothing until query-time linking exists |
| D2 Matching | per-label rules + subset match only when exactly one candidate fits | rules alone; fuzzy similarity; embeddings or an LLM | "exactly one" means one **in this batch**: an absent Alice Smith lets a lone "Alice" join Alice Johnson |
| D3 Scope | one label; a Contact only within its organisation | across labels | a real cross-label identity (an employee who signs a contract) is not linked |
| D4 Ambiguity | stay separate, reported | resolve by context; first or most frequent | a residue of duplicates that someone has to review |
| D5 Canonical | people: fullest seen; company, place: constructed; optional `known_names` | always the longest; the most frequent | a person's canonical name depends on the batch; 5 client titles lose their legal suffix |
| D6 Aliases | `aliases` list on the node | `Alias` nodes; a table outside the graph | not indexed, and `SET +=` replaces the list, so a later batch erases earlier aliases |
| D7 Conflicts | canonical wins, gaps filled, lists united, conflicts reported | last writer wins; keep every value | the losing value survives only in the report |
| D8 Shape | the specified function + alias map + `remap_edges` | the list only; one result object | nothing forces the caller to remap; an edge left on a merged-away spelling fails at load |
| D9 Grouping | pairwise, union-find, repeated to a fixpoint | fold into the first match; scored clustering | chaining: A~B and B~C also joins A and C (bounded here by "exactly one") |
| D10 Evidence | gold set + real-corpus checks + mutation | unit tests only; eyeballing | the gold set came from the same audit that shaped the rules, so the scores are optimistic |
| Initials (found later) | an initial is a word | drop single letters | "Jordan Kyle Bishop" and "Jordan K. Bishop" stay two people |

## Verified, not assumed
```
tests, dedup        : 109 passed (104 hermetic, 3 on the real corpus, 2 live; none skipped)
gold sets           : people, companies and places: 0 false splits, 0 false merges on the labelled pairs
real corpus         : 32 clients written 43 ways -> 32 companies (11 pairs that are one client, 11 merged, 0 wrong)
                      32-person roster -> 32 people (nobody merged with anybody)
                      58 first/last-name mentions: 53 name exactly one person (75 pairs with the roster, all 75 merged,
                        0 wrong) and 5 name several (Alex, Chen, Emily, Jordan, Thompson): all 5 left apart and reported
                      11 written places -> 10 nodes (the one merge is San Francisco)
                      32 signature blocks -> 19 contacts; 3 honorifics stripped and kept as aliases
random check        : 2,000 seeded batches in the tests, plus about 90,000 more run by hand: order-independent,
                      idempotent, no two output nodes share a key, no name or property lost, every reported ambiguity real
mutation check      : 101 mutants, all killed in one run against the final tests (the first run killed 92 of 100; the 8 survivors are
                      described below). 14 in normalisation (case folding, Unicode normalisation, an apostrophe splitting a word,
                      punctuation, stacked honorifics, dropped initials, "the", legal suffixes), 5 in places, 7 in the rules table,
                      13 in the canonical choice, 12 in aliases and properties, 10 in scope, keys and the alias map, 7 in the
                      grouping (merge on the first match, ambiguity unreported, a non-strict subset, short forms joining anything,
                      one round, matching by the shortest form), 8 in edges, and 25 one-entry removals from the suffix, honorific
                      and state tables
scale               : 500 people 0.03 s, 2,000 people 0.37 s, 5,000 people 2.2 s (quadratic per label)
live                : 2 tests through GraphStore; the real graph afterwards still has 8 constraints and 0 nodes
full suite          : 441 passed, 11 skipped (Qdrant and Ollama are stopped and each skip says so); was 332 and 11
CI mode             : 398 passed, 54 deselected (was 291 and 52)
ruff, format, mypy  : the three CI commands are clean (ruff check and ruff format --check on src/ and tests/, mypy src/);
                      mypy over src and tests together still reports 3 errors in tests/vectorstore/test_store.py, a file this step did not touch
```
**What the checks taught.** The first mutation run left 8 of 100 mutants alive. Five were real gaps: stacked honorifics (`Prof. Dr.`), a lone contact's organisation keeping its
legal suffix, and three output orders (nodes, ambiguities, their candidates) that no test pinned. Two were state-table entries no test used
(ME, TX), so all 51 are now checked against a second, independent typing. One was dead code: no output depends on the order of the groups, so
the sort was **deleted** instead of tested.

**A random check found what neither the example tests nor the mutants could.** Among 20,000 random batches one gave a different answer on a
second run: `Anna Smith` and `Anna Smith-Jones` both have two blank-separated parts, so the tie went to the shorter text, and the canonical name lost
a word. Completeness is now counted in the words the match used. Mutants test the tests; only a property (order-independent, idempotent, nothing
lost) tested the logic.

**A design flaw, found by asking what a mutant meant.** The first normaliser dropped single-letter words as initials, and a mutant that restored
them survived. Dropping them would have read `J. Bishop` as `Bishop` and landed it on `Alan Bishop`, and made `Jordan K. Bishop` and
`Jordan L. Bishop` one person. Initials are words now (D-2026-09-21-04).

**Two smaller things.** Timing 5,000 synthetic people took 10.8 s because two sets were rebuilt for every pair; building each once gave 2.2 s
with identical output. And my first count of first-name-only mentions in the HR records (103) included full-name hits; recounted properly it is
43, in 10 of the 32 records.

## Key concepts
🔴 = core to RAG and worth being able to explain; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Entity resolution (deduplication, record linkage):** deciding which records describe the same real-world thing, so one entity is one node.
- 🔴 **False merge vs false split:** joining two different entities vs leaving one entity in two pieces. In a graph a false merge is worse: it fuses
  two neighbourhoods, so multi-hop retrieval walks from one person's documents into another's.
- 🔴 **Canonicalisation and aliases:** choosing the one name a node is keyed on and keeping every other spelling as data, so nothing is lost.
- 🔴 **Precision and recall of a merge:** precision = merged pairs that really are one entity; recall = real pairs that were merged. Measured on a labelled
  gold set of must-merge and must-not-merge pairs, because a count of merges cannot tell a good merge from a bad one.
- 🔴 **Ambiguity handling ("exactly one"):** a short form merges only when a single candidate fits; otherwise it is left apart and reported.
- 🟡 **Blocking:** comparing only plausible pairs (same first letter, same organisation) so the work is not quadratic. Taught, not built: hundreds of nodes do not need it.
- 🟡 **Survivorship:** the rule for which property value a merged node keeps when its parts disagree (here: the canonical node's, gaps filled, conflicts reported).
- 🟡 **Order-independence and idempotence:** the same nodes in any order give the same result, and running it on its own output changes nothing. Both are tested; both
  are what let a pipeline re-run safely.
- 🟡 **Union-find (disjoint sets):** a structure that joins groups and answers "which group is this in" in near-constant time.
- 🟡 **Unicode normalisation (NFKC) and case folding:** making visually identical strings compare equal ("é" typed as one character or as `e` plus an accent).
- ⚪ **Frozen dataclasses, sorted output, `MappingProxyType`:** plumbing that keeps the result deterministic and read-only.

Interview markers (`# INTERVIEW:` in `src/graph/dedup.py`): `_cluster` (the two ways to be wrong, and why "exactly one"), its fixpoint loop
(why the result is order-independent), `_canonical` (constructed vs observed canonical names) and `_merge_properties` (survivorship).

## Honest limitations
1. **Only the corpus's own variants have been tried, and no extraction output exists.** The gold sets came from the same audit that shaped the
   rules, so precision and recall of 1.000 say the rules cover those variants, not that they will cover what an extractor produces.
2. **"Exactly one" means exactly one in this batch.** A lone `Alice` joins the one `Alice Johnson` present, even if the Alice meant is an
   `Alice Smith` who was not extracted with her. The rule cannot see nodes it was not given.
3. **A person's canonical name depends on the batch.** Company and place names are constructed, so a suffix, case or state-abbreviation variant never
   changes the key (a short form joined to a longer name still depends on the batch); a person's fullest spelling is whatever the batch held,
   so `Alice Johnson` in one batch and `Alice Johnson Smith` in the next become two nodes.
   `known_names` (the HR roster) pins employees; contacts have no roster.
4. **Aliases are a list on the node, so they cannot be searched and a later upsert replaces them.** The store finds a node only by its exact
   `name`, and `SET n += $props` overwrites the whole list (a live test pins this). The ingestion job must read and union the lists, or aliases
   must become `Alias` nodes. Until then "Ms. Lancaster" in a question still finds nothing.
5. **Five client titles lose their legal suffix.** P4-M2 keyed `Company` on the contract title as written; the constructed name drops `Inc.`
   and `Co.`, so Pinnacle Insurance Co., Premier Adjusters Inc., Roadway Insurance Inc., Stellar Insurance Co. and WellCare Insurance Co.
   are keyed without them. Passing the titles as `known_names` restores them (tested); otherwise D-2026-09-20-04 must be amended.
6. **Not handled, and the gaps fail in opposite directions.** Most leave a duplicate (a false split): a reordered name (`Chen, Alex`), a nickname
   (`Bob` and `Robert`), typos, transliterations; and a glued honorific keeps its place in the key (`Dr.Karen Phillips`). **One is a false merge:**
   a generational suffix is an ordinary word, so a lone `Robert Chen Jr.` absorbs a lone `Robert Chen`, who may be his father (checked: the result is one
   node, `Robert Chen Jr.`, with the alias `Robert Chen`). With both a Jr. and a Sr. present the short form is ambiguous and stays apart. The corpus has no
   such names, so nothing was built; making a suffix block the merge is a small change.
7. **A middle name and its initial stay two people.** `Jordan Kyle Bishop` and `Jordan K. Bishop` share no subset relation, so they are not merged.
   That is a false split, which costs less than the false merge that dropping initials would allow.
8. **Places written as `Remote (Based in Denver, Colorado)` are not merged with `Denver, Colorado`:** 11 written ways become 10 nodes, and the one
   merge is San Francisco. The extractor has to turn those strings into a city plus the `remote` flag on `BASED_IN` before deduplication.
9. **The comparison is quadratic per label:** 500 people take 0.03 s, 2,000 take 0.4 s, 5,000 take 2.2 s. Blocking is the exit, taught and not built.
10. **A losing property value survives only in the report.** Nothing stores both.
11. **Nothing forces the caller to remap edges.** An edge left on a spelling that was merged away fails at load with `MissingEndpoint`, which is
    loud, but the fix is the caller's to remember.
12. **Nodes already in the graph are not touched.** A spelling merged away in this batch but written by an earlier one stays as its own node until
    the ingestion job reconciles them and deletes what a re-run no longer produces.

## How this connects
**Previous:** P4-M2 fixed identity (name; name + organisation for contacts; never fuzzy on people) and P4-M3 gave a store that writes a node by its exact key and reads it by
exact `name`. **This step:** makes the extractor's output safe to load, by turning many spellings into one node with the rest as aliases, and by saying how to rewrite the edges.
**Next:** the extractor, which must emit `Contact` nodes with their organisation, pass the HR roster and the contract titles as `known_names`, and call `remap_edges` before loading;
then the graph ingestion job (nodes, then edges, provenance, and deletion of stale nodes; it must also union alias lists, because the store replaces them); then query-time entity
linking (a question's words to a node, which needs a way to look a node up by alias), fusion with vector results, and an evaluation set.

## How to do this yourself
1. Count before you design: list the variants of one entity in your own data (this corpus: a company written 2 to 4 ways, a person by first name, surname, with an honorific or an initial, a city with a state abbreviation).
2. Write down what a false merge and a false split cost in your system. Everything else follows from which is worse.
3. Write one normaliser per entity type (people, companies, places) and test each on real variants. Group by equal normalised form first.
4. Add partial matching (a short form joining a longer one) only with an ambiguity check, and decide what "ambiguous" does: here, nothing is merged and it is reported.
5. Decide the canonical name, what happens to a disagreeing property, and where the other spellings are kept. Return the mapping so edges can be rewritten.
6. Build a gold set of must-merge and must-not-merge pairs from your own data, and score precision and recall before and after every rule change.
7. Test order-independence (shuffle the input) and idempotence (run it on its own output).
8. Break it on purpose: drop the ambiguity check, drop case folding, and confirm a test fails each time.
9. Read: Christen, *Data Matching* (2012); Elmagarmid et al., "Duplicate Record Detection: A Survey" (2007); Papadakis et al., "Blocking and Filtering Techniques for Entity Resolution" (2020).

## Practice exercises
1. In a REPL, run `resolve_entities` on `Alice`, `alice`, `Alice Johnson` and print `.nodes` and `.alias_map.pairs()`. Add `Alice Smith` and predict the output before you run it.
2. Give it `Alice`, `Alice Johnson`, `Alice Johnson Smith` in all six orders. Put a `print` in `_cluster`'s loop and count the rounds. Why is one round not enough?
3. Find a pair the rules get wrong (try `Bob Chen` and `Robert Chen`). Say what it would take to merge them, and what new false merge that rule would risk.
4. Change `set(form) < set(other)` to `<=`. Which test fails, or does none? Explain `Chen, Alex` versus `Alex Chen`.
5. Remove `.casefold()` from `_words`. Which tests fail, and which of the corpus checks would have noticed?
6. Shuffle the `MIXED` list 1000 times and compare the results. Then replace the repeat-until-nothing-merges loop by a single pass and see which shuffle breaks it.
7. Run `deduplicate_entities` on its own output for the corpus roster plus its first-name mentions. Is it a fixed point? Why does that matter for a re-run of an ingestion job?
8. Write the Cypher `MATCH (e:Employee) WHERE $name IN e.aliases RETURN e` and `PROFILE` it. Is an index used? What would `Alias` nodes change?
9. Upsert a node with `aliases: ["A"]` and then with `["B"]` through `GraphStore` on a `__test_` value. Read it back, then design the read-and-union the ingestion job needs.
10. Score a six-pair gold set by hand (four must-merge, two must-not) where one pair is falsely merged and one is missed. Compute precision and recall, and say which error each number hides.

## Sources
- Fellegi & Sunter (1969), "A Theory for Record Linkage", *Journal of the American Statistical Association* 64(328) — the probabilistic model that later matchers extend.
- Christen (2012), *Data Matching: Concepts and Techniques for Record Linkage, Entity Resolution, and Duplicate Detection*, Springer — the pipeline (normalise, block, compare, classify, evaluate).
- Elmagarmid, Ipeirotis & Verykios (2007), "Duplicate Record Detection: A Survey", *IEEE TKDE* 19(1).
- Papadakis, Skoutas, Thanos & Palpanas (2020), "Blocking and Filtering Techniques for Entity Resolution: A Survey", *ACM Computing Surveys* 53(2).
- Narayan et al. (2022), "Can Foundation Models Wrangle Your Data?", arXiv:2205.09911 — the LLM-as-matcher alternative deliberately not used here.
- Cormen, Leiserson, Rivest & Stein, *Introduction to Algorithms*, the chapter on disjoint-set data structures (union-find).
- Unicode Standard Annex #15, "Unicode Normalization Forms"; Python documentation for `unicodedata.normalize` and `str.casefold`.
- Neo4j Cypher manual, `SET` with `+=` (a map replaces the value of each property it names, lists included).
