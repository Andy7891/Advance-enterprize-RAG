# C4: the Neo4j image turns NEO4J_* variables into neo4j.conf settings -- and rejects unknown ones.
import subprocess                                                # to run docker


def env_to_setting(name):                                        # the image's naming rule, re-implemented
    key = name[len("NEO4J_"):]                                   # drop the prefix
    key = key.replace("__", "\x00")                              # protect double underscores
    key = key.replace("_", ".")                                  # single underscore -> dot
    return key.replace("\x00", "_")                              # double underscore -> single underscore


for name in ("NEO4J_server_memory_heap_max__size", "NEO4J_server_memory_pagecache_size", "NEO4J_PASSWORD"):
    print(f"1. {name:<38} -> setting {env_to_setting(name)!r}")

# 2. What the running server actually loaded from our variables
out = subprocess.run(["docker", "exec", "advancerag-neo4j-1", "sh", "-c",
                      "grep -E '^server.memory.(heap|pagecache)' conf/neo4j.conf"],
                     capture_output=True, text=True).stdout.strip()
print("2. conf/neo4j.conf written by the entrypoint:")
for line in out.splitlines():
    print("     ", line)

# 3. A throwaway container (no volume, no ports) with one unknown NEO4J_* variable: it refuses to start
r = subprocess.run(["docker", "run", "--rm", "-e", "NEO4J_AUTH=none", "-e", "NEO4J_PASSWORD=x", "neo4j:5.26.0"],
                   capture_output=True, text=True, timeout=180)
failure = next((l for l in (r.stdout + r.stderr).splitlines() if "Unrecognized setting" in l), "(no error line)")
print("3. throwaway container exit code:", r.returncode)
print("   ", failure.replace("PASSWORD x", "PASSWORD <value>")[:150])
