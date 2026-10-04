# Phase 6, Microstep 3 — Design RAG Prompt Template

**Date:** 2026-09-29
**Core learning: Yes — prompt engineering.** This is the first step in Phase 6 whose deliverable
is entirely design: no code, just the actual text that turns "we retrieved some chunks" into "the
model answers grounded in them, honestly, with citations."

## The gap this step closes
`LocalLLM.generate(prompt, temperature, max_tokens)` (P6-M2) can call the model, but nothing yet
decides WHAT to put in `prompt`. Calling it with just the raw question gets an answer from
Mistral's own training data — the retrieved chunks (Phase 5's whole output) would never even be
read. This step designs the two strings (system + user) that fix that, and the rules that make
the answer trustworthy rather than merely fluent.

## What was built
- **[`docs/prompts.md`](../docs/prompts.md)** (the task's literal deliverable): the system
  prompt, the user prompt template (built from real `RankedChunk` fields), a measured 2000-token
  context budget with a verified greedy-best-fit algorithm, the exact `LocalLLM` signature change
  this design assumes, a real worked example from live `HybridRetriever` output (including an
  honest note about what it does NOT demonstrate), and a hand-built example specifically for the
  contradiction rule, checked against the real corpus text.
- No Python code (D8) — `src/orchestration/prompts.py` is left for a later microstep.

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 System/user boundary | Two templates, targeting Ollama's native `system` field (measured: it wraps as `[INST] {system}\n\n{prompt}[/INST]`, Mistral's own trained format) | One concatenated string (works today, no code change); `/api/chat` messages instead | using it for real needs `LocalLLM` to gain a `system` parameter — not built this step, precisely specified for later |
| D2 Grounding | Strict: only the provided context, explicit refusal string when insufficient | Soft (allow outside knowledge); none | this project's own evaluation would become impossible to interpret if the model could answer from memory too |
| D3 Citations | Hybrid: inline `[N]` citations (best effort) + one worked few-shot example, PLUS a mechanical source list a real implementation can always fall back on | Inline-only; mechanical-only | inline precision still depends on a model P6-M2 already measured to ignore strict format instructions twice |
| D4 Context budget | 2000 approx-tokens for sources, greedy best-fit in rank order (verified against real measured chunk-size percentiles) | Fixed chunk count; no cap | `approx_token_count` is an estimate, not real tokenization — the 1260-token margin exists specifically to absorb that error |
| D5 Ordering | Sources, then question, then "Answer:" | Question first | no project-specific evidence either way; matches the common convention |
| D6 Contradictions | Explicit rule + worked phrasing, grounded in this project's own documented CEO contradiction | No instruction | this exact scenario is a real, already-known corpus fact, not hypothetical |
| D7 Style | Concise, plain prose, no filler | No guidance | consistent with whatever `max_tokens` the caller sets |
| D8 Scope | `docs/prompts.md` only, no code | Also write `src/orchestration/prompts.py` now | matches the task's own explicit "Inputs: None (pure design)" |

## Verified, not assumed
```
Ollama's real template (docker exec ... ollama show mistral --template): confirmed [INST]
  {system}\n\n{prompt}[/INST] wrapping, not assumed from documentation alone
System prompt token count       : 386 (count_tokens_approx, run against the real string)
Real worked example (§7)        : real HybridRetriever output for "Who is the CEO of
                                   Insurellm?" -- unedited, including its own honest gap
Budget algorithm (§4)            : traced against real measured percentiles (max=1265, p95=909,
                                   p90=613, median=204) -- kept 2 of 4, total 1878/2000
Contradiction example (§8)       : both quoted facts verified against real source .md files
                                   (found the Apex Reinsurance file I first guessed was WRONG --
                                   the real signatory is on GlobalRe Partners' contract; fixed)
```

## Findings
1. **Ollama already solves the system/user problem correctly — the design just has to use it.**
   Before checking the real template, concatenating system+user into one string seemed like the
   "simple" choice. The real template dump showed Ollama already applies Mistral's own trained
   `[INST] {system}\n\n{prompt}[/INST]` format automatically, for free, the moment `system` is
   passed — the "correct" choice turned out to also be nearly free, once measured instead of
   assumed.
2. **A first draft of the contradiction example cited the wrong file.** I initially wrote
   "Contract with Apex Reinsurance" from memory of the general fact (7 contracts signed by
   Jennifer Rodriguez); grepping the real corpus showed Apex Reinsurance isn't one of the seven —
   GlobalRe Partners' contract is. Fixed before publishing, not after.
3. **The real, live worked example (§7) doesn't demonstrate the rule it's near** — the live
   `HybridRetriever` top-3 for the CEO question doesn't include the Jennifer Rodriguez chunk at
   k=3. Rather than quietly swap in a cleaner, cherry-picked retrieval result, the doc shows the
   real one and says so, with a separate, clearly-labelled constructed example for the
   contradiction rule itself.
4. **The naive token-budget rule ("drop from the bottom") is worse than a slightly less obvious
   one.** Simulating "stop at the first chunk that doesn't fit" against real percentiles would
   have kept only one chunk (1265 tokens) and thrown away a perfectly usable 613-token chunk right
   behind it. "Skip one that doesn't fit, keep checking smaller ones behind it" recovers that
   value — found by actually running the numbers, not by picking the first plausible-sounding rule.
5. **The few-shot justification (D3) held on an easy task and reversed on a hard one — this design
   has now actually been run against the real model.** A follow-up deep dive
   ([docs/31](../docs/31%20few_shot_tie_investigation_deep_dive.md)) tested zero-shot vs. few-shot
   for real. On a trivial single-source question, both tied perfectly (5/5 samples each) — even
   with the citation instruction deleted entirely, the model still cited, so the easy case never
   could have shown a gap. On the REAL two-source contradiction case (§8, the actual production
   worked example), a gap appeared and went the OPPOSITE way expected: zero-shot flagged the
   disagreement in 4/5 samples; few-shot, with the worked example, flagged it in only 1/5,
   confidently picking one CEO instead. The likely cause: the worked example's own resolution
   ("[2] appears to be the more recent figure") teaches "pick a winner," and the model
   over-generalizes that specific move to a case with no valid recency signal at all. This is a
   real, measured risk in the current design, not a hypothetical one — see decision.md
   D-2026-09-29-25 and LEARNING_LOG 2026-09-29 for the full record. Not fixed this session,
   deliberately — revising a completed design deliverable is a call for whoever picks this back up.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **Grounding / closed-world instruction:** telling the model its answer must come only from
  what's provided — the single mechanism that separates "RAG" from "a chatbot that happens to
  have some documents nearby."
- 🔴 **Few-shot vs. zero-shot:** showing one worked example versus only describing the rule —
  chosen here specifically because this project already measured this model's format compliance
  from instructions alone to be unreliable.
- 🟡 **Context budgeting:** a prompt template is not just text, it's text that must fit a real,
  finite window — and the "obvious" truncation rule isn't always the best one.
- 🟡 **Native chat templates:** a served model's own instruction format (here, Mistral's
  `[INST]...[/INST]`) is usually already implemented correctly by the serving layer (Ollama) —
  check before hand-rolling it yourself.
- ⚪ The exact citation bracket syntax, the exact refusal string — specific choices, not concepts.

## Honest limitations
1. ~~Nothing in this design has been run against the real model yet.~~ **Partially resolved by a
   follow-up deep dive (docs/31):** `LocalLLM` still doesn't have a real `system` parameter (§6 is
   still unbuilt), so the exact production prompts have never been sent as designed — but the
   RULES/example/sources TEXT has now been tested, concatenated into one prompt string as §6 itself
   says is an acceptable stand-in until `system` exists. That test found Finding 5 above: a real,
   measured risk in the worked example's own resolution strategy. What's still untested: the exact
   final prompt shape once `system` support is actually built, and every case other than the one
   easy and one hard example tried so far.
2. **The 2000-token budget and 0.0–0.2 temperature recommendation are reasoned defaults, not
   tuned values** — no evaluation set exists yet to tune them against (the same honest gap
   flagged since Phase 5).
3. ~~Citation reliability is genuinely unknown~~ **Narrowed, not resolved:** the follow-up testing
   shows citation PRESENCE is reliable even under an ablation with no citation instruction at all —
   but citation/disagreement CORRECTNESS on a hard case is not, and the mechanical fallback (D3)
   is exactly the safety net that makes this an acceptable, disclosed risk rather than a blocking one.

## How this connects
**Previous:** P6-M2 gave a stable way to call the model; nothing decided what to say to it.
**This step:** the actual words. **Next (not yet specified to me):** almost certainly building
the real prompt-construction code (`src/orchestration/prompts.py`, reading a `RetrievalResult`
and producing these two strings for real) and the `LocalLLM` `system` parameter this design
assumes — then, eventually, running it against the real corpus and the five labelled questions to
see what actually happens.

## How to do this yourself
1. Before designing a prompt for a specific model, check that model's REAL served template
   (`ollama show <model> --template`) rather than assuming a generic chat format — the serving
   layer may already solve part of the problem correctly.
2. Ground every citation/example claim in the actual source data, not from memory of a summary —
   a wrong file name in a worked example is a real, embarrassing bug in a document whose whole
   job is accuracy.
3. When designing a truncation/budget rule, simulate it against real measured data before
   committing to the first plausible-sounding version.
4. Let already-measured model behavior (not assumption) decide how much you can trust
   instructions alone versus needing a worked example.
5. Show your design's own limitations in its own worked examples, rather than picking examples
   that flatter it.

## Practice exercises
1. By hand, using §4's algorithm, work out which chunks survive a 2000-token budget for a ranked
   list of [1100, 700, 500, 300] tokens. Check your answer against the described algorithm.
2. Rewrite the system prompt's refusal string to be less rigid ("say something like...") instead
   of exact. What do you gain? What do you lose, thinking about a future caller that might want
   to detect refusals programmatically?
3. Using `ollama show mistral --template` yourself, find the `.Messages` branch (used by
   `/api/chat`) and compare it to the `/api/generate` branch this design uses. What's different
   about how `system` is applied when there's a conversation history versus a single turn?
4. Take §7's real worked example and manually write what you think a well-behaved answer would
   be. Then read §8's target answer. Are they similar in structure?
5. Compute, by hand, how many tokens `count_tokens_approx` would estimate for a 500-character
   contract clause. Then find a real chunk close to that length in `data/chunks/` and check.
6. Design (on paper) what should happen if TWO chunks in the numbered source list are near-exact
   duplicates (the same fact from two files). Does anything in the current rules address this?
7. Rewrite Rule 3 (contradiction handling) to also cover a THIRD, partially-agreeing source. Is
   "say so explicitly" still enough guidance, or does three-way disagreement need its own example?
8. Pick a different one of this project's five labelled acceptance questions and manually trace
   what its real top-3 hybrid retrieval would look like formatted through §3's template
   (`hybrid.retrieve(question, 3)`, no Cohere needed).
9. Argue, in your own words, for D1(b) (one concatenated string) instead of the chosen D1(a).
   What would you need to be true about this project's priorities for that to be the better call?
10. Once `system` support exists: run §7's exact prompt through the real model and compare the
    real answer to what Rule 1–4 predict. Where does it match, where does it not?

## Sources
- Lewis et al. (2020), [RAG](https://arxiv.org/abs/2005.11401), arXiv:2005.11401.
- Anthropic, [Be clear, direct, and detailed](https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct).
- [Ollama modelfile: TEMPLATE](https://github.com/ollama/ollama/blob/main/docs/modelfile.mdx#template)
  (URL corrected during the docs/30 deep dive — ollama's repo renamed this file upstream).
- OWASP, [LLM01: Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/).
- [notes/step-6.2-llm-wrapper.md](step-6.2-llm-wrapper.md) — the measured instruction-following
  limitation this step's whole citation strategy is built around.
- [decision.md D-2026-09-22-07](../decision.md) — the CEO contradiction this step designs for.
