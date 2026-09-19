# C2: NEO4J_AUTH is applied only when the data volume is FIRST created. Evidence from the live container.
import subprocess                                             # to call the docker CLI

CONTAINER = "advancerag-neo4j-1"                              # the compose container name


def sh(cmd):                                                  # run a shell command inside the container
    return subprocess.run(["docker", "exec", CONTAINER, "sh", "-c", cmd],
                          capture_output=True, text=True).stdout.strip()


# 1. The volume mounted at /data: the auth file lives in the "system" database area
print("1. /data contents      :", sh("ls /data").split())
print("   auth file present   :", sh("ls /data/dbms 2>/dev/null") or "(none)")

# 2. When was the volume initialised, and how often has the container started since?
created = subprocess.run(["docker", "volume", "inspect", "advancerag_neo4j_data", "--format", "{{.CreatedAt}}"],
                         capture_output=True, text=True).stdout.strip()
print("2. volume created at   :", created)

# 3. The entrypoint tries to set the initial password on EVERY start; the log explains why it has no effect
logs = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True)
lines = [l for l in (logs.stdout + logs.stderr).splitlines() if "only take effect" in l]
print("3. entrypoint message  :", lines[-1] if lines else "(not found)")
print("   seen", len(lines), "time(s) in this container's log")
