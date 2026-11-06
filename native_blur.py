"""Optional Windows accent blur on a separate non-activating background HWND.

SetWindowCompositionAttribute is a compatibility API, so this effect is opt-in.
The normal Qt alpha background is always retained as the fallback.
"""
import ctypes as C
from ctypes import wintypes as W
import os
import sys
import time
from PySide6.QtCore import QObject, QTimer, QRectF
from PySide6.QtWidgets import QApplication


class WindowClass(C.Structure):
    _fields_ = [('size', W.UINT), ('style', W.UINT), ('procedure', C.c_void_p),
                ('class_extra', C.c_int), ('window_extra', C.c_int), ('instance', W.HINSTANCE),
                ('icon', W.HICON), ('cursor', W.HANDLE), ('background', W.HBRUSH),
                ('menu', W.LPCWSTR), ('name', W.LPCWSTR), ('small_icon', W.HICON)]


class Margins(C.Structure):
    _fields_ = [(name, C.c_int) for name in ('left', 'right', 'top', 'bottom')]


class Accent(C.Structure):
    _fields_ = [('state', C.c_int), ('flags', W.DWORD), ('color', W.DWORD), ('animation', W.DWORD)]


class CompositionData(C.Structure):
    _fields_ = [('attribute', C.c_int), ('data', C.c_void_p), ('size', C.c_size_t)]


def system_allows_blur():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            if not winreg.QueryValueEx(key, 'EnableTransparency')[0]:
                return False
    except OSError:
        pass
    user = C.WinDLL('user32')
    if user.GetSystemMetrics(0x1000):
        return False
    class Contrast(C.Structure):
        _fields_ = [('size', W.UINT), ('flags', W.DWORD), ('scheme', W.LPWSTR)]
    contrast = Contrast(C.sizeof(Contrast), 0, None)
    user.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, C.c_void_p, W.UINT]
    if user.SystemParametersInfoW(0x42, contrast.size, C.byref(contrast), 0) and contrast.flags & 1:
        return False
    return True


class NativeBlurShelf(QObject):
    def __init__(self, dock):
        super().__init__(dock)
        self.dock = dock
        self.hwnd = None
        self.registered = False
        self.failed = False
        self.closed = False
        self.visible = False
        self.bounds = None
        self.rectangle = QRectF()
        self.policy_time = -3.
        self.policy_allowed = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(lambda: self.sync(self.rectangle))
        self.supported = (os.name == 'nt' and QApplication.platformName() != 'offscreen'
                          and sys.getwindowsversion().build >= 22621)
        if not self.supported:
            return
        self.user, self.kernel = C.WinDLL('user32'), C.WinDLL('kernel32')
        self.dwm, self.gdi = C.WinDLL('dwmapi'), C.WinDLL('gdi32')
        if not hasattr(self.user, 'SetWindowCompositionAttribute'):
            self.supported = False
            return
        signatures = {
            'DefWindowProcW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], C.c_ssize_t),
            'RegisterClassExW': ([C.POINTER(WindowClass)], W.ATOM),
            'UnregisterClassW': ([W.LPCWSTR, W.HINSTANCE], W.BOOL),
            'CreateWindowExW': ([W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p], W.HWND),
            'SetWindowCompositionAttribute': ([W.HWND,C.POINTER(CompositionData)], W.BOOL),
            'SetWindowPos': ([W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT],W.BOOL),
            'SetWindowRgn': ([W.HWND,W.HRGN,W.BOOL],C.c_int),
            'ClientToScreen': ([W.HWND,C.POINTER(W.POINT)],W.BOOL),
            'ShowWindow': ([W.HWND,C.c_int],W.BOOL),
            'DestroyWindow': ([W.HWND],W.BOOL),
        }
        for name,(arguments,result) in signatures.items():
            fn=getattr(self.user,name);fn.argtypes,fn.restype=arguments,result
        self.kernel.GetModuleHandleW.argtypes=[W.LPCWSTR]
        self.kernel.GetModuleHandleW.restype=W.HMODULE
        self.instance=self.kernel.GetModuleHandleW(None)
        self.name='BrisaBlur_'+str(id(self))
        prototype=C.WINFUNCTYPE(C.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        def callback(hwnd,message,wparam,lparam):
            if message==0x84:return -1  # HTTRANSPARENT
            if message==0x21:return 3  # MA_NOACTIVATE
            if message==0x14:return 1  # no opaque background erase
            return self.user.DefWindowProcW(hwnd,message,wparam,lparam)
        self.procedure=prototype(callback)
        self.dwm.DwmExtendFrameIntoClientArea.argtypes=[W.HWND,C.POINTER(Margins)]
        self.dwm.DwmExtendFrameIntoClientArea.restype=W.LONG
        self.gdi.CreateRoundRectRgn.argtypes=[C.c_int]*6
        self.gdi.CreateRoundRectRgn.restype=W.HRGN
        self.gdi.DeleteObject.argtypes=[W.HANDLE]

    def create(self):
        definition=WindowClass()
        definition.size=C.sizeof(definition)
        definition.procedure=C.cast(self.procedure,C.c_void_p)
        definition.instance=self.instance
        definition.name=self.name
        if not self.user.RegisterClassExW(C.byref(definition)):
            return False
        self.registered=True
        self.hwnd=self.user.CreateWindowExW(0x08000000|0x80|0x20,self.name,'Brisa Blur',
                                            0x80000000,0,0,1,1,None,None,self.instance,None)
        if not self.hwnd:
            self.destroy_surface()
            return False
        margins=Margins(-1,-1,-1,-1)
        if self.dwm.DwmExtendFrameIntoClientArea(self.hwnd,C.byref(margins))!=0:
            self.destroy_surface()
            return False
        policy=Accent(4,0,0x201c1c1c,0)
        data=CompositionData(19,C.cast(C.pointer(policy),C.c_void_p),C.sizeof(policy))
        if not self.user.SetWindowCompositionAttribute(self.hwnd,C.byref(data)):
            self.destroy_surface()
            return False
        return True

    def sync(self, rectangle):
        if self.closed:
            return
        self.rectangle=QRectF(rectangle)
        if not self.dock.blur_enabled or not self.supported:
            self.timer.stop()
            self.destroy_surface()
            self.failed=False
            return
        self.timer.start() if not self.timer.isActive() else None
        if time.monotonic()-self.policy_time>=2:
            self.policy_allowed=system_allows_blur()
            self.policy_time=time.monotonic()
        if not self.policy_allowed:
            self.destroy_surface()
            return
        if self.failed or self.rectangle.isEmpty():
            return
        if not self.dock.isVisible() or self.dock.hidden_amount>=.98 or getattr(self.dock,'closing',False):
            self.hide()
            return
        if not self.hwnd and not self.create():
            self.failed=True
            return
        ratio=self.dock.dock_screen.devicePixelRatio()
        origin=W.POINT()
        if not self.user.ClientToScreen(int(self.dock.winId()),C.byref(origin)):
            self.hide()
            return
        bounds=(origin.x+round(rectangle.x()*ratio),origin.y+round(rectangle.y()*ratio),
                max(1,round(rectangle.width()*ratio)),max(1,round(rectangle.height()*ratio)))
        if self.bounds!=bounds or not self.visible:
            x,y,width,height=bounds
            if self.bounds is None or self.bounds[2:]!=bounds[2:]:
                diameter=min(round(42*ratio), width, height)
                region=self.gdi.CreateRoundRectRgn(0,0,width+1,height+1,diameter,diameter)
                if not region or not self.user.SetWindowRgn(self.hwnd,region,False):
                    if region:self.gdi.DeleteObject(region)
                    self.destroy_surface();self.failed=True
                    return
            # Unowned, directly behind Dock; no activation, taskbar button or input.
            if not self.user.SetWindowPos(self.hwnd,int(self.dock.winId()),x,y,width,height,0x10|0x40):
                self.destroy_surface();self.failed=True
                return
            self.bounds,self.visible=bounds,True

    def hide(self):
        if self.hwnd:self.user.ShowWindow(self.hwnd,0)
        self.visible=False

    def destroy_surface(self):
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd)
            self.hwnd=None
        if self.registered:
            self.user.UnregisterClassW(self.name,self.instance)
            self.registered=False
        self.visible=False
        self.bounds=None

    def invalidate(self):
        """Retry composition after display, resume or DWM reset notifications."""
        if self.closed:
            return
        self.destroy_surface()
        self.failed=False
        self.policy_time=-3.

    def close(self):
        self.closed=True
        self.timer.stop()
        self.destroy_surface()
