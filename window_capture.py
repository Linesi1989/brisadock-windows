"""Bounded, on-demand capture of one explicitly selected window, in memory."""
import ctypes as C
from ctypes import wintypes as W
import sys
import struct
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QRect, QProcess, QTimer, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage
from appbar import MonitorInfo


class BitmapHeader(C.Structure):
    _fields_ = [('size', W.DWORD), ('width', W.LONG), ('height', W.LONG), ('planes', W.WORD),
                ('bits', W.WORD), ('compression', W.DWORD), ('image_size', W.DWORD),
                ('xppm', W.LONG), ('yppm', W.LONG), ('used', W.DWORD), ('important', W.DWORD)]


class BitmapInfo(C.Structure):
    _fields_ = [('header', BitmapHeader), ('color', W.DWORD)]


def logical_bounds(backend, window, screen, destination_hwnd):
    if not backend.valid(window) or backend.u.IsIconic(window['id']):
        return None
    user = backend.u
    user.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
    user.MonitorFromWindow.restype = W.HMONITOR
    user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    user.GetMonitorInfoW.argtypes = [W.HMONITOR, C.POINTER(MonitorInfo)]
    monitor = user.MonitorFromWindow(window['id'], 2)
    if monitor != user.MonitorFromWindow(destination_hwnd, 2):
        return None
    info = MonitorInfo()
    info.cbSize = C.sizeof(info)
    rect = W.RECT()
    if not user.GetMonitorInfoW(monitor, C.byref(info)) or not user.GetWindowRect(window['id'], C.byref(rect)):
        return None
    ratio = screen.devicePixelRatio()
    base = screen.geometry()
    return QRect(round(base.x()+(rect.left-info.monitor.left)/ratio), round(base.y()+(rect.top-info.monitor.top)/ratio),
                 round((rect.right-rect.left)/ratio), round((rect.bottom-rect.top)/ratio))


def _capture_window(window):
    user, gdi = C.WinDLL('user32'), C.WinDLL('gdi32')
    user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    user.GetWindowRect.restype = W.BOOL
    user.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
    user.PrintWindow.restype = W.BOOL
    pid = W.DWORD()
    user.GetWindowThreadProcessId(window['id'], C.byref(pid))
    rect = W.RECT()
    if pid.value != window['pid'] or not user.GetWindowRect(window['id'], C.byref(rect)):
        return None
    width, height = rect.right-rect.left, rect.bottom-rect.top
    if width < 32 or height < 32 or width*height > 4_000_000:
        return None
    gdi.CreateCompatibleDC.argtypes = [W.HDC]
    gdi.CreateCompatibleDC.restype = W.HDC
    gdi.CreateDIBSection.argtypes = [W.HDC, C.POINTER(BitmapInfo), W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]
    gdi.CreateDIBSection.restype = W.HBITMAP
    gdi.SelectObject.argtypes = [W.HDC, W.HANDLE]
    gdi.SelectObject.restype = W.HANDLE
    gdi.DeleteObject.argtypes = [W.HANDLE]
    gdi.DeleteDC.argtypes = [W.HDC]
    dc = gdi.CreateCompatibleDC(None)
    if not dc:
        return None
    bitmap, previous = None, None
    try:
        info = BitmapInfo()
        info.header = BitmapHeader(C.sizeof(BitmapHeader), width, -height, 1, 32, 0, width*height*4, 0, 0, 0, 0)
        pointer = C.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, C.byref(info), 0, C.byref(pointer), None, 0)
        if not bitmap or not pointer.value:
            return None
        previous = gdi.SelectObject(dc, bitmap)
        if not user.PrintWindow(window['id'], dc, 0):
            return None
        gdi.GdiFlush()
        user.GetWindowThreadProcessId(window['id'], C.byref(pid))
        if pid.value != window['pid']:
            return None
        data = C.string_at(pointer, width*height*4)
        return QImage(data, width, height, width*4, QImage.Format_RGB32).copy()
    finally:
        if previous:
            gdi.SelectObject(dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        gdi.DeleteDC(dc)


def capture_window(window):
    # Worker threads do not inherit Qt's thread-specific DPI awareness.
    # PrintWindow and geometry validation must use physical pixels consistently.
    user=C.WinDLL('user32')
    user.SetThreadDpiAwarenessContext.argtypes=[C.c_void_p]
    user.SetThreadDpiAwarenessContext.restype=C.c_void_p
    previous=user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
    try:
        return _capture_window(window)
    finally:
        if previous:user.SetThreadDpiAwarenessContext(previous)


MAX_FRAME = 24*1024*1024


def capture_server():
    """Private pipe protocol; no QApplication, profile, disk images or GUI hooks."""
    import os
    import msvcrt
    kernel = C.WinDLL('kernel32')
    kernel.GetStdHandle.argtypes = [W.DWORD]
    kernel.GetStdHandle.restype = W.HANDLE
    # PyInstaller windowed builds set sys.stdin/stdout to None despite QProcess pipes.
    incoming = sys.stdin.buffer if sys.stdin is not None else os.fdopen(
        msvcrt.open_osfhandle(kernel.GetStdHandle(0xfffffff6), os.O_RDONLY | os.O_BINARY), 'rb')
    stream = sys.stdout.buffer if sys.stdout is not None else os.fdopen(
        msvcrt.open_osfhandle(kernel.GetStdHandle(0xfffffff5), os.O_WRONLY | os.O_BINARY), 'wb')
    def send(image):
        data = QByteArray()
        if image is not None and not image.isNull():
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, 'PNG')
        payload = bytes(data)
        if len(payload) > MAX_FRAME:
            payload = b''
        stream.write(b'BRCP'+struct.pack('<I', len(payload))+payload)
        stream.flush()
    send(None)  # Ready handshake; later empty frames mean capture failed.
    for line in incoming:
        try:
            import json
            window = json.loads(line)
            if (not isinstance(window, dict) or set(window) != {'id', 'pid'}
                    or any(type(value) is not int or value <= 0 for value in window.values())):
                return 2
            send(capture_window(window))
        except Exception:
            send(None)
    return 0


class CaptureBroker(QObject):
    ready = Signal(int, object)

    def __init__(self, parent=None, command=None, timeout_ms=300, startup_ms=2000):
        super().__init__(parent)
        self.command = command or ([sys.executable, '--capture-server'] if getattr(sys, 'frozen', False)
                                    else [sys.executable, str(Path(__file__).resolve()), '--capture-server'])
        self.timeout_ms, self.startup_ms = timeout_ms, startup_ms
        self.busy = False
        self.counter = 0
        self.process = None
        self.server_ready = False
        self.pending = None
        self.buffer = bytearray()
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expire)

    def warm(self):
        if self.closed or self.process is not None:
            return
        process = QProcess(self)
        self.process = process
        self.server_ready = False
        self.buffer.clear()
        process.readyReadStandardOutput.connect(lambda: self.read(process))
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda *_: self.exited(process))
        process.errorOccurred.connect(lambda *_: self.failed(process))
        self.timer.start(self.startup_ms)
        process.start(self.command[0], self.command[1:])

    def request(self, window):
        if self.closed or self.busy:
            return None
        self.busy = True
        self.counter += 1
        self.pending = {'id': int(window['id']), 'pid': int(window['pid'])}
        self.warm()
        if self.server_ready:
            self.submit()
        return self.counter

    def submit(self):
        if self.process is None or self.pending is None:
            return
        import json
        self.process.write(json.dumps(self.pending).encode('ascii')+b'\n')
        self.pending = None
        self.timer.start(self.timeout_ms)

    def read(self, process):
        if process is not self.process:
            return
        self.buffer.extend(bytes(process.readAllStandardOutput()))
        if len(self.buffer) > MAX_FRAME+8:
            self.expire()
            return
        while len(self.buffer) >= 8:
            if self.buffer[:4] != b'BRCP':
                self.expire()
                return
            size = struct.unpack('<I', self.buffer[4:8])[0]
            if size > MAX_FRAME:
                self.expire()
                return
            if len(self.buffer) < size+8:
                return
            payload = bytes(self.buffer[8:size+8])
            del self.buffer[:size+8]
            self.timer.stop()
            if not self.server_ready:
                if payload:
                    self.expire()
                    return
                self.server_ready = True
                self.submit()
                continue
            image = QImage.fromData(payload, 'PNG') if payload else None
            if image is not None and (image.isNull() or image.width()*image.height() > 4_000_000):
                image = None
            self.complete(image)

    def complete(self, image):
        self.timer.stop()
        self.pending = None
        if self.busy:
            token = self.counter
            self.busy = False
            self.ready.emit(token, image)

    def retire(self):
        process, self.process = self.process, None
        self.server_ready = False
        self.buffer.clear()
        if process is not None:
            process.finished.connect(process.deleteLater)
            if process.state() != QProcess.NotRunning:
                process.kill()  # Only this broker's private capture helper.
            else:
                process.deleteLater()

    def expire(self):
        was_ready = self.server_ready
        self.retire()
        self.complete(None)
        if was_ready and not self.closed:
            QTimer.singleShot(0, self.warm)

    def exited(self, process):
        if process is self.process:
            self.retire()
            self.complete(None)

    def failed(self, process):
        if process is self.process:
            self.retire()
            self.complete(None)

    def close(self):
        self.closed = True
        self.timer.stop()
        self.busy = False
        self.pending = None
        self.retire()


if __name__ == '__main__':
    sys.exit(capture_server() if sys.argv[1:] == ['--capture-server'] else 2)
