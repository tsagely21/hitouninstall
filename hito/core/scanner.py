"""Multi-threaded, read-only storage scanner.

Safety and accuracy rules:
  * junctions, symlinks and other name-surrogate reparse points are never
    followed, so scans cannot loop or count a folder twice;
  * hard-linked files (common in C:\\Windows\\WinSxS) are counted once;
  * cloud-only placeholders (OneDrive "online-only" files) take no local space
    and are reported separately instead of being added to folder sizes;
  * unreadable folders are recorded and skipped.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from hito.core import categories as cats
from hito.core.fsutil import entry_attrs, is_cloud_only, is_link_like, norm

MAX_ERRORS_KEPT = 500


class DirNode:
    __slots__ = ("name", "parent", "children", "size", "own_size", "files", "dirs", "ctx", "error", "cloud_size")

    def __init__(self, name: str, parent: "DirNode | None", ctx: str | None = None):
        self.name = name
        self.parent = parent
        self.children: list[DirNode] = []
        self.size = 0          # total bytes including subfolders (filled in after the walk)
        self.own_size = 0      # bytes of files directly inside this folder
        self.files = 0         # file count including subfolders
        self.dirs = 0
        self.ctx = ctx
        self.error: str | None = None
        self.cloud_size = 0

    @property
    def path(self) -> str:
        parts = []
        node = self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        parts.reverse()
        return os.path.join(*parts) if len(parts) > 1 else parts[0]

    def __repr__(self):
        return f"DirNode({self.path!r}, {self.size})"


class FileRec:
    __slots__ = ("parent", "name", "size", "mtime", "cat")

    def __init__(self, parent: DirNode, name: str, size: int, mtime: float, cat: str):
        self.parent = parent
        self.name = name
        self.size = size
        self.mtime = mtime
        self.cat = cat

    @property
    def path(self) -> str:
        return os.path.join(self.parent.path, self.name)


@dataclass
class ScanOptions:
    workers: int = 6
    index_min_size: int = 1024 * 1024           # files at least this big are kept for search/sort
    hardlinks: str = "windows"                  # "windows" | "everywhere" | "off"
    excluded: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    root_path: str
    root: DirNode
    categories: dict[str, int]
    category_counts: dict[str, int]
    files: list[FileRec]
    total_files: int
    total_dirs: int
    errors: list[tuple[str, str]]
    error_count: int
    links_skipped: int
    cloud_only_bytes: int
    hardlink_bytes: int
    started: float
    elapsed: float
    cancelled: bool

    @property
    def total_size(self) -> int:
        return self.root.size

    def largest_dirs(self, max_depth: int = 3, limit: int = 15, min_share: float = 0.0) -> list[DirNode]:
        """Biggest folders, preferring the deepest folder that still holds most of the space."""
        out: list[DirNode] = []
        stack = [(self.root, 0)]
        while stack:
            node, depth = stack.pop()
            for child in node.children:
                if depth + 1 <= max_depth:
                    out.append(child)
                    stack.append((child, depth + 1))
        out.sort(key=lambda n: n.size, reverse=True)
        picked: list[DirNode] = []
        for n in out:
            if n.size <= 0 or n.size < self.total_size * min_share:
                continue
            # skip a parent if one of its children carries >80% of it and is already listed
            if any(_is_ancestor(p, n) and n.size > p.size * 0.8 for p in picked):
                picked = [p for p in picked if not (_is_ancestor(p, n) and n.size > p.size * 0.8)]
            if any(_is_ancestor(n, p) for p in picked):
                continue
            picked.append(n)
            if len(picked) >= limit:
                break
        return picked

    def find_node(self, path: str) -> DirNode | None:
        target = norm(path)
        root = norm(self.root_path)
        if target == root:
            return self.root
        if not target.startswith(root.rstrip("\\/") + os.sep) and not target.startswith(root):
            return None
        rest = target[len(root):].strip("\\/")
        node = self.root
        for part in rest.split(os.sep):
            nxt = next((c for c in node.children if c.name.lower() == part), None)
            if nxt is None:
                return None
            node = nxt
        return node


def _is_ancestor(a: DirNode, b: DirNode) -> bool:
    node = b.parent
    while node is not None:
        if node is a:
            return True
        node = node.parent
    return False


class _Stats:
    """Progress counters. Updated without locks; small races only affect the live display."""

    def __init__(self):
        self.files = 0
        self.dirs = 0
        self.bytes = 0
        self.current = ""


class StorageScanner:
    def __init__(self, root_path: str, classifier: cats.Classifier, options: ScanOptions | None = None,
                 cancel: threading.Event | None = None, progress=None):
        self.root_path = os.path.abspath(root_path)
        if len(self.root_path) == 2 and self.root_path[1] == ":":
            self.root_path += os.sep
        self.classifier = classifier
        self.opt = options or ScanOptions()
        self.cancel = cancel or threading.Event()
        self.progress = progress
        self.excluded = {norm(p) for p in self.opt.excluded if p}
        self._q: queue.Queue = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._seen_inodes: set[tuple[int, int]] = set()
        self._stats = _Stats()
        self._files: list[FileRec] = []
        self._errors: list[tuple[str, str]] = []
        self._error_count = 0
        self._links = 0
        self._cloud = 0
        self._hardlink_bytes = 0
        self._cat_sizes: list[dict] = []
        self._cat_counts: list[dict] = []
        self._is_drive_root = os.path.splitdrive(self.root_path)[1] in ("\\", "/")

    # -- public -------------------------------------------------------------
    def run(self) -> ScanResult:
        started = time.time()
        root_ctx = self._root_context()
        root = DirNode(self.root_path, None, root_ctx)
        self._put(root, self.root_path)
        threads = [threading.Thread(target=self._worker, daemon=True, name=f"scan-{i}")
                   for i in range(max(1, self.opt.workers))]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            threads[0].join(timeout=0.15)
            if not threads[0].is_alive():
                time.sleep(0.05)
            if self.progress:
                s = self._stats
                self.progress({"files": s.files, "dirs": s.dirs, "bytes": s.bytes, "current": s.current})
        self._finalize(root)
        sizes: dict[str, int] = defaultdict(int)
        counts: dict[str, int] = defaultdict(int)
        for d in self._cat_sizes:
            for k, v in d.items():
                sizes[k] += v
        for d in self._cat_counts:
            for k, v in d.items():
                counts[k] += v
        return ScanResult(
            root_path=self.root_path, root=root, categories=dict(sizes), category_counts=dict(counts),
            files=self._files, total_files=root.files, total_dirs=root.dirs, errors=self._errors,
            error_count=self._error_count, links_skipped=self._links, cloud_only_bytes=self._cloud,
            hardlink_bytes=self._hardlink_bytes, started=started, elapsed=time.time() - started,
            cancelled=self.cancel.is_set(),
        )

    # -- internals ----------------------------------------------------------
    def _root_context(self) -> str | None:
        """Context of the scan root, derived from its ancestors when scanning a subfolder."""
        drive, rest = os.path.splitdrive(self.root_path)
        ctx = None
        cur = drive + os.sep
        parts = [p for p in rest.split(os.sep) if p]
        for i, part in enumerate(parts):
            cur = os.path.join(cur, part)
            ctx = self.classifier.dir_context(ctx, cur, part, is_drive_root_child=(i == 0))
        return ctx

    def _put(self, node: DirNode, path: str):
        with self._lock:
            self._pending += 1
        self._q.put((node, path))

    def _done_one(self):
        with self._lock:
            self._pending -= 1
            finished = self._pending == 0
        if finished:
            for _ in range(max(1, self.opt.workers)):
                self._q.put(None)

    def _error(self, path: str, exc: Exception):
        self._error_count += 1
        if len(self._errors) < MAX_ERRORS_KEPT:
            self._errors.append((path, exc.strerror or str(exc) if isinstance(exc, OSError) else str(exc)))

    def _worker(self):
        cat_sizes: dict[str, int] = defaultdict(int)
        cat_counts: dict[str, int] = defaultdict(int)
        self._cat_sizes.append(cat_sizes)
        self._cat_counts.append(cat_counts)
        clf = self.classifier
        index_min = self.opt.index_min_size
        hard = self.opt.hardlinks
        stats = self._stats
        while True:
            item = self._q.get()
            if item is None:
                return
            node, path = item
            try:
                if self.cancel.is_set():
                    continue
                stats.current = path
                in_root = self._is_drive_root and node.parent is None
                check_links = hard == "everywhere" or (hard == "windows" and node.ctx == cats.WINDOWS)
                try:
                    it = os.scandir(path)
                except OSError as exc:
                    node.error = exc.strerror or str(exc)
                    self._error(path, exc)
                    continue
                with it:
                    for entry in it:
                        try:
                            attrs, tag = entry_attrs(entry)
                            if is_link_like(attrs, tag) or entry.is_symlink():
                                self._links += 1
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if self.excluded and norm(entry.path) in self.excluded:
                                    continue
                                ctx = clf.dir_context(node.ctx, entry.path, entry.name, is_drive_root_child=in_root)
                                child = DirNode(entry.name, node, ctx)
                                node.children.append(child)
                                stats.dirs += 1
                                self._put(child, entry.path)
                                continue
                            st = entry.stat(follow_symlinks=False)
                            size = st.st_size
                            stats.files += 1
                            if is_cloud_only(attrs):
                                node.cloud_size += size
                                self._cloud += size
                                node.files += 1
                                continue
                            if check_links:
                                try:
                                    full = os.stat(entry.path, follow_symlinks=False)
                                    if full.st_nlink > 1:
                                        key = (full.st_dev, full.st_ino)
                                        with self._lock:
                                            seen = key in self._seen_inodes
                                            if not seen:
                                                self._seen_inodes.add(key)
                                        if seen:
                                            self._hardlink_bytes += size
                                            node.files += 1
                                            continue
                                except OSError:
                                    pass
                            cat = clf.file_category(node.ctx, entry.name, in_drive_root=in_root)
                            cat_sizes[cat] += size
                            cat_counts[cat] += 1
                            node.own_size += size
                            node.files += 1
                            stats.bytes += size
                            if size >= index_min:
                                self._files.append(FileRec(node, entry.name, size, st.st_mtime, cat))
                        except OSError as exc:
                            self._error(entry.path, exc)
            finally:
                self._done_one()

    @staticmethod
    def _finalize(root: DirNode):
        """Roll own sizes up the tree (iterative post-order, no recursion limit)."""
        order: list[DirNode] = []
        stack = [root]
        while stack:
            n = stack.pop()
            order.append(n)
            stack.extend(n.children)
        for n in reversed(order):
            n.size = n.own_size + sum(c.size for c in n.children)
            n.files += sum(c.files for c in n.children)
            n.dirs = len(n.children) + sum(c.dirs for c in n.children)
            n.cloud_size += sum(c.cloud_size for c in n.children)
            n.children.sort(key=lambda c: c.size, reverse=True)


def list_folder_files(path: str) -> list[tuple[str, int, float, int]]:
    """Files directly inside a folder: (name, size, mtime, attrs). Used for lazy explorer listing."""
    out = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    attrs, tag = entry_attrs(e)
                    if is_link_like(attrs, tag) or not e.is_file(follow_symlinks=False):
                        continue
                    st = e.stat(follow_symlinks=False)
                    out.append((e.name, st.st_size, st.st_mtime, attrs))
                except OSError:
                    continue
    except OSError:
        pass
    return out
