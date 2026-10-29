"""Settings, operation log, registry backups and scan history."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QListWidget, QMessageBox, QScrollArea, QSpinBox, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from hito import APP_NAME, __version__, history
from hito.core import winapi
from hito.core.fsutil import human_size
from hito.safety import oplog
from hito.settings import data_dir
from hito.ui import icons
from hito.ui.dialogs import ConfirmDialog, InfoDialog
from hito.ui.widgets import Card, PageHeader, button, label
from hito.uninstall import regops


class SettingsTab(QWidget):
    def __init__(self, state, on_theme):
        super().__init__()
        self.state = state
        self.on_theme = on_theme
        s = state.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 12, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setSpacing(14)
        lay.setContentsMargins(0, 0, 8, 0)

        c1 = Card()
        c1.lay.addWidget(label("Safety", "H2"))
        f = QFormLayout()
        f.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        f.setLabelAlignment(Qt.AlignLeft)
        f.setHorizontalSpacing(24)
        f.setVerticalSpacing(10)
        self.dry = QCheckBox("Dry-run mode: simulate every delete and only write it to the log")
        self.dry.setChecked(s.dry_run)
        f.addRow(self.dry)
        self.user_mode = QComboBox()
        self.user_mode.setMinimumWidth(380)
        self.user_mode.addItem("Move to the Recycle Bin (recommended)", "recycle")
        self.user_mode.addItem("Delete permanently", "permanent")
        self.user_mode.setCurrentIndex(0 if s.user_delete_mode == "recycle" else 1)
        f.addRow("Files you select yourself", self.user_mode)
        self.clean_mode = QComboBox()
        self.clean_mode.setMinimumWidth(380)
        self.clean_mode.addItem("Delete permanently (they are disposable caches)", "permanent")
        self.clean_mode.addItem("Move to the Recycle Bin", "recycle")
        self.clean_mode.setCurrentIndex(0 if s.cleanup_delete_mode == "permanent" else 1)
        f.addRow("Smart Cleanup files", self.clean_mode)
        self.age = QSpinBox()
        self.age.setMinimumWidth(140)
        self.age.setRange(0, 24 * 30)
        self.age.setSuffix(" hours")
        self.age.setValue(s.temp_min_age_hours)
        self.age.setToolTip("Temp files changed more recently than this are kept because programs may still use them.")
        f.addRow("Keep temp files newer than", self.age)
        c1.lay.addLayout(f)
        lay.addWidget(c1)

        c2 = Card()
        c2.lay.addWidget(label("Scanning", "H2"))
        f2 = QFormLayout()
        f2.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        f2.setHorizontalSpacing(24)
        f2.setVerticalSpacing(10)
        self.workers = QSpinBox()
        self.workers.setMinimumWidth(140)
        self.workers.setRange(1, 32)
        self.workers.setValue(s.scan_workers)
        f2.addRow("Parallel scan threads", self.workers)
        self.hard = QComboBox()
        self.hard.setMinimumWidth(380)
        for text, val in (("Inside the Windows folder (fast, accurate for system files)", "windows"),
                          ("Everywhere (slower)", "everywhere"), ("Off (fastest, may over-count)", "off")):
            self.hard.addItem(text, val)
        self.hard.setCurrentIndex(max(0, self.hard.findData(s.hardlinks)))
        f2.addRow("Count hard-linked files once", self.hard)
        self.index = QSpinBox()
        self.index.setMinimumWidth(140)
        self.index.setRange(0, 1024)
        self.index.setSuffix(" MB")
        self.index.setValue(s.index_min_mb)
        self.index.setToolTip("Files at least this big are kept in memory for search and 'Largest files'. "
                              "0 keeps every file (uses more memory on big drives).")
        f2.addRow("Searchable file size from", self.index)
        c2.lay.addLayout(f2)
        c2.lay.addWidget(label("Excluded folders (never scanned)", "H3"))
        self.excl = QListWidget()
        self.excl.setMaximumHeight(110)
        self.excl.addItems(s.excluded_paths)
        c2.lay.addWidget(self.excl)
        er = QHBoxLayout()
        add = button("Add folder...", None, "folder")
        add.clicked.connect(self._add_excl)
        rem = button("Remove", None, "cancel")
        rem.clicked.connect(lambda: [self.excl.takeItem(self.excl.row(i)) for i in self.excl.selectedItems()])
        er.addWidget(add)
        er.addWidget(rem)
        er.addStretch(1)
        c2.lay.addLayout(er)
        lay.addWidget(c2)

        c3 = Card()
        c3.lay.addWidget(label("Custom categories", "H2"))
        c3.lay.addWidget(label("Group your own file types (for example .psd, .blend) or folders into a category of their "
                               "own in the Storage Analyzer.", "Faint", wrap=True))
        self.cats = QTreeWidget()
        self.cats.setHeaderLabels(["Name", "Extensions", "Folders"])
        self.cats.setRootIsDecorated(False)
        self.cats.setMaximumHeight(140)
        self.cats.header().setSectionResizeMode(QHeaderView.Stretch)
        for c in s.custom_categories:
            self.cats.addTopLevelItem(QTreeWidgetItem([c.get("name", ""), " ".join(c.get("extensions", [])),
                                                       "; ".join(c.get("folders", []))]))
        c3.lay.addWidget(self.cats)
        cr = QHBoxLayout()
        ac = button("Add category...", None, "check")
        ac.clicked.connect(self._add_cat)
        rc = button("Remove", None, "cancel")
        rc.clicked.connect(lambda: [self.cats.takeTopLevelItem(self.cats.indexOfTopLevelItem(i)) for i in self.cats.selectedItems()])
        cr.addWidget(ac)
        cr.addWidget(rc)
        cr.addStretch(1)
        c3.lay.addLayout(cr)
        lay.addWidget(c3)

        c4 = Card()
        c4.lay.addWidget(label("Appearance & about", "H2"))
        f4 = QFormLayout()
        self.theme = QComboBox()
        self.theme.setMinimumWidth(380)
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        self.theme.setCurrentIndex(0 if s.theme == "dark" else 1)
        f4.addRow("Theme", self.theme)
        c4.lay.addLayout(f4)
        admin = "running as administrator" if winapi.is_admin() else "running without administrator rights"
        c4.lay.addWidget(label(f"{APP_NAME} {__version__} - {admin}. Works offline; no account or data collection.", "Muted", wrap=True))
        c4.lay.addWidget(label(f"Settings, logs and registry backups: {data_dir()}", "Faint", wrap=True))
        ob = button("Open data folder", None, "folder")
        ob.clicked.connect(lambda: winapi.open_in_explorer(data_dir()))
        c4.lay.addWidget(ob, 0, Qt.AlignLeft)
        lay.addWidget(c4)

        row = QHBoxLayout()
        row.addStretch(1)
        save = button("Save settings", "primary", "check")
        save.clicked.connect(self.save)
        row.addWidget(save)
        lay.addLayout(row)
        lay.addStretch(1)

    def _add_excl(self):
        d = QFileDialog.getExistingDirectory(self, "Exclude folder")
        if d:
            self.excl.addItem(os.path.normpath(d))

    def _add_cat(self):
        name, ok = QInputDialog.getText(self, "New category", "Category name:")
        if not ok or not name.strip():
            return
        exts, ok = QInputDialog.getText(self, "Extensions", "File extensions separated by spaces (e.g. .psd .ai .blend):")
        if not ok:
            return
        folders, ok = QInputDialog.getText(self, "Folders (optional)", "Folders separated by ; (optional):")
        self.cats.addTopLevelItem(QTreeWidgetItem([name.strip(), exts.strip(), folders.strip() if ok else ""]))

    def save(self):
        s = self.state.settings
        s.dry_run = self.dry.isChecked()
        s.user_delete_mode = self.user_mode.currentData()
        s.cleanup_delete_mode = self.clean_mode.currentData()
        s.temp_min_age_hours = self.age.value()
        s.scan_workers = self.workers.value()
        s.hardlinks = self.hard.currentData()
        s.index_min_mb = self.index.value()
        s.excluded_paths = [self.excl.item(i).text() for i in range(self.excl.count())]
        cats = []
        palette = ["#C084FC", "#F472B6", "#FB923C", "#4ADE80", "#22D3EE", "#FACC15"]
        for i in range(self.cats.topLevelItemCount()):
            it = self.cats.topLevelItem(i)
            cats.append({"name": it.text(0), "color": palette[i % len(palette)],
                         "extensions": [e for e in it.text(1).replace(",", " ").split() if e],
                         "folders": [f.strip() for f in it.text(2).split(";") if f.strip()]})
        s.custom_categories = cats
        new_theme = self.theme.currentData()
        theme_changed = new_theme != s.theme
        s.theme = new_theme
        self.state.save_settings()
        if theme_changed:
            self.on_theme(new_theme)
        InfoDialog(self, "Saved", "Settings saved. New scan options apply to the next scan.", glyph="check").exec()


class LogTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by path, operation or result")
        self.filter.textChanged.connect(self.load)
        bar.addWidget(self.filter, 1)
        rb = button("Refresh", None, "refresh")
        rb.clicked.connect(self.load)
        bar.addWidget(rb)
        ob = button("Open log file", None, "open")
        ob.clicked.connect(lambda: winapi.open_in_explorer(oplog.log_path()))
        bar.addWidget(ob)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Time", "Operation", "Result", "Target", "Details"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        for i, w in enumerate((140, 140, 90, 420)):
            self.tree.setColumnWidth(i, w)
        lay.addWidget(self.tree, 1)

    def load(self):
        q = self.filter.text().lower().strip()
        self.tree.clear()
        for e in oplog.read_recent(3000):
            extra = {k: v for k, v in e.items() if k not in ("ts", "op", "target", "result")}
            if "bytes" in extra:
                extra["bytes"] = human_size(extra["bytes"])
            details = ", ".join(f"{k}={v}" for k, v in extra.items())
            row = [datetime.fromtimestamp(e.get("ts", 0)).strftime("%Y-%m-%d %H:%M:%S"), e.get("op", ""),
                   e.get("result", ""), e.get("target", ""), details]
            if q and not any(q in str(x).lower() for x in row):
                continue
            it = QTreeWidgetItem(row)
            it.setToolTip(3, row[3])
            it.setToolTip(4, details)
            self.tree.addTopLevelItem(it)


class BackupsTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.addWidget(label("Every registry key or value removed by the Deep Uninstaller is exported here first. Restoring "
                            "re-imports the .reg file (HKLM entries need administrator rights).", "Faint", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Backup", "Created", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ob = button("Show file", None, "open")
        ob.clicked.connect(self._show)
        row.addWidget(ob)
        rb = button("Restore selected...", "primary", "restore")
        rb.clicked.connect(self._restore)
        row.addWidget(rb)
        lay.addLayout(row)

    def load(self):
        self.tree.clear()
        for path, mtime, size in regops.list_backups():
            it = QTreeWidgetItem([os.path.basename(path), datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                                  human_size(size)])
            it.setData(0, Qt.UserRole, path)
            self.tree.addTopLevelItem(it)

    def _show(self):
        it = self.tree.currentItem()
        if it:
            winapi.open_in_explorer(it.data(0, Qt.UserRole))

    def _restore(self):
        it = self.tree.currentItem()
        if not it:
            return
        path = it.data(0, Qt.UserRole)
        try:
            with open(path, encoding="utf-16", errors="replace") as fh:
                preview = fh.read(6000)
        except OSError:
            preview = path
        dlg = ConfirmDialog(self, "Restore this registry backup?", "The keys and values in this file are written back "
                            "to the registry.", preview.splitlines()[:200], confirm_text="Restore", danger=False,
                            glyph="restore")
        if dlg.exec():
            ok, msg = regops.restore_backup(path)
            InfoDialog(self, "Restored" if ok else "Restore failed", msg, glyph="check" if ok else "warning").exec()


class ScanHistoryTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["When", "Location", "Total", "Files", "Took", "Unreadable", "Top category"])
        self.tree.setRootIsDecorated(False)
        for i, w in enumerate((140, 200, 90, 90, 70, 90)):
            self.tree.setColumnWidth(i, w)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        cb = button("Clear history", None, "delete")
        cb.clicked.connect(self._clear)
        row.addWidget(cb)
        lay.addLayout(row)

    def load(self):
        self.tree.clear()
        for h in history.load():
            cats = h.get("categories", {})
            top = max(cats.items(), key=lambda kv: kv[1])[0] if cats else ""
            top_lbl = h.get("labels", {}).get(top, top)
            self.tree.addTopLevelItem(QTreeWidgetItem([
                datetime.fromtimestamp(h["ts"]).strftime("%Y-%m-%d %H:%M"), h["root"], human_size(h["total"]),
                f"{h['files']:,}", f"{h['elapsed']} s", str(h.get("errors", 0)),
                f"{top_lbl} ({human_size(cats.get(top, 0))})" if top else "",
            ]))

    def _clear(self):
        if QMessageBox.question(self, "Clear history", "Remove all saved scan summaries?") == QMessageBox.Yes:
            history.clear()
            self.load()


class SettingsPage(QWidget):
    def __init__(self, state, on_theme):
        super().__init__()
        self.setObjectName("Page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(10)
        lay.addWidget(PageHeader("Settings & History", "Safety options, the operation log, registry backups and past scans."))
        card = Card(padding=14)
        self.tabs = QTabWidget()
        self.log = LogTab()
        self.backups = BackupsTab()
        self.hist = ScanHistoryTab()
        self.tabs.addTab(SettingsTab(state, on_theme), icons.icon("settings", None, 16), "Settings")
        self.tabs.addTab(self.log, icons.icon("list", None, 16), "Operation log")
        self.tabs.addTab(self.backups, icons.icon("registry", None, 16), "Registry backups")
        self.tabs.addTab(self.hist, icons.icon("history", None, 16), "Scan history")
        self.tabs.currentChanged.connect(self._tab)
        card.lay.addWidget(self.tabs)
        lay.addWidget(card, 1)

    def _tab(self, i):
        w = self.tabs.widget(i)
        if hasattr(w, "load"):
            w.load()

    def handle_payload(self, payload):
        if payload == "log":
            self.tabs.setCurrentIndex(1)
        self._tab(self.tabs.currentIndex())

    def showEvent(self, e):
        super().showEvent(e)
        self._tab(self.tabs.currentIndex())
