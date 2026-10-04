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
