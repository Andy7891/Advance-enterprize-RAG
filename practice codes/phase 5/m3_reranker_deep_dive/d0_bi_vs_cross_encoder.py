# Minimal demo of C1: a bi-encoder scores a chunk WITHOUT seeing the question; a cross-encoder scores
# the (question, chunk) pair TOGETHER. Both scorers here are toys, built to show the SHAPE of each.
import math  # gives square roots for the cosine formula

QUESTION = "Carllm handles claims"  # the question, without punctuation
CHUNKS = {  # two chunks made of EXACTLY the same words, in a different order
    "A": "Carllm handles claims not quotes",  # the words of the question appear together, in order
    "B": "claims quotes not handles Carllm",  # the same words, scattered
}


def words(text):
    return text.lower().split()  # splits a text into lower-case words


def vector(text):
    counts = {}  # will hold how many times each word occurs
    for word in words(text):  # walks every word in the text
        counts[word] = counts.get(word, 0) + 1  # counts the word
    return counts  # hands back the word-count vector


def cosine(a, b):
    dot = sum(a[w] * b.get(w, 0) for w in a)  # multiplies matching words and adds them up
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))  # multiplies the two lengths
    return dot / norm  # returns the cosine similarity


doc_vectors = {name: vector(text) for name, text in CHUNKS.items()}  # BI-ENCODER: every chunk is turned into a vector ONCE, before any question exists
embed_calls = len(CHUNKS)  # counts the offline document passes just made


def bi_encoder_score(question, name):
    return cosine(vector(question), doc_vectors[name])  # compares the question vector with the stored chunk vector


def cross_encoder_score(question, text):
    doc = words(text)  # splits the chunk into words
    hits = [i for i, w in enumerate(doc) if w in words(question)]  # finds where each question word sits in the chunk
    if not hits:  # covers a chunk with no question word at all
        return 0.0  # scores it zero
    closeness = 1.0 / (1 + (max(hits) - min(hits)))  # rewards question words that appear close together in the chunk
    coverage = len(set(doc) & set(words(question))) / len(set(words(question)))  # rewards covering more of the question
    return round(coverage * closeness, 3)  # combines both into one pair score


print("BI-ENCODER   (chunk vectors precomputed, question compared to them):")  # heading for the first scorer
for name in CHUNKS:  # scores each chunk
    print(f"  chunk {name}: {bi_encoder_score(QUESTION, name):.3f}")  # prints the cosine
print("CROSS-ENCODER (question and chunk read together, one pass per pair):")  # heading for the second scorer
for name, text in CHUNKS.items():  # scores each chunk
    print(f"  chunk {name}: {cross_encoder_score(QUESTION, text):.3f}")  # prints the pair score

corpus_size, pool = 223, 20  # this project's chunk count and the pool size used in the tests
print(f"passes per question, bi-encoder: 1 query vector (the {corpus_size} chunk vectors already exist)")  # cost of stage 1
print(f"passes per question, cross-encoder over the whole corpus: {corpus_size}")  # cost if a cross-encoder scored everything
print(f"passes per question, cross-encoder over a pool of {pool}: {pool}")  # cost when it only scores the shortlist
# the toy scorers are not real models; the point is WHEN each one can run and what each one gets to see
