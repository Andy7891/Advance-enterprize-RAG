# Minimal demo of C3: Docker Desktop's WSL2 VM has ITS OWN memory ceiling, separate from
# (and smaller than) this host machine's real RAM -- read live with `docker stats`, the
# exact command that first surfaced this during P6-M1.
import subprocess  # lets this script run a real, read-only docker command

CONTAINERS = ["advancerag-ollama-1", "advancerag-neo4j-1", "advancerag-qdrant-1"]
# the three services this project's docker-compose.yml defines

result = subprocess.run(  # runs the real `docker stats` command, once, no streaming
    ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}", *CONTAINERS],
    capture_output=True,
    text=True,
)
print("LIVE right now (docker stats --no-stream):")  # heading for the real, current reading
print(result.stdout or "(docker not reachable, or these containers are not running)")  # the real output

print("\nRECORDED during this project's P6-M1 (from decision.md's own correction note):")
# a frozen, documented reading from the actual incident -- not re-run here on purpose,
# since reproducing the crash on demand isn't something a demo script should risk doing
INCIDENT_READING = {
    "before Mistral was pulled": {"ollama": "121 MiB / 7.6 GiB (1.6%)"},
    "warm, after the crash and recovery": {
        "ollama": "5.97 GiB / 7.6 GiB (78.5%)",
        "neo4j": "894 MiB / 7.6 GiB (11.5%)",
        "qdrant": "139 MiB / 7.6 GiB (1.8%)",
    },
}
for moment, readings in INCIDENT_READING.items():  # walks the two recorded snapshots
    print(f"  {moment}:")  # names which moment this snapshot is from
    for name, usage in readings.items():  # walks each container's reading at that moment
        print(f"    {name:<8} {usage}")  # prints the recorded real usage

print(
    "\nThe crash happened BETWEEN these two snapshots -- during Mistral's cold load, "
    "briefly higher than either steady-state reading, which pushed the shared ~7.6 GiB "
    "VM budget over the edge and took Neo4j and Qdrant down with it (see notes/step-6.1)."
)
