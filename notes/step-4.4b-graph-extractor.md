# Phase 4, Microstep 4b — The graph extractor (a step the task assumed existed)

**Date:** 2026-09-21

## The step that was missing
The P4-M5 task lists "GraphExtractor (P4-M2)" as an input. No extractor existed: P4-M2 built the *model*, P4-M3 the records and
store, P4-M4 the deduplication, and D-2026-09-20-10 and D-2026-09-21-01 had deferred the extractor on purpose. Loading chunks into
Neo4j needs something that turns chunk text into `GraphNode` and `GraphEdge` records, so it had to be built first. You accepted the
recommendations D1 to D12; D0 (sequencing) went unanswered, so I took its recommended option (D-2026-09-21-13): **this step builds
the extractor and proves it against the audit with no database; P4-M5, the ingestion job, comes next** and uses your D6 to D12.

## What was built
- **`src/graph/extractor.py`** (standard library plus the project's own records; no database, no model):
  - `GraphExtractor.extract(chunk, *, lookahead="") -> Extraction(nodes, edges, mentions, warnings)`: one chunk, rules per category;
  - `GraphExtractor.extract_all(chunks)`: every chunk, each given the start of its successor in the same document;
  - `GraphExtractor.from_chunks(chunks)`: reads the closed lists (8 products, the 32-person roster, 9 cities) from the corpus itself.
- **Tests:** `tests/graph/test_extractor.py`, 68 tests: 57 hermetic (one wording at a time, each with a near-miss it must ignore) and 11 on the real corpus (audit counts, chunks against whole documents, extraction through P4-M4). The corpus tests skip when `data/` is absent.
- **Docs:** decisions D-2026-09-21-13 to -21, `docs/architecture.md` (Graph extraction), README row, learning log.
- Nothing is written to Neo4j, and nothing merges spellings: several chunks emit the same node and P4-M4's `resolve_entities` unifies them.

What each category yields:
| Category | Nodes | Edges | Read from |
|---|---|---|---|
| contracts | `Contract` (title), client `Company` (+`legal_name`), `Product`, `Contact` | `WITH_CLIENT`, `FOR_PRODUCT`, `SIGNED_BY` (title) | the H1 title; the Parties list; three signature layouts |
| employees | `Employee` (`job_title`), `Location`, `Product` | `BASED_IN` (remote), `WORKS_ON`, `FOUNDED` | the HR summary; product names in the text; "co-founded Insurellm" |
| company pages | `Company` Insurellm, `Employee`, `Location` | `FOUNDED`, `HAS_OFFICE` (headquarters) | "founded by <roster name>"; two wordings of the office list |
| products | `Product` | none | the file name |

## Your choices (my recommendations) and how each one failed or could fail
| Decision | Chosen | Rejected | Failure mode (observed where marked) |
|---|---|---|---|
| D1 Technique | rules | LLM extraction; hybrid | a wording nobody saw yields nothing, silently, unless counts are checked; prose facts hang on exact phrasing |
| D2 Unit | per chunk + metadata (+ a bounded lookahead, found necessary) | per document | **observed:** the chunker cut a signature block in half, so a chunk alone lost one signatory of 46 |
| D3 Input | `read_chunks("data/chunks")` (skips `report.json`) | re-chunk; read Qdrant | stale if the sources changed after P2 ran |
| D4 Properties | minimal (`legal_name`, `title`, `job_title`, edge flags) | everything; chunk text | a question about salary cannot be answered from the graph (intended) |
| D5 Company keys | title as written (M5 passes the titles as `known_names`) | suffix-free keys; legal names | needs all 32 titles present; the extractor does not decide it, M5 does |
| Order | extractor first | one step | one more step before "the full graph" |

## Verified, not assumed
```
tests, extractor    : 68 passed (57 hermetic, 11 on the real corpus; none skipped)
audit numbers       : 223 chunks read in 0.04 s, 0 warnings
                      32 contracts, 32 employees, 8 products; 33 companies (32 clients + Insurellm)
                      32 WITH_CLIENT, 32 FOR_PRODUCT, 32 BASED_IN (5 remote); exactly the audit's 6 WORKS_ON pairs
                      12 legal names (11 differ from the title); 5 offices, 1 headquarters; 1 founder
                      signatories: the audit's 34 blocks are found (32 filled, in 16 contracts) plus 14 from two other layouts = 46, in 23 contracts
                      mentions: 102 of the 223 chunks name at least one entity (Company 40, Employee 33, Location 42, Product 46, Contact 46)
chunks vs documents : the 46 signatories read from chunks equal the 46 read from whole documents
                      without the lookahead: 45 (one signature is cut by the chunker)
through P4-M4       : 262 extracted nodes -> 143 (Company 33, Contact 29, Contract 32, Employee 32, Location 9, Product 8);
                      0 ambiguities, 0 conflicts; 160 edges -> 154 after remapping; 0 dangling endpoints
mutation check      : 36 mutants, all killed in one run against the final tests. The first run killed 32: three survivors were real test gaps (the lookahead's
                      bound, a legal name as a way to be named, a name found only in the lookahead counted as a mention) and one was a mutant that
                      changed nothing, which I replaced. 6 in chunk boundaries, 10 in contracts, 5 in employees, 6 in company pages, 3 in products and
                      closed lists, 6 in mentions
full suite          : 466 passed, 54 deselected in CI mode (was 398 and 54). No live tests were run: the extractor has none and Neo4j is stopped
ruff, format, mypy  : the three CI commands are clean
```
**What the checks taught.** Four things the rules would have got wrong if I had trusted the audit's numbers without re-measuring on the chunks:
1. **The audit counted source files; the extractor reads chunks.** The same signature pattern finds 34 blocks in the files and 33 in the chunks, because P2's
   chunker cut `**Dr. Karen Phillips**` between "Dr." and "Karen". The fix is a bounded lookahead and the rule that a fact belongs to the chunk where it *starts*.
2. **The chunker also rewrites whitespace.** In the files the Parties list is two bullets on two lines; in the chunks it is one line, `- Insurellm, Inc. - DriveSmart
   Insurance Corp.` A pattern written against the file found 0 legal names on the chunks. It now splits on a dash between spaces, and both forms are tested.
3. **The same fact is worded twice.** The offices are "offices in San Francisco (HQ), New York, ..." on the overview page and "its San Francisco headquarters along with
   satellite offices in key markets including New York, ..." on the about page. Reading only one gave a wrong warning; reading both with an explicit `headquarters` flag on
   every edge keeps the two chunks from disagreeing.
4. **A signer's organisation is not always the client's key.** "GlobalRe Partners International, Ltd." signs for the client titled "GlobalRe Partners"; P4-M4 could not
   pin it. A contract has two parties, so a side that is not Insurellm's is the client (D-2026-09-21-17).

A tooling slip is worth recording too: one of my edit scripts lost its backslashes in the shell and turned `\b` in a regular expression into a backspace character. The
headquarters flag was then missing from the About page, which is how it was noticed; the fix was to write such scripts as files, and to check the source for control characters.

## Key concepts
🔴 = core to RAG and worth being able to explain; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Information extraction:** turning unstructured or semi-structured text into structured facts (entities and relations) that a graph can hold.
- 🔴 **Rules versus a language model for extraction:** rules are exact, repeatable and testable against a count but read only the wordings they were written for; a model reads
  prose but is slower, varies between runs and can invent.
- 🔴 **Chunk boundaries cut facts:** a fact that straddles two chunks is invisible to each; the remedy is a bounded look at the neighbour plus a rule for who owns the fact.
- 🔴 **Provenance / mentions:** recording which chunk names an entity, so a graph hit can lead back to the passage (the MENTIONS edges of the next step).
- 🟡 **Whole-document oracle:** running the same rules on the full source text and requiring the chunked result to equal it, which isolates the damage done by chunking.
- 🟡 **Closed-list (dictionary) matching:** recognising entities only from a list read from the corpus (products, staff, cities), which is why no product is invented.
- 🟡 **Audit-count testing:** checking an extractor against counts measured independently, because a rule that reads nothing passes every example test.
- ⚪ **Frozen dataclasses and tuples in `Extraction`:** plumbing that keeps a result read-only.

Interview markers (`# INTERVIEW:` in `src/graph/extractor.py`): the module's rules-versus-model docstring, the lookahead constant (chunk boundaries), `from_chunks` (closed lists
from the corpus) and `_party` (a contract has two parties).

## Honest limitations
1. **Rules read only what they were written for.** A new contract layout yields no signatories and no warning; only the audit-count tests notice. Three layouts are read;
   the 14 signatories from layouts B and C are measured, not audited.
2. **The lookahead is 400 characters.** A fact longer than that and cut by the chunker is still lost.
3. **The chunker also loses layout, not only facts.** Any rule written against a source file must be checked against the chunk text (the Parties list was).
4. **`mentions` are not a recall guarantee.** An HR chunk that says "Avery" never mentions Avery Lancaster; the document link (`HAS_CHUNK`, `DESCRIBED_IN`) covers that.
5. **Contacts are people named in contracts, nothing more.** "John Smith", "Jane Smith" and "John Doe" are in the corpus as signatories; whether they are real people is not the extractor's question.
6. **A contact split by the chunker has no `MENTIONS` edge** unless its name is whole in one chunk's own text; its `SIGNED_BY` edge and its contract are unaffected.
7. **Free-text facts are not extracted:** who reports to whom (one statement in 32 records), performance, skills. The model has no room for them (P4-M2).
8. **The extractor does not merge.** 262 nodes are emitted for 143 entities; without P4-M4 the graph would hold every spelling.
9. **It is only as current as `data/chunks/`.** If the documents changed after P2 ran, the facts are from the old text.

## How this connects
**Previous:** P4-M2 fixed what may exist; P4-M3 wrote it; P4-M4 merged spellings. **This step:** produces the records those steps consume, from the 223 chunks P2 wrote.
**Next (P4-M5):** the ingestion job, using your decisions D6 to D12: one corpus-wide `resolve_entities` call with the roster and the contract titles as `known_names`; `Document` and
`Chunk` nodes, `HAS_CHUNK`, `DESCRIBED_IN` and `MENTIONS`; a rebuild that refuses a non-empty graph unless `--rebuild`; every edge endpoint validated before any write; and a log with the
entity and relationship counts and the extraction time. After that: query-time entity linking, fusion with vector results, and the evaluation set (with the CEO contradiction: HR says
Avery Lancaster, seven signatures say Jennifer Rodriguez).

## How to do this yourself
1. Count before you write a rule: for each category, which facts are in every document, in some, in none, and in how many *wordings* (this corpus: 3 signature layouts, 2 office sentences).
2. Read the text your extractor will actually see. If it reads chunks, open a chunk file and compare it with the source: line breaks and bullets may differ.
3. Write one small pattern per wording, anchor it, and give it a test with the real wording and one with a near-miss it must ignore.
4. Decide which chunk owns a fact, and how a fact that straddles two chunks is handled; measure how many are cut.
5. Build an oracle (the same rules over whole documents, or the audit's counts) and require the chunked result to match it.
6. Run the result through your deduplication and look for dangling edges before any database sees it.
7. Break the rules on purpose (change `==` to `>=`, drop a placeholder check) and confirm a test fails each time.
8. Read: Hearst (1992) on pattern-based relation extraction; Sarawagi (2008) "Information Extraction"; the Python `re` HOWTO; Jurafsky & Martin, *Speech and Language Processing*, the information-extraction chapters.

## Practice exercises
1. Run `GraphExtractor.from_chunks` on `data/chunks` and print the number of nodes and edges by type. Compare each with the audit table in `notes/step-4.2-graph-schema.md`.
2. Take the Harmony Health Plans contract's last two chunks. Call `extract` on the first chunk with and without `lookahead=`, and on both chunks separately. Which signatories does each call find?
3. Change `LOOKAHEAD_CHARS` to 5 and run the corpus tests. Which test fails, and what does its message say?
4. Open `data/chunks/contracts__Contract with DriveSmart Insurance for Carllm.md.json` and find the Parties list; then find it in the source contract. What differs?
5. Write a fourth signature layout (make one up), add a test that fails, then add the pattern. What would have told you a real one was missing?
6. Add a new product page to a scratch copy of the data and rebuild the extractor with `from_chunks`. What changed without editing any code?
7. Feed the extractor a contract title with an unknown product. What is returned, and why is that better than guessing from the word after "for"?
8. Run `resolve_entities` on the extracted nodes without `known_names`. Which five company keys change, and why?
9. Find a fact in an HR record that the model has room for but the rules do not extract. Write the rule and the test.
10. Ask what would break if the extractor emitted `headquarters` only when it was True. Build the two conflicting edges by hand and pass them through `remap_edges`.

## Sources
- Hearst, M. (1992), "Automatic Acquisition of Hyponyms from Large Text Corpora", COLING — the classic pattern-based relation extraction.
- Sarawagi, S. (2008), "Information Extraction", *Foundations and Trends in Databases* 1(3).
- Jurafsky, D. and Martin, J., *Speech and Language Processing*, the chapters on information extraction and relation extraction.
- Edge et al. (2024), "From Local to Global: A Graph RAG Approach to Query-Focused Summarization", arXiv:2404.16130 — LLM extraction per chunk, the alternative not taken here.
- Neo4j GraphRAG Python package documentation (`SimpleKGPipeline`) and LangChain `LLMGraphTransformer` — model-based knowledge-graph builders.
- Python documentation: the `re` module and its HOWTO; `dataclasses`.
