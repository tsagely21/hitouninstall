"""Shared UI flows: deleting user-selected files, opening locations, admin restart."""
from __future__ import annotations

import os

from PySide6.QtWidgets import QApplication, QMessageBox

from hito.core import winapi
from hito.core.fsutil import human_size
from hito.safety.deleter import PERMANENT, RECYCLE, Deleter
from hito.safety.protect import BLOCK, USER, WARN, SafetyPolicy
from hito.ui.dialogs import ConfirmDialog, InfoDialog, report_details
from hito.ui.workers import run_task


def delete_user_paths(parent, state, items: list[tuple[str, int]], source: str, on_finished=None):
    """Confirm, then delete files/folders the user picked (Recycle Bin by default)."""
    if not items:
        return
    policy = SafetyPolicy(USER)
    allowed, warned, blocked = [], [], []
    for path, size in items:
        v = policy.check(path)
        if v.level == BLOCK:
            blocked.append((path, size, v.reason))
        elif v.level == WARN:
            warned.append((path, size, v.reason))
        else:
            allowed.append((path, size))
    if not allowed and not warned:
        InfoDialog(parent, "Nothing can be deleted", "Every selected item is protected.",
                   "\n".join(f"{p}\n    {r}" for p, _s, r in blocked), glyph="shield").exec()
        return
    s = state.settings
    total = sum(sz for _p, sz in allowed) + sum(sz for _p, sz, _r in warned)
    lines = []
    if s.dry_run:
        lines.append("DRY RUN is on - nothing will be changed, actions are only simulated and logged.\n")
    for p, sz in allowed:
        lines.append(f"{human_size(sz):>9}  {p}")
    for p, sz, r in warned:
        lines.append(f"{human_size(sz):>9}  {p}\n           WARNING: {r}")
    if blocked:
        lines.append(f"\nProtected - will NOT be touched ({len(blocked)}):")
        lines += [f"           {p}  ({r})" for p, _s, r in blocked]
    count = len(allowed) + len(warned)
    dlg = ConfirmDialog(
        parent, f"Delete {count:,} item(s)?",
        f"{human_size(total)} selected. By default items go to the <b>Recycle Bin</b> so you can restore them. "
        "Files on network or some removable drives may be deleted permanently by Windows.",
        lines, confirm_text="Delete", danger=True,
        options=[("Delete permanently instead of using the Recycle Bin (cannot be undone)", s.user_delete_mode == PERMANENT)],
        require_ack=("I understand some items may contain saves, settings or browser data" if warned else None))
    if not dlg.exec():
        return
    mode = PERMANENT if dlg.option(0) else RECYCLE
    paths = [p for p, _ in allowed] + [p for p, _s, _r in warned]
    sizes = {p: sz for p, sz in allowed} | {p: sz for p, sz, _r in warned}
    dry = s.dry_run
    QApplication.setOverrideCursor(winapi_wait_cursor())

    def work(cancel, progress):
        d = Deleter(SafetyPolicy(USER), mode=mode, dry_run=dry, allow_warn=True, cancel=cancel, source=source)
        rep = d.delete(paths, sizes)
        removed = [] if dry else [p for p in paths if not os.path.lexists(p)]
        return rep, removed

    def done(res):
        QApplication.restoreOverrideCursor()
        rep, removed = res
        state.apply_removed(removed)
        details = report_details(rep)
        InfoDialog(parent, "Done" if not rep.errors else "Finished with problems", rep.summary,
                   details, glyph="check" if not rep.errors else "warning").exec()
        if on_finished:
            on_finished(rep, removed)

    def err(msg):
        QApplication.restoreOverrideCursor()
        InfoDialog(parent, "Delete failed", "Something went wrong; nothing else was changed.", msg, glyph="warning").exec()

    run_task(work, on_result=done, on_error=err)


def winapi_wait_cursor():
    from PySide6.QtCore import Qt
    return Qt.BusyCursor


def open_location(path: str):
    winapi.open_in_explorer(path)


def restart_as_admin(parent) -> None:
    r = QMessageBox.question(parent, "Restart as administrator",
                             "Hito will restart with administrator rights so it can read and clean system "
                             "locations. Windows will show a permission prompt.\n\nRestart now?")
    if r == QMessageBox.Yes and winapi.relaunch_as_admin():
        QApplication.quit()
