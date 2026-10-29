"""Application entry point."""
from __future__ import annotations

import os
import sys
import traceback


def _set_app_id():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hito.StorageManager")
        except Exception:  # noqa: BLE001
            pass


def _install_excepthook():
    """Log unexpected errors and keep the app alive instead of crashing silently."""
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            from hito.settings import sub_dir
            with open(os.path.join(sub_dir("logs"), "errors.log"), "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance():
                QMessageBox.warning(None, "Unexpected error", "Something went wrong. Nothing was deleted because of it.\n\n"
                                    + str(exc)[:500])
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if "--self-test" in argv:
        from hito.selftest import run
        return run(argv[argv.index("--self-test") + 1] if len(argv) > argv.index("--self-test") + 1 else "")
    _set_app_id()
    _install_excepthook()
    from PySide6.QtWidgets import QApplication

    from hito import APP_NAME
    from hito.ui import icons, theme
    from hito.ui.main_window import MainWindow
    from hito.ui.state import AppState

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Hito")
    app.setStyle("Fusion")
    state = AppState()
    holder: dict = {}

    theme.ensure_assets()

    def apply_theme(name: str):
        theme.set_theme(name)
        icons.clear_cache()
        app.setStyleSheet(theme.stylesheet())

    def rebuild(name: str):
        old = holder.get("win")
        start = old.current_key() if old else "dashboard"
        geo = old.saveGeometry() if old else None
        apply_theme(name)
        win = MainWindow(state, rebuild, start_page=start)
        if geo is not None:
            win.restoreGeometry(geo)
        win.show()
        holder["win"] = win
        if old:
            old.hide()
            old.deleteLater()

    apply_theme(state.settings.theme)
    app.setWindowIcon(icons.app_icon())
    rebuild(state.settings.theme)
    return app.exec()
