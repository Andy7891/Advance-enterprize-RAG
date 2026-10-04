# Minimal demo of C1: a Protocol lets RetrievalPipeline accept ANY object with the right
# methods -- not just the real HybridRetriever/Reranker -- which is what makes it testable
# without a database, and what "duck typing, formalized" means in practice.
from typing import Protocol  # the tool that lets us describe a SHAPE instead of a class


class CanGreet(Protocol):  # describes anything with a .greet(name) -> str method
    def greet(self, name: str) -> str: ...  # the only thing CanGreet cares about


class Robot:  # a class that never inherits from CanGreet and never mentions it
    def greet(self, name: str, volume: str = "normal") -> str:  # has an EXTRA optional param
        return f"BEEP BOOP HELLO {name.upper()}"  # a robot-flavoured greeting


class Human:  # a completely different, unrelated class
    def greet(self, name: str) -> str:  # matches CanGreet's shape exactly
        return f"Hi {name}, nice to meet you"  # a human-flavoured greeting


class Cat:  # a class that does NOT satisfy CanGreet at all
    def meow(self) -> str:  # has no greet method whatsoever
        return "meow"  # the only thing a Cat can say


def introduce(greeter: CanGreet, name: str) -> str:  # only cares that greeter CAN greet
    return greeter.greet(name)  # calls the method, not caring what class greeter really is


print(introduce(Robot(), "Ada"))  # a Robot satisfies CanGreet, despite never mentioning it
print(introduce(Human(), "Ada"))  # so does a Human, despite being unrelated to Robot

try:  # tries the thing mypy would refuse to even let you write
    introduce(Cat(), "Ada")  # a Cat has no greet method at all
except AttributeError as error:  # Python only discovers this at runtime, the hard way
    print(f"Cat crashes at RUNTIME: {error}")  # mypy would have caught this before running

# mypy accepts Robot even though its greet() has an EXTRA optional parameter (volume) --
# a concrete method with MORE optional parameters still satisfies a Protocol that calls
# it with FEWER arguments. This is exactly the relationship between HybridSearcher's
# retrieve_with_scores(query, k) and the real HybridRetriever.retrieve_with_scores(query,
# k, vector_weight=None, graph_weight=None).
