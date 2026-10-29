"""Registry backup, removal and restore for approved leftover entries.

Every key or value is exported with reg.exe to a .reg file before it is
removed, and can be re-imported from Settings & History.
"""
from __future__ import annotations

import os
import re
import time

from hito.core import winapi
from hito.safety import oplog
from hito.settings import sub_dir

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

HIVES = {
    "HKEY_LOCAL_MACHINE": "HKLM", "HKLM": "HKLM",
    "HKEY_CURRENT_USER": "HKCU", "HKCU": "HKCU",
    "HKEY_CLASSES_ROOT": "HKCR", "HKCR": "HKCR",
}

# Keys that may never be removed, even if something points at them.
PROTECTED_KEYS = [
    r"HKLM\SOFTWARE", r"HKLM\SOFTWARE\WOW6432Node", r"HKCU\SOFTWARE", r"HKLM\SOFTWARE\Microsoft",
    r"HKCU\SOFTWARE\Microsoft", r"HKLM\SOFTWARE\WOW6432Node\Microsoft", r"HKLM\SOFTWARE\Classes",
    r"HKCU\SOFTWARE\Classes", r"HKLM\SOFTWARE\Policies", r"HKCU\SOFTWARE\Policies", r"HKLM\SYSTEM",
    r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run", r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
    r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
    r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
]
PROTECTED_PREFIXES = [r"HKLM\SYSTEM", r"HKLM\SOFTWARE\Microsoft\Windows NT", r"HKLM\SOFTWARE\Classes\CLSID",
                      r"HKLM\SOFTWARE\Microsoft\Cryptography", r"HKLM\SOFTWARE\Microsoft\Windows Defender",
                      r"HKLM\SAM", r"HKLM\SECURITY"]


def short(full: str) -> str:
    hive, _, rest = full.partition("\\")
    return f"{HIVES.get(hive.upper(), hive)}\\{rest}".rstrip("\\")


def is_protected_key(full: str) -> bool:
    s = short(full).lower()
    if s in {k.lower() for k in PROTECTED_KEYS}:
        return True
    if any(s == p.lower() or s.startswith(p.lower() + "\\") for p in PROTECTED_PREFIXES):
        return True
    return s.count("\\") < 2  # never a hive root or a first-level key like HKCU\Software


def _open(full: str, access):
    hive, _, sub = short(full).partition("\\")
    h = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER, "HKCR": winreg.HKEY_CLASSES_ROOT}[hive]
    # Paths include WOW6432Node explicitly when they mean the 32-bit view.
    return winreg.OpenKey(h, sub, 0, access | winreg.KEY_WOW64_64KEY)


def key_exists(full: str) -> bool:
    if winreg is None:
        return False
    try:
        with _open(full, winreg.KEY_READ):
            return True
    except OSError:
        return False


def value_exists(full: str, name: str) -> bool:
    if winreg is None:
        return False
    try:
        with _open(full, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


def backup_key(full: str, label: str = "") -> str:
    """Export a key (with subkeys) to a .reg file and return its path."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label or short(full))[:80]
    dest = os.path.join(sub_dir("backups"), f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}.reg")
    r = winapi.run_hidden(["reg.exe", "export", short(full), dest, "/y", "/reg:64"], timeout=60)
    if r.returncode != 0 or not os.path.isfile(dest):
        raise OSError(f"Backup failed for {full}: {(r.stderr or r.stdout).strip()}")
    return dest


def _delete_tree(k, sub: str):
    with winreg.OpenKey(k, sub, 0, winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY) as child:
        while True:
            try:
                name = winreg.EnumKey(child, 0)
            except OSError:
                break
            _delete_tree(child, name)
    winreg.DeleteKeyEx(k, sub, winreg.KEY_WOW64_64KEY, 0)


def delete_key(full: str, dry_run: bool = False) -> tuple[bool, str]:
    """Back up then delete a key tree. Returns (ok, message)."""
    if is_protected_key(full):
        oplog.record("registry-delete", full, "blocked", reason="protected key")
        return False, "Protected registry key - never removed"
    if not key_exists(full):
        return False, "Key no longer exists"
    if dry_run:
        oplog.record("dry-run", full, "simulated", kind="registry-key")
        return True, "Dry run: would back up and delete this key"
    try:
        backup = backup_key(full)
    except OSError as exc:
        oplog.record("registry-delete", full, "error", error=str(exc))
        return False, f"Not deleted: {exc}"
    parent, _, leaf = short(full).rpartition("\\")
    try:
        with _open(parent, winreg.KEY_ALL_ACCESS) as pk:
            _delete_tree(pk, leaf)
    except PermissionError:
        oplog.record("registry-delete", full, "error", error="access denied", backup=backup)
        return False, "Access denied (administrator rights needed)"
    except OSError as exc:
        oplog.record("registry-delete", full, "error", error=str(exc), backup=backup)
        return False, str(exc)
    oplog.record("registry-delete", full, "ok", backup=backup)
    return True, f"Deleted (backup: {os.path.basename(backup)})"


def delete_value(full: str, name: str, dry_run: bool = False) -> tuple[bool, str]:
    if not value_exists(full, name):
        return False, "Value no longer exists"
    if dry_run:
        oplog.record("dry-run", f"{full}\\{name}", "simulated", kind="registry-value")
        return True, "Dry run: would back up the key and delete this value"
    try:
        backup = backup_key(full, label=f"{short(full)}_{name}")
    except OSError as exc:
        return False, f"Not deleted: {exc}"
    try:
        with _open(full, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except PermissionError:
        return False, "Access denied (administrator rights needed)"
    except OSError as exc:
        return False, str(exc)
    oplog.record("registry-delete-value", f"{full}\\{name}", "ok", backup=backup)
    return True, f"Deleted (backup: {os.path.basename(backup)})"


def list_backups() -> list[tuple[str, float, int]]:
    folder = sub_dir("backups")
    out = []
    for f in os.listdir(folder):
        if f.lower().endswith(".reg"):
            p = os.path.join(folder, f)
            st = os.stat(p)
            out.append((p, st.st_mtime, st.st_size))
    return sorted(out, key=lambda x: x[1], reverse=True)


def restore_backup(path: str) -> tuple[bool, str]:
    """Re-import a .reg backup. HKLM backups need administrator rights."""
    r = winapi.run_hidden(["reg.exe", "import", path, "/reg:64"], timeout=60)
    ok = r.returncode == 0
    oplog.record("registry-restore", path, "ok" if ok else "error", error=None if ok else (r.stderr or r.stdout).strip())
    return ok, "Restored" if ok else (r.stderr or r.stdout).strip() or "reg import failed"
