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
