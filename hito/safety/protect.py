"""The protection policy every destructive action must pass.

check() returns BLOCK (never allowed), WARN (allowed only after the user sees
the warning) or OK. The rules are deliberately conservative: when in doubt a
path is blocked, and folders such as Windows, Program Files roots, user
profile roots and known personal folders can never be deleted as a whole.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from hito.core import winapi
from hito.core.fsutil import env_path, norm

OK, WARN, BLOCK = "ok", "warn", "block"

USER = "user"          # files the user picked in the analyzer / large / duplicate views
CLEANUP = "cleanup"    # catalogued cache and temp locations
LEFTOVER = "leftover"  # remnants of an uninstalled application

ROOT_SYSTEM_NAMES = {
    "system volume information", "$recycle.bin", "recovery", "boot", "efi", "pagefile.sys", "hiberfil.sys",
    "swapfile.sys", "bootmgr", "bootnxt", "$winreagent", "$sysreset", "windows.old", "$windows.~bt",
    "$windows.~ws", "config.msi", "$getcurrent", "dumpstack.log.tmp",
}
SAVE_SEGMENTS = {"saves", "save", "savegames", "savedgames", "saved games", "my games", "savedata"}
CACHE_SEGMENTS = {"cache", "cache2", "code cache", "gpucache", "dawncache", "grshadercache", "shadercache",
                  "startupcache", "jumplistcache", "thumbnails", "cachestorage", "scriptcache"}


# The only places inside C:\Windows and ProgramData\Microsoft that cleanup may ever touch,
# whatever the catalogue says (defence in depth).
WINDOWS_CLEANUP_WHITELIST = ["Temp", "Minidump", "MEMORY.DMP", "LiveKernelReports", "Logs", "Panther", "debug"]
PROGRAMDATA_MS_CLEANUP_WHITELIST = [r"Windows\WER"]


@dataclass
class Verdict:
    level: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.level != BLOCK


def _env(var: str) -> str:
    return os.environ.get(var, "") or ""


def critical_roots() -> list[str]:
    """Folders that may never be removed themselves (their contents are judged separately)."""
    home = os.path.expanduser("~")
    roots = [
        _env("WINDIR") or r"C:\Windows", _env("ProgramFiles"), _env("ProgramFiles(x86)"), _env("ProgramW6432"),
        _env("ProgramData"), _env("PUBLIC"), os.path.dirname(home), home,
        os.path.join(home, "AppData"), _env("LOCALAPPDATA"), _env("APPDATA"), os.path.join(home, "AppData", "LocalLow"),
        _env("CommonProgramFiles"), _env("CommonProgramFiles(x86)"), _env("CommonProgramW6432"),
        _env("OneDrive"), _env("OneDriveConsumer"), _env("OneDriveCommercial"),
        env_path("%APPDATA%", "Microsoft", "Windows", "Start Menu"),
        env_path("%ProgramData%", "Microsoft", "Windows", "Start Menu"),
        os.path.join(home, "Favorites"), os.path.join(home, "Contacts"), os.path.join(home, "Links"),
        _env("TEMP"), env_path("%LOCALAPPDATA%", "Temp"), os.path.join(_env("WINDIR") or r"C:\Windows", "Temp"),
    ]
    for kf in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "SavedGames"):
        roots.append(winapi.known_folder(kf))
    return [norm(r) for r in roots if r]


def personal_roots() -> list[str]:
    return [norm(winapi.known_folder(k)) for k in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]


def program_roots() -> list[str]:
    return [norm(r) for r in (_env("ProgramFiles"), _env("ProgramFiles(x86)"), _env("ProgramW6432")) if r]


def own_paths() -> list[str]:
    import sys
    from hito.settings import data_dir
    if winapi.is_packaged():
        # the unpacked program folder and the launched .exe itself (not the folder it sits in)
        paths = [data_dir(), os.path.dirname(os.path.abspath(sys.executable)), winapi.program_path()]
    else:
        paths = [data_dir(), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    return [norm(p) for p in paths]


def running_executables() -> set[str]:
    try:
        import psutil
    except ImportError:
        return set()
    exes = set()
    for p in psutil.process_iter(["exe"]):
        exe = p.info.get("exe")
        if exe:
            exes.add(norm(exe))
    return exes


def _u(p: str, r: str) -> bool:
    """is_under for already-normalised paths (hot path: called per file during cleanup)."""
    return p == r or p.startswith(r if r.endswith(os.sep) else r + os.sep)


@dataclass
class SafetyPolicy:
    context: str
    allowed_roots: list[str] = field(default_factory=list)       # cleanup: catalogue roots this batch may touch
    other_app_roots: list[str] = field(default_factory=list)     # leftover: install dirs of apps still installed
    check_running: bool = True
    running_exes: set[str] | None = None                         # pass to reuse one process snapshot

    def __post_init__(self):
        self._critical = set(critical_roots())
        self._personal = personal_roots()
        self._programs = program_roots()
        self._own = own_paths()
        self._allowed = [norm(r) for r in self.allowed_roots if r]
        self._other_apps = [norm(r) for r in self.other_app_roots if r and len(norm(r)) > 3]
        if not self.check_running:
            self._running = set()
        else:
            self._running = self.running_exes if self.running_exes is not None else running_executables()
        windir = norm(_env("WINDIR") or r"C:\Windows")
        self._windir = windir
        self._program_data = norm(_env("ProgramData") or r"C:\ProgramData")
        self._pkg_cache = os.path.join(self._program_data, "package cache")
        self._pd_microsoft = os.path.join(self._program_data, "microsoft")
        self._saved_games = norm(winapi.known_folder("SavedGames"))
        self._win_ok = [os.path.join(windir, x.lower()) for x in WINDOWS_CLEANUP_WHITELIST]
        self._pdms_ok = [os.path.join(self._pd_microsoft, x.lower()) for x in PROGRAMDATA_MS_CLEANUP_WHITELIST]

    def check(self, path: str) -> Verdict:
        if not path or not os.path.isabs(path):
            return Verdict(BLOCK, "Not an absolute path")
        p = norm(path)
        drive, rest = os.path.splitdrive(p)
        parts = [x for x in rest.split(os.sep) if x]
        if not parts:
            return Verdict(BLOCK, "Drive roots can never be deleted")
        if parts[0] in ROOT_SYSTEM_NAMES:
            return Verdict(BLOCK, f"Protected Windows location ({parts[0]})")
        if p in self._critical:
            return Verdict(BLOCK, "Protected system or personal root folder")
        for own in self._own:
            if _u(p, own) or _u(own, p):
                return Verdict(BLOCK, "Belongs to this program (logs, backups or the program itself)")

        if _u(p, self._windir):
            if self.context == CLEANUP and any(_u(p, a) for a in self._allowed) and any(_u(p, w) for w in self._win_ok):
                return self._running_check(p) or Verdict(OK)
            return Verdict(BLOCK, "Inside the Windows folder")

        lower_parts = set(parts)
        if "windowsapps" in lower_parts and any(_u(p, r) for r in self._programs):
            return Verdict(BLOCK, "Microsoft Store package folder - uninstall the app instead")
        if _u(p, self._pkg_cache):
            return Verdict(BLOCK, "Shared installer cache used for repairs and uninstalls")
        if _u(p, self._pd_microsoft) and not (
                self.context == CLEANUP and any(_u(p, a) for a in self._allowed) and any(_u(p, w) for w in self._pdms_ok)):
            return Verdict(BLOCK, "Windows component data in ProgramData\\Microsoft")

        hit = self._running_check(p)
        if hit:
            return hit

        for other in self._other_apps:
            if _u(p, other) or _u(other, p):
                return Verdict(BLOCK, "Used by another installed application")

        in_programs = any(_u(p, r) for r in self._programs)
        if self.context == USER and in_programs:
            return Verdict(BLOCK, "Installed program files - use Installed Apps to uninstall instead")
        if self.context == CLEANUP:
            if not any(_u(p, a) for a in self._allowed):
                return Verdict(BLOCK, "Outside the cleanup locations selected for this run")
            if any(_u(p, r) for r in self._personal):
                return Verdict(BLOCK, "Personal folder content is never touched by cleanup")

        if self._is_browser_profile_data(parts) and self.context != USER:
            return Verdict(BLOCK, "Browser profile data (passwords, cookies, history) is never removed")
        if lower_parts & SAVE_SEGMENTS or _u(p, self._saved_games):
            return Verdict(WARN, "May contain game saves or profiles")
        if self.context == LEFTOVER and any(_u(p, r) for r in self._personal):
            return Verdict(WARN, "Inside a personal folder - may contain your own files")
        if self.context == USER and self._is_browser_profile_data(parts):
            return Verdict(WARN, "Browser profile data (passwords, cookies, history)")
        return Verdict(OK)

    def _running_check(self, p: str) -> Verdict | None:
        for exe in self._running:
            if exe == p or exe.startswith(p + os.sep):
                return Verdict(BLOCK, f"In use by a running program ({os.path.basename(exe)})")
        return None

    @staticmethod
    def _is_browser_profile_data(parts: list[str]) -> bool:
        joined = os.sep.join(parts)
        browser = ("user data" in parts) or ("firefox" in joined and "profiles" in parts)
        if not browser:
            return False
        return not (set(parts) & CACHE_SEGMENTS)
