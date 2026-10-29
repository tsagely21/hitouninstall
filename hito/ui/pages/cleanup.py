"""Smart Cleanup: scan -> explain -> select -> preview -> confirm -> clean -> report."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView, QScrollArea, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from hito.cleanup.catalog import ADVANCED, RISK_LABELS, REVIEW, SAFE, build_catalog
from hito.cleanup.engine import PlanItem, describe_plan, execute_plan, run_tool, scan_catalog
from hito.core import winapi
from hito.core.fsutil import human_size
from hito.safety.deleter import PERMANENT, RECYCLE
from hito.ui import actions
from hito.ui.dialogs import ConfirmDialog, InfoDialog, report_details
from hito.ui.widgets import Card, PageHeader, ProgressPanel, badge, button, label
from hito.ui.workers import run_task

RISK_TEXT = {
    SAFE: "Disposable temporary files and caches that programs recreate automatically.",
    REVIEW: "Usually safe, but removing them costs something: re-downloads, slower first use, or lost history.",
    ADVANCED: "System changes made through Windows' own tools. Each needs a separate, explicit confirmation.",
}


class FilePicker(QDialog):
    """Lets the user untick individual files of one cleanup target."""

    def __init__(self, parent, scan, selected: set[str] | None):
        super().__init__(parent)
        self.setWindowTitle(scan.target.label)
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.addWidget(label(scan.target.label, "H2"))
        lay.addWidget(label(scan.target.description, "Muted", wrap=True))
        files = sorted(scan.files, key=lambda f: f[1], reverse=True)
        self.shown = files[:3000]
        if len(files) > len(self.shown):
            lay.addWidget(label(f"Showing the {len(self.shown):,} largest of {len(files):,} files. Files not shown stay "
                                "selected unless you clear everything.", "Faint", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setUniformRowHeights(True)
        for path, size in self.shown:
            it = QTreeWidgetItem([path, human_size(size)])
            it.setData(0, Qt.UserRole, (path, size))
            it.setCheckState(0, Qt.Checked if selected is None or path in selected else Qt.Unchecked)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for text, state in (("Select all", Qt.Checked), ("Select none", Qt.Unchecked)):
            b = button(text)
            b.clicked.connect(lambda _=False, s=state: self._all(s))
            row.addWidget(b)
        row.addStretch(1)
        ok = button("Use selection", "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        self.all_files = files

    def _all(self, state):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)

    def selection(self) -> list[tuple[str, int]] | None:
        unticked = set()
        ticked = 0
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                ticked += 1
            else:
                unticked.add(it.data(0, Qt.UserRole)[0])
        if not unticked:
            return None
        if ticked == 0:
            return []
        return [f for f in self.all_files if f[0] not in unticked]


class TargetRow(Card):
    def __init__(self, page, scan):
        super().__init__(padding=14, spacing=4, hover=True)
        self.page = page
        self.scan = scan
        self.files: list | None = None  # None = all files
        t = scan.target
        top = QHBoxLayout()
        top.setSpacing(10)
        self.check = QCheckBox()
        self.check.setChecked(t.default and t.kind != "tool" and scan.found)
        self.check.toggled.connect(page.update_total)
        if t.kind != "tool":
            top.addWidget(self.check)
        name = label(t.label, "H3")
        top.addWidget(name)
        top.addWidget(badge(t.group, "muted"))
        if scan.running:
            b = badge(f"Close {', '.join(p.replace('.exe', '') for p in scan.running)} for a full clean", "review")
            b.setToolTip("Files in use by a running app are skipped safely.")
            top.addWidget(b)
        if scan.needs_admin:
            top.addWidget(badge("Needs administrator", "review"))
        top.addStretch(1)
        size = "-" if scan.bytes is None else human_size(scan.bytes)
        if t.kind == "dns":
            size = "no disk space"
        self.size_lbl = label(size, "Stat")
        top.addWidget(self.size_lbl)
        self.lay.addLayout(top)
        self.lay.addWidget(label(t.description, "Muted", wrap=True))
        bottom = QHBoxLayout()
        eff = label("After cleaning: " + t.effect, "Faint", wrap=True)
        bottom.addWidget(eff, 1)
        if t.kind == "files" and scan.count:
            self.count_lbl = label(f"{scan.count:,} files", "Faint")
            bottom.addWidget(self.count_lbl)
            det = button("Choose files...", "ghost", "list")
            det.clicked.connect(self.pick)
            bottom.addWidget(det)
        if t.kind == "tool":
            run = button(t.tool.get("label", "Run..."), "danger" if t.admin else None, "shield")
            run.clicked.connect(lambda: page.run_tool(scan))
            bottom.addWidget(run)
        if scan.note:
            self.lay.addWidget(label(scan.note, "Faint", wrap=True))
        self.lay.addLayout(bottom)

    @property
    def selected(self) -> bool:
        return self.check.isChecked() and self.scan.target.kind != "tool"

    def plan_item(self) -> PlanItem:
        return PlanItem(self.scan, self.files)

    def pick(self):
        current = None if self.files is None else {p for p, _ in self.files}
        dlg = FilePicker(self, self.scan, current)
        if dlg.exec():
            self.files = dlg.selection()
            n = len(self.scan.files) if self.files is None else len(self.files)
            if hasattr(self, "count_lbl"):
                self.count_lbl.setText(f"{n:,} of {self.scan.count:,} files")
            self.check.setChecked(n > 0)
            self.page.update_total()


class CleanupPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setObjectName("Page")
        self.rows: list[TargetRow] = []
        self.task = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 16)
        lay.setSpacing(14)
        head = PageHeader("Smart Cleanup", "Scans known temporary and cache locations. You choose what goes; "
                          "documents, downloads, saves and passwords are never included.")
        if not winapi.is_admin():
            ab = button("Restart as administrator", None, "admin", "Lets Hito include Windows temp, logs and crash dumps")
            ab.clicked.connect(lambda: actions.restart_as_admin(self))
            head.actions.addWidget(ab)
        self.scan_btn = button("Scan again", "primary", "refresh")
        self.scan_btn.clicked.connect(self.start_scan)
        head.actions.addWidget(self.scan_btn)
        lay.addWidget(head)
        self.progress = ProgressPanel()
        self.progress.cancelRequested.connect(lambda: self.task and self.task.stop())
        lay.addWidget(self.progress)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 8, 0)
        self.body_lay.setSpacing(10)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

        footer = Card(padding=14)
        fl = QHBoxLayout()
        fl.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label("Selected for cleanup", "Muted"))
        self.total_lbl = label("-", "Stat")
        col.addWidget(self.total_lbl)
        fl.addLayout(col)
        self.mode_lbl = label("", "Faint", wrap=True)
        fl.addWidget(self.mode_lbl, 1)
        sel_safe = button("Select Safe only", None, "check")
        sel_safe.clicked.connect(self.select_safe)
        fl.addWidget(sel_safe)
        self.clean_btn = button("Clean selected...", "primary", "broom")
        self.clean_btn.clicked.connect(self.clean)
        fl.addWidget(self.clean_btn)
        footer.lay.addLayout(fl)
        lay.addWidget(footer)

        state.cleanupScanned.connect(self.populate)
        state.settingsChanged.connect(self.update_total)
        if state.cleanup_scans is not None:
            self.populate()
        else:
            self.body_lay.addWidget(label("Scanning cleanup locations...", "Muted"))

    def handle_payload(self, payload):
        if payload == "scan" and self.task is None:
            self.start_scan()

    def start_scan(self):
        if self.task is not None:
            return
        self.scan_btn.setEnabled(False)
        self.progress.start("Scanning cleanup locations", determinate=True)
        targets = build_catalog(self.state.settings.temp_min_age_hours)

        def work(cancel, progress):
            return scan_catalog(targets, cancel, lambda i, n, lbl: progress((i, n, lbl)))

        def prog(p):
            i, n, lbl = p
            self.progress.update_progress(lbl, i * 100 / max(1, n))

        def done(res):
            self.state.cleanup_scans = res
            self.state.cleanupScanned.emit()

        def fin():
            self.task = None
            self.scan_btn.setEnabled(True)
            self.progress.finish()

        self.task = run_task(work, on_result=done, on_progress=prog, on_done=fin,
                             on_error=lambda m: InfoDialog(self, "Scan failed", "", m, glyph="warning").exec())

    def populate(self):
        while self.body_lay.count():
            it = self.body_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.rows = []
        scans = self.state.cleanup_scans or []
        for risk in (SAFE, REVIEW, ADVANCED):
            group = [s for s in scans if s.target.risk == risk and (s.found or s.target.kind == "tool")]
            if risk == ADVANCED:
                group = [s for s in group if s.roots or not s.target.paths]
            if not group:
                continue
            total = sum(s.bytes or 0 for s in group if s.target.kind != "dns")
            h = QHBoxLayout()
            h.addWidget(label(RISK_LABELS[risk], "H2"))
            h.addWidget(badge(RISK_LABELS[risk], risk))
            h.addStretch(1)
            h.addWidget(label(human_size(total) + " found", "Muted"))
            hw = QWidget()
            hw.setLayout(h)
            self.body_lay.addWidget(hw)
            self.body_lay.addWidget(label(RISK_TEXT[risk], "Faint", wrap=True))
            for s in sorted(group, key=lambda s: -(s.bytes or 0)):
                row = TargetRow(self, s)
                self.rows.append(row)
                self.body_lay.addWidget(row)
        missing = [s for s in scans if not s.found and s.target.kind == "files"]
        if missing:
            self.body_lay.addWidget(label(f"{len(missing)} other locations were checked and are already empty or not installed.",
                                          "Faint", wrap=True))
        self.body_lay.addStretch(1)
        self.update_total()

    def select_safe(self):
        for r in self.rows:
            if r.scan.target.kind != "tool":
                r.check.setChecked(r.scan.target.risk == SAFE and r.scan.found)

    def _mode(self) -> str:
        return PERMANENT if self.state.settings.cleanup_delete_mode == PERMANENT else RECYCLE

    def update_total(self):
        items = [r.plan_item() for r in self.rows if r.selected]
        self.total_lbl.setText(human_size(sum(i.bytes for i in items)))
        mode = "deleted permanently (they are disposable)" if self._mode() == PERMANENT else "moved to the Recycle Bin"
        dry = "  DRY RUN is on: nothing will actually change." if self.state.settings.dry_run else ""
        self.mode_lbl.setText(f"Selected files will be {mode}. Change this in Settings.{dry}")
        self.clean_btn.setEnabled(bool(items))

    def clean(self):
        items = [r.plan_item() for r in self.rows if r.selected]
        items = [i for i in items if i.scan.target.kind != "files" or i.selected_files]
        if not items:
            return
        dry = self.state.settings.dry_run
        mode = self._mode()
        lines = describe_plan(items, mode, dry)
        running = sorted({p for i in items for p in i.scan.running})
        note = ""
        if running:
            note = f"<br>Running now: {', '.join(running)}. Their files in use will be skipped."
        total = sum(i.bytes for i in items)
        dlg = ConfirmDialog(self, "Clean selected items?",
                            f"About {human_size(total)} in {len(items)} location(s).{note}", lines,
                            confirm_text="Simulate" if dry else "Clean now", danger=not dry, glyph="broom")
        if not dlg.exec():
            return
        self.clean_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.progress.start("Cleaning", determinate=True)

        def work(cancel, progress):
            return execute_plan(items, mode, dry, cancel, lambda i, n, lbl: progress((i, n, lbl)))

        def prog(p):
            i, n, lbl = p
            self.progress.update_progress(lbl, i * 100 / max(1, n))

        def done(out):
            removed = sum(r.removed for r in out.reports.values())
            skipped = sum(len(r.skipped) for r in out.reports.values())
            failed = sum(len(r.errors) for r in out.reports.values())
            if out.dry_run:
                summary = f"Dry run: {removed:,} items ({human_size(out.freed_bytes)}) would have been removed. Nothing changed."
            else:
                summary = (f"<b>{human_size(out.freed_bytes)}</b> removed ({removed:,} items). "
                           f"Free space on the affected drives grew by <b>{human_size(out.disk_delta)}</b>.")
                if mode == RECYCLE:
                    summary += " Files went to the Recycle Bin; space is freed once you empty it."
            if skipped or failed:
                summary += f"<br>{skipped:,} skipped (in use or protected), {failed:,} could not be removed."
            details = "\n\n".join(f"== {k} ==\n{report_details(r)}" for k, r in out.reports.items() if r.skipped or r.errors)
            InfoDialog(self, "Cleanup finished", summary, details[:200_000], glyph="check").exec()

        def fin():
            self.task = None
            self.progress.finish()
            self.scan_btn.setEnabled(True)
            self.start_scan()

        self.task = run_task(work, on_result=done, on_progress=prog, on_done=fin,
                             on_error=lambda m: InfoDialog(self, "Cleanup failed", "", m, glyph="warning").exec())

    def run_tool(self, scan):
        t = scan.target
        dry = self.state.settings.dry_run
        cmd = t.tool.get("powershell") or f"{t.tool.get('exe')} {t.tool.get('args', '')}"
        lines = [f"Run: {cmd}", "", t.description, "", "Consequence: " + t.effect]
        if t.tool.get("note"):
            lines += ["", t.tool["note"]]
        if t.admin and not winapi.is_admin():
            InfoDialog(self, "Administrator rights needed",
                       "This action uses a Windows tool that needs administrator rights. Use 'Restart as administrator' "
                       "first.", glyph="shield").exec()
            return
        dlg = ConfirmDialog(self, t.label, "This is an <b>Advanced</b> action that changes Windows itself.", lines,
                            confirm_text="Simulate" if dry else "Run it", danger=True,
                            require_ack="I understand what this changes and that it cannot be undone here",
                            glyph="shield")
        if not dlg.exec():
            return
        self.progress.start(f"Running {t.label}")
        self.progress.cancel_btn.setEnabled(False)

        def done(res):
            ok, text = res
            InfoDialog(self, "Done" if ok else "Did not complete", text.splitlines()[-1] if text else "", text,
                       glyph="check" if ok else "warning").exec()

        run_task(lambda c, p: run_tool(t, dry), on_result=done, on_done=self.progress.finish)
