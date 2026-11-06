"""Asynchronous Dock stack previews, file-system updates and bounded thumbnail cache."""
from collections import OrderedDict
from pathlib import Path
import os
import queue
import threading
import time

from PySide6.QtCore import QObject, Signal, QFileSystemWatcher, QRectF, Qt
from PySide6.QtGui import QImageReader, QIcon, QPixmap, QColor, QPen
from file_icons import stack_icon
from folder_content import scan_directory


def canonical(path):
    # Paint-time cache lookup must not stat files or resolve junctions.
    return os.path.normcase(os.path.abspath(path))


def read_preview(path, order):
    if not Path(path).is_dir():
        return dict(directory=False, entries=[], count=0, error='')
    if order == '_folder':
        return dict(directory=True, entries=[], count=0, error='')
    entries, count, error = scan_directory(path, order, limit=3)
    result = []
    for _, name, value, directory in entries:
        image = None
        if not directory and Path(value).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}:
            reader = QImageReader(value)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(128, 128, Qt.KeepAspectRatio))
                decoded = reader.read()
                if not decoded.isNull():
                    image = decoded
        result.append(dict(path=value, name=name, directory=directory, image=image))
    return dict(directory=True, entries=result, count=count, error=error)


class StackPreviews(QObject):
    ready = Signal(str, int, object)
    changed = Signal()

    def __init__(self, parent, preferences, read=read_preview):
        super().__init__(parent)
        self.preferences = preferences
        self.read = read
        self.cache = OrderedDict()
        self.desired = {}
        self.displays = {}
        self.pending = {}
        self.generation = 0
        self.closed = threading.Event()
        self.queue = queue.Queue(maxsize=64)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.invalidate)
        self.watcher.fileChanged.connect(self.invalidate)
        self.watched_owners = {}
        self.folder_icon = stack_icon('folder', directory=True)
        self.native_icons = getattr(parent, 'application_icons', None)
        if self.native_icons:
            self.native_icons.changed.connect(self.native_icon_ready)
        self.ready.connect(self.receive)
        QImageReader.setAllocationLimit(64)
        for index in range(2):
            threading.Thread(target=self.work, daemon=True, name=f'BrisaStack-{index}').start()

    def work(self):
        while not self.closed.is_set():
            request = self.queue.get()
            if request is None:
                return
            path, order, generation = request
            try:
                result = self.read(path, order)
            except Exception:
                result = dict(directory=False, entries=[], count=0, error='No disponible')
            if not self.closed.is_set():
                try:
                    self.ready.emit(path, generation, result)
                except RuntimeError:
                    return

    def sync(self, paths):
        if self.closed.is_set():
            return
        paths = [path for path in paths if path and not path.startswith(('http:', 'https:', 'shell:', 'trash:'))]
        configurations = {canonical(path): self.preferences(path) for path in paths[:24]}
        self.displays = {path: values.get('folder_display', 'folder') for path, values in configurations.items()}
        self.desired = {path: values.get('folder_order', 'date') if self.displays[path] == 'stack' else '_folder'
                        for path, values in configurations.items()}
        for path in list(self.cache):
            if path not in self.desired:
                self.cache.pop(path, None)
                self.pending.pop(path, None)
        for path in list(self.pending):
            if path not in self.desired:
                self.pending.pop(path, None)
        now = time.monotonic()
        for path, order in self.desired.items():
            cached = self.cache.get(path)
            if cached is not None:
                cached['display'] = self.displays[path]
            if cached is None or cached['order'] != order or now-cached['time'] > 15:
                self.request(path)
        self.sync_watches()

    def request(self, path):
        if self.closed.is_set() or path not in self.desired:
            return
        order = self.desired[path]
        if self.pending.get(path, (None, None))[0] == order:
            return
        self.generation += 1
        generation = self.generation
        try:
            self.queue.put_nowait((path, order, generation))
        except queue.Full:
            return
        self.pending[path] = (order, generation)

    def invalidate(self, path):
        for owner in self.watched_owners.get(canonical(path), ()):
            # Bump the request generation, so an in-flight earlier scan is stale.
            self.pending.pop(owner, None)
            self.request(owner)

    def receive(self, path, generation, result):
        pending = self.pending.get(path)
        if self.closed.is_set() or pending is None or pending[1] != generation or path not in self.desired:
            return
        self.pending.pop(path, None)
        for entry in result['entries']:
            image = entry.pop('image', None)
            entry['icon'] = QIcon(QPixmap.fromImage(image)) if image is not None else stack_icon(entry['path'], directory=entry['directory'])
            entry['native'] = image is None and (entry['directory'] or Path(entry['path']).suffix.lower() in ('.exe', '.lnk', '.ico', '.dll'))
            if entry['native'] and self.native_icons:
                improved = self.native_icons.request(entry['path'])
                if improved is not None:
                    entry['icon'] = improved
        result.update(order=pending[0], display=self.displays[path], time=time.monotonic())
        self.cache[path] = result
        while len(self.cache) > 24:
            self.cache.popitem(last=False)
        self.sync_watches()
        self.changed.emit()

    def native_icon_ready(self, path):
        changed = False
        for result in self.cache.values():
            for entry in result['entries']:
                if entry['path'] == path and entry.get('native'):
                    icon = self.native_icons.request(path)
                    entry['icon'] = icon or stack_icon(path, directory=entry['directory'])
                    changed = True
        if changed:
            self.changed.emit()

    def sync_watches(self):
        owners = {}
        for path, result in self.cache.items():
            if result['directory'] and path in self.desired:
                for watched in [path]+[entry['path'] for entry in result['entries']]:
                    owners.setdefault(canonical(watched), set()).add(path)
        existing = {canonical(path) for path in self.watcher.directories()+self.watcher.files()}
        remove = existing-set(owners)
        if remove:
            self.watcher.removePaths(list(remove))
        add = set(owners)-existing
        if add:
            self.watcher.addPaths(list(add))
        self.watched_owners = owners

    def result(self, path):
        return self.cache.get(canonical(path))

    def draw(self, painter, rect, result):
        entries = result['entries']
        if not entries:
            self.folder_icon.paint(painter, rect.toRect())
            return
        for index in reversed(range(len(entries))):
            painter.save()
            center = rect.center()
            painter.translate(center)
            painter.rotate(-index*6)
            card = QRectF(-rect.width()/2+index*2, -rect.height()/2-index*2, rect.width(), rect.height())
            if index:
                painter.setPen(QPen(QColor(0, 0, 0, 25), 1))
                painter.setBrush(QColor(245, 248, 252))
                painter.drawRoundedRect(card.adjusted(9, 5, -9, -5), 3, 3)
            entries[index]['icon'].paint(painter, card.toRect())
            painter.restore()

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.pending.clear()
        self.cache.clear()
        watched = self.watcher.directories()+self.watcher.files()
        if watched:
            self.watcher.removePaths(watched)
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        for _ in range(2):
            self.queue.put_nowait(None)
