import os

import pytest

from hito.core import winapi
from hito.safety import oplog
from hito.safety.deleter import PERMANENT, Deleter, safe_rmtree
from hito.safety.protect import BLOCK, CLEANUP, LEFTOVER, OK, USER, WARN, SafetyPolicy
from tests.conftest import make_junction, write


def pol(ctx=USER, **kw):
    return SafetyPolicy(ctx, check_running=False, **kw)


@pytest.mark.parametrize("path", [
    "C:\\", "D:\\",
    os.environ.get("WINDIR", r"C:\Windows"),
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "drivers"),
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.path.expanduser("~"),
    os.environ["LOCALAPPDATA"],
    os.environ["APPDATA"],
    r"C:\System Volume Information",
    r"C:\pagefile.sys",
    r"C:\$Recycle.Bin\S-1-5",
    r"C:\Windows.old",
    r"C:\Recovery",
    os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Package Cache", "x"),
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Installer", "abc.msi"),
])
def test_always_blocked(path):
    for ctx in (USER, CLEANUP, LEFTOVER):
        assert pol(ctx, allowed_roots=[path]).check(path).level == BLOCK, (ctx, path)


def test_known_personal_roots_blocked_but_contents_allowed_for_user():
    docs = winapi.known_folder("Documents")
    assert pol().check(docs).level == BLOCK
    assert pol().check(winapi.known_folder("Downloads")).level == BLOCK
    assert pol().check(os.path.join(docs, "old_report.pdf")).level == OK


def test_program_files_needs_uninstaller_in_user_context():
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    p = os.path.join(pf, "SomeApp", "app.exe")
    assert pol(USER).check(p).level == BLOCK
    assert pol(LEFTOVER).check(p).level == OK


def test_cleanup_only_inside_allowed_roots(tmp_path):
    allowed = tmp_path / "cache"
    assert pol(CLEANUP, allowed_roots=[str(allowed)]).check(str(allowed / "f.bin")).level == OK
    assert pol(CLEANUP, allowed_roots=[str(allowed)]).check(str(tmp_path / "other" / "f.bin")).level == BLOCK


def test_cleanup_never_touches_windows_outside_allowed():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    temp = os.path.join(windir, "Temp")
    p = pol(CLEANUP, allowed_roots=[temp])
    assert p.check(os.path.join(temp, "x.tmp")).level == OK
    assert p.check(os.path.join(windir, "System32", "x.dll")).level == BLOCK


def test_leftover_respects_other_apps(tmp_path):
    other = tmp_path / "OtherApp"
    p = pol(LEFTOVER, other_app_roots=[str(other)])
    assert p.check(str(other)).level == BLOCK
    assert p.check(str(other / "data")).level == BLOCK
    assert p.check(str(tmp_path)).level == BLOCK  # parent of another app


def test_browser_profile_data_and_saves():
    local = os.environ["LOCALAPPDATA"]
    login = os.path.join(local, "Google", "Chrome", "User Data", "Default", "Login Data")
    cache = os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cache", "f_0001")
    assert pol(LEFTOVER).check(login).level == BLOCK
    assert pol(CLEANUP, allowed_roots=[os.path.dirname(cache)]).check(cache).level == OK
    assert pol(USER).check(login).level == WARN
    saves = os.path.join(winapi.known_folder("Documents"), "My Games", "Game", "save1.dat")
    assert pol(LEFTOVER).check(saves).level == WARN


def test_running_program_is_blocked(tmp_path):
    exe = str(tmp_path / "App" / "app.exe")
    from hito.core.fsutil import norm
    p = SafetyPolicy(USER, running_exes={norm(exe)})
    assert p.check(exe).level == BLOCK
    assert p.check(str(tmp_path / "App")).level == BLOCK


def test_own_data_dir_blocked(isolated_data_dir):
    assert pol().check(str(isolated_data_dir / "logs" / "operations.jsonl")).level == BLOCK


def test_dry_run_changes_nothing(tmp_path):
    f = write(tmp_path / "keep.bin", 100)
    rep = Deleter(pol(), mode=PERMANENT, dry_run=True).delete([f])
    assert os.path.exists(f)
    assert rep.removed == 1 and rep.bytes == 100 and rep.dry_run
    assert oplog.read_recent()[0]["op"] == "dry-run"


def test_permanent_delete_and_log(tmp_path):
    f = write(tmp_path / "gone.bin", 10)
    d = tmp_path / "folder"
    write(d / "a" / "b.bin", 20)
    rep = Deleter(pol(), mode=PERMANENT).delete([f, str(d)])
    assert not os.path.exists(f) and not d.exists()
    assert rep.removed == 2 and rep.bytes == 30
    ops = [e["op"] for e in oplog.read_recent()]
    assert ops.count("delete") == 2


def test_blocked_paths_are_skipped_and_logged(tmp_path):
    rep = Deleter(pol(), mode=PERMANENT).delete([os.environ.get("WINDIR", r"C:\Windows")])
    assert rep.removed == 0 and rep.skipped
    assert oplog.read_recent()[0]["op"] == "skip"


def test_warn_requires_explicit_allow(tmp_path, monkeypatch):
    f = write(tmp_path / "saves" / "slot1.sav", 5)
    rep = Deleter(pol(), mode=PERMANENT, allow_warn=False).delete([f])
    assert os.path.exists(f) and "confirmation" in rep.skipped[0][1]
    rep = Deleter(pol(), mode=PERMANENT, allow_warn=True).delete([f])
    assert not os.path.exists(f)


def test_rmtree_does_not_follow_junctions(tmp_path):
    outside = tmp_path / "precious"
    keep = write(outside / "important.doc", 50)
    victim = tmp_path / "victim"
    write(victim / "junk.tmp", 10)
    if not make_junction(str(victim / "link"), str(outside)):
        pytest.skip("cannot create junctions here")
    errors = []
    safe_rmtree(str(victim), errors=errors)
    assert not victim.exists()
    assert os.path.exists(keep), "junction target must survive"


def test_deleting_a_junction_removes_only_the_link(tmp_path):
    outside = tmp_path / "target"
    keep = write(outside / "file.txt", 5)
    link = tmp_path / "link"
    if not make_junction(str(link), str(outside)):
        pytest.skip("cannot create junctions here")
    Deleter(pol(), mode=PERMANENT).delete([str(link)])
    assert not os.path.lexists(link)
    assert os.path.exists(keep)
