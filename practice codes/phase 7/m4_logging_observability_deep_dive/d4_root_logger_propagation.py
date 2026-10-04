# Minimal demo of C5: logging.basicConfig() attaches a handler to the ROOT logger, and every
# OTHER logger (including a stand-in for a third-party dependency) propagates its own records
# up to root by default -- so a root-level formatter affects the whole process, not just
# "your own" log calls. Also shows the one real way a logger can OPT OUT of this.
import logging

# Simulates this project's own real setup: one custom JSON-ish formatter, attached at root.
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("CUSTOM-FORMAT | %(name)s | %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler])

print("--- a logger for THIS project's own code ---")
app_logger = logging.getLogger("src.api.main")
app_logger.info("a real request was handled")

print("\n--- a logger standing in for a THIRD-PARTY dependency (e.g. httpx) ---")
print("(it never called basicConfig, never attached its own handler -- just logs normally)")
dependency_logger = logging.getLogger("some_dependency.internal")
dependency_logger.info("an HTTP request the dependency made internally")
print("-> printed in the SAME custom format -- it propagated all the way up to root")

print("\n--- a logger that explicitly opts OUT of propagation (mirrors uvicorn's own access log) ---")
independent_logger = logging.getLogger("independent_access_log")
independent_logger.propagate = False
independent_handler = logging.StreamHandler()
independent_handler.setFormatter(logging.Formatter("INDEPENDENT | %(message)s"))
independent_logger.addHandler(independent_handler)
independent_logger.info("this one manages its own formatting entirely")
print("-> never reached root's handler at all -- propagate=False stops it at its own logger")

print("\nlogger hierarchy check:")
print("  app_logger.propagate        =", app_logger.propagate)
print("  dependency_logger.propagate =", dependency_logger.propagate)
print("  independent_logger.propagate=", independent_logger.propagate)
print("  root logger's own handlers  =", logging.getLogger().handlers)
