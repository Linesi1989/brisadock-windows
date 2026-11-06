"""Individual minimized-window tiles, stable order and bounded in-memory previews."""
from collections import OrderedDict
import time
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPen


def identity(window):
    return window['pid'], window['id'], window['key']


class MinimizedTiles:
    def __init__(self):
        self.windows = OrderedDict()
        self.frames = OrderedDict()
        self.reserved = OrderedDict()
        self.restoring = {}

    def reserve(self, window):
        key = identity(window)
        self.restoring.pop(key, None)
        if key not in self.windows or self.windows[key].get('pending_minimize'):
            self.reserved[key] = (dict(window), time.monotonic()+2)

    def update(self, windows):
        alive = {identity(window): window for window in windows}
        now = time.monotonic()
        self.restoring = {key: until for key, until in self.restoring.items()
                          if key in alive and alive[key].get('minimized') and until > now}
        minimized = {key: window for key, window in alive.items()
                     if window.get('minimized') and not window.get('hidden') and key not in self.restoring}
        for key, (window, until) in list(self.reserved.items()):
            if key in minimized or key not in alive or alive[key].get('hidden') or until <= now:
                self.reserved.pop(key, None)
            else:
                minimized[key] = dict(window, minimized=True, pending_minimize=True)
        self.windows = OrderedDict((key, minimized[key]) for key in self.windows if key in minimized)
        self.windows.update((key, window) for key, window in minimized.items() if key not in self.windows)
        # Grace period covers ShowWindowAsync and an in-flight older snapshot.
        self.frames = OrderedDict((key, value) for key, value in self.frames.items()
                                  if key in alive and (key in minimized or now-value[1] < 2))

    def remember(self, window, image, detailed=False):
        if image is None or image.isNull():
            return
        sample = image.scaled(12, 12)
        if not any(sample.pixelColor(x, y).lightness() > 2 for x in range(12) for y in range(12)):
            return
        key = identity(window)
        self.frames.pop(key, None)
        width, height = (480, 320) if detailed else (200, 140)
        preview = image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation) if image.width() > width or image.height() > height else image.copy()
        self.frames[key] = (preview, time.monotonic())
        while len(self.frames) > 32:
            self.frames.popitem(last=False)

    def forget(self, window):
        key = identity(window)
        self.frames.pop(key, None)
        self.windows.pop(key, None)
        self.reserved.pop(key, None)
        self.restoring[key] = time.monotonic()+2

    def items(self, limit=8):
        windows = list(self.windows.values())
        result = [dict(name=window['title'], path=window['path'], windows=[window],
                       minimized_window=True, window_id=identity(window)) for window in windows[:limit]]
        if len(windows) > limit:
            result.append(dict(name=f'Más ventanas minimizadas ({len(windows)-limit})', path='',
                               windows=windows[limit:], minimized_window=True, window_overflow=True,
                               window_id=('overflow',)))
        return result

    def draw(self, painter, rect, item, icon, light):
        painter.save()
        body = QRectF(rect.left()+2, rect.top()+6, rect.width()-4, rect.height()-12)
        painter.setBrush(QColor('#e5e9ef' if light else '#3c4657'))
        painter.setPen(QPen(QColor(255, 255, 255, 160), 1))
        painter.drawRoundedRect(body, 4, 4)
        image = self.frames.get(item.get('window_id'), (None, 0))[0]
        if image is not None:
            inset = body.adjusted(2, 2, -2, -2)
            fitted = image.size().scaled(inset.size().toSize(), Qt.KeepAspectRatio)
            target = QRectF(inset.center().x()-fitted.width()/2, inset.center().y()-fitted.height()/2,
                            fitted.width(), fitted.height())
            painter.drawImage(target, image)
        else:
            icon.paint(painter, body.adjusted(5, 5, -5, -5).toRect())
        if item.get('window_overflow'):
            painter.setPen(QColor('#212733' if light else '#ffffff'))
            painter.drawText(body, Qt.AlignCenter, str(len(item['windows']))+' +')
        else:
            badge = QRectF(rect.right()-rect.width()*.42, rect.bottom()-rect.height()*.40,
                           rect.width()*.42, rect.height()*.42)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#f4f5f8' if light else '#252832'))
            painter.drawRoundedRect(badge, 4, 4)
            icon.paint(painter, badge.adjusted(1, 1, -1, -1).toRect())
        painter.restore()
