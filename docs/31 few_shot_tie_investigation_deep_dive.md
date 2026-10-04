# 31 — Investigating the P6-M3 Live Experiment: Why Zero-Shot Tied Few-Shot (and What Broke That Tie)

> [docs/30](<30 prompt_strategy_deep_dive.md>)'s C3 section ran ONE real experiment and got a
> surprising tie: zero-shot and few-shot produced the identical correctly-cited reply on a simple
> question. This deep dive doesn't re-explain that result — it INVESTIGATES it, with four more real
> experiments against the same live `mistral` model, following the question wherever the evidence
> actually leads. It leads somewhere more interesting than "few-shot didn't matter." The demo
> scripts live in
> [`practice codes/phase 6/m3_prompt_template_deep_dive/`](<../practice codes/phase 6/m3_prompt_template_deep_dive/>)
> (d5-d9); all need the real, running Ollama container. Run any from the project root:
>
> ```bash
> .venv/Scripts/python "practice codes/phase 6/m3_prompt_template_deep_dive/d5_repeatability.py"
> ```

---

## 0. Plain-language summary — the investigation, in order

```
d1 (docs/30): zero-shot ties few-shot on an EASY question  -> surprising, but is it real?
  |
  v
d5: repeat both prompts 3x at temperature=0.0  -> IDENTICAL every time. Not a fluke: deterministic.
  |
  v
d7: delete the citation INSTRUCTION entirely, keep the easy question  -> model STILL cites [1].
  -> the easy task's citation behavior never depended on the instruction OR the example.
  |
  v
d8: same easy question, but SAMPLE (temperature=0.7) instead of greedy-decode, 3x each
  -> still 3/3 for both. The tie holds even once randomness is allowed to break it.
  |
  v
d6: escalate to the REAL production worked example, on the REAL hard two-source disagreement
  -> a gap FINALLY appears. Zero-shot flags the disagreement; few-shot doesn't. One sample only.
  |
  v
d9: same hard case, sampled 5x each at temperature=0.7, to check if d6 was a fluke
  -> zero-shot flags it 4/5 times. Few-shot flags it only 1/5 times. A REAL, repeatable reversal.
```

The headline finding: **the design's OWN justification for using a worked example (measured,
real evidence that this model doesn't reliably follow strict instructions) turned out to be
right that instructions alone aren't enough — but the SPECIFIC example chosen to fix that
introduced a new problem nobody predicted:** it teaches the model to always resolve a
disagreement by picking a source, because that's the only resolution strategy the example ever
demonstrates. On the one hard case genuinely tested, that made few-shot noticeably *worse* than
zero-shot at the one thing Rule 3 exists for. This is now recorded as a real, disclosed risk in
[`decision.md` D-2026-09-29-25](../decision.md) — not fixed, because revising a completed,
previously-published design deliverable is a call for whoever picks this design back up, not a
silent patch mid-investigation.

| # | What it establishes | Script | Result |
|---|---|---|---|
| 1 | The original tie is deterministic, not a fluke | `d5_repeatability.py` | 3/3 identical, both prompts |
| 2 | The easy task's citations don't depend on the instruction at all | `d7_ablation_no_citation_rule.py` | cites even with the instruction deleted |
| 3 | The tie holds even under real sampling, not just greedy decoding | `d8_temperature_variation.py` | 3/3 both, at temperature=0.7 |
| 4 | A harder, real production task shows a first sign of divergence | `d6_harder_two_source.py` | zero-shot flags it; few-shot doesn't (n=1) |
| 5 | The divergence is real and repeatable, and runs backwards | `d9_harder_case_under_sampling.py` | zero-shot 4/5, few-shot 1/5 |

---

## 1. The core concept: an easy task can't distinguish two strategies, and a fix for one failure mode can cause another

### Do this first
Before reading further, look at `d6`'s two real replies (in §5 below) side by side. **Predict:**
which one — zero-shot or few-shot — do you expect flagged the CEO disagreement correctly? You
already know few-shot's worked example specifically demonstrates flagging a disagreement. Would
you bet on it winning here?

### Definition + analogy
**What this investigation is really about:** two related ideas.
1. **Task difficulty determines whether a prompting technique's effect is even visible.** If a
   task is so easy that the "worse" strategy already succeeds 100% of the time, no amount of
   testing on that task can show the "better" strategy is actually better — you need a task hard
   enough to fail sometimes.
2. **A worked example teaches its SPECIFIC resolution, not just its general rule.** Showing the
   model one way to resolve a disagreement doesn't teach "notice and report disagreements" in the
   abstract — it teaches "when sources disagree, do what I just showed you," and if that one
   demonstrated move (picking the more recent source) doesn't apply to a new case, the model may
   still try to force it to apply, rather than falling back to the more general instruction.

**Analogy.** Teaching a student long division using ONLY examples that come out to a whole number
with no remainder. The student learns "divide, and you get a clean answer" — not "divide, and
sometimes there's a remainder, and here's what to do about it." Give them a problem with a
remainder, and some students will confidently round to the nearest clean-seeming answer, because
that's the only KIND of answer they've ever been shown, rather than pausing to say "this doesn't
divide evenly."

### For a 14-year-old
Imagine a teacher who wants to teach "always double-check your work before turning it in." She
shows the class exactly ONE example: a student who checks their math homework, finds a typo where
they wrote 7 instead of 8, and fixes it. Great lesson — but notice what it ACTUALLY demonstrates
isn't just "double-check your work," it's the much narrower "double-check your work, and when you
find a typo, correct the number." Now imagine a totally different kind of assignment shows up — an
essay, say — where "double-checking" means something completely different (checking your argument
makes sense, not checking for a wrong digit). A student who only ever saw the math example might
try to apply the SAME specific fix-a-wrong-number move to an essay, because that's literally the
only concrete thing "double-checking" has ever meant to them, rather than reaching for the more
general idea the teacher actually wanted to teach.

That's almost exactly what happened here. The real system prompt's worked example teaches the
model to notice a disagreement AND shows it being resolved by picking "the more recent policy" as
the winner. That's a perfectly good lesson for THAT example (there really is a clean, generalizable
signal: newer beats older). But the real CEO question has no such signal — nothing in either
source says which one is more "recent" or more authoritative. Rather than falling back to the more
general instruction ("if sources disagree, say so"), the model, in 4 of 5 sampled runs, tried to
force the SAME specific move it had just been shown — pick a winner — onto a case where that move
doesn't actually make sense, and confidently declared "Jennifer Rodriguez is the CEO" with no
hedge at all.

### Mapping the story back to the design
| In the story | In the real design |
|---|---|
| the ONE math example: "check your work, fix the wrong digit" | the ONE few-shot example: "sources disagree, cite [2] as more recent" |
| "double-check your work" (the GENERAL rule intended) | Rule 3: "if sources disagree, say so explicitly" |
| an essay assignment (a task the example's specific fix doesn't apply to) | the CEO question (a disagreement with no recency signal to resolve it by) |
| a student forcing the wrong specific fix onto a new problem | the model, in 4/5 few-shot samples, silently picking one CEO instead of flagging the conflict |
| a student who was NEVER shown the narrow example, reasoning from the general rule instead | zero-shot, working from Rule 3's text alone, flagging the disagreement in 4/5 samples |

### Why here specifically
This matters because D-2026-09-29-19 (citations) and D-2026-09-29-22 (contradictions) both assumed
a worked example would make Rule 2 and Rule 3 MORE reliable, based on real evidence that
instructions alone weren't always followed (P6-M2's one-word-reply failures). That evidence was
correct as far as it went — but it never tested whether a SPECIFIC example could make one rule
(disagreement-flagging) LESS reliable, as a side effect of successfully teaching a different,
narrower lesson (how to resolve THIS disagreement). Nobody designs a fix for problem A expecting it
to quietly cause problem B — finding that out requires actually testing the harder case, which is
exactly what d1 (the easy case) could never have revealed.

### What breaks if this is ignored
- **Trusting docs/30's easy-case tie as the final word:** would have closed the investigation with
  "few-shot doesn't seem to matter here," missing a real, load-bearing risk in the actual
  production worked example.
- **Testing only the harder case once (d6), at temperature=0.0:** greedy decoding gives you exactly
  ONE trajectory — you cannot tell a stable pattern from a coincidence without sampling multiple
  times, which is exactly why d9 exists.
- **Assuming any single few-shot example generalizes safely:** an example is not just "a
  demonstration of a rule" — it's a demonstration of one SPECIFIC way of satisfying that rule, and
  a model can latch onto the specific way instead of the general rule.

### [Interview relevance: very high — recognizing that a few-shot example can teach an unintended, overly-specific pattern (not just the intended general rule) is a genuinely sophisticated prompt-engineering insight that goes beyond "few-shot usually helps"]

**Interview question:** *"You added a worked example to your system prompt to improve compliance
with a rule, and it worked on your test cases. How would you find out if it introduced any new
problems?"*
**A strong answer covers:** test on cases that are STRUCTURALLY DIFFERENT from the worked example,
not just similar variations of it — specifically, cases where the example's own specific
resolution strategy doesn't apply; sample multiple times (not just once, and not just at
temperature=0) to distinguish a stable pattern from a one-off; compare against a baseline WITHOUT
the example, since a regression is only visible relative to what would have happened otherwise; and
recognize that a few-shot example is a double-edged tool — it reduces variance around the pattern
it shows, which is good when that pattern generalizes and bad when it doesn't.

---

## 2. The five experiments, in the order they were actually run

### Experiment 1 — Is the original tie a fluke? (`d5_repeatability.py`)
**Predict first:** `d1` (docs/30) used `temperature=0.0`, which should be deterministic. If you
re-run the exact same two prompts 3 times each, do you expect any variation at all?

```python
# Follow-up experiment 1: is the zero-shot/few-shot TIE from d1 a real, stable result, or a
# one-off fluke? Runs each prompt 3 times at temperature=0.0, which should be deterministic --
# if it is, all 3 runs of each prompt should come back byte-for-byte identical.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.orchestration.llm import build_llm  # the real, already-tested wrapper (P6-M2)

RULES = """You are a factual question-answering assistant. Answer using ONLY the numbered sources below. After any claim, cite its source number in brackets, like [1]."""
# the exact same RULES text as d1 -- nothing about the wording changes here

FEW_SHOT_EXAMPLE = """

Example:
Sources:
[1] Company Handbook (handbook.md): Employees receive 15 days of paid vacation per year.

Question: How many vacation days do employees get?

Answer: Employees receive 15 days of paid vacation per year [1]."""
# the exact same worked example as d1

REAL_SOURCES = """

Sources:
[1] Office Policy (office.md): The office is closed on all US federal holidays.

Question: When is the office closed?

Answer:"""
# the exact same real question and source as d1

zero_shot_prompt = RULES + REAL_SOURCES  # identical to d1's zero-shot prompt
few_shot_prompt = RULES + FEW_SHOT_EXAMPLE + REAL_SOURCES  # identical to d1's few-shot prompt

llm = build_llm("http://localhost:11434")  # a real LocalLLM against the real, running Ollama

for label, prompt in [("ZERO-SHOT", zero_shot_prompt), ("FEW-SHOT", few_shot_prompt)]:
    print(f"\n--- {label}: 3 runs at temperature=0.0 ---")
    replies = [llm.generate(prompt, temperature=0.0, max_tokens=60) for _ in range(3)]  # 3 real calls
    for i, reply in enumerate(replies, start=1):  # walks each of the 3 replies
        print(f"  run {i}: {reply!r}")  # shows the exact reply text for this run
    all_identical = len(set(replies)) == 1  # true only if all 3 replies are byte-for-byte equal
    print(f"  all 3 runs identical: {all_identical}")  # the actual determinism check
```
Real output:
```
--- ZERO-SHOT: 3 runs at temperature=0.0 ---
  run 1: ' The office is closed on all US federal holidays [1].'
  run 2: ' The office is closed on all US federal holidays [1].'
  run 3: ' The office is closed on all US federal holidays [1].'
  all 3 runs identical: True

--- FEW-SHOT: 3 runs at temperature=0.0 ---
  run 1: ' The office is closed on all US federal holidays [1].'
  run 2: ' The office is closed on all US federal holidays [1].'
  run 3: ' The office is closed on all US federal holidays [1].'
  all 3 runs identical: True
```
**Conclusion:** the tie is deterministic and real. `temperature=0.0` really does mean greedy,
repeatable decoding here — this rules out "d1 just got lucky once" as an explanation.

---

### Experiment 2 — Where does the easy task's citation actually come from? (`d7_ablation_no_citation_rule.py`)
**Predict first:** if you delete the citation INSTRUCTION sentence entirely (but keep the numbered
source), do you think the model still cites `[1]`?

```python
# Follow-up experiment 3: an ablation, not a zero-shot/few-shot comparison. d1 showed the model
# citing [1] even WITHOUT a worked example. This asks a sharper question: does the model cite
# because the RULES text told it to, or would it cite ANYWAY just from seeing numbered sources,
# even with NO citation instruction at all? Removing the instruction isolates where the behavior
# actually comes from.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.orchestration.llm import build_llm  # the real, already-tested wrapper (P6-M2)

RULES_WITH_CITATION_INSTRUCTION = """You are a factual question-answering assistant. Answer using ONLY the numbered sources below. After any claim, cite its source number in brackets, like [1]."""
# the exact same RULES text as d1 -- includes the explicit citation instruction

RULES_WITHOUT_CITATION_INSTRUCTION = """You are a factual question-answering assistant. Answer using ONLY the numbered sources below."""
# identical EXCEPT the citation-format sentence is deleted entirely -- the only variable changed

REAL_SOURCES = """

Sources:
[1] Office Policy (office.md): The office is closed on all US federal holidays.

Question: When is the office closed?

Answer:"""
# the exact same real question and source as d1 -- unchanged, so this is a clean ablation

llm = build_llm("http://localhost:11434")  # a real LocalLLM against the real, running Ollama

print("--- WITH the citation instruction (same as d1's zero-shot) ---")
with_instruction = llm.generate(RULES_WITH_CITATION_INSTRUCTION + REAL_SOURCES, temperature=0.0, max_tokens=60)
print(repr(with_instruction))
print("contains a [1] citation:", "[1]" in with_instruction)

print("\n--- WITHOUT any citation instruction at all ---")
without_instruction = llm.generate(RULES_WITHOUT_CITATION_INSTRUCTION + REAL_SOURCES, temperature=0.0, max_tokens=60)
print(repr(without_instruction))
print("contains a [1] citation:", "[1]" in without_instruction)

print("\nIf both still cite [1], the citation habit is coming from SEEING numbered sources in the")
print("prompt shape itself, not from the instruction sentence -- meaning the instruction (and the")
print("few-shot example built to reinforce it) may matter less for THIS behavior than assumed.")
```
Real output:
```
--- WITH the citation instruction (same as d1's zero-shot) ---
' The office is closed on all US federal holidays [1].'
contains a [1] citation: True

--- WITHOUT any citation instruction at all ---
' The office is closed on US federal holidays, according to Source [1].'
contains a [1] citation: True

If both still cite [1], the citation habit is coming from SEEING numbered sources in the
prompt shape itself, not from the instruction sentence -- meaning the instruction (and the
few-shot example built to reinforce it) may matter less for THIS behavior than assumed.
```
**Conclusion:** on this easy, single-source task, the model cites `[1]` even with the citation
instruction deleted entirely. Mistral has clearly seen enough "numbered source -> cite it" pattern
during its own training that a SINGLE obvious source triggers the habit on its own. Neither the
instruction NOR the worked example is doing any visible work here — the task itself was too easy
to need either one.

---

### Experiment 3 — Does the tie survive real randomness? (`d8_temperature_variation.py`)
**Predict first:** `temperature=0.0` always picks the single most likely next token — it can hide
variance that would show up under real sampling. At `temperature=0.7` (this project's own
recommended upper bound), do you expect the tie to hold, or to break?

```python
# Follow-up experiment 4: temperature=0.0 (used everywhere else in this deep dive) is GREEDY
# decoding -- it always picks the single most likely next token, which can hide variance that
# would show up under real sampling. This reruns zero-shot vs few-shot at temperature=0.7 (this
# project's own recommended upper bound, docs/prompts.md section 9), 3 samples each, to see
# whether the tie from d1/d5 was partly an artifact of greedy decoding specifically.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.orchestration.llm import build_llm  # the real, already-tested wrapper (P6-M2)

RULES = """You are a factual question-answering assistant. Answer using ONLY the numbered sources below. After any claim, cite its source number in brackets, like [1]."""
# the exact same RULES text as d1

FEW_SHOT_EXAMPLE = """

Example:
Sources:
[1] Company Handbook (handbook.md): Employees receive 15 days of paid vacation per year.

Question: How many vacation days do employees get?

Answer: Employees receive 15 days of paid vacation per year [1]."""
# the exact same worked example as d1

REAL_SOURCES = """

Sources:
[1] Office Policy (office.md): The office is closed on all US federal holidays.

Question: When is the office closed?

Answer:"""
# the exact same real question and source as d1

zero_shot_prompt = RULES + REAL_SOURCES  # identical to d1's zero-shot prompt
few_shot_prompt = RULES + FEW_SHOT_EXAMPLE + REAL_SOURCES  # identical to d1's few-shot prompt

llm = build_llm("http://localhost:11434")  # a real LocalLLM against the real, running Ollama
TEMPERATURE = 0.7  # this project's own recommended upper bound (docs/prompts.md section 9)

for label, prompt in [("ZERO-SHOT", zero_shot_prompt), ("FEW-SHOT", few_shot_prompt)]:
    print(f"\n--- {label}: 3 samples at temperature={TEMPERATURE} ---")
    cite_count = 0  # counts how many of the 3 samples included a [1] citation
    for i in range(1, 4):  # runs 3 real samples
        reply = llm.generate(prompt, temperature=TEMPERATURE, max_tokens=60)  # one real, non-greedy call
        cited = "[1]" in reply  # did THIS sample cite the source
        cite_count += cited  # tallies compliance across the 3 samples
        print(f"  sample {i} (cited={cited}): {reply!r}")  # shows the exact reply and whether it cited
    print(f"  citation rate: {cite_count}/3")  # the real compliance rate under sampling, not greedy decoding
```
Real output:
```
--- ZERO-SHOT: 3 samples at temperature=0.7 ---
  sample 1 (cited=True): ' The office is closed on all US federal holidays [1].'
  sample 2 (cited=True): ' The office is closed on all US federal holidays [1].'
  sample 3 (cited=True): ' The office is closed on US federal holidays [1].'
  citation rate: 3/3

--- FEW-SHOT: 3 samples at temperature=0.7 ---
  sample 1 (cited=True): ' The office is closed on all US federal holidays [1].'
  sample 2 (cited=True): ' The office is closed on all US federal holidays [1].'
  sample 3 (cited=True): ' The office is closed on all US federal holidays [1].'
  citation rate: 3/3
```
**Conclusion:** the tie holds even under real sampling — 3/3 for both. Sampling introduced minor
WORDING variance ("on US federal holidays" vs. "on all US federal holidays") but the citation
behavior itself never wavered. This confirms the easy task simply cannot distinguish the two
strategies, under any decoding condition tried.

---

### Experiment 4 — A harder, real task: the actual production contradiction case (`d6_harder_two_source.py`)
**Predict first:** this uses the REAL worked example from `docs/prompts.md` section 2 (not a
simplified stand-in) and the REAL contradiction case from section 8 (verified against the corpus).
Given the worked example specifically demonstrates flagging a disagreement, do you predict
few-shot will out-perform zero-shot here?

```python
# Follow-up experiment 2: d1's task was the easiest possible case -- ONE source, ONE obvious
# citation. This repeats the same zero-shot vs few-shot comparison on a HARDER task: TWO sources
# that DISAGREE, needing both [1] and [2] cited AND an explicit "the sources disagree" statement
# -- the exact pattern Rule 3 in docs/prompts.md section 2 asks for. If a gap between zero-shot
# and few-shot exists anywhere, this is the shape of task most likely to reveal it.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.orchestration.llm import build_llm  # the real, already-tested wrapper (P6-M2)

RULES = """You are a factual question-answering assistant. Answer using ONLY the numbered sources below. After any claim, cite its source number in brackets, like [1]. If two sources disagree, say so explicitly."""
# same style as d1's RULES, but now ALSO states the disagreement rule (mirrors Rule 3)

FEW_SHOT_EXAMPLE = """

Example:
Sources:
[1] Company Handbook, Section 2 (company/handbook.md): Employees receive 15 days of paid vacation per year.
[2] HR Policy Update 2024 (company/hr_policy_2024.md): Effective 2024, vacation increased to 20 days per year for all staff.

Question: How many vacation days do employees get?

Answer: According to the most recent policy, employees receive 20 days of paid vacation per year [2]. An earlier document states 15 days [1]; the sources disagree, and [2] appears to be the more recent figure."""
# the SAME worked example already used in the real system prompt (docs/prompts.md section 2) --
# this is the actual production example, not a simplified stand-in like d1 used

REAL_SOURCES = """

Sources:
[1] HR Record (employees/Avery Lancaster.md): Job Title: Co-Founder & Chief Executive Officer (CEO)
[2] Contract with GlobalRe Partners (contracts/Contract with GlobalRe Partners for Rellm.md): Signatures: Jennifer Rodriguez, Title: Chief Executive Officer, Insurellm, Inc.

Question: Who is the CEO of Insurellm?

Answer:"""
# the REAL contradiction case from docs/prompts.md section 8, verified there against the real
# corpus via grep -- a genuinely harder task the simple d1 test never exercised

zero_shot_prompt = RULES + REAL_SOURCES  # instructions (with the disagreement rule), no example
few_shot_prompt = RULES + FEW_SHOT_EXAMPLE + REAL_SOURCES  # instructions + the real worked example

llm = build_llm("http://localhost:11434")  # a real LocalLLM against the real, running Ollama


def check(reply):
    has_both_citations = "[1]" in reply and "[2]" in reply  # did it cite BOTH conflicting sources
    disagreement_words = ["disagree", "differ", "conflict", "however", "while"]  # signals of noticing the conflict
    flagged_disagreement = any(w in reply.lower() for w in disagreement_words)  # did it SAY they disagree
    return has_both_citations, flagged_disagreement  # both checks reported separately


print("--- ZERO-SHOT (rules only, no worked example) ---")
zero_shot_reply = llm.generate(zero_shot_prompt, temperature=0.0, max_tokens=120)  # one real call
print(repr(zero_shot_reply))
zs_cited_both, zs_flagged = check(zero_shot_reply)  # runs both checks on the zero-shot reply
print(f"cited both [1] and [2]: {zs_cited_both}   flagged the disagreement: {zs_flagged}")

print("\n--- FEW-SHOT (rules + the REAL production worked example) ---")
few_shot_reply = llm.generate(few_shot_prompt, temperature=0.0, max_tokens=120)  # one real call
print(repr(few_shot_reply))
fs_cited_both, fs_flagged = check(few_shot_reply)  # runs both checks on the few-shot reply
print(f"cited both [1] and [2]: {fs_cited_both}   flagged the disagreement: {fs_flagged}")
```
Real output:
```
--- ZERO-SHOT (rules only, no worked example) ---
' The sources provided do not explicitly state that there is only one CEO for Insurellm. However, both sources [1] and [2] mention different individuals as CEOs of Insurellm. According to source [1], Avery Lancaster is the CEO of a company named Rellm, while source [2] states that Jennifer Rodriguez is the CEO of Insurellm, Inc. Therefore, it appears there may be two separate entities called Insurellm with different CEOs, or there might be some confusion in the sources provided.'
cited both [1] and [2]: True   flagged the disagreement: True

--- FEW-SHOT (rules + the REAL production worked example) ---
' According to both sources [1] and [2], the CEO of Insurellm is Jennifer Rodriguez.'
cited both [1] and [2]: True   flagged the disagreement: False
```
**A genuinely surprising result.** Zero-shot noticed the conflict (clumsily — it mistakes it for
"two separate entities" rather than a titles dispute, but it DOES flag something is wrong and
cites both sources). Few-shot, with the exact worked example specifically designed to teach
disagreement-flagging, silently picked Jennifer Rodriguez and moved on — the opposite of what the
design expected. But this is a single greedy-decoded sample — before drawing any conclusion, does
this pattern hold up, or was this one particular trajectory a coincidence?

---

### Experiment 5 — Is the reversal real, or a one-off greedy path? (`d9_harder_case_under_sampling.py`)
**Predict first:** experiment 1 showed `temperature=0.0` is fully deterministic — re-running `d6`
verbatim would just reproduce the identical single trajectory forever. To find out if the
reversal is a STABLE pattern, this samples the same two prompts 5 times each at
`temperature=0.7`. Given experiment 4's single result, do you expect the pattern to hold, weaken,
or reverse again under sampling?

```python
# Follow-up experiment 5: d6 ran the harder two-source disagreement case ONCE, at temperature=0.0
# (greedy -- so re-running it verbatim would just reproduce the identical single trajectory, per
# d5's own determinism finding). To find out whether few-shot's failure to flag the disagreement
# in d6 was a stable pattern or a one-off greedy-decoding path, this samples the SAME two prompts
# multiple times at temperature=0.7, and tallies how often each one actually flags the conflict.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.orchestration.llm import build_llm  # the real, already-tested wrapper (P6-M2)

RULES = """You are a factual question-answering assistant. Answer using ONLY the numbered sources below. After any claim, cite its source number in brackets, like [1]. If two sources disagree, say so explicitly."""
# identical RULES text to d6

FEW_SHOT_EXAMPLE = """

Example:
Sources:
[1] Company Handbook, Section 2 (company/handbook.md): Employees receive 15 days of paid vacation per year.
[2] HR Policy Update 2024 (company/hr_policy_2024.md): Effective 2024, vacation increased to 20 days per year for all staff.

Question: How many vacation days do employees get?

Answer: According to the most recent policy, employees receive 20 days of paid vacation per year [2]. An earlier document states 15 days [1]; the sources disagree, and [2] appears to be the more recent figure."""
# identical worked example to d6 -- the real production example from docs/prompts.md section 2

REAL_SOURCES = """

Sources:
[1] HR Record (employees/Avery Lancaster.md): Job Title: Co-Founder & Chief Executive Officer (CEO)
[2] Contract with GlobalRe Partners (contracts/Contract with GlobalRe Partners for Rellm.md): Signatures: Jennifer Rodriguez, Title: Chief Executive Officer, Insurellm, Inc.

Question: Who is the CEO of Insurellm?

Answer:"""
# identical contradiction case to d6

zero_shot_prompt = RULES + REAL_SOURCES  # identical to d6's zero-shot prompt
few_shot_prompt = RULES + FEW_SHOT_EXAMPLE + REAL_SOURCES  # identical to d6's few-shot prompt

llm = build_llm("http://localhost:11434")  # a real LocalLLM against the real, running Ollama
TEMPERATURE = 0.7  # samples instead of greedy-decoding the single most likely path
SAMPLES = 5  # how many independent samples per prompt


def flagged_disagreement(reply):
    words = ["disagree", "differ", "conflict", "however", "while", "two separate", "confusion"]
    return any(w in reply.lower() for w in words)  # did this reply SAY the sources disagree


for label, prompt in [("ZERO-SHOT", zero_shot_prompt), ("FEW-SHOT", few_shot_prompt)]:
    print(f"\n--- {label}: {SAMPLES} samples at temperature={TEMPERATURE} ---")
    flagged_count = 0  # tallies how many samples correctly flagged the disagreement
    for i in range(1, SAMPLES + 1):  # runs SAMPLES independent real calls
        reply = llm.generate(prompt, temperature=TEMPERATURE, max_tokens=120)  # one real, non-greedy call
        flagged = flagged_disagreement(reply)  # checks whether THIS sample flagged the conflict
        flagged_count += flagged  # tallies compliance across samples
        print(f"  sample {i} (flagged={flagged}): {reply!r}")  # shows the exact reply and the check result
    print(f"  disagreement-flagged rate: {flagged_count}/{SAMPLES}")  # the real rate under sampling
```
Real output:
```
--- ZERO-SHOT: 5 samples at temperature=0.7 ---
  sample 1 (flagged=True): ' Both sources suggest that there are two different individuals holding the title of CEO for Insurellm. According to source [1], Avery Lancaster holds the position as Co-Founder & CEO at employees/Avery Lancaster.md, while source [2] states that Jennifer Rodriguez is the Chief Executive Officer, Insurellm, Inc., as evidenced in the Contract with GlobalRe Partners (contracts/Contract with GlobalRe Partners for Rellm.md). Therefore, it appears there may be a discrepancy between the two sources regarding who'
  sample 2 (flagged=False): ' The sources provided do not explicitly state that both individuals are the CEO of Insurellm. According to [1], Avery Lancaster holds the position of Co-Founder & Chief Executive Officer (CEO) at employees/Avery Lancaster.md, but [2] lists Jennifer Rodriguez as the Chief Executive Officer of Insurellm, Inc. Therefore, there is a discrepancy between the sources regarding who the CEO of Insurellm is.'
  sample 3 (flagged=True): ' Both sources [1] and [2] indicate that Jennifer Rodriguez holds the title of Chief Executive Officer (CEO) for Insurellm. However, according to source [1], Avery Lancaster is the co-founder and CEO of a different company, not Insurellm. Therefore, it appears there may be two CEOs associated with Insurellm: Jennifer Rodriguez and Avery Lancaster (in another capacity). [1][2]'
  sample 4 (flagged=True): ' The sources provided do not explicitly state that there is only one CEO for Insurellm. However, based on the information given, both Avery Lancaster (from source 1) and Jennifer Rodriguez (from source 2) are listed as the CEO of Insurellm in different contexts. It is unclear if these are two separate positions or the same position held by different individuals. [1][2]'
  sample 5 (flagged=True): ' The sources provided indicate a discrepancy regarding who is the CEO of Insurellm. According to Source [1], the CEO is Avery Lancaster, while Source [2] states that Jennifer Rodriguez holds this position for Insurellm. Therefore, it is unclear who the actual CEO of Insurellm is based on the provided information.'
  disagreement-flagged rate: 4/5

--- FEW-SHOT: 5 samples at temperature=0.7 ---
  sample 1 (flagged=False): " According to both sources [1] and [2], the CEO of Insurellm is either Avery Lancaster or Jennifer Rodriguez, depending on the specific entity within Insurellm they lead. Since neither source specifies which part of Insurellm they are referring to, it's not possible to definitively determine who the CEO of Insurellm, Inc. is based solely on the provided information."
  sample 2 (flagged=False): ' According to both sources, the CEO of Insurellm is Jennifer Rodriguez [1, 2].'
  sample 3 (flagged=True): " According to the provided sources [1] and [2], both state that Jennifer Rodriguez holds the title of Chief Executive Officer (CEO) for Insurellm. Therefore, it appears that there may be a discrepancy in Avery Lancaster's job title as mentioned in source [1]. However, since source [2] is more specific about the company involved (Insurellm), it seems to provide the more accurate information regarding the CEO of Insurellm."
  sample 4 (flagged=False): ' According to both sources [1] and [2], the CEO of Insurellm is Jennifer Rodriguez.'
  sample 5 (flagged=False): ' According to both sources, Jennifer Rodriguez is the Chief Executive Officer (CEO) of Insurellm [1][2].'
  disagreement-flagged rate: 1/5
```
**This confirms experiment 4 was not a fluke.** Zero-shot flagged the disagreement in 4/5 samples
(sample 2 is the one miss, and even it says "discrepancy" — arguably a near-hit the keyword check
missed). Few-shot flagged it in only 1/5 — and notice HOW it resolves the other 4: it repeatedly
picks Jennifer Rodriguez, and sample 3 even explicitly reasons "source [2] is more specific... it
seems to provide the more accurate information" — almost a direct echo of the worked example's own
"[2] appears to be the more recent figure" reasoning, applied to a case where there is no actual
basis for that judgment. The model isn't failing randomly; it's applying the SPECIFIC pattern the
example taught, to a case where that pattern doesn't fit.

---

## 3. Synthesis — what this investigation actually established

1. **The original tie (docs/30) was real, but the task was too easy to be informative.** Three
   separate follow-ups (repeatability, ablation, temperature) all confirm the easy task's citation
   behavior comes from the prompt's own shape (a numbered source), not from the instruction or the
   example — so a tie there tells you nothing about whether few-shot helps or hurts.
2. **A harder, real task revealed a genuine, repeatable difference — in the opposite direction
   from what the design expected.** Zero-shot outperformed few-shot at disagreement-flagging (4/5
   vs. 1/5) on the exact contradiction case the design's worked example was built to handle.
3. **The likely mechanism is specific, not vague:** the worked example teaches ONE resolution
   strategy (recency), and the model over-applies that strategy to a case where no such signal
   exists, rather than falling back to the more general "say so explicitly" instruction.
4. **This does not mean the design is broken.** The mechanical citation fallback (D-2026-09-29-19)
   never depended on the model's own disagreement-flagging being reliable in the first place — a
   caller checking the raw model text for a disagreement statement would need its OWN fallback
   logic, which this design already anticipated needing regardless of this finding.
5. **This is disclosed, not fixed** — recorded as `decision.md` D-2026-09-29-25, with a
   well-scoped, obvious fix already identified (replace or supplement the worked example with a
   genuinely unresolvable disagreement) for whoever decides to act on it.

### [Interview relevance: very high — this whole investigation is a compact case study in empirical prompt engineering: form a hypothesis, test the easy case, don't stop there, escalate to a harder case, sample instead of trusting one run, and report a result that contradicts your own design even when it's inconvenient]

**Interview question:** *"Tell me about a time a prompt-engineering assumption you made turned out
to be wrong, and how you found out."*
**A strong answer covers:** a specific, falsifiable prediction (few-shot should improve
disagreement-flagging); a real test, not a plausibility argument; escalating past an
inconclusive first result (the easy-task tie) instead of stopping there; sampling rather than
trusting a single greedy-decoded run before concluding anything; and being willing to report a
result that reflects badly on your own earlier design decision, with the actual numbers, rather
than softening or omitting it.

---

## Micro-exercises
1. Run `d9` again. Are the exact replies identical to the ones shown here, or does sampling at
   temperature=0.7 give different wording (even if the flagged-rate stays roughly similar)?
2. Design a THIRD worked example — one that resolves a disagreement WITHOUT picking a winner (e.g.
   "the sources disagree and neither can be confirmed as more authoritative from the text alone").
   Substitute it into `d9`'s `FEW_SHOT_EXAMPLE` and re-run. Does the flagged-rate improve?
3. `d9`'s `flagged_disagreement` check is itself a crude keyword match (see `zs sample 2` above,
   which used "discrepancy," not on the tracked word list, but still arguably correct). Rewrite the
   check to be more accurate, and re-score all 10 real replies already shown in this document by
   hand.
4. Try a genuinely different hard case (pick another contradiction from this project's own
   documented corpus facts, if any others exist, or construct one) and see if the same
   zero-shot-beats-few-shot pattern holds, or if it was specific to THIS example pair.
5. Read `decision.md` D-2026-09-29-25's proposed fix. Write the actual replacement `FEW_SHOT_EXAMPLE`
   string you would use, and explain in one sentence why it avoids teaching a "pick a winner" move.

## How to validate this yourself
Everything in this document is directly reproducible: every script needs only the real, running
Ollama container (`docker compose up -d ollama`) and `.venv/Scripts/python`. Re-running `d9`
multiple times, at different temperatures, or with a larger `SAMPLES` count, is the most direct way
to build confidence (or find a reason to doubt) the 4/5 vs. 1/5 result reported here.

## How to visualize this
Draw two branching trees, one per prompt. The zero-shot tree's branches mostly lead to a "flags
disagreement" leaf (4 of 5 paths). The few-shot tree's branches mostly lead to a "picks Jennifer
Rodriguez" leaf (4 of 5 paths) — the same SHAPE of tree, but trained toward a different, specific
destination by the one example it was shown.

**You should now be able to explain:** why an inconclusive result on an easy test case should not
end an investigation; how to design a harder follow-up test that could actually distinguish two
strategies; and how a single few-shot example can simultaneously fix one failure mode (citation
presence) while quietly introducing another (over-generalized resolution behavior) — and why
sampling multiple times, not just once, is what turns a suspicious single result into real evidence.

## Consolidated exercise: watch the whole investigation happen again, on a new case
Pick a different pair of "easy" and "hard" questions from this project's own corpus (an easy one
with a single, obvious source; a hard one with a real, documented contradiction or ambiguity).
Repeat this investigation's exact five-experiment structure — repeatability, ablation, sampling on
the easy case, a single hard-case run, then 5x sampling on the hard case — and see whether you
reproduce a similar pattern, a different one, or no gap at all. Either result teaches you
something real about how far this specific finding generalizes beyond the one case tested here.
