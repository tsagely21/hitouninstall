"""Large-file and duplicate-file finders.

Duplicates are confirmed in three stages so most files are never read:
  1. group by exact size;
  2. compare a cheap signature (first + middle + last 64 KB);
  3. confirm with a full BLAKE2b content hash.
Hard links (same file id) are not duplicates - deleting one frees nothing -
so they are collapsed first. Cloud-only placeholders are skipped because
reading them would download them.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field

from hito.core.fsutil import is_cloud_only, norm, walk_files

CHUNK = 64 * 1024


@dataclass
class FoundFile:
    path: str
    size: int
    mtime: float


@dataclass
class DupGroup:
    size: int
    digest: str
    files: list[FoundFile] = field(default_factory=list)

    @property
    def wasted(self) -> int:
        return self.size * (len(self.files) - 1)


def system_skip_dirs() -> set[str]:
    """Folders skipped when 'protect system locations' is on."""
    env = os.environ
    dirs = [env.get("WINDIR", r"C:\Windows"), env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", ""),
            env.get("ProgramData", ""), env.get("LOCALAPPDATA", ""), env.get("APPDATA", ""),
            os.path.join(os.path.expanduser("~"), "AppData")]
    names = []
    for drive in "CDEFGHIJ":
        names += [f"{drive}:\\System Volume Information", f"{drive}:\\$Recycle.Bin", f"{drive}:\\Windows.old",
                  f"{drive}:\\Recovery", f"{drive}:\\$WinREAgent"]
    return {norm(d) for d in dirs + names if d}


def _skipper(excluded: list[str], protect_system: bool):
    skip = {norm(p) for p in excluded if p}
    if protect_system:
        skip |= system_skip_dirs()
    if not skip:
        return None
    return lambda p: norm(p) in skip


def find_large(roots: list[str], min_size: int, excluded: list[str] | None = None, protect_system: bool = False,
               cancel=None, progress=None) -> list[FoundFile]:
    out: list[FoundFile] = []
    skip = _skipper(excluded or [], protect_system)
    seen = 0
    for root in roots:
        for path, st, attrs in walk_files(root, cancel=cancel, skip_dir=skip):
            seen += 1
            if progress and seen % 2000 == 0:
                progress("Scanning", seen, path)
            if st.st_size >= min_size and not is_cloud_only(attrs):
                out.append(FoundFile(path, st.st_size, st.st_mtime))
    out.sort(key=lambda f: f.size, reverse=True)
    return out


def signature(path: str, size: int) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            parts = [fh.read(CHUNK)]
            if size > 3 * CHUNK:
                fh.seek(size // 2)
                parts.append(fh.read(CHUNK))
                fh.seek(max(0, size - CHUNK))
                parts.append(fh.read(CHUNK))
            elif size > CHUNK:
                parts.append(fh.read())
        return hashlib.blake2b(b"".join(parts), digest_size=16).digest()
    except OSError:
        return None


def full_hash(path: str, cancel=None) -> str | None:
    h = hashlib.blake2b(digest_size=32)
    try:
        with open(path, "rb") as fh:
            while True:
                if cancel is not None and cancel.is_set():
                    return None
                block = fh.read(1024 * 1024)
                if not block:
                    break
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def _file_id(path: str):
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino) if st.st_ino else None
    except OSError:
        return None


def find_duplicates(roots: list[str], min_size: int = 1024 * 1024, excluded: list[str] | None = None,
                    protect_system: bool = True, cancel=None, progress=None) -> list[DupGroup]:
    skip = _skipper(excluded or [], protect_system)
    by_size: dict[int, list[FoundFile]] = defaultdict(list)
    seen = 0
    for root in roots:
        for path, st, attrs in walk_files(root, cancel=cancel, skip_dir=skip):
            seen += 1
            if progress and seen % 2000 == 0:
                progress("1/3 Grouping by size", seen, path)
            if st.st_size >= max(1, min_size) and not is_cloud_only(attrs):
                by_size[st.st_size].append(FoundFile(path, st.st_size, st.st_mtime))
    candidates = [g for g in by_size.values() if len(g) > 1]

    # collapse hard links (same physical file)
    stage1: list[list[FoundFile]] = []
    for g in candidates:
        ids: dict = {}
        for f in g:
            fid = _file_id(f.path)
            key = fid if fid else ("path", f.path)
            ids.setdefault(key, f)
        uniq = list(ids.values())
        if len(uniq) > 1:
            stage1.append(uniq)

    stage2: list[list[FoundFile]] = []
    total = sum(len(g) for g in stage1) or 1
    done = 0
    for g in stage1:
        if cancel is not None and cancel.is_set():
            return []
        sigs: dict[bytes, list[FoundFile]] = defaultdict(list)
        for f in g:
            done += 1
            if progress and done % 50 == 0:
                progress("2/3 Comparing signatures", done * 100 // total, f.path)
            s = signature(f.path, f.size)
            if s is not None:
                sigs[s].append(f)
        stage2 += [v for v in sigs.values() if len(v) > 1]

    groups: list[DupGroup] = []
    total = sum(len(g) for g in stage2) or 1
    done = 0
    for g in stage2:
        hashes: dict[str, list[FoundFile]] = defaultdict(list)
        for f in g:
            if cancel is not None and cancel.is_set():
                return []
            done += 1
            if progress:
                progress("3/3 Verifying full contents", done * 100 // total, f.path)
            d = full_hash(f.path, cancel)
            if d:
                hashes[d].append(f)
        for d, files in hashes.items():
            if len(files) > 1:
                groups.append(DupGroup(files[0].size, d, sorted(files, key=lambda f: f.mtime)))
    groups.sort(key=lambda grp: grp.wasted, reverse=True)
    return groups


def auto_select(group: DupGroup, keep: str = "oldest") -> list[FoundFile]:
    """Files to remove so exactly one copy stays. keep: oldest | newest | shortest."""
    if keep == "newest":
        keeper = max(group.files, key=lambda f: f.mtime)
    elif keep == "shortest":
        keeper = min(group.files, key=lambda f: (len(f.path), f.path))
    else:
        keeper = min(group.files, key=lambda f: f.mtime)
    return [f for f in group.files if f is not keeper]


def validate_selection(groups: list[DupGroup], selected: set[str]) -> list[DupGroup]:
    """Groups in which every copy is selected (must be refused)."""
    sel = {norm(p) for p in selected}
    return [g for g in groups if all(norm(f.path) in sel for f in g.files)]
