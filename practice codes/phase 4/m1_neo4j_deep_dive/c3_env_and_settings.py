# C3: where credentials come from -- .env, compose interpolation, pydantic-settings. Never prints the password.
import os                                                      # to set/unset environment variables
import shutil                                                  # to move .env aside temporarily
import subprocess                                              # to call `docker compose config`
from pathlib import Path                                       # file paths

# 1. Compose interpolation: ${VAR:-default} substitutes a default, ${VAR:?msg} stops with an error
env = {**os.environ}                                           # copy of the current environment
env.pop("NEO4J_PASSWORD", None)                                # make sure the shell does not supply it
shutil.move(".env", ".env.demo_backup")                        # hide .env so compose cannot read it
try:
    r = subprocess.run(["docker", "compose", "config", "--quiet"], capture_output=True, text=True, env=env)
    print("1. compose without .env :", "exit", r.returncode, "|", r.stderr.strip().splitlines()[-1][:110])
finally:
    shutil.move(".env.demo_backup", ".env")                    # always put .env back
r = subprocess.run(["docker", "compose", "config", "--quiet"], capture_output=True, text=True)
print("   compose with .env    :", "exit", r.returncode)

# 2. What compose substituted into the neo4j service (password masked)
out = subprocess.run(["docker", "compose", "config"], capture_output=True, text=True).stdout
auth_line = next(l.strip() for l in out.splitlines() if "NEO4J_AUTH" in l)
user, _, password = auth_line.split(": ", 1)[1].partition("/")  # "neo4j/<password>" -> user, password
print("2. NEO4J_AUTH resolved  :", f"{user}/<{len(password)} characters>")

# 3. pydantic-settings priority: environment variable > .env file > class default
from src.core.config import Settings                           # the project's settings class

print("3. from .env            :", Settings().neo4j_uri)      # .env sets bolt://localhost:7687
os.environ["NEO4J_URI"] = "bolt://from-env-var:7687"            # a real env var beats .env
print("   env var overrides    :", Settings().neo4j_uri)
del os.environ["NEO4J_URI"]
print("   class default        :", Settings(_env_file=None).neo4j_uri, "(when no .env is read)")
print("   default password     :", repr(Settings(_env_file=None).neo4j_password), "(empty, no well-known value)")

# 4. .env must never be committed
r = subprocess.run(["git", "check-ignore", "-v", ".env"], capture_output=True, text=True)
print("4. git check-ignore .env:", r.stdout.strip())
print("   .env tracked by git? :", bool(subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True).stdout.strip()))
print("   .env still present   :", Path(".env").exists())
