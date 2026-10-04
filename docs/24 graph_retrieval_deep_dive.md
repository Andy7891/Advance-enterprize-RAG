# 24 — Graph-Based Retrieval: Deep Dive (P5-M1)

> Every output block in this document was produced on 2026-09-23 by running the code: against a
> live Neo4j **5.26.0 community** + APOC holding the real 223-chunk corpus, and the real
> [`src/retrieval/graph_retriever.py`](../src/retrieval/graph_retriever.py). All scripts are in
> [`practice codes/phase 5/m1_graph_retrieval_deep_dive/`](../practice%20codes/phase%205/m1_graph_retrieval_deep_dive/).
> Run any of them from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 5/m1_graph_retrieval_deep_dive/d0_entity_linking.py"
> ```
>
> None of them need Neo4j except by importing the real `GraphRetriever` module (which only touches
> the network when a `GraphStore` is actually constructed and used — none of these scripts do that).

---

## 0. Plain-language summary

`GraphRetriever` answers one question: given a piece of free text, what does the knowledge graph
know that's relevant? Everything built in P4 speaks in exact node names — `MATCH (e:Product {name:
'Carllm'})` — and a question is never that tidy. This step is the translator: recognize which real
things a question names, walk outward through the graph from them, and hand back real, readable
chunks — not graph nodes, not IDs, actual `DocumentChunk` objects a person (or an LLM prompt) could
read.

```
"Who works on Carllm?"
   │
   ├─ link      match the question's words against the graph's own 111 known names   (C1)
   │             -> found: [("Product", "Carllm")]
   │
   ├─ traverse  multi_hop_search at hops 1 and 2, diffed against each other           (C2)
   │             -> Carllm itself (hop 0), then whatever's within 2 hops
   │
   ├─ collect   MENTIONS + home-document chunks, for every entity found               (C3)
   │             -> a set of chunk_ids per entity
   │
   ├─ rank      hop distance, then how many named entities reach each chunk at that
   │             same nearest distance, then chunk_id so ties never vary                (C4)
   │
   └─ hydrate   chunk_ids -> real DocumentChunk objects, from a cache built once      (C5)
                 built when the GraphRetriever was constructed
```

### Concepts map

| # | Concept | Core RAG or boilerplate? |
|---|---|---|
| C1 | Query-time entity linking: free text → a known name | 🔴 **Core RAG** (GraphRAG interview staple) |
| C2 | Recovering hidden information by diffing successive calls | 🟡 Engineering (general-purpose trick) |
| C3 | Chunk hydration / provenance bridging | 🔴 **Core RAG** |
| C4 | Ranking by graph structure, not similarity | 🔴 **Core RAG** (interview-heavy) |
| C5 | Caching expensive, unchanging setup at construction time | 🟡 Engineering (everyday OOP) |

---

## C1 — Query-time entity linking: free text → a known name

### Do this first
Before reading on, run
[`d0_entity_linking.py`](../practice%20codes/phase%205/m1_graph_retrieval_deep_dive/d0_entity_linking.py).
**Predict** first: would a NAIVE substring check ("does 'carllm' appear anywhere in this text?") ever
find a match that shouldn't count?

### 1. Definition + analogy
**What it is.** **Entity linking** is deciding that a specific span of free text refers to a specific,
known thing — here, that the word "Carllm" in a question refers to the exact `Product` node named
`"Carllm"` in the graph. `GraphRetriever` does this the cheapest way that's still CORRECT for a small,
closed vocabulary: check every one of the graph's current entity names against the question, as a
**whole word**, case-insensitively — not a raw substring check, which would treat "Carllmish" as
containing "Carllm."

**Analogy.** If you're scanning a party guest list for "Al," a substring check would also "find" Al
inside "Alabama," "Alligator," and "Alfredo" printed on the menu nearby. Checking for "Al" as a whole
WORD — surrounded by spaces or punctuation, not buried inside a longer word — is the difference
between finding your actual guest and finding every word that happens to contain those two letters.

### For a 14-year-old
Imagine you're given a list of your classmates' first names, and a teacher hands you an essay someone
wrote, asking: "which of these names are actually mentioned in this essay?" The LAZY way to check is
just to see if the LETTERS of each name show up anywhere in the essay's text at all. Say one of your
classmates is named "Ann." If the essay contains the word "banana," your lazy check would say "yes,
found Ann!" — because the letters A-N-N are hiding right there in the middle of b-AN-Nana. Obviously
wrong: nobody named Ann is being talked about, the essay just happens to contain a fruit whose spelling
overlaps.

The FIX is to check that the name shows up as its OWN separate word — with a space, a comma, the start
or end of a sentence, something that isn't just another letter, on BOTH sides of it. "Ann" surrounded
by actual word boundaries matches "Ann went to the store" but correctly refuses to match inside
"banana," because the letters before and after "ann" in banana are still letters, not a boundary. This
is exactly the difference between a lazy substring search and a real word-boundary match.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the classmate name list | `self._names_by_label` — every real entity name currently in the graph |
| the essay | the query text passed into `retrieve()` |
| the lazy "letters appear anywhere" check | `naive_substring_match` in the demo script |
| "Ann" wrongly found inside "banana" | "Carllm" wrongly found inside "Carllmish" by the naive check |
| requiring a real word boundary on both sides | `_word_pattern`'s `(?<!\w)` and `(?!\w)` — "not preceded/followed by a word character" |
| checking without caring about capitalization | `re.IGNORECASE` in `_word_pattern` |

### 2. Why here specifically
This corpus's entity vocabulary is small and closed — 111 real names as of the last real run (33
Company, 32 Employee, 8 Product, 29 Contact, 9 Location). For a vocabulary this size, exact whole-word
matching is fast, needs zero new dependencies, and is a technique this codebase ALREADY trusts:
`extractor.py`'s `_mentions` method uses the identical whole-word regex shape to decide whether a
chunk mentions an entity. Reusing it here means query-time linking and extraction-time linking agree
on what "mentioning" a name means.

### 3. Minimal code
`d0_entity_linking.py` — the naive check versus the real one, on a deliberately tricky query:
```python
# Minimal demo of C1: turning free text into a KNOWN entity name needs whole-word
# matching, not naive substring matching. Uses the REAL functions from graph_retriever.py.
from src.retrieval.graph_retriever import _match_entities, _word_pattern

NAMES_BY_LABEL = {"Product": ["Carllm"], "Company": ["DriveSmart Insurance"]}


def naive_substring_match(query, names_by_label):
    found = []
    for label, names in names_by_label.items():
        for name in names:
            if name.lower() in query.lower():
                found.append((label, name))
    return found


print("Query: 'What does Carllm do?'")
print("  naive substring match:", naive_substring_match("What does Carllm do?", NAMES_BY_LABEL))
print("  real _match_entities :", _match_entities("What does Carllm do?", NAMES_BY_LABEL))

print("\nQuery: 'Is Carllmish a real word?' (contains the letters C-a-r-l-l-m, but NOT the entity)")
print("  naive substring match:", naive_substring_match("Is Carllmish a real word?", NAMES_BY_LABEL))
print("  real _match_entities :", _match_entities("Is Carllmish a real word?", NAMES_BY_LABEL))

print("\nInspect the compiled pattern for a two-word name:")
pattern = _word_pattern("DriveSmart Insurance")
print(" ", pattern.pattern)
```
Real output:
```
Query: 'What does Carllm do?'
  naive substring match: [('Product', 'Carllm')]
  real _match_entities : [('Product', 'Carllm')]

Query: 'Is Carllmish a real word?' (contains the letters C-a-r-l-l-m, but NOT the entity)
  naive substring match: [('Product', 'Carllm')]
  real _match_entities : []

Inspect the compiled pattern for a two-word name:
  (?<!\w)DriveSmart\s+Insurance(?!\w)
```
Both checks agree on the first query. On the second, the naive check wrongly "finds" Carllm inside
"Carllmish" — the real one correctly refuses. Same input, one silent false positive avoided.

### The exact code I wrote (`src/retrieval/graph_retriever.py`)
```python
# lines 25-29
def _word_pattern(name: str) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(word) for word in name.split())
    # rebuilds the name as a regex, one escaped word per gap, joined by whitespace
    return re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE)
    # compiles it as a whole-word, case-insensitive pattern

# lines 32-44
def _match_entities(query: str, names_by_label: dict[str, list[str]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for label, names in names_by_label.items():             # 🎯 checks EVERY known name, every call
        for name in names:
            if _word_pattern(name).search(query):           # 🎯 INTERVIEW: word-boundary, not substring
                found.append((label, name))
    return found
```
**🎯 Interview-relevant line:** `(?<!\w){words}(?!\w)` — a negative lookbehind and lookahead for a
"word character." This single regex idiom is the entire difference between correct and incorrect
entity recognition here, and it's a pattern worth having memorized cold for any text-matching
interview question.

### 4. What breaks if this changes
- **Drop the word-boundary lookarounds, keep plain substring search:** "Carllmish," "Marketllm" (the
  real corpus typo from P4-M7's spot-check!), or any word merely containing a product's letters would
  be wrongly linked, silently inflating recall with wrong entities.
- **Drop `re.IGNORECASE`:** a perfectly reasonable lowercase question ("what does carllm do") would
  link nothing at all, since every real name is stored capitalized.

### [Interview relevance: high — regex word-boundary matching and its false-positive/false-negative trade-offs come up in almost any NLP or text-processing interview]

**Interview question:** *"Given a fixed list of product names, how would you detect if a user's
question mentions one, and what's a common bug in a naive implementation?"*
**A strong answer covers:** whole-word matching (not substring) to avoid false positives from names
that are substrings of other words; case-insensitivity, since users don't capitalize consistently;
and the trade-off this approach makes — it cannot handle paraphrases or typos, which is fine for a
small, well-known vocabulary but would need embeddings or fuzzy matching at a larger, messier scale.

### Canonical docs
- [Python `re` HOWTO: lookahead/lookbehind assertions](https://docs.python.org/3/howto/regex.html)
- [Wikipedia: Named-entity linking](https://en.wikipedia.org/wiki/Entity_linking)
- [`src/graph/extractor.py`](../src/graph/extractor.py)'s `_mentions` method — the exact same
  technique, first proven correct there (P4-M4b).

### Micro-exercises (absolute-beginner, learn by doing)
1. Run `d0_entity_linking.py`. Add a THIRD query: `"CARLLM is great"` (all caps). Predict, then check,
   whether `_match_entities` finds it.
2. Add a name with a hyphen, like `"Re-Insurance Co"`, to `NAMES_BY_LABEL`. Does `_word_pattern` treat
   the hyphen as part of a "word," or as a boundary? Test it against `"the ReInsurance Co is real"`
   (no hyphen in the query) and explain the result.
3. Write a query that should match TWO different entities at once (a Product and a Company). Confirm
   `_match_entities` finds both.
4. Change `_word_pattern` to remove `re.IGNORECASE` in a scratch copy. Run the hermetic test
   `test_word_pattern_is_case_insensitive` from `tests/retrieval/test_graph_retriever.py`. What fails?
5. Think of a real question about this corpus that WOULD be answered correctly by a human but WON'T
   link any entity with this technique (a paraphrase, not a typo). Confirm it with `retrieve()`.

**How it works internally.** Python's `re` module compiles the pattern into a finite state machine;
`(?<!\w)` and `(?!\w)` are zero-width assertions — they check a condition at a position WITHOUT
consuming any characters, which is why the boundary check adds no extra characters to what actually
gets matched.

### How to validate this yourself
The naive-vs-real side-by-side output on the "Carllmish" query IS the validation — same input, two
functions, one wrong answer and one right one, visible without needing a live database at all.

### How to visualize this
Paste `(?<!\w)Carllm(?!\w)` into [regex101.com](https://regex101.com) against both `"Carllmish"` and
`"What does Carllm do?"` — the tool visually highlights exactly where the boundary check succeeds and
fails.

**You should now be able to explain:** why substring matching is unsafe for entity recognition against
a fixed vocabulary, and the exact regex idiom that fixes it.

---

## C2 — Recovering hidden information by diffing successive calls

### Do this first
Before reading on, run
[`d1_diff_to_recover_distance.py`](../practice%20codes/phase%205/m1_graph_retrieval_deep_dive/d1_diff_to_recover_distance.py).
**Predict** first: if a function tells you "these 3 names are within 2 hops" and separately "these 2
names are within 1 hop," can you figure out which ONE name is *exactly* 2 hops away, without the
function ever telling you distances directly?

### 1. Definition + analogy
**What it is.** Sometimes a function computes something useful internally and simply doesn't expose
it in its return value — and you can't (or shouldn't) change the function, because other code already
depends on its existing contract. `GraphStore.multi_hop_search` is exactly this case: it computes
`min(length(p)) AS distance` for every entity it finds, but its Cypher's final `RETURN` clause only
selects the entity NAME, sorted nearest-first — the distance value itself never leaves the query.
The fix, when you need that hidden number, is to call the function at successive limits and take the
SET DIFFERENCE: everything in the "within 2" result that ISN'T in the "within 1" result must be
*exactly* 2 hops away, because `multi_hop_search(hops=2)`'s result is always a superset of
`multi_hop_search(hops=1)`'s.

**Analogy.** A tour guide announces "everyone within a 1-mile walk of here" and, later, "everyone
within a 2-mile walk." She never states anyone's individual distance. But if you write down BOTH lists
and cross out every name that was already on the first list, whoever's left on the second list must be
between 1 and 2 miles away — you've recovered a distance range the guide never directly told you,
purely by comparing two announcements she already made for an unrelated reason.

### For a 14-year-old
Imagine a treasure hunt game where a walkie-talkie occasionally announces "everyone who has found a
coin so far: [list of names]." It announces this TWICE: once after exactly 5 minutes, and again after
exactly 10 minutes. The walkie-talkie never tells you WHEN each individual person found their coin —
just the running total list at each check-in.

But you're smart: you write down the 5-minute list, then write down the 10-minute list, and then cross
out every name that ALREADY appeared on the 5-minute list. Whoever's name is NEW on the 10-minute list
— appearing there but not on the earlier one — must have found their coin sometime between minute 5
and minute 10. You just figured out a piece of timing information the walkie-talkie never announced
directly, purely by comparing two lists that were announced for a totally different reason (a running
total, not a timing report).

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the walkie-talkie's running-total announcements | `multi_hop_search(name, hop, label=label)` at each `hop` |
| "everyone who found a coin by minute 5" | `multi_hop_search(..., hops=1)` — everything within 1 hop |
| "everyone who found a coin by minute 10" | `multi_hop_search(..., hops=2)` — everything within 1 OR 2 hops |
| crossing out names already on the earlier list | `new_at_this_hop = within_hop - previous_names` |
| the names left over, found "between 5 and 10 minutes" | entities newly reached at hop 2, recovered without the function ever saying "2" |
| never changing how the walkie-talkie itself works | `multi_hop_search`'s own return type (`list[str]`) is never touched, so its 40 existing tests stay valid |

### 2. Why here specifically
D5 (ranking) needs to know EACH found entity's hop distance to sort correctly. Changing
`multi_hop_search`'s return shape to also include distance would touch a function with 40 existing
tests built against its current `list[str]` contract (P4-M3, P4-M6). Diffing two calls gets the same
information from the outside, at the cost of one extra query per hop level, without touching code that
already works and is already tested.

### 3. Minimal code
`d1_diff_to_recover_distance.py` — a toy chain, with the SAME information-hiding shape as
`multi_hop_search`:
```python
# Minimal demo of C2: a function can compute a number internally and still never GIVE
# it back to you. When you can't change the function, calling it at each depth and
# diffing consecutive results recovers the missing number from the outside.
from collections import deque

edges = [("Alice", "Bob"), ("Bob", "Carol"), ("Carol", "Dave")]
neighbours = {}
for a, b in edges:
    neighbours.setdefault(a, []).append(b)
    neighbours.setdefault(b, []).append(a)


def names_within(start, hops):
    # THIS is deliberately shaped like multi_hop_search: it computes a real distance
    # internally (the BFS naturally knows how far each node is) but only RETURNS names.
    visited = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if visited[node] >= hops:
            continue
        for neighbour in neighbours.get(node, []):
            if neighbour not in visited:
                visited[neighbour] = visited[node] + 1
                queue.append(neighbour)
    return sorted(n for n in visited if n != start)  # <- distance thrown away here on purpose


print("names_within('Alice', hops=1):", names_within("Alice", 1))
print("names_within('Alice', hops=2):", names_within("Alice", 2))

print("\nRecovering each name's actual distance by diffing consecutive calls:")
previous = set()
for hop in range(1, 4):
    within_hop = set(names_within("Alice", hop))
    new_at_this_hop = within_hop - previous
    print(f"  hop {hop}: newly reached = {sorted(new_at_this_hop)}")
    previous = within_hop
```
Real output:
```
names_within('Alice', hops=1): ['Bob']
names_within('Alice', hops=2): ['Bob', 'Carol']
names_within('Alice', hops=3): ['Bob', 'Carol', 'Dave']

Recovering each name's actual distance by diffing consecutive calls:
  hop 1: newly reached = ['Bob']
  hop 2: newly reached = ['Carol']
  hop 3: newly reached = ['Dave']
```
`names_within` never returns a distance for anyone. The diff loop recovers the EXACT distance of every
single node anyway, purely from three calls to a function that only ever answers "who's within X."

### The exact code I wrote (`src/retrieval/graph_retriever.py`)
```python
# lines 118-134
def _entities_by_hop(self, name: str, label: str) -> dict[int, list[tuple[str | None, str]]]:
    by_hop: dict[int, list[tuple[str | None, str]]] = {0: [(label, name)]}
    previous_names: set[str] = set()
    for hop in range(1, self._hops + 1):
        within_hop = set(self._store.multi_hop_search(name, hop, label=label))
        # 🎯 INTERVIEW: multi_hop_search never returns a distance -- only nearest-first names
        new_at_this_hop = within_hop - previous_names
        # 🎯 the set difference IS the recovered "exactly at this hop" information
        by_hop[hop] = [(None, entity_name) for entity_name in sorted(new_at_this_hop)]
        previous_names = within_hop
    return by_hop
```
**🎯 Interview-relevant lines:** the two marked lines together. Recognizing that
`multi_hop_search(hops=2)`'s result is a SUPERSET of `multi_hop_search(hops=1)`'s (because anything
within 1 hop is trivially also within 2) is what makes the set-difference trick valid at all — it
isn't a coincidence, it follows directly from what "within N hops" means.

### 4. What breaks if this changes
- **Call `multi_hop_search` only once, at `hops=2`, and skip the diffing:** every entity gets treated
  as hop distance 2 (or some arbitrary constant), and D4's ranking degrades to "found vs. not found,"
  losing the "closer is more relevant" signal entirely.
- **Assume the diff trick works for ANY two calls, not just consecutive hop levels:** it only holds
  because of the nesting property (hops=1 ⊆ hops=2 ⊆ hops=3); diffing `hops=1` against `hops=3`
  directly would conflate "exactly 2 hops" and "exactly 3 hops" into one bucket.

### [Interview relevance: medium-high — "how do you get information out of a black-box function without modifying it" is a real, generalizable debugging/integration skill]

**Interview question:** *"You're using a third-party function that returns a sorted list but not the
underlying sort keys, and you need those keys. You can't modify the function. What do you do?"*
**A strong answer covers:** looking for a way to call the function at different PARAMETERS such that
the difference between two calls reveals the hidden information (as done here with hop counts);
recognizing when this technique is valid (the results must nest — one call's output must be a subset
of another's) versus when it silently gives wrong answers; and, as a fallback, whether re-implementing
a minimal version of the function's logic yourself is cheaper than this kind of reverse-engineering.

### Canonical docs
- [Python `set` — difference()](https://docs.python.org/3/library/stdtypes.html#frozenset.difference)
- [docs/19 graph_store_deep_dive.md](19%20graph_store_deep_dive.md) — `multi_hop_search`'s own
  Cypher, including the `min(length(p)) AS distance` line this technique works around.

### Micro-exercises
1. Run `d1_diff_to_recover_distance.py`. Add a branch to the chain (`Bob -> Eve`, a second node at hop
   2 from Alice). Does the diff loop correctly report BOTH `Carol` and `Eve` as newly reached at hop 2?
2. Try diffing `hops=1` directly against `hops=3` (skip `hops=2` entirely). Which node(s) get
   misclassified, and why does skipping a level break the technique?
3. Modify `names_within` to actually RETURN distances too (change its return type). Now that you have
   real distances, confirm they match what the diffing technique recovered.
4. In `GraphRetriever`, what would happen if `self._hops` were 1 instead of 2? Trace through
   `_entities_by_hop`'s loop by hand — does the diffing logic even run more than once?
5. Find one other place in this project where a value gets recomputed indirectly instead of a function
   being changed to return it (hint: recall the M5 deep dive's discussion of what information a
   function's return shape hides).

**How it works internally.** Python's `set.difference` (the `-` operator) is implemented as an O(min(len(a),
len(b))) operation internally — it iterates the smaller set and checks membership in the larger one, which
is O(1) per check thanks to hashing, so the diff itself is cheap regardless of how large each hop's
result set gets.

### How to validate this yourself
The three `names_within` calls print DIFFERENT growing lists; the diff loop's output, read top to
bottom, reconstructs exactly which node was added at which hop — compare the two outputs by eye.

### How to visualize this
Draw the chain `Alice-Bob-Carol-Dave` on paper. Circle the hop-1 result in blue, the hop-2 result in
red (a bigger circle containing the blue one), and the hop-3 result in green (bigger still). The "new"
name at each hop is exactly the ring between one circle and the next.

**You should now be able to explain:** why `multi_hop_search(hops=2)`'s result must always be a
superset of `multi_hop_search(hops=1)`'s, and how that fact is what makes the diffing technique valid.

---

## C3 — Chunk hydration: a graph edge is an ID, not content

### Do this first
Before reading on, run
[`d2_chunk_hydration.py`](../practice%20codes/phase%205/m1_graph_retrieval_deep_dive/d2_chunk_hydration.py).
**Predict** first: after `MATCH (c:Chunk)-[:MENTIONS]->(e:Product {name:'Carllm'}) RETURN c` in Neo4j,
do you have the actual TEXT of that chunk, or something else?

### 1. Definition + analogy
**What it is.** By design (P4-M2), a `Chunk` node in Neo4j holds only its `chunk_id` — never the
chunk's actual text. A graph query can tell you WHICH chunk is connected to an entity, but getting the
readable content requires a SECOND lookup, into wherever the text actually lives (here, the JSON files
`read_chunks()` loads from `data/chunks/`). This two-step shape — an ID from one system, then content
from another, joined by that ID — is called **hydration**: turning a bare reference into the full
object it refers to.

**Analogy.** A library's card catalogue tells you a book's shelf location (an "ID"), not the book's
actual pages. Finding the location is step one; walking to the shelf and pulling the book off is step
two. Nobody confuses "I found the catalogue card" with "I've read the book" — yet it's an easy mistake
to make with a graph query, where the query result LOOKS like an answer even though it's still just a
pointer.

### For a 14-year-old
Imagine a school lost-and-found system where every item gets a numbered ticket, and a big whiteboard
lists "Ticket #47: blue jacket, found near the gym." If a teacher asks you "is there a blue jacket in
the lost and found?", checking the WHITEBOARD only tells you "yes, ticket #47 says so" — it does NOT
put an actual jacket in your hands. To actually GIVE someone the jacket, you have to take that ticket
number, walk over to the ACTUAL lost-and-found bin, and dig out the physical item that matches ticket
#47. The whiteboard (a list of IDs and short descriptions) and the bin (the actual physical items) are
two completely separate places, and you need BOTH steps — check the board, THEN go get the item — to
actually hand someone a real jacket instead of just a ticket number.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the whiteboard listing ticket numbers | Neo4j's `Chunk` nodes — holding only `chunk_id`, never text |
| a `MENTIONS` edge pointing from a chunk to an entity | the graph telling you WHICH ticket number is relevant |
| the physical lost-and-found bin | `data/chunks/*.json` on disk — where the actual chunk TEXT lives |
| walking from the whiteboard to the bin | `self._chunks_by_id[chunk_id]` — the second lookup |
| handing someone the actual jacket, not just a ticket number | `retrieve()` returning real `DocumentChunk` objects, not bare chunk_id strings |

### 2. Why here specifically
Every one of `GraphRetriever`'s internal collections (`seed_hops`, `best_hop`, `agreeing`) is keyed by `chunk_id`
— a string, an ID, nothing readable. The task's own required output type, `list[DocumentChunk]`, is
only satisfiable by this second lookup. Skipping it would mean `retrieve()` could only ever hand back
IDs, which is useless to whatever calls it next (a prompt-construction step needs real text, not a
label).

### 3. Minimal code
`d2_chunk_hydration.py` — two separate stores, joined by the ID a graph query gives you:
```python
# Minimal demo of C3: a graph edge is an ID, not content. Turning a graph hit into real
# text needs a SECOND lookup, into whatever store actually holds the text.
graph_mentions = {
    "Carllm": ["products/Carllm.md_0", "contracts/Contract with DriveSmart.md_0"],
}

chunk_text_store = {
    "products/Carllm.md_0": "# Carllm\n\nCarllm is Insurellm's auto insurance platform.",
    "contracts/Contract with DriveSmart.md_0": "# Contract with DriveSmart for Carllm\n\n...",
}


def graph_only_answer(entity):
    return graph_mentions.get(entity, [])


def hydrated_answer(entity):
    return [chunk_text_store[chunk_id] for chunk_id in graph_mentions.get(entity, [])]


print("Graph-only result (what Neo4j alone can give you):")
for chunk_id in graph_only_answer("Carllm"):
    print(" ", chunk_id)

print("\nHydrated result (after the second lookup, into the text store):")
for text in hydrated_answer("Carllm"):
    print(" ", text.splitlines()[0])
```
Real output:
```
Graph-only result (what Neo4j alone can give you):
  products/Carllm.md_0
  contracts/Contract with DriveSmart.md_0

Hydrated result (after the second lookup, into the text store):
  # Carllm
  # Contract with DriveSmart for Carllm
```
The graph-only result is a list of strings that mean nothing to a person reading them. The hydrated
result is actual content — the exact difference between "the graph found something" and "here's what
it found."

### The exact code I wrote (`src/retrieval/graph_retriever.py`)
```python
# lines 63-66 (inside __init__)
self._chunks_by_id: dict[str, DocumentChunk] = {
    chunk.chunk_id: chunk for chunk in read_chunks(chunks_dir)
}
# loads every chunk from disk and indexes it by its own chunk_id      <- 🎯 the "bin," loaded once

# lines 109-112 (the last two lines of retrieve)
top = ranked[:k]
return [self._chunks_by_id[chunk_id] for chunk_id in top if chunk_id in self._chunks_by_id]
# 🎯 INTERVIEW: this is the ENTIRE hydration step -- one dict lookup per surviving chunk_id
```
**🎯 Interview-relevant lines:** the whole retrieval pipeline — entity linking, traversal, ranking —
produces nothing but `chunk_id` strings until this exact line. Everything before it operates purely on
the graph's identifiers; this is the single moment IDs become content.

### 4. What breaks if this changes
- **Return `top` (the ranked chunk_ids) directly, skip hydration:** `retrieve()` would violate its own
  declared return type (`list[DocumentChunk]`) and hand back meaningless ID strings to whatever calls
  it — a caller expecting readable text gets ticket numbers instead of jackets.
- **Look up chunks from a source that's out of sync with the graph** (an older `data/chunks/`
  snapshot than what was actually ingested): a `chunk_id` the graph found could be missing from the
  cache — which is exactly why `if chunk_id in self._chunks_by_id` guards the final line, silently
  dropping anything that can't be hydrated rather than crashing.

### [Interview relevance: high — "how does a graph or vector index relate to the actual content it indexes" is a fundamental RAG architecture question]

**Interview question:** *"In a RAG system, why might your vector index or knowledge graph and your
actual document store ever disagree, and how do you handle a lookup that fails?"*
**A strong answer covers:** an index (vector or graph) is a POINTER structure — built at one point in
time, from one snapshot of the data — while the underlying documents can change or be regenerated
independently; the fix isn't to prevent this (it's often unavoidable at scale) but to handle a failed
hydration gracefully (skip, log, or flag it) rather than crashing the whole retrieval; and, ideally,
having a process that keeps the two in sync (here, always re-running both the graph ingestion and the
chunking pipeline together).

### Canonical docs
- [docs/17 neo4j_setup_deep_dive.md](17%20neo4j_setup_deep_dive.md) and
  [docs/18 graph_schema_deep_dive.md](18%20graph_schema_deep_dive.md) — why `Chunk` nodes hold IDs
  only (P4-M2's original decision).
- General RAG architecture: any introduction to "vector database + document store" two-system designs
  makes the same ID/content split (e.g. Pinecone, Weaviate, or Qdrant's own payload-vs-external-store
  patterns).

### Micro-exercises
1. Run `d2_chunk_hydration.py`. Delete one entry from `chunk_text_store` (simulate a missing chunk) and
   change `hydrated_answer` to skip missing ids instead of crashing with a `KeyError`.
2. In `tests/retrieval/test_graph_retriever.py`, find the live test that checks
   `products/Carllm.md_2` is returned. Open `data/chunks/products__Carllm.md.json` and confirm that
   chunk really is index 2 (0-based) and really doesn't mention "Carllm" by name in its own text.
3. Trace, on paper, every place a bare `chunk_id` string exists inside `retrieve()` BEFORE the final
   hydration line — how many different dicts/sets is it a key or member of along the way?
4. What would happen if `self._chunks_by_id` were built freshly inside `retrieve()` instead of once in
   `__init__`? Time the difference over 5 calls to `retrieve()` (reuse the timing pattern from C5's demo).
5. Modify `GraphRetriever` in a scratch copy to raise an exception instead of silently dropping a
   chunk_id that isn't in `self._chunks_by_id`. Which is the better default for this project, and why?

**How it works internally.** `self._chunks_by_id` is a plain Python `dict`, giving O(1) average-case
lookup by `chunk_id` — the same reason `_KEY_BY_LABEL` and other lookup tables throughout this codebase
are built as dicts rather than searched linearly.

### How to validate this yourself
Compare the two printed sections in the demo's output side by side: one is a list of ID strings, the
other is readable text — the difference between them is hydration made visible.

### How to visualize this
Draw two boxes: "Neo4j (ids only)" and "data/chunks/ (text)," with a single arrow between them labelled
`chunk_id`. Every retrieval this project does — graph OR vector — crosses that exact same arrow at
some point; only the box on the left changes (Neo4j here, Qdrant in P3).

**You should now be able to explain:** why a graph query result is not yet an "answer," and the exact
line in `GraphRetriever` where an ID becomes real content.

---

## C4 — Ranking by graph structure, not similarity

> **Correction (2026-09-24).** The first version of this section, and the first version of the code
> it describes, contained a mistake, found while building P5-M2. The code counted a query entity as
> "agreeing" on a chunk if it reached that chunk **at any distance**. Two entities whose
> neighbourhoods overlap (a Company and the Product it licenses are two hops apart) therefore gave
> **every** chunk in the overlap the same agreement count, and the tie fell to whatever order the
> chunks were inserted in. The "DriveSmart contract moves from 3rd to 1st" result I reported and
> tested was that accident, not convergence. It is fixed below, and this section now teaches the
> corrected rule plus the bug, because a test that passes for the wrong reason is worth studying.

### Do this first
Before reading on, run
[`d3_ranking_by_convergence.py`](../practice%20codes/phase%205/m1_graph_retrieval_deep_dive/d3_ranking_by_convergence.py).
**Predict** first: two entities named in one question both lead to the same three chunks, but one
entity reaches two of them only by going through the other. Should that indirect reach count as
"agreeing" as much as a direct hit? And does ranking need any similarity score at all?

### 1. Definition + analogy
**What it is.** Vector search ranks by similarity — how close two pieces of text are in a learned
embedding space. `GraphRetriever` ranks by two purely STRUCTURAL signals instead: **hop distance**
(how many relationships away a chunk's entity is from the entity the question named — closer is more
relevant) and **agreement** (how many of the DIFFERENT entities a question names each reach this
chunk at that same nearest distance — a chunk that more of the question's entities hit *directly* is
more likely to be the right answer). Neither signal involves a vector, a dot product, or a trained
model of any kind — they're properties of the GRAPH's shape alone.

The words "at that same nearest distance" carry the whole fix. An entity that reaches a chunk only by
going through another entity is weak evidence about that chunk; counting it as much as a direct hit
was the original mistake.

**Analogy.** If you ask "which restaurant does BOTH my friend Sam and my friend Priya recommend?", you
don't need to compare restaurant reviews for similarity — you just need to know which restaurant shows
up on both of their individual recommendation lists. A restaurant on both lists is a stronger answer
than one only Sam mentioned, purely because of HOW MANY of your actual questions ("what does Sam like,"
"what does Priya like") converge on it.

### For a 14-year-old
Imagine you're trying to figure out which classmate two of your friends are BOTH talking about,
without either friend saying the classmate's name directly. Friend 1 gives you a list of clues that
narrow it down to 3 possible classmates. Friend 2, completely separately, gives you a DIFFERENT list of
clues that narrow it down to 2 different possible classmates. If exactly ONE name appears on BOTH
lists, you've found your answer — not because that name is somehow more "similar" to anything, but
purely because it's the one thing both trails of clues led back to. If you'd only listened to Friend 1,
you'd have 3 candidates with no way to pick between them. Having a SECOND, independent trail of clues
that happens to overlap on one specific answer is what makes that answer stand out — the more separate
trails point at the same thing, the more confident you can be, with zero need to measure how "similar"
anything is to anything else.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| Friend 1's list of clues | one query entity's (e.g. "Carllm"'s) set of connected chunks |
| Friend 2's independent list of clues | a SECOND query entity's (e.g. "Velocity Auto Solutions"'s) set of connected chunks |
| the one name Friend 2 names DIRECTLY, that Friend 1 also names | the chunk both entities hit at hop 0 — the Velocity contract's first chunk |
| a name Friend 2 only arrives at by repeating what Friend 1 said | the Carllm chunks Friend 2's entity reaches at hop 2, through the Product |
| "the more trails point at the same answer, the more confident you are" | `-agreeing[chunk_id]` in the sort key — only seeds reaching the chunk at its nearest distance count |
| never measuring how "similar" two clues are | no embedding, no cosine similarity, anywhere in `retrieve()`'s ranking step |
| a clue that's more specific (mentioned early, close to the person) mattering more | `best_hop[chunk_id]` — a chunk reached at hop 0 outranks one only reached at hop 2 |

### 2. Why here specifically
`retrieve(query, k)` must return chunks in SOME order, or `k` (top-k) is meaningless. Since graph
retrieval has no natural numeric score the way cosine similarity gives one, this step needed a ranking
rule with no similarity model at all — hop distance and agreement are the two signals the graph
traversal already computes as a side effect of doing its job, so ranking by them costs nothing extra.
A third element, the `chunk_id`, is not a relevance signal at all: it exists only so that chunks
tied on both signals come out in the same order every time.

### 3. Minimal code
`d3_ranking_by_convergence.py` — the original rule and the corrected rule, side by side, on the
situation that exposed the bug (a Company and the Product it licenses, two hops apart, both named in
one question):
```python
# Minimal demo of C4: ranking by (hop distance, how many query entities AGREE), and the
# bug the first version had. Two rules run side by side on the situation that exposed it:
# a Company and the Product it licenses, two hops apart, both named in one question.
reaches = {
    # for each seed entity: the chunks it reached, and the hop distance it reached each at
    ("Product", "Carllm"): {  # the Product reaches all three chunks directly (hop 0)
        "contracts/DriveSmart.md_0": 0,
        "contracts/Velocity.md_0": 0,
        "products/Carllm.md_2": 0,
    },
    ("Company", "Velocity"): {  # the Company mentions ONE chunk directly, and the rest via Carllm
        "contracts/Velocity.md_0": 0,  # its own contract chunk: a direct, hop-0 hit
        "contracts/DriveSmart.md_0": 2,  # reached only by going Company -> Contract -> Product
        "products/Carllm.md_2": 2,  # same: two hops away, through the Product
    },
}

chunk_ids = sorted({chunk for hops in reaches.values() for chunk in hops})  # every chunk, A-Z
best_hop = {c: min(h[c] for h in reaches.values() if c in h) for c in chunk_ids}  # nearest reach


def any_distance_count(chunk_id):
    # the ORIGINAL rule: a seed counts if it reached this chunk at ANY distance at all
    return sum(1 for hops in reaches.values() if chunk_id in hops)


def nearest_distance_count(chunk_id):
    # the CORRECTED rule: a seed counts only if it reached this chunk at the chunk's nearest distance
    return sum(1 for hops in reaches.values() if hops.get(chunk_id) == best_hop[chunk_id])


for name, count in (("original: any distance", any_distance_count), ("corrected: nearest distance", nearest_distance_count)):
    ranked = sorted(chunk_ids, key=lambda c: (best_hop[c], -count(c), c))  # hop, then agreement, then A-Z
    print(f"\n{name}")
    for chunk_id in ranked:
        print(f"  {chunk_id:<28} hop={best_hop[chunk_id]}  agreeing seeds={count(chunk_id)}")
```
Real output:
```
original: any distance
  contracts/DriveSmart.md_0    hop=0  agreeing seeds=2
  contracts/Velocity.md_0      hop=0  agreeing seeds=2
  products/Carllm.md_2         hop=0  agreeing seeds=2

corrected: nearest distance
  contracts/Velocity.md_0      hop=0  agreeing seeds=2
  contracts/DriveSmart.md_0    hop=0  agreeing seeds=1
  products/Carllm.md_2         hop=0  agreeing seeds=1
```
Under the original rule **every chunk has 2 agreeing seeds**, so agreement separates nothing and the
alphabet decides (DriveSmart wins, for no good reason). Under the corrected rule only the Velocity
chunk has 2, because only there do BOTH entities hit it at hop 0. This is exactly what the real graph
did, and it is why the two tests added in the correction exist: one hermetic
(`test_a_chunk_two_query_entities_both_reach_directly_outranks_one_only_one_reaches`, which fails on
the original code) and one live, on the real Velocity contract.

### The exact code I wrote (`src/retrieval/graph_retriever.py`)
```python
# lines 79-95 (inside retrieve: record, per chunk, how near EACH seed got to it)
seed_hops: dict[str, dict[tuple[str, str], int]] = {}
for label, name in seeds:
    entities_by_hop = self._entities_by_hop(name, label)
    for hop, entities in entities_by_hop.items():
        for entity_label, entity_name in entities:
            for chunk_id in self._chunk_ids_for(entity_name, entity_label):
                hops_by_seed = seed_hops.setdefault(chunk_id, {})
                hops_by_seed[(label, name)] = min(hops_by_seed.get((label, name), hop), hop)
                # 🎯 keyed by the ORIGINAL seed, holding the smallest distance THAT seed reached it at

# lines 97-108 (turn those records into a ranking)
best_hop = {chunk_id: min(hops.values()) for chunk_id, hops in seed_hops.items()}
# 🎯 a chunk's nearest distance from ANY seed
agreeing = {
    chunk_id: sum(1 for hop in hops.values() if hop == best_hop[chunk_id])
    for chunk_id, hops in seed_hops.items()
}
# 🎯 INTERVIEW: only seeds reaching the chunk at exactly that nearest distance count as agreeing
ranked = sorted(
    best_hop,
    key=lambda chunk_id: (best_hop[chunk_id], -agreeing[chunk_id], chunk_id),
)
# 🎯 hop distance, then agreement, then chunk_id -- a tuple key, no scores, and no ties left unresolved
```
**🎯 Interview-relevant lines:** the `agreeing` line. The original code kept a `set` of seeds per
chunk, which throws away *how near* each seed got, so a seed that reached a chunk two hops away
counted exactly like one that hit it directly. Keeping the distance per seed (`seed_hops`) is what
lets the corrected rule say "count only the seeds that are as close as the closest one." The final
`chunk_id` in the sort key is the second fix: without it, ties came out in Python `set` order, which
changes on every run (hash randomization), so the same question gave different results on different
days.

### 4. What breaks if this changes
- **Count a seed for reaching a chunk at ANY distance (the original code):** seeds with overlapping
  neighbourhoods tie every chunk in the overlap, so agreement stops separating anything and the
  tie-break decides. On the real graph, naming "Velocity Auto Solutions" and "Carllm" together
  returned exactly the same order as naming Carllm alone.
- **Drop the final `chunk_id` from the sort key:** equal chunks come out in Python `set` order, which
  is randomized per process. The same question returns a different order on each run, and the
  hybrid retriever built on top inherits that irreproducibility.
- **Sort by agreement first, hop distance second:** a chunk two entities reach at hop 2 would outrank
  a chunk one entity hits directly at hop 0 — a distant, weak connection beating a close, direct one.
- **Attribute agreement to the intermediate entity instead of the original seed:** if "Carllm" reaches
  both "Tyler Brooks" and "Rachel Martinez" at hop 1 and both share a chunk, that chunk would look like
  it has 2 agreeing entities when only ONE original query entity is behind both paths.

### [Interview relevance: very high — designing a non-similarity-based ranking signal, and knowing when structural signals beat learned ones, is a strong differentiator in GraphRAG interviews]

**Interview question:** *"How would you rank results from a knowledge-graph query when there's no
natural similarity score, the way there is for vector search?"*
**A strong answer covers:** using structural properties the traversal already computes for free — hop
distance (closer = more relevant) and agreement (how many independent parts of the query's intent
reach this result at its nearest distance, not merely at some distance); being explicit that this
produces an ORDERING, not a numeric score comparable across different queries or fusable with a vector
search's cosine scores without further work; noticing that a ranking with many ties needs a
deterministic final tie-break, or the output differs between runs; and naming that as the real
complexity in a full GraphRAG system — combining a structural ranking with a similarity-based one
requires a deliberate fusion strategy (reciprocal rank fusion is a common choice), not just
concatenating scores from two different scales. P5-M2 builds exactly that fusion.

### Canonical docs
- Edge et al. (2024), ["From Local to Global..."](https://arxiv.org/abs/2404.16130), arXiv:2404.16130
- Cormack, Clarke & Buettcher (2009), ["Reciprocal Rank Fusion"](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) —
  the standard technique for combining rankings from different, non-comparable signals (relevant to
  this project's NEXT step, fusing graph and vector retrieval).

### Micro-exercises
1. Run `d3_ranking_by_convergence.py`. In the `reaches` table, change the Velocity seed's hit on
   `products/Carllm.md_2` from `2` to `0`. Predict the corrected ranking before running it.
2. Change the Velocity seed's hit on its own contract chunk from `0` to `1`. Now which chunk wins, and
   which signal (distance or agreement) decided it?
3. Add a THIRD seed that hits `contracts/Velocity.md_0` directly (hop 0). Confirm three-way agreement
   outranks two-way, under the corrected rule.
4. Read `retrieve()`'s real loop (lines 82-95). If the SAME chunk is reached via two DIFFERENT
   entities that were BOTH found at hop 1 from the SAME seed, how many entries does that chunk get in
   `seed_hops[chunk_id]`? Why does that matter for the correctness of the agreement count?
5. Delete `chunk_id` from the sort key in a scratch copy and run the hermetic test
   `test_chunks_tied_on_distance_and_convergence_come_back_in_chunk_id_order` under
   `PYTHONHASHSEED=1` and `PYTHONHASHSEED=2`. What changes between the two runs, and why?

**How it works internally.** Python's `sorted()` with a tuple key sorts LEXICOGRAPHICALLY — it
compares the first tuple element for all items first, and only looks at the second element to break
ties where the first element is equal, which is exactly "hop distance first, agreement as a
tie-break, chunk_id as the last resort" with no extra code needed beyond the tuple itself. `sorted()`
is also STABLE: items that compare equal keep the order they arrived in, and that arrival order came
from a Python `set`, whose string ordering changes with `PYTHONHASHSEED` on every process start. That
is why the last tuple element cannot be left out.

### How to validate this yourself
Two tests are the validation, and they were written to fail on the original code. The hermetic one,
`test_a_chunk_two_query_entities_both_reach_directly_outranks_one_only_one_reaches`, builds a tiny fake
graph with a Product and a Company two hops apart and requires the chunk both hit directly to come
first. The live one, `test_live_retrieve_ranks_a_chunk_higher_when_two_query_entities_reach_it_directly`,
does the same on the real Velocity contract, and first checks that the chunk is NOT already first when
only the product is named, so passing cannot be an accident of ordering. That first check is the lesson:
a demonstration must fail when the thing it demonstrates is removed.

### How to visualize this
Draw the found entities as two circles overlapping like a Venn diagram; put each chunk in the region
that matches which entities' searches reached it. Write each chunk's distance as a number beside it.
The chunks in the overlap that BOTH circles reach at their nearest distance are the ones agreement
promotes — literally visible as a diagram, no scores needed. A chunk in the overlap that one circle
reaches at distance 0 and the other at distance 2 is NOT promoted; that is the corrected rule, and the
original code missed that distinction.

**You should now be able to explain:** two structural ranking signals that require no similarity model
at all, and why combining a structural ranking with a similarity-based one (vector search) is a real,
separate design problem this step deliberately leaves open.

---

## C5 — Caching expensive, unchanging setup at construction time

### Do this first
Before reading on, run
[`d4_cache_at_construction.py`](../practice%20codes/phase%205/m1_graph_retrieval_deep_dive/d4_cache_at_construction.py).
**Predict** first: if building a `GraphRetriever` costs one network round trip, and you call
`retrieve()` on it 10 times, should that cost be paid once or ten times?

### 1. Definition + analogy
**What it is.** Some setup work produces a result that doesn't change between calls — here,
`store.all_entity_names()`, which reflects the graph's CURRENT state, not anything specific to one
query. Doing that work in `__init__` (once, when the object is built) instead of inside `retrieve()`
(every single call) is a basic but easy-to-miss optimization: pay the cost once, reuse the result for
the object's whole lifetime.

**Analogy.** A chef doesn't re-sharpen every knife before cutting each individual vegetable — they
sharpen once, at the start of prep, and then use the same sharp knives for the whole meal. Re-sharpening
before every single carrot would be correct but wasteful, because sharpness doesn't change between
carrots.

### For a 14-year-old
Imagine you're going to answer 10 different trivia questions about your school, and each question
might mention a teacher's name. Before you can answer ANY of them, you need a list of every teacher's
name to check against. You have two options: you could walk down to the school office and ask for the
full staff list BEFORE every single question — 10 separate trips to the office for the exact same
list, which never changes between question 1 and question 10 — or you could make ONE trip to the
office at the very start, write the whole list down on a sticky note, and then just glance at your own
sticky note for all 10 questions. The second way gets the exact same correct answers, just without
walking to the office nine extra, completely unnecessary times.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| walking to the school office | `store.all_entity_names()` — one real Neo4j query |
| the staff list, unchanging across all 10 questions | the graph's current entity names, unchanging across all calls to `retrieve()` |
| writing it on a sticky note once | `self._names_by_label = store.all_entity_names()` inside `__init__` |
| glancing at the sticky note for question 2 through 10 | every later `retrieve()` call reading `self._names_by_label` — no new query |
| walking to the office before EVERY question (the wasteful way) | `RecomputesEveryCall` in the demo — calling the expensive lookup inside `retrieve()` itself |

### 2. Why here specifically
A `GraphRetriever` is meant to be built once and asked many questions over its lifetime — exactly the
shape where caching unchanging setup pays off most. `all_entity_names()` reflects the graph's state at
CONSTRUCTION time (D7 in this step's decision log): a deliberate trade-off, accepting that the cache
could go stale if the graph changes after the object is built, in exchange for not paying a network
round trip on every single question asked.

### 3. Minimal code
`d4_cache_at_construction.py` — the same expensive lookup, called every time versus cached once:
```python
# Minimal demo of C5: doing expensive, unchanging setup ONCE when an object is built,
# instead of redoing it on every call, using the SAME shape GraphRetriever's __init__ uses.
import time


def slow_lookup():
    time.sleep(0.05)  # stands in for one real network round trip to Neo4j
    return {"Product": ["Carllm"], "Company": ["DriveSmart Insurance"]}


class RecomputesEveryCall:
    def retrieve(self, query):
        names = slow_lookup()  # pays the cost EVERY single call
        return [name for names_ in names.values() for name in names_ if name in query]


class CachesAtConstruction:
    def __init__(self):
        self._names = slow_lookup()  # pays the cost exactly ONCE, when the object is built

    def retrieve(self, query):
        return [name for names_ in self._names.values() for name in names_ if name in query]


QUERIES = ["Carllm", "DriveSmart Insurance", "Carllm", "Carllm"]

started = time.perf_counter()
slow = RecomputesEveryCall()
for q in QUERIES:
    slow.retrieve(q)
print(f"RecomputesEveryCall : {time.perf_counter() - started:.3f}s for {len(QUERIES)} calls")

started = time.perf_counter()
fast = CachesAtConstruction()
for q in QUERIES:
    fast.retrieve(q)
print(f"CachesAtConstruction: {time.perf_counter() - started:.3f}s for {len(QUERIES)} calls")
```
Real output:
```
RecomputesEveryCall : 0.205s for 4 calls
CachesAtConstruction: 0.050s for 4 calls
```
Both classes give identical ANSWERS. The caching version is roughly 4× faster here purely because it
paid the 0.05s setup cost once instead of four times — and the gap only grows the more `retrieve()` is
called.

### The exact code I wrote (`src/retrieval/graph_retriever.py`)
```python
# lines 50-66 (the whole constructor)
def __init__(
    self,
    store: GraphStore,
    *,
    chunks_dir: Path = DEFAULT_OUTPUT_DIR,
    hops: int = DEFAULT_HOPS,
) -> None:
    self._store = store
    self._hops = hops
    self._names_by_label = store.all_entity_names()
    # 🎯 INTERVIEW: the one, one-time network round trip -- everything below reuses this
    self._chunks_by_id: dict[str, DocumentChunk] = {
        chunk.chunk_id: chunk for chunk in read_chunks(chunks_dir)
    }
    # 🎯 the second one-time cost: reading and indexing every chunk, once
```
**🎯 Interview-relevant lines:** both cached values live in `__init__`, not `retrieve()`. Being able
to point to EXACTLY which lines pay a one-time cost, and explain why they're safe to compute once
(they don't depend on the QUERY, only on the graph's and disk's current state) is the whole skill here.

### 4. What breaks if this changes
- **Move `store.all_entity_names()` inside `retrieve()`:** every single question asked pays a full
  network round trip before entity linking can even start — correct, but needlessly slow for a
  `GraphRetriever` meant to answer many questions over its lifetime.
- **Cache it but never refresh it, then run a new graph ingestion while the same `GraphRetriever`
  instance stays alive:** the cached names silently go stale — a newly-added entity would never be
  recognized in any query asked after that point, until a fresh `GraphRetriever` is constructed.

### [Interview relevance: medium — recognizing what's safe to cache and when a cache goes stale is a fundamental systems-design skill, tested in almost any backend interview]

**Interview question:** *"When is it safe to cache a value computed inside a class's constructor, and
what's the risk?"*
**A strong answer covers:** caching is safe when the value doesn't depend on per-call input and
doesn't change faster than the object's own lifetime; the risk is staleness — if the underlying data
source changes while the object is still alive, the cache silently serves outdated information; and
the mitigation is either accepting that trade-off explicitly (as done here, documented as D7) or adding
an explicit refresh/invalidation mechanism if staleness would actually cause a real problem.

### Canonical docs
- [Python data model: `__init__`](https://docs.python.org/3/reference/datamodel.html#object.__init__)
- Fowler, M., ["Memoization"](https://martinfowler.com/bliki/vary.html) and general caching-pattern
  references — the general name for "compute once, reuse the result."

### Micro-exercises
1. Run `d4_cache_at_construction.py`. Change `QUERIES` to have 20 entries instead of 4. How does the
   gap between the two classes' timings change, and why does it grow rather than stay constant?
2. Add a THIRD class, `RefreshesEveryNCalls`, that recomputes `slow_lookup()` every 3rd call. Where's
   the right balance between "always fresh" and "always fast" for a use case you can imagine?
3. In `GraphRetriever`, add a `refresh()` method that recomputes `self._names_by_label`. When would a
   caller need to call it?
4. Time the REAL `GraphRetriever(store)` construction against a live Neo4j (reuse the timing code from
   the earlier smoke test). How much of that time is `all_entity_names()` versus `read_chunks()`?
5. Explain, in one sentence, why `self._hops` does NOT need any caching logic at all, unlike
   `self._names_by_label` and `self._chunks_by_id`.

**How it works internally.** There's no special mechanism here beyond ordinary Python instance
attributes — `self._names_by_label` is just a dict living on the object for as long as the object
itself lives, exactly the same as any other instance variable; the "caching" is entirely a matter of
WHEN the assignment happens (constructor vs. method), not any special caching library or decorator.

### How to validate this yourself
The two timings in the demo's real output ARE the validation — same answers, 4× time difference,
purely from moving one line from inside a loop to inside a constructor.

### How to visualize this
Draw a timeline for both classes across 4 calls: for `RecomputesEveryCall`, draw a "network trip" box
before EVERY call; for `CachesAtConstruction`, draw ONE "network trip" box before all four calls
begin. The total width of the boxes is the visual proof of the speedup.

**You should now be able to explain:** the difference between per-call and per-object setup cost, and
which of `GraphRetriever`'s attributes qualify for construction-time caching and why.

## Consolidated end-to-end exercise: watch the whole thing work

1. **Predict, then link.** Pick a question about this corpus you haven't tried yet. Before running
   anything, write down which entity name(s) you expect `_match_entities` to find in it (C1).
2. **Trace the hops by hand.** For each entity you predicted, sketch (on paper) what you think is
   within 1 hop and within 2 hops, using what you already know about the schema's relationship types
   (`WORKS_ON`, `FOR_PRODUCT`, etc. — see `docs/18`). Then run
   `retriever._entities_by_hop(name, label)` and compare (C2).
3. **Follow one chunk_id from graph to text.** Pick one chunk_id your traversal reaches. Open the
   matching file in `data/chunks/` and confirm the text is what you expected BEFORE calling
   `retrieve()` and letting hydration do it for you (C3).
4. **Predict the ranking, then check it.** Before calling `retrieve()`, guess which chunk will come
   out on top, and whether it's because of hop distance, agreement or (if everything ties) the
   chunk_id order. Run it and see if you were right (C4).
5. **Time it twice.** Call `retriever.retrieve(...)` twice with different questions, and confirm the
   SECOND call is faster to start than the first `GraphRetriever(store)` construction was — proof the
   construction-time cache is doing its job (C5).

If your predictions in steps 1, 2 and 4 were right before you ran anything, you've internalized how
this retriever actually works — not just that it works.
