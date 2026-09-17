# Mutation testing in miniature: break the code on purpose, check a test notices.
import math


def validate(vector, dim=3):
    if len(vector) != dim:
        raise ValueError("wrong dimension")
    if not all(math.isfinite(x) for x in vector):
        raise ValueError("NaN")


def mutant_no_nan_check(vector, dim=3):     # someone "simplifies" the function
    if len(vector) != dim:
        raise ValueError("wrong dimension")


def weak_test(fn):
    """Only checks the happy path."""
    fn([0.0, 0.6, 0.8])
    return True


def strong_test(fn):
    """Also checks that bad input is REJECTED."""
    fn([0.0, 0.6, 0.8])
    try:
        fn([math.nan, 0.6, 0.8])
    except ValueError:
        return True
    return False


for test in (weak_test, strong_test):
    original = test(validate)
    mutant = test(mutant_no_nan_check)
    verdict = "catches the mutant" if original and not mutant else "MISSES the mutant"
    print(f"{test.__name__:<11}: original passes={original}, mutant passes={mutant} -> {verdict}")
