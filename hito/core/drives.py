"""Connected drive discovery."""
from __future__ import annotations

import shutil
from dataclasses import dataclass

from hito.core import winapi


@dataclass
class DriveInfo:
    root: str           # "C:\\"
    label: str
    filesystem: str
    kind: str           # Local disk / Removable / Network ...
    total: int
    used: int
    free: int

    @property
    def letter(self) -> str:
        return self.root[:2]

    @property
    def percent(self) -> float:
        return (self.used / self.total * 100.0) if self.total else 0.0

    @property
    def title(self) -> str:
        name = self.label or ("Local Disk" if self.kind == "Local disk" else self.kind)
        return f"{name} ({self.letter})"


def list_drives(include_network: bool = True) -> list[DriveInfo]:
    drives: list[DriveInfo] = []
    for root in winapi.logical_drives():
        kind = winapi.drive_type(root)
        if kind in ("CD/DVD", "No root", "Unknown"):
            continue
        if kind == "Network" and not include_network:
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue  # empty card reader, locked BitLocker volume, ...
        vol = winapi.volume_info(root)
        drives.append(DriveInfo(root, vol.label, vol.filesystem, kind, usage.total, usage.used, usage.free))
    return drives
