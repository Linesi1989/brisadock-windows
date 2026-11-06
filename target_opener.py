"""Keep Windows Shell handlers out of the Dock GUI process."""
import os
from pathlib import Path
import sys
from PySide6.QtCore import QObject, Signal, QProcess, QTimer


def helper_command(target):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--shell-open', target]
    return [sys.executable, str(Path(__file__).resolve()), '--shell-open', target]


def shell_open(target):
    # No profile lock, QApplication, command shell or document parsing here.
    import ctypes as C
    from ctypes import wintypes as W
    if os.name != 'nt' or not target or '\x00' in target:
        return 1
    class ExecuteInfo(C.Structure):
        _fields_ = [('cbSize', W.DWORD), ('fMask', W.ULONG), ('hwnd', W.HWND),
                    ('lpVerb', W.LPCWSTR), ('lpFile', W.LPCWSTR), ('lpParameters', W.LPCWSTR),
                    ('lpDirectory', W.LPCWSTR), ('nShow', C.c_int), ('hInstApp', W.HINSTANCE),
                    ('lpIDList', C.c_void_p), ('lpClass', W.LPCWSTR), ('hkeyClass', W.HKEY),
                    ('dwHotKey', W.DWORD), ('hIcon', W.HANDLE), ('hProcess', W.HANDLE)]
    ole = C.WinDLL('ole32')
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    kernel = C.WinDLL('kernel32')
    kernel.SetDllDirectoryW.argtypes = [W.LPCWSTR]
    if getattr(sys, 'frozen', False):
        kernel.SetDllDirectoryW(None)
    shell = C.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [C.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype = W.BOOL
    try:
        # A document can default to play, edit, preview, or an extension-owned verb.
        # Invoke Explorer's actual default action instead of forcing 'open'.
        if Path(target).is_file() and Path(target).suffix.casefold() not in {'.exe', '.com', '.bat', '.cmd', '.lnk', '.appref-ms'}:
            from shell_items import invoke_default
            try:
                return invoke_default(target)
            except FileNotFoundError:
                return 3
            except OSError:
                return 1
        info = ExecuteInfo();info.cbSize = C.sizeof(info)
        info.fMask = 0x400 | 0x100  # NO_UI and NOASYNC: report errors; isolate slow COM handlers.
        info.lpVerb = None;info.lpFile = target;info.nShow = 1
        if shell.ShellExecuteExW(C.byref(info)):
            return 0
        error = C.get_last_error()
        return 3 if error in (2, 3) else 4 if error in (31, 1155) else 1
    finally:
        if initialized:
            ole.CoUninitialize()


class TargetOpener(QObject):
    finished = Signal(str, bool, str)

    def __init__(self, parent=None, command=helper_command, timeout_ms=60000):
        super().__init__(parent)
        self.command = command
        self.timeout_ms = timeout_ms
        self.pending = {}
        self.closed = False

    def request(self, target):
        key = os.path.normcase(target)
        if self.closed or key in self.pending:
            return False
        process = QProcess(self)
        timer = QTimer(process);timer.setSingleShot(True)
        state = dict(process=process, timer=timer, target=target, timeout=False)
        self.pending[key] = state
        process.setStandardInputFile(QProcess.nullDevice())
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setStandardErrorFile(QProcess.nullDevice())
        process.finished.connect(lambda code, status: self.complete(key, code, status))
        process.errorOccurred.connect(lambda error: self.failed(key, error))
        timer.timeout.connect(lambda: self.expire(key))
        try:
            command = self.command(target)
            process.start(command[0], command[1:])
            timer.start(self.timeout_ms)
        except Exception:
            self.complete(key, 1, QProcess.CrashExit)
            return False
        return True

    def expire(self, key):
        state = self.pending.get(key)
        if state:
            state['timeout'] = True
            state['process'].kill()  # Only our Shell helper, never the external viewer.

    def failed(self, key, error):
        if error == QProcess.FailedToStart:
            self.complete(key, 1, QProcess.CrashExit)

    def complete(self, key, code, status):
        state = self.pending.pop(key, None)
        if state is None:
            return
        state['timer'].stop()
        success = code == 0 and status == QProcess.NormalExit and not state['timeout']
        message = ('Solicitud enviada a Windows.' if success else
                   'Windows no respondió al abrir el archivo. Puedes volver a intentarlo; Brisa sigue disponible.' if state['timeout'] else
                   'El archivo o acceso ya no existe.' if code == 3 else
                   'No hay una aplicación asociada. Elige una con Abrir con en el Explorador.' if code == 4 else
                   'Windows no pudo abrir este elemento. Comprueba su aplicación predeterminada en el Explorador.')
        state['process'].deleteLater()
        if not self.closed:
            self.finished.emit(state['target'], success, message)

    def close(self):
        self.closed = True
        for state in list(self.pending.values()):
            state['timer'].stop()
            state['process'].kill()


if __name__ == '__main__':
    sys.exit(shell_open(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == '--shell-open' else 2)
