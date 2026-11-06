"""Read Shell namespace drops without executing or creating temporary shortcuts."""
import os
from pathlib import Path
import struct
from PySide6.QtCore import Qt

SHELL_FORMAT = 'application/x-qt-windows-mime;value="Shell IDList Array"'
APPS_FOLDER = '::{4234d49b-0245-4df3-b780-3893943456e1}\\'


def drop_action(actions):
    # Start may offer LINK only. Never accept MOVE: pinning does not move files.
    return Qt.CopyAction if actions & Qt.CopyAction else Qt.LinkAction if actions & Qt.LinkAction else Qt.IgnoreAction


def local_paths(mime):
    urls = mime.urls()
    # Start can advertise file:AUMID. It is a Shell item, not a local file path.
    if not urls or len(urls) > 64 or not all(url.isLocalFile() for url in urls):
        return []
    paths = [url.toLocalFile() for url in urls]
    return paths if all(Path(path).is_absolute() for path in paths) else []


def accepts_mime(mime):
    return bool(local_paths(mime)) or (os.name == 'nt' and mime.hasFormat(SHELL_FORMAT))


def split_cida(data):
    """Validate all relative offsets and ITEMIDLIST lengths before native calls."""
    if not 12 <= len(data) <= 1024*1024:
        raise ValueError('Invalid Shell drop size')
    count = struct.unpack_from('<I', data)[0]
    header = 4 + (count+1)*4
    if not 1 <= count <= 64 or header > len(data):
        raise ValueError('Invalid Shell item count')
    offsets = struct.unpack_from('<'+'I'*(count+1), data, 4)
    lists = []
    for offset in offsets:
        if offset < header or offset+2 > len(data):
            raise ValueError('Invalid Shell item offset')
        start = offset
        for _ in range(4096):
            if offset+2 > len(data):
                raise ValueError('Truncated Shell item')
            length = struct.unpack_from('<H', data, offset)[0]
            if length == 0:
                lists.append(data[start:offset+2])
                break
            if length < 2 or offset+length > len(data):
                raise ValueError('Invalid Shell item length')
            offset += length
        else:
            raise ValueError('Shell item nesting limit')
    return [lists[0][:-2]+child for child in lists[1:]]


def shell_entry(pidl):
    import ctypes as C
    from ctypes import wintypes as W
    shell = C.WinDLL('shell32');ole = C.WinDLL('ole32')
    shell.SHGetNameFromIDList.argtypes = [C.c_void_p, W.DWORD, C.POINTER(W.LPWSTR)]
    shell.SHGetNameFromIDList.restype = W.LONG
    ole.CoTaskMemFree.argtypes = [C.c_void_p]
    buffer = C.create_string_buffer(pidl)
    def name(kind, source=buffer):
        pointer = W.LPWSTR()
        result = shell.SHGetNameFromIDList(source, kind, C.byref(pointer))
        try:
            return pointer.value if result >= 0 and pointer else ''
        finally:
            if pointer:ole.CoTaskMemFree(C.cast(pointer,C.c_void_p))
    local = name(0x80058000)  # SIGDN_FILESYSPATH
    if local and Path(local).exists():
        return dict(path=local, name=Path(local).stem)
    parsing = name(0x80028000)  # SIGDN_DESKTOPABSOLUTEPARSING
    # Some AppsFolder items report just their AUMID even for absolute parsing.
    offset = last = 0
    while struct.unpack_from('<H', pidl, offset)[0]:
        last = offset
        offset += struct.unpack_from('<H', pidl, offset)[0]
    parent = C.create_string_buffer(pidl[:last]+b'\0\0')
    parent_name = name(0x80028000, parent).casefold()
    if parsing.casefold().startswith(APPS_FOLDER) or parent_name == APPS_FOLDER[:-1]:
        appid = parsing[len(APPS_FOLDER):] if parsing.casefold().startswith(APPS_FOLDER) else parsing
        # Desktop AppsFolder IDs may themselves contain an executable path.
        if appid and len(appid) <= 32768 and not any(c in appid for c in '\x00\r\n'):
            return dict(path='shell:AppsFolder\\'+appid, name=name(0) or appid, kind='app')
    return None


def drop_entries(mime, resolver=shell_entry):
    # Native Shell identity takes precedence over Qt's lossy URI conversion.
    if os.name == 'nt' and mime.hasFormat(SHELL_FORMAT):
        try:
            pidls = split_cida(bytes(mime.data(SHELL_FORMAT)))
            result = []
            seen = set()
            for pidl in pidls:
                entry = resolver(pidl)
                if entry and entry['path'].casefold() not in seen:
                    seen.add(entry['path'].casefold());result.append(entry)
            if result:
                return result
        except (ValueError, OSError, struct.error):
            pass
    paths = local_paths(mime)
    return [dict(path=path, name=Path(path).stem) for path in paths] if paths and all(Path(path).exists() for path in paths) else []
