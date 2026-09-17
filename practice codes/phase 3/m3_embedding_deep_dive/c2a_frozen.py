# What frozen=True, slots=True and tuple-vs-list actually protect against.
import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WithList:
    vector: list[float]


@dataclass(frozen=True, slots=True)
class WithTuple:
    vector: tuple[float, ...]


a = WithList(vector=[0.6, 0.8])
b = WithTuple(vector=(0.6, 0.8))

# 1. frozen: you cannot REPLACE a field
try:
    b.vector = (1.0, 0.0)
except dataclasses.FrozenInstanceError as e:
    print("1. reassign field  ->", type(e).__name__, e)

# 2. but frozen is shallow: the list INSIDE can still be edited in place
a.vector[0] = 999.0
print("2. list edited in place, no error ->", a.vector)

# 3. a tuple has no in-place edit at all
try:
    b.vector[0] = 999.0
except TypeError as e:
    print("3. tuple edit      ->", type(e).__name__, e)

# 4. slots: no accidental new attributes (typo protection, less memory)
try:
    object.__setattr__(b, "vectr", (0.0,))
except AttributeError as e:
    print("4. new attribute   ->", type(e).__name__, e)

# 5. frozen dataclasses are hashable and compare by value
print("5. equal by value:", WithTuple((0.6, 0.8)) == b, "| hashable:", hash(b) == hash(WithTuple((0.6, 0.8))))
