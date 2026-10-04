# Minimal demo of C2: a pytest FIXTURE is a function pytest calls for you and hands the
# result to any test that asks for it by NAME. `scope` decides how often it re-runs.
# Run with: .venv/Scripts/python -m pytest <this file> -v -s
import pytest

calls = {"function_scoped": 0, "module_scoped": 0}  # counts how many times each fixture body actually ran


@pytest.fixture()  # default scope: "function" -- runs fresh for EVERY test that uses it
def function_scoped():
    calls["function_scoped"] += 1  # bump the counter every time this fixture's body runs
    print(f"\n  [setup]  function_scoped body ran (call #{calls['function_scoped']})")
    yield "fresh-every-time"  # this is the VALUE the test receives as its argument
    print("  [teardown] function_scoped cleanup")  # runs after the test, every time


@pytest.fixture(scope="module")  # runs ONCE per test file, its result is reused after that
def module_scoped():
    calls["module_scoped"] += 1  # bump the counter -- this should only ever reach 1
    print(f"\n  [setup]  module_scoped body ran (call #{calls['module_scoped']})")
    yield "computed-once"  # every test in this file gets the SAME object back
    print("  [teardown] module_scoped cleanup")  # runs once, after the LAST test uses it


def test_one(function_scoped, module_scoped):
    print(f"  test_one received: {function_scoped!r}, {module_scoped!r}")
    assert function_scoped == "fresh-every-time"


def test_two(function_scoped, module_scoped):
    print(f"  test_two received: {function_scoped!r}, {module_scoped!r}")
    assert module_scoped == "computed-once"  # the SAME value test_one saw, not a new one


def test_three_confirms_the_counts():
    # By now, two tests have asked for each fixture. function_scoped ran twice (once per
    # test); module_scoped ran once (shared across the whole file).
    assert calls == {"function_scoped": 2, "module_scoped": 1}
