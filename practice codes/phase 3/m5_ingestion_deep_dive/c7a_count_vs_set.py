# Why verification compares SETS of chunk_ids, not counts.
disk = {"about_0", "about_1", "carllm_0", "carllm_1"}
stored = {"about_0", "about_1", "carllm_0", "old_policy_0"}    # carllm_1 missing, old_policy_0 stale

print("count check : disk", len(disk), "| stored", len(stored), "->", "PASS" if len(disk) == len(stored) else "FAIL")
missing = sorted(disk - stored)
unexpected = sorted(stored - disk)
print("set check   : missing", missing, "| unexpected", unexpected, "->", "PASS" if not (missing or unexpected) else "FAIL")
