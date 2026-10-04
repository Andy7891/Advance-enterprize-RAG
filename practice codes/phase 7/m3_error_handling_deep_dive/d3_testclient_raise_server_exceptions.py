# Minimal demo of C4: TestClient's raise_server_exceptions parameter (default True) changes
# what "the test failed" even means -- and, combined with C2's finding, explains exactly why
# THIS project's own 500-handler test failed on its first real run.
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/no-handler-at-all")
def no_handler_at_all():
    raise RuntimeError("nothing anywhere catches this")
    # zero custom exception handlers registered in this whole app -- the most basic case


@app.exception_handler(ValueError)
async def handle_value_error(request, exc):
    return JSONResponse({"caught": "ValueError"}, status_code=500)


@app.exception_handler(Exception)
async def handle_anything(request, exc):
    return JSONResponse({"caught": "Exception"}, status_code=500)


@app.get("/raise-specific")
def raise_specific():
    raise ValueError("a SPECIFIC, registered failure")


@app.get("/raise-unregistered")
def raise_unregistered():
    raise RuntimeError("only the bare Exception handler can catch this one")


print("=== TestClient(app)  -- the default, raise_server_exceptions=True ===")
client_default = TestClient(app)

for path in ("/no-handler-at-all", "/raise-specific", "/raise-unregistered"):
    try:
        r = client_default.get(path)
        print(f"{path:24s} -> clean response: {r.status_code} {r.json()}")
    except Exception as e:
        print(f"{path:24s} -> RAISED into the caller: {type(e).__name__}: {e}")

print("\n=== TestClient(app, raise_server_exceptions=False) ===")
client_quiet = TestClient(app, raise_server_exceptions=False)

for path in ("/no-handler-at-all", "/raise-specific", "/raise-unregistered"):
    r = client_quiet.get(path)
    print(f"{path:24s} -> clean response: {r.status_code} {r.json()}")

print("\n-> /no-handler-at-all and /raise-unregistered ALWAYS raise into the default client,")
print("   because nothing (or only the bare-Exception handler) stands between them and")
print("   ServerErrorMiddleware's own re-raise.")
print("-> /raise-specific NEVER raises into the caller, in EITHER client -- its handler is")
print("   registered for a specific class, so it is handled entirely by ExceptionMiddleware,")
print("   which does not re-raise at all.")
