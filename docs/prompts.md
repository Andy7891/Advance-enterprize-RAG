# RAG Prompt Strategy (P6-M3)

**Status:** design only — no code in this step (`Inputs: None (pure design)`). The templates
below are the literal strings a future prompt-builder should produce; `src/orchestration/prompts.py`
implementing them is deliberately left for a later microstep (D8).

**A gap this design surfaces, not fixes:** [`LocalLLM.generate`/`.stream`](../src/orchestration/llm.py)
(P6-M2) take only `prompt: str` — there is no `system` parameter yet, even though the underlying
`ollama.Client.generate` already supports one. This design targets Ollama's *native* system
mechanism (D1), which means using it for real needs one small, precisely-scoped follow-up to
`LocalLLM` — spelled out exactly in [§6](#6-the-localllm-follow-up-this-design-assumes).

---

## 1. How Ollama actually formats a prompt (measured, not assumed)

Before designing anything, I checked what Mistral's real, installed Ollama template does —
`docker exec advancerag-ollama-1 ollama show mistral --template`:

```
{{- else }}[INST] {{ if .System }}{{ .System }}

{{ end }}{{ .Prompt }}[/INST]
{{- end }}{{ .Response }}
```

(this is the branch used by `/api/generate`, which `LocalLLM` calls — the `.Messages` branch
above it is for `/api/chat`, not used here). In plain terms: if a `system` string is passed,
Ollama wraps it as `[INST] {system}\n\n{prompt}[/INST]` automatically — this is Mistral's own
trained instruction format, and Ollama applies it for us. If no `system` is passed, it's just
`[INST] {prompt}[/INST]`. This is why the design below is written as **two separate strings**
(D1a), not one hand-concatenated block: Ollama's own template already does the wrapping correctly,
for free, the moment `system` is actually passed through.

Also measured, from the same container's logs (P6-M1): the model is served with a **4096-token
context window** (`n_ctx=4096`; Mistral itself trains up to 32768, but Ollama serves 4096 unless
`num_ctx` is explicitly raised). Every budget number in this design is chosen against that real
4096, not the model's theoretical maximum.

## 2. The system prompt

```text
You are a factual question-answering assistant for Insurellm, an insurance technology company. You answer questions using ONLY the numbered source excerpts provided in the user message below. You do not use any knowledge you have from training.

Rules:
1. Answer using ONLY the information in the numbered sources. If the sources do not contain enough information to answer, respond exactly: "I don't have enough information in the provided documents to answer that." Do not guess or use outside knowledge.
2. After any sentence or claim drawn from a source, cite it with the source's number in brackets, like [1]. If a claim draws on more than one source, cite all of them, like [1][3].
3. If two or more sources disagree with each other, say so explicitly (for example: "Source [1] states X, while source [2] states Y") instead of silently choosing one.
4. Be concise. Answer in plain prose, in as few sentences as the question requires. Do not repeat the question, and do not add disclaimers or summaries that were not asked for.

Example:
Sources:
[1] Company Handbook, Section 2 (company/handbook.md): Employees receive 15 days of paid vacation per year.
[2] HR Policy Update 2024 (company/hr_policy_2024.md): Effective 2024, vacation increased to 20 days per year for all staff.

Question: How many vacation days do employees get?

Answer: According to the most recent policy, employees receive 20 days of paid vacation per year [2]. An earlier document states 15 days [1]; the sources disagree, and [2] appears to be the more recent figure.
```

**What each part does, and which decision it implements:**
| Part | Does | Decision |
|---|---|---|
| Opening sentence | Names the domain and states the closed-world constraint ("ONLY...you do not use...training") up front, before any rule | D2 (strict grounding) |
| Rule 1 | The exact refusal string the model should produce when context is insufficient — a fixed string, not "say something like," so a caller can eventually pattern-match it if needed | D2 |
| Rule 2 | The citation format (`[N]`, stackable as `[1][3]`) | D3 (citation, inline half) |
| Rule 3 | The contradiction instruction, with a worked phrasing so the model has a concrete pattern to imitate, not just an abstract instruction | D6 |
| Rule 4 | Length/style constraint | D7 |
| "Example:" block | One complete, worked demonstration exercising rules 2 AND 3 simultaneously (a citation AND a disagreement) in a single self-contained mini-answer | D3 (the few-shot half) — see §5 for why this exists at all |

**Measured cost:** 386 approx-tokens (`count_tokens_approx`, this project's own chars/4 estimate —
verified by actually running it against this exact string, not calculated by hand).

## 3. The user prompt template

```text
Sources:
{numbered_sources}

Question: {question}

Answer:
```

Where `{numbered_sources}` is built from a `RetrievalResult`'s `results` (each a `RankedChunk`),
best-ranked first, one entry per surviving chunk (after §4's budget is applied):

```text
[{i}] {chunk.title} ({chunk.source_path}):
{chunk.text}
```

`i` starts at 1 and increments per surviving chunk (its position in the SURVIVING list, not its
original `final_rank` — if chunk 2 of 3 gets dropped by the budget, the remaining two are still
numbered `[1]` and `[2]`, never `[1]` and `[3]`, so the citation numbers the model sees always
match a contiguous, present list).

**Why this exact ordering (D5):** sources first, question last, "Answer:" as the final token
before generation begins — the question sits closest to where the model starts writing, and
nothing about the task is left implicit ("Answer:" is a direct instruction to start responding,
not just a label).

**Why `title` and `source_path` are both included:** `title` is human-readable (what a person
reading the answer would want to see cited); `source_path` is the literal, disambiguating file
path (`DocumentChunk.source_path`'s own docstring, written in P2, already says "used for
citations in the UI" — this is that intent, finally used). Including both costs a few tokens and
buys unambiguous traceability even when two documents share a similar title.

## 4. Context token budget (measured, not guessed)

**The real problem:** three max-size chunks (1265 approx-tokens each, the real measured maximum
from this corpus) is 3795 tokens — inside a 4096-token window that also has to hold the system
prompt (386), the question, Ollama's own `[INST]` wrapping, and room for the answer itself. There
is no margin left. This is a real, reachable failure mode, not a hypothetical one.

**The budget:** cap retrieved-source text at **2000 approx-tokens**. Arithmetic:
```
4096 (context window)
- 386 (system prompt, including the worked example)
-  50 (question + "Sources:"/"Question:"/"Answer:" labels + [INST] wrapping, generous estimate)
- 400 (a typical max_tokens the caller sets for the answer itself)
------
3260 theoretically available for retrieved sources
```
2000 leaves **1260 tokens of margin** below that theoretical ceiling — deliberate, because
`approx_token_count` is `len(text) // 4`, an *estimate* (this project's own documented trade-off,
`src/ingestion/schemas.py`), not real tokenization, and can run higher than the estimate for
some text. A caller wanting a longer answer (bigger `max_tokens`) eats directly into this same
margin.

**The algorithm** (walk the ranked list best-to-worst; verified against real measured chunk
sizes, not invented ones):
```
kept = []
total = 0
for chunk in results:  # RankedChunk, in final_rank order, best first
    if total + chunk.approx_token_count <= 2000 or not kept:
        # fits within budget, OR this is the very first chunk (always keep at least one)
        kept.append(chunk)
        total += chunk.approx_token_count
    # else: this one chunk doesn't fit right now -- skip it, but keep checking the
    # REST of the ranked list, since a smaller, lower-ranked chunk might still fit
return kept
```
This is a **greedy best-fit in rank order**, not a simple "keep the top N" or "drop from the
bottom" — verified with the real measured percentiles from this corpus (max=1265, p95=909,
p90=613, median=204 approx-tokens):
```
chunk of 1265 tokens -> KEPT, running total 1265
chunk of  909 tokens would push total to 2174 > 2000 -> DROPPED (already have 1)
chunk of  613 tokens -> KEPT, running total 1878
chunk of  204 tokens would push total to 2082 > 2000 -> DROPPED (already have 2)
final kept: 2   final total: 1878
```
A pure "stop at the first one that doesn't fit" rule would have kept only the first (1265) chunk
and thrown away the perfectly-useful 613-token chunk behind it. Skip-and-continue recovers that
value. The rule "always keep at least the top chunk" (the `or not kept` clause) means a single
enormous top-ranked chunk is never allowed to leave the model with zero sources.

**A design decision this step does NOT make:** whether to re-summarize an over-budget chunk
instead of dropping it whole. Out of scope — dropping whole chunks is simpler and preserves
exact-quote citation integrity; summarizing would need its own model call and its own correctness
questions.

## 5. Why a worked example (few-shot), not instructions alone

This is grounded in already-measured evidence, not a guess. [P6-M2](step-6.2-llm-wrapper.md)
found — **twice, independently** — that this exact model does not reliably follow a strict "say
exactly this" instruction even at `temperature=0.0` (asked for "OK", got "Understood."; asked for
"PONG", got "Fun!"). Asking this model to follow a citation FORMAT purely from prose instructions
carries the same real risk. The system prompt's "Example:" block gives the model one concrete,
complete pattern to imitate (a real citation, a real disagreement, a real concise close) rather
than relying on instructions alone — and D3's mechanical fallback (below) means the design does
not depend entirely on the model getting the format right regardless.

**The mechanical half of D3, not yet implementable without code:** whatever a caller of this
design does with the model's raw text output, it should ALSO independently know the real,
numbered source list it built (§3) — meaning a real implementation can always append a reliable
"Sources: [1] ..., [2] ..." list built from data, regardless of whether the model's inline `[N]`
citations were precise. This document specifies the sources list construction (§3) precisely
enough to make that mechanical fallback possible later; building it is not this step's job.

## 6. The `LocalLLM` follow-up this design assumes

To actually use `system` (not required to finish this step, named so the next one is unambiguous):
```python
def generate(
    self, prompt: str, temperature: float, max_tokens: int, *, system: str | None = None
) -> str:
    ...
    response = self._client.generate(
        model=self._model, prompt=prompt, system=system,
        options={"temperature": temperature, "num_predict": max_tokens}, stream=False,
    )
```
A single new **keyword-only** parameter, defaulting to `None` — fully backward compatible with
every existing call site and every one of P6-M2's 35 tests. `stream()` would take the same
addition. Until this exists, §2's system prompt and §3's user prompt can still be concatenated
into one string and passed as `prompt` alone — it will work (Mistral still reads it inside one
`[INST]...[/INST]` block), just without the structural system/user separation the model was
actually trained with.

## 7. A real worked example (live retrieval, unedited)

Run against the real `HybridRetriever` (no Cohere needed) for **"Who is the CEO of Insurellm?"**
— one of this project's own five labelled acceptance questions:

```
top-3 hybrid chunk_ids: employees/Jordan K. Bishop.md_2 (27 tok), company/about.md_0 (225 tok),
                        contracts/Contract with Greenstone Insurance for Homellm.md_0 (65 tok)
total: 317 approx-tokens -- well under the 2000 budget, nothing dropped
```

The real, resulting user prompt (unedited):

```text
Sources:
[1] HR Record (employees/Jordan K. Bishop.md):
Jordan K. Bishop is a valued member of the Insurellm family, exhibiting a commitment to growth and development despite recent challenges.

[2] About Insurellm (company/about.md):
# About Insurellm

Insurellm was founded by Avery Lancaster in 2015 as an insurance tech startup designed to disrupt an industry in need of innovative products. Its first product was Markellm, the marketplace connecting consumers with insurance providers. The company experienced rapid growth in its first five years, expanding its product portfolio to include Carllm (auto insurance portal), Homellm (home insurance portal), and Rellm (enterprise reinsurance platform). By 2020, Insurellm had reached a peak of 200 employees with 12 offices across the US. However, the company underwent a strategic restructuring in 2022-2023 to focus on profitability and sustainable growth. This included consolidating office locations, implementing a remote-first strategy, and streamlining operations. As of 2025, Insurellm operates with a lean, highly efficient team of 32 employees who have built a portfolio of 32 active contracts spanning all eight product lines. The company maintains its San Francisco headquarters along with small satellite offices in key markets including New York, Austin, Chicago, and Denver.

[3] Contract with Greenstone Insurance for Homellm (contracts/Contract with Greenstone Insurance for Homellm.md):
# Contract with Greenstone Insurance for Homellm

---

## Terms

1. **Parties**: This Contract ("Agreement") is entered into on this day, [Insert Date], between Insurellm ("Provider"), located at [Provider Address], and Greenstone Insurance ("Customer"), located at [Customer Address].

Question: Who is the CEO of Insurellm?

Answer:
```

**Honest limitation of this specific example:** it does NOT demonstrate Rule 3 (contradiction
handling). Source [2] names Avery Lancaster as *founder* (not explicitly "CEO" — the same
leniency P6-M2/P5's labels already flagged), and the chunk naming Jennifer Rodriguez as a
contract signatory never made it into this particular top-3. A hybrid top-3 is genuinely what
this system returns for this question today; I'm showing it unedited rather than replacing it
with a cleaner, cherry-picked example. §8 constructs the contradiction case separately, by hand,
specifically because live retrieval didn't happen to surface it here.

## 8. A constructed example: the contradiction rule, in action

This one is NOT from live retrieval — built by hand from this project's own known, documented
data contradiction (`decision.md` D-2026-09-22-07) to verify Rule 3 is a coherent instruction
before any code exists to test it for real:

```text
Sources:
[1] HR Record (employees/Avery Lancaster.md):
Job Title: Co-Founder & Chief Executive Officer (CEO)

[2] Contract with GlobalRe Partners for Rellm (contracts/Contract with GlobalRe Partners for Rellm.md):
Signatures: Jennifer Rodriguez, Title: Chief Executive Officer, Insurellm, Inc.

Question: Who is the CEO of Insurellm?

Answer:
```
The rules above call for something like: *"According to Avery Lancaster's HR record, Avery
Lancaster is Co-Founder & CEO [1]. However, a contract is signed by Jennifer Rodriguez with the
title Chief Executive Officer [2]; the sources disagree on who currently holds this title."* This
is a target to check a real implementation against later — not a claim about what Mistral will
actually produce, which is untested until `LocalLLM` gains `system` support (§6) and this is run
for real.

## 9. Recommended call parameters (not a new decision — a direct consequence of D2/D7)

- `temperature`: **0.0–0.2**. Factual, grounded Q&A wants minimal creative deviation — high
  temperature actively works against Rule 1 and Rule 2's precision.
- `max_tokens`: **300–500** for a typical answer, consistent with Rule 4's "be concise" and with
  §4's budget arithmetic, which assumed 400.

## 10. Explicitly out of scope

**Prompt injection.** A retrieved chunk could, in principle, contain adversarial text like
"ignore previous instructions." This corpus is entirely internal, synthetic, non-adversarial
content — not user-uploaded or web content — so this is a real, standard RAG concern
([OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)) named here deliberately,
not solved, because this system's actual threat model doesn't currently call for it.

## Sources
- Lewis et al. (2020), [Retrieval-Augmented Generation](https://arxiv.org/abs/2005.11401),
  arXiv:2005.11401 — the architecture this whole prompt sits inside.
- Anthropic, [Be clear, direct, and detailed](https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct) —
  the general prompt-engineering principles behind the rule list's structure.
- [Ollama modelfile: TEMPLATE](https://github.com/ollama/ollama/blob/main/docs/modelfile.mdx#template) —
  the mechanism §1 measured directly from the real, installed model. (URL updated during the P6-M3
  deep dive: ollama's repo renamed `docs/modelfile.md` to `docs/modelfile.mdx` upstream, breaking
  the original link — caught by re-verifying every citation before publishing the new doc.)
- OWASP, [LLM01: Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) — §10.
- [notes/step-6.2-llm-wrapper.md](../notes/step-6.2-llm-wrapper.md) — the measured
  instruction-following limitation §5's whole argument rests on.
- [notes/step-6.3-prompt-templates.md](../notes/step-6.3-prompt-templates.md) — this step's own
  build notes, decisions, and practice exercises.
