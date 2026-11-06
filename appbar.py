"""Optional Windows appbar reservation; never changes taskbar settings."""
import ctypes as C
from ctypes import wintypes as W
from PySide6.QtCore import Qt, QRect
from PySide6.QtWidgets import QWidget


class AppBarData(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('callback', W.UINT),
                ('edge', W.UINT), ('rect', W.RECT), ('param', W.LPARAM)]


class MonitorInfo(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]


class AppBarHost(QWidget):
    def __init__(self, dock):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.dock = dock
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.shell = C.WinDLL('shell32')
        self.user = C.WinDLL('user32')
        self.shell.SHAppBarMessage.argtypes = [W.DWORD, C.POINTER(AppBarData)]
        self.shell.SHAppBarMessage.restype = C.c_size_t
        self.user.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
        self.user.RegisterWindowMessageW.restype = W.UINT
        self.user.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
        self.user.MonitorFromWindow.restype = W.HMONITOR
        self.user.GetMonitorInfoW.argtypes = [W.HMONITOR, C.POINTER(MonitorInfo)]
        self.user.GetMonitorInfoW.restype = W.BOOL
        self.message = self.user.RegisterWindowMessageW('BrisaDock.AppBar.Notification')
        self.taskbar_message = self.user.RegisterWindowMessageW('TaskbarCreated')
        self.registered = False
        self.rect = None
        self.last_position = None
        self.result_codes = {}
        self.data = AppBarData()
        self.data.cbSize = C.sizeof(AppBarData)
        self.data.hWnd = int(self.winId())
        self.data.callback = self.message

    def reserve(self, edge, thickness):
        info = MonitorInfo()
        info.cbSize = C.sizeof(info)
        monitor = self.user.MonitorFromWindow(int(self.dock.winId()), 2)
        if not self.user.GetMonitorInfoW(monitor, C.byref(info)):
            return False
        if not self.registered:
            self.resize(1, 1)
            self.move(self.dock.pos())
            self.show()
            code = self.shell.SHAppBarMessage(0, C.byref(self.data))
            self.result_codes['register'] = code
            if not code:
                self.hide()
                return False
            self.registered = True
        self.data.edge = {'left':0, 'right':2, 'bottom':3}[edge]
        self.data.rect = info.monitor
        self.shell.SHAppBarMessage(2, C.byref(self.data))
        ratio = self.dock.dock_screen.devicePixelRatio()
        pixels = round(thickness*ratio)
        rect = self.data.rect
        if edge == 'left':
            rect.right = rect.left + pixels
        elif edge == 'right':
            rect.left = rect.right - pixels
        else:
            rect.top = rect.bottom - pixels
        position = (edge, rect.left, rect.top, rect.right, rect.bottom)
        if self.last_position != position:
            code = self.shell.SHAppBarMessage(3, C.byref(self.data))
            self.result_codes['position'] = code
            if not code:
                self.release()
                return False
            self.last_position = position
        logical = self.dock.dock_screen.geometry()
        self.rect = QRect(round(logical.left()+(rect.left-info.monitor.left)/ratio),
                          round(logical.top()+(rect.top-info.monitor.top)/ratio),
                          round((rect.right-rect.left)/ratio), round((rect.bottom-rect.top)/ratio))
        if self.geometry() != self.rect:
            self.setGeometry(self.rect)
        self.show()
        return True

    def release(self):
        if self.registered:
            self.shell.SHAppBarMessage(1, C.byref(self.data))
        self.registered = False
        self.rect = None
        self.last_position = None
        self.hide()

    def nativeEvent(self, event_type, message):
        if hasattr(self, 'message'):
            native = W.MSG.from_address(int(message))
            if native.message == self.message and native.wParam == 1 and self.registered:
                self.dock.schedule_geometry()
            elif native.message == self.taskbar_message:
                self.registered = False
                self.rect = None
                self.last_position = None
                self.dock.schedule_geometry()
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event):
        self.release()
        super().closeEvent(event)
