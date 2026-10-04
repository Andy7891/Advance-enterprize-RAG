# Minimal demo of C4: a Protocol method needs ONE @overload per SHAPE the real code
# actually calls it with -- generate(..., stream=False) and generate(..., stream=True)
# return DIFFERENT types, so ONE signature cannot honestly describe both.
from typing import Literal, Protocol, overload  # the exact tools LLMClient itself uses


class Reply:  # stands in for ollama.GenerateResponse
    def __init__(self, text):
        self.text = text  # the one field this toy reply carries


class TwoShapeClient(Protocol):  # the REAL project's approach: one overload per shape
    @overload
    def ask(self, q: str, *, many: Literal[False] = False) -> Reply: ...  # ONE reply back

    @overload
    def ask(self, q: str, *, many: Literal[True]) -> list[Reply]: ...  # MANY replies back


class RealClient:  # a concrete class that actually satisfies TwoShapeClient
    def ask(self, q, *, many=False):
        if many:  # covers the "give me several" shape
            return [Reply(q.upper()), Reply(q.lower())]  # returns a LIST, matching many=True's promise
        return Reply(q.title())  # returns ONE Reply, matching many=False's promise


def use_client(client: TwoShapeClient) -> None:
    single = client.ask("hello", many=False)  # mypy expects a Reply here, and gets one
    print("many=False ->", type(single).__name__, single.text)  # .text works: single IS a Reply
    several = client.ask("hello", many=True)  # mypy expects a list[Reply] here, and gets one
    print("many=True  ->", type(several).__name__, [r.text for r in several])  # needs a loop: it's a LIST


use_client(RealClient())  # RealClient never mentions TwoShapeClient, yet satisfies it structurally

# Try this by hand: delete the SECOND @overload above (the many=True one) and re-run mypy on
# this file. It will now complain at the `client.ask("hello", many=True)` call site, because
# the Protocol never told it that shape returns a list -- exactly the gap LLMClient would have
# if it only declared stream=False and something tried calling stream=True through it.
