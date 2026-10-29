"""The single gateway for removing files and folders.

Every removal: re-checks the SafetyPolicy, honours dry-run, uses the Recycle
Bin or permanent deletion as requested, never follows junctions/symlinks
while deleting folders, and records the outcome in the operation log.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field

from hito.core.fsutil import entry_attrs, is_link_like, path_is_link_like, tree_size
from hito.safety import oplog
from hito.safety.protect import BLOCK, WARN, SafetyPolicy

RECYCLE = "recycle"
PERMANENT = "permanent"


@dataclass
class DeleteReport:
    mode: str
    dry_run: bool
    removed: int = 0
    bytes: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def merge(self, other: "DeleteReport"):
        self.removed += other.removed
        self.bytes += other.bytes
        self.skipped += other.skipped
        self.errors += other.errors

    @property
    def summary(self) -> str:
        from hito.core.fsutil import human_size
        verb = "Would remove" if self.dry_run else ("Moved to Recycle Bin" if self.mode == RECYCLE else "Removed")
        s = f"{verb}: {self.removed:,} item(s), {human_size(self.bytes)}"
        if self.skipped:
            s += f" | skipped {len(self.skipped):,}"
        if self.errors:
            s += f" | failed {len(self.errors):,}"
        return s


def _unlink(path: str, force_readonly: bool):
    try:
        os.unlink(path)
    except PermissionError:
        if not force_readonly:
            raise
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)


def safe_rmtree(path: str, force_readonly: bool = False, errors: list | None = None) -> int:
    """Delete a folder tree without ever following links. Returns bytes freed.

    Junctions/symlinks inside the tree are removed as links; their targets are untouched.
    """
    freed = 0
    errors = errors if errors is not None else []
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError as exc:
        errors.append((path, str(exc)))
        return 0
    for entry in entries:
        try:
            attrs, tag = entry_attrs(entry)
            if is_link_like(attrs, tag) or entry.is_symlink():
                if attrs & 0x10:  # directory link (junction / dir symlink)
                    os.rmdir(entry.path)
                else:
                    os.unlink(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                freed += safe_rmtree(entry.path, force_readonly, errors)
            else:
                size = entry.stat(follow_symlinks=False).st_size
                _unlink(entry.path, force_readonly)
                freed += size
        except OSError as exc:
            errors.append((entry.path, exc.strerror or str(exc)))
    try:
        os.rmdir(path)
    except OSError as exc:
        if not errors:
            errors.append((path, exc.strerror or str(exc)))
    return freed


class Deleter:
    def __init__(self, policy: SafetyPolicy, mode: str = RECYCLE, dry_run: bool = False, allow_warn: bool = False,
                 cancel=None, progress=None, log_each: bool = True, source: str = ""):
        if mode not in (RECYCLE, PERMANENT):
            raise ValueError(mode)
        self.policy = policy
        self.mode = mode
        self.dry_run = dry_run
        self.allow_warn = allow_warn
        self.cancel = cancel
        self.progress = progress
        self.log_each = log_each
        self.source = source

    def delete(self, paths, sizes: dict[str, int] | None = None) -> DeleteReport:
        report = DeleteReport(self.mode, self.dry_run)
        paths = list(paths)
        for i, path in enumerate(paths):
            if self.cancel is not None and self.cancel.is_set():
                report.skipped.append((path, "Cancelled"))
                continue
            if self.progress:
                self.progress(i, len(paths), path)
            self._delete_one(path, (sizes or {}).get(path), report)
        if self.progress:
            self.progress(len(paths), len(paths), "")
        return report

    def _delete_one(self, path: str, size: int | None, report: DeleteReport):
        verdict = self.policy.check(path)
        if verdict.level == BLOCK or (verdict.level == WARN and not self.allow_warn):
            reason = verdict.reason if verdict.level == BLOCK else f"Needs confirmation: {verdict.reason}"
            report.skipped.append((path, reason))
            self._log("skip", path, "skipped", reason=reason)
            return
        if not os.path.lexists(path):
            report.skipped.append((path, "Already gone"))
            return
        is_link = path_is_link_like(path)
        is_dir = os.path.isdir(path) and not is_link
        if size is None:
            size = 0 if is_link else tree_size(path)[0]
        if self.dry_run:
            report.removed += 1
            report.bytes += size
            self._log("dry-run", path, "simulated", bytes=size, mode=self.mode)
            return
        try:
            if self.mode == RECYCLE:
                from send2trash import send2trash
                send2trash(path)
                report.removed += 1
                report.bytes += size
                self._log("recycle", path, "ok", bytes=size)
            elif is_link:
                if os.path.isdir(path):
                    os.rmdir(path)
                else:
                    os.unlink(path)
                report.removed += 1
                self._log("delete", path, "ok", bytes=0, note="link removed, target untouched")
            elif is_dir:
                errs: list = []
                freed = safe_rmtree(path, force_readonly=True, errors=errs)
                report.bytes += freed
                if errs:
                    report.errors.extend(errs[:50])
                    self._log("delete", path, "partial", bytes=freed, error=f"{len(errs)} item(s) could not be removed")
                else:
                    report.removed += 1
                    self._log("delete", path, "ok", bytes=freed)
            else:
                _unlink(path, force_readonly=True)
                report.removed += 1
                report.bytes += size
                self._log("delete", path, "ok", bytes=size)
        except Exception as exc:  # noqa: BLE001 - report every failure, never crash
            msg = getattr(exc, "strerror", None) or str(exc)
            report.errors.append((path, msg))
            self._log("delete", path, "error", error=msg)

    def _log(self, op: str, path: str, result: str, **extra):
        if self.log_each or result not in ("ok", "simulated"):
            oplog.record(op, path, result, context=self.policy.context, source=self.source or None, **extra)
