"""Deep Uninstaller: official uninstall, completion detection, evidence-based leftover review, batch queue."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QRadioButton, QScrollArea, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from hito.core.fsutil import human_size
from hito.safety.deleter import PERMANENT, RECYCLE, Deleter
from hito.safety.protect import LEFTOVER, SafetyPolicy
from hito.ui import actions, icons, theme
from hito.ui.dialogs import ConfirmDialog, InfoDialog, report_details
from hito.ui.widgets import Card, PageHeader, button, divider, label
from hito.ui.workers import run_task
from hito.uninstall import leftovers as lo
from hito.uninstall import methods, processes, regops, runner

CONF_COLORS = {lo.HIGH: "ok", lo.MEDIUM: "warn", lo.LOW: "faint"}
RISK_COLORS = {lo.RISK_LOW: "ok", lo.RISK_REVIEW: "warn", lo.RISK_HIGH: "danger"}
KIND_LABEL = {"folder": "Folder", "file": "File", "shortcut": "Shortcut", "regkey": "Registry key",
              "regvalue": "Registry value"}


class Job:
    def __init__(self, app, deep: bool):
        self.app = app
        self.deep = deep
        self.status = "Waiting"
        self.outcome = None
        self.snapshot = None
        self.leftovers: list[lo.LeftoverItem] = []


class UninstallerPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setObjectName("Page")
        self.jobs: list[Job] = []
        self.current: Job | None = None
        self.task = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(14)
        head = PageHeader("Deep Uninstaller", "Runs each program's own uninstaller, confirms it really finished, "
                          "then finds leftovers with evidence that they belong to it. You approve every item.")
        self.picker = QComboBox()
        self.picker.setMinimumWidth(280)
        self.picker.setEditable(True)
        self.picker.lineEdit().setPlaceholderText("Add an app to the queue...")
        head.actions.addWidget(self.picker)
        add = button("Add", None, "check")
        add.clicked.connect(self._add_from_picker)
        head.actions.addWidget(add)
        lay.addWidget(head)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)

        left = Card(padding=12)
        left.lay.addWidget(label("Queue", "H3"))
        self.queue = QListWidget()
        self.queue.currentRowChanged.connect(self._select_job)
        left.lay.addWidget(self.queue, 1)
        rm = button("Remove from queue", None, "cancel")
        rm.clicked.connect(self._remove_job)
        left.lay.addWidget(rm)
        left.setMinimumWidth(240)
        split.addWidget(left)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right = QWidget()
        right_scroll.setWidget(right)
        self.rl = QVBoxLayout(right)
        self.rl.setContentsMargins(0, 0, 6, 0)
        self.rl.setSpacing(12)
        split.addWidget(right_scroll)
        split.setSizes([260, 900])

        # details
        self.details = Card()
        self.title = label("No app selected", "H2")
        self.details.lay.addWidget(self.title)
        self.meta = label("Choose apps in Installed Apps & Games, or add one above.", "Muted", wrap=True)
        self.meta.setTextFormat(Qt.RichText)
        self.details.lay.addWidget(self.meta)
        self.method_lbl = label("", "Faint", wrap=True)
        self.method_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details.lay.addWidget(self.method_lbl)
        self.details.lay.addWidget(divider())
        mode_row = QHBoxLayout()
        self.r_std = QRadioButton("Standard uninstall - run the uninstaller only")
        self.r_deep = QRadioButton("Deep uninstall - also find leftovers to review")
        grp = QButtonGroup(self)
        grp.addButton(self.r_std)
        grp.addButton(self.r_deep)
        self.r_deep.setChecked(True)
        self.r_std.toggled.connect(self._mode_changed)
        mode_row.addWidget(self.r_std)
        mode_row.addWidget(self.r_deep)
        mode_row.addStretch(1)
        self.details.lay.addLayout(mode_row)
        self.proc_lbl = label("", "Muted", wrap=True)
        self.details.lay.addWidget(self.proc_lbl)
        prow = QHBoxLayout()
        self.close_btn = button("Ask apps to close", None, "cancel")
        self.close_btn.clicked.connect(self._close_procs)
        self.kill_btn = button("End task", None, "stop", "Force-close the remaining processes (unsaved work is lost)")
        self.kill_btn.clicked.connect(self._kill_procs)
        prow.addWidget(self.close_btn)
        prow.addWidget(self.kill_btn)
        prow.addStretch(1)
        self.details.lay.addLayout(prow)
        arow = QHBoxLayout()
        arow.addStretch(1)
        self.leftover_only = button("Scan for leftovers only", None, "search",
                                    "For apps that are already gone or whose uninstaller is missing")
        self.leftover_only.clicked.connect(lambda: self._scan_leftovers(force=True))
        arow.addWidget(self.leftover_only)
        self.start_btn = button("Uninstall...", "danger", "uninstall")
        self.start_btn.clicked.connect(self._start)
        arow.addWidget(self.start_btn)
        self.details.lay.addLayout(arow)
        self.rl.addWidget(self.details)

        # status
        self.status_card = Card()
        sh = QHBoxLayout()
        sh.addWidget(label("Progress", "H3"), 1)
        self.stop_btn = button("Stop waiting", None, "stop")
        self.stop_btn.clicked.connect(lambda: self.task and self.task.stop())
        sh.addWidget(self.stop_btn)
        self.status_card.lay.addLayout(sh)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        self.status_card.lay.addWidget(self.log)
        self.rl.addWidget(self.status_card)

        # leftovers
        self.left_card = Card()
        lh = QHBoxLayout()
        lh.addWidget(label("Leftovers found", "H2"), 1)
        self.left_sum = label("", "Muted")
        lh.addWidget(self.left_sum)
        self.left_card.lay.addLayout(lh)
        self.left_card.lay.addWidget(label(
            "Only items with ownership evidence are listed. High-confidence, low-risk items are pre-selected; review "
            "the rest. Registry entries are backed up before removal and can be restored from Settings & History.",
            "Faint", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Type", "Size", "Confidence", "Risk", "Why it was found"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(260)
        hh = self.tree.header()
        hh.setStretchLastSection(True)
        for i, w in enumerate((380, 100, 80, 90, 80)):
            self.tree.setColumnWidth(i, w)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        self.tree.itemChanged.connect(self._update_left_sum)
        self.left_card.lay.addWidget(self.tree, 1)
        lr = QHBoxLayout()
        for text, fn in (("Select recommended", self._select_recommended), ("Select none", self._select_none)):
            b = button(text)
            b.clicked.connect(fn)
            lr.addWidget(b)
        ob = button("Open location", None, "open")
        ob.clicked.connect(self._open_left)
        lr.addWidget(ob)
        lr.addStretch(1)
        self.remove_btn = button("Remove selected...", "danger", "delete")
        self.remove_btn.clicked.connect(self._remove_leftovers)
        lr.addWidget(self.remove_btn)
        self.left_card.lay.addLayout(lr)
        self.rl.addWidget(self.left_card)

        nrow = QHBoxLayout()
        nrow.addStretch(1)
        self.next_btn = button("Next app in queue", "primary", "chevron")
        self.next_btn.clicked.connect(self._next)
        nrow.addWidget(self.next_btn)
        self.rl.addLayout(nrow)
        self.rl.addStretch(1)

        state.appsChanged.connect(self._fill_picker)
        self._fill_picker()
        self._refresh()

    # -- queue ------------------------------------------------------------------
    def _fill_picker(self):
        self.picker.clear()
        for a in sorted(self.state.apps or [], key=lambda a: a.name.lower()):
            self.picker.addItem(a.name, a.key)
        self.picker.setCurrentIndex(-1)

    def _add_from_picker(self):
        key = self.picker.currentData()
        if key is None:
            text = self.picker.currentText().lower()
            match = [a for a in self.state.apps or [] if a.name.lower() == text]
            app = match[0] if match else None
        else:
            app = next((a for a in self.state.apps or [] if a.key == key), None)
        if app:
            self.handle_payload({"apps": [app], "deep": self.r_deep.isChecked()})

    def handle_payload(self, payload):
        if not isinstance(payload, dict):
            return
        existing = {j.app.key for j in self.jobs}
        for a in payload.get("apps", []):
            if a.key not in existing:
                self.jobs.append(Job(a, payload.get("deep", True)))
        self._rebuild_queue()
        if self.current is None or self.current.status in ("Done", "Removed"):
            nxt = next((i for i, j in enumerate(self.jobs) if j.status == "Waiting"), None)
            if nxt is not None:
                self.queue.setCurrentRow(nxt)

    def _rebuild_queue(self):
        cur = self.queue.currentRow()
        self.queue.blockSignals(True)
        self.queue.clear()
        for j in self.jobs:
            it = QListWidgetItem(icons.icon("game" if j.app.is_game else "apps", None, 16), f"{j.app.name}\n{j.status}")
            self.queue.addItem(it)
        self.queue.blockSignals(False)
        if 0 <= cur < len(self.jobs):
            self.queue.setCurrentRow(cur)

    def _select_job(self, row: int):
        if self.task is not None:
            return
        self.current = self.jobs[row] if 0 <= row < len(self.jobs) else None
        self._refresh()

    def _remove_job(self):
        row = self.queue.currentRow()
        if self.task is None and 0 <= row < len(self.jobs):
            self.jobs.pop(row)
            self.current = None
            self._rebuild_queue()
            self._refresh()

    def _next(self):
        nxt = next((i for i, j in enumerate(self.jobs) if j.status == "Waiting"), None)
        if nxt is not None:
            self.queue.setCurrentRow(nxt)

    def _set_status(self, job: Job, status: str):
        job.status = status
        i = self.jobs.index(job) if job in self.jobs else -1
        if i >= 0 and self.queue.item(i):
            self.queue.item(i).setText(f"{job.app.name}\n{status}")

    # -- details ------------------------------------------------------------------
    def _mode_changed(self):
        if self.current:
            self.current.deep = self.r_deep.isChecked()

    def _refresh(self):
        j = self.current
        has = j is not None
        for w in (self.start_btn, self.r_std, self.r_deep, self.leftover_only):
            w.setEnabled(has and self.task is None)
        self.status_card.setVisible(has and bool(self.log.toPlainText()))
        self.left_card.setVisible(has and bool(j.leftovers))
        waiting = sum(1 for x in self.jobs if x.status == "Waiting")
        self.next_btn.setVisible(waiting > 0 and has and j.status != "Waiting")
        if not has:
            self.title.setText("No app selected")
            self.meta.setText("Choose apps in <b>Installed Apps & Games</b> (tick several for a batch), or add one above.")
            self.method_lbl.setText("")
            self.proc_lbl.setText("")
            self.close_btn.hide()
            self.kill_btn.hide()
            return
        a = j.app
        self.r_deep.setChecked(j.deep)
        self.r_std.setChecked(not j.deep)
        self.title.setText(a.name)
        size = human_size(a.size) if a.size else "unknown size"
        if a.size_source == "reported":
            size = "~" + size + " (reported)"
        parts = [f"<b>Publisher:</b> {a.publisher or '-'}", f"<b>Version:</b> {a.version or '-'}",
                 f"<b>Platform:</b> {a.platform}", f"<b>Size:</b> {size}"]
        if a.install_location:
            parts.append(f"<b>Location:</b> {a.install_location}")
        if a.install_date:
            parts.append(f"<b>Installed:</b> {a.install_date.isoformat()}")
        self.meta.setText(" &nbsp;-&nbsp; ".join(parts))
        m = methods.resolve(a)
        self.method_lbl.setText(f"Method: {m.description}\n{m.detail}")
        self.start_btn.setText(f"Uninstall {a.name}..." if j.status == "Waiting" else "Run uninstaller again...")
        self.start_btn.setEnabled(self.task is None and m.kind != "none")
        self._check_procs()
        self._fill_leftovers()

    def _check_procs(self):
        j = self.current
        procs = processes.find_app_processes(j.app.install_location) if j else []
        self._procs = procs
        if procs:
            names = ", ".join(sorted({p.name for p in procs}))
            self.proc_lbl.setText(f"Running now: {names}. Close them before uninstalling so files are not locked.")
        else:
            self.proc_lbl.setText("No running processes from this app were found." if j and j.app.install_location else "")
        self.close_btn.setVisible(bool(procs))
        self.kill_btn.setVisible(bool(procs))

    def _close_procs(self):
        procs = self._procs

        def done(remaining):
            self._check_procs()
            if remaining:
                self.proc_lbl.setText(self.proc_lbl.text() + " Some did not close; you can use End task.")

        run_task(lambda c, p: processes.request_close(procs), on_result=done)

    def _kill_procs(self):
        names = sorted({p.name for p in self._procs})
        dlg = ConfirmDialog(self, "End these tasks?", "Unsaved work in these programs will be lost.",
                            [f"End {n}" for n in names], confirm_text="End task")
        if dlg.exec():
            errs = processes.force_end(self._procs)
            if errs:
                InfoDialog(self, "Some tasks could not be ended", "", "\n".join(errs), glyph="warning").exec()
            self._check_procs()

    # -- uninstall ------------------------------------------------------------------
    def _say(self, msg: str):
        self.status_card.show()
        self.log.appendPlainText(msg)

    def _start(self):
        j = self.current
        if j is None:
            return
        a = j.app
        m = methods.resolve(a)
        if m.kind == "none":
            return
        self._check_procs()
        lines = [f"Program: {a.name} {a.version}", f"Method: {m.description}", f"Command: {m.detail}", ""]
        if self._procs:
            lines.append("WARNING: these processes are still running: " + ", ".join(sorted({p.name for p in self._procs})))
        if j.deep:
            lines.append("After it finishes, Hito scans for leftovers. Nothing extra is removed until you approve it.")
        else:
            lines.append("Standard mode: only the official uninstaller runs. No extra files are removed.")
        if self.state.settings.dry_run:
            lines.insert(0, "Note: dry-run mode does not apply to the app's own uninstaller - it will really run.\n")
        lines.append("Windows may ask for permission if the uninstaller needs administrator rights.")
        dlg = ConfirmDialog(self, f"Uninstall {a.name}?", "The program's own uninstaller will open. Follow its steps.",
                            lines, confirm_text="Uninstall", danger=True, glyph="uninstall")
        if not dlg.exec():
            return
        j.snapshot = lo.take_snapshot(a)
        self.log.clear()
        self._say(f"Starting: {m.detail}")
        self._set_status(j, "Uninstalling...")
        self.start_btn.setEnabled(False)
        self.leftover_only.setEnabled(False)
        self.stop_btn.setEnabled(True)

        def work(cancel, progress):
            return runner.run_uninstall(a, m, cancel, status=progress)

        def done(outcome):
            j.outcome = outcome
            self._say(f"Result: {outcome.message} ({outcome.elapsed:.0f} s)")
            if outcome.removed:
                self._set_status(j, "Removed")
                self.state.apps = [x for x in (self.state.apps or []) if x.key != a.key]
                self.state.appsChanged.emit()
            else:
                self._set_status(j, {"cancelled": "Cancelled", "still_installed": "Still installed"}.get(outcome.status, "Failed"))

        def fin():
            self.task = None
            self.stop_btn.setEnabled(False)
            self._refresh()
            if j.outcome and j.outcome.removed and j.deep:
                self._scan_leftovers()

        self.task = run_task(work, on_result=done, on_progress=self._say, on_done=fin, throttle=0.0,
                             on_error=lambda msg: self._say("Error: " + msg))

    # -- leftovers ------------------------------------------------------------------
    def _scan_leftovers(self, force: bool = False):
        j = self.current
        if j is None or self.task is not None:
            return
        if j.snapshot is None:
            j.snapshot = lo.take_snapshot(j.app)
        still = not (j.outcome and j.outcome.removed)
        if force and still:
            r = ConfirmDialog(self, "Scan anyway?", "This app still appears to be installed. Leftover results will "
                              "include its live files, so nothing will be pre-selected.",
                              ["Read-only scan; nothing is removed without your approval."], confirm_text="Scan",
                              danger=False, glyph="search")
            if not r.exec():
                return
        self._say("Scanning for leftovers (read-only)...")
        others = [a for a in (self.state.apps or []) if a.key != j.app.key]
        self.start_btn.setEnabled(False)

        def work(cancel, progress):
            return lo.scan_leftovers(j.snapshot, others, cancel)

        def done(res):
            items, excluded = res
            if still:
                for it in items:
                    it.selected = False
            j.leftovers = items
            self._say(f"Found {len(items)} leftover item(s)"
                      + (f"; {len(excluded)} matching item(s) were excluded because they are protected or shared." if excluded else "."))
            if not items:
                self._say("Nothing left behind that can be attributed to this app with confidence.")
            if j.status in ("Removed", "Uninstalling..."):
                self._set_status(j, "Review leftovers" if items else "Done")

        def fin():
            self.task = None
            self._refresh()

        self.task = run_task(work, on_result=done, on_done=fin, on_error=lambda m: self._say("Leftover scan error: " + m))

    def _fill_leftovers(self):
        j = self.current
        self.tree.blockSignals(True)
        self.tree.clear()
        if j:
            c = theme.current()
            for item in j.leftovers:
                row = QTreeWidgetItem([item.display, KIND_LABEL.get(item.kind, item.kind),
                                       human_size(item.size) if not item.is_registry else "", item.confidence.title(),
                                       item.risk.title(), item.evidence])
                row.setCheckState(0, Qt.Checked if item.selected else Qt.Unchecked)
                row.setData(0, Qt.UserRole, item)
                row.setToolTip(0, item.display)
                row.setToolTip(5, item.evidence)
                row.setIcon(0, icons.icon("registry" if item.is_registry else ("link" if item.kind == "shortcut" else
                                                                             "folder" if item.kind == "folder" else "file"), None, 16))
                row.setForeground(3, QBrush(QColor(c[CONF_COLORS[item.confidence]])))
                row.setForeground(4, QBrush(QColor(c[RISK_COLORS[item.risk]])))
                self.tree.addTopLevelItem(row)
        self.tree.blockSignals(False)
        self._update_left_sum()

    def _items(self, checked_only=True):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            item = row.data(0, Qt.UserRole)
            item.selected = row.checkState(0) == Qt.Checked
            if item.selected or not checked_only:
                out.append(item)
        return out

    def _update_left_sum(self, *_):
        sel = self._items()
        allv = self._items(False)
        self.left_sum.setText(f"{len(sel)} of {len(allv)} selected - {human_size(sum(i.size for i in sel))}")
        self.remove_btn.setEnabled(bool(sel) and self.task is None)

    def _select_recommended(self):
        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            it = row.data(0, Qt.UserRole)
            rec = it.confidence == lo.HIGH and it.risk == lo.RISK_LOW and (self.current.outcome and self.current.outcome.removed)
            row.setCheckState(0, Qt.Checked if rec else Qt.Unchecked)

    def _select_none(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.Unchecked)

    def _open_left(self):
        row = self.tree.currentItem()
        if row:
            it = row.data(0, Qt.UserRole)
            if not it.is_registry:
                actions.open_location(it.path)

    def _remove_leftovers(self):
        j = self.current
        sel = self._items()
        if not sel or j is None:
            return
        s = self.state.settings
        mode = PERMANENT if s.user_delete_mode == PERMANENT else RECYCLE
        lines = []
        if s.dry_run:
            lines.append("DRY RUN is on - nothing will be changed.\n")
        risky = [i for i in sel if i.risk == lo.RISK_HIGH or i.confidence == lo.LOW]
        for i in sel:
            verb = ("Back up and delete" if i.is_registry else ("Move to Recycle Bin" if mode == RECYCLE else "Delete permanently"))
            lines.append(f"{verb}: {i.display}" + (f"  ({human_size(i.size)})" if not i.is_registry else ""))
            if i in risky:
                lines.append(f"      CHECK: {i.evidence}")
        dlg = ConfirmDialog(self, f"Remove {len(sel)} leftover item(s)?",
                            "Registry entries are exported to a .reg backup first. Files go to the Recycle Bin unless "
                            "you chose permanent deletion in Settings.", lines, confirm_text="Remove", danger=True,
                            require_ack="I reviewed the low-confidence / high-risk items" if risky else None)
        if not dlg.exec():
            return
        others = [a.install_location for a in (self.state.apps or []) if a.key != j.app.key and a.install_location]
        dry = s.dry_run
        self.remove_btn.setEnabled(False)

        def work(cancel, progress):
            fs = [i for i in sel if not i.is_registry]
            reg = [i for i in sel if i.is_registry]
            d = Deleter(SafetyPolicy(LEFTOVER, other_app_roots=others), mode=mode, dry_run=dry, allow_warn=True,
                        cancel=cancel, source=f"leftovers:{j.app.name}")
            rep = d.delete([i.path for i in fs], {i.path: i.size for i in fs})
            reg_results = []
            for i in reg:
                if i.kind == "regkey":
                    ok, msg = regops.delete_key(i.path, dry_run=dry)
                else:
                    ok, msg = regops.delete_value(i.path, i.value_name, dry_run=dry)
                reg_results.append((i, ok, msg))
            return rep, reg_results

        def done(res):
            rep, reg_results = res
            lines = [rep.summary]
            ok_reg = sum(1 for _i, ok, _m in reg_results if ok)
            if reg_results:
                lines.append(f"Registry: {ok_reg} of {len(reg_results)} entries removed (backed up first).")
            details = report_details(rep)
            bad = [f"{i.display}\n    {m}" for i, ok, m in reg_results if not ok]
            if bad:
                details += "\n\nRegistry entries not removed:\n" + "\n".join(bad)
            gone = {os.path.normcase(i.path) for i in sel if not i.is_registry and not os.path.lexists(i.path)}
            gone_reg = {(i.path, i.value_name) for i, ok, _m in reg_results if ok and not dry}
            j.leftovers = [i for i in j.leftovers if os.path.normcase(i.path) not in gone and (i.path, i.value_name) not in gone_reg]
            self._set_status(j, "Done" if not j.leftovers else "Review leftovers")
            InfoDialog(self, "Leftovers removed" if not (rep.errors or bad) else "Finished with problems",
                       "<br>".join(lines), details, glyph="check").exec()
            self._refresh()

        run_task(work, on_result=done, on_error=lambda m: InfoDialog(self, "Removal failed", "", m, glyph="warning").exec())

