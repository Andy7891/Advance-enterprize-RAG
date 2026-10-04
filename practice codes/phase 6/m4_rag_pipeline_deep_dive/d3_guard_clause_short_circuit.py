# Minimal demo of C4: a guard clause / short-circuit -- when a case is ALREADY certain, handle
# it immediately, and never call an expensive or unreliable step "just in case" it agrees.
CALLS_MADE = []  # a stand-in for tracking real model calls, so we can prove one never happened


def unreliable_expensive_model(prompt):  # stands in for a real LLM call
    CALLS_MADE.append(prompt)  # records that a real call happened
    return "some plausible-sounding but not-guaranteed-correct answer"  # simulates model output


def answer_without_guard(question, sources):  # the naive version: ALWAYS calls the model
    if not sources:  # no sources at all
        prompt = f"{question} (no sources provided -- please say you don't know)"
    else:
        prompt = f"{question} using: {sources}"
    return unreliable_expensive_model(prompt)  # called EVERY time, even with zero sources


def answer_with_guard(question, sources):  # the guarded version: short-circuits on a certain case
    if not sources:  # this case is 100% certain -- there is nothing to answer from
        return "I don't have enough information to answer that."  # answered WITHOUT calling the model
    prompt = f"{question} using: {sources}"
    return unreliable_expensive_model(prompt)  # only called when there is real material to use


print("--- WITHOUT a guard clause ---")
CALLS_MADE.clear()  # resets the call counter before this scenario
print(answer_without_guard("Who is the CEO?", sources=[]))  # asks with zero sources
print(f"real model calls made: {len(CALLS_MADE)}")

print("\n--- WITH a guard clause ---")
CALLS_MADE.clear()  # resets the call counter before this scenario
print(answer_with_guard("Who is the CEO?", sources=[]))  # asks the same question, zero sources
print(f"real model calls made: {len(CALLS_MADE)}")

print("\nSame outward wording in THIS toy case -- but the guarded version never trusted the model")
print("to correctly notice 'no sources' on its own, and never paid for a call to find out.")
