"""Single-frame curved contraction. Images stay in application memory only."""
import time
import ctypes as C
from ctypes import wintypes as W
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget
from sheet_geometry import sheet_mesh, canonical


class SheetEffect(QWidget):
    finished = Signal()

    def __init__(self, backend, window, target, screen, origin, image, edge='bottom', reverse=False):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setWindowTitle('Brisa — Animación')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.backend, self.window, self.target, self.screen = backend, window, target, screen
        self.origin, self.image, self.edge, self.reverse = QRect(origin), image, edge, reverse
        self.started, self.completed, self.progress = False, False, 1. if reverse else 0.
        self.suppress_restore = False
        self.restore_transitions = None
        self.finishing = False
        self.target_provider = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.box = self.origin.united(self.target).adjusted(-2, -2, 2, 2)
        self.setGeometry(self.box)

    def begin(self):
        if self.image is None or self.image.isNull() or not self.backend.valid(self.window):
            return False
        if bool(self.backend.u.IsIconic(self.window['id'])) != self.reverse:
            return False
        _, oy, _, oh = canonical(self.origin.getRect(), self.edge)
        _, ty, _, th = canonical(self.target.getRect(), self.edge)
        if ty+th < oy+oh*.6:
            return False
        self.show()
        self.backend.dwm.DwmSetWindowAttribute.argtypes=[W.HWND,W.DWORD,C.c_void_p,W.DWORD]
        self.backend.dwm.DwmSetWindowAttribute.restype=W.LONG
        previous=W.BOOL()
        if self.backend.dwm.DwmGetWindowAttribute(self.window['id'],3,C.byref(previous),C.sizeof(previous))==0:
            disabled=W.BOOL(True)
            if self.backend.dwm.DwmSetWindowAttribute(self.window['id'],3,C.byref(disabled),C.sizeof(disabled))==0:
                self.restore_transitions=previous.value
        if not self.reverse:
            self.backend.minimize(self.window)
        self.started = True
        self.segment_progress = self.progress
        self.duration = .55
        self.start_time = time.monotonic()
        self.timer.start(16)
        self.update()
        return True

    def advance(self):
        if not self.backend.valid(self.window) or not self.backend.u.IsIconic(self.window['id']):
            self.suppress_restore = True
            self.close()
            return
        if self.target_provider:
            target = self.target_provider()
            if target is not None and target != self.target:
                self.target = QRect(target)
                self.box = self.origin.united(self.target).adjusted(-2, -2, 2, 2)
                self.setGeometry(self.box)
        elapsed = min(1., (time.monotonic()-self.start_time)/self.duration)
        end_progress = 0. if self.reverse else 1.
        self.progress = self.segment_progress+(end_progress-self.segment_progress)*elapsed
        self.update()
        if elapsed >= 1 or not self.backend.valid(self.window):
            self.completed = True
            if self.reverse and self.backend.valid(self.window):
                self.backend.activate(self.window)
            self.close()

    def redirect(self, reverse):
        if not self.started or self.completed or self.finishing or not self.backend.valid(self.window):
            return False
        if self.reverse == reverse:
            return True
        self.segment_progress = self.progress
        self.reverse = reverse
        self.duration = max(.08, .55*abs((0. if reverse else 1.)-self.progress))
        self.start_time = time.monotonic()
        return True

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for v0, v1, points in sheet_mesh(self.origin.getRect(), self.target.getRect(), self.progress, self.edge, overlap=.0015):
            if self.edge == 'bottom':
                source = QRectF(0, v0*self.image.height(), self.image.width(), (v1-v0)*self.image.height())
            elif self.edge == 'right':
                source = QRectF(v0*self.image.width(), 0, (v1-v0)*self.image.width(), self.image.height())
            else:
                source = QRectF((1-v1)*self.image.width(), 0, (v1-v0)*self.image.width(), self.image.height())
            destination = QPolygonF([QPointF(x-self.box.x(), y-self.box.y()) for x, y in points])
            src = QPolygonF([source.topLeft(), source.topRight(), source.bottomRight(), source.bottomLeft()])
            transform = QTransform()
            if not QTransform.quadToQuad(src, destination, transform):
                continue
            clip = QPainterPath()
            clip.addPolygon(destination)
            painter.save()
            painter.setClipPath(clip)
            painter.setTransform(transform)
            painter.drawImage(source, self.image, source)
            painter.restore()

    def closeEvent(self, event):
        if self.finishing:
            return super().closeEvent(event)
        self.finishing = True
        self.timer.stop()
        if self.restore_transitions is not None and self.backend.valid(self.window):
            previous=W.BOOL(self.restore_transitions)
            self.backend.dwm.DwmSetWindowAttribute(self.window['id'],3,C.byref(previous),C.sizeof(previous))
        if (self.started and self.reverse and not self.completed and not self.suppress_restore
                and self.backend.valid(self.window) and self.backend.u.IsIconic(self.window['id'])):
            self.backend.activate(self.window)
        self.finished.emit()
        super().closeEvent(event)
