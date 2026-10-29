"""Catalogue of cleanup targets.

Ported from the reference cleaner (pc-storage-cleaner) and tightened:
  * per-profile browser caches (every Chromium profile, every Firefox profile)
    instead of only "Default";
  * age filters on temp folders so files in use by running programs are left;
  * removed targets that deleted personal or recovery data in the original:
    Office "Recent"/"UnsavedFiles", Zoom "data", Recent Documents, Prefetch,
    the Windows Installer $PatchCache$, event-log clearing and the print
    spooler restart;
  * Windows.old, the component store, Delivery Optimization and hibernation
    are handled through the supported Windows tools, never by deleting files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from hito.core.fsutil import env_path

SAFE, REVIEW, ADVANCED = "safe", "review", "advanced"
RISK_LABELS = {SAFE: "Safe", REVIEW: "Review recommended", ADVANCED: "Advanced"}


@dataclass
class Target:
    id: str
    label: str
    group: str
    risk: str
    description: str                       # what the location contains
    effect: str                            # what happens after it is cleaned
    paths: list[str] = field(default_factory=list)   # folders/files, %VARS% and * globs allowed
    patterns: list[str] = field(default_factory=list)  # file name globs; empty = every file
    min_age_hours: int = 0
    admin: bool = False
    kind: str = "files"                    # files | recycle_bin | dns | tool
    tool: dict = field(default_factory=dict)
    processes: list[str] = field(default_factory=list)  # close these apps for a complete clean
    default: bool = False
    prune_dirs: bool = True
    recursive: bool = True                 # False: only files directly inside each path

    @property
    def risk_label(self) -> str:
        return RISK_LABELS[self.risk]


L = "%LOCALAPPDATA%"
R = "%APPDATA%"
W = "%WINDIR%"
PD = "%ProgramData%"

CHROMIUM = [
    ("chrome", "Google Chrome", L + r"\Google\Chrome\User Data", "chrome.exe"),
    ("edge", "Microsoft Edge", L + r"\Microsoft\Edge\User Data", "msedge.exe"),
    ("brave", "Brave", L + r"\BraveSoftware\Brave-Browser\User Data", "brave.exe"),
    ("vivaldi", "Vivaldi", L + r"\Vivaldi\User Data", "vivaldi.exe"),
    ("opera", "Opera", R + r"\Opera Software\Opera Stable", "opera.exe"),
    ("operagx", "Opera GX", R + r"\Opera Software\Opera GX Stable", "opera.exe"),
]


def _chromium_targets() -> list[Target]:
    out = []
    for key, name, base, proc in CHROMIUM:
        if key.startswith("opera"):
            prof = [base, base + r"\_side_profiles\*"]
        else:
            prof = [base + r"\Default", base + r"\Profile *", base + r"\Guest Profile"]
        cache_dirs = ["Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache"]
        paths = [f"{p}\\{c}" for p in prof for c in cache_dirs]
        paths += [base + r"\GrShaderCache", base + r"\ShaderCache", base + r"\GraphiteDawnCache"]
        out.append(Target(
            f"browser_{key}", f"{name} cache", "Browsers", SAFE,
            f"Cached web pages, images, scripts and GPU shaders for every {name} profile.",
            "Sites load a little slower the first time. Passwords, cookies, history and bookmarks are not touched.",
            paths, processes=[proc], default=True))
        out.append(Target(
            f"browser_{key}_sw", f"{name} offline site storage", "Browsers", REVIEW,
            "Service-worker caches that let installed web apps and some sites work offline.",
            "Web apps re-download their offline data. You stay signed in.",
            [f"{p}\\Service Worker\\CacheStorage" for p in prof] + [f"{p}\\Service Worker\\ScriptCache" for p in prof],
            processes=[proc]))
    return out


def _steam_targets() -> list[Target]:
    try:
        from hito.apps.launchers import steam_libraries, steam_root
        root = steam_root()
        libs = steam_libraries()
    except Exception:  # noqa: BLE001
        root, libs = "", []
    out = []
    if root:
        out.append(Target(
            "steam_cache", "Steam web cache & logs", "Game launchers", SAFE,
            "Steam's built-in browser cache, HTTP cache, crash dumps and log files.",
            "Steam store pages reload their images. Games, saves and downloads are untouched.",
            [root + r"\appcache\httpcache", root + r"\htmlcache", L + r"\Steam\htmlcache", root + r"\logs",
             root + r"\dumps"], processes=["steam.exe"], default=True))
    if libs:
        out.append(Target(
            "steam_shader", "Steam shader pre-caches", "Game launchers", REVIEW,
            "Pre-compiled graphics shaders Steam downloads for each game.",
            "Games may stutter briefly while shaders rebuild; Steam can download them again.",
            [lib + r"\steamapps\shadercache" for lib in libs], processes=["steam.exe"]))
        out.append(Target(
            "steam_downloading", "Steam unfinished downloads", "Game launchers", REVIEW,
            "Partially downloaded games and updates waiting in Steam's download folders.",
            "Any paused download or update restarts from the beginning.",
            [lib + r"\steamapps\downloading" for lib in libs] + [lib + r"\steamapps\temp" for lib in libs],
            processes=["steam.exe"]))
    return out


def build_catalog(temp_min_age_hours: int = 24) -> list[Target]:
    t: list[Target] = [
        # --- Windows -------------------------------------------------------
        Target("user_temp", "User temporary files", "Windows", SAFE,
               "Files programs left in your %TEMP% folder.",
               f"Nothing you use. Files changed in the last {temp_min_age_hours} h are kept because running programs may still need them.",
               ["%TEMP%"], min_age_hours=temp_min_age_hours, default=True),
        Target("windows_temp", "Windows temporary files", "Windows", SAFE,
               r"Files left in C:\Windows\Temp by installers and services.",
               f"Nothing you use. Files changed in the last {temp_min_age_hours} h are kept.",
               [W + r"\Temp"], min_age_hours=temp_min_age_hours, admin=True, default=True),
        Target("thumbnails", "Thumbnail cache", "Windows", SAFE,
               "File Explorer's saved preview thumbnails for pictures and videos.",
               "Explorer rebuilds thumbnails as you browse folders. Files in use are skipped.",
               [L + r"\Microsoft\Windows\Explorer"], patterns=["thumbcache_*.db"], default=True, prune_dirs=False, recursive=False),
        Target("icon_cache", "Icon cache", "Windows", REVIEW,
               "File Explorer's cached program and file-type icons.",
               "Icons can look blank until Explorer restarts or you sign out. Only useful if icons look wrong.",
               [L + r"\Microsoft\Windows\Explorer", L], patterns=["iconcache_*.db", "IconCache.db"], prune_dirs=False, recursive=False),
        Target("wer", "Windows error reports", "Windows", SAFE,
               "Crash and problem reports queued or archived by Windows Error Reporting.",
               "You lose the ability to resend these old reports to Microsoft.",
               [L + r"\Microsoft\Windows\WER", PD + r"\Microsoft\Windows\WER\ReportArchive",
                PD + r"\Microsoft\Windows\WER\ReportQueue", PD + r"\Microsoft\Windows\WER\Temp"], default=True),
        Target("crash_dumps", "App crash dumps", "Windows", SAFE,
               r"Memory dumps written when programs crash (AppData\Local\CrashDumps).",
               "Developers can no longer analyse those old crashes.",
               [L + r"\CrashDumps"], default=True),
        Target("system_dumps", "System crash dumps", "Windows", REVIEW,
               "Minidumps and MEMORY.DMP written when Windows itself crashed (blue screens).",
               "You lose the data needed to diagnose past blue screens.",
               [W + r"\Minidump", W + r"\MEMORY.DMP", W + r"\LiveKernelReports"], admin=True),
        Target("windows_logs", "Old Windows logs", "Windows", REVIEW,
               r"Setup, CBS and diagnostic logs in C:\Windows\Logs older than 30 days.",
               "Older troubleshooting history is gone; current logs are kept.",
               [W + r"\Logs", W + r"\Panther", W + r"\debug"], patterns=["*.log", "*.etl", "*.cab", "*.txt"],
               min_age_hours=24 * 30, admin=True),
        Target("recycle_bin", "Recycle Bin", "Windows", REVIEW,
               "Files you deleted that are still kept in the Recycle Bin on every drive.",
               "Emptying is permanent: these files can no longer be restored.",
               kind="recycle_bin"),
        Target("dns", "DNS resolver cache", "Windows", SAFE,
               "Remembered website addresses. Takes no real disk space.",
               "Fixes stale-address problems; sites resolve again on first visit.", kind="dns"),

        # --- Apps ----------------------------------------------------------
        Target("discord", "Discord cache", "Apps", SAFE,
               "Discord's cached images, scripts and GPU data.",
               "Images and emojis download again. Messages and settings are on Discord's servers.",
               [R + r"\discord\Cache", R + r"\discord\Code Cache", R + r"\discord\GPUCache",
                R + r"\discordptb\Cache", R + r"\discordcanary\Cache"], processes=["Discord.exe"], default=True),
        Target("teams", "Microsoft Teams cache", "Apps", SAFE,
               "Cached data for classic and new Microsoft Teams.",
               "Teams loads slower once while it refreshes. Chats live in the cloud.",
               [R + r"\Microsoft\Teams\Cache", R + r"\Microsoft\Teams\Code Cache", R + r"\Microsoft\Teams\GPUCache",
                R + r"\Microsoft\Teams\Service Worker\CacheStorage",
                L + r"\Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams\EBWebView\Default\Cache"],
               processes=["Teams.exe", "ms-teams.exe"], default=True),
        Target("slack", "Slack cache", "Apps", SAFE, "Slack's cached files and GPU data.",
               "Slack re-downloads images once.",
               [R + r"\Slack\Cache", R + r"\Slack\Code Cache", R + r"\Slack\GPUCache", R + r"\Slack\Service Worker\CacheStorage"],
               processes=["slack.exe"], default=True),
        Target("spotify", "Spotify streaming cache", "Apps", REVIEW,
               "Songs Spotify cached while streaming. Can also hold songs downloaded for offline use.",
               "Recently played music streams again; offline downloads may need re-downloading.",
               [L + r"\Spotify\Storage", L + r"\Spotify\Browser\Cache"], processes=["Spotify.exe"]),
        Target("vscode", "VS Code caches & logs", "Apps", SAFE,
               "Visual Studio Code's cache, logs and downloaded extension installers.",
               "Nothing visible; VS Code recreates them.",
               [R + r"\Code\Cache", R + r"\Code\CachedData", R + r"\Code\Code Cache", R + r"\Code\GPUCache",
                R + r"\Code\CachedExtensionVSIXs", R + r"\Code\logs"], processes=["Code.exe"], default=True),
        Target("onedrive_logs", "OneDrive logs", "Apps", SAFE, "OneDrive sync client log files.",
               "Nothing visible. Your files are not affected.",
               [L + r"\Microsoft\OneDrive\logs", L + r"\Microsoft\OneDrive\setup\logs"], min_age_hours=24, default=True),
        Target("adobe_media", "Adobe media cache", "Apps", REVIEW,
               "Premiere Pro / After Effects media cache and peak files.",
               "Adobe apps rebuild audio waveforms and conformed media when you open projects.",
               [R + r"\Adobe\Common\Media Cache Files", R + r"\Adobe\Common\Media Cache", R + r"\Adobe\Common\Peak Files"]),
        Target("gpu_shader", "Graphics driver shader cache", "Apps", REVIEW,
               "Shader caches kept by NVIDIA, AMD, Intel and DirectX.",
               "Games may stutter briefly while shaders are rebuilt.",
               [L + r"\NVIDIA\DXCache", L + r"\NVIDIA\GLCache", L + r"\AMD\DxCache", L + r"\AMD\GLCache",
                L + r"\AMD\DxcCache", L + r"\D3DSCache", L + r"\Intel\ShaderCache", "%USERPROFILE%\\AppData\\LocalLow\\NVIDIA\\PerDriverVersion\\DXCache"]),

        # --- Game launchers ------------------------------------------------
        Target("epic", "Epic Games Launcher cache & logs", "Game launchers", SAFE,
               "The Epic launcher's web cache, HTTP cache and logs.",
               "Store pages reload. Installed games are untouched.",
               [L + r"\EpicGamesLauncher\Saved\webcache", L + r"\EpicGamesLauncher\Saved\webcache_4147",
                L + r"\EpicGamesLauncher\Saved\webcache_4430", L + r"\EpicGamesLauncher\Saved\HttpRequestCache",
                L + r"\EpicGamesLauncher\Saved\Logs"], processes=["EpicGamesLauncher.exe"], default=True),
        Target("battlenet", "Battle.net cache & logs", "Game launchers", SAFE,
               "Blizzard Battle.net app cache and logs.", "The app reloads its data. Games are untouched.",
               [R + r"\Battle.net\Cache", R + r"\Battle.net\BrowserCache", R + r"\Battle.net\Logs",
                L + r"\Battle.net\Cache", L + r"\Battle.net\BrowserCaches", L + r"\Blizzard Entertainment\Battle.net\Cache"],
               processes=["Battle.net.exe"], default=True),
        Target("ea", "EA app cache", "Game launchers", SAFE, "EA app web cache and logs.",
               "The app reloads its data. Games are untouched.",
               [L + r"\Electronic Arts\EA Desktop\CEF\BrowserCache", L + r"\Electronic Arts\EA Desktop\Logs",
                L + r"\EADesktop\cache"], processes=["EADesktop.exe"], default=True),
        Target("gog", "GOG Galaxy web cache", "Game launchers", SAFE, "GOG Galaxy's embedded browser cache.",
               "Store pages reload.", [PD + r"\GOG.com\Galaxy\webcache", L + r"\GOG.com\Galaxy\Applications\webcache"],
               processes=["GalaxyClient.exe"], default=True),

        # --- Developer -----------------------------------------------------
        Target("npm", "npm cache", "Developer", REVIEW, "Downloaded npm package tarballs.",
               "The next npm install downloads packages again. Projects are not affected.",
               [R + r"\npm-cache", L + r"\npm-cache"]),
        Target("yarn", "Yarn cache", "Developer", REVIEW, "Yarn's global package cache.",
               "Packages are downloaded again on next install.", [L + r"\Yarn\Cache", "%USERPROFILE%\\.yarn\\berry\\cache"]),
        Target("pip", "pip cache", "Developer", REVIEW, "Python wheels and downloads cached by pip.",
               "pip downloads packages again when needed.", [L + r"\pip\cache"]),
        Target("uv", "uv cache", "Developer", REVIEW, "Python packages cached by uv.",
               "uv downloads packages again when needed.", [L + r"\uv\cache"]),
        Target("nuget", "NuGet HTTP cache", "Developer", REVIEW, ".NET NuGet HTTP and temp caches (not the package folder).",
               "Packages are fetched again on restore.", [L + r"\NuGet\v3-cache", L + r"\NuGet\plugins-cache", L + r"\Temp\NuGetScratch"]),
        Target("gradle", "Gradle caches", "Developer", REVIEW, "Gradle build and dependency caches.",
               "The next build re-downloads dependencies and is slower once.", ["%USERPROFILE%\\.gradle\\caches"]),
        Target("go_build", "Go build cache", "Developer", REVIEW, "Compiled Go packages.",
               "The next go build recompiles.", [L + r"\go-build"]),
        Target("cargo", "Cargo registry cache", "Developer", REVIEW, "Downloaded Rust crate archives.",
               "Crates are downloaded again when building.", ["%USERPROFILE%\\.cargo\\registry\\cache"]),

        # --- System tools (supported Windows mechanisms only) ---------------
        Target("windows_old", "Previous Windows installation", "System tools", ADVANCED,
               r"C:\Windows.old and setup leftovers kept for about 10 days after a Windows upgrade.",
               "You can no longer roll back to the previous Windows version. Removed with Windows' own Disk Cleanup.",
               [r"C:\Windows.old"], kind="tool",
               tool={"label": "Open Disk Cleanup", "exe": "cleanmgr.exe", "args": "/d C:", "elevate": False,
                     "note": "In Disk Cleanup choose 'Clean up system files', then 'Previous Windows installation(s)'."}),
        Target("component_store", "Windows Update component cleanup", "System tools", ADVANCED,
               "Superseded Windows Update components in the component store (WinSxS).",
               "Installed updates can no longer be uninstalled. Uses DISM /StartComponentCleanup and needs administrator rights.",
               kind="tool", admin=True,
               tool={"label": "Run DISM cleanup", "exe": "Dism.exe", "args": "/Online /Cleanup-Image /StartComponentCleanup",
                     "capture": True}),
        Target("delivery_opt", "Delivery Optimization cache", "System tools", ADVANCED,
               "Windows Update files kept to share with other PCs.",
               "Windows downloads updates directly instead of reusing these. Uses the Delete-DeliveryOptimizationCache cmdlet.",
               [W + r"\ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization\Cache",
                W + r"\SoftwareDistribution\DeliveryOptimization"],
               kind="tool", admin=True,
               tool={"label": "Clear with Windows", "powershell": "Delete-DeliveryOptimizationCache -Force", "capture": True}),
        Target("hibernation", "Hibernation file", "System tools", ADVANCED,
               r"C:\hiberfil.sys, about as large as part of your RAM.",
               "Turns off hibernation and Fast Startup until you run 'powercfg /hibernate on'.",
               [r"C:\hiberfil.sys"], kind="tool", admin=True,
               tool={"label": "Turn off hibernation", "exe": "powercfg.exe", "args": "/hibernate off", "capture": True}),
    ]
    t[10:10] = _chromium_targets() + [
        Target("firefox", "Firefox cache", "Browsers", SAFE,
               "Cached pages, start-up cache and thumbnails for every Firefox profile.",
               "Sites load a little slower once. Passwords, cookies and history are not touched.",
               [L + r"\Mozilla\Firefox\Profiles\*\cache2", L + r"\Mozilla\Firefox\Profiles\*\startupCache",
                L + r"\Mozilla\Firefox\Profiles\*\thumbnails", L + r"\Mozilla\Firefox\Profiles\*\jumpListCache"],
               processes=["firefox.exe"], default=True),
    ]
    t += _steam_targets()
    return t


def expand(path: str) -> list[str]:
    """Expand %VARS% and * wildcards to existing paths."""
    import glob
    p = env_path(path)
    if not p:
        return []
    if "*" in p or "?" in p:
        return sorted(glob.glob(p))
    return [p] if os.path.lexists(p) else []
