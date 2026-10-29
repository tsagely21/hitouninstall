"""Installed application and game discovery.

Sources: the Windows uninstall registry (HKLM 64/32-bit, HKCU), Microsoft
Store packages (Get-AppxPackage), Steam app manifests and Epic manifests.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

from hito.apps import launchers
from hito.core import winapi
from hito.core.fsutil import norm

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

UNINSTALL_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
GUID_RE = re.compile(r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")

GAME_PUBLISHERS = {
    "valve", "blizzard entertainment", "riot games", "epic games", "electronic arts", "ubisoft", "rockstar games",
    "bethesda softworks", "cd projekt red", "mojang", "bungie", "activision", "square enix", "bandai namco",
    "sega", "capcom", "2k", "paradox interactive", "gog.com", "hoyoverse", "cognosphere", "nexon",
}
GAME_UNINSTALL_HINTS = ("steam://uninstall", "battle.net", "ea desktop", "eadesktop", "ubisoft", "gog galaxy",
                        "riot client", "xboxgames", "epic games")


@dataclass
class AppRecord:
    key: str                     # unique id, e.g. "reg:HKLM64:{GUID}" or "steam:730"
    name: str
    publisher: str = ""
    version: str = ""
    install_location: str = ""
    install_date: date | None = None
    source: str = "win32"        # win32 | msi | store | steam | epic
    platform: str = "Windows"    # Windows / Microsoft Store / Steam / Epic Games / Battle.net / ...
    is_game: bool = False
    uninstall_string: str = ""
    quiet_uninstall_string: str = ""
    msi_product_code: str = ""
    reg_hive: str = ""           # HKLM | HKCU
    reg_view: int = 64
    reg_path: str = ""           # subkey under the hive
    display_icon: str = ""
    reported_size: int = 0       # bytes Windows / launcher reports
    measured_size: int | None = None
    size_note: str = ""
    package_full_name: str = ""
    package_family: str = ""
    steam_appid: str = ""
    epic_app_name: str = ""
    manifest: str = ""
    shared_component: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return self.measured_size if self.measured_size is not None else self.reported_size

    @property
    def size_source(self) -> str:
        if self.measured_size is not None:
            return "measured"
        if self.reported_size:
            return "reported"
        return "unknown"

    @property
    def can_uninstall(self) -> bool:
        return bool(self.uninstall_string or self.msi_product_code or self.package_full_name or self.steam_appid
                    or self.epic_app_name)

    @property
    def reg_full_path(self) -> str:
        if not self.reg_hive:
            return ""
        hive = "HKEY_LOCAL_MACHINE" if self.reg_hive == "HKLM" else "HKEY_CURRENT_USER"
        path = self.reg_path
        if self.reg_hive == "HKLM" and self.reg_view == 32:
            path = path.replace("SOFTWARE\\", "SOFTWARE\\WOW6432Node\\", 1)
        return f"{hive}\\{path}"


# --- Registry ----------------------------------------------------------------

def _q(k, name, default=""):
    try:
        v, _ = winreg.QueryValueEx(k, name)
        return v if v is not None else default
    except OSError:
        return default


def _parse_install_date(raw) -> date | None:
    s = str(raw or "").strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return d if 1995 <= d.year <= date.today().year else None
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        try:
            return date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            return None
    return None


def _icon_path(raw: str) -> str:
    raw = (raw or "").strip().strip('"')
    if "," in raw:
        head, _, tail = raw.rpartition(",")
        if tail.strip().lstrip("-").isdigit():
            raw = head
    raw = os.path.expandvars(raw.strip().strip('"'))
    return raw if raw and os.path.isfile(raw) else ""


def registry_apps() -> list[AppRecord]:
    if winreg is None:
        return []
    out: list[AppRecord] = []
    views = [
        ("HKLM", winreg.HKEY_LOCAL_MACHINE, 64, winreg.KEY_WOW64_64KEY),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE, 32, winreg.KEY_WOW64_32KEY),
        ("HKCU", winreg.HKEY_CURRENT_USER, 64, winreg.KEY_WOW64_64KEY),
    ]
    for hive_name, hive, view, flag in views:
        try:
            root = winreg.OpenKey(hive, UNINSTALL_SUBKEY, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        rec = _record_from_key(k, sub, hive_name, view)
                except OSError:
                    continue
                if rec:
                    out.append(rec)
    return out


def _record_from_key(k, sub: str, hive_name: str, view: int) -> AppRecord | None:
    name = str(_q(k, "DisplayName")).strip()
    if not name:
        return None
    if _q(k, "SystemComponent", 0) == 1 or _q(k, "ParentKeyName") or _q(k, "ParentDisplayName"):
        return None
    if str(_q(k, "ReleaseType")).lower() in ("update", "hotfix", "security update", "service pack"):
        return None
    uninstall = str(_q(k, "UninstallString")).strip()
    is_msi = _q(k, "WindowsInstaller", 0) == 1
    product_code = sub if (is_msi and GUID_RE.fullmatch(sub)) else ""
    if not product_code and uninstall.lower().startswith("msiexec"):
        m = GUID_RE.search(uninstall)
        product_code = m.group(0) if m else ""
    if not uninstall and not product_code:
        return None
    size_kb = _q(k, "EstimatedSize", 0)
    try:
        size = int(size_kb) * 1024
    except (TypeError, ValueError):
        size = 0
    loc = os.path.expandvars(str(_q(k, "InstallLocation")).strip().strip('"'))
    rec = AppRecord(
        key=f"reg:{hive_name}{view}:{sub}", name=name, publisher=str(_q(k, "Publisher")).strip(),
        version=str(_q(k, "DisplayVersion")).strip(), install_location=os.path.normpath(loc) if loc else "",
        install_date=_parse_install_date(_q(k, "InstallDate")), source="msi" if product_code else "win32",
        uninstall_string=uninstall, quiet_uninstall_string=str(_q(k, "QuietUninstallString")).strip(),
        msi_product_code=product_code, reg_hive=hive_name, reg_view=view, reg_path=f"{UNINSTALL_SUBKEY}\\{sub}",
        display_icon=_icon_path(str(_q(k, "DisplayIcon"))), reported_size=size,
    )
    if not rec.install_location and rec.display_icon:
        guess = os.path.dirname(rec.display_icon)
        if _looks_like_app_dir(guess):
            rec.extra["location_from_icon"] = guess
    m = re.match(r"steam app (\d+)$", sub, re.I)
    if m or "steam://uninstall" in uninstall.lower():
        rec.steam_appid = m.group(1) if m else uninstall.rsplit("/", 1)[-1]
    _tag_platform(rec)
    return rec


def _looks_like_app_dir(path: str) -> bool:
    n = norm(path)
    generic = {norm(p) for p in (os.environ.get("WINDIR", r"C:\Windows"), os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32"),
                                 os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                                 os.environ.get("CommonProgramFiles", "")) if p}
    return n not in generic and len(n) > 3


def _tag_platform(rec: AppRecord):
    pub = rec.publisher.lower()
    us = rec.uninstall_string.lower()
    loc = rec.install_location.lower()
    if rec.steam_appid:
        rec.platform, rec.is_game = "Steam", True
    elif "battle.net" in us or pub.startswith("blizzard"):
        rec.platform, rec.is_game = "Battle.net", True
    elif "eadesktop" in us or "ea desktop" in us or pub == "electronic arts":
        rec.platform, rec.is_game = "EA app", True
    elif "gog" in us or pub == "gog.com":
        rec.platform, rec.is_game = "GOG", True
    elif "ubisoft" in us or pub.startswith("ubisoft"):
        rec.platform, rec.is_game = "Ubisoft Connect", True
    elif "riot" in us or pub.startswith("riot"):
        rec.platform, rec.is_game = "Riot", True
    if not rec.is_game:
        if any(h in us or h in loc for h in GAME_UNINSTALL_HINTS) or pub in GAME_PUBLISHERS:
            rec.is_game = True
        if "steamapps" in loc:
            rec.is_game, rec.platform = True, "Steam"
    if rec.name.lower() in ("steam", "epic games launcher", "battle.net", "ea app", "gog galaxy", "ubisoft connect"):
        rec.is_game = False  # the launchers themselves are applications


# --- Microsoft Store ---------------------------------------------------------

_STORE_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$start = @{}
Get-StartApps | ForEach-Object { $pfn = ($_.AppID -split '!')[0]; if (-not $start.ContainsKey($pfn)) { $start[$pfn] = $_.Name } }
$pk = Get-AppxPackage -PackageTypeFilter Main | Where-Object { -not $_.IsFramework -and -not $_.NonRemovable -and $_.SignatureKind -ne 'System' }
$items = $pk | ForEach-Object {
  [pscustomobject]@{
    Name = $_.Name; FullName = $_.PackageFullName; Family = $_.PackageFamilyName; Publisher = $_.Publisher
    Version = $_.Version.ToString(); Location = $_.InstallLocation; Display = $start[$_.PackageFamilyName]
    Signature = $_.SignatureKind.ToString(); Dev = $_.IsDevelopmentMode
  }
}
$json = ConvertTo-Json -InputObject @($items) -Compress -Depth 2
[IO.File]::WriteAllText($env:HITO_OUT, $json, [Text.Encoding]::UTF8)
"""


def _publisher_name(dn: str) -> str:
    m = re.search(r"(?:^|,\s*)O=\"?([^,\"]+)", dn or "") or re.search(r"CN=\"?([^,\"]+)", dn or "")
    return m.group(1).strip() if m else (dn or "")


def _store_manifest(location: str) -> tuple[str, str, str]:
    """(display name, publisher display name, logo file) from AppxManifest.xml."""
    try:
        tree = ET.parse(os.path.join(location, "AppxManifest.xml"))
    except (OSError, ET.ParseError):
        return "", "", ""
    vals = {}
    for el in tree.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("DisplayName", "PublisherDisplayName", "Logo") and tag not in vals and el.text:
            vals[tag] = el.text.strip()
    clean = lambda v: "" if v.startswith("ms-resource:") else v  # noqa: E731
    return clean(vals.get("DisplayName", "")), clean(vals.get("PublisherDisplayName", "")),         _resolve_logo(location, vals.get("Logo", ""))


def _resolve_logo(location: str, logo: str) -> str:
    if not logo:
        return ""
    base, ext = os.path.splitext(os.path.join(location, logo))
    for cand in (base + ext, base + ".scale-100" + ext, base + ".scale-200" + ext, base + ".targetsize-48" + ext):
        if os.path.isfile(cand):
            return cand
    folder = os.path.dirname(base)
    stem = os.path.basename(base).lower()
    try:
        for f in os.listdir(folder):
            if f.lower().startswith(stem) and f.lower().endswith(ext.lower()):
                return os.path.join(folder, f)
    except OSError:
        pass
    return ""


last_store_error = ""


def store_apps() -> list[AppRecord]:
    global last_store_error
    last_store_error = ""
    if not winapi.IS_WINDOWS:
        return []
    import tempfile
    fd, out_file = tempfile.mkstemp(prefix="hito_store_", suffix=".json")
    os.close(fd)
    try:
        os.environ["HITO_OUT"] = out_file
        r = winapi.run_powershell(_STORE_PS, timeout=240)
        with open(out_file, encoding="utf-8-sig") as fh:
            raw = fh.read().strip()
        if not raw:
            last_store_error = (r.stderr or "PowerShell returned nothing").strip()[:300]
            return []
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - PowerShell missing/blocked: report, keep the other sources
        last_store_error = str(exc)[:300] or exc.__class__.__name__
        return []
    finally:
        try:
            os.remove(out_file)
        except OSError:
            pass
    if isinstance(data, dict):
        data = [data]
    out = []
    for p in data:
        loc = p.get("Location") or ""
        m_name, m_pub, logo = _store_manifest(loc) if loc else ("", "", "")
        display = p.get("Display") or ""
        if not display and not (m_name and p.get("Signature") == "Store"):
            continue  # background components and codecs without a Start entry
        rec = AppRecord(
            key=f"store:{p.get('FullName')}", name=display or m_name, publisher=m_pub or _publisher_name(p.get("Publisher", "")),
            version=p.get("Version", ""), install_location=loc, source="store", platform="Microsoft Store",
            package_full_name=p.get("FullName", ""), package_family=p.get("Family", ""), display_icon=logo,
        )
        rec.is_game = "xboxgames" in loc.lower()
        out.append(rec)
    return out


# --- Launchers ---------------------------------------------------------------

def launcher_apps() -> list[AppRecord]:
    out = []
    for g in launchers.steam_games():
        out.append(AppRecord(
            key=f"steam:{g['appid']}", name=g["name"], publisher="Steam", install_location=g["install"],
            source="steam", platform="Steam", is_game=not g["shared"], steam_appid=g["appid"],
            reported_size=g["size"], manifest=g["manifest"], display_icon=g["icon"], shared_component=g["shared"],
        ))
    for g in launchers.epic_games():
        out.append(AppRecord(
            key=f"epic:{g['app_name']}", name=g["name"], publisher="Epic Games Store", version=g.get("version", ""),
            install_location=os.path.normpath(g["install"]) if g["install"] else "", source="epic",
            platform="Epic Games", is_game=True, epic_app_name=g["app_name"], reported_size=g["size"],
            manifest=g["manifest"], display_icon=g["exe"] if g["exe"] and os.path.isfile(g["exe"]) else "",
        ))
    return out


def discover_all(include_store: bool = True) -> list[AppRecord]:
    reg = registry_apps()
    launch = launcher_apps()
    store = store_apps() if include_store else []
    by_steam = {a.steam_appid: a for a in launch if a.steam_appid}
    merged: list[AppRecord] = []
    for a in reg:
        if a.steam_appid and a.steam_appid in by_steam:
            s = by_steam[a.steam_appid]
            if a.display_icon and not s.display_icon:
                s.display_icon = a.display_icon
            s.reg_hive, s.reg_view, s.reg_path = a.reg_hive, a.reg_view, a.reg_path
            s.uninstall_string = a.uninstall_string
            continue
        merged.append(a)
    merged += launch + store
    # de-duplicate identical registry entries visible in several views
    seen: dict[tuple, AppRecord] = {}
    for a in merged:
        k = (a.name.lower(), a.version.lower(), a.source if a.source in ("store", "steam", "epic") else "reg")
        if k in seen:
            prev = seen[k]
            if not prev.install_location and a.install_location:
                seen[k] = a
            continue
        seen[k] = a
    apps = list(seen.values())
    _mark_shared_locations(apps)
    return sorted(apps, key=lambda a: a.name.lower())


def _mark_shared_locations(apps: list[AppRecord]):
    """Flag apps whose install folder overlaps another app's, so sizes are not trusted blindly."""
    locs = [(norm(a.install_location), a) for a in apps if a.install_location and len(a.install_location) > 3]
    for la, a in locs:
        for lb, b in locs:
            if a is b:
                continue
            if la == lb:
                a.size_note = f"Shares its folder with {b.name}"
                break
            if lb.startswith(la + os.sep):
                a.size_note = f"Folder also contains {b.name}"
                break


def app_icon_path(app: AppRecord) -> str:
    if app.display_icon:
        return app.display_icon
    return app.extra.get("location_from_icon", "")
