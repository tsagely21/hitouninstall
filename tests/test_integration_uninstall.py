"""End-to-end deep uninstall of a throwaway dummy app (real registry, real files, real uninstaller).

Opt-in: set HITO_INTEGRATION=1. Everything it creates has a unique random name under HKCU and the
user profile, and is removed again in `finally`.
"""
import os
import shutil
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HITO_INTEGRATION") != "1" or os.name != "nt",
                                reason="set HITO_INTEGRATION=1 to run (creates and removes a dummy app)")

UNINSTALL = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall"


def reg(*args):
    return subprocess.run(["reg", *args], capture_output=True, text=True)


def test_deep_uninstall_dummy_app():
    from hito.apps.inventory import registry_apps
    from hito.safety.deleter import PERMANENT, Deleter
    from hito.safety.protect import LEFTOVER, SafetyPolicy
    from hito.uninstall import leftovers as lo
    from hito.uninstall import methods, regops, runner

    name = f"HitoTestWidget_{uuid.uuid4().hex[:8]}"
    key = f"{UNINSTALL}\\{name}"
    pub_key = r"HKCU\Software\HitoTestCo"
    install = os.path.join(os.environ["LOCALAPPDATA"], "Programs", name)
    roaming = os.path.join(os.environ["APPDATA"], name)
    try:
        os.makedirs(os.path.join(install, "data"))
        with open(os.path.join(install, "app.exe"), "wb") as fh:
            fh.write(b"MZ" + b"\0" * 1000)
        with open(os.path.join(install, "data", "big.bin"), "wb") as fh:
            fh.write(b"x" * 5000)
        cmd = os.path.join(install, "uninstall.cmd")
        with open(cmd, "w") as fh:
            fh.write(f'@echo off\r\nrmdir /s /q "{install}\\data"\r\ndel /q "{install}\\app.exe"\r\n'
                     f'reg delete "{key}" /f >nul\r\n')
        os.makedirs(roaming)
        with open(os.path.join(roaming, "settings.json"), "w") as fh:
            fh.write("{}")
        reg("add", key, "/v", "DisplayName", "/d", f"{name} 1.0", "/f")
        reg("add", key, "/v", "Publisher", "/d", "HitoTestCo", "/f")
        reg("add", key, "/v", "DisplayVersion", "/d", "1.0", "/f")
        reg("add", key, "/v", "InstallLocation", "/d", install, "/f")
        reg("add", key, "/v", "UninstallString", "/d", f'"{cmd}"', "/f")
        reg("add", f"{pub_key}\\{name}", "/v", "Theme", "/d", "dark", "/f")

        app = next(a for a in registry_apps() if a.name.startswith(name))
        assert app.install_location.lower() == install.lower()
        m = methods.resolve(app)
        assert m.kind == "exe" and m.file.lower() == cmd.lower()

        snap = lo.take_snapshot(app)
        assert "app.exe" in snap.exe_names

        outcome = runner.run_uninstall(app, m, timeout_s=120)
        assert outcome.removed, outcome.message
        assert not regops.key_exists(f"HKEY_CURRENT_USER\\{key[5:]}")

        others = [a for a in registry_apps() if a.key != app.key]
        items, _excluded = lo.scan_leftovers(snap, others)
        found = {(i.kind, i.path.lower()) for i in items}
        assert ("folder", install.lower()) in found
        assert ("folder", roaming.lower()) in found
        reg_item = next(i for i in items if i.kind == "regkey" and i.path.lower().endswith(name.lower()))
        assert reg_item.confidence == lo.HIGH
        by_path = {i.path.lower(): i for i in items}
        assert by_path[install.lower()].selected and by_path[roaming.lower()].selected

        rep = Deleter(SafetyPolicy(LEFTOVER, other_app_roots=[a.install_location for a in others if a.install_location]),
                      mode=PERMANENT).delete([install, roaming])
        assert rep.removed == 2, rep
        assert not os.path.exists(install) and not os.path.exists(roaming)

        ok, msg = regops.delete_key(reg_item.path)
        assert ok, msg
        assert not regops.key_exists(reg_item.path)
        backup = regops.list_backups()[0][0]
        ok, msg = regops.restore_backup(backup)
        assert ok, msg
        assert regops.key_exists(reg_item.path), "restore from .reg backup must bring the key back"
    finally:
        reg("delete", key, "/f")
        reg("delete", pub_key, "/f")
        shutil.rmtree(install, ignore_errors=True)
        shutil.rmtree(roaming, ignore_errors=True)
