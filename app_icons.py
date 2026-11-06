"""Asynchronous persistent icons, retained without an arbitrary entry-count cap."""
import ctypes as C
import queue
import threading
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon, QPixmap, QImageReader
from icon_store import IconStore, canonical, current
from icon_sources import extract_image, extract_with_dependencies
from icon_visuals import visible_image


class ApplicationIcons(QObject):
    ready = Signal(str, object)
    changed = Signal(str)
    recheck_seconds = 5.

    def __init__(self, parent=None, cache_dir=None, extractor=None):
        super().__init__(parent)
        self.images = {}
        self.pending = {}
        self.attempted = set()
        self.last_validation = {}
        self.revisions = {}
        self.snapshots = {}
        self.accepted = set()
        self.sequence = 0
        self.errors = {}
        self.jobs = queue.Queue()
        self.stopped = threading.Event()
        self.extractor = extractor or extract_with_dependencies
        profile = getattr(parent, 'config_path', None)
        self.store = IconStore(cache_dir or (profile.parent/'cache/icons-v1')) if cache_dir or profile else None
        self.ready.connect(self.receive)
        QImageReader.setAllocationLimit(64)
        self.workers = [threading.Thread(target=self.run, daemon=True) for _ in range(2)]
        for worker in self.workers:
            worker.start()
        self.warmer = None
        if self.store:
            self.warmer = threading.Thread(target=self.prime, daemon=True)
            self.warmer.start()

    def request(self, path):
        key = canonical(path)
        icon = self.cached(path)
        if (not self.stopped.is_set() and key not in self.pending
                and time.monotonic()-self.last_validation.get(key, -1e9) >= self.recheck_seconds):
            self.sequence += 1
            self.pending[key] = self.sequence
            self.jobs.put_nowait((path, self.sequence))
        return icon

    def cached(self, path):
        return self.images.get(path, self.images.get(canonical(path)))

    def prime(self):
        for path, result in self.store.entries():
            if self.stopped.is_set():
                return
            self.snapshots[canonical(path)] = result['dependencies']
            try:
                self.ready.emit(path, dict(result, revision=0))
            except RuntimeError:
                return

    def run(self):
        ole = C.WinDLL('ole32')
        initialized = ole.CoInitializeEx(None, 2) in (0, 1)
        try:
            while not self.stopped.is_set():
                try:
                    path, revision = self.jobs.get(timeout=.2)
                except queue.Empty:
                    continue
                key = canonical(path)
                try:
                    if key in self.accepted and current(self.snapshots.get(key)):
                        result = dict(unchanged=True)
                    else:
                        result = self.store.load(path) if self.store else None
                        if result is None:
                            image, dependencies = self.extractor(path)
                            if not visible_image(image):
                                image = None
                            result = dict(image=image, dependencies=dependencies)
                            if self.store:
                                self.store.save(path, image, dependencies)
                        self.snapshots[key] = result['dependencies']
                    self.errors.pop(key, None)
                except Exception as error:
                    self.errors[key] = type(error).__name__+': '+str(error)
                    result = dict(error=True)
                if not self.stopped.is_set():
                    try:
                        self.ready.emit(path, dict(result, revision=revision))
                    except RuntimeError:
                        break
        finally:
            if initialized:
                ole.CoUninitialize()

    def receive(self, path, result):
        if self.stopped.is_set():
            return
        key = canonical(path)
        if not isinstance(result, dict):
            result = dict(image=result, revision=self.pending.get(key, 0))
        revision = result.get('revision', 0)
        if revision < self.revisions.get(key, 0) or (revision == 0 and key in self.pending):
            return
        self.revisions[key] = revision
        self.pending.pop(key, None)
        self.last_validation[key] = time.monotonic()
        if result.get('error'):
            return
        self.accepted.add(key)
        self.attempted.add(path)
        if result.get('unchanged'):
            return
        image = result.get('image')
        if visible_image(image):
            icon = QIcon(QPixmap.fromImage(image))
            self.images[key] = icon
            self.images[path] = icon
        else:
            for alias in list(self.images):
                if canonical(alias) == key:
                    self.images.pop(alias, None)
        self.changed.emit(path)

    def close(self):
        self.stopped.set()
