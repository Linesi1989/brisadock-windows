"""Optional Shell attention notifications; observes only this desktop."""
import ctypes as C
from ctypes import wintypes as W
import os


class ShellAttention:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.user = C.WinDLL('user32')
        self.user.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
        self.user.RegisterWindowMessageW.restype = W.UINT
        self.user.RegisterShellHookWindow.argtypes = [W.HWND]
        self.user.RegisterShellHookWindow.restype = W.BOOL
        self.user.DeregisterShellHookWindow.argtypes = [W.HWND]
        self.user.DeregisterShellHookWindow.restype = W.BOOL
        self.user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.user.GetWindowThreadProcessId.restype = W.DWORD
        self.message = self.user.RegisterWindowMessageW('SHELLHOOK')
        self.available = bool(self.user.RegisterShellHookWindow(hwnd))

    def identity(self, hwnd):
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if not pid.value or pid.value == os.getpid():
            return None
        return pid.value, int(hwnd)

    def close(self):
        if self.available:
            self.user.DeregisterShellHookWindow(self.hwnd)
            self.available = False
