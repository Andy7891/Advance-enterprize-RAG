# Minimal demo of C1: a FastAPI app is a real Python OBJECT, not magic -- it's an ASGI-
# compatible callable you can inspect, just like any other class instance.
from fastapi import FastAPI  # the class whose instances are ASGI applications

app = FastAPI(title="Demo", version="0.1.0")  # creates a real object, nothing runs yet
print("type:", type(app))  # shows it's a real, ordinary Python class instance
print("is callable (ASGI app):", callable(app))  # confirms it implements __call__, like any ASGI app
print("title:", app.title, " version:", app.version)  # reads back the metadata we set
print("registered routes before any @app.get:", [r.path for r in app.routes])
# FastAPI ships a couple of its OWN built-in routes even before we add any (docs, openapi.json)
