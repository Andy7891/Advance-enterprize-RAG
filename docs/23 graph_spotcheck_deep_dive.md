# 23 — Graph Spot-Check: Deep Dive (P4-M7)

> Every output block in this document was produced on 2026-09-23 by running the code: against a
> live Neo4j **5.26.0 community** + APOC (`docker compose up -d neo4j`) holding the real 223-chunk
> corpus, and the real findings from
> [`notes/step-4.7-graph-spotcheck-report.md`](../notes/step-4.7-graph-spotcheck-report.md). All
> scripts are in
> [`practice codes/phase 4/m7_graph_spotcheck_deep_dive/`](../practice%20codes/phase%204/m7_graph_spotcheck_deep_dive/).
> Run any of them from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 4/m7_graph_spotcheck_deep_dive/d0_self_check_vs_independent_check.py"
> ```
>
> `d4` needs `docker compose up -d neo4j`; it writes and deletes a tiny scratch subgraph under a
> throwaway label (`M7FixtureThing`) that the real 8-label model never uses.

---

## 0. Plain-language summary

P4-M7 built no new src/ module. It asked a narrower, more human question: *does the graph P4-M5 built
and P4-M6 tested actually match what the source documents say, checked by someone who didn't write
either of those?* Six checks, each starting from a raw `.md` file read directly (never from the
extractor's own output), found that all six matched — plus two things worth knowing that turned out
not to be bugs at all. This document is about the *thinking*, not a new file: how to check a system
against ground truth it had no part in producing, how much a small sample can and can't prove, how to
correctly locate a mismatch once you find one, and — because it came up directly during this step — a
real boundary this project's own agent will not cross, regardless of convenience.

```
P4-M7: read data/*.md directly  -->  write down expectations  -->  query live Neo4j  -->  compare
          (C1: independent            (C2: 6 checks,               (C3: Cypher            (C4: classify
           ground truth)               not all 76 docs)             pattern match)          any mismatch)

throughout: Neo4j Browser opened for visual exploration, login left to the human   (C5)
```

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | Independent ground truth vs. self-referential checking | 🔴 **Core engineering** (interview-heavy) |
| C2 | Spot-checking / sampling coverage vs. exhaustive verification | 🟡 Engineering (statistics-adjacent) |
| C3 | Cypher pattern matching: `MATCH`, variable-length paths | 🔴 **Core RAG** (GraphRAG interview staple) |
| C4 | Diagnosing WHERE a discrepancy actually lives | 🔴 **Core engineering** (interview-heavy) |
| C5 | The credential boundary in agentic tooling | 🔴 **Core AI-engineering** (agent safety, very interview-relevant right now) |

---

## C1 — Independent ground truth vs. self-referential checking

### Do this first
Before reading on, run
[`d0_self_check_vs_independent_check.py`](../practice%20codes/phase%204/m7_graph_spotcheck_deep_dive/d0_self_check_vs_independent_check.py).
**Predict** first: if a buggy extractor is run twice on the SAME input, will comparing its two outputs
ever reveal that it's wrong?

### 1. Definition + analogy
**What it is.** A check is only as strong as what it's checked AGAINST. **Self-referential
checking** compares a system's output to itself — a previous run, its own log, its own internal
model of what it did. It can catch *regressions* (a change that makes today's answer differ from
yesterday's), but it can never catch a mistake baked into the system from the start, because the
"before" and "after" both share the same blind spot. **Independent ground truth** checking compares
the system's output to something built with NO knowledge of the system's own code or assumptions — a
raw source document, a hand count, a different implementation entirely. Only this second kind can
catch a bug the system has always had.

**Analogy.** If you mark your own exam paper using your own worked solutions, a mistake in your
method shows up as a "correct" answer both times — your solution key has the same mistake your answer
does. A mistake is only caught when someone (or something) that solved the problem an entirely
different way checks your work.

### For a 14-year-old
Imagine you're grading your own maths homework using an answer key — but here's the twist: you wrote
the answer key yourself, using the SAME method you used to solve the actual problems. If you made a
mistake in your method (say, you always forget to carry the 1 in addition), that exact same mistake
is baked into BOTH your homework answers AND your answer key. When you check one against the other,
they match perfectly — "10 out of 10, great job!" — even though every single answer is actually wrong.
The checking felt real. It caught nothing, because the thing doing the checking and the thing being
checked shared the exact same blind spot.

Now imagine instead you ask your little sister — who's never seen your method, doesn't know your
shortcuts, and just carefully adds up the numbers her own way — to check just THREE of your answers by
solving them completely from scratch, herself, on paper. She's not looking at your work at all; she's
redoing the problem independently and then comparing her own answer to yours. If your "forget to carry
the 1" mistake is in there, this is the ONLY way of the two that has any chance of catching it, because
her method doesn't share your blind spot.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| grading your own homework with your own answer key | comparing Neo4j against `Extraction.nodes`/`edges` — the extractor's own belief about the text |
| the shared "forget to carry the 1" mistake | a rule-based extraction miss the extractor's own author (me) would never think to test for |
| your little sister solving it fresh, on her own | reading `data/employees/Rachel Martinez.md` directly, before running any query |
| her method has no access to your shortcuts | `independent_check` in the demo never looks at what the extractor decided — only at the raw source text |
| catching the "Marketllm" typo this way | `test_graph_store.py`/`test_ingest.py` never would have looked for it; only C1's approach could |

### 2. Why here specifically
P4-M5's `_verify` and P4-M6's `tests/test_graph.py` are both EXCELLENT at their job — but their job is
comparing the graph against what the pipeline computed, written by the same person (me) with the same
assumptions about the corpus. P4-M7 exists precisely because that comparison, however thorough,
structurally cannot catch a mistake shared between "what I built" and "what I tested." Reading the
source directly is the only way to get a second, truly independent opinion.

### 3. Minimal code
`d0_self_check_vs_independent_check.py` — the same buggy extractor, checked two ways:
```python
# Minimal demo of C1: checking a system against ITS OWN prior output proves nothing new;
# checking it against an INDEPENDENT reading of the original source can catch a real miss.
# Recreates the shape of the real Rachel Martinez / Marketllm finding from P4-M7.
SOURCE_TEXT = "Supported product development for Marketllm marketplace."
# the ACTUAL closed list of real product names (from data/products/ file stems)
KNOWN_PRODUCTS = {"Markellm", "Carllm"}


def buggy_extractor(text):
    # "extracts" a product by looking for an EXACT closed-list name inside the text
    found = [p for p in KNOWN_PRODUCTS if p in text]
    # returns whatever exact matches it found (possibly none at all)
    return found


extracted = buggy_extractor(SOURCE_TEXT)
print("extractor's own output:", extracted)


def self_check(extracted_before, extracted_after):
    # compares the system's output against ANOTHER RUN of the SAME system
    return extracted_before == extracted_after


again = buggy_extractor(SOURCE_TEXT)
print("self-check (compares output to itself):", self_check(extracted, again))


def independent_check(source_text, extracted):
    # reads the RAW text directly, independent of the extractor's own beliefs
    mentions_something_llm_shaped = "llm" in source_text.lower()
    # flags a problem only if the text LOOKS like it names a product, but nothing came out
    looks_suspicious = mentions_something_llm_shaped and not extracted
    return not looks_suspicious


print("independent check (reads the raw text directly):", independent_check(SOURCE_TEXT, extracted))
```
Real output:
```
extractor's own output: []
self-check (compares output to itself): True
independent check (reads the raw text directly): False
```
`self_check` says "True" — everything's fine. `independent_check`, reading the same raw sentence with
no knowledge of the extractor's rules, says "False" — something looks off. Same underlying miss, two
completely different verdicts, depending only on what the check was compared against.

### The exact code I wrote (`notes/step-4.7-graph-spotcheck-report.md`)
```
## What this checkpoint is, and isn't
P4-M5's `_verify` stage and P4-M6's `tests/test_graph.py` already prove, mechanically, that Neo4j
holds what the extraction/ingestion code *computed*. This checkpoint asks a different question: does
the graph match what the **raw source documents actually say**, checked by reading them directly and
independently of any extractor code?
...
**Ground truth for every check below was read directly from `data/*.md` source files, before any    <- 🎯 INTERVIEW
Neo4j query was run** — never from `Extraction` objects, never from a prior audit number.
```
**🎯 Interview-relevant line:** the bolded sentence. It's a rule I held myself to procedurally — read
the file, write down what I expect, THEN query — precisely because it's easy to accidentally query
first and let the result quietly shape what you "remember" expecting.

### 4. What breaks if this changes
- **Check against the extractor's own output instead:** every P4-M7 finding disappears. The Marketllm
  miss and the overview.md discrepancy are both invisible from that vantage point — the extractor
  never claimed anything about them in the first place, so there'd be nothing to disagree with.
- **Check against a PRIOR spot-check's own notes, instead of re-reading the source each time:** the
  checkpoint degrades into a self-check over a few iterations — "does this match what I wrote down
  last time" is not "does this match what the document actually says."

### [Interview relevance: very high — "how do you validate an ML/data pipeline when your tests were written by the same person who built it" is a real, commonly-asked reliability question]

**Interview question:** *"Your team's test suite for a data pipeline has 95% coverage and has never
caught a production bug. What's a possible explanation, and what would you do about it?"*
**A strong answer covers:** that high coverage measures LINES executed by tests, not INDEPENDENCE of
the test's expectations from the code's own assumptions; that if the same engineer (or team, or LLM)
wrote both the pipeline and its tests, a shared misunderstanding of the domain can pass every test
while being wrong; and the fix — periodically validating against a source with no relationship to the
code (a domain expert's manual review, the raw input data, a completely different implementation) —
not just adding more tests written the same way.

### Canonical docs
- Popper, K., *The Logic of Scientific Discovery* — falsifiability: a test that can't fail against
  independent evidence isn't really testing anything (the philosophical root of this whole concept).
- [Google, "The ML Test Score" (2017)](https://research.google/pubs/pub46555/) — see the section on
  validating a pipeline's assumptions against data it wasn't built from.
- Beizer, B., *Software Testing Techniques* — the "pesticide paradox": tests eventually stop finding
  new bugs BECAUSE the code has adapted to pass exactly those tests.

### Micro-exercises (absolute-beginner, learn by doing)
1. Run `d0_self_check_vs_independent_check.py`. Change `SOURCE_TEXT` to `"Rachel works on Carllm."`
   (a correctly-spelled product). Does `independent_check` still flag a problem? Why not?
2. Add a SECOND typo case, `"Worked closely with the Healthlm team"` (missing an 'l'). Extend
   `independent_check` to catch it too — what general rule are you writing, beyond "contains llm"?
3. Pick a REAL employee file you haven't looked at (`data/employees/*.md`), read it, and manually
   check whether every product it mentions is spelled EXACTLY as one of the 8 real product names.
4. Explain, in one sentence, why running `buggy_extractor` a THIRD time and comparing it to the first
   two runs still wouldn't catch the Marketllm miss, no matter how many times you repeat it.
5. Find one other place in this project where two independently-derived numbers were compared (hint:
   P4-M4b's audit-count testing, or P4-M2's schema audit). What was the independent source in each
   case, and was it a document, a hand-count, or something else?

**How it works internally.** There's no library or framework here — this is a property of WHAT you
compare against, not a tool. The only "internals" worth understanding is your own check's dependency
graph: trace backwards from your assertion to its expected value, and ask whether that expected value
was EVER derived from the same code path you're testing.

### How to validate this yourself
The two printed verdicts (`True` vs `False`) on the exact same underlying miss ARE the validation —
side by side, they show the whole concept in two lines of output.

### How to visualize this
Draw two boxes side by side: "Extractor" and "Extractor's test," with an arrow from one to the other
labelled "written by the same assumptions." Now draw a THIRD box, "Raw source file," with NO arrow
connecting it to either — only a human (or a script) reading it fresh. That missing arrow is the whole
point.

**You should now be able to explain:** why a test suite's coverage percentage says nothing about
whether it can catch a bug the codebase has always had, and what specifically makes a check
"independent."

---

## C2 — Spot-checking vs. exhaustive verification

### Do this first
Before reading on, run
[`d1_spotcheck_sampling_coverage.py`](../practice%20codes/phase%204/m7_graph_spotcheck_deep_dive/d1_spotcheck_sampling_coverage.py).
**Predict** first: if exactly ONE of 76 documents has a real bug, what's your gut guess for how often
a random check of 6 documents would happen to include it?

### 1. Definition + analogy
**What it is.** **Exhaustive verification** checks every single item in a population (all 76
documents, all 223 chunks) — P4-M5's `_verify` and the live corpus test already do this, by count.
**Spot-checking** examines a small SAMPLE instead, trading certainty for speed. The trade-off is
precise and often surprising: a RANDOM sample of size 6 out of 76 catches a single, randomly-placed
bug only about 8% of the time — worse odds than most people expect. P4-M7's 6 checks were NOT random,
though — they were *curated* to hit a different item from every category (employee, contract, company
page, a known edge case, a multi-hop claim, provenance), which is a fundamentally different strategy:
it can't catch a bug in an untouched document, but it's far more likely to catch a bug that affects a
whole CATEGORY of rule, because at least one representative of every category was actually checked.

**Analogy.** Tasting one spoonful from a big pot of soup tells you a lot about the soup's overall
flavor (assuming it's been stirred) — that's a curated, representative sample. Randomly picking a
single square inch of a 76-page document to proofread and hoping it contains the one typo in the whole
thing is a very different, much weaker bet.

### For a 14-year-old
Imagine a teacher has graded 76 essays, and you want to know: did she make ANY grading mistakes? You
don't have time to re-grade all 76 yourself. You have two ways to spot-check her work:

**Way 1 — pick 6 completely at random**, like pulling 6 names out of a hat from the full class of 76.
If there's exactly ONE essay she graded wrong, the odds your random 6 happens to include that ONE
specific essay are pretty low — you're mostly checking essays that were fine anyway, and you might
easily miss the one that wasn't, just by bad luck.

**Way 2 — deliberately pick ONE essay from every different TYPE of assignment** — one persuasive
essay, one narrative, one from the strongest student, one from a student who usually struggles, one
that's unusually short, one that's unusually long. This isn't random at all — you're choosing on
purpose to touch every different "shape" of essay there is. If the teacher tends to make a mistake
with a whole CATEGORY of essay (say, she always mis-grades narrative essays specifically), this
approach is far more likely to catch it than blind luck, because you made sure at least one narrative
essay was in your sample. What it still can't catch is a one-off mistake on a SPECIFIC essay that
doesn't represent any pattern at all — say, she just had a bad moment on essay #41, for no category
reason whatsoever.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| 76 essays | the 76 real documents in the corpus |
| the one specific essay with a random mistake | `BROKEN_ITEM = 41` in the demo — a hypothetical single bad document |
| picking 6 essays completely at random | `random.sample(range(76), 6)` in `d1`'s simulation |
| picking one essay per CATEGORY on purpose | P4-M7's actual 6 checks: one employee, one contract, the company page, the CEO case, a multi-hop claim, one provenance check |
| catching a mistake specific to a whole grading category | catching a bug in a whole RULE (e.g. every signature-block parse, every office-city parse) |
| missing a one-off mistake on essay #41 for no pattern reason | missing a typo that's specific to ONE document and no wider rule (which P4-M7 genuinely could not have caught, and doesn't claim to) |

### 2. Why here specifically
P4-M5's exhaustive count-verification already answers "does every number add up." What it structurally
can't answer is "does a SPECIFIC fact, traced by hand from its own source sentence to its own graph
edge, actually look right." Six curated checks, one per category, are enough to sample every KIND of
extraction rule at least once, at a fraction of the cost of hand-verifying all 76 documents.

### 3. Minimal code
`d1_spotcheck_sampling_coverage.py` — how often a sample of a given size would catch one hidden bug:
```python
# Minimal demo of C2: a random SAMPLE of checks catches most single-category bugs cheaply,
# but "cheaply" and "certainly" are different things -- a sample can always miss.
import random

random.seed(7)  # fixed seed, so this demo prints the SAME numbers every time it runs

POPULATION_SIZE = 76  # e.g. the 76 real documents in this corpus
BROKEN_ITEM = 41  # pretend exactly ONE document (out of 76) has a real bug in it


def spot_check(sample_size, trials=20000):
    # runs many pretend "spot checks", each picking `sample_size` random documents
    catches = 0
    for _ in range(trials):
        sample = random.sample(range(POPULATION_SIZE), sample_size)
        # a spot check "catches" the bug only if the ONE broken item happens to be sampled
        if BROKEN_ITEM in sample:
            catches += 1
    return catches / trials


for size in (1, 5, 6, 20, 76):
    rate = spot_check(size)
    print(f"sample size {size:>3} of {POPULATION_SIZE}: catches the bug {rate:.1%} of the time")
```
Real output:
```
sample size   1 of 76: catches the bug 1.5% of the time
sample size   5 of 76: catches the bug 6.7% of the time
sample size   6 of 76: catches the bug 7.8% of the time
sample size  20 of 76: catches the bug 26.2% of the time
sample size  76 of 76: catches the bug 100.0% of the time
```
A RANDOM sample of 6 (P4-M7's actual count) would catch a single hidden document-specific bug less
than 8% of the time. That's exactly why P4-M7's 6 checks were never meant to be random — they were
picked to guarantee every CATEGORY got touched, which this simulation doesn't model at all (it assumes
the bug's location is unknown and uniform, which is the honest worst case for a truly random sample).

### The exact code I wrote (`notes/step-4.7-graph-spotcheck-report.md`)
```
## Findings
### Check 1 — An employee: Rachel Martinez
### Check 2 — A contract: DriveSmart Insurance for Carllm
### Check 3 — The company page: founder and offices
### Check 4 — The known CEO contradiction
### Check 5 — Multi-hop: connected with no direct edge
### Check 6 — Provenance: does HAS_CHUNK match the real files on disk?
```
**🎯 Interview-relevant structure:** six headings, and every single one names a DIFFERENT category of
the graph model (Employee, Contract+Contact, Company+Location, a known data conflict, a multi-hop
relationship pattern, provenance/`Chunk`). That's the curated-sample strategy made visible just from
the table of contents — no two checks touch the same kind of thing.

### 4. What breaks if this changes
- **Pick 6 checks all from contracts (the largest category):** the sample would say nothing about
  employees, the company page, or provenance at all — high depth, zero breadth.
- **Pick 6 TRULY random documents instead of one per category:** per the simulation above, roughly a
  92% chance of missing a hidden single-document bug entirely, and no guarantee every category gets
  represented either.

### [Interview relevance: high — "how would you validate a large dataset/pipeline when you can't check everything" is a standard data-quality interview question]

**Interview question:** *"You have a pipeline that processes a million records. You can't manually
verify all of them. How do you decide what to spot-check?"*
**A strong answer covers:** stratified/curated sampling (deliberately touching every category or edge
case at least once) over pure random sampling, when the goal is catching category-wide bugs cheaply;
being explicit about what a sample CANNOT catch (a bug specific to one record with no wider pattern);
and combining spot-checks with cheap, automatable EXHAUSTIVE checks (like count-verification) so the
two together cover both breadth (spot-check) and completeness (exhaustive count).

### Canonical docs
- [Wikipedia: Stratified sampling](https://en.wikipedia.org/wiki/Stratified_sampling) — the formal
  name for "one from every category" sampling.
- Google, ["What is a Confusion Matrix"](https://developers.google.com/machine-learning/crash-course/classification/thresholding)
  and general ML evaluation docs — the broader idea of sampling strategy affecting what you can detect.

### Micro-exercises
1. Run `d1_spotcheck_sampling_coverage.py`. Change `BROKEN_ITEM` to a different number. Does the
   catch-rate for each sample size change? Why or why not?
2. Modify the simulation so there are TWO broken items instead of one. Does a sample of 6 catch AT
   LEAST ONE of them more often than it caught the single one before?
3. Write a curated (non-random) version of `spot_check`: always include document index 0, 25, 50, and
   75 (spread across the range) plus 2 random ones. Does this catch `BROKEN_ITEM = 41` more reliably
   across different seeds than a fully random sample of 6?
4. In your own words, why does sample size 76 (checking literally everything) give exactly 100%? What
   does that number represent in P4-M5's own `_verify` stage?
5. Pick a 7th category P4-M7 didn't check (hint: re-read `docs/21`'s concepts — is there a category
   involving `MENTIONS` specifically that wasn't directly spot-checked?). Design one check for it in
   the same table format as the real report.

**How it works internally.** `random.sample` implements a variant of reservoir/Fisher-Yates sampling
internally to pick `k` distinct items from a range without replacement in O(k) time; `random.seed(7)`
makes Python's pseudo-random generator produce the exact same sequence every run, which is why this
demo's numbers are reproducible instead of different every time.

### How to validate this yourself
Run the script twice — with and without `random.seed(7)` commented out. With the seed, the numbers
are identical every run (useful for a demo); without it, they wiggle slightly around the same
percentages (useful for confirming the numbers reflect a real underlying probability, not a fluke of
one seed).

### How to visualize this
Plot `sample_size` (x-axis) against `catch rate` (y-axis) for sizes 1 through 76 — the curve is not a
straight line; it should look like a smooth curve approaching 100% as sample size approaches the full
population, which is the classic shape of "diminishing returns from more random sampling."

**You should now be able to explain:** the real, calculated cost of relying on a small RANDOM sample
versus a small CURATED one, and specifically what class of bug neither can ever catch.

---

## C3 — Cypher pattern matching: describing a shape, not a procedure

### Do this first
Before reading on, run (needs `docker compose up -d neo4j`):
```bash
.venv/Scripts/python "practice codes/phase 4/m7_graph_spotcheck_deep_dive/d4_cypher_pattern_matching.py"
```
**Predict** first: in a chain `Alice -> Bob -> Carol`, will a pattern matching "1 to 2 hops from
Alice" return just Bob, just Carol, or both?

### 1. Definition + analogy
**What it is.** Cypher's `MATCH` clause is **declarative**: you describe the SHAPE you're looking for
— `(x)-[:KNOWS]->(y)` means "some node, connected by a KNOWS edge, to some other node" — and Neo4j's
query engine figures out how to find every place that shape occurs in the graph. This is fundamentally
different from a procedural loop ("for each node, check its neighbours, check THEIR neighbours...")
that YOU would have to write by hand in a general-purpose language. A `*1..2` inside a relationship
pattern means "this edge, repeated 1 or 2 times in a row" — a variable-length path, which is what lets
a single query answer "everything within N hops" without you writing any traversal loop at all.

**Analogy.** Asking a librarian "find every book with a red cover, published after 2010, by an author
whose last name starts with M" describes the SHAPE of what you want; you don't tell the librarian
which shelf to walk down first or in what order to check books. `MATCH` is the same kind of request,
aimed at a graph instead of a shelf.

### For a 14-year-old
Imagine a big pile of connected dominoes laid out on a table, each one touching the next in a long,
branching chain. Someone asks you: "find me any domino that's connected to the red domino by exactly
ONE other domino in between." You don't think about HOW to search — you just picture the SHAPE:
red-domino, then one more, then the one you're looking for. You scan the table for that exact shape
wherever it appears, and it might appear in several different places at once, going off in different
directions.

Now imagine someone asks a HARDER question: "find every domino reachable from the red one, going
through ONE or TWO more dominoes, in any direction." This time you're not looking for one exact
distance — you're looking for anything at either 1 hop OR 2 hops away, and it doesn't matter which
direction the chain bends. That's the `*1..2` idea: instead of memorizing "check hop 1, then check hop
2, then combine the results" as a step-by-step procedure, you just describe the RANGE of shape you're
willing to accept, and something else does the actual searching for you.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| a domino | a node (`:M7FixtureThing`, or in the real model, `:Employee`, `:Product`, etc.) |
| two dominoes touching | a relationship (`:M7FIXTURE_KNOWS`, or `:WORKS_ON`, `:FOR_PRODUCT` in the real model) |
| "the red domino, then one more, then this one" | `MATCH (x)-[:REL]->(y)` — a single, fixed-length hop |
| "reachable through one OR two more dominoes" | `MATCH (x)-[:REL*1..2]->(z)` — a variable-length path |
| you don't write the search procedure yourself | Neo4j's query planner decides HOW to traverse; you only state WHAT shape to find |
| the shape appearing in several places on the table | `search_related`/`multi_hop_search` (P4-M3) returning MULTIPLE matches for one pattern |

### 2. Why here specifically
Every one of P4-M7's 6 checks is a `MATCH` pattern, chosen specifically to describe the SHAPE the
source document implied *before* the query was written (an employee node with WORKS_ON and BASED_IN
edges; a contract with WITH_CLIENT, FOR_PRODUCT and two SIGNED_BY edges; a two-hop chain with no direct
edge). Writing the expected shape down BEFORE running the query is what makes the check meaningful —
running a vague, exploratory query and then rationalizing whatever comes back would blur into
confirming the extractor's own output all over again (C1's whole point).

### 3. Minimal code
`d4_cypher_pattern_matching.py` — a tiny 3-node chain, matched two ways, live:
```python
# Minimal demo of C3: a Cypher MATCH pattern describes a SHAPE (nodes and the relationships
# between them) and Neo4j finds every place in the graph that shape occurs. A tiny, isolated
# scratch subgraph, written and deleted by exact identity (the M6/M7 project convention).
from src.graph.setup import build_driver

with build_driver() as driver:
    with driver.session(database="neo4j") as session:
        print("Step 1: write a tiny 3-node scratch subgraph")
        session.run(
            "MERGE (a:M7FixtureThing {name: 'Alice'}) "
            "MERGE (b:M7FixtureThing {name: 'Bob'}) "
            "MERGE (c:M7FixtureThing {name: 'Carol'}) "
            "MERGE (a)-[:M7FIXTURE_KNOWS]->(b) "
            "MERGE (b)-[:M7FIXTURE_KNOWS]->(c)"
        ).consume()
        print("  written: Alice -> Bob -> Carol")

        print("\nStep 2: a pattern that matches ONE direct relationship")
        for row in session.run(
            "MATCH (x:M7FixtureThing)-[:M7FIXTURE_KNOWS]->(y:M7FixtureThing) "
            "RETURN x.name AS x, y.name AS y"
        ):
            print(" ", row["x"], "->", row["y"])

        print("\nStep 3: a pattern that matches TWO hops, through an intermediate node")
        for row in session.run(
            "MATCH (x:M7FixtureThing)-[:M7FIXTURE_KNOWS*1..2]->(z:M7FixtureThing) "
            "WHERE x.name = 'Alice' "
            "RETURN DISTINCT z.name AS reachable_from_alice"
        ):
            print(" ", row["reachable_from_alice"])

        print("\nStep 4: clean up by exact identity -- delete only what step 1 created")
        session.run("MATCH (n:M7FixtureThing) DETACH DELETE n").consume()
        print("  cleaned up")
```
Real output:
```
Step 1: write a tiny 3-node scratch subgraph
  written: Alice -> Bob -> Carol

Step 2: a pattern that matches ONE direct relationship
  Alice -> Bob
  Bob -> Carol

Step 3: a pattern that matches TWO hops, through an intermediate node
  Bob
  Carol

Step 4: clean up by exact identity -- delete only what step 1 created
  cleaned up
```
Step 2's fixed-length pattern finds both direct links (Alice→Bob, Bob→Carol) as separate rows. Step
3's variable-length pattern, starting only from Alice, returns BOTH Bob (1 hop) and Carol (2 hops) —
one query, no loop written by hand, covering a whole range of distances at once. This is the exact
mechanism `multi_hop_search` (P4-M3, exercised again in P4-M6 and re-confirmed by P4-M7's own real
2-hop check).

Note on isolation here: `M7FixtureThing`/`M7FIXTURE_KNOWS` aren't real labels this project's 8-label
model ever uses at all, so deleting `MATCH (n:M7FixtureThing) ...` is safe by construction — even
simpler than M6's fixture, which had to reuse REAL model labels (`Employee`, `Product`) and therefore
needed per-node exact-identity deletion instead of a whole-label wipe.

### The exact code I wrote (`notes/step-4.7-graph-spotcheck-report.md`)
```cypher
-- Check 5's live query: "is there ANY direct edge, and what's reachable in 2 hops instead?"
MATCH (e:Employee {name:'Rachel Martinez'})-[]-(client:Company {name:'DriveSmart Insurance'})
RETURN count(*) AS direct_edges
-- returned 0: no direct edge of ANY type connects them

MATCH (e:Employee {name:'Rachel Martinez'})-[:WORKS_ON]->(p:Product)
      <-[:FOR_PRODUCT]-(c:Contract)-[:WITH_CLIENT]->(client:Company)
RETURN p.name, collect(DISTINCT client.name) AS clients_licensing_it
-- 🎯 INTERVIEW: a 3-hop pattern (WORKS_ON, FOR_PRODUCT reversed, WITH_CLIENT), fully spelled out,
-- found FOUR real clients including DriveSmart Insurance
```
**🎯 Interview-relevant part:** the second query spells out an explicit multi-relationship-type chain
(`WORKS_ON` then `FOR_PRODUCT` reversed then `WITH_CLIENT`) rather than a single `*1..N` — because the
question wasn't "anything within N hops," it was "specifically THROUGH a contract," which needs each
relationship TYPE named in order. Knowing when to use a variable-length `*` versus an explicit chain
of specific relationship types is exactly the kind of Cypher fluency a GraphRAG interview probes for.

### 4. What breaks if this changes
- **Use `*1..3` instead of the explicit 3-edge chain in Check 5:** the query would ALSO match paths
  through relationship types that happen to exist between an Employee and a Company by some other
  route (e.g. through `BASED_IN` and `HAS_OFFICE` if they ever shared a location) — a much less
  precise claim than "specifically through a contract."
- **Forget the `DISTINCT` in step 3's query:** if two different 1-hop paths both eventually reached
  Carol, she'd be returned twice — a subtle bug that only shows up once the graph has more than one
  path between two nodes, exactly the trap `search_related`'s own `DISTINCT r` guards against (P4-M3).

### [Interview relevance: very high — Cypher pattern matching, and specifically variable-length paths, is one of the most direct "have you actually used a graph database" interview checks]

**Interview question:** *"Write a Cypher query to find everything within 2 hops of a given node, and
explain why you'd use `*1..2` instead of two separate queries."*
**A strong answer covers:** the `MATCH (n {id: $id})-[*1..2]-(m) RETURN DISTINCT m` shape itself;
that Neo4j's query planner traverses this as a single graph walk rather than running "1-hop query,
then 2-hop query, then union them" — meaning shared work (like matching the 1-hop neighbours) isn't
repeated; and why `DISTINCT` matters (a node reachable by more than one path at the same or different
hop-counts would otherwise be returned more than once).

### Canonical docs
- [Neo4j Cypher manual: patterns](https://neo4j.com/docs/cypher-manual/current/patterns/)
- [Neo4j Cypher manual: variable-length relationships](https://neo4j.com/docs/cypher-manual/current/patterns/reference/#variable-length-relationships)
- [docs/19 graph_store_deep_dive.md](19%20graph_store_deep_dive.md) — `multi_hop_search`'s full
  implementation deep dive (P4-M3), which this demo is a stripped-down version of.

### Micro-exercises
1. Run `d4_cypher_pattern_matching.py`. Change `*1..2` to `*1..1`. Does Carol still appear in step 3's
   output? Why not?
2. Add a fourth node, Dave, with `Carol -[:M7FIXTURE_KNOWS]-> Dave`, and change the range to `*1..3`.
   Predict the output before running it.
3. Change step 2's pattern from `-[:M7FIXTURE_KNOWS]->` (directed) to `-[:M7FIXTURE_KNOWS]-`
   (undirected, no arrow). Does the result change for this particular chain? What would it change for
   a chain with an edge pointing the "wrong" way?
4. Write a Cypher query (on paper, or live against the real corpus) that finds every `Employee` whose
   `Location` is the SAME city as any `Company` office (a 2-label join with no direct relationship
   type in common — think about what pattern would even express this).
5. Open Neo4j Browser (`http://localhost:7474`) and run
   `MATCH p = (e:Employee {name:'Rachel Martinez'})-[:WORKS_ON|FOR_PRODUCT|WITH_CLIENT*1..3]-(x) RETURN p`
   — does the rendered diagram visually confirm the 4 clients Check 5 found?

**How it works internally.** Neo4j stores relationships as direct pointers between node records on
disk (index-free adjacency) — following a relationship from a node is an O(1) pointer lookup, not an
index search, which is WHY a graph database can traverse a `*1..N` pattern efficiently: cost scales
with the number of PATHS actually explored, not with the total size of the database.

### How to validate this yourself
Steps 2 and 3's different outputs, from the SAME 3-node graph, ARE the validation: one shows exactly
the direct edges; the other shows everything within a range, including nodes with no direct edge to
the start at all.

### How to visualize this
Neo4j Browser renders every query's result as an actual node-and-edge diagram automatically — run
step 3's query there (adjust the label/relationship names) and watch Alice, Bob and Carol appear as
connected circles, with the path highlighted, instead of just three names in a text list.

**You should now be able to explain:** the difference between a fixed-length and a variable-length
Cypher pattern, why `DISTINCT` matters for multi-hop queries, and when an explicit relationship-type
chain is more precise than a `*` wildcard.

---

## C4 — Diagnosing WHERE a discrepancy actually lives

### Do this first
Before reading on, run
[`d2_classify_a_discrepancy.py`](../practice%20codes/phase%204/m7_graph_spotcheck_deep_dive/d2_classify_a_discrepancy.py).
**Predict** first: the `overview.md` contract-count mismatch and the `Marketllm` typo are both "the
graph doesn't say what the source implies" — should they be handled the same way?

### 1. Definition + analogy
**What it is.** Finding a mismatch is only step one. The SAME symptom ("expected X, got Y") can have
three completely different correct responses, depending on where the mismatch actually lives: (1) the
pipeline's code has a genuine bug and should be fixed; (2) the gap is a KNOWN, already-documented
limitation of a deliberate design choice, and the correct response is to note it, not touch the code;
(3) the pipeline is behaving exactly as designed, and the SOURCE DATA itself is the inconsistent one —
fixing "the pipeline" would be fixing something that was never broken. Treating all three the same way
("something doesn't match, go change the code") is how a correct system gets broken chasing a mismatch
that was never its fault.

**Analogy.** A smoke detector going off could mean an actual fire (fix the fire), a battery that's
known to trigger false alarms near the expiry date (a documented, accepted quirk — replace the battery
on schedule, don't rewire the house), or someone burning toast two rooms away (the smoke detector is
working exactly as designed; the toaster is the "issue," and it isn't actually an issue at all).
Reacting to the alarm sound the same way in all three cases would be a mistake every time except one.

### For a 14-year-old
Imagine you're a detective, and three different times this week, a shopkeeper tells you "the cash
register total doesn't match what I expected." Each time, you have to figure out WHY before you decide
what to do — because "fix the register" is only the right answer once.

**Case 1:** you check the register's own transaction log against the ACTUAL pile of receipts on the
counter, and they genuinely disagree — the machine really did add something up wrong. This is a real
bug. Fix the register.

**Case 2:** the register's total is "off" by exactly the amount of a KNOWN, already-explained rounding
rule the store adopted last month (round every total down to the nearest 5 cents) — everyone already
knows about this, it's written on a sign by the till. This isn't a bug at all; it's working exactly as
intended. Don't touch the register — just remember the sign exists.

**Case 3:** the register agrees PERFECTLY with the actual receipts, but the shopkeeper's own
handwritten notebook — where she jots down her own rough estimate of the day's sales as she goes —
says a different number. The register isn't wrong. Her notebook estimate was just off. The problem
isn't the machine at all; it's the separate piece of paper she was comparing it to.

Getting the "why" wrong here is expensive: if you "fix" the register in Case 2 or Case 3, you'd
actually BREAK something that was working correctly, chasing a mismatch that was never the register's
fault to begin with.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the register's actual transaction log vs. real receipts disagreeing | Case 1: `PIPELINE BUG` — like the original `DESCRIBED_IN` miscount from P4-M5, before it was fixed |
| the known, sign-posted rounding rule | Case 2: `ACCEPTED LIMITATION` — the Marketllm/Markellm typo, already documented as "rules read only exact wordings" (P4-M4b) |
| the shopkeeper's separate, rougher notebook estimate | Case 3: `SOURCE DATA ISSUE` — `overview.md`'s own prose contract-count breakdown, never part of the extraction model at all |
| "fix the register" being wrong twice out of three times | changing the extractor's code in response to either Case 2 or Case 3 would have been a mistake |

### 2. Why here specifically
P4-M7 found exactly these three shapes of thing, in miniature: the ORIGINAL `DESCRIBED_IN` bug from
P4-M5 (a real pipeline mistake, already fixed by the time this checkpoint ran), the Marketllm typo (an
accepted limitation, already documented in P4-M4b's own notes), and the `overview.md` narrative
mismatch (a source data quality issue, newly found, that has nothing to do with the pipeline's
correctness at all). Reporting all three as "found a discrepancy" without distinguishing them would
have been far less useful than naming which is which.

### 3. Minimal code
`d2_classify_a_discrepancy.py` — a tiny classifier, run against the three real P4-M7 findings:
```python
# Minimal demo of C4: when a check finds a mismatch, WHERE it lives decides what to do
# about it. Three real P4-M7 findings, run through the same small classifier.
def classify(matches_pipeline_output, matches_source_document, is_a_stated_limitation):
    # everything lines up: pipeline, source, and expectations all agree
    if matches_pipeline_output and matches_source_document:
        return "NO DISCREPANCY -- matches"
    # the gap already has a WRITTEN-DOWN design reason (checked before anything else)
    if is_a_stated_limitation:
        return "ACCEPTED LIMITATION -- document it, do not change code"
    # the pipeline did what its own code says it should, but the SOURCE disagrees
    if matches_pipeline_output and not matches_source_document:
        return "SOURCE DATA ISSUE -- flag the data, do not touch the pipeline"
    # the pipeline's output matches NEITHER its own intent NOR the real source
    return "PIPELINE BUG -- fix the code"


# Finding 1: Rachel Martinez's "Marketllm" typo. The extractor's OWN behaviour (skip an
# unrecognised name) is exactly what P4-M4b documented as its rule-based design's limit.
print("Marketllm typo         :", classify(
    matches_pipeline_output=True, matches_source_document=False, is_a_stated_limitation=True
))

# Finding 2: overview.md's contract-count narrative. The GRAPH matches its own real files
# (the pipeline behaved correctly); the SOURCE's own prose page is what's inconsistent.
print("overview.md contradiction:", classify(
    matches_pipeline_output=True, matches_source_document=False, is_a_stated_limitation=False
))

# Finding 3 (hypothetical): if DESCRIBED_IN had shipped its original, uncaught bug, it would
# have matched NEITHER what the pipeline was supposed to do NOR the real source documents.
print("hypothetical DESCRIBED_IN bug:", classify(
    matches_pipeline_output=False, matches_source_document=False, is_a_stated_limitation=False
))
```
Real output:
```
Marketllm typo         : ACCEPTED LIMITATION -- document it, do not change code
overview.md contradiction: SOURCE DATA ISSUE -- flag the data, do not touch the pipeline
hypothetical DESCRIBED_IN bug: PIPELINE BUG -- fix the code
```
Three different mismatches, three different correct responses — and the classifier makes the
DIFFERENCE explicit instead of lumping them all under "found an issue."

### The exact code I wrote (`notes/step-4.7-graph-spotcheck-report.md`)
```
## The one real discrepancy found — and where it actually lives
...
**This is not a graph or extraction bug.** `overview.md` and `about.md` are free-text narrative
pages; their per-category contract counts are not part of the graph model and were never meant to be
extracted (P4-M4b's own stated limitation: "free-text facts are not extracted"). The graph faithfully
reflects the actual 32 contract *files* that exist, which is the thing it was built to reflect.        <- 🎯 INTERVIEW
```
**🎯 Interview-relevant line:** the bolded sentence, stated FIRST, before any explanation. Leading a
finding with an explicit classification, before the supporting detail, is what turns "I found
something weird" into an actionable report someone can act on without re-deriving the classification
themselves.

### 4. What breaks if this changes
- **Report every mismatch as "found a bug" without classifying it:** someone downstream (possibly a
  future version of me) tries to "fix" the extractor to somehow reconcile `overview.md`'s prose with
  reality, introducing new, unnecessary logic for a problem that was never in the code.
- **Classify everything as "accepted limitation" to avoid extra work:** a genuine future pipeline bug
  gets waved through as "probably fine, like that one time before," and never actually gets fixed.

### [Interview relevance: very high — root-cause analysis, and specifically NOT jumping straight to "fix the code," is a core debugging/reliability-engineering skill assessed in almost every senior engineering interview]

**Interview question:** *"You find that your system's output doesn't match what you expected. Walk me
through how you'd figure out whether it's a bug in your code, a known limitation, or bad input data."*
**A strong answer covers:** checking whether the system did what its OWN logic says it should do first
(if not, likely a real bug); checking whether the gap is already documented as an accepted trade-off
(if so, stop — it's not new information); and, if the system behaved exactly as designed but still
disagrees with an independent source, examining whether that independent source is itself reliable
before assuming the system needs to change — plus naming a concrete example from their own experience
where they (or a teammate) initially misdiagnosed which of the three it was.

### Canonical docs
- [Google SRE Book, "Effective Troubleshooting"](https://sre.google/sre-book/effective-troubleshooting/)
- [The "Five Whys" technique](https://en.wikipedia.org/wiki/Five_whys) — a related, more general
  root-cause technique for tracing a symptom back to where it actually originates.

### Micro-exercises
1. Run `d2_classify_a_discrepancy.py`. Add a FOURTH call representing "an employee's location city is
   spelled two different ways in the SAME HR file" (pick your own flag values) — which bucket should
   it land in, and does the classifier agree with your reasoning?
2. Find `classify`'s branch ordering. What would change if `is_a_stated_limitation` were checked LAST
   instead of second? Would any of the three real findings be classified differently?
3. Take a real P4-M2/M4b limitation from `docs/21` or `docs/22`'s "Honest limitations" sections and
   run it through `classify` by hand — which bucket does it fall into?
4. Write a FOURTH classify branch for a case none of the three real findings hit: "matches the source
   document but NOT what the pipeline's own code says it should produce." What real-world situation
   would that describe (hint: think about what it means for output to accidentally be RIGHT for the
   WRONG reason)?
5. Pick a discrepancy from your own experience (any project, any pipeline) and describe, in one
   sentence each, which of the three flags (`matches_pipeline_output`, `matches_source_document`,
   `is_a_stated_limitation`) applied, and which bucket it landed in.

**How it works internally.** This is a plain decision tree — three `if` statements checked in a fixed
order, each one a yes/no branch. The interesting engineering isn't the code (there's almost none); it's
the DISCIPLINE of always asking these three specific questions, in this order, before deciding what to
do about any mismatch.

### How to validate this yourself
Trace each of the three real findings through the function by hand, on paper, before running the
script — then confirm the printed output matches your own trace.

### How to visualize this
Draw the three `if` branches as an actual flowchart with three exit boxes ("fix code," "document,
don't touch," "flag the data") — then write each of P4-M7's three real findings next to the exit box
it belongs in.

**You should now be able to explain:** the three distinct categories a mismatch can fall into, and why
responding to all of them the same way ("go fix the pipeline") is itself a mistake two times out of
three.

---

## C5 — The credential boundary in agentic tooling

### Do this first
Before reading on, run
[`d3_credential_boundary.py`](../practice%20codes/phase%204/m7_graph_spotcheck_deep_dive/d3_credential_boundary.py).
**Predict** first: is there any real, structural difference between a password sitting in a Python
variable read from `.env`, versus that same password being typed into a login form — or is it just the
same secret, so it shouldn't matter which path it takes?

### 1. Definition + analogy
**What it is.** An AI agent that can both read configuration files AND control a browser has two
completely different paths a secret could travel through, and they are NOT equivalent even when the
bytes are identical. **Path A**: a human already configured `.env` with a password; trusted,
human-authored application code (`build_driver()`) reads it and uses it to open a database
connection it, and the human, already control. **Path B**: the agent itself reads that same value and
then performs a sequence of UI actions — typing into a form field — that it decided to take, in an
interface it does not own the outcome of. The VALUE can be identical; the ACTION is categorically
different, because Path B is the agent actively authenticating itself as a human, into a surface where
mistakes (a wrong site, a phishing look-alike, a shared screen) have real consequences no `.env` file
read ever has.

**Analogy.** A house key sitting in a labelled drawer, used by the homeowner's own smart lock system
to unlock the door when THEY press the button, is completely different from a stranger picking up that
same key and using it to unlock the door themselves — even though it's the exact same metal key. The
object is identical. Who is exercising the access, and through what mechanism, is not.

### For a 14-year-old
Imagine your parents give you the code to the house alarm system so that the SMART DOORBELL — a piece
of software they set up and trust — can automatically disarm the alarm when it recognizes their car
pulling into the driveway. That's completely fine: they configured it, they trust the specific,
narrow thing it does with that code, and it only ever does that one thing.

Now imagine a different scenario: a delivery robot shows up at your door, and to make things "easier,"
it asks YOU to just tell IT the alarm code so that it can type it into the keypad itself, on your
behalf, whenever it needs to get in. Even if the robot is completely trustworthy and would type the
EXACT same code, this is a totally different situation. Now the code is being typed by something that
isn't the system your parents originally set up and scoped to one specific job — it's a general-purpose
actor typing your secret into a keypad because it decided, in the moment, that this was a convenient
way to get something done. If that robot is ever fooled, tricked, or simply wrong about which door
it's standing at, the code has now been typed somewhere your parents never explicitly authorized. The
smart doorbell's code-reading was ALWAYS scoped to one narrow, pre-approved action. The delivery
robot's request was open-ended and general-purpose — and that openness is exactly the risk.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the smart doorbell reading the alarm code, for ONE pre-approved purpose | `build_driver()` reading `NEO4J_PASSWORD` from `.env`, for the one purpose of opening a `neo4j.Driver` |
| your parents configuring and trusting that one narrow behaviour in advance | this project's own `.env` file and `src/core/config.py`, written by the human who owns this project |
| the delivery robot asking to type the code into the keypad itself | the agent typing a password into Neo4j Browser's login form |
| "even though it's the same code, the situations aren't the same" | Path A and Path B in the demo carry the SAME value, through structurally different mechanisms |
| refusing the robot's request, even though it's probably fine this one time | this session leaving Neo4j Browser's login for the human, even for the project's own local dev database |

### 2. Why here specifically
P4-M7's D2 decision picked "Neo4j Browser, opened in the built-in browser pane" for visual, manual
exploration. Getting there required a login. This was the exact moment the credential boundary
actually mattered in this project — not a hypothetical, a real fork in the task where one path (typing
the password) would have been marginally more convenient, and was declined anyway, in favour of
opening the page and leaving the login step to the human.

### 3. Minimal code
`d3_credential_boundary.py` — the same value, two structurally different paths, no real secret used:
```python
# Minimal demo of C5: the SAME password can travel through two different paths -- one a
# trusted, human-configured path (fine), the other an automated UI-typing path (never done
# by this agent, regardless of whose database it is). No real password is used or printed.
import os

os.environ["DEMO_DB_PASSWORD"] = "example-only"


def connect_with_app_config():
    # reads the password from an environment variable the HUMAN already set up
    password = os.environ["DEMO_DB_PASSWORD"]
    # this value flows straight into a driver object the human's own code controls
    return {"user": "neo4j", "password": password, "path": "env -> driver, human-authored code"}


def type_into_a_login_form(password_value):
    # this function represents an AGENT typing a value into a UI FIELD it does not control
    keystrokes = [f"type character: {c!r}" for c in password_value]
    return {"keystrokes": keystrokes[:3] + ["..."], "path": "value -> simulated UI automation"}


connection = connect_with_app_config()
print("Path A (application code reading its own config):")
print(" ", connection)

typed = type_into_a_login_form(os.environ["DEMO_DB_PASSWORD"])
print("\nPath B (an agent filling in a login form):")
print(" ", typed)
```
Real output:
```
Path A (application code reading its own config):
  {'user': 'neo4j', 'password': 'example-only', 'path': 'env -> driver, human-authored code'}
  -- this is what src/graph/setup.py's build_driver() actually does. Normal, expected,
     the same pattern used everywhere in this project to reach Neo4j.

Path B (an agent filling in a login form):
  {'keystrokes': ["type character: 'e'", "type character: 'x'", "type character: 'a'", '...'], 'path': 'value -> simulated UI automation'}
  -- structurally identical VALUE, but now it exists as actions taken inside a UI
     the agent does not own. This is the path this project's agent never takes,
     regardless of who owns the database being logged into.
```

### The exact code I wrote (from this session's own transcript, P4-M7)
```
Good — Neo4j's still up with the real corpus loaded, so this is fully doable right now. Before any
code: the gap analysis, then decisions.
...
**D2 — Where the inspection happens**
- **(a) [recommended] Neo4j Browser at `localhost:7474`, opened in my built-in browser pane** ...
...
Now let me open Neo4j Browser in the built-in browser pane. One note before I do: I have your Neo4j
password available in `.env`, but I won't type it into the login form myself -- entering passwords    <- 🎯 INTERVIEW
into fields is something I don't do, even for a local dev database. I'll open the page and ask you
to log in.
```
**🎯 Interview-relevant line:** stated BEFORE the action, not after. Announcing a safety boundary in
advance, rather than silently working around it or explaining it only if asked, is what makes a
boundary a genuine design principle rather than an excuse invoked after the fact.

### 4. What breaks if this changes
- **The agent types the password into Neo4j Browser "just this once, it's a local database":** the
  precedent set is that the boundary is negotiable based on the agent's own risk assessment of a
  specific case — the exact reasoning that fails the moment the case ISN'T actually low-risk (a wrong
  URL, a look-alike page, a database that turns out not to be as "local" as assumed).
- **The agent refuses to open Neo4j Browser at all, out of excess caution:** the genuinely useful,
  zero-risk part of the plan (opening the page, running read-only queries, showing the human a place
  to explore) gets thrown away along with the one part that needed a boundary.

### [Interview relevance: extremely high right now — "how do you scope what an autonomous agent is allowed to do with credentials" is one of the most actively discussed AI-engineering topics in 2025-2026, directly relevant to anyone building agentic systems]

**Interview question:** *"You're building an agent that can browse the web and also read your app's
config files. How do you prevent it from ever using a real credential in a way you didn't intend?"*
**A strong answer covers:** drawing the boundary at the ACTION, not the VALUE — the same secret is
fine flowing through code a human wrote and scoped in advance, and risky the moment the agent itself
decides, in the moment, to type it somewhere; preferring narrow, purpose-built integrations (a
database driver, an API client) over general-purpose UI automation for anything credential-bearing;
and treating "the destination is trusted" as insufficient justification on its own, because the
agent's own judgment about trust is exactly the thing that could be wrong or manipulated.

### Canonical docs
- [OWASP: Credential Stuffing / Secrets Management cheatsheets](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- Anthropic, ["Computer use" safety guidance](https://docs.anthropic.com/en/docs/agents-and-tools/computer-use) —
  the general framing of what an autonomous agent should and shouldn't be trusted to do with a UI.
- [NIST SP 800-53, AC-6 (Least Privilege)](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final) —
  the formal security principle underneath this specific case.

### Micro-exercises
1. Run `d3_credential_boundary.py`. List every other place in this project's code where a credential
   (Neo4j, Qdrant, Ollama) is read from `.env`/`settings` — is the PATTERN always "Path A" (config into
   trusted application code), never "Path B"?
2. Describe a THIRD path, different from both A and B: an agent generating a one-time API token itself
   and using it programmatically, without ever seeing the human's actual master password. Where would
   you draw the line for that path?
3. Find one real product or tool (a password manager, an OAuth flow) that is specifically DESIGNED to
   let an agent complete a login WITHOUT ever seeing the raw password. How does it achieve that?
4. Argue the OTHER side for one paragraph: what's the strongest case FOR an agent being allowed to type
   a password into a form it's confident is legitimate? What specific new risk does allowing that
   introduce, even under the "confident" condition?
5. Write your own one-sentence rule (not copied from this document) for when an agent should and
   shouldn't be allowed to move a secret from a config file into a UI action.

**How it works internally.** There's no library here either — like C4, this is a property of WHERE a
line is drawn in a decision-making process, not a mechanism. The one piece of "internals" worth
knowing: this project's actual safety rules are enforced as instructions the agent follows, not as a
technical sandboxing mechanism that would make the action impossible — the boundary held because it
was followed, which is itself worth understanding as a limitation of instruction-following safety.

### How to validate this yourself
Grep this project's `src/` for every place `NEO4J_PASSWORD`/`settings.neo4j_password` is used
(`grep -rn "neo4j_password" src/`) — every single result should be inside a `build_driver`-style
function, never inside anything that drives a browser.

### How to visualize this
Draw the password as a single dot with two arrows leaving it: one arrow goes to a box labelled
"`build_driver()` — human-scoped, narrow, always the same action," the other goes to a box labelled
"typing into a form — general-purpose, agent-decided, refused." Two arrows, one dot, very different
boxes.

**You should now be able to explain:** why the SAME secret value can be safe in one code path and
refused in another, and why "it's just my own local database" doesn't change which path is which.

## Consolidated end-to-end exercise: watch the whole thing work

1. **Read a document you haven't seen, cold.** Pick any `data/employees/*.md` or `data/contracts/*.md`
   file this deep dive hasn't already used. Read it and write down, on paper, every fact you'd expect
   the graph to contain about it — BEFORE running anything (C1).
2. **Decide your sample honestly.** If you were only going to check 6 things total across the WHOLE
   corpus (including the one you just picked), would you spend one of them on this document, or on a
   category you haven't touched yet? Justify it in one sentence (C2).
3. **Write the Cypher yourself.** Based on what you wrote down in step 1, write a `MATCH` query — not
   copied from the report — that would confirm or deny it. Run it against the real, live Neo4j.
4. **If anything doesn't match, classify it before reacting.** Run it through the three questions from
   `d2_classify_a_discrepancy.py`: does it match what the pipeline's OWN rules say it should do? Is it
   already a documented limitation? If not, is the SOURCE itself possibly the inconsistent side (C4)?
5. **If your check needs Neo4j Browser, open it and log in yourself.** Notice, the next time an
   assistant (this one or otherwise) offers to "just" open a database tool for you, whether it types
   your credentials in or hands you the login step (C5).

If you can walk through all five steps on a document you chose yourself, with nobody telling you which
Cypher to write, you understand P4-M7 — not as a report you read, but as a way of checking any system,
including ones nobody has built the automated tests for yet.
