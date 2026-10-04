# Minimal demo of C4: Field(examples=[...]) flows directly into the generated JSON Schema, which
# is exactly what Swagger UI reads to prefill its own "Try it out" form -- no separate mechanism.
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

app = FastAPI()


class WithExample(BaseModel):
    query: str = Field(..., min_length=1, examples=["Who founded Insurellm?"])


class WithoutExample(BaseModel):
    query: str = Field(..., min_length=1)


@app.post("/with-example")
def with_example(body: WithExample):
    return {"ok": True}


@app.post("/without-example")
def without_example(body: WithoutExample):
    return {"ok": True}


schema = app.openapi()
print("WithExample's own real JSON Schema:")
print(json.dumps(schema["components"]["schemas"]["WithExample"], indent=2))
print("\nWithoutExample's own real JSON Schema:")
print(json.dumps(schema["components"]["schemas"]["WithoutExample"], indent=2))

print("\n-> 'examples' is a REAL, standard JSON Schema keyword (not FastAPI-specific) -- any")
print("   schema-aware tool (Swagger UI's own request-body prefill, a code generator, a linter)")
print("   can read it the same way, since it is just data sitting in the schema itself.")
