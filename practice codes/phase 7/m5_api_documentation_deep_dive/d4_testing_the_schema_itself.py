# Minimal demo of C5: a runtime test (TestClient, checking real request/response behavior) and
# a documentation test (checking app.openapi() directly) prove two INDEPENDENT properties -- one
# can pass while the other fails. Exactly this project's own real P7-M5 story, reproduced.
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient


def build_app(fixed: bool) -> FastAPI:
    app = FastAPI()
    kwargs = {"response_class": StreamingResponse} if fixed else {}

    @app.get("/stream", **kwargs)
    def stream() -> StreamingResponse:
        return StreamingResponse(iter(["real", "data", "here"]))

    return app


def runtime_test(app: FastAPI) -> bool:
    """Checks real BEHAVIOR: does a real request actually get the real streamed content?"""
    client = TestClient(app)
    response = client.get("/stream")
    return response.status_code == 200 and response.text == "realdatahere"


def documentation_test(app: FastAPI) -> bool:
    """Checks the DOCUMENTATION: does the schema correctly describe what /stream returns?"""
    content = app.openapi()["paths"]["/stream"]["get"]["responses"]["200"].get("content") or {}
    return "application/json" not in content


for label, fixed in [("BEFORE the fix (response_class not set)", False),
                      ("AFTER the fix (response_class=StreamingResponse)", True)]:
    print(f"=== {label} ===")
    app = build_app(fixed)
    print("  runtime_test (real request/response behavior):", "PASS" if runtime_test(app) else "FAIL")
    print("  documentation_test (schema accuracy):          ", "PASS" if documentation_test(app) else "FAIL")
    print()

print("-> the runtime test passes in BOTH cases -- the route always worked correctly.")
print("-> only the documentation test catches the real bug: a schema lying about the response")
print("   type, something no amount of testing real REQUESTS could ever have caught.")
