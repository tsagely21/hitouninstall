"""Large file scanner and three-stage duplicate finder."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QSpinBox, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from hito.core.drives import list_drives
from hito.core.fsutil import fmt_time, human_size
from hito.dupes.finder import auto_select, find_duplicates, find_large, validate_selection
from hito.ui import actions, icons
from hito.ui.dialogs import InfoDialog
from hito.ui.widgets import Card, PageHeader, ProgressPanel, button, label
from hito.ui.workers import run_task

THRESHOLDS = [("100 MB", 100), ("500 MB", 500), ("1 GB", 1024), ("5 GB", 5 * 1024), ("10 GB", 10 * 1024)]


def source_combo() -> QComboBox:
    cb = QComboBox()
    cb.setMinimumWidth(220)
    home = os.path.expanduser("~")
    cb.addItem(icons.icon("folder", None, 16), f"My files ({home})", home)
    for d in list_drives():
        cb.addItem(icons.icon("drive", None, 16), d.title, d.root)
    cb.addItem(icons.icon("folder", None, 16), "Choose a folder...", "__folder__")
    return cb


def pick_root(parent, cb: QComboBox) -> str | None:
    root = cb.currentData()
    if root == "__folder__":
        root = QFileDialog.getExistingDirectory(parent, "Choose a folder")
        return os.path.normpath(root) if root else None
    return root


class LargeTab(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.task = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(label("Look in", "Muted"))
        self.src = source_combo()
        bar.addWidget(self.src)
        bar.addWidget(label("Larger than", "Muted"))
        self.th = QComboBox()
        for text, mb in THRESHOLDS:
            self.th.addItem(text, mb)
        self.th.addItem("Custom", -1)
        self.custom = QSpinBox()
        self.custom.setRange(1, 1024 * 1024)
        self.custom.setSuffix(" MB")
        self.custom.setValue(state.settings.large_threshold_mb)
        self.custom.hide()
        self.th.currentIndexChanged.connect(lambda: self.custom.setVisible(self.th.currentData() == -1))
        i = self.th.findData(state.settings.large_threshold_mb)
        self.th.setCurrentIndex(i if i >= 0 else 2)
        bar.addWidget(self.th)
        bar.addWidget(self.custom)
        self.sys = QCheckBox("Skip Windows and program folders")
        self.sys.setChecked(True)
        bar.addWidget(self.sys)
        bar.addStretch(1)
        self.go = button("Find large files", "primary", "search")
        self.go.clicked.connect(self.start)
        bar.addWidget(self.go)
        lay.addLayout(bar)
        self.progress = ProgressPanel()
        self.progress.cancelRequested.connect(lambda: self.task and self.task.stop())
        lay.addWidget(self.progress)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Size", "Type", "Modified", "Folder"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        for i, w in enumerate((300, 90, 90, 130)):
            self.tree.setColumnWidth(i, w)
        self.tree.itemDoubleClicked.connect(lambda it, _c: actions.open_location(it.data(0, Qt.UserRole)[0]))
        lay.addWidget(self.tree, 1)
        foot = QHBoxLayout()
        self.sum = label("", "Muted")
        foot.addWidget(self.sum, 1)
        ob = button("Open location", None, "open")
        ob.clicked.connect(self._open)
        foot.addWidget(ob)
        db = button("Delete selected...", "danger", "delete")
        db.clicked.connect(self._delete)
        foot.addWidget(db)
        lay.addLayout(foot)

    def start(self):
        root = pick_root(self, self.src)
        if not root or self.task:
            return
        mb = self.th.currentData()
        mb = self.custom.value() if mb == -1 else mb
        self.state.settings.large_threshold_mb = mb
        min_size = mb * 1024 * 1024
        protect = self.sys.isChecked()
        excluded = self.state.settings.excluded_paths
        self.go.setEnabled(False)
        self.progress.start(f"Looking for files over {human_size(min_size)} in {root}")

        def work(cancel, progress):
            return find_large([root], min_size, excluded, protect, cancel, lambda st, n, p: progress((n, p)))

        def prog(p):
            self.progress.update_progress(f"{p[0]:,} files checked - {p[1]}")

        def fin():
            self.task = None
            self.go.setEnabled(True)
            self.progress.finish()

        self.task = run_task(work, on_result=self._show, on_progress=prog, on_done=fin,
                             on_error=lambda m: InfoDialog(self, "Scan failed", "", m, glyph="warning").exec())

    def _show(self, files):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        clf = self.state.classifier
        for f in files:
            ext = os.path.splitext(f.path)[1].lower().lstrip(".")
            it = SizeItem([os.path.basename(f.path), human_size(f.size), clf.label(clf.classify_path(f.path)),
                           fmt_time(f.mtime), os.path.dirname(f.path)])
            it.setData(0, Qt.UserRole, (f.path, f.size))
            it.setData(1, Qt.UserRole, f.size)
            it.setToolTip(0, f.path)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            it.setIcon(0, icons.icon("file", None, 16))
            it.setToolTip(2, ext.upper())
            self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(1, Qt.DescendingOrder)
        self.sum.setText(f"{len(files):,} files - {human_size(sum(f.size for f in files))}")

    def _selected(self):
        return [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]

    def _open(self):
        sel = self._selected()
        if sel:
            actions.open_location(sel[0][0])

    def _delete(self):
        sel = self._selected()

        def after(_rep, removed):
            gone = {os.path.normcase(p) for p in removed}
            for i in reversed(range(self.tree.topLevelItemCount())):
                if os.path.normcase(self.tree.topLevelItem(i).data(0, Qt.UserRole)[0]) in gone:
                    self.tree.takeTopLevelItem(i)

        actions.delete_user_paths(self, self.state, sel, source="large-files", on_finished=after)


class SizeItem(QTreeWidgetItem):
    """Sorts the size column numerically."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if col == 1:
            return (self.data(1, Qt.UserRole) or 0) < (other.data(1, Qt.UserRole) or 0)
        return super().__lt__(other)


class DupesTab(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.task = None
        self.groups = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(label("Look in", "Muted"))
        self.src = source_combo()
        bar.addWidget(self.src)
        bar.addWidget(label("Ignore files under", "Muted"))
        self.min = QSpinBox()
        self.min.setRange(0, 100000)
        self.min.setSuffix(" MB")
        self.min.setValue(state.settings.dup_min_mb)
        bar.addWidget(self.min)
        self.sys = QCheckBox("Protect system and app folders")
        self.sys.setChecked(state.settings.dup_protect_system)
        self.sys.setToolTip("Skips Windows, Program Files, ProgramData and AppData. Duplicates there are usually "
                            "intentional copies that programs need.")
        bar.addWidget(self.sys)
        bar.addStretch(1)
        self.go = button("Find duplicates", "primary", "copy")
        self.go.clicked.connect(self.start)
        bar.addWidget(self.go)
        lay.addLayout(bar)
        lay.addWidget(label("Files are grouped by size, compared by a quick signature, then confirmed byte-for-byte with a "
                            "full content hash. Online-only cloud files are skipped so nothing is downloaded.",
                            "Faint", wrap=True))
        self.progress = ProgressPanel()
        self.progress.cancelRequested.connect(lambda: self.task and self.task.stop())
        lay.addWidget(self.progress)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Size", "Modified", "Folder"])
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        for i, w in enumerate((340, 90, 130)):
            self.tree.setColumnWidth(i, w)
        self.tree.itemChanged.connect(self._update_sum)
        self.tree.itemDoubleClicked.connect(self._dbl)
        lay.addWidget(self.tree, 1)
        foot = QHBoxLayout()
        self.sum = label("", "Muted")
        foot.addWidget(self.sum, 1)
        foot.addWidget(label("Auto-select extras, keeping", "Faint"))
        self.keep = QComboBox()
        self.keep.addItem("the oldest copy", "oldest")
        self.keep.addItem("the newest copy", "newest")
        self.keep.addItem("the shortest path", "shortest")
        foot.addWidget(self.keep)
        ab = button("Auto-select", None, "check")
        ab.clicked.connect(self._auto)
        foot.addWidget(ab)
        cb = button("Clear", None, "cancel")
        cb.clicked.connect(self._clear)
        foot.addWidget(cb)
        db = button("Delete selected...", "danger", "delete")
        db.clicked.connect(self._delete)
        foot.addWidget(db)
        lay.addLayout(foot)

    def start(self):
        root = pick_root(self, self.src)
        if not root or self.task:
            return
        s = self.state.settings
        s.dup_min_mb = self.min.value()
        s.dup_protect_system = self.sys.isChecked()
        min_size = max(1, self.min.value() * 1024 * 1024)
        protect = self.sys.isChecked()
        self.go.setEnabled(False)
        self.progress.start(f"Finding duplicates in {root}", determinate=False)

        def work(cancel, progress):
            return find_duplicates([root], min_size, s.excluded_paths, protect, cancel,
                                   lambda st, n, p: progress((st, n, p)))

        def prog(p):
            stage, n, path = p
            pct = n if not stage.startswith("1/") else None
            self.progress.update_progress(f"{stage} - {path}", pct)

        def fin():
            self.task = None
            self.go.setEnabled(True)
            self.progress.finish()

        self.task = run_task(work, on_result=self._show, on_progress=prog, on_done=fin,
                             on_error=lambda m: InfoDialog(self, "Scan failed", "", m, glyph="warning").exec())

    def _show(self, groups):
        self.groups = groups
        self.tree.blockSignals(True)
        self.tree.clear()
        for g in groups:
            top = QTreeWidgetItem([f"{len(g.files)} identical copies - {human_size(g.wasted)} recoverable",
                                   human_size(g.size), "", ""])
            top.setIcon(0, icons.icon("copy", None, 16))
            top.setFirstColumnSpanned(False)
            f0 = top.font(0)
            f0.setBold(True)
            top.setFont(0, f0)
            for f in g.files:
                ch = QTreeWidgetItem([os.path.basename(f.path), human_size(f.size), fmt_time(f.mtime),
                                      os.path.dirname(f.path)])
                ch.setData(0, Qt.UserRole, (f.path, f.size))
                ch.setCheckState(0, Qt.Unchecked)
                ch.setToolTip(0, f.path)
                top.addChild(ch)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        self._update_sum()
        if not groups:
            self.sum.setText("No duplicates found.")

    def _children(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            yield i, top, [top.child(k) for k in range(top.childCount())]

    def _selected(self):
        out = []
        for _i, _top, kids in self._children():
            out += [k.data(0, Qt.UserRole) for k in kids if k.checkState(0) == Qt.Checked]
        return out

    def _update_sum(self, *_):
        total = sum(g.wasted for g in self.groups)
        sel = self._selected()
        self.sum.setText(f"{len(self.groups):,} groups - {human_size(total)} recoverable - "
                         f"{len(sel):,} selected ({human_size(sum(s for _p, s in sel))})")

    def _auto(self):
        keep = self.keep.currentData()
        self.tree.blockSignals(True)
        for i, _top, kids in self._children():
            remove = {f.path for f in auto_select(self.groups[i], keep)}
            for k in kids:
                k.setCheckState(0, Qt.Checked if k.data(0, Qt.UserRole)[0] in remove else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_sum()

    def _clear(self):
        self.tree.blockSignals(True)
        for _i, _top, kids in self._children():
            for k in kids:
                k.setCheckState(0, Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_sum()

    def _dbl(self, item, _col):
        d = item.data(0, Qt.UserRole)
        if d:
            actions.open_location(d[0])

    def _delete(self):
        sel = self._selected()
        if not sel:
            return
        bad = validate_selection(self.groups, {p for p, _s in sel})
        if bad:
            InfoDialog(self, "Keep at least one copy",
                       f"{len(bad)} group(s) have every copy selected. Untick at least one file in each group.",
                       "\n".join(g.files[0].path for g in bad[:50]), glyph="shield").exec()
            return

        def after(_rep, removed):
            gone = {os.path.normcase(p) for p in removed}
            for g in self.groups:
                g.files = [f for f in g.files if os.path.normcase(f.path) not in gone]
            self._show([g for g in self.groups if len(g.files) > 1])

        actions.delete_user_paths(self, self.state, sel, source="duplicates", on_finished=after)


class LargeDupesPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(10)
        lay.addWidget(PageHeader("Large & Duplicate Files", "Find big files and exact copies. You decide what to keep; "
                                 "at least one copy of every duplicate always stays."))
        card = Card(padding=14)
        self.tabs = QTabWidget()
        self.large = LargeTab(state)
        self.dupes = DupesTab(state)
        self.tabs.addTab(self.large, icons.icon("large", None, 16), "Large files")
        self.tabs.addTab(self.dupes, icons.icon("copy", None, 16), "Duplicates")
        card.lay.addWidget(self.tabs)
        lay.addWidget(card, 1)

    def handle_payload(self, payload):
        if payload == "dupes":
            self.tabs.setCurrentIndex(1)
        elif payload == "large":
            self.tabs.setCurrentIndex(0)
