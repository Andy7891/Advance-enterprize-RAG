# Minimal demo of C3: building the real 400 (malformed JSON) vs 422 (well-formed, invalid)
# split from RequestValidationError.errors()'s own real structure -- and printing that raw
# structure so the distinction is seen directly, not just asserted.
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field


class Item(BaseModel):
    name: str = Field(..., min_length=1)
    count: int = Field(default=1, gt=0)


app = FastAPI()


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request, exc: RequestValidationError):
    errors = exc.errors()
    print(f"  raw errors(): {errors}")
    if errors and all(e["type"] == "json_invalid" for e in errors):
        # every reported error is a parse failure -- the request itself is malformed
        return JSONResponse(status_code=400, content={"error": {"code": "invalid_json"}})
    message = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors)
    return JSONResponse(
        status_code=422, content={"error": {"code": "validation_error", "message": message}}
    )


@app.post("/items")
def create_item(item: Item):
    return {"ok": True}


client = TestClient(app)

print("case 1: malformed JSON body (not parseable at all)")
r = client.post("/items", content=b"{oops", headers={"content-type": "application/json"})
print(f"  -> {r.status_code} {r.json()}\n")

print("case 2: valid JSON, missing required field")
r = client.post("/items", json={"count": 5})
print(f"  -> {r.status_code} {r.json()}\n")

print("case 3: valid JSON, a field value that fails its own rule")
r = client.post("/items", json={"name": "x", "count": -1})
print(f"  -> {r.status_code} {r.json()}\n")

print("case 4: fully valid")
r = client.post("/items", json={"name": "x", "count": 1})
print(f"  -> {r.status_code} {r.json()}")
