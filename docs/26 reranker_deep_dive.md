# 26 — Reranker: Deep Dive (P5-M3)

> The demo outputs below were produced on 2026-09-25 by running the scripts in
> [`practice codes/phase 5/m3_reranker_deep_dive/`](../practice%20codes/phase%205/m3_reranker_deep_dive/)
> against the real [`src/retrieval/reranker.py`](../src/retrieval/reranker.py). None of them needs a
> Cohere key, a database, or the network. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 5/m3_reranker_deep_dive/d1_rerank_call_shape.py"
> ```
>
> **Honest status:** the reranker has never been run against the real Cohere API (no key in `.env`
> yet). Everything below about Cohere's behaviour comes from its documentation or the SDK's source;
> everything about results comes from fakes, a stubbed network, or the hybrid retriever alone. The
> pool numbers in C3 are real measurements of the hybrid retriever on the real corpus.

---

## 0. Plain-language summary

Retrieval so far (vector search, graph search, and their fusion) ranks chunks without ever reading the
question and a chunk *together*. `Reranker` adds a second stage: take a short list of candidates, hand
the question and each candidate's text to a model that reads them as a pair, and keep the best k.

```
question ─► hybrid retriever ─► N candidates (say 20) ─► Reranker (one Cohere call) ─► top k chunks
            (recall: don't miss it)                        (precision: put the best first)
```

Five concepts, in the order the code uses them:

| # | Concept | Core RAG? | Where in the code |
|---|---|---|---|
| C1 | Two-stage retrieval: bi-encoder vs cross-encoder | 🔴 core | the class as a whole |
| C2 | The rerank call: what goes out, what comes back | 🔴 core | `rerank_with_scores`, lines 165-190 |
| C3 | The candidate pool is the ceiling | 🔴 core | the input to `rerank`, produced by the hybrid |
| C4 | A client that fails fast: no hidden retries | 🟡 important | `build_cohere_client`, lines 69-85 |
| C5 | The small rules: validate, de-duplicate, short-circuit, inject | ⚪ mostly plumbing | lines 88-144, 155-161 |

---

## C1 — Two-stage retrieval: bi-encoder vs cross-encoder

### Do this first
Run [`d0_bi_vs_cross_encoder.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d0_bi_vs_cross_encoder.py).
**Predict first:** two chunks contain exactly the same words in a different order. Will a scorer that
turns each chunk into a vector *before* seeing the question tell them apart? Will one that reads the
question and chunk together?

### 1. Definition + analogy
**What it is.** A **bi-encoder** (what embedding search uses) turns the question and every chunk into
vectors *separately* and compares them; chunk vectors can be computed once, in advance, so it is fast
enough to search the whole corpus. A **cross-encoder** (what a reranker is) takes the question and one
chunk as a *single input* and outputs one relevance score; because it needs the question, nothing can
be precomputed, so it costs one model pass per candidate per question. **Two-stage retrieval** uses
the cheap one to find candidates (recall) and the accurate one to order them (precision). The idea
comes from Nogueira & Cho (2019).

**Analogy.** A talent show. Round 1 judges only watch recorded audition tapes made before anyone knew
which song would be requested. Round 2 is one expert who hears the request and each finalist perform
it, live. You can afford the expert for 20 finalists, not for 10,000 applicants.

**For a 14-year-old.** You are looking for a library book that answers your homework question. A quick
helper scans covers and titles of thousands of books and pulls 20 that look right. That is fast, but a
cover cannot tell whether the book really answers *your* question. Then you sit down and read the first
page of each of the 20 with your question in your head, and pick the best three. Reading is slow, so
you only do it for the 20, not the thousands. The second step is the reranker.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the quick helper scanning covers | vector search / graph search / their fusion (`HybridRetriever`) |
| the 20 books pulled | the list of `DocumentChunk`s passed to `Reranker.rerank` |
| reading the first page with your question in mind | Cohere scoring the question and one chunk text together |
| picking the best three | `top_n = k`, and `ranked[:k]` |
| "reading is slow, so only for the 20" | one request per question, with a short candidate list |

### 2. Why here specifically, and what breaks
P5-M2's fusion uses positions only; it never checks whether a chunk states the answer. On this
corpus, for "Who is the CEO of Insurellm?" the hybrid's 20 candidates are mostly other employees' HR
records.
- **Rerank the whole corpus instead of a pool:** 223 model passes per question instead of 20, and the
  cost grows with the corpus.
- **Skip the reranker:** ordering stays position-based; chunks that merely share entities with the
  question keep outranking chunks that state the answer.
- **Rerank only 3 chunks (`N = k`):** it can only permute what it already has, so it can fix
  nothing (C3).

[Interview relevance: very high. "Why use a reranker, and why not just a better embedding model?" is a
standard RAG interview question; interviewers listen for the cost model, not for brand names.]

**Interview question:** *"Why not use a cross-encoder for the whole retrieval step?"*
**A strong answer covers:** a cross-encoder must see the question, so no document-side work can be done
in advance and cost is O(corpus) per query; a bi-encoder precomputes document vectors, so query cost is
one embedding plus a nearest-neighbour lookup; hence retrieve-then-rerank; the candidate list size trades
recall against latency and money; and rerankers improve *order*, they cannot add what stage 1 missed.

### 3. Minimal code that explains the concept
The complete script, [`d0_bi_vs_cross_encoder.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d0_bi_vs_cross_encoder.py) (run from the project root):
```python
# Minimal demo of C1: a bi-encoder scores a chunk WITHOUT seeing the question; a cross-encoder scores
# the (question, chunk) pair TOGETHER. Both scorers here are toys, built to show the SHAPE of each.
import math  # gives square roots for the cosine formula

QUESTION = "Carllm handles claims"  # the question, without punctuation
CHUNKS = {  # two chunks made of EXACTLY the same words, in a different order
    "A": "Carllm handles claims not quotes",  # the words of the question appear together, in order
    "B": "claims quotes not handles Carllm",  # the same words, scattered
}


def words(text):
    return text.lower().split()  # splits a text into lower-case words


def vector(text):
    counts = {}  # will hold how many times each word occurs
    for word in words(text):  # walks every word in the text
        counts[word] = counts.get(word, 0) + 1  # counts the word
    return counts  # hands back the word-count vector


def cosine(a, b):
    dot = sum(a[w] * b.get(w, 0) for w in a)  # multiplies matching words and adds them up
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))  # multiplies the two lengths
    return dot / norm  # returns the cosine similarity


doc_vectors = {name: vector(text) for name, text in CHUNKS.items()}  # BI-ENCODER: every chunk is turned into a vector ONCE, before any question exists
embed_calls = len(CHUNKS)  # counts the offline document passes just made


def bi_encoder_score(question, name):
    return cosine(vector(question), doc_vectors[name])  # compares the question vector with the stored chunk vector


def cross_encoder_score(question, text):
    doc = words(text)  # splits the chunk into words
    hits = [i for i, w in enumerate(doc) if w in words(question)]  # finds where each question word sits in the chunk
    if not hits:  # covers a chunk with no question word at all
        return 0.0  # scores it zero
    closeness = 1.0 / (1 + (max(hits) - min(hits)))  # rewards question words that appear close together in the chunk
    coverage = len(set(doc) & set(words(question))) / len(set(words(question)))  # rewards covering more of the question
    return round(coverage * closeness, 3)  # combines both into one pair score


print("BI-ENCODER   (chunk vectors precomputed, question compared to them):")  # heading for the first scorer
for name in CHUNKS:  # scores each chunk
    print(f"  chunk {name}: {bi_encoder_score(QUESTION, name):.3f}")  # prints the cosine
print("CROSS-ENCODER (question and chunk read together, one pass per pair):")  # heading for the second scorer
for name, text in CHUNKS.items():  # scores each chunk
    print(f"  chunk {name}: {cross_encoder_score(QUESTION, text):.3f}")  # prints the pair score

corpus_size, pool = 223, 20  # this project's chunk count and the pool size used in the tests
print(f"passes per question, bi-encoder: 1 query vector (the {corpus_size} chunk vectors already exist)")  # cost of stage 1
print(f"passes per question, cross-encoder over the whole corpus: {corpus_size}")  # cost if a cross-encoder scored everything
print(f"passes per question, cross-encoder over a pool of {pool}: {pool}")  # cost when it only scores the shortlist
# the toy scorers are not real models; the point is WHEN each one can run and what each one gets to see
```
Real output:
```
BI-ENCODER   (chunk vectors precomputed, question compared to them):
  chunk A: 0.775
  chunk B: 0.775
CROSS-ENCODER (question and chunk read together, one pass per pair):
  chunk A: 0.333
  chunk B: 0.200
passes per question, bi-encoder: 1 query vector (the 223 chunk vectors already exist)
passes per question, cross-encoder over the whole corpus: 223
passes per question, cross-encoder over a pool of 20: 20
```

The toys are not real models. Read the *shape*: the bi-encoder ties the two chunks (same words, so the
same vector) while the pair scorer separates them, because it can look at how the question's words sit
in the chunk. That is exactly the extra information a real cross-encoder uses.

### The exact code I wrote (`src/retrieval/reranker.py`)
```python
# src/retrieval/reranker.py, lines 49-67, verbatim
 49 | class RerankClient(Protocol):
 50 |     def rerank(  # 🎯 the only thing a Reranker needs from a client: score these documents against this query
 51 |         self, *, model: str, query: str, documents: Sequence[str], top_n: int
 52 |     ) -> RerankResponse: ...
 53 | 
 54 |     # anything that can score documents against a query, such as cohere.ClientV2
 55 | 
 56 | 
 57 | @dataclass(frozen=True, slots=True)
 58 | class RerankedChunk:  # 🎯 the result row: the chunk, the score, and where it came in
 59 |     """One reranked result: the chunk, the model's relevance score, and where it came in."""
 60 | 
 61 |     chunk: DocumentChunk
 62 |     # the chunk itself, exactly as the caller passed it in
 63 |     relevance_score: float
 64 |     # the reranker's score for this chunk, meaningful only next to scores from the same model
 65 |     input_rank: int
 66 |     # the chunk's 1-based position in the list the caller passed in, before reranking
 67 | 
```

```python
# src/retrieval/reranker.py, lines 125-134, verbatim
125 | class Reranker:
126 |     """Reorders candidate chunks by how well each one answers the question."""
127 | 
128 |     def __init__(self, client: RerankClient, *, model: str = DEFAULT_MODEL) -> None:  # 🎯 the client is injected, not built here
129 |         _check_text("model", model)
130 |         # refuses an empty model name at construction time
131 |         self._client = client
132 |         # keeps the client every rerank request will be sent through
133 |         self._model = model
134 |         # keeps the model name sent with every request
```

**🎯 Interview-relevant lines:** the `RerankClient` Protocol (49-55) says a reranker needs exactly one
capability: given a question and documents, return scored positions. Nothing in it says "bi" or
"cross"; the model choice is entirely inside whatever implements `rerank`.

### Canonical docs
- Nogueira & Cho (2019), [Passage Re-ranking with BERT](https://arxiv.org/abs/1901.04085)
- Cohere, [Rerank overview](https://docs.cohere.com/docs/rerank)
- Sentence-Transformers, [CrossEncoder usage](https://sbert.net/docs/cross_encoder/usage/usage.html)

### Micro-exercises
1. In `d0`, change B to `"Carllm handles claims not quotes"` (identical to A). What do both scorers
   give now? *Hint: identical text, identical scores.*
2. Add a chunk C `"quotes not claims handles Carllm"`. Which scorer separates C from B? *Hint: look at
   the span between the first and last question word.*
3. Make the corpus 10,000 chunks in the cost lines. How many passes does the cross-encoder need per
   question over the whole corpus versus a pool of 20?
4. Name one thing a cross-encoder can see that a bi-encoder cannot. *Hint: it sees the question and the
   chunk in the same input.*
5. Why can a bi-encoder's chunk vectors be stored in Qdrant but a cross-encoder's scores cannot? *Hint:
   the score depends on the question.*

**How it works internally.** A real cross-encoder concatenates question and chunk into one token
sequence, runs a transformer over it so every question token can attend to every chunk token, and reads
a single relevance number off the end. The API hides all of that behind `rerank`.

### How to validate
`tests/retrieval/test_reranker.py::test_the_scores_the_client_returns_decide_the_order` (order follows
the client's scores, not arrival order) and, once a key exists, the live report that prints where an
accepted chunk sits before and after reranking.

### How to visualize
Draw two funnels: a wide one labelled "vector + graph: 223 chunks in, 20 out (cheap, fast)" feeding a
narrow one labelled "reranker: 20 in, 3 out (slow, careful)".

**You should now be able to explain:** why a reranker is a second stage, and what a cross-encoder can
see that a bi-encoder cannot.

---

## C2 — The rerank call: what goes out, what comes back

### Do this first
Run [`d1_rerank_call_shape.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d1_rerank_call_shape.py).
**Predict first:** the reply names documents by *position*, not by text. If the reply says index 2 is
best, which chunk from the input is that, and what does `input_rank` say?

### 1. Definition + analogy
**What it is.** One request carries the model name, the question, the list of **document texts** and
`top_n`. The reply is a list of `(index, relevance_score)` pairs, best first, where `index` is the
position of a document in the list that was sent. `Reranker` sends only `chunk.text`, asks for
`min(k, number of candidates)`, and turns each returned `index` back into the original chunk.

**Analogy.** You hand a teacher 20 answer sheets, each with a card number, and say "give me the three
best". The teacher returns card numbers with marks. You look the numbers up to find which sheets they are.

**For a 14-year-old.** Imagine you put 20 exam papers in a numbered pile and ask a marker for the best
three. The marker does not copy the papers; they just say "number 17 got 0.91, number 3 got 0.40". You
must still remember which paper was number 17 — that is why the code keeps a list, and also keeps each
paper's *original* place in line (`input_rank`), so you can tell how far the marker moved it.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the numbered pile of papers | `documents=[chunk.text for chunk in candidates]` (line 168) |
| "give me the best three" | `top_n=min(k, len(candidates))` (line 169) |
| the marker's "number 17 got 0.91" | `result.index`, `result.relevance_score` |
| looking number 17 up in the pile | `candidates[result.index]` (line 182) |
| remembering the paper's original place | `input_ranks[result.index]` → `RerankedChunk.input_rank` |

### 2. Why here specifically, and what breaks
- **Send `title + text`:** the model would also read titles like `products/Carllm.md`; more context for
  entity questions, but no longer exactly what the generator will read (decision D5).
- **Ask for all N back (`top_n = len`) and cut locally:** the service returns more data for nothing.
- **Trust `result.index` without a range check:** a negative index would silently pick the *last*
  chunk in Python; the guard (line 176) raises instead.
- **Use the API's order without re-sorting:** ties would depend on the service; the local sort
  `(-score, input_rank)` (line 188) makes them deterministic.

[Interview relevance: high. "How do you map rerank results back to your documents?" tests whether you
know results are positional and whether you protect against bad indices.]

**Interview question:** *"What does a rerank API take and return, and how do you use the result?"*
**A strong answer covers:** it takes a query, a list of document strings, and how many to return; it
returns positions and relevance scores (not documents), so you map back by index; scores are only
comparable within one call to one model; keep the original rank for explainability; validate indices;
and cap the number of documents per request.

### 3. Minimal code that explains the concept
The complete script, [`d1_rerank_call_shape.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d1_rerank_call_shape.py) (run from the project root):
```python
# Minimal demo of C2: exactly what Reranker sends, what comes back, and how a result index becomes a chunk.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects with named attributes

sys.path.insert(0, ".")  # lets Python import src.* and tests.* when run from the project root
from src.retrieval.reranker import Reranker  # the real production class
from tests.support import make_chunk  # builds a real DocumentChunk


class ShowingClient:
    def rerank(self, *, model, query, documents, top_n):
        print("  SENT  model:", model)  # shows the model name sent
        print("  SENT  query:", query)  # shows the question sent
        print("  SENT  documents:", documents)  # shows the exact texts sent, in order
        print("  SENT  top_n:", top_n)  # shows how many results were requested
        answer = [  # a canned reply, best first, naming documents by POSITION
            SimpleNamespace(index=2, relevance_score=0.91),  # the third document sent is the best
            SimpleNamespace(index=0, relevance_score=0.40),  # the first document sent is second
        ]
        print("  GOT   results:", [(r.index, r.relevance_score) for r in answer])  # shows the positions and scores returned
        return SimpleNamespace(results=answer[:top_n])  # answers with at most top_n results


pool = [  # three candidates, as the hybrid retriever would hand them over
    make_chunk("company/about.md", 0, "Founded in 2015."),  # input position 1
    make_chunk("products/Carllm.md", 0, "Carllm handles auto claims."),  # input position 2
    make_chunk("company/careers.md", 0, "We are hiring engineers."),  # input position 3
]
print("rerank_with_scores('What does Carllm do?', pool, k=2):")  # announces the call
for item in Reranker(ShowingClient()).rerank_with_scores("What does Carllm do?", pool, 2):
    print(f"  RESULT {item.chunk.chunk_id:<24} score={item.relevance_score}  input_rank={item.input_rank}")  # prints each returned chunk
print("index 2 in the reply became the chunk that was 3rd in the input; input_rank remembers that 3")  # explains the mapping
```
Real output:
```
rerank_with_scores('What does Carllm do?', pool, k=2):
  SENT  model: rerank-v3.5
  SENT  query: What does Carllm do?
  SENT  documents: ['Founded in 2015.', 'Carllm handles auto claims.', 'We are hiring engineers.']
  SENT  top_n: 2
  GOT   results: [(2, 0.91), (0, 0.4)]
  RESULT company/careers.md_0     score=0.91  input_rank=3
  RESULT company/about.md_0       score=0.4  input_rank=1
index 2 in the reply became the chunk that was 3rd in the input; input_rank remembers that 3
```

Note that `company/careers.md_0`, which was **third** in the input, came back first: the reply said
`index 2`, and `input_rank=3` records where it started.

### The exact code I wrote (`src/retrieval/reranker.py`)
```python
# src/retrieval/reranker.py, lines 151-190, verbatim
151 |     def rerank_with_scores(
152 |         self, query: str, chunks: Sequence[DocumentChunk], k: int
153 |     ) -> list[RerankedChunk]:
154 |         """The k best results, each with its relevance score and its position before reranking."""
155 |         _check_k(k)  # 🎯 validate k before any request
156 |         # refuses a k that is not a positive integer before any request is sent
157 |         _check_text("query", query)  # 🎯 validate the question before any request
158 |         # refuses an empty question before any request is sent
159 |         candidates, input_ranks = _unique_chunks(chunks)  # 🎯 drop repeats (C5)
160 |         # drops repeats so a chunk cannot appear twice in the result
161 |         if not candidates:  # 🎯 nothing to score: no request (C5)
162 |             # covers an empty candidate list, where there is nothing to score
163 |             return []
164 |             # answers without spending a request
165 |         response = self._client.rerank(
166 |             model=self._model,
167 |             query=query,
168 |             documents=[chunk.text for chunk in candidates],  # 🎯 ONLY the chunk texts are sent, in input order
169 |             top_n=min(k, len(candidates)),  # 🎯 ask for no more than exist
170 |         )
171 |         # asks the model to score every candidate's text against the query and return the best few
172 |         ranked: list[RerankedChunk] = []
173 |         # collects one RerankedChunk per result the model returned
174 |         for result in response.results:
175 |             # walks the model's results, which name candidates by their position
176 |             if not 0 <= result.index < len(candidates):  # 🎯 a bad index must not silently pick a chunk
177 |                 # catches a position outside the list that was sent
178 |                 raise ValueError(f"reranker returned an unknown document index {result.index}")
179 |                 # refuses it, because a negative index would silently pick the wrong chunk
180 |             ranked.append(
181 |                 RerankedChunk(
182 |                     chunk=candidates[result.index],  # 🎯 index -> the chunk that was sent at that position
183 |                     relevance_score=result.relevance_score,
184 |                     input_rank=input_ranks[result.index],  # 🎯 where that chunk started
185 |                 )
186 |             )
187 |             # pairs the model's score with the chunk it belongs to and where that chunk came in
188 |         ranked.sort(key=lambda item: (-item.relevance_score, item.input_rank))  # 🎯 score first, original position breaks ties
189 |         # orders by score, then by original position so equal scores are deterministic
190 |         return ranked[:k]  # 🎯 cut to k
```

**🎯 Interview-relevant lines:** 165-170 is the entire model call; 174-187 is the whole "map results back"
step; 188 is why equal scores never come out in a random order.

### Canonical docs
- Cohere, [Rerank overview](https://docs.cohere.com/docs/rerank) and [best practices](https://docs.cohere.com/docs/reranking-best-practices)
- Cohere Python SDK 7.0.8: `ClientV2.rerank(*, model, query, documents, top_n, ...)`

### Micro-exercises
1. In `d1`, change the canned reply to `index=1` first. Which chunk is returned first and what is its
   `input_rank`? *Hint: index 1 is the second document sent.*
2. Change `k` to 3 and the canned reply to only two results. How many chunks come back? *Hint: the
   code returns what the service returned.*
3. Make the reply repeat `index=0` twice. What does `Reranker` return? *Hint: nothing de-duplicates
   the reply, only the input; think about whether that is a gap.*
4. Send a pool where two chunks have the same text. Does the model see two documents? *Hint: they
   are different chunk ids, so both are sent.*
5. Why is `input_rank` 1-based and computed from the list *as given*, not from the de-duplicated one?
   *Hint: see the duplicate test in C5.*

**How it works internally.** `candidates` and `input_ranks` are two parallel lists built by
`_unique_chunks`. The service only ever sees `documents`; everything else (chunk objects, ids, original
rank) stays on our side and is re-attached by position.

### How to validate
`test_the_query_the_model_and_only_the_chunk_texts_are_sent_in_input_order`,
`test_scores_and_input_positions_are_recorded`, and the real-SDK test
`test_the_real_sdk_sends_the_expected_request_and_its_response_is_parsed`, which checks the endpoint,
the bearer header, and the exact JSON body.

### How to visualize
Draw the input list with numbered boxes, the reply as arrows from `index` values back to those boxes,
and the final list rebuilt from the arrows.

**You should now be able to explain:** what is sent, what comes back, and how a positional reply is
turned back into chunks.

---

## C3 — The candidate pool is the ceiling

### Do this first
Run [`d2_pool_recall_ceiling.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d2_pool_recall_ceiling.py).
**Predict first:** at a pool of 20, is the chunk that actually *states* the CEO's title in reach of a
perfect reranker?

### 1. Definition + analogy
**What it is.** A reranker only reorders the candidates it receives. If the best chunk is not among
them, no reranker can find it. So the **recall of stage 1 at pool size N** is the ceiling on what stage
2 can achieve, and N is a real tuning parameter: too small loses answers, too large costs more and adds
noise.

**Analogy.** A coach picks a team from a tryout shortlist. However good the coach, the star who was
never invited to try out cannot be picked.

**For a 14-year-old.** Your school only lets 20 kids audition for the play. The director is great at
choosing the best three, but if the best actor wasn't on the list of 20, the director can't choose them.
Making the list longer helps (you invite 50), but then the director spends much longer watching
auditions.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the shortlist of 20 | `hybrid.retrieve(question, POOL_SIZE)` with `POOL_SIZE = 20` |
| the director choosing three | `Reranker.rerank(question, pool, 3)` |
| the best actor not invited | `employees/Avery Lancaster.md_0` is not in a pool of 20 |
| "invite 50 instead" | `POOL_SIZE = 50`: the direct answer appears, at hybrid rank 17 |
| "the director takes much longer" | more chunk texts sent per call, more latency and cost |

### 2. Why here specifically, and what breaks
Measured on the real corpus, no Cohere involved (the rank of the first accepted chunk in the hybrid's
list of N): Carllm is absent at N=3 and at rank 3 from N=10 on; the CEO label's chunk sits at 2, 3, 15,
21, 9 for N = 3, 10, 20, 30, 50 (it moves because a deeper pool lets more chunks that both retrievers
agree on outrank it); and the chunk that *states* the title enters only at N=50, rank 17.
- **`N = k`:** the reranker becomes a permutation of an already-final list.
- **`N` too small (20) for the CEO question:** the direct answer is out of reach; the accepted "hit" is
  an indirect chunk that names the founder.
- **`N` too large:** more text sent per call and a noisier pool for the model to sort.
- **Judging by the label alone:** the CEO label accepts `company/about.md_0`, whose text says the
  company "was founded by Avery Lancaster in 2015". A hit on it means less than a hit on the others.

[Interview relevance: very high. "How many documents do you rerank, and how did you choose?" is one
of the most common RAG design questions.]

**Interview question:** *"How would you choose how many candidates to send to the reranker?"*
**A strong answer covers:** measure stage-1 recall@N on labelled questions (the reranker's ceiling);
plot recall against latency and cost; note diminishing returns; typical ranges are tens to a few hundred
depending on cost; make it configurable; and remember that changing N changes which candidates exist,
not just how many.

### 3. Minimal code that explains the concept
The complete script, [`d2_pool_recall_ceiling.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d2_pool_recall_ceiling.py) (run from the project root):
```python
# Minimal demo of C3: a reranker can only reorder the pool it is given, so pool recall is its ceiling.
# The ranks below were MEASURED on the real corpus (hybrid retriever, no Cohere): the rank of the first
# chunk in the hybrid list of N that the project labels accept. None means it is not in the list.
MEASURED = {  # label -> {pool size N: rank of the first accepted chunk in the hybrid list of N}
    "Carllm product page": {3: None, 10: 3, 20: 3, 30: 3, 50: 3},  # the page appears once N reaches 10
    "CEO (label: About page, names the founder)": {3: 2, 10: 3, 20: 15, 30: 21, 50: 9},  # position moves with N
    "CEO (direct: Avery Lancaster's own record)": {3: None, 10: None, 20: None, 30: None, 50: 17},  # only appears at N=50
}
FINAL_K = 3  # how many chunks the reranker keeps

print(f"{'answer':<46} N=3   N=10  N=20  N=30  N=50")  # column headings
for label, by_pool in MEASURED.items():  # goes through each labelled answer
    cells = []  # will hold one cell per pool size
    for n, rank in by_pool.items():  # goes through each pool size
        in_pool = rank is not None  # checks whether the answer is inside the pool at all
        as_is = in_pool and rank <= FINAL_K  # checks whether it is already in the top 3 without any reranker
        cells.append("top3" if as_is else ("in-pool" if in_pool else "LOST"))  # top3 = fine already, in-pool = a reranker could lift it, LOST = out of reach
    print(f"{label:<46} " + "  ".join(f"{c:<7}" for c in cells))  # prints the row
print("top3 = already in the top 3 | in-pool = a perfect reranker could bring it to the top 3 | LOST = no reranker can")  # legend
```
Real output:
```
answer                                         N=3   N=10  N=20  N=30  N=50
Carllm product page                            LOST     top3     top3     top3     top3   
CEO (label: About page, names the founder)     top3     top3     in-pool  in-pool  in-pool
CEO (direct: Avery Lancaster's own record)     LOST     LOST     LOST     LOST     in-pool
top3 = already in the top 3 | in-pool = a perfect reranker could bring it to the top 3 | LOST = no reranker can
```

The `MEASURED` table is real data from this project's hybrid retriever, not invented. `LOST` cells are
where a perfect reranker still fails.

### The exact code I wrote
The reranker takes whatever list it is given and never asks for more. The pool is produced by the hybrid
(`src/retrieval/hybrid_retriever.py`, from P5-M2), where depth is `3 * k`:
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
227 |         depth = k * self._candidate_multiplier  # 🎯 pool depth is 3 times the k that was asked for
228 |         # works out how many candidates each retriever is asked for
229 |         vector_hits = self._vector_store.search_with_scores(query, depth)  # 🎯 vector candidates
230 |         # asks vector search for its top candidates, with cosine scores
231 |         graph_hits = self._graph_retriever.retrieve(query, depth)  # 🎯 graph candidates
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
242 |         return fused[:k]  # 🎯 cut to k after fusing
243 |         # keeps only the k best fused results
```

and the reranker's own entry point accepts the list as-is:
```python
# src/retrieval/reranker.py, lines 146-149, verbatim
146 |     def rerank(self, query: str, chunks: Sequence[DocumentChunk], k: int) -> list[DocumentChunk]:  # 🎯 the pool comes in as `chunks`
147 |         """The k chunks that best answer `query`, best first."""
148 |         return [item.chunk for item in self.rerank_with_scores(query, chunks, k)]
149 |         # drops the scores and positions, leaving only the chunks in reranked order
```

**🎯 Interview-relevant lines:** the reranker has no pool-size parameter at all (decision D4): the caller
asks the hybrid for N and passes the result in. That keeps one job per class, at the cost of no ready-made
"retrieve N then rerank" call yet.

### Canonical docs
- Cohere, [Reranking best practices](https://docs.cohere.com/docs/reranking-best-practices)
- Weaviate, [Hybrid search explained](https://weaviate.io/blog/hybrid-search-explained) (why stage 1 uses fusion)

### Micro-exercises
1. In `d2`, add a fourth answer whose ranks you invent, `{3: None, 10: None, 20: 12, 30: 12, 50: 12}`.
   Which cells are `LOST`, and at what N would a reranker first matter?
2. Which pool size would you pick for these three rows if each extra chunk costs money? *Hint: the
   smallest N where every row is `top3` or `in-pool`.*
3. Why does the CEO label's rank go 2, 3, 15, 21, 9 instead of steadily worsening? *Hint: the pool's
   composition changes with N, see P5-M2 depth findings.*
4. Run `hybrid.retrieve("Who is the CEO of Insurellm?", 50)` and find `employees/Avery Lancaster.md_0`.
   What rank is it, and what does that say about using N=50?
5. Suggest one way to get the direct CEO chunk into a small pool without raising N. *Hint: the labels,
   the entity linking, or a different fusion weight; there is no single right answer.*

**How it works internally.** The hybrid asks each retriever for `3 * N` candidates, fuses, and cuts to
`N`; the reranker then scores those N. Two different depths are stacked, so the reranker's pool is itself
a product of P5-M2's depth rule.

### How to validate
The live test `test_live_every_labelled_answer_is_inside_the_pool_the_reranker_receives` needs no Cohere
key: it asserts an accepted chunk is inside the 20-candidate pool for all five questions. (It passes on
the real corpus today.)

### How to visualize
Draw a number line of hybrid ranks 1 to 50, mark the accepted chunk's position for each N, and shade the
first 20 as "the pool". Anything outside the shade is out of reach.

**You should now be able to explain:** why a reranker's ceiling is set before it runs, and why the pool
size is a parameter to measure, not a constant to guess.

---

## C4 — A client that fails fast: no hidden retries

### Do this first
Run [`d3_fail_fast_client.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d3_fail_fast_client.py).
**Predict first:** the service answers "429 too many requests" once. How many HTTP requests does the
stock Cohere SDK send? How many does `build_cohere_client` send?

### 1. Definition + analogy
**What it is.** Many SDKs retry failed requests on their own. The Cohere SDK, by default, retries
`408`, `409`, `429` and every `5xx` twice with exponential backoff and, with no custom HTTP client, waits
up to 300 seconds per request. That is a hidden policy: the caller never learns a retry happened.
`build_cohere_client` turns retries off (`max_retries=0`) and sets a 30-second timeout, so an error
surfaces immediately and nothing is swallowed (decision D6).

**Analogy.** You ask a shop assistant to check whether an item is in stock. They are told "the system is
busy, try later" and quietly wait and ask twice more before answering you, without saying so. You
wanted to know right away so you could decide.

**For a 14-year-old.** You text a friend "are you free?" and your phone silently re-sends the same text
twice if they do not answer, and waits five minutes before telling you it failed. You would rather see
"no reply" at once and decide what to do. Turning off silent re-sending is `max_retries=0`.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| the phone silently re-sending | the SDK's default `base_max_retries = 2` |
| "the system is busy" | HTTP 429 (Cohere trial keys: 10 rerank requests per minute) |
| waiting five minutes | the SDK's default 300 s timeout |
| "I'd rather know at once" | `max_retries=0` and `timeout=REQUEST_TIMEOUT_SECONDS` (30 s) |
| counting how many texts were sent | a stubbed HTTP layer that counts requests |

### 2. Why here specifically, and what breaks
- **Leave the SDK defaults:** "errors propagate" would be untrue; a rate-limited call pauses and
  re-sends invisibly, and a stuck request can hold a thread for five minutes.
- **`max_retries=2` deliberately:** friendlier to bursts on a trial key, but the caller cannot see
  the retries or the added latency (decision D6c, a one-argument flip).
- **A fake client in the tests instead of the real SDK:** the fake would never notice the default
  retries. Only the real SDK over a stubbed transport did.

[Interview relevance: high. Retries, timeouts and rate limits for third-party APIs come up in almost
every production-readiness discussion.]

**Interview question:** *"How do you handle rate limits from a hosted model API?"*
**A strong answer covers:** know the provider's limits and what your SDK does by default; retry only
idempotent calls, with exponential backoff and jitter, honouring `Retry-After`; cap retries and set
timeouts; make the policy visible (logs or metrics) rather than hidden in a library; decide explicitly
between failing, degrading, or queueing; and test the policy against a stubbed transport.

### 3. Minimal code that explains the concept
The complete script, [`d3_fail_fast_client.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d3_fail_fast_client.py) (run from the project root):
```python
# Minimal demo of C4: the Cohere SDK silently retries a 429 by default; build_cohere_client turns that off.
# A stubbed HTTP layer counts the requests. Nothing here touches the network.
import sys  # gives access to the module search path
import time  # measures how long each call takes

import cohere  # the real Cohere SDK
import httpx  # the HTTP library the SDK uses

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.reranker import build_cohere_client  # the real factory


def counting_client(factory):
    requests = []  # will hold one entry per HTTP request the SDK sends

    def handler(request):
        requests.append(request)  # counts this request
        return httpx.Response(429, json={"message": "too many requests"})  # answers that the caller is rate limited

    http = httpx.Client(transport=httpx.MockTransport(handler))  # an HTTP layer answered by the handler, not the network
    return factory(http), requests  # builds the client on that layer and hands back the counter


def try_rerank(label, factory):
    client, requests = counting_client(factory)  # builds a client and its request counter
    started = time.perf_counter()  # notes the start time
    try:
        client.rerank(model="rerank-v3.5", query="q", documents=["a document"], top_n=1)  # makes one rerank call
    except Exception as error:  # catches whatever the SDK raises
        name = type(error).__name__  # names the error
    seconds = time.perf_counter() - started  # measures the elapsed time
    print(f"{label:<28} requests sent: {len(requests)}   error: {name}   took {seconds:.1f}s")  # prints the outcome


try_rerank("SDK defaults", lambda http: cohere.ClientV2("test-key", httpx_client=http))  # a plain client, exactly as the SDK ships
try_rerank("build_cohere_client (ours)", lambda http: build_cohere_client("test-key", httpx_client=http))  # the project factory
print("one 429 became 3 requests under the defaults and exactly 1 under ours")  # the lesson
```
Real output:
```
SDK defaults                 requests sent: 3   error: TooManyRequestsError   took 3.1s
build_cohere_client (ours)   requests sent: 1   error: TooManyRequestsError   took 0.0s
one 429 became 3 requests under the defaults and exactly 1 under ours
```

The 3.1 seconds are the SDK sleeping between its silent retries.

### The exact code I wrote (`src/retrieval/reranker.py`)
```python
# src/retrieval/reranker.py, lines 69-85, verbatim
 69 | def build_cohere_client(
 70 |     api_key: str, *, httpx_client: httpx.Client | None = None
 71 | ) -> cohere.ClientV2:
 72 |     """A Cohere client that fails fast: no hidden retries and a short timeout."""  # 🎯 fail fast, no hidden retries
 73 |     if not api_key.strip():  # 🎯 a blank key fails here, naming the fix
 74 |         # covers a key that was never filled in, so the failure names the fix
 75 |         raise ValueError(
 76 |             "COHERE_API_KEY is empty: create a key at dashboard.cohere.com and add it to .env"
 77 |         )
 78 |         # names the setting to fill in rather than failing later with an opaque 401
 79 |     return cohere.ClientV2(
 80 |         api_key,
 81 |         timeout=REQUEST_TIMEOUT_SECONDS,  # 🎯 30 s, not the SDK's 300 s
 82 |         max_retries=0,  # 🎯 the SDK's own retry-on-429/5xx is OFF
 83 |         httpx_client=httpx_client,
 84 |     )
 85 |     # builds the client with the SDK's own retry-on-429/5xx turned off, so errors surface at once
```

and the test that keeps it true (`tests/retrieval/test_reranker.py`):
```python
# tests/retrieval/test_reranker.py, lines 395-419, verbatim
395 | @pytest.mark.parametrize(
396 |     ("status", "error"),
397 |     [
398 |         (429, TooManyRequestsError),
399 |         (500, InternalServerError),
400 |         (401, UnauthorizedError),
401 |     ],
402 | )
403 | # runs once per failure status
404 | def test_the_real_sdk_raises_after_exactly_one_request_with_no_hidden_retry(status, error):  # 🎯 runs once each for 429, 500 and 401
405 |     count = []
406 |     # counts every request the SDK sends
407 | 
408 |     def handler(request):
409 |         count.append(request)
410 |         # counts this request
411 |         return httpx.Response(status, json={"message": "nope"})
412 |         # answers with the failure status
413 | 
414 |     with pytest.raises(error):
415 |         # expects the SDK's own error for that status
416 |         sdk_reranker(handler).rerank("q", chunks_named("A"), 1)
417 |         # reranks one chunk
418 |     assert len(count) == 1  # 🎯 one request, not three
419 |     # requires a single request: the SDK's default of two silent retries is switched off
```

**🎯 Interview-relevant lines:** `max_retries=0` (line 82) is one argument, but it is the difference
between "errors propagate" being true or false. The test on the right counts requests through the real
SDK so nobody can undo it silently.

### Canonical docs
- Cohere, [API keys and rate limits](https://docs.cohere.com/docs/rate-limits) (trial: 10 rerank req/min, 1,000 calls/month)
- Cohere Python SDK 7.0.8 source, `cohere/core/http_client.py` (`_should_retry`, `base_max_retries = 2`)
- httpx, [MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)

### Micro-exercises
1. In `d3`, add a third client with `max_retries=1`. How many requests and how long? *Hint: 2 requests.*
2. Make the handler return 401 instead of 429. How many requests do the defaults send? *Hint: 401 is not
   in the SDK's retry list.*
3. Make the handler return 500. Do the defaults retry? Does ours?
4. Set `REQUEST_TIMEOUT_SECONDS` to 0.001 in a scratch copy and point a real client at a slow stub. What
   error do you get, and does it retry?
5. Where would you log a retry if you wanted the visible-retry policy (D6c)? *Hint: around the client
   call in `rerank_with_scores`, not inside the SDK.*

**How it works internally.** The SDK's HTTP wrapper loops while `retries < max_retries` and sleeps a
backoff (or the `Retry-After` value) between attempts. Setting `max_retries=0` makes the loop exit after
the first response, so the error is raised at once.

### How to validate
`test_the_real_sdk_raises_after_exactly_one_request_with_no_hidden_retry` (429, 500 and 401), and the
mutation check: switching `max_retries` back to 2 makes that test fail.

### How to visualize
A timeline with request arrows: the defaults show three arrows with waits between them; ours shows one
arrow and an immediate error.

**You should now be able to explain:** what a library's hidden retry policy is, why it can contradict a
design decision, and how a stubbed transport proves the real behaviour.

---

## C5 — The small rules: validate, de-duplicate, short-circuit, inject

### Do this first
Run [`d4_edge_rules.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d4_edge_rules.py).
**Predict first:** for each of the six calls, is a request sent, and what is in `documents`?

### 1. Definition + analogy
**What it is.** Five small rules around the model call: reject a bad `k` or a blank question *before*
spending a request; return `[]` for an empty list without a request; send each distinct chunk once; cap
`top_n` at the list length; refuse an impossible index in the reply. And the client is **injected**, so
tests can swap it.

**Analogy.** A kitchen checks the order slip before turning on the oven, skips an empty order, does not
cook the same dish twice, and refuses to plate a dish number that is not on the menu.

**For a 14-year-old.** Before you pay a taxi to drive you, you check you have an address (validation),
you do not book a taxi if nobody is going anywhere (short-circuit), and you do not write the same friend's
name twice on the guest list (de-duplication). And if someone tells you "seat number -1", you say that
seat does not exist instead of putting a guest in the last chair by accident.

**Mapping the story back to the code:**
| In the story | In the real code |
|---|---|
| checking you have an address | `_check_k`, `_check_text("query", query)` (lines 155-158) |
| not booking a taxi for nobody | `if not candidates: return []` (lines 161-164) |
| one name once on the guest list | `_unique_chunks` (lines 102-122) |
| "seat -1 does not exist" | the index guard (line 176) |
| "any taxi company will do" | the `RerankClient` Protocol and `Reranker(client)` |

### 2. Why here specifically, and what breaks
- **Validate after the call:** a blank question would cost a real request and return an API error.
- **No empty shortcut:** an empty document list would be sent to the API and fail there.
- **No de-duplication:** one chunk could occupy two of the k slots and the model would score it twice.
- **No index guard:** a `-1` in the reply would silently return the last chunk.
- **Build the client inside the class:** every test would need the network or monkeypatching.
All five were checked by breaking the code on purpose: each mutation was caught by a test.

[Interview relevance: medium. Not RAG-specific, but "how did you test it without calling the API?" is a
common follow-up.]

**Interview question:** *"How would you test a wrapper around a paid third-party API?"*
**A strong answer covers:** inject the client and test logic with a fake; test the wire format with the
real SDK over a stubbed transport built from the SDK's own types; keep a few live smoke tests behind a
marker that skips without a key; watch quotas and rate limits; and check the tests themselves by breaking
the code and confirming they fail.

### 3. Minimal code that explains the concept
The complete script, [`d4_edge_rules.py`](../practice%20codes/phase%205/m3_reranker_deep_dive/d4_edge_rules.py) (run from the project root):
```python
# Minimal demo of C5: the small rules around the model call, using the real Reranker and a recording client.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects with named attributes

sys.path.insert(0, ".")  # lets Python import src.* and tests.* when run from the project root
from src.retrieval.reranker import Reranker  # the real production class
from tests.support import make_chunk  # builds a real DocumentChunk

calls = []  # records every request the recording client receives


class RecordingClient:
    def __init__(self, order=None):
        self.order = order  # an optional fixed answer, as document positions

    def rerank(self, *, model, query, documents, top_n):
        calls.append({"documents": list(documents), "top_n": top_n})  # records what was sent
        positions = self.order if self.order is not None else list(range(len(documents)))  # chooses which positions to answer with
        return SimpleNamespace(results=[SimpleNamespace(index=i, relevance_score=1.0 - 0.1 * i) for i in positions][:top_n])  # answers best first, at most top_n


def chunk(name):
    return make_chunk(name, 0, f"text of {name}")  # a tiny chunk whose text is "text of <name>"


def show(label, action):
    calls.clear()  # forgets earlier requests
    try:
        result = action()  # runs the call
        outcome = [getattr(r, "chunk", r).chunk_id for r in result]  # lists the chunk ids returned
    except ValueError as error:  # catches the validation errors
        outcome = f"ValueError: {error}"  # records the message
    print(f"{label:<34} -> {outcome}   requests: {len(calls)}   sent: {calls[0] if calls else '-'}")  # prints result and what was sent


reranker = Reranker(RecordingClient())  # a reranker over the recording client
A, B, C = chunk("A"), chunk("B"), chunk("C")  # three different chunks
show("empty candidate list", lambda: reranker.rerank("q", [], 3))  # nothing to score: no request
show("duplicate A in the input", lambda: reranker.rerank_with_scores("q", [A, B, A, C], 3))  # A must be sent and returned once
show("k larger than the list", lambda: reranker.rerank("q", [A, B], 10))  # top_n is capped at the list length
show("k = 0", lambda: reranker.rerank("q", [A], 0))  # invalid k: refused before any request
show("blank question", lambda: reranker.rerank("  ", [A], 1))  # invalid question: refused before any request
show("impossible index from the API", lambda: Reranker(RecordingClient(order=[-1])).rerank("q", [A, B], 1))  # a negative index must not silently pick a chunk
```
Real output:
```
empty candidate list               -> []   requests: 0   sent: -
duplicate A in the input           -> ['A_0', 'B_0', 'C_0']   requests: 1   sent: {'documents': ['text of A', 'text of B', 'text of C'], 'top_n': 3}
k larger than the list             -> ['A_0', 'B_0']   requests: 1   sent: {'documents': ['text of A', 'text of B'], 'top_n': 2}
k = 0                              -> ValueError: k must be a positive integer, got 0   requests: 0   sent: -
blank question                     -> ValueError: query must be a non-empty string, got '  '   requests: 0   sent: -
impossible index from the API      -> ValueError: reranker returned an unknown document index -1   requests: 1   sent: {'documents': ['text of A', 'text of B'], 'top_n': 1}
```

Look at the second line: the input was `[A, B, A, C]`, the request carried three documents, and A came
back once.

### The exact code I wrote (`src/retrieval/reranker.py`)
```python
# src/retrieval/reranker.py, lines 88-122, verbatim
 88 | def _check_k(k: int) -> None:
 89 |     if isinstance(k, bool) or not isinstance(k, int) or k < 1:  # 🎯 k must be a plain positive integer (bool is rejected too)
 90 |         # rejects anything that is not a plain positive integer
 91 |         raise ValueError(f"k must be a positive integer, got {k!r}")
 92 |         # names the argument and the value so the caller can see what was wrong
 93 | 
 94 | 
 95 | def _check_text(name: str, value: str) -> None:
 96 |     if not isinstance(value, str) or not value.strip():  # 🎯 the question must be a non-empty string
 97 |         # rejects a value that is not a string, or is empty or only whitespace
 98 |         raise ValueError(f"{name} must be a non-empty string, got {value!r}")
 99 |         # names the argument and the value so the caller can see what was wrong
100 | 
101 | 
102 | def _unique_chunks(chunks: Sequence[DocumentChunk]) -> tuple[list[DocumentChunk], list[int]]:
103 |     unique: list[DocumentChunk] = []
104 |     # keeps each distinct chunk, in the order it first appeared
105 |     input_ranks: list[int] = []
106 |     # keeps each kept chunk's 1-based position in the list the caller passed in
107 |     seen: set[str] = set()
108 |     # keeps the chunk ids already kept
109 |     for rank, chunk in enumerate(chunks, start=1):
110 |         # walks the caller's list in order, numbering positions from 1
111 |         if chunk.chunk_id in seen:  # 🎯 a repeat of an already-seen chunk id
112 |             # spots a chunk that already appeared earlier in the list
113 |             continue  # 🎯 is skipped, so one chunk cannot take two places
114 |             # skips it so one chunk cannot take two places in the result
115 |         seen.add(chunk.chunk_id)
116 |         # remembers this chunk id
117 |         unique.append(chunk)
118 |         # keeps the chunk
119 |         input_ranks.append(rank)  # 🎯 but its FIRST position is remembered
120 |         # keeps the position it came in at
121 |     return unique, input_ranks
122 |     # hands back the distinct chunks and their original positions
```

```python
# src/retrieval/reranker.py, lines 136-144, verbatim
136 |     @classmethod
137 |     def from_settings(
138 |         cls, config: Settings | None = None, *, model: str = DEFAULT_MODEL
139 |     ) -> Reranker:
140 |         """A Reranker using the Cohere key from `.env`."""
141 |         config = config or settings  # 🎯 falls back to the app-wide settings
142 |         # falls back to the app-wide settings when none are passed
143 |         return cls(build_cohere_client(config.cohere_api_key), model=model)  # 🎯 builds the real client from .env
144 |         # builds the client from the configured key and wraps it in a Reranker
```

**🎯 Interview-relevant lines:** `_unique_chunks` keeps two parallel lists so `input_rank` reports the
position in the list *as given*, counting the repeat: for `[A, B, A, C]`, C reports `input_rank = 4`.

### Canonical docs
- Python docs, [`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
- httpx, [MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)

### Micro-exercises
1. In `d4`, add a call with the pool `[A, A, A]`. What is sent and returned? *Hint: one document.*
2. Pass `True` as `k`. What happens and why? *Hint: `bool` is a subclass of `int`.*
3. Make the recording client answer `order=[5]` with two documents. Which error and message do you get?
4. Remove the `seen` check in a scratch copy and run the tests. Which test names the problem?
5. Why is the empty-list check placed after `_check_k`? *Hint: an invalid `k` should fail even with
   no candidates; see `test_k_is_checked_even_when_there_are_no_candidates`.*

**How it works internally.** Everything before line 165 is pure Python on local data; the single line
that costs money is the `self._client.rerank(...)` call, and every rule exists to make sure it is only
made when it will be useful.

### How to validate
`test_an_empty_candidate_list_returns_nothing_and_sends_no_request`,
`test_a_chunk_repeated_in_the_input_is_scored_once_at_its_first_position`,
`test_a_bad_k_is_rejected_before_any_request`, and `test_an_impossible_document_index_from_the_client_is_refused`.

### How to visualize
A vertical pipeline with gates before the paid call: "valid k?", "valid question?", "any candidates?",
"de-duplicate", then the API box, then "valid indices?".

**You should now be able to explain:** why each guard sits where it does, and what each one prevents.

---

## Consolidated end-to-end exercise: watch it work

1. **Predict.** For the five labelled questions and a pool of 20, write down which you expect the
   reranker to move and which to leave alone. Check `d2` and the pool table first: which are already
   in the top 3, and which are out of reach?
2. **Run the pool check** (needs Docker services, no Cohere key):
   ```bash
   .venv/Scripts/python -m pytest tests/retrieval/test_reranker.py -m live -p no:cacheprovider -q
   ```
   One live test passes; four skip with "COHERE_API_KEY is empty".
3. **Add your key** to `.env` as `COHERE_API_KEY=...` (you create it at dashboard.cohere.com; nothing
   in this repo can). Then re-run with `-s` to print the table. It sends the five questions and their
   20 candidate chunk texts each to Cohere.
4. **Read the table**: "pool rank", "hybrid k=3" (what the system returns today) and "reranked". For
   each question say whether the reranker helped, hurt, or did nothing, and whether five questions can
   support that conclusion (C3).
5. **Break it on purpose:** in a scratch copy set `max_retries=2`, run the C4 test, and watch it fail;
   then remove the `seen` check and watch the C5 test fail.

If your predictions in steps 1 and 4 matched what you saw, you understand this stage and its limits, not
just that it runs.
