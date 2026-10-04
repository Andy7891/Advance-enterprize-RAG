# Minimal demo of C1: turning free text into a KNOWN entity name needs whole-word
# matching, not naive substring matching. Uses the REAL functions from graph_retriever.py.
from src.retrieval.graph_retriever import _match_entities, _word_pattern

# a tiny, made-up "known names" table, in the SAME shape GraphRetriever._names_by_label uses
NAMES_BY_LABEL = {"Product": ["Carllm"], "Company": ["DriveSmart Insurance"]}


def naive_substring_match(query, names_by_label):
    # the WRONG way: checks if the name's letters simply appear anywhere in the text
    found = []
    for label, names in names_by_label.items():
        for name in names:
            if name.lower() in query.lower():
                found.append((label, name))
    return found


print("Query: 'What does Carllm do?'")
print("  naive substring match:", naive_substring_match("What does Carllm do?", NAMES_BY_LABEL))
print("  real _match_entities :", _match_entities("What does Carllm do?", NAMES_BY_LABEL))

print("\nQuery: 'Is Carllmish a real word?' (contains the letters C-a-r-l-l-m, but NOT the entity)")
print("  naive substring match:", naive_substring_match("Is Carllmish a real word?", NAMES_BY_LABEL))
print("  real _match_entities :", _match_entities("Is Carllmish a real word?", NAMES_BY_LABEL))
# the naive version wrongly "finds" Carllm inside Carllmish; the real one correctly does not

print("\nQuery: 'what does carllm do' (different case, no question mark)")
print("  real _match_entities :", _match_entities("what does carllm do", NAMES_BY_LABEL))
# case-insensitive: still finds it

print("\nInspect the compiled pattern for a two-word name:")
pattern = _word_pattern("DriveSmart Insurance")
print(" ", pattern.pattern)
# shows the actual regex: whitespace between the two words, word boundaries on both ends
