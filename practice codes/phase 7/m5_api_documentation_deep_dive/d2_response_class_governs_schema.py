# Minimal demo of C3: the route's configured `response_class` -- NOT the function's own return
# type hint, NOT response_model=None -- is what governs whether FastAPI adds its automatic
# content entry to the schema. Three variants, same route logic, three different schemas.
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.get("/a")  # the ORIGINAL, unfixed attempt: nothing overridden
def a() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))


@app.get("/b", response_model=None)  # a PLAUSIBLE-sounding guess that turns out not to help
def b() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))


@app.get("/c", response_class=StreamingResponse)  # the REAL fix
def c() -> StreamingResponse:
    return StreamingResponse(iter(["x"]))


schema = app.openapi()
for path in ("/a", "/b", "/c"):
    response_200 = schema["paths"][path]["get"]["responses"]["200"]
    content = response_200.get("content")
    label = {"a": "nothing set", "b": "response_model=None", "c": "response_class=StreamingResponse"}
    print(f"{path}  ({label[path[1]]})")
    print("   content:", list(content.keys()) if content else "(no content key at all)")

print("\n-> /a and /b both still carry the spurious 'application/json': {'schema': {}} entry --")
print("   the return type hint and response_model=None had NO effect on it at all.")
print("-> /c has NO content key whatsoever: response_class=StreamingResponse suppresses the")
print("   automatic entry entirely, but adds nothing real in its place -- a real response=")
print("   override (like this project's own CHAT_RESPONSES) is still needed to describe what")
print("   the endpoint ACTUALLY returns, exactly what src/api/main.py's /chat route does.")
