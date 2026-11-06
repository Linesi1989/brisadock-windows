"""Track only windows explicitly hidden by this dock and recover them on restart."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import threading


class HiddenWindows:
    def __init__(self, backend):
        self.backend = backend
        self.entries = {}
        self.lock = threading.RLock()
        self.path = None
        backend.k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)]*4
        backend.k.GetProcessTimes.restype = W.BOOL
        backend.u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        backend.u.GetClassNameW.restype = C.c_int

    def process_started(self, pid):
        handle = self.backend.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [W.FILETIME() for _ in range(4)]
            if self.backend.k.GetProcessTimes(handle, *(C.byref(value) for value in times)):
                return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            self.backend.k.CloseHandle(handle)
        return None

    def valid(self, entry):
        return self.backend.valid(entry) and self.process_started(entry['pid']) == entry['started']

    def configure(self, path):
        self.path = Path(path)
        try:
            entries = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(entries, list):
                return
            with self.lock:
                for entry in entries[:512]:
                    if (isinstance(entry, dict) and all(type(entry.get(key)) is int and entry[key] > 0
                            for key in ('id', 'pid', 'started')) and self.valid(entry)):
                        self.entries[(entry['pid'], entry['id'])] = entry
            self.restore_all()
        except (OSError, ValueError, TypeError, OverflowError):
            pass

    def save(self):
        if self.path is None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(list(self.entries.values())), encoding='utf-8')
            temporary.replace(self.path)
            return True
        except OSError:
            return False

    def tracked(self, window):
        with self.lock:
            return (window['pid'], window['id']) in self.entries

    def can_hide(self, window):
        if not self.backend.valid(window):
            return False
        name = C.create_unicode_buffer(256)
        self.backend.u.GetClassNameW(window['id'], name, len(name))
        return bool(name.value) and name.value not in {'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'}

    def hide(self, window):
        with self.lock:
            identity = (window['pid'], window['id'])
            if identity in self.entries:
                if not self.valid(self.entries[identity]):
                    self.entries.pop(identity, None)
                    self.save()
                    return False
                return (bool(self.backend.u.ShowWindowAsync(window['id'], 0))
                        if self.backend.u.IsWindowVisible(window['id']) else True)
            if not self.can_hide(window) or not self.backend.u.IsWindowVisible(window['id']):
                return False
            started = self.process_started(window['pid'])
            if started is None or len(self.entries) >= 512:
                return False
            entry = dict(id=window['id'], pid=window['pid'], started=started)
            self.entries[identity] = entry
            # Write recovery information before changing visibility.
            if not self.save() or not self.backend.u.ShowWindowAsync(window['id'], 0):
                self.entries.pop(identity, None)
                self.save()
                return False
            return True

    def show(self, window):
        with self.lock:
            identity = (window['pid'], window['id'])
            entry = self.entries.get(identity)
            if entry is None:
                return True
            if not self.valid(entry):
                self.entries.pop(identity, None)
                self.save()
                return False
            # SW_SHOWNA preserves minimized/maximized state and avoids activation.
            if not self.backend.u.ShowWindowAsync(entry['id'], 8):
                return False
            self.entries.pop(identity, None)
            self.save()
            return True

    def prune(self):
        with self.lock:
            expired = [key for key, entry in self.entries.items() if not self.valid(entry)]
            for key in expired:
                self.entries.pop(key, None)
            if expired:
                self.save()

    def restore_all(self):
        with self.lock:
            for entry in list(self.entries.values()):
                self.show(entry)
