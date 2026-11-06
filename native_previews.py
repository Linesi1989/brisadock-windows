"""Live DWM thumbnails. Used only for an explicitly opened window panel."""
import ctypes as C
from ctypes import wintypes as W


class ThumbnailProperties(C.Structure):
    _fields_ = [('flags', W.DWORD), ('destination', W.RECT), ('source', W.RECT),
                ('opacity', C.c_ubyte), ('visible', W.BOOL), ('client_only', W.BOOL)]


class DwmThumbnails:
    def __init__(self, destination):
        self.destination = destination
        self.handles = []
        self.api = C.WinDLL('dwmapi')
        self.api.DwmRegisterThumbnail.argtypes = [W.HWND, W.HWND, C.POINTER(W.HANDLE)]
        self.api.DwmRegisterThumbnail.restype = W.LONG
        self.api.DwmQueryThumbnailSourceSize.argtypes = [W.HANDLE, C.POINTER(W.SIZE)]
        self.api.DwmQueryThumbnailSourceSize.restype = W.LONG
        self.api.DwmUpdateThumbnailProperties.argtypes = [W.HANDLE, C.POINTER(ThumbnailProperties)]
        self.api.DwmUpdateThumbnailProperties.restype = W.LONG
        self.api.DwmUnregisterThumbnail.argtypes = [W.HANDLE]
        self.api.DwmUnregisterThumbnail.restype = W.LONG

    def add(self, source, rectangle):
        handle = W.HANDLE()
        code = self.api.DwmRegisterThumbnail(self.destination, source, C.byref(handle))
        if code != 0:
            return False
        size = W.SIZE()
        if self.api.DwmQueryThumbnailSourceSize(handle, C.byref(size)) != 0 or size.cx <= 0 or size.cy <= 0:
            self.api.DwmUnregisterThumbnail(handle)
            return False
        x, y, width, height = rectangle
        scale = min(width / size.cx, height / size.cy)
        w, h = round(size.cx*scale), round(size.cy*scale)
        left, top = round(x+(width-w)/2), round(y+(height-h)/2)
        properties = ThumbnailProperties()
        properties.flags = 1 | 4 | 8 | 16
        properties.destination = W.RECT(left, top, left+w, top+h)
        properties.opacity = 255
        properties.visible = True
        properties.client_only = False
        code = self.api.DwmUpdateThumbnailProperties(handle, C.byref(properties))
        if code != 0:
            self.api.DwmUnregisterThumbnail(handle)
            return False
        self.handles.append(handle)
        return True

    def clear(self):
        for handle in self.handles:
            self.api.DwmUnregisterThumbnail(handle)
        self.handles.clear()
