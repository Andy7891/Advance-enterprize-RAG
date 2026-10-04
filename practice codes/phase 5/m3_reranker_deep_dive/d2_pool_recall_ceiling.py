# Minimal demo of C3: a reranker can only reorder the pool it is given, so pool recall is its ceiling.
# The ranks below were MEASURED on the real corpus (hybrid retriever, no Cohere): the rank of the first
# chunk in the hybrid list of N that the project labels accept. None means it is not in the list.
MEASURED = {  # label -> {pool size N: rank of the first accepted chunk in the hybrid list of N}
    "Carllm product page": {3: None, 10: 3, 20: 3, 30: 3, 50: 3},  # the page appears once N reaches 10
    "CEO (label: About page, names the founder)": {3: 2, 10: 3, 20: 15, 30: 21, 50: 9},  # position moves with N
    "CEO (direct: Avery Lancaster's own record)": {3: None, 10: None, 20: None, 30: None, 50: 17},  # only appears at N=50
}
FINAL_K = 3  # how many chunks the reranker keeps

print(f"{'answer':<46} N=3   N=10  N=20  N=30  N=50")  # column headings
for label, by_pool in MEASURED.items():  # goes through each labelled answer
    cells = []  # will hold one cell per pool size
    for n, rank in by_pool.items():  # goes through each pool size
        in_pool = rank is not None  # checks whether the answer is inside the pool at all
        as_is = in_pool and rank <= FINAL_K  # checks whether it is already in the top 3 without any reranker
        cells.append("top3" if as_is else ("in-pool" if in_pool else "LOST"))  # top3 = fine already, in-pool = a reranker could lift it, LOST = out of reach
    print(f"{label:<46} " + "  ".join(f"{c:<7}" for c in cells))  # prints the row
print("top3 = already in the top 3 | in-pool = a perfect reranker could bring it to the top 3 | LOST = no reranker can")  # legend
