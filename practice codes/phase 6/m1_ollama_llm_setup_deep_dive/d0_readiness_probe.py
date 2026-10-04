# Minimal demo of C1: a "phone book" (listed) versus actually CALLING the number (working).
# Two of the three "employees" listed as available cannot actually take the call.
DIRECTORY = {  # a company's phone directory -- who is LISTED as reachable
    "Alice": "555-0101",  # a real, working line
    "Bob": "555-0102",  # a disconnected line -- still listed, though
    "Carol": "555-0103",  # a line that rings forever and nobody answers
}

WHO_ACTUALLY_ANSWERS = {"Alice"}  # the ground truth: only Alice will really pick up


def is_listed(name):
    return name in DIRECTORY  # checks the directory only -- no call is made


def actually_works(name):
    if name not in DIRECTORY:  # covers a name that isn't even in the directory
        return False, "not listed at all"  # reports why, without dialing anything
    if name not in WHO_ACTUALLY_ANSWERS:  # covers a listed name that won't really answer
        return False, "listed, but the call did not go through"  # reports the real outcome
    return True, "answered"  # reports a real, successful call


print("Checking the directory alone (what `ollama list` / GenerationClient.list() gives you):")
for name in DIRECTORY:  # goes through every listed name
    print(f"  {name:<6} listed: {is_listed(name)}")  # prints ONLY presence, nothing more

print("\nActually calling each one (what health_check's real generate() call gives you):")
for name in DIRECTORY:  # goes through every listed name again
    works, reason = actually_works(name)  # this time, really "dials" and checks
    print(f"  {name:<6} works: {works!s:<5} ({reason})")  # prints the real, only-provable-by-trying result
# Bob and Carol are BOTH listed, but only Alice is actually ready -- being in the
# directory never proves the call will be answered, only that a number exists for it.
