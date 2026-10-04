# Phase 5, Microstep 2 — Hybrid Retriever (Vector + Graph)

**Date:** 2026-09-24
**Core learning: Yes — ensemble retrieval (rank fusion).** Two retrievers now exist with different
blind spots; this step is the first one whose job is to combine them, and the combining rule is the
whole lesson.

## The gap this step closes
Vector search (P3) finds text that *means* something like the question, but it is weak on names:
on this corpus the first `products/Carllm.md_*` page is at rank 22 of 223 for "What does the Carllm
product do?", and no chunk the labels accept for "Who is the CEO of Insurellm?" is in the top 40. Graph
retrieval (P5-M1) is the opposite: it is strong when the question names a known entity, and blind
(returns `[]`) when it does not. It also produces an *ordering* with no scores, while vector search
produces cosine similarities, so the two lists cannot simply be added together.

| Question (the five labelled in `tests/test_retrieval.py`) | vector top-3 | graph top-3 | hybrid top-3 |
|---|---|---|---|
| Who founded Insurellm? | hit (rank 1) | hit (rank 1) | hit (rank 1) |
| What are the company values? | hit (rank 3) | `[]`, names no entity | hit (rank 3) |
| Which product is for life insurance? | hit (rank 1) | `[]`, names no entity | hit (rank 1) |
| What does the Carllm product do? | miss (page at rank 22) | miss (page at rank 7) | **miss at k=3, hit at rank 3 at k=10** |
| Who is the CEO of Insurellm? | miss (absent from top 40) | hit (rank 1) | **hit (rank 2)** |

## What was built
- **[`src/retrieval/hybrid_retriever.py`](../src/retrieval/hybrid_retriever.py)**
  - `fuse_rankings(vector_hits, graph_hits, *, vector_weight, graph_weight, rrf_c=60)`: a pure
    function, no I/O. Every list adds `weight / (rrf_c + rank)` to each chunk it contains; the sums
    decide the order.
  - `HybridRetriever(vector_store, graph_retriever, *, default_vector_weight=1.0,
    default_graph_weight=1.0, rrf_c=60, candidate_multiplier=3)`.
  - `retrieve(query, k, vector_weight=None, graph_weight=None) -> list[DocumentChunk]`: the required
    signature. A weight left as `None` falls back to the constructor default.
  - `retrieve_with_scores(...) -> list[FusedChunk]`: the same call, but each result carries its fused
    score, its rank in each list, and the raw cosine, so every position can be explained.
- **No existing public interface changed.** `QdrantVectorStore` was not touched. `GraphRetriever`'s
  interface was not touched either, but two **defects in its ranking were found while building this
  step and fixed** — see "Two P5-M1 defects" below.
- **Tests:** [`tests/retrieval/test_hybrid_retriever.py`](../tests/retrieval/test_hybrid_retriever.py),
  42 tests: 33 hermetic (pure arithmetic, fake retrievers, and a real in-memory Qdrant with a toy
  embedder) and 9 live (real Qdrant + Ollama + Neo4j together, the first test in this project needing
  all three at once): 8 pass and 1 is a strict `xfail`. Plus 2 new hermetic tests in
  `tests/retrieval/test_graph_retriever.py` for the two fixes.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Fusion | weighted Reciprocal Rank Fusion (ranks only) | weighted sum of normalised scores; vector-first concatenation | throws away magnitude: a 0.95 and a 0.60 cosine at adjacent ranks count the same |
| D2 Weights | non-negative multipliers, per call, default 1.0/1.0; only the ratio matters | fractions summing to 1; module constants | nothing tells you the right ratio: at depth ×3, (2,1) *loses* the CEO answer at every k tried, (1,2) finds it, and five questions cannot say which to trust |
| D3 Constant `c` | 60, configurable | 1 (top-heavy); tuned | the curve is flat (rank 20 is worth only ~24% less than rank 1), so agreement between lists beats position inside one list |
| D4 Candidate depth | each retriever asked for `3 × k`, fused list cut to `k` | exactly `k`; a fixed 50/100 | **results depend on k**: `retrieve(q, 3)` is not `retrieve(q, 10)[:3]` |
| D5 Failures | an empty graph list adds nothing; exceptions from either retriever propagate | catch graph errors and fall back to vector-only | a down Neo4j fails the whole request instead of quietly degrading (loud, but less available) |
| D6 Return type | `retrieve` → `list[DocumentChunk]`; `retrieve_with_scores` → `FusedChunk`s | scored objects only | a caller of `retrieve` cannot see why something ranked; it must call the second method |
| D7 Acceptance | the five labelled questions, live; known misses kept as strict xfail | a new synthetic set; hermetic tests only | five questions can demonstrate an effect but cannot tune anything |
| D8 Execution | sequential: vector, then graph | concurrent threads/async | latency is the sum of the two, not the larger |
| D9 Module shape | new module: pure function + class, Protocol-typed injection | subclass `GraphRetriever`; add a method to `QdrantVectorStore` | a Protocol is not checked at runtime: any object with the right method names is accepted, even with different semantics |

## Verified, not assumed
```
this step's tests    : 42 (33 hermetic + 9 live); live = 8 passed + 1 xfailed (strict)
whole suite, live    : 594 passed, 3 xfailed  (all three services up; ~90s)
CI mode              : 523 passed, 74 deselected  (-m "not tiktoken and not live")
ruff, format, mypy   : clean
latency (15 calls)   : vector median 85 ms, graph median 141 ms (0.7 ms when no entity is named),
                       hybrid median 230 ms  ~ their sum, because D8 runs them one after the other
```
The three `xfailed` are the two original vector-only misses in `tests/test_retrieval.py` (they test
`QdrantVectorStore` alone, which this step did not change, so they *should* still fail) and the new
hybrid Carllm k=3 case.

**A worked example: why the Carllm contract chunk wins at k=3.** `contracts/Contract with TechDrive
Insurance for Carllm.md_0` is vector rank 9 and graph rank 3. Fused score
`1/(60+9) + 1/(60+3) = 0.01449 + 0.01587 = 0.03037`. The vector list's own #1 scores
`1/61 = 0.01639` and the graph list's own #1 also `0.01639`. Two middling ranks beat one great rank.
That is RRF working as designed, and it is also the reason candidate depth (D4) matters: this chunk
only gets a vector rank at all because the vector list was read nine deep.

**The sweep** (first rank at which an acceptable chunk appears; "-" = not in the top k). Depth ×3 and
weights (1,1) are the defaults agreed *before* this was run; the sweep describes sensitivity and was
not used to choose them.

| Carllm ("product page") | k=3 | k=5 | k=10 |
|---|---|---|---|
| depth ×3, weights (1,1)  ← default | - | - | 3 |
| depth ×3, weights (1,2) | - | - | 2 |
| depth ×3, weights (2,1) | - | - | 5 |
| depth ×10, weights (1,1) | 3 | 3 | 3 |
| depth ×10, weights (1,2) | 2 | 3 | 3 |

| CEO | k=3 | k=5 | k=10 |
|---|---|---|---|
| depth ×3, weights (1,1)  ← default | 2 | 2 | 3 |
| depth ×3, weights (1,2) | 1 | 1 | 2 |
| depth ×3, weights (2,1) | - | - | - |
| depth ×10, weights (1,1) | 3 | - | - |
| depth ×10, weights (1,2) | 2 | - | - |

The three known-answer questions gave 1, 3, 1 in every cell.

## What the numbers say (and do not say)
1. **The CEO miss is fixed at k=3, partly by luck.** The hit is `company/about.md_0`, graph rank 1.
   "Insurellm" is a hub entity connected to almost everything, so hundreds of chunks tie on hop
   distance and agreement, and the final tie-break is alphabetical `chunk_id`, where `company/` sorts
   first. A differently-named file would not have been rank 1. The fusion is working; the graph
   ranking underneath it is weakly informative for hub entities.
   **Correction added 2026-09-25 (found during P5-M3):** the label is also lenient. `company/about.md_0`
   says "Insurellm was founded by Avery Lancaster in 2015": it names the founder, not the CEO. The
   chunk that states the title, `employees/Avery Lancaster.md_0`, is not in the hybrid's top 20 at all
   (it appears at rank 17 only when 50 are requested). So "the CEO miss is fixed" means "an accepted,
   indirect answer now reaches the top 3", not "the chunk that states the answer does".
2. **The Carllm miss is not fixed at k=3.** Vector search only reaches a Carllm product page at rank
   22, and at depth 9 it is never seen. At k=10 (depth 30) it is found, at rank 3. This is recorded as
   a strict xfail (so fixing it later turns the run red until someone promotes it) plus a passing k=10
   test, rather than hidden by raising the default depth until the test goes green.
3. **No single depth wins both questions.** Depth ×10 fixes Carllm at every k but loses the CEO at
   k≥5, because a deeper pool lets more "two middling ranks" chunks outscore a chunk that is strong in
   only one list. Weights (1,2) helped both misses and (2,1) hurt both, but that is two questions and
   a knob turned while looking at the answers, which is overfitting, not evidence.
4. **Results depend on k.** With depth = 3k, `retrieve("...Carllm...", 3)` returns
   `[TechDrive md_0, Lifellm md_2, DriveSmart md_0]` while `retrieve(..., 10)[:3]` returns
   `[TechDrive md_0, TechDrive md_1, products/Carllm md_0]`. Anything that pages, caches by prefix, or
   compares runs at different k will see this. Options if it matters: a depth floor
   (`max(3k, 30)`) or a fixed depth. Not changed, because it is a design decision.
5. **Five questions are a demonstration, not a tuning set.** Weights, `c` and depth should be chosen
   from a labelled set of a few dozen questions with a held-out part, using a metric such as hit@k or
   MRR.

## Two P5-M1 defects found and fixed while building this
Both were in `GraphRetriever`'s ranking and both were my errors. Disclosed in full because two of my
own earlier claims rested on them.
1. **Ties came out in a different order on every run.** `_chunk_ids_for` returns a `set`; string
   order in a `set` changes with `PYTHONHASHSEED`, which is randomized per process, and `sorted()` is
   stable, so tied chunks kept whatever order the set gave them. Found because the hybrid sweep gave
   different fused lists for the same question. Fixed by adding `chunk_id` as the last sort key.
   Regression test proven by mutation: fails under 4 hash seeds without the fix, passes under 4 with
   it. **Side effect:** alphabetical order puts `company/` before `contracts/` before `employees/`
   before `products/`, so product pages sit last among ties. That is a bias, not a relevance signal.
2. **The "convergence" signal was diluted, and my earlier verification of it was wrong.** A query
   entity was credited for reaching a chunk *at any distance*, so a Company and the Product it
   licenses (two hops apart) tied on every chunk in their overlap, and the alphabet decided. The
   "DriveSmart contract moves from 3rd to 1st" result I reported for P5-M1 was an insertion-order
   artifact. The rule is now: count a query entity only for chunks it reaches at that chunk's
   *nearest* distance. The new hermetic test failed before the fix and passes after. I treated this as
   a bug against the stated intent of D-2026-09-23-05, not a new design choice; reverting it is one
   line if you prefer the old rule. The five acceptance questions each name at most one entity, so
   their results are unaffected.

`docs/24`, `notes/step-5.1-graph-retrieval.md`, `docs/architecture.md` and `decision.md` were
corrected to match.

## Key concepts
🔴 = core to RAG and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Hybrid / ensemble retrieval:** run two or more retrievers whose mistakes differ, then merge
  their results; the gain comes from the merge, not from either retriever getting better.
- 🔴 **Reciprocal Rank Fusion (RRF):** score(chunk) = Σ over lists of `weight / (c + rank)`; uses
  positions only, so cosine floats and an unscored graph ordering become comparable.
- 🔴 **Agreement as evidence:** a chunk found by *both* retrievers gets two contributions, and this
  is where most of the fusion gain on this corpus comes from.
- 🟡 **Candidate depth:** how far down each list you read before fusing; too shallow loses a chunk
  that is only mid-ranked in each list, too deep floods the pool with noise.
- 🟡 **Weighted fusion:** per-list multipliers; only their ratio matters; 0 removes a list's
  influence on order (its chunks still appear, scored only by the other list).
- 🟡 **Complete sort keys / deterministic ties:** if a sort key leaves ties, the output depends on
  input order, and input order from a `set` of strings is not stable across runs.
- 🟡 **A test that passes for the wrong reason:** a demonstration must fail when the thing it
  demonstrates is removed. P5-M1's convergence "demo" did not.
- ⚪ **Protocol-typed injection, validation helpers, frozen dataclass:** ordinary plumbing that makes
  the two real ideas above testable without any service running.

## Honest limitations
1. **Rank-only fusion.** Magnitude (a 0.95 vs a 0.60 cosine) is discarded on purpose; if magnitude
   matters, score-based fusion or a reranker is the next tool.
2. **Graph ranking is weak for hub entities** ("Insurellm"): hundreds of ties, broken alphabetically.
3. **Depth makes results k-dependent**, and no depth wins both hard questions.
4. **No evaluation set**, so weights, `c` and depth are unproven defaults, not tuned values.
5. **Sequential calls:** latency is vector + graph (about 230 ms median here).
6. **Neo4j down means the hybrid fails**, by design (D5), rather than degrading to vector-only.
7. **Entity linking is still exact-name only** (P5-M1's limitation): a paraphrase gives the graph
   list nothing, and the hybrid then equals vector search.

## How this connects
**Previous:** P3 gave ranked chunks with cosine scores (`search_with_scores`); P5-M1 gave graph-ranked
chunks with no scores. **This step:** merges them and makes the disagreement between them visible
(`vector_rank`, `graph_rank`). **Next (not yet specified to me):** Phase 5 is titled "Retrieval &
Reranking", so the likely next step is a reranker that reads the question and each fused candidate
together and re-scores them, which is the tool for the magnitude and hub-entity problems above.

## How to do this yourself
1. Write down what each retriever actually returns (scores? only an order?) and pick the common
   denominator. Here it was rank.
2. Write the merge as a **pure function over plain data** first and test it with numbers you worked
   out by hand, before any class or service exists.
3. Decide the candidate depth on purpose and look at what it does to results at more than one `k`.
4. Wrap it in a class that takes its retrievers as arguments, and test call order, arguments,
   defaults, and error propagation with fakes. No services needed.
5. Check it against a real (in-memory) store, then run the live acceptance questions. Keep known
   misses as strict `xfail` with an honest reason.
6. Before trusting any live result, run it twice and under a different `PYTHONHASHSEED`.

## Practice exercises
1. By hand: a chunk at vector rank 4 and graph rank 10, weights (1,1), `c=60`. What is its score?
   Check it against `fuse_rankings`.
2. Run `retrieve_with_scores("Who is the CEO of Insurellm?", 3)`. Two results have the same score;
   which rule separates them, and in which line of the code?
3. Call `retrieve` with weights (1,0), then (0,1). Compare with pure vector and pure graph order.
   What is different about the chunks the muted list contributes?
4. Find the smallest `candidate_multiplier` that puts a `products/Carllm.md_*` chunk in the top 3 at
   k=3. Predict it from "vector rank 22" first.
5. Set `rrf_c` to 1, 60, then 100000. Describe what happens to "agreement beats position". What does
   `c → ∞` reduce the scoring to?
6. Swap the order of the two retriever calls in `retrieve_with_scores`. Does any result change? What
   does change (think of two failures at once)?
7. Ask "What does Insurellm do?". Read the graph list: how many chunks tie? What does the fused list
   look like next to the vector list?
8. Generalise `fuse_rankings` on paper to N ranked lists with N weights. What happens to the
   `vector_rank` / `graph_rank` fields of `FusedChunk`?
9. What would relative-score fusion need from the graph list that it does not have? Invent a graph
   score and say what could go wrong with it.
10. Design an evaluation set: 30 questions, how you label acceptable chunks, which metric, and how you
    split tuning questions from held-out questions so the weights are not overfit.

## Sources
- Cormack, Clarke & Buettcher (2009), ["Reciprocal Rank Fusion outperforms Condorcet and individual
  Rank Learning Methods"](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf), SIGIR 2009 — where
  the formula and `c = 60` come from.
- Rackauckas (2024), ["RAG-Fusion"](https://arxiv.org/abs/2402.03367), arXiv:2402.03367 — RRF applied
  to RAG retrieval.
- Edge et al. (2024), ["From Local to Global: A Graph RAG Approach"](https://arxiv.org/abs/2404.16130),
  arXiv:2404.16130 — why a graph signal is worth fusing with a vector one.
- Weaviate, ["Hybrid search explained"](https://weaviate.io/blog/hybrid-search-explained) — the RRF
  sum, `k = 60`, and how it differs from relative-score fusion (their default since v1.24).
- Qdrant, [hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/) — RRF and DBSF
  as built-in fusion options in the store this project already uses.
- LangChain's `EnsembleRetriever` implements the same idea (weighted RRF). I could not open its docs
  page to confirm the details, so treat that as a pointer, not a citation.
- [docs/24 graph_retrieval_deep_dive.md](../docs/24%20graph_retrieval_deep_dive.md) — the graph list
  this step consumes, including the corrected ranking rule.
