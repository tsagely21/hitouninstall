"""Run an uninstaller and detect when it really finishes.

Many uninstallers (NSIS, Inno Setup) copy themselves to %TEMP%, start the
copy and exit immediately, so "the process ended" is not "the app is gone".
Completion is decided by the app's own registration disappearing (uninstall
key, Store package, launcher manifest), while helper processes are tracked.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from hito.apps.inventory import AppRecord
from hito.core import winapi
from hito.safety import oplog
from hito.uninstall.methods import UninstallMethod

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None
try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

HELPER_NAME_RE = re.compile(r"^(au_|un_a|un_|_iu|unins|uninst|uninstall|_un|setup|msiexec|is-)", re.I)
MSI_CODES = {0: "Completed", 1602: "Cancelled by the user", 1605: "Not installed (already removed)",
             1641: "Completed - Windows is restarting", 3010: "Completed - restart required", 1618: "Another installation is running"}


@dataclass
class Outcome:
    status: str           # removed | still_installed | failed | cancelled | timeout
    message: str
    exit_code: int | None = None
    elapsed: float = 0.0
    restart_needed: bool = False

    @property
    def removed(self) -> bool:
        return self.status == "removed"


def still_installed(app: AppRecord) -> bool:
    if app.source == "store":
        r = winapi.run_powershell(
            f"if (Get-AppxPackage | Where-Object PackageFullName -eq '{app.package_full_name}') {{ 'yes' }} else {{ 'no' }}",
            timeout=60)
        return "yes" in (r.stdout or "")
    if app.source in ("steam", "epic") and app.manifest:
        return os.path.isfile(app.manifest)
    if app.reg_hive and winreg is not None:
        hive = winreg.HKEY_LOCAL_MACHINE if app.reg_hive == "HKLM" else winreg.HKEY_CURRENT_USER
        flag = winreg.KEY_WOW64_32KEY if app.reg_view == 32 else winreg.KEY_WOW64_64KEY
        try:
            with winreg.OpenKey(hive, app.reg_path, 0, winreg.KEY_READ | flag):
                return True
        except OSError:
            return False
    return bool(app.install_location and os.path.isdir(app.install_location))


def _helpers(started: float, tracked: set[int]) -> list[int]:
    if psutil is None:
        return []
    temp = os.path.normcase(os.environ.get("TEMP", ""))
    alive = []
    for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            if p.info["pid"] in tracked:
                alive.append(p.info["pid"])
                continue
            if (p.info.get("create_time") or 0) < started - 1:
                continue
            name = p.info.get("name") or ""
            exe = os.path.normcase(p.info.get("exe") or "")
            if HELPER_NAME_RE.match(name) or (temp and exe.startswith(temp)):
                alive.append(p.info["pid"])
        except (psutil.Error, KeyError):
            continue
    return alive


def run_uninstall(app: AppRecord, method: UninstallMethod, cancel=None, status=None,
                  timeout_s: int = 3600) -> Outcome:
    say = status or (lambda _m: None)
    t0 = time.time()
    oplog.record("uninstall-start", app.name, "started", method=method.kind, command=method.command_line)

    def finish(o: Outcome) -> Outcome:
        o.elapsed = time.time() - t0
        oplog.record("uninstall", app.name, o.status, message=o.message, exit_code=o.exit_code, method=method.kind)
        return o

    if method.kind == "none":
        return finish(Outcome("failed", method.description))

    if method.kind == "store":
        say("Removing the Microsoft Store package...")
        r = winapi.run_powershell(f"Remove-AppxPackage -Package '{method.file}'", timeout=600)
        if still_installed(app):
            err = (r.stderr or "").strip().splitlines()
            return finish(Outcome("failed", err[0] if err else "Windows did not remove the package", r.returncode))
        return finish(Outcome("removed", "Package removed", r.returncode))

    if method.kind in ("steam", "epic"):
        say("Opened the launcher. Confirm the uninstall there; Hito is waiting for it to finish...")
        try:
            winapi.open_uri(method.file)
        except OSError as exc:
            return finish(Outcome("failed", f"Could not open the launcher: {exc}"))
        return finish(_wait_registration_gone(app, cancel, say, timeout_s, t0))

    # msi / exe
    say("Starting the uninstaller - follow its window (Windows may ask for permission)...")
    try:
        proc = winapi.shell_execute(method.file, method.params)
    except winapi.LaunchError as exc:
        return finish(Outcome("failed", str(exc)))
    tracked: set[int] = {proc.pid} if proc.pid else set()
    code = None
    try:
        while True:
            if cancel is not None and cancel.is_set():
                return finish(Outcome("cancelled", "Stopped waiting. The uninstaller may still be open."))
            if time.time() - t0 > timeout_s:
                return finish(Outcome("timeout", "The uninstaller did not finish in time."))
            if psutil is not None and proc.pid:
                try:
                    for c in psutil.Process(proc.pid).children(recursive=True):
                        tracked.add(c.pid)
                except psutil.Error:
                    pass
            if proc.wait(400):
                code = proc.exit_code()
                break
    finally:
        proc.close()

    if method.kind == "msi" and code == 1602:
        return finish(Outcome("cancelled", MSI_CODES[1602], code))

    say("Uninstaller window closed - checking whether the app is gone...")
    o = _wait_registration_gone(app, cancel, say, timeout_s, t0, tracked=tracked, helpers_only=True)
    o.exit_code = code
    if o.status == "still_installed" and method.kind == "msi" and code not in (None, 0, 1641, 3010):
        o = Outcome("failed", f"Windows Installer error {code}: {MSI_CODES.get(code, 'see the operation log')}", code)
    if code in (3010, 1641):
        o.restart_needed = True
        o.message += " A restart is needed to finish."
    return finish(o)


def _wait_registration_gone(app: AppRecord, cancel, say, timeout_s: int, t0: float,
                            tracked: set[int] | None = None, helpers_only: bool = False) -> Outcome:
    started = t0
    quiet_since = None
    while True:
        if not still_installed(app):
            return Outcome("removed", "Uninstalled")
        if cancel is not None and cancel.is_set():
            return Outcome("cancelled", "Stopped waiting before the app was removed.")
        if time.time() - t0 > timeout_s:
            return Outcome("timeout", "Timed out waiting for the uninstall to finish.")
        if helpers_only:
            alive = _helpers(started, tracked or set())
            if alive:
                quiet_since = None
                say("The uninstaller is still running...")
            else:
                quiet_since = quiet_since or time.time()
                if time.time() - quiet_since > 6:
                    return Outcome("still_installed",
                                   "The uninstaller closed but the app is still registered. It may have been cancelled, "
                                   "failed, or need a restart.")
        time.sleep(1.5)
