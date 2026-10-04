# Minimal demo of C5: isolating a failure by testing progressively SMALLER pieces -- exactly
# the method used live in this project to prove a real Ollama timeout was NOT a bug in P6-M5's
# own code. Simulates a flaky "service" and walks through the same isolation process.
import random  # used only to make this toy "service" fail sometimes, like a real one might

random.seed(2)  # makes this demo's "flakiness" reproducible for anyone reading the output


def flaky_service(work_units):  # simulates something that sometimes fails under "load"
    if work_units > 5:  # pretend heavier calls are more likely to hit the problem
        raise TimeoutError(f"timed out doing {work_units} units of work")
    return f"done {work_units} units"  # succeeds when the work is small enough


def full_pipeline():  # the ORIGINAL, full-featured call -- lots of moving parts, like RAGPipeline
    step1 = flaky_service(work_units=8)  # retrieval-shaped work
    step2 = flaky_service(work_units=2)  # budgeting-shaped work (cheap, pure Python in reality)
    return step1, step2


print("--- layer 1: run the FULL pipeline ---")
try:
    print("result:", full_pipeline())
except TimeoutError as e:
    print(f"FAILED: {e}")  # a failure here alone doesn't say WHERE the problem is

print("\n--- layer 2: call just the FIRST step alone (removes everything else) ---")
try:
    print("result:", flaky_service(work_units=8))
except TimeoutError as e:
    print(f"FAILED: {e}  <- still fails, with NOTHING else involved")
    # this is the key moment: the SAME failure, with zero pipeline code in the loop

print("\n--- layer 3: shrink the work until it stops failing ---")
for units in [8, 6, 5, 3]:  # walks from "known to fail" down toward "known to succeed"
    try:
        result = flaky_service(work_units=units)
        print(f"work_units={units}: SUCCESS ({result})")
    except TimeoutError as e:
        print(f"work_units={units}: FAILED ({e})")

print("\nConclusion: the failure reproduces with ONE call, no pipeline, no other code --")
print("it's the service itself struggling under load, not a bug in the code calling it.")
