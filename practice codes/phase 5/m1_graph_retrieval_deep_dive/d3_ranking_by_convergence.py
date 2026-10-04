# Minimal demo of C4: ranking by (hop distance, how many query entities AGREE), and the
# bug the first version had. Two rules run side by side on the situation that exposed it:
# a Company and the Product it licenses, two hops apart, both named in one question.
reaches = {
    # for each seed entity: the chunks it reached, and the hop distance it reached each at
    ("Product", "Carllm"): {  # the Product reaches all three chunks directly (hop 0)
        "contracts/DriveSmart.md_0": 0,
        "contracts/Velocity.md_0": 0,
        "products/Carllm.md_2": 0,
    },
    ("Company", "Velocity"): {  # the Company mentions ONE chunk directly, and the rest via Carllm
        "contracts/Velocity.md_0": 0,  # its own contract chunk: a direct, hop-0 hit
        "contracts/DriveSmart.md_0": 2,  # reached only by going Company -> Contract -> Product
        "products/Carllm.md_2": 2,  # same: two hops away, through the Product
    },
}

chunk_ids = sorted({chunk for hops in reaches.values() for chunk in hops})  # every chunk, A-Z
best_hop = {c: min(h[c] for h in reaches.values() if c in h) for c in chunk_ids}  # nearest reach


def any_distance_count(chunk_id):
    # the ORIGINAL rule: a seed counts if it reached this chunk at ANY distance at all
    return sum(1 for hops in reaches.values() if chunk_id in hops)


def nearest_distance_count(chunk_id):
    # the CORRECTED rule: a seed counts only if it reached this chunk at the chunk's nearest distance
    return sum(1 for hops in reaches.values() if hops.get(chunk_id) == best_hop[chunk_id])


for name, count in (("original: any distance", any_distance_count), ("corrected: nearest distance", nearest_distance_count)):
    ranked = sorted(chunk_ids, key=lambda c: (best_hop[c], -count(c), c))  # hop, then agreement, then A-Z
    print(f"\n{name}")
    for chunk_id in ranked:
        print(f"  {chunk_id:<28} hop={best_hop[chunk_id]}  agreeing seeds={count(chunk_id)}")
# the original rule gives every chunk 2 agreeing seeds, so the alphabet decides (DriveSmart wins);
# the corrected rule gives only the Velocity chunk 2, because only there do BOTH seeds hit at hop 0
