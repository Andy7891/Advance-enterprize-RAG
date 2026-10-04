# Minimal demo of C2: a Pydantic BaseModel parameter gives FastAPI automatic request parsing,
# type coercion, and validation -- including business rules like "must be positive", for free.
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field  # Field lets a constraint travel WITH the type itself


class Item(BaseModel):
    name: str = Field(..., min_length=1)  # "..." means required; min_length=1 rejects ""
    count: int = Field(default=1, gt=0)  # defaults to 1 if omitted; gt=0 rejects 0 and negatives


app = FastAPI()


@app.post("/items")
def create_item(item: Item):  # FastAPI parses the real request body INTO a real Item for us
    return {
        "received_name": item.name,
        "received_count": item.count,
        "type_of_count": type(item.count).__name__,  # proves it's a real Python int, not a string
    }


client = TestClient(app)
print("valid:", client.post("/items", json={"name": "apple", "count": 3}).json())
print("valid, count sent as a JSON STRING:", client.post("/items", json={"name": "apple", "count": "3"}).json())
# JSON itself has no separate "must be a number" enforcement at the wire level; Pydantic coerces

print("\nmissing name -> status:", client.post("/items", json={"count": 3}).status_code)
print("count=0 -> status:", client.post("/items", json={"name": "apple", "count": 0}).status_code)
print("count=-1 -> status:", client.post("/items", json={"name": "apple", "count": -1}).status_code)
print("count='abc' -> status:", client.post("/items", json={"name": "apple", "count": "abc"}).status_code)

print("\nomit count entirely:", client.post("/items", json={"name": "apple"}).json())
# requires count's own default (1) to apply when the client sends nothing for it at all
