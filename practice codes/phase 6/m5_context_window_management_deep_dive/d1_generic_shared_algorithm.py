# Minimal demo of C2: one generic algorithm, shared by code working with two completely
# different types -- exactly how _greedy_best_fit serves both fit_context (DocumentChunk) and
# apply_budget (RankedChunk) without either one knowing the other's type exists.
from collections.abc import Sequence  # for clean type hints on the shared function
from typing import TypeVar  # the tool that lets one function work with ANY type, safely

T = TypeVar("T")  # a placeholder type -- "whatever type the caller actually gives us"


def best_fit(items: Sequence[T], sizes: Sequence[int], budget: int) -> list[T]:
    # works on items of ANY type T, as long as a parallel list of sizes is given alongside them
    kept: list[T] = []  # will hold survivors, of whatever type T turns out to be
    total = 0  # running total of "size" committed so far
    for item, size in zip(items, sizes):  # walks both lists together, item by item
        if total + size <= budget or not kept:  # fits, or this is the first item regardless
            kept.append(item)  # keeps this item, whatever type it is
            total += size  # updates the running total
    return kept  # hands back the survivors, still typed as T


# --- using it with plain strings ---
words = ["elephant", "cat", "dog"]
word_sizes = [8, 3, 3]
print("words kept:", best_fit(words, word_sizes, budget=11))

# --- using it with a totally different type: small dictionaries ---
boxes = [{"name": "big"}, {"name": "small"}]
box_sizes = [7, 2]
print("boxes kept:", best_fit(boxes, box_sizes, budget=8))

print("\nSame function, zero changes, two completely unrelated item types -- this is exactly")
print("how fit_context (DocumentChunk) and apply_budget (RankedChunk) share _greedy_best_fit.")
