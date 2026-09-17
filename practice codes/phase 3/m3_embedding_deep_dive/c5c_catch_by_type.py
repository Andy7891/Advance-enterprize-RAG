# Catch-all vs catch-by-type: which one hides our own bugs?
class EmbeddingError(RuntimeError):
    pass


def buggy_client():
    response = {"embedings": []}          # typo in OUR code, not a server problem
    return response["embeddings"]


def with_catch_all():
    try:
        return buggy_client()
    except Exception as exc:
        raise EmbeddingError(f"embedding failed: {exc!r}") from exc


def with_catch_by_type():
    try:
        return buggy_client()
    except (ConnectionError, TimeoutError) as exc:      # only failures the SERVER can cause
        raise EmbeddingError(f"embedding failed: {exc!r}") from exc


for fn in (with_catch_all, with_catch_by_type):
    try:
        fn()
    except Exception as e:
        print(f"{fn.__name__:<20} -> {type(e).__name__}: {e}")
