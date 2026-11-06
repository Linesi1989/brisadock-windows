"""Windows document drops: preserve shortcut arguments, never use a command shell."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import threading
from PySide6.QtCore import QObject, Signal


def accepts_documents(item, targets=None):
    if item.get('symbol') or item.get('minimized_window'):
        return False
    path = item.get('path', '')
    suffix = Path(path).suffix.lower()
    if suffix == '.exe':
        return True
    if suffix == '.lnk':
        target = (targets or {}).get(path)
        return target is None or target == path or Path(target).suffix.lower() == '.exe'
    return False


def split_arguments(arguments):
    shell = C.WinDLL('shell32', use_last_error=True)
    kernel = C.WinDLL('kernel32')
    shell.CommandLineToArgvW.argtypes = [W.LPCWSTR, C.POINTER(C.c_int)]
    shell.CommandLineToArgvW.restype = C.POINTER(W.LPWSTR)
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    count = C.c_int()
    result = shell.CommandLineToArgvW('brisa-placeholder '+arguments, C.byref(count))
    if not result:
        raise OSError('No se pudieron leer los argumentos del acceso.')
    try:
        return [result[index] for index in range(1, count.value)]
    finally:
        kernel.LocalFree(C.cast(result, C.c_void_p))


def shortcut_details(path):
    script = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:BRISA_DOCUMENT_SHORTCUT); "
              "@{target=$s.TargetPath; arguments=$s.Arguments; directory=$s.WorkingDirectory} | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=dict(os.environ, BRISA_DOCUMENT_SHORTCUT=str(path)),
                            capture_output=True, timeout=4, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise OSError('No se pudo resolver el acceso directo.')
    details = json.loads(result.stdout.decode('utf-8-sig'))
    if not isinstance(details, dict):
        raise ValueError('Acceso directo no compatible.')
    return details


def launch_plan(item, files):
    if not accepts_documents(item) or not 1 <= len(files) <= 64:
        raise ValueError('Selecciona una aplicación compatible y entre 1 y 64 archivos.')
    documents = [str(Path(path).resolve()) for path in files]
    if any(not Path(path).is_file() for path in documents):
        raise ValueError('Solo se admiten archivos locales existentes; las carpetas se pueden anclar en un espacio libre.')
    source = Path(item['path'])
    if not source.is_file():
        raise ValueError('La aplicación o el acceso directo ya no existe.')
    arguments = []
    directory = None
    if source.suffix.lower() == '.lnk':
        details = shortcut_details(source)
        source = Path(details.get('target') or '')
        arguments = split_arguments(details.get('arguments') or '')
        working = details.get('directory')
        directory = working if working and Path(working).is_dir() else None
    if source.suffix.lower() != '.exe' or not source.is_file():
        raise ValueError('Este acceso no apunta a un ejecutable .exe compatible.')
    return [str(source.resolve()), *arguments, *documents], directory


class DocumentOpener(QObject):
    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.stop = threading.Event()

    def request(self, item, files):
        if self.busy or self.stop.is_set():
            return False
        self.busy = True
        def run():
            success, message = False, ''
            try:
                command, directory = launch_plan(item, files)
                if not self.stop.is_set():
                    subprocess.Popen(command, cwd=directory, shell=False)
                    success = True
                    message = f"Solicitud de apertura enviada a {item['name']}."
            except Exception as error:
                message = str(error) if isinstance(error, (OSError, ValueError, subprocess.TimeoutExpired)) else type(error).__name__
            self.busy = False
            if not self.stop.is_set():
                try:
                    self.finished.emit(success, message)
                except RuntimeError:
                    pass
        threading.Thread(target=run, daemon=True, name='BrisaDocumentDrop').start()
        return True
