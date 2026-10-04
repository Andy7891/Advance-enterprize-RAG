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
