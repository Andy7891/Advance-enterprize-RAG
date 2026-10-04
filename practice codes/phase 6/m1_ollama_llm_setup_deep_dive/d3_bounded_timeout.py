# Minimal demo of C4: a bounded timeout fails FAST and predictably; no timeout means
# waiting however long the other side takes -- shown here with a server that answers
# late on purpose, not never, so this demo stays short and safe to run.
import threading  # runs the toy slow server in the background while the demo continues
import time  # measures real elapsed time for both attempts
from http.server import BaseHTTPRequestHandler, HTTPServer  # a real, correct HTTP server

import httpx  # the same HTTP library the project's own Ollama client sits on top of

SLOW_SERVER_DELAY_SECONDS = 3  # how long the toy server waits before finally replying


class SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(SLOW_SERVER_DELAY_SECONDS)  # simulates a service that is just slow, not dead
        try:
            self.send_response(200)  # answers, eventually, with a normal HTTP 200
            self.end_headers()  # finishes the headers before writing the body
            self.wfile.write(b"OK")  # writes the real response body
        except (ConnectionAbortedError, ConnectionResetError):
            print("  (server side: tried to answer, but the bounded client had already left)")
            # this is the EXPECTED other half of the bounded-timeout story: the server was
            # never told to stop working, it just found nobody listening when it finished

    def log_message(self, *args):
        pass  # silences the server's own per-request log lines for this demo


def start_slow_server():
    server = HTTPServer(("127.0.0.1", 0), SlowHandler)  # a real local HTTP server, any free port
    threading.Thread(target=server.handle_request, daemon=True).start()  # serves exactly one request
    return f"http://127.0.0.1:{server.server_port}/"  # hands back this one server's own address


started = time.perf_counter()  # starts the bounded attempt's stopwatch
try:
    httpx.get(start_slow_server(), timeout=1.5)  # a SHORT, bounded timeout, its OWN fresh server
except httpx.TimeoutException as error:
    print(f"bounded  (timeout=1.5s): FAILED after {time.perf_counter() - started:.2f}s -- {type(error).__name__}")
    # fails fast and predictably, well before the server would ever have answered

started = time.perf_counter()  # starts the patient attempt's stopwatch
response = httpx.get(start_slow_server(), timeout=10.0)  # a longer timeout, a SEPARATE fresh server
print(f"patient  (timeout=10s):  SUCCEEDED after {time.perf_counter() - started:.2f}s -- {response.text}")
# succeeds, but only because this caller was willing to wait as long as it took

# PROBE_TIMEOUT_SECONDS = 120.0 in the real code picks a value in between: long enough for
# a real model to answer, short enough that a truly hung Ollama fails in minutes, not forever.
