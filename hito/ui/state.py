"""Shared application state passed to every page."""
from __future__ import annotations

import os
from collections import defaultdict

from PySide6.QtCore import QObject, Signal

from hito.core.categories import Classifier
from hito.core.fsutil import norm
from hito.settings import Settings


class AppState(QObject):
    scanFinished = Signal(object)      # ScanResult
    scanChanged = Signal()             # sizes changed after deletions
    appsChanged = Signal()
    cleanupScanned = Signal()
    settingsChanged = Signal()
    navigate = Signal(str, object)     # page key, payload

    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self._game_roots: list[str] | None = None
        self.classifier = self._make_classifier()
        self.scan = None
        self.files_by_dir: dict[int, list] = {}
        self.apps = None
        self.cleanup_scans = None

    def _make_classifier(self) -> Classifier:
        if self._game_roots is None:
            try:
                from hito.apps.launchers import game_roots
                self._game_roots = game_roots()
            except Exception:  # noqa: BLE001
                self._game_roots = []
        return Classifier(self.settings.custom(), self._game_roots)

    def save_settings(self):
        self.settings.save()
        self.classifier = self._make_classifier()
        self.settingsChanged.emit()

    def set_scan(self, result):
        self.scan = result
        idx = defaultdict(list)
        for f in result.files:
            idx[id(f.parent)].append(f)
        self.files_by_dir = idx
        self.scanFinished.emit(result)

    def apply_removed(self, removed_paths: list[str]):
        """Update the in-memory scan after files/folders were removed."""
        if not self.scan or not removed_paths:
            return
        gone = {norm(p) for p in removed_paths}
        for p in removed_paths:
            node = self.scan.find_node(p)
            if node is not None and node.parent is not None:
                _subtract(node.parent, node.size, node.files)
                node.parent.children = [c for c in node.parent.children if c is not node]
                continue
            parent = self.scan.find_node(os.path.dirname(p))
            if parent is not None:
                size = 0
                for f in self.files_by_dir.get(id(parent), []):
                    if norm(f.path) == norm(p):
                        size = f.size
                        break
                if size:
                    parent.own_size = max(0, parent.own_size - size)
                    _subtract(parent, size, 1)
        self.scan.files = [f for f in self.scan.files if norm(f.path) not in gone and not _under_any(f.path, gone)]
        idx = defaultdict(list)
        for f in self.scan.files:
            idx[id(f.parent)].append(f)
        self.files_by_dir = idx
        self.scanChanged.emit()


def _subtract(node, size: int, files: int):
    while node is not None:
        node.size = max(0, node.size - size)
        node.files = max(0, node.files - files)
        node = node.parent


def _under_any(path: str, roots: set[str]) -> bool:
    p = norm(path)
    return any(p.startswith(r + os.sep) for r in roots)
