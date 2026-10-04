# Minimal demo of C4: a middleware registered via app.add_middleware(...) sits OUTSIDE
# ExceptionMiddleware but INSIDE ServerErrorMiddleware -- so it sees a SPECIFIC handler's
# response as a normal completion, but sees the bare-Exception handler's case as a raised
# exception passing THROUGH it instead. Proven with and without the fix.
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def build_app(with_try_except: bool):
    app = FastAPI()
    seen = []  # records what THIS middleware actually observed, per request

    class Middleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            status_holder = {"status": None}

            async def wrapped_send(message):
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                await send(message)

            if with_try_except:
                try:
                    await self.app(scope, receive, wrapped_send)
                except Exception as exc:
                    seen.append(("caught-then-reraised", type(exc).__name__, status_holder["status"]))
                    raise
                else:
                    seen.append(("normal-completion", None, status_holder["status"]))
            else:
                # the REJECTED version: no try/except at all
                await self.app(scope, receive, wrapped_send)
                seen.append(("normal-completion", None, status_holder["status"]))
                # this line NEVER runs for the bare-Exception case -- the exception skips it

    app.add_middleware(Middleware)

    class SpecificError(Exception):
        pass

    @app.exception_handler(SpecificError)
    async def h1(request, exc):
        return JSONResponse({"x": 1}, status_code=409)

    @app.exception_handler(Exception)
    async def h2(request, exc):
        return JSONResponse({"x": 2}, status_code=500)

    @app.get("/specific")
    def specific():
        raise SpecificError("handled by a SPECIFIC registered handler")

    @app.get("/bare")
    def bare():
        raise RuntimeError("only the bare Exception handler catches this")

    return app, seen


for label, with_try_except in [("WITHOUT the try/except (the rejected version)", False),
                                ("WITH the try/except (the real, shipped version)", True)]:
    print(f"=== {label} ===")
    app, seen = build_app(with_try_except)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/specific")
    client.get("/bare")
    for i, path in enumerate(("/specific", "/bare")):
        print(f"  {path:10s} -> middleware saw: {seen[i] if i < len(seen) else 'NOTHING LOGGED'}")
    print()
