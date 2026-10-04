# Minimal demo of C2: a random SAMPLE of checks catches most single-category bugs cheaply,
# but "cheaply" and "certainly" are different things -- a sample can always miss.
import random

random.seed(7)  # fixed seed, so this demo prints the SAME numbers every time it runs

POPULATION_SIZE = 76  # e.g. the 76 real documents in this corpus
BROKEN_ITEM = 41  # pretend exactly ONE document (out of 76) has a real bug in it


def spot_check(sample_size, trials=20000):
    # runs many pretend "spot checks", each picking `sample_size` random documents
    catches = 0
    for _ in range(trials):
        sample = random.sample(range(POPULATION_SIZE), sample_size)
        # a spot check "catches" the bug only if the ONE broken item happens to be sampled
        if BROKEN_ITEM in sample:
            catches += 1
    # the fraction of trials that happened to include the broken item
    return catches / trials


for size in (1, 5, 6, 20, 76):
    # print how often a spot check of THIS size would have caught the one real bug
    rate = spot_check(size)
    print(f"sample size {size:>3} of {POPULATION_SIZE}: catches the bug {rate:.1%} of the time")
