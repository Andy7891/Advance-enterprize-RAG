# Minimal demo of C3: what `scope`, `receive`, and `send` actually ARE -- the real ASGI message
# dicts a plain FastAPI route produces, made visible by printing every one that passes through
# a middleware's own wrapped `send`. No FastAPI magic hidden here: this IS the wire protocol.
from fastapi import FastAPI
from fastapi.testclient import TestClient


class PrintEverythingMiddleware:
    """The simplest possible ASGI middleware: prints scope once, then every message sent."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        print("scope (trimmed to the interesting keys):")
        for key in ("type", "method", "path", "http_version"):
            print(f"  {key!r}: {scope[key]!r}")

        async def logging_send(message):
            print("send() was called with:", message)
            await send(message)  # ALWAYS forward -- this is what makes it a passthrough

        await self.app(scope, receive, logging_send)


app = FastAPI()
app.add_middleware(PrintEverythingMiddleware)


@app.get("/ping")
def ping():
    return {"ok": True}


client = TestClient(app)
print("=== a plain JSON route ===")
client.get("/ping")

print("\n=== a streaming route, to see MULTIPLE http.response.body messages ===")


def three_chunks():
    yield "a"
    yield "b"
    yield "c"


from fastapi.responses import StreamingResponse


@app.get("/stream")
def stream():
    return StreamingResponse(three_chunks())


client.get("/stream")
print("\n-> notice: one http.response.start, then one http.response.body PER yielded chunk,")
print("   each with more_body=True except the very last one (more_body=False, or omitted).")
