"""Native window discovery/actions. No process termination or shell commands."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
from appbar import MonitorInfo


class WindowsBackend:
    status = 'Windows: integración nativa'

    def __init__(self):
        self.u = C.WinDLL('user32', use_last_error=True)
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.dwm = C.WinDLL('dwmapi')
        self.callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback_type, W.LPARAM], W.BOOL),
            'IsWindowVisible': ([W.HWND], W.BOOL),
            'IsWindow': ([W.HWND], W.BOOL),
            'IsIconic': ([W.HWND], W.BOOL),
            'IsZoomed': ([W.HWND], W.BOOL),
            'GetWindowRect': ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            'MonitorFromWindow': ([W.HWND, W.DWORD], W.HMONITOR),
            'GetMonitorInfoW': ([W.HMONITOR, C.POINTER(MonitorInfo)], W.BOOL),
            'GetWindow': ([W.HWND, W.UINT], W.HWND),
            'GetWindowTextLengthW': ([W.HWND], C.c_int),
            'GetWindowTextW': ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            'GetWindowThreadProcessId': ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            'GetWindowLongW': ([W.HWND, C.c_int], W.LONG),
            'GetForegroundWindow': ([], W.HWND),
            'ShowWindowAsync': ([W.HWND, C.c_int], W.BOOL),
            'SetForegroundWindow': ([W.HWND], W.BOOL),
            'PostMessageW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.u, name)
            fn.argtypes, fn.restype = args, result
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = W.LONG
        from hidden_windows import HiddenWindows
        self.hidden = HiddenWindows(self)

    def snapshot(self):
        self.hidden.prune()
        result = []
        foreground = self.u.GetForegroundWindow()
        def visit(hwnd, _):
            pid = W.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
            managed = self.hidden.tracked(dict(pid=pid.value, id=int(hwnd)))
            if not self.u.IsWindowVisible(hwnd) and not managed:
                return True
            # Tool windows and owned dialogs are represented by their parent app.
            if self.u.GetWindowLongW(hwnd, -20) & 0x80 or self.u.GetWindow(hwnd, 4):
                return True
            cloaked = W.DWORD()
            self.dwm.DwmGetWindowAttribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked))
            if cloaked.value:
                return True
            count = self.u.GetWindowTextLengthW(hwnd)
            if not count:
                return True
            title = C.create_unicode_buffer(count + 1)
            self.u.GetWindowTextW(hwnd, title, count + 1)
            pid = W.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
            if pid.value == os.getpid():
                return True
            handle = self.k.OpenProcess(0x1000, False, pid.value)
            if not handle:
                return True
            try:
                buffer = C.create_unicode_buffer(32768)
                length = W.DWORD(len(buffer))
                if not self.k.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(length)):
                    return True
                path = buffer.value
            finally:
                self.k.CloseHandle(handle)
            fullscreen = False
            monitor = self.u.MonitorFromWindow(hwnd, 2)
            if hwnd == foreground and not self.u.IsIconic(hwnd) and not self.u.IsZoomed(hwnd):
                bounds = W.RECT()
                info = MonitorInfo()
                info.cbSize = C.sizeof(info)
                if self.u.GetWindowRect(hwnd, C.byref(bounds)) and self.u.GetMonitorInfoW(monitor, C.byref(info)):
                    full = info.monitor
                    fullscreen = (bounds.left <= full.left+1 and bounds.top <= full.top+1 and
                                  bounds.right >= full.right-1 and bounds.bottom >= full.bottom-1 and
                                  not self.u.GetWindowLongW(hwnd, -16) & 0x00c00000)
            result.append(dict(id=int(hwnd), pid=pid.value, title=title.value,
                               path=path, key=os.path.normcase(path), name=Path(path).stem,
                               minimized=bool(self.u.IsIconic(hwnd)), active=hwnd == foreground,
                               hidden=managed and not bool(self.u.IsWindowVisible(hwnd)),
                               fullscreen=fullscreen, monitor=monitor))
            return True
        callback = self.callback_type(visit)
        self.u.EnumWindows(callback, 0)
        return result

    def valid(self, window):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(window['id'], C.byref(pid))
        return self.u.IsWindow(window['id']) and pid.value == window['pid']

    def activate(self, window):
        if not self.valid(window):
            return False
        if not self.hidden.show(window):
            return False
        if self.u.IsIconic(window['id']):
            self.u.ShowWindowAsync(window['id'], 9)
        return bool(self.u.SetForegroundWindow(window['id']))

    def minimize(self, window):
        if self.valid(window):
            return bool(self.u.ShowWindowAsync(window['id'], 6))
        return False

    def close_window(self, window):
        if self.valid(window):
            return bool(self.u.PostMessageW(window['id'], 0x10, 0, 0))
        return False

    def close(self):
        self.hidden.restore_all()
