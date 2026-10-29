"""Find and close processes that belong to an application before uninstalling it."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from hito.core import winapi
from hito.core.fsutil import norm

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

NEVER_TOUCH = {"explorer.exe", "svchost.exe", "csrss.exe", "winlogon.exe", "lsass.exe", "services.exe",
               "dwm.exe", "smss.exe", "wininit.exe", "system", "registry"}


@dataclass
class AppProcess:
    pid: int
    name: str
    exe: str


def find_app_processes(install_location: str) -> list[AppProcess]:
    if psutil is None or not install_location or len(norm(install_location)) <= 3:
        return []
    root = norm(install_location)
    own = os.getpid()
    out = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        exe = p.info.get("exe") or ""
        if not exe or p.info["pid"] == own or (p.info.get("name") or "").lower() in NEVER_TOUCH:
            continue
        n = norm(exe)
        if n.startswith(root + os.sep):
            out.append(AppProcess(p.info["pid"], p.info.get("name") or os.path.basename(exe), exe))
    return out


def request_close(procs: list[AppProcess], wait_s: float = 8.0) -> list[AppProcess]:
    """Politely ask windows to close; returns processes still running afterwards."""
    if not procs:
        return []
    winapi.close_windows_of({p.pid for p in procs})
    deadline = time.time() + wait_s
    remaining = procs
    while time.time() < deadline:
        remaining = [p for p in procs if psutil.pid_exists(p.pid)]
        if not remaining:
            break
        time.sleep(0.4)
    return remaining


def force_end(procs: list[AppProcess]) -> list[str]:
    """Terminate processes after the user explicitly chose 'End task'. Returns error messages."""
    errors = []
    for p in procs:
        try:
            psutil.Process(p.pid).terminate()
        except psutil.NoSuchProcess:
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{p.name} ({p.pid}): {exc}")
    return errors
