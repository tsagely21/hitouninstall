"""Storage categories and the rules that assign files to them.

Classification works in two layers:
  1. A *location context* inherited down the folder tree (Windows, Program
     Files, a Steam library, AppData caches, Downloads, ...).
  2. If no context applies, the file extension decides (video, pictures, ...).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from hito.core import winapi
from hito.core.fsutil import env_path, norm


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    color: str
    description: str


GAMES = "games"
APPS = "apps"
VIDEOS = "videos"
PICTURES = "pictures"
MUSIC = "music"
DOCUMENTS = "documents"
DOWNLOADS = "downloads"
ARCHIVES = "archives"
DESKTOP = "desktop"
TEMP = "temp"
WINDOWS = "windows"
DEV = "dev"
RECYCLE = "recycle"
OTHER = "other"

BUILTIN: list[Category] = [
    Category(GAMES, "Games", "#A855F7", "Steam, Epic, Xbox and other game libraries"),
    Category(APPS, "Installed apps", "#6366F1", "Program Files, ProgramData and app data folders"),
    Category(VIDEOS, "Videos", "#EC4899", "Video files outside app folders"),
    Category(PICTURES, "Pictures", "#F59E0B", "Photos, images and design files"),
    Category(MUSIC, "Music & audio", "#10B981", "Songs, recordings and audio projects"),
    Category(DOCUMENTS, "Documents", "#3B82F6", "Office files, PDFs and text"),
    Category(DOWNLOADS, "Downloads", "#14B8A6", "Everything in your Downloads folder"),
    Category(ARCHIVES, "Archives & images", "#F97316", "ZIP, RAR, 7z, ISO and disk images"),
    Category(DESKTOP, "Desktop", "#06B6D4", "Files kept on the Desktop"),
    Category(TEMP, "Temp & caches", "#EAB308", "Temporary files and app caches"),
    Category(WINDOWS, "Windows system", "#64748B", "Windows, page file, hibernation and restore data"),
    Category(DEV, "Development", "#84CC16", "node_modules, virtual envs, build caches, source"),
    Category(RECYCLE, "Recycle Bin", "#EF4444", "Deleted files still held in the Recycle Bin"),
    Category(OTHER, "Other", "#94A3B8", "Files that fit no other category"),
]

_EXT = {
    VIDEOS: "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts mts m2ts vob ogv rmvb",
    PICTURES: "jpg jpeg png gif bmp tif tiff webp heic heif raw cr2 cr3 nef arw dng orf psd xcf svg ico avif jxl kra",
    MUSIC: "mp3 wav flac aac ogg m4a wma opus aiff aif alac mid midi als flp",
    DOCUMENTS: "pdf doc docx xls xlsx ppt pptx odt ods odp txt rtf md csv epub mobi one pages key numbers tex",
    ARCHIVES: "zip rar 7z tar gz tgz bz2 xz zst cab iso img vhd vhdx vmdk wim esd dmg",
    DEV: "py pyc pyd js ts jsx tsx c cpp h hpp cs java class jar rs go rb php ipynb whl o obj pdb lib ilk pch sln csproj",
    APPS: "exe msi msix appx dll sys",
}
EXTENSION_MAP: dict[str, str] = {f".{e}": cat for cat, exts in _EXT.items() for e in exts.split()}

DEV_DIR_NAMES = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".gradle", ".m2", ".cargo", ".rustup", ".nuget",
    ".npm", ".pnpm-store", ".yarn", "site-packages", ".tox", ".mypy_cache", ".pytest_cache", ".next", ".vs",
    ".idea", ".android", ".conda", "anaconda3", "miniconda3",
}
CACHE_DIR_NAMES = {
    "cache", "caches", "cache2", "code cache", "gpucache", "shadercache", "dxcache", "glcache", "temp", "tmp",
    "inetcache", "crashdumps", "logs", "webcache", "htmlcache", "httpcache", "crashpad", "d3dscache",
    "grshadercache", "dawncache", "service worker",
}
GAME_DIR_NAMES = {"steamapps", "xboxgames", "epic games", "riot games", "gog games", "ea games", "battle.net games"}
ROOT_SYSTEM_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log", "dumpstack.log.tmp", "bootmgr"}
ROOT_SYSTEM_DIRS = {"system volume information", "windows.old", "$windows.~bt", "$windows.~ws", "recovery",
                    "$winreagent", "$sysreset", "boot", "config.msi", "msocache", "perflogs"}

# Internal contexts that map onto categories. "appdata" behaves like "apps"
# except that cache folders inside it become Temp & caches.
CTX_CATEGORY = {"appdata": APPS}
SEALED = {WINDOWS, APPS, GAMES, RECYCLE}


@dataclass
class CustomCategory:
    name: str
    color: str = "#C084FC"
    extensions: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"custom:{self.name}"


class Classifier:
    def __init__(self, custom: list[CustomCategory] | None = None, game_roots: list[str] | None = None):
        self.custom = list(custom or [])
        self.categories: dict[str, Category] = {c.id: c for c in BUILTIN}
        for cc in self.custom:
            self.categories[cc.id] = Category(cc.id, cc.name, cc.color, "Custom category")
        self.ext_map = dict(EXTENSION_MAP)
        for cc in self.custom:
            for e in cc.extensions:
                e = e.strip().lower()
                if e:
                    self.ext_map[e if e.startswith(".") else "." + e] = cc.id
        self.root_rules: dict[str, str] = {}
        self._build_root_rules(game_roots or [])

    def _add(self, path: str, ctx: str):
        if path:
            self.root_rules[norm(path)] = ctx

    def _build_root_rules(self, game_roots: list[str]):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        self._add(windir, WINDOWS)
        self._add(os.path.join(windir, "Temp"), TEMP)
        self._add(os.path.join(windir, "SoftwareDistribution", "Download"), TEMP)
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
            self._add(os.environ.get(var, ""), APPS)
        self._add(env_path("%LOCALAPPDATA%", "Programs"), APPS)
        for var in ("LOCALAPPDATA", "APPDATA"):
            self._add(os.environ.get(var, ""), "appdata")
        self._add(env_path("%USERPROFILE%", "AppData", "LocalLow"), "appdata")
        self._add(os.environ.get("TEMP", ""), TEMP)
        self._add(env_path("%LOCALAPPDATA%", "Temp"), TEMP)
        self._add(winapi.known_folder("Downloads"), DOWNLOADS)
        self._add(winapi.known_folder("Desktop"), DESKTOP)
        for root in game_roots:
            self._add(root, GAMES)
        for cc in self.custom:
            for f in cc.folders:
                self._add(f, cc.id)

    def label(self, cat_id: str) -> str:
        c = self.categories.get(cat_id)
        return c.label if c else cat_id

    def color(self, cat_id: str) -> str:
        c = self.categories.get(cat_id)
        return c.color if c else "#94A3B8"

    def dir_context(self, parent_ctx: str | None, path: str, name: str, is_drive_root_child: bool = False) -> str | None:
        """Context for a folder given its parent's context."""
        rule = self.root_rules.get(norm(path))
        if rule is not None:
            return rule
        lname = name.lower()
        if is_drive_root_child:
            if lname == "$recycle.bin":
                return RECYCLE
            if lname in ROOT_SYSTEM_DIRS:
                return WINDOWS
            if lname in GAME_DIR_NAMES:
                return GAMES
        if parent_ctx in SEALED or (parent_ctx or "").startswith("custom:"):
            return parent_ctx
        if lname in DEV_DIR_NAMES:
            return DEV
        if parent_ctx in (DEV, TEMP):
            return parent_ctx
        if lname in GAME_DIR_NAMES:
            return GAMES
        if parent_ctx == "appdata" and lname in CACHE_DIR_NAMES:
            return TEMP
        return parent_ctx

    def context_category(self, ctx: str | None) -> str | None:
        if ctx is None:
            return None
        return CTX_CATEGORY.get(ctx, ctx)

    def file_category(self, ctx: str | None, name: str, in_drive_root: bool = False) -> str:
        cat = self.context_category(ctx)
        ext = os.path.splitext(name)[1].lower()
        custom = self.ext_map.get(ext) if ext else None
        if custom and custom.startswith("custom:") and cat not in SEALED:
            return custom
        if cat is not None:
            return cat
        if in_drive_root and name.lower() in ROOT_SYSTEM_FILES:
            return WINDOWS
        return custom or OTHER

    def classify_path(self, path: str) -> str:
        """Classify a single file path from scratch (slower; used outside full scans)."""
        path = os.path.abspath(path)
        drive, rest = os.path.splitdrive(path)
        parts = [p for p in rest.split(os.sep) if p]
        ctx: str | None = None
        cur = drive + os.sep
        for i, part in enumerate(parts[:-1]):
            cur = os.path.join(cur, part)
            ctx = self.dir_context(ctx, cur, part, is_drive_root_child=(i == 0))
        return self.file_category(ctx, parts[-1] if parts else path, in_drive_root=len(parts) == 1)
