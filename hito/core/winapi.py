"""Thin ctypes wrappers around the Win32 calls the app needs.

Everything here degrades gracefully (returns a neutral value) so the core
modules can still be imported and unit-tested off Windows.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

# File attribute / reparse constants (winnt.h)
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ONLY_MASK = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # junctions and volume mount points
IO_REPARSE_TAG_SYMLINK = 0xA000000C
REPARSE_NAME_SURROGATE_BIT = 0x20000000  # tag points at another file system location

DRIVE_TYPES = {0: "Unknown", 1: "No root", 2: "Removable", 3: "Local disk", 4: "Network", 5: "CD/DVD", 6: "RAM disk"}


def is_packaged() -> bool:
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def program_path() -> str:
    """The .exe the user launched (for Nuitka onefile, not the unpacked copy)."""
    if not is_packaged():
        return os.path.abspath(sys.argv[0])
    compiled = globals().get("__compiled__")
    for cand in (getattr(compiled, "original_argv0", None), sys.argv[0], sys.executable):
        if cand and cand.lower().endswith(".exe") and os.path.isfile(cand):
            return os.path.abspath(cand)
    return sys.executable


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Restart this program elevated. Only ever called from an explicit user click."""
    if not IS_WINDOWS:
        return False
    if is_packaged():
        exe, params = program_path(), " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe, params = sys.executable, "-m hito " + " ".join(f'"{a}"' for a in sys.argv[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


@dataclass
class VolumeInfo:
    label: str
    filesystem: str
    serial: int


def volume_info(root: str) -> VolumeInfo:
    if not IS_WINDOWS:
        return VolumeInfo("", "", 0)
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    max_len = wintypes.DWORD()
    flags = wintypes.DWORD()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), name, 261, ctypes.byref(serial), ctypes.byref(max_len), ctypes.byref(flags), fs, 261
    )
    if not ok:
        return VolumeInfo("", "", 0)
    return VolumeInfo(name.value, fs.value, serial.value)


def drive_type(root: str) -> str:
    if not IS_WINDOWS:
        return "Unknown"
    return DRIVE_TYPES.get(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)), "Unknown")


def logical_drives() -> list[str]:
    if not IS_WINDOWS:
        return ["/"]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]


# --- Known folders ---------------------------------------------------------

_KNOWN_FOLDER_GUIDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "SavedGames": "{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        h = text.strip("{}").replace("-", "")
        g = cls()
        g.Data1 = int(h[0:8], 16)
        g.Data2 = int(h[8:12], 16)
        g.Data3 = int(h[12:16], 16)
        for i in range(8):
            g.Data4[i] = int(h[16 + i * 2:18 + i * 2], 16)
        return g


_known_cache: dict[str, str] = {}


def known_folder(name: str) -> str:
    """Resolve a shell known folder (handles OneDrive-redirected Desktop/Documents)."""
    if name in _known_cache:
        return _known_cache[name]
    path = ""
    if IS_WINDOWS and name in _KNOWN_FOLDER_GUIDS:
        try:
            guid = _GUID.parse(_KNOWN_FOLDER_GUIDS[name])
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                path = out.value or ""
                ctypes.windll.ole32.CoTaskMemFree(out)
        except Exception:
            path = ""
    if not path:
        fallback = {"SavedGames": "Saved Games"}.get(name, name)
        path = os.path.join(os.path.expanduser("~"), fallback)
    _known_cache[name] = path
    return path


# --- Recycle Bin -----------------------------------------------------------

class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


def recycle_bin_info(root: str | None = None) -> tuple[int, int]:
    """(bytes, item_count) in the Recycle Bin, all drives when root is None."""
    if not IS_WINDOWS:
        return 0, 0
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    hr = ctypes.windll.shell32.SHQueryRecycleBinW(root, ctypes.byref(info))
    if hr != 0:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin() -> bool:
    if not IS_WINDOWS:
        return False
    SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI, SHERB_NOSOUND = 0x1, 0x2, 0x4
    hr = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
    # S_OK, or E_UNEXPECTED when the bin was already empty
    return hr in (0, -2147418113)


# --- Process launching -----------------------------------------------------

class _SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


class LaunchError(Exception):
    pass


class LaunchedProcess:
    """A process started through ShellExecuteEx. Windows shows its UAC prompt if it needs one."""

    def __init__(self, handle, pid: int):
        self.handle = handle
        self.pid = pid

    def wait(self, timeout_ms: int) -> bool:
        """True once the process has exited."""
        if not self.handle:
            return True
        WAIT_OBJECT_0 = 0
        return ctypes.windll.kernel32.WaitForSingleObject(self.handle, timeout_ms) == WAIT_OBJECT_0

    def exit_code(self) -> int | None:
        if not self.handle:
            return None
        code = wintypes.DWORD()
        if ctypes.windll.kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return None if code.value == 259 else int(code.value)  # 259 = STILL_ACTIVE
        return None

    def close(self):
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def shell_execute(file: str, params: str = "", verb: str = "open", directory: str | None = None) -> LaunchedProcess:
    if not IS_WINDOWS:
        raise LaunchError("Windows only")
    SEE_MASK_NOCLOSEPROCESS = 0x40
    SEE_MASK_FLAG_NO_UI = 0x400
    info = _SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
    info.lpVerb = verb
    info.lpFile = file
    info.lpParameters = params or None
    info.lpDirectory = directory
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.GetLastError()
        if err == 1223:
            raise LaunchError("The administrator prompt was declined.")
        raise LaunchError(f"Could not start {file} ({ctypes.FormatError(err).strip()})")
    pid = ctypes.windll.kernel32.GetProcessId(info.hProcess) if info.hProcess else 0
    return LaunchedProcess(info.hProcess, int(pid))


def open_in_explorer(path: str) -> None:
    if not IS_WINDOWS:
        return
    if os.path.isdir(path):
        subprocess.Popen(["explorer", os.path.normpath(path)])
    else:
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')


def open_uri(uri: str) -> None:
    if IS_WINDOWS:
        os.startfile(uri)  # noqa: S606 - user-initiated launcher URIs only


# --- Windows of a process --------------------------------------------------

def close_windows_of(pids: set[int]) -> int:
    """Ask the top-level windows owned by pids to close (WM_CLOSE). Returns windows signalled."""
    if not IS_WINDOWS or not pids:
        return 0
    WM_CLOSE = 0x0010
    user32 = ctypes.windll.user32
    count = 0
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        nonlocal count
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            count += 1
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return count


def hidden_startupinfo():
    """STARTUPINFO that keeps helper consoles (reg, powershell) from flashing."""
    if not IS_WINDOWS:
        return None, 0
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si, subprocess.CREATE_NO_WINDOW


def run_hidden(args, timeout: float = 120, input_text: str | None = None) -> subprocess.CompletedProcess:
    si, flags = hidden_startupinfo()
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, startupinfo=si, creationflags=flags,
        input=input_text, encoding="utf-8", errors="replace",
    )


def run_powershell(script: str, timeout: float = 120) -> subprocess.CompletedProcess:
    import base64
    prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    encoded = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
    return run_hidden(args, timeout=timeout)
