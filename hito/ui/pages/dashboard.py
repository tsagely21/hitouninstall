"""Dashboard: drives, last scan breakdown, largest folders/apps, recoverable space, quick actions."""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QBoxLayout, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget)

from hito import history
from hito.cleanup.catalog import SAFE, build_catalog
from hito.cleanup.engine import scan_catalog
from hito.core import winapi
from hito.core.drives import list_drives
from hito.core.fsutil import human_size
from hito.ui import actions
from hito.ui.widgets import (Card, DonutChart, DriveCard, Legend, PageHeader, UsageBar, badge, button, divider,
                             glyph_label, label)
from hito.ui.workers import run_task


class ResponsiveRow(QWidget):
    """Lays children side by side when wide, stacked when narrow."""

    def __init__(self, breakpoint: int = 860, stretches: list[int] | None = None):
        super().__init__()
        self.bp = breakpoint
        self.box = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(16)
        self.stretches = stretches or []

    def add(self, w, stretch: int = 1):
        self.box.addWidget(w, stretch)

    def resizeEvent(self, e):
        d = QBoxLayout.LeftToRight if self.width() >= self.bp else QBoxLayout.TopToBottom
        if self.box.direction() != d:
            self.box.setDirection(d)
        super().resizeEvent(e)


def ago(ts: float) -> str:
    s = time.time() - ts
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return datetime.fromtimestamp(ts).strftime("%b %d, %Y")


class DashboardPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 24, 28, 28)
        lay.setSpacing(18)

        hour = datetime.now().hour
        greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        head = PageHeader("Dashboard", f"{greet}. Here is how your storage looks right now.")
        if winapi.is_admin():
            head.actions.addWidget(badge("Administrator", "info"))
        else:
            b = button("Restart as administrator", None, "admin",
                       "Needed only for system locations such as C:\\Windows\\Temp. Windows will ask first.")
            b.clicked.connect(lambda: actions.restart_as_admin(self))
            head.actions.addWidget(b)
        rb = button("Refresh", None, "refresh")
        rb.clicked.connect(self.refresh_drives)
        head.actions.addWidget(rb)
        lay.addWidget(head)

        # hero
        self.hero = Card(padding=22, spacing=6)
        self.hero.setObjectName("HeroCard")
        hl = QHBoxLayout()
        hl.setSpacing(24)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("Safely recoverable right now", "H3"))
        self.hero_value = label("Checking...", "Big")
        self.hero_value.setStyleSheet("font-size: 30pt; font-weight: 800;")
        col.addWidget(self.hero_value)
        self.hero_sub = label("Temporary files and caches marked Safe in Smart Cleanup", None, wrap=True)
        col.addWidget(self.hero_sub)
        hl.addLayout(col, 2)
        col2 = QVBoxLayout()
        col2.setSpacing(8)
        col2.addStretch(1)
        clean = button("Review & clean", None, "broom")
        clean.setStyleSheet("QPushButton { background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.35);"
                            " color: white; font-weight: 600; padding: 9px 18px; }"
                            "QPushButton:hover { background: rgba(255,255,255,0.28); }")
        clean.clicked.connect(lambda: state.navigate.emit("cleanup", None))
        col2.addWidget(clean, 0, Qt.AlignRight)
        self.hero_last = label("", None)
        self.hero_last.setAlignment(Qt.AlignRight)
        col2.addWidget(self.hero_last)
        hl.addLayout(col2, 1)
        self.hero.lay.addLayout(hl)
        lay.addWidget(self.hero)

        lay.addWidget(label("Drives", "H2"))
        self.drive_grid = QGridLayout()
        self.drive_grid.setSpacing(16)
        lay.addLayout(self.drive_grid)

        # breakdown + largest folders
        row = ResponsiveRow()
        self.cat_card = Card()
        top = QHBoxLayout()
        self.cat_title = label("Storage by category", "H2")
        top.addWidget(self.cat_title, 1)
        self.cat_when = label("", "Faint")
        top.addWidget(self.cat_when)
        self.cat_card.lay.addLayout(top)
        inner = QHBoxLayout()
        self.donut = DonutChart()
        self.donut.setMinimumSize(230, 230)
        self.donut.segmentClicked.connect(self._open_category)
        inner.addWidget(self.donut, 1)
        self.legend = Legend(max_rows=10)
        self.legend.itemClicked.connect(self._open_category)
        inner.addWidget(self.legend, 1)
        self.cat_card.lay.addLayout(inner)
        self.cat_empty = QWidget()
        el = QVBoxLayout(self.cat_empty)
        el.addWidget(label("No drive scanned yet. A scan reads every folder (nothing is changed) to show what uses space.",
                           "Muted", wrap=True))
        sb = button("Scan drive C:", "primary", "pie")
        sb.clicked.connect(lambda: state.navigate.emit("analyzer", "C:\\"))
        el.addWidget(sb, 0, Qt.AlignLeft)
        self.cat_card.lay.addWidget(self.cat_empty)
        row.add(self.cat_card, 3)

        self.folders_card = Card()
        self.folders_card.lay.addWidget(label("Largest folders", "H2"))
        self.folders_box = QVBoxLayout()
        self.folders_box.setSpacing(8)
        self.folders_card.lay.addLayout(self.folders_box)
        self.folders_card.lay.addStretch(1)
        row.add(self.folders_card, 2)
        lay.addWidget(row)

        row2 = ResponsiveRow()
        self.apps_card = Card()
        h = QHBoxLayout()
        h.addWidget(label("Largest apps & games", "H2"), 1)
        ab = button("All apps", "ghost", "apps")
        ab.clicked.connect(lambda: state.navigate.emit("apps", None))
        h.addWidget(ab)
        self.apps_card.lay.addLayout(h)
        self.apps_box = QVBoxLayout()
        self.apps_box.setSpacing(8)
        self.apps_card.lay.addLayout(self.apps_box)
        self.apps_card.lay.addStretch(1)
        row2.add(self.apps_card, 3)

        qa = Card()
        qa.lay.addWidget(label("Quick actions", "H2"))
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (text, ic, key, payload) in enumerate([
            ("Analyze a drive", "pie", "analyzer", None), ("Smart Cleanup", "broom", "cleanup", "scan"),
            ("Uninstall apps", "uninstall", "apps", None), ("Find duplicates", "copy", "large", "dupes"),
            ("Large files", "large", "large", "large"), ("History & log", "history", "settings", "log"),
        ]):
            b = button(text, None, ic)
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _=False, k=key, p=payload: state.navigate.emit(k, p))
            grid.addWidget(b, i // 2, i % 2)
        qa.lay.addLayout(grid)
        qa.lay.addWidget(divider())
        qa.lay.addWidget(label("Recent scans", "H3"))
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(4)
        qa.lay.addLayout(self.recent_box)
        qa.lay.addStretch(1)
        row2.add(qa, 2)
        lay.addWidget(row2)
        lay.addStretch(1)

        state.scanFinished.connect(lambda _r: self.refresh_scan())
        state.scanChanged.connect(self.refresh_scan)
        state.appsChanged.connect(self.refresh_apps)
        state.cleanupScanned.connect(self.refresh_recoverable)
        self.refresh_drives()
        self.refresh_scan()
        self.refresh_apps()
        self._estimate_recoverable()
        self._discover_apps()

    # -- drives -------------------------------------------------------------
    def refresh_drives(self):
        while self.drive_grid.count():
            it = self.drive_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        drives = list_drives()
        cols = 3
        for c in range(cols):
            self.drive_grid.setColumnStretch(c, 1)
        for i, d in enumerate(drives):
            card = DriveCard(d)
            card.scanRequested.connect(lambda root: self.state.navigate.emit("analyzer", root))
            self.drive_grid.addWidget(card, i // cols, i % cols)
        if not drives:
            self.drive_grid.addWidget(label("No drives could be read.", "Muted"), 0, 0)

    # -- recoverable --------------------------------------------------------
    def _estimate_recoverable(self):
        if self.state.cleanup_scans is not None:
            self.refresh_recoverable()
            return

        def work(cancel, progress):
            return scan_catalog(build_catalog(self.state.settings.temp_min_age_hours), cancel)

        def done(res):
            self.state.cleanup_scans = res
            self.state.cleanupScanned.emit()

        run_task(work, on_result=done, on_error=lambda m: self.hero_value.setText("-"))

    def refresh_recoverable(self):
        scans = self.state.cleanup_scans or []
        safe = sum(s.bytes or 0 for s in scans if s.target.risk == SAFE and s.target.kind == "files")
        review = sum(s.bytes or 0 for s in scans if s.target.risk != SAFE and s.target.kind in ("files", "recycle_bin"))
        self.hero_value.setText(human_size(safe))
        self.hero_sub.setText(f"Temporary files and caches marked Safe. Another {human_size(review)} needs your review "
                              "(Recycle Bin, developer caches, logs).")

    # -- scan breakdown -----------------------------------------------------
    def _summary(self):
        clf = self.state.classifier
        r = self.state.scan
        if r is not None:
            segs = [(k, clf.label(k), v, clf.color(k)) for k, v in sorted(r.categories.items(), key=lambda kv: -kv[1])]
            return segs, r.root_path, r.total_size, time.time(), [(d.path, d.size) for d in r.largest_dirs(limit=7)], True
        hist = history.load()
        if hist:
            h = hist[0]
            segs = [(k, h["labels"].get(k, k), v, h["colors"].get(k, "#94A3B8")) for k, v in h["categories"].items()]
            return segs, h["root"], h["total"], h["ts"], h.get("top_dirs", [])[:7], False
        return None

    def refresh_scan(self):
        s = self._summary()
        has = s is not None
        self.donut.setVisible(has)
        self.legend.setVisible(has)
        self.cat_empty.setVisible(not has)
        while self.folders_box.count():
            it = self.folders_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for h in history.load()[:4]:
            self.recent_box.addWidget(label(f"{h['root']}  -  {human_size(h['total'])}  -  {ago(h['ts'])}", "Muted"))
        if not history.load():
            self.recent_box.addWidget(label("No scans yet", "Faint"))
        if not has:
            self.cat_when.setText("")
            self.hero_last.setText("")
            self.folders_box.addWidget(label("Scan a drive to see its largest folders.", "Muted", wrap=True))
            return
        segs, root, total, ts, top, live = s
        self.cat_title.setText(f"Storage by category - {root}")
        self.cat_when.setText(("Live result" if live else f"Saved scan, {ago(ts)}"))
        self.hero_last.setText(f"Last scan: {root} {ago(ts)}")
        self.donut.set_data(segs, human_size(total), "scanned")
        self.legend.set_data(segs, total)
        biggest = top[0][1] if top else 1
        for path, size in top:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(3)
            hl = QHBoxLayout()
            name = label(path)
            name.setToolTip(path)
            name.setMinimumWidth(80)
            hl.addWidget(name, 1)
            hl.addWidget(label(human_size(size), "H3"))
            v.addLayout(hl)
            v.addWidget(UsageBar(size / biggest if biggest else 0, 6, alert=False))
            self.folders_box.addWidget(w)

    def _open_category(self, key: str):
        if self.state.scan is not None:
            self.state.navigate.emit("analyzer", {"category": key})

    # -- apps ---------------------------------------------------------------
    def _discover_apps(self):
        if self.state.apps is not None:
            return
        from hito.apps.inventory import discover_all

        def done(apps):
            if self.state.apps is None:
                self.state.apps = apps
                self.state.appsChanged.emit()

        run_task(lambda c, p: discover_all(), on_result=done)

    def refresh_apps(self):
        while self.apps_box.count():
            it = self.apps_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        apps = self.state.apps
        if not apps:
            self.apps_box.addWidget(label("Open Installed Apps & Games to measure what each program uses.", "Muted", wrap=True))
            return
        top = sorted((a for a in apps if a.size), key=lambda a: a.size, reverse=True)[:7]
        biggest = top[0].size if top else 1
        for a in top:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(3)
            hl = QHBoxLayout()
            hl.addWidget(glyph_label("game" if a.is_game else "apps", 14))
            hl.addWidget(label(a.name), 1)
            hl.addWidget(label(("" if a.size_source == "measured" else "~") + human_size(a.size), "H3"))
            v.addLayout(hl)
            v.addWidget(UsageBar(a.size / biggest, 6, alert=False))
            self.apps_box.addWidget(w)
