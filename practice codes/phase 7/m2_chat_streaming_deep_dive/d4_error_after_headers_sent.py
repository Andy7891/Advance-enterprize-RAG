# Minimal demo of C5: once a streaming response starts, you can no longer "change your mind"
# and send a clean HTTP error status -- an unhandled exception mid-stream behaves very
# differently from a normal (non-streaming) route's exception.
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

app = FastAPI()


def broken_stream():  # the REJECTED approach: let the exception propagate, unhandled
    yield "first chunk\n"
    raise RuntimeError("something failed after this point")


def safe_stream():  # the CHOSEN approach: catch it, report it AS an event instead
    try:
        yield "first chunk\n"
        raise RuntimeError("something failed after this point")
    except RuntimeError as e:
        yield f"[ERROR] {e}\n"


@app.get("/broken")
def broken():
    return StreamingResponse(broken_stream())


@app.get("/safe")
def safe():
    return StreamingResponse(safe_stream())


client = TestClient(app)

print("--- /broken: the unhandled-exception path ---")
try:
    r = client.get("/broken")
    print("status:", r.status_code)
    print("body:", repr(r.text))
except Exception as e:
    print("the exception reached the CLIENT's own code directly:", type(e).__name__, "-", e)
    print("notice: no clean 500 status, no error body -- just the raw exception itself")

print("\n--- /safe: caught and reported as a real chunk of content instead ---")
r2 = client.get("/safe")
print("status:", r2.status_code)
print("body:", repr(r2.text))
