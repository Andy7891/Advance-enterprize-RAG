# Minimal demo of C5: translating between two different vocabularies for the SAME concept
# -- and what happens when a translation is simply forgotten (it is silently ignored, not
# an error), exactly like max_tokens -> num_predict in the real code.
OUR_REQUEST = {"max_tokens": 50, "temperature": 0.2}  # the vocabulary LocalLLM's own callers use
OLLAMA_NAMES = {"max_tokens": "num_predict", "temperature": "temperature"}  # the REAL translation table


def translate_correctly(request, name_map):
    return {name_map[key]: value for key, value in request.items()}  # renames every key it knows about


def translate_with_a_forgotten_mapping(request, name_map):
    return {name_map.get(key, key): value for key, value in request.items() if key in name_map}
    # BUT the caller below passes an INCOMPLETE name_map on purpose, to show what happens


print("correct translation:", translate_correctly(OUR_REQUEST, OLLAMA_NAMES))
# every key ends up under Ollama's own real name -- {'num_predict': 50, 'temperature': 0.2}

BROKEN_NAMES = {"temperature": "temperature"}  # "max_tokens" was simply forgotten from this table
sent_to_ollama = translate_with_a_forgotten_mapping(OUR_REQUEST, BROKEN_NAMES)
print("with a forgotten mapping:", sent_to_ollama)
print("max_tokens=50 silently VANISHED -- Ollama never even sees it, and raises NO error at all")
print("(a real API rejecting an unknown field would be safer than one that just ignores it)")
