# Assert by RANK, not by SCORE: a harmless model update breaks score tests, not rank tests.
import math

VOCAB = ["founded", "acme", "2015", "widget", "values"]


def embed(text, weights):
    # count vocabulary words, scale each by a per-word weight (the "model"), make unit length
    raw = [text.lower().count(word) * w for word, w in zip(VOCAB, weights)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


DOCS = {
    "about_0": "Acme was founded in 2015",
    "about_1": "Acme has two offices",
    "widget_0": "The widget ships in blue",
}
MODEL_V1 = [1.0, 1.0, 1.0, 1.0, 1.0]
MODEL_V2 = [1.4, 0.8, 1.6, 1.0, 1.0]        # "retrained": same idea, slightly different weights


def search(question, weights):
    q = embed(question, weights)
    scored = [(sum(a * b for a, b in zip(q, embed(t, weights))), cid) for cid, t in DOCS.items()]
    return sorted(scored, reverse=True)


baseline_score = search("Who founded Acme?", MODEL_V1)[0][0]      # the number a score test would hard-code
print(f"score test written against v1: expects {baseline_score:.4f}")
for label, weights in (("v1", MODEL_V1), ("v2", MODEL_V2)):
    ranking = search("Who founded Acme?", weights)
    top_score, top_id = ranking[0]
    score_ok = abs(top_score - baseline_score) < 0.001
    rank_ok = top_id == "about_0"
    print(f"model {label}: ranking {[(cid, round(s, 3)) for s, cid in ranking]}")
    print(f"          score test {'PASS' if score_ok else 'FAIL'} | rank test {'PASS' if rank_ok else 'FAIL'}")
