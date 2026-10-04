# Minimal demo of C2: registering a handler for the literal Exception class does NOT go into
# the same place as every other registered type. Proven structurally, by inspecting Starlette's
# own constructed middleware stack directly -- not inferred from behavior.
from fastapi import FastAPI
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware.exceptions import ExceptionMiddleware

app = FastAPI()


class SpecificError(Exception):
    pass


@app.exception_handler(SpecificError)
async def handle_specific(request, exc):
    return None  # never actually called in this script -- only registration matters here


@app.exception_handler(Exception)
async def handle_anything(request, exc):
    return None


print("app.exception_handlers -- BOTH registrations live in the SAME flat dict:")
for key, handler in app.exception_handlers.items():
    print(f"  {key!r} -> {handler.__name__}")

print("\nwalking the REAL, constructed ASGI middleware chain (app.build_middleware_stack()):")
stack = app.build_middleware_stack()
node = stack
depth = 0
while hasattr(node, "app"):
    print(f"  depth {depth}: {type(node).__name__}")
    if isinstance(node, ServerErrorMiddleware):
        name = node.handler.__name__ if node.handler else None
        print(f"      .handler (ONE slot, Exception/500 only)  = {name}")
    if isinstance(node, ExceptionMiddleware):
        names = [k.__name__ if isinstance(k, type) else k for k in node._exception_handlers]
        print(f"      ._exception_handlers (a dict, everything else) = {names}")
    node = node.app
    depth += 1
    if depth > 6:
        break

print("\n-> SpecificError's handler lives in ExceptionMiddleware's own dict.")
print("-> Exception's handler was pulled OUT into ServerErrorMiddleware's single .handler slot.")
print("-> These are two different middleware classes with two different behaviors on purpose.")
