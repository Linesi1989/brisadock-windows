"""Optional DWM shrink animation for minimize requests made through Brisa."""
import ctypes as C
from ctypes import wintypes as W
import time

from PySide6.QtCore import Qt, QRect, QTimer, Signal
from PySide6.QtWidgets import QWidget
from native_previews import DwmThumbnails, ThumbnailProperties


class MinimizeEffect(QWidget):
    finished = Signal()

    def __init__(self, backend, window, target, screen, destination_hwnd, reverse=False, origin=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet('background: #202938;')
        self.backend, self.window, self.target, self.screen = backend, window, target, screen
        self.destination_hwnd = destination_hwnd
        self.reverse = reverse
        self.saved_origin = QRect(origin) if origin is not None else None
        self.thumbnails = None
        self.restore_transitions = None
        self.progress = 0.
        self.started = False
        self.completed = False
        self.suppress_restore = False
        self.finishing = False
        self.target_provider = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)

    def begin(self):
        if not self.backend.valid(self.window):
            return False
        minimized = bool(self.backend.u.IsIconic(self.window['id']))
        if (not self.reverse and minimized) or (self.reverse and (not minimized or self.saved_origin is None)):
            return False
        api = self.backend.u
        api.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        api.GetWindowRect.restype = W.BOOL
        rect = W.RECT()
        if not api.GetWindowRect(self.window['id'], C.byref(rect)):
            return False
        # Windows physical coordinates -> the chosen Qt screen's logical space.
        from appbar import MonitorInfo
        api.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
        api.MonitorFromWindow.restype = W.HMONITOR
        if api.MonitorFromWindow(self.window['id'], 2) != api.MonitorFromWindow(self.destination_hwnd, 2):
            return False
        api.GetMonitorInfoW.argtypes = [W.HMONITOR, C.POINTER(MonitorInfo)]
        api.GetMonitorInfoW.restype = W.BOOL
        monitor = MonitorInfo()
        monitor.cbSize = C.sizeof(monitor)
        if not api.GetMonitorInfoW(api.MonitorFromWindow(self.window['id'], 2), C.byref(monitor)):
            return False
        # Cross-screen animation can cross DPI domains: leave that case to Windows.
        geometry = self.screen.geometry()
        ratio = self.screen.devicePixelRatio()
        if abs(round(geometry.width()*ratio) - (monitor.monitor.right-monitor.monitor.left)) > 2:
            return False
        self.origin = QRect(round(geometry.x()+(rect.left-monitor.monitor.left)/ratio),
                            round(geometry.y()+(rect.top-monitor.monitor.top)/ratio),
                            round((rect.right-rect.left)/ratio), round((rect.bottom-rect.top)/ratio))
        if self.reverse:
            self.origin = self.saved_origin
        if self.origin.width() < 20 or self.origin.height() < 20:
            return False
        self.setGeometry(self.target if self.reverse else self.origin)
        self.show()
        self.thumbnails = DwmThumbnails(int(self.winId()))
        if not self.thumbnails.add(self.window['id'], [0, 0, round(self.width()*ratio), round(self.height()*ratio)]):
            self.close()
            return False
        self.backend.dwm.DwmSetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
        self.backend.dwm.DwmSetWindowAttribute.restype = W.LONG
        previous = W.BOOL()
        # Only override transitions if Windows lets us read and restore the old state.
        if self.backend.dwm.DwmGetWindowAttribute(self.window['id'], 3, C.byref(previous), C.sizeof(previous)) == 0:
            disabled = W.BOOL(True)
            if self.backend.dwm.DwmSetWindowAttribute(self.window['id'], 3, C.byref(disabled), C.sizeof(disabled)) == 0:
                self.restore_transitions = previous.value
        if not self.reverse:
            self.backend.minimize(self.window)
        self.started = True
        self.progress = 1. if self.reverse else 0.
        self.segment_progress = self.progress
        self.segment_origin = QRect(self.geometry())
        self.duration = .34
        self.start_time = time.monotonic()
        self.timer.start(16)
        return True

    def advance(self):
        if not self.backend.valid(self.window) or not self.backend.u.IsIconic(self.window['id']):
            self.suppress_restore = True
            self.close()
            return
        if self.target_provider:
            target = self.target_provider()
            if target is not None:
                self.target = QRect(target)
        progress = min(1., (time.monotonic()-self.start_time)/self.duration)
        eased = 1-(1-progress)**3
        end_progress = 0. if self.reverse else 1.
        self.progress = self.segment_progress+(end_progress-self.segment_progress)*eased
        def lerp(a, b):
            return round(a+(b-a)*eased)
        start, end = self.segment_origin, self.origin if self.reverse else self.target
        bounds = QRect(lerp(start.x(), end.x()), lerp(start.y(), end.y()),
                       max(2, lerp(start.width(), end.width())),
                       max(2, lerp(start.height(), end.height())))
        self.setGeometry(bounds)
        if self.thumbnails and self.thumbnails.handles:
            ratio = self.devicePixelRatioF()
            properties = ThumbnailProperties()
            properties.flags = 1 | 4 | 8
            properties.destination = W.RECT(0, 0, round(bounds.width()*ratio), round(bounds.height()*ratio))
            properties.opacity = round(255*(1-self.progress*.25))
            properties.visible = True
            self.thumbnails.api.DwmUpdateThumbnailProperties(self.thumbnails.handles[0], C.byref(properties))
        if progress >= 1 or not self.backend.valid(self.window):
            self.completed = True
            if self.reverse and self.backend.valid(self.window):
                self.backend.activate(self.window)
            self.close()

    def redirect(self, reverse):
        if not self.started or self.completed or self.finishing or not self.backend.valid(self.window):
            return False
        if self.reverse == reverse:
            return True
        self.segment_origin = QRect(self.geometry())
        self.segment_progress = self.progress
        self.reverse = reverse
        distance = abs((0. if reverse else 1.)-self.progress)
        self.duration = max(.08, .34*distance)
        self.start_time = time.monotonic()
        return True

    def closeEvent(self, event):
        if self.finishing:
            return super().closeEvent(event)
        self.finishing = True
        self.timer.stop()
        if self.thumbnails:
            self.thumbnails.clear()
        if self.restore_transitions is not None and self.backend.valid(self.window):
            value = W.BOOL(self.restore_transitions)
            self.backend.dwm.DwmSetWindowAttribute(self.window['id'], 3, C.byref(value), C.sizeof(value))
        if (self.started and self.reverse and not self.completed and not self.suppress_restore
                and self.backend.valid(self.window) and self.backend.u.IsIconic(self.window['id'])):
            self.backend.activate(self.window)
        self.finished.emit()
        super().closeEvent(event)
