# Sync = set arithmetic between what the index holds and what the source holds.
stored = {"doc-about": {"about_0", "about_1"}, "doc-carllm": {"carllm_0"}, "doc-old-policy": {"policy_0", "policy_1"}}
disk = {"doc-about": {"about_0", "about_1"}, "doc-carllm": {"carllm_0"}, "doc-new-faq": {"faq_0"}}

orphans = set(stored) - set(disk)          # in the index, gone from the source
new_docs = set(disk) - set(stored)         # in the source, not yet in the index
both = set(stored) & set(disk)             # in both: re-upserted (delete-by-doc_id + upsert)
print("stored documents :", sorted(stored))
print("disk documents   :", sorted(disk))
print("orphans (delete) :", sorted(orphans))
print("new (insert)     :", sorted(new_docs))
print("in both (replace):", sorted(both))


def sync(stored, disk, documents_failed):
    result = {doc: set(ids) for doc, ids in stored.items()}
    for doc, ids in disk.items():                       # upsert stage
        result[doc] = set(ids)
    orphan_docs = set(stored) - set(disk)
    if documents_failed:                                # the guard
        return result, f"kept {len(orphan_docs)} orphan(s) because {documents_failed} document(s) failed to chunk"
    for doc in orphan_docs:                             # orphan stage
        del result[doc]
    return result, f"removed {len(orphan_docs)} orphan(s)"


for failed in (0, 1):
    result, note = sync(stored, disk, failed)
    print(f"documents_failed={failed}: index now {sorted(result)} -> {note}")
