# Where a failure leaves the index depends on WHEN writing starts.
# Two orders over 4 documents; the model fails on document 3 in both.
docs = ["about", "careers", "carllm", "culture"]
old_index = {d: "v1" for d in docs}


def embed(doc):
    if doc == "carllm":
        raise ConnectionError("model server went away")
    return "v2"


def embed_all_then_write(index):
    vectors = {d: embed(d) for d in docs}      # fails here, before any write
    index.update(vectors)


def embed_and_write_each(index):
    for d in docs:
        index[d] = embed(d)                     # writes as it goes


for strategy in (embed_all_then_write, embed_and_write_each):
    index = dict(old_index)
    try:
        strategy(index)
    except ConnectionError as e:
        print(f"{strategy.__name__:<22} failed ({e}) -> index: {index}")
