# Minimal demo of C1: a Protocol describes SHAPE (which methods must exist), not lineage --
# any object with the right methods satisfies it, with zero inheritance and zero registration.
from typing import Protocol  # the tool that lets us describe a shape instead of a base class


class Greeter(Protocol):  # defines the SHAPE: anything with a greet(name) -> str method
    def greet(self, name: str) -> str: ...  # the one method this Protocol requires


class FormalGreeter:  # does NOT inherit from Greeter at all -- structural typing needs no base class
    def greet(self, name: str) -> str:  # matches the shape Greeter requires
        return f"Good day, {name}."  # a real, distinct implementation


class CasualGreeter:  # also does NOT inherit from Greeter
    def greet(self, name: str) -> str:  # also matches the shape
        return f"Hey {name}!"  # a completely different implementation, same shape


def announce(greeter: Greeter, name: str) -> None:  # accepts ANYTHING satisfying the Protocol
    print(greeter.greet(name))  # calls the one method the Protocol promises will exist


print("--- two unrelated classes, both satisfying the same Protocol ---")
announce(FormalGreeter(), "Dr. Rossi")  # a "real" implementation, exactly like RetrievalPipeline
announce(CasualGreeter(), "Sam")  # a "fake" implementation, exactly like a test double


class Silent:  # does NOT define greet() at all -- does not satisfy the Protocol's shape
    pass  # an empty class, nothing more


print("\n--- what happens if an object does NOT satisfy the Protocol ---")
try:
    announce(Silent(), "Anyone")  # this is a REAL runtime error, not a type-checker-only one
except AttributeError as exc:
    print(f"AttributeError: {exc}")  # Protocol checking is STATIC (mypy, before running); at
    # runtime, Python only discovers a missing method the instant it's actually called
