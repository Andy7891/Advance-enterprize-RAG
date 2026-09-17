# zip() silently stops at the shorter list; zip(strict=True) refuses.
chunks = ["chunk_0", "chunk_1", "chunk_2"]
vectors = ["vec_0", "vec_1"]                 # the server "lost" one

print("zip()            ->", list(zip(chunks, vectors)), " <- chunk_2 vanished, no error")
try:
    list(zip(chunks, vectors, strict=True))
except ValueError as e:
    print("zip(strict=True) ->", type(e).__name__ + ":", e)
