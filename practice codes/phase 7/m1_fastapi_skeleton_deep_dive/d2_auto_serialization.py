# Minimal demo of C3: returning a plain Python object gets automatically turned into a real
# JSON HTTP response -- no manual json.dumps(), no manual Content-Type header.
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()


@app.get("/dict")
def as_dict():
    return {"status": "ok", "count": 3}  # a plain Python dict -- nothing JSON-specific here


@app.get("/list")
def as_list():
    return ["a", "b", "c"]  # a plain Python list, also a valid return value


client = TestClient(app)
r1 = client.get("/dict")
print("status:", r1.status_code, " content-type:", r1.headers["content-type"])
print("raw bytes:", r1.content)  # shows the REAL bytes actually sent over the wire
print("parsed json:", r1.json())  # the same bytes, decoded back into Python

r2 = client.get("/list")
print("\nstatus:", r2.status_code, " parsed json:", r2.json())
