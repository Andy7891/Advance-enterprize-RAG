# Minimal demo of C5: an explicitly-absent timeout kwarg is NOT the same as httpx's own
# sensible default -- ollama.Client(host=...) with no timeout= passes timeout=None straight
# through to httpx, and None means "disabled", not "use the normal default."
import httpx

print("httpx.Client() -- no timeout argument at all:")
print(" ", httpx.Client().timeout)

print("\nhttpx.Client(timeout=None) -- EXPLICITLY passed None:")
c = httpx.Client(timeout=None)
print(" ", c.timeout)
print(f"   connect={c.timeout.connect} read={c.timeout.read} write={c.timeout.write} pool={c.timeout.pool}")

print("\nhttpx.Client(timeout=60.0) -- an explicit, real bound:")
print(" ", httpx.Client(timeout=60.0).timeout)

print("\nollama.Client(host=...) with no timeout kwarg -- what this project's own Embedder")
print("used to do, before EMBED_TIMEOUT_SECONDS was added. ollama.Client stores its real")
print("underlying httpx.Client as `._client` (src: ollama/_client.py Client.__init__ calls")
print("`super().__init__(httpx.Client, host, **kwargs)`), so the REAL, constructed client's")
print("own timeout can be read directly, not just a function signature's default value:")
import ollama

real_embedder_client = ollama.Client(host="http://localhost:11434")
print(" ", real_embedder_client._client.timeout)
print("  -> exactly the same Timeout(timeout=None) as the explicit case above: no timeout at")
print("     all, not httpx's own 5s default -- this is what every /chat request embedded its")
print("     query through, until EMBED_TIMEOUT_SECONDS fixed it (decision.md D-2026-09-30-25)")
