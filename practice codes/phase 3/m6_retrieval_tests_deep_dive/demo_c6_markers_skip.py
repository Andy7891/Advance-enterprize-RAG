# Run three ways and compare:
#   pytest demo_c6_markers_skip.py -q -rs -p no:cacheprovider            (everything: skips shown with reasons)
#   pytest demo_c6_markers_skip.py -q -rs -m "not slowlive"  ...          (deselected: not even considered)
#   pytest demo_c6_markers_skip.py -q -p no:cacheprovider --strict-markers (unregistered marker = error)
import pytest

print("\n[import] module-level code runs once, when pytest imports this file")
SERVICE_DOWN = "pretend service is not reachable"      # stand-in for live_services_down()

LIVE_MARKS = [pytest.mark.slowlive, pytest.mark.skipif(SERVICE_DOWN is not None, reason=SERVICE_DOWN)]


def live_test(function):
    for mark in LIVE_MARKS:
        function = mark(function)
    return function


def test_hermetic():
    assert 1 + 1 == 2


@live_test
def test_needs_service():
    raise AssertionError("never runs while the service is down")


@live_test
@pytest.mark.parametrize("question", ["a", "b"])
def test_needs_service_parametrised(question):
    raise AssertionError("never runs while the service is down")
