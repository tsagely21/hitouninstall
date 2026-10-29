"""`HitoUninstall.exe --self-test REPORT.json`: read-only check that every engine works in this build.

Nothing is deleted or changed. Useful after packaging and for troubleshooting on another PC.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import traceback


def _check(results: dict, name: str, fn):
    t = time.time()
    try:
        results[name] = {"ok": True, "detail": fn(), "seconds": round(time.time() - t, 2)}
    except Exception as exc:  # noqa: BLE001
        results[name] = {"ok": False, "detail": f"{exc}\n{traceback.format_exc(limit=4)}"}


def run(report_path: str = "") -> int:
    from hito import __version__
    from hito.apps.inventory import registry_apps, store_apps
    from hito.apps.launchers import steam_games
    from hito.cleanup.catalog import build_catalog
    from hito.cleanup.engine import scan_catalog
    from hito.core import winapi
    from hito.core.categories import Classifier
    from hito.core.drives import list_drives
    from hito.core.scanner import ScanOptions, StorageScanner
    from hito.dupes.finder import find_duplicates
    from hito.safety.deleter import PERMANENT, Deleter
    from hito.safety.protect import USER, SafetyPolicy

    results: dict = {"version": __version__, "admin": winapi.is_admin(), "program": winapi.program_path()}
    _check(results, "imports", lambda: [m.__name__ for m in (__import__("psutil"), __import__("send2trash"),
                                                              __import__("PySide6.QtWidgets"))])
    _check(results, "drives", lambda: [f"{d.root} {d.percent:.0f}%" for d in list_drives()])

    def scan():
        r = StorageScanner(os.path.expanduser("~"), Classifier(), ScanOptions(workers=4)).run()
        return {"files": r.total_files, "bytes": r.total_size, "errors": r.error_count}
    _check(results, "scanner(home)", scan)
    _check(results, "registry_apps", lambda: len(registry_apps()))
    _check(results, "store_apps", lambda: len(store_apps()))
    _check(results, "steam_games", lambda: len(steam_games()))
    _check(results, "cleanup_scan", lambda: sum((s.bytes or 0) for s in scan_catalog(build_catalog())))

    def dupes_and_dry_delete():
        with tempfile.TemporaryDirectory() as d:
            data = os.urandom(4096)
            for n in ("a.bin", "b.bin"):
                with open(os.path.join(d, n), "wb") as fh:
                    fh.write(data)
            groups = find_duplicates([d], min_size=1, protect_system=False)
            rep = Deleter(SafetyPolicy(USER), mode=PERMANENT, dry_run=True).delete([os.path.join(d, "a.bin")])
            still = os.path.exists(os.path.join(d, "a.bin"))
            return {"groups": len(groups), "dry_run_kept_file": still, "simulated": rep.removed}
    _check(results, "duplicates+dry_run", dupes_and_dry_delete)
    results["all_ok"] = all(v.get("ok") for v in results.values() if isinstance(v, dict))
    text = json.dumps(results, indent=2, default=str)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0 if results["all_ok"] else 1
