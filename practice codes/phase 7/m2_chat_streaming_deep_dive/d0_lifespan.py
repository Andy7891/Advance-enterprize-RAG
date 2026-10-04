# Minimal demo of C1: FastAPI's lifespan only runs under a REAL server, or TestClient used
# AS a context manager -- a plain TestClient(app) never triggers it, exactly as confirmed
# empirically this session (tests/test_health.py keeps passing with lifespan=lifespan added).
from contextlib import asynccontextmanager  # the tool that turns a generator into a lifespan hook
from fastapi import FastAPI
from fastapi.testclient import TestClient

startup_log = []  # records real startup/shutdown events, so we can SEE when they actually fire


@asynccontextmanager
async def lifespan(app: FastAPI):  # runs once at real startup, once at real shutdown
    startup_log.append("startup")  # marks that real setup ran
    app.state.resource = "a real, expensive thing, built once"  # stored for every request to reuse
    yield  # the app serves requests for as long as it's running, between here and the next line
    startup_log.append("shutdown")  # marks that real teardown ran


app = FastAPI(lifespan=lifespan)


@app.get("/resource")
def get_resource():
    return {"resource": app.state.resource}  # reads back whatever lifespan stored


print("--- using TestClient WITHOUT a `with` block ---")
client = TestClient(app)
print("startup_log so far:", startup_log)
try:
    print(client.get("/resource").json())
except Exception as e:
    print("FAILED:", type(e).__name__, e)
    # app.state.resource was never set, because lifespan never ran

print("\n--- using TestClient AS a context manager (this DOES trigger lifespan) ---")
with TestClient(app) as client2:
    print("startup_log so far:", startup_log)
    print(client2.get("/resource").json())
print("startup_log after exiting the with-block:", startup_log)
