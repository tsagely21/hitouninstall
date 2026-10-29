import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path_factory, monkeypatch):
    """Keep settings, logs and backups out of the real %LOCALAPPDATA%."""
    d = tmp_path_factory.mktemp("hito_data")
    monkeypatch.setenv("HITO_DATA_DIR", str(d))
    return d


def make_junction(link: str, target: str) -> bool:
    if os.name != "nt":
        return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
    return r.returncode == 0


def write(path, size: int = 0, data: bytes | None = None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if data is not None else b"x" * size)
    return str(path)
