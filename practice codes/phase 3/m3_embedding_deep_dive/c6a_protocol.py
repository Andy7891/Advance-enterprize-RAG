# Structural typing: a class satisfies a Protocol by having the right method -- no inheritance.
from typing import Any, Protocol


class EmbedClient(Protocol):
    def embed(self, model: str = ..., input: list[str] = ..., truncate: bool | None = ...) -> Any: ...


class GoodFake:                      # does NOT inherit from EmbedClient
    def embed(self, model: str = "", input: list[str] = [], truncate: bool | None = None) -> Any:
        return {"embeddings": [[1.0] for _ in input]}


class WrongFake:                     # method named differently
    def encode(self, texts: list[str]) -> Any:
        return [[1.0] for _ in texts]


def use(client: EmbedClient) -> int:
    return len(client.embed(model="m", input=["a", "b"], truncate=False)["embeddings"])


print("GoodFake works at runtime:", use(GoodFake()))
try:
    use(WrongFake())                 # mypy flags this line BEFORE running
except AttributeError as e:
    print("WrongFake fails at runtime:", e)
