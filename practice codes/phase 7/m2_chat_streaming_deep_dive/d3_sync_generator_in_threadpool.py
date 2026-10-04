# Minimal demo of C4: proving (not assuming) that a BLOCKING sync generator, driven through
# Starlette's real iterate_in_threadpool, does NOT freeze the async event loop -- other async
# work keeps running concurrently while the sync generator is blocked.
import asyncio
import time
from starlette.concurrency import iterate_in_threadpool  # the REAL function StreamingResponse uses


def blocking_generator():  # stands in for RAGPipeline.stream(): real blocking work per item
    for i in range(3):
        time.sleep(0.1)  # simulates a real blocking network call (a real Ollama/Qdrant round trip)
        yield f"chunk-{i}"


async def ticker(counter):  # a separate async task that should keep running if the loop is free
    while True:
        counter[0] += 1  # proves this task got to run
        await asyncio.sleep(0.02)  # yields control, the way real async code does


async def main():
    counter = [0]
    tick_task = asyncio.create_task(ticker(counter))
    # starts the ticker CONCURRENTLY with the blocking generator below
    start = time.perf_counter()
    async for chunk in iterate_in_threadpool(blocking_generator()):
        # the EXACT mechanism StreamingResponse uses internally for a plain sync generator
        elapsed = time.perf_counter() - start
        print(f"got {chunk!r} at {elapsed:.2f}s -- ticker count so far: {counter[0]}")
    tick_task.cancel()  # stops the ticker once the blocking generator is fully exhausted


asyncio.run(main())
print("\nIf the ticker count kept rising WHILE the blocking generator was sleeping, the event")
print("loop was never frozen -- proving a sync RAGPipeline.stream() call, even a real 300-second")
print("one, still lets /health and every other concurrent request keep being served normally.")
