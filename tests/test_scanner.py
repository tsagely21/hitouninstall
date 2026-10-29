import os
import threading

import pytest

from hito.core.categories import Classifier
from hito.core.scanner import ScanOptions, StorageScanner
from tests.conftest import make_junction, write


def neutral_classifier():
    """pytest's tmp_path lives in %TEMP%, which would make every file 'Temp & caches'."""
    clf = Classifier()
    clf.root_rules = {}
    return clf


def scan(root, **opt):
    return StorageScanner(str(root), neutral_classifier(), ScanOptions(workers=3, index_min_size=0, **opt)).run()


def test_sizes_roll_up(tmp_path):
    write(tmp_path / "a" / "one.bin", 1000)
    write(tmp_path / "a" / "b" / "two.bin", 2000)
    write(tmp_path / "three.bin", 500)
    r = scan(tmp_path)
    assert r.total_size == 3500
    assert r.total_files == 3
    a = r.find_node(str(tmp_path / "a"))
    assert a is not None and a.size == 3000
    assert r.find_node(str(tmp_path / "a" / "b")).size == 2000
    assert len(r.files) == 3


def test_junction_loop_is_not_followed(tmp_path):
    write(tmp_path / "data" / "file.bin", 4096)
    if not make_junction(str(tmp_path / "data" / "loop"), str(tmp_path)):
        pytest.skip("cannot create junctions here")
    r = scan(tmp_path)
    assert r.total_size == 4096          # not counted twice, no infinite loop
    assert r.links_skipped >= 1


def test_junction_to_other_folder_not_double_counted(tmp_path):
    write(tmp_path / "real" / "big.bin", 10_000)
    if not make_junction(str(tmp_path / "alias"), str(tmp_path / "real")):
        pytest.skip("cannot create junctions here")
    assert scan(tmp_path).total_size == 10_000


def test_hard_links_counted_once(tmp_path):
    src = write(tmp_path / "orig.bin", 8000)
    try:
        os.link(src, tmp_path / "link.bin")
    except OSError:
        pytest.skip("hard links unsupported")
    assert scan(tmp_path, hardlinks="everywhere").total_size == 8000
    assert scan(tmp_path, hardlinks="off").total_size == 16000


def test_cancel_stops_scan(tmp_path):
    for i in range(50):
        write(tmp_path / f"d{i}" / "f.bin", 10)
    ev = threading.Event()
    ev.set()
    r = StorageScanner(str(tmp_path), Classifier(), ScanOptions(workers=2), cancel=ev).run()
    assert r.cancelled
    assert r.total_files < 50


def test_unreadable_folder_is_recorded_not_fatal(tmp_path, monkeypatch):
    write(tmp_path / "ok" / "f.bin", 10)
    write(tmp_path / "locked" / "g.bin", 10)
    real = os.scandir

    def fake(path="."):
        if str(path).endswith("locked"):
            raise PermissionError(13, "Access is denied", str(path))
        return real(path)

    monkeypatch.setattr("hito.core.scanner.os.scandir", fake)
    r = scan(tmp_path)
    assert r.error_count == 1
    assert r.total_size == 10
    assert r.find_node(str(tmp_path / "locked")).error


def test_categories_by_extension(tmp_path):
    write(tmp_path / "clip.mp4", 300)
    write(tmp_path / "photo.jpg", 200)
    write(tmp_path / "notes.pdf", 100)
    write(tmp_path / "node_modules" / "x.txt", 50)
    r = scan(tmp_path)
    assert r.categories["videos"] == 300
    assert r.categories["pictures"] == 200
    assert r.categories["documents"] == 100
    assert r.categories["dev"] == 50


def test_largest_dirs(tmp_path):
    write(tmp_path / "big" / "f.bin", 9000)
    write(tmp_path / "small" / "f.bin", 10)
    r = scan(tmp_path)
    top = r.largest_dirs(limit=1)
    assert top and top[0].name == "big"
