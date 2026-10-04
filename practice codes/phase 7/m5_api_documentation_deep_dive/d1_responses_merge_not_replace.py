# Minimal demo of C2: adding responses={200: {...}} to a route does NOT replace FastAPI's own
# automatically-generated 200 entry -- it MERGES alongside it. The exact real behavior found
# while fixing /chat's own documentation this step, reproduced in isolation.
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.get(
    "/stream",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def stream() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))


schema = app.openapi()
content = schema["paths"]["/stream"]["get"]["responses"]["200"]["content"]
print("content types present on the 200 response:", list(content.keys()))
print(json.dumps(content, indent=2))
print("\n-> 'application/json' is FastAPI's own automatic default -- it was never asked for,")
print("   and responses={} alone did not remove it. Both content types coexist.")
