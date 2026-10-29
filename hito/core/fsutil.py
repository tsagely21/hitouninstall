"""Path and size helpers shared by every module."""
from __future__ import annotations

import os
import stat as _stat
from datetime import datetime

from hito.core import winapi


def human_size(n: float | int | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}" if abs(n) < 100 else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_time(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return ""


def norm(path: str) -> str:
    """Case-folded absolute path without a trailing separator (except drive roots)."""
    p = os.path.normcase(os.path.abspath(path))
    if len(p) > 3:
        p = p.rstrip("\\/")
    return p


def is_under(path: str, root: str, *, inclusive: bool = True) -> bool:
    p, r = norm(path), norm(root)
    if p == r:
        return inclusive
    r = r if r.endswith(os.sep) else r + os.sep
    return p.startswith(r)


def env_path(*parts: str) -> str:
    """Join after expanding %VARS%; empty string if a variable is missing."""
    joined = os.path.join(*parts)
    expanded = os.path.expandvars(joined)
    return "" if "%" in expanded else expanded


def entry_attrs(entry: os.DirEntry) -> tuple[int, int]:
    """(file_attributes, reparse_tag) from scandir data without an extra system call."""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return 0, 0
    return getattr(st, "st_file_attributes", 0), getattr(st, "st_reparse_tag", 0)


def is_link_like(attrs: int, tag: int) -> bool:
    """True for junctions, symlinks and any other reparse point that redirects elsewhere.

    Cloud-file placeholders are reparse points too, but they are not name
    surrogates, so OneDrive folders are still walked.
    """
    if not attrs & winapi.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    if tag == 0:
        return True  # unknown tag: be conservative and treat it like a link
    return bool(tag & winapi.REPARSE_NAME_SURROGATE_BIT)


def is_cloud_only(attrs: int) -> bool:
    return bool(attrs & winapi.CLOUD_ONLY_MASK)


def path_is_link_like(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if _stat.S_ISLNK(st.st_mode):
        return True
    return is_link_like(getattr(st, "st_file_attributes", 0), getattr(st, "st_reparse_tag", 0))


def walk_files(root: str, cancel=None, skip_dir=None, on_error=None):
    """Yield (path, stat_result, attrs) for every regular file under root.

    Never follows junctions/symlinks, so loops and double counting are impossible.
    skip_dir(path) -> bool lets callers prune protected folders.
    """
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError as exc:
            if on_error:
                on_error(current, exc)
            continue
        with it:
            for entry in it:
                try:
                    attrs, tag = entry_attrs(entry)
                    if is_link_like(attrs, tag) or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if skip_dir is None or not skip_dir(entry.path):
                            stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        yield entry.path, entry.stat(follow_symlinks=False), attrs
                except OSError as exc:
                    if on_error:
                        on_error(entry.path, exc)


def tree_size(path: str, cancel=None) -> tuple[int, int]:
    """(bytes, files) under path, skipping links and cloud-only placeholders."""
    if os.path.isfile(path):
        try:
            return os.lstat(path).st_size, 1
        except OSError:
            return 0, 0
    total = count = 0
    for _p, st, attrs in walk_files(path, cancel=cancel):
        if not is_cloud_only(attrs):
            total += st.st_size
        count += 1
    return total, count
