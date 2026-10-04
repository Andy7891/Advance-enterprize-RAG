# Minimal demo of C1: a "grounded" answerer only uses what's IN the sources; an "ungrounded"
# one falls back to whatever it already "knows" -- shown here with a fact that is TRUE but
# absent from the sources, so only the grounded answerer correctly refuses.
SOURCES = {  # the ONLY facts this toy assistant is given for this question
    1: "Insurellm was founded in 2015.",  # mentions the founding year, not who founded it
}
MODEL_OWN_TRAINING_KNOWLEDGE = {  # a stand-in for whatever a real LLM "remembers" from training
    "insurellm founder": "Avery Lancaster",  # happens to be TRUE, but it is not in SOURCES above
}


def ungrounded_answer(question):
    key = question.lower().replace("who founded insurellm?", "insurellm founder").strip("? ")
    if key in MODEL_OWN_TRAINING_KNOWLEDGE:  # checks its OWN memory, ignoring the sources entirely
        return MODEL_OWN_TRAINING_KNOWLEDGE[key]  # answers confidently, from memory
    return "I don't know."  # only refuses if even its own memory has nothing


def grounded_answer(question):
    for number, text in SOURCES.items():  # checks ONLY the provided sources, nothing else
        if "founded" in text and "by" in text:  # a crude check for "does this actually say WHO"
            return f"{text} [{number}]"  # would cite it, if the sources actually said who
    return "I don't have enough information in the provided documents to answer that."
    # refuses, because rule 1 says ONLY the sources count, and they don't name a founder


question = "Who founded Insurellm?"  # a question the SOURCES do not actually answer
print("SOURCES available to both answerers:", SOURCES)
print()
print("ungrounded_answer(...) ->", repr(ungrounded_answer(question)))  # confidently right, by luck
print("grounded_answer(...)   ->", repr(grounded_answer(question)))  # honestly refuses
print()
print("The ungrounded answer happens to be TRUE here -- that is exactly the danger: it is right")
print("for the wrong reason, and would be EQUALLY confident if its 'memory' were wrong instead.")
