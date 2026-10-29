"""User settings and the app data folder (%LOCALAPPDATA%\\HitoUninstall)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

from hito import APP_ID
from hito.core.categories import CustomCategory


def data_dir() -> str:
    base = os.environ.get("HITO_DATA_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_ID)
    os.makedirs(base, exist_ok=True)
    return base


def sub_dir(name: str) -> str:
    path = os.path.join(data_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path


@dataclass
class Settings:
    theme: str = "dark"                       # "dark" | "light"
    dry_run: bool = False                     # simulate every destructive action
    user_delete_mode: str = "recycle"         # files you pick yourself: "recycle" | "permanent"
    cleanup_delete_mode: str = "permanent"    # disposable caches: "permanent" | "recycle"
    hardlinks: str = "windows"                # "windows" | "everywhere" | "off"
    scan_workers: int = 6
    index_min_mb: int = 1
    large_threshold_mb: int = 1024
    dup_min_mb: int = 1
    dup_protect_system: bool = True
    temp_min_age_hours: int = 24
    excluded_paths: list[str] = field(default_factory=list)
    custom_categories: list[dict] = field(default_factory=list)

    def custom(self) -> list[CustomCategory]:
        out = []
        for c in self.custom_categories:
            try:
                out.append(CustomCategory(c["name"], c.get("color", "#C084FC"), list(c.get("extensions", [])),
                                          list(c.get("folders", []))))
            except (KeyError, TypeError):
                continue
        return out

    @classmethod
    def path(cls) -> str:
        return os.path.join(data_dir(), "settings.json")

    @classmethod
    def load(cls) -> "Settings":
        try:
            with open(cls.path(), encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in raw.items() if k in known})
        if s.user_delete_mode not in ("recycle", "permanent"):
            s.user_delete_mode = "recycle"
        if s.cleanup_delete_mode not in ("recycle", "permanent"):
            s.cleanup_delete_mode = "permanent"
        return s

    def save(self) -> None:
        tmp = self.path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, self.path())
