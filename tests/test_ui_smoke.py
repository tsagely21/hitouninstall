"""GUI smoke tests on the offscreen Qt platform: pages build, background tasks finish, delete flows work."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.conftest import write  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait(app, cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        QThreadPool.globalInstance().waitForDone(50)
        if cond():
            app.processEvents()
            return True
    return False


@pytest.fixture
def window(qapp, monkeypatch):
    from hito.ui import dialogs, theme
    from hito.ui.main_window import MainWindow
    from hito.ui.state import AppState
    monkeypatch.setattr(dialogs.ConfirmDialog, "exec", lambda self: True)
    monkeypatch.setattr(dialogs.InfoDialog, "exec", lambda self: True)
    theme.set_theme("dark")
    qapp.setStyleSheet(theme.stylesheet())
    state = AppState()
    state.settings.user_delete_mode = "permanent"   # keep test files out of the real Recycle Bin
    win = MainWindow(state, lambda _t: None)
    win.show()
    yield win, state
    QThreadPool.globalInstance().waitForDone(30000)
    win.close()


def test_all_pages_build_and_switch(qapp, window):
    win, state = window
    for key in win.pages:
        state.navigate.emit(key, None)
        qapp.processEvents()
        assert win.stack.currentWidget() is win.pages[key]


def test_analyzer_scan_and_delete(qapp, window, tmp_path):
    win, state = window
    keep = write(tmp_path / "keep" / "a.bin", 4096)
    victim = write(tmp_path / "junk" / "big.bin", 2 * 1024 * 1024)
    page = win.pages["analyzer"]
    state.navigate.emit("analyzer", str(tmp_path))
    assert wait(qapp, lambda: state.scan is not None and page.task is None)
    assert state.scan.total_size == 4096 + 2 * 1024 * 1024
    page.set_mode("files")
    assert page.model.rowCount() == 1  # only the 2 MB file is indexed
    from hito.ui import actions
    done = []
    actions.delete_user_paths(page, state, [(victim, 2 * 1024 * 1024)], "test", on_finished=lambda r, rm: done.append(rm))
    assert wait(qapp, lambda: bool(done))
    assert not os.path.exists(victim) and os.path.exists(keep)
    assert state.scan.total_size == 4096, "tree sizes update after deletion"


def test_duplicates_flow_keeps_one_copy(qapp, window, tmp_path):
    win, state = window
    data = os.urandom(200_000)
    a = write(tmp_path / "one" / "photo.jpg", data=data)
    b = write(tmp_path / "two" / "photo copy.jpg", data=data)
    tab = win.pages["large"].dupes
    tab.min.setValue(0)
    tab.sys.setChecked(False)
    tab.src.addItem("test", str(tmp_path))
    tab.src.setCurrentIndex(tab.src.count() - 1)
    tab.start()
    assert wait(qapp, lambda: tab.task is None and tab.groups)
    assert len(tab.groups) == 1
    tab._auto()
    assert len(tab._selected()) == 1
    tab._delete()
    assert wait(qapp, lambda: sum(os.path.exists(p) for p in (a, b)) == 1)


def test_duplicates_refuses_deleting_every_copy(qapp, window, tmp_path):
    win, state = window
    data = os.urandom(50_000)
    a = write(tmp_path / "x.bin", data=data)
    b = write(tmp_path / "y.bin", data=data)
    tab = win.pages["large"].dupes
    tab.min.setValue(0)
    tab.sys.setChecked(False)
    tab.src.addItem("test", str(tmp_path))
    tab.src.setCurrentIndex(tab.src.count() - 1)
    tab.start()
    assert wait(qapp, lambda: tab.task is None and tab.groups)
    from PySide6.QtCore import Qt
    for _i, _top, kids in tab._children():
        for k in kids:
            k.setCheckState(0, Qt.Checked)
    tab._delete()
    QThreadPool.globalInstance().waitForDone(3000)
    assert os.path.exists(a) and os.path.exists(b)


def test_cleanup_dry_run_end_to_end(qapp, window):
    """Runs the real catalogue in dry-run mode: every selected action is simulated, nothing changes."""
    win, state = window
    from hito.safety import oplog
    state.settings.dry_run = True
    page = win.pages["cleanup"]
    page.start_scan()
    assert wait(qapp, lambda: page.task is None and state.cleanup_scans is not None, timeout=180)
    page.populate()
    page.select_safe()
    selected = [r for r in page.rows if r.selected]
    if not selected:
        pytest.skip("nothing to clean on this machine")
    before = {p: os.path.exists(p) for r in selected for p, _ in r.scan.files[:200]}
    page.clean()
    assert wait(qapp, lambda: page.task is None, timeout=300)
    assert all(os.path.exists(p) == existed for p, existed in before.items() if existed)
    ops = oplog.read_recent(50)
    assert any(e["op"] == "cleanup-summary" and e["result"] == "simulated" for e in ops)
