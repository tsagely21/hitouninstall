"""Storage Analyzer: full-drive scan, treemap, category donut and file explorer."""
from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit, QMenu,
                               QPushButton, QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget)

from hito import history
from hito.core.drives import list_drives
from hito.core.fsutil import fmt_time, human_size
from hito.core.scanner import DirNode, FileRec, ScanOptions, StorageScanner, list_folder_files
from hito.ui import actions, icons
from hito.ui.dialogs import InfoDialog
from hito.ui.widgets import (Card, DonutChart, Legend, PageHeader, ProgressPanel, Treemap, TreemapItem, badge, button,
                             label)
from hito.ui.workers import run_task

DIR_PALETTE = ["#7C3AED", "#6366F1", "#8B5CF6", "#4F46E5", "#A855F7", "#5B21B6", "#7E22CE", "#3B82F6", "#9333EA"]
COLS = ["Name", "Size", "Type", "Modified", "Category", "Location"]


class FileModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[tuple] = []  # (name, size, type, mtime, cat_label, location, is_dir, path, cat_id)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return r[0]
            if col == 1:
                return human_size(r[1])
            if col == 2:
                return r[2]
            if col == 3:
                return fmt_time(r[3])
            if col == 4:
                return r[4]
            if col == 5:
                return r[5]
        elif role == Qt.UserRole:
            return (r[0].lower(), r[1], r[2], r[3] or 0, r[4], r[5].lower())[col]
        elif role == Qt.DecorationRole and col == 0:
            return icons.icon("folder" if r[6] else "file", "#A78BFA" if r[6] else None, 16)
        elif role == Qt.ToolTipRole:
            return r[7]
        elif role == Qt.TextAlignmentRole and col == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class FileFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.cat = ""
        self.setSortRole(Qt.UserRole)

    def set_filters(self, text: str, cat: str):
        self.beginFilterChange()
        self.text = text.lower().strip()
        self.cat = cat
        self.endFilterChange()

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        if self.text and self.text not in r[0].lower():
            return False
        if self.cat and r[8] != self.cat:
            return False
        return True


class AnalyzerPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setObjectName("Page")
        self.task = None
        self.node: DirNode | None = None
        self.mode = "folder"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(14)

        head = PageHeader("Storage Analyzer", "Read-only scan of every folder on a drive. Nothing is changed until you delete something yourself.")
        self.source = QComboBox()
        self.source.setMinimumWidth(240)
        self._fill_sources()
        head.actions.addWidget(self.source)
        self.scan_btn = button("Scan", "primary", "pie")
        self.scan_btn.clicked.connect(self.start_scan)
        head.actions.addWidget(self.scan_btn)
        lay.addWidget(head)

        self.progress = ProgressPanel()
        self.progress.cancelRequested.connect(self.cancel_scan)
        lay.addWidget(self.progress)

        self.summary = QHBoxLayout()
        self.summary.setSpacing(8)
        lay.addLayout(self.summary)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)

        viz = Card(padding=12, spacing=8)
        self.tabs = QTabWidget()
        tm = QWidget()
        tml = QVBoxLayout(tm)
        tml.setContentsMargins(0, 6, 0, 0)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        tml.addLayout(self.crumbs)
        self.treemap = Treemap()
        self.treemap.itemActivated.connect(self._treemap_click)
        self.treemap.contextRequested.connect(self._treemap_menu)
        tml.addWidget(self.treemap, 1)
        self.tabs.addTab(tm, icons.icon("grid", None, 16), "Treemap")
        cat = QWidget()
        cl = QHBoxLayout(cat)
        self.donut = DonutChart()
        self.donut.segmentClicked.connect(self._category_clicked)
        cl.addWidget(self.donut, 1)
        self.legend = Legend(max_rows=16)
        self.legend.itemClicked.connect(self._category_clicked)
        cl.addWidget(self.legend, 1)
        self.tabs.addTab(cat, icons.icon("pie", None, 16), "Categories")
        viz.lay.addWidget(self.tabs)
        split.addWidget(viz)

        exp = Card(padding=12, spacing=8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.up_btn = button("", None, "up", "Up one folder")
        self.up_btn.clicked.connect(self.go_up)
        bar.addWidget(self.up_btn)
        self.mode_folder = button("This folder", "chip")
        self.mode_folder.setCheckable(True)
        self.mode_folder.setChecked(True)
        self.mode_files = button("Largest files", "chip")
        self.mode_files.setCheckable(True)
        self.mode_folder.clicked.connect(lambda: self.set_mode("folder"))
        self.mode_files.clicked.connect(lambda: self.set_mode("files"))
        bar.addWidget(self.mode_folder)
        bar.addWidget(self.mode_files)
        self.where = label("", "Faint")
        bar.addWidget(self.where, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search file names")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search)
        self.cat_filter = QComboBox()
        self.cat_filter.setMinimumWidth(150)
        self.cat_filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.cat_filter)
        self.open_btn = button("Open", None, "open", "Show the selected item in File Explorer")
        self.open_btn.clicked.connect(self._open_selected)
        bar.addWidget(self.open_btn)
        self.del_btn = button("Delete...", "danger", "delete", "Delete the selected items (Recycle Bin by default)")
        self.del_btn.clicked.connect(self._delete_selected)
        bar.addWidget(self.del_btn)
        exp.lay.addLayout(bar)

        self.model = FileModel()
        self.proxy = FileFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for i, w in enumerate((300, 90, 110, 130, 130)):
            self.table.setColumnWidth(i, w)
        self.table.doubleClicked.connect(self._row_activated)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        exp.lay.addWidget(self.table, 1)
        split.addWidget(exp)
        split.setSizes([420, 380])

        self._set_enabled(False)
        state.scanChanged.connect(self._refresh_after_change)
        state.settingsChanged.connect(self._fill_cat_filter)

    # -- sources / scanning --------------------------------------------------
    def _fill_sources(self):
        self.source.clear()
        for d in list_drives():
            self.source.addItem(icons.icon("drive", None, 16), f"{d.title}  -  {human_size(d.used)} used", d.root)
        self.source.addItem(icons.icon("folder", None, 16), "Choose a folder...", "__folder__")

    def handle_payload(self, payload):
        if isinstance(payload, str):
            idx = next((i for i in range(self.source.count())
                        if (self.source.itemData(i) or "").lower() == payload.lower()), -1)
            if idx >= 0:
                self.source.setCurrentIndex(idx)
            if self.task is None and (self.state.scan is None or self.state.scan.root_path.lower() != payload.lower()):
                self.start_scan(payload)
        elif isinstance(payload, dict) and payload.get("category") and self.state.scan is not None:
            self.set_mode("files")
            idx = self.cat_filter.findData(payload["category"])
            if idx >= 0:
                self.cat_filter.setCurrentIndex(idx)

    def start_scan(self, root: str | None = None):
        if self.task is not None:
            return
        if not root:
            root = self.source.currentData()
        if root == "__folder__":
            root = QFileDialog.getExistingDirectory(self, "Choose a folder to analyze")
            if not root:
                return
            root = os.path.normpath(root)
        s = self.state.settings
        opts = ScanOptions(workers=s.scan_workers, index_min_size=s.index_min_mb * 1024 * 1024,
                           hardlinks=s.hardlinks, excluded=s.excluded_paths)
        clf = self.state.classifier
        self.scan_btn.setEnabled(False)
        self.progress.start(f"Scanning {root}")

        def work(cancel, progress):
            return StorageScanner(root, clf, opts, cancel, progress).run()

        def prog(p):
            self.progress.update_progress(f"{p['files']:,} files  -  {human_size(p['bytes'])}  -  {p['current']}")

        self.task = run_task(work, on_result=self._scan_done, on_progress=prog, on_error=self._scan_error,
                             on_done=self._scan_finished, throttle=0.12)

    def cancel_scan(self):
        if self.task:
            self.task.stop()
            self.progress.update_progress("Cancelling...")

    def _scan_finished(self):
        self.task = None
        self.scan_btn.setEnabled(True)
        self.progress.finish()

    def _scan_error(self, msg):
        InfoDialog(self, "Scan failed", "The scan stopped because of an error.", msg, glyph="warning").exec()

    def _scan_done(self, result):
        self.state.set_scan(result)
        try:
            history.add_scan(result, self.state.classifier)
        except Exception:  # noqa: BLE001
            pass
        self._fill_cat_filter()
        self.node = result.root
        self._set_enabled(True)
        self._show_summary()
        self.refresh_view()
        cats = sorted(result.categories.items(), key=lambda kv: -kv[1])
        clf = self.state.classifier
        segs = [(k, clf.label(k), v, clf.color(k)) for k, v in cats]
        self.donut.set_data(segs, human_size(result.total_size), f"{result.total_files:,} files")
        self.legend.set_data(segs, result.total_size)

    def _show_summary(self):
        while self.summary.count():
            it = self.summary.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        r = self.state.scan
        if r is None:
            return
        self.summary.addWidget(badge(f"{human_size(r.total_size)} in {r.root_path}", "info"))
        self.summary.addWidget(badge(f"{r.total_files:,} files - {r.total_dirs:,} folders", "muted"))
        self.summary.addWidget(badge(f"{r.elapsed:.1f} s", "muted"))
        if r.cancelled:
            self.summary.addWidget(badge("Cancelled - partial result", "review"))
        if r.cloud_only_bytes:
            b = badge(f"{human_size(r.cloud_only_bytes)} online-only (not on disk)", "muted")
            b.setToolTip("OneDrive/cloud placeholders. They take no local space and are not counted.")
            self.summary.addWidget(b)
        if r.hardlink_bytes:
            b = badge(f"{human_size(r.hardlink_bytes)} hard links counted once", "muted")
            b.setToolTip("Windows stores many system files once with several names. Each is counted only once.")
            self.summary.addWidget(b)
        if r.error_count:
            eb = QPushButton(f"{r.error_count:,} unreadable")
            eb.setProperty("kind", "ghost")
            eb.setToolTip("Folders Windows would not let Hito read (often system folders). Click for the list.")
            eb.clicked.connect(self._show_errors)
            self.summary.addWidget(eb)
        self.summary.addStretch(1)

    def _show_errors(self):
        r = self.state.scan
        text = "\n".join(f"{p}\n    {m}" for p, m in r.errors)
        if r.error_count > len(r.errors):
            text += f"\n... and {r.error_count - len(r.errors):,} more"
        InfoDialog(self, "Unreadable locations", "These were skipped. Restarting as administrator lets Hito read most "
                   "system folders.", text).exec()

    def _fill_cat_filter(self):
        cur = self.cat_filter.currentData()
        self.cat_filter.blockSignals(True)
        self.cat_filter.clear()
        self.cat_filter.addItem("All categories", "")
        for cid, c in self.state.classifier.categories.items():
            self.cat_filter.addItem(c.label, cid)
        i = self.cat_filter.findData(cur)
        self.cat_filter.setCurrentIndex(max(0, i))
        self.cat_filter.blockSignals(False)

    def _set_enabled(self, on: bool):
        for w in (self.up_btn, self.mode_folder, self.mode_files, self.search, self.cat_filter, self.open_btn, self.del_btn):
            w.setEnabled(on)

    # -- view ---------------------------------------------------------------
    def set_mode(self, mode: str):
        self.mode = mode
        self.mode_folder.setChecked(mode == "folder")
        self.mode_files.setChecked(mode == "files")
        self.refresh_table()

    def refresh_view(self):
        self._crumbs()
        self._treemap()
        self.refresh_table()

    def _refresh_after_change(self):
        if self.state.scan is None:
            return
        if self.node is not None and not self._attached(self.node):
            self.node = self.state.scan.root
        self._show_summary()
        self.refresh_view()

    def _attached(self, node: DirNode) -> bool:
        while node.parent is not None:
            if node not in node.parent.children:
                return False
            node = node.parent
        return node is self.state.scan.root

    def _crumbs(self):
        while self.crumbs.count():
            it = self.crumbs.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        chain = []
        n = self.node
        while n is not None:
            chain.append(n)
            n = n.parent
        chain.reverse()
        for i, n in enumerate(chain):
            b = QPushButton(n.name.rstrip("\\") or n.name)
            b.setProperty("kind", "ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, nn=n: self.open_node(nn))
            self.crumbs.addWidget(b)
            if i < len(chain) - 1:
                self.crumbs.addWidget(label("›", "Faint"))
        self.crumbs.addStretch(1)
        if self.node is not None:
            self.crumbs.addWidget(label(f"{human_size(self.node.size)} - {self.node.files:,} files", "Muted"))

    def _dir_color(self, node: DirNode, i: int) -> str:
        cat = self.state.classifier.context_category(node.ctx)
        if cat:
            return self.state.classifier.color(cat)
        return DIR_PALETTE[i % len(DIR_PALETTE)]

    def _items_for(self, node: DirNode, limit: int = 60) -> list[TreemapItem]:
        clf = self.state.classifier
        items = []
        for i, c in enumerate(node.children[:limit]):
            items.append(TreemapItem(c.name, c.size, self._dir_color(c, i), c, True, c.path))
        rest = node.children[limit:]
        if rest:
            items.append(TreemapItem(f"{len(rest)} more folders", sum(c.size for c in rest), "#475569", None, False))
        files = sorted(self.state.files_by_dir.get(id(node), []), key=lambda f: f.size, reverse=True)
        shown = 0
        for f in files[:limit]:
            items.append(TreemapItem(f.name, f.size, clf.color(f.cat), f, False, f.path))
            shown += f.size
        small = node.own_size - shown
        if small > 0:
            items.append(TreemapItem("Smaller files", small, "#64748B", None, False))
        return items

    def _treemap(self):
        if self.node is None:
            self.treemap.set_items([])
            return
        self.treemap.set_items(self._items_for(self.node),
                               child_provider=lambda it: self._items_for(it.payload, 24) if isinstance(it.payload, DirNode) else [])

    def refresh_table(self):
        if self.state.scan is None:
            self.model.set_rows([])
            return
        clf = self.state.classifier
        rows = []
        if self.mode == "folder" and self.node is not None:
            path = self.node.path
            self.where.setText(path)
            for c in self.node.children:
                cat = clf.context_category(c.ctx) or ""
                rows.append((c.name, c.size, "Folder", None, clf.label(cat) if cat else "", path, True, c.path, cat))
            for name, size, mtime, _attrs in list_folder_files(path):
                cat = clf.file_category(self.node.ctx, name, in_drive_root=self.node.parent is None)
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                rows.append((name, size, f"{ext.upper()} file" if ext else "File", mtime, clf.label(cat), path, False,
                             os.path.join(path, name), cat))
        else:
            self.where.setText(f"Files of {self.state.settings.index_min_mb} MB or more in {self.state.scan.root_path}")
            for f in self.state.scan.files:
                ext = os.path.splitext(f.name)[1].lower().lstrip(".")
                loc = f.parent.path
                rows.append((f.name, f.size, f"{ext.upper()} file" if ext else "File", f.mtime, clf.label(f.cat), loc,
                             False, os.path.join(loc, f.name), f.cat))
        self.model.set_rows(rows)
        self.table.sortByColumn(1, Qt.DescendingOrder)
        self._apply_filter()

    def _apply_filter(self):
        self.proxy.set_filters(self.search.text(), self.cat_filter.currentData() or "")

    # -- navigation ---------------------------------------------------------
    def open_node(self, node: DirNode):
        self.node = node
        if self.mode != "folder":
            self.mode = "folder"
            self.mode_folder.setChecked(True)
            self.mode_files.setChecked(False)
        self.refresh_view()

    def go_up(self):
        if self.node is not None and self.node.parent is not None:
            self.open_node(self.node.parent)

    def _treemap_click(self, payload):
        if isinstance(payload, DirNode):
            self.open_node(payload)
        elif isinstance(payload, FileRec):
            self.open_node(payload.parent)
            self.search.setText(payload.name)

    def _category_clicked(self, key: str):
        self.set_mode("files")
        i = self.cat_filter.findData(key or "")
        self.cat_filter.setCurrentIndex(max(0, i))

    def _row_activated(self, index):
        r = self.model.rows[self.proxy.mapToSource(index).row()]
        if r[6]:
            node = self.state.scan.find_node(r[7])
            if node:
                self.open_node(node)
        else:
            actions.open_location(r[7])

    def _selected(self) -> list[tuple[str, int]]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [(self.model.rows[r][7], self.model.rows[r][1]) for r in sorted(rows)]

    def _open_selected(self):
        sel = self._selected()
        if sel:
            actions.open_location(sel[0][0])
        elif self.node is not None:
            actions.open_location(self.node.path)

    def _delete_selected(self):
        sel = self._selected()
        if sel:
            actions.delete_user_paths(self, self.state, sel, source="analyzer")

    def _menu(self, paths: list[tuple[str, int]], pos, is_dir: bool = False):
        m = QMenu(self)
        if not paths:
            return
        first = paths[0][0]
        a = QAction(icons.icon("open"), "Show in File Explorer", m)
        a.triggered.connect(lambda: actions.open_location(first))
        m.addAction(a)
        c = QAction(icons.icon("copy"), "Copy path", m)
        c.triggered.connect(lambda: QGuiApplication.clipboard().setText("\n".join(p for p, _ in paths)))
        m.addAction(c)
        if is_dir:
            s = QAction(icons.icon("pie"), "Scan only this folder", m)
            s.triggered.connect(lambda: self.start_scan(first))
            m.addAction(s)
        m.addSeparator()
        d = QAction(icons.icon("delete", theme_danger()), f"Delete {len(paths)} item(s)...", m)
        d.triggered.connect(lambda: actions.delete_user_paths(self, self.state, paths, source="analyzer"))
        m.addAction(d)
        m.exec(pos)

    def _table_menu(self, pos):
        sel = self._selected()
        if not sel:
            return
        idx = self.table.indexAt(pos)
        is_dir = idx.isValid() and self.model.rows[self.proxy.mapToSource(idx).row()][6]
        self._menu(sel, self.table.viewport().mapToGlobal(pos), is_dir)

    def _treemap_menu(self, payload, gpos):
        if isinstance(payload, DirNode):
            self._menu([(payload.path, payload.size)], gpos, True)
        elif isinstance(payload, FileRec):
            self._menu([(payload.path, payload.size)], gpos)


def theme_danger():
    from hito.ui import theme
    return theme.current()["danger"]
