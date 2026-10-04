# Minimal demo of C2: FastAPI's @app.middleware("http") (BaseHTTPMiddleware) returns from
# call_next as soon as a streaming response's FIRST message arrives, not once its real body
# finishes -- proven side by side against a raw ASGI middleware on the SAME slow route.
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient


def slow_stream():
    for i in range(3):
        time.sleep(0.3)  # a real, blocking delay -- stands in for a real generation chunk
        yield f"chunk-{i}\n"


# --- app 1: timed with @app.middleware("http") -------------------------------------------------
app1 = FastAPI()
base_http_elapsed = []


@app1.middleware("http")
async def base_http_timing(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    base_http_elapsed.append(time.perf_counter() - start)
    return response


@app1.get("/stream")
def stream1():
    return StreamingResponse(slow_stream())


# --- app 2: timed with a raw ASGI middleware, wrapping send directly ---------------------------
app2 = FastAPI()
raw_asgi_elapsed = []


class RawTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()

        async def wrapped_send(message):
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                raw_asgi_elapsed.append(time.perf_counter() - start)
            await send(message)

        await self.app(scope, receive, wrapped_send)


app2.add_middleware(RawTimingMiddleware)


@app2.get("/stream")
def stream2():
    return StreamingResponse(slow_stream())


client1 = TestClient(app1)
overall1_start = time.perf_counter()
client1.get("/stream")
overall1 = time.perf_counter() - overall1_start

client2 = TestClient(app2)
overall2_start = time.perf_counter()
client2.get("/stream")
overall2 = time.perf_counter() - overall2_start

print(f"real total request time (both apps, same route): app1={overall1:.3f}s  app2={overall2:.3f}s")
print(f"\n@app.middleware('http')  -- call_next's own elapsed time: {base_http_elapsed[0]:.3f}s")
print(f"raw ASGI middleware      -- send-to-final-chunk elapsed:   {raw_asgi_elapsed[0]:.3f}s")
print("\n-> call_next returned almost immediately: it saw the response OBJECT exist, not finish.")
print("-> the raw ASGI middleware's own timer only stopped once the real, full body was sent.")
