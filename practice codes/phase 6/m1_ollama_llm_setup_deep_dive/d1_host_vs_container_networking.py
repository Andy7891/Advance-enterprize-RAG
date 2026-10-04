# Minimal demo of C2: a hostname that only exists INSIDE Docker's own network fails to
# resolve from this host process, while "localhost" (this machine itself) always works.
import socket  # the standard library's own name-resolution and connection tool

socket.setdefaulttimeout(2)  # refuses to let a resolution attempt hang this demo

CANDIDATES = [  # two ways settings.ollama_url or a CLI flag could name the same server
    ("ollama", "the Docker Compose SERVICE NAME -- only meaningful inside Docker's network"),
    ("localhost", "THIS machine, from THIS machine's own point of view"),
]

for host, meaning in CANDIDATES:  # tries to resolve each hostname in turn
    print(f'trying "{host}"  ({meaning})')  # explains what is about to be attempted
    try:
        address = socket.getaddrinfo(host, 11434)[0][4]  # asks the OS to resolve this name
        print(f"  -> RESOLVES to {address}")  # prints the real IP address it found
    except OSError as error:  # covers the OS being unable to find that name at all
        print(f"  -> FAILS to resolve: {error}")  # prints the REAL error this host gives

# `ollama` is a name Docker's own internal DNS invents for containers on its network --
# it means nothing to this host process, which never joined that network. `localhost`
# means "this machine" everywhere, which is why src/embedding/embedder.py's CLI (and now
# src/generation/setup.py's CLI) hardcode it as their default instead of trusting a
# setting whose default value was written for code running INSIDE a container.
