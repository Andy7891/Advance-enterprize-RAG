# Why NaN must be stopped before storage: it poisons every comparison it touches.
import math

nan_vector = [math.nan, 0.0]
query = [1.0, 0.0]
score = sum(a * b for a, b in zip(nan_vector, query))
print("dot product with a NaN vector:", score)
print("NaN > 0.5 ?", score > 0.5, "| NaN < 0.5 ?", score < 0.5, "| NaN == NaN ?", score == score)

scores = [0.9, math.nan, 0.3, 0.7]
print("sorted with a NaN in the list:", sorted(scores, reverse=True), "<- order is no longer trustworthy")
