"""Keep application launchers separate from pinned folders and documents."""
from pathlib import Path
from desktop_backend import merge_items

APP_EXTENSIONS = {'.exe', '.com', '.bat', '.cmd', '.appref-ms', '.desktop'}


def pin_zone(pin, targets=None):
    if pin.get('zone') in ('apps', 'files'):
        return pin['zone']
    if pin.get('kind') == 'app':
        return 'apps'
    if pin.get('kind') in ('file', 'folder'):
        return 'files'
    path = pin['path']
    target = (targets or {}).get(path, pin.get('target', path))
    if target.startswith('shell:AppsFolder\\') or Path(target).suffix.lower() in APP_EXTENSIONS:
        return 'apps'
    if Path(path).suffix.lower() == '.lnk' and target == path:
        return 'apps'  # Unresolved shortcut: retain its initial launcher placement.
    return 'files'


def zoned_items(pins, windows, targets=None):
    indices = [index for index, pin in enumerate(pins) if pin_zone(pin, targets) == 'apps']
    applications = merge_items([pins[index] for index in indices], windows, targets, pin_indices=indices)
    resources = [dict(pin, pin_index=index, windows=[], zone='files') for index, pin in enumerate(pins)
                 if pin_zone(pin, targets) == 'files']
    return applications, resources


def neighbor(pins, index, direction, targets=None):
    indices = [candidate for candidate, pin in enumerate(pins)
               if pin_zone(pin, targets) == pin_zone(pins[index], targets)]
    position = indices.index(index)+direction
    return indices[position] if 0 <= position < len(indices) else None
