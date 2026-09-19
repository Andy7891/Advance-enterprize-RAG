# Four kinds of test double for one dependency: an embedding client with .embed().
from unittest.mock import Mock


def search(client, question, documents):
    """Tiny retriever: embed question + documents, return the best document."""
    vectors = client.embed(input=[question, *documents])["embeddings"]
    q, docs = vectors[0], vectors[1:]
    scores = [sum(a * b for a, b in zip(q, d)) for d in docs]
    return documents[scores.index(max(scores))]


docs = ["acme was founded in 2015", "the widget is blue"]


class Stub:
    """Returns a canned answer; knows nothing about the input."""
    def embed(self, input):
        return {"embeddings": [[1, 0], [1, 0], [0, 1]]}


class Fake:
    """A working, simplified implementation: 2-D 'model' = [has 'founded', has 'widget']."""
    def embed(self, input):
        return {"embeddings": [[float("founded" in t), float("widget" in t)] for t in input]}


class Spy(Fake):
    """A fake that also records how it was called."""
    def __init__(self):
        self.calls = []

    def embed(self, input):
        self.calls.append(list(input))
        return super().embed(input)


print("stub :", search(Stub(), "who founded acme?", docs), "(same answer for ANY question)")
print("stub :", search(Stub(), "what colour is the widget?", docs), "<- wrong, the stub cannot tell")
print("fake :", search(Fake(), "who founded acme?", docs))
print("fake :", search(Fake(), "what colour is the widget?", docs))

spy = Spy()
search(spy, "who founded acme?", docs)
print("spy  : recorded call ->", spy.calls)

mock = Mock()
mock.embed.return_value = {"embeddings": [[1, 0], [1, 0], [0, 1]]}
search(mock, "who founded acme?", docs)
mock.embed.assert_called_once_with(input=["who founded acme?", *docs])
print("mock : assert_called_once_with passed; calls =", mock.embed.call_args_list)
