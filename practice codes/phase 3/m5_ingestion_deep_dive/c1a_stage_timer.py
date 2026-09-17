# A job as named, timed stages -- a tiny copy of ingest.py's _StageTimer, printed step by step.
import time
from contextlib import contextmanager

stage_seconds = {}
current = None


@contextmanager
def stage(name):
    global current
    current = name
    print(f"  enter {name!r}")
    started = time.perf_counter()
    try:
        yield                                   # the body of the `with` block runs here
    finally:                                    # runs whether the body finished or raised
        stage_seconds[name] = round(time.perf_counter() - started, 3)
        print(f"  leave {name!r} after {stage_seconds[name]}s")


print("RUN 1: every stage succeeds")
with stage("load"):
    time.sleep(0.05)
with stage("embed"):
    time.sleep(0.20)
with stage("write"):
    time.sleep(0.01)
print("  timings:", stage_seconds, "| slowest:", max(stage_seconds, key=stage_seconds.get))

print("RUN 2: the middle stage fails")
stage_seconds.clear()
try:
    with stage("load"):
        time.sleep(0.05)
    with stage("embed"):
        time.sleep(0.10)
        raise ConnectionError("model server went away")
    with stage("write"):                        # never reached
        time.sleep(0.01)
except ConnectionError as e:
    print(f"  caught {type(e).__name__}: {e}")
print("  timings:", stage_seconds, "| failed in:", current, "| 'write' present?", "write" in stage_seconds)
