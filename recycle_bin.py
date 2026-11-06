"""Read-only Shell recycle-bin state; bounded polling outside the GUI thread."""
import ctypes as C
from ctypes import wintypes as W
import threading
from PySide6.QtCore import QObject, QTimer, Signal


class QueryInfo(C.Structure):
    _fields_ = [('size', W.DWORD), ('bytes', C.c_longlong), ('items', C.c_longlong)]


def query_count():
    shell = C.WinDLL('shell32')
    shell.SHQueryRecycleBinW.argtypes = [W.LPCWSTR, C.POINTER(QueryInfo)]
    shell.SHQueryRecycleBinW.restype = W.LONG
    info = QueryInfo()
    info.size = C.sizeof(info)
    result = shell.SHQueryRecycleBinW(None, C.byref(info))
    return int(info.items) if result == 0 and info.items >= 0 else None


class RecycleBinMonitor(QObject):
    changed = Signal(object)

    def __init__(self, parent=None, query=query_count):
        super().__init__(parent)
        self.query = query
        self.closed = False
        self.busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        if self.closed or self.busy:
            return
        self.busy = True
        def run():
            result = None
            try:
                result = self.query()
            except (OSError, AttributeError):
                pass
            finally:
                self.busy = False
            if not self.closed:
                try:
                    self.changed.emit(result)
                except RuntimeError:
                    pass
        threading.Thread(target=run, daemon=True, name='BrisaRecycleBin').start()

    def close(self):
        self.closed = True
        self.timer.stop()
