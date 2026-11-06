"""Shared folder ordering and bounded directory enumeration, used off the GUI thread."""
import heapq
from pathlib import Path
import os
import re
import stat as stat_module
import unicodedata

ORDERS = (('date', 'Fecha de modificación'), ('name', 'Nombre'),
          ('kind', 'Tipo'), ('created', 'Fecha de creación'))


def natural_name(name):
    value = ''.join(ch for ch in unicodedata.normalize('NFKD', name.casefold())
                    if not unicodedata.combining(ch))
    return tuple((1, int(part)) if part.isdigit() else (0, part) for part in re.split(r'(\d+)', value))


def scan_directory(path, order='date', limit=96):
    count = 0
    def records():
        nonlocal count
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith('.'):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if getattr(info, 'st_file_attributes', 0) & 6:
                        continue  # Windows hidden/system entries, such as desktop.ini.
                    directory = stat_module.S_ISDIR(info.st_mode)
                    name_key = natural_name(entry.name)
                    key = (name_key if order == 'name' else
                           (not directory, Path(entry.name).suffix.casefold(), name_key) if order == 'kind' else
                           (-getattr(info, 'st_birthtime', info.st_ctime), name_key) if order == 'created' else
                           (-info.st_mtime, name_key))
                    count += 1
                    yield key, entry.name, entry.path, directory
                except OSError:
                    continue
    try:
        entries = heapq.nsmallest(limit, records())
        return entries, count, ''
    except OSError as error:
        return [], 0, error.strerror or type(error).__name__
