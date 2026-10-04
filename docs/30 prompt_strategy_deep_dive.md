# 30 — RAG Prompt Strategy: Deep Dive (P6-M3)

> This step produced no Python file — its deliverable is [`docs/prompts.md`](prompts.md) itself,
> a design document. This deep dive deconstructs THAT document's content the same way earlier
> ones deconstructed source code, quoting its real line numbers. The demo scripts below live in
> [`practice codes/phase 6/m3_prompt_template_deep_dive/`](<../practice codes/phase 6/m3_prompt_template_deep_dive/>)
> and need no Docker except `d1`, which makes real calls to the already-running `mistral` model —
> the only genuinely NEW measurement in this whole deep dive, since it tests this step's own
> central hypothesis for the first time. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 6/m3_prompt_template_deep_dive/d0_grounding.py"
> ```

---

## 0. Plain-language summary

`docs/prompts.md` is not code — it's the actual words that will be sent to the model, plus the
rules for how to build them. Five ideas do all the real work:

```
question + retrieved chunks
  |
  v
apply the 2000-token budget (C4) -- keep the best chunks that fit, never zero
  |
  v
build the numbered Sources block (title, source_path, text) + the question
  |
  v
combine with the SYSTEM prompt:
  - grounding rule + exact refusal string                    (C1)
  - ONE worked example, not instructions alone                 (C3)
  - citation rule, backed by a mechanical fallback              (C5)
  |
  v
Ollama's own template wraps it: [INST] {system}\n\n{prompt}[/INST]   (C2)
  |
  v
LocalLLM.generate(...) -- a real call, tested live for the first time in this deep dive
```

Five concepts, in the order they matter most:

| # | Concept | Interview-important? | Where in `docs/prompts.md` |
|---|---|---|---|
| C1 | Grounding / closed-world instruction | 🔴 very high | §2, Rule 1 (lines 43-46) |
| C2 | Native chat templates (system/user separation) | 🔴 high | §1 (lines 15-38), §6 (lines 185-201) |
| C3 | Few-shot vs. zero-shot prompting | 🔴 high | §5 (lines 165-181) |
| C4 | Context window budgeting (greedy best-fit) | 🟡 high | §4 (lines 109-163) |
| C5 | Hybrid citation strategy | 🟡 medium-high | §2 Rule 2 (line 47), §5 (lines 176-181) |

---

## C1 — Grounding / closed-world instruction

### Do this first
Run [`d0_grounding.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d0_grounding.py>).
**Predict first:** the provided sources say Insurellm was founded in 2015, but never say WHO
founded it. An "ungrounded" answerer happens to know the (true) answer from its own memory. Should
it say so, or refuse?

### 1. Definition + analogy
**What it is.** "Grounding" means restricting a model's answer to ONLY the information it was
explicitly given in this call, and instructing it to say so plainly when that information is
missing — rather than filling gaps with whatever it learned during training. A "closed-world"
instruction is the specific phrasing that enforces this: "answer using ONLY..." plus an exact
refusal string for when the sources don't cover it.

**Analogy.** An exam that says "answer using only the attached reading passage" — a student who
happens to know the real-world answer from outside reading is still expected to write "not stated
in the passage," because the exam is testing comprehension of the extract, not general knowledge.

### For a 14-year-old
Imagine a reading-comprehension test. You're handed one short paragraph about a company, and the
question asks something the paragraph never actually says. Now imagine you HAPPEN to already know
the real answer — maybe you read about this company somewhere else last week. Do you write down
what you already knew? A good exam explicitly says NO: "answer using ONLY the attached passage; if
it isn't there, write 'not stated.'" That rule exists because the exam isn't testing whether you
know things in general — it's testing whether you can find the answer IN THIS SPECIFIC TEXT. If
you're allowed to use outside knowledge whenever it's convenient, the exam can never tell the
difference between you reading carefully and you already happening to know the answer by luck.

A RAG system has EXACTLY this problem. When it retrieves the wrong (or incomplete) documents for a
question, the model still has its own enormous "outside knowledge" from training sitting right
there, ready to fill in the gap — confidently, fluently, and with zero indication anything was
missing from the actual documents. The grounding rule is the "answer using only the attached
passage" instruction, applied to an AI instead of a student, for exactly the same reason: to make
retrieval quality the thing that actually determines whether an answer is right, not the model's
own memory quietly filling in for bad retrieval.

**Mapping the story back to the design:**
| In the story | In the real design |
|---|---|
| the exam's attached reading passage | the numbered `Sources:` block built from `RankedChunk`s |
| "answer using only the attached passage" | the system prompt's opening sentence and Rule 1 |
| a question the passage doesn't cover | a question whose retrieved chunks don't actually contain the answer |
| the student secretly knowing the real answer anyway | the model's own training data containing the true fact |
| "write 'not stated' instead" | the exact refusal string: *"I don't have enough information in the provided documents to answer that."* |
| why the exam-writer cares at all | this project needs to know whether RETRIEVAL or the MODEL'S MEMORY produced a correct answer |

### 2. Why here specifically
Phase 5 already measured retrieval returning weak or irrelevant top-k results for some real
questions on this corpus. Without a grounding rule, a model asked one of those questions would
very plausibly answer correctly ANYWAY — from Mistral's own training data about generic insurance
companies, or by hallucinating a plausible-sounding name — and there would be no way to tell that
retrieval had actually failed. Grounding makes retrieval failures VISIBLE as refusals, instead of
invisible as lucky (or unlucky) guesses.

### What breaks if this changes
- **Remove Rule 1 entirely:** the model answers from whatever mix of context and training data it
  wants, with no way to attribute a correct (or wrong) answer to either source — this project's
  own evaluation would become uninterpretable.
- **Soften it to "prefer the sources, but you may use general knowledge":** sounds more helpful,
  but silently reopens the same attribution problem for any question where the model's training
  data happens to know the answer.
- **Keep the rule but drop the EXACT refusal string** (just say "acknowledge when you don't know"):
  a caller wanting to detect refusals programmatically later (e.g., to trigger a "no answer found"
  UI state) loses a reliable string to match against.

### [Interview relevance: very high — "how do you prevent a RAG system from hallucinating when retrieval fails" is one of the single most common RAG interview questions, and "grounding"/"closed-world prompting" is the exact vocabulary interviewers expect]

**Interview question:** *"Your RAG system's retrieval sometimes returns irrelevant documents. How
do you stop the LLM from confidently answering anyway?"*
**A strong answer covers:** an explicit system instruction restricting answers to the provided
context only ("grounding" or "closed-world" prompting); a specific, exact refusal phrase for
insufficient context, rather than a vague "say you don't know," so downstream code can detect it
reliably; naming that this doesn't guarantee compliance (a model can still ignore the instruction
— see C3), so it's a mitigation, not a proof; and that evaluating a RAG system honestly requires
being able to tell "the model refused because retrieval failed" apart from "the model hallucinated
a wrong answer," which an ungrounded system can never distinguish.

### 3. Minimal code that explains the concept
The complete script, [`d0_grounding.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d0_grounding.py>) (run from the project root):
```python
# Minimal demo of C1: a "grounded" answerer only uses what's IN the sources; an "ungrounded"
# one falls back to whatever it already "knows" -- shown here with a fact that is TRUE but
# absent from the sources, so only the grounded answerer correctly refuses.
SOURCES = {  # the ONLY facts this toy assistant is given for this question
    1: "Insurellm was founded in 2015.",  # mentions the founding year, not who founded it
}
MODEL_OWN_TRAINING_KNOWLEDGE = {  # a stand-in for whatever a real LLM "remembers" from training
    "insurellm founder": "Avery Lancaster",  # happens to be TRUE, but it is not in SOURCES above
}


def ungrounded_answer(question):
    key = question.lower().replace("who founded insurellm?", "insurellm founder").strip("? ")
    if key in MODEL_OWN_TRAINING_KNOWLEDGE:  # checks its OWN memory, ignoring the sources entirely
        return MODEL_OWN_TRAINING_KNOWLEDGE[key]  # answers confidently, from memory
    return "I don't know."  # only refuses if even its own memory has nothing


def grounded_answer(question):
    for number, text in SOURCES.items():  # checks ONLY the provided sources, nothing else
        if "founded" in text and "by" in text:  # a crude check for "does this actually say WHO"
            return f"{text} [{number}]"  # would cite it, if the sources actually said who
    return "I don't have enough information in the provided documents to answer that."
    # refuses, because rule 1 says ONLY the sources count, and they don't name a founder


question = "Who founded Insurellm?"  # a question the SOURCES do not actually answer
print("SOURCES available to both answerers:", SOURCES)
print()
print("ungrounded_answer(...) ->", repr(ungrounded_answer(question)))  # confidently right, by luck
print("grounded_answer(...)   ->", repr(grounded_answer(question)))  # honestly refuses
print()
print("The ungrounded answer happens to be TRUE here -- that is exactly the danger: it is right")
print("for the wrong reason, and would be EQUALLY confident if its 'memory' were wrong instead.")
```
Real output:
```
SOURCES available to both answerers: {1: 'Insurellm was founded in 2015.'}

ungrounded_answer(...) -> 'Avery Lancaster'
grounded_answer(...)   -> "I don't have enough information in the provided documents to answer that."

The ungrounded answer happens to be TRUE here -- that is exactly the danger: it is right
for the wrong reason, and would be EQUALLY confident if its 'memory' were wrong instead.
```
The ungrounded answerer got the RIGHT answer here — by luck. Change
`MODEL_OWN_TRAINING_KNOWLEDGE`'s value to a WRONG name and re-run: the ungrounded version will be
exactly as confident, for exactly the same reason, while being wrong.

### The exact text I wrote (`docs/prompts.md`)
> **Line 43:** "You are a factual question-answering assistant for Insurellm, an insurance
> technology company. You answer questions using ONLY the numbered source excerpts provided in
> the user message below. You do not use any knowledge you have from training."

This is the closed-world constraint, stated before any numbered rule.

> **Line 46:** "1. Answer using ONLY the information in the numbered sources. If the sources do
> not contain enough information to answer, respond exactly: 'I don't have enough information in
> the provided documents to answer that.' Do not guess or use outside knowledge."

This is the exact refusal string — fixed, not "say something like."

**🎯 Interview-relevant lines:** the word "ONLY" appears twice across these two lines, and that
repetition is deliberate, not sloppy writing — grounding fails softly and silently if the
instruction is ever ambiguous about whether outside knowledge is allowed.

### Canonical docs
- Lewis et al. (2020), [RAG](https://arxiv.org/abs/2005.11401), arXiv:2005.11401 — the paper that
  named the architecture this rule exists to make trustworthy.
- Anthropic, [Reducing hallucinations](https://docs.claude.com/en/docs/test-and-evaluate/strengthen-guardrails/reduce-hallucinations) —
  closed-world/grounding instructions as a standard hallucination-mitigation technique.

### Micro-exercises
1. In `d0`, add a THIRD question whose answer genuinely IS in `SOURCES`. Write a
   `grounded_answer` case for it that correctly cites `[1]`. Confirm both answerers now agree.
2. Change `MODEL_OWN_TRAINING_KNOWLEDGE`'s value to something FALSE (a made-up name). Re-run.
   Does `ungrounded_answer`'s confidence change at all? What does that prove about why grounding
   matters even when the model happens to be right?
3. Read `docs/prompts.md` lines 43-46 aloud, word by word. Circle (on paper) every word doing
   grounding work versus every word doing something else (tone, domain-naming).
4. Rewrite Rule 1 to be SOFTER ("prefer the sources, but you may use general knowledge if truly
   necessary"). Using `d0`'s own two functions as a model, write what `grounded_answer` would now
   have to do differently for the founder question.
5. Find one of this project's OWN five labelled acceptance questions where you'd predict grounding
   matters most (hint: which one has the weakest retrieval, per Phase 5's own measurements?).

**How it works internally.** There's no mechanism here beyond what an instruction-following model
does with any instruction: it isn't a hard constraint enforced by code, it's a strong influence on
the model's own token-by-token generation, learned during its instruction-tuning. This is exactly
why C3 (few-shot) and C5 (a mechanical fallback) exist — an instruction alone is a request, not a
guarantee.

### How to validate this yourself
Once `LocalLLM` gains `system` support (§6), ask a real question this corpus's retrieval is known
to answer badly (the Carllm product question) alongside its actual, weak top-k chunks, and check
whether the model refuses (grounding worked) or confidently answers something plausible-sounding
but ungrounded (grounding was ignored). Until then, `d0`'s toy version is the only "validation"
possible — a real, honest limitation, stated plainly in `docs/prompts.md` itself.

### How to visualize this
Draw two paths from one question: one arrow goes through a box labelled "retrieved sources" only;
the other goes through a SECOND box labelled "the model's own training data" as well. Grounding is
the rule that's supposed to close off the second arrow entirely — draw it with an X through it.

**You should now be able to explain:** why a RAG system needs an explicit grounding instruction at
all, and why a model being "right anyway" without one is not actually a success.

---

## C2 — Native chat templates (system/user separation)

### Do this first
Run [`d3_system_user_template.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d3_system_user_template.py>).
**Predict first:** `LocalLLM` today has no `system` parameter. What EXACT string, including any
special tokens, do you think actually reaches the model right now for a plain question?

### 1. Definition + analogy
**What it is.** Instruction-tuned models like Mistral aren't trained on raw text — they're
trained on a specific TEMPLATE that marks where instructions end and the user's actual message
begins (Mistral's is `[INST] ... [/INST]`). A model-serving layer (here, Ollama) applies this
template automatically, IF you use its native fields correctly — pass a `system` string, and it
gets wrapped in exactly the position and format the model was trained to expect.

**Analogy.** A prescription pad has a printed structure — patient name here, medication there,
doctor's signature at the bottom — and a pharmacist trusts information written in the RIGHT box
more than the same words scrawled across the whole page in no particular place.

### For a 14-year-old
Imagine a doctor's prescription pad that has specific printed boxes: one for the patient's name,
one for the medicine, one for the dosage, one for the doctor's signature at the bottom. A
pharmacist reading it KNOWS, just from which box something is written in, what kind of information
it is — even without re-reading every word carefully. Now imagine a doctor who, instead of using
the boxes properly, just scribbles everything — the patient's name, the medicine, the dosage, ALL
of it — as one giant paragraph crammed into the "medicine" box. The pharmacist can probably still
figure it out by reading carefully... but it's harder, slower, and exactly the kind of message
this pad was NOT designed for, even though technically every word is still there somewhere on the
page.

Mistral was "trained" the same way that pharmacist was trained to read prescriptions — on messages
that use PROPER boxes: one part for "who you are and how to behave" (the system instructions), a
separate part for "what's actually being asked right now" (the user's message), both wrapped in a
specific marker (`[INST]...[/INST]`) the model was shown thousands of times during training. Ollama
already knows how to fill in those boxes correctly for you, automatically, the SECOND you give it
a real `system` value instead of stuffing everything into one big scribbled paragraph.

**Mapping the story back to the design:**
| In the story | In the real design |
|---|---|
| the prescription pad's printed boxes | Mistral's own trained `[INST]...[/INST]` template |
| the "patient name" / "medicine" boxes specifically | the `system` vs. `prompt` fields `ollama.Client.generate` exposes |
| the pharmacist reading a properly-filled pad easily | Mistral responding to correctly-templated input, as trained |
| the doctor scribbling everything into one box anyway | today's `LocalLLM`, with no `system` parameter, forced to put everything in `prompt` |
| the pharmacist still probably figuring it out | Mistral likely still responding reasonably even to the "everything concatenated" fallback (D1b) |
| the pad automatically formatting each box correctly | Ollama's real template, doing the `[INST] {system}\n\n{prompt}[/INST]` wrapping for you |

### 2. Why here specifically
This design could have simply concatenated system and user text into one string (D1b) — it works,
and needs zero changes to `LocalLLM`. But checking the REAL, installed model's template (not
assuming) showed Ollama already implements the CORRECT mechanism, essentially for free, the moment
`system` is passed as its own field — making the "more correct" choice also the cheaper one to
adopt, once actually measured instead of guessed at.

### What breaks if this changes
- **Never check the real template, just assume a generic chat format:** you might design around
  OpenAI's `{"role": "system", ...}` messages format, which is NOT what Ollama's `/api/generate`
  (or Mistral's own trained format) actually uses — a design built on the wrong assumption.
- **Use `/api/chat` (the `.Messages` branch) instead of `/api/generate`:** also correct, and also
  handles `system` properly, but is a bigger interface change to `LocalLLM` than adding one
  parameter to the `/api/generate` call it already makes (P6-M2's own established choice).
- **Add the `system` parameter to `LocalLLM` but forget to pass it through to
  `client.generate(..., system=system)`:** silently reverts to the "everything as one string in
  `prompt`" behavior with NO error — a parameter that exists but does nothing, a real and easy
  mistake given how P6-M2 already found a "silently does nothing" bug once (max_tokens mapping).

### [Interview relevance: high — knowing that instruction-tuned models use specific trained templates (not just "throw text at it") is a genuine signal of understanding how these models actually work, versus just calling an API]

**Interview question:** *"You want to set a system prompt for a locally-served open-weight model.
How do you make sure it's actually applied correctly?"*
**A strong answer covers:** instruction-tuned models are trained on a SPECIFIC template (e.g.
Mistral's `[INST]...[/INST]`, Llama's own different one); the serving layer (Ollama, vLLM, etc.)
is usually responsible for applying that template correctly, given the RIGHT input fields; check
the model's actual served template (`ollama show <model> --template`) rather than assuming a
generic format; and that manually concatenating system+user into one string often still "works"
reasonably (the model wasn't trained on garbage, after all) but forgoes the structure it was
specifically trained to use.

### 3. Minimal code that explains the concept
The complete script, [`d3_system_user_template.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d3_system_user_template.py>) (run from the project root):
```python
# Minimal demo of C3: Ollama's REAL, measured Mistral template (docker exec ... ollama show
# mistral --template), reproduced here as plain Python string substitution, so you can see
# EXACTLY what [INST]...[/INST] block the model actually receives, with or without `system`.
# The real Go template's relevant branch (verbatim, from `ollama show mistral --template`):
REAL_TEMPLATE_BRANCH = "[INST] {{ if .System }}{{ .System }}\n\n{{ end }}{{ .Prompt }}[/INST]"
print("the real template branch /api/generate uses:")
print(" ", REAL_TEMPLATE_BRANCH)


def apply_real_template(prompt, system=None):
    if system:  # mirrors the real template's "{{ if .System }}" check
        return f"[INST] {system}\n\n{prompt}[/INST]"  # system present: wrapped exactly like this
    return f"[INST] {prompt}[/INST]"  # system absent: just the prompt, still INST-wrapped


print("\n--- what LocalLLM sends TODAY (no `system` parameter exists yet) ---")
today = apply_real_template("What is the capital of France?")  # system=None, the only option now
print(repr(today))

print("\n--- what section 6's proposed LocalLLM change would send, once built ---")
system_text = "You are a helpful geography assistant. Answer in one word."
tomorrow = apply_real_template("What is the capital of France?", system=system_text)
print(repr(tomorrow))

print("\n--- the REJECTED alternative (D1b): manually concatenating system+user into ONE string ---")
concatenated_by_hand = apply_real_template(system_text + "\n\n" + "What is the capital of France?")
print(repr(concatenated_by_hand))
print("\nNotice: the 'tomorrow' and 'concatenated by hand' strings end up nearly identical --")
print("the REAL difference is that Ollama does that wrapping FOR you, correctly, once `system`")
print("is a real parameter, instead of you having to reproduce its exact format by hand forever.")
```
Real output:
```
the real template branch /api/generate uses:
  [INST] {{ if .System }}{{ .System }}

{{ end }}{{ .Prompt }}[/INST]

--- what LocalLLM sends TODAY (no `system` parameter exists yet) ---
'[INST] What is the capital of France?[/INST]'

--- what section 6's proposed LocalLLM change would send, once built ---
'[INST] You are a helpful geography assistant. Answer in one word.\n\nWhat is the capital of France?[/INST]'

--- the REJECTED alternative (D1b): manually concatenating system+user into ONE string ---
'[INST] You are a helpful geography assistant. Answer in one word.\n\nWhat is the capital of France?[/INST]'

Notice: the 'tomorrow' and 'concatenated by hand' strings end up nearly identical --
the REAL difference is that Ollama does that wrapping FOR you, correctly, once `system`
is a real parameter, instead of you having to reproduce its exact format by hand forever.
```

### The exact text I wrote (`docs/prompts.md`)
> **Lines 21-24** (the real Go template's `/api/generate` branch, captured verbatim from the
> running container via `docker exec advancerag-ollama-1 ollama show mistral --template`):
> ```
> {{- else }}[INST] {{ if .System }}{{ .System }}
>
> {{ end }}{{ .Prompt }}[/INST]
> {{- end }}{{ .Response }}
> ```

> **Lines 187-194** (the proposed `LocalLLM.generate` change):
> ```python
> def generate(
>     self, prompt: str, temperature: float, max_tokens: int, *, system: str | None = None
> ) -> str:
>     ...
>     response = self._client.generate(
>         model=self._model, prompt=prompt, system=system,
>         options={"temperature": temperature, "num_predict": max_tokens}, stream=False,
>     )
> ```
> Line 188 is the ONE new parameter this design assumes, keyword-only, defaulting to `None`.
> Line 192 shows it passed straight through to the real SDK's own `system` field.

**🎯 Interview-relevant lines:** lines 21-24 are not something I wrote from a template or
documentation — they were copied verbatim from `docker exec advancerag-ollama-1 ollama show
mistral --template`, run against the real, installed model. Being able to say "I checked the
actual served template" instead of "the docs say" is the whole point of this concept.

### Canonical docs
- [Ollama modelfile: TEMPLATE](https://github.com/ollama/ollama/blob/main/docs/modelfile.mdx#template)
- [Mistral AI docs: tokenization / instruction format](https://docs.mistral.ai/guides/tokenization/) —
  the `[INST]...[/INST]` convention itself.

### Micro-exercises
1. Run `docker exec advancerag-ollama-1 ollama show mistral --template` yourself. Find the
   `.Messages` branch (used by `/api/chat`) and compare how IT applies `system` differently from
   the `/api/generate` branch this design uses.
2. In `d3`, change `system_text` to an empty string `""`. Predict, then check: does
   `apply_real_template` treat it the same as `system=None`? *Hint: re-read the real template's
   `{{ if .System }}` check — is an empty string "truthy" in Go templates the way it might not be
   in Python?*
3. Pull a DIFFERENT model (`llama3.2`) and run `ollama show llama3.2 --template`. Is its template
   structure similar to Mistral's, or completely different?
4. Using `d3`'s `apply_real_template` function, build the exact string that would result from
   §7's real worked example (the CEO question) WITH a system prompt attached. How long is it,
   roughly, compared to without?
5. Write, on paper, what `LocalLLM.generate`'s real implementation change (§6) would look like if
   you were adding `system` support via `/api/chat` (a messages list) instead of `/api/generate`.

**How it works internally.** Ollama's server receives the raw JSON request (`model`, `prompt`,
`system`, `options`, ...), loads the named model's own `Modelfile` (which stores that Go template
string), and renders it with the request's fields substituted in — exactly like Python's own
f-strings or `.format()`, just using Go's template syntax and evaluated server-side, before a
single token is fed to the actual model. The model itself never "knows" about JSON fields; it only
ever sees the final, rendered `[INST]...[/INST]` text.

### How to validate this yourself
There's no direct client-side way to SEE the exact rendered string Ollama sends into the model
(it happens server-side) — `ollama show --template` combined with reasoning about the
substitution, as `d3` does, is the most direct verification available without server-side
logging. `docker logs -f advancerag-ollama-1` while making a real call will show internal
`llama_context`/`llama_kv_cache` log lines, but not the literal rendered prompt text itself.

### How to visualize this
Draw a small pipe: a box "your `system` string" and a box "your `prompt` string" both feeding
into a middle box labelled "Ollama's template renderer," which outputs ONE box: the final
`[INST]...[/INST]` string that's the only thing the model itself ever actually sees.

**You should now be able to explain:** why an instruction-tuned model needs its input in a
SPECIFIC format, and how Ollama's `system` field connects to that format automatically.

---

## C3 — Few-shot vs. zero-shot prompting

### Do this first
Run [`d1_few_shot_live_experiment.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d1_few_shot_live_experiment.py>).
**Predict first:** this is a REAL call to the real, running `mistral` model — not a simulation.
Given P6-M2 already measured this model ignoring a "reply with exactly one word" instruction
twice, do you predict the zero-shot (instructions-only) prompt will produce a `[1]` citation? Does
adding ONE worked example (few-shot) change the outcome?

### 1. Definition + analogy
**What it is.** **Zero-shot** prompting means describing a rule in words and hoping the model
follows it. **Few-shot** prompting means also SHOWING the model one (or a few) complete, correct
examples of the desired behavior, so it has a concrete pattern to imitate, not just an abstract
description. `docs/prompts.md`'s system prompt uses few-shot specifically because of ALREADY
MEASURED evidence (not a guess) that this model's zero-shot instruction-following is unreliable —
see below for what happened when that specific claim was finally tested directly.

**Analogy.** Telling someone "fold the shirt neatly" (zero-shot) versus folding one shirt in front
of them first, then asking them to do the rest the same way (few-shot).

### For a 14-year-old
Imagine you're teaching a younger sibling to fold laundry. You could just say "fold the shirts
neatly" and walk away — that's zero-shot. Some kids will do a great job; others will hear "fold
neatly" and produce something that's technically folded but nothing like what you meant, because
"neatly" doesn't actually tell them WHERE to fold, which way to turn the sleeves, or how big the
final rectangle should be. Now instead, imagine you fold ONE shirt slowly in front of them,
narrating each step, and THEN say "now do the rest like that" — that's few-shot. You haven't
explained any NEW rule they didn't already sort of know ("fold neatly" was always the goal) — but
you've shown them one concrete, complete example of what "neatly" actually means in practice, and
that's often the difference between a vague instruction landing correctly and it not.

This project already caught its own AI assistant (Mistral) failing at the "just tell it" approach
— TWICE. Asked to reply with exactly the word "OK," it said "Understood." Asked for exactly
"PONG," it said "Fun!" Those are small, harmless misses, but they PROVE this specific model
sometimes hears an instruction and does something else entirely reasonable-sounding instead of
literally following it. Given that, trusting a MORE complicated instruction (a citation format,
which is a real structural rule, not a single word) purely from prose felt like tempting the same
problem — so the system prompt shows the model ONE complete, correctly-cited, correctly-formatted
answer first, the same way you'd fold one shirt before asking for the rest. (Whether that specific
worry actually PLAYS OUT for citations — as opposed to single-word replies — is exactly what this
deep dive tests for real, below.)

**Mapping the story back to the design:**
| In the story | In the real design |
|---|---|
| "fold the shirt neatly" | Rule 2's citation instruction, in words alone |
| folding a rectangle that's technically "folded" but wrong | a real answer with no `[N]` citations at all |
| folding ONE shirt in front of them first | the system prompt's "Example:" block |
| "now do the rest like that" | the model generating a NEW answer, imitating the shown pattern |
| the two prior "OK"->"Understood.", "PONG"->"Fun!" misses | the measured evidence (P6-M2) this whole decision is built on |
| "some kids do fine with just the instruction" | some models/some tasks might not need the example at all — the thing this deep dive actually tested |

### 2. Why here specifically
This isn't a generic "few-shot is usually better" claim — it's a decision made because THIS
project ALREADY measured THIS specific model failing at literal instruction-following, twice, in
the immediately preceding microstep. Using that evidence, instead of a general prompt-engineering
platitude, is the whole point of building this project's own track record before designing on top
of it. But that evidence was about a DIFFERENT, narrower task (single-word replies) — extrapolating
it to citation formatting was an assumption, and assumptions get tested. See below.

### What breaks if this changes
- **Zero-shot only (no example):** relies entirely on the model correctly interpreting Rule 2 from
  prose — the exact category of instruction this model has already been observed to sometimes
  ignore, for a DIFFERENT task shape.
- **Too many examples (heavy few-shot, e.g. 5+ worked answers):** costs real context-window tokens
  (every example adds to the 386-token system prompt measured in §2) that could otherwise go
  toward retrieved sources (§4's own 2000-token budget) — a real trade-off, not a free improvement.
- **An example that doesn't match the real task's shape** (e.g., an example with no citations at
  all, or one that never shows a disagreement): teaches the WRONG pattern — the chosen example
  deliberately exercises both Rule 2 (citation) and Rule 3 (disagreement) in one shot, precisely so
  it teaches both at once.

### [Interview relevance: very high — few-shot vs. zero-shot is one of the most fundamental, frequently-asked prompt-engineering distinctions, and grounding the choice in MEASURED model behavior rather than a general rule of thumb is exactly the kind of answer that stands out]

**Interview question:** *"When would you use a few-shot example instead of just describing the
rule in your system prompt?"*
**A strong answer covers:** few-shot costs context-window tokens that zero-shot doesn't, so it's a
real trade-off, not a free upgrade; it helps most when the desired behavior is STRUCTURAL (a
format, a pattern) rather than a simple factual instruction; the right test is empirical — does
THIS model, on THIS task, actually comply better with an example than without one — not a general
assumption either way; and, the strongest answer, citing that this decision should be grounded in
actually observed model behavior rather than "few-shot is generally better" — and being honest
when a direct test doesn't confirm what an earlier, related measurement suggested it would.

### 3. Minimal code that explains the concept — a REAL, live experiment
The complete script, [`d1_few_shot_live_experiment.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d1_few_shot_live_experiment.py>) (run from the project root; needs the real, running Ollama):
```python
# A REAL experiment, not a toy: does the worked example (few-shot) actually improve citation
# compliance versus instructions alone (zero-shot), on the REAL model this project designed for?
# This directly tests docs/prompts.md section 5's own justification for existing.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.orchestration.llm import build_llm  # the real, already-tested wrapper (P6-M2)

RULES = """You are a factual question-answering assistant. Answer using ONLY the numbered sources below. After any claim, cite its source number in brackets, like [1]."""
# the SAME rules text, used in both prompts below -- only the presence of a worked example differs

FEW_SHOT_EXAMPLE = """

Example:
Sources:
[1] Company Handbook (handbook.md): Employees receive 15 days of paid vacation per year.

Question: How many vacation days do employees get?

Answer: Employees receive 15 days of paid vacation per year [1]."""
# ONE worked example, matching the real system prompt's own "Example:" block

REAL_SOURCES = """

Sources:
[1] Office Policy (office.md): The office is closed on all US federal holidays.

Question: When is the office closed?

Answer:"""
# a DIFFERENT question and source than the example, so the model cannot just copy the example

zero_shot_prompt = RULES + REAL_SOURCES  # instructions, then straight to the real question
few_shot_prompt = RULES + FEW_SHOT_EXAMPLE + REAL_SOURCES  # instructions, ONE example, then the real question

llm = build_llm("http://localhost:11434")  # a real LocalLLM against the real, running Ollama

print("--- ZERO-SHOT (rules only, no worked example) ---")
zero_shot_reply = llm.generate(zero_shot_prompt, temperature=0.0, max_tokens=60)  # one real call
print(repr(zero_shot_reply))
print("contains a [1] citation:", "[1]" in zero_shot_reply)

print("\n--- FEW-SHOT (rules + one worked example) ---")
few_shot_reply = llm.generate(few_shot_prompt, temperature=0.0, max_tokens=60)  # one real call
print(repr(few_shot_reply))
print("contains a [1] citation:", "[1]" in few_shot_reply)
```
Real output, from the real, running `mistral` model — **the first time this step's own central
justification has actually been tested, not just argued for**:
```
--- ZERO-SHOT (rules only, no worked example) ---
' The office is closed on all US federal holidays [1].'
contains a [1] citation: True

--- FEW-SHOT (rules + one worked example) ---
' The office is closed on all US federal holidays [1].'
contains a [1] citation: True
```

**Honest result: this did NOT confirm the hypothesis.** Both prompts produced the exact same
correctly-cited reply. Zero-shot needed no worked example at all for this question. This is worth
sitting with rather than explaining away: `docs/prompts.md` §5 justified few-shot using evidence
from a DIFFERENT, narrower task (P6-M2's "reply with exactly one word" failures) and extrapolated
it to a structurally different one (adding a bracketed citation after a claim). That extrapolation
was reasonable to make, but it was still an assumption, not a measurement — and this experiment is
the first time anyone actually measured it. The honest conclusion isn't "few-shot was unnecessary
all along" either — one single-source, single-citation question is a low bar; the real system
prompt's worked example ALSO teaches the harder pattern of citing two conflicting sources in one
answer (Rule 3), which this simple test never exercises. What the result actually shows is
narrower and more useful than either conclusion: the specific evidence used to justify few-shot
didn't transfer to this task the way it was assumed to, and a claim like that needs its own direct
test, on its own task shape, before it can be trusted — exactly the same "measure, don't assume"
lesson this project has hit before (the Ollama template, the citation fact-check in §8), now
landing on this project's own design reasoning instead of someone else's code.

### The exact text I wrote (`docs/prompts.md`)
> **Lines 167-174:** "This is grounded in already-measured evidence, not a guess.
> [P6-M2](step-6.2-llm-wrapper.md) found — **twice, independently** — that this exact model does
> not reliably follow a strict 'say exactly this' instruction even at `temperature=0.0` (asked for
> 'OK', got 'Understood.'; asked for 'PONG', got 'Fun!'). Asking this model to follow a citation
> FORMAT purely from prose instructions carries the same real risk. The system prompt's 'Example:'
> block gives the model one concrete, complete pattern to imitate (a real citation, a real
> disagreement, a real concise close) rather than relying on instructions alone — and D3's
> mechanical fallback (below) means the design does not depend entirely on the model getting the
> format right regardless."

Line 168 is the specific, measured evidence this whole decision rests on. Line 171 states the
mechanism: ONE concrete pattern to imitate, not just an abstract rule.

**🎯 Interview-relevant lines:** line 168 links directly to a DIFFERENT microstep's own measured
finding — a genuinely strong prompt-engineering habit is citing your own prior evidence, not just
prompt-engineering folklore. Line 174's mention of "D3's mechanical fallback" is exactly why this
untested extrapolation was a REASONABLE risk to take, not a reckless one: even if few-shot hadn't
helped, C5's mechanical citation list was already the real safety net, not the model's compliance.

### Canonical docs
- Brown et al. (2020), [GPT-3: Language Models are Few-Shot Learners](https://arxiv.org/abs/2005.14165),
  arXiv:2005.14165 — the paper that introduced and named few-shot prompting as a distinct technique.
- [notes/step-6.2-llm-wrapper.md](../notes/step-6.2-llm-wrapper.md) — the two measured zero-shot
  failures this whole decision is a direct response to.

### Micro-exercises
1. Run `d1` a second time. Is the zero-shot result the SAME as the first run? (`temperature=0.0`
   should make it deterministic — does it?)
2. Change `FEW_SHOT_EXAMPLE` to a worked example that does NOT include a `[1]` citation at all
   (just plain prose). Re-run the few-shot half. Does citation compliance change?
3. The live test only exercised ONE source and ONE citation. Edit `d1` to give it TWO sources that
   disagree (mirroring Rule 3), and ask a question needing both `[1]` and `[2]` in one answer. Does
   zero-shot still match few-shot on this harder shape, or does a gap finally appear?
4. Add a SECOND few-shot example (two worked answers instead of one) and re-run. Does compliance
   improve further, stay the same, or does the longer prompt change anything else you notice?
5. Time both calls (`time.perf_counter()` around each `llm.generate(...)`). Is the few-shot call
   meaningfully slower, given it's a longer prompt Ollama has to process first?

**How it works internally.** An instruction-tuned model doesn't "understand" a worked example the
way a person does — it's simply continuing a pattern of text. A correct example immediately before
the real task shifts the probability distribution over the model's NEXT tokens toward continuing
that same STRUCTURE (citations present, a similar sentence shape), the same statistical mechanism
that makes the model complete "roses are red, violets are..." — it's pattern continuation, not
comprehension of a rule. On an EASY task (one source, one obvious citation), the base rate of
"correctly continuing the pattern" is already high enough that the example adds nothing measurable
— which is consistent with, not contradictory to, the result above.

### How to validate this yourself
`d1`'s own printed `"[1]" in reply` check IS the validation — run it, read the real booleans, and
you have actual, first-party evidence (not an assumption carried over from P6-M2's unrelated
one-word-instruction findings) about THIS specific behavior, on THIS specific task shape. Exercise
3 above is the natural next validation step, on a harder task shape than this deep dive tested.

### How to visualize this
Draw the system prompt as a strip of text. In the zero-shot version, draw an arrow straight from
"Rule 2" to the model's output, labelled "interpretation required." In the few-shot version, draw
the SAME arrow, but route it THROUGH a box labelled "worked example" first — pattern to copy,
not just a rule to interpret. Then draw both arrows arriving at the SAME output box, labelled
"identical result, this task" — the honest picture for what was actually measured here.

**You should now be able to explain:** the real difference between zero-shot and few-shot
prompting, why this design choice was backed by real prior evidence, and why a direct test of that
reasoning — even one that doesn't confirm it — is more valuable than trusting the extrapolation.

---

## C4 — Context window budgeting (greedy best-fit)

### Do this first
Run [`d2_budget_algorithm.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d2_budget_algorithm.py>).
**Predict first:** four chunks, sized 1265, 909, 613, and 204 tokens, best-ranked first, budget
2000. Which ones survive?

### 1. Definition + analogy
**What it is.** A model's context window is a hard, finite limit — every token of system prompt,
retrieved sources, question, and generated answer together must fit inside it (here, a measured
4096). "Budgeting" means deciding, BEFORE generation, exactly how much of that limited space
retrieved context gets, and which specific chunks to keep when the ranked list doesn't all fit.

**Analogy.** Packing a suitcase with a strict weight limit: you don't just throw in items in
whatever order until it's full and hope the last thing fit — you decide a packing STRATEGY (most
important items first, skip a bulky one if a smaller useful one fits instead) before you start.

### For a 14-year-old
Imagine packing a suitcase for a trip, but the airline has a strict weight limit, and you have a
RANKED list of things you want to bring, most important first: your laptop (heavy), your favorite
jacket (medium), a phone charger (light), and a spare pair of shoes (medium-heavy). If you just
pack in order and stop the SECOND something doesn't fit, you might pack the heavy laptop, then hit
the jacket and find it pushes you over the limit, and just... give up, leaving the charger and
shoes at home too — even though BOTH of those would have fit easily on their own! A smarter
packer keeps going: skip the jacket since it doesn't fit right now, but still try the charger
(fits!) and the shoes (also fits!) — ending up with MORE useful stuff packed overall, just by not
giving up the instant one specific item didn't work.

That's exactly the difference between the REJECTED "stop at the first miss" rule and the ACTUAL
"skip and keep checking" algorithm this design uses. And just like you'd never leave with an EMPTY
suitcase even if your single most important item alone were over the weight limit (you'd still
bring it, over limit or not, rather than travel with nothing) — the algorithm always keeps at
least the single best-ranked chunk, no matter its size.

**Mapping the story back to the design:**
| In the story | In the real design |
|---|---|
| the ranked packing list, most important first | `RetrievalResult.results`, a `RankedChunk` tuple, best `final_rank` first |
| the suitcase's weight limit | the 2000 approx-token budget |
| giving up the instant the jacket doesn't fit | the REJECTED "stop at first miss" rule |
| skipping the jacket but still trying the charger and shoes | the `or not kept` / skip-and-continue algorithm this design actually uses |
| bringing the heaviest item anyway, over the "ideal" limit, rather than nothing | "always keep at least the top chunk" |
| the airline's ACTUAL weight limit, not an estimate of it | the real, measured 4096-token context window (not Mistral's theoretical 32768) |

### 2. Why here specifically
This corpus has real chunks up to 1265 approx-tokens (measured, Phase 5). Three such chunks alone
would be 3795 tokens — inside a measured 4096-token window that must ALSO hold the system prompt,
question, and generated answer. Without an explicit budget, this is a real, reachable way to
silently overflow the model's context — not a hypothetical edge case.

### What breaks if this changes
- **No budget at all:** the real, measured failure mode above — a genuinely possible silent
  overflow on a question that happens to retrieve several large chunks.
- **A fixed chunk-count cap (e.g., always exactly 3), no token awareness:** three max-size chunks
  (3795 tokens) would still nearly fill the entire window, leaving no room for the question or the
  answer — the exact problem a token-aware budget exists to prevent.
- **"Stop at the first miss" instead of "skip and keep checking":** demonstrated directly by `d2`
  — discards a perfectly useful smaller chunk sitting right behind an oversized one, for no
  benefit.

### [Interview relevance: high — "how do you handle a context window limit when you have more retrieved context than fits" is a standard, practical RAG systems-design question]

**Interview question:** *"You retrieve 10 chunks but only 4096 tokens of context are available.
How do you decide what to include?"*
**A strong answer covers:** measure real chunk sizes on your actual data, don't guess; reserve
budget for everything ELSE sharing the same window (system prompt, question, expected answer
length) before deciding how much is left for context; prefer keeping chunks WHOLE over truncating
mid-text (loses less coherence, and preserves exact-quote citation integrity); use a greedy
best-fit approach rather than stopping at the first oversized item, since a later, smaller item
might still fit; and always guarantee at least one source, even if the single best one alone
exceeds the "ideal" budget — an over-budget answer beats an unfounded one.

### 3. Minimal code that explains the concept
The complete script, [`d2_budget_algorithm.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d2_budget_algorithm.py>) (run from the project root):
```python
# Minimal demo of C4: the EXACT algorithm from docs/prompts.md section 4, run against real
# measured chunk-size percentiles from this corpus (max, p95, p90, median approx-tokens).
BUDGET = 2000  # the exact number chosen in docs/prompts.md section 4, with its own worked arithmetic


def apply_budget(chunks_best_to_worst):
    kept = []  # will hold the chunks that survive the budget, in their original order
    total = 0  # the running total of approx-tokens committed so far
    for size in chunks_best_to_worst:  # walks the ranked list from best to worst
        if total + size <= BUDGET or not kept:  # fits, OR this is the very first chunk
            kept.append(size)  # keeps this chunk
            total += size  # adds its size to the running total
        # else: this one chunk does not fit right now -- it is skipped, but the loop
        # keeps going, because a SMALLER, lower-ranked chunk further down might still fit
    return kept, total  # hands back which sizes survived, and their real total


REAL_PERCENTILES = {"max": 1265, "p95": 909, "p90": 613, "median": 204}  # measured, not invented
ranked_sizes = [REAL_PERCENTILES["max"], REAL_PERCENTILES["p95"], REAL_PERCENTILES["p90"], REAL_PERCENTILES["median"]]
# treats the four percentiles as if they were four real chunks, best (biggest/most-relevant-here) first

survivors, total = apply_budget(ranked_sizes)  # runs the real algorithm against real numbers
print(f"budget: {BUDGET} approx-tokens")
print(f"ranked chunk sizes (best to worst): {ranked_sizes}")
print(f"survivors: {survivors}")
print(f"final total: {total} / {BUDGET}")

print("\ncompare to the REJECTED 'stop at first miss' rule:")
naive_kept = []  # what a simpler, worse rule would keep
naive_total = 0  # its own running total
for size in ranked_sizes:  # the SAME ranked list
    if naive_total + size > BUDGET and naive_kept:  # the first chunk that doesn't fit STOPS everything
        break  # gives up on the rest of the list entirely, even if a smaller one would fit
    naive_kept.append(size)
    naive_total += size
print(f"naive survivors: {naive_kept}  (total {naive_total}) -- discards the useful {REAL_PERCENTILES['p90']}-token chunk")
```
Real output:
```
budget: 2000 approx-tokens
ranked chunk sizes (best to worst): [1265, 909, 613, 204]
survivors: [1265, 613]
final total: 1878 / 2000

compare to the REJECTED 'stop at first miss' rule:
naive survivors: [1265]  (total 1265) -- discards the useful 613-token chunk
```

### The exact text I wrote (`docs/prompts.md`)
> **Line 116:** "**The budget:** cap retrieved-source text at **2000 approx-tokens**. Arithmetic:"
>
> **Lines 118-123:**
> ```
> 4096 (context window)
> - 386 (system prompt, including the worked example)
> -  50 (question + "Sources:"/"Question:"/"Answer:" labels + [INST] wrapping, generous estimate)
> - 400 (a typical max_tokens the caller sets for the answer itself)
> ------
> 3260 theoretically available for retrieved sources
> ```

Line 116 is the exact number chosen. Line 123 is what's actually left over for retrieved sources,
after everything else sharing the window is reserved.

> **Lines 134-143:**
> ```
> kept = []
> total = 0
> for chunk in results:  # RankedChunk, in final_rank order, best first
>     if total + chunk.approx_token_count <= 2000 or not kept:
>         # fits within budget, OR this is the very first chunk (always keep at least one)
>         kept.append(chunk)
>         total += chunk.approx_token_count
>     # else: this one chunk doesn't fit right now -- skip it, but keep checking the
>     # REST of the ranked list, since a smaller, lower-ranked chunk might still fit
> return kept
> ```

Line 137 is the fits-OR-first-chunk check — never leave the model with zero sources. Line 141 is
where the chunk is skipped, but the loop CONTINUES, unlike a naive stop-at-first-miss rule.

**🎯 Interview-relevant lines:** line 137's `or not kept` is a small, three-word clause doing a
real job — without it, a single oversized top-ranked chunk could leave the model with literally
no context at all, which is worse than one over-length source.

### Canonical docs
- [Ollama modelfile: PARAMETER (num_ctx)](https://github.com/ollama/ollama/blob/main/docs/modelfile.mdx#parameter) —
  the setting that would actually raise the served 4096 limit, if ever needed.
- `src/ingestion/schemas.py`'s own `count_tokens_approx` docstring — this project's own
  documented reasoning for the chars/4 estimate the whole budget is built on.

### Micro-exercises
1. In `d2`, change `BUDGET` to `1200`. Predict, then check: how many of the four real percentile
   sizes survive now?
2. Add a FIFTH, tiny chunk (50 tokens) to `ranked_sizes`, placed LAST (lowest-ranked). Does it
   get a chance to be kept, given the other four's sizes? Trace it by hand first.
3. Modify `apply_budget` to also RETURN which sizes were dropped, not just which survived. What
   would a caller do differently knowing both lists, versus just the survivors?
4. Using `count_tokens_approx` (or `len(text)//4` directly), estimate how many CHARACTERS 2000
   tokens roughly corresponds to. Open a chunk file in `data/chunks/` close to that size.
5. Design, on paper, a DIFFERENT budget rule that reserves MORE margin when `max_tokens` (the
   caller's requested answer length) is larger. What would the arithmetic in §4 look like if
   `max_tokens` were a parameter instead of a fixed assumption of 400?

**How it works internally.** This is a single linear pass over the ranked chunk list — no
sorting is needed (the list already arrives sorted by `final_rank`), and the algorithm makes
exactly one decision per chunk (`fits or already-empty -> keep`, `else -> skip`) in one pass,
so it costs O(n) in the number of candidate chunks, trivial compared to the cost of the
generation call that follows it.

### How to validate this yourself
Once a real prompt-builder exists, feed it a `RetrievalResult` deliberately constructed (via a
fake `HybridRetriever`/`Reranker`, exactly like P5-M4's own hermetic tests) with chunks whose
`approx_token_count` values you choose yourself, and assert the exact survivor list — this is
directly testable with zero real services, the same pattern this whole project already uses.

### How to visualize this
Draw the ranked chunk list as a horizontal bar, each chunk a rectangle sized by its real token
count. Draw a vertical line at the 2000-token mark. Shade every rectangle that ends up INSIDE the
line green, and the ones that don't fit but get skipped-over red — the greedy algorithm's own
"skip, don't stop" behavior becomes visually obvious as green rectangles appearing AFTER a red one.

**You should now be able to explain:** why a token budget needs an algorithm at all (not just "cut
off wherever it happens to overflow"), and why skip-and-continue beats stop-at-first-miss.

---

## C5 — Hybrid citation strategy

### Do this first
Run [`d4_citation_strategy.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d4_citation_strategy.py>).
**Predict first:** the model's raw reply sometimes contains perfect `[1][2]` citations, sometimes
none at all. Does the FINAL answer a reader sees still contain a real, correct source list either
way?

### 1. Definition + analogy
**What it is.** A "hybrid" strategy combines something that MIGHT work well (the model's own
inline `[N]` citations, following Rule 2 and the worked example) with something that RELIABLY
always works (a plain source list, built directly from the real data, with zero dependence on the
model's behavior). The reader always gets the reliable list; the model's own inline citations are
a bonus layer of precision when they happen to be correct.

**Analogy.** A restaurant receipt that always lists every item you ordered at the bottom (reliable,
mechanical, from the register's own records) — even if the waiter ALSO verbally mentioned some of
them while serving (which you might have missed, misheard, or the waiter might have forgotten to
say).

### For a 14-year-old
Imagine a restaurant where the waiter sometimes mentions, out loud, "this is your soup" or "here's
your burger" as they set each dish down. That's nice, helpful, and it's how MOST restaurants work
— but it's not perfectly reliable: the waiter might forget to say it for one dish, or you might be
busy talking to a friend and miss it. Now imagine that EVERY restaurant, no matter how forgetful
the waiter is that day, ALSO prints a receipt at the end listing every single item you actually
ordered, straight from the kitchen's own register — a completely separate, mechanical record that
never depends on the waiter remembering to say anything.

If the waiter announces every dish perfectly, great — you got BOTH the nice spoken confirmation
AND the receipt. If the waiter forgets to mention three dishes, it genuinely doesn't matter — the
receipt still has everything, because it was never generated by the waiter's memory in the first
place, it came from the kitchen's own records. That's the hybrid citation design: the model's
inline `[N]` citations are the friendly waiter (nice when it works, not depended upon), and the
mechanical source list (built from the REAL `RankedChunk` data, not from anything the model said)
is the receipt that's always right regardless.

**Mapping the story back to the design:**
| In the story | In the real design |
|---|---|
| the waiter announcing dishes out loud | the model's own inline `[N]` citations in its generated text |
| the waiter forgetting one dish | the model failing to cite a claim it actually drew from a source |
| the register's own mechanical printed receipt | the numbered `Sources:` list, built from real `RankedChunk` data (§3) |
| the receipt never depending on the waiter's memory | the mechanical list never depending on parsing or trusting the model's own text |
| getting BOTH when the waiter does a good job | a well-formatted answer with correct inline citations AND the reliable list underneath |

### 2. Why here specifically
Directly connected to C3: this project has ALREADY measured this model failing simple, literal
instructions twice. Trusting inline citations as the ONLY source of truth would repeat that exact
risk for something more complex (a structural format) than the single-word instructions that
already failed — and C3's own live experiment above shows that risk isn't always realized, but
it's also not always absent (the simple test case that succeeded here never exercised the harder,
two-source disagreement pattern). The mechanical half costs nothing in terms of model reliability
— it's built entirely from data this system already has (the real, retrieved `RankedChunk`s),
independent of whatever the model actually wrote.

### What breaks if this changes
- **Inline citations only, no mechanical fallback (D3b, rejected):** if the model doesn't cite
  (or cites incorrectly), the reader has NO reliable way to know which documents actually informed
  the answer — exactly the failure mode `d4`'s "model gave no citations at all" case demonstrates.
- **Mechanical list only, no inline citation attempt at all (D3c, rejected):** simpler and fully
  reliable, but throws away the more USEFUL claim-level precision ("THIS specific sentence came
  from THIS specific source") that inline citations give when the model does comply.
- **Trust the model's inline citations to be ACCURATE (not just present):** `d4` only checks
  whether `[N]` tokens appear in the text — it does NOT verify the model cited the RIGHT source
  for each claim. That's a real, harder problem this design does not solve (worth naming as a
  genuine open limitation, not glossing over).

### [Interview relevance: medium-high — "defense in depth" / not trusting a single, potentially-unreliable mechanism for something important is a broadly valuable systems-design habit, applied here to LLM output specifically]

**Interview question:** *"How do you ensure a RAG answer's citations are reliable, given the LLM
might not follow your citation instructions perfectly?"*
**A strong answer covers:** don't rely on the model's own text as the only source of citation
truth — you already KNOW which chunks were retrieved and shown to the model, independent of
anything it generates, so a mechanical, data-driven citation list is always constructible; treat
the model's own inline citations as a best-effort enhancement, not the sole mechanism; and name the
harder, unsolved problem explicitly — verifying a citation is not just PRESENT but CORRECT (that
claim X really did come from source [2], not source [1]) needs something beyond simple string
matching, and isn't addressed by this design.

### 3. Minimal code that explains the concept
The complete script, [`d4_citation_strategy.py`](<../practice codes/phase 6/m3_prompt_template_deep_dive/d4_citation_strategy.py>) (run from the project root):
```python
# Minimal demo of C4: the hybrid citation strategy -- a mechanical source list is appended
# REGARDLESS of whether the model's own inline [N] citations were perfect, partial, or absent.
import re  # used only to detect which [N] citations the model's raw text actually contains

SOURCES = {  # the real, numbered source list this call's user prompt was built from (section 3)
    1: ("HR Record", "employees/Avery Lancaster.md"),
    2: ("Contract with GlobalRe Partners", "contracts/Contract with GlobalRe Partners for Rellm.md"),
}


def mechanical_source_list(sources):
    lines = [f"[{n}] {title} ({path})" for n, (title, path) in sources.items()]  # one line per source
    return "Sources:\n" + "\n".join(lines)  # a plain, reliable list -- no model involved at all


def add_citations_reliably(model_raw_text, sources):
    cited = set(int(n) for n in re.findall(r"\[(\d+)\]", model_raw_text))  # what the model DID cite
    print(f"  model's own raw text cited: {sorted(cited) or 'nothing'}")  # shows the model's real compliance
    return model_raw_text.strip() + "\n\n" + mechanical_source_list(sources)  # appended NO MATTER WHAT


PERFECT_REPLY = "Avery Lancaster is CEO [1]. However, Jennifer Rodriguez signed a contract as CEO [2]; the sources disagree."
PARTIAL_REPLY = "Avery Lancaster is CEO. However, another document names a different CEO as well."
NO_CITATION_REPLY = "The sources disagree about who the CEO is."

for label, reply in [("model cited everything correctly", PERFECT_REPLY),
                      ("model forgot to cite anything", PARTIAL_REPLY),
                      ("model gave no citations at all", NO_CITATION_REPLY)]:
    print(f"\n--- {label} ---")
    final_answer = add_citations_reliably(reply, SOURCES)  # runs the SAME reliable process every time
    print(final_answer)

print("\nIn all three cases, the reader ends up with a real, correct source list at the bottom --")
print("the mechanical fallback's reliability never depended on how well the model followed Rule 2.")
```
Real output:
```
--- model cited everything correctly ---
  model's own raw text cited: [1, 2]
Avery Lancaster is CEO [1]. However, Jennifer Rodriguez signed a contract as CEO [2]; the sources disagree.

Sources:
[1] HR Record (employees/Avery Lancaster.md)
[2] Contract with GlobalRe Partners (contracts/Contract with GlobalRe Partners for Rellm.md)

--- model forgot to cite anything ---
  model's own raw text cited: nothing
Avery Lancaster is CEO. However, another document names a different CEO as well.

Sources:
[1] HR Record (employees/Avery Lancaster.md)
[2] Contract with GlobalRe Partners (contracts/Contract with GlobalRe Partners for Rellm.md)

--- model gave no citations at all ---
  model's own raw text cited: nothing
The sources disagree about who the CEO is.

Sources:
[1] HR Record (employees/Avery Lancaster.md)
[2] Contract with GlobalRe Partners (contracts/Contract with GlobalRe Partners for Rellm.md)

In all three cases, the reader ends up with a real, correct source list at the bottom --
the mechanical fallback's reliability never depended on how well the model followed Rule 2.
```

### The exact text I wrote (`docs/prompts.md`)
> **Line 47:** "2. After any sentence or claim drawn from a source, cite it with the source's
> number in brackets, like [1]. If a claim draws on more than one source, cite all of them, like
> [1][3]."

This is the inline half: what the model is asked to attempt.

> **Lines 176-180:** "**The mechanical half of D3, not yet implementable without code:** whatever
> a caller of this design does with the model's raw text output, it should ALSO independently know
> the real, numbered source list it built (§3) — meaning a real implementation can always append a
> reliable 'Sources: [1] ..., [2] ...' list built from data, regardless of whether the model's
> inline `[N]` citations were precise."

This is the mechanical half: built from data the system already has, independent of the model.

**🎯 Interview-relevant lines:** the phrase "regardless of whether the model's inline `[N]`
citations were precise" on line 179 is the entire design philosophy of this concept, stated in one
sentence.

### Canonical docs
- [OWASP LLM09: Overreliance](https://genai.owasp.org/llmrisk/llm09-overreliance/) — the general
  risk category a purely model-dependent citation mechanism would fall into.
- Anthropic, [Increasing output consistency](https://docs.claude.com/en/docs/test-and-evaluate/strengthen-guardrails/increase-consistency) —
  general patterns for not fully trusting a single generation for a structural requirement.

### Micro-exercises
1. In `d4`, add a FOURTH case: the model cites a source number that DOESN'T EXIST in `SOURCES`
   (e.g., `[3]`). What does `add_citations_reliably` do with it? Should it do something different?
2. Modify `add_citations_reliably` to WARN (print a message) when the model's own citations don't
   match the full set of available sources, instead of silently ignoring the mismatch.
3. Using `re.findall`, write a check for whether the model cited sources in ASCENDING order
   (`[1]` before `[2]`) versus out of order. Does the current design care about citation order?
4. Design (on paper) a simple way to check whether a SPECIFIC sentence's claim actually matches
   the content of the source it cited — harder than just checking a `[N]` token is present.
5. Think about what would happen if `SOURCES` had 10 entries instead of 2. Would a reader still
   find the mechanical list useful, or does it need its own organization (grouping, summarizing)
   at that scale?

**How it works internally.** `add_citations_reliably` does two completely independent things:
(1) a cheap, read-only regex scan of the model's own text purely to REPORT what it did (never to
gate or alter the output), and (2) an unconditional string concatenation of a list built entirely
from `SOURCES` — a plain Python dict this code already had before the model was ever called. The
model's behavior can be anything at all; the second part's correctness doesn't depend on it.

### How to validate this yourself
Feed `add_citations_reliably` an intentionally empty or garbled `model_raw_text` (`""`, or random
unrelated text) and confirm the mechanical source list still appears, complete and correct, at the
bottom — the strongest possible proof that the reliable half truly doesn't depend on the model.

### How to visualize this
Draw two independent pipes feeding into one final document: pipe A is "the model's raw text" (thin,
sometimes has gaps — draw a few dashed sections in it); pipe B is "the mechanical source list"
(solid, unbroken, drawn directly from a small database icon, never touching pipe A at all). Both
pour into the same final "Answer shown to the reader" box.

**You should now be able to explain:** why depending on a single, potentially-unreliable mechanism
for something as important as citations is risky, and what "hybrid" specifically means in this
design's citation strategy.

---

## Consolidated end-to-end exercise: watch the whole thing work

1. **You already ran the real experiment.** Look back at C3's real result: zero-shot and few-shot
   tied on the simple case. Now design a HARDER version by hand (two disagreeing sources) and
   predict again before running it — this is the natural continuation of a prediction that didn't
   land the way the design's own reasoning expected.
2. **Trace §4's algorithm on the REAL corpus.** Pick one of the five labelled acceptance
   questions, run `hybrid.retrieve(question, 5)` (no Cohere needed) against the real corpus, read
   each chunk's real `approx_token_count`, and apply §4's algorithm by hand before writing any
   code. Then check your answer against `d2`'s own logic, adapted to your real numbers.
3. **Break C1 on purpose.** In `d0`, delete the grounding check from `grounded_answer` entirely
   (always fall through to a guess). What does the function now have in common with
   `ungrounded_answer`?
4. **Break C2 on purpose.** In `d3`, change `apply_real_template` to ALWAYS wrap with `system`,
   even when `system=None` (e.g., wrap it as `"[INST] None\n\n{prompt}[/INST]"`). What would this
   look like if it actually reached the real model?
5. **Break C4 on purpose.** In `d2`, set `BUDGET = 100` (smaller than even the median chunk).
   Trace what survives. Does the `or not kept` rule still guarantee at least one chunk, even one
   that's 12x over budget?
6. **Break C5 on purpose.** In `d4`, make `add_citations_reliably` return EARLY (skip the
   mechanical list) whenever the model's raw text already contains at least one `[N]`. Re-run the
   "model forgot to cite anything" case — does the reader now get a source list at all?
7. **Put it back together.** Undo every change from steps 3-6. Re-run all five scripts and confirm
   they match this document's own real, printed output.

The most important lesson in this deep dive isn't any single one of the five concepts — it's that
step 1 above actually happened during this document's own construction: a design decision's
justification was tested for real, for the first time, and the test came back more nuanced than
the argument that motivated it. That's not a flaw in the design (C5's mechanical fallback meant
nothing depended on the answer either way) — it's what "measure, don't assume" looks like when
applied to your own reasoning, not just to someone else's code or an external library's docs.
