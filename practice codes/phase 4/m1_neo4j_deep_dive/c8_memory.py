# C8: heap vs page cache -- what we configured, what the JVM reports, what the container uses.
import subprocess                                               # to call docker stats

from src.graph.setup import build_driver                        # the project's driver factory

with build_driver() as driver, driver.session() as session:
    # 1. The settings Neo4j loaded (set by our NEO4J_server_memory_* variables)
    rows = session.run(
        "SHOW SETTINGS YIELD name, value WHERE name IN "
        "['server.memory.heap.initial_size', 'server.memory.heap.max_size', 'server.memory.pagecache.size'] "
        "RETURN name, value ORDER BY name"
    ).data()
    for row in rows:
        print(f"1. {row['name']:<34} = {row['value']}")

    # 2. What the Java virtual machine inside the container reports about its heap right now
    heap = session.run(
        "CALL dbms.queryJmx('java.lang:type=Memory') YIELD attributes "
        "RETURN attributes.HeapMemoryUsage.value.properties AS heap"
    ).single()["heap"]
    mib = lambda b: f"{b / 2**20:,.0f} MiB"                     # bytes -> MiB for display
    print(f"2. JVM heap: used {mib(heap['used'])}, committed {mib(heap['committed'])}, max {mib(heap['max'])}")

# 3. How big the graph's store files are (what the page cache would hold). apoc.monitor.store is in
#    APOC *Extended*, not the Core jar we enabled, so the size is read from the filesystem instead.
store = subprocess.run(["docker", "exec", "advancerag-neo4j-1", "du", "-sh", "/data/databases/neo4j"],
                       capture_output=True, text=True).stdout.split()[0]
print(f"3. store files of database 'neo4j' on disk: {store}  (a 512 MiB page cache holds all of it)")

# 4. The container as a whole: heap + page cache + everything else the process uses
stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}: {{.MemUsage}}"],
                       capture_output=True, text=True).stdout.strip().splitlines()
for line in stats:
    print("4.", line)
