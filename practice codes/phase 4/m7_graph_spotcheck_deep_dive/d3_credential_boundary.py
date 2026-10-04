# Minimal demo of C5: the SAME password can travel through two different paths -- one a
# trusted, human-configured path (fine), the other an automated UI-typing path (never done
# by this agent, regardless of whose database it is). No real password is used or printed.
import os

# a stand-in .env value, never a real secret -- this script never touches the real one
os.environ["DEMO_DB_PASSWORD"] = "example-only"


def connect_with_app_config():
    # reads the password from an environment variable the HUMAN already set up
    password = os.environ["DEMO_DB_PASSWORD"]
    # this value flows straight into a driver object the human's own code controls
    return {"user": "neo4j", "password": password, "path": "env -> driver, human-authored code"}


def type_into_a_login_form(password_value):
    # this function represents an AGENT typing a value into a UI FIELD it does not control
    keystrokes = [f"type character: {c!r}" for c in password_value]
    # the value now exists as a sequence of simulated UI actions, not as a variable in trusted code
    return {"keystrokes": keystrokes[:3] + ["..."], "path": "value -> simulated UI automation"}


connection = connect_with_app_config()
print("Path A (application code reading its own config):")
print(" ", connection)
print("  -- this is what src/graph/setup.py's build_driver() actually does. Normal, expected,")
print("     the same pattern used everywhere in this project to reach Neo4j.")

typed = type_into_a_login_form(os.environ["DEMO_DB_PASSWORD"])
print("\nPath B (an agent filling in a login form):")
print(" ", typed)
print("  -- structurally identical VALUE, but now it exists as actions taken inside a UI")
print("     the agent does not own. This is the path this project's agent never takes,")
print("     regardless of who owns the database being logged into.")
