# Minimal demo of C3: a synthetic test fixture has to be shaped like the REAL thing the
# code under test expects -- not just "clearly fake." An underscore prefix looks safely
# test-only to a human, but breaks the extractor's own regex. Uses the REAL extractor.
from src.graph.extractor import GraphExtractor
from src.ingestion.schemas import DocumentChunk, compute_chunk_id, compute_doc_id

EX = GraphExtractor(products=[], employees=["__test_Founder"], cities={})  # a tiny extractor, roster of one


def company_page(text):
    # A minimal "About page" chunk, the only category whose text triggers _company_page.
    path = "company/about.md"
    return DocumentChunk(
        chunk_id=compute_chunk_id(path, 0),
        doc_id=compute_doc_id(path),
        source_path=path,
        category="company",
        title="about",
        chunk_index=0,
        text=text,
        char_count=len(text),
        approx_token_count=len(text) // 4,
    )


print("Attempt 1: an underscore-prefixed fixture name, the naive choice for 'test-only'")
chunk = company_page("Insurellm was founded by __test_Founder in 2015.")  # looks safe to a human
extraction = EX.extract(chunk)
founded_edges = [e for e in extraction.edges if e.type == "FOUNDED"]
print(f"  FOUNDED edges found: {len(founded_edges)}  warnings: {extraction.warnings}")
# The _FOUNDED_BY regex requires `[A-Z][\w.'-]*` -- a CAPITAL LETTER first. An underscore
# is not a capital letter, so the pattern never matches "__test_Founder" at all: the
# extractor doesn't even NOTICE a founder was mentioned, let alone warn about it.

print("\nAttempt 2: a capitalised, word-shaped fixture name")
EX2 = GraphExtractor(products=[], employees=["M6Fixture Founder"], cities={})
chunk2 = company_page("Insurellm was founded by M6Fixture Founder in 2015.")
extraction2 = EX2.extract(chunk2)
founded_edges2 = [e for e in extraction2.edges if e.type == "FOUNDED"]
print(f"  FOUNDED edges found: {len(founded_edges2)}  warnings: {extraction2.warnings}")
# "M6Fixture" starts with a capital M, so the SAME regex matches it exactly the way it
# would match a real name like "Avery Lancaster" -- the fixture now exercises the real rule.
