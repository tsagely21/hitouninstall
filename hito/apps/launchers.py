"""Game launcher metadata: Steam (libraryfolders.vdf / appmanifest_*.acf) and Epic (.item manifests)."""
from __future__ import annotations

import glob
import json
import os
import re

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

STEAM_SHARED_APPIDS = {"228980"}  # Steamworks Common Redistributables


def parse_vdf(text: str) -> dict:
    """Minimal Valve KeyValues parser (quoted keys/values, nested braces)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    stack: list[dict] = [{}]
    key: str | None = None
    for quoted, brace in tokens:
        if brace == "{":
            new: dict = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            val = quoted.replace("\\\\", "\\").replace('\\"', '"')
            if key is None:
                key = val
            else:
                stack[-1][key] = val
                key = None
    return stack[0]


def steam_root() -> str:
    if winreg is None:
        return ""
    for hive, sub, name in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
                            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                val, _ = winreg.QueryValueEx(k, name)
                if val and os.path.isdir(val):
                    return os.path.normpath(val)
        except OSError:
            continue
    return ""


def steam_libraries() -> list[str]:
    root = steam_root()
    if not root:
        return []
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="replace") as fh:
            data = parse_vdf(fh.read())
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        for k, v in folders.items():
            path = v.get("path") if isinstance(v, dict) else (v if k.isdigit() else None)
            if path and os.path.isdir(path):
                libs.append(os.path.normpath(path))
    except OSError:
        pass
    seen, out = set(), []
    for lib in libs:
        key = os.path.normcase(lib)
        if key not in seen:
            seen.add(key)
            out.append(lib)
    return out


def steam_games() -> list[dict]:
    games = []
    root = steam_root()
    for lib in steam_libraries():
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            try:
                with open(acf, encoding="utf-8", errors="replace") as fh:
                    st = parse_vdf(fh.read()).get("AppState", {})
            except OSError:
                continue
            appid = st.get("appid", "")
            install = os.path.join(lib, "steamapps", "common", st.get("installdir", ""))
            icon = ""
            for cand in (os.path.join(root, "appcache", "librarycache", f"{appid}_icon.jpg"),
                         *glob.glob(os.path.join(root, "appcache", "librarycache", appid, "*.jpg"))[:1]):
                if cand and os.path.isfile(cand):
                    icon = cand
                    break
            games.append({
                "appid": appid, "name": st.get("name") or f"Steam app {appid}", "install": install,
                "size": int(st.get("SizeOnDisk", "0") or 0), "manifest": acf, "library": lib,
                "last_updated": int(st.get("LastUpdated", "0") or 0), "icon": icon,
                "shared": appid in STEAM_SHARED_APPIDS,
            })
    return games


def epic_games() -> list[dict]:
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    out = []
    for item in glob.glob(os.path.join(pd, "Epic", "EpicGamesLauncher", "Data", "Manifests", "*.item")):
        try:
            with open(item, encoding="utf-8") as fh:
                m = json.load(fh)
        except (OSError, ValueError):
            continue
        if m.get("bIsApplication") is False:
            continue
        out.append({
            "name": m.get("DisplayName") or m.get("AppName"), "install": m.get("InstallLocation", ""),
            "size": int(m.get("InstallSize") or 0), "app_name": m.get("AppName", ""), "manifest": item,
            "version": m.get("AppVersionString", ""),
            "exe": os.path.join(m.get("InstallLocation", ""), m.get("LaunchExecutable", "")) if m.get("LaunchExecutable") else "",
        })
    return out


def game_roots() -> list[str]:
    """Folders whose whole contents count as Games in the storage analyzer."""
    roots = [os.path.join(lib, "steamapps") for lib in steam_libraries()]
    for g in epic_games():
        if g.get("install"):
            roots.append(g["install"])
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for p in (os.path.join(pf, "Epic Games"), os.path.join(pf86, "Epic Games"), os.path.join(pf, "Riot Games"),
              os.path.join(pf86, "GOG Galaxy", "Games"), os.path.join(pf, "EA Games"), os.path.join(pf86, "Ubisoft",
              "Ubisoft Game Launcher", "games"), os.path.join(pf, "ModifiableWindowsApps")):
        if os.path.isdir(p):
            roots.append(p)
    return roots
