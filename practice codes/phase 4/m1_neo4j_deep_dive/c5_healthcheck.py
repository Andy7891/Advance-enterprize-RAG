# C5: the healthcheck -- shell parameter expansion, compose's $$ escaping, and Docker's health log.
import json                                                     # to parse docker inspect output
import subprocess                                               # to run docker

# 1. Shell parameter expansion, shown with a FAKE value (never the real password)
demo = subprocess.run(
    ["docker", "exec", "advancerag-neo4j-1", "sh", "-c",
     'X="neo4j/s3cr/et"; echo "user=${X%%/*}"; echo "password=${X#*/}"'],   # %%/* cuts from the first "/" on; #*/ cuts up to it
    capture_output=True, text=True).stdout.split()
print("1. with X='neo4j/s3cr/et'   :", demo, "<- only the FIRST '/' splits, so a '/' in a password survives")

# 2. What compose passes to Docker: $$ became a single $, so the CONTAINER'S shell expands it
out = subprocess.run(["docker", "inspect", "advancerag-neo4j-1", "--format", "{{json .Config.Healthcheck}}"],
                     capture_output=True, text=True).stdout
health = json.loads(out)                                        # the healthcheck config as stored by Docker
print("2. stored test command      :", health["Test"][1][:95], "...")
print("   interval/timeout/retries :", health["Interval"] // 10**9, "s /", health["Timeout"] // 10**9, "s /", health["Retries"])

# 3. The last health results Docker recorded
state = json.loads(subprocess.run(["docker", "inspect", "advancerag-neo4j-1", "--format", "{{json .State.Health}}"],
                                  capture_output=True, text=True).stdout)
print("3. status                   :", state["Status"], "| failing streak:", state["FailingStreak"])
for entry in state["Log"][-2:]:                                 # Docker keeps the last few probe results
    print("   probe exit", entry["ExitCode"], "| output:", entry["Output"].strip().replace("\n", " | ")[:60])

# 4. The same probe with a WRONG password: what "unhealthy" would look like
bad = subprocess.run(["docker", "exec", "advancerag-neo4j-1", "cypher-shell", "-a", "bolt://localhost:7687",
                      "-u", "neo4j", "-p", "definitely-wrong", "RETURN 1"], capture_output=True, text=True)
print("4. wrong password probe     : exit", bad.returncode, "|", (bad.stderr or bad.stdout).strip()[:90])
