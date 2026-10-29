import os
import subprocess

import pytest

from hito.apps.inventory import AppRecord, _parse_install_date
from hito.apps.launchers import parse_vdf
from hito.uninstall import leftovers as lo
from hito.uninstall.methods import resolve, split_command
from hito.uninstall.regops import is_protected_key
from tests.conftest import write


def test_split_command(tmp_path):
    exe = write(tmp_path / "My App" / "unins000.exe", 1)
    assert split_command(f'"{exe}" /SILENT') == (exe, "/SILENT")
    assert split_command(f"{exe} /x") == (exe, "/x")          # unquoted path with spaces
    assert split_command("MsiExec.exe /I{12345678-1234-1234-1234-123456789ABC}")[0].lower() == "msiexec.exe"


def test_resolve_msi_uses_uninstall_switch():
    app = AppRecord("k", "Thing", uninstall_string="MsiExec.exe /I{12345678-1234-1234-1234-123456789ABC}")
    m = resolve(app)
    assert m.kind == "msi" and m.params == "/x {12345678-1234-1234-1234-123456789ABC}"


def test_resolve_store_steam_and_missing(tmp_path):
    assert resolve(AppRecord("k", "S", source="store", package_full_name="Pkg_1.0_x64__abc")).kind == "store"
    assert resolve(AppRecord("k", "G", source="steam", steam_appid="730")).file == "steam://uninstall/730"
    missing = str(tmp_path / "gone" / "uninst.exe")
    assert resolve(AppRecord("k", "X", uninstall_string=f'"{missing}"')).kind == "none"


def test_install_date_parsing():
    assert str(_parse_install_date("20240131")) == "2024-01-31"
    assert _parse_install_date("garbage") is None
    assert _parse_install_date("19000101") is None


def test_vdf_parser():
    data = parse_vdf('"libraryfolders" { "0" { "path" "C:\\\\Steam" "apps" { "730" "123" } } }')
    assert data["libraryfolders"]["0"]["path"] == "C:\\Steam"
    assert data["libraryfolders"]["0"]["apps"]["730"] == "123"


def test_protected_registry_keys():
    assert is_protected_key(r"HKEY_LOCAL_MACHINE\SOFTWARE")
    assert is_protected_key(r"HKLM\SOFTWARE\Microsoft")
    assert is_protected_key(r"HKLM\SYSTEM\CurrentControlSet\Services\x")
    assert is_protected_key(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run")
    assert is_protected_key(r"HKCU\Software")
    assert not is_protected_key(r"HKCU\SOFTWARE\Contoso\Widget")


def test_clean_name_and_keys():
    assert lo.clean_name("7-Zip 25.01 (x64)") == "7-Zip"
    snap = lo.AppSnapshot(AppRecord("k", "Widget Studio 2.3", publisher="Contoso Ltd."), r"C:\Program Files\WidgetStudio")
    strong, weak = lo.name_keys(snap)
    assert "widgetstudio" in strong
    assert "widget studio" in weak
    assert lo._publisher_key("Contoso Ltd.") == "contoso"
    assert lo._publisher_key("Microsoft Corporation") == ""


@pytest.fixture
def fake_roots(tmp_path, monkeypatch):
    roaming = tmp_path / "Roaming"
    local = tmp_path / "Local"
    for d in (roaming, local):
        d.mkdir()
    monkeypatch.setattr(lo, "search_roots", lambda: [(str(roaming), "AppData\\Roaming"), (str(local), "AppData\\Local")])
    monkeypatch.setattr(lo, "shortcut_roots", lambda: [])
    monkeypatch.setattr(lo, "winreg", None)
    return roaming, local


def test_leftover_scan_evidence_and_safety(tmp_path, fake_roots):
    roaming, local = fake_roots
    install = tmp_path / "PF" / "WidgetStudio"
    write(install / "leftover.dll", 10)
    write(roaming / "WidgetStudio" / "settings.json", 5)        # matches install folder name -> high
    write(roaming / "Contoso" / "Widget Studio" / "a.cfg", 5)   # publisher\product -> high
    write(local / "Widget Studio" / "cache.bin", 5)             # exact product name -> medium
    write(local / "WidgetStudioBeta" / "x.bin", 5)              # prefix only -> low, unselected
    write(local / "Common" / "x.bin", 5)                        # generic -> never
    write(local / "OtherTool" / "x.bin", 5)                     # unrelated -> never
    other = AppRecord("o", "Other Tool", install_location=str(local / "OtherTool"))
    app = AppRecord("k", "Widget Studio 2.3", publisher="Contoso Ltd.", install_location=str(install))
    items, _ = lo.scan_leftovers(lo.AppSnapshot(app, str(install)), [other])
    by = {os.path.relpath(i.path, tmp_path): i for i in items}
    assert by[os.path.join("PF", "WidgetStudio")].confidence == lo.HIGH
    assert by[os.path.join("Roaming", "WidgetStudio")].confidence == lo.HIGH
    assert by[os.path.join("Roaming", "Contoso", "Widget Studio")].confidence == lo.HIGH
    assert by[os.path.join("Local", "Widget Studio")].confidence == lo.MEDIUM
    assert by[os.path.join("Local", "WidgetStudioBeta")].confidence == lo.LOW
    assert not by[os.path.join("Local", "WidgetStudioBeta")].selected
    assert os.path.join("Local", "Common") not in by
    assert os.path.join("Local", "OtherTool") not in by
    assert all(not i.selected for i in items if i.confidence != lo.HIGH)


def test_leftover_never_proposes_other_apps_folder(tmp_path, fake_roots):
    roaming, _ = fake_roots
    shared = roaming / "Widget"
    write(shared / "data.bin", 5)
    other = AppRecord("o", "Widget Pro", install_location=str(shared))
    app = AppRecord("k", "Widget", install_location="")
    items, excluded = lo.scan_leftovers(lo.AppSnapshot(app, ""), [other])
    assert all(os.path.normcase(i.path) != os.path.normcase(str(shared)) for i in items)


def test_lnk_target_parser(tmp_path):
    target = write(tmp_path / "Target App" / "app.exe", 1)
    lnk = str(tmp_path / "App.lnk")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{target}';$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    if r.returncode != 0 or not os.path.exists(lnk):
        pytest.skip("cannot create shortcuts here")
    assert os.path.normcase(lo.lnk_target(lnk)) == os.path.normcase(target)
