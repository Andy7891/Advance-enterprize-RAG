# Minimal demo of C3: the REAL Server-Sent Events wire format, hand-built exactly like
# src/api/main.py's own _sse_events -- including the multi-line framing rule most hand-rolled
# SSE implementations get wrong the first time.
def to_sse_events(fragments):
    for fragment in fragments:  # walks each real fragment, in order
        payload = fragment.replace("\n", "\ndata: ")
        # SSE requires EVERY LINE of multi-line data to carry its own "data: " prefix -- a
        # fragment with an embedded newline would otherwise silently break the event's framing
        yield f"data: {payload}\n\n"
        # one real SSE event: "data: <text>", then a REQUIRED blank line ending it
    yield "data: [DONE]\n\n"
    # an explicit end-of-stream marker, so a client can tell "finished normally" from "cut off"


fragments = ["Hello", ", world", "\n\nSources:\n[1] a.md"]
# the last fragment mirrors RAGPipeline.stream()'s own real final chunk: it starts with "\n\n"

raw = "".join(to_sse_events(fragments))
print("--- rendered (what a human reading raw text sees) ---")
print(raw)
print("--- repr (the EXACT bytes/escapes, nothing hidden) ---")
print(repr(raw))
