# Minimal demo of C2: a frozen dataclass stops you REASSIGNING a field, but only a tuple
# (not a list) stops you MUTATING what a field points to. This is why RetrievalResult uses
# tuple[RankedChunk, ...], not list[RankedChunk].
from dataclasses import FrozenInstanceError, dataclass  # the decorator and its own error type


@dataclass(frozen=True)  # looks fully immutable at first glance
class LeakyResult:
    query: str  # the question
    chunks: list[str]  # a LIST -- looks like the obvious choice


@dataclass(frozen=True)  # the real project's choice
class SafeResult:
    query: str  # the question
    chunks: tuple[str, ...]  # a TUPLE -- no method exists to change it in place


leaky = LeakyResult("q", ["A", "B"])  # builds a "frozen" result holding a list
safe = SafeResult("q", ("A", "B"))  # builds a genuinely frozen result holding a tuple

try:  # tries to REPLACE the whole field on the leaky one
    leaky.chunks = ["X"]  # reassigning a field
except FrozenInstanceError as error:  # frozen=True DOES stop this
    print(f"Reassigning leaky.chunks:   blocked -- {error}")

leaky.chunks.append("SNUCK IN")  # does NOT reassign the field, just mutates the list IN PLACE
print(f"leaky.chunks after append:  {leaky.chunks}  <- frozen=True did NOT stop this!")

try:  # tries the same trick on the tuple-based one
    safe.chunks.append("SNUCK IN")  # tuples have no .append method at all
except AttributeError as error:  # so this fails a different way, for a different reason
    print(f"safe.chunks.append(...):   blocked -- {error}")

try:  # tries to reassign the field on the safe one too, for completeness
    safe.chunks = ("X",)  # reassigning a field
except FrozenInstanceError as error:  # frozen=True stops this one too
    print(f"Reassigning safe.chunks:   blocked -- {error}")

print(f"\nleaky ended up as: {leaky}")  # shows the corrupted "frozen" object
print(f"safe stayed as:    {safe}")  # shows the tuple-based one is genuinely untouched
