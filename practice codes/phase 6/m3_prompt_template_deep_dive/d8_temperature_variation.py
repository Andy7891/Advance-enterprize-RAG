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
