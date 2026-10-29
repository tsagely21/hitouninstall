"""Main window: sidebar navigation and page stack."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from hito import APP_NAME, __version__
from hito.core import winapi
from hito.core.drives import list_drives
from hito.core.fsutil import human_size
from hito.ui import icons
from hito.ui.pages.analyzer import AnalyzerPage
from hito.ui.pages.apps import AppsPage
from hito.ui.pages.cleanup import CleanupPage
from hito.ui.pages.dashboard import DashboardPage
from hito.ui.pages.large_dupes import LargeDupesPage
from hito.ui.pages.settings_page import SettingsPage
from hito.ui.pages.uninstaller import UninstallerPage
from hito.ui.widgets import UsageBar, glyph_label, label

NAV = [
    ("dashboard", "Dashboard", "home"),
    ("analyzer", "Storage Analyzer", "pie"),
    ("cleanup", "Smart Cleanup", "broom"),
    ("apps", "Installed Apps & Games", "apps"),
    ("uninstaller", "Deep Uninstaller", "uninstall"),
    ("large", "Large & Duplicate Files", "copy"),
    ("settings", "Settings & History", "settings"),
]


class MainWindow(QMainWindow):
    def __init__(self, state, on_theme, start_page: str = "dashboard"):
        super().__init__()
        self.state = state
        self.setWindowTitle(APP_NAME + (" (Administrator)" if winapi.is_admin() else ""))
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(980, 640)
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            w, h = min(1360, int(screen.width() * 0.94)), min(880, int(screen.height() * 0.92))
            self.resize(w, h)
            self.move(screen.x() + (screen.width() - w) // 2, screen.y() + (screen.height() - h) // 2)
        else:
            self.resize(1360, 880)
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(250)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 16)
        sl.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = glyph_label("pie", 26)
        brand.addWidget(logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        bcol.addWidget(label("Hito", "Brand"))
        bcol.addWidget(label("Storage manager & uninstaller", "BrandSub"))
        brand.addLayout(bcol, 1)
        sl.addLayout(brand)
        sl.addSpacing(18)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        factories = {
            "dashboard": lambda: DashboardPage(state), "analyzer": lambda: AnalyzerPage(state),
            "cleanup": lambda: CleanupPage(state), "apps": lambda: AppsPage(state),
            "uninstaller": lambda: UninstallerPage(state), "large": lambda: LargeDupesPage(state),
            "settings": lambda: SettingsPage(state, on_theme),
        }
        for key, text, ic in NAV:
            b = QPushButton("  " + text.replace("&", "&&"))
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setIcon(icons.nav_icon(ic))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            group.addButton(b)
            sl.addWidget(b)
            self.buttons[key] = b
            page = factories[key]()
            self.pages[key] = page
            self.stack.addWidget(page)
        sl.addStretch(1)

        drives = list_drives()
        sysd = next((d for d in drives if d.root.upper().startswith("C")), drives[0] if drives else None)
        if sysd:
            sl.addWidget(label(f"{sysd.letter} {sysd.percent:.0f}% used", "Faint"))
            sl.addWidget(UsageBar(sysd.used / sysd.total if sysd.total else 0, 6))
            sl.addWidget(label(f"{human_size(sysd.free)} free", "Faint"))
        sl.addSpacing(8)
        sl.addWidget(label(f"Version {__version__}" + (" - DRY RUN" if state.settings.dry_run else ""), "Faint"))

        h.addWidget(side)
        h.addWidget(self.stack, 1)
        state.navigate.connect(self._navigate)
        self.go(start_page)

    def current_key(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is w), "dashboard")

    def go(self, key: str):
        self.buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])

    def _navigate(self, key: str, payload):
        if key not in self.pages:
            return
        self.go(key)
        page = self.pages[key]
        if payload is not None and hasattr(page, "handle_payload"):
            page.handle_payload(payload)
