"""Desktop integration and deterministic grouping, independent of Qt rendering."""
import configparser
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import threading


def canonical(path):
    return os.path.normcase(os.path.realpath(path)) if path else ''


def application_target(path):
    """Resolve launcher identity without executing the launcher."""
    if os.name == 'nt' and path.startswith('shell:AppsFolder\\'):
        from package_paths import executable_for_aumid
        return executable_for_aumid(path) or path
    if path.startswith(('https://', 'http://')):
        if os.name == 'nt':
            import winreg
            try:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice') as key:
                        progid = winreg.QueryValueEx(key, 'ProgId')[0]
                except OSError:
                    progid = 'https'
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\shell\open\command') as key:
                    command = winreg.QueryValue(key, None)
                tokens = shlex.split(command, posix=False)
                if tokens:
                    return os.path.expandvars(tokens[0].strip('"'))
            except (OSError, ValueError):
                pass
        else:
            try:
                name = subprocess.run(['xdg-settings', 'get', 'default-web-browser'], capture_output=True,
                                      text=True, timeout=2).stdout.strip()
                if name and '/' not in name:
                    folders = [Path.home()/'.local/share/applications', Path('/usr/local/share/applications'), Path('/usr/share/applications')]
                    for folder in folders:
                        if (folder/name).is_file():
                            return application_target(str(folder/name))
            except (OSError, subprocess.TimeoutExpired):
                pass
        return path
    if path.lower().endswith('.lnk') and os.name == 'nt':
        command = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:BRISA_SHORTCUT); $s.TargetPath"
        env = dict(os.environ, BRISA_SHORTCUT=path)
        try:
            target = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                    env=env, capture_output=True, timeout=4,
                                    creationflags=subprocess.CREATE_NO_WINDOW).stdout.decode('utf-8', 'replace').strip()
            return target or path
        except (OSError, subprocess.TimeoutExpired):
            return path
    if path.endswith('.desktop'):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding='utf-8')
            command = shlex.split(parser.get('Desktop Entry', 'Exec', fallback=''))
            if command and command[0] != 'env':
                return shutil.which(command[0]) or command[0]
        except (OSError, ValueError, configparser.Error):
            pass
    return path


def merge_items(pins, windows, targets=None, pin_indices=None):
    """Keep pin order and group visible windows by executable identity."""
    targets = targets or {}
    groups = {}
    for window in windows:
        groups.setdefault(window['key'], []).append(window)
    items = []
    claimed = set()
    for index, pin in enumerate(pins):
        target = targets.get(pin['path'], pin.get('target', pin['path']))
        key = canonical(target)
        original_index = pin_indices[index] if pin_indices is not None else index
        items.append(dict(pin, pin_index=original_index, key=key, windows=groups.get(key, [])))
        claimed.add(key)
    # Stable alphabetical ordering avoids jumps when foreground order changes.
    for key in sorted(groups):
        if key not in claimed:
            window = groups[key][0]
            items.append(dict(name=window['name'], path=window['path'], key=key,
                              windows=groups[key], temporary=True))
    return items


class NullBackend:
    def __init__(self, status):
        self.status = status

    def snapshot(self):
        return []

    def close(self):
        pass


class X11Backend:
    status = 'KDE/X11: integración EWMH (pendiente de prueba en Mint)'

    def __init__(self):
        from Xlib import display, X, Xatom, protocol
        self.X, self.Xatom, self.protocol = X, Xatom, protocol
        self.display = display.Display()
        self.root = self.display.screen().root
        self.lock = threading.RLock()

    def atom(self, name):
        return self.display.intern_atom(name)

    def prop(self, window, name):
        value = window.get_full_property(self.atom(name), self.X.AnyPropertyType)
        return value.value if value else []

    def snapshot(self):
        from Xlib.error import BadWindow
        result = []
        with self.lock:
            active = self.prop(self.root, '_NET_ACTIVE_WINDOW')
            active = int(active[0]) if len(active) else 0
            for handle in self.prop(self.root, '_NET_CLIENT_LIST'):
                try:
                    window = self.display.create_resource_object('window', int(handle))
                    state = self.prop(window, '_NET_WM_STATE')
                    if self.atom('_NET_WM_STATE_SKIP_TASKBAR') in state:
                        continue
                    pid = self.prop(window, '_NET_WM_PID')
                    if not len(pid) or int(pid[0]) == os.getpid():
                        continue
                    path = os.readlink(f'/proc/{int(pid[0])}/exe')
                    title = self.prop(window, '_NET_WM_NAME')
                    title = title.decode('utf-8', 'replace') if isinstance(title, bytes) else (window.get_wm_name() or Path(path).stem)
                    result.append(dict(id=int(handle), pid=int(pid[0]), path=path, key=canonical(path),
                                       title=title, name=Path(path).stem,
                                       minimized=self.atom('_NET_WM_STATE_HIDDEN') in state,
                                       urgent=self.atom('_NET_WM_STATE_DEMANDS_ATTENTION') in state,
                                       active=int(handle) == active))
                except (BadWindow, OSError):
                    continue
            self.display.flush()
        return result

    def send(self, window, message, data):
        from Xlib.error import BadWindow
        with self.lock:
            try:
                resource = self.display.create_resource_object('window', window['id'])
                pid = self.prop(resource, '_NET_WM_PID')
                if not len(pid) or int(pid[0]) != window['pid']:
                    return False
                event = self.protocol.event.ClientMessage(window=window['id'],
                            client_type=self.atom(message), data=(32, data))
                self.root.send_event(event, event_mask=self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask)
                self.display.flush()
                return True
            except BadWindow:
                return False

    def activate(self, window):
        return self.send(window, '_NET_ACTIVE_WINDOW', [2, self.X.CurrentTime, 0, 0, 0])

    def minimize(self, window):
        return self.send(window, 'WM_CHANGE_STATE', [3, 0, 0, 0, 0])

    def close_window(self, window):
        return self.send(window, '_NET_CLOSE_WINDOW', [self.X.CurrentTime, 2, 0, 0, 0])

    def close(self):
        with self.lock:
            self.display.close()


def create_backend():
    if os.name == 'nt':
        from windows_backend import WindowsBackend
        return WindowsBackend()
    if os.environ.get('XDG_SESSION_TYPE') == 'wayland' or os.environ.get('WAYLAND_DISPLAY'):
        return NullBackend('Wayland: solo lanzador; no se enumeran ventanas nativas')
    try:
        return X11Backend()
    except Exception as error:
        return NullBackend(f'Integración X11 no disponible: {type(error).__name__}')
