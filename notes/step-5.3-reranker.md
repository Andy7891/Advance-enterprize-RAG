# Phase 5, Microstep 3 — Reranker (Cohere Rerank)

**Date:** 2026-09-25
**Core learning: Yes — ranking strategies (two-stage retrieval).** Retrieval finds candidates;
a reranker decides their final order by reading the question and each chunk together.

> **Status: built and tested, live measurement pending.** No Cohere key has been added to `.env`
> yet (`COHERE_API_KEY=` is empty), so the reranker has never been run against the real API. Every
> claim below about Cohere's behaviour comes from its documentation or from the SDK's own code, and
> every claim about results comes from tests with a fake client or a stubbed network. The first real
> run is one command (see "What is still open").

## The gap this step closes
P5-M2 fuses two ranked lists using positions only. It never reads the question and a chunk together,
so nothing checks whether a chunk actually states the answer. For "Who is the CEO of Insurellm?" the
hybrid's 20 best candidates are mostly other employees' HR records, and the chunk that states the
answer, `employees/Avery Lancaster.md_0` ("Job Title: Co-Founder & Chief Executive Officer"), is not
among them at all. A reranker is a **cross-encoder**: one model reads the question and one chunk as a
single input and returns a relevance score. That is more precise than comparing two independently
computed vectors, but costs one model pass per candidate per question, so it is used only on a short
list.

```
question ─► hybrid retriever (recall: cast a wide net, N candidates) ─► Reranker (precision: reorder) ─► top k
```

## What was built
- **[`src/retrieval/reranker.py`](../src/retrieval/reranker.py)**
  - `Reranker(client, *, model="rerank-v3.5")`
  - `rerank(query, chunks, k) -> list[DocumentChunk]`: the required signature.
  - `rerank_with_scores(query, chunks, k) -> list[RerankedChunk]`: each result also carries
    `relevance_score` and `input_rank` (its 1-based position before reranking).
  - `Reranker.from_settings()` and `build_cohere_client(api_key)`: build the real client from
    `.env`, with the SDK's hidden retries switched off and a 30 s timeout.
- **[`tests/retrieval/test_reranker.py`](../tests/retrieval/test_reranker.py)**, 40 tests:
  35 hermetic (a fake client, plus the REAL Cohere SDK over `httpx.MockTransport`) and 5 live.
- No existing file changed. `Settings.cohere_api_key` and `cohere==7.0.8` were already in place from
  Step 1.1.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Provider | Cohere Rerank API | local BGE cross-encoder; both behind one interface | needs a key only you can create, network, trial limits (10 rerank req/min, 1,000 calls/month), chunk text leaves the machine |
| D2 Model | `rerank-v3.5` | `rerank-v4.0-pro` / `-fast` | an older generation; v4's context length is not in the docs table I read |
| D3 Return type | `list[DocumentChunk]` + `rerank_with_scores` | plain only; scored objects only | two public methods to keep consistent |
| D4 Pool size | the caller chooses (20 in tests) | reranker fetches `multiplier × k`; a composite retriever now | no ready pipeline yet; the pool's content depends on N (see findings) |
| D5 Text sent | `chunk.text` only | `title + text`; truncation | a chunk that never names its subject loses the context its title carries |
| D6 Errors | propagate; SDK retries off | fall back to hybrid order; retry with backoff | an outage or a burst over 10 req/min fails the whole request |
| D7 Edge cases | empty → `[]`, no request; no relevance cut-off | absolute/relative threshold | junk still reaches the prompt when the pool holds no good answer |
| D8 Shape | new module, client injected, `from_settings` factory | client built inside; backend interface | a Protocol is not enforced at run time |
| D9 Acceptance | hermetic + few live + before/after report on the 5 questions | 20-30 labelled questions with hit@k/MRR; hermetic only | five questions show at most two movable cases and cannot support a general claim |

## Verified, not assumed
```
tests (this step)    : 40 = 35 hermetic + 5 live (1 needs no Cohere key and passes; 4 skip until a key exists)
mutation check       : 10 deliberate breakages of reranker.py, each caught by a test, none survived
whole suite, live    : 630 passed, 4 skipped, 3 xfailed  (services up, Cohere key empty; ~87 s)
CI mode              : 558 passed, 79 deselected  (-m "not tiktoken and not live")
ruff, format, mypy   : clean
```
The 10 breakages: SDK retries back on, empty-list shortcut removed, de-duplication removed, index
range check removed, input-position tie-break removed, title sent with the text, `top_n` not capped
at the list length, `k` check removed, cut to `k` removed, query check removed.

**What the real-SDK tests prove.** They run the installed `cohere.ClientV2` against a stubbed HTTP
layer, with the response built from the SDK's own `V2RerankResponse` type. That confirms, without any
network: the request goes to `/v2/rerank` with `Authorization: Bearer <key>`; the body holds exactly
`model`, `query`, `documents` (the chunk texts, in order) and `top_n`; the response parses into
correctly scored, correctly ranked chunks; and a 429, a 500 and a 401 each cause **exactly one**
request.

## Findings

### 1. The SDK's defaults contradicted decision D6(a)
Reading `cohere/core/http_client.py`: by default the SDK retries `408, 409, 429` and every `5xx`
twice, with exponential backoff, and its default timeout is 300 s. "Errors propagate" would have been
untrue: a rate-limited call would have paused and retried invisibly. `build_cohere_client` now passes
`max_retries=0` and `timeout=30`, and a test through the real SDK fails if that changes. Flipping to
retry-with-backoff (D6c) is one argument.

### 2. A reranker can only reorder what it is given: the pool
Measured on the real 223-chunk collection and graph (no Cohere involved): the rank, in the hybrid's
own list of N chunks, of the first chunk the project's labels accept.

| Question | N=3 | N=10 | N=20 | N=30 | N=50 |
|---|---|---|---|---|---|
| Who founded Insurellm? | 1 | 1 | 1 | 1 | 1 |
| What are the company values? | 3 | 3 | 3 | 3 | 3 |
| Which product is for life insurance? | 1 | 1 | 1 | 1 | 1 |
| What does the Carllm product do? | - | 3 | 3 | 3 | 3 |
| Who is the CEO of Insurellm? | 2 | 3 | **15** | 21 | 9 |

All five labelled answers are inside a 20-candidate pool, so the reranker has a chance at every
question (a live test asserts this, and it runs without a key). But the CEO answer's position is not
monotonic in N (2, 3, 15, 21, 9). That is P5-M2's "results depend on k" showing up again: a deeper
pool lets more chunks that both retrievers agree on outrank it. It means the reranker's job is very
different at N=10 (answer already at 3) than at N=20 (answer at 15), and the pool size is a real
tuning parameter that five questions cannot set.

**The CEO label is lenient, and that matters.** The chunk counted as an answer at N=20 is
`company/about.md_0`, whose text says "Insurellm was founded by Avery Lancaster in 2015": it names
the founder, not the CEO. The chunk that states the title, `employees/Avery Lancaster.md_0`, is NOT
in the 20-pool; it enters the hybrid list only at N=50 (rank 17). So at N=20 even a perfect reranker
can at best promote an indirect answer, and a "hit" on this question means less than a hit on the
others. A pool of 50 contains the direct answer, at the cost of sending 50 chunk texts per call.

### 3. Two different "before" baselines
"What does the system return today" is `hybrid.retrieve(q, 3)`. "The first three of a 20-pool" is a
different list. They disagree: for Carllm, today's top 3 misses while the pool's first three hit
(rank 3); for the CEO question, today's top 3 hits (rank 2) while the pool's first three miss
(rank 15). The live report prints today's top 3 next to the reranked top 3 and the pool rank, so
neither baseline is hidden.

### 4. Cohere's limits shape the tests
Trial keys allow 10 rerank requests per minute and 1,000 calls per month (Cohere rate-limit docs).
The live fixture makes 5 calls per run, 6.5 s apart. Running the live tests twice within a minute
could still return a 429, which propagates by design.

## Key concepts
🔴 = core to RAG and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Two-stage retrieval:** stage 1 optimises recall (find everything plausible), stage 2 optimises
  precision (order it well). The reranker's ceiling is the pool's recall.
- 🔴 **Cross-encoder vs bi-encoder:** a bi-encoder embeds question and chunk separately and compares
  vectors (fast, precomputable); a cross-encoder reads both together (accurate, one pass per pair).
- 🔴 **Relevance score:** a model-specific number used to order candidates; not comparable across
  models or, safely, across questions, which is why there is no cut-off (D7).
- 🟡 **Candidate pool size (N):** how many chunks stage 1 hands to stage 2; it trades recall against
  cost and noise.
- 🟡 **Fail fast on a client library's defaults:** read what retries and timeouts an SDK applies before
  claiming a behaviour.
- 🟡 **Testing a client against a fake network:** exercises the real library's request and response
  handling without a service, unlike a fake that replaces the library.
- ⚪ Dependency injection, validation helpers, frozen dataclass: plumbing that makes the above testable.

## Honest limitations
1. **Never run against the real Cohere API.** Quality, latency and the exact score values are
   unmeasured.
2. **Five questions.** Three are already answered by vector search alone; at most two can move. And
   the CEO label accepts an indirect answer (the founder, not the title), so it flatters any system.
3. **The pool size N=20 is an untuned default;** the CEO finding shows it matters.
4. **Chunk text leaves the machine** on every live run (the question plus 20 chunk texts, 5 times).
5. **Only `chunk.text` is scored;** a chunk that never names its subject may be underrated.
6. **No cut-off,** so a pool with no good answer still yields k chunks.
7. **A trial key can rate-limit you,** and by design nothing retries.

## What is still open (needs you)
1. Create a Cohere trial key at dashboard.cohere.com and add it to `.env` as `COHERE_API_KEY=...`
   yourself (I cannot create accounts or enter keys).
2. Run the live report. It sends the five questions and their 20 candidate chunk texts each to Cohere:
   ```bash
   .venv/Scripts/python -m pytest tests/retrieval/test_reranker.py -m live -s -p no:cacheprovider
   ```
3. Send me the printed table. I will then write the outcome assertions (which questions the reranker
   fixes or breaks, with honest strict `xfail`s where it does not) and add the real numbers here.

## How this connects
**Previous:** P5-M2 produces the fused candidate list; P5-M1 and P3 feed it. **This step:** the second
stage that reorders it. **Next (not yet specified to me):** something has to call "retrieve N, then
rerank to k" as one operation and hand the result to a generator; that composition is deliberately not
built here (D4).

## How to do this yourself
1. Decide what stage 1 returns and how many candidates stage 2 will see; measure the pool's recall on
   your labelled questions first, because a reranker cannot recover what is not in the pool.
2. Read the provider's rerank reference: what it takes (`query`, `documents`, `top_n`), what it returns
   (`index`, `relevance_score`), its context length, and its rate limits.
3. Read the SDK's source for retries and timeouts before you promise a failure behaviour.
4. Write the wrapper against a small interface, and test it with a fake first.
5. Test the real SDK against `httpx.MockTransport`: check the URL, the auth header, the body, and how
   errors are raised and counted.
6. Only then run it live, with a key you created yourself, on a handful of questions, printing a
   before/after table rather than asserting outcomes you have not seen.

## Practice exercises
1. Run the live report and compare "hybrid k=3" with "reranked" for every question. Which improved,
   which did not, and is any change larger than what one question can tell you?
2. Change `POOL_SIZE` to 10, then 30, then 50. Which questions change, and does the CEO answer's pool
   rank (table above) predict it? At 50 the direct answer, `employees/Avery Lancaster.md_0`, is in
   the pool: does the reranker put it first?
3. Send `title + text` instead of `text` (D5b) for the Carllm question. Does a contract chunk that
   never names Carllm move?
4. Print `relevance_score` for all 20 candidates of one question. Is there a visible gap between the
   relevant and irrelevant chunks? What threshold would you pick, and why is that risky?
5. Ask Cohere for `top_n=20` and compare the full reranked order with the hybrid order using a rank
   correlation (Spearman). How much does the reranker disagree with fusion?
6. Raise `max_retries` to 2 in `build_cohere_client` and fire 12 calls in a minute. What changes for
   the caller, and what does the test that counts requests now say?
7. Switch the model to `rerank-v4.0-fast`. Compare scores and latency (`time.perf_counter`) on the
   same five questions.
8. Add a 6th labelled question whose answer you know, run it through hybrid → rerank, and record the
   result. What would 30 such questions let you conclude that 5 cannot?
9. Sketch the local alternative: which library call would replace `client.rerank`, what would the
   `documents` truncation look like for a 512-token model, and how many of the 223 chunks it would cut.
10. Break `_unique_chunks` on purpose (remove the `seen` check) and run the tests. Which test tells you,
    and what would the user have seen without it?

## Sources
- Nogueira & Cho (2019), ["Passage Re-ranking with BERT"](https://arxiv.org/abs/1901.04085): the
  original cross-encoder reranking result.
- Cohere, [Rerank overview](https://docs.cohere.com/docs/rerank), [best practices](https://docs.cohere.com/docs/reranking-best-practices)
  and [rate limits](https://docs.cohere.com/docs/rate-limits): models, context length, limits.
- Cohere Python SDK 7.0.8 source (`cohere/core/http_client.py`): the default retry and timeout
  behaviour described in finding 1.
- BGE reranker family, [`bge-reranker-v2-m3`](https://huggingface.co/BAAI/bge-reranker-v2-m3) and
  [C-Pack](https://arxiv.org/abs/2309.07597): the local alternative (D1b).
- Sentence-Transformers, [CrossEncoder usage](https://sbert.net/docs/cross_encoder/usage/usage.html).
- Khattab & Zaharia (2020), [ColBERT](https://arxiv.org/abs/2004.12832), and Sun et al. (2023),
  [RankGPT](https://arxiv.org/abs/2304.09542): the late-interaction and LLM-based alternatives.
- [docs/24](../docs/24%20graph_retrieval_deep_dive.md) and [docs/25](../docs/25%20hybrid_retriever_deep_dive.md):
  the two retrievers whose fused list this step reorders.
