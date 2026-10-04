# The real bug this step found: DESCRIBED_IN must check the chunk's own CATEGORY, not just
# the node's LABEL. Runs on the real 223-chunk corpus, no database needed (pure Python).
from src.graph.extractor import GraphExtractor  # the P4-M4b rule-based extractor, unmodified
from src.graph.ingest import _HOME_CATEGORY, _plan_writes  # the real constant and function this bug lived in
from src.ingestion.pipeline import read_chunks  # reads the 223 real chunks written by P2

chunks = read_chunks()  # every chunk from data/chunks/*.json, in the job's real input order
extractor = GraphExtractor.from_chunks(chunks)  # builds the closed lists (products/roster/cities) from the corpus itself
extractions = extractor.extract_all(chunks)  # runs the extractor over every chunk, with the lookahead


def entity_id(node):
    # A hashable identity for a GraphNode: its label plus its sorted key items, so two
    # references to "the same" node compare equal even if they're different Python objects.
    return node.label, tuple(sorted(node.key.items()))


def naive_described_in(chunks, extractions):
    # The FIRST version of this rule: "the node's label is one DESCRIBED_IN allows as a
    # source", with no check on which chunk created it. This is the version that shipped
    # with the bug.
    home_labels = set(_HOME_CATEGORY)  # {"Product", "Contract", "Employee"} -- the 3 labels DESCRIBED_IN allows
    seen = {}  # every (entity, document) pair the naive rule would turn into an edge
    for chunk, extraction in zip(chunks, extractions, strict=True):  # walk chunk and its extraction together
        for node in extraction.nodes:  # every node THIS chunk's extraction produced
            if node.label in home_labels:  # the naive check: label alone, nothing about the chunk's category
                seen[(entity_id(node), chunk.doc_id)] = True  # record: this chunk "described" this entity
    return len(seen)  # total DESCRIBED_IN edges the naive rule would write


def fixed_described_in(chunks, extractions):
    # The FIXED version: the real _plan_writes function, which checks chunk.category too.
    return len(_plan_writes(chunks, extractions).described_in)  # ask the real code for the real count


naive = naive_described_in(chunks, extractions)  # run the naive (buggy) version
fixed = fixed_described_in(chunks, extractions)  # run the fixed (shipped) version
print(f"naive  (label only)          : {naive} DESCRIBED_IN edges")  # what the first version wrote
print(f"fixed  (label AND category)  : {fixed} DESCRIBED_IN edges")  # what actually ships today
print(f"difference                   : {naive - fixed} edges that were never really a home page")

# Show exactly which entities the naive version over-counted, and where the extra edges came from.
plan = _plan_writes(chunks, extractions)  # the real write-plan, built once, reused below
fixed_docs = {}  # entity identity -> set of documents the FIXED rule says describe it
for edge in plan.described_in:  # every DESCRIBED_IN edge the fixed rule actually produced
    key = (edge.source.label, tuple(sorted(edge.source.key.items())))  # the entity's identity
    fixed_docs.setdefault(key, set()).add(edge.target.key["doc_id"])  # record which document(s) describe it

naive_docs = {}  # entity identity -> set of documents the NAIVE rule says describe it
for chunk, extraction in zip(chunks, extractions, strict=True):  # same walk as naive_described_in
    for node in extraction.nodes:  # every node this chunk's extraction produced
        if node.label in _HOME_CATEGORY:  # the naive check again: label only
            naive_docs.setdefault(entity_id(node), set()).add(chunk.doc_id)  # record every chunk that named it

over_counted = {k: v for k, v in naive_docs.items() if len(v) > 1}  # entities "described" in >1 document
print(f"\nentities the naive rule described in MORE THAN ONE document ({len(over_counted)} of them):")
for key, docs in sorted(over_counted.items(), key=lambda kv: -len(kv[1])):  # worst offenders first
    name = dict(key[1]).get("name") or dict(key[1]).get("doc_id")  # the entity's display name or doc_id
    print(f"  {key[0]:<10} {name:<20} naive={len(docs)} fixed={len(fixed_docs.get(key, ()))}")  # compare both
