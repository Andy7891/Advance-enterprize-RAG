# Minimal demo of C4: the Cohere SDK silently retries a 429 by default; build_cohere_client turns that off.
# A stubbed HTTP layer counts the requests. Nothing here touches the network.
import sys  # gives access to the module search path
import time  # measures how long each call takes

import cohere  # the real Cohere SDK
import httpx  # the HTTP library the SDK uses

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.reranker import build_cohere_client  # the real factory


def counting_client(factory):
    requests = []  # will hold one entry per HTTP request the SDK sends

    def handler(request):
        requests.append(request)  # counts this request
        return httpx.Response(429, json={"message": "too many requests"})  # answers that the caller is rate limited

    http = httpx.Client(transport=httpx.MockTransport(handler))  # an HTTP layer answered by the handler, not the network
    return factory(http), requests  # builds the client on that layer and hands back the counter


def try_rerank(label, factory):
    client, requests = counting_client(factory)  # builds a client and its request counter
    started = time.perf_counter()  # notes the start time
    try:
        client.rerank(model="rerank-v3.5", query="q", documents=["a document"], top_n=1)  # makes one rerank call
    except Exception as error:  # catches whatever the SDK raises
        name = type(error).__name__  # names the error
    seconds = time.perf_counter() - started  # measures the elapsed time
    print(f"{label:<28} requests sent: {len(requests)}   error: {name}   took {seconds:.1f}s")  # prints the outcome


try_rerank("SDK defaults", lambda http: cohere.ClientV2("test-key", httpx_client=http))  # a plain client, exactly as the SDK ships
try_rerank("build_cohere_client (ours)", lambda http: build_cohere_client("test-key", httpx_client=http))  # the project factory
print("one 429 became 3 requests under the defaults and exactly 1 under ours")  # the lesson
