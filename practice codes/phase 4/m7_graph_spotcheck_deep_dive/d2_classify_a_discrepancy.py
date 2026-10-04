# Minimal demo of C4: when a check finds a mismatch, WHERE it lives decides what to do
# about it. Three real P4-M7 findings, run through the same small classifier.
def classify(matches_pipeline_output, matches_source_document, is_a_stated_limitation):
    # everything lines up: pipeline, source, and expectations all agree
    if matches_pipeline_output and matches_source_document:
        return "NO DISCREPANCY -- matches"
    # the gap already has a WRITTEN-DOWN design reason (checked before anything else)
    if is_a_stated_limitation:
        return "ACCEPTED LIMITATION -- document it, do not change code"
    # the pipeline did what its own code says it should, but the SOURCE disagrees
    if matches_pipeline_output and not matches_source_document:
        return "SOURCE DATA ISSUE -- flag the data, do not touch the pipeline"
    # the pipeline's output matches NEITHER its own intent NOR the real source
    return "PIPELINE BUG -- fix the code"


# Finding 1: Rachel Martinez's "Marketllm" typo. The extractor's OWN behaviour (skip an
# unrecognised name) is exactly what P4-M4b documented as its rule-based design's limit.
print("Marketllm typo         :", classify(
    matches_pipeline_output=True, matches_source_document=False, is_a_stated_limitation=True
))

# Finding 2: overview.md's contract-count narrative. The GRAPH matches its own real files
# (the pipeline behaved correctly); the SOURCE's own prose page is what's inconsistent.
print("overview.md contradiction:", classify(
    matches_pipeline_output=True, matches_source_document=False, is_a_stated_limitation=False
))

# Finding 3 (hypothetical): if DESCRIBED_IN had shipped its original, uncaught bug, it would
# have matched NEITHER what the pipeline was supposed to do NOR the real source documents.
print("hypothetical DESCRIBED_IN bug:", classify(
    matches_pipeline_output=False, matches_source_document=False, is_a_stated_limitation=False
))
