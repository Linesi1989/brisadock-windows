"""Read registered package identities/paths for this user, without activation."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import os
import xml.etree.ElementTree as ET


def package_names(family):
    if os.name != 'nt':
        return []
    api = C.WinDLL('kernel32')
    query = api.GetPackagesByPackageFamily
    query.argtypes = [W.LPCWSTR, C.POINTER(W.UINT), C.POINTER(W.LPWSTR), C.POINTER(W.UINT), W.LPWSTR]
    query.restype = W.LONG
    count, length = W.UINT(), W.UINT()
    result = query(family, C.byref(count), None, C.byref(length), None)
    if result not in (0, 122) or not count.value:
        return []
    names = (W.LPWSTR*count.value)()
    buffer = C.create_unicode_buffer(length.value)
    if query(family, C.byref(count), names, C.byref(length), buffer) != 0:
        return []
    return sorted(names[index] for index in range(count.value))


def package_folder(name):
    query = C.WinDLL('kernel32').GetPackagePathByFullName
    query.argtypes = [W.LPCWSTR, C.POINTER(W.UINT), W.LPWSTR]
    query.restype = W.LONG
    length = W.UINT()
    if query(name, C.byref(length), None) != 122:
        return None
    buffer = C.create_unicode_buffer(length.value)
    return Path(buffer.value) if query(name, C.byref(length), buffer) == 0 else None


def known_folder_target(value, existing=True):
    """Resolve desktop Start-app identifiers without activating the Shell item."""
    if os.name != 'nt' or not value.startswith('shell:AppsFolder\\'):
        return None
    try:
        import uuid
        identifier=value.split('\\',1)[1]
        folder_id,relative=identifier.split('\\',1)
        guid=(C.c_ubyte*16).from_buffer_copy(uuid.UUID(folder_id.strip('{}')).bytes_le)
        relative=Path(relative)
        if relative.drive or relative.is_absolute() or '..' in relative.parts:
            return None
    except (ValueError,IndexError):
        return None
    shell,ole=C.WinDLL('shell32'),C.WinDLL('ole32')
    ole.CoInitializeEx.argtypes=[C.c_void_p,W.DWORD];ole.CoInitializeEx.restype=W.LONG
    initialized=ole.CoInitializeEx(None,2) in (0,1)
    ole.CoTaskMemFree.argtypes=[C.c_void_p]
    shell.SHGetKnownFolderPath.argtypes=[C.c_void_p,W.DWORD,W.HANDLE,C.POINTER(C.c_void_p)]
    shell.SHGetKnownFolderPath.restype=W.LONG
    output=C.c_void_p()
    try:
        if shell.SHGetKnownFolderPath(C.byref(guid),0,None,C.byref(output))!=0 or not output.value:
            return None
        target=Path(C.wstring_at(output))/relative
        return str(target) if not existing or target.is_file() else None
    finally:
        if output.value:ole.CoTaskMemFree(output)
        if initialized:ole.CoUninitialize()


def executable_for_aumid(value):
    try:
        identifier = value.split('\\', 1)[1]
        if '!' not in identifier:
            target=known_folder_target(value)
            return target if target and Path(target).suffix.lower() in ('.exe','.com') else None
        family, app_id = identifier.split('!', 1)
    except (IndexError, ValueError):
        return None
    for name in reversed(package_names(family)):
        folder = package_folder(name)
        if folder is None:
            continue
        try:
            for app in ET.parse(folder/'AppxManifest.xml').getroot().iter():
                if app.tag.rsplit('}', 1)[-1] == 'Application' and app.get('Id') == app_id:
                    executable = app.get('Executable')
                    if executable:
                        return str(folder/executable.replace('\\', '/'))
        except (OSError, ET.ParseError):
            continue
    return None
