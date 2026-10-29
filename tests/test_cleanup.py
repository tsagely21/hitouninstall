import os
import time

from hito.cleanup.catalog import SAFE, Target, build_catalog
from hito.cleanup.engine import PlanItem, describe_plan, execute_plan, scan_target
from hito.safety.deleter import PERMANENT
from tests.conftest import write


def old(path, days=3):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def target(root, **kw):
    return Target("t", "Test cache", "Test", SAFE, "desc", "effect", [str(root)], **kw)


def test_catalog_is_sane():
    cat = build_catalog()
    ids = [t.id for t in cat]
    assert len(ids) == len(set(ids))
    for t in cat:
        assert t.risk in ("safe", "review", "advanced")
        assert t.description and t.effect
        for p in t.paths:
            low = p.lower()
            # never target personal data or installer caches the reference project touched
            assert "\\office\\recent" not in low and "unsaveddfiles" not in low
            assert "$patchcache$" not in low and "\\prefetch" not in low
            assert "\\documents" not in low and "\\downloads" not in low


def test_scan_respects_age_and_patterns(tmp_path):
    a = write(tmp_path / "old.log", 100)
    old(a)
    write(tmp_path / "new.log", 100)
    b = write(tmp_path / "old.txt", 100)
    old(b)
    s = scan_target(target(tmp_path, patterns=["*.log"], min_age_hours=24))
    assert [os.path.basename(p) for p, _ in s.files] == ["old.log"]
    assert s.bytes == 100


def test_execute_removes_only_planned_files_and_prunes(tmp_path):
    a = write(tmp_path / "sub" / "a.tmp", 10)
    b = write(tmp_path / "b.tmp", 20)
    s = scan_target(target(tmp_path))
    plan = [PlanItem(s, files=[(a, 10)])]
    out = execute_plan(plan, PERMANENT, dry_run=False)
    assert not os.path.exists(a)
    assert os.path.exists(b)
    assert not (tmp_path / "sub").exists()      # emptied folder pruned
    assert tmp_path.exists()                    # root kept
    assert out.freed_bytes == 10


def test_execute_dry_run(tmp_path):
    a = write(tmp_path / "a.tmp", 10)
    s = scan_target(target(tmp_path))
    out = execute_plan([PlanItem(s)], PERMANENT, dry_run=True)
    assert os.path.exists(a)
    assert out.freed_bytes == 10 and out.dry_run
    assert describe_plan([PlanItem(s)], PERMANENT, True)[0].startswith("DRY RUN")


def test_files_outside_target_root_refused(tmp_path):
    root = tmp_path / "cache"
    write(root / "x.tmp", 1)
    outsider = write(tmp_path / "personal.doc", 5)
    s = scan_target(target(root))
    rep = execute_plan([PlanItem(s, files=[(outsider, 5)])], PERMANENT, dry_run=False).reports["t"]
    assert os.path.exists(outsider)
    assert rep.skipped
