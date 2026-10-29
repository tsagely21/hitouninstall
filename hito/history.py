"""Scan history: compact summaries of finished storage scans."""
from __future__ import annotations

import json
import os
import time

from hito.settings import data_dir

MAX_ENTRIES = 50


def _path() -> str:
    return os.path.join(data_dir(), "history.json")


def load() -> list[dict]:
    try:
        with open(_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add_scan(result, classifier) -> dict:
    """Store a summary of a ScanResult (no file lists)."""
    entry = {
        "ts": time.time(),
        "root": result.root_path,
        "total": result.total_size,
        "files": result.total_files,
        "dirs": result.total_dirs,
        "elapsed": round(result.elapsed, 1),
        "errors": result.error_count,
        "cancelled": result.cancelled,
        "cloud_only": result.cloud_only_bytes,
        "categories": {k: v for k, v in sorted(result.categories.items(), key=lambda kv: -kv[1])},
        "labels": {k: classifier.label(k) for k in result.categories},
        "colors": {k: classifier.color(k) for k in result.categories},
        "top_dirs": [(d.path, d.size) for d in result.largest_dirs(limit=10)],
    }
    items = [entry] + load()
    try:
        with open(_path(), "w", encoding="utf-8") as fh:
            json.dump(items[:MAX_ENTRIES], fh)
    except OSError:
        pass
    return entry


def clear() -> None:
    try:
        os.remove(_path())
    except OSError:
        pass
