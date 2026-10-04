# Minimal demo of C1: checking a system against ITS OWN prior output proves nothing new;
# checking it against an INDEPENDENT reading of the original source can catch a real miss.
# Recreates the shape of the real Rachel Martinez / Marketllm finding from P4-M7.
SOURCE_TEXT = "Supported product development for Marketllm marketplace."
# the ACTUAL closed list of real product names (from data/products/ file stems)
KNOWN_PRODUCTS = {"Markellm", "Carllm"}


def buggy_extractor(text):
    # "extracts" a product by looking for an EXACT closed-list name inside the text
    found = [p for p in KNOWN_PRODUCTS if p in text]
    # returns whatever exact matches it found (possibly none at all)
    return found


extracted = buggy_extractor(SOURCE_TEXT)
# prints what the extractor believes it found (an empty list, in this case)
print("extractor's own output:", extracted)


def self_check(extracted_before, extracted_after):
    # compares the system's output against ANOTHER RUN of the SAME system
    return extracted_before == extracted_after


# running the same buggy extractor twice on the same input, and comparing the two runs
again = buggy_extractor(SOURCE_TEXT)
print("self-check (compares output to itself):", self_check(extracted, again))
# self_check is happy: the SAME wrong answer, twice, still "matches"


def independent_check(source_text, extracted):
    # reads the RAW text directly, independent of the extractor's own beliefs
    mentions_something_llm_shaped = "llm" in source_text.lower()
    # flags a problem only if the text LOOKS like it names a product, but nothing came out
    looks_suspicious = mentions_something_llm_shaped and not extracted
    return not looks_suspicious


print("independent check (reads the raw text directly):", independent_check(SOURCE_TEXT, extracted))
# independent_check is NOT happy: it noticed a product-shaped word in the raw text that
# never made it into the extractor's output -- exactly what a human, reading the source
# document directly, would notice and the extractor's own self-check cannot.
