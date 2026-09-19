# Run with:  .venv/Scripts/python -m pytest "practice codes/phase 3/m6_retrieval_tests_deep_dive/demo_c5_fixture_scopes.py" -q -s -p no:cacheprovider
# Fixture scope decides HOW OFTEN setup runs; yield + finally decides that teardown ALWAYS runs.
import pytest

events = []


@pytest.fixture
def per_test():
    events.append("setup per_test")
    yield "fresh"
    events.append("teardown per_test")


@pytest.fixture(scope="session")
def expensive():
    events.append("SETUP expensive (once)")
    try:
        yield "shared"
    finally:
        events.append("TEARDOWN expensive (once, after the last test)")
        print("\n" + "\n".join(f"  {i + 1:>2}. {e}" for i, e in enumerate(events)))


def test_one(per_test, expensive):
    events.append("  run test_one")


def test_two(per_test, expensive):
    events.append("  run test_two (fails)")
    assert False, "a failing test must not skip teardown"


def test_three(per_test):
    events.append("  run test_three (does not ask for expensive)")
