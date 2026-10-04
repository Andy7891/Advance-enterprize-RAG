# Minimal demo of C1: what a LogRecord actually contains, how extra={} attaches new fields to
# one, and how a custom Formatter turns it into real JSON -- built up step by step, the exact
# pieces _JsonFormatter (src/api/main.py) is made of.
import json
import logging

# Step 1: a LogRecord is a real, ordinary Python object with real attributes -- not magic.
record = logging.LogRecord(
    name="demo",
    level=logging.INFO,
    pathname=__file__,
    lineno=10,
    msg="hello %s",
    args=("world",),
    exc_info=None,
)
print("record.getMessage() does the %-interpolation for you:", record.getMessage())
print("a few of its own built-in attributes:")
for key in ("name", "levelname", "created", "msecs", "process", "thread"):
    print(f"  {key} = {getattr(record, key)!r}")

# Step 2: extra={...} on a real logging call just SETS plain attributes on the record object.
logger = logging.getLogger("demo")
handler = logging.Handler()
captured = []
handler.emit = lambda r: captured.append(r)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.info("a request happened", extra={"method": "GET", "duration_ms": 12.3})
real_record = captured[0]
print("\nafter extra={'method': 'GET', 'duration_ms': 12.3}:")
print("  real_record.method       =", real_record.method)
print("  real_record.duration_ms  =", real_record.duration_ms)
print("  -> extra fields are just ordinary attributes, set directly on the record object")

# Step 3: telling "extra" fields apart from the record's OWN built-in ones -- computed from a
# real instance, not hand-copied from documentation (exactly what src/api/main.py does).
reserved = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}
extra_only = {k: v for k, v in vars(real_record).items() if k not in reserved}
print("\nfields NOT in a fresh LogRecord's own reserved set:", extra_only)

# Step 4: a minimal version of _JsonFormatter, built from exactly the pieces above.
def to_json(record: logging.LogRecord) -> str:
    payload = {
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    for key, value in vars(record).items():
        if key not in reserved:
            payload[key] = value
    return json.dumps(payload)


print("\nfinal JSON line:", to_json(real_record))
