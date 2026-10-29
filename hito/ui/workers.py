"""Background tasks: run blocking work on a thread pool, deliver results on the GUI thread."""
from __future__ import annotations

import threading
import time
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_live: set = set()


class _Signals(QObject):
    progress = Signal(object)
    result = Signal(object)
    error = Signal(str)
    done = Signal()


class TaskHandle:
    def __init__(self):
        self.cancel = threading.Event()
        self.signals = _Signals()
        self.running = True

    def stop(self):
        self.cancel.set()


class _Runnable(QRunnable):
    def __init__(self, handle: TaskHandle, fn, args, kwargs, throttle: float):
        super().__init__()
        self.handle = handle
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.throttle = throttle
        self._last = 0.0
        self.setAutoDelete(True)

    def _progress(self, payload, force: bool = False):
        now = time.monotonic()
        if force or now - self._last >= self.throttle:
            self._last = now
            self.handle.signals.progress.emit(payload)

    def run(self):
        h = self.handle
        try:
            res = self.fn(h.cancel, self._progress, *self.args, **self.kwargs)
            h.signals.result.emit(res)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never crashes the app
            h.signals.error.emit(f"{exc}\n\n{traceback.format_exc(limit=6)}")
        finally:
            h.running = False
            h.signals.done.emit()


def run_task(fn, *args, on_result=None, on_progress=None, on_error=None, on_done=None,
             throttle: float = 0.08, **kwargs) -> TaskHandle:
    """fn(cancel_event, progress_callback, *args, **kwargs) runs on a worker thread."""
    h = TaskHandle()
    if on_result:
        h.signals.result.connect(on_result)
    if on_progress:
        h.signals.progress.connect(on_progress)
    if on_error:
        h.signals.error.connect(on_error)
    if on_done:
        h.signals.done.connect(on_done)
    _live.add(h)
    h.signals.done.connect(lambda: _live.discard(h))
    QThreadPool.globalInstance().start(_Runnable(h, fn, args, kwargs, throttle))
    return h
