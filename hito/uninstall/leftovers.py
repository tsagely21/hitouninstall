"""Evidence-based leftover detection after an uninstall.

Nothing is matched on a loose name alone. Each candidate gets:
  * a confidence (high / medium / low) from the strength of the ownership
    evidence: recorded install path > shortcut/registry value pointing into
    that path > exact folder name match > partial match;
  * a risk (low / review / high) from the safety policy and location;
and is pre-selected only when confidence is high and risk is low.
Generic names (Common, Update, Microsoft, ...) and folders that another
installed program uses are never proposed.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field

from hito.apps.inventory import AppRecord
from hito.core import winapi
from hito.core.fsutil import env_path, norm, tree_size
from hito.safety.protect import BLOCK, LEFTOVER, WARN, SafetyPolicy, critical_roots

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

HIGH, MEDIUM, LOW = "high", "medium", "low"
RISK_LOW, RISK_REVIEW, RISK_HIGH = "low", "review", "high"

GENERIC = {
    "microsoft", "windows", "common", "common files", "update", "updater", "updates", "temp", "tmp", "cache", "data",
    "app", "apps", "application", "applications", "program", "programs", "software", "games", "game", "launcher",
    "client", "setup", "installer", "install", "tools", "tool", "system", "shared", "runtime", "runtimes", "driver",
    "drivers", "intel", "nvidia", "amd", "google", "adobe", "packages", "package", "local", "roaming", "user",
    "users", "default", "config", "settings", "logs", "log", "steam", "epic", "electron", "python", "java", "node",
    "crashpad", "crashreports", "plugins", "plugin", "bin", "lib", "documents", "downloads", "desktop", "music",
    "videos", "pictures", "saved games", "my games", "publisher", "inc", "llc", "ltd", "corporation", "corp",
    "studio", "studios", "service", "services", "helper", "agent", "assistant", "manager", "center", "centre",
    "help", "support", "online", "connect", "hub", "home", "pro", "free", "lite", "edition", "x64", "x86",
    "win64", "win32", "64-bit", "32-bit", "mozilla", "apple", "oracle", "dell", "hp", "lenovo", "asus", "realtek",
}
NOISE_RE = re.compile(r"(\(.*?\)|\bv?\d+(\.\d+)+\b|\b(x64|x86|64-bit|32-bit|amd64|win64|win32)\b|\bversion\b.*$)", re.I)


@dataclass
class LeftoverItem:
    kind: str                # folder | file | shortcut | regkey | regvalue
    path: str                # file system path, or full registry key path
    value_name: str = ""     # for regvalue
    size: int = 0
    confidence: str = MEDIUM
    risk: str = RISK_REVIEW
    evidence: str = ""
    selected: bool = False

    @property
    def is_registry(self) -> bool:
        return self.kind in ("regkey", "regvalue")

    @property
    def display(self) -> str:
        return f"{self.path}  [{self.value_name}]" if self.kind == "regvalue" else self.path


@dataclass
class AppSnapshot:
    app: AppRecord
    install_location: str
    exe_names: set[str] = field(default_factory=set)
    icon_dir: str = ""


def take_snapshot(app: AppRecord) -> AppSnapshot:
    """Record evidence before uninstalling (the uninstaller deletes it)."""
    loc = app.install_location or app.extra.get("location_from_icon", "")
    snap = AppSnapshot(app, loc, icon_dir=os.path.dirname(app.display_icon) if app.display_icon else "")
    if loc and os.path.isdir(loc) and len(norm(loc)) > 3:
        stack = [(loc, 0)]
        while stack and len(snap.exe_names) < 60:
            cur, depth = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False) and depth < 2:
                            stack.append((e.path, depth + 1))
                        elif e.name.lower().endswith(".exe"):
                            snap.exe_names.add(e.name.lower())
            except OSError:
                continue
    return snap


def clean_name(name: str) -> str:
    n = NOISE_RE.sub(" ", name or "")
    n = re.sub(r"\s+", " ", n).strip(" -_.,")
    return n


def name_keys(snap: AppSnapshot) -> tuple[set[str], set[str]]:
    """(strong keys, weak keys): lower-case folder names that identify the app."""
    app = snap.app
    strong, weak = set(), set()
    base = os.path.basename(norm(snap.install_location)) if snap.install_location else ""
    if base and base not in GENERIC and len(base) >= 3:
        strong.add(base)
    cn = clean_name(app.name).lower()
    for k in {cn, cn.replace(" ", ""), cn.replace(" ", "-"), cn.replace(" ", "_")}:
        if k and k not in GENERIC and len(k) >= 3:
            weak.add(k)
    if app.package_family:
        strong.add(app.package_family.lower())
    return strong, weak - strong


def _publisher_key(pub: str) -> str:
    p = re.sub(r"[,.]?\s*(inc|llc|ltd|gmbh|corp|corporation|co|s\.a|limited|ab|pbc)\.?$", "", (pub or "").strip(), flags=re.I)
    p = p.strip(" .,").lower()
    return "" if p in GENERIC or len(p) < 3 else p


def search_roots() -> list[tuple[str, str]]:
    """(folder, label) pairs where app leftovers commonly live."""
    roots = [
        (os.environ.get("ProgramFiles", ""), "Program Files"),
        (os.environ.get("ProgramFiles(x86)", ""), "Program Files (x86)"),
        (os.environ.get("ProgramData", ""), "ProgramData"),
        (os.environ.get("LOCALAPPDATA", ""), "AppData\\Local"),
        (env_path("%LOCALAPPDATA%", "Programs"), "AppData\\Local\\Programs"),
        (os.environ.get("APPDATA", ""), "AppData\\Roaming"),
        (env_path("%USERPROFILE%", "AppData", "LocalLow"), "AppData\\LocalLow"),
        (winapi.known_folder("Documents"), "Documents"),
    ]
    return [(r, lbl) for r, lbl in roots if r and os.path.isdir(r)]


def shortcut_roots() -> list[str]:
    return [p for p in (
        env_path("%APPDATA%", "Microsoft", "Windows", "Start Menu", "Programs"),
        env_path("%ProgramData%", "Microsoft", "Windows", "Start Menu", "Programs"),
        winapi.known_folder("Desktop"),
        env_path("%PUBLIC%", "Desktop"),
    ) if p and os.path.isdir(p)]


def lnk_target(path: str) -> str:
    """Local target path of a .lnk file (MS-SHLLINK LinkInfo.LocalBasePath)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(64 * 1024)
        if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
            return ""
        flags = struct.unpack_from("<I", data, 0x14)[0]
        pos = 0x4C
        if flags & 0x1:  # HasLinkTargetIDList
            pos += 2 + struct.unpack_from("<H", data, pos)[0]
        if not flags & 0x2:  # HasLinkInfo
            return ""
        li = pos
        header_size = struct.unpack_from("<I", data, li + 4)[0]
        li_flags = struct.unpack_from("<I", data, li + 8)[0]
        if not li_flags & 0x1:  # VolumeIDAndLocalBasePath
            return ""
        if header_size >= 0x24:
            off_u = struct.unpack_from("<I", data, li + 0x1C)[0]
            end = data.find(b"\x00\x00", li + off_u)
            while end != -1 and (end - (li + off_u)) % 2:
                end = data.find(b"\x00\x00", end + 1)
            return data[li + off_u:end].decode("utf-16-le", "replace")
        off = struct.unpack_from("<I", data, li + 0x10)[0]
        end = data.find(b"\x00", li + off)
        return data[li + off:end].decode("mbcs" if os.name == "nt" else "latin-1", "replace")
    except (OSError, struct.error, ValueError):
        return ""


class LeftoverScanner:
    def __init__(self, snap: AppSnapshot, other_apps: list[AppRecord], cancel=None):
        self.snap = snap
        self.app = snap.app
        self.cancel = cancel
        self.others = [a for a in other_apps if a.key != self.app.key]
        other_roots = [a.install_location for a in self.others if a.install_location]
        self.policy = SafetyPolicy(LEFTOVER, other_app_roots=other_roots)
        self.strong, self.weak = name_keys(snap)
        self.publisher = _publisher_key(self.app.publisher)
        self.other_names = set()
        for a in self.others:
            self.other_names.add(clean_name(a.name).lower())
            if a.install_location:
                self.other_names.add(os.path.basename(norm(a.install_location)))
        self.install_n = norm(snap.install_location) if snap.install_location else ""
        self.critical = set(critical_roots())
        self.items: list[LeftoverItem] = []
        self._seen: set[str] = set()
        self.excluded: list[tuple[str, str]] = []

    # -- helpers ------------------------------------------------------------
    def _cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def _add_fs(self, path: str, confidence: str, evidence: str, kind: str | None = None):
        n = norm(path)
        if n in self._seen or not os.path.lexists(path):
            return
        self._seen.add(n)
        if n in self.critical or len(n) <= 3:
            return
        verdict = self.policy.check(path)
        if verdict.level == BLOCK:
            self.excluded.append((path, verdict.reason))
            return
        kind = kind or ("folder" if os.path.isdir(path) else "file")
        size = tree_size(path, cancel=self.cancel)[0] if kind != "shortcut" else os.path.getsize(path)
        if verdict.level == WARN:
            risk = RISK_HIGH
            evidence = f"{evidence}. Warning: {verdict.reason}"
        elif confidence == HIGH:
            risk = RISK_LOW
        else:
            risk = RISK_REVIEW
        self.items.append(LeftoverItem(kind, path, size=size, confidence=confidence, risk=risk, evidence=evidence,
                                       selected=(confidence == HIGH and risk == RISK_LOW)))

    def _add_reg(self, full: str, confidence: str, evidence: str, value: str = "", hklm: bool = False):
        key = f"{full.lower()}|{value.lower()}"
        if key in self._seen:
            return
        self._seen.add(key)
        from hito.uninstall.regops import is_protected_key
        if not value and is_protected_key(full):
            return
        risk = RISK_LOW if confidence == HIGH and not hklm else RISK_REVIEW
        self.items.append(LeftoverItem("regvalue" if value else "regkey", full, value_name=value, confidence=confidence,
                                       risk=risk, evidence=evidence, selected=(confidence == HIGH)))

    def _claimed_by_other(self, folder_name: str) -> bool:
        return folder_name in self.other_names

    # -- scan ---------------------------------------------------------------
    def run(self) -> list[LeftoverItem]:
        self._install_folder()
        if not self._cancelled():
            self._store_package_data()
        if not self._cancelled():
            self._named_folders()
        if not self._cancelled():
            self._shortcuts()
        if not self._cancelled() and winreg is not None:
            self._registry()
        order = {HIGH: 0, MEDIUM: 1, LOW: 2}
        self.items.sort(key=lambda i: (order[i.confidence], i.is_registry, -i.size))
        return self.items

    def _install_folder(self):
        loc = self.snap.install_location
        if loc and os.path.isdir(loc):
            self._add_fs(loc, HIGH, "Install folder recorded by the app's own uninstall entry")
        icon_dir = self.snap.icon_dir
        if icon_dir and os.path.isdir(icon_dir) and norm(icon_dir) != self.install_n and not self._is_generic_dir(icon_dir):
            base = os.path.basename(norm(icon_dir))
            conf = HIGH if base in self.strong or base in self.weak else MEDIUM
            self._add_fs(icon_dir, conf, "Folder of the app's registered icon/executable")

    def _store_package_data(self):
        fam = self.app.package_family
        if fam:
            p = env_path("%LOCALAPPDATA%", "Packages", fam)
            if os.path.isdir(p):
                self._add_fs(p, HIGH, "Store app data folder named after the exact package family")

    def _is_generic_dir(self, path: str) -> bool:
        return os.path.basename(norm(path)) in GENERIC or norm(path) in self.critical

    def _named_folders(self):
        for root, label in search_roots():
            if self._cancelled():
                return
            try:
                entries = list(os.scandir(root))
            except OSError:
                continue
            for e in entries:
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                lname = e.name.lower()
                if lname in GENERIC:
                    continue
                if self.publisher and lname == self.publisher:
                    self._publisher_folder(e.path, label)
                    continue
                self._match_folder(e.path, lname, label, parent_is_publisher=False)

    def _publisher_folder(self, path: str, label: str):
        try:
            subs = [s for s in os.scandir(path) if s.is_dir(follow_symlinks=False)]
            files = [s for s in os.scandir(path) if s.is_file(follow_symlinks=False)]
        except OSError:
            return
        for s in subs:
            self._match_folder(s.path, s.name.lower(), label, parent_is_publisher=True)
        if len(subs) <= 1 and not files and all(
                s.name.lower() in self.strong or s.name.lower() in self.weak for s in subs):
            if not self._claimed_by_other(os.path.basename(path).lower()):
                self._add_fs(path, MEDIUM, f"Publisher folder in {label} that only holds this app's data")

    def _match_folder(self, path: str, lname: str, label: str, parent_is_publisher: bool):
        if self._claimed_by_other(lname) and lname not in self.strong:
            return
        where = f"{label}\\{os.path.basename(os.path.dirname(path))}" if parent_is_publisher else label
        if lname in self.strong:
            conf = HIGH
            ev = f"Folder in {where} with the same name as the app's install folder"
        elif lname in self.weak:
            conf = HIGH if parent_is_publisher else MEDIUM
            ev = f"Folder in {where} named exactly after the app" + (" under its publisher" if parent_is_publisher else "")
        elif any(len(k) >= 5 and lname.startswith(k) for k in self.strong | self.weak):
            conf = LOW
            ev = f"Folder in {where} whose name starts with the app name - check before removing"
        else:
            return
        if conf != LOW and self.snap.exe_names and self._contains_app_exe(path):
            conf = HIGH
            ev += "; contains the app's own executables"
        if label == "Documents" and conf == HIGH:
            conf = MEDIUM
        self._add_fs(path, conf, ev)

    def _contains_app_exe(self, path: str) -> bool:
        stack, seen = [(path, 0)], 0
        while stack and seen < 400:
            cur, depth = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        seen += 1
                        if e.name.lower() in self.snap.exe_names:
                            return True
                        if depth < 2 and e.is_dir(follow_symlinks=False):
                            stack.append((e.path, depth + 1))
            except OSError:
                continue
        return False

    def _shortcuts(self):
        for root in shortcut_roots():
            stack = [(root, 0)]
            while stack:
                cur, depth = stack.pop()
                try:
                    entries = list(os.scandir(cur))
                except OSError:
                    continue
                for e in entries:
                    lname = e.name.lower()
                    if e.is_dir(follow_symlinks=False):
                        if depth == 0 and (lname in self.strong or lname in self.weak or
                                           (self.publisher and lname == self.publisher)):
                            if self._folder_only_dead_links(e.path):
                                self._add_fs(e.path, HIGH if self.install_n else MEDIUM,
                                             "Start Menu folder whose shortcuts point to the removed app")
                                continue
                        if depth < 2:
                            stack.append((e.path, depth + 1))
                        continue
                    if not lname.endswith(".lnk"):
                        continue
                    target = lnk_target(e.path)
                    if target and self.install_n and norm(target).startswith(self.install_n + os.sep):
                        self._add_fs(e.path, HIGH, f"Shortcut pointing into the removed install folder ({target})", "shortcut")
                    elif target and not os.path.exists(target) and os.path.splitext(lname)[0] in (self.weak | self.strong):
                        self._add_fs(e.path, HIGH, f"Broken shortcut named after the app ({target})", "shortcut")

    def _folder_only_dead_links(self, folder: str) -> bool:
        try:
            files = [f for f in os.scandir(folder)]
        except OSError:
            return False
        if not files:
            return True
        for f in files:
            if not f.name.lower().endswith(".lnk"):
                return False
            t = lnk_target(f.path)
            if t and os.path.exists(t) and not (self.install_n and norm(t).startswith(self.install_n + os.sep)):
                return False
        return True

    # -- registry -----------------------------------------------------------
    def _registry(self):
        app = self.app
        # Orphaned uninstall entry
        if app.reg_full_path:
            from hito.uninstall.regops import key_exists
            if key_exists(app.reg_full_path):
                self._add_reg(app.reg_full_path, HIGH,
                              "The app's own uninstall entry, still registered after its uninstaller finished",
                              hklm=app.reg_hive == "HKLM")
        pub_names = set()
        if app.publisher:
            pub_names = {app.publisher, self.publisher}
        prod_names = {clean_name(app.name)} | {os.path.basename(self.snap.install_location)} if self.snap.install_location else {clean_name(app.name)}
        prod_names = {p for p in prod_names if p and p.lower() not in GENERIC and len(p) >= 3}
        bases = [("HKEY_CURRENT_USER\\SOFTWARE", False), ("HKEY_LOCAL_MACHINE\\SOFTWARE", True),
                 ("HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node", True)]
        for base, hklm in bases:
            subkeys = _subkeys(base)
            lower = {s.lower(): s for s in subkeys}
            for pub in pub_names:
                real_pub = lower.get((pub or "").lower())
                if not real_pub or real_pub.lower() in GENERIC or real_pub.lower() in ("microsoft", "classes", "policies", "wow6432node"):
                    continue
                pub_key = f"{base}\\{real_pub}"
                children = _subkeys(pub_key)
                for child in children:
                    if child.lower() in {p.lower() for p in prod_names}:
                        self._add_reg(f"{pub_key}\\{child}", HIGH, "Settings key under the app's publisher, named after the app", hklm=hklm)
                if children and all(c.lower() in {p.lower() for p in prod_names} for c in children) and not _has_values(pub_key):
                    if not self._claimed_by_other(real_pub.lower()):
                        self._add_reg(pub_key, MEDIUM, "Publisher key that only holds this app's settings", hklm=hklm)
            for prod in prod_names:
                real = lower.get(prod.lower())
                if real and real.lower() not in ("microsoft", "classes", "policies", "wow6432node", "clients"):
                    if not self._claimed_by_other(real.lower()):
                        self._add_reg(f"{base}\\{real}", MEDIUM, "Settings key named exactly after the app", hklm=hklm)
        # Startup entries and App Paths that point into the removed install folder
        if self.install_n or self.snap.exe_names:
            for run_key, hklm in (("HKEY_CURRENT_USER\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run", False),
                                  ("HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run", True),
                                  ("HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Run", True)):
                for name, data in _values(run_key):
                    if self._points_into_app(str(data)):
                        self._add_reg(run_key, HIGH, f"Startup entry that launches the removed app: {data}", value=name, hklm=hklm)
            ap = "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths"
            for sub in _subkeys(ap):
                default = dict(_values(f"{ap}\\{sub}")).get("", "")
                if default and self._points_into_app(str(default)) and not os.path.exists(str(default).strip('"')):
                    self._add_reg(f"{ap}\\{sub}", HIGH, f"App Paths entry for the removed program ({default})", hklm=True)

    def _points_into_app(self, data: str) -> bool:
        d = os.path.expandvars(data).strip().lower()
        if self.install_n and len(self.install_n) > 3:
            path = d.strip('"')
            if path.startswith(self.install_n + os.sep) or ('"' + self.install_n + os.sep) in d:
                return True
        return False


def _subkeys(full: str) -> list[str]:
    if winreg is None:
        return []
    from hito.uninstall.regops import _open
    out = []
    try:
        with _open(full, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


def _values(full: str) -> list[tuple[str, object]]:
    if winreg is None:
        return []
    from hito.uninstall.regops import _open
    out = []
    try:
        with _open(full, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out.append((name, data))
                i += 1
    except OSError:
        pass
    return out


def _has_values(full: str) -> bool:
    return bool(_values(full))


def scan_leftovers(snap: AppSnapshot, other_apps: list[AppRecord], cancel=None) -> tuple[list[LeftoverItem], list[tuple[str, str]]]:
    s = LeftoverScanner(snap, other_apps, cancel)
    return s.run(), s.excluded
