"""Real-stack measurements the deterministic checker cannot make (docs/api-contract.md, G1 and G2).

  python real_stack_probes.py direct-abandon              control: a client walks away from Ollama itself
  python real_stack_probes.py abandon  --base-url URL     a client walks away from /chat mid-answer
  python real_stack_probes.py complete --base-url URL     the same question, left to finish
  python real_stack_probes.py two-users --base-url URL    two simultaneous questions

Every run first waits for Ollama to go idle, so nothing it measures is left over from an earlier
run. CPU comes from `docker stats`, summed over cores (400 % = four cores busy). The digits
describe THIS host (CPU-only Mistral-7B); what transfers is the shape: does the CPU drop at once
after the client leaves, or keep burning for nobody.
"""

import argparse
import subprocess
import sys
import threading
import time

import httpx

OLLAMA_CONTAINER = "advancerag-ollama-1"
OLLAMA_URL = "http://127.0.0.1:11434"
QUESTION = {
    "query": "Explain in detail what Insurellm is, which products it sells, and who runs it.",
    "k": 5,
}
# a broad question with a generous k, so the answer is long enough that "still generating for
# nobody" is distinguishable from "finished on its own" within the observation window
IDLE_PERCENT = 20.0
# below this the model is not generating (an idle Ollama sits at about 0-2 %)


def cpu() -> float:
    """The Ollama container's CPU %, summed over cores, from one `docker stats` sample."""
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", OLLAMA_CONTAINER],
        capture_output=True,
        text=True,
        timeout=60,
    )
    text = result.stdout.strip().rstrip("%")
    return float(text) if text else float("nan")


def wait_idle(limit: float = 300.0) -> None:
    """Blocks until two consecutive CPU samples are idle, so a run starts from a quiet model."""
    started = time.perf_counter()
    quiet = 0
    while quiet < 2:
        quiet = quiet + 1 if cpu() < IDLE_PERCENT else 0
        if time.perf_counter() - started > limit:
            sys.exit(f"Ollama did not go idle within {limit:.0f} s; nothing was measured")
    print(f"Ollama idle (waited {time.perf_counter() - started:.0f} s)", flush=True)


def watch_after_leaving(left: float, limit: float = 240.0) -> None:
    """Prints CPU samples until the model has been idle twice in a row, or `limit` seconds pass."""
    quiet = 0
    peak = 0.0
    while time.perf_counter() - left < limit:
        sample = cpu()
        elapsed = time.perf_counter() - left
        peak = max(peak, sample)
        print(f"  t+{elapsed:5.1f} s after leaving: Ollama CPU {sample:6.1f} %", flush=True)
        quiet = quiet + 1 if sample < IDLE_PERCENT else 0
        if quiet >= 2:
            print(f"=> idle {elapsed:.0f} s after the client left (peak {peak:.0f} %)")
            return
    print(f"=> STILL BUSY {limit:.0f} s after the client left (peak {peak:.0f} %)")


def read_events(response: httpx.Response, stop_after: int | None) -> tuple[int, float | None, str]:
    """Reads `data:` lines; returns (events read, seconds to the first one, last event)."""
    started = time.perf_counter()
    events, first, last = 0, None, ""
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        events += 1
        last = line[5:].strip()
        if first is None:
            first = time.perf_counter() - started
        if stop_after is not None and events >= stop_after:
            break
    return events, first, last


def abandon(base_url: str, leave_after: int) -> None:
    wait_idle()
    started = time.perf_counter()
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, read=600.0)) as client:
        with client.stream("POST", "/chat", json=QUESTION) as response:
            events, first, _ = read_events(response, stop_after=leave_after)
    left = time.perf_counter()
    print(f"first event after {first:.1f} s; the client left after {events} events", flush=True)
    watch_after_leaving(left)
    print(f"(whole probe {time.perf_counter() - started:.0f} s)")


def complete(base_url: str) -> None:
    wait_idle()
    started = time.perf_counter()
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, read=600.0)) as client:
        with client.stream("POST", "/chat", json=QUESTION) as response:
            events, first, last = read_events(response, stop_after=None)
    total = time.perf_counter() - started
    print(f"first event after {first:.1f} s; {events} events; finished at {total:.1f} s with {last!r}")
    print(f"=> generation after the first event took {total - (first or 0):.1f} s")


def two_users(base_url: str) -> None:
    wait_idle()
    results: dict[str, tuple[int, float | None, str, float]] = {}
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, read=900.0)) as client:
            barrier.wait()
            started = time.perf_counter()
            with client.stream("POST", "/chat", json=QUESTION) as response:
                events, first, last = read_events(response, stop_after=None)
            results[name] = (events, first, last, time.perf_counter() - started)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("user A", "user B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for name, (events, first, last, total) in sorted(results.items()):
        print(f"{name}: first event {first:.1f} s, finished {total:.1f} s, {events} events, {last!r}")


def direct_abandon(leave_after: int) -> None:
    """The control: leaves Ollama itself, with no API server in between."""
    wait_idle()
    payload = {
        "model": "mistral",
        "prompt": "Write a long, detailed essay on the history of insurance.",
        "stream": True,
    }
    with httpx.Client(base_url=OLLAMA_URL, timeout=httpx.Timeout(30.0, read=600.0)) as client:
        with client.stream("POST", "/api/generate", json=payload) as response:
            lines = 0
            for line in response.iter_lines():
                if line.strip():
                    lines += 1
                if lines >= leave_after:
                    break
    left = time.perf_counter()
    print(f"read {lines} streamed lines from Ollama and left", flush=True)
    watch_after_leaving(left)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["direct-abandon", "abandon", "complete", "two-users"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--leave-after", type=int, default=3, help="events to read before leaving")
    args = parser.parse_args()
    if args.mode == "direct-abandon":
        direct_abandon(args.leave_after)
    elif args.mode == "abandon":
        abandon(args.base_url, args.leave_after)
    elif args.mode == "complete":
        complete(args.base_url)
    else:
        two_users(args.base_url)


if __name__ == "__main__":
    main()
