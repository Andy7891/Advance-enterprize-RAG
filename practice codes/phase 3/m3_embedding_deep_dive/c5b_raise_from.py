# Wrapping an error: add context (which chunks) without losing the original cause.
import traceback


class EmbeddingError(RuntimeError):
    pass


def server_call():
    raise ConnectionError("Failed to connect to Ollama")


def embed_one_batch(ids):
    try:
        server_call()
    except ConnectionError as exc:
        raise EmbeddingError(f"embedding failed for {ids}: {exc}") from exc


try:
    embed_one_batch(["company/about.md_0", "company/about.md_1"])
except EmbeddingError as err:
    print("message   :", err)
    print("__cause__ :", repr(err.__cause__))
    print("--- full traceback (note the 'direct cause' line) ---")
    print(traceback.format_exc().replace(__file__, "c5b_raise_from.py"))
