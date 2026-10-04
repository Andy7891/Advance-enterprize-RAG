# Minimal demo of C4: FastAPI's "app" object does NOT listen on any network port by itself --
# it's an ASGI application: an async callable a separate SERVER (uvicorn) drives.
import inspect  # used to inspect app.__call__ without ever running a real server
from fastapi import FastAPI

app = FastAPI()

print("is app.__call__ an async function?", inspect.iscoroutinefunction(app.__call__))
print("app.__call__ signature:", inspect.signature(app.__call__))
# ASGI's real contract: async def __call__(self, scope, receive, send) -- three arguments:
# scope (metadata about this one connection/request), receive (an async function to get
# incoming messages), send (an async function to send a response back)

print("\nNo TCP socket is open right now -- 'app' is just this callable sitting in memory.")
print("Running `uvicorn src.api.main:app` is what actually:")
print("  1. opens a real TCP socket and listens for HTTP connections")
print("  2. for each request, builds a real 'scope' dict and calls `await app(scope, receive, send)`")
print("  3. writes whatever 'send' was called with back onto the socket as real HTTP bytes")
