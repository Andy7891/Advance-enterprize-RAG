# Minimal demo of C5: GenerationClient describes only the TWO methods health_check uses,
# so a tiny fake can stand in for the real ollama.Client -- and the fake's generate() must
# match the real one's exact stream=Literal[False] typing, not just "some bool".
from types import SimpleNamespace  # builds tiny stand-in objects with named fields
from typing import Literal, Protocol  # the tools this file's own Protocol is built from


class GenerationClient(Protocol):  # the EXACT shape src/generation/setup.py declares
    def list(self): ...  # only asks: can you list what is pulled?

    def generate(self, model: str, prompt: str, *, stream: Literal[False] = False): ...
    # only asks: can you generate, called this exact way?


class FakeOllama:  # never inherits from GenerationClient, never mentions it
    def list(self):
        model = SimpleNamespace(model="mistral:latest")  # a fake "model" entry
        return SimpleNamespace(models=[model])  # shaped just enough like ollama.ListResponse

    def generate(self, model, prompt, *, stream=False):
        return SimpleNamespace(response=f"(fake reply to: {prompt!r})")  # a fake, instant reply


def probe(client: GenerationClient, model: str) -> str:  # only trusts the Protocol's checklist
    names = {entry.model for entry in client.list().models}  # reads what the client says is pulled
    if model not in names:  # covers the model never being listed
        return f"{model} is not listed"  # reports without ever calling generate()
    reply = client.generate(model=model, prompt="hi", stream=False)  # calls the SAME way health_check does
    return reply.response  # hands back whatever the client answered


print(probe(FakeOllama(), "mistral:latest"))  # a FakeOllama satisfies the Protocol structurally
print(probe(FakeOllama(), "llama3.2:latest"))  # a model this fake never listed

# Try this by hand: change FakeOllama.generate's signature to `stream: bool = False`
# (plain bool, not Literal[False]) and run `mypy` on this file. It still passes here,
# because THIS Protocol also declares plain-ish typing -- the real project's Protocol is
# checked against ollama.Client's own @overload-decorated generate(), which is stricter;
# see docs/28 C5 for the exact mismatch mypy caught on the real class.
