# Two different clocks: wall-clock time for "when", a monotonic counter for "how long".
import time
from datetime import datetime, timezone

local = datetime.now()                               # naive: no timezone attached
utc = datetime.now(timezone.utc)                     # aware: carries +00:00
print("naive local  :", local.isoformat(timespec="seconds"), "| tzinfo:", local.tzinfo)
print("aware UTC    :", utc.isoformat(timespec="seconds"), "| tzinfo:", utc.tzinfo)

# Comparing a naive and an aware timestamp is an error, not a silent mistake
try:
    local < utc
except TypeError as e:
    print("naive < aware:", e)

# time.time() is wall-clock seconds (can jump if the system clock is adjusted);
# time.perf_counter() only moves forward and has fine resolution -> right for durations
print("clock info time         :", time.get_clock_info("time").monotonic, "monotonic,",
      time.get_clock_info("time").resolution, "s resolution")
print("clock info perf_counter :", time.get_clock_info("perf_counter").monotonic, "monotonic,",
      time.get_clock_info("perf_counter").resolution, "s resolution")
start = time.perf_counter()
sum(range(1_000_000))
print(f"summing 1,000,000 ints took {time.perf_counter() - start:.4f}s (measured with perf_counter)")
