"""Work out the legitimate uninstall mechanism for an application."""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from hito.apps.inventory import GUID_RE, AppRecord


@dataclass
class UninstallMethod:
    kind: str          # msi | exe | store | steam | epic | none
    file: str = ""
    params: str = ""
    description: str = ""
    detail: str = ""

    @property
    def command_line(self) -> str:
        if self.kind in ("exe", "msi"):
            return f'"{self.file}" {self.params}'.strip()
        return self.file


def split_command(cmd: str) -> tuple[str, str]:
    """Split an UninstallString into (executable, arguments).

    Handles quoted paths, unquoted paths containing spaces and bare program names.
    """
    cmd = os.path.expandvars(cmd.strip())
    if not cmd:
        return "", ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end], cmd[end + 1:].strip()
    # Unquoted: try progressively longer prefixes until one is an existing file.
    parts = cmd.split(" ")
    for i in range(len(parts), 0, -1):
        cand = " ".join(parts[:i])
        if os.path.isfile(cand) or (not cand.lower().endswith(".exe") and os.path.isfile(cand + ".exe")):
            return cand, " ".join(parts[i:]).strip()
    try:
        first = shlex.split(cmd, posix=False)[0]
    except ValueError:
        first = parts[0]
    return first.strip('"'), cmd[len(first):].strip()


def resolve(app: AppRecord) -> UninstallMethod:
    if app.source == "store" and app.package_full_name:
        return UninstallMethod("store", app.package_full_name, description="Microsoft Store package removal",
                               detail=f"Remove-AppxPackage -Package {app.package_full_name}")
    if app.steam_appid:
        return UninstallMethod("steam", f"steam://uninstall/{app.steam_appid}",
                               description="Steam uninstall (Steam asks you to confirm)",
                               detail="Steam removes the game files; cloud saves stay in your Steam account.")
    if app.source == "epic":
        return UninstallMethod("epic", "com.epicgames.launcher://store/library",
                               description="Epic Games Launcher",
                               detail="The Epic launcher opens on your Library. Choose the game's '...' menu > Uninstall. "
                                      "Hito detects when the launcher has removed it.")
    if app.msi_product_code:
        code = app.msi_product_code
        return UninstallMethod("msi", "msiexec.exe", f"/x {code}", description="Windows Installer (MSI) uninstall",
                               detail=f"msiexec.exe /x {code}")
    if app.uninstall_string:
        exe, args = split_command(app.uninstall_string)
        low = exe.lower()
        if os.path.basename(low) in ("msiexec", "msiexec.exe"):
            m = GUID_RE.search(args)
            if m:
                return UninstallMethod("msi", "msiexec.exe", f"/x {m.group(0)}", description="Windows Installer (MSI) uninstall",
                                       detail=f"msiexec.exe /x {m.group(0)}")
        missing = not os.path.isabs(exe) or not os.path.isfile(exe)
        if missing and os.path.isabs(exe):
            return UninstallMethod("none", description="The app's uninstaller file is missing",
                                   detail=f"Expected {exe}. The app may already be partly removed; "
                                          "a leftover scan can still find its files.")
        return UninstallMethod("exe", exe, args, description="The app's own uninstaller",
                               detail=f'"{exe}" {args}'.strip())
    return UninstallMethod("none", description="No uninstall method registered",
                           detail="Windows has no uninstaller for this entry.")
