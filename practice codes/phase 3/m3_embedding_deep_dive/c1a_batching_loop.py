# How embed_batch cuts a list into batches -- pure Python, no Ollama.
items = [f"chunk_{i}" for i in range(40)]
batch_size = 16

# range(start, stop, step) yields the START index of every batch: 0, 16, 32
starts = list(range(0, len(items), batch_size))
print("batch start indices:", starts)

for start in starts:
    batch = items[start : start + batch_size]      # slicing past the end is safe in Python
    print(f"batch [{start:>2}:{start + batch_size:>2}] -> {len(batch):>2} items, first={batch[0]}, last={batch[-1]}")

# The number of requests is the ceiling of len / batch_size
import math
print("requests needed:", math.ceil(len(items) / batch_size))
print("items[32:48] on a 40-item list has", len(items[32:48]), "items (no IndexError)")
