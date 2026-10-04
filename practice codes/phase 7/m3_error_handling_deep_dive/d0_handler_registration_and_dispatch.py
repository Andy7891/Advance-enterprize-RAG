# Minimal demo of C1: a raised exception is dispatched to the handler registered for the
# MOST SPECIFIC matching class in its own MRO -- not "was this exact type registered."
# QdrantDown below is NEVER registered directly; only its ancestor, UpstreamError, is.
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

app = FastAPI()

calls = []  # records which handler actually ran, so dispatch is PROVEN, not assumed


class UpstreamError(Exception):
    """A made-up base class, standing in for a family of related backend failures."""


class QdrantDown(UpstreamError):
    """A more specific failure -- one particular KIND of UpstreamError."""


@app.exception_handler(UpstreamError)
async def handle_upstream_error(request, exc):
    calls.append("handle_upstream_error")
    # registered for UpstreamError -- but will ALSO catch any subclass of it
    return JSONResponse({"handled_by": "UpstreamError handler", "type": type(exc).__name__})


@app.get("/raise-base")
def raise_base():
    raise UpstreamError("a generic upstream problem")


@app.get("/raise-specific")
def raise_specific():
    raise QdrantDown("qdrant specifically is down")
    # QdrantDown has NO handler of its own registered anywhere in this app


client = TestClient(app)

r1 = client.get("/raise-base")
print("raise-base     ->", r1.status_code, r1.json())

r2 = client.get("/raise-specific")
print("raise-specific ->", r2.status_code, r2.json())

print("\nhandlers that ran, in order:", calls)
print("QdrantDown's own MRO:", [c.__name__ for c in QdrantDown.__mro__])
print("-> dispatch walked this MRO and used the first ancestor with a REGISTERED handler")
