# Minimal demo of C1: app.openapi() builds the schema by INSPECTING route definitions (type
# hints, Pydantic models, decorator arguments) -- it never actually CALLS any route function.
# Proven with a route that would crash if it were ever really invoked.
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI(title="Introspection Demo")

call_count = {"n": 0}


@app.get("/would-crash")
def would_crash():
    call_count["n"] += 1  # this line NEVER runs just from generating the schema
    raise RuntimeError("if this ever actually ran, something is very wrong")


print("--- generating the schema (app.openapi()) ---")
schema = app.openapi()
print("schema generated successfully:", "paths" in schema and "/would-crash" in schema["paths"])
print("call_count after schema generation:", call_count["n"])

print("\n--- fetching /docs and /openapi.json over real HTTP ---")
client = TestClient(app)
r1 = client.get("/openapi.json")
r2 = client.get("/docs")
print("openapi.json status:", r1.status_code, " docs status:", r2.status_code)
print("call_count after fetching both:", call_count["n"])

print("\n--- caching: app.openapi() called a second time ---")
schema_again = app.openapi()
print("same object returned (identity check):", schema is schema_again)
print("app.openapi_schema is set:", app.openapi_schema is not None)

print("\n--- only an ACTUAL call to the route executes its body ---")
try:
    client.get("/would-crash")
except RuntimeError as exc:
    print("a REAL request DID reach and run the function -- it raised:", exc)
print("call_count now:", call_count["n"])
