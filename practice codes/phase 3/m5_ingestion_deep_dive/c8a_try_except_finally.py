# try / except / finally + re-raise: record a failure without swallowing it.
import json


def job(fail_at):
    report = {"status": "running", "failed_stage": None, "error": None, "stages_done": []}
    current = None
    try:
        for current in ("load", "embed", "write"):
            if current == fail_at:
                raise ValueError(f"something broke in {current}")
            report["stages_done"].append(current)
        report["status"] = "succeeded"
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["failed_stage"] = current
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise                                    # the caller still sees the original exception
    finally:
        print("   finally -> writing log:", json.dumps(report))


for fail_at in (None, "embed"):
    print(f"job(fail_at={fail_at!r})")
    try:
        result = job(fail_at)
        print("   returned:", result["status"])
    except ValueError as e:
        print("   caller received the exception:", repr(e))


def swallowing_job():
    try:
        raise ValueError("something broke")
    except Exception:
        return {"status": "failed"}              # no re-raise: the caller cannot tell it failed

print("swallowing version returns:", swallowing_job(), "<- no exception, exit code would be 0")
