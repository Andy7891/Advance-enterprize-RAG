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
