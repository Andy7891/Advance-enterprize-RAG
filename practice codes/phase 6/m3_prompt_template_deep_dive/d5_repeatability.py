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
