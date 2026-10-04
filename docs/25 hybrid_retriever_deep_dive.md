# 25 — Hybrid Retriever: Deep Dive (P5-M2)

> Every output block below was produced on 2026-09-24/25 by running the scripts in
> [`practice codes/phase 5/m2_hybrid_retriever_deep_dive/`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/)
> against the real [`src/retrieval/hybrid_retriever.py`](../src/retrieval/hybrid_retriever.py). The
> demos need no database. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 5/m2_hybrid_retriever_deep_dive/d1_rrf_by_hand.py"
> ```
>
> The live numbers (Carllm rank 22, CEO graph rank 1, and so on) come from the real 223-chunk
> collection and 442-node graph; see [`notes/step-5.2-hybrid-retriever.md`](../notes/step-5.2-hybrid-retriever.md).

---

## 0. Plain-language summary

P3 built a retriever that understands *meaning* (vector search). P5-M1 built one that understands
*names and connections* (graph search). Each is wrong in a different place. `HybridRetriever` runs
both on the same question and merges their two ranked lists into one. The merge rule is the whole
lesson: the two lists are not comparable (one has cosine scores, the other only an order), so the rule
uses **positions only**.

```
question
  ├─► vector search  ─► ranked list + cosine scores ─┐
  │                                                   ├─► fuse_rankings (weighted RRF) ─► top k chunks
  └─► graph retrieval ─► ranked list, no scores ──────┘
```

Five concepts, in the order the code uses them:

| # | Concept | Core RAG? | Where in the code |
|---|---|---|---|
| C1 | Hybrid / ensemble retrieval: why two retrievers | 🔴 core | the class as a whole |
| C2 | Reciprocal Rank Fusion: merging by rank | 🔴 core | `fuse_rankings`, lines 109-139 |
| C3 | Agreement beats position; weights | 🔴 core | the `weight / (rrf_c + rank)` lines, 123 and 138 |
| C4 | Candidate depth: why k changes the answer | 🟡 important | `depth = k * multiplier`, line 227 |
| C5 | Pure function + injected retrievers + validate-first | ⚪ mostly plumbing | Protocols 27-36, constructor 168-195, validators 55-81 |

---

## C1 — Hybrid (ensemble) retrieval

### Do this first
Run [`d0_why_two_retrievers.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d0_why_two_retrievers.py).
**Predict first:** for "Who is the CEO of Insurellm?", will the vector list contain the answer? The
graph list? What does the graph return for a question that names no entity?

### 1. Definition + analogy
**What it is.** Hybrid (ensemble) retrieval runs two or more retrievers whose *mistakes differ* and
merges what they return. The gain comes from the merge, not from making either retriever better.
Vector search is weak on names (the first Carllm product page is vector rank 22 of 223); graph search
is blind when a question names no known entity (it returns `[]`).

**Analogy.** You lose a jacket at school. You ask two helpers. Helper A is great at *"it was something
like a blue winter coat"* but useless if you say *"it was Priya's"*. Helper B knows every student by
name and where they sit, but if you only describe the coat, B shrugs. Ask both, and combine their
answers, and you find jackets neither would find alone.

**For a 14-year-old.** Imagine two friends helping you guess a song. One hums the tune back to you and
says "it sounds like these five songs". The other only knows the singers and says "if you tell me the
singer, I know all their songs". If you give a singer's name, the second friend nails it. If you only
remember the vibe, the first one does. A smart person listens to both and pays extra attention to any
song both friends mention.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| helper A: "sounds like" | `QdrantVectorStore.search_with_scores` (cosine similarity) |
| helper B: "knows names" | `GraphRetriever.retrieve` (entity linking + graph walk) |
| "if you only describe the coat, B shrugs" | graph returns `[]` when no entity is named |
| the person listening to both | `HybridRetriever` |
| how they combine the answers | `fuse_rankings` (C2) |
| "extra attention to songs both mention" | a chunk in both lists gets two score contributions (C3) |

### 2. Why here specifically, and what breaks
The project's own labelled questions include two known vector misses (Carllm, CEO). Graph retrieval
alone gets 2 of the 5 at top-3; vector alone gets 3 of 5; together 4 of 5 at k=3.
- **Remove the graph list:** the hybrid becomes plain vector search; CEO is lost again. (Caveat: the
  chunk the label accepts, `company/about.md_0`, names the founder, not the CEO; the chunk stating the
  title, `employees/Avery Lancaster.md_0`, is outside the hybrid's top 20. See notes/step-5.3.)
- **Remove the vector list:** questions naming no entity return nothing.
- **Change: run only when a question names an entity:** you would need an entity check *before*
  retrieval, and lose the agreement bonus on the questions that matter most.

[Interview relevance: very high. "Why hybrid search?" is one of the most common RAG interview
questions, and interviewers listen for *complementary failure modes*, not "it is more accurate".]

**Interview question:** *"When does hybrid retrieval help, and when might it not?"*
**A strong answer covers:** the two retrievers must fail on *different* questions (keyword/graph
strong on exact names, dense strong on paraphrase); the merge rule has to cope with incomparable
scores; it does not help if one retriever is strictly worse everywhere (it just adds noise); you need
labelled questions to know which case you are in.

### 3. Minimal code that explains the concept
The complete script, [`d0_why_two_retrievers.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d0_why_two_retrievers.py) (run from the project root):
```python
# Minimal demo of C1: two retrievers with DIFFERENT blind spots, then the simplest possible merge.
# The lists are the real ones the project produced for "Who is the CEO of Insurellm?" (shortened).
vector_top = [  # what vector search (P3) returned, best first
    "employees/Jordan K. Bishop.md_2",  # a chunk that only sounds similar to the question
    "contracts/Greenstone for Homellm.md_0",  # another similar-sounding chunk
    "contracts/Apex Reinsurance for Rellm.md_0",  # a third similar-sounding chunk
]
graph_top = [  # what graph retrieval (P5-M1) returned, best first
    "company/about.md_0",  # the page the project test accepts (it names the founder)
    "company/careers.md_0",  # another chunk connected to the entity Insurellm
    "company/culture.md_0",  # another chunk connected to the entity Insurellm
]
answer = "company/about.md_0"  # the chunk the project's test labels as an acceptable answer

print("vector alone finds the answer in its top 3:", answer in vector_top)  # False expected
print("graph  alone finds the answer in its top 3:", answer in graph_top)  # True expected
print("graph list for a question naming no entity:", [])  # graph is blind without an entity

merged = []  # the simplest merge: take one chunk from each list in turn
for vector_chunk, graph_chunk in zip(vector_top, graph_top):  # walks both lists side by side
    for chunk_id in (vector_chunk, graph_chunk):  # takes the vector pick, then the graph pick
        if chunk_id not in merged:  # skips a chunk already taken from the other list
            merged.append(chunk_id)  # adds the chunk to the merged list
print("interleaved merge, first 3:", merged[:3])  # the answer now surfaces at position 2
print("answer in merged top 3:", answer in merged[:3])  # True: the merge recovered it
# interleaving ignores agreement and weights, which is what RRF (C2, C3) adds
```
Real output:
```
vector alone finds the answer in its top 3: False
graph  alone finds the answer in its top 3: True
graph list for a question naming no entity: []
interleaved merge, first 3: ['employees/Jordan K. Bishop.md_2', 'company/about.md_0', 'contracts/Greenstone for Homellm.md_0']
answer in merged top 3: True
```

### The exact code I wrote (`src/retrieval/hybrid_retriever.py`)
```python
# src/retrieval/hybrid_retriever.py, lines 27-36, verbatim
 27 | class VectorSearcher(Protocol):
 28 |     def search_with_scores(self, query_text: str, k: int) -> list[ScoredChunk]: ...  # 🎯 vector side: chunks WITH cosine scores
 29 | 
 30 |     # anything that can return scored chunks for a question, such as QdrantVectorStore
 31 | 
 32 | 
 33 | class GraphSearcher(Protocol):
 34 |     def retrieve(self, query: str, k: int) -> list[DocumentChunk]: ...  # 🎯 graph side: chunks, order only, no scores
 35 | 
 36 |     # anything that can return ranked chunks for a question, such as GraphRetriever
```

```python
# src/retrieval/hybrid_retriever.py, lines 39-52, verbatim
 39 | @dataclass(frozen=True, slots=True)
 40 | class FusedChunk:
 41 |     """One fused result: the chunk, its fused score, and where each retriever ranked it."""
 42 | 
 43 |     chunk: DocumentChunk  # 🎯 the chunk itself
 44 |     # the chunk itself, taken from whichever list contained it first
 45 |     score: float
 46 |     # the summed weighted reciprocal-rank contribution from both lists
 47 |     vector_rank: int | None  # 🎯 position in the vector list, or None
 48 |     # the chunk's 1-based position in the vector list, or None if vector search missed it
 49 |     graph_rank: int | None  # 🎯 position in the graph list, or None
 50 |     # the chunk's 1-based position in the graph list, or None if graph retrieval missed it
 51 |     vector_score: float | None
 52 |     # the raw cosine similarity vector search gave it, or None if vector search missed it
```

```python
# src/retrieval/hybrid_retriever.py, lines 210-243, verbatim
210 |     def retrieve_with_scores(
211 |         self,
212 |         query: str,
213 |         k: int,
214 |         vector_weight: float | None = None,
215 |         graph_weight: float | None = None,
216 |     ) -> list[FusedChunk]:
217 |         """The k best fused results, each with its fused score and per-retriever ranks."""
218 |         _check_positive_int("k", k)
219 |         # refuses a k that is not a positive integer before any retriever is called
220 |         use_vector = self._default_vector_weight if vector_weight is None else vector_weight
221 |         # picks the caller's vector weight, or the default when none was passed
222 |         use_graph = self._default_graph_weight if graph_weight is None else graph_weight
223 |         # picks the caller's graph weight, or the default when none was passed
224 |         _check_weights(use_vector, use_graph)
225 |         # refuses unusable weights before any retriever is called
226 | 
227 |         depth = k * self._candidate_multiplier
228 |         # works out how many candidates each retriever is asked for
229 |         vector_hits = self._vector_store.search_with_scores(query, depth)  # 🎯 call 1: vector search
230 |         # asks vector search for its top candidates, with cosine scores
231 |         graph_hits = self._graph_retriever.retrieve(query, depth)  # 🎯 call 2: graph retrieval
232 |         # asks graph retrieval for its top candidates, in ranked order
233 | 
234 |         fused = fuse_rankings(  # 🎯 the merge happens here, and only here
235 |             vector_hits,
236 |             graph_hits,
237 |             vector_weight=use_vector,
238 |             graph_weight=use_graph,
239 |             rrf_c=self._rrf_c,
240 |         )
241 |         # merges the two candidate lists into one fused ranking
242 |         return fused[:k]  # 🎯 cut to k AFTER fusing
243 |         # keeps only the k best fused results
```

**🎯 Interview-relevant lines:** The two Protocols (27-36) say what "a retriever" is to the hybrid, and they make the asymmetry explicit: one returns scores, the other does not. `FusedChunk` (39-52) is the result row that keeps both lists' positions visible. `retrieve_with_scores` (210-243) is the whole class in nine working lines: validate, call both retrievers, fuse, cut. Nothing is combined until `fuse_rankings` runs.

### Canonical docs
- Weaviate, [Hybrid search explained](https://weaviate.io/blog/hybrid-search-explained)
- Qdrant, [Hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- Edge et al. (2024), [arXiv:2404.16130](https://arxiv.org/abs/2404.16130)

### Micro-exercises
1. In `d0`, add a fourth question-shaped case: a question naming no entity. What does the graph list
   become, and what does the fused list equal? *Hint: the test `test_live_a_question_naming_no_entity_gives_exactly_the_vector_order`.*
2. Name a real question where the *graph* is wrong and vector is right. *Hint: a paraphrase that names no entity.*
3. Name a question where *both* are wrong. What would fix it? *Hint: exact-name linking, P5-M1 limit 1.*
4. Change the `answer` in `d0` to a chunk in neither list. What does "hybrid" give you now?
5. Explain in one sentence why hybrid retrieval is useless if list B is a shuffled copy of list A.

**How it works internally.** Nothing is "combined" until `fuse_rankings` runs; the class just calls
each retriever independently (lines 229-231) and hands both lists to the merge.

### How to validate
`tests/retrieval/test_hybrid_retriever.py::test_live_ceo_question_that_vector_search_misses_is_found_by_the_hybrid`
(live) asserts vector-only top-3 misses and hybrid top-3 hits.

### How to visualize
Draw two circles (vector hits, graph hits) over the same 223 dots. Shade the answer dot. It is inside
one circle, or both, or neither; the merge decides what you see first.

**You should now be able to explain:** what "complementary blind spots" means, and why the graph's
lack of scores forces a rank-based merge.

---

## C2 — Reciprocal Rank Fusion (merge by rank)

### Do this first
Run [`d1_rrf_by_hand.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d1_rrf_by_hand.py).
**Predict first:** chunk C is vector rank 3 and graph rank 1; chunk A is vector rank 1 only. Which wins?

### 1. Definition + analogy
**What it is.** Reciprocal Rank Fusion gives every chunk, for each list it appears in, a score
`weight / (c + rank)` (rank starts at 1, `c = 60`), adds the contributions, and sorts. It uses only
**positions**, so cosine floats and an unscored graph order become comparable (Cormack, Clarke &
Buettcher, SIGIR 2009).

**Analogy.** Two judges at a talent show rank contestants but use different scoring scales (one gives
marks out of 10, the other just lines people up). You cannot add "8.5" to "second place". But you can
say: first place is worth a lot, second a bit less, and so on, and add those up across judges.

**For a 14-year-old.** Two teachers each hand you a list of the best students for the science fair.
One list has test scores next to the names, the other list is just names in order. You cannot add a
score to "just an order". So you decide: being 1st on a list earns 1/61 points, 2nd earns 1/62, 3rd
1/63, and so on. Add up each student's points across both lists. Whoever has the most points goes
first. A student who is 3rd on one list and 1st on the other collects two piles of points and beats
someone who was 1st on only one list.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| test scores next to the names | `ScoredChunk.score` (raw cosine), kept only for display in `FusedChunk.vector_score` |
| a list that is just names in order | the `list[DocumentChunk]` from `GraphRetriever.retrieve` |
| "1st earns 1/61" | `weight / (rrf_c + rank)` with rank 1, line 123 / 138 |
| adding piles of points | `scores[chunk_id] = scores.get(chunk_id, 0.0) + ...` |
| "whoever has most goes first" | `fused.sort(key=...)`, line 153 |
| a student on both lists | one `chunk_id` present in both `vector_ranks` and `graph_ranks` |

### 2. Why here specifically, and what breaks
- **Sum the raw scores instead:** the graph has none. You would have to invent one, and a made-up
  graph score on the same scale as cosine would be arbitrary.
- **Use 0-based ranks:** rank 0 with `c = 0` divides by zero; with `c = 60` every value shifts, and
  the tests (which compute `1/(60+rank)` with 1-based ranks) fail.
- **Count a chunk twice if it repeats inside one list** (remove lines 113-116): the same chunk collects
  extra points from one list, an unfair boost. The test
  `test_a_chunk_repeated_inside_one_list_counts_once_at_its_first_rank` covers this.
- **Drop the final tie-break** (lines 153-159): equal scores would fall back on dict order, which is
  deterministic here only because of insertion order; the explicit key makes the rule visible.

[Interview relevance: very high. RRF is a built-in fusion option in Qdrant and Weaviate (Weaviate's
default is relative-score fusion since v1.24, so know both). Being able to write the formula and explain why it needs no score normalisation is a
strong signal.]

**Interview question:** *"How do you combine BM25 and vector results when their scores are on
different scales?"*
**A strong answer covers:** normalise scores (min-max, or relative-score fusion) or use rank-based
fusion; give the RRF formula `Σ 1/(c + rank)` and say `c = 60` from the original paper smooths the
curve; note RRF ignores magnitude (its weakness) and needs no tuning of scales (its strength).

### 3. Minimal code that explains the concept
The complete script, [`d1_rrf_by_hand.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d1_rrf_by_hand.py) (run from the project root):
```python
# Minimal demo of C2: Reciprocal Rank Fusion by hand, then checked against the REAL fuse_rankings.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects with named attributes

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.hybrid_retriever import fuse_rankings  # the real production function


def chunk(name):
    return SimpleNamespace(chunk_id=name)  # a stand-in chunk: fuse_rankings only reads chunk_id


def scored(name, score):
    return SimpleNamespace(chunk=chunk(name), score=score)  # a stand-in vector hit: chunk + cosine


vector_hits = [scored("A", 0.90), scored("B", 0.80), scored("C", 0.70)]  # vector ranks 1, 2, 3
graph_hits = [chunk("C"), chunk("D")]  # graph ranks 1, 2 (no scores at all)
c = 60  # the smoothing constant

by_hand = {  # each chunk score worked out with weight / (c + rank), both weights 1
    "A": 1 / (c + 1),  # only in the vector list, at rank 1
    "B": 1 / (c + 2),  # only in the vector list, at rank 2
    "C": 1 / (c + 3) + 1 / (c + 1),  # vector rank 3 PLUS graph rank 1: two contributions
    "D": 1 / (c + 2),  # only in the graph list, at rank 2
}
print("by hand:")  # heading for the hand-computed scores
for name, score in sorted(by_hand.items(), key=lambda pair: -pair[1]):  # best first
    print(f"  {name}  {score:.5f}")  # one line per chunk

print("real fuse_rankings:")  # heading for the production function answer
for item in fuse_rankings(vector_hits, graph_hits, vector_weight=1.0, graph_weight=1.0):
    print(f"  {item.chunk.chunk_id}  {item.score:.5f}  vec_rank={item.vector_rank}  graph_rank={item.graph_rank}")
# C wins although neither list ranked it first; B and D tie, and the vector rank breaks the tie
```
Real output:
```
by hand:
  C  0.03227
  A  0.01639
  B  0.01613
  D  0.01613
real fuse_rankings:
  C  0.03227  vec_rank=3  graph_rank=1
  A  0.01639  vec_rank=1  graph_rank=None
  B  0.01613  vec_rank=2  graph_rank=None
  D  0.01613  vec_rank=None  graph_rank=2
```

### The exact code I wrote (`src/retrieval/hybrid_retriever.py`)
```python
# src/retrieval/hybrid_retriever.py, lines 84-162, verbatim
 84 | def fuse_rankings(
 85 |     vector_hits: Sequence[ScoredChunk],
 86 |     graph_hits: Sequence[DocumentChunk],
 87 |     *,
 88 |     vector_weight: float,
 89 |     graph_weight: float,
 90 |     rrf_c: float = RRF_C,
 91 | ) -> list[FusedChunk]:
 92 |     """Merge a scored vector ranking and a plain graph ranking, best chunk first."""
 93 |     _check_weights(vector_weight, graph_weight)
 94 |     # refuses unusable weights before any arithmetic happens
 95 |     _check_number("rrf_c", rrf_c, minimum=0)
 96 |     # refuses a negative or non-numeric smoothing constant
 97 | 
 98 |     chunks: dict[str, DocumentChunk] = {}
 99 |     # keeps one DocumentChunk per chunk_id, whichever list produced it first
100 |     scores: dict[str, float] = {}
101 |     # keeps the running fused score for each chunk_id
102 |     vector_ranks: dict[str, int] = {}
103 |     # keeps each chunk_id's 1-based position in the vector list
104 |     graph_ranks: dict[str, int] = {}
105 |     # keeps each chunk_id's 1-based position in the graph list
106 |     vector_scores: dict[str, float] = {}
107 |     # keeps each chunk_id's raw cosine similarity from the vector list
108 | 
109 |     for rank, hit in enumerate(vector_hits, start=1):  # 🎯 ranks are 1-based
110 |         # walks the vector list from best to worst, numbering positions from 1
111 |         chunk_id = hit.chunk.chunk_id
112 |         # reads the id that identifies this chunk in both lists
113 |         if chunk_id in vector_ranks:  # 🎯 a repeat inside ONE list is ignored
114 |             # spots a chunk that already appeared earlier in this same list
115 |             continue
116 |             # skips it so a repeat cannot be counted twice
117 |         vector_ranks[chunk_id] = rank
118 |         # records where the vector list ranked this chunk
119 |         vector_scores[chunk_id] = hit.score
120 |         # records the cosine similarity the vector list gave this chunk
121 |         chunks.setdefault(chunk_id, hit.chunk)
122 |         # remembers the chunk object if no earlier list has supplied it
123 |         scores[chunk_id] = scores.get(chunk_id, 0.0) + vector_weight / (rrf_c + rank)  # 🎯 THE formula (vector list)
124 |         # adds the vector list's weighted reciprocal-rank contribution
125 | 
126 |     for rank, chunk in enumerate(graph_hits, start=1):
127 |         # walks the graph list from best to worst, numbering positions from 1
128 |         chunk_id = chunk.chunk_id
129 |         # reads the id that identifies this chunk in both lists
130 |         if chunk_id in graph_ranks:
131 |             # spots a chunk that already appeared earlier in this same list
132 |             continue
133 |             # skips it so a repeat cannot be counted twice
134 |         graph_ranks[chunk_id] = rank
135 |         # records where the graph list ranked this chunk
136 |         chunks.setdefault(chunk_id, chunk)
137 |         # remembers the chunk object if no earlier list has supplied it
138 |         scores[chunk_id] = scores.get(chunk_id, 0.0) + graph_weight / (rrf_c + rank)  # 🎯 THE formula (graph list)
139 |         # adds the graph list's weighted reciprocal-rank contribution
140 | 
141 |     fused = [
142 |         FusedChunk(
143 |             chunk=chunks[chunk_id],
144 |             score=scores[chunk_id],
145 |             vector_rank=vector_ranks.get(chunk_id),
146 |             graph_rank=graph_ranks.get(chunk_id),
147 |             vector_score=vector_scores.get(chunk_id),
148 |         )
149 |         for chunk_id in chunks
150 |     ]
151 |     # builds one FusedChunk per distinct chunk_id seen in either list
152 | 
153 |     fused.sort(  # 🎯 ties: vector rank first, then graph rank
154 |         key=lambda item: (
155 |             -item.score,
156 |             math.inf if item.vector_rank is None else item.vector_rank,
157 |             math.inf if item.graph_rank is None else item.graph_rank,
158 |         )
159 |     )
160 |     # orders by fused score, then by vector rank, then by graph rank, so ties are deterministic
161 |     return fused
162 |     # hands back every fused chunk, best first
```

**🎯 Interview-relevant lines:** Lines 123 and 138 are the whole algorithm. Everything else is bookkeeping so a result can explain itself: four dictionaries keyed by `chunk_id` (98-107), one pass per list, one sort (153-159).

### Canonical docs
- Cormack, Clarke & Buettcher (2009), [Reciprocal Rank Fusion](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)
- Weaviate, [Hybrid search explained](https://weaviate.io/blog/hybrid-search-explained) (the sum, and `k = 60`)
- Rackauckas (2024), [RAG-Fusion, arXiv:2402.03367](https://arxiv.org/abs/2402.03367)

### Micro-exercises
1. By hand: a chunk at vector rank 4 and graph rank 10. Score? *Hint: `1/64 + 1/70`.* Check it with `d1`.
2. In `d1`, change `graph_hits` to `[chunk("D"), chunk("C")]`. Who wins now and why? *Hint: C loses its rank-1 graph bonus.*
3. Add a chunk E present in neither list. Does it appear in the output? *Hint: only ids seen in a list get a score.*
4. Give `vector_hits` a repeated chunk (A at ranks 1 and 2). What is A's `vector_rank`? *Hint: the first one; line 113.*
5. Predict and check: if both weights are 0, what does `fuse_rankings` do? *Hint: `_check_weights`, line 78.*

**How it works internally.** Four dictionaries (`chunks`, `scores`, `vector_ranks`, `graph_ranks`) are
keyed by `chunk_id`. Each list is walked once, best to worst, adding to `scores`. The final list of
`FusedChunk` is built from the keys of `chunks` and sorted once. Total work: two passes and a sort.

### How to validate
`tests/retrieval/test_hybrid_retriever.py::test_the_fused_score_is_the_weighted_sum_of_reciprocal_ranks`
(exact arithmetic) and the live `test_live_every_fused_score_equals_its_recomputed_reciprocal_rank_sum`.

### How to visualize
A two-column table: rows are chunks, columns are `vector_rank` and `graph_rank`, a third column the
score. `retrieve_with_scores` already prints exactly this data.

**You should now be able to explain:** the RRF formula, why it needs no score normalisation, and what
information it throws away.

---

## C3 — Agreement beats position; weights

### Do this first
Run [`d2_agreement_and_weights.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d2_agreement_and_weights.py).
**Predict first:** a chunk ranked 9th by vector and 3rd by graph, versus a chunk ranked 1st by only one
list. At `c = 60`, which wins? At `c = 1`?

### 1. Definition + analogy
**What it is.** Because `1/(60 + rank)` falls slowly (rank 20 is worth only about 24% less than rank
1), *appearing in both lists* is worth more than *ranking first in one*. Weights multiply a list's
contribution; only their **ratio** matters, and 0 silences a list's influence on order.

**Analogy.** Two independent friends both mention a restaurant, though neither's favourite. That is
better evidence than one friend's raving about a place the other has never heard of.

**For a 14-year-old.** Two classmates who do not know each other both say "try the pizza place on Elm
Street", though neither puts it first. A third person shouts "the burger place is the BEST!" but nobody
else has heard of it. You would probably trust the two independent mentions more. Weights are like
saying "I trust my friend Sam twice as much as Priya": Sam's votes count double.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| two friends both mention the pizza place | a chunk in both `vector_ranks` and `graph_ranks` |
| "neither's favourite" | middling ranks, like 9 and 3 |
| the burger place shouted about by one person | a chunk with only one list contribution, at rank 1 |
| "I trust Sam twice as much" | `vector_weight=2, graph_weight=1` |
| "ignore Priya completely" | a weight of `0` |
| how much a favourite matters | the constant `rrf_c`: small `c` makes rank 1 dominate |

### 2. Why here specifically, and what breaks
- **`c = 1`:** rank 1 gets 1/2 and rank 9+3 gets 0.35, so the single #1 wins; agreement stops mattering.
  This is the CEO/Carllm story reversed. See the real output below.
- **Weights (2, 1) at depth ×3:** the CEO answer exists only in the graph list, so it is pushed out at
  every k tried. Weights (1, 2) helped both hard questions. That is two questions and a knob turned
  while looking at the answers, so it is overfitting, not evidence.
- **Negative weights:** would *subtract* evidence, so they are rejected at line 74-77.
- **Both weights 0:** every score is 0; rejected at line 78.

[Interview relevance: high. Interviewers ask "how do you tune the balance between lexical and semantic
search?" and expect: weights or `alpha`, tuned on a labelled set, not by eyeballing.]

**Interview question:** *"You set hybrid weights to favour keyword search and one query improved. Do
you ship it?"*
**A strong answer covers:** one query is not evidence; you need a labelled set (dozens of questions),
a metric (hit@k, MRR), and a held-out split so you do not overfit the weights; report per-question
wins and losses, not only the average.

### 3. Minimal code that explains the concept
The complete script, [`d2_agreement_and_weights.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d2_agreement_and_weights.py) (run from the project root):
```python
# Minimal demo of C3: at c=60 two middling ranks beat one great rank, and weights shift that.
def rrf(vector_rank, graph_rank, c=60, wv=1.0, wg=1.0):
    total = 0.0  # starts the running score at zero
    if vector_rank is not None:  # only counts the vector list if it ranked the chunk
        total += wv / (c + vector_rank)  # adds the weighted reciprocal of the vector rank
    if graph_rank is not None:  # only counts the graph list if it ranked the chunk
        total += wg / (c + graph_rank)  # adds the weighted reciprocal of the graph rank
    return total  # hands back the fused score


print("TechDrive contract (vector rank 9, graph rank 3) vs a chunk ranked #1 by one list only:")
for c in (1, 60, 1000):  # a top-heavy constant, the real one, and a huge one
    both = rrf(9, 3, c=c)  # the chunk both lists rank mediocrely
    one = rrf(1, None, c=c)  # the chunk only one list ranks, but ranks first
    print(f"  c={c:<5} both-lists={both:.5f}  one-list-#1={one:.5f}  agreement wins={both > one}")

print("\nWeights: vector-only #1 versus graph-only #1 (only the RATIO matters):")
for wv, wg in ((1, 1), (2, 1), (1, 2), (0.5, 1)):  # four weight settings; (1,2) equals (0.5,1)
    v = rrf(1, None, wv=wv, wg=wg)  # the vector-only chunk score
    g = rrf(None, 1, wv=wv, wg=wg)  # the graph-only chunk score
    winner = "vector" if v > g else "graph" if g > v else "tie"  # who is ahead
    print(f"  weights=({wv},{wg})  vector-only={v:.5f}  graph-only={g:.5f}  ahead: {winner}")
```
Real output:
```
TechDrive contract (vector rank 9, graph rank 3) vs a chunk ranked #1 by one list only:
  c=1     both-lists=0.35000  one-list-#1=0.50000  agreement wins=False
  c=60    both-lists=0.03037  one-list-#1=0.01639  agreement wins=True
  c=1000  both-lists=0.00199  one-list-#1=0.00100  agreement wins=True

Weights: vector-only #1 versus graph-only #1 (only the RATIO matters):
  weights=(1,1)  vector-only=0.01639  graph-only=0.01639  ahead: tie
  weights=(2,1)  vector-only=0.03279  graph-only=0.01639  ahead: vector
  weights=(1,2)  vector-only=0.01639  graph-only=0.03279  ahead: graph
  weights=(0.5,1)  vector-only=0.00820  graph-only=0.01639  ahead: graph
```

### The exact code I wrote (`src/retrieval/hybrid_retriever.py`)
```python
# src/retrieval/hybrid_retriever.py, lines 55-81, verbatim
 55 | def _check_number(name: str, value: float, *, minimum: float) -> None:
 56 |     if isinstance(value, bool) or not isinstance(value, int | float):  # 🎯 True is an int in Python, so bool is rejected explicitly
 57 |         # rejects anything that is not a plain int or float, including True and False
 58 |         raise ValueError(f"{name} must be a finite number >= {minimum}, got {value!r}")
 59 |         # names the argument and the value so the caller can see what was wrong
 60 |     if not math.isfinite(value) or value < minimum:  # 🎯 NaN and infinity rejected
 61 |         # rejects NaN, infinity, and anything below the allowed minimum
 62 |         raise ValueError(f"{name} must be a finite number >= {minimum}, got {value!r}")
 63 |         # names the argument and the value so the caller can see what was wrong
 64 | 
 65 | 
 66 | def _check_positive_int(name: str, value: int) -> None:
 67 |     if isinstance(value, bool) or not isinstance(value, int) or value < 1:
 68 |         # rejects anything that is not a plain integer of at least 1
 69 |         raise ValueError(f"{name} must be a positive integer, got {value!r}")
 70 |         # names the argument and the value so the caller can see what was wrong
 71 | 
 72 | 
 73 | def _check_weights(vector_weight: float, graph_weight: float) -> None:
 74 |     _check_number("vector_weight", vector_weight, minimum=0)
 75 |     # confirms the vector weight is a finite number that is not negative
 76 |     _check_number("graph_weight", graph_weight, minimum=0)
 77 |     # confirms the graph weight is a finite number that is not negative
 78 |     if vector_weight == 0 and graph_weight == 0:  # 🎯 the one pair of valid weights that scores everything 0
 79 |         # catches the one combination that would give every chunk a score of zero
 80 |         raise ValueError("vector_weight and graph_weight cannot both be 0")
 81 |         # explains why two individually valid weights were still rejected
```

```python
# src/retrieval/hybrid_retriever.py, lines 197-225, verbatim
197 |     def retrieve(
198 |         self,
199 |         query: str,
200 |         k: int,
201 |         vector_weight: float | None = None,  # 🎯 None means use the default; 0 is a real value
202 |         graph_weight: float | None = None,
203 |     ) -> list[DocumentChunk]:
204 |         """The k best chunks after fusing both rankings, best first."""
205 |         fused = self.retrieve_with_scores(query, k, vector_weight, graph_weight)
206 |         # runs the full fusion, keeping the scores and ranks for a moment
207 |         return [item.chunk for item in fused]
208 |         # drops the scores and ranks, leaving only the chunks in fused order
209 | 
210 |     def retrieve_with_scores(
211 |         self,
212 |         query: str,
213 |         k: int,
214 |         vector_weight: float | None = None,
215 |         graph_weight: float | None = None,
216 |     ) -> list[FusedChunk]:
217 |         """The k best fused results, each with its fused score and per-retriever ranks."""
218 |         _check_positive_int("k", k)
219 |         # refuses a k that is not a positive integer before any retriever is called
220 |         use_vector = self._default_vector_weight if vector_weight is None else vector_weight  # 🎯 is None, not `or`: 0 or 1.0 would give 1.0
221 |         # picks the caller's vector weight, or the default when none was passed
222 |         use_graph = self._default_graph_weight if graph_weight is None else graph_weight
223 |         # picks the caller's graph weight, or the default when none was passed
224 |         _check_weights(use_vector, use_graph)
225 |         # refuses unusable weights before any retriever is called
```

```python
# src/retrieval/hybrid_retriever.py, lines 123-123, verbatim
123 |         scores[chunk_id] = scores.get(chunk_id, 0.0) + vector_weight / (rrf_c + rank)  # 🎯 weight multiplies the reciprocal rank
```

**🎯 Interview-relevant lines:** `fuse_rankings` (see C2) multiplies each list's reciprocal rank by its weight at lines 123 and 138. The validators (55-81) guarantee the weights are usable; `retrieve_with_scores` (220-225) resolves per-call weights against constructor defaults. `None` versus `0` is the subtle part.

### Canonical docs
- Weaviate, [Hybrid search explained](https://weaviate.io/blog/hybrid-search-explained): ranked fusion vs relative-score fusion
- Cormack et al. (2009): the experiments behind `c = 60`

### Micro-exercises
1. In `d2`, add `c=10`. Where does agreement stop winning? *Hint: solve `1/(c+9)+1/(c+3) > 1/(c+1)`.*
2. Prove `(1,2)` and `(0.5,1)` give the same order for any two chunks. *Hint: multiply every score by 2.*
3. Find weights where the graph-only #1 loses to the vector-only #3. *Hint: `wg/61 < wv/63`.*
4. What does weight `0` do to a chunk that is only in that list? *Hint: score 0; it still appears, last.*
5. Why does the code use `is None` and not `or`? *Hint: `0 or 1.0` is `1.0`.*

**How it works internally.** Weights enter only as multipliers in the two addition lines, so scaling
both by the same factor scales every score equally and cannot change the order.

### How to validate
`test_weights_decide_between_chunks_that_only_one_retriever_found`,
`test_a_zero_weight_silences_that_list_but_its_chunks_still_appear_last`,
`test_constructor_default_weights_apply_and_per_call_weights_override_them`.

### How to visualize
Plot score against rank for `c = 1`, `60`, `1000`. At 60 the curve is nearly flat, which is the whole
reason agreement dominates.

**You should now be able to explain:** why two middling ranks can beat one first place, what `c`
controls, and why five questions cannot pick the weights.

---

## C4 — Candidate depth: why k changes the answer

### Do this first
Run [`d3_candidate_depth.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d3_candidate_depth.py).
**Predict first:** does the `PAGE` chunk (vector rank 22, graph rank 7) appear at k=3? At k=10? Are the
first three results of k=10 the same as the k=3 result?

### 1. Definition + analogy
**What it is.** Each retriever is asked for `3 × k` candidates, not `k`, and the fused list is cut to
`k`. A chunk that is mediocre in both lists can only win if both lists are read deep enough to *see* it.
The price: the pool depends on `k`, so `retrieve(q, 3)` is not `retrieve(q, 10)[:3]`.

**Analogy.** Choosing a class captain: if each teacher can only nominate their top 3, a student ranked
5th by both never gets a look. Let each nominate their top 9 and that student's two mentions add up.

**For a 14-year-old.** You are picking a team and two coaches each give you a list. If you only read
the top 3 of each list, a player who is 5th on both lists is invisible, even though two coaches like
him. Read the top 9 and he appears twice and rises. But now the answer to "who are the best 3" depends
on how far down you read, and reading further can bring in more surprise names.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| how many names you read from each coach | `depth = k * self._candidate_multiplier`, line 227 |
| the team size you finally pick | `fused[:k]`, line 242 |
| "read further, more players show up" | a larger multiplier, e.g. ×10 |
| the invisible player | the Carllm product page, vector rank 22 |
| "the answer depends on how far you read" | results depend on `k` |

### 2. Why here specifically, and what breaks
- **depth = k:** fusion degenerates to interleaving two top-k lists; agreement can rarely form.
- **depth ×10:** fixes Carllm at every k, but the CEO answer is pushed out at k>=5, because more
  "two middling ranks" chunks compete. No single depth won both hard questions.
- **Fixed depth (say 50):** results become independent of `k`, but you pay for 50 candidates on every
  call, even `k=1`.

[Interview relevance: high. "How many candidates do you retrieve before reranking or fusing?" is a
standard design question; the trade-off is recall versus noise versus latency.]

**Interview question:** *"Why retrieve more candidates than you return?"*
**A strong answer covers:** fusion and reranking can only promote what is in the pool (recall ceiling);
larger pools raise recall but add noise and cost; the pool size should be tuned on labelled data; and
if it is a multiple of `k`, results are not prefix-consistent, which matters for paging and caching.

### 3. Minimal code that explains the concept
The complete script, [`d3_candidate_depth.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d3_candidate_depth.py) (run from the project root):
```python
# Minimal demo of C4: each retriever is asked for depth = 3*k candidates, so k changes the answer.
# Uses the REAL HybridRetriever with fake retrievers shaped like the real Carllm situation.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.hybrid_retriever import HybridRetriever  # the real class


def chunk(name):
    return SimpleNamespace(chunk_id=name)  # a stand-in chunk with just a chunk_id


VECTOR_ORDER = [f"v{n}" for n in range(1, 22)] + ["PAGE"] + [f"v{n}" for n in range(23, 41)]  # PAGE at 22
VECTOR_ORDER[8] = "CONTRACT"  # the contract chunk sits at vector rank 9
GRAPH_ORDER = ["g1", "g2", "CONTRACT", "g4", "g5", "g6", "PAGE"] + [f"g{n}" for n in range(8, 41)]  # PAGE at 7


class FakeVector:
    def search_with_scores(self, query_text, k):
        return [SimpleNamespace(chunk=chunk(c), score=0.5) for c in VECTOR_ORDER[:k]]  # top k only


class FakeGraph:
    def retrieve(self, query, k):
        return [chunk(c) for c in GRAPH_ORDER[:k]]  # top k only


hybrid = HybridRetriever(FakeVector(), FakeGraph())  # depth multiplier defaults to 3
for k in (3, 10):  # a small k and a larger one
    ids = [c.chunk_id for c in hybrid.retrieve("question", k)]  # the fused top k
    print(f"k={k:<2} depth={3 * k:<2} -> {ids[:5]}  PAGE in result: {'PAGE' in ids}")
first_three_of_10 = [c.chunk_id for c in hybrid.retrieve("q", 10)][:3]  # top 3 of the k=10 call
result_for_3 = [c.chunk_id for c in hybrid.retrieve("q", 3)]  # the k=3 call
print("first three of k=10 equal the k=3 result:", first_three_of_10 == result_for_3)  # prefix check
```
Real output:
```
k=3  depth=9  -> ['CONTRACT', 'v1', 'g1']  PAGE in result: False
k=10 depth=30 -> ['CONTRACT', 'PAGE', 'v1', 'g1', 'v2']  PAGE in result: True
first three of k=10 equal the k=3 result: False
```

### The exact code I wrote (`src/retrieval/hybrid_retriever.py`)
```python
# src/retrieval/hybrid_retriever.py, lines 165-195, verbatim
165 | class HybridRetriever:
166 |     """Runs vector search and graph retrieval on one question and fuses the two rankings."""
167 | 
168 |     def __init__(
169 |         self,
170 |         vector_store: VectorSearcher,
171 |         graph_retriever: GraphSearcher,
172 |         *,
173 |         default_vector_weight: float = DEFAULT_WEIGHT,  # 🎯 constructor default weights
174 |         default_graph_weight: float = DEFAULT_WEIGHT,
175 |         rrf_c: float = RRF_C,
176 |         candidate_multiplier: int = CANDIDATE_MULTIPLIER,  # 🎯 multiplier: pool size = k times this
177 |     ) -> None:
178 |         _check_weights(default_vector_weight, default_graph_weight)
179 |         # refuses unusable default weights at construction time
180 |         _check_number("rrf_c", rrf_c, minimum=0)
181 |         # refuses a negative or non-numeric smoothing constant at construction time
182 |         _check_positive_int("candidate_multiplier", candidate_multiplier)  # 🎯 a multiplier below 1 would ask for nothing
183 |         # refuses a multiplier that would ask each retriever for nothing
184 |         self._vector_store = vector_store
185 |         # keeps the vector retriever that every question will be sent to
186 |         self._graph_retriever = graph_retriever
187 |         # keeps the graph retriever that every question will be sent to
188 |         self._default_vector_weight = default_vector_weight
189 |         # keeps the vector weight used when a call does not pass one
190 |         self._default_graph_weight = default_graph_weight
191 |         # keeps the graph weight used when a call does not pass one
192 |         self._rrf_c = rrf_c
193 |         # keeps the smoothing constant used in every fusion
194 |         self._candidate_multiplier = candidate_multiplier
195 |         # keeps how many times k each retriever is asked for
```

```python
# src/retrieval/hybrid_retriever.py, lines 210-243, verbatim
210 |     def retrieve_with_scores(
211 |         self,
212 |         query: str,
213 |         k: int,
214 |         vector_weight: float | None = None,
215 |         graph_weight: float | None = None,
216 |     ) -> list[FusedChunk]:
217 |         """The k best fused results, each with its fused score and per-retriever ranks."""
218 |         _check_positive_int("k", k)
219 |         # refuses a k that is not a positive integer before any retriever is called
220 |         use_vector = self._default_vector_weight if vector_weight is None else vector_weight
221 |         # picks the caller's vector weight, or the default when none was passed
222 |         use_graph = self._default_graph_weight if graph_weight is None else graph_weight
223 |         # picks the caller's graph weight, or the default when none was passed
224 |         _check_weights(use_vector, use_graph)
225 |         # refuses unusable weights before any retriever is called
226 | 
227 |         depth = k * self._candidate_multiplier  # 🎯 the pool size
228 |         # works out how many candidates each retriever is asked for
229 |         vector_hits = self._vector_store.search_with_scores(query, depth)  # 🎯 asked for depth, not k
230 |         # asks vector search for its top candidates, with cosine scores
231 |         graph_hits = self._graph_retriever.retrieve(query, depth)  # 🎯 asked for depth, not k
232 |         # asks graph retrieval for its top candidates, in ranked order
233 | 
234 |         fused = fuse_rankings(
235 |             vector_hits,
236 |             graph_hits,
237 |             vector_weight=use_vector,
238 |             graph_weight=use_graph,
239 |             rrf_c=self._rrf_c,
240 |         )
241 |         # merges the two candidate lists into one fused ranking
242 |         return fused[:k]  # 🎯 the cut to k comes AFTER fusion
243 |         # keeps only the k best fused results
```

**🎯 Interview-relevant lines:** The multiplier is a constructor argument (176) validated at 182. Line 227 turns `k` into the pool size, lines 229 and 231 ask both retrievers for that many, and line 242 cuts to `k` only after fusion. `fuse_rankings` itself never sees `k`.

### Canonical docs
- Weaviate, [Hybrid search explained](https://weaviate.io/blog/hybrid-search-explained)
- Qdrant, [Hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/) (prefetch limits are the same idea)

### Micro-exercises
1. In `d3`, pass `candidate_multiplier=8` and re-run at k=3. Does `PAGE` appear? *Hint: need depth >= 22.*
2. Set the multiplier to 1. What changes at k=10? *Hint: only 10 deep in each list.*
3. Move `PAGE` to vector rank 15. At which k does it first appear? *Hint: depth 3k >= 15.*
4. Write down `max(3k, 30)` as a depth rule. Does it make `retrieve(q,3)` equal `retrieve(q,10)[:3]`? *Hint: at k=3 and k=10 the depth is 30 for both.*
5. Give one caller (paging? caching?) that breaks on prefix inconsistency. *Hint: page 2 = results 4-6.*

**How it works internally.** The retrievers are called with a larger `k`; nothing in `fuse_rankings`
knows about `k` at all. It fuses whatever lists it is given.

### How to validate
`test_each_retriever_is_asked_for_three_times_k_and_vector_goes_first`,
`test_the_candidate_multiplier_is_configurable`, and the live pair
`test_live_carllm_product_question_at_k3_is_still_a_miss` (strict xfail) /
`test_live_carllm_product_question_is_found_once_the_candidate_pool_is_deeper`.

### How to visualize
Draw the two ranked lists as vertical strips, mark the top `depth` of each with a bracket, and colour
the chunk that lies inside only one bracket at k=3 and both at k=10.

**You should now be able to explain:** why the pool must be deeper than `k`, and why that makes results
depend on `k`.

---

## C5 — Pure function, injected retrievers, validate first

### Do this first
Run [`d4_seams_and_validation.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d4_seams_and_validation.py).
**Predict first:** in what order are the two retrievers called, and are they called at all when `k=0`?

### 1. Definition + analogy
**What it is.** Three ordinary engineering habits that make the two real ideas testable: the merge is a
**pure function** (data in, data out, no I/O); the class takes its retrievers as **injected**
arguments typed by a Protocol (anything with the right method works); and arguments are **validated
before** any expensive call.

**Analogy.** A cook who checks the order slip before switching on the oven, and who uses whatever pan
you hand over, is easier to test than one who fetches ingredients from a locked cupboard.

**For a 14-year-old.** Before you spend 20 minutes baking, you check the recipe says "2 eggs" and not
"-2 eggs". If the recipe is nonsense you say so at once. And if the recipe just says "any pan", you can
test it with a toy pan instead of the good one.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| "check the recipe first" | `_check_positive_int` and `_check_weights` before lines 229-231 |
| "any pan will do" | the `VectorSearcher` / `GraphSearcher` Protocols |
| the toy pan | `RecordingVector` / `RecordingGraph` in `d4`, `FakeVector` in the tests |
| baking | the real Qdrant and Neo4j calls |
| the maths done on paper | `fuse_rankings`, a pure function |

### 2. Why here specifically, and what breaks
- **Validate after the calls:** a bad `k` would cost a full embedding call and a graph query before failing.
- **Hard-code `QdrantVectorStore`:** every test would need services running (the test suite would
  drop from 33 hermetic tests to almost none).
- **Protocols are not enforced at runtime:** an object with the right method names but different
  meaning is accepted. That is the accepted trade-off of duck typing.
- **Catch retriever errors and continue:** a broken Neo4j would silently degrade the system. Errors
  propagate on purpose.

[Interview relevance: medium. Not RAG-specific, but "how did you test it without the database?" is a
common follow-up, and the answer is dependency injection.]

**Interview question:** *"How would you unit-test a hybrid retriever without running a vector database?"*
**A strong answer covers:** separate the pure merge from I/O and test it with plain numbers; inject the
retrievers behind an interface and use fakes that record arguments; add one integration test with a
real in-memory store; keep a small live suite for acceptance only.

### 3. Minimal code that explains the concept
The complete script, [`d4_seams_and_validation.py`](../practice%20codes/phase%205/m2_hybrid_retriever_deep_dive/d4_seams_and_validation.py) (run from the project root):
```python
# Minimal demo of C5: injected retrievers make the class testable with fakes, and bad arguments
# are rejected BEFORE either retriever is called.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.hybrid_retriever import HybridRetriever  # the real class

calls = []  # records every call the fakes receive, in order


class RecordingVector:
    def search_with_scores(self, query_text, k):
        calls.append(("vector", k))  # notes that vector search was called, and with which k
        return []  # returns no hits


class RecordingGraph:
    def retrieve(self, query, k):
        calls.append(("graph", k))  # notes that graph retrieval was called, and with which k
        return []  # returns no hits


hybrid = HybridRetriever(RecordingVector(), RecordingGraph())  # any objects with these methods work
hybrid.retrieve("q", 4)  # one valid call
print("valid call order and k:", calls)  # expect vector first, then graph, both asked for 12

calls.clear()  # forgets the earlier calls
bad_calls = (  # three unusable calls
    ("k=0", {"k": 0}),  # k must be at least 1
    ("weight=-1", {"k": 3, "vector_weight": -1}),  # negative weight
    ("both weights 0", {"k": 3, "vector_weight": 0, "graph_weight": 0}),  # every score would be 0
)
for label, kwargs in bad_calls:  # tries each bad call in turn
    try:
        hybrid.retrieve("q", **kwargs)  # a call with an unusable argument
    except ValueError as error:
        print(f"{label:<15} -> ValueError: {error}")  # shows the message the caller sees
print("retrievers called during the bad calls:", calls)  # expect an empty list
```
Real output:
```
valid call order and k: [('vector', 12), ('graph', 12)]
k=0             -> ValueError: k must be a positive integer, got 0
weight=-1       -> ValueError: vector_weight must be a finite number >= 0, got -1
both weights 0  -> ValueError: vector_weight and graph_weight cannot both be 0
retrievers called during the bad calls: []
```

### The exact code I wrote (`src/retrieval/hybrid_retriever.py`)
```python
# src/retrieval/hybrid_retriever.py, lines 27-36, verbatim
 27 | class VectorSearcher(Protocol):
 28 |     def search_with_scores(self, query_text: str, k: int) -> list[ScoredChunk]: ...  # 🎯 anything with this method is a vector retriever
 29 | 
 30 |     # anything that can return scored chunks for a question, such as QdrantVectorStore
 31 | 
 32 | 
 33 | class GraphSearcher(Protocol):
 34 |     def retrieve(self, query: str, k: int) -> list[DocumentChunk]: ...  # 🎯 anything with this method is a graph retriever
 35 | 
 36 |     # anything that can return ranked chunks for a question, such as GraphRetriever
```

```python
# src/retrieval/hybrid_retriever.py, lines 55-81, verbatim
 55 | def _check_number(name: str, value: float, *, minimum: float) -> None:
 56 |     if isinstance(value, bool) or not isinstance(value, int | float):  # 🎯 bool is an int subclass: rejected on purpose
 57 |         # rejects anything that is not a plain int or float, including True and False
 58 |         raise ValueError(f"{name} must be a finite number >= {minimum}, got {value!r}")
 59 |         # names the argument and the value so the caller can see what was wrong
 60 |     if not math.isfinite(value) or value < minimum:  # 🎯 NaN, infinity, and below-minimum rejected
 61 |         # rejects NaN, infinity, and anything below the allowed minimum
 62 |         raise ValueError(f"{name} must be a finite number >= {minimum}, got {value!r}")
 63 |         # names the argument and the value so the caller can see what was wrong
 64 | 
 65 | 
 66 | def _check_positive_int(name: str, value: int) -> None:
 67 |     if isinstance(value, bool) or not isinstance(value, int) or value < 1:
 68 |         # rejects anything that is not a plain integer of at least 1
 69 |         raise ValueError(f"{name} must be a positive integer, got {value!r}")
 70 |         # names the argument and the value so the caller can see what was wrong
 71 | 
 72 | 
 73 | def _check_weights(vector_weight: float, graph_weight: float) -> None:
 74 |     _check_number("vector_weight", vector_weight, minimum=0)
 75 |     # confirms the vector weight is a finite number that is not negative
 76 |     _check_number("graph_weight", graph_weight, minimum=0)
 77 |     # confirms the graph weight is a finite number that is not negative
 78 |     if vector_weight == 0 and graph_weight == 0:  # 🎯 both weights 0 rejected
 79 |         # catches the one combination that would give every chunk a score of zero
 80 |         raise ValueError("vector_weight and graph_weight cannot both be 0")
 81 |         # explains why two individually valid weights were still rejected
```

```python
# src/retrieval/hybrid_retriever.py, lines 165-195, verbatim
165 | class HybridRetriever:
166 |     """Runs vector search and graph retrieval on one question and fuses the two rankings."""
167 | 
168 |     def __init__(
169 |         self,
170 |         vector_store: VectorSearcher,
171 |         graph_retriever: GraphSearcher,
172 |         *,
173 |         default_vector_weight: float = DEFAULT_WEIGHT,
174 |         default_graph_weight: float = DEFAULT_WEIGHT,
175 |         rrf_c: float = RRF_C,
176 |         candidate_multiplier: int = CANDIDATE_MULTIPLIER,
177 |     ) -> None:
178 |         _check_weights(default_vector_weight, default_graph_weight)  # 🎯 constructor validates its own defaults
179 |         # refuses unusable default weights at construction time
180 |         _check_number("rrf_c", rrf_c, minimum=0)
181 |         # refuses a negative or non-numeric smoothing constant at construction time
182 |         _check_positive_int("candidate_multiplier", candidate_multiplier)
183 |         # refuses a multiplier that would ask each retriever for nothing
184 |         self._vector_store = vector_store  # 🎯 the injected retrievers are simply stored
185 |         # keeps the vector retriever that every question will be sent to
186 |         self._graph_retriever = graph_retriever
187 |         # keeps the graph retriever that every question will be sent to
188 |         self._default_vector_weight = default_vector_weight
189 |         # keeps the vector weight used when a call does not pass one
190 |         self._default_graph_weight = default_graph_weight
191 |         # keeps the graph weight used when a call does not pass one
192 |         self._rrf_c = rrf_c
193 |         # keeps the smoothing constant used in every fusion
194 |         self._candidate_multiplier = candidate_multiplier
195 |         # keeps how many times k each retriever is asked for
```

```python
# src/retrieval/hybrid_retriever.py, lines 210-227, verbatim
210 |     def retrieve_with_scores(
211 |         self,
212 |         query: str,
213 |         k: int,
214 |         vector_weight: float | None = None,
215 |         graph_weight: float | None = None,
216 |     ) -> list[FusedChunk]:
217 |         """The k best fused results, each with its fused score and per-retriever ranks."""
218 |         _check_positive_int("k", k)  # 🎯 validation runs BEFORE either retriever is called
219 |         # refuses a k that is not a positive integer before any retriever is called
220 |         use_vector = self._default_vector_weight if vector_weight is None else vector_weight
221 |         # picks the caller's vector weight, or the default when none was passed
222 |         use_graph = self._default_graph_weight if graph_weight is None else graph_weight
223 |         # picks the caller's graph weight, or the default when none was passed
224 |         _check_weights(use_vector, use_graph)
225 |         # refuses unusable weights before any retriever is called
226 | 
227 |         depth = k * self._candidate_multiplier
```

**🎯 Interview-relevant lines:** The Protocols (27-36) are what makes fakes possible; the validators (55-81) and the first lines of `retrieve_with_scores` (218-225) run before any retriever call at 229-231, so a bad argument costs no embedding request and no graph query.

### Canonical docs
- Python docs, [`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
- Python docs, [`math.isfinite`](https://docs.python.org/3/library/math.html#math.isfinite)

### Micro-exercises
1. In `d4`, add a `weight=nan` call. What message do you get? *Hint: `float("nan")`; `isfinite` is False.*
2. Pass `True` as `k`. Why does it fail? *Hint: bool is a subclass of int.*
3. Make `RecordingGraph.retrieve` raise. Does `hybrid.retrieve` catch it? *Hint: no; D-2026-09-24-07.*
4. Swap the order of the two calls in your copy. Does the result change? *Hint: no, each list is independent; only which error appears first changes.*
5. Give a class satisfying `VectorSearcher` without importing anything from `src`. *Hint: just define `search_with_scores`.*

**How it works internally.** Protocols are only read by the type checker (mypy); at runtime Python
just calls the method. That is why fakes work with no base class.

### How to validate
`test_bad_arguments_are_rejected_before_either_retriever_is_called`,
`test_an_error_from_either_retriever_propagates`, `test_unusable_weights_are_rejected`.

### How to visualize
A timeline with the validation step as a gate before the two retriever boxes: a red arrow leaves the
gate for a bad argument and never reaches them.

**You should now be able to explain:** why the merge is a pure function, why validation precedes the
calls, and what dependency injection buys you in tests.

---

## Consolidated end-to-end exercise: watch it work

1. **Predict.** For "Who is the CEO of Insurellm?" at k=3, write down which retriever supplies
   `company/about.md_0`, what rank it has, and what its fused score will be. *(Answer key is in
   `notes/step-5.2-hybrid-retriever.md`, but predict first.)*
2. **Run it live** (Docker services up):
   ```bash
   .venv/Scripts/python -c "from src.embedding.embedder import Embedder, build_ollama_client; from src.graph.setup import build_driver; from src.retrieval.graph_retriever import GraphRetriever; from src.retrieval.graph_store import GraphStore; from src.retrieval.hybrid_retriever import HybridRetriever; from src.vectorstore.setup import build_qdrant_client; from src.vectorstore.store import QdrantVectorStore; e=Embedder(build_ollama_client('http://localhost:11434'), ollama_url='http://localhost:11434'); v=QdrantVectorStore(build_qdrant_client('http://localhost:6333'), e, collection='chunks'); d=build_driver().__enter__(); h=HybridRetriever(v, GraphRetriever(GraphStore(d))); [print(i.score, i.vector_rank, i.graph_rank, i.chunk.chunk_id) for i in h.retrieve_with_scores('Who is the CEO of Insurellm?', 3)]"
   ```
3. **Explain each row** from `vector_rank` and `graph_rank` alone (C2, C3).
4. **Vary k** (3, 5, 10) for "What does the Carllm product do?" and watch the first `products/Carllm`
   chunk appear (C4).
5. **Break it on purpose:** pass `vector_weight=2, graph_weight=1` and watch the CEO answer disappear
   (C3), then pass `k=0` and confirm neither retriever runs (C5).

If your predictions in steps 1, 3 and 4 were right before you ran anything, you understand this
retriever, not just that it works.
