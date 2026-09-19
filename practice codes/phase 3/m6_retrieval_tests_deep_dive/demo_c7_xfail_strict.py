# Run with: pytest demo_c7_xfail_strict.py -q -rxX -p no:cacheprovider
import pytest

ranking_today = ["products/Lifellm.md_2", "contracts/TechDrive_1", "products/Lifellm.md_1"]
ranking_after_fix = ["products/Carllm.md_0", "products/Lifellm.md_2", "contracts/TechDrive_1"]


@pytest.mark.xfail(strict=True, reason="known miss: product names not separated by embeddings")
def test_known_miss_still_present():
    assert any(c.startswith("products/Carllm.md") for c in ranking_today)


@pytest.mark.xfail(strict=True, reason="known miss: product names not separated by embeddings")
def test_known_miss_after_the_fix_strict():
    assert any(c.startswith("products/Carllm.md") for c in ranking_after_fix)


@pytest.mark.xfail(reason="same, but not strict")
def test_known_miss_after_the_fix_not_strict():
    assert any(c.startswith("products/Carllm.md") for c in ranking_after_fix)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="only an assertion counts as the known miss")
def test_crash_is_not_the_known_miss():
    ranking_today[99]                                   # an IndexError is a different problem
