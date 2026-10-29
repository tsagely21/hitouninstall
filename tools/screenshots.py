"""Dev helper: open the real app, visit each page and save window screenshots.

Usage: python tools/screenshots.py OUT_DIR [--scan PATH] [--theme light|dark] [--size WxH]
Read-only: it scans and lists, it never deletes anything.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hito.ui import icons, theme  # noqa: E402
from hito.ui.main_window import MainWindow  # noqa: E402
from hito.ui.state import AppState  # noqa: E402


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    scan = sys.argv[sys.argv.index("--scan") + 1] if "--scan" in sys.argv else None
    th = sys.argv[sys.argv.index("--theme") + 1] if "--theme" in sys.argv else "dark"
    size = sys.argv[sys.argv.index("--size") + 1] if "--size" in sys.argv else "1360x880"
    w, h = (int(x) for x in size.split("x"))
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.ensure_assets()
    theme.set_theme(th)
    icons.clear_cache()
    app.setStyleSheet(theme.stylesheet())
    state = AppState()
    win = MainWindow(state, lambda _t: None)
    win.resize(w, h)
    win.show()
    steps = []

    def shot(name):
        def f():
            win.grab().save(os.path.join(out, f"{name}.png"))
            print("saved", name, flush=True)
        return f

    def go(key, payload=None):
        return lambda: state.navigate.emit(key, payload)

    steps += [(6000, shot("01_dashboard"))]
    if scan:
        steps += [(100, go("analyzer", scan)), (1000, lambda: None)]
        steps += [("scan", shot("02_analyzer_treemap"))]
        steps += [(300, lambda: win.pages["analyzer"].tabs.setCurrentIndex(1)), (600, shot("03_analyzer_categories"))]
        steps += [(300, lambda: win.pages["analyzer"].tabs.setCurrentIndex(0)), (100, go("dashboard")), (900, shot("04_dashboard_after_scan"))]
    steps += [(100, go("cleanup")), (2500, shot("05_cleanup"))]
    steps += [(100, go("apps")), ("apps", shot("06_apps"))]
    steps += [(100, go("uninstaller")), (800, shot("07_uninstaller"))]
    steps += [(100, go("large")), (800, shot("08_large"))]
    steps += [(100, go("settings")), (800, shot("09_settings"))]
    steps += [(100, app.quit)]

    def run(i=0):
        if i >= len(steps):
            return
        delay, fn = steps[i]
        if delay == "scan":
            if win.pages["analyzer"].task is not None or state.scan is None:
                QTimer.singleShot(500, lambda: run(i))
                return
            delay = 1200
        if delay == "apps":
            p = win.pages["apps"]
            if p.task is not None or p.size_task is not None or state.apps is None:
                QTimer.singleShot(700, lambda: run(i))
                return
            delay = 800

        def step():
            fn()
            run(i + 1)

        QTimer.singleShot(delay, step)

    run()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
