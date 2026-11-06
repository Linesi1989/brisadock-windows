"""Read the OS application catalog without executing discovered launchers."""
import configparser
import ctypes as C
import json
import os
from pathlib import Path
import shutil
import subprocess
import unicodedata
import uuid


def search_text(value):
    return ''.join(ch for ch in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(ch))


def windows_program_dirs():
    from ctypes import wintypes as W
    shell = C.WinDLL('shell32')
    ole = C.WinDLL('ole32')
    shell.SHGetKnownFolderPath.argtypes = [C.c_void_p, W.DWORD, W.HANDLE, C.POINTER(W.LPWSTR)]
    shell.SHGetKnownFolderPath.restype = W.LONG
    ole.CoTaskMemFree.argtypes = [C.c_void_p]
    folders = []
    for identifier in ('A77F5D77-2E2B-44C3-A6A2-ABA601054A51', '0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8'):
        guid = (C.c_ubyte*16).from_buffer_copy(uuid.UUID(identifier).bytes_le)
        pointer = W.LPWSTR()
        if shell.SHGetKnownFolderPath(C.byref(guid), 0, None, C.byref(pointer)) == 0:
            try:
                folders.append(Path(pointer.value))
            finally:
                ole.CoTaskMemFree(C.cast(pointer, C.c_void_p))
    return folders


def scan_windows_shortcuts(roots=None, stop=None):
    found = {}
    for root in windows_program_dirs() if roots is None else roots:
        for directory, subdirectories, filenames in os.walk(root, followlinks=False):
            if stop and stop.is_set():
                return list(found.values())
            subdirectories[:] = sorted(subdirectories)
            for filename in sorted(filenames):
                if Path(filename).suffix.lower() not in {'.lnk', '.appref-ms'}:
                    continue
                path = str(Path(directory)/filename)
                name = Path(filename).stem
                # User entry overrides the same relative launcher in Common Programs.
                # Different subfolders can contain unrelated apps with the same name.
                key = str(Path(path).relative_to(root)).casefold()
                if key not in found:
                    group = Path(directory).relative_to(root).parts
                    found[key] = dict(name=name, path=path, icon='', keywords='',
                                      category=group[0] if group else '', source='start-menu')
    return sorted(found.values(), key=lambda entry: search_text(entry['name']))


def store_apps(stop=None):
    command = '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress'
    try:
        process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        import time
        deadline = time.monotonic()+10
        while True:
            if (stop and stop.is_set()) or time.monotonic() > deadline:
                process.kill()  # Only the read-only helper process created above.
                process.communicate()
                return []
            try:
                output, _ = process.communicate(timeout=.15)
                break
            except subprocess.TimeoutExpired:
                continue
        rows = json.loads(output.decode('utf-8-sig'))
        if isinstance(rows, dict):
            rows = [rows]
        return [dict(name=row['Name'], path='shell:AppsFolder\\'+row['AppID'], icon='', keywords='')
                for row in rows if isinstance(row, dict) and isinstance(row.get('Name'), str)
                and isinstance(row.get('AppID'), str) and row['AppID']]
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return []


def linux_application_dirs():
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
    roots = [data_home/'applications']
    roots += [Path(value)/'applications' for value in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if value]
    roots += [Path.home()/'.local/share/flatpak/exports/share/applications', Path('/var/lib/flatpak/exports/share/applications')]
    return list(dict.fromkeys(roots))


def scan_desktop_entries(roots=None, desktops=None, locale=None, stop=None):
    desktops = set((os.environ.get('XDG_CURRENT_DESKTOP') or 'KDE').split(':')) if desktops is None else set(desktops)
    locale = locale or os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES') or os.environ.get('LANG', '')
    locale = locale.split('.')[0].split('@')[0]
    locales = list(dict.fromkeys([locale, locale.split('_')[0]]))
    seen = set()
    result = []
    for root in linux_application_dirs() if roots is None else roots:
        root = Path(root)
        for directory, _, filenames in os.walk(root, followlinks=False):
            if stop and stop.is_set():
                return result
            for filename in sorted(filenames):
                if not filename.endswith('.desktop'):
                    continue
                path = Path(directory)/filename
                identifier = path.relative_to(root).as_posix().replace('/', '-')
                if identifier in seen:
                    continue
                seen.add(identifier)  # User Hidden=true entries mask the system entry.
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    parser.read(path, encoding='utf-8')
                    entry = parser['Desktop Entry']
                    if entry.get('Type') != 'Application' or entry.getboolean('Hidden', False) or entry.getboolean('NoDisplay', False):
                        continue
                    only = set(filter(None, entry.get('OnlyShowIn', '').split(';')))
                    exclude = set(filter(None, entry.get('NotShowIn', '').split(';')))
                    if (only and not desktops.intersection(only)) or desktops.intersection(exclude):
                        continue
                    required = entry.get('TryExec', '')
                    if required and not (os.path.isfile(required) and os.access(required, os.X_OK)) and not shutil.which(required):
                        continue
                    if not entry.get('Exec') and not entry.getboolean('DBusActivatable', False):
                        continue
                    name = next((entry[f'Name[{value}]'] for value in locales if value and f'Name[{value}]' in entry), entry.get('Name', ''))
                    if not name:
                        continue
                    result.append(dict(name=name, path=str(path), icon=entry.get('Icon', ''),
                                       keywords=entry.get('Keywords', '')+' '+entry.get('GenericName', '')))
                    if len(result) >= 4000:
                        return result
                except (OSError, UnicodeError, KeyError, ValueError, configparser.Error):
                    continue
    return sorted(result, key=lambda entry: search_text(entry['name']))


def merge_catalog(first, second):
    primary_names = {search_text(entry['name']) for entry in first}
    entries = {os.path.normcase(entry['path']): entry for entry in second
               if search_text(entry['name']) not in primary_names}
    entries.update({os.path.normcase(entry['path']): entry for entry in first})
    return sorted(entries.values(), key=lambda entry: search_text(entry['name']))
