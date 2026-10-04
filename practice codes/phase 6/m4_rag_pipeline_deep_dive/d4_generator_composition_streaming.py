# Minimal demo of C5: composing generators -- a generator function can walk ANOTHER generator's
# values one at a time, then yield something extra of its own once the inner one is exhausted.
def inner_stream():  # stands in for LLM.stream(): yields a few real fragments, lazily
    print("  [inner] producing fragment 1")  # printed only when this fragment is actually requested
    yield "Hello"
    print("  [inner] producing fragment 2")
    yield ", world"
    print("  [inner] producing fragment 3")
    yield "!"


def outer_stream():  # stands in for RAGPipeline.stream(): wraps inner_stream, adds one more value
    for fragment in inner_stream():  # walks every value inner_stream yields, one at a time
        yield fragment  # passes it straight through to whoever is consuming outer_stream
    yield " [Sources: 1]"  # after the inner generator is fully exhausted, yields ONE more value


print("--- consuming outer_stream() one piece at a time ---")
for piece in outer_stream():  # each loop turn pulls exactly one more value, lazily, not all at once
    print(f"received: {piece!r}")

print("\nNotice the [inner]/received print order above: nothing runs ahead of time -- each")
print("'producing fragment N' line only appears the instant that specific piece is requested.")
