# 19 — Graph Records and the Neo4j Store: Deep Dive (P4-M3)

> Every output block was produced on 2026-09-20 by running the code against the live Neo4j **5.26.0 Community** container.
> Demos that write use a throwaway label or a `__demo_` prefix and delete what they made (each has a `finally`); after all ten
> ran, the real graph still had exactly 8 constraints and 0 nodes. Nothing prints the password.
>
> This document follows three rules:
> 1. **Minimal demos.** Each demo holds one concept and nothing more (18 to 40 lines each; 201 code lines in all), and
>    **every line has a comment saying what it does.**
> 2. **Exact code.** For each concept you get the code built in P4-M3 as an **annotated copy**: each line keeps its real number and a
>    `#▸` comment above it says what it does. These blocks are generated from the source files and each annotated line is checked against
>    the file, so the code shown is the code that runs.
> 3. **Interview tags.** Every concept title carries a bracket for an **AI-engineer interview**:
>    **[INTERVIEW: MUST KNOW]**, **[INTERVIEW: GOOD TO KNOW]** or **[INTERVIEW: RARELY ASKED]**. That is my judgement of what such
>    interviews emphasise, not a survey: companies differ, so read it as a priority order for your time.
>
> Demos are in [`practice codes/phase 4/m3_graph_store_deep_dive/`](../practice%20codes/phase%204/m3_graph_store_deep_dive/). Run from the project root
> (Neo4j must be running for all but C2, C3 and C10: `docker compose up -d neo4j`):
> ```bash
> .venv/Scripts/python "practice codes/phase 4/m3_graph_store_deep_dive/c1_multi_hop_idea.py"
> ```

---

## 0. What P4-M3 is, and the interview map

P4-M2 decided what the graph may contain. P4-M3 builds the two things that put data in and get answers out:

```
extractor (next step) ──► GraphNode / GraphEdge ──► GraphStore ──► Neo4j          C2 C3  (records)   C4 C5 C6 C7  (writes)
                          (validated when built)     upsert_node   (8 constraints)
                                                     upsert_edge
question ──► entity name ──► search_related ──► edges              C8  (lookup)
                             multi_hop_search ──► names            C9  (traversal)
tests: a fake driver, live tests on the real labels, a 29-mutant check                                       C10
```

Nothing is loaded yet, so this is proven on synthetic data only.

### Interview map (for an AI engineer)

| # | Concept | Tag | Why an interviewer asks | A question you may get |
|---|---|---|---|---|
| C1 | Why a graph store: multi-hop questions | **[INTERVIEW: MUST KNOW]** | it is the "why not just vectors?" question | "When would you add a knowledge graph to a RAG system?" |
| C2 | Validate at the boundary | **[INTERVIEW: GOOD TO KNOW]** | data quality decides retrieval quality | "Where do you validate data in an ingestion pipeline?" |
| C3 | Frozen is shallow | **[INTERVIEW: GOOD TO KNOW]** | a classic Python question | "Does `frozen=True` make a dataclass immutable?" |
| C4 | Idempotent upsert (`MERGE`, `SET +=`) | **[INTERVIEW: MUST KNOW]** | re-runnable ingestion is a core pipeline skill | "How do you make an ingestion job safe to re-run?" |
| C5 | Referential integrity (strict endpoints) | **[INTERVIEW: GOOD TO KNOW]** | typos silently split entities | "What happens when a relationship points at something that does not exist yet?" |
| C6 | Managed transactions and retries | **[INTERVIEW: GOOD TO KNOW]** | backend and data-engineering depth | "How do you make retries safe?" |
| C7 | Parameters vs structure (injection) | **[INTERVIEW: MUST KNOW]** | LLMs that write queries make this a live risk | "How do you stop an LLM-generated query doing damage?" |
| C8 | Entity lookup, namesakes, provenance | **[INTERVIEW: MUST KNOW]** | entity linking is where graph RAG usually fails | "How do you connect a user's question to the right node?" |
| C9 | Multi-hop paths, cost, and caps | **[INTERVIEW: MUST KNOW]** | the failure mode of graph retrieval | "How do you stop multi-hop retrieval exploding?" |
| C10 | Testing: fake driver, live sweep, mutants | **[INTERVIEW: RARELY ASKED]** directly; useful in "how do you test RAG?" | shows engineering maturity | "How do you test a data pipeline?" |

**If you have one hour:** C1, C4, C7, C8, C9.

---


## C1 — Why a graph store: multi-hop questions  **[INTERVIEW: MUST KNOW]**

*Interviewers test whether you know when a knowledge graph earns its cost, and can say what vector search cannot do.*

### 1. Definition + analogy
A **multi-hop question** needs a chain of facts, each written in a different place. "Which client is connected to the product Rachel works on?"
An HR record says Rachel works on Carllm; a contract says it is for Carllm and the client DriveSmart. **No single passage names both Rachel and
DriveSmart.** Vector search returns passages *similar to the question*, so it cannot join facts that sit in different passages. A **graph** stores
entities and the relationships between them, so it can *follow* them.

`GraphStore` exposes two read operations over the graph: `search_related` (one hop: everything attached to an entity) and `multi_hop_search` (up
to N hops). The graph does not replace vector search: the graph finds *entities and connections*, and vector search finds the *passages* that
describe them (the idea behind GraphRAG, Edge et al. 2024).

**Limits.** A graph needs extraction (which makes errors), a model, and upkeep. It helps only for questions about *connections*, not for "explain
this paragraph".

**Analogy.** A subway map. A phone book lists similar entries but cannot tell you how to get from one place to another; the map's lines can.

### For a 14-year-old
Your school has a sheet saying "Sam plays in the band", and a different sheet saying "the band practises in the hall", and a third saying "the hall is
locked on Fridays". Nobody wrote "Sam's practice is affected on Fridays". But if you draw dots and arrows, you can follow them: Sam → band → hall →
Friday. Following arrows to answer a question no single sheet answers is what a graph is for.

### 2. Minimal demo: three facts from two documents, one question across them
`c1_multi_hop_idea.py`
```python
# C1: a graph answers a question that needs facts from different documents, by following relationships.
import uuid                                                                  # to make a label nothing else uses

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                        # a throwaway label, so cleanup is exact
with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.run(                                                         # write three facts as one chain
            f"CREATE (:{label} {{name: 'Rachel'}})-[:WORKS_ON]->(:{label} {{name: 'Carllm'}})"  # fact from an HR record: Rachel works on Carllm
            f"<-[:FOR_PRODUCT]-(:{label} {{name: 'Contract 7'}})-[:WITH_CLIENT]->(:{label} {{name: 'DriveSmart'}})"  # facts from a contract: for Carllm, with DriveSmart
        ).consume()                                                          # wait for the write to finish
        names = session.run(                                                 # ask: what is connected to Rachel?
            f"MATCH (:{label} {{name: 'Rachel'}})-[*1..3]-(t:{label}) RETURN DISTINCT t.name AS name"  # anything within 3 relationships, either direction
        ).value("name")                                                      # a plain list of names
        print(sorted(names))                                                 # DriveSmart appears, though no single document names both it and Rachel
    finally:                                                                 # always clean up
        session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()          # remove the demo's nodes
```
Real output:
```
['Carllm', 'Contract 7', 'DriveSmart']
```

**How to read it.** Two documents wrote three facts. Asking what is within 3 relationships of Rachel returns `DriveSmart`, though no document names
both. That is the whole idea; C9 makes it safe.

### 3. Exact project code: the two read operations (annotated copy of `src/retrieval/graph_store.py`)
```python
#▸ L93: the store: one object that reads and writes the entity graph
93  class GraphStore:
#▸ L94: its docstring: the driver is injected, not created here (C10)
94      """Reads and writes the entity graph through an injected Neo4j driver."""
...
#▸ L178: one hop: every edge attached to an entity
178     def search_related(
#▸ L180: the entity's exact name (C8)
180         entity_name: str,
#▸ L182: optional (a string or None): narrow the search to one kind of node, for namesakes (C8)
182         label: str | None = None,
#▸ L183: optional: also return "where it was read" edges (C8)
183         include_provenance: bool = False,
#▸ L184: a cap on the number of edges returned
184         limit: int = DEFAULT_LIMIT,
#▸ L185: it returns edges, each with its real source and target
185     ) -> list[GraphEdge]:
#▸ L186-191: the docstring states the rules: exact name, all namesakes unless narrowed, provenance off by default
186         """Every edge attached to the entity, in either direction.
187
188         Matches the exact `name`. A name can belong to several nodes (an Employee and a
189         Contact can share one), so all of them are searched unless `label` narrows it.
190         Provenance edges (MENTIONS and friends) are left out unless asked for.
191         """
...
#▸ L219: up to N hops from an entity
219     def multi_hop_search(
#▸ L220: the start's name, the hop count, and the same optional label and limit
220         self, start_entity: str, hops: int, *, label: str | None = None, limit: int = DEFAULT_LIMIT
#▸ L221: it returns the names of what it reaches, nearest first
221     ) -> list[str]:
#▸ L222-228: the docstring states the rules: domain edges only, the start never returned, nameless nodes walked through (C9)
222         """Names of the entities within `hops` steps of the start, nearest first.
223
224         Follows domain relationships in either direction, from every node with that
225         name. The start itself is never returned. A node with no name, such as a
226         Contract, is walked THROUGH but not returned, so "which clients have a Carllm
227         contract" still works; a Contract with a `title` property is returned by title.
228         """
```

### 4. Interview: what a strong answer contains
- **"When would you add a knowledge graph to a RAG system?"** Strong: when questions need *connections* across documents (multi-hop), exact entity relationships, or
  explainable paths; when the corpus has entities that recur across documents; and only after measuring that vector search misses them. It states the cost
  (extraction errors, a schema, maintenance) and that the usual design is *hybrid*. Weak: "graphs are more accurate".
- **"What goes wrong with graph retrieval?"** Wrong or missing extraction, ambiguous entities (C8), hub nodes that connect everything (C9), stale data.

### Micro-exercises
1. In the demo, add a second contract for Carllm with another client. What does the query return now?
2. Change `*1..3` to `*1..2`. Which name disappears, and why?
3. Write the sentence a vector search would have to find to answer the demo's question. Does that sentence exist in any document?

**Checkpoint:** say why vector search cannot answer the demo's question, and what the graph does differently.

---

## C2 — Validate at the boundary: records  **[INTERVIEW: GOOD TO KNOW]**

*Interviewers ask where you validate data in a pipeline, and whether you can make bad states hard to represent.*

### 1. Definition + analogy
A **record** is the typed object that carries one node or edge from the extractor to the store: `GraphNode` (label, key, properties), `NodeRef` (label and key only, to
name a node), and `GraphEdge` (type, source, target, properties). Each is **checked against the P4-M2 model the moment it is built**: the label must exist, the key must
be exactly the model's key, values must be clean strings, properties must be storable, and an edge must join the kinds of node the model allows. A record that exists is a
record the store can write.

Why not let the database check? Neo4j **Community Edition cannot enforce that a key is present** (P4-M2), so a missing key part would otherwise be written silently.
Validating at the boundary means the mistake fails *next to the code that made it*, with a message that names the problem, instead of surfacing later as a bad node.

**Limits.** It costs a check per record, and a record can only be as right as the model. It checks *shape*, not *truth*: a well-formed but wrong fact passes.

**Analogy.** A post office that refuses a parcel with no address at the counter, instead of finding it undeliverable three cities later.

### For a 14-year-old
When you hand in a form at school, the person at the desk checks it *before* taking it: name filled in? Right class? If not, they hand it straight back and say what's missing.
That's much better than the form getting lost in the office and someone finding a half-empty form next month. The record types are the person at the desk.

### 2. Minimal demo: one valid record and five ways to get it wrong
`c2_validate_at_the_boundary.py`
```python
# C2: validate at the boundary -- a record that exists is a record the store can write. No database needed.
from src.graph.records import GraphEdge, GraphNode, InvalidRecord, NodeRef   # the records built in P4-M3

good = GraphNode("Company", {"name": "Acme"}, {"legal_name": "Acme Inc."})   # valid: a known label, its full key, storable properties
print("valid:", good.label, dict(good.key), dict(good.properties))           # show what it holds

mistakes = {                                                                 # five ways to get it wrong, each wrapped so it runs later
    "unknown label": lambda: GraphNode("Person", {"name": "Avery"}),         # the model has no Person label
    "missing key part": lambda: GraphNode("Contact", {"name": "Bob"}),       # a Contact needs name AND organization
    "padded key": lambda: GraphNode("Product", {"name": "Carllm "}),         # a trailing space would create a ghost node
    "None property": lambda: GraphNode("Product", {"name": "Carllm"}, {"line": None}),  # None would delete the property on write
    "wrong endpoint": lambda: GraphEdge(                                     # a client must be a Company, not a Product
        "WITH_CLIENT", NodeRef("Contract", {"doc_id": "k1"}), NodeRef("Product", {"name": "Carllm"})  # source and target labels
    ),
}
for reason, build in mistakes.items():                                       # try each mistake in turn
    try:                                                                     # each one must be refused
        build()                                                              # construct the bad record
    except InvalidRecord as error:                                           # the record types' own error
        print(f"{reason:<16} ->", error)                                     # the message names what is wrong
```
Real output:
```
valid: Company {'name': 'Acme'} {'legal_name': 'Acme Inc.'}
unknown label    -> GraphNode: unknown label 'Person'; the model has ['Chunk', 'Company', 'Contact', 'Contract', 'Document', 'Employee', 'Location', 'Product']
missing key part -> GraphNode: Contact is identified by ('name', 'organization'), got ('name',)
padded key       -> GraphNode: key 'name' has leading or trailing whitespace ('Carllm '); normalise it before building the record
None property    -> GraphNode Product: property 'line' has unsupported value None; use str, int, float, bool or a list of one of them, and omit the property instead of passing None
wrong endpoint   -> GraphEdge WITH_CLIENT: target must be one of ('Company',), got Product
```

**How to read it.** Each mistake is refused at construction with a message that says what is wrong: an unknown label, a missing part of a composite key, a padded key
(which would create a "ghost" node, C5), a `None` property (which would *delete* the property on write, C4), and an edge to the wrong kind of node.

### 3. Exact project code: the checks and the record types (annotated copy of `src/graph/records.py`)
The model lookups and the error type:
```python
#▸ L25: label -> the key properties the model requires, built once from the P4-M2 registry
25  _KEY_BY_LABEL = {node.label: node.key for node in NODE_TYPES}
#▸ L26: relationship name -> its allowed endpoints and properties
26  _RELATIONSHIP_BY_NAME = {rel.name: rel for rel in RELATIONSHIP_TYPES}
#▸ L29: one error type for "does not fit the model", a ValueError so callers can catch it broadly
29  class InvalidRecord(ValueError):
```
Identity: is the label known, is the key complete, are the values clean?
```python
#▸ L33: validate a label and its key; `record` is only used to name the culprit in messages
33  def _check_identity(label: str, key: Mapping[str, Any], record: str) -> None:
#▸ L34: the key the model requires for this label, or None if the label is unknown
34      key_names = _KEY_BY_LABEL.get(label)
#▸ L35-38: an unknown label: refuse and list the labels that do exist
35      if key_names is None:
36          raise InvalidRecord(
37              f"{record}: unknown label {label!r}; the model has {sorted(_KEY_BY_LABEL)}"
38          )
#▸ L39-40: why this matters: the database cannot enforce key presence on Community, so this is the guard
39      # A missing key part cannot be caught by the database on Community Edition
40      # (decision D-2026-09-20-05), so this is the guard the loader was promised.
#▸ L41: the key must have exactly the model's properties: none missing, none extra
41      if set(key) != set(key_names):
#▸ L42: say which properties the label needs and what was given
42          raise InvalidRecord(f"{record}: {label} is identified by {key_names}, got {tuple(key)}")
#▸ L43: now check each key value
43      for name, value in key.items():
#▸ L44: a key value must be a non-blank string
44          if not isinstance(value, str) or not value.strip():
#▸ L45: refuse, showing the bad value
45              raise InvalidRecord(f"{record}: key {name!r} must be a non-empty string, got {value!r}")
#▸ L46-47: why whitespace is refused: a padded value is a different value to a constraint, so it would create a ghost node
46          # "Carllm " would MERGE into a second, ghost node: a uniqueness constraint
47          # treats it as a different value. Normalising is the extractor's job.
#▸ L48: a key with leading or trailing whitespace...
48          if value != value.strip():
#▸ L49-52: ...is refused, and the message says to normalise first
49              raise InvalidRecord(
50                  f"{record}: key {name!r} has leading or trailing whitespace ({value!r}); "
51                  "normalise it before building the record"
52              )
```
Properties: are they values Neo4j can store?
```python
#▸ L55: can Neo4j store this as a property value?
55  def _is_storable(value: Any) -> bool:
#▸ L56-57: a string, int, float or bool is fine
56      if isinstance(value, _SCALARS):
57          return True
#▸ L58: a list is fine only if all its items have one type
58      if isinstance(value, list):
#▸ L59-60: why type(): bool is a subclass of int, and Neo4j rejects a list mixing the two
59          # type(), not isinstance(): bool is an int subclass, and Neo4j rejects a list
60          # that mixes the two.
#▸ L61: the set of distinct item types in the list
61          kinds = {type(item) for item in value}
#▸ L62: at most one type, and that type must be a storable scalar (an empty list is allowed)
62          return len(kinds) <= 1 and kinds <= set(_SCALARS)
#▸ L63: anything else (None, a dict, a tuple, an object) is not storable
63      return False
...
#▸ L66-68: validate a record's properties; `reserved` is the key, which properties may not repeat
66  def _check_properties(
67      properties: Mapping[str, Any], reserved: Mapping[str, Any], record: str
68  ) -> None:
#▸ L69: check each property in turn
69      for name, value in properties.items():
#▸ L70-71: a property name must be a non-empty string
70          if not isinstance(name, str) or not name:
71              raise InvalidRecord(f"{record}: property names must be non-empty strings, got {name!r}")
#▸ L72-73: a property may not repeat a key part: the key already says it
72          if name in reserved:
73              raise InvalidRecord(f"{record}: {name!r} is part of the identity, not a property")
#▸ L74: the value must be storable
74          if not _is_storable(value):
#▸ L75-76: why None gets a special message: SET += {x: null} deletes x, so "unknown" must be an omitted property
75              # None is called out because it is the likely mistake: `SET n += {x: null}`
76              # would DELETE x, so "unknown" must be an omitted property, not a null.
#▸ L77-81: refuse, and say what to use instead
77              raise InvalidRecord(
78                  f"{record}: property {name!r} has unsupported value {value!r}; use str, int, "
79                  "float, bool or a list of one of them, and omit the property instead of "
80                  "passing None"
81              )
```
The three record types:
```python
#▸ L95: an immutable record with no per-instance dict (C3 explains the "immutable" part)
95  @dataclass(frozen=True, slots=True)
#▸ L96: names a node without describing it
96  class NodeRef:
#▸ L99: the node's label
99      label: str
#▸ L100: the properties of its key
100     key: Mapping[str, str]
#▸ L102: runs right after the dataclass builds the object: this is where validation happens
102     def __post_init__(self) -> None:
#▸ L103: validate the label and the key
103         _check_identity(self.label, self.key, "NodeRef")
#▸ L104: replace the key with a read-only copy (C3)
104         object.__setattr__(self, "key", _frozen(self.key))
#▸ L106-107: prints as Label(prop='value'), readable in error messages
106     def __str__(self) -> str:
107         return f"{self.label}({', '.join(f'{name}={value!r}' for name, value in self.key.items())})"
...
#▸ L110: an immutable record
110 @dataclass(frozen=True, slots=True)
#▸ L111: one node to write
111 class GraphNode:
#▸ L114: its label
114     label: str
#▸ L115: its identity
115     key: Mapping[str, str]
#▸ L116: everything else that describes it; empty by default
116     properties: Mapping[str, PropertyValue] = field(default_factory=dict)
#▸ L118: validate on construction
118     def __post_init__(self) -> None:
#▸ L119: the label and key
119         _check_identity(self.label, self.key, "GraphNode")
#▸ L120: the properties, which may not repeat the key
120         _check_properties(self.properties, self.key, f"GraphNode {self.label}")
#▸ L121: freeze the key (C3)
121         object.__setattr__(self, "key", _frozen(self.key))
#▸ L122: freeze the properties (C3)
122         object.__setattr__(self, "properties", _frozen(self.properties))
#▸ L124: expose ref like an attribute
124     @property
#▸ L125: the node's reference: identity without properties
125     def ref(self) -> NodeRef:
#▸ L126: build it from this node's label and key
126         return NodeRef(self.label, dict(self.key))
...
#▸ L129: an immutable record
129 @dataclass(frozen=True, slots=True)
#▸ L130: one relationship to write
130 class GraphEdge:
#▸ L133: the relationship type, e.g. WITH_CLIENT
133     type: str
#▸ L134: where the arrow starts, by reference
134     source: NodeRef
#▸ L135: where it ends, by reference
135     target: NodeRef
#▸ L136: the edge's own properties; empty by default
136     properties: Mapping[str, PropertyValue] = field(default_factory=dict)
#▸ L138: validate on construction
138     def __post_init__(self) -> None:
#▸ L139: look the type up in the model
139         rel = _RELATIONSHIP_BY_NAME.get(self.type)
#▸ L140-144: an unknown relationship: refuse and list the ones that exist
140         if rel is None:
141             raise InvalidRecord(
142                 f"GraphEdge: unknown relationship {self.type!r}; the model has "
143                 f"{sorted(_RELATIONSHIP_BY_NAME)}"
144             )
#▸ L145-148: check the source and then the target against the labels the model allows
145         for role, ref, allowed in (
146             ("source", self.source, rel.sources),
147             ("target", self.target, rel.targets),
148         ):
#▸ L149-153: an endpoint must be a NodeRef; the message says to use node.ref
149             if not isinstance(ref, NodeRef):
150                 raise InvalidRecord(
151                     f"GraphEdge {self.type}: {role} must be a NodeRef (use node.ref), "
152                     f"got {type(ref).__name__}"
153                 )
#▸ L154-157: the endpoint's label must be one the model allows at that end
154             if ref.label not in allowed:
155                 raise InvalidRecord(
156                     f"GraphEdge {self.type}: {role} must be one of {allowed}, got {ref.label}"
157                 )
#▸ L158-159: why edge properties are restricted: an undeclared one is more likely a typo than a new fact
158         # A closed model: an edge property nobody declared is far more likely a typo
159         # ("titel") than a new fact, and it would be written silently.
#▸ L160: the properties the model does not declare for this relationship
160         undeclared = set(self.properties) - set(rel.properties)
#▸ L161-165: refuse if there are any
161         if undeclared:
162             raise InvalidRecord(
163                 f"GraphEdge {self.type}: undeclared properties {sorted(undeclared)}; "
164                 f"the model declares {rel.properties}"
165             )
#▸ L166: check they are storable (an edge has no key to repeat)
166         _check_properties(self.properties, {}, f"GraphEdge {self.type}")
#▸ L167: freeze the properties (C3)
167         object.__setattr__(self, "properties", _frozen(self.properties))
```

### 4. Interview: what a strong answer contains
- **"Where do you validate data in an ingestion pipeline?"** Strong: *at the boundary*, once, into a type that cannot represent the invalid case ("parse, don't validate"); fail fast with an error that names the
  problem; and do not rely on the database alone, since it may not enforce the rule (here: key presence on Community). Mention that validation checks shape, not truth.
- **"Why not just let the database reject bad data?"** Because the database's error arrives later, names neither the record nor the field, and some rules cannot be expressed in the database at all.

### Micro-exercises
1. Build `GraphNode("Product", {"name": "Carllm"}, {"line": True})` and `{"line": [1, True]}`. Which is accepted, and why?
2. Add a check that refuses a key longer than 200 characters. Which function does it belong in?
3. Why does `GraphEdge` refuse an undeclared property while `GraphNode` accepts any property name?

**Checkpoint:** name three things a record refuses, and say which of them the database could not have caught on Community Edition.

---

## C3 — Frozen is shallow: immutability in Python  **[INTERVIEW: GOOD TO KNOW]**

*A classic Python question; it shows whether you know the difference between rebinding and mutating.*

### 1. Definition + analogy
`@dataclass(frozen=True)` makes assigning to a field raise `FrozenInstanceError`. It does **not** make the *objects* in those fields immutable: if a field holds a `dict`
or a `list`, that dict can still be edited. Immutability is **shallow**. For a record that has been *validated*, this matters: a caller could edit the dict afterwards
and put an invalid value inside a record that already passed its checks.

The fix used here: copy the dict into a `MappingProxyType`, a **read-only view**. The caller's own dict is then separate, and the record's view cannot be written to.
Lists inside stay editable (still shallow), which is a documented limit, not an accident.

Inside a frozen dataclass you cannot write `self.key = ...`, even in `__post_init__`; the copy is installed with `object.__setattr__`, which bypasses the frozen guard.

**Analogy.** Laminating a sheet of paper stops you writing on it, but a photo *clipped* to it can still be swapped for another.

### For a 14-year-old
Imagine a locked box with a label. You can't change the label (that's `frozen`). But if the box holds a notebook, anyone with the box can still write in the notebook. To protect the
notebook, you make a *photocopy* of it, put the photocopy behind glass, and keep the original away from everyone.

### 2. Minimal demo: what frozen does and does not block
`c3_frozen_is_shallow.py`
```python
# C3: frozen=True is shallow, and how the record closes the gap. No database needed.
from dataclasses import FrozenInstanceError, dataclass                       # the error frozen raises, and the decorator

from src.graph.records import GraphNode                                      # the project's record


@dataclass(frozen=True)                                                      # a plain frozen dataclass, as many people write it
class Naive:                                                                 # holds one dict
    tags: dict                                                               # the dict inside is still an ordinary, editable dict


naive = Naive({"a": 1})                                                      # build one
try:                                                                         # re-assigning the field is blocked...
    naive.tags = {}                                                          # ...so this must fail
except FrozenInstanceError:                                                  # the error frozen=True raises
    print("re-assigning the field: blocked")                                 # the part frozen=True does
naive.tags["a"] = 99                                                         # ...but editing the dict inside works
print("editing the dict inside:", naive.tags)                                # the "immutable" object changed

mine = {"line": "auto"}                                                      # the caller's own dict
node = GraphNode("Product", {"name": "Carllm"}, mine)                        # the project's record copies it into a read-only view
mine["line"] = "changed later"                                               # the caller edits their dict after construction
print("caller edit after building:", dict(node.properties))                  # the record is unaffected
try:                                                                         # writing through the record itself...
    node.properties["line"] = "home"                                         # ...is refused
except TypeError:                                                            # a read-only mapping has no item assignment
    print("editing through the record: blocked")                            # the gap the copy closes
```
Real output:
```
re-assigning the field: blocked
editing the dict inside: {'a': 99}
caller edit after building: {'line': 'auto'}
editing through the record: blocked
```

**How to read it.** Re-assigning the field is blocked, but editing the dict inside works. The project's record copies the dict, so a later edit by the caller changes nothing, and
writing through the record's view raises `TypeError`.

### 3. Exact project code: the copy and where it is installed (annotated copy of `src/graph/records.py`)
```python
#▸ L84: turn a mapping into a read-only copy
84  def _frozen(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
#▸ L85-89: why: frozen only stops re-assigning a field, so the dict inside would stay editable; a private read-only copy closes that
85      # INTERVIEW: frozen=True only stops re-assigning a field. The dict inside would
86      # still be editable, so a caller could change a record AFTER it was validated.
87      # A read-only view over a private copy closes that. (Lists inside stay editable:
88      # the guard is shallow, and copying them here only protects against the caller's
89      # own later edits.)
#▸ L90-92: copy every item (copying any list so the caller's list is separate) and wrap the copy in a read-only view
90      return MappingProxyType(
91          {name: list(value) if isinstance(value, list) else value for name, value in mapping.items()}
92      )
...
#▸ L95: frozen: assigning to a field raises FrozenInstanceError
95  @dataclass(frozen=True, slots=True)
#▸ L104: bypass the frozen guard once, inside __post_init__, to install the read-only copy
104         object.__setattr__(self, "key", _frozen(self.key))
#▸ L121: the same for a GraphNode's key
121         object.__setattr__(self, "key", _frozen(self.key))
#▸ L122: and its properties
122         object.__setattr__(self, "properties", _frozen(self.properties))
```

### 4. Interview: what a strong answer contains
- **"Does `frozen=True` make a dataclass immutable?"** Strong: no, it is shallow: it blocks re-binding fields, not mutating a mutable field's contents. Fixes: use immutable types
  (`tuple`, `frozenset`), or a read-only view (`MappingProxyType`), or copy defensively. Bonus: a frozen dataclass whose fields are unhashable (a dict) is not hashable, so it cannot be a dict key or set member.
- **"Why `object.__setattr__`?"** Because a frozen dataclass overrides `__setattr__` to raise; `object.__setattr__` is the escape hatch used by the dataclass machinery itself.

### Micro-exercises
1. Try `node.properties["tags"].append(1)` on a record whose property is a list. Does it work? Why does the code copy lists too?
2. Put a `tuple` in a frozen dataclass instead of a `list`. What changes about mutability and hashing?
3. Remove `_frozen(...)` from one `__post_init__` and run `pytest tests/graph/test_records.py`. Which test fails?

**Checkpoint:** explain the difference between blocking `record.x = 1` and blocking `record.x["k"] = 1`, and how the record blocks both.

---


## C4 — Idempotent upsert: `MERGE` and `SET +=`  **[INTERVIEW: MUST KNOW]**

*Re-runnable ingestion is a core pipeline skill; expect "how do you make this job safe to run twice?".*

### 1. Definition + analogy
An **upsert** is "create it, or update it if it already exists", written as **one idempotent operation**: running it twice leaves the same result as running it once. In Cypher:

| Piece | Does | If you get it wrong |
|---|---|---|
| `MERGE (n:Label {key: $k})` | find the node with this **full key**, or create it | `CREATE` would make a second node on every re-run |
| `SET n += $props` | add these properties and overwrite the ones named; **never delete** others | `SET n = $props` **replaces everything, including the key** |

`MERGE` is only safe and fast because a **uniqueness constraint** (P4-M2) sits under it: the constraint makes "one node per key" a database rule, and its index makes the lookup a seek.
`+=` matters because two documents may each know *part* of an entity: both can write it without erasing each other.

**Limits.** Upsert does not *delete*: a fact removed from a document stays in the graph until something removes it (a later step). And `MERGE` only helps if the key is complete and clean (C2).

**Analogy.** Updating a contact in your phone: if "Acme" exists you add a number to it; you don't create a second Acme, and you don't wipe the address when you add the number.

### For a 14-year-old
You keep a notebook of your friends. When you learn something new about Sam, you first *look for Sam's page*. If it's there, you add the new fact at the bottom. If not, you start a new page. That's `MERGE` (look first) plus `+=` (add, never tear out).
`SET =` would be tearing out the whole page and writing only the new fact, including throwing away Sam's name.

### 2. Minimal demo: merge twice, then replace
`c4_idempotent_upsert.py`
```python
# C4: idempotent upsert -- MERGE on the key, SET += to add properties without deleting any.
import uuid                                                                  # to make a label nothing else uses

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                        # a throwaway label, so cleanup is exact


def show(session, note: str) -> None:                                        # print how many nodes exist and what the first one holds
    rows = session.run(f"MATCH (n:{label}) RETURN properties(n) AS p").value("p")  # every node's properties
    print(f"{note:<28} nodes={len(rows)} properties={rows[0] if rows else None}")  # one line per step


with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.run(f"MERGE (n:{label} {{name: 'Acme'}}) SET n += {{a: 1}}").consume()  # first write: creates the node
        session.run(f"MERGE (n:{label} {{name: 'Acme'}}) SET n += {{b: 2}}").consume()  # second write: finds it, adds b
        show(session, "MERGE twice, SET +=")                                 # one node, name + a + b: nothing lost
        session.run(f"MERGE (n:{label} {{name: 'Acme'}}) SET n = {{c: 3}}").consume()  # SET = replaces every property
        show(session, "then SET =")                                          # the key itself is gone
    finally:                                                                 # always clean up
        session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()          # remove the demo's nodes
```
Real output:
```
MERGE twice, SET +=          nodes=1 properties={'b': 2, 'a': 1, 'name': 'Acme'}
then SET =                   nodes=1 properties={'c': 3}
```

**How to read it.** Writing the same node twice leaves **one** node holding `name`, `a` and `b`: nothing lost. Then `SET =` leaves only `c`: **the key is gone**, so this node can never be found by `MERGE` again.

### 3. Exact project code: how the store upserts a node (annotated copy of `src/retrieval/graph_store.py`)
```python
#▸ L52: build the key part of the pattern, e.g. "name: $k_name" (or two parts for a Contact)
52  def _key_pattern(label: str, prefix: str) -> str:
#▸ L53-54: why splicing is safe here: the names come from the model, never from data; the VALUES are parameters (C7)
53      # The property names come from the model, never from data, so splicing them is
54      # safe; the VALUES are always parameters.
#▸ L55: one "property: $parameter" per key property, in the model's order
55      return ", ".join(f"{name}: ${prefix}_{name}" for name in _KEY_BY_LABEL[label])
...
#▸ L58: map the record's key values to those parameter names
58  def _key_params(key: Mapping[str, str], prefix: str) -> dict[str, str]:
#▸ L59: e.g. {"k_name": "Acme"}
59      return {f"{prefix}_{name}": value for name, value in key.items()}
...
#▸ L115: write one node
115     def upsert_node(self, node: GraphNode) -> None:
#▸ L116: its contract: create it, or add these properties to the existing one
116         """Create the node, or add these properties to the one that already exists."""
#▸ L117-121: why: MERGE finds-or-creates one node (atomic under the constraint); += adds without deleting, while = would wipe the key
117         # INTERVIEW: MERGE on the full key finds-or-creates exactly one node, and the
118         # uniqueness constraint from P4-M2 makes that atomic and index-backed.
119         # `SET n += map` adds and overwrites the properties given but never deletes
120         # the others, so two documents that each know part of an entity can both
121         # write it. `SET n = map` would wipe the key itself.
#▸ L122: MERGE on the full key, then SET n += $props: the whole idea in one line
122         query = f"MERGE (n:{node.label} {{{_key_pattern(node.label, 'k')}}}) SET n += $props"
#▸ L123: the key values and the properties travel as parameters, not text
123         params = {**_key_params(node.key, "k"), "props": dict(node.properties)}
#▸ L124: run it as a managed write transaction (C6) and wait for it to finish
124         self._write(lambda tx: tx.run(query, params).consume())
```

### 4. Interview: what a strong answer contains
- **"How do you make an ingestion job safe to re-run?"** Strong: *deterministic keys* + *upsert* + a *uniqueness constraint*, so a re-run finds the same rows; no `CREATE`/`INSERT` without a key; and say what upsert does **not** do (deletions/stale data need a separate sync).
- **"`MERGE` versus `CREATE`?"** `MERGE` finds-or-creates; `CREATE` always creates. Also: `MERGE` on a *partial* key can create duplicates, which is why the key must be complete.
- **"Why does `SET n = props` cause a bug here?"** It replaces every property, including the key.

### Micro-exercises
1. Run `MERGE (n:L {name: 'x'}) SET n += {a: null}` on a node that has `a`. What happens to `a`? (This is why records refuse `None`.)
2. Replace `MERGE` with `CREATE` in the demo and run it twice. Count the nodes.
3. `MERGE (n:L {name: 'x', extra: 1})` on a node that only has `name`. Does it find it?

**Checkpoint:** explain why `MERGE` plus a uniqueness constraint is safer than `MERGE` alone, and what `+=` protects that `=` destroys.

---

## C5 — Referential integrity: strict endpoints  **[INTERVIEW: GOOD TO KNOW]**

*Shows you think about data quality in graphs, where nothing forces an edge to point at something real.*

### 1. Definition + analogy
**Referential integrity** means a relationship may only join things that exist. In a relational database a foreign key enforces it. In Neo4j Community nothing does, and `MERGE (b:Product {name: 'Carlllm'})` on an *endpoint* will happily **create a new product** for a typo. No uniqueness constraint
objects, because the values differ. The store therefore `MATCH`es both endpoints and refuses (`MissingEndpoint`, naming which one) if either is absent.

**The price:** nodes must be loaded before edges. **The gain:** a misspelling can no longer split one entity into two (a *ghost node*).

**Limits.** It catches a typo only when the typo does not match a real node; a *wrong but existing* name (Carllm instead of Homellm) passes. Catching that is the extractor's job.

**Analogy.** A phone that refuses to save a call log entry for a contact that does not exist, instead of quietly inventing a new contact called "Mumm".

### For a 14-year-old
You write "Sam is friends with Alex" in your notebook. If you can't find Alex's page, you have two choices: quietly make a new page called "Alexx" (with a typo), or stop and say "I don't have an Alex yet". The second is safer, because otherwise you end up with two pages for one person.

### 2. Minimal demo: a typo'd product, refused, versus merged
`c5_strict_endpoints.py`
```python
# C5: referential integrity -- an edge may only join nodes that exist, or a typo becomes a ghost node.
from src.graph.records import GraphEdge, GraphNode, NodeRef                  # the records built in P4-M3
from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)
from src.retrieval.graph_store import GraphStore, MissingEndpoint            # the store and its error

contract = NodeRef("Contract", {"doc_id": "__demo_k1"})                      # a contract that will exist (the prefix marks demo data)
typo = NodeRef("Product", {"name": "__demo_Carlllm"})                        # a product name with a typo, which will not exist


def products(session) -> int:                                                # how many demo products exist
    return session.run("MATCH (p:Product) WHERE p.name STARTS WITH '__demo_' RETURN count(p) AS c").single()["c"]  # count them


with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        store = GraphStore(driver)                                           # refuses to start unless the schema is in force
        store.upsert_node(GraphNode("Contract", {"doc_id": "__demo_k1"}))    # only the contract exists
        try:                                                                 # writing an edge to the typo'd product...
            store.upsert_edge(GraphEdge("FOR_PRODUCT", contract, typo))      # ...must be refused
        except MissingEndpoint as error:                                     # the store's own error
            print("strict store:", error)                                    # it names the missing node
        print("products after the refusal:", products(session))              # 0: no ghost was created
        session.run("MATCH (c:Contract {doc_id: '__demo_k1'}) MERGE (c)-[:FOR_PRODUCT]->(:Product {name: '__demo_Carlllm'})").consume()  # the alternative: MERGE the endpoint
        print("products after a MERGEd endpoint:", products(session))        # 1: the typo is now a real product node
    finally:                                                                 # always clean up
        session.run("MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH '__demo_') DETACH DELETE n").consume()  # remove demo nodes by prefix
```
Real output:
```
strict store: cannot write FOR_PRODUCT: Product(name='__demo_Carlllm') not found; upsert the node first
products after the refusal: 0
products after a MERGEd endpoint: 1
```

**How to read it.** The strict store refuses the edge, names the missing node, and creates **no** product (0). Merging the endpoint instead creates a real `Product` from the typo (1): the ghost.

### 3. Exact project code: `upsert_edge` and its diagnosis (annotated copy of `src/retrieval/graph_store.py`)
```python
#▸ L48: the store's own error for "a node this edge needs does not exist"
48  class MissingEndpoint(LookupError):
#▸ L49: its docstring
49      """An edge was written before one of its two nodes exists."""
...
#▸ L126: write one relationship between two existing nodes
126     def upsert_edge(self, edge: GraphEdge) -> None:
#▸ L127-130: its contract, including that it raises MissingEndpoint
127         """Create the relationship between two nodes that already exist, or update its properties.
128
129         Raises MissingEndpoint if either node is absent.
130         """
#▸ L131: short names for the two endpoint references
131         source, target = edge.source, edge.target
#▸ L132-134: why MATCH, not MERGE: MERGE would create a stub for a typo and split one product in two; the price is load order
132         # INTERVIEW: the endpoints are MATCHed, not MERGEd. MERGE would quietly create
133         # a stub node for a typo ("Carlllm") and split one product into two, which no
134         # constraint can catch. The price is load order: nodes before edges.
#▸ L135: build the query text
135         query = (
#▸ L136: find the source node by its label and full key; if it does not exist, nothing below runs
136             f"MATCH (a:{source.label} {{{_key_pattern(source.label, 'a')}}}) "
#▸ L137: find the target node the same way
137             f"MATCH (b:{target.label} {{{_key_pattern(target.label, 'b')}}}) "
#▸ L138: create the relationship if it is absent (an idempotent write, C4) and keep it as `r`...
138             f"MERGE (a)-[r:{edge.type}]->(b) SET r += $props "
#▸ L139: why RETURN count(r): counting always returns a row, so "nothing matched" comes back as 0, not as no rows
139             # Aggregating makes zero matches return one row holding 0, not no rows.
#▸ L140: ...and report how many relationships were written or found
140             "RETURN count(r) AS written"
#▸ L141: end of the query text
141         )
#▸ L142-146: the endpoints' key values (prefixed a_ and b_ so they cannot collide) and the edge's properties, all as parameters
142         params = {
143             **_key_params(source.key, "a"),
144             **_key_params(target.key, "b"),
145             "props": dict(edge.properties),
146         }
#▸ L148: the function the driver will run inside a transaction (C6)
148         def work(tx: ManagedTransaction) -> None:
#▸ L149: run the query and read the one row it returns; 0 means an endpoint was missing
149             if tx.run(query, params).single(strict=True)["written"] == 0:
#▸ L150: explain which one and raise; raising inside the transaction function rolls it back
150                 raise MissingEndpoint(self._explain_missing(tx, edge, params))
#▸ L152: run it as a managed write transaction
152         self._write(work)
...
#▸ L154-156: on failure only, work out WHICH endpoint is missing so the message can say so
154     def _explain_missing(
155         self, tx: ManagedTransaction, edge: GraphEdge, params: dict[str, Any]
156     ) -> str:
#▸ L157-162: one read query that checks each endpoint separately with OPTIONAL MATCH and reports whether it was found
157         found = tx.run(
158             f"OPTIONAL MATCH (a:{edge.source.label} {{{_key_pattern(edge.source.label, 'a')}}}) "
159             f"OPTIONAL MATCH (b:{edge.target.label} {{{_key_pattern(edge.target.label, 'b')}}}) "
160             "RETURN a IS NOT NULL AS source_found, b IS NOT NULL AS target_found",
161             params,
162         ).single(strict=True)
#▸ L163-170: collect the references of the endpoints that were not found
163         missing = [
164             str(ref)
165             for ref, present in (
166                 (edge.source, found["source_found"]),
167                 (edge.target, found["target_found"]),
168             )
169             if not present
170         ]
#▸ L171: both exist now...
171         if not missing:
#▸ L172: ...which can only mean one was created between the two queries
172             # Both nodes exist now, so one was created between the two queries.
#▸ L173: so say retry
173             return f"cannot write {edge.type}: an endpoint was created while writing; retry"
#▸ L174: otherwise name the missing node(s) and say to upsert them first
174         return f"cannot write {edge.type}: {' and '.join(missing)} not found; upsert the node first"
```

### 4. Interview: what a strong answer contains
- **"What happens when a relationship points at something that doesn't exist yet?"** Strong: name the two policies (lenient: create a stub; strict: refuse), what each risks (ghost/duplicate entities vs load-order coupling), and pick with a reason. Bonus: this is the graph version of a foreign key.
- **"How do typos become duplicate entities?"** Exact-match keys treat "Carlllm" as a new value; the defence is normalisation, a closed vocabulary, and strict endpoints.

### Micro-exercises
1. Upsert the edge in the other order (edge first, then both nodes). What happens, and what does it say about load order?
2. Make the extractor's typo *valid* by naming another real product. Does the store catch it? What would?
3. Why does the failure diagnosis run a *second* query instead of the first one telling you which end is missing?

**Checkpoint:** state one thing strict endpoints prevent and one thing they cannot.

---

## C6 — Managed transactions and retries  **[INTERVIEW: GOOD TO KNOW]**

*Backend and data-engineering depth: what a database does when a transaction fails, and what that demands of your code.*

### 1. Definition + analogy
A **managed transaction** (`session.execute_write(fn)` / `execute_read(fn)`) hands your *function* to the driver, which runs it in a transaction and **re-runs the whole function** if it fails
with a *transient* error (a deadlock, a leader change). That is why the function must be **idempotent**: a `MERGE` finds whatever an earlier, half-finished attempt already made, while a `CREATE` would duplicate it.

Two rules follow. **Do only database work inside the function** (a retried function that also sends an email or calls an LLM would do it twice). And **copy results out inside it**: a `Result` cannot be read after its transaction closes.

**Limits.** The driver retries only errors it classes as transient, for a bounded time; a wrong query or a constraint violation is not retried.

**Analogy.** Handing a courier a sealed job card instead of doing the errand yourself: if the door is locked, the courier comes back later and repeats the *whole* card, so the card must be safe to repeat.

### For a 14-year-old
You tell a helper: "Write my name on the sign-in sheet." If the pen runs out halfway, the helper starts over. If the instruction is "add one more tally mark", starting over gives *two* marks: a mess. If it is "make sure my name is on the sheet", starting over is harmless. Managed transactions are the helper; your instruction must be the safe kind.

### 2. Minimal demo: fail once on purpose and watch the driver run the function again
`c6_managed_transaction_retry.py`
```python
# C6: a managed transaction re-runs its function on a transient failure, so the function must be idempotent.
from neo4j.exceptions import TransientError                                  # the kind of error the driver treats as "try again"

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

attempts = []                                                                # one entry per time the driver runs our function


def work(tx):                                                                # the transaction function handed to the driver
    attempts.append(1)                                                       # record this attempt
    tx.run("MERGE (n:DemoRetry {k: 1})").consume()                           # an idempotent write: MERGE finds what an earlier attempt made
    if len(attempts) == 1:                                                   # on the first attempt only...
        raise TransientError("simulated deadlock")                           # ...fail as a deadlock would


with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.execute_write(work)                                          # run it as a managed write transaction
        print("times the function ran:", len(attempts))                      # more than once: the driver retried it
        nodes = session.run("MATCH (n:DemoRetry) RETURN count(n) AS c").single()["c"]  # how many nodes exist afterwards
        print("nodes created:", nodes)                                       # 1: the retry did not duplicate the write
    finally:                                                                 # always clean up
        session.run("MATCH (n:DemoRetry) DETACH DELETE n").consume()         # remove the demo's node
```
Real output:
```
Transaction failed and will be retried in 1.1173265421130039s (simulated deadlock)
times the function ran: 2
nodes created: 1
```

**How to read it.** The function raised a transient error on its first attempt; the driver logged that it would retry, waited about a second, and ran it again: **2 runs, 1 node**. The write was idempotent, so the retry did not duplicate it.

### 3. Exact project code: the two transaction helpers (annotated copy of `src/retrieval/graph_store.py`)
```python
#▸ L25: a type variable: _write returns whatever the transaction function returns
25  T = TypeVar("T")
...
#▸ L254: run a function as a managed WRITE transaction
254     def _write(self, work: Callable[[ManagedTransaction], T]) -> T:
#▸ L255: open a session on the chosen database and close it when done (sessions are cheap; the driver holds the connection pool)
255         with self._driver.session(database=self._database) as session:
#▸ L256-259: why: the driver retries `work` on transient failures, which is safe only because every write here is an idempotent MERGE
256             # INTERVIEW: execute_write runs `work` in a managed transaction and RETRIES
257             # it on transient failures (a deadlock, a leader change). That is only safe
258             # because every write here is idempotent: MERGE finds whatever an earlier,
259             # half-finished attempt already made.
#▸ L260: hand the function to the driver and return its result
260             return session.execute_write(work)
...
#▸ L262: run a query as a managed READ transaction and return plain rows
262     def _read(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
#▸ L263: the function the driver will run
263         def work(tx: ManagedTransaction) -> list[dict[str, Any]]:
#▸ L264-265: why: a Result cannot be read once its transaction has closed
264             # Rows are copied out inside the transaction: a Result cannot be read
265             # once its transaction has closed.
#▸ L266: run the query and convert every record to a plain dict inside the transaction
266             return [record.data() for record in tx.run(query, params)]
#▸ L268: open a session
268         with self._driver.session(database=self._database) as session:
#▸ L269: run it as a managed read transaction
269             return session.execute_read(work)
```

### 4. Interview: what a strong answer contains
- **"How do you make retries safe?"** Strong: make the operation idempotent (deterministic keys, upsert); keep side effects (emails, LLM calls, payments) *outside* the retried block or guard them with an idempotency key; bound retries and back off; do not retry non-transient errors.
- **"What is a deadlock and what do you do about it?"** Two transactions each hold something the other needs; the database aborts one with a transient error, and the right response is to retry it.

### Micro-exercises
1. In the demo, replace `MERGE` with `CREATE` and count the nodes. What does that show?
2. Raise a non-transient error (`ValueError`) on the first attempt. Does the driver retry?
3. Put a `print` of the current time inside `work` and read the gap between the two attempts.

**Checkpoint:** say what "the function must be idempotent" means, and what goes wrong if it is not.

---


## C7 — Parameters vs structure: injection safety  **[INTERVIEW: MUST KNOW]**

*Once an LLM can write your queries ("text-to-Cypher", "text-to-SQL"), injection stops being a web-app topic and becomes an AI-engineer topic.*

### 1. Definition + analogy
**Injection** happens when input escapes the position it was meant for and changes what a statement *does*. The defence has two halves:

| Half | Rule | Why it works |
|---|---|---|
| **Values** | always send as **parameters** (`$name`), never format them into the text | the server binds a parameter *after* parsing, so it can only ever be data |
| **Structure** (labels, relationship types, a hop bound) | cannot be parameters (observed: `MATCH (n:$label)` is a syntax error), so splice them **only if they come from the model**, never from data | the set of possible values is closed and known |

That is exactly how the store is written: labels, relationship types and the hop count are spliced from the model and validated integers; every name, key and property **value** is a parameter.

A second line of defence for *LLM-written* queries: run them in a **read transaction**. On the live server a write attempted inside `execute_read` is refused (`Neo.ClientError.Statement.AccessMode`), so a hostile or mistaken query cannot change data.

**Limits.** Parameters do not stop a *legitimate but expensive* query, so add limits and timeouts. Community Edition has no role-based access, so the read transaction is a real but partial control.

**Analogy.** A form with "special instructions". If the kitchen *obeys* whatever is written in it, anyone can order everything free. Values go in a box the kitchen treats as *data*; the dish itself must come from the menu.

### For a 14-year-old
Your friend runs a shop and follows written orders. If an order says "buy **X**", and X is filled in by a stranger, the stranger can write "X, and also empty the till". The safe way: X must be *one item from the shop's own list*, and anything the stranger writes is only ever read as an item name, never as an instruction.

### 2. Minimal demo: a hostile value as text and as a parameter, then two limits
`c7_parameters_vs_structure.py`
```python
# C7: send VALUES as parameters (they stay data); only names from the model may be spliced into query text.
from neo4j.exceptions import Neo4jError                                      # base class of every error the server reports

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

hostile = "x'}) DETACH DELETE n //"                                          # a "name" that tries to close the pattern and add a clause
unsafe = f"MATCH (n:Product {{name: '{hostile}'}}) RETURN count(n) AS c"     # what plain string formatting would build (printed, never run)
print("formatted into the text:", unsafe)                                    # the hostile value has become part of the query

with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    safe = session.run(                                                      # the same lookup with the value as a parameter
        "MATCH (n:Product {name: $name}) RETURN count(n) AS c", name=hostile  # $name is bound after parsing, so it can only be data
    ).single()["c"]                                                          # the count of products with that exact name
    print("passed as a parameter:", safe, "matches, nothing executed")      # 0: it was just a string that matched nothing
    try:                                                                     # a LABEL, however, cannot be a parameter...
        session.run("MATCH (n:$label) RETURN n", label="Product").consume()  # ...so this is refused
    except Neo4jError as error:                                              # the server rejects it while parsing
        print("label as a parameter:", type(error).__name__)                 # a syntax error: labels must be spliced, so they must come from the model
    try:                                                                     # a query written by an LLM should run read-only
        session.execute_read(lambda tx: tx.run("CREATE (:DemoReadOnly)").consume())  # a write attempted inside a read transaction
    except Neo4jError as error:                                              # the server refuses it
        print("write in a read transaction:", error.code)                    # AccessMode: a second line of defence
```
Real output:
```
formatted into the text: MATCH (n:Product {name: 'x'}) DETACH DELETE n //'}) RETURN count(n) AS c
passed as a parameter: 0 matches, nothing executed
label as a parameter: CypherSyntaxError
write in a read transaction: Neo.ClientError.Statement.AccessMode
```

**How to read it.** Formatted into the text, the hostile value **rewrites the query** (`DETACH DELETE n` is now part of it; this line was printed, never run). Passed as a parameter it is just a string that matched nothing. A label cannot be a parameter, so labels must come from the model. And a write inside a read transaction is refused by the server.

### 3. Exact project code: what is spliced and what is a parameter (annotated copy of `src/retrieval/graph_store.py`)
```python
#▸ L52: the key part of a pattern: property NAMES are spliced, VALUES are parameters
52  def _key_pattern(label: str, prefix: str) -> str:
#▸ L53-54: why it is safe: the names come from the model, never from data
53      # The property names come from the model, never from data, so splicing them is
54      # safe; the VALUES are always parameters.
#▸ L55: "name: $k_name": the value is the parameter $k_name, not text
55      return ", ".join(f"{name}: ${prefix}_{name}" for name in _KEY_BY_LABEL[label])
...
#▸ L62: the entity lookup, one MATCH per label
62  def _lookup(labels: Sequence[str]) -> str:
#▸ L63-66: why labels are spliced: a label cannot be a parameter, so it must come from the model; one MATCH per label keeps each an index seek
63      # INTERVIEW: a label cannot be a query parameter, so labels are spliced in, which
64      # is safe only because they come from the model. One MATCH per label, joined by
65      # UNION, so each is an index seek on that label's constraint; a label-less
66      # `MATCH (n {name: $name})` would scan every node in the graph.
#▸ L67: one "MATCH (e:Label {name: $name}) RETURN e" per label: the label is spliced, the NAME is a parameter
67      return " UNION ".join(f"MATCH (e:{label} {{name: $name}}) RETURN e" for label in labels)
...
#▸ L229: the hop bound is spliced into the text, so it must be a real integer in range (a bool is an int in Python, so it is refused explicitly)
229         if isinstance(hops, bool) or not isinstance(hops, int) or not 1 <= hops <= MAX_HOPS:
#▸ L230: refuse anything else before a query is built
230             raise ValueError(f"hops must be an integer from 1 to {MAX_HOPS}, got {hops!r}")
...
#▸ L239: the relationship types, spliced: they come from the model
239         types = "|".join(DOMAIN_RELATIONSHIPS)
#▸ L243: the only place text is built from the model: types and the checked integer hops
243             f"MATCH p = (s)-[:{types}*1..{hops}]-(t) WHERE NOT t IN starts "
...
#▸ L250: the start's name and the limit go to the server as PARAMETERS, in a dict separate from the query text
250         return [row["entity"] for row in self._read(query, {"name": name, "limit": limit})]
...
#▸ L262: every read takes the query text and its parameters as two separate arguments, so values never reach the text
262     def _read(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
```

### 4. Interview: what a strong answer contains
- **"How do you stop an LLM-generated query doing damage?"** Strong, in layers: (1) never let the LLM write *values into text*: use parameters, or have it emit a structured request you turn into a query; (2) allow-list labels, relationship types and properties from the schema, and validate identifiers; (3) run in a *read-only* transaction or role; (4) cap results and time; (5) log and review. Mention that identifiers cannot be parameterised, which is why the allow-list matters.
- **"Why can't you parameterise a table or label name?"** The parser needs the name to plan the query before any parameter is bound.
- **"What is second-order injection?"** Stored data later used to build a query as text; the same rule applies to data read from the database.

### Micro-exercises
1. Change the demo's `hostile` string and print the "formatted" query again. What does the server see?
2. Try `MATCH (n:$($label)) RETURN n` with a parameter. It is accepted for queries in 5.26: why is it still not a substitute for an allow-list?
3. Which parts of `multi_hop_search`'s query text are not fixed strings, and where does each come from?

**Checkpoint:** name what may be spliced into a query and what may never be, and the second layer of defence for LLM-written queries.

---

## C8 — Entity lookup: namesakes, exact match, and provenance edges  **[INTERVIEW: MUST KNOW]**

*Entity linking, connecting a user's words to the right node, is where graph RAG most often fails in practice.*

### 1. Definition + analogy
`search_related(name)` finds a node **by its exact `name`** and returns every edge attached to it, with the edge's real direction. Three design points make it correct:

1. **Namesakes are all returned unless a label narrows it.** "Robert Chen" is both an Insurellm `Employee` and a client's `Contact`. Guessing one would be a silent wrong answer, so all are searched and `label="Employee"` narrows it.
2. **Domain edges vs provenance edges.** *Domain* edges say something about the world (`WORKS_ON`, `FOR_PRODUCT`). *Provenance* edges (`MENTIONS`, `HAS_CHUNK`, `DESCRIBED_IN`) say where a fact was *read*. Returning provenance edges by default would bury a product's real relationships under every chunk that merely names it, so they are **off unless asked for**.
3. **Exact, case-sensitive match on `name`.** It is the simplest thing an entity linker can be built against, and one index seek per label. Fuzzy matching is a *later* step; the price is that `carllm` will not match `Carllm`.

**Limits.** Only labels with a `name` can be looked up (a `Contract` has none and is reached through neighbours). Nothing here *links a question's words* to a name: that is the next step.

**Analogy.** Looking a person up in a company directory: two "Robert Chen" entries must both appear, each with their department, or you email the wrong one.

### For a 14-year-old
Two different kids in the school are called Sam. If someone asks "what is Sam's favourite game?", the honest answer is "which Sam?", or show both Sams' answers. Picking one at random would give a confident wrong answer. Also, a "Sam's page" listing every place anyone *mentioned* Sam would be huge, so by default you show the real facts about Sam and keep the "mentioned in" list for when you ask.

### 2. Minimal demo: namesakes, a label, and provenance on and off
`c8_entity_lookup.py`
```python
# C8: looking an entity up by name -- namesakes, and why domain edges and provenance edges are kept apart.
from src.graph.records import GraphEdge, GraphNode, NodeRef                  # the records built in P4-M3
from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)
from src.retrieval.graph_store import GraphStore                             # the store under study

N = "__demo_Robert Chen"                                                     # one name, shared by two different people (the prefix marks demo data)
with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        store = GraphStore(driver)                                           # refuses to start unless the schema is in force
        product = NodeRef("Product", {"name": "__demo_Homellm"})             # a product
        employee = NodeRef("Employee", {"name": N})                          # an Insurellm engineer called Robert Chen
        contact = NodeRef("Contact", {"name": N, "organization": "Fortress"})  # a client's officer with the same name
        contract = NodeRef("Contract", {"doc_id": "__demo_k1"})              # a contract the client's officer signed
        chunk = NodeRef("Chunk", {"chunk_id": "__demo_about_0"})             # a chunk of text that mentions the product
        for ref in (product, employee, contact, contract, chunk):            # write every node first: edges need both ends to exist
            store.upsert_node(GraphNode(ref.label, dict(ref.key)))           # one upsert per node
        store.upsert_edge(GraphEdge("WORKS_ON", employee, product))          # the engineer works on the product
        store.upsert_edge(GraphEdge("SIGNED_BY", contract, contact, {"title": "Chief Underwriting Officer"}))  # the officer signed the contract
        store.upsert_edge(GraphEdge("MENTIONS", chunk, product))             # a provenance edge: where the product was read

        both = store.search_related(N)                                       # by name alone: every node with that name is searched
        print("namesakes, no label :", [e.type for e in both])               # both people's edges come back
        only = store.search_related(N, label="Employee")                     # a label narrows it to one person
        print("label='Employee'    :", [e.type for e in only])               # just the engineer's edge

        plain = store.search_related("__demo_Homellm")                       # a product's edges, default
        print("product, default    :", [e.type for e in plain])              # domain edges only: no MENTIONS
        full = store.search_related("__demo_Homellm", include_provenance=True)  # a product's edges, provenance included
        print("include_provenance  :", [e.type for e in full])               # MENTIONS appears
    finally:                                                                 # always clean up
        session.run("MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH '__demo_') DETACH DELETE n").consume()  # remove demo nodes by prefix
```
Real output:
```
namesakes, no label : ['SIGNED_BY', 'WORKS_ON']
label='Employee'    : ['WORKS_ON']
product, default    : ['WORKS_ON']
include_provenance  : ['MENTIONS', 'WORKS_ON']
```

**How to read it.** Searching the shared name returns the edges of **both** people (`SIGNED_BY` and `WORKS_ON`); adding `label="Employee"` returns only `WORKS_ON`. For the product, the default returns only the domain edge; `include_provenance=True` also returns `MENTIONS`.

### 3. Exact project code: the lookup rules (annotated copy of `src/retrieval/graph_store.py`)
The constants that separate the two kinds of edge, and which labels have names:
```python
#▸ L33: the labels that record where text came from (Document, Chunk)
33  _PROVENANCE_LABELS = frozenset(node.label for node in NODE_TYPES if node.layer == "provenance")
#▸ L35-37: why the split exists: provenance edges return every passage that merely mentions an entity and make co-mentioned entities look adjacent
35  # A relationship is provenance if either end is a Document or a Chunk. Those record
36  # WHERE a fact was read; following them from an entity returns every passage that
37  # merely mentions it, and makes two entities named in one chunk look 2 hops apart.
#▸ L38-40: every relationship that touches neither a Document nor a Chunk
38  DOMAIN_RELATIONSHIPS = tuple(
39      rel.name for rel in RELATIONSHIP_TYPES if not _PROVENANCE_LABELS & {*rel.sources, *rel.targets}
40  )
#▸ L41: every relationship, used when provenance is requested
41  ALL_RELATIONSHIPS = tuple(rel.name for rel in RELATIONSHIP_TYPES)
#▸ L43-44: why only some labels: a Contract, Document or Chunk has no name to look up
43  # Only labels whose identity includes a name can be looked up by name. Contract,
44  # Document and Chunk are reached through their neighbours instead.
#▸ L45: the labels whose key includes `name`
45  NAMED_LABELS = tuple(node.label for node in NODE_TYPES if "name" in node.key)
```
Turning a name into nodes, and rows into records:
```python
#▸ L70-75: an ORDER BY key that works for every kind of node, so results have one fixed order
70  def _sort_key(variable: str) -> str:
71      # Nodes have different identity properties, and ORDER BY must be total or the
72      # same question could return its edges in a different order on the next run.
73      return (
74          f"coalesce({variable}.name, {variable}.title, {variable}.doc_id, {variable}.chunk_id, '')"
75      )
#▸ L78: rebuild a NodeRef from a node's labels and properties
78  def _node_ref(labels: Sequence[str], properties: Mapping[str, Any]) -> NodeRef:
#▸ L79: try each label the node has
79      for label in labels:
#▸ L80: the key the model gives this label, or None if it is not a model label
80          key_names = _KEY_BY_LABEL.get(label)
#▸ L81: use the first model label whose whole key is present
81          if key_names and all(name in properties for name in key_names):
#▸ L82: build the reference from just the key properties
82              return NodeRef(label, {name: properties[name] for name in key_names})
#▸ L83-85: if no label matches the node is outside the model: raise, so retrieval is never silently incomplete
83      # Failing loudly: a node from outside the model means someone wrote to this graph
84      # by hand, and hiding it would make a retrieval answer silently incomplete.
85      raise ValueError(f"a node with labels {list(labels)} is not part of the graph model")
...
#▸ L272: validate a name and remove surrounding spaces
272 def _clean_name(name: str) -> str:
#▸ L273-274: why trimming: a stored key cannot have padding, so a padded query could only miss
273     # Keys can never carry surrounding whitespace (records refuse it), so a query
274     # that does could only ever miss; trimming turns a typo into a hit.
#▸ L275: trim it if it is a string, otherwise treat it as empty
275     cleaned = name.strip() if isinstance(name, str) else ""
#▸ L276-277: refuse an empty name
276     if not cleaned:
277         raise ValueError("an entity name must be a non-empty string")
#▸ L278: the cleaned name
278     return cleaned
...
#▸ L281: which labels to look in
281 def _labels_to_search(label: str | None) -> tuple[str, ...]:
#▸ L282-283: no label given: search every label that has a name
282     if label is None:
283         return NAMED_LABELS
#▸ L284-285: a label with no name cannot be searched by name: refuse and list the ones that can
284     if label not in NAMED_LABELS:
285         raise ValueError(f"cannot look up {label!r} by name; labels with a name are {NAMED_LABELS}")
#▸ L286: otherwise search only that label
286     return (label,)
```
The method itself:
```python
#▸ L192: clean and validate the name
192         name = _clean_name(entity_name)
#▸ L193: validate the limit
193         _check_limit(limit)
#▸ L194: the MATCH-per-label text for the labels to search
194         lookup = _lookup(_labels_to_search(label))
#▸ L195: domain relationships by default, all of them if provenance was asked for
195         relationships = ALL_RELATIONSHIPS if include_provenance else DOMAIN_RELATIONSHIPS
#▸ L196: build the query
196         query = (
#▸ L197: find the start node(s): every node with this name in the chosen labels
197             f"CALL {{ {lookup} }} "
#▸ L198-199: why no arrow: "connected to" means both directions, and each edge still reports its true source and target
198             # The pattern has no arrow because "connected to" means both directions;
199             # each edge still reports its real source and target.
#▸ L200: every relationship attached to a found node, limited to the allowed types
200             "MATCH (e)-[r]-() WHERE type(r) IN $types "
#▸ L201: why DISTINCT: two matched nodes joined to each other would otherwise return the same edge twice
201             # Two namesakes joined to each other would otherwise return the same edge twice.
#▸ L202: one row per relationship
202             "WITH DISTINCT r "
#▸ L203: name the relationship's real start and end nodes
203             "WITH r, startNode(r) AS s, endNode(r) AS t "
#▸ L204-205: return the type, both nodes' labels and properties, and the edge's properties
204             "RETURN type(r) AS rel_type, labels(s) AS s_labels, properties(s) AS s_props, "
205             "labels(t) AS t_labels, properties(t) AS t_props, properties(r) AS r_props "
#▸ L206: a total order: type, then source, then target, then a limit
206             f"ORDER BY rel_type, {_sort_key('s_props')}, {_sort_key('t_props')} LIMIT $limit"
#▸ L207: end of the query
207         )
#▸ L208: run it as a managed read (C6); the name, types and limit are parameters (C7)
208         rows = self._read(query, {"name": name, "types": list(relationships), "limit": limit})
#▸ L209-217: turn each row into a GraphEdge (validated by C2's checks)
209         return [
210             GraphEdge(
211                 row["rel_type"],
212                 _node_ref(row["s_labels"], row["s_props"]),
213                 _node_ref(row["t_labels"], row["t_props"]),
214                 row["r_props"],
215             )
216             for row in rows
217         ]
```

### 4. Interview: what a strong answer contains
- **"How do you connect a user's question to the right node?"** Strong: *entity linking*: extract candidate mentions, normalise, look up (exact, then fuzzy or embedding-based), and **handle ambiguity explicitly** (return the candidates, disambiguate with context, or ask). Say that a graph is only as good as this step, and that returning one guess for a namesake is a silent error.
- **"Why keep provenance separate from the domain graph?"** So traversals and "related to" answers are about the world, while provenance answers "where was this said?", and the two are joined only at the chunk id.

### Micro-exercises
1. Add a `Company` and a `Location` with the same name and search it. How many nodes match, and why does the edge between them come back once?
2. Search `carllm` (lower case). What do you get, and where would normalisation belong?
3. List the labels that cannot be looked up by name, and say how you would still reach them.

**Checkpoint:** explain why the lookup returns both people for a shared name, and why `MENTIONS` is off by default.

---

## C9 — Multi-hop paths: routes vs entities, distance, and caps  **[INTERVIEW: MUST KNOW]**

*The failure mode of graph retrieval: interviewers ask how you keep "connected to" meaningful and cheap.*

### 1. Definition + analogy
`multi_hop_search(start, hops)` returns the names of everything within `hops` steps of the start, **nearest first**, following domain relationships in either direction. Its rules, each learned from a real case:

- **Paths are not entities.** A pattern like `(a)-[*1..2]-(t)` enumerates *routes*. Two routes to the same node are two rows; the query must collapse them to one entity. Cost grows with the number of routes, not the number of entities.
- **Shortest distance wins.** A node reachable at 1 and at 3 hops is at distance 1 and is listed once.
- **The start is never returned**, even when a route leads back to it (a founder, their company and its head-office city form a triangle).
- **A hop cap (3).** Each extra hop multiplies the routes, and by the fourth "connected" reaches most of the graph.
- **Domain edges only.** Walking provenance edges would make two entities named in one chunk look 2 hops apart.
- **Nodes with no name are walked *through* but not returned.** A contract links a product to its client, so it is a step on the path even though it has no name to show.

**Limits.** Path enumeration is fine at this size; on a large graph the exit is APOC's path expander, which visits each node once. Hubs (a city that many employees live in) make "2 hops" broad, and nothing excludes them yet.

**Analogy.** Asking "who could I reach through friends of friends?": the same person can be reached by several chains, but you list them once, at the shortest chain, and you never list yourself.

### For a 14-year-old
Ask "who are all the people connected to me within three handshakes?". Two different chains might lead to the same person: you still count them once, at the *shortest* chain. You don't count yourself, even if a chain loops back to you. And you stop at three handshakes, because by the tenth handshake it's practically everyone in the world, which tells you nothing.

### 2. Minimal demo: routes vs entities, the triangle, and the cap
`c9_multi_hop_paths.py`
```python
# C9: variable-length paths -- routes vs entities, shortest distance, the start excluded, and a hop cap.
import uuid                                                                  # to make a label nothing else uses

from src.graph.records import GraphEdge, GraphNode, NodeRef                  # the records built in P4-M3
from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)
from src.retrieval.graph_store import GraphStore                             # the store under study

label = f"Demo{uuid.uuid4().hex[:8]}"                                        # a throwaway label for the raw-Cypher part
with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.run(f"CREATE (a:{label} {{n:'A'}})-[:R]->(:{label} {{n:'B'}})-[:R]->(d:{label} {{n:'D'}}), (a)-[:R]->(:{label} {{n:'C'}})-[:R]->(d)").consume()  # a diamond: two routes from A to D
        counts = session.run(                                                # count routes and entities for the same question
            f"MATCH p = (:{label} {{n:'A'}})-[*1..2]-(t:{label} {{n:'D'}}) RETURN count(p) AS routes, count(DISTINCT t) AS entities"  # A to D within 2 hops
        ).single()                                                           # one row
        print("A to D:", counts.data())                                      # 2 routes, but 1 entity: paths are not nodes

        store = GraphStore(driver)                                           # the store under study
        founder = NodeRef("Employee", {"name": "__demo_Avery"})              # a founder
        company = NodeRef("Company", {"name": "__demo_Insurellm"})           # the company they founded
        city = NodeRef("Location", {"name": "__demo_San Francisco"})        # its head-office city, where the founder also lives
        for ref in (founder, company, city):                                 # write the nodes first
            store.upsert_node(GraphNode(ref.label, dict(ref.key)))           # one upsert per node
        store.upsert_edge(GraphEdge("FOUNDED", founder, company))            # founder -> company
        store.upsert_edge(GraphEdge("HAS_OFFICE", company, city, {"headquarters": True}))  # company -> city
        store.upsert_edge(GraphEdge("BASED_IN", founder, city, {"remote": False}))  # founder -> city: a triangle
        print("3 hops from the founder:", store.multi_hop_search("__demo_Avery", 3))  # both neighbours once, at distance 1, and never the founder
        try:                                                                 # more hops than the cap...
            store.multi_hop_search("__demo_Avery", 4)                        # ...is refused
        except ValueError as error:                                          # the store's guard against path explosion
            print("4 hops:", error)                                          # it states the cap
    finally:                                                                 # always clean up
        session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()          # remove the raw-Cypher nodes
        session.run("MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH '__demo_') DETACH DELETE n").consume()  # remove demo nodes by prefix
```
Real output:
```
A to D: {'routes': 2, 'entities': 1}
3 hops from the founder: ['__demo_Insurellm', '__demo_San Francisco']
4 hops: hops must be an integer from 1 to 3, got 4
```

**How to read it.** The diamond has **2 routes but 1 entity** from A to D. The founder's triangle returns each neighbour **once** and never the founder. Four hops is refused with the cap in the message.

### 3. Exact project code: `multi_hop_search` and its guards (annotated copy of `src/retrieval/graph_store.py`)
```python
#▸ L27-28: why the cap: each hop multiplies the routes, and a fourth already reaches most of this graph
27  # Every extra hop multiplies the paths Neo4j has to enumerate, and on this graph a
28  # fourth hop already reaches most of it: "connected" stops meaning anything.
#▸ L29: the hop cap
29  MAX_HOPS = 3
#▸ L30: the default number of results
30  DEFAULT_LIMIT = 50
...
#▸ L88: validate a limit
88  def _check_limit(limit: int) -> None:
#▸ L89: a limit must be a positive integer (a bool is an int in Python, so it is refused explicitly)
89      if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
#▸ L90: refuse anything else
90          raise ValueError(f"limit must be a positive integer, got {limit!r}")
...
#▸ L219: up to N hops from an entity
219     def multi_hop_search(
#▸ L229: the hop count must be an integer from 1 to the cap
229         if isinstance(hops, bool) or not isinstance(hops, int) or not 1 <= hops <= MAX_HOPS:
#▸ L230: refuse anything else, stating the cap
230             raise ValueError(f"hops must be an integer from 1 to {MAX_HOPS}, got {hops!r}")
#▸ L231: clean the start's name
231         name = _clean_name(start_entity)
#▸ L232: validate the limit
232         _check_limit(limit)
#▸ L233: the MATCH-per-label text for the start node(s)
233         lookup = _lookup(_labels_to_search(label))
#▸ L234-238: why: the pattern enumerates PATHS, so cost grows with routes; the cap and domain-only edges keep it small, and APOC's path expander is the exit
234         # INTERVIEW: a variable-length pattern enumerates PATHS, not nodes, so cost
235         # grows with the number of routes between entities, not with the number of
236         # entities. The 3-hop cap and domain-only edges keep it small here; the exit
237         # is APOC's path expander, which visits each node once. Hops is spliced in as
238         # an integer because a length bound cannot be a parameter.
#▸ L239: the relationship types to walk, joined for the pattern
239         types = "|".join(DOMAIN_RELATIONSHIPS)
#▸ L240: build the query
240         query = (
#▸ L241: find every node with the start's name
241             f"CALL {{ {lookup} }} "
#▸ L242: gather them, then take each as a start (all namesakes)
242             "WITH collect(e) AS starts UNWIND starts AS s "
#▸ L243: every path of 1 to `hops` domain relationships, either direction; the start itself is excluded
243             f"MATCH p = (s)-[:{types}*1..{hops}]-(t) WHERE NOT t IN starts "
#▸ L244: why min: a node reachable at 1 and at 3 hops is at 1
244             # The shortest route wins: a node reachable at 1 and at 3 hops is at 1.
#▸ L245: one row per reached node, with its shortest distance
245             "WITH t, min(length(p)) AS distance "
#▸ L246: the node's display name (its name, else its title); merge namesakes at their shortest distance
246             "WITH coalesce(t.name, t.title) AS entity, min(distance) AS distance "
#▸ L247: drop nodes with no name or title: they were walked through but are not returned
247             "WHERE entity IS NOT NULL "
#▸ L248: nearest first, then alphabetical (a total order), capped by the limit
248             "RETURN entity, distance ORDER BY distance, entity LIMIT $limit"
#▸ L249: end of the query
249         )
#▸ L250: run it as a managed read and return just the names
250         return [row["entity"] for row in self._read(query, {"name": name, "limit": limit})]
```

### 4. Interview: what a strong answer contains
- **"How do you stop multi-hop retrieval exploding?"** Strong: cap the hops; restrict relationship types; treat paths vs nodes (use a traversal that visits each node once, or dedupe); handle *hub* nodes (exclude or down-weight); cap results and set a timeout; and evaluate whether a third hop still returns *useful* nodes.
- **"What is a hub node and why is it a problem?"** A node connected to a huge number of others (a city, a company). Any two things linked to it become "2 hops apart", which drowns the signal.
- **"Paths vs nodes?"** A variable-length pattern returns one row per *route*; without de-duplication the same entity appears many times and the cost is exponential in the worst case.

### Micro-exercises
1. Add a second route from A to D in the diamond and count `routes` and `entities` again.
2. Call the store with `hops=3` from an employee in a city with 10 colleagues. What does the hub do to the result?
3. Replace `min(length(p))` with `max(length(p))` in a scratch copy. Which live test fails, and what does its name tell you?

**Checkpoint:** say why the diamond has 2 routes and 1 entity, and list three rules that keep `multi_hop_search` meaningful.

---


## C10 — Testing: a fake driver, live tests with a sweep, and mutants  **[INTERVIEW: RARELY ASKED]** directly, useful in "how do you test a RAG pipeline?"

*Not a topic in itself, but it shows engineering maturity when you are asked how you would test data ingestion or retrieval.*

### 1. Definition + analogy
Three ideas, each small:

| Idea | What it does | Why |
|---|---|---|
| **Inject the dependency** | `GraphStore(driver)` is *handed* its driver instead of building one | a test can hand it a **fake** that records what was sent |
| **Two kinds of test** | a fake proves the exact query and parameters sent; live tests prove what Neo4j then *does*, on the real labels with values prefixed `__test_` that a **sweep** deletes before and after | fast and hermetic in CI, honest against the real server |
| **Mutation testing** | change the code on purpose (one plausible mistake at a time) and require a test to fail | proves the tests *can* fail: passing tests alone do not |

The 29-mutant check on this step found **three real gaps**: two guards no test covered (`DISTINCT`, and excluding the start from its own results) and one test that passed by luck. It
first passed because three employees happened to come back in alphabetical order *without* the tie-breaker; it now uses five, inserted in an order that is neither alphabetical nor its reverse.

**Limits.** A fake encodes your *beliefs* about the server, so it needs live tests beside it. A sweep by prefix relies on a naming convention, and the mutation runs here were hand-driven, not part of CI.

**Analogy.** Practising on a flight simulator (fake), then flying the real plane with an instructor (live), and finally testing the *alarms* by deliberately triggering a fault (mutants).

### For a 14-year-old
To check that a vending machine gives the right snack, you first try a toy machine that just *writes down* which button you pressed (the fake). Then you try the real one, using coins with a special mark so you can collect them afterwards (the sweep). Finally, to check your checking works, you secretly swap two buttons and see whether you notice (a mutant). If you don't notice, your checks were weak.

### 2. Minimal demo: what the store sends, and a test noticing a wrong edit
`c10_fake_driver_and_a_mutant.py`
```python
# C10: test what the store SENDS with a fake driver, and see how a test notices a wrong edit. No database needed.
from src.graph.records import GraphNode                                      # the record to write
from src.retrieval.graph_store import GraphStore                             # the store under study

sent = []                                                                    # every (query, parameters) the store sends


class Result:                                                                # what a query returns
    def consume(self):                                                       # upsert_node's only call on a result
        return None                                                          # nothing to report


class Tx:                                                                    # stands in for a transaction
    def run(self, query, params):                                            # the store calls this with its Cypher and parameters
        sent.append((query, params))                                         # record them instead of talking to a server
        return Result()                                                      # hand back the stand-in result


class Session:                                                               # stands in for a session
    def __enter__(self):                                                     # the store uses `with driver.session() as session`
        return self                                                          # the session is itself
    def __exit__(self, *exc):                                                # nothing to close
        return None                                                          # do not swallow exceptions
    def execute_write(self, work):                                           # the managed-transaction entry point
        return work(Tx())                                                    # run the store's function against the stand-in


class Driver:                                                                # stands in for the driver
    def session(self, *, database=None):                                     # the store asks for a session
        return Session()                                                     # hand back the stand-in


store = GraphStore(Driver(), verify_schema=False)                            # the injected fake; skip the schema check, which needs a server
store.upsert_node(GraphNode("Company", {"name": "Acme"}, {"legal_name": "Acme Inc."}))  # write one node
query, params = sent[0]                                                      # what the store actually sent
print("query :", query)                                                      # the Cypher text
print("params:", params)                                                     # the parameters: values travel separately from the text
print("test passes on the real query :", "SET n += $props" in query)         # the assertion a test makes
mutant = query.replace("+=", "=")                                            # a mutant: the query after a wrong edit (SET = would wipe the key)
print("test passes on the mutant     :", "SET n += $props" in mutant)        # False: the test fails, so the mutant is caught
```
Real output:
```
query : MERGE (n:Company {name: $k_name}) SET n += $props
params: {'k_name': 'Acme', 'props': {'legal_name': 'Acme Inc.'}}
test passes on the real query : True
test passes on the mutant     : False
```

**How to read it.** The store wrote to the fake and the demo read back exactly what was sent, with values in `params` and not in the query text. A test that asserts `SET n += $props` passes on the real query and **fails on the mutant** (`SET n = ...`): the mutant is caught.

### 3. Exact project code: injection, the fake, the sweep, and two tests (annotated copies)
The store is handed its driver, and checks the schema before use (`src/retrieval/graph_store.py`):
```python
#▸ L96-98: the constructor: the driver is INJECTED, so the caller owns its lifetime and a test can pass a fake
96      def __init__(
97          self, driver: GraphDriver, *, database: str = "neo4j", verify_schema: bool = True
98      ) -> None:
#▸ L99: keep the injected driver
99          self._driver = driver
#▸ L100: and the database name
100         self._database = database
#▸ L101: by default, check the graph before using it (tests with a fake turn this off)
101         if verify_schema:
#▸ L102-104: why: without the constraints MERGE is no longer atomic and lookups scan, so refuse early
102             # Without the constraints MERGE still runs, but it is no longer atomic
103             # under concurrent writers and every lookup becomes a scan. Refusing here
104             # is cheaper than finding duplicates later.
#▸ L105: ask the P4-M2 checker whether all 8 constraints are in force
105             status = check_schema(driver, database=database)
#▸ L106: if any is missing or wrong...
106             if not status.ok:
#▸ L107-111: ...refuse to start, listing the problems and the command that fixes them
107                 raise SchemaMismatch(
108                     "graph schema is not in force: "
109                     + "; ".join(status.problems)
110                     + " (run: python -m src.graph.schema)"
111                 )
```
The fake driver (`tests/retrieval/test_graph_store.py`): it records what the store sends and replays canned rows.
```python
#▸ L29-31: a result row: a dict that also has .data(), like the driver's Record
29  class FakeRecord(dict):
30      def data(self):
31          return dict(self)
#▸ L34-36: a query result holding canned rows
34  class FakeResult:
35      def __init__(self, rows):
36          self.rows = [FakeRecord(row) for row in rows]
#▸ L38-39: rows can be looped over, like a real result
38      def __iter__(self):
39          return iter(self.rows)
#▸ L41-44: like the driver's single(): with strict=True it raises unless there is exactly one row
41      def single(self, strict=False):
42          if strict and len(self.rows) != 1:
43              raise AssertionError(f"expected exactly one row, got {len(self.rows)}")
44          return self.rows[0] if self.rows else None
#▸ L46-47: upsert_node calls consume(); there is nothing to report
46      def consume(self):
47          return None
#▸ L50-52: a fake transaction
50  class FakeTx:
51      def __init__(self, driver):
52          self.driver = driver
#▸ L54: the store calls this with its Cypher text and its parameters
54      def run(self, query, parameters=None):
#▸ L55: RECORD what was sent: this list is what tests assert on
55          self.driver.calls.append((query, dict(parameters or {})))
#▸ L56: hand back the next canned reply, so a test scripts what "the database" says
56          return FakeResult(self.driver.replies.pop(0) if self.driver.replies else [])
#▸ L59-61: a fake session
59  class FakeSession:
60      def __init__(self, driver):
61          self.driver = driver
#▸ L74-76: record that a WRITE transaction was used, then run the store's function against the fake transaction
74      def execute_write(self, work):
75          self.driver.modes.append("write")
76          return work(FakeTx(self.driver))
#▸ L78-80: the same for a READ transaction
78      def execute_read(self, work):
79          self.driver.modes.append("read")
80          return work(FakeTx(self.driver))
#▸ L96-100: the fake driver: canned replies, canned constraint rows, and lists of what it was asked
96  class FakeStoreDriver:
97      def __init__(self, replies=(), constraint_rows=None):
98          self.replies = list(replies)
99          self.constraint_rows = _model_rows() if constraint_rows is None else constraint_rows
100         self.calls, self.modes, self.databases, self.schema_queries = [], [], [], []
#▸ L102-104: hand out a fake session, remembering which database was asked for
102     def session(self, *, database=None):
103         self.databases.append(database)
104         return FakeSession(self)
#▸ L107-109: build a store on a fake with the schema check skipped
107 def _store(*replies):
108     driver = FakeStoreDriver(replies)
109     return GraphStore(driver, verify_schema=False), driver
```
A test on what is sent, and one that needs the real server:
```python
#▸ L159: a fake-driver test: no server needed
159 def test_upsert_node_merges_on_the_key_and_adds_the_properties():
#▸ L160: a store on a fake driver
160     store, driver = _store()
#▸ L161: write one node
161     store.upsert_node(GraphNode("Company", {"name": "Acme"}, {"legal_name": "Acme Inc."}))
#▸ L163: unpack exactly one recorded call (the test fails if there is more or fewer)
163     ((query, params),) = driver.calls
#▸ L164: the exact text: this line is what kills the "SET =" mutant
164     assert query == "MERGE (n:Company {name: $k_name}) SET n += $props"
#▸ L165: the values travelled as parameters, not text (C7)
165     assert params == {"k_name": "Acme", "props": {"legal_name": "Acme Inc."}}
#▸ L166-167: the write must have used a managed WRITE transaction (C6)
166     # A managed transaction, so a transient failure is retried by the driver.
167     assert driver.modes == ["write"]
```
The sweep and the fixture that keep the real graph clean:
```python
#▸ L344: every value the live tests create starts with this, so cleanup can find exactly them
344 TEST_PREFIX = "__test_"
#▸ L349: delete every test node from the real labels
349 def _sweep(driver) -> None:
#▸ L350: open a session
350     with driver.session() as session:
#▸ L351: for each real label...
351         for node in NODE_TYPES:
#▸ L352-355: ...delete the nodes whose first key property starts with the prefix; DETACH removes their relationships
352             session.run(
353                 f"MATCH (n:{node.label}) WHERE n.{node.key[0]} STARTS WITH $prefix DETACH DELETE n",
354                 prefix=TEST_PREFIX,
355             ).consume()
#▸ L394: setup and teardown shared by the live tests
394 @pytest.fixture
#▸ L395: provides a store on the real server
395 def graph():
#▸ L396: open a real connection
396     with build_driver() as driver:
#▸ L397: make sure the real constraints exist (idempotent)
397         apply_schema(driver)
#▸ L398: sweep BEFORE the test: this repairs a run that crashed earlier
398         _sweep(driver)
#▸ L399: run the test...
399         try:
#▸ L400: ...handing it a store and a small graph-building helper
400             yield Graph(GraphStore(driver), driver)
#▸ L401: and whatever happens...
401         finally:
#▸ L402: ...sweep AFTER it too
402             _sweep(driver)
```
The test that a mutant forced me to write (it now needs a tie-breaker to pass):
```python
#▸ L545: the strengthened ordering test
545 def test_live_search_related_orders_ties_by_name_and_limits_after_ordering(graph):
#▸ L546: one product...
546     product = graph.add("Product", name="Carllm")
#▸ L547-548: why five employees in this order: it is neither alphabetical nor its reverse, so only a real tie-breaker can sort them
547     # Inserted in an order that is neither alphabetical nor its reverse, so the two edges
548     # tie on everything but the source's name and only a real tie-breaker can sort them.
#▸ L549-550: ...that five employees work on
549     for name in ("Carol", "Alice", "Eve", "Bob", "Dave"):
550         graph.link("WORKS_ON", graph.add("Employee", name=name), product)
#▸ L551: the order a correct store must return
551     alphabetical = [graph.n(name) for name in ("Alice", "Bob", "Carol", "Dave", "Eve")]
#▸ L553: every edge, with no limit
553     everything = graph.store.search_related(product.key["name"])
#▸ L554: the same question with limit=2
554     first_two = graph.store.search_related(product.key["name"], limit=2)
#▸ L556: the full result must be alphabetical by source name
556     assert [edge.source.key["name"] for edge in everything] == alphabetical
#▸ L557: the limit must apply AFTER ordering: it returns the first two of that same order
557     assert first_two == everything[:2]
```
Some of the 29 mutants (each was one edit to the source; each was caught):

| Mutant | Caught by |
|---|---|
| `SET n += $props` changed to `SET n = $props` | the exact-query test above |
| the endpoints `MERGE`d instead of `MATCH`ed | the exact-query test for `upsert_edge`, and the no-ghost live test |
| `DISTINCT` removed | the "company and city with the same name" live test (written after the first run found it uncovered) |
| the start allowed in its own results | the triangle live test (also written after the first run) |
| `min(length(p))` changed to `max(...)` | the "each entity once at its shortest distance" live test |
| the tie-breaker removed from `ORDER BY` | the ordering test above (**survived the first run** until it was strengthened) |

### 4. Interview: what a strong answer contains
- **"How do you test a data pipeline or a retrieval component?"** Strong: unit tests with a fake for the exact behaviour; integration tests against the real store with *isolated, self-cleaning data*; a small labelled evaluation set for retrieval quality (a different kind of test); and a way to check the tests themselves (mutation testing or fault injection).
- **"Why inject the driver instead of creating it inside?"** Testability and lifetime control: the caller decides when it opens and closes, and a test passes a fake.
- **"What is wrong with mocks?"** They encode your assumptions; pair them with tests against the real thing, or a *contract test* that runs the same scenario on both.

### Micro-exercises
1. Change `+=` to `=` in `upsert_node` and run `pytest tests/retrieval -q`. Which test fails first, and does it need the server?
2. Kill the sweep (comment out one `_sweep` call) and run a live test twice. What happens on the second run?
3. Invent a thirtieth mutant and find the test that kills it, or write the test that should.

**Checkpoint:** explain why the fake alone would not have found the `DISTINCT` gap, and what a mutant is.

---

## Validation

Run from the project root; each should match.

```bash
.venv/Scripts/python -m pytest tests/graph/test_records.py tests/retrieval -q -p no:warnings
# 98 passed
.venv/Scripts/python -m pytest tests/ -q -p no:warnings -m "not tiktoken and not live"
# 291 passed, 52 deselected
.venv/Scripts/python -m ruff check src/ tests/ && .venv/Scripts/python -m ruff format --check src/ tests/
# All checks passed!  /  55 files already formatted
.venv/Scripts/python -m mypy src/
# Success: no issues found in 24 source files
for f in "practice codes/phase 4/m3_graph_store_deep_dive"/c*.py; do .venv/Scripts/python "$f" > /dev/null 2>&1 && echo "ok: $f"; done
.venv/Scripts/python -m src.graph.schema --check
# schema verified: 8/8 constraints   (still exactly the 8 real constraints, and no nodes)
```

Self-check questions (answer without looking):
1. Why can vector search not answer the C1 demo's question, and what does the graph do differently? **[MUST KNOW]**
2. What does `SET n += $props` do that `SET n = $props` does not, and why does the key matter? **[MUST KNOW]**
3. Which parts of a query may be spliced, which may never be, and what is the second layer of defence for LLM-written queries? **[MUST KNOW]**
4. Why does `search_related` return both people for a shared name, and why is `MENTIONS` off by default? **[MUST KNOW]**
5. The diamond has 2 routes and 1 entity: what does that imply for a variable-length query? **[MUST KNOW]**
6. Why must a retried transaction function be idempotent? **[GOOD TO KNOW]**
7. What does `frozen=True` not protect, and how does the record fix it? **[GOOD TO KNOW]**
8. What does strict endpoints prevent, and what can it not catch? **[GOOD TO KNOW]**

## Visualization

**`upsert_edge` (C5, C6)**
```
GraphEdge (validated, C2) ─► MATCH source, MATCH target, MERGE edge, RETURN count(r)
                                    │ inside one managed write transaction (retried if transient, C6)
                          written = 0 ? ── yes ─► diagnose which endpoint is missing ─► raise MissingEndpoint (rolls back)
                                    │ no
                                  done (idempotent: running it again changes nothing)
```

**`multi_hop_search` (C9)**
```
name ─► every node with that name (one index seek per named label)
     ─► all paths of 1..hops DOMAIN relationships, either direction, never back to a start node
     ─► one row per reached node, at its SHORTEST distance
     ─► nodes with no name/title dropped (walked through, not returned)
     ─► ORDER BY distance, name ─► LIMIT ─► List[str]
```

**Tools to see it yourself**
- **Neo4j Browser:** http://localhost:7474, then paste the C1 or C9 Cypher and add `PROFILE` in front to see the plan.
- **`pytest tests/retrieval -v -rs`:** which tests are hermetic (fake driver) and which are live, and why any skipped.

## End-to-end "watch it work" exercise

1. Run `c1_multi_hop_idea.py`, then change the chain and predict the output before running it again.
2. Run `c4_idempotent_upsert.py`; then swap `MERGE` for `CREATE` and count the nodes.
3. Run `c5_strict_endpoints.py`; then upsert the *missing* product first and watch the edge succeed.
4. Run `c7_parameters_vs_structure.py`; then try your own hostile string as a parameter.
5. Run `c8_entity_lookup.py`; then add a third person with the same name and predict the edges.
6. Run `c9_multi_hop_paths.py`; then add a second route in the diamond and re-read the counts.
7. Run `c10_fake_driver_and_a_mutant.py`; then write a second mutant and a test that catches it.
8. Log it: add a `LEARNING_LOG.md` line for the one behaviour that surprised you most.

## Canonical sources
- Neo4j: [Cypher manual: `MERGE`](https://neo4j.com/docs/cypher-manual/current/clauses/merge/) · [variable-length patterns (Patterns)](https://neo4j.com/docs/cypher-manual/current/) · [parameters](https://neo4j.com/docs/cypher-manual/current/syntax/parameters/) · [Python driver: transactions](https://neo4j.com/docs/python-manual/current/) · [APOC path expander](https://neo4j.com/docs/apoc/current/)
- GraphRAG: [Edge et al. 2024 (arXiv:2404.16130)](https://arxiv.org/abs/2404.16130) · [Peng et al. 2024 survey (arXiv:2408.08921)](https://arxiv.org/abs/2408.08921)
- Design: [Parse, don't validate (Alexis King)](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/) · [OWASP: Injection prevention](https://cheatsheetseries.owasp.org/cheatsheets/Injection_Prevention_Cheat_Sheet.html)
- Python: [`dataclasses` (frozen)](https://docs.python.org/3/library/dataclasses.html) · [`types.MappingProxyType`](https://docs.python.org/3/library/types.html#types.MappingProxyType)
- Testing: [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html) · [Fowler: Contract Test](https://martinfowler.com/bliki/ContractTest.html) · [mutmut](https://mutmut.readthedocs.io/)
- Project: [decision.md](../decision.md) (D-2026-09-20-10 to -17) · [notes/step-4.3-graph-store.md](../notes/step-4.3-graph-store.md) · [docs/architecture.md](architecture.md) (Graph store) · [docs/18 graph_schema_deep_dive.md](18%20graph_schema_deep_dive.md)
