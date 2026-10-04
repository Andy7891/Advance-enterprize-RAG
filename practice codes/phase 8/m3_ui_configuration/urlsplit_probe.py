"""P8-M3 measurement 7: how urllib.parse.urlsplit and httpx treat the awkward addresses.

    python "practice codes/phase 8/m3_ui_configuration/urlsplit_probe.py"

Predict first: for each address, what is the scheme, the host, the port? Which ones raise? The answers
are why src/ui/endpoint.py checks whitespace BEFORE parsing, reads `.port` inside a try, and rebuilds the
address from `.netloc` rather than `.hostname`. (Printed with ascii() so a Windows console cannot choke.)
"""

from urllib.parse import urlsplit

import httpx

cases = [
    "localhost:8000", "//localhost:8000", "ftp://host", "http://", "http:///path", "http://host:abc",
    "http://host:99999", "http://host:0", "http://host:", "http://[::1", "http://[::1]:8000",
    "http://host name", "http://host/?a=1", "http://host/?", "http://host/#top",
    "http://user:secret@host", "http://@host", "user:secret@host:8000", "HTTP://LocalHost:8000",
    "http://:8000", "http://host/api//", "http://bücher.example", "http://a_b:8000", "http://host.",
    "http://host\n/x",
]
for c in cases:
    try:
        p = urlsplit(c)
        try:
            port = p.port
        except ValueError as e:
            port = f"ValueError({e})"
        print(
            f"{ascii(c):30} scheme={p.scheme!r:8} netloc={ascii(p.netloc):20} host={ascii(p.hostname):14} "
            f"port={port!r:44} path={p.path!r}"
        )
    except ValueError as e:
        print(f"{ascii(c):30} urlsplit ValueError: {e}")

print("\nwhat httpx does with a few accepted-looking forms (building a URL only, no network):")
for c in [
    "http://host:/chat", "http://host./chat", "http://bücher.example/chat",
    "http://a_b:8000/chat", "http://[::1]:8000/chat",
]:
    try:
        print(f"   {ascii(c):34} ->", httpx.URL(c))
    except Exception as e:
        print(f"   {ascii(c):34} -> {type(e).__name__}: {e}")
