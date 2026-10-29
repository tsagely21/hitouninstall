"""Append-only operation log (JSON lines) for every destructive or system action."""
from __future__ import annotations

import json
import os
import threading
import time

from hito.settings import sub_dir

_lock = threading.Lock()


def log_path() -> str:
    return os.path.join(sub_dir("logs"), "operations.jsonl")


def record(op: str, target: str = "", result: str = "ok", **extra) -> dict:
    """op examples: delete, recycle, skip, dry-run, uninstall, registry-delete, cleanup-summary."""
    entry = {"ts": time.time(), "op": op, "target": target, "result": result}
    entry.update({k: v for k, v in extra.items() if v is not None})
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        try:
            with open(log_path(), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
    return entry


def read_recent(limit: int = 2000) -> list[dict]:
    try:
        with open(log_path(), encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out
