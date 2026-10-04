# Minimal demo of C2: a path operation decorator just REGISTERS a plain function against a
# (method, path) pair -- no magic beyond an ordinary Python decorator.
from fastapi import FastAPI  # the class whose .get()/.post()/etc. are the decorators
from fastapi.testclient import TestClient  # drives the app in-process, no real network needed

app = FastAPI()


@app.get("/hello")  # registers: GET /hello -> call hello_world()
def hello_world():
    return {"message": "hello"}  # a plain Python dict, returned from a plain Python function


@app.get("/hello/{name}")  # a path PARAMETER: {name} becomes a real function argument
def hello_name(name: str):
    return {"message": f"hello {name}"}  # FastAPI extracts "name" from the URL and passes it in


client = TestClient(app)  # drives the ASGI app directly, in-process
print(client.get("/hello").json())  # calls GET /hello for real, through the real app
print(client.get("/hello/Ada").json())  # calls GET /hello/Ada -- "Ada" becomes name="Ada"
print("routes:", [(r.methods, r.path) for r in app.routes if hasattr(r, "methods")])
# shows the real registry: each decorator added one entry, mapping (method, path) -> function
