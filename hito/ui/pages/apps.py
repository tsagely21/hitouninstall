"""Installed Apps & Games: discovery, real size measurement, sorting, uninstall entry point."""
from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QFileInfo, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileIconProvider, QHBoxLayout, QHeaderView, QLineEdit,
                               QTableView, QVBoxLayout, QWidget)

from hito.apps.inventory import app_icon_path, discover_all
from hito.core.fsutil import human_size, norm, tree_size
from hito.ui import actions, icons
from hito.ui.dialogs import InfoDialog
from hito.ui.widgets import PageHeader, ProgressPanel, button, label
from hito.ui.workers import run_task

COLS = ["", "Name", "Publisher", "Version", "Platform", "Size", "Installed", "Location"]
GENERIC_LOCATIONS = None


def _generic_locations() -> set[str]:
    global GENERIC_LOCATIONS
    if GENERIC_LOCATIONS is None:
        env = os.environ
        GENERIC_LOCATIONS = {norm(p) for p in (env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", ""),
                                                env.get("WINDIR", ""), env.get("ProgramData", ""), env.get("LOCALAPPDATA", ""),
                                                env.get("APPDATA", ""), os.path.expanduser("~"),
                                                env.get("CommonProgramFiles", "")) if p}
    return GENERIC_LOCATIONS


class AppModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.apps = []
        self.checked: set[str] = set()
        self._icons: dict[str, QIcon] = {}
        self._provider = QFileIconProvider()

    def set_apps(self, apps):
        self.beginResetModel()
        self.apps = apps
        self.checked &= {a.key for a in apps}
        self.endResetModel()

    def refresh_row(self, key: str):
        for i, a in enumerate(self.apps):
            if a.key == key:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLS) - 1))
                return

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.apps)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[s]
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            f |= Qt.ItemIsUserCheckable
        return f

    def _icon(self, a) -> QIcon:
        if a.key in self._icons:
            return self._icons[a.key]
        path = app_icon_path(a)
        ic = None
        if path and os.path.isfile(path):
            if path.lower().endswith((".png", ".jpg", ".jpeg", ".ico", ".bmp")):
                pm = QPixmap(path)
                if not pm.isNull():
                    ic = QIcon(pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                ic = self._provider.icon(QFileInfo(path))
                if ic.isNull():
                    ic = None
        if ic is None:
            ic = icons.icon("game" if a.is_game else ("store" if a.source == "store" else "apps"), "#A78BFA", 20)
        self._icons[a.key] = ic
        return ic

    def data(self, index, role=Qt.DisplayRole):
        a = self.apps[index.row()]
        c = index.column()
        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if a.key in self.checked else Qt.Unchecked
        if role == Qt.DecorationRole and c == 1:
            return self._icon(a)
        if role == Qt.DisplayRole:
            if c == 1:
                return a.name
            if c == 2:
                return a.publisher
            if c == 3:
                return a.version
            if c == 4:
                return a.platform + (" - game" if a.is_game and a.platform == "Windows" else "")
            if c == 5:
                if a.size_source == "measured":
                    return human_size(a.size)
                if a.size_source == "reported":
                    return "~" + human_size(a.size)
                return "-"
            if c == 6:
                return a.install_date.isoformat() if a.install_date else ""
            if c == 7:
                return a.install_location
        if role == Qt.UserRole:
            return ("", a.name.lower(), a.publisher.lower(), a.version, a.platform, a.size or 0,
                    a.install_date.isoformat() if a.install_date else "", a.install_location.lower())[c]
        if role == Qt.ToolTipRole:
            if c == 5:
                tip = {"measured": "Measured by reading the install folder.",
                       "reported": "Estimated - reported by Windows or the launcher, not measured.",
                       "unknown": "Unknown - no install folder is recorded, so the size cannot be measured."}[a.size_source]
                if a.size_note:
                    tip += f"\n{a.size_note}"
                if a.source == "store":
                    tip += "\nStore apps share framework packages that are not included."
                return tip
            if c == 1:
                return f"{a.name}\nUninstall: {a.uninstall_string or a.package_full_name or a.platform}"
        if role == Qt.TextAlignmentRole and c == 5:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 0:
            key = self.apps[index.row()].key
            if Qt.CheckState(value) == Qt.Checked:
                self.checked.add(key)
            else:
                self.checked.discard(key)
            self.dataChanged.emit(index, index)
            return True
        return False


class AppFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.kind = "all"
        self.setSortRole(Qt.UserRole)

    def set_filter(self, text, kind):
        self.beginFilterChange()
        self.text, self.kind = text.lower().strip(), kind
        self.endFilterChange()

    def filterAcceptsRow(self, row, parent):
        a = self.sourceModel().apps[row]
        if self.text and self.text not in a.name.lower() and self.text not in a.publisher.lower():
            return False
        if self.kind == "games" and not a.is_game:
            return False
        if self.kind == "apps" and (a.is_game or a.source == "store"):
            return False
        if self.kind == "store" and a.source != "store":
            return False
        return True


class AppsPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setObjectName("Page")
        self.task = None
        self.size_task = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(14)
        head = PageHeader("Installed Apps & Games",
                          "Programs from the Windows uninstall registry, the Microsoft Store, Steam and Epic. "
                          "Sizes are measured from the install folder where possible.")
        self.refresh_btn = button("Refresh", None, "refresh")
        self.refresh_btn.clicked.connect(self.load)
        head.actions.addWidget(self.refresh_btn)
        lay.addWidget(head)
        self.progress = ProgressPanel()
        self.progress.cancelRequested.connect(self._cancel)
        lay.addWidget(self.progress)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.chips = {}
        for key, text in (("all", "All"), ("apps", "Apps"), ("games", "Games"), ("store", "Microsoft Store")):
            b = button(text, "chip")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._set_kind(k))
            self.chips[key] = b
            bar.addWidget(b)
        self.chips["all"].setChecked(True)
        self.kind = "all"
        bar.addSpacing(8)
        self.count_lbl = label("", "Faint")
        bar.addWidget(self.count_lbl, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name or publisher")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self._filter)
        bar.addWidget(self.search)
        self.sort = QComboBox()
        for text, col, order in (("Largest first", 5, Qt.DescendingOrder), ("Smallest first", 5, Qt.AscendingOrder),
                                 ("Name A-Z", 1, Qt.AscendingOrder), ("Publisher", 2, Qt.AscendingOrder),
                                 ("Newest install", 6, Qt.DescendingOrder)):
            self.sort.addItem(text, (col, order))
        self.sort.currentIndexChanged.connect(self._sort)
        bar.addWidget(self.sort)
        lay.addLayout(bar)

        self.model = AppModel()
        self.proxy = AppFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setIconSize(QSize(22, 22))
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setStretchLastSection(True)
        for i, w in enumerate((34, 280, 170, 110, 120, 90, 100)):
            self.table.setColumnWidth(i, w)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.doubleClicked.connect(lambda _i: self._uninstall(deep=True))
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.sel_lbl = label("Tick apps to uninstall several in a row.", "Faint")
        foot.addWidget(self.sel_lbl, 1)
        ob = button("Open location", None, "open")
        ob.clicked.connect(self._open)
        foot.addWidget(ob)
        sb = button("Standard uninstall", None, "uninstall",
                    "Run the app's own uninstaller only. No extra files are removed.")
        sb.clicked.connect(lambda: self._uninstall(deep=False))
        foot.addWidget(sb)
        db = button("Deep uninstall", "primary", "sparkle",
                    "Run the uninstaller, then scan for leftovers you can review and remove")
        db.clicked.connect(lambda: self._uninstall(deep=True))
        foot.addWidget(db)
        lay.addLayout(foot)
        self.model.dataChanged.connect(self._update_sel)
        self.loaded = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self.loaded:
            self.loaded = True
            if self.state.apps is not None:
                self._show(self.state.apps)
                self._measure()
            else:
                self.load()

    def handle_payload(self, payload):
        if payload == "refresh":
            self.load()

    # -- loading -------------------------------------------------------------
    def load(self):
        if self.task is not None:
            return
        if self.size_task:
            self.size_task.stop()
        self.refresh_btn.setEnabled(False)
        self.progress.start("Finding installed programs...")
        self.progress.cancel_btn.setEnabled(False)

        def fin():
            self.task = None
            self.refresh_btn.setEnabled(True)
            self.progress.finish()

        self.task = run_task(lambda c, p: discover_all(), on_result=self._loaded, on_done=fin,
                             on_error=lambda m: InfoDialog(self, "Could not list apps", "", m, glyph="warning").exec())

    def _loaded(self, apps):
        from hito.apps import inventory
        self.state.apps = apps
        self._show(apps)
        if inventory.last_store_error:
            self.count_lbl.setText(self.count_lbl.text() + " - Microsoft Store apps could not be listed "
                                   "(press Refresh to retry)")
            self.count_lbl.setToolTip(inventory.last_store_error)
        self.state.appsChanged.emit()
        self._measure()

    def _show(self, apps):
        self.model.set_apps(apps)
        self._sort()
        self._filter()

    def _measure(self):
        apps = [a for a in (self.state.apps or []) if a.install_location and a.measured_size is None]
        generic = _generic_locations()
        todo = [a for a in apps if norm(a.install_location) not in generic and os.path.isdir(a.install_location)]
        if not todo:
            return
        self.progress.start("Measuring app sizes", determinate=True)
        self.progress.cancel_btn.setEnabled(True)

        def work(cancel, progress):
            for i, a in enumerate(todo):
                if cancel.is_set():
                    break
                progress((i, len(todo), a))
                size, _n = tree_size(a.install_location, cancel=cancel)
                if not cancel.is_set():
                    # 0 usually means the folder could not be read (e.g. WindowsApps): keep the estimate
                    a.measured_size = size if size > 0 else None
                    progress((i + 1, len(todo), a))
            return True

        def prog(p):
            i, n, a = p
            self.progress.update_progress(f"{a.name}", i * 100 / max(1, n))
            self.model.refresh_row(a.key)

        def fin():
            self.size_task = None
            self.progress.finish()
            self.model.layoutChanged.emit()
            self._sort()
            self._filter()
            self.state.appsChanged.emit()

        self.size_task = run_task(work, on_progress=prog, on_done=fin, throttle=0.0)

    def _cancel(self):
        if self.size_task:
            self.size_task.stop()

    # -- filters -----------------------------------------------------------
    def _set_kind(self, kind):
        self.kind = kind
        for k, b in self.chips.items():
            b.setChecked(k == kind)
        self._filter()

    def _filter(self):
        self.proxy.set_filter(self.search.text(), self.kind)
        n = self.proxy.rowCount()
        total = sum((self.model.apps[self.proxy.mapToSource(self.proxy.index(i, 0)).row()].size or 0) for i in range(n))
        self.count_lbl.setText(f"{n} shown - {human_size(total)}")

    def _sort(self):
        col, order = self.sort.currentData()
        self.table.sortByColumn(col, order)

    def _update_sel(self, *_):
        n = len(self.model.checked)
        self.sel_lbl.setText(f"{n} app(s) ticked for batch uninstall." if n else "Tick apps to uninstall several in a row.")

    # -- actions -----------------------------------------------------------
    def _current(self):
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        return self.model.apps[self.proxy.mapToSource(idx).row()]

    def _open(self):
        a = self._current()
        if a and a.install_location and os.path.isdir(a.install_location):
            actions.open_location(a.install_location)

    def _uninstall(self, deep: bool):
        apps = [a for a in self.model.apps if a.key in self.model.checked]
        if not apps:
            a = self._current()
            apps = [a] if a else []
        if not apps:
            InfoDialog(self, "Pick an app", "Select an app (or tick several) first.").exec()
            return
        self.state.navigate.emit("uninstaller", {"apps": apps, "deep": deep})
        self.model.checked.clear()
        self.model.layoutChanged.emit()
        self._update_sel()

