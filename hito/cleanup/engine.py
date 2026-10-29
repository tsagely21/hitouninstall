"""Cleanup scanning (read-only) and execution (only after confirmation)."""
from __future__ import annotations

import fnmatch
import os
import shutil
import time
from dataclasses import dataclass, field

from hito.cleanup.catalog import Target, expand
from hito.core import winapi
from hito.core.fsutil import entry_attrs, human_size, is_cloud_only, is_link_like, path_is_link_like, tree_size, walk_files
from hito.safety import oplog
from hito.safety.deleter import DeleteReport, Deleter, PERMANENT
from hito.safety.protect import CLEANUP, SafetyPolicy, running_executables

MAX_FILES_KEPT = 200_000


@dataclass
class TargetScan:
    target: Target
    roots: list[str] = field(default_factory=list)
    files: list[tuple[str, int]] = field(default_factory=list)
    bytes: int | None = 0
    count: int = 0
    running: list[str] = field(default_factory=list)
    needs_admin: bool = False
    note: str = ""
    errors: int = 0

    @property
    def found(self) -> bool:
        if self.target.kind in ("dns",):
            return True
        if self.target.kind == "tool":
            return bool(self.roots) or not self.target.paths
        return (self.bytes or 0) > 0 or self.count > 0


def running_process_names() -> set[str]:
    try:
        import psutil
        return {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
    except Exception:  # noqa: BLE001
        return set()


def _matches(name: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    lname = name.lower()
    return any(fnmatch.fnmatch(lname, p.lower()) for p in patterns)


def scan_target(t: Target, running: set[str] | None = None, cancel=None) -> TargetScan:
    scan = TargetScan(t)
    running = running if running is not None else set()
    scan.running = [p for p in t.processes if p.lower() in running]
    scan.needs_admin = t.admin and not winapi.is_admin()

    if t.kind == "recycle_bin":
        size, count = winapi.recycle_bin_info()
        scan.bytes, scan.count = size, count
        return scan
    if t.kind == "dns":
        scan.bytes, scan.count = 0, 0
        return scan

    for raw in t.paths:
        for p in expand(raw):
            if path_is_link_like(p):
                continue
            scan.roots.append(p)
    if t.kind == "tool":
        if scan.roots:
            total = 0
            for r in scan.roots:
                if os.path.isfile(r):
                    try:
                        total += os.stat(r).st_size
                    except OSError:
                        scan.note = "Size needs administrator rights to measure"
                else:
                    total += tree_size(r, cancel=cancel)[0]
            scan.bytes = total
        else:
            scan.bytes = None
        return scan

    cutoff = time.time() - t.min_age_hours * 3600 if t.min_age_hours else None

    def on_error(_path, _exc):
        scan.errors += 1

    def consider(path: str, size: int, mtime: float, attrs: int):
        if is_cloud_only(attrs) or not _matches(os.path.basename(path), t.patterns):
            return
        if cutoff is not None and mtime > cutoff:
            return
        scan.count += 1
        scan.bytes += size
        if len(scan.files) < MAX_FILES_KEPT:
            scan.files.append((path, size))

    seen_roots: set[str] = set()
    for root in scan.roots:
        key = os.path.normcase(root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        if cancel is not None and cancel.is_set():
            break
        if os.path.isfile(root):
            try:
                st = os.lstat(root)
                consider(root, st.st_size, st.st_mtime, getattr(st, "st_file_attributes", 0))
            except OSError:
                scan.errors += 1
            continue
        if t.recursive:
            for path, st, attrs in walk_files(root, cancel=cancel, on_error=on_error):
                consider(path, st.st_size, st.st_mtime, attrs)
        else:
            try:
                with os.scandir(root) as it:
                    for e in it:
                        a, tag = entry_attrs(e)
                        if is_link_like(a, tag) or not e.is_file(follow_symlinks=False):
                            continue
                        st = e.stat(follow_symlinks=False)
                        consider(e.path, st.st_size, st.st_mtime, a)
            except OSError:
                scan.errors += 1
    if scan.errors and scan.needs_admin:
        scan.note = "Some files need administrator rights"
    elif scan.errors:
        scan.note = f"{scan.errors} location(s) could not be read"
    if scan.count > len(scan.files):
        scan.note = (scan.note + "; " if scan.note else "") + f"only the first {len(scan.files):,} files will be cleaned this run"
    return scan


def scan_catalog(targets: list[Target], cancel=None, progress=None) -> list[TargetScan]:
    running = running_process_names()
    out = []
    for i, t in enumerate(targets):
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(i, len(targets), t.label)
        try:
            out.append(scan_target(t, running, cancel))
        except Exception as exc:  # noqa: BLE001 - one broken target must not stop the scan
            s = TargetScan(t, bytes=0, note=f"Scan failed: {exc}")
            out.append(s)
    if progress:
        progress(len(targets), len(targets), "")
    return out


@dataclass
class PlanItem:
    scan: TargetScan
    files: list[tuple[str, int]] | None = None   # None = every file found

    @property
    def selected_files(self) -> list[tuple[str, int]]:
        return self.scan.files if self.files is None else self.files

    @property
    def bytes(self) -> int:
        if self.scan.target.kind == "recycle_bin":
            return self.scan.bytes or 0
        return sum(s for _, s in self.selected_files)


def describe_plan(items: list[PlanItem], mode: str, dry_run: bool) -> list[str]:
    """Human-readable list of the exact actions execute_plan will take."""
    how = "move to the Recycle Bin" if mode != PERMANENT else "permanently delete"
    lines = []
    for it in items:
        t = it.scan.target
        if t.kind == "recycle_bin":
            lines.append(f"Empty the Recycle Bin on all drives ({human_size(it.bytes)}, {it.scan.count:,} items) - permanent")
        elif t.kind == "dns":
            lines.append("Flush the DNS resolver cache (ipconfig /flushdns)")
        else:
            roots = sorted({os.path.dirname(p) if not t.recursive else _root_of(p, it.scan.roots) for p, _ in it.selected_files})
            lines.append(f"{t.label}: {how} {len(it.selected_files):,} file(s), {human_size(it.bytes)}")
            for r in roots[:6]:
                lines.append(f"    in {r}")
            if len(roots) > 6:
                lines.append(f"    ... and {len(roots) - 6} more folder(s)")
    if dry_run:
        lines.insert(0, "DRY RUN - nothing will be changed; actions are only simulated and logged.")
    return lines


def _root_of(path: str, roots: list[str]) -> str:
    np = os.path.normcase(path)
    for r in roots:
        if np.startswith(os.path.normcase(r)):
            return r
    return os.path.dirname(path)


@dataclass
class CleanupOutcome:
    reports: dict[str, DeleteReport] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    planned_bytes: int = 0
    freed_bytes: int = 0
    disk_delta: int = 0
    dry_run: bool = False


def _drives_of(items: list[PlanItem]) -> set[str]:
    drives = {"C:\\"}
    for it in items:
        for r in it.scan.roots:
            d = os.path.splitdrive(r)[0]
            if d:
                drives.add(d + "\\")
    return drives


def _free(drives: set[str]) -> int:
    total = 0
    for d in drives:
        try:
            total += shutil.disk_usage(d).free
        except OSError:
            pass
    return total


def prune_empty_dirs(root: str, policy: SafetyPolicy) -> int:
    """Remove empty sub-folders below root (never root itself, never through links)."""
    removed = 0
    dirs: list[str] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    a, tag = entry_attrs(e)
                    if is_link_like(a, tag):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(e.path)
                        stack.append(e.path)
        except OSError:
            continue
    for d in sorted(dirs, key=len, reverse=True):
        if not policy.check(d).allowed:
            continue
        try:
            os.rmdir(d)  # only succeeds when empty
            removed += 1
        except OSError:
            pass
    return removed


def execute_plan(items: list[PlanItem], mode: str, dry_run: bool, cancel=None, progress=None) -> CleanupOutcome:
    out = CleanupOutcome(dry_run=dry_run)
    out.planned_bytes = sum(it.bytes for it in items)
    drives = _drives_of(items)
    before = _free(drives)
    running = running_executables()
    total_files = sum(len(it.selected_files) for it in items) or 1
    done = 0
    for it in items:
        t = it.scan.target
        if cancel is not None and cancel.is_set():
            out.messages.append("Cancelled before " + t.label)
            break
        if t.kind == "recycle_bin":
            rep = DeleteReport("permanent", dry_run)
            if dry_run:
                rep.removed, rep.bytes = it.scan.count, it.bytes
                oplog.record("dry-run", "Recycle Bin", "simulated", bytes=it.bytes)
            elif winapi.empty_recycle_bin():
                rep.removed, rep.bytes = it.scan.count, it.bytes
                oplog.record("empty-recycle-bin", "Recycle Bin", "ok", bytes=it.bytes)
            else:
                rep.errors.append(("Recycle Bin", "Windows could not empty the Recycle Bin"))
                oplog.record("empty-recycle-bin", "Recycle Bin", "error")
            out.reports[t.id] = rep
            continue
        if t.kind == "dns":
            rep = DeleteReport("n/a", dry_run)
            if not dry_run:
                try:
                    r = winapi.run_hidden(["ipconfig", "/flushdns"], timeout=20)
                    if r.returncode != 0:
                        rep.errors.append(("DNS", r.stdout.strip() or r.stderr.strip()))
                except Exception as exc:  # noqa: BLE001
                    rep.errors.append(("DNS", str(exc)))
            oplog.record("dry-run" if dry_run else "flush-dns", "DNS cache", "simulated" if dry_run else "ok")
            out.reports[t.id] = rep
            continue
        if t.kind != "files":
            continue
        files = it.selected_files
        allowed = it.scan.roots if t.recursive else [p for p, _ in files]
        policy = SafetyPolicy(CLEANUP, allowed_roots=allowed, running_exes=running)

        def prog(i, n, path, _base=done):
            if progress:
                progress(_base + i, total_files, t.label)

        deleter = Deleter(policy, mode=mode, dry_run=dry_run, allow_warn=True, cancel=cancel, progress=prog,
                          log_each=False, source=t.id)
        rep = deleter.delete([p for p, _ in files], sizes=dict(files))
        done += len(files)
        if not dry_run and t.prune_dirs and t.recursive:
            for r in it.scan.roots:
                if os.path.isdir(r):
                    prune_empty_dirs(r, policy)
        out.reports[t.id] = rep
        oplog.record("cleanup-target", t.label, "simulated" if dry_run else "ok", bytes=rep.bytes,
                     removed=rep.removed, skipped=len(rep.skipped), failed=len(rep.errors), mode=mode)
    out.freed_bytes = sum(r.bytes for r in out.reports.values())
    out.disk_delta = 0 if dry_run else max(0, _free(drives) - before)
    oplog.record("cleanup-summary", "Smart Cleanup", "simulated" if dry_run else "ok",
                 bytes=out.freed_bytes, disk_delta=out.disk_delta, mode=mode)
    return out


def run_tool(t: Target, dry_run: bool) -> tuple[bool, str]:
    """Run one of the supported Windows tools behind an Advanced target."""
    tool = t.tool
    desc = tool.get("powershell") or f"{tool.get('exe')} {tool.get('args', '')}".strip()
    if dry_run:
        oplog.record("dry-run", t.label, "simulated", command=desc)
        return True, f"Dry run: would run `{desc}`."
    if t.admin and not winapi.is_admin():
        return False, "This action needs administrator rights. Use 'Restart as administrator' first."
    oplog.record("system-tool", t.label, "started", command=desc)
    try:
        if not tool.get("capture"):
            winapi.shell_execute(tool["exe"], tool.get("args", ""))
            return True, tool.get("note", "Opened.")
        if "powershell" in tool:
            r = winapi.run_powershell(tool["powershell"], timeout=1800)
        else:
            r = winapi.run_hidden([tool["exe"], *tool.get("args", "").split()], timeout=3600)
        text = (r.stdout or "") + (r.stderr or "")
        ok = r.returncode == 0
        oplog.record("system-tool", t.label, "ok" if ok else "error", exit_code=r.returncode)
        return ok, text.strip()[-4000:] or ("Completed." if ok else f"Exit code {r.returncode}")
    except Exception as exc:  # noqa: BLE001
        oplog.record("system-tool", t.label, "error", error=str(exc))
        return False, str(exc)
