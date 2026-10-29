import os

import pytest

from hito.dupes.finder import auto_select, find_duplicates, find_large, validate_selection
from tests.conftest import write


def test_finds_identical_files_only(tmp_path):
    payload = os.urandom(300_000)
    a = write(tmp_path / "a" / "copy1.bin", data=payload)
    b = write(tmp_path / "b" / "copy2.bin", data=payload)
    # same size, same head and tail, different middle -> signature or hash must separate it
    altered = bytearray(payload)
    altered[150_000] ^= 0xFF
    write(tmp_path / "c" / "near.bin", data=bytes(altered))
    write(tmp_path / "d" / "other.bin", data=os.urandom(1000))
    groups = find_duplicates([str(tmp_path)], min_size=1, protect_system=False)
    assert len(groups) == 1
    assert sorted(f.path for f in groups[0].files) == sorted([a, b])
    assert groups[0].wasted == 300_000


def test_hard_links_are_not_duplicates(tmp_path):
    src = write(tmp_path / "x.bin", data=os.urandom(5000))
    try:
        os.link(src, tmp_path / "y.bin")
    except OSError:
        pytest.skip("hard links unsupported")
    assert find_duplicates([str(tmp_path)], min_size=1, protect_system=False) == []


def test_auto_select_keeps_exactly_one(tmp_path):
    payload = os.urandom(2000)
    paths = [write(tmp_path / f"f{i}.bin", data=payload) for i in range(3)]
    g = find_duplicates([str(tmp_path)], min_size=1, protect_system=False)[0]
    for keep in ("oldest", "newest", "shortest"):
        remove = auto_select(g, keep)
        assert len(remove) == 2
    assert validate_selection([g], set(paths)) == [g]
    assert validate_selection([g], set(paths[:2])) == []


def test_find_large_threshold(tmp_path):
    write(tmp_path / "big.bin", 5000)
    write(tmp_path / "small.bin", 10)
    out = find_large([str(tmp_path)], min_size=1000)
    assert [os.path.basename(f.path) for f in out] == ["big.bin"]
