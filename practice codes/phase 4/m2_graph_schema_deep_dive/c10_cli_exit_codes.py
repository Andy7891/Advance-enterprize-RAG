# C10: the CLI -- exit codes, --check vs apply, and reusing the readiness check. Runs against the REAL graph.
# Nothing here changes the database: the real schema is already in force, so applying it again is a no-op.
import subprocess                                                        # to run the CLI exactly as a shell or CI job would
import sys                                                               # for the path of the Python interpreter running this demo

from src.graph.schema import main                                        # the CLI entry point, called in-process
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)


def constraint_count() -> int:                                           # how many constraints the real database has right now
    with build_driver() as driver, driver.session() as session:          # open a connection pool, then one session
        return session.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS c").single()["c"]  # count them


before = constraint_count()                                              # remember the state before we touch anything
print("1. constraints before          :", before)                        # 8: the model

print("2. main(['--check'])           :")                                # verify only
code = main(["--check"])                                                 # prints one line and returns an exit code
print("   returned                    :", code)                          # 0 = schema in force

print("3. main([])  (apply)           :")                                # apply, then verify
code = main([])                                                          # every constraint already exists, so nothing is created
print("   returned                    :", code)                          # 0

print("4. wrong address, --check      :")                                # what a stopped or unreachable server looks like
code = main(["--check", "--uri", "bolt://localhost:1"])                  # nothing listens on port 1
print("   returned                    :", code)                          # 1, with a readable reason and no stack trace

print("5. a --password flag           :")                                # secrets never go on the command line
try:                                                                     # argparse rejects unknown flags by exiting
    main(["--password", "secret"])                                       # not a flag this CLI defines
except SystemExit as exc:                                                # argparse calls sys.exit(2)
    print("   exit code                   :", exc.code)                  # 2 = usage error

print("6. as a shell/CI job sees it   :")                                # a separate process, like `python -m src.graph.schema --check`
done = subprocess.run(                                                   # run the module as a command
    [sys.executable, "-m", "src.graph.schema", "--check"],               # the same command the README documents
    capture_output=True, text=True,                                      # capture its output instead of printing it
)                                                                        # end of the call
print("   stdout                      :", done.stdout.strip())          # the one summary line
print("   process exit status         :", done.returncode)               # 0: what `&&` and CI steps look at

print("7. constraints after           :", constraint_count(), "(unchanged:", constraint_count() == before, ")")  # nothing was modified
