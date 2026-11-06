"""Route standard minimize-button clicks to Brisa; no DLL injection or global settings."""
import ctypes as C
from ctypes import wintypes as W
import threading
import time
from PySide6.QtCore import QObject, Signal, QTimer


class MouseInfo(C.Structure):
    _fields_ = [('point',W.POINT),('data',W.DWORD),('flags',W.DWORD),
                ('time',W.DWORD),('extra',C.c_size_t)]


class TitleBarInfo(C.Structure):
    _fields_=[('size',W.DWORD),('title',W.RECT),('states',W.DWORD*6),('buttons',W.RECT*6)]


class CaptionMinimizer(QObject):
    requested = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.targets = {}
        self.enabled = False
        self.pressed = None
        self.mouse_down = False
        self.regions = {}
        self.thread = None
        self.thread_id = 0
        self.hook = None
        self.error = ''
        self.ready = threading.Event()
        self.user = C.WinDLL('user32',use_last_error=True)
        self.kernel = C.WinDLL('kernel32',use_last_error=True)
        self.callback_type = C.WINFUNCTYPE(C.c_ssize_t,C.c_int,W.WPARAM,W.LPARAM)
        signatures = {
            'SetWindowsHookExW':([C.c_int,self.callback_type,W.HINSTANCE,W.DWORD],W.HANDLE),
            'CallNextHookEx':([W.HANDLE,C.c_int,W.WPARAM,W.LPARAM],C.c_ssize_t),
            'UnhookWindowsHookEx':([W.HANDLE],W.BOOL),
            'GetWindowThreadProcessId':([W.HWND,C.POINTER(W.DWORD)],W.DWORD),
            'GetWindowLongW':([W.HWND,C.c_int],W.LONG),
            'GetAsyncKeyState':([C.c_int],C.c_short),
            'GetCursorPos':([C.POINTER(W.POINT)],W.BOOL),
            'GetWindowRect':([W.HWND,C.POINTER(W.RECT)],W.BOOL),
            'WindowFromPoint':([W.POINT],W.HWND),
            'GetAncestor':([W.HWND,W.UINT],W.HWND),
            'GetDpiForWindow':([W.HWND],W.UINT),
            'IsIconic':([W.HWND],W.BOOL),
            'IsWindowVisible':([W.HWND],W.BOOL),
            'SendMessageTimeoutW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM,W.UINT,W.UINT,C.POINTER(C.c_size_t)],C.c_ssize_t),
            'GetMessageW':([C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT],W.BOOL),
            'PeekMessageW':([C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT],W.BOOL),
            'TranslateMessage':([C.POINTER(W.MSG)],W.BOOL),
            'DispatchMessageW':([C.POINTER(W.MSG)],C.c_ssize_t),
            'PostThreadMessageW':([W.DWORD,W.UINT,W.WPARAM,W.LPARAM],W.BOOL),
        }
        for name,(args,result) in signatures.items():
            function=getattr(self.user,name);function.argtypes=args;function.restype=result
        self.user.GetForegroundWindow.restype=W.HWND
        self.refresh_timer=QTimer(self);self.refresh_timer.setInterval(100)
        self.refresh_timer.timeout.connect(self.refresh_regions)
        self.kernel.GetCurrentThreadId.restype=W.DWORD
        self.kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];self.kernel.GetModuleHandleW.restype=W.HMODULE

    def configure(self, enabled, windows):
        self.targets = {int(w['id']):int(w['pid']) for w in windows}
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if enabled:
            self.refresh_timer.start()
            self.refresh_regions()
            self.ready.clear()
            self.thread=threading.Thread(target=self.run,daemon=True,name='BrisaCaptionButtons')
            self.thread.start();self.ready.wait(.25)
        else:
            self.refresh_timer.stop();self.regions={}
            self.stop_thread()

    def refresh_regions(self):
        # Cross-process queries run outside the mouse callback, for one active window.
        if not self.enabled:
            return
        if self.mouse_down:
            # Recover only outside the hook when an up event was lost.
            if not self.user.GetAsyncKeyState(1) & 0x8000:
                self.mouse_down = False
                self.pressed = None
                self.regions = {}
            return
        hwnd=int(self.user.GetForegroundWindow() or 0)
        expected=self.targets.get(hwnd)
        if not expected or not self.user.IsWindowVisible(hwnd) or self.user.IsIconic(hwnd):
            self.regions={};return
        self.user.SetThreadDpiAwarenessContext.argtypes=[C.c_void_p]
        self.user.SetThreadDpiAwarenessContext.restype=C.c_void_p
        previous=self.user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        try:
            pid=W.DWORD();self.user.GetWindowThreadProcessId(hwnd,C.byref(pid))
            if pid.value!=expected or not self.user.GetWindowLongW(hwnd,-16)&0x20000:
                self.regions={};return
            info=TitleBarInfo();info.size=C.sizeof(info);result=C.c_size_t()
            ok=self.user.SendMessageTimeoutW(hwnd,0x33f,0,C.addressof(info),0x23,15,C.byref(result))
            rect=info.buttons[2]
            bounds = (rect.left,rect.top,rect.right,rect.bottom) if ok and not info.states[2]&0x8001 and rect.right>rect.left and rect.bottom>rect.top else self.pointer_minimize_region(hwnd)
            self.regions = {hwnd:(expected,bounds,time.monotonic()+.25)} if bounds else {}
        finally:
            if previous:self.user.SetThreadDpiAwarenessContext(previous)

    def pointer_minimize_region(self, hwnd):
        # Custom frames may omit TITLEBARINFOEX. Never guess a button position:
        # cache only the exact physical pixel confirmed as HTMINBUTTON by the app.
        point = W.POINT()
        rect = W.RECT()
        if not self.user.GetCursorPos(C.byref(point)) or not self.user.GetWindowRect(hwnd,C.byref(rect)):
            return None
        dpi = self.user.GetDpiForWindow(hwnd) or 96
        if not (rect.left <= point.x < rect.right and rect.top <= point.y < min(rect.bottom, rect.top+round(80*dpi/96))):
            return None
        under = self.user.WindowFromPoint(point)
        if not under or self.user.GetAncestor(under,2) != hwnd:
            return None
        result = C.c_size_t()
        packed = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
        ok = self.user.SendMessageTimeoutW(hwnd,0x84,0,packed,0x23,15,C.byref(result))
        return (point.x,point.y,point.x+1,point.y+1) if ok and result.value == 8 else None

    def region_hit(self, point, allow_expired=False):
        hwnd=int(self.user.GetForegroundWindow() or 0)
        region=self.regions.get(hwnd)
        if not self.enabled or not region:
            return None
        pid,rect,expires=region
        if not allow_expired and time.monotonic()>expires:
            return None
        left,top,right,bottom=rect
        return (hwnd,pid) if left<=point.x<right and top<=point.y<bottom else None

    def handle_mouse(self, message, point):
        if message==0x201:
            self.mouse_down=True
            self.pressed=self.region_hit(point)
            if self.pressed is None:self.regions={}
            return self.pressed is not None
        if message==0x202:
            self.mouse_down=False
            pressed,self.pressed=self.pressed,None
            if pressed is not None:
                if self.region_hit(point,allow_expired=True)==pressed:
                    self.requested.emit(*pressed)
                self.regions={}
                return True
        return False

    def callback(self, code, message, pointer):
        if code == 0 and message in (0x201,0x202):
            try:
                info=C.cast(pointer,C.POINTER(MouseInfo)).contents
                if self.handle_mouse(message,info.point):
                    return 1
            except Exception:
                pass
        return self.user.CallNextHookEx(self.hook,code,message,pointer)

    def run(self):
        # Low-level mouse coordinates are physical; use the same DPI context.
        self.user.SetThreadDpiAwarenessContext.argtypes=[C.c_void_p]
        self.user.SetThreadDpiAwarenessContext.restype=C.c_void_p
        previous_dpi=self.user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        self.thread_id=self.kernel.GetCurrentThreadId()
        message=W.MSG();self.user.PeekMessageW(C.byref(message),None,0,0,0)
        callback=self.callback_type(self.callback)
        hook=self.user.SetWindowsHookExW(14,callback,self.kernel.GetModuleHandleW(None),0)
        self.hook=hook
        if not hook:self.error=str(C.get_last_error())
        self.ready.set()
        try:
            while hook and self.user.GetMessageW(C.byref(message),None,0,0)>0:
                self.user.TranslateMessage(C.byref(message));self.user.DispatchMessageW(C.byref(message))
        finally:
            if hook:self.user.UnhookWindowsHookEx(hook)
            self.hook=None;self.thread_id=0;self.pressed=None
            if previous_dpi:self.user.SetThreadDpiAwarenessContext(previous_dpi)

    def stop_thread(self):
        if self.thread_id:self.user.PostThreadMessageW(self.thread_id,0x12,0,0)
        if self.thread and self.thread is not threading.current_thread():self.thread.join(.3)
        self.thread=None

    def close(self):
        self.enabled=False;self.targets={};self.regions={};self.refresh_timer.stop();self.stop_thread()
