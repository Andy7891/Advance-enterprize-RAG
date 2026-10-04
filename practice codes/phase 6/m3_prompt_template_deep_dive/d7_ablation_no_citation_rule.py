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
