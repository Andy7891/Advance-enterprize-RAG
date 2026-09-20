# 18 — Graph Model and Schema Constraints: Deep Dive (P4-M2)

> Every output block in this document was produced on 2026-09-20 by running the code against the live
> Neo4j **5.26.0 Community** container (`advancerag-neo4j-1`) and the real corpus: `data/` (76 documents) and
> `data/chunks/` (223 chunks). Demos that write to the graph use a unique throwaway label, and each prints a
> `cleanup` line; after all eleven were run, the real graph still held exactly the 8 real constraints and 0 nodes.
> Nothing prints the password.
>
> For every concept you get:
> 1. a **definition** (what, key terms, how it fits, limits, analogy) and a **14-year-old** version;
> 2. a **minimal demo**, in which **every line has a comment saying what it does**, with its real output;
> 3. the **exact project code** built in P4-M2, as an **annotated copy**. Each line keeps its real line number,
>    and a `#▸` comment above it says what that line does. (The files themselves keep their shorter WHY-only
>    comments, following the project's code rule; the line-by-line annotation lives here.) These blocks were
>    generated from the source files themselves, and each annotated line was checked against the file, so the
>    code shown is the code that runs.
>
> Demos are in [`practice codes/phase 4/m2_graph_schema_deep_dive/`](../practice%20codes/phase%204/m2_graph_schema_deep_dive/). Run from the project root:
> ```bash
> .venv/Scripts/python "practice codes/phase 4/m2_graph_schema_deep_dive/c1_audit_the_corpus.py"
> ```
> Neo4j must be running for C3–C11 (`docker compose up -d neo4j`); C1 and C2 only read `data/`. C4 also reads
> `data/chunks/` (produced by `python -m src.ingestion.pipeline`). Qdrant is not needed by any demo.
> One line in C4's demo (the opening `f"""`) has its comment on the line *above* it, because a `#` written after
> the quotes would be part of the string.

---

## 0. Plain-language summary

P4-M1 delivered an empty, authenticated, verified Neo4j. A database that will store **anything** needs a plan for
**what** to store, and rules that stop it drifting. P4-M2 is that plan, written as code, and it was made **after
measuring the documents**, not before:

```
data/ (76 documents)                                   src/graph/schema.py
 32 contracts   ── audit (C1) ──►  the model  ──────►  NODE_TYPES         8 labels, each with an identity key     C1 C2 C5
 32 HR records                                          RELATIONSHIP_TYPES 10 types, with allowed endpoints          C1 C4
  8 products                                            │
  4 company pages                                       ├─ constraint_statement()  one IF NOT EXISTS DDL per label   C3 C6
                                                        ├─ apply_schema()          create, wait for indexes, verify  C7
Neo4j Community can enforce:  uniqueness                ├─ check_schema()          compare by DEFINITION (read-only) C8
Neo4j Community cannot:       NOT NULL, node key        └─ main()                  python -m src.graph.schema [--check]  C10
   → the loader must MERGE with complete keys   C9
tests/graph/test_schema.py: the same scenarios on a fake AND the real server; a sweep; a doc guard; mutants   C11
```

Nothing is loaded yet: the graph has **8 constraints and 0 nodes**. This step proves the rules *enforce*; loading and
querying come next.

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | From a corpus to a graph model: audit first, then a closed schema | 🔴 **Core RAG** (what GraphRAG quality rests on) |
| C2 | Identity and keys: what makes two mentions the same node | 🔴 **Core RAG** |
| C3 | Uniqueness constraints and `MERGE`: idempotent loads | 🔴 **Core RAG** |
| C4 | Provenance: the graph stores IDs, the text stays in Qdrant | 🔴 **Core RAG** (how graph and vector results are combined) |
| C5 | Entity resolution: why people are `Employee` and `Contact` | 🔴 **Core RAG** |
| C6 | DDL cannot take labels as parameters: identifier validation | 🟡 Engineering (security-minded) |
| C7 | `apply_schema`: idempotent creation, counters, the silent no-op | 🟡 Engineering (interview-heavy) |
| C8 | `check_schema`: verify by definition | 🟡 Engineering |
| C9 | What Community Edition cannot enforce, so the loader must | 🟡 Engineering |
| C10 | The CLI: exit codes, `--check`, reusing the readiness check | ⚪ Boilerplate |
| C11 | Testing: one scenario on a fake and the real server, a sweep, a doc guard, mutants | 🟡 Engineering |

> **A note on the open question.** P4-M1 ended with: "is a unique-label-per-test-run strategy safe if a test run
> crashes before its finally block?" It is not, on its own. C11 shows the fix that was built: sweep leftovers by
> **name prefix** at the start of every run, so a crashed run is repaired by the next one.

---


## C1 — From a corpus to a graph model: audit first, then a closed schema

### 1. Definition + analogy

**What it is.** A **graph model** is the set of decisions that say what a graph will contain:

| Decision | The question it answers | In this project |
|---|---|---|
| **Node labels** | what *kinds* of thing exist? | 8: `Document`, `Chunk`, `Company`, `Product`, `Contract`, `Employee`, `Contact`, `Location` |
| **Relationship types** and their **endpoints** | how may things connect, and from what to what? | 10, e.g. `WITH_CLIENT` goes only from `Contract` to `Company` |
| **Identity keys** | which property makes two mentions the *same* node? | one per label (C2) |
| **Layers** | which labels describe the world, and which describe where facts came from? | *provenance* (`Document`, `Chunk`) and *domain* (the other six) |

Neo4j is **schema-optional**: it stores any label you invent, on any node, with any properties. That freedom is exactly
why the model has to be a deliberate, written-down, enforced artefact. Nothing else will stop `Person`, `person` and
`Persons` from all existing next week.

**Closed vs open models.** There are two ways to get a model:
- **Open:** let an LLM read each chunk and invent entity types as it goes. You get whatever it decides today.
- **Closed:** decide the types up front, from the data, and make the extractor fit them.

A uniqueness constraint (C3) can only guard a label that is fixed in advance, so this project chose **closed**. That was
possible because the corpus is small (76 documents) and templated, and it was made *evidence-based* by **auditing first**:
counting what the documents really contain (demo below). Tools that extract with an LLM usually accept an allow-list of
node and relationship types for the same reason (for example LangChain's `LLMGraphTransformer` takes `allowed_nodes` and
`allowed_relationships`).

**The registry.** The model lives in `src/graph/schema.py` as **data**: `NODE_TYPES` and `RELATIONSHIP_TYPES`, tuples of
frozen dataclasses. The constraint DDL (C3, C6), the verification (C8), the documentation tables in
`docs/architecture.md` and the tests (C11) are all *derived* from it, so there is one source of truth.

**Key terms.** *Label*: a node's type. *Relationship type*: the verb on an arrow. *Endpoint*: the label at either end of
a relationship. *Layer*: provenance (where text came from) vs domain (what it talks about). *Closed model*: types fixed in
advance. *Registry*: the model held as data.

**Limits.** A model written in Python is a *convention* the database does not know about. Neo4j Community cannot enforce
relationship endpoints (C9), so `RELATIONSHIP_TYPES` is a reference the loader must follow, not a database rule.

**Analogy.** A library decides its catalogue cards before cataloguing: author, title, subject, and what makes two cards
"the same book". A cataloguer who invents new card types as they go produces a catalogue nobody can search.

### For a 14-year-old

Imagine you have a giant box of mixed LEGO and you want to sort it. You could dump it on the floor and make up piles as you
go ("hmm, these look like wheels... these are sort of long..."), and you'd end up with a mess: three different piles that
all contain wheels.

Or you could **first look at what's actually in the box**, count it ("32 flat blue ones, 8 wheels, 11 with weird
stickers"), and then **decide your bins**: "wheels", "flat pieces", "weird ones". Every piece goes in exactly one bin, and
you know in advance what bins exist.

We looked in the box (76 documents), counted, and made the bins. The computer has to follow them.

### 2. Why (vs the traditional approach)
A relational database makes you write `CREATE TABLE` before you insert anything, so the *database* refuses the wrong
shape. A graph database doesn't, so unless you write the model down and check it, the shape is decided by whichever
extraction run happened last. The usual "extract first, look at the result afterwards" workflow discovers the schema by
accident; here the schema was measured first, and the audit also found things a generic "insurance company graph" would
have got wrong (C2, C5).

### 3a. Minimal demo — audit the corpus, then print the model it led to
Read-only: touches `data/` and the registry, never the database.

`c1_audit_the_corpus.py`
```python
# C1: audit the corpus BEFORE choosing a graph model. Read-only: touches data/ and the model, never the database.
import re                                                                # regular expressions, to pull fields out of Markdown text
from collections import Counter                                          # a dict that counts how often each value appears
from pathlib import Path                                                 # object-style file paths

from src.graph.schema import NODE_TYPES, RELATIONSHIP_TYPES             # the model this audit led to (built in P4-M2)

data = Path("data")                                                      # the corpus folder, relative to the project root
docs = {                                                                 # dictionary: relative path -> full text of that file
    p.relative_to(data).as_posix(): p.read_text(encoding="utf-8")        # key like "contracts/x.md", value = the file's text
    for p in sorted(data.rglob("*.md"))                                  # every Markdown file under data/, in a stable order
    if ".ipynb_checkpoints" not in p.parts                               # skip Jupyter's autosave copies (they double-count)
}                                                                        # end of the dictionary
print("1. documents per category      :", dict(Counter(path.split("/")[0] for path in docs)))  # count files per top-level folder

contracts = {p: t for p, t in docs.items() if p.startswith("contracts/")}   # only the contract documents
with_number = [p for p, t in contracts.items() if re.search(r"Contract (Number|ID)\b", t)]  # contracts that name a number or an ID
print(f"2. contracts with a number     : {len(with_number)} of {len(contracts)}  (so a number cannot be a contract's identity)")  # how many lack one

placeholder = re.compile(r"\[(?:Insert )?(?:Start |Effective )?Date\]|\[Name\]")  # unfilled template fields such as [Date] or [Name]
print("3. with an unfilled [field]    :", sum(1 for t in contracts.values() if placeholder.search(t)), "contracts")  # count contracts containing one

locations = Counter(                                                     # count each distinct raw location string
    m[1].strip()                                                         # the text after "Location:", trimmed
    for p, t in docs.items() if p.startswith("employees/")               # only the HR records
    if (m := re.search(r"\*\*Location:?\*\*:?\s*(.+)", t))               # the Location line, with "**Location:**" or "**Location**:"
)                                                                        # end of the counter
print(f"4. employee location strings   : {len(locations)} different, e.g.", list(locations)[:3])  # spellings, not cities

roster = {Path(p).stem for p in docs if p.startswith("employees/")}      # the 32 employee names (file names without .md)
block = re.compile(                                                      # a signature block is three bold lines:
    r"\*\*(?P<name>[^*\n]+?)\*\*[ \t]*\n"                                # the signer's name
    r"\*\*Title\*\*:?[ \t]*(?P<title>[^\n]+?)[ \t]*\n"                   # their title
    r"\*\*(?P<org>[^*\n]+?)\*\*"                                         # the organisation they sign for
)                                                                        # end of the pattern
signers = [m.groupdict() for t in contracts.values() for m in block.finditer(t)]  # every signature block in every contract
ours = [s for s in signers if s["org"].lower().startswith("insurellm")]  # the blocks signed for Insurellm
on_roster = sum(s["name"] in roster for s in ours)                       # how many of those names are real employees
print(f"5. Insurellm-side signers      : {len(ours)} blocks, {on_roster} of them on the HR roster")  # the surprise: none
ceos = Counter(s["name"] for s in ours if "Chief Executive" in s["title"])  # who signs as Insurellm's CEO, and how often
hr_title = re.search(r"\*\*Job Title:?\*\*:?\s*(.+)", docs["employees/Avery Lancaster.md"])[1]  # what Avery's HR record says
print("6. signs as Insurellm CEO      :", dict(ceos), "| HR record says:", hr_title.strip())  # the corpus contradicts itself
clients = [s for s in signers if s not in ours]                          # the blocks signed for the client companies
print("7. client signers named like an employee:", [(s["name"], s["org"]) for s in clients if s["name"] in roster])  # namesakes

print("8. the model this led to:")                                       # now show the result of P4-M2
for n in NODE_TYPES:                                                     # every node type in the registry
    print(f"   {n.layer:<10} {n.label:<9} identified by {n.key}")        # its layer, label and identity key
for r in RELATIONSHIP_TYPES:                                             # every relationship type in the registry
    print(f"   {r.name:<12} {', '.join(r.sources)} -> {', '.join(r.targets)}")  # its allowed start and end labels
```
Real output:
```
1. documents per category      : {'company': 4, 'contracts': 32, 'employees': 32, 'products': 8}
2. contracts with a number     : 19 of 32  (so a number cannot be a contract's identity)
3. with an unfilled [field]    : 7 contracts
4. employee location strings   : 11 different, e.g. ['San Francisco, California', 'Denver, Colorado', 'Austin, Texas']
5. Insurellm-side signers      : 17 blocks, 0 of them on the HR roster
6. signs as Insurellm CEO      : {'Jennifer Rodriguez': 7} | HR record says: Co-Founder & Chief Executive Officer (CEO)
7. client signers named like an employee: [('Robert Chen', 'Fortress Business Underwriters'), ('Marcus Johnson', 'Rapid Claims Associates, LLC')]
8. the model this led to:
   provenance Document  identified by ('doc_id',)
   provenance Chunk     identified by ('chunk_id',)
   domain     Company   identified by ('name',)
   domain     Product   identified by ('name',)
   domain     Contract  identified by ('doc_id',)
   domain     Employee  identified by ('name',)
   domain     Contact   identified by ('name', 'organization')
   domain     Location  identified by ('name',)
   HAS_CHUNK    Document -> Chunk
   MENTIONS     Chunk -> Company, Product, Contract, Employee, Contact, Location
   DESCRIBED_IN Product, Contract, Employee -> Document
   FOR_PRODUCT  Contract -> Product
   WITH_CLIENT  Contract -> Company
   SIGNED_BY    Contract -> Contact
   WORKS_ON     Employee -> Product
   BASED_IN     Employee -> Location
   HAS_OFFICE   Company -> Location
   FOUNDED      Employee -> Company
```

**How to read it.**
- **Lines 1–3:** the documents are enumerable (four categories), but the contracts are messy: only 19 of 32 name a
  number, and 7 contain unfilled `[Date]` / `[Name]` fields. That already rules out "contract number" as an identity.
- **Line 4:** 11 different location strings for what will turn out to be 9 cities (C2).
- **Lines 5–7:** none of the 17 Insurellm-side signatories is on the HR roster, and the contracts name a *different* CEO
  than the HR record does (Jennifer Rodriguez ×7 vs Avery Lancaster). Two client-side signatories share names with
  employees. Those three facts are why people are split into two labels (C5).
- **Line 8:** the model that came out of it: two layers, eight labels, ten relationship types.

### 3b. Exact project code — the registry (annotated copy of `src/graph/schema.py`)
The two record types that describe the model:
```python
#▸ L40: make NodeType an immutable record: once built it cannot be changed, so nothing can alter the model at run time
40  @dataclass(frozen=True)
#▸ L41: one entry of the model: a kind of node
41  class NodeType:
#▸ L42: the node label, e.g. "Company"
42      label: str
#▸ L43-45: a comment: every key part must be present on every node; Community cannot enforce that (C9), so the loader has to
43      # The properties that together identify one node. Every one must be set on every
44      # node: Community Edition cannot enforce that (decision D-2026-09-20-05), so the
45      # loader has to, by MERGEing with complete keys.
#▸ L46: the identity key: the property names that together identify one node (a tuple, so a composite key is just two names)
46      key: tuple[str, ...]
#▸ L47: "provenance" or "domain"; Literal lets mypy reject any other word
47      layer: Literal["provenance", "domain"]
#▸ L48: one sentence saying what the label is for and how its key was chosen
48      meaning: str
...
#▸ L56: make RelationshipType immutable too
56  @dataclass(frozen=True)
#▸ L57: one entry of the model: a kind of relationship
57  class RelationshipType:
#▸ L58: the relationship type in UPPER_SNAKE_CASE, e.g. WITH_CLIENT
58      name: str
#▸ L59: the labels the arrow may START at (a tuple, because DESCRIBED_IN starts at three labels)
59      sources: tuple[str, ...]
#▸ L60: the labels the arrow may END at
60      targets: tuple[str, ...]
#▸ L61: one sentence saying what the relationship says
61      meaning: str
#▸ L62: the properties the arrow itself carries (for example title on SIGNED_BY); empty by default
62      properties: tuple[str, ...] = ()
```
Two real entries, the first provenance and the last the one with the composite key:
```python
#▸ L65: the model's node types, as one immutable tuple
65  NODE_TYPES: tuple[NodeType, ...] = (
#▸ L66: start of the first entry
66      NodeType(
#▸ L67: its label
67          "Document",
#▸ L68: its identity key: a single property
68          ("doc_id",),
#▸ L69: its layer
69          "provenance",
#▸ L70-71: its meaning: the text says what doc_id is
70          "One source file under data/. doc_id is the 16-character SHA-256 of its path, the "
71          "same value the chunk schema carries.",
#▸ L72: end of the entry
72      ),
...
#▸ L108: start of the Contact entry
108     NodeType(
#▸ L109: its label
109         "Contact",
#▸ L110: its identity key: TWO properties together (C2, C5)
110         ("name", "organization"),
#▸ L111: its layer
111         "domain",
#▸ L112-113: its meaning: namesakes exist, so the organisation is part of the identity
112         "A person named in a contract, such as a signatory. Identity is name plus "
113         "organisation, because namesakes exist: a client's 'Robert Chen' is not Insurellm's.",
#▸ L114: end of the entry
114     ),
```
The relationship table starts here; `MENTIONS` is the bridge to the vector store (C4):
```python
#▸ L124: every label in the "domain" layer, computed from NODE_TYPES so it can never disagree with it
124 DOMAIN_LABELS: tuple[str, ...] = tuple(n.label for n in NODE_TYPES if n.layer == "domain")
#▸ L126: the model's relationship types, as one immutable tuple
126 RELATIONSHIP_TYPES: tuple[RelationshipType, ...] = (
#▸ L130: start of the MENTIONS entry
130     RelationshipType(
#▸ L131: its name
131         "MENTIONS",
#▸ L132: it starts at a Chunk
132         ("Chunk",),
#▸ L133: it may end at ANY domain label (a chunk can name a product, a person, a place...)
133         DOMAIN_LABELS,
#▸ L134-135: its meaning: the bridge from a graph hit to a chunk_id
134         "The chunk's text names this entity. The bridge from a graph hit to the vector "
135         "store: follow it to a chunk_id.",
#▸ L136: end of the entry
136     ),
```
And the test that checks the registry is internally consistent (`tests/graph/test_schema.py`):
```python
#▸ L214: test that every relationship connects labels that really exist
214 def test_every_relationship_connects_labels_that_exist():
#▸ L215: the set of every label in the model
215     labels = {n.label for n in NODE_TYPES}
#▸ L216: check each relationship type in turn
216     for rel in RELATIONSHIP_TYPES:
#▸ L217: it must have at least one start label and one end label (rel.name is the failure message)
217         assert rel.sources and rel.targets, rel.name
#▸ L218: every start label must be a defined label (subset test), else "starts at an undefined label"
218         assert set(rel.sources) <= labels, f"{rel.name} starts at an undefined label"
#▸ L219: every end label must be a defined label, else "ends at an undefined label"
219         assert set(rel.targets) <= labels, f"{rel.name} ends at an undefined label"
#▸ L220: the name must be UPPER_SNAKE_CASE (letters and single underscores), Neo4j's convention for relationship types
220         assert re.fullmatch(r"[A-Z]+(_[A-Z]+)*", rel.name), f"{rel.name} is not UPPER_SNAKE"
```
**How it fits.** Nothing here touches the database. It is a **description** that the rest of the module turns into rules.

### 4. Benefits
The model is reviewable in one file, derived artefacts cannot disagree with it, and it was chosen from evidence.

- **What breaks:** an open model (labels multiply and constraints can't guard them); keying on a property that is not
  always present; a model held only in someone's head or in a wiki that nobody compares with the code.
- **Docs:** [Neo4j data modelling](https://neo4j.com/docs/getting-started/data-modeling/) ·
  [Cypher manual: labels and types](https://neo4j.com/docs/cypher-manual/current/) ·
  the LangChain documentation for `LLMGraphTransformer` (its `allowed_nodes` and `allowed_relationships` options)

### Micro-exercises
1. In the C1 demo, add a count of employees whose location starts with `Remote`. Does it match C2's number?
2. Which of the four `data/` categories has the *least* uniform template? Find a number that shows it.
3. Add a field to `NodeType` (say `description`), build one entry, and run `pytest tests/graph -q`. What breaks, if anything?
4. Argue for or against adding a `Department` label. What would you have to *measure* first?
5. Draw the ten relationship types on paper as arrows between the eight labels.

**Internals.** Neo4j keeps each label, relationship type and property key as an integer **token** in a store, and each node
holds the token ids of its labels. That is why labels are cheap and why a *misspelt* label silently becomes a new, empty
one (the server only prints a notification, as the `label does not exist` warning you may have seen). Nothing in the
database will tell you the label was a typo.
**Checkpoint:** name the four decisions a graph model records, and say why a *closed* model is what makes a constraint useful.

---


## C2 — Identity and keys: what makes two mentions the same node

### 1. Definition + analogy

**What it is.** An **identity key** is the property (or properties) whose values decide that two mentions are **the same
node**. Every load asks the same question: "I have just read *Carllm*. Is that a node I already have, or a new one?" The
key is the answer.

| Term | Meaning | Example here |
|---|---|---|
| **Natural key** | a value that already exists in the data | a product's name `Carllm` |
| **Surrogate key** | a value you invent (auto-number, random UUID) | *not used*: a random id makes every re-run create new nodes |
| **Deterministic key** | computed from the source by a fixed rule, so the same input always gives the same key | `doc_id` = first 16 hex characters of SHA-256 of the file path |
| **Composite key** | several properties together | `Contact`: `(name, organization)` |
| **Normalisation** | making equivalent spellings identical *before* matching | `San Francisco, CA` → `San Francisco, California` |

**How the keys were chosen** (each from what is *always present*, checked in C1's and this concept's demos):

| Label | Key | Why, from the corpus |
|---|---|---|
| `Document`, `Chunk` | `doc_id`, `chunk_id` | already in the chunk schema; deterministic; shared with Qdrant |
| `Contract` | `doc_id` of its file | **13 of 32** contracts have no number, and there are 8 header layouts; the file always exists |
| `Company` | `name` (short) | the contract *title* names the client in every one of the 32; the legal name differs in 11 of 12 contracts that give one |
| `Product` | `name` | a **closed list of 8** (the file names in `data/products/`); 2 contract titles append a tagline |
| `Employee` | `name` | 32 distinct names on a closed roster |
| `Location` | `name` as `City, State` | after normalising, 9 cities (from 11 spellings) |
| `Contact` | `(name, organization)` | namesakes exist (C5) |

**A constraint does not clean dirty input.** If the loader matched `San Francisco, CA` and `San Francisco, California`
without normalising, the constraint would happily keep *both*, because as strings they are different. The order is:
**normalise, then `MERGE`.**

**Key terms.** *Idempotent*: running the operation twice leaves the same result as once. *Path-derived vs
content-derived*: `doc_id` hashes the **path** (so editing a file keeps its identity; the P2 schema explains why).

**Limits.** A natural key is only as good as the data: two employees who ever share a name would collide (the constraint
would raise at load time, which is the signal to add a disambiguator). Normalisation rules (like the state abbreviation
table in the demo) belong to extraction and are only illustrated here.

**Analogy.** Passport numbers identify people better than names, because two people can share a name. But if a third of
your people have no passport, you can't use it as *the* ID: you use the registry file each person is kept in.

### For a 14-year-old

At school, "Sam" isn't enough to find someone, because there might be three Sams. So the school uses **student numbers**.
But suppose a third of the students never got a number. Then student numbers can't be the thing you rely on. You'd use
something **every** student has, like "which class register are they written in".

Also, if one teacher writes "Year 9" and another writes "Yr 9" and another "9th", a computer thinks those are three
different classes. Someone has to **tidy the spelling first**. That's *normalising*. Only after tidying can the
computer see they're the same.

### 2. Why (vs the traditional approach)
A relational table has a `PRIMARY KEY` that is unique **and** not null, so the database refuses a row with no key. Its
usual auto-increment ids make re-running a load *append* rows (a second "Carllm" with id 2). Neo4j Community can enforce
"unique" but not "present" (C9), and a random id would make re-runs duplicate. So this project uses **deterministic natural
keys** and makes the loader responsible for presence.

### 3a. Minimal demo — deterministic ids, missing numbers, two names for one company, taglines, and 11 spellings for 9 cities
Reads `data/` only; no database.

`c2_identity_keys.py`
```python
# C2: identity -- what makes two mentions "the same thing". Reads data/ only; no database needed.
import re                                                                # regular expressions
from collections import Counter                                          # counts how often each value appears
from pathlib import Path                                                 # object-style file paths

from src.ingestion.schemas import compute_doc_id                         # the project's deterministic document id

data = Path("data")                                                      # the corpus folder
path = "contracts/Contract with DriveSmart Insurance for Carllm.md"      # one contract, as a path relative to data/
print("1a. doc_id of that path        :", compute_doc_id(path))          # 16 hex characters, derived from the path only
print("1b. computed again             :", compute_doc_id(path) == compute_doc_id(path))  # same input -> same id, so a re-run finds the same node
print("1c. a different path           :", compute_doc_id("contracts/Contract with Apex Reinsurance for Rellm.md"))  # another file -> another id

contracts = {p.name: p.read_text(encoding="utf-8") for p in sorted((data / "contracts").glob("*.md"))}  # file name -> text
numbers = {name: re.search(r"Contract (?:Number|ID):?\*{0,2}:?\s*([A-Z0-9][A-Z0-9-]+)", text) for name, text in contracts.items()}  # number if present
missing = [n for n, m in numbers.items() if m is None]                   # contracts with no number at all
print(f"2.  contracts without a number : {len(missing)} of {len(contracts)}, e.g.", missing[0])  # the reason a number cannot be the key

print("3.  short name vs legal name (Company key vs property):")         # the title names the client one way, the body another
for name, text in list(contracts.items()):                               # walk the contracts
    title = re.search(r"^# Contract with (.+?) for ", text, re.M)        # the client's short name from the H1 title
    parties = re.search(r"\*\*Parties:?\*\*:?\s*\n((?:[ \t]*-[ \t]*.+\n?)+)", text)  # the bullet list under "Parties:"
    if not (title and parties):                                          # skip contracts without a Parties block
        continue                                                         # (only 12 have one)
    legal = [x.strip("- \t") for x in parties[1].splitlines() if x.strip() and "Insurellm" not in x]  # the client's line
    print(f"    title {title[1]!r:<30} legal {legal[0]!r}")              # show both spellings side by side
    if title[1] == "DriveSmart Insurance":                               # stop after a few examples
        break                                                            # (DriveSmart is the second one printed)

products = sorted(p.stem for p in (data / "products").glob("*.md"))      # the closed list of 8 product names, from file names
resolved = Counter()                                                     # how many contracts resolve to each product
for text in contracts.values():                                          # every contract
    tail = re.search(r"^# Contract with .+? for (.+?)\s*$", text, re.M)[1]  # everything after "for" in the title
    resolved[next(p for p in products if tail.startswith(p))] += 1       # match by the known name, ignoring any tagline
tagged = [re.search(r"for (.+?)\s*$", re.search(r"^# .*$", t, re.M)[0])[1] for t in contracts.values()]  # the raw title tails
print("4.  a title that adds a tagline:", [x for x in tagged if ":" in x or " - " in x][:1])  # why we do not just split the title
print("    resolved against the 8 names:", dict(resolved))               # every contract lands on exactly one product

STATES = {"CA": "California"}                                            # state abbreviations seen in the corpus (only one)


def normalise_location(raw: str) -> tuple[str, bool]:                    # turn a raw HR string into ("City, State", is_remote)
    remote = raw.startswith("Remote")                                    # "Remote (Based in Phoenix, Arizona)" is a remote employee
    inner = re.search(r"\((?:Based in )?([^)]+)\)", raw) if remote else None  # a remote employee's base city is inside brackets
    place = inner[1] if inner else raw                                   # use the bracket text for remote staff, else the whole string
    city, _, state = (part.strip() for part in place.partition(","))     # split "City, State" at the first comma
    return f"{city}, {STATES.get(state, state)}", remote                 # expand an abbreviation, keep anything else


raw = [re.search(r"\*\*Location:?\*\*:?\s*(.+)", p.read_text(encoding="utf-8"))[1].strip()  # each employee's raw location string
       for p in (data / "employees").glob("*.md")]                       # over all the HR records
clean = [normalise_location(x) for x in raw]                             # normalise every one
print(f"5.  raw location strings       : {len(set(raw))}")               # how many spellings there are
print(f"    after normalising          : {len({c for c, _ in clean})} cities, {sum(r for _, r in clean)} remote employees")  # how many places there really are
print("    example                    :", normalise_location("San Francisco, CA"), normalise_location("Remote (Based in Phoenix, Arizona)"))  # both forms
```
Real output:
```
1a. doc_id of that path        : e1d50d989d7d0276
1b. computed again             : True
1c. a different path           : 4a96b15d84b0cec1
2.  contracts without a number : 13 of 32, e.g. Contract with Apex Reinsurance for Rellm - AI-Powered Enterprise Reinsurance Solution.md
3.  short name vs legal name (Company key vs property):
    title 'Continental Commercial Group' legal 'Continental Commercial Group, Inc.'
    title 'DriveSmart Insurance'         legal 'DriveSmart Insurance Corp.'
4.  a title that adds a tagline: ['Rellm: AI-Powered Enterprise Reinsurance Solution']
    resolved against the 8 names: {'Healthllm': 4, 'Rellm': 4, 'Bizllm': 4, 'Markellm': 4, 'Carllm': 4, 'Lifellm': 4, 'Claimllm': 4, 'Homellm': 4}
5.  raw location strings       : 11
    after normalising          : 9 cities, 5 remote employees
    example                    : ('San Francisco, California', False) ('Phoenix, Arizona', True)
```

**How to read it.**
- **1a–1c:** the same path always gives the same `doc_id`, and a different path gives a different one. That is what lets a
  re-run `MERGE` onto the node it made last time.
- **2:** 13 of 32 contracts have no number at all, which is why a contract is keyed on its file, not its number.
- **3:** the title says `DriveSmart Insurance`, the Parties block says `DriveSmart Insurance Corp.`; two spellings of one
  company. The short title name is the key; the legal name becomes a property.
- **4:** a title can carry a tagline (`Rellm: AI-Powered Enterprise Reinsurance Solution`), so the product is *resolved
  against the closed list of 8* rather than cut out of the title. Every contract lands on exactly one product (4 each).
- **5:** 11 raw strings become 9 cities once `CA` is expanded and the five `Remote (Based in ...)` values are unwrapped.
  (`normalise_location` is an **illustration** written in the demo; the real normaliser arrives with extraction.)

### 3b. Exact project code — keys in the model (annotated copy of `src/graph/schema.py`)
How a key becomes a constraint name (the property names are joined into it, so the name says what it protects):
```python
#▸ L46: the identity key as a tuple of property names: one name for most labels, two for Contact
46      key: tuple[str, ...]
#▸ L50: expose constraint_name as an attribute
50      @property
#▸ L51: build the constraint's name from the model entry
51      def constraint_name(self) -> str:
#▸ L52: turn the PascalCase label into snake_case ("JobTitle" -> "job_title"); the lookbehind stops a leading underscore
52          snake = re.sub(r"(?<!^)(?=[A-Z])", "_", self.label).lower()
#▸ L53: label + every key property + "unique": Contact -> contact_name_organization_unique
53          return f"{snake}_{'_'.join(self.key)}_unique"
```
Four real entries, chosen for their keys:
```python
#▸ L80: start of Company
80      NodeType(
#▸ L81: label
81          "Company",
#▸ L82: key: the short name from the contract title
82          ("name",),
#▸ L83: layer
83          "domain",
#▸ L84-85: meaning: the legal name is a property, not the key
84          "Insurellm and its clients. name is the short form used in contract titles; the "
85          "legal name that appears in the contract body is a property.",
#▸ L86: end
86      ),
...
#▸ L94: start of Contract
94      NodeType(
#▸ L95: label
95          "Contract",
#▸ L96: key: the doc_id of the contract's own file, because a contract number is missing from 13 of 32
96          ("doc_id",),
#▸ L97: layer
97          "domain",
#▸ L98-99: meaning: says why a number cannot be the identity
98          "One contract, identified by its source document: contract numbers are missing "
99          "from many contracts, so a number cannot be the identity.",
#▸ L100: end
100     ),
...
#▸ L115: start of Location
115     NodeType(
#▸ L116: label
116         "Location",
#▸ L117: key: the normalised "City, State"
117         ("name",),
#▸ L118: layer
118         "domain",
#▸ L119-120: meaning: records that the corpus writes San Francisco both ways
119         "A city, normalised to 'City, State' with the full state name, because the "
120         "corpus writes San Francisco both ways.",
#▸ L121: end
121     ),
```
The deterministic id that `Document` and `Contract` share (annotated copy of `src/ingestion/schemas.py`, from P2):
```python
#▸ L117: derive a document's id from its path alone
117 def compute_doc_id(source_path: str) -> str:
#▸ L130: unify Windows and POSIX separators and strip leading/trailing slashes, so the same file gives the same id on any machine
130     normalized = source_path.replace("\\", "/").strip("/")
#▸ L131: SHA-256 of the UTF-8 path, keep the first 16 hex characters
131     return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
```
And the test that pins the naming (`tests/graph/test_schema.py`):
```python
#▸ L206: the constraint name is what SHOW CONSTRAINTS and error messages show, so it is part of the interface
206 def test_constraint_names_are_stable_and_readable():
#▸ L208: map every label in the real model to its constraint name
208     by_label = {n.label: n.constraint_name for n in NODE_TYPES}
#▸ L209: a single-property key gives document_doc_id_unique
209     assert by_label["Document"] == "document_doc_id_unique"
#▸ L210: a composite key lists every property: contact_name_organization_unique
210     assert by_label["Contact"] == "contact_name_organization_unique"
#▸ L211: a multi-word label is converted to snake_case
211     assert NodeType("JobTitle", ("name",), "domain", "").constraint_name == "job_title_name_unique"
```
**How it fits.** The keys live in `NODE_TYPES`; C3 turns each into a database rule; C5 explains the `Contact` key.

### 4. Benefits
Re-running any load is safe, keys are explainable from evidence, and the constraint names read like the rule they enforce.

- **What breaks:** keying on a field that is sometimes absent (`contract_number`); skipping normalisation (two `San
  Francisco` nodes); random ids (duplicates on every re-run); fuzzy matching people by name (C5).
- **Docs:** [Neo4j data modelling: identifiers](https://neo4j.com/docs/getting-started/data-modeling/) ·
  [Fellegi & Sunter, "A Theory for Record Linkage" (1969)](https://doi.org/10.1080/01621459.1969.10501049) ·
  [Python `hashlib`](https://docs.python.org/3/library/hashlib.html)

### Micro-exercises
1. In the demo, change one character of `path` and print `compute_doc_id` again. How different is the result?
2. Find a contract whose Parties-block name equals its title name. (The demo says there is exactly one of 12.)
3. Add `"TX": "Texas"` to `STATES`. Would any output change? Why or why not?
4. Which contract titles would break a "split the title on ' for '" rule for the product? (Look at the Rellm ones.)
5. Suggest a key for a `Feature` label taken from a product page's bullet list, and say what could go wrong.

**Internals.** `hashlib.sha256(...).hexdigest()[:16]` keeps 64 of 256 bits. With 76 documents the chance of two
paths colliding is negligible (birthday bound about n²/2⁶⁵), and a collision would surface as a constraint error rather
than silent corruption.
**Checkpoint:** explain why `Contract` is keyed on its document and not on its contract number, and why normalisation must
come *before* `MERGE`.

---


## C3 — Uniqueness constraints and `MERGE`: idempotent loads

### 1. Definition + analogy

**What it is.** A **uniqueness constraint** is a rule stored *in the database*: "no two nodes with label `L` may have the
same value for property `p`" (or the same *combination* of values, for a composite key). The database checks it on every
write and refuses a violation.

**`MERGE`** is Cypher's find-or-create: `MERGE (c:Company {name: 'Acme'})` finds the node if it exists, and creates it if it
doesn't. Together they give **idempotent loads**: run the same load twice and the graph ends up the same.

| Piece | What it does | Cost |
|---|---|---|
| `CREATE` | always makes a new node | none, and no checking: duplicates are your problem |
| `MERGE` | find, else create | needs a fast way to *find*, which is what the constraint's index gives |
| **Uniqueness constraint** | refuses a second node with the same key | an index to maintain on every write |
| **Backing index** | a **range index** the constraint owns automatically | so `MERGE` and lookups on the key are index seeks, not scans |

**Adding a constraint over existing duplicates fails.** The server checks the rule against the data first and refuses to
create a rule the data already violates. You must clean the data, then add the rule.

**Concurrency.** Without a constraint, two transactions running the same `MERGE` at the same moment can each find
nothing and each create the node. The constraint turns the second into a failure or a wait. (The Cypher manual discusses
this under `MERGE` and uniqueness constraints. The demo below shows the **single-writer** behaviour; it does not try to
reproduce the race.)

**Key terms.** *Constraint*: a database-enforced rule. *Idempotent*. *Backing index*. *Index seek* (a direct lookup) vs
*scan* (reading everything).

**Limits.** A uniqueness constraint catches a **duplicate key**, not a **missing** one (C9). It also does nothing about
*different spellings* of one thing (C2).

**Analogy.** A membership desk with a rule: "one card per member number". Asking "give me member 42's card" (`MERGE`) either
hands over the existing card or issues one. The rule (the constraint) means a second card for 42 can never be printed even
by accident, and the card index (the backing index) means the desk finds 42 instantly.

### For a 14-year-old

Think of a club where every member must have a **unique nickname**. The club keeps an alphabetical list, so checking
"is this nickname taken?" is quick.

- Someone says "I'm **Acme**". You look at the list. Not there? Write it down. **There already?** Say "you're already in
  the club" and don't write a second one. That "look first, add only if missing" move is **MERGE**.
- The rule "no two people with the same nickname" is the **constraint**. If you tried to add a second Acme by force, the
  club secretary would say **no**.
- If you'd already let two Acmes in, you can't announce the rule yet: "there are already two Acmes!" First fix the list,
  *then* make the rule.

### 2. Why (vs the traditional approach)
In SQL, `INSERT ... ON CONFLICT DO NOTHING` (or `MERGE`) relies on a `UNIQUE` index in exactly the same way. The difference
is that in Neo4j nothing exists until you create it: a graph with no constraint accepts duplicates silently, and the
`MERGE` you wrote to be safe is only *mostly* safe.

### 3a. Minimal demo — duplicates, a refused rule, an enforced rule, `MERGE`, the backing index, an index seek
Live Neo4j, throwaway label, self-cleaning.

`c3_uniqueness_and_merge.py`
```python
# C3: a uniqueness constraint + MERGE make "one node per identity" a database rule. Live Neo4j, self-cleaning.
import uuid                                                              # to make a label no other data can have

from neo4j.exceptions import ConstraintError, Neo4jError                 # duplicate-key error, and the base class of all server errors

from src.graph.schema import NodeType, constraint_statement             # the project's model class and its DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                    # a unique label isolates this demo's nodes
node = NodeType(label, ("name",), "domain", "demo")                      # a model entry for the throwaway label, identified by name


def operators(plan: dict):                                               # walk a PROFILE plan and yield every operator's name
    yield plan["operatorType"]                                           # this operator
    for child in plan.get("children", []):                               # then each of its inputs
        yield from operators(child)                                      # recursively


with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        session.run(f"CREATE (:{label} {{name: 'Acme'}})").consume()     # 1. no constraint yet: a first Acme
        session.run(f"CREATE (:{label} {{name: 'Acme'}})").consume()     #    and a second one, silently accepted
        count = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]  # how many nodes exist now
        print("1. CREATE twice, no constraint :", count, "nodes")        # 2: nothing stopped the duplicate

        try:                                                             # 2. try to add the rule over data that already breaks it
            session.run(constraint_statement(node)).consume()            # the project's own DDL for this node type
        except Neo4jError as exc:                                        # the server refuses to create a rule the data violates
            print("2. constraint over duplicates:", type(exc).__name__, "|", " ".join(str(exc).split())[:120])  # what it says

        session.run(f"MATCH (n:{label}) WITH n LIMIT 1 DETACH DELETE n").consume()  # 3. remove one of the duplicates
        added = session.run(constraint_statement(node)).consume().counters.constraints_added  # now the DDL succeeds; count what it added
        print("3. constraint created          :", added, "-> named", node.constraint_name)  # the name comes from NodeType.constraint_name

        try:                                                             # 4. now a duplicate must be refused
            session.run(f"CREATE (:{label} {{name: 'Acme'}})").consume()  # a second Acme again
        except ConstraintError as exc:                                   # the specific error class for a duplicate key
            print("4. CREATE a duplicate         :", type(exc).__name__, "|", " ".join(str(exc).split())[:110])  # refused

        first = session.run(f"MERGE (n:{label} {{name: 'Zed'}})").consume().counters.nodes_created   # 5. MERGE = find, or create if absent
        second = session.run(f"MERGE (n:{label} {{name: 'Zed'}})").consume().counters.nodes_created  # the same MERGE again
        print("5. MERGE twice, nodes created  :", first, "then", second)  # 1 then 0: re-running a load never duplicates

        index = session.run(                                             # 6. the constraint owns an index
            "SHOW INDEXES YIELD name, type, state, owningConstraint WHERE owningConstraint = $c RETURN type, state",  # look it up by owner
            c=node.constraint_name,                                      # the constraint's name is the parameter
        ).single()                                                       # exactly one index expected
        print("6. backing index               :", index.data())         # a RANGE index, ONLINE

        plan = session.run(f"PROFILE MATCH (n:{label} {{name: 'Zed'}}) RETURN n").consume().profile  # 7. run a lookup and keep the executed plan
        print("7. lookup by key uses          :", [o for o in operators(plan) if "Seek" in o or "Scan" in o])  # an index seek, not a scan
    finally:                                                             # always clean up, even after an error above
        session.run(f"DROP CONSTRAINT {node.constraint_name} IF EXISTS").consume()  # remove the demo's constraint
        removed = session.run(f"MATCH (n:{label}) DETACH DELETE n RETURN count(n) AS c").single()["c"]  # delete the demo's nodes
        print("8. cleanup                     :", removed, "nodes deleted; constraint dropped; label", label, "left nothing behind")  # done
```
Real output:
```
1. CREATE twice, no constraint : 2 nodes
2. constraint over duplicates: DatabaseError | {neo4j_code: Neo.DatabaseError.Schema.ConstraintCreationFailed} {message: Unable to create Constraint( name='democb2e822
3. constraint created          : 1 -> named democb2e8228_name_unique
4. CREATE a duplicate         : ConstraintError | {neo4j_code: Neo.ClientError.Schema.ConstraintValidationFailed} {message: Node(10) already exists with label `
5. MERGE twice, nodes created  : 1 then 0
6. backing index               : {'type': 'RANGE', 'state': 'ONLINE'}
7. lookup by key uses          : ['NodeUniqueIndexSeek@neo4j']
8. cleanup                     : 2 nodes deleted; constraint dropped; label Democb2e8228 left nothing behind
```

**How to read it.**
- **1:** with no rule, two `Acme` nodes exist. Nothing complained.
- **2:** trying to add the rule while both exist is **refused** (`ConstraintCreationFailed`): the server won't create a rule
  the data breaks.
- **3:** after removing one duplicate, `constraint_statement(node)` (the project's DDL generator) creates it; the name
  `demo…_name_unique` comes from `NodeType.constraint_name` (C2).
- **4:** now a second `Acme` is refused with `ConstraintError`, the class the Python driver raises for a duplicate key.
- **5:** `MERGE` created 1 node the first time and 0 the second: **re-running a load never duplicates**.
- **6:** the constraint owns an index: type `RANGE`, state `ONLINE`.
- **7:** a lookup by key runs as `NodeUniqueIndexSeek`, a direct index lookup, not a scan of every node.

### 3b. Exact project code — the DDL generator and its tests (annotated copies)
`src/graph/schema.py`:
```python
#▸ L222: build the DDL that makes one node type's key unique
222 def constraint_statement(node: NodeType) -> str:
#▸ L223: docstring: what the function returns
223     """The idempotent DDL that makes `node.key` unique for `node.label`."""
#▸ L224: for each key property, write "n.<name>", validating the name first (C6)
224     properties = [f"n.{_identifier(p)}" for p in node.key]
#▸ L225: one property is written bare (n.name); several are written as a parenthesised tuple ((n.name, n.organization))
225     target = properties[0] if len(properties) == 1 else f"({', '.join(properties)})"
#▸ L226: start of the statement text
226     return (
#▸ L227: CREATE CONSTRAINT <name> IF NOT EXISTS: a no-op if it already exists (C7 explains what "exists" means)
227         f"CREATE CONSTRAINT {_identifier(node.constraint_name)} IF NOT EXISTS "
#▸ L228: FOR (n:<Label>) REQUIRE <properties> IS UNIQUE: the rule itself
228         f"FOR (n:{_identifier(node.label)}) REQUIRE {target} IS UNIQUE"
#▸ L229: end of the statement text
229     )
```
`tests/graph/test_schema.py`, the exact text for a single and a composite key:
```python
#▸ L256: test that the generated DDL is exactly the idempotent statement
256 def test_constraint_statements_are_idempotent_ddl():
#▸ L257: a one-property key, like Company
257     single = NodeType("Company", ("name",), "domain", "")
#▸ L258: a two-property key, like Contact
258     composite = NodeType("Contact", ("name", "organization"), "domain", "")
#▸ L259: the whole string must match, character for character
259     assert constraint_statement(single) == (
#▸ L260-261: the expected text for a single key: bare n.name
260         "CREATE CONSTRAINT company_name_unique IF NOT EXISTS "
261         "FOR (n:Company) REQUIRE n.name IS UNIQUE"
#▸ L262: end of the first expectation
262     )
#▸ L263: the same check for the composite key
263     assert constraint_statement(composite) == (
#▸ L264-265: the expected text for a composite key: a parenthesised tuple
264         "CREATE CONSTRAINT contact_name_organization_unique IF NOT EXISTS "
265         "FOR (n:Contact) REQUIRE (n.name, n.organization) IS UNIQUE"
#▸ L266: end of the second expectation
266     )
```
And the live test that proves **every one of the 8 real constraints** rejects a duplicate:
```python
#▸ L546: mark it as needing a running service (skipped, with a reason, if Neo4j is down)
546 @pytest.mark.live
#▸ L547: also skip automatically when Neo4j is not reachable
547 @live
#▸ L548: run this test once per node type in the model (8 times), naming each run by its label
548 @pytest.mark.parametrize("node", NODE_TYPES, ids=lambda n: n.label)
#▸ L549: the test; `real_schema` is a fixture that applies the real model first (C11)
549 def test_live_every_real_constraint_rejects_a_duplicate_key(real_schema, node):
#▸ L550: a unique value prefixed "__test_", so cleanup can find exactly these nodes
550     token = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
#▸ L551: one value for every key property (one for most labels, two for Contact)
551     properties = {name: f"{token}_{name}" for name in node.key}
#▸ L552: a CREATE that takes the whole map as a parameter
552     query = f"CREATE (:{node.label} $properties)"
#▸ L554: the first create must succeed
554     _run(real_schema, query, properties=properties)
#▸ L555: the second, identical create must be refused with a duplicate-key error
555     with pytest.raises(ConstraintError):
#▸ L556: try to create the duplicate
556         _run(real_schema, query, properties=properties)
```
**How it fits.** `NODE_TYPES` says what identifies each node; this turns it into rules; C7 applies them safely; the live test
proves the rules really are in force on the real server.

### 4. Benefits
Loads are repeatable, duplicates are impossible rather than unlikely, and key lookups are indexed for free.

- **What breaks:** using `CREATE` in a loader; adding a constraint after loading dirty data (it will refuse); assuming the
  constraint protects against a *missing* key (C9); each constraint slows writes slightly (an index to maintain), which is
  irrelevant at this size.
- **Docs:** [Cypher manual: constraints](https://neo4j.com/docs/cypher-manual/current/constraints/) ·
  [`MERGE`](https://neo4j.com/docs/cypher-manual/current/clauses/merge/) ·
  [Python driver: `ConstraintError`](https://neo4j.com/docs/api/python-driver/current/api.html#neo4j.exceptions.ConstraintError)

### Micro-exercises
1. In the demo, `MERGE` a node with the same key but `SET n.x = 1` the first time and `SET n.x = 2` the second. What is `x`?
2. Print the `SHOW INDEXES` row *before* creating the constraint. Is there an index?
3. Replace `MERGE` with `CREATE` in step 5 and read the error.
4. Create the composite constraint from `Contact`'s `NodeType` and try two nodes that differ only in `organization`.
5. `PROFILE` the same lookup on a label with **no** constraint. Which operator do you see instead of `NodeUniqueIndexSeek`?

**Internals.** A uniqueness constraint is *implemented by* its index: Neo4j stores the constraint and creates a range
index with `owningConstraint` pointing back at it (visible in `SHOW INDEXES`). Dropping the constraint drops the index.
That is why creating a constraint over existing data has to **populate** the index first (C7's `db.awaitIndexes`).
**Checkpoint:** explain why the demo's step 2 fails but step 3 succeeds, and what `MERGE` returns the second time.

---


## C4 — Provenance: the graph stores IDs, the text stays in Qdrant

### 1. Definition + analogy

**What it is.** **Provenance** is *where a fact came from*. GraphRAG needs it because the two stores do different jobs:

| Store | Good at | Holds |
|---|---|---|
| **Qdrant** (vector) | "which passages are *like* this question?" | the chunk **text** and its vector |
| **Neo4j** (graph) | "which entities exist and how are they connected?" | entities, relationships, and **IDs** of the chunks that support them |

To combine their answers you need a key both sides share. That key is **`chunk_id`** (`"{source_path}_{chunk_index}"`,
e.g. `products/Carllm.md_0`). Qdrant stores each chunk under a *point id* that is derived from it by a fixed function
(`point_id(chunk_id)`, a UUIDv5), so the graph never needs to store the Qdrant id: it can always **compute** it.

**Three relationships carry the bridge:**

| Relationship | Reads as | Used for |
|---|---|---|
| `(Document)-[:HAS_CHUNK]->(Chunk)` | the file was split into this chunk | from a document to all its chunks |
| `(Chunk)-[:MENTIONS]->(entity)` | this chunk's text names the entity | "every place that talks about Carllm" |
| `(entity)-[:DESCRIBED_IN]->(Document)` | this is the entity's own page | "the page *about* Carllm" (prefer it) |

**Why `DESCRIBED_IN` as well as `MENTIONS`?** Carllm is named in 18 chunks, across company pages, contracts, HR records and
its own page (demo). Most are passing mentions. `DESCRIBED_IN` singles out the page that is *about* the entity, which is the
path meant for the P3 miss: the dense search for "What does the Carllm product do?" ranked another product first and
returned no chunk of `products/Carllm.md` in the top 10. A graph lookup of the product's own page does not depend on
embedding similarity. *This is intended, and this concept shows the graph side only; the combined retrieval is built later.*

**Key terms.** *Provenance layer* (`Document`, `Chunk`). *Single owner of the text* (Qdrant). *Join key* (`chunk_id`).
*Derivable id* (computed, not stored).

**Limits.** Two stores can disagree: if a chunk is deleted from Qdrant and the graph still lists its `chunk_id`, a lookup
returns a dangling id. Nothing synchronises them yet; the later linking step has to (and P3's ingestion job already owns
"orphans" on the Qdrant side).

**Analogy.** A book has an **index** at the back: "Carllm: pages 12, 40, 41". The index doesn't copy the pages; it points at
them. If the index also contained the text, every edit to a page would have to be made twice.

### For a 14-year-old

Imagine a big encyclopedia with a **sticky-note index** on the wall. The encyclopedia has all the words. The sticky notes
don't copy the words; each just says "Carllm → see page 12, page 40".

If you photocopied the words onto the sticky notes, then whenever someone fixed a typo in the encyclopedia you'd have to
fix every sticky note too, and you'd forget one. So the notes only hold **page numbers**, and you always read the real
page in the encyclopedia.

Also, some pages *mention* Carllm in passing ("we also sell Carllm..."), while **one page is actually about it**. We mark
that special page separately so we can go there first.

### 2. Why (vs the traditional approach)
The simple approach is to copy the chunk text into a `text` property on each graph node. It works until the text changes:
then there are two copies to keep in sync, the graph gets larger, and any embedding-model change means re-doing both. Storing
only IDs keeps Qdrant the single owner and makes a graph result a **list of keys** that can be fetched from Qdrant, or
intersected with a vector result, by `chunk_id`.

### 3a. Minimal demo — the real chunks, a tiny provenance graph, a graph hit, the entity's own page, and the Qdrant ids
Uses the 223 real chunks on disk; live Neo4j, throwaway labels, self-cleaning; Qdrant is not needed (the join key is
*computed*).

`c4_provenance_bridge.py`
```python
# C4: the provenance bridge -- the graph stores IDs, the text stays in Qdrant, and they meet on chunk_id.
# Uses the REAL chunks on disk (data/chunks/, written by the P3 pipeline). Live Neo4j, self-cleaning. Qdrant is not needed.
import json                                                              # to read the chunk files
import re                                                                # to find chunks that name a product
import uuid                                                              # to make labels no other data can have
from collections import Counter                                          # counts how often each value appears
from pathlib import Path                                                 # object-style file paths

from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)
from src.vectorstore.store import point_id                               # chunk_id -> Qdrant point id (a deterministic UUIDv5)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
doc_l, chunk_l, prod_l = f"DemoDoc{suffix}", f"DemoChunk{suffix}", f"DemoProduct{suffix}"  # stand-ins for Document, Chunk and Product

chunks = []                                                              # every chunk the P3 pipeline produced
for f in sorted(Path("data/chunks").glob("*.json")):                     # one JSON file per document, plus report.json
    loaded = json.loads(f.read_text(encoding="utf-8"))                   # parse the file
    if isinstance(loaded, list):                                         # chunk files are lists; report.json is a dict, so skip it
        chunks.extend(loaded)                                            # add this document's chunks
print("1. chunks on disk               :", len(chunks))                  # 223 in this project

mentions = [c for c in chunks if re.search(r"\bCarllm\b", c["text"])]    # the chunks whose TEXT names the product Carllm
print("2. chunks that name Carllm      :", len(mentions), "in", dict(Counter(c["category"] for c in mentions)))  # spread over categories

rows = [                                                                 # what goes into the graph: IDs only, never the text
    {"doc_id": c["doc_id"], "source_path": c["source_path"], "chunk_id": c["chunk_id"], "chunk_index": c["chunk_index"]}  # four small fields
    for c in mentions                                                    # one row per chunk that names Carllm
]                                                                        # end of the list
own_doc = next(c["doc_id"] for c in chunks if c["source_path"] == "products/Carllm.md")  # the doc_id of the product's own page

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        session.run(                                                     # load Document, Chunk, Product and their relationships
            # the Cypher below is a multi-line f-string; the // comments inside it are Cypher comments, not Python
            f"""
            UNWIND $rows AS row                                          // run the block below once for each row
            MERGE (d:{doc_l} {{doc_id: row.doc_id}})                     // find or create the Document by its key
            SET d.source_path = row.source_path                          // remember where the file lives
            MERGE (c:{chunk_l} {{chunk_id: row.chunk_id}})               // find or create the Chunk by its key
            SET c.chunk_index = row.chunk_index                          // remember its position (still no text)
            MERGE (d)-[:HAS_CHUNK]->(c)                                  // the document owns this chunk
            MERGE (p:{prod_l} {{name: 'Carllm'}})                        // find or create the one Carllm product node
            MERGE (c)-[:MENTIONS]->(p)                                   // the chunk's text names the product
            """,                                                         # end of the Cypher text
            rows=rows,                                                   # the parameter: a list of small dicts
        ).consume()                                                      # wait for the write to finish
        session.run(                                                     # link the product to its own page
            f"MATCH (p:{prod_l} {{name: 'Carllm'}}), (d:{doc_l} {{doc_id: $id}}) MERGE (p)-[:DESCRIBED_IN]->(d)",  # entity -> its document
            id=own_doc,                                                  # the product page's doc_id
        ).consume()                                                      # wait for the write to finish

        keys = session.run(f"MATCH (c:{chunk_l}) RETURN keys(c) AS k LIMIT 1").single()["k"]  # which properties does a Chunk node hold?
        print("3. a Chunk node holds           :", keys, "(no text)")   # the text is not in the graph

        hit = session.run(                                               # a graph hit: everything that MENTIONS Carllm
            f"MATCH (:{prod_l} {{name: 'Carllm'}})<-[:MENTIONS]-(c) RETURN c.chunk_id AS id ORDER BY id"  # follow MENTIONS backwards
        ).value("id")                                                    # a plain list of chunk_ids
        print("4. graph hit (MENTIONS)         :", len(hit), "chunk_ids, e.g.", hit[:3])  # what a graph lookup returns

        own = session.run(                                               # the entity's OWN page, via DESCRIBED_IN then HAS_CHUNK
            f"MATCH (:{prod_l})-[:DESCRIBED_IN]->(:{doc_l})-[:HAS_CHUNK]->(c) RETURN c.chunk_id AS id ORDER BY c.chunk_index"  # two hops
        ).value("id")                                                    # the product page's chunk_ids
        print("5. its own page (DESCRIBED_IN)  :", own)                  # the page the dense search missed in the top 10

        print("6. the join key for Qdrant      :", {cid: point_id(cid) for cid in own})  # derive the Qdrant point ids; nothing stored
    finally:                                                             # always clean up, even after an error above
        removed = session.run(                                           # delete every node with one of the three demo labels
            f"MATCH (n) WHERE n:{doc_l} OR n:{chunk_l} OR n:{prod_l} DETACH DELETE n RETURN count(n) AS c"  # DETACH removes relationships too
        ).single()["c"]                                                  # how many nodes were deleted
        print("7. cleanup                      :", removed, "nodes deleted")  # nothing left behind
```
Real output:
```
1. chunks on disk               : 223
2. chunks that name Carllm      : 18 in {'company': 3, 'contracts': 11, 'employees': 2, 'products': 2}
3. a Chunk node holds           : ['chunk_id', 'chunk_index'] (no text)
4. graph hit (MENTIONS)         : 18 chunk_ids, e.g. ['company/about.md_0', 'company/about.md_1', 'company/overview.md_0']
5. its own page (DESCRIBED_IN)  : ['products/Carllm.md_0', 'products/Carllm.md_2']
6. the join key for Qdrant      : {'products/Carllm.md_0': '1ef611d2-481e-5faa-8451-316260fe51d2', 'products/Carllm.md_2': 'c48c7974-977d-5474-a96a-2fb4b810ac04'}
7. cleanup                      : 28 nodes deleted
```

**How to read it.**
- **1–2:** 223 chunks exist; 18 of them name Carllm, spread across `company`, `contracts`, `employees` and `products`.
- **3:** a `Chunk` node holds `chunk_id` and `chunk_index` and **nothing else**: no text.
- **4:** the graph hit (`MENTIONS` followed backwards) is a plain list of `chunk_id`s.
- **5:** `DESCRIBED_IN` then `HAS_CHUNK` reaches the product's own page: `products/Carllm.md_0` and `_2`. (This demo loaded only
  the chunks that *name* Carllm, so the page's chunk 1, which doesn't, is absent here. A real load gives *every* chunk its
  `HAS_CHUNK`.)
- **6:** each `chunk_id` maps to its Qdrant point id by `point_id()`, which is pure computation. That is the join.
- **7:** 28 demo nodes deleted (18 chunks, their documents, and the product node).

### 3b. Exact project code — the bridge in the model (annotated copies)
`src/graph/schema.py`, the module docstring that states the design:
```python
#▸ L4: the sentence that names the two layers
4   Two layers, joined by chunk IDs:
#▸ L6: the provenance layer: Document and Chunk, which hold IDs only
6   - provenance -- `Document` and `Chunk`: where text came from. They hold IDs only.
#▸ L7-8: the text stays in Qdrant; a graph hit and a vector hit meet on chunk_id, and the Qdrant point id is derived from it
7     Chunk text stays in Qdrant, its single owner, so a graph hit and a vector hit meet
8     on `chunk_id` (Qdrant's point id is derived from it by `vectorstore.store.point_id`).
```
The two relationship types that are not `MENTIONS` (which C1 showed):
```python
#▸ L127: start of HAS_CHUNK
127     RelationshipType(
#▸ L128: name, start label (Document), end label (Chunk) and meaning, all on one line
128         "HAS_CHUNK", ("Document",), ("Chunk",), "The document was split into this chunk."
#▸ L129: end of the entry
129     ),
...
#▸ L137: start of DESCRIBED_IN
137     RelationshipType(
#▸ L138: name
138         "DESCRIBED_IN",
#▸ L139: it may start at three labels: the entities that have a page of their own
139         ("Product", "Contract", "Employee"),
#▸ L140: and always ends at a Document
140         ("Document",),
#▸ L141: meaning: its product page, its contract, its HR record
141         "The entity's own page: its product page, its contract, its HR record.",
#▸ L142: end of the entry
142     ),
```
The derivable Qdrant id (annotated copy of `src/vectorstore/store.py`, from P3-M4):
```python
#▸ L41-43: a warning: never change the namespace, or every chunk gets a new point id and the next upsert duplicates the collection
41  # INTERVIEW: never change this value. Point IDs are derived from it, so a new
42  # namespace gives every chunk a new ID and the next upsert duplicates the whole
43  # collection instead of overwriting it.
#▸ L44: the fixed UUID that every point id is derived from
44  POINT_ID_NAMESPACE = uuid.UUID("f46fd59c-cfad-4523-9397-d36d7de5de4e")
#▸ L69: turn a chunk_id into its Qdrant point id
69  def point_id(chunk_id: str) -> str:
#▸ L76: UUIDv5 = a hash of (namespace, chunk_id): the same chunk_id always gives the same id, on any machine, with nothing stored
76      return str(uuid.uuid5(POINT_ID_NAMESPACE, chunk_id))
```
And the test that keeps the bridge honest (`tests/graph/test_schema.py`):
```python
#▸ L223: test that chunks connect to the rest of the model only through the documented bridges
223 def test_provenance_layer_only_connects_through_the_documented_bridges():
#▸ L224-226: the reason: anything else would let graph data drift away from the chunk_id that Qdrant is keyed on
224     # Chunks are the meeting point with the vector store: only a Document owns them, and
225     # only MENTIONS points from them. Anything else would let graph data drift away from
226     # the chunk_id that Qdrant is keyed on.
#▸ L227: collect the name of every relationship that starts or ends at a Chunk
227     chunk_edges = {
#▸ L230: there must be exactly two: HAS_CHUNK (owned by a document) and MENTIONS (pointing at entities)
230     assert chunk_edges == {"HAS_CHUNK", "MENTIONS"}
#▸ L231: pick out the MENTIONS relationship
231     mentions = next(r for r in RELATIONSHIP_TYPES if r.name == "MENTIONS")
#▸ L232: it must start only at a Chunk
232     assert mentions.sources == ("Chunk",)
#▸ L233: and may end at any domain label
233     assert mentions.targets == DOMAIN_LABELS
#▸ L234: the domain layer must be exactly these six labels, so adding one is a visible, deliberate change
234     assert set(DOMAIN_LABELS) == {
```
**How it fits.** The retriever (later) will run these patterns; the loader (later) will create the edges; this step fixes
the vocabulary and proves the identity rules underneath them.

### 4. Benefits
One owner of the text, graph results that are just keys, and a Qdrant id that can never disagree with the chunk id.

- **What breaks:** copying text into nodes (two copies to sync); changing `POINT_ID_NAMESPACE` (every chunk gets a new id);
  a graph that still lists chunk ids Qdrant has dropped (dangling ids).
- **Docs:** [Edge et al., GraphRAG (arXiv:2404.16130)](https://arxiv.org/abs/2404.16130) ·
  [RFC 9562 (UUIDv5)](https://www.rfc-editor.org/rfc/rfc9562) ·
  [Qdrant: points and ids](https://qdrant.tech/documentation/concepts/points/)

### Micro-exercises
1. In the demo, change `Carllm` to `Lifellm` (and the product page path). How many chunks name it?
2. Count the chunks that name *both* Carllm and Homellm (use two regexes on each chunk).
3. Print `point_id("products/Carllm.md_0")` twice, in two separate runs. Same value? Why?
4. Add the missing `products/Carllm.md_1` chunk to the demo graph. Which line of the demo decides it was left out?
5. Write the Cypher that returns the `source_path` of every document that mentions Carllm but is not its own page.

**Internals.** `uuid.uuid5(namespace, name)` computes SHA-1 over the namespace bytes plus the name, then sets the version and
variant bits, so it is a *name-based*, reproducible UUID (RFC 9562). The graph's `Chunk.chunk_id` is human-readable; the
Qdrant id is the machine form of the same fact.
**Checkpoint:** name the three provenance relationships, and say what the graph must send to Qdrant to fetch a chunk's text.

---


## C5 — Entity resolution: why people are `Employee` and `Contact`

### 1. Definition + analogy

**What it is.** **Entity resolution** is deciding whether two mentions refer to the **same real-world thing**. It can fail in
two opposite ways:

| Error | What happens | Example here |
|---|---|---|
| **False merge** | two different things are treated as one | the client's *Robert Chen* (Chief Underwriting Officer at Fortress) and Insurellm's *Robert Chen* (engineer) become one person with two jobs |
| **False split** | one thing is treated as two | `DriveSmart Insurance` and `DriveSmart Insurance Corp.` become two companies (C2) |

**What the corpus contains** (measured in C1's demo):
- **17** Insurellm-side signature blocks; **0** of the signers are on the 32-person HR roster (Jennifer Rodriguez signs as
  "Chief Executive Officer" 7 times, Michael Torres 5 times, Sarah Chen 4 times).
- The HR record and About page name **Avery Lancaster** as CEO: the corpus **contradicts itself**.
- **2** client-side signers share a name with an employee: Robert Chen (Fortress) and Marcus Johnson (Rapid Claims).
- Several names are one word away from roster names: *Sarah Chen* / *Alex Chen* / *Robert Chen* / *Sarah Williams*, *Jennifer
  Rodriguez* / *Carlos Rodriguez* / *Jennifer Adams*. A **fuzzy** matcher (similar spelling counts as a match) would
  wrongly link them.

**The design that follows:**
- Two **labels**: `Employee` (on the HR roster, keyed on `name`) and `Contact` (named in a contract, keyed on
  `(name, organization)`). A name that exists in both is two nodes, because a label is part of a node's identity.
- People are matched by **exact full name plus organisation context**, never by similarity.
- The contradiction is **kept, not resolved**: Avery is an `Employee`, Jennifer Rodriguez is a `Contact` at Insurellm with
  the title she signs under. Deciding which "CEO" a question means is retrieval's job; the graph just refuses to lie.

**Key terms.** *Blocking* (only compare candidates that share something), *exact vs fuzzy matching*, *false merge / false
split*, *authoritative source* (the HR record is the authority for who works at Insurellm).

**Limits.** The label split makes a false merge across labels *impossible*, but it cannot stop the loader deciding wrongly
which label a mention belongs to. That rule (roster match plus organisation) is extraction's job and is decided, not yet built.

**Analogy.** Two students named Alex Kim in different schools: same name, different people. A register that lists "Alex Kim"
once, with both schools' grades, would give one child a wrong record. Adding the school to the identity fixes it.

### For a 14-year-old

There's a boy called **Robert Chen** in your school (he's good at computers). There's a *different* man called Robert Chen
who runs a company across town. If you make one "Robert Chen" card and write both jobs on it, the card now says "a
schoolboy who is also the boss of a company", which is **nonsense**.

The fix is to keep **two kinds of cards**: a blue "Student" card and a green "Business contact" card. A green card also
says *which company*. Now both Roberts exist, each with the right facts.

And here's a twist that really happened in our data: two different papers name **two different bosses** of our company. We
don't secretly pick one. We keep both, each on its own card, and say where we read it.

### 2. Why (vs the traditional approach)
The common shortcut is one `Person` label with `MERGE (p:Person {name: $name})`. It works on clean data and quietly
corrupts data like this: the second `MERGE` *finds* the first Robert Chen and `SET` overwrites his job. It also tempts people
to add fuzzy matching to "handle typos", which links Sarah Chen to Alex Chen. Making the difference **structural** (two
labels, and an organisation in the key) is more reliable than hoping the loader remembers.

### 3a. Minimal demo — the one-label mistake, then the two-label model, on the real HR records and signature blocks
Reads the real corpus; live Neo4j, throwaway labels, self-cleaning.

`c5_two_person_labels.py`
```python
# C5: why people are split into Employee and Contact. Reads the real corpus; live Neo4j on throwaway labels, self-cleaning.
import re                                                                # regular expressions, to read the Markdown
import uuid                                                              # to make labels no other data can have
from pathlib import Path                                                 # object-style file paths

from src.graph.schema import NodeType, constraint_statement             # the project's model class and DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
person_l, emp_l, con_l = f"DemoPerson{suffix}", f"DemoEmployee{suffix}", f"DemoContact{suffix}"  # one-label experiment, then the two-label model
employee = NodeType(emp_l, ("name",), "domain", "demo")                  # like Employee: identified by name
contact = NodeType(con_l, ("name", "organization"), "domain", "demo")    # like Contact: identified by name AND organisation

data = Path("data")                                                      # the corpus folder
roster = {}                                                              # employee name -> job title, from the HR records
for p in (data / "employees").glob("*.md"):                              # every HR record
    roster[p.stem] = re.search(r"\*\*Job Title:?\*\*:?\s*(.+)", p.read_text(encoding="utf-8"))[1].strip()  # the Job Title line

block = re.compile(                                                      # a signature block is three bold lines:
    r"\*\*(?P<name>[^*\n]+?)\*\*[ \t]*\n\*\*Title\*\*:?[ \t]*(?P<title>[^\n]+?)[ \t]*\n\*\*(?P<org>[^*\n]+?)\*\*"  # name, title, organisation
)                                                                        # end of the pattern
signers = []                                                             # (name, title, organisation) for every real signer
for p in sorted((data / "contracts").glob("*.md")):                      # every contract
    text = p.read_text(encoding="utf-8")                                 # its text
    client = re.search(r"^# Contract with (.+?) for ", text, re.M)[1]    # the client's short name, from the title
    for m in block.finditer(text):                                       # each signature block in it
        if m["name"].startswith("["):                                    # skip unfilled "[Name]" placeholders
            continue                                                     # they name nobody
        org = "Insurellm" if m["org"].lower().startswith("insurellm") else client  # the organisation, as one canonical name
        signers.append((m["name"], m["title"].strip(), org))             # keep the signer

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        print("1. ONE label, keyed on name (the mistake):")              # first, what goes wrong with a single Person label
        session.run(f"CREATE CONSTRAINT demo_person_{suffix} IF NOT EXISTS FOR (p:{person_l}) REQUIRE p.name IS UNIQUE").consume()  # unique name
        for title, org in [("Senior Full Stack Engineer", "Insurellm"), ("Chief Underwriting Officer", "Fortress")]:  # two DIFFERENT Robert Chens
            session.run(f"MERGE (p:{person_l} {{name: 'Robert Chen'}}) SET p.title = $t, p.org = $o", t=title, o=org).consume()  # MERGE finds the first
        merged = session.run(f"MATCH (p:{person_l}) RETURN p.name AS name, p.title AS title, p.org AS org, count(*) AS people").data()  # what is stored
        print("  ", merged)                                              # ONE person: the engineer's job was overwritten

        print("2. TWO labels: Employee (name) and Contact (name + organisation):")  # now the model P4-M2 chose
        session.run(constraint_statement(employee)).consume()            # the project's DDL: employee names are unique
        session.run(constraint_statement(contact)).consume()             # the project's DDL: (name, organisation) pairs are unique
        for name, title in roster.items():                               # load the 32 employees
            session.run(f"MERGE (e:{emp_l} {{name: $n}}) SET e.job_title = $t", n=name, t=title).consume()  # keyed on name
        for name, title, org in signers:                                 # load every named signer
            session.run(f"MERGE (c:{con_l} {{name: $n, organization: $o}}) SET c.title = $t", n=name, o=org, t=title).consume()  # keyed on the pair
        counts = session.run(f"MATCH (e:{emp_l}) WITH count(e) AS employees MATCH (c:{con_l}) RETURN employees, count(c) AS contacts").single()  # sizes
        print("   nodes:", counts.data())                                # 32 employees; contacts are the distinct (name, org) pairs

        both = session.run(                                              # names that exist as BOTH an employee and a contact
            f"MATCH (e:{emp_l}), (c:{con_l}) WHERE e.name = c.name "     # same name, different labels
            "RETURN e.name AS name, e.job_title AS employee_job, c.organization AS contact_org, c.title AS contact_title ORDER BY name"  # both facts
        ).data()                                                         # rows as dicts
        for row in both:                                                 # each namesake pair
            print("   namesakes kept apart:", row)                      # two people, two nodes, both facts intact

        ceo_emp = session.run(f"MATCH (e:{emp_l}) WHERE e.job_title CONTAINS 'Chief Executive' RETURN e.name AS name").value("name")  # HR record's CEO
        ceo_con = session.run(f"MATCH (c:{con_l}) WHERE c.organization = 'Insurellm' AND c.title CONTAINS 'Chief Executive' RETURN c.name AS name").value("name")  # contracts' CEO
        print("3. CEO by HR record (Employee)  :", ceo_emp)              # Avery Lancaster
        print("   CEO by contracts (Contact)   :", ceo_con, "- both kept, neither overwrites the other")  # Jennifer Rodriguez
    finally:                                                             # always clean up, even after an error above
        for name in (f"demo_person_{suffix}", employee.constraint_name, contact.constraint_name):  # the three demo constraints
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop each
        removed = session.run(                                           # delete every node with a demo label
            f"MATCH (n) WHERE n:{person_l} OR n:{emp_l} OR n:{con_l} DETACH DELETE n RETURN count(n) AS c"  # DETACH removes relationships too
        ).single()["c"]                                                  # how many were deleted
        print("4. cleanup                      :", removed, "nodes deleted; constraints dropped")  # nothing left behind
```
Real output:
```
1. ONE label, keyed on name (the mistake):
   [{'name': 'Robert Chen', 'title': 'Chief Underwriting Officer', 'org': 'Fortress', 'people': 1}]
2. TWO labels: Employee (name) and Contact (name + organisation):
   nodes: {'employees': 32, 'contacts': 19}
   namesakes kept apart: {'name': 'Marcus Johnson', 'employee_job': 'Customer Success Manager', 'contact_org': 'Rapid Claims Associates', 'contact_title': 'Chief Claims Officer'}
   namesakes kept apart: {'name': 'Robert Chen', 'employee_job': 'Senior Full Stack Engineer', 'contact_org': 'Fortress Business Underwriters', 'contact_title': 'Chief Underwriting Officer'}
3. CEO by HR record (Employee)  : ['Avery Lancaster']
   CEO by contracts (Contact)   : ['Jennifer Rodriguez'] - both kept, neither overwrites the other
4. cleanup                      : 52 nodes deleted; constraints dropped
```

**How to read it.**
- **1:** with one label keyed on name, the two Robert Chens became **one** node whose title is `Chief Underwriting Officer` and
  org `Fortress`; the engineer's job was **overwritten** (`SET` on the matched node). A false fact, with no error.
- **2:** with two labels, all 32 employees load, and 19 distinct `(name, organization)` contacts load. The `namesakes` rows
  show `Marcus Johnson` and `Robert Chen` existing in **both** labels, each with the right job.
- **3:** the two CEOs sit side by side: `Avery Lancaster` from the HR record (`Employee`) and `Jennifer Rodriguez` from the
  contracts (`Contact` at Insurellm). Neither erased the other.
- **4:** 52 nodes deleted (1 + 32 + 19) and the three demo constraints dropped.

### 3b. Exact project code — the two person labels (annotated copies)
`src/graph/schema.py`:
```python
#▸ L101: start of Employee
101     NodeType(
#▸ L102: label
102         "Employee",
#▸ L103: key: the name alone, because the roster is closed and its 32 names are distinct
103         ("name",),
#▸ L104: layer
104         "domain",
#▸ L105-106: meaning: nobody who merely appears in a contract is an Employee
105         "A person on the HR roster (data/employees/). Nobody who merely appears in a "
106         "contract is an Employee.",
#▸ L107: end
107     ),
...
#▸ L108: start of Contact
108     NodeType(
#▸ L109: label
109         "Contact",
#▸ L110: key: name AND organisation together
110         ("name", "organization"),
#▸ L111: layer
111         "domain",
#▸ L112-113: meaning: namesakes exist, so a client's Robert Chen is not Insurellm's
112         "A person named in a contract, such as a signatory. Identity is name plus "
113         "organisation, because namesakes exist: a client's 'Robert Chen' is not Insurellm's.",
#▸ L114: end
114     ),
```
`tests/graph/test_schema.py`, the unit test that states the rule in words:
```python
#▸ L244: test that the two person labels are really different
244 def test_employees_and_contacts_are_different_labels():
#▸ L245-247: the reason, in the file's own words: two Robert Chens, and two different CEOs
245     # The corpus has a client-side "Robert Chen" and an Insurellm employee "Robert Chen",
246     # and names the Insurellm CEO differently in contracts than in the HR record. One
247     # shared label keyed on name would merge them; two labels make that impossible.
#▸ L248: index the real model by label
248     labels = {n.label: n for n in NODE_TYPES}
#▸ L249: an Employee is identified by name alone
249     assert labels["Employee"].key == ("name",)
#▸ L250: a Contact by name plus organisation
250     assert labels["Contact"].key == ("name", "organization")
```
and the **live** test that proves it on the real server:
```python
#▸ L559: needs a running service
559 @pytest.mark.live
#▸ L560: skipped automatically, with a reason, if Neo4j is down
560 @live
#▸ L561: the test; `real_schema` is a fixture that applies the real model first (C11)
561 def test_live_namesakes_are_kept_apart_by_organisation_and_by_label(real_schema):
#▸ L562: a unique "__test_..." prefix so cleanup can find these nodes
562     token = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
#▸ L563: one parameterised CREATE for a Contact
563     contact = "CREATE (:Contact {name: $name, organization: $org})"
#▸ L565: Robert Chen at Fortress: allowed
565     _run(real_schema, contact, name=f"{token}_Robert Chen", org="Fortress")
#▸ L566: the reason for the next line
566     # Same name at another organisation: a different person, allowed.
#▸ L567: Robert Chen at Insurellm: a different person, allowed
567     _run(real_schema, contact, name=f"{token}_Robert Chen", org="Insurellm")
#▸ L568: the reason for the next line
568     # The same name as an Employee is a different label, so it cannot collide either.
#▸ L569: an Employee called Robert Chen: a different label, so it cannot collide
569     _run(real_schema, "CREATE (:Employee {name: $name})", name=f"{token}_Robert Chen")
#▸ L570: the reason for the next line
570     # The identical (name, organisation) pair is the one thing refused.
#▸ L571: the identical (name, organisation) pair is the one thing refused
571     with pytest.raises(ConstraintError):
#▸ L572: try to create it again
572         _run(real_schema, contact, name=f"{token}_Robert Chen", org="Fortress")
```
**How it fits.** The label split is decided here; the loader that *chooses* the label (roster match plus organisation) is
built with extraction. The database will hold whichever nodes it is given but can never confuse the two kinds.

### 4. Benefits
Namesakes and contradictions stay visible instead of being silently merged, and the guard is structural.

- **What breaks:** a single `Person` label; `SET` on a `MERGE`d node that may have matched the wrong person; fuzzy matching
  of names; taking a signature block as proof someone is an employee.
- **Docs:** [Fellegi & Sunter, "A Theory for Record Linkage" (1969)](https://doi.org/10.1080/01621459.1969.10501049) ·
  [Christen, *Data Matching* (Springer, 2012)](https://link.springer.com/book/10.1007/978-3-642-31164-2) ·
  [Neo4j: `MERGE` semantics](https://neo4j.com/docs/cypher-manual/current/clauses/merge/)

### Micro-exercises
1. In the demo, change the `Contact` key to name only (drop `organization`). What happens to the 19 contacts?
2. Add a check that lists every roster name that appears as a `Contact` *at Insurellm*. Is it empty?
3. Find the one-word-away pairs yourself: compare every signer with every roster name by last name.
4. Write the Cypher for "who signed contracts as Chief Revenue Officer, and for how many?"
5. Decide, and justify, how the loader should treat "Avery Lancaster" when the About page mentions her (Employee or Contact?).

**Internals.** A node's labels are a *set*, and `MATCH (n:Employee)` uses the label index, so the two people do not compete for
the same key space. A uniqueness constraint is scoped **per label**: `Employee.name` and `Contact.name` are independent rules.
**Checkpoint:** explain why the demo's step 1 loses information without raising an error, and what the two-label model changes.

---


## C6 — DDL cannot take labels as parameters: identifier validation

### 1. Definition + analogy

**What it is.** In Cypher you should send *values* as **parameters** (`$name`), never paste them into the query text. But a
constraint statement has parts that are not values: the **label**, the **property name** and (optionally) the **constraint
name**. Whether these may be parameters is a property of the server, so it was **tested** (demo, Neo4j 5.26):

| Position | As a parameter? |
|---|---|
| a value, e.g. `{name: $n}` | yes (the normal case) |
| a label in a pattern, `MATCH (n:$label)` | **no** (syntax error; 5.26 has a separate dynamic form `n:$(expr)` for queries) |
| a label in constraint DDL, `FOR (n:$label)` | **no** (syntax error, and the dynamic form fails too) |
| a property in constraint DDL, `REQUIRE n.$p` | **no** (syntax error) |
| the constraint's *name*, `CREATE CONSTRAINT $cname` | **yes** |

So the label and property names must be **spliced into the text**. Splicing text into a query is exactly how **injection**
happens, so the project **validates** every spliced name: it must be a plain identifier (letters, digits, underscore; not
starting with a digit), or the statement is never built.

**Key terms.** *Parameter*: a value sent separately from the query text, so it is never parsed as code. *Injection*: input
that escapes its intended position and changes the meaning of the statement. *Allow-list validation*: accept only what
matches a strict pattern, rather than trying to block bad input. `re.fullmatch`: the pattern must match the **whole**
string.

**Limits.** The model is *trusted code today* (the labels are constants in this repository), so this is defence in depth:
it matters if the model is ever built from configuration, from a file, or from an LLM's output. The pattern is deliberately
narrower than Cypher's rules: Cypher would also accept backtick-quoted names with spaces, and this code does not.

**Analogy.** A restaurant order form with a "special instructions" box. If the kitchen *reads and obeys* whatever is
written there, someone can write "and give me everything free". Values go in a box the kitchen treats as *data*
(parameters); the *dish name* must be picked from the menu (validated identifier).

### For a 14-year-old

Imagine you send your friend a note: "Please buy **X** from the shop." If X is meant to be a snack, fine. But what if someone
sneaks in: "**a sandwich. Also, give them all my money.**" If your friend just reads the whole note out loud to the shopkeeper,
the shopkeeper hears "give them all my money" too.

The safe way: your friend only accepts X if it's **a single word from the shop's list**. Anything else? "That's not a valid
item", and the note is torn up **before** it reaches the shopkeeper. That's what validating an identifier does.

### 2. Why (vs the traditional approach)
Query parameters are the standard defence in SQL and Cypher, and they are enough for *values*. For structure (table or
label names) neither language lets you parameterise, so the standard answers are an **allow-list** or a **strict identifier
pattern**, which is what the project does. The alternative ("just use an f-string, the labels are ours") works right up to
the day someone builds a model from a config file.

### 3a. Minimal demo — what the server accepts as a parameter, what an unvalidated f-string would send, and the eight real statements
Live Neo4j (only failing statements plus one throwaway constraint that is dropped), self-cleaning.

`c6_ddl_identifiers.py`
```python
# C6: constraint DDL cannot take labels or property names as parameters, so the code splices names and validates them.
# Live Neo4j for the syntax checks (only harmless failing statements plus one throwaway constraint), self-cleaning.
import uuid                                                              # to make a label no other data can have

from neo4j.exceptions import Neo4jError                                  # base class of every error the server reports

from src.graph.schema import NODE_TYPES, NodeType, constraint_statement  # the model class, the model and the DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                    # a unique label; nothing is ever created under it


def attempt(session, title: str, query: str, **parameters) -> None:      # run one statement and report success or the failure class
    try:                                                                 # the server may refuse
        session.run(query, **parameters).consume()                       # run it and wait for the result
        print(f"   OK    {title}")                                       # accepted
    except Neo4jError as exc:                                            # refused
        print(f"   FAIL  {title} -> {type(exc).__name__}")               # show which class of error


with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    print("1. what the server accepts as a parameter (Neo4j 5.26):")     # observed, not assumed
    attempt(session, "MATCH (n:$label)                      label as parameter", "MATCH (n:$label) RETURN n", label=label)  # not allowed
    attempt(session, "FOR (n:$label) ... constraint          label as parameter", "CREATE CONSTRAINT d1 IF NOT EXISTS FOR (n:$label) REQUIRE n.name IS UNIQUE", label=label)  # not allowed
    attempt(session, "REQUIRE n.$p IS UNIQUE                 property as parameter", f"CREATE CONSTRAINT d2 IF NOT EXISTS FOR (n:{label}) REQUIRE n.$p IS UNIQUE", p="name")  # not allowed
    try:                                                                 # a constraint NAME, however, may be a parameter
        attempt(session, "CREATE CONSTRAINT $cname ...           constraint name as parameter", f"CREATE CONSTRAINT $cname IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE", cname=f"demo_{label}")  # allowed
    finally:                                                             # clean up the one constraint that was created
        session.run(f"DROP CONSTRAINT demo_{label} IF EXISTS").consume()  # drop it

print("2. so the code splices names into the text, after validating them:")  # the project's answer
evil = NodeType("Company) detach delete n //", ("name",), "domain", "")  # a label crafted to break out of the pattern
naive = f"CREATE CONSTRAINT x IF NOT EXISTS FOR (n:{evil.label}) REQUIRE n.name IS UNIQUE"  # what plain string formatting would produce
print("   naive f-string would send   :", naive)                         # the label closed the pattern and added a clause
try:                                                                     # the project's generator validates every name first
    constraint_statement(evil)                                           # this must refuse
except ValueError as exc:                                                # plain identifiers only
    print("   constraint_statement says   :", exc)                      # rejected before anything reaches the database

print("3. the eight statements the model generates:")                    # the real DDL for the real model
for node in NODE_TYPES:                                                  # each node type in the registry
    print("  ", constraint_statement(node))                             # the exact text sent to Neo4j
```
Real output:
```
1. what the server accepts as a parameter (Neo4j 5.26):
   FAIL  MATCH (n:$label)                      label as parameter -> CypherSyntaxError
   FAIL  FOR (n:$label) ... constraint          label as parameter -> CypherSyntaxError
   FAIL  REQUIRE n.$p IS UNIQUE                 property as parameter -> CypherSyntaxError
   OK    CREATE CONSTRAINT $cname ...           constraint name as parameter
2. so the code splices names into the text, after validating them:
   naive f-string would send   : CREATE CONSTRAINT x IF NOT EXISTS FOR (n:Company) detach delete n //) REQUIRE n.name IS UNIQUE
   constraint_statement says   : 'company) detach delete n //_name_unique' is not a plain identifier and cannot be used in DDL
3. the eight statements the model generates:
   CREATE CONSTRAINT document_doc_id_unique IF NOT EXISTS FOR (n:Document) REQUIRE n.doc_id IS UNIQUE
   CREATE CONSTRAINT chunk_chunk_id_unique IF NOT EXISTS FOR (n:Chunk) REQUIRE n.chunk_id IS UNIQUE
   CREATE CONSTRAINT company_name_unique IF NOT EXISTS FOR (n:Company) REQUIRE n.name IS UNIQUE
   CREATE CONSTRAINT product_name_unique IF NOT EXISTS FOR (n:Product) REQUIRE n.name IS UNIQUE
   CREATE CONSTRAINT contract_doc_id_unique IF NOT EXISTS FOR (n:Contract) REQUIRE n.doc_id IS UNIQUE
   CREATE CONSTRAINT employee_name_unique IF NOT EXISTS FOR (n:Employee) REQUIRE n.name IS UNIQUE
   CREATE CONSTRAINT contact_name_organization_unique IF NOT EXISTS FOR (n:Contact) REQUIRE (n.name, n.organization) IS UNIQUE
   CREATE CONSTRAINT location_name_unique IF NOT EXISTS FOR (n:Location) REQUIRE n.name IS UNIQUE
```

**How to read it.**
- **1:** the label and the property name are refused as parameters (`CypherSyntaxError`); the constraint *name* is accepted. That
  last row corrected a comment in the source, which had said names of constraints could not be parameterised.
- **2:** an unvalidated f-string with a crafted label would send `... FOR (n:Company) detach delete n //) REQUIRE ...`: the
  label closed the pattern and added a clause. `constraint_statement` raises `ValueError` first, so nothing is sent. (It checks
  the *derived constraint name* before the label, which is why the message shows `company) detach delete n //_name_unique`;
  the label would have been rejected too.)
- **3:** the eight statements the real model generates, exactly as sent.

### 3b. Exact project code — the validation (annotated copies)
`src/graph/schema.py`:
```python
#▸ L28-32: the comment that records the observed rule: labels and property names cannot be parameters, so they are spliced, and therefore validated
28  # In constraint DDL a label or property name cannot be a parameter or a dynamic expression
29  # (observed on 5.26: both are syntax errors), so they are spliced into the statement text.
30  # Restricting them to plain identifiers keeps that safe even if a model is ever built from
31  # something other than this file's constants. The constraint name is spliced too, for one
32  # uniform rule, although 5.26 would accept it as a parameter.
#▸ L33: the allowed shape: a letter or underscore, then any letters, digits or underscores
33  _IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
...
#▸ L216: validate one name and return it unchanged
216 def _identifier(value: str) -> str:
#▸ L217: fullmatch (the WHOLE string must fit), so "Two Words" or "a; b" fail even though they start with valid characters
217     if not _IDENTIFIER.fullmatch(value):
#▸ L218: refuse loudly, naming the bad value with !r (so a stray space or quote is visible)
218         raise ValueError(f"{value!r} is not a plain identifier and cannot be used in DDL")
#▸ L219: pass a valid name through, so the call can be nested inside an f-string
219     return value
...
#▸ L224: every key property goes through _identifier before it is written into the text
224     properties = [f"n.{_identifier(p)}" for p in node.key]
#▸ L227: the constraint name is validated too, for one uniform rule, though 5.26 would accept it as a parameter
227         f"CREATE CONSTRAINT {_identifier(node.constraint_name)} IF NOT EXISTS "
#▸ L228: and so is the label
228         f"FOR (n:{_identifier(node.label)}) REQUIRE {target} IS UNIQUE"
```
`tests/graph/test_schema.py`, four hostile shapes must be refused **before** anything reaches the database:
```python
#▸ L269: run the test below once for each bad model entry in the list
269 @pytest.mark.parametrize(
#▸ L270: the argument name each entry is passed as
270     "bad",
#▸ L272: a label that closes the pattern and injects a clause
272         NodeType("Company) DETACH DELETE n //", ("name",), "domain", ""),
#▸ L273: a property name that ends the statement and starts another
273         NodeType("Company", ("name; DROP DATABASE neo4j",), "domain", ""),
#▸ L274: a label with a space
274         NodeType("Two Words", ("name",), "domain", ""),
#▸ L275: a label that starts with a digit
275         NodeType("1Company", ("name",), "domain", ""),
#▸ L278: the test: the parameter `bad` is one of the four entries
278 def test_identifiers_that_are_not_plain_names_never_reach_the_database(bad):
#▸ L279-280: the reason: only plain identifiers may be spliced into the text
279     # A label or property name in constraint DDL cannot be a parameter, so it is spliced
280     # into the text; that is only safe while nothing but plain identifiers can get through.
#▸ L281: a fake driver that records every statement it is sent
281     driver = FakeSchemaDriver()
#▸ L282: applying it must raise ValueError with this message
282     with pytest.raises(ValueError, match="not a plain identifier"):
#▸ L283: try to apply a model containing the bad entry
283         apply_schema(driver, (bad,))
#▸ L284: and nothing at all may have reached the (fake) database
284     assert driver.statements == []
```
**How it fits.** Every statement in C3, C7 and C8 passes through `_identifier`; C11's fake asserts that none is sent for a bad name.

### 4. Benefits
A model built from anything untrusted cannot smuggle Cypher into the schema, and the failure is loud and early.

- **What breaks:** using `re.match` instead of `fullmatch` (a valid *prefix* passes); formatting *values* (not just names)
  into the text; assuming a name is safe because the model file is ours.
- **Docs:** [Cypher manual: parameters](https://neo4j.com/docs/cypher-manual/current/syntax/parameters/) ·
  [Cypher manual: naming rules](https://neo4j.com/docs/cypher-manual/current/syntax/naming/) ·
  [OWASP: Injection prevention](https://cheatsheetseries.owasp.org/cheatsheets/Injection_Prevention_Cheat_Sheet.html) ·
  [Python `re.fullmatch`](https://docs.python.org/3/library/re.html#re.fullmatch)

### Micro-exercises
1. Change `fullmatch` to `match` in a scratch copy and rerun the four hostile cases. Which ones now get through, and why does only one still fail?
2. Try `MATCH (n:$($label)) RETURN n` in the Browser with a parameter. It is accepted for queries; try the same in `FOR (...)`.
3. Add a unicode label such as `Café`. Is it accepted? Should it be?
4. Write a pattern that also allows backtick-quoted names, and say why you would or would not.
5. Which of the eight generated statements has parentheses, and which piece of `constraint_statement` decides that?

**Internals.** Cypher's grammar expects a fixed *symbolic name* in these positions and reads it at parse time, before any parameters are bound, which
is why a parameter cannot supply one. A value parameter is bound *after* parsing, so it can never change the statement's shape.
**Checkpoint:** say which of label, property name, constraint name and value can be a parameter in a constraint statement, and
what the project does about the ones that cannot.

---


## C7 — `apply_schema`: idempotent creation, counters, and the silent no-op

### 1. Definition + analogy

**What it is.** `apply_schema` makes the database match the model **and proves it did**. It is safe to run any number of times.
It does four things in order:

| Step | What | Why |
|---|---|---|
| 1 | one `CREATE CONSTRAINT ... IF NOT EXISTS` per node type, each **on its own** (auto-commit) | Neo4j forbids schema changes in a transaction that has already written |
| 2 | read the server's own `constraints_added` counter for each | `1` = created, `0` = a no-op; that is how the report knows |
| 3 | `CALL db.awaitIndexes(60)` | *created* is not *enforced*: over existing data the backing index can still be populating |
| 4 | `check_schema` (C8), and raise `SchemaMismatch` if a rule is not in force | because step 2's `0` can mean "already there" **or** "silently skipped" |

**The trap it exists for.** `IF NOT EXISTS` makes creation idempotent, but it is a **silent no-op** in two more situations, both
observed on this server (demo, steps 3–4):

| Situation | `constraints_added` | Error? |
|---|---|---|
| the constraint already exists | 0 | no (intended) |
| **a constraint with the same name but a different definition** exists | 0 | **no** |
| an equivalent constraint exists under another name | 0 | no |

The middle row is dangerous: apply "succeeds" while the rule you wanted is **not** in force. So the counters alone are not
trusted; the result is *verified by definition* afterwards (C8).

**Key terms.** *Idempotent*. *Auto-commit*: a statement that runs in its own implicit transaction. *Counters*: per-statement
change statistics from the server's result summary. *`db.awaitIndexes`*: a built-in procedure that blocks until indexes are
`ONLINE` (or the timeout passes).

**Limits.** It never repairs a mismatch: dropping a constraint is a human decision (the same stance as `ensure_collection` for
Qdrant in P3-M1). A 60-second wait is a ceiling, not a guarantee; on a very large graph population could take longer.

**Analogy.** A builder who is asked to "install the smoke alarm in room 3 **if there isn't one**". If a *doorbell* has already
been wired in that spot under the name "smoke alarm", a careless builder says "already installed" and leaves. A careful one
**tests the alarm** before signing off. `apply_schema` tests the alarm.

### For a 14-year-old

You ask a helper: "Put a lock on the shed **if it doesn't have one**." The helper looks: there's already something on the
door called "lock". They shrug, "already done!" and leave.

But the "lock" turns out to be a **picture of a lock** someone drew on the door. It looks right, and it doesn't lock anything.

A careful helper does not just look at the label. They **push on the door** to check it is really locked. And if it's a
drawing, they don't quietly rip it off and fit a lock; they **come and tell you**, "there's a drawing here, what do you want me
to do?" That's `apply_schema`: create if needed, then test, then report a problem rather than silently "fixing" it.

### 2. Why (vs the traditional approach)
Migration tools (Flyway, Alembic) record which changes ran and refuse to re-run them; that is their idempotency. Neo4j has no
migration table, so idempotency comes from `IF NOT EXISTS` plus a check that the *result* is right. The check matters because,
unlike a migration tool, `IF NOT EXISTS` gives you no way to tell "already done correctly" from "already done differently".

### 3a. Minimal demo — apply twice, read the counters, plant an impostor, and try DDL after a write
Live Neo4j, throwaway labels, self-cleaning.

`c7_apply_schema.py`
```python
# C7: apply_schema step by step -- idempotent creation, the counters, the silent no-op, and DDL after a write.
# Live Neo4j on throwaway labels, self-cleaning.
import time                                                              # to time the wait for indexes
import uuid                                                              # to make labels no other data can have

from neo4j.exceptions import Neo4jError                                  # base class of every error the server reports

from src.graph.schema import NodeType, SchemaMismatch, apply_schema, constraint_statement  # the code under study
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
single = NodeType(f"Demo{suffix}", ("name",), "domain", "demo")          # a one-property key, like Company
composite = NodeType(f"DemoC{suffix}", ("name", "organization"), "domain", "demo")  # a two-property key, like Contact
types = (single, composite)                                              # the demo's tiny "model"
tx_label = f"DemoT{suffix}"                                              # a third label, for the transaction experiment

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        first = apply_schema(driver, types)                              # 1. apply the tiny model to an empty label set
        print("1. first apply   : created", len(first.created), "present", len(first.present))  # both are new
        second = apply_schema(driver, types)                             # 2. apply it again
        print("2. second apply  : created", len(second.created), "present", len(second.present))  # nothing new: safe to re-run

        raw = session.run(constraint_statement(single)).consume().counters.constraints_added  # 3. the server's own counter for one statement
        print("3. counters      : constraints_added =", raw, "(0 = a no-op; this is how 'created' vs 'present' is known)")  # 0

        session.run(f"DROP CONSTRAINT {single.constraint_name}").consume()  # 4. remove the rule, then plant an IMPOSTOR under the same name
        session.run(                                                     # the impostor: same name, but it protects another property
            f"CREATE CONSTRAINT {single.constraint_name} IF NOT EXISTS FOR (n:{single.label}) REQUIRE n.other IS UNIQUE"  # a different rule
        ).consume()                                                      # wait for it
        noop = session.run(constraint_statement(single)).consume().counters.constraints_added  # now ask for the REAL rule under that name
        print("4. name taken    : constraints_added =", noop, "-> IF NOT EXISTS silently did nothing")  # 0, no error
        try:                                                             # apply_schema must not be fooled by that silence
            apply_schema(driver, (single,))                              # it verifies by definition after creating
        except SchemaMismatch as exc:                                    # so it raises
            print("   apply_schema  : SchemaMismatch |", exc)            # naming both definitions
        session.run(f"DROP CONSTRAINT {single.constraint_name}").consume()  # remove the impostor
        healed = apply_schema(driver, (single,))                         # and apply again
        print("   after dropping: created", len(healed.created), "-> the real rule is now in force")  # 1

        try:                                                             # 5. schema change after a write in ONE transaction
            with driver.session() as other, other.begin_transaction() as tx:  # an explicit transaction on its own session
                tx.run(f"CREATE (:{tx_label} {{name: 'x'}})").consume()  # a write ...
                tx.run(f"CREATE CONSTRAINT demo_tx_{suffix} IF NOT EXISTS FOR (n:{tx_label}) REQUIRE n.name IS UNIQUE").consume()  # ... then DDL
                tx.commit()                                              # never reached
        except Neo4jError as exc:                                        # the server refuses
            print("5. DDL after a write in one transaction:", type(exc).__name__, "|", exc.code)  # why apply_schema runs each DDL on its own

        started = time.perf_counter()                                    # 6. time the wait for indexes
        session.run("CALL db.awaitIndexes($seconds)", seconds=60).consume()  # blocks until every index is ONLINE (or 60 s pass)
        print(f"6. awaitIndexes  : returned after {(time.perf_counter() - started) * 1000:.0f} ms (empty labels, so immediate)")  # fast here
    finally:                                                             # always clean up, even after an error above
        for name in (single.constraint_name, composite.constraint_name, f"demo_tx_{suffix}"):  # every constraint the demo may have made
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop it if it exists
        removed = session.run(                                           # delete any nodes with a demo label
            f"MATCH (n) WHERE n:{single.label} OR n:{composite.label} OR n:{tx_label} DETACH DELETE n RETURN count(n) AS c"  # DETACH removes relationships too
        ).single()["c"]                                                  # how many were deleted
        print("7. cleanup       :", removed, "nodes deleted; constraints dropped")  # nothing left behind
```
Real output:
```
1. first apply   : created 2 present 0
2. second apply  : created 0 present 2
3. counters      : constraints_added = 0 (0 = a no-op; this is how 'created' vs 'present' is known)
4. name taken    : constraints_added = 0 -> IF NOT EXISTS silently did nothing
   apply_schema  : SchemaMismatch | demofd342f21_name_unique exists but is UNIQUENESS on Demofd342f21(other), not UNIQUENESS on Demofd342f21(name); drop it to let the model recreate it
   after dropping: created 1 -> the real rule is now in force
5. DDL after a write in one transaction: Forbidden | Neo.ClientError.Transaction.ForbiddenDueToTransactionType
6. awaitIndexes  : returned after 4 ms (empty labels, so immediate)
7. cleanup       : 0 nodes deleted; constraints dropped
```

**How to read it.**
- **1–2:** the first apply creates both constraints; the second creates none (`created 0 present 2`). Re-running is safe.
- **3:** the raw counter for one statement is `0`: that is exactly how `apply_schema` distinguishes `present` from `created`.
- **4:** with an *impostor* under the expected name, `IF NOT EXISTS` returns `0` and raises nothing (a silent no-op), but
  `apply_schema` raises `SchemaMismatch` naming **both** definitions (`Demo…(other)` vs `Demo…(name)`). After the impostor is
  dropped, the next apply creates the real rule.
- **5:** DDL after a write in one explicit transaction is refused (`Forbidden`, code `ForbiddenDueToTransactionType`), which is why
  each statement runs on its own.
- **6:** `db.awaitIndexes` returned in 3 ms because the labels are empty. It exists for the day the graph has data.
- **7:** no nodes were ever created; the demo's constraints are dropped.

### 3b. Exact project code — `apply_schema` and its report types (annotated copy of `src/graph/schema.py`)
```python
#▸ L35-36: the reason for the wait: created is not the same as enforced
35  # `CREATE CONSTRAINT` returns once the constraint exists, but over existing data its
36  # backing index can still be populating. Waiting makes "applied" mean "enforced".
#▸ L37: the longest to wait for indexes to come online: 60 seconds
37  AWAIT_INDEXES_SECONDS = 60
...
#▸ L197: make the report an immutable record
197 @dataclass(frozen=True)
#▸ L198: what apply_schema returns
198 class SchemaReport:
#▸ L199: the constraint names this call created
199     created: tuple[str, ...]
#▸ L200: the constraint names whose statement was a no-op
200     present: tuple[str, ...]
...
#▸ L232: apply the model to the database
232 def apply_schema(
#▸ L233: any driver-shaped object: the real one, or a test fake (C11)
233     driver: GraphDriver,
#▸ L234: the model to apply; defaults to the real one, so tests can pass a tiny throwaway model
234     node_types: tuple[NodeType, ...] = NODE_TYPES,
#▸ L235: the database name; Community has exactly one, called "neo4j"
235     database: str = "neo4j",
#▸ L236: it returns a report of what happened
236 ) -> SchemaReport:
#▸ L237: docstring, first line: create what is missing, then verify; safe to re-run
237     """Create every missing constraint, then verify the result. Safe to re-run.
#▸ L239: and it raises if, afterwards, an expected constraint is still not in force
239     Raises SchemaMismatch if, afterwards, an expected constraint is still not in force.
#▸ L241: names of constraints this call made
241     created: list[str] = []
#▸ L242: names whose statement did nothing
242     present: list[str] = []
#▸ L243-244: the reason for auto-commit: schema changes after a write in one transaction are refused
243     # Each statement runs on its own (auto-commit): Neo4j forbids schema changes in a
244     # transaction that has already written (observed: ForbiddenDueToTransactionType).
#▸ L245: open one session, and close it automatically at the end of the block
245     with driver.session(database=database) as session:
#▸ L246: one statement per node type in the model
246         for node in node_types:
#▸ L247: run the DDL, wait for it to finish (consume), and take the server's change counters from the summary
247             counters = session.run(constraint_statement(node)).consume().counters
#▸ L248-249: the comment: 1 means new, 0 means a no-op, and a no-op is only trusted after the definition check below
248             # constraints_added is 1 for a new constraint and 0 for a no-op; a no-op is
249             # only trusted after check_schema compares the definitions below.
#▸ L250: put the name in `created` if the counter is non-zero, else in `present`
250             (created if counters.constraints_added else present).append(node.constraint_name)
#▸ L251: wait until every index is ONLINE, for at most 60 seconds ($seconds is a parameter: a value)
251         session.run("CALL db.awaitIndexes($seconds)", seconds=AWAIT_INDEXES_SECONDS).consume()
#▸ L253: now compare what the database enforces with the model (C8)
253     status = check_schema(driver, node_types, database)
#▸ L254: any expected rule missing or wrong?
254     if not status.ok:
#▸ L255: then raise, listing every problem at once, separated by semicolons
255         raise SchemaMismatch("; ".join(status.problems))
#▸ L256: otherwise return what was created and what was already present
256     return SchemaReport(created=tuple(created), present=tuple(present))
```
The error type it raises:
```python
#▸ L188: a RuntimeError subclass, so callers can catch exactly this failure
188 class SchemaMismatch(RuntimeError):
#▸ L189: docstring: the constraints differ from the model in a way IF NOT EXISTS hides
189     """The database's constraints differ from this model in a way `IF NOT EXISTS` hides.
#▸ L191-193: the reason, in the file's own words: a same-name, different-definition constraint is a silent no-op
191     `CREATE CONSTRAINT ... IF NOT EXISTS` is a silent no-op when a constraint of the
192     same name exists with a different definition (observed on the live server), so
193     creating alone would report success while the expected rule is not enforced.
```
And two tests: the same scenario on a fake **and** the real server (C11), and a fake-only check of the order of steps:
```python
#▸ L290: one test, run twice: `schema_db` is the fake once and the real server once
290 def test_first_apply_creates_everything_and_the_second_creates_nothing(schema_db, scratch):
#▸ L291: apply the throwaway model
291     first = apply_schema(schema_db, scratch)
#▸ L292: both constraints must be reported as created
292     assert first.created == tuple(n.constraint_name for n in scratch)
#▸ L293: and none as already present
293     assert first.present == ()
#▸ L295: apply it again
295     second = apply_schema(schema_db, scratch)
#▸ L296-297: the reason: the counts come from the server's own counters
296     # Reported from the server's own counters, not from a guess: the second run really
297     # was a no-op, so re-running the setup is safe.
#▸ L298: nothing may be created the second time
298     assert second.created == ()
#▸ L299: both must now be reported as present
299     assert second.present == tuple(n.constraint_name for n in scratch)
...
#▸ L366: fake-only: check the ORDER of the statements
366 def test_apply_waits_for_indexes_before_it_verifies(scratch):
#▸ L368: apply the model to the recording fake
368     apply_schema(driver, scratch)
#▸ L370-371: a helper: the indexes of recorded statements that start with a given prefix
370     def position(prefix: str) -> list[int]:
371         return [i for i, s in enumerate(driver.statements) if s.startswith(prefix)]
#▸ L373: where the CREATE statements are
373     creates = position("CREATE CONSTRAINT")
#▸ L374: where the index wait is; the one-element unpack also asserts there is exactly one
374     (wait,) = position("CALL db.awaitIndexes")
#▸ L375: where the verification query is (exactly one)
375     (verify,) = position("SHOW CONSTRAINTS")
#▸ L376: the reason: the wait belongs after every CREATE and before verification
376     # Created is not online: the wait comes after every CREATE and before verification.
#▸ L377: every model entry was created, and the order is CREATE... then wait, then verify
377     assert len(creates) == len(scratch) and max(creates) < wait < verify
```
**How it fits.** C3's `constraint_statement` supplies the DDL, C6 validates its names, C8 does the verification, and C10's CLI
calls this and reports the counts.

### 4. Benefits
Safe to re-run, honest about what it did, and impossible to fool with a same-named constraint.

- **What breaks:** trusting `IF NOT EXISTS` alone; running DDL inside a write transaction; returning before indexes are online
  on a graph with data; auto-dropping a mismatched constraint (which would silently remove a rule someone chose).
- **Docs:** [Cypher manual: create constraints](https://neo4j.com/docs/cypher-manual/current/constraints/managing-constraints/) ·
  [Operations manual: `db.awaitIndexes`](https://neo4j.com/docs/operations-manual/current/procedures/) ·
  [Python driver: result summary counters](https://neo4j.com/docs/api/python-driver/current/api.html#neo4j.SummaryCounters)

### Micro-exercises
1. In the demo, print `first.created` and `second.present` to see the actual constraint names.
2. Make the model contain three types and one already-existing constraint. What are `created` and `present`?
3. Remove the `db.awaitIndexes` call in a scratch copy. Which test fails, and why does it need a *fake* to notice?
4. Plant the impostor with a *different* type (a `NODE KEY` is refused here, so try a second uniqueness constraint on another
   property). What does the message say?
5. Why does `apply_schema` accept a `database` argument if Community has only one database?

**Internals.** `Result.consume()` drains the result and returns a `ResultSummary`; its `counters` object (`constraints_added`,
`nodes_created`, ...) is filled in by the server. The `with driver.session(...)` block returns the connection to the pool on
exit.
**Checkpoint:** name the three situations in which `IF NOT EXISTS` returns `constraints_added = 0`, and say which one
`apply_schema` cannot tell apart from the others without step 4.

---


## C8 — `check_schema`: verify by definition

### 1. Definition + analogy

**What it is.** `check_schema` is a **read-only** comparison between the constraints the database enforces and the ones the
model expects. It answers "is the schema in force?" without changing anything. It reads `SHOW CONSTRAINTS` and compares
by **definition**, not by name.

A constraint's *definition* is the tuple `(type, entity type, labels, properties)`, for example
`('UNIQUENESS', 'NODE', ('Contact',), ('name', 'organization'))`. The name is just a label someone gave it.

| What is found | Result | Why |
|---|---|---|
| a constraint with the expected definition (any name) | **in force** | the rule is enforced, whatever it is called |
| nothing with that definition, and nothing with that name | **problem:** `missing <name>: ...` | the rule isn't there |
| nothing with that definition, but the **name** is taken | **problem:** `<name> exists but is ... on X(other), not ... on X(name)` | an impostor: the name is used for a different rule |
| constraints the model does not mention | listed as **`unexpected`**, not a failure | a developer's experiment must not break the check |

`SchemaStatus` carries `problems` (one sentence per expected constraint that is not in force), `unexpected`, and an `ok`
property that is true when there are no problems.

**Key terms.** *Definition vs name*. *Read-only check*. *Informational vs failing findings*. *`SHOW CONSTRAINTS ... YIELD`*:
the command that lists constraints, with `YIELD` choosing columns.

**Limits.** It only understands node **uniqueness** constraints (the only kind the model defines). A different kind of
constraint under the expected name is *described* in the problem text (`NODE KEY on ...`), not compared further. It checks
that a rule exists, not that the *data* obeys it (the server guarantees that when a constraint is created).

**Analogy.** An inspector with a checklist: "smoke alarm in room 3, wired to the panel". They don't accept a sticker
saying *SMOKE ALARM*; they press the test button. And if they find a *carbon-monoxide* detector nobody listed, they
**note it in the margin** but don't fail the building for it.

### For a 14-year-old

You're checking a treasure map against the real island. The map says: "a red flag on the north beach, a rope bridge, and a
blue door". You walk the island with a tick-list.

- Red flag there? ✔. It doesn't matter *who* planted it or what name they gave it, the **flag is there**.
- Rope bridge? ✘ **Missing.** Write it on the list.
- Blue door? There's a door, but it's **green**. So the map's blue door is *not* there. Write "there's a green door where a blue one
  should be".
- A random scarecrow the map doesn't mention? Write it in the margin, but that's not a mistake in the map.

### 2. Why (vs the traditional approach)
Schema-drift checks in SQL tools (`alembic check`, Liquibase `diff`) do the same thing: compare an expected schema with the real
one. The key design choice here is matching on **meaning** rather than **name**: two constraints with different names and the
same definition enforce exactly the same thing, and a constraint with the *right name and the wrong definition* enforces the
*wrong* thing. Both cases are exactly the ones `IF NOT EXISTS` hides (C7).

### 3a. Minimal demo — missing, in force, foreign rule, same rule under another name, and an impostor
Live Neo4j, throwaway labels, self-cleaning.

`c8_check_schema.py`
```python
# C8: check_schema -- compare what the database enforces with what the model expects, by DEFINITION.
# Live Neo4j on throwaway labels, self-cleaning.
import uuid                                                              # to make labels no other data can have

from src.graph.schema import NodeType, apply_schema, check_schema       # the code under study
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
single = NodeType(f"Demo{suffix}", ("name",), "domain", "demo")          # a one-property key
composite = NodeType(f"DemoC{suffix}", ("name", "organization"), "domain", "demo")  # a two-property key
types = (single, composite)                                              # the demo's tiny "model"
alias, extra = f"demo_alias_{suffix}", f"demo_extra_{suffix}"            # names for two hand-made constraints


def ddl(session, name: str, label: str, prop: str) -> None:              # helper: create a one-property uniqueness constraint by hand
    session.run(f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE").consume()  # plain DDL


with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        before = check_schema(driver, types)                             # 1. nothing applied yet
        print("1. before apply :", "ok" if before.ok else "NOT ok")      # the model is not in force
        for problem in before.problems:                                  # one sentence per missing constraint
            print("     -", problem)                                      # names the missing constraint and its definition

        apply_schema(driver, types)                                      # apply the tiny model
        row = session.run(                                               # 2. what the server reports for one constraint
            "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties WHERE name = $n RETURN *",  # the five columns check_schema reads
            n=composite.constraint_name,                                 # look up the composite one
        ).single()                                                       # exactly one row
        print("2. one SHOW CONSTRAINTS row:", row.data())                # type, entityType, labels, properties (in creation order)
        print("   after apply  :", "ok" if check_schema(driver, types).ok else "NOT ok")  # in force

        ddl(session, extra, single.label, "other")                       # 3. an unrelated rule that someone added by hand
        status = check_schema(driver, types)                             # check again
        print("3. foreign rule :", "ok" if status.ok else "NOT ok", "| reported as unexpected:", extra in status.unexpected)  # informational only
        real = [n for n in status.unexpected if not n.startswith("demo")]  # this demo's model is tiny, so the 8 REAL constraints also look foreign
        print("   (the", len(real), "real project constraints are listed as unexpected too: this model only has 2 types)")  # explains the long list

        session.run(f"DROP CONSTRAINT {single.constraint_name}").consume()  # 4. remove the real rule and add the same RULE under another name
        ddl(session, alias, single.label, "name")                        # equivalent definition, different name
        status = check_schema(driver, types)                             # check again
        print("4. same rule, other name:", "ok" if status.ok else "NOT ok", "| alias flagged as foreign:", alias in status.unexpected)  # matched by definition

        session.run(f"DROP CONSTRAINT {alias}").consume()                # 5. remove the alias and plant an IMPOSTOR under the expected name
        ddl(session, single.constraint_name, single.label, "legal_name")  # right name, wrong property
        status = check_schema(driver, types)                             # check again
        print("5. impostor     :", "ok" if status.ok else "NOT ok")      # the rule is not really in force
        for problem in status.problems:                                  # the explanation
            print("     -", problem)                                     # names what exists and what was expected
    finally:                                                             # always clean up, even after an error above
        for name in (single.constraint_name, composite.constraint_name, alias, extra):  # every constraint the demo may have made
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop it if it exists
        print("6. cleanup      : demo constraints dropped (no nodes were ever created)")  # nothing left behind
```
Real output:
```
1. before apply : NOT ok
     - missing democ1f4d062_name_unique: UNIQUENESS on Democ1f4d062(name)
     - missing demo_cc1f4d062_name_organization_unique: UNIQUENESS on DemoCc1f4d062(name, organization)
2. one SHOW CONSTRAINTS row: {'name': 'demo_cc1f4d062_name_organization_unique', 'type': 'UNIQUENESS', 'entityType': 'NODE', 'labelsOrTypes': ['DemoCc1f4d062'], 'properties': ['name', 'organization']}
   after apply  : ok
3. foreign rule : ok | reported as unexpected: True
   (the 8 real project constraints are listed as unexpected too: this model only has 2 types)
4. same rule, other name: ok | alias flagged as foreign: False
5. impostor     : NOT ok
     - democ1f4d062_name_unique exists but is UNIQUENESS on Democ1f4d062(legal_name), not UNIQUENESS on Democ1f4d062(name); drop it to let the model recreate it
6. cleanup      : demo constraints dropped (no nodes were ever created)
```

**How to read it.**
- **1:** before anything is applied, two problems, each naming the missing constraint **and** what it should protect.
- **2:** one real `SHOW CONSTRAINTS` row: the five fields `check_schema` reads. Note that `properties` keeps the order the
  constraint was created with.
- **3:** a hand-made constraint the model doesn't know is reported as `unexpected` but the check stays `ok`. The 8 real project
  constraints are also "unexpected" here only because this demo's model has 2 types; in the real run they *are* the model.
- **4:** the same rule under another name still counts (`ok`), and is **not** flagged as foreign: matching is by definition.
- **5:** an impostor (right name, wrong property `legal_name`) makes the check fail with a sentence naming both the found and the
  wanted definition, and telling you to drop it.

### 3b. Exact project code — `check_schema` and `SchemaStatus` (annotated copy of `src/graph/schema.py`)
```python
#▸ L203: make the status an immutable record
203 @dataclass(frozen=True)
#▸ L204: what check_schema returns
204 class SchemaStatus:
#▸ L205: a comment: problems are one sentence per expected constraint that is not in force
205     # One sentence per expected constraint that is not in force.
#▸ L206: the failing findings; empty by default
206     problems: tuple[str, ...] = ()
#▸ L207-208: a comment: foreign constraints are informational, so an experiment cannot fail the check
207     # Constraints in the database that this model does not define. Informational: a
208     # developer's experiment should not make the schema check fail.
#▸ L209: the names of constraints the model does not define; empty by default
209     unexpected: tuple[str, ...] = ()
#▸ L211: make ok read like an attribute
211     @property
#▸ L212: true or false
212     def ok(self) -> bool:
#▸ L213: ok exactly when there are no problems
213         return not self.problems
```
```python
#▸ L259: compare the database with the model (read-only)
259 def check_schema(
#▸ L260: any driver-shaped object
260     driver: GraphDriver,
#▸ L261: the model to check; defaults to the real one
261     node_types: tuple[NodeType, ...] = NODE_TYPES,
#▸ L262: the database to inspect
262     database: str = "neo4j",
#▸ L263: it returns a status object
263 ) -> SchemaStatus:
#▸ L264-268: docstring: definitions, not names, because the same rule under another name enforces the same thing
264     """Compare the database's constraints with the model. Read-only.
265
266     Constraints are matched by definition, not by name: the same rule under another
267     name enforces exactly the same thing (also a silent no-op for IF NOT EXISTS).
268     """
#▸ L269: open a session and close it automatically
269     with driver.session(database=database) as session:
#▸ L270: read every row into a list, so the session can close before the analysis
270         rows = list(
#▸ L271: send one read-only query
271             session.run(
#▸ L272-273: list constraints with exactly the five columns needed: name, type, entityType, labelsOrTypes, properties
272                 "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties "
273                 "RETURN name, type, entityType, labelsOrTypes, properties"
#▸ L277: a dictionary from definition to name...
277     by_definition = {
#▸ L278-282: ...where the definition is a tuple of type, entity type, labels and properties (lists become tuples so they can be dictionary keys)
278         (
279             row["type"],
280             row["entityType"],
281             tuple(row["labelsOrTypes"]),
282             tuple(row["properties"]),
#▸ L283: the value stored under each definition is the constraint's name
283         ): row["name"]
#▸ L286: a second dictionary from name to the whole row, for describing an impostor
286     by_name = {row["name"]: row for row in rows}
#▸ L288: two accumulators: problem sentences, and the set of definitions the model expects
288     problems, expected_definitions = [], set()
#▸ L289: for each node type the model expects...
289     for node in node_types:
#▸ L290: ...write down the definition it should have: a uniqueness constraint on this label and these properties
290         definition = ("UNIQUENESS", "NODE", (node.label,), node.key)
#▸ L291: remember it, so anything else can later be called "unexpected"
291         expected_definitions.add(definition)
#▸ L292: is that rule enforced, under any name?
292         if definition in by_definition:
#▸ L293: then this node type is fine; go to the next one
293             continue
#▸ L294: otherwise, is the EXPECTED NAME used by some other constraint?
294         found = by_name.get(node.constraint_name)
#▸ L295: build the human-readable form of the wanted rule for the message
295         wanted = f"UNIQUENESS on {node.label}({', '.join(node.key)})"
#▸ L296: nothing uses that name either...
296         if found is None:
#▸ L297: ...so it is simply missing
297             problems.append(f"missing {node.constraint_name}: {wanted}")
#▸ L298: the name is taken by something else...
298         else:
#▸ L299-303: ...so describe the impostor (its type, label and properties), the wanted rule, and the remedy: drop it
299             problems.append(
300                 f"{node.constraint_name} exists but is {found['type']} on "
301                 f"{'/'.join(found['labelsOrTypes'])}({', '.join(found['properties'])}), "
302                 f"not {wanted}; drop it to let the model recreate it"
303             )
#▸ L305: list, alphabetically, every constraint whose definition the model did not ask for
305     unexpected = sorted(
#▸ L308: hand back the problems and the foreign constraints as tuples
308     return SchemaStatus(problems=tuple(problems), unexpected=tuple(unexpected))
```
Three tests, each run on **both** a fake and the real server (C11), plus one that only the fake can stage:
```python
#▸ L313: the impostor scenario, on the fake and the real server
313 def test_same_name_with_a_different_definition_is_reported_not_hidden(schema_db, scratch):
#▸ L315: the setup: a constraint that already uses the expected NAME but protects another property
315     # A constraint that already uses the expected NAME, but protects another property.
#▸ L316: plant the impostor with plain DDL
316     _run(
#▸ L322: the point: the silent no-op would report success here
322     # IF NOT EXISTS would report success here. Verifying by definition does not.
#▸ L323: applying must raise
323     with pytest.raises(SchemaMismatch) as error:
#▸ L325: the message names the constraint
325     assert single.constraint_name in str(error.value)
#▸ L326: it shows what was found (the wrong property)
326     assert f"{single.label}(other)" in str(error.value)
#▸ L327: and what was wanted (the right one)
327     assert f"{single.label}(name)" in str(error.value)
...
#▸ L330: the alias scenario
330 def test_the_same_rule_under_another_name_counts_as_present(schema_db, scratch):
#▸ L338: apply the model: the rule already holds under another name
338     report = apply_schema(schema_db, (single,))
#▸ L342: so nothing is created
342     assert report.created == ()
#▸ L344: and the model is satisfied, with the alias NOT called foreign
344     assert status.ok and alias not in status.unexpected
...
#▸ L347: the foreign-constraint scenario
347 def test_unrelated_constraints_are_reported_but_do_not_fail_the_check(schema_db, scratch):
#▸ L359: a hand-made extra constraint must not break the check
359     assert status.ok, "an experiment in the Browser must not break the schema check"
#▸ L360: but it must be reported
360     assert extra in status.unexpected
...
#▸ L387: fake-only: a NODE KEY cannot be created on Community, so the fake stages it
387 def test_a_constraint_of_another_type_under_the_expected_name_is_described(scratch):
#▸ L390-395: plant a NODE KEY under the expected name
390     driver.constraints[single.constraint_name] = (
391         "NODE KEY",
392         "NODE",
393         (single.label,),
394         ("name", "id"),
395     )
#▸ L397: check it
397     status = check_schema(driver, (single,))
#▸ L399: it does not satisfy a UNIQUENESS expectation
399     assert not status.ok
#▸ L400: and the message says what it actually is and how to fix it
400     assert "NODE KEY on" in status.problems[0] and "drop it" in status.problems[0]
```
**How it fits.** `apply_schema` (C7) calls this after creating; the CLI's `--check` (C10) calls it alone.

### 4. Benefits
The check cannot be satisfied by a look-alike, it explains itself, and it never fails on someone else's experiment.

- **What breaks:** comparing by name only (an impostor passes); failing on every foreign constraint (developers' experiments
  break CI); comparing property lists as *sets* when their order matters for a composite key.
- **Docs:** [Cypher manual: `SHOW CONSTRAINTS`](https://neo4j.com/docs/cypher-manual/current/constraints/managing-constraints/) ·
  [Python `sorted`](https://docs.python.org/3/library/functions.html#sorted) ·
  [Alembic: autogenerate and `check`](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)

### Micro-exercises
1. In the demo, plant a constraint on `(name, organization)` in the *opposite* order `(organization, name)`. Is the composite
   type satisfied? What does that say about property order?
2. Print `by_definition` for the real database (write a three-line script) and find the composite entry.
3. Make the model expect a `Company` constraint on `legal_name` too. What does `check_schema` say on the real database?
4. Why does `check_schema` build `rows = list(...)` before leaving the `with`? (Hint: what happens to an unconsumed result when the
   session closes?)
5. Add a "warning" level between `problems` and `unexpected`. Which findings belong there?

**Internals.** `SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties` returns lists for `labelsOrTypes` and
`properties` (a constraint can span several); Python lists are unhashable, so they are converted to tuples to be dictionary keys.
A `dict` preserves insertion order, and `sorted(...)` gives a stable, testable order for `unexpected`.
**Checkpoint:** explain what makes a constraint "in force" for `check_schema`, and why an impostor is a *problem* while a foreign
constraint is only a *note*.

---


## C9 — What Community Edition cannot enforce, so the loader must

### 1. Definition + analogy

**What it is.** Neo4j comes in editions. **Community** (what this project runs) supports **uniqueness** constraints. The
other constraint kinds need **Enterprise**:

| Constraint | Says | Community 5.26 |
|---|---|---|
| **Uniqueness** | no two nodes share this key | ✅ supported (single and composite) |
| **Property existence** (`IS NOT NULL`) | every node with this label has this property | ❌ refused: "requires Neo4j Enterprise Edition" |
| **Property type** (`IS :: STRING`) | the property is of this type | ❌ refused (same message) |
| **Node key** | unique **and** present | ❌ refused (same message) |

So the database can promise "**no duplicate keys**" but not "**every node has a key**". Three more behaviours, all observed in
the demo, complete the picture:

1. A uniqueness constraint **ignores a node that lacks a key property**. Two `Ann` nodes with no organisation were both accepted
   by the `(name, organization)` constraint, even though they are obviously duplicates.
2. **`MERGE` refuses a null key** (`SemanticError`: "Cannot merge the following node because of null property value").
3. **`CREATE` does not.** `CREATE (n:L) SET n.name = $v` with `$v = null` made a node with **no properties at all**.

**The consequence is a rule for the loader:** *always `MERGE`, never `CREATE`, and supply the complete key.* `MERGE` then acts as
the guard the database cannot be (it raises on a missing key part), and the constraint catches the duplicates.

**Key terms.** *Edition* (Community vs Enterprise). *Existence / type / node-key constraint*. *Null*: a missing property (in
Neo4j, setting a property to null removes it). *Keyless node*: a node with no value for its identity key.

**Limits.** This relies on the loader always using `MERGE` with complete keys. Nothing in the database stops someone running a
`CREATE` from the Browser. The live tests pin each observation, so an edition change fails a test instead of going unnoticed.

**Analogy.** A club whose rule says "no two members may have the same membership number". It has no rule that says "everyone
must have a number". A person who arrives with **no number at all** isn't a duplicate of anyone, so they're let in, and so is the next
numberless person. The only defence is the person at the door (the loader) who says "no number, no entry" (`MERGE` refuses).

### For a 14-year-old

The club rule is "no two members with the **same** nickname". Now two kids turn up who **don't have a nickname** and the
club secretary looks at his list: "is there already someone with *no* nickname? That's not a nickname, so the rule doesn't
apply." He lets both in. You end up with two blank name-tags, and nobody can tell them apart.

The fix isn't a better rule (the club can't afford that fancy rule: it's a paid-for feature). The fix is to train the **door
person**: "Never let anyone in without a nickname written down." In our project the door person is the loader, and its motto is
"use MERGE and fill in every part of the key."

### 2. Why (vs the traditional approach)
In SQL, `PRIMARY KEY` means unique **and** `NOT NULL`, so the database is the guard. In Neo4j Community the guard has to be
in the application. The alternatives were **Enterprise** (node-key and existence constraints, but a licence and not what is pinned
and running) or a **periodic scan** for keyless nodes (finds problems after the fact instead of preventing them). The project
chose the loader guard plus tests that pin the server's behaviour.

### 3a. Minimal demo — which constraints are refused, the null hole, and MERGE vs CREATE with a null
Live Neo4j, throwaway labels, self-cleaning.

`c9_community_limits.py`
```python
# C9: what Neo4j Community Edition cannot enforce, so the loader must. Live Neo4j on throwaway labels, self-cleaning.
import re                                                                # to pull the key phrase out of an error message
import uuid                                                              # to make labels no other data can have

from neo4j.exceptions import ClientError, ConstraintError, Neo4jError    # error classes: any client error, duplicate key, any server error

from src.graph.schema import NodeType, constraint_statement             # the project's model class and DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
label, keyless = f"Demo{suffix}", f"DemoK{suffix}"                       # one label with a composite key, one for the null experiment
composite = NodeType(label, ("name", "organization"), "domain", "demo")  # a two-property key, like Contact
names = [composite.constraint_name, f"demo_nn_{suffix}", f"demo_ty_{suffix}", f"demo_nk_{suffix}", f"demo_kl_{suffix}"]  # constraints to drop at the end

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        edition = session.run("CALL dbms.components() YIELD edition RETURN edition").single()["edition"]  # 1. which edition is this?
        print("1. edition                     :", edition)               # community

        for tag, rule in [("NOT NULL", "n.name IS NOT NULL"), ("TYPE", "n.name IS :: STRING"), ("NODE KEY", "(n.name, n.organization) IS NODE KEY")]:  # the three refused kinds
            key = {"NOT NULL": names[1], "TYPE": names[2], "NODE KEY": names[3]}[tag]  # a constraint name for each attempt
            try:                                                         # the server may refuse
                session.run(f"CREATE CONSTRAINT {key} IF NOT EXISTS FOR (n:{label}) REQUIRE {rule}").consume()  # try to create it
                print(f"2. {tag:<9} constraint      : accepted (this would mean Enterprise!)")  # not expected here
            except Neo4jError as exc:                                    # refused
                why = re.search(r"([A-Za-z ]+requires Neo4j Enterprise Edition)", str(exc))  # the sentence that explains it
                print(f"2. {tag:<9} constraint      :", type(exc).__name__, "|", why[1].strip() if why else str(exc)[:80])  # class and reason

        session.run(constraint_statement(composite)).consume()           # 3. the composite UNIQUENESS constraint IS allowed
        session.run(f"CREATE (:{label} {{name: 'Bob', organization: 'X'}})").consume()  # a first Bob at X
        try:                                                             # the same pair again
            session.run(f"CREATE (:{label} {{name: 'Bob', organization: 'X'}})").consume()  # a duplicate pair
        except ConstraintError:                                          # refused, as designed
            print("3. duplicate (name, org) pair  : refused by the constraint")  # the rule works when both parts are present

        session.run(f"CREATE (:{label} {{name: 'Ann'}}), (:{label} {{name: 'Ann'}})").consume()  # 4. the same name twice, with NO organisation
        anns = session.run(f"MATCH (n:{label} {{name: 'Ann'}}) RETURN count(n) AS c").single()["c"]  # how many Anns exist
        print("4. two 'Ann' with no org       :", anns, "nodes accepted (a missing key part slips past the constraint)")  # the hole

        for title, query in [("MERGE null name", f"MERGE (:{keyless} {{name: $v}})"), ("MERGE null org ", f"MERGE (:{label} {{name: 'Ann', organization: $v}})")]:  # 5. MERGE with a null key part
            try:                                                         # MERGE should refuse
                session.run(query, v=None).consume()                     # try it with None (null)
                print(f"5. {title}             : accepted")              # not expected
            except ClientError as exc:                                   # refused
                print(f"5. {title}             :", exc.code.split(".")[-1], "(MERGE checks the key before writing)")  # SemanticError

        session.run(f"CREATE (n:{keyless}) SET n.name = $v", v=None).consume()  # 6. CREATE has no such check: a node with no name at all
        props = session.run(f"MATCH (n:{keyless}) RETURN keys(n) AS k").single()["k"]  # which properties did it end up with?
        print("6. CREATE with a null name     : node created, properties =", props)  # empty: a keyless node
    finally:                                                             # always clean up, even after an error above
        for name in names:                                               # every constraint the demo may have made
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop it if it exists
        removed = session.run(f"MATCH (n) WHERE n:{label} OR n:{keyless} DETACH DELETE n RETURN count(n) AS c").single()["c"]  # delete demo nodes
        print("7. cleanup                     :", removed, "nodes deleted; constraints dropped")  # nothing left behind
```
Real output:
```
1. edition                     : community
2. NOT NULL  constraint      : DatabaseError | Property existence constraint requires Neo4j Enterprise Edition
2. TYPE      constraint      : DatabaseError | Property type constraint requires Neo4j Enterprise Edition
2. NODE KEY  constraint      : DatabaseError | Node Key constraint requires Neo4j Enterprise Edition
3. duplicate (name, org) pair  : refused by the constraint
4. two 'Ann' with no org       : 2 nodes accepted (a missing key part slips past the constraint)
5. MERGE null name             : SemanticError (MERGE checks the key before writing)
5. MERGE null org              : SemanticError (MERGE checks the key before writing)
6. CREATE with a null name     : node created, properties = []
7. cleanup                     : 4 nodes deleted; constraints dropped
```

**How to read it.**
- **1:** the server is `community`.
- **2:** all three non-uniqueness kinds are refused with `DatabaseError`, each saying it requires Enterprise Edition.
- **3:** the composite **uniqueness** constraint works: an identical `(name, org)` pair is refused.
- **4:** **the hole:** two `Ann` nodes with no organisation were accepted. That is what the loader's complete-key rule prevents.
- **5:** `MERGE` with a null key part fails in both forms (single key and composite key): it checks *before* writing.
- **6:** `CREATE ... SET n.name = null` made a node with `properties = []`. The database would let a keyless node in.
- **7:** 4 nodes deleted (Bob, two Anns, the keyless one) and the constraints dropped.

### 3b. Exact project code — the rule and the tests that pin it (annotated copies)
The model records the rule where a key is defined (`src/graph/schema.py`):
```python
#▸ L43-45: every key part must be present; Community cannot enforce that (D-2026-09-20-05), so the loader must MERGE with complete keys
43      # The properties that together identify one node. Every one must be set on every
44      # node: Community Edition cannot enforce that (decision D-2026-09-20-05), so the
45      # loader has to, by MERGEing with complete keys.
```
The test that pins "Community cannot enforce required properties" (`tests/graph/test_schema.py`):
```python
#▸ L578: needs a running service
578 @pytest.mark.live
#▸ L579: skipped automatically, with a reason, if Neo4j is down
579 @live
#▸ L580: the test; `live_db` is a real driver that sweeps scratch leftovers before and after (C11)
580 def test_live_community_edition_cannot_enforce_required_properties(live_db, scratch):
#▸ L581: creating the constraint must fail with a DatabaseError
581     with pytest.raises(DatabaseError) as error:
#▸ L582: send the DDL to the real server
582         _run(
#▸ L584: a NOT NULL constraint on a throwaway label
584             f"CREATE CONSTRAINT scratch_notnull_{uuid.uuid4().hex[:8]} IF NOT EXISTS "
#▸ L585: REQUIRE n.name IS NOT NULL: the kind Community refuses
585             f"FOR (n:{scratch[0].label}) REQUIRE n.name IS NOT NULL",
#▸ L587-588: the reason: if it succeeds the server is Enterprise, and the loader-side validation decision should be revisited
587     # If this ever succeeds the server is Enterprise, and the loader-side validation
588     # decision (D-2026-09-20-05) should be revisited.
#▸ L589: and the failure must be for that reason, not some other error
589     assert "requires Neo4j Enterprise Edition" in str(error.value)
```
The test that pins the `MERGE`-vs-`CREATE` behaviour and the null hole:
```python
#▸ L592: needs a running service
592 @pytest.mark.live
#▸ L593: skipped automatically, with a reason, if Neo4j is down
593 @live
#▸ L594: the test, again on a real driver and throwaway labels
594 def test_live_merge_refuses_a_null_key_but_create_silently_accepts_it(live_db, scratch):
#▸ L595: the two throwaway node types: a single key and a composite key
595     single, composite = scratch
#▸ L596: apply their uniqueness constraints
596     apply_schema(live_db, scratch)
#▸ L598: MERGE with a null key must raise a client error...
598     with pytest.raises(ClientError) as merge_error:
#▸ L599: ...try it, passing None (null) for the key
599         _run(live_db, f"MERGE (:{single.label} {{name: $value}})", value=None)
#▸ L600: ...and the error must be the SemanticError we observed
600     assert merge_error.value.code == "Neo.ClientError.Statement.SemanticError"
#▸ L602-604: the reason, in the file's own words: CREATE has no guard and a uniqueness constraint ignores keyless nodes
602     # CREATE has no such guard, and a uniqueness constraint ignores a node that lacks a
603     # key property. Two keyless "duplicates" therefore coexist: the loader must use
604     # MERGE with complete keys, because the database will not catch the gap.
#▸ L605: two nodes with the same name and NO organisation, made with CREATE
605     keyless = f"CREATE (:{composite.label} {{name: 'Ann'}}), (:{composite.label} {{name: 'Ann'}})"
#▸ L606: they must both be accepted: the test documents the hole rather than hiding it
606     _run(live_db, keyless)
```
The same facts, for humans, in `docs/architecture.md` (exact copy of the table):

| Rule | Enforced by |
|---|---|
| One node per identity | uniqueness constraints (8, created by `apply_schema`); each has a backing range index, so `MERGE` on the key is an index lookup |
| A key property is present | **not** the database (existence constraints need Enterprise): the loader must `MERGE` with complete keys. `MERGE` raises on a null key; `CREATE` does not |
| Two people with the same name are different people | the `Employee` / `Contact` split, and `organization` inside the `Contact` key |
| Relationship endpoints have the right labels | **not** the database: `RELATIONSHIP_TYPES` is the reference, and the loader must use it |

**How it fits.** This is the contract the loader (built with extraction) has to honour. Two rows of that table are *not*
database rules: presence of keys and relationship endpoints.

### 4. Benefits
The gap in the database is known, written down, and pinned by tests, so the next step is designed around it.

- **What breaks:** a loader using `CREATE`; a `MERGE` whose key part can be null being caught *late* (it raises, which is good, but
  the load has already started); assuming a composite constraint covers a missing part; assuming relationships are type-checked.
- **Docs:** [Neo4j editions](https://neo4j.com/docs/operations-manual/current/introduction/#_neo4j_editions) ·
  [Cypher manual: constraints](https://neo4j.com/docs/cypher-manual/current/constraints/) ·
  [Cypher manual: `MERGE` and null](https://neo4j.com/docs/cypher-manual/current/clauses/merge/)

### Micro-exercises
1. In the demo, try `MERGE (:L {name: 'Ann', organization: ''})` (an *empty string*). Does it succeed? Is that a "complete key"?
2. Write a Cypher query that finds keyless `Contact` nodes. What would you do with them?
3. Find out what `IS :: STRING` would give you that uniqueness cannot (think of a numeric `doc_id`).
4. Explain why the loader guard is stronger with `MERGE` than with `CREATE ... WHERE NOT EXISTS`.
5. List two other places in the stack where a guard lives in the application because the database can't provide it.

**Internals.** Neo4j does not store a "null" property: setting a property to null *removes* it, which is why `keys(n)` is empty
for the keyless node. A uniqueness constraint's backing index has no entry for a node that lacks the property, so such nodes are
invisible to it.
**Checkpoint:** state what a uniqueness constraint *does not* prevent on Community, and name the two Cypher facts that make
`MERGE` with complete keys the right loader rule.

---


## C10 — The CLI: exit codes, `--check`, and reusing the readiness check

### 1. Definition + analogy

**What it is.** `python -m src.graph.schema` is the human- and CI-facing entry point. It wraps `apply_schema` and `check_schema` in
a command with **three jobs**: choose a mode, say what happened in one line, and return an **exit code** that a shell, a Makefile or a
CI step can act on.

| Command | Does | Writes? | Exit 0 when | Exit 1 when |
|---|---|---|---|---|
| `python -m src.graph.schema` | apply, then verify | **yes** (creates missing constraints) | every constraint is in force | not ready, or `SchemaMismatch` |
| `python -m src.graph.schema --check` | verify only | **never** | every constraint is in force | not ready, or any constraint missing/wrong |
| a flag that doesn't exist (e.g. `--password`) | argparse refuses | no | n/a | **exit 2** (usage error) |

Design points, each visible in the code:
- **It reuses the P4-M1 readiness check first** (`health_check`). A wrong password or a stopped container therefore gets its specific,
  human explanation ("authentication failed... NEO4J_AUTH only applies when the volume is first created", or "cannot reach Neo4j
  ... is the container running?") instead of a stack trace.
- **No `--password` flag**, so a secret never lands in shell history. It reads `.env` (via `settings`) only.
- **`--check` never writes.** It is the safe command for CI and for "did someone change my constraints?".
- **The return value is the exit code**: `main()` returns an `int`, and `sys.exit(main())` at the bottom hands it to the operating
  system. Because `main()` returns instead of exiting, tests can call it directly.

**Key terms.** *Exit code*: the number a process returns to its caller (`0` = success by convention). *`argparse`*: the standard
command-line parser. *`python -m`*: run a module as a script. *`if __name__ == "__main__"`*: run only when executed, not when imported.

**Limits.** The output is for humans (one summary line, plus a list on failure); it is not machine-readable JSON. Exit code `1`
covers several different failures; the message says which.

**Analogy.** A car's dashboard: a single light (the exit code) that says "OK" or "not OK", plus a small message screen (the printed
line) that says why. A mechanic (CI) reads the light; a driver reads the message.

### For a 14-year-old

When you finish a test at school you get a **grade** (a number) and a **comment** ("you forgot to show your working"). The teacher's
computer only looks at the grade: 0 means "fine", anything else means "needs attention". You, the student, read the comment.

Our command works like that. It gives a **number** (0 or 1) that other programs read, and a **sentence** that people read. There's a
"look but don't touch" version (`--check`), and a version that fixes what's missing. And it **refuses to accept a password typed on
the command line**, because anyone glancing over your shoulder (or reading your command history) could see it.

### 2. Why (vs the traditional approach)
Ad-hoc scripts print "done" and exit 0 even when something went wrong, so a pipeline keeps going. A real CLI makes the *outcome* the
process's exit status, which is what `&&`, `set -e`, CI runners and `docker compose` health checks all understand. Splitting **check**
(read-only) from **apply** (writes) follows the same idea as `terraform plan` vs `terraform apply`.

### 3a. Minimal demo — in-process and as a separate process, against the real graph
Nothing changes in the database: the real schema is already in force, so the apply is a no-op.

`c10_cli_exit_codes.py`
```python
# C10: the CLI -- exit codes, --check vs apply, and reusing the readiness check. Runs against the REAL graph.
# Nothing here changes the database: the real schema is already in force, so applying it again is a no-op.
import subprocess                                                        # to run the CLI exactly as a shell or CI job would
import sys                                                               # for the path of the Python interpreter running this demo

from src.graph.schema import main                                        # the CLI entry point, called in-process
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)


def constraint_count() -> int:                                           # how many constraints the real database has right now
    with build_driver() as driver, driver.session() as session:          # open a connection pool, then one session
        return session.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS c").single()["c"]  # count them


before = constraint_count()                                              # remember the state before we touch anything
print("1. constraints before          :", before)                        # 8: the model

print("2. main(['--check'])           :")                                # verify only
code = main(["--check"])                                                 # prints one line and returns an exit code
print("   returned                    :", code)                          # 0 = schema in force

print("3. main([])  (apply)           :")                                # apply, then verify
code = main([])                                                          # every constraint already exists, so nothing is created
print("   returned                    :", code)                          # 0

print("4. wrong address, --check      :")                                # what a stopped or unreachable server looks like
code = main(["--check", "--uri", "bolt://localhost:1"])                  # nothing listens on port 1
print("   returned                    :", code)                          # 1, with a readable reason and no stack trace

print("5. a --password flag           :")                                # secrets never go on the command line
try:                                                                     # argparse rejects unknown flags by exiting
    main(["--password", "secret"])                                       # not a flag this CLI defines
except SystemExit as exc:                                                # argparse calls sys.exit(2)
    print("   exit code                   :", exc.code)                  # 2 = usage error

print("6. as a shell/CI job sees it   :")                                # a separate process, like `python -m src.graph.schema --check`
done = subprocess.run(                                                   # run the module as a command
    [sys.executable, "-m", "src.graph.schema", "--check"],               # the same command the README documents
    capture_output=True, text=True,                                      # capture its output instead of printing it
)                                                                        # end of the call
print("   stdout                      :", done.stdout.strip())          # the one summary line
print("   process exit status         :", done.returncode)               # 0: what `&&` and CI steps look at

print("7. constraints after           :", constraint_count(), "(unchanged:", constraint_count() == before, ")")  # nothing was modified
```
Real output:
```
usage: c10_cli_exit_codes.py [-h] [--check] [--uri URI] [--user USER]
c10_cli_exit_codes.py: error: unrecognized arguments: --password secret
1. constraints before          : 8
2. main(['--check'])           :
schema verified: 8/8 constraints
   returned                    : 0
3. main([])  (apply)           :
schema applied: 8 constraints in force (0 created, 8 already present)
   returned                    : 0
4. wrong address, --check      :
NOT ready: cannot reach Neo4j (Couldn't connect to localhost:1 (resolved to ('[::1]:1', '127.0.0.1:1')):
Failed to establish connection to ResolvedIPv6Address(('::1', 1, 0, 0)) (reason [WinError 10061] No connection could be made because the target machine actively refused it)
Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 1)) (reason [WinError 10061] No connection could be made because the target machine actively refused it)); is the container running?
   returned                    : 1
5. a --password flag           :
   exit code                   : 2
6. as a shell/CI job sees it   :
   stdout                      : schema verified: 8/8 constraints
   process exit status         : 0
7. constraints after           : 8 (unchanged: True )
```

**How to read it.**
- **1 and 7:** 8 constraints before and after, and `unchanged: True`: none of these commands changed anything.
- **2:** `--check` printed one line and returned `0`.
- **3:** the apply reported `0 created, 8 already present` (idempotent) and returned `0`.
- **4:** an unreachable address printed `NOT ready: cannot reach Neo4j (...)` with the driver's reason and the hint "is the container
  running?", and returned `1`. No stack trace.
- **5:** `--password` isn't a flag, so `argparse` exits with code `2`. Its usage message went to **stderr**, which is why it is
  the first thing in the captured output above (stderr is unbuffered; stdout is buffered when piped).
- **6:** run as a real process, the same command returned status `0`: that is the number a CI step looks at.

### 3b. Exact project code — `main` and the entry point (annotated copy of `src/graph/schema.py`)
```python
#▸ L311: the CLI entry point; argv=None means "read sys.argv"; it RETURNS an exit code
311 def main(argv: list[str] | None = None) -> int:
#▸ L312: docstring: the two modes and the exit code
312     """CLI: python -m src.graph.schema [--check] -- exit 0 if the schema is in force."""
#▸ L313: create the argument parser with a help description
313     parser = argparse.ArgumentParser(description="Apply and verify the graph schema constraints.")
#▸ L314: --check is a switch (store_true): present means verify only
314     parser.add_argument("--check", action="store_true", help="verify only; never write")
#▸ L315: an optional --uri; if absent, NEO4J_URI from .env is used
315     parser.add_argument("--uri", default=None, help="defaults to NEO4J_URI from .env")
#▸ L316: an optional --user; if absent, NEO4J_USER from .env is used
316     parser.add_argument("--user", default=None, help="defaults to NEO4J_USER from .env")
#▸ L317: parse the arguments; an unknown flag such as --password makes argparse exit with code 2
317     args = parser.parse_args(argv)
#▸ L319: decide the URI once, so the same value is used to connect and in messages
319     uri = args.uri or settings.neo4j_uri
#▸ L320: build the driver and close it automatically when the block ends
320     with build_driver(uri, args.user) as driver:
#▸ L321-322: the reason: a wrong password or stopped container gets a specific explanation, not a stack trace
321         # Reuse the P4-M1 readiness check so a wrong password or a stopped container
322         # gets its specific explanation instead of a stack trace.
#▸ L323: run the P4-M1 readiness check; it returns a status and never raises
323         status = health_check(driver)
#▸ L324: not ready?
324         if not status.ready:
#▸ L325: print why, using the check's own message
325             print(f"NOT ready: {status.problem}")
#▸ L326: exit code 1: stop before touching the schema
326             return 1
#▸ L328: verify-only mode
328         if args.check:
#▸ L329: read-only comparison (C8)
329             result = check_schema(driver)
#▸ L330: one of two headings
330             headline = "schema verified" if result.ok else "SCHEMA NOT IN FORCE"
#▸ L331: how many of the model's constraints are in force: total minus problems
331             in_force = len(NODE_TYPES) - len(result.problems)
#▸ L332: print the heading and the count, e.g. "schema verified: 8/8 constraints"
332             print(f"{headline}: {in_force}/{len(NODE_TYPES)} constraints")
#▸ L333: one line per problem
333             for problem in result.problems:
#▸ L334: print it, indented
334                 print(f"  - {problem}")
#▸ L335: list any constraints the model doesn't define
335             _print_unexpected(result.unexpected)
#▸ L336: exit code 0 if everything is in force, else 1
336             return 0 if result.ok else 1
#▸ L338: apply mode: creating might raise
338         try:
#▸ L339: create what is missing, wait for indexes, verify (C7)
339             report = apply_schema(driver)
#▸ L340: an impostor constraint was found
340         except SchemaMismatch as exc:
#▸ L341: print the explanation
341             print(f"SCHEMA MISMATCH: {exc}")
#▸ L342: exit code 1
342             return 1
#▸ L343: success: one summary line...
343         print(
#▸ L344-345: ...with the model size and how many were created vs already present
344             f"schema applied: {len(NODE_TYPES)} constraints in force "
345             f"({len(report.created)} created, {len(report.present)} already present)"
#▸ L347: note any constraints the model doesn't define
347         _print_unexpected(check_schema(driver).unexpected)
#▸ L348: exit code 0
348         return 0
...
#▸ L351: a helper shared by both modes
351 def _print_unexpected(names: tuple[str, ...]) -> None:
#▸ L352: only print when there is something to say
352     if names:
#▸ L353: one line naming them: informational, never a failure
353         print(f"note: {len(names)} constraint(s) are not part of the model: {', '.join(names)}")
...
#▸ L356: true only when run as a script (python -m src.graph.schema), not when imported
356 if __name__ == "__main__":
#▸ L357: run main() and hand its return value to the operating system as the process's exit status
357     sys.exit(main())
```
The tests drive `main()` directly against a fake (`tests/graph/test_schema.py`):
```python
#▸ L406: a fixture: setup shared by the CLI tests
406 @pytest.fixture
#▸ L407: pytest's `monkeypatch` swaps names in a module for the duration of one test
407 def cli(monkeypatch):
#▸ L408: an in-memory database
408     driver = FakeSchemaDriver()
#▸ L409: make the CLI use the fake instead of connecting
409     monkeypatch.setattr(graph_schema, "build_driver", lambda uri=None, user=None: driver)
#▸ L410: and skip the real readiness check: report ready
410     monkeypatch.setattr(graph_schema, "health_check", lambda d: GraphStatus(ready=True))
#▸ L411: give the tests the fake, so they can inspect what the CLI did to it
411     return driver
...
#▸ L414: apply mode, run twice
414 def test_cli_applies_the_whole_model_and_reports_what_it_did(cli, capsys):
#▸ L415: the exit code must be 0
415     assert graph_schema.main([]) == 0
#▸ L416: and every model entry must exist in the fake
416     assert len(cli.constraints) == len(NODE_TYPES)
#▸ L417: and the output says all were created, none already present
417     assert f"{len(NODE_TYPES)} created, 0 already present" in capsys.readouterr().out
#▸ L419: run it again: still 0
419     assert graph_schema.main([]) == 0
#▸ L420: and now nothing is created and all are present
420     assert f"0 created, {len(NODE_TYPES)} already present" in capsys.readouterr().out
...
#▸ L423: check mode on an empty database
423 def test_cli_check_never_writes_and_exits_1_when_constraints_are_missing(cli, capsys):
#▸ L424: exit code 1: nothing is in force
424     assert graph_schema.main(["--check"]) == 1
#▸ L426: the heading and count say 0 of 8
426     assert out.startswith(f"SCHEMA NOT IN FORCE: 0/{len(NODE_TYPES)} constraints")
#▸ L427: and the missing constraints are named
427     assert "missing company_name_unique" in out
#▸ L428: every statement the CLI ran was a read
428     assert all(s.startswith("SHOW CONSTRAINTS") for s in cli.statements)
#▸ L429: and the fake database is still empty: --check wrote nothing
429     assert cli.constraints == {}
...
#▸ L448: a readiness failure stops the CLI
448 def test_cli_stops_at_the_readiness_problem_without_touching_the_schema(cli, monkeypatch, capsys):
#▸ L449-453: make the readiness check report "cannot reach Neo4j"
449     monkeypatch.setattr(
450         graph_schema,
451         "health_check",
452         lambda d: GraphStatus(ready=False, problem="cannot reach Neo4j"),
453     )
#▸ L454: exit code 1
454     assert graph_schema.main([]) == 1
#▸ L455: the output is exactly the "NOT ready" line
455     assert capsys.readouterr().out == "NOT ready: cannot reach Neo4j\n"
#▸ L456: and no statement at all reached the database
456     assert cli.statements == []
...
#▸ L467: a password must never be a command-line flag
467 def test_cli_has_no_password_flag():
#▸ L468: argparse exits...
468     with pytest.raises(SystemExit) as exit_:
#▸ L469: ...when given a flag the CLI doesn't define
469         graph_schema.main(["--password", "secret"])
#▸ L470: with the conventional "usage error" code
470     assert exit_.value.code == 2
```
**How it fits.** README documents `python -m src.graph.schema --check` as the read-only verification; the live test
`test_live_real_schema_is_in_force_and_reapplying_changes_nothing` calls `main(["--check"])` against the real server and expects `0`.

### 4. Benefits
Scriptable, safe by default (`--check`), secret-free, and it explains failures in words.

- **What breaks:** a CLI that calls `sys.exit` inside logic (untestable); printing errors but exiting 0; accepting secrets as flags;
  a `--check` that quietly writes.
- **Docs:** [Python `argparse`](https://docs.python.org/3/library/argparse.html) ·
  [Python `sys.exit`](https://docs.python.org/3/library/sys.html#sys.exit) ·
  [`python -m`](https://docs.python.org/3/using/cmdline.html#cmdoption-m) ·
  [pytest `monkeypatch`](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)

### Micro-exercises
1. Run `python -m src.graph.schema --check; echo $?` in Git Bash. What does `$?` print?
2. Stop Neo4j (`docker compose stop neo4j`) and run the command. Read the message, then start it again.
3. Add a `--quiet` flag that prints nothing but still returns the exit code. Which tests need updating?
4. Why does `main` *return* `1` instead of calling `sys.exit(1)` in the middle?
5. Put a wrong password in `.env` (and restore it!) and read the message. Which earlier concept produced it?

**Internals.** `python -m src.graph.schema` imports the package `src`, finds the module, and runs it with `__name__ == "__main__"`, so the
bottom `if` fires. `sys.exit(0)` raises `SystemExit`, which the interpreter turns into the process exit status; `argparse` uses the same
mechanism (`exit code 2`) when it rejects a flag.
**Checkpoint:** say what `--check` guarantees, and what exit code each of "ready and in force", "not ready" and "unknown flag" returns.

---


## C11 — Testing: one scenario on a fake and the real server, a sweep, a doc guard, and mutants

### 1. Definition + analogy

**What it is.** `tests/graph/test_schema.py` has 44 tests (17 of them live). Five ideas do the work; each is small.

| Idea | What it does | Why it matters here |
|---|---|---|
| **A fake that encodes observed behaviour** | `FakeSchemaDriver` mimics just the constraint behaviour the module relies on | most tests run in milliseconds with no server, so CI needs no Neo4j |
| **The same scenario on the fake *and* the real server** | one fixture, two parameters (`fake`, `live`) | if the fake's beliefs about Neo4j were wrong, the live copy of the same test fails |
| **Scratch data swept by name prefix** | throwaway labels start `Scratch`, constraints `scratch`; a sweep removes them before and after | a crashed run is repaired by the next one |
| **A documentation guard** | a test compares the tables in `docs/architecture.md` with the model | documentation that nothing compares with the code drifts |
| **Mutation testing, by hand** | inject one plausible mistake at a time and require a test to fail | proves the tests can fail, not just that they pass |

**Live tests skip themselves, with a reason.** They are marked `live` and check `neo4j_down()`; if the server isn't reachable they skip and
say why. CI deselects them with `-m "not tiktoken and not live"`. A *wrong password* is not "down": it fails, it does not skip.

**The fake is a contract, not a guess.** A fake that is never checked against the real thing slowly turns into a story you tell
yourself. Running identical scenarios on both (a *contract test*) is what keeps the story true: all five scenario tests exist in two
copies (`[fake]`, `[live]`), and both must pass.

**The mutants.** Each was one edit to `src/graph/schema.py`; each was caught, and the source was restored and hash-checked afterwards:

| # | Mutant (a plausible mistake) | Caught by |
|---|---|---|
| M1 | DDL loses `IF NOT EXISTS` | `test_constraint_statements_are_idempotent_ddl` |
| M2 | `check_schema` matches by name only | `test_same_name_with_a_different_definition_is_reported_not_hidden[fake]` |
| M3 | `apply_schema` skips the verification | the same test |
| M4 | `created` / `present` swapped | `test_first_apply_creates_everything_and_the_second_creates_nothing[fake]` |
| M5 | identifiers not validated | `test_identifiers_that_are_not_plain_names_never_reach_the_database[bad0]` |
| M6 | foreign constraints never reported | `test_unrelated_constraints_are_reported_but_do_not_fail_the_check[fake]` |
| M7 | no wait for indexes | `test_apply_waits_for_indexes_before_it_verifies` |
| M8 | `--check` secretly applies | `test_cli_check_never_writes_and_exits_1_when_constraints_are_missing` |
| M9 | composite key written in the wrong order | `test_constraint_statements_are_idempotent_ddl` |
| M10 | constraint name ignores the key | `test_constraint_names_are_stable_and_readable` |
| M11 | a same-name impostor is dropped from the problems | the same-name test |

(The runs used `-x`, which stops at the first failure, so each row shows the first test to fail, not the only one.)

**Key terms.** *Hermetic*: needs nothing outside the process. *Fake*: a stand-in that behaves like the real thing for the parts
you use. *Contract test*: the same test against two implementations. *Fixture*: reusable test setup. *Mutation testing*: change the
code on purpose to check the tests notice.

**Limits.** The fake only models what was observed; it will happily accept DDL the real server would refuse, unless it asserts
(it does, for shapes it doesn't model). The sweep relies on a naming convention. Only five scenarios are dual-run; the CLI and
enforcement tests use one implementation each. The mutation runs were hand-driven, not part of the repository's CI.

**Analogy.** A flight simulator. It's a great way to train and to run a thousand scenarios cheaply, but a simulator that has never been
compared with a real aircraft may be teaching pilots the wrong things. So every so often you fly the *same* manoeuvre in both and check
the instruments agree.

### For a 14-year-old

You practise a video game level on an **easy practice copy** (fast, free, no risk). But the practice copy is only trustworthy if it
behaves like the real game. So sometimes you play **the exact same moves on both** and check you get the same score. If they differ, the
practice copy needs fixing.

Also: if you leave your stuff scattered in a shared room, the next person shouldn't have to guess what's yours. So **everything you bring
has your team's name on the label**, and the first thing anyone does when they arrive is clear away anything with that name on. Even if
you fell asleep and forgot to tidy up, the next person does it.

And to check the tests really work, you **deliberately break the machine** in eleven small ways, to see if the alarm goes off each time.

### 2. Why (vs the traditional approach)
Many suites test only against a mock (fast but unverified) or only against the real thing (slow, needs infrastructure, skipped in CI).
Doing both, with the *same* test body, gives speed and truth. Testcontainers is the heavier alternative (a fresh throwaway server per
run); it would remove the need for a sweep but needs Docker access from the test process, which the project's CI (a Windows runner that
cannot start service containers) does not have.

### 3a. Minimal demo — one scenario on both, a simulated crash and its sweep, the doc guard, and a mutant
Live Neo4j on `Scratch` labels (the prefix the sweep looks for), self-cleaning. It imports the project's own fake and helpers.

`c11_testing_fake_vs_live.py`
```python
# C11: the testing ideas behind tests/graph/test_schema.py -- one scenario on a fake AND the real server, a sweep, a doc guard, a mutant.
# Live Neo4j on throwaway "Scratch" labels (the prefix the project's sweep looks for), self-cleaning.
import sys                                                               # to add the project root to Python's import path
import uuid                                                              # to make labels no other data can have
from pathlib import Path                                                 # object-style file paths

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))             # only `src` is installed as a package; this makes `tests` importable too

from src.graph.schema import NODE_TYPES, NodeType, SchemaMismatch, apply_schema, check_schema  # the code under test
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)
from tests.graph.test_schema import FakeSchemaDriver, _sweep_scratch, _table  # the project's own fake, sweep helper and doc-table reader

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
types = (                                                                # a tiny model on labels that start with "Scratch"
    NodeType(f"Scratch{suffix}", ("name",), "domain", "demo"),           # a one-property key
    NodeType(f"ScratchC{suffix}", ("name", "organization"), "domain", "demo"),  # a two-property key
)                                                                        # end of the tiny model


def scenario(driver) -> dict:                                            # ONE scenario, run unchanged against any driver
    seen = {}                                                            # what we observed
    first = apply_schema(driver, types)                                  # apply twice ...
    second = apply_schema(driver, types)                                 # ... to see created vs present
    seen["created first/second"] = (len(first.created), len(second.created))  # (2, 0) if idempotent
    with driver.session() as session:                                    # now plant an impostor under the first constraint's name
        session.run(f"DROP CONSTRAINT {types[0].constraint_name} IF EXISTS").consume()  # remove the real rule
        session.run(f"CREATE CONSTRAINT {types[0].constraint_name} IF NOT EXISTS FOR (n:{types[0].label}) REQUIRE n.other IS UNIQUE").consume()  # wrong rule
    try:                                                                 # apply must notice
        apply_schema(driver, (types[0],))                                # verify-by-definition should raise
        seen["impostor"] = "ACCEPTED (bug)"                              # if we get here, the check is broken
    except SchemaMismatch:                                               # the expected outcome
        seen["impostor"] = "SchemaMismatch"                              # noticed
    return seen                                                          # hand the observations back


fake = FakeSchemaDriver()                                                # the in-memory stand-in from the test file
with build_driver() as live:                                             # the real server
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        _sweep_scratch(live)                                             # start from a clean slate
        on_fake, on_live = scenario(fake), scenario(live)                # 1. run the SAME scenario on both
        print("1. fake :", on_fake)                                      # what the fake did
        print("   live :", on_live)                                      # what Neo4j did
        print("   identical:", on_fake == on_live, "-> the fake still tells the truth about Neo4j")  # the point of running both

        with live.session() as session:                                  # 2. simulate a test run that crashed before cleaning up
            session.run(f"CREATE CONSTRAINT scratch_leftover_{suffix} IF NOT EXISTS FOR (n:ScratchLeft{suffix}) REQUIRE n.name IS UNIQUE").consume()  # a leftover rule
            session.run(f"CREATE (:ScratchLeft{suffix} {{name: 'orphan'}})").consume()  # and a leftover node
            left = session.run("SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'scratch' RETURN count(*) AS c").single()["c"]  # how many scratch rules exist
            print("2. leftovers before the sweep:", left, "scratch constraints")  # the crashed run's debris
            _sweep_scratch(live)                                         # the next run starts by sweeping, by name prefix
            left = session.run("SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'scratch' RETURN count(*) AS c").single()["c"]  # count again
            nodes = session.run("MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Scratch') RETURN count(n) AS c").single()["c"]  # and nodes
            print("   after the sweep           :", left, "constraints,", nodes, "nodes")  # repaired
    finally:                                                             # always clean up, even after an error above
        _sweep_scratch(live)                                             # the sweep IS the cleanup

documented = {                                                           # 3. the doc guard: read the table in docs/architecture.md
    cells[0].strip("`"): (cells[1], tuple(k.strip("` ") for k in cells[2].split(",")))  # label -> (layer, key)
    for cells in _table("graph-nodes")                                   # each data row of the node table
}                                                                        # end of the dictionary
model = {n.label: (n.layer, n.key) for n in NODE_TYPES}                  # the same facts, from the code
print("3. doc table == model          :", documented == model)           # True: the documentation matches the code
drifted = {**model, "Department": ("domain", ("name",))}                 # pretend someone added a label in code but not in the doc
print("   after adding 'Department'   :", documented == drifted, "| only in code:", set(drifted) - set(documented))  # the guard would fail


def mutant_ok(driver, node: NodeType) -> bool:                           # 4. a MUTANT: check by NAME only (a plausible mistake)
    with driver.session() as session:                                    # ask the database what constraints exist
        names = {r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name RETURN name")}  # just their names
    return node.constraint_name in names                                 # "a constraint with that name exists" -- ignores its definition


trap = FakeSchemaDriver()                                                # use the fake so nothing touches the real database
trap.constraints[types[0].constraint_name] = ("UNIQUENESS", "NODE", (types[0].label,), ("other",))  # the impostor: right name, wrong property
print("4. real check_schema says ok   :", check_schema(trap, (types[0],)).ok)   # False: it compares definitions
print("   mutant (name only) says ok  :", mutant_ok(trap, types[0]), "<- wrong; test_same_name_with_a_different_definition... fails on this mutant")  # True: it would slip through
```
Real output:
```
1. fake : {'created first/second': (2, 0), 'impostor': 'SchemaMismatch'}
   live : {'created first/second': (2, 0), 'impostor': 'SchemaMismatch'}
   identical: True -> the fake still tells the truth about Neo4j
2. leftovers before the sweep: 3 scratch constraints
   after the sweep           : 0 constraints, 0 nodes
3. doc table == model          : True
   after adding 'Department'   : False | only in code: {'Department'}
4. real check_schema says ok   : False
   mutant (name only) says ok  : True <- wrong; test_same_name_with_a_different_definition... fails on this mutant
```

**How to read it.**
- **1:** the same scenario returned the same observations from the fake and from Neo4j: two created then none, and `SchemaMismatch` for
  the impostor. `identical: True` is the whole point of running both.
- **2:** the "crashed run" left **3** scratch constraints (the deliberate leftover plus the two the scenario itself left) and a node;
  `_sweep_scratch` removed all of them by prefix.
- **3:** the documentation table equals the model; after pretending a `Department` label had been added in code only, they differ, and the
  difference is named. That is the failure the real test would produce.
- **4:** the real `check_schema` says `False` for an impostor; a mutant that matches by name says `True`. That "wrongly OK" is exactly what
  `test_same_name_with_a_different_definition...` would catch.

### 3b. Exact project code — the fake, the fixtures and the guard (annotated copies of `tests/graph/test_schema.py`)
The fake driver (the part of Neo4j's constraint behaviour the module relies on):
```python
#▸ L74: an in-memory stand-in that looks like a Neo4j driver
74  class FakeSchemaDriver:
#▸ L75-80: the docstring lists the three behaviours it encodes, each observed on the live server
75      """Just the constraint behaviour this module relies on, each point observed live:
76
77      - `CREATE ... IF NOT EXISTS` reports constraints_added 1 for a new constraint, 0 for a no-op;
78      - it is a silent no-op when the NAME is taken, even by a different definition;
79      - it is also a silent no-op when an equivalent constraint exists under another name.
80      """
#▸ L82-84: a pattern that recognises the one DDL shape the code emits: single or composite uniqueness
82      CREATE = re.compile(
83          r"CREATE CONSTRAINT (\w+) IF NOT EXISTS FOR \(n:(\w+)\) REQUIRE (.+) IS UNIQUE"
84      )
#▸ L86: create the fake's state
86      def __init__(self) -> None:
#▸ L87: the constraints it "has": name -> (type, entity, labels, properties)
87          self.constraints: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {}
#▸ L88: every statement it was sent, in order (tests inspect this)
88          self.statements: list[str] = []
#▸ L89: every database name it was asked for
89          self.databases: list[str | None] = []
#▸ L91-92: lets the fake be used in `with ... as driver`, like the real driver
91      def __enter__(self):
92          return self
#▸ L94-95: nothing to close
94      def __exit__(self, *exc):
95          return None
#▸ L97-98: the real driver has this; the fake always "connects"
97      def verify_connectivity(self) -> None:
98          return None
#▸ L100-102: hand back a session that forwards queries to this fake, remembering the database name
100     def session(self, *, database=None):
101         self.databases.append(database)
102         return FakeSession(self)
#▸ L104: interpret one statement
104     def execute(self, query: str) -> FakeResult:
#▸ L105: record it
105         self.statements.append(query)
#▸ L106: a create statement?
106         if query.startswith("CREATE CONSTRAINT"):
#▸ L107: parse it with the pattern
107             match = self.CREATE.fullmatch(query)
#▸ L108: anything else is a bug in the test or the code, so fail loudly instead of guessing
108             assert match, f"the fake only models single/composite uniqueness DDL, got: {query}"
#▸ L109: pull out the constraint name, the label and the property text
109             name, label, properties = match.groups()
#▸ L110: turn "(n.name, n.organization)" into ("name", "organization")
110             keys = tuple(p.strip().removeprefix("n.") for p in properties.strip("()").split(","))
#▸ L111: write down the definition, as SHOW CONSTRAINTS would report it
111             definition = ("UNIQUENESS", "NODE", (label,), keys)
#▸ L112: the two silent-no-op rules: the NAME is taken (by anything) OR an equivalent definition exists
112             if name in self.constraints or definition in self.constraints.values():
#▸ L113: then do nothing and report 0 added: no error, exactly like the real server
113                 return FakeResult()
#▸ L114: otherwise remember the new constraint
114             self.constraints[name] = definition
#▸ L115: and report 1 added
115             return FakeResult(added=1)
#▸ L116: a listing query?
116         if query.startswith("SHOW CONSTRAINTS"):
#▸ L117: return one row per constraint...
117             return FakeResult(
#▸ L118-124: ...shaped like the real columns: name, type, entityType, labelsOrTypes, properties
118                 {
119                     "name": name,
120                     "type": kind,
121                     "entityType": entity,
122                     "labelsOrTypes": list(labels),
123                     "properties": list(properties),
124                 }
#▸ L125: ...built from the fake's state
125                 for name, (kind, entity, labels, properties) in self.constraints.items()
#▸ L127: the wait for indexes?
127         if query.startswith("CALL db.awaitIndexes"):
#▸ L128: nothing to wait for: return at once
128             return FakeResult()
#▸ L129: a DROP CONSTRAINT ... IF EXISTS? (used by the shared fixtures)
129         if match := re.fullmatch(r"DROP CONSTRAINT (\w+) IF EXISTS", query):
#▸ L130: forget it, if it exists
130             self.constraints.pop(match.group(1), None)
#▸ L131: nothing to return
131             return FakeResult()
#▸ L132: any other statement is not modelled: fail loudly
132         raise AssertionError(f"the fake does not model: {query}")
```
The fixtures that make one scenario run twice, and keep the real server clean:
```python
#▸ L137: ask once, at import, why Neo4j might be unreachable (None if it is up)
137 _NEO4J_DOWN = neo4j_down()
#▸ L138: a marker that skips a test, with that reason, when Neo4j is down
138 live = pytest.mark.skipif(_NEO4J_DOWN is not None, reason=_NEO4J_DOWN or "")
#▸ L141-143: run one statement on any driver-shaped object and wait for it
141 def _run(driver, query: str, **parameters) -> None:
142     with driver.session() as session:
143         session.run(query, **parameters).consume()
#▸ L146: remove leftovers from earlier (possibly crashed) runs
146 def _sweep_scratch(driver) -> None:
#▸ L149-151: the reason: found by NAME PREFIX, not remembered in memory
149     Scratch things are found by NAME PREFIX rather than remembered in memory, so a run
150     that died before its own cleanup is repaired by the next one. Real labels never
151     start with "Scratch", and real constraints never start with "scratch".
#▸ L153: open a session
153     with driver.session() as session:
#▸ L154-159: list every constraint whose name starts with "scratch"
154         names = [
155             record["name"]
156             for record in session.run(
157                 "SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'scratch' RETURN name"
158             )
159         ]
#▸ L160: for each of them...
160         for name in names:
#▸ L161: ...drop it
161             session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()
#▸ L162-164: and delete every node that carries a label starting with "Scratch"
162         session.run(
163             "MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Scratch') DETACH DELETE n"
164         ).consume()
#▸ L167: a fixture: setup and teardown around a test
167 @pytest.fixture
#▸ L168: provides a real driver
168 def live_db():
#▸ L170: open a real connection
170     with build_driver() as driver:
#▸ L171: sweep BEFORE the test: repairs a run that crashed earlier
171         _sweep_scratch(driver)
#▸ L172: run the test...
172         try:
#▸ L173: ...handing it the driver
173             yield driver
#▸ L174: and whatever happens...
174         finally:
#▸ L175: ...sweep AFTER it too
175             _sweep_scratch(driver)
#▸ L178: a fixture with TWO parameters: every test that uses it runs twice
178 @pytest.fixture(params=["fake", pytest.param("live", marks=[pytest.mark.live, live])])
#▸ L179: the fixture body; `request.param` says which run this is
179 def schema_db(request):
#▸ L180: the first run...
180     if request.param == "fake":
#▸ L181: ...uses a fresh fake
181         return FakeSchemaDriver()
#▸ L182: the second run reuses the live_db fixture, so its sweep and teardown apply
182     return request.getfixturevalue("live_db")
#▸ L185: another fixture
185 @pytest.fixture
#▸ L186: provides two throwaway model entries
186 def scratch() -> tuple[NodeType, NodeType]:
#▸ L188: a random suffix so parallel or repeated runs never share labels
188     token = uuid.uuid4().hex[:8]
#▸ L190: a single-key entry on a Scratch... label
190         NodeType(f"Scratch{token}", ("name",), "domain", "throwaway"),
#▸ L191: a composite-key entry
191         NodeType(f"ScratchC{token}", ("name", "organization"), "domain", "throwaway"),
```
The real-label fixtures use values instead of labels to find what to delete:
```python
#▸ L510: every value the enforcement tests create starts with "__test_"
510 TEST_PREFIX = "__test_"
#▸ L513: delete those nodes from the REAL labels
513 def _delete_test_nodes(driver) -> None:
#▸ L516: for each real label
516         for node in NODE_TYPES:
#▸ L517-520: delete nodes whose first key property starts with the prefix; DETACH removes their relationships
517             session.run(
518                 f"MATCH (n:{node.label}) WHERE n.{node.key[0]} STARTS WITH $prefix DETACH DELETE n",
519                 prefix=TEST_PREFIX,
520             ).consume()
#▸ L523: a fixture: setup and teardown
523 @pytest.fixture
#▸ L524: provides a real driver with the REAL model applied
524 def real_schema():
#▸ L526: open a real connection
526     with build_driver() as driver:
#▸ L527: make sure the real constraints exist (idempotent)
527         apply_schema(driver)
#▸ L528: clear any test nodes a crashed run left on the real labels
528         _delete_test_nodes(driver)
#▸ L529: run the test...
529         try:
#▸ L530: ...handing it the driver
530             yield driver
#▸ L531: and whatever happens...
531         finally:
#▸ L532: ...clean up again
532             _delete_test_nodes(driver)
```
The documentation guard (it reads the tables between the markers in `docs/architecture.md`):
```python
#▸ L476: read one marked table from the architecture document
476 def _table(marker: str) -> list[list[str]]:
#▸ L477: the document's text
477     text = ARCHITECTURE.read_text(encoding="utf-8")
#▸ L478: the part between <!-- marker:start --> and <!-- marker:end -->
478     section = re.search(rf"<!-- {marker}:start -->(.*?)<!-- {marker}:end -->", text, re.DOTALL)
#▸ L479: fail clearly if the markers are missing
479     assert section, f"docs/architecture.md is missing the {marker} markers"
#▸ L480-484: split every table line into its trimmed cells
480     rows = [
481         [cell.strip() for cell in line.strip().strip("|").split("|")]
482         for line in section.group(1).splitlines()
483         if line.startswith("|")
484     ]
#▸ L485: a comment: skip the header and separator rows
485     # Data rows start with a backticked name; this skips the header and separator rows.
#▸ L486: keep only rows whose first cell is a backticked name
486     return [cells for cells in rows if cells[0].startswith("`")]
#▸ L489-490: split "`a`, `b`" into ("a", "b")
489 def _names(cell: str) -> tuple[str, ...]:
490     return tuple(part.strip().strip("`") for part in cell.split(",") if part.strip())
#▸ L493: test the node table against the model
493 def test_architecture_doc_node_table_matches_the_model():
#▸ L494-496: build label -> (layer, key) from the document
494     documented = {
495         cells[0].strip("`"): (cells[1], _names(cells[2])) for cells in _table("graph-nodes")
496     }
#▸ L497: it must equal the same facts computed from NODE_TYPES
497     assert documented == {n.label: (n.layer, n.key) for n in NODE_TYPES}
```
**How it fits.** These are the tests that `pytest tests/graph` runs; CI runs the hermetic ones (fake parameters, unit and guard tests) and
deselects the live ones.

### 4. Benefits
Fast feedback with no infrastructure, a fake that is checked against the truth, safe cleanup, and documentation that can't quietly rot.

- **What breaks:** a mock never compared with the real thing; scratch data tracked in memory (a crash leaks it); documentation tables
  nothing checks; tests that pass while asserting nothing (hence the mutants).
- **Docs:** [pytest: parametrizing fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html#parametrizing-fixtures) ·
  [pytest: skipping](https://docs.pytest.org/en/stable/how-to/skipping.html) ·
  [Fowler: Contract Test](https://martinfowler.com/bliki/ContractTest.html) ·
  [Testcontainers for Python](https://testcontainers-python.readthedocs.io/) ·
  [mutmut (mutation testing)](https://mutmut.readthedocs.io/)

### Micro-exercises
1. Run `pytest tests/graph -v -rs` with Neo4j stopped. Which tests skip, and what reason do they print?
2. Break the fake (make it ignore the "name is taken" rule) and run the tests. Which `[fake]` and which `[live]` tests fail? What does that tell you?
3. Add a leftover by hand (`CREATE CONSTRAINT scratch_x ...`) and run `pytest tests/graph -q`. Is it gone afterwards?
4. Add a `Department` row to `docs/architecture.md`'s node table only. Which test fails, and what does it say?
5. Invent a twelfth mutant (for example, `check_schema` returns `ok=True` whenever `problems` has one item) and find the test that kills it.

**Internals.** `request.getfixturevalue("live_db")` fetches a fixture *by name inside another fixture*; pytest still tears it down after the
test, in the right order. Fixture parameters give every dependent test a new id (`[fake]`, `[live]`), which is why the mutation table
shows those suffixes.
**Checkpoint:** explain why a green test suite on a fake alone would not prove the schema code works, and what makes the live copy of the
same scenario a *contract*.

---


## Validation

Run from the project root; each should match.

```bash
.venv/Scripts/python -m src.graph.schema --check
# schema verified: 8/8 constraints
.venv/Scripts/python -m pytest tests/graph -q -p no:warnings -rs
# 58 passed
.venv/Scripts/python -m pytest tests/ -q -p no:warnings -m "not tiktoken and not live"
# 211 passed, 34 deselected
.venv/Scripts/python -m ruff check src/ tests/ && .venv/Scripts/python -m ruff format --check src/ tests/
# All checks passed!  /  49 files already formatted
.venv/Scripts/python -m mypy src/
# Success: no issues found in 21 source files
```

Then run all eleven demos (Neo4j up, `data/chunks/` present) and check the real graph is untouched:

```bash
for f in "practice codes/phase 4/m2_graph_schema_deep_dive"/c*.py; do .venv/Scripts/python "$f" > /dev/null && echo "ok: $f"; done
.venv/Scripts/python -m src.graph.schema --check
# schema verified: 8/8 constraints   (still exactly the 8 real constraints, and no nodes)
```

Self-check questions (answer without looking):
1. Why can't `contract_number` be the key of `Contract`? Give two pieces of evidence from the audit.
2. What does the demo's one-label experiment do to the two Robert Chens, and which Cypher clause does it?
3. `CREATE CONSTRAINT ... IF NOT EXISTS` returned `constraints_added = 0`. Name three different reasons it might have.
4. Why does `check_schema` compare definitions rather than names, and which problem sentence covers an impostor?
5. What is different about `MERGE` and `CREATE` when a key value is null, and what rule for the loader follows?
6. Why does the graph store `chunk_id` and not the chunk text, and how does a graph result reach Qdrant?
7. Which of label, property name, constraint name and value can be a parameter in constraint DDL? What does the code do about the rest?
8. How does the test suite know the fake driver still behaves like Neo4j?
9. What does `python -m src.graph.schema --check` promise never to do, and what are its exit codes?
10. What does a uniqueness constraint *not* prevent on Community Edition?

## Visualization

**The model (labels and relationships)**
```
                                   ┌─────────────── MENTIONS ───────────────┐
Document ──HAS_CHUNK──► Chunk ─────┤   Company   Product   Contract         │
   ▲                               │   Employee  Contact   Location  ◄──────┘
   │                               └────────────────────────────────────────  (any domain label)
   └── DESCRIBED_IN ── Product | Contract | Employee

Contract ──FOR_PRODUCT──► Product          Employee ──WORKS_ON──► Product
Contract ──WITH_CLIENT──► Company          Employee ──BASED_IN {remote}──► Location
Contract ──SIGNED_BY {title}──► Contact    Employee ──FOUNDED──► Company
Company  ──HAS_OFFICE {headquarters}──► Location
```

**`apply_schema` (C7)**
```
for each NodeType ─► constraint_statement() ─► session.run(DDL) ─► counters.constraints_added
                                                                     ├─ 1 → created
                                                                     └─ 0 → present (already there... or skipped: unknown yet)
                     CALL db.awaitIndexes(60)   ← wait until the indexes are ONLINE
                     check_schema() ─► problems? ──── yes ──► raise SchemaMismatch("; ".join(problems))
                                           └── no ───► SchemaReport(created, present)
```

**`check_schema` for one expected constraint (C8)**
```
SHOW CONSTRAINTS ─► by_definition {(type, entity, labels, props): name}   by_name {name: row}
expected = ("UNIQUENESS", "NODE", (label,), key)
   expected in by_definition?  ── yes ──► in force (whatever its name)
        │ no
   by_name has the expected name? ── no ──► problem: "missing <name>: UNIQUENESS on Label(key)"
        │ yes
        └─► problem: "<name> exists but is <type> on X(props), not UNIQUENESS on Label(key); drop it ..."
anything in by_definition not expected ─► unexpected (informational only)
```

**Tools to see it yourself**
- **Neo4j Browser:** http://localhost:7474 (log in with the `.env` credentials), then `SHOW CONSTRAINTS`, `SHOW INDEXES` (look at `owningConstraint`),
  and `CALL db.labels()`.
- **`python -m src.graph.schema --check`:** the read-only status of the whole model, in one line.
- **`pytest tests/graph -v -rs`:** which tests ran, which were `[fake]` and which `[live]`, and why any skipped.
- **`docs/architecture.md` (section "Graph model"):** the two tables the doc-guard test compares with the code.

## End-to-end "watch it work" exercise

1. **Audit:** run `c1_audit_the_corpus.py` and note the three numbers that shaped the model (19 of 32, 11 spellings, 0 of 17).
2. **See identity:** run `c2_identity_keys.py`, then change one character of the path and watch `doc_id` change.
3. **Break the rule safely:** run `c3_uniqueness_and_merge.py`, then repeat its steps by hand in the Browser on a label of your own.
4. **Follow a graph hit to a vector id:** run `c4_provenance_bridge.py` and copy one printed `point_id`; explain how you would fetch that chunk from Qdrant.
5. **Watch a false merge happen:** run `c5_two_person_labels.py` and read step 1 twice.
6. **Try to inject:** run `c6_ddl_identifiers.py`, then add your own hostile label and confirm `constraint_statement` refuses it.
7. **Plant an impostor:** run `c7_apply_schema.py` and `c8_check_schema.py` and read both error sentences aloud.
8. **Meet the edition limits:** run `c9_community_limits.py`, then write the one-sentence rule the loader must follow.
9. **Use the CLI:** run `c10_cli_exit_codes.py`, then `python -m src.graph.schema --check; echo $?` in your own shell.
10. **Test the tests:** run `c11_testing_fake_vs_live.py`, then break the fake on purpose (exercise 2 in C11) and watch which tests notice.
11. **Log it:** add a `LEARNING_LOG.md` line with the one Neo4j behaviour that surprised you most.

## Canonical sources
- Neo4j: [Cypher manual: constraints](https://neo4j.com/docs/cypher-manual/current/constraints/) · [`MERGE`](https://neo4j.com/docs/cypher-manual/current/clauses/merge/) · [parameters](https://neo4j.com/docs/cypher-manual/current/syntax/parameters/) · [naming rules](https://neo4j.com/docs/cypher-manual/current/syntax/naming/) · [data modelling](https://neo4j.com/docs/getting-started/data-modeling/) · [editions](https://neo4j.com/docs/operations-manual/current/introduction/#_neo4j_editions) · [status codes](https://neo4j.com/docs/status-codes/current/)
- Python driver: [manual](https://neo4j.com/docs/python-manual/current/) · [API and errors](https://neo4j.com/docs/api/python-driver/current/api.html)
- Python: [`dataclasses`](https://docs.python.org/3/library/dataclasses.html) · [`re`](https://docs.python.org/3/library/re.html) · [`hashlib`](https://docs.python.org/3/library/hashlib.html) · [`argparse`](https://docs.python.org/3/library/argparse.html) · [`sys.exit`](https://docs.python.org/3/library/sys.html#sys.exit)
- Testing: [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html) · [pytest skipping](https://docs.pytest.org/en/stable/how-to/skipping.html) · [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html) · [Fowler: Contract Test](https://martinfowler.com/bliki/ContractTest.html) · [mutmut](https://mutmut.readthedocs.io/)
- Entity resolution: [Fellegi & Sunter (1969)](https://doi.org/10.1080/01621459.1969.10501049) · [Christen, *Data Matching* (2012)](https://link.springer.com/book/10.1007/978-3-642-31164-2)
- GraphRAG: [Edge et al. 2024 (arXiv:2404.16130)](https://arxiv.org/abs/2404.16130) · [Peng et al. 2024 survey (arXiv:2408.08921)](https://arxiv.org/abs/2408.08921)
- Ids: [RFC 9562 (UUIDs)](https://www.rfc-editor.org/rfc/rfc9562)
- Project: [decision.md](../decision.md) (D-2026-09-20-01 to -09) · [notes/step-4.2-graph-schema.md](../notes/step-4.2-graph-schema.md) · [docs/architecture.md](architecture.md) (Graph model)
