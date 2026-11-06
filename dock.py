"""Brisa Dock: an independent, free desktop launcher. MIT license."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import configparser
from collections import OrderedDict

from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QPointF, QFileInfo, QStandardPaths, QUrl, QThread, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QPainter, QPen, QIcon, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QFileDialog, QFileIconProvider, QMessageBox, QToolTip
from desktop_backend import create_backend, merge_items, application_target, canonical
from dock_state import RecentApplications
from dock_icons import draw_builtin
from instance import VERSION
from icon_visuals import visible_icon, application_fallback
from minimized_tiles import MinimizedTiles, identity as tile_identity
from folder_content import ORDERS
from dock_zones import pin_zone, zoned_items, neighbor
from appearance import normalize as normalize_appearance, background as appearance_background
from display_policy import MODES, EdgeHandoff, screen_identity, select_screen
from shell_drop import accepts_mime, drop_action, drop_entries, local_paths

APP = 'BrisaDock'


class WindowWatcher(QThread):
    changed = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_event = threading.Event()
        self.paths_lock = threading.Lock()
        self.paths = []
        self.backend = None

    def set_paths(self, paths):
        with self.paths_lock:
            self.paths = list(paths)

    def run(self):
        try:
            self.backend = create_backend()
            if hasattr(self.backend, 'hidden'):
                self.backend.hidden.configure(self.parent().config_path.with_name('hidden-windows.json'))
            cache = {}
            refreshed = {}
            last_windows = []
            while not self.stop_event.is_set():
                with self.paths_lock:
                    paths = list(self.paths)
                for path in paths:
                    if self.stop_event.is_set():
                        break
                    if path not in cache or (path.startswith('shell:AppsFolder\\') and time.monotonic()-refreshed.get(path, 0) > 5):
                        cache[path] = application_target(path)
                        refreshed[path] = time.monotonic()
                try:
                    windows = self.backend.snapshot()
                    last_windows = windows
                    self.changed.emit(windows, dict(cache), self.backend.status)
                except Exception as error:
                    self.changed.emit(last_windows, dict(cache), f'Integración temporalmente no disponible: {type(error).__name__}')
                self.stop_event.wait(1.0)
        except Exception as error:
            self.changed.emit([], {}, f'Integración no disponible: {type(error).__name__}')
        finally:
            if self.backend:
                self.backend.close()


def read_settings(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {}
        data['pins'] = [p for p in data.get('pins', []) if isinstance(p, dict)
                        and isinstance(p.get('path'), str) and isinstance(p.get('name'), str)][:18]
        data['size'] = max(16, min(72, int(data.get('size', 48))))
        data['magnification'] = max(1.1, min(1.7, float(data.get('magnification', 1.48))))
        data['hide_delay'] = max(.2, min(5., float(data.get('hide_delay', 1.))))
        data['edge'] = data.get('edge') if data.get('edge') in ('bottom', 'left', 'right') else 'bottom'
        data['gap'] = max(0, min(32, int(data.get('gap', 0))))
        data['animation_style'] = data.get('animation_style') if data.get('animation_style') in ('scale', 'sheet') else 'scale'
        return data
    except (OSError, ValueError, TypeError):
        return {}


class Dock(QWidget):
    closed = Signal()
    appearance_changed = Signal()
    display_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Brisa Dock '+VERSION)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.blur_shelf = None
        self.application_icons = None
        profile = QApplication.instance().property('brisaProfileDir') or QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
        self.config_path = Path(profile) / 'settings.json'
        if os.name == 'nt':
            from app_icons import ApplicationIcons
            self.application_icons = ApplicationIcons(self)
            self.application_icons.changed.connect(self.application_icon_changed)
            QApplication.instance().aboutToQuit.connect(self.application_icons.close)
        self.config = read_settings(self.config_path)
        self.appearance = normalize_appearance(self.config.get('appearance'))
        self.size = self.config.get('size', 48)
        self.auto_hide = bool(self.config.get('auto_hide', False))
        self.magnify = bool(self.config.get('magnify', True))
        self.light = bool(self.config.get('light', False))
        self.blur_enabled = bool(self.config.get('blur_enabled', False))
        self.magnification = self.config.get('magnification', 1.48)
        self.hide_delay = self.config.get('hide_delay', 1.)
        self.edge = self.config.get('edge', 'bottom')
        self.screen_mode = self.config.get('screen_mode', 'follow_edge')
        if self.screen_mode not in MODES:
            self.screen_mode = 'follow_edge'
        self.screen_id = self.config.get('screen_id', '')
        if not isinstance(self.screen_id, str):
            self.screen_id = ''
        self.screen_handoff = EdgeHandoff()
        self.gap = self.config.get('gap', 0)
        self.reserve_space = bool(self.config.get('reserve_space', False))
        self.appbar = None
        self.animate_minimize = bool(self.config.get('animate_minimize', False))
        self.animate_window_buttons = bool(self.config.get('animate_window_buttons', False))
        self.caption_minimizer = None
        self.effects = []
        self.minimized_bounds = {}
        self.animation_style = self.config.get('animation_style', 'scale')
        self.sheet_frames = OrderedDict()
        self.sheet_pending = {}
        self.capture_broker = None
        self.minimize_into_app = bool(self.config.get('minimize_into_app', False))
        self.click_previews = bool(self.config.get('click_previews', False))
        self.minimized_tiles = MinimizedTiles()
        self.trash_count = None
        self.recycle_monitor = None
        self.stack_previews = None
        self.keyboard_hotkey = bool(self.config.get('keyboard_hotkey', True))
        self.dock_hotkey = None
        self.keyboard_controller = None
        self.keyboard_index = -1
        self.keyboard_launch = False
        self.last_context_action = None
        self.tile_capture_broker = None
        self.tile_pending = {}
        self.catalog = []
        self.catalog_time = 0
        self.catalog_worker = None
        self.hide_fullscreen = bool(self.config.get('hide_fullscreen', True))
        self.fullscreen_active = False
        self.show_recent = bool(self.config.get('show_recent', True))
        self.recent = RecentApplications(self.config.get('recent_apps', []))
        self.show_attention = bool(self.config.get('show_attention', True))
        self.attention_requests = {}
        self.shell_attention = None
        self.pins = self.config.get('pins', [])
        self.provider = QFileIconProvider()
        self.icon_cache = {}
        self.rects = []
        self.hover = -1
        self.hidden_amount = 0.0
        self.zoom = []
        self.bounce_index = -1
        self.bounce_until = 0
        self.menu_open = False
        self.last_inside = time.monotonic()
        self.windows = []
        self.targets = {}
        self.integration_status = 'Detectando ventanas…'
        self.press_pos = None
        self.press_pin = None
        self.press_item = None
        self.drag_target = None
        self.drag_snapshot = None
        self.drag_position = None
        self.drag_offsets = {}
        self.reorder_timer = QTimer(self)
        self.reorder_timer.setInterval(16)
        self.reorder_timer.timeout.connect(self.animate_reorder)
        self.dragged = False
        self.drag_cancelled = False
        self.removed_pin = None
        self.external_drag = False
        self.drop_index = -1
        self.drop_label = ''
        self.document_opener = None
        self.target_opener = None
        self.refresh_items()
        self.watcher = WindowWatcher(self)
        self.watcher.set_paths([p['path'] for p in self.pins] + ['https://www.google.com'])
        self.watcher.changed.connect(self.receive_windows)
        self.watcher.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        from native_blur import NativeBlurShelf
        self.blur_shelf = NativeBlurShelf(self)
        QApplication.instance().aboutToQuit.connect(self.blur_shelf.close)
        self.show()
        self.position_dock()
        if os.name == 'nt':
            if QApplication.platformName() != 'offscreen':
                from keyboard_dock import DockHotkey
                self.dock_hotkey = DockHotkey(int(self.winId()), self.keyboard_hotkey)
            from recycle_bin import RecycleBinMonitor
            self.recycle_monitor = RecycleBinMonitor(self)
            self.recycle_monitor.changed.connect(self.recycle_state_changed)
            from appbar import AppBarHost
            self.appbar = AppBarHost(self)
            self.sync_reservation()
            try:
                from attention import ShellAttention
                self.shell_attention = ShellAttention(int(self.winId()))
            except (OSError, AttributeError):
                pass
        if os.name == 'nt' and QApplication.platformName() != 'offscreen':
            from caption_minimize import CaptionMinimizer
            self.caption_minimizer = CaptionMinimizer(self)
            self.caption_minimizer.requested.connect(self.caption_minimize_requested, Qt.QueuedConnection)
            self.sync_caption_minimizer()
        self.geometry_pending = False
        for screen in QApplication.screens():
            self.watch_screen(screen)
        QApplication.instance().screenAdded.connect(self.screen_added)
        QApplication.instance().screenRemoved.connect(self.screen_removed)
        QApplication.instance().primaryScreenChanged.connect(self.display_topology_changed)
        from folder_stacks import StackPreviews
        self.stack_previews = StackPreviews(self, self.folder_preferences)
        self.stack_previews.changed.connect(self.update)
        self.sync_folder_previews()
        self.warm_sheet_capture()

    def warm_sheet_capture(self):
        if (os.name != 'nt' or QApplication.platformName() == 'offscreen'
                or not self.animate_minimize or self.animation_style != 'sheet'):
            return
        if self.capture_broker is None:
            from window_capture import CaptureBroker
            self.capture_broker = CaptureBroker(self)
            self.capture_broker.ready.connect(self.sheet_capture_ready)
        self.capture_broker.warm()

    def refresh_items(self):
        home = str(Path.home())
        downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or home
        file_windows = [w for w in self.windows if w['name'].lower() == ('explorer' if os.name == 'nt' else 'dolphin')]
        browser_target = self.targets.get('https://www.google.com', '')
        browser_windows = [w for w in self.windows if browser_target and w['key'] == canonical(browser_target)]
        self.items = [dict(name='Archivos', path=home, symbol='folder', windows=file_windows),
                      dict(name='Navegador', path='https://www.google.com', symbol='web', windows=browser_windows)]
        if browser_windows:
            # Keep the default-browser slot, but expose the app that owns its windows.
            browser = browser_windows[0]
            self.items[1] = dict(name=self.running_name(browser), path=browser['path'],
                                 windows=browser_windows, temporary=True)
        pinned_keys = {canonical(self.targets.get(p['path'], p['path'])) for p in self.pins if pin_zone(p, self.targets) == 'apps'}
        if browser_target and canonical(browser_target) in pinned_keys:
            self.items = self.items[:1]
        builtin_ids = {w['id'] for w in file_windows + browser_windows if w['key'] not in pinned_keys}
        applications, resources = zoned_items(self.pins, [w for w in self.windows if w['id'] not in builtin_ids], self.targets)
        self.items += applications
        self.items.append(dict(name='Aplicaciones', path='', symbol='apps'))
        self.recent_start = len(self.items)
        excluded = pinned_keys | {window['key'] for window in self.windows}
        excluded |= {canonical(browser_target)} if browser_target else set()
        if os.name == 'nt':
            excluded.add(canonical(str(Path(os.environ.get('WINDIR', 'C:\\Windows'))/'explorer.exe')))
        if self.show_recent:
            self.items += self.recent.visible(excluded)
        self.utility_start = len(self.items)
        self.items += resources
        self.items.append(dict(name='Descargas', path=downloads, symbol='download'))
        if os.name == 'nt':
            self.minimized_tiles.update(self.windows)
            if not self.minimize_into_app:
                self.items += self.minimized_tiles.items()
        self.items.append(dict(name=self.trash_label(), path='shell:RecycleBinFolder' if os.name == 'nt' else 'trash:/',
                               symbol='trash', full=bool(self.trash_count)))
        self.zoom = [1.0] * len(self.items)
        if hasattr(self, 'watcher'):
            self.watcher.set_paths([p['path'] for p in self.pins] + ['https://www.google.com'])
        self.resize_to_fit()
        self.sync_folder_previews()
        if self.keyboard_controller:
            self.keyboard_controller.reconcile()

    def sync_folder_previews(self):
        if self.stack_previews is not None:
            paths = [entry['path'] for entry in self.pins]
            paths += [entry['path'] for entry in self.items if entry.get('symbol') == 'download']
            self.stack_previews.sync(paths)

    def receive_windows(self, windows, targets, status):
        if self.menu_open or self.dragged or self.external_drag:
            return
        if self.show_recent and self.recent.observe(self.windows, windows):
            self.save()
        self.windows, self.targets, self.integration_status = windows, targets, status
        alive = {(window['pid'], window['id']) for window in windows}
        active_apps = {window['key'] for window in windows if window.get('active')}
        self.attention_requests = {identity: stamp for identity, stamp in self.attention_requests.items()
                                   if identity in alive and not any((w['pid'], w['id']) == identity and w['key'] in active_apps for w in windows)}
        if self.show_attention:
            for window in windows:
                if window.get('urgent') and window['key'] not in active_apps:
                    self.request_attention((window['pid'], window['id']))
        live = {(window['pid'], window['id']) for window in windows if window.get('minimized')}
        self.minimized_bounds = {key: bounds for key, bounds in self.minimized_bounds.items() if key in live}
        self.sheet_frames = OrderedDict((key, image) for key, image in self.sheet_frames.items() if key in live)
        fullscreen = False
        if os.name == 'nt' and self.watcher.backend and hasattr(self.watcher.backend, 'u'):
            from ctypes import wintypes as W
            api = self.watcher.backend.u
            api.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
            api.MonitorFromWindow.restype = W.HMONITOR
            monitor = api.MonitorFromWindow(int(self.winId()), 2)
            fullscreen = any(window.get('active') and window.get('fullscreen') and window.get('monitor') == monitor for window in windows)
        if fullscreen != self.fullscreen_active:
            self.fullscreen_active = fullscreen
            self.sync_reservation()
            self.position_dock()
        old_zoom = self.zoom[:]
        old_identity = [(i['path'], i.get('pin_index'), i.get('window_id')) for i in self.items]
        self.refresh_items()
        if old_identity == [(i['path'], i.get('pin_index'), i.get('window_id')) for i in self.items]:
            self.zoom = old_zoom
        self.sync_caption_minimizer()
        self.update()

    def trash_label(self):
        if self.trash_count is None:
            return 'Papelera'
        return 'Papelera vacía' if self.trash_count == 0 else f'Papelera · {self.trash_count} '+('elemento' if self.trash_count == 1 else 'elementos')

    def recycle_state_changed(self, count):
        self.trash_count = count
        for item in self.items:
            if item.get('symbol') == 'trash':
                item.update(name=self.trash_label(), full=bool(count))
        self.update()

    def resize_to_fit(self):
        if not QApplication.screens():
            return
        current = getattr(self, 'dock_screen', QApplication.screenAt(QCursor.pos()))
        self.dock_screen = select_screen(self.screen_mode, self.screen_id, current, QApplication.screens(), QApplication.primaryScreen())
        area = self.anchor_area()
        width = area.width() if self.edge == 'bottom' else area.height()
        # Keep all launchers accessible even when many are pinned.
        spacing = min(18, max(6, self.size*.375))
        self.step = min(self.size + spacing, max(12, (width - 56 - 2*self.size) / len(self.items)))
        self.base_size = min(self.size, self.step - 6)
        length = int(self.step * len(self.items) + 44 + 2*self.size)
        self.resize(length, 186) if self.edge == 'bottom' else self.resize(186, length)
        self.position_dock()

    def anchor_area(self):
        if self.dock_screen not in QApplication.screens():
            self.dock_screen = QApplication.primaryScreen()
        area = self.dock_screen.availableGeometry()
        if self.fullscreen_active and self.hide_fullscreen:
            return self.dock_screen.geometry()
        if self.appbar and self.appbar.rect is not None:
            if self.edge == 'bottom':
                area.setBottom(self.appbar.rect.bottom())
            elif self.edge == 'left':
                area.setLeft(self.appbar.rect.left())
            else:
                area.setRight(self.appbar.rect.right())
        return area

    def schedule_geometry(self, *args):
        if getattr(self, 'closing', False) or getattr(self, 'geometry_pending', False):
            return
        self.geometry_pending = True
        def update():
            self.geometry_pending = False
            if getattr(self, 'closing', False):
                return
            self.sync_reservation()
            self.resize_to_fit()
        QTimer.singleShot(80, update)

    def screen_removed(self, screen):
        if self.dock_screen == screen or self.dock_screen not in QApplication.screens():
            self.dock_screen = QApplication.primaryScreen()
        self.display_topology_changed()

    def watch_screen(self, screen):
        screen.availableGeometryChanged.connect(self.schedule_geometry)
        screen.geometryChanged.connect(self.display_topology_changed)
        screen.logicalDotsPerInchChanged.connect(self.display_topology_changed)

    def screen_added(self, screen):
        self.watch_screen(screen)
        self.display_topology_changed()

    def display_topology_changed(self, *args):
        if getattr(self, 'closing', False):
            return
        if hasattr(self, 'reorder_timer'):
            self.cancel_interrupted_gesture()
        # Windows/Qt deliver monitor, DPI and work-area changes separately.
        # Rebuild after the burst, including when the surviving screen is unchanged.
        self.display_revision = getattr(self, 'display_revision', 0) + 1
        revision = self.display_revision
        def recover():
            if not getattr(self, 'closing', False) and self.display_revision == revision:
                self.recover_display()
        QTimer.singleShot(120, recover)
        QTimer.singleShot(900, recover)

    def cancel_interrupted_gesture(self):
        if self.press_pos is not None or self.drag_snapshot is not None:
            self.restore_drag_order()
            self.press_pos, self.press_pin, self.press_item = None, None, None
            self.drag_position, self.drag_target = None, None
            self.dragged = self.drag_cancelled = False
            QToolTip.hideText()
        self.drag_offsets.clear()
        self.reorder_timer.stop()

    def recover_display(self):
        if getattr(self, 'closing', False):
            return
        if self.press_pos is not None:
            # A gesture begun after the notification must survive the delayed pass.
            QTimer.singleShot(120, self.recover_display)
            return
        screens = QApplication.screens()
        if not screens:
            return
        self.dock_screen = select_screen(self.screen_mode, self.screen_id, self.dock_screen, screens, QApplication.primaryScreen())
        self.screen_handoff.reset()
        for effect in self.effects[:]:
            effect.close()
        if self.appbar:
            self.appbar.release()
        self.fullscreen_active = False
        self.hidden_amount = 0.0
        self.last_inside = time.monotonic()
        if self.blur_shelf:
            # Discard the compositor surface belonging to the previous topology.
            self.blur_shelf.invalidate()
        self.resize_to_fit()
        self.sync_reservation()
        self.position_dock()
        self.raise_without_focus()
        self.update()
        self.sync_caption_minimizer()
        self.display_changed.emit()

    def raise_without_focus(self):
        if os.name == 'nt' and QApplication.platformName() != 'offscreen':
            import ctypes as C
            from ctypes import wintypes as W
            user = C.WinDLL('user32')
            user.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
            user.SetWindowPos.restype = W.BOOL
            user.SetWindowPos(int(self.winId()), -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
        else:
            self.raise_()

    def at_reveal_edge(self, cursor):
        # Both edges matter: Explorer can temporarily report an old work area
        # after unplugging a display or changing its taskbar auto-hide state.
        for area in (self.anchor_area(), self.dock_screen.geometry()):
            if self.edge == 'bottom':
                hit = area.bottom()-3 <= cursor.y() <= area.bottom() and self.x() <= cursor.x() < self.x()+self.width()
            elif self.edge == 'left':
                hit = area.left() <= cursor.x() <= area.left()+3 and self.y() <= cursor.y() < self.y()+self.height()
            else:
                hit = area.right()-3 <= cursor.x() <= area.right() and self.y() <= cursor.y() < self.y()+self.height()
            if hit:
                return True
        return False

    def sync_reservation(self):
        if self.appbar:
            if self.reserve_space and not self.auto_hide and not (self.fullscreen_active and self.hide_fullscreen):
                if not self.appbar.reserve(self.edge, self.shelf_rect().height()+9+self.gap if self.edge == 'bottom' else self.shelf_rect().width()+9+self.gap):
                    self.integration_status = 'Windows no pudo reservar el espacio del dock.'
            else:
                self.appbar.release()

    def layout_centers(self):
        widths = [self.step + self.base_size*(zoom-1) for zoom in self.zoom]
        length = self.width() if self.edge == 'bottom' else self.height()
        cursor = (length - sum(widths))/2
        centers = []
        for width in widths:
            centers.append(cursor + width/2)
            cursor += width
        return centers, sum(widths) + 26

    def position_dock(self):
        if not QApplication.screens():
            return
        if self.dock_screen not in QApplication.screens():
            self.dock_screen = QApplication.primaryScreen()
        area = self.anchor_area()
        offset = round(self.hidden_amount * (112+self.gap))
        if self.edge == 'bottom':
            self.move(area.center().x() - self.width() // 2, area.bottom()-self.height()+1-self.gap+offset)
        elif self.edge == 'left':
            self.move(area.left()+self.gap-offset, area.center().y()-self.height()//2)
        else:
            self.move(area.right()-self.width()+1-self.gap+offset, area.center().y()-self.height()//2)
        controller = getattr(self, 'keyboard_controller', None)
        if controller and controller.isVisible():
            controller.setGeometry(self.geometry())
        if self.blur_shelf:
            self.blur_shelf.sync(self.blur_shelf.rectangle)

    def item_rect(self, center, size, lift=0):
        if self.edge == 'bottom':
            return QRectF(center-size/2, 165-size-lift, size, size)
        if self.edge == 'left':
            return QRectF(21+lift, center-size/2, size, size)
        return QRectF(165-size-lift, center-size/2, size, size)

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(size=self.size, auto_hide=self.auto_hide, magnify=self.magnify, light=self.light,
                    magnification=self.magnification, hide_delay=self.hide_delay,
                    edge=self.edge, gap=self.gap, reserve_space=self.reserve_space,
                    animate_minimize=self.animate_minimize,
                    animation_style=self.animation_style,
                    hide_fullscreen=self.hide_fullscreen,
                    show_recent=self.show_recent, recent_apps=self.recent.serialize(), show_attention=self.show_attention,
                    folder_mode=self.config.get('folder_mode', 'auto'), pins=self.pins)
        if self.drag_snapshot is not None:
            data['pins'] = self.drag_snapshot
        data['screen_mode'] = self.screen_mode
        data['screen_id'] = self.screen_id
        data['folder_order'] = self.config.get('folder_order', 'date')
        data['folder_display'] = self.config.get('folder_display', 'folder')
        data['animate_window_buttons'] = self.animate_window_buttons
        data['minimize_into_app'] = self.minimize_into_app
        data['click_previews'] = self.click_previews
        data['keyboard_hotkey'] = self.keyboard_hotkey
        data['appearance'] = self.appearance
        data['blur_enabled'] = self.blur_enabled
        for key in ('folder_preferences', 'app_categories', 'apps_view', 'apps_layout'):
            if key in self.config:
                data[key] = self.config[key]
        temporary = self.config_path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.config_path)
        except OSError as error:
            QMessageBox.warning(self, APP, f'No se pudo guardar la configuración:\n{error}')

    def follow_pointer_screen(self):
        blocked = (self.screen_mode != 'follow_edge' or self.menu_open or self.press_pos is not None
                   or self.external_drag or self.effects or self.sheet_pending or self.tile_pending
                   or self.keyboard_controller is not None or QApplication.mouseButtons() != Qt.NoButton
                   or getattr(self, 'closing', False))
        target = self.screen_handoff.poll(QCursor.pos(), self.edge, QApplication.screens(), self.dock_screen,
                                          time.monotonic(), blocked=bool(blocked))
        if target is not None:
            self.dock_screen = target
            self.recover_display()

    def tick(self):
        if not QApplication.screens():
            self.screen_handoff.reset()
            return
        self.follow_pointer_screen()
        if self.keyboard_controller and self.keyboard_controller.isVisible():
            self.last_inside = time.monotonic()
            self.hover = self.keyboard_index
            return
        if self.press_pos is not None or self.menu_open:
            self.last_inside = time.monotonic()
            self.update()
            return
        point = self.mapFromGlobal(QCursor.pos())
        inside = self.rect().contains(point)
        if self.hidden_amount > .8:
            inside = self.at_reveal_edge(QCursor.pos())
            if inside:
                self.raise_without_focus()
        now = time.monotonic()
        if inside or self.menu_open or self.effects or self.sheet_pending or self.press_pos is not None or self.external_drag:
            self.last_inside = now
        timed_hide = self.auto_hide and now-self.last_inside > self.hide_delay
        full_hide = self.hide_fullscreen and self.fullscreen_active and now-self.last_inside > min(self.hide_delay, .35)
        target = 1.0 if timed_hide or full_hide else 0.0
        old = self.hidden_amount
        self.hidden_amount += (target - old) * 0.22
        if abs(target - self.hidden_amount) < 0.01:
            self.hidden_amount = target
        changed = abs(self.hidden_amount - old) > 0.001
        old_hover = self.hover
        self.hover = -1
        axis = point.x() if self.edge == 'bottom' else point.y()
        centers, _ = self.layout_centers()
        if inside and self.hidden_amount < 0.3:
            for i in range(len(self.items)):
                center = centers[i]
                if abs(axis - center) < self.step / 2:
                    self.hover = i
        for i in range(len(self.items)):
            distance = abs(axis - centers[i])
            goal = 1 + (self.magnification-1) * math.exp(-((distance / (self.step * 1.15)) ** 2)) if self.magnify and inside and target == 0 else 1
            previous = self.zoom[i]
            self.zoom[i] += (goal - previous) * 0.25
            changed |= abs(previous - self.zoom[i]) > 0.001
        if changed:
            self.position_dock()
        if self.edge != 'bottom' and self.hover != old_hover and not self.external_drag:
            if self.hover >= 0:
                import html
                QToolTip.showText(QCursor.pos(), html.escape(self.items[self.hover]['name']), self)
            else:
                QToolTip.hideText()
        attention_animation = any(now-stamp < 1.2 for stamp in self.attention_requests.values())
        if changed or inside or now < self.bounce_until or attention_animation:
            self.update()
        self.timer.setInterval(16 if changed or inside or now < self.bounce_until or attention_animation else 80)

    def application_icon_changed(self, path):
        key = os.path.normcase(os.path.abspath(path))
        for alias in list(getattr(self, 'icon_cache', {})):
            if os.path.normcase(os.path.abspath(alias)) == key:
                self.icon_cache.pop(alias, None)
        self.update()

    def icon_for(self, item, high_resolution=True):
        path = item['path']
        if self.application_icons and (item.get('directory') or item.get('kind') == 'folder'
                                      or path.startswith('shell:AppsFolder\\')
                                      or Path(path).suffix.lower() in ('.exe', '.lnk', '.ico')):
            improved = (self.application_icons.request(path) if high_resolution
                        else self.application_icons.cached(path))
            if visible_icon(improved):
                return improved
        if path not in self.icon_cache:
            icon = self.provider.icon(QFileInfo(path)) if Path(path).exists() else QIcon.fromTheme('application-x-executable')
            if path.endswith('.desktop'):
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    parser.read(path, encoding='utf-8')
                    name = parser.get('Desktop Entry', 'Icon', fallback='')
                    themed = QIcon(name) if os.path.isabs(name) else QIcon.fromTheme(name)
                    if not themed.isNull():
                        icon = themed
                except (OSError, configparser.Error):
                    pass
            self.icon_cache[path] = icon
        icon = self.icon_cache[path]
        # Native providers are lazy: rasterize only when visible/high-resolution
        # artwork is requested, not while constructing every catalog row.
        if not icon.isNull() and (not high_resolution or visible_icon(icon)):
            return icon
        if item.get('directory') or item.get('kind') == 'folder':
            from file_icons import stack_icon
            return stack_icon(path, directory=True)
        return application_fallback(item.get('name') or Path(path).stem, path)

    def shelf_rect(self):
        _, shelf_width = self.layout_centers()
        compact = max(0, 48-self.base_size)
        if self.edge == 'bottom':
            return QRectF((self.width()-shelf_width)/2, 108+compact, shelf_width, 69-compact)
        return QRectF(9 if self.edge == 'left' else 108+compact,
                      (self.height()-shelf_width)/2, 69-compact, shelf_width)

    def paintEvent(self, event):
        centers, _ = self.layout_centers()
        shelf = self.shelf_rect()
        if self.blur_shelf:
            self.blur_shelf.sync(shelf)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for pad in range(7, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 5))
            painter.drawRoundedRect(shelf.adjusted(-pad, -pad / 2, pad, pad), 23, 23)
        painter.setBrush(appearance_background(self.appearance, 'dock', self.light))
        painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
        painter.drawRoundedRect(shelf, 21, 21)
        separators = [self.utility_start]
        if self.recent_start < self.utility_start:
            separators.append(self.recent_start)
        for boundary in separators:
            if not 0 < boundary < len(centers):
                continue
            sx = (centers[boundary-1] + centers[boundary])/2
            painter.setPen(QPen(QColor(255, 255, 255, 65), 1))
            if self.edge == 'bottom':
                inset = min(15, shelf.height()*.22)
                painter.drawLine(QPointF(sx, shelf.top()+inset), QPointF(sx, shelf.bottom()-inset))
            else:
                inset = min(15, shelf.width()*.22)
                painter.drawLine(QPointF(shelf.left()+inset, sx), QPointF(shelf.right()-inset, sx))
        self.rects = []
        for i, item in enumerate(self.items):
            painter.save()
            if item.get('windows') and all(window.get('hidden') for window in item['windows']):
                painter.setOpacity(.5)
            if self.dragged and not self.drag_cancelled and self.press_pin is not None and item.get('pin_index') == self.press_pin:
                painter.setOpacity(.16)
            size = self.base_size * self.zoom[i]
            bounce = max(0, self.bounce_until - time.monotonic())
            lift = abs(math.sin(bounce * 15)) * 13 if i == self.bounce_index and bounce else 0
            stamps = [self.attention_requests[(w['pid'], w['id'])] for w in item.get('windows', [])
                      if (w['pid'], w['id']) in self.attention_requests]
            if stamps and self.show_attention:
                age = time.monotonic()-max(stamps)
                if age < 1.2:
                    lift = max(lift, abs(math.sin(age*10))*12*(1-age/1.2))
            visual_center = centers[i] + self.drag_offsets.get(item['path'], 0)
            rect = self.item_rect(visual_center, size, lift)
            self.rects.append(rect)
            if self.dragged and not self.drag_cancelled and self.drag_target is not None and item.get('pin_index') == self.drag_target:
                painter.setPen(QPen(QColor('#8dd5ff'), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), 12, 12)
            if i == self.drop_index:
                painter.setPen(QPen(QColor('#8dd5ff'), 2))
                painter.setBrush(QColor(65, 155, 225, 90))
                painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 12, 12)
            symbol = item.get('symbol')
            preview = self.stack_previews.result(item['path']) if self.stack_previews and item['path'] else None
            if item.get('minimized_window'):
                self.minimized_tiles.draw(painter, rect, item, self.icon_for(item), self.light)
            elif preview and preview['directory'] and symbol != 'folder':
                if preview.get('display') == 'stack':
                    self.stack_previews.draw(painter, rect, preview)
                elif symbol == 'download':
                    draw_builtin(painter, rect, symbol)
                else:
                    icon = self.application_icons.request(item['path']) if self.application_icons else None
                    (icon or self.stack_previews.folder_icon).paint(painter, rect.toRect())
            elif symbol:
                draw_builtin(painter, rect, 'trash_full' if symbol == 'trash' and item.get('full') else symbol)
            else:
                self.icon_for(item).paint(painter, rect.toRect())
            if i == self.keyboard_index:
                painter.setPen(QPen(QColor('#79bcff'), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), 12, 12)
            if stamps and self.show_attention:
                painter.setPen(QPen(QColor('white'), 1))
                painter.setBrush(QColor('#efad32'))
                painter.drawEllipse(QPointF(rect.right()-3, rect.top()+4), 5, 5)
            if item.get('windows') and not item.get('minimized_window'):
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor('#525256' if self.light else '#e9e9ec'))
                point = QPointF(rect.center().x(), 171) if self.edge == 'bottom' else QPointF(15 if self.edge == 'left' else 171, rect.center().y())
                painter.drawEllipse(point, 2.4, 2.4)
            if i == self.hover and self.edge == 'bottom' and not self.external_drag and not self.menu_open and not self.dragged:
                text = item['name'][:45]
                painter.setFont(QFont('Segoe UI' if os.name == 'nt' else 'Sans Serif', 10))
                tw = painter.fontMetrics().horizontalAdvance(text) + 20
                tx = max(1, min(self.width() - tw - 1, rect.center().x() - tw / 2))
                label = QRectF(tx, max(1, rect.top()-37), tw, 27)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(250, 250, 252, 245) if self.light else QColor(35, 35, 39, 245))
                painter.drawRoundedRect(label, 7, 7)
                pointer = max(label.left()+8, min(label.right()-8, rect.center().x()))
                painter.drawPolygon(QPolygonF([QPointF(pointer-5, label.bottom()-.5),
                                              QPointF(pointer+5, label.bottom()-.5), QPointF(pointer, label.bottom()+6)]))
                painter.setPen(QColor('#27272b' if self.light else '#f5f5f7'))
                painter.drawText(label, Qt.AlignCenter, text)
            painter.restore()
        if self.dragged and not self.drag_cancelled and self.drag_position is not None and self.press_item:
            size = self.base_size * 1.08
            rect = QRectF(self.drag_position.x()-size/2, self.drag_position.y()-size/2, size, size)
            item = self.press_item
            painter.save()
            painter.setOpacity(.95)
            preview = self.stack_previews.result(item['path']) if self.stack_previews else None
            if preview and preview.get('display') == 'stack':
                self.stack_previews.draw(painter, rect, preview)
            elif item.get('symbol'):
                draw_builtin(painter, rect, item['symbol'])
            elif preview and preview['directory']:
                icon = self.application_icons.request(item['path']) if self.application_icons else None
                (icon or self.stack_previews.folder_icon).paint(painter, rect.toRect())
            else:
                self.icon_for(item).paint(painter, rect.toRect())
            painter.restore()
        if self.external_drag and self.drop_label:
            painter.setFont(QFont('Segoe UI' if os.name == 'nt' else 'Sans Serif', 10))
            text = painter.fontMetrics().elidedText(self.drop_label, Qt.ElideRight, self.width()-28)
            width = painter.fontMetrics().horizontalAdvance(text)+20
            label = QRectF((self.width()-width)/2, 10, width, 27)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(20, 25, 35, 240))
            painter.drawRoundedRect(label, 7, 7)
            painter.setPen(QColor('white'))
            painter.drawText(label, Qt.AlignCenter, text)

    def launch(self, index, force_new=False):
        item = self.items[index]
        if item.get('minimized_window'):
            if item.get('window_overflow'):
                self.window_overview(item)
            else:
                self.window_action('activate', item['windows'][0])
            return
        if item.get('symbol') == 'apps':
            self.applications_panel()
            return
        if item.get('symbol') == 'add':
            self.add_pin()
            return
        windows = item.get('windows', [])
        if windows and not force_new:
            if self.click_previews and len(windows) > 1:
                self.window_overview(item)
                return
            self.show_application(item)
            # macOS-style primary click activates; it does not toggle minimize.
            chosen = next((w for w in windows if w['active']), next((w for w in windows if not w['minimized']), windows[0]))
            self.window_action('activate', chosen)
            return
        if not force_new and Path(item['path']).is_dir() and item.get('symbol') != 'folder':
            self.folder_stack(item)
            return
        target = item['path']
        if self.open_path(target):
            self.bounce_index = index
            self.bounce_until = time.monotonic() + 0.65

    def open_path(self, target):
        if os.name == 'nt':
            if self.target_opener is None:
                from target_opener import TargetOpener
                self.target_opener = TargetOpener(self)
                self.target_opener.finished.connect(self.target_open_finished)
            return self.target_opener.request(target)
        try:
            if target.endswith('.desktop'):
                subprocess.Popen(['gio', 'launch', target])
            elif os.name != 'nt' and Path(target).is_file() and os.access(target, os.X_OK):
                subprocess.Popen([target])
            else:
                url = QUrl(target) if target.startswith(('https://', 'http://', 'shell:', 'trash:')) else QUrl.fromLocalFile(target)
                if not QDesktopServices.openUrl(url):
                    raise OSError('El sistema no pudo abrir este elemento.')
            return True
        except OSError as error:
            QMessageBox.warning(self, APP, str(error))
            return False

    def target_open_finished(self, target, success, message):
        if getattr(self, 'closing', False):
            return
        self.integration_status = message
        if not success:
            QToolTip.showText(self.mapToGlobal(self.rect().center()), message, self, self.rect(), 8000)

    def show_application(self, item):
        hidden = getattr(self.watcher.backend, 'hidden', None)
        if hidden:
            for window in item.get('windows', []):
                hidden.show(window)

    def hide_application(self, item, others=False):
        hidden = getattr(self.watcher.backend, 'hidden', None)
        if hidden is None:
            return
        selected_ids = {window['id'] for window in item.get('windows', [])}
        targets = ([window for window in self.windows if window['id'] not in selected_ids
                    and window['key'] not in {entry['key'] for entry in item.get('windows', [])}]
                   if others else item.get('windows', []))
        # Finish pending effects first, so an overlay cannot later re-show a hidden window.
        target_ids = {window['id'] for window in targets}
        for token, pending in list(self.tile_pending.items()):
            if pending['id'] in target_ids:
                self.tile_pending.pop(token, None)
        for token, request in list(self.sheet_pending.items()):
            if request[0]['id'] in target_ids:
                self.sheet_pending.pop(token, None)
        for effect in self.effects[:]:
            if effect.window['id'] in target_ids:
                effect.suppress_restore = True
                effect.close()
        for window in targets:
            self.minimized_tiles.forget(window)
        failures = sum(not hidden.hide(window) for window in targets if hidden.can_hide(window))
        if failures:
            self.integration_status = 'Windows no permitió ocultar alguna ventana; puedes seguir usando el dock.'

    def show_hidden_applications(self):
        hidden = getattr(self.watcher.backend, 'hidden', None)
        if hidden:
            hidden.restore_all()

    def receive_catalog(self, entries, complete):
        self.catalog = list(entries)
        if complete:
            self.catalog_time = time.monotonic()

    def request_attention(self, identity):
        if self.show_attention and identity and identity not in self.attention_requests:
            self.attention_requests[identity] = time.monotonic()
            self.update()

    def handle_surface_event(self, message, parameter=0):
        display_change = message in (0x7E, 0x2E0, 0x31E)  # Display / DPI / DWM composition.
        resumed = message == 0x218 and parameter in (6, 7, 0x12)
        if display_change or resumed:
            self.display_topology_changed()
            return True
        return False

    def nativeEvent(self, event_type, message):
        if os.name == 'nt':
            from ctypes import wintypes as W
            native = W.MSG.from_address(int(message))
            self.handle_surface_event(native.message, native.wParam)
        hotkey = getattr(self, 'dock_hotkey', None)
        if hotkey and hotkey.matches(message):
            QTimer.singleShot(0, self.begin_keyboard_navigation)
            return True, 0
        hook = getattr(self, 'shell_attention', None)
        if hook and hook.available:
            from ctypes import wintypes as W
            native = W.MSG.from_address(int(message))
            if native.message == hook.message and native.wParam == 0x8006:
                self.request_attention(hook.identity(native.lParam))
        return super().nativeEvent(event_type, message)

    def begin_keyboard_navigation(self, preferred=None):
        if self.menu_open or self.press_pos is not None or self.external_drag or getattr(self, 'closing', False):
            return
        if self.keyboard_controller:
            self.keyboard_controller.raise_()
            self.keyboard_controller.setFocus()
            return
        from keyboard_dock import KeyboardDock, item_identity
        if preferred is None:
            active = next((item for item in self.items if any(window.get('active') for window in item.get('windows', []))), self.items[0])
            preferred = item_identity(active)
        self.reveal_instance()
        self.zoom = [1.0]*len(self.items)
        controller = KeyboardDock(self, preferred)
        self.keyboard_controller = controller
        controller.show()
        controller.raise_()
        controller.activateWindow()
        controller.setFocus()
        self.update()

    def reveal_instance(self):
        self.show()
        self.hidden_amount = 0
        self.last_inside = time.monotonic()+1.5
        self.position_dock()
        self.raise_()

    def clear_recent(self):
        self.recent.clear()
        self.save()
        self.refresh_items()

    def remove_recent(self, key):
        self.recent.remove(key)
        self.save()
        self.refresh_items()

    def applications_panel(self):
        from apps_panel import ApplicationsPanel
        self.menu_open = True
        panel = ApplicationsPanel(self)
        try:
            panel.exec()
        finally:
            self.menu_open = False
            self.last_inside = time.monotonic()
            panel.deleteLater()

    @staticmethod
    def running_name(window):
        name = window['name']
        return {'chrome': 'Google Chrome', 'msedge': 'Microsoft Edge',
                'firefox': 'Firefox', 'explorer': 'Explorador de archivos',
                'whatsapp.root': 'WhatsApp'}.get(name.lower(), name)

    def running_applications(self):
        groups = {}
        for window in self.windows:
            entry = groups.setdefault(window['key'], dict(name=self.running_name(window),
                                        path=window['path'], windows=[], key=window['key']))
            entry['windows'].append(window)
        return sorted(groups.values(), key=lambda entry: entry['name'].casefold())

    def running_applications_panel(self):
        from running_apps_panel import RunningAppsPanel
        self.menu_open = True
        panel = RunningAppsPanel(self)
        try:
            panel.exec()
        finally:
            self.menu_open = False
            self.last_inside = time.monotonic()
            panel.deleteLater()

    def pin_application(self, entry, persist=True):
        path = entry['path']
        key = canonical(self.targets.get(path, path))
        if any(canonical(self.targets.get(pin['path'], pin['path'])) == key for pin in self.pins):
            return
        if len(self.pins) >= 18:
            QMessageBox.information(self, APP, 'Se admiten hasta 18 accesos personalizados.')
            return
        if Path(path).exists() or (os.name == 'nt' and path.startswith('shell:AppsFolder\\')):
            self.pins.append(dict(name=entry['name'], path=path, kind='app'))
            if persist:
                self.save()
            self.refresh_items()

    def pin_catalog_entry(self, entry, global_position):
        key = canonical(self.targets.get(entry['path'], entry['path']))
        if any(canonical(self.targets.get(pin['path'], pin['path'])) == key for pin in self.pins):
            return True
        if len(self.pins) >= 18:
            self.integration_status = 'No se añadió la app: ya hay 18 accesos personalizados.'
            return False
        previous = len(self.pins)
        self.pin_application(entry)
        if len(self.pins) == previous:
            self.integration_status = 'No se añadió la app: su acceso ya no está disponible.'
            return False
        source = len(self.pins)-1
        destination = self.pin_drag_destination(self.mapFromGlobal(global_position), source)
        if destination is not None and source != destination:
            self.pins.insert(destination, self.pins.pop(source))
            self.save()
            self.refresh_items()
        return True

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragged:
            source = self.press_pin
            if not self.drag_cancelled and source is not None and 0 <= source < len(self.pins):
                if self.pin_removal_zone(event.position()):
                    self.drag_snapshot = None
                    self.change_pin(source, 0)
                elif self.pin_drag_destination(event.position(), source) is not None:
                    # The live preview already holds the final order. Commit once.
                    self.drag_snapshot = None
                    self.save()
                else:
                    self.restore_drag_order()
            else:
                self.restore_drag_order()
            QToolTip.hideText()
        if event.button() == Qt.LeftButton and not self.dragged and self.press_pos is not None:
            for i, rect in enumerate(self.rects):
                if rect.contains(event.position()):
                    if event.modifiers() & Qt.ControlModifier and self.items[i].get('windows'):
                        self.window_overview(self.items[i])
                    elif event.modifiers() & Qt.AltModifier:
                        for window in self.items[i].get('windows', []):
                            self.window_action('minimize', window)
                    else:
                        self.launch(i, bool(event.modifiers() & Qt.ShiftModifier))
                    break
        if event.button() == Qt.LeftButton:
            self.press_pos, self.press_pin, self.dragged = None, None, False
            self.press_item, self.drag_target = None, None
            self.drag_snapshot, self.drag_position = None, None
            self.drag_cancelled = False
            self.last_inside = time.monotonic()
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton and self.press_pos is not None:
            self.drag_cancelled = True
            self.restore_drag_order()
            QToolTip.hideText()
            return
        if event.button() == Qt.LeftButton:
            self.press_pos = event.position().toPoint()
            self.dragged = False
            self.drag_snapshot = None
            self.drag_position = None
            self.drag_cancelled = False
            self.press_item = next((dict(item) for item, rect in zip(self.items, self.rects)
                                    if rect.contains(event.position())), None)
            self.press_pin = next((item.get('pin_index') for item, rect in zip(self.items, self.rects)
                                   if rect.contains(event.position())), None)

    def mouseMoveEvent(self, event):
        if self.press_pos is None or self.drag_cancelled or not event.buttons() & Qt.LeftButton:
            return
        if not self.dragged and (event.position().toPoint() - self.press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        if self.press_pin is None:
            item = self.press_item or {}
            if not item.get('temporary') or item.get('minimized_window'):
                return
            self.dragged = True
            if len(self.pins) >= 18:
                return
            if self.drag_snapshot is None:
                self.drag_snapshot = [dict(pin) for pin in self.pins]
            self.pin_application(item, persist=False)
            key = canonical(item['path'])
            self.press_pin = next((i for i, pin in enumerate(self.pins)
                                   if canonical(self.targets.get(pin['path'], pin['path'])) == key), None)
            if self.press_pin is None:
                return
        self.dragged = True
        if self.drag_snapshot is None:
            self.drag_snapshot = [dict(pin) for pin in self.pins]
        self.drag_position = QPointF(event.position())
        self.drag_target = self.pin_drag_destination(event.position(), self.press_pin)
        if self.drag_target is not None and self.drag_target != self.press_pin and not self.pin_removal_zone(event.position()):
            centers, _ = self.layout_centers()
            before = {item['path']: centers[i]+self.drag_offsets.get(item['path'], 0)
                      for i, item in enumerate(self.items) if 'pin_index' in item}
            self.pins.insert(self.drag_target, self.pins.pop(self.press_pin))
            self.press_pin = self.drag_target
            self.refresh_items()
            centers, _ = self.layout_centers()
            self.drag_offsets = {item['path']: before.get(item['path'], centers[i])-centers[i]
                                 for i, item in enumerate(self.items) if 'pin_index' in item}
            self.reorder_timer.start()
        self.update()
        if not self.drag_cancelled:
            label = 'Quitar del dock' if self.pin_removal_zone(event.position()) else 'Mover acceso · clic derecho para cancelar'
            QToolTip.showText(event.globalPosition().toPoint(), label, self)

    def restore_drag_order(self):
        if self.drag_snapshot is not None:
            self.pins = [dict(pin) for pin in self.drag_snapshot]
            self.drag_snapshot = None
            self.drag_offsets.clear()
            self.reorder_timer.stop()
            self.refresh_items()
            self.update()

    def animate_reorder(self):
        self.drag_offsets = {path: offset*.68 for path, offset in self.drag_offsets.items() if abs(offset) > .3}
        if not self.drag_offsets:
            self.reorder_timer.stop()
        self.update()

    def pin_drag_destination(self, position, source):
        zone = pin_zone(self.pins[source], self.targets)
        centers, _ = self.layout_centers()
        choices = [(centers[i], item['pin_index']) for i, item in enumerate(self.items)
                   if 'pin_index' in item and pin_zone(self.pins[item['pin_index']], self.targets) == zone]
        if not choices:
            return None
        coordinate = position.x() if self.edge == 'bottom' else position.y()
        if not choices[0][0]-self.step/2 <= coordinate <= choices[-1][0]+self.step/2:
            return None
        return min(choices, key=lambda pair: abs(pair[0]-coordinate))[1]

    def pin_removal_zone(self, position):
        # Require a deliberate distance from the painted shelf, on every edge.
        centers, length = self.layout_centers()
        shelf = self.shelf_rect()
        return not shelf.adjusted(-64, -64, 64, 64).contains(position)

    def sync_caption_minimizer(self):
        if self.caption_minimizer is None:
            return
        enabled = self.animate_minimize and self.animate_window_buttons and not getattr(self, 'closing', False)
        backend = self.watcher.backend
        windows = []
        if enabled and backend and hasattr(backend, 'u'):
            monitor = backend.u.MonitorFromWindow(int(self.winId()), 2)
            windows = [w for w in self.windows if not w.get('minimized') and not w.get('fullscreen')
                       and w.get('monitor') == monitor and self.transition_target(w) is not None]
        self.caption_minimizer.configure(enabled, windows)

    def caption_minimize_requested(self, hwnd, pid):
        window = next((w for w in self.windows if w['id'] == hwnd and w['pid'] == pid), None)
        backend = self.watcher.backend
        identity = window or dict(id=hwnd, pid=pid)
        if not backend or not backend.valid(identity):
            return
        if window is None or getattr(self, 'closing', False):
            backend.minimize(identity)
            return
        self.last_inside = time.monotonic()
        self.window_action('minimize', window)

    def window_action(self, action, window, skip_tile_capture=False):
        if self.watcher.backend:
            try:
                if action in ('activate', 'close_window'):
                    for token, pending in list(self.tile_pending.items()):
                        if tile_identity(pending) == tile_identity(window):
                            self.tile_pending.pop(token, None)
                if action in ('activate', 'close_window'):
                    for token, request in list(self.sheet_pending.items()):
                        if request[0]['id'] == window['id']:
                            self.sheet_pending.pop(token, None)
                active = next((effect for effect in self.effects if tile_identity(effect.window) == tile_identity(window)), None)
                if active:
                    if action in ('minimize', 'activate') and active.redirect(action == 'activate'):
                        return
                    active.suppress_restore = action == 'close_window'
                    active.close()
                if (action == 'minimize' and not self.minimize_into_app and os.name == 'nt'
                        and hasattr(self.watcher.backend, 'u') and self.watcher.backend.valid(window)
                        and not self.watcher.backend.u.IsIconic(window['id'])):
                    self.minimized_tiles.reserve(window)
                    self.refresh_items()
                if (action == 'minimize' and not skip_tile_capture
                        and (not self.animate_minimize or (self.minimize_into_app and self.animation_style != 'sheet'))
                        and os.name == 'nt'
                        and self.request_tile_capture(window)):
                    return
                if self.animate_minimize and os.name == 'nt' and hasattr(self.watcher.backend, 'u'):
                    minimized = bool(self.watcher.backend.u.IsIconic(window['id']))
                    identity = (window['pid'], window['id'])
                    if self.animation_style == 'sheet':
                        if action == 'minimize' and not minimized and self.request_sheet_capture(window):
                            return
                        if action == 'activate' and minimized and identity in self.sheet_frames and identity in self.minimized_bounds:
                            if self.start_sheet_effect(window, self.sheet_frames[identity], self.minimized_bounds[identity], True):
                                return
                    if action == 'minimize' and not minimized and self.start_minimize_effect(window):
                        return
                    if action == 'activate' and minimized and (window['pid'], window['id']) in self.minimized_bounds and self.start_minimize_effect(window, True):
                        return
                success = getattr(self.watcher.backend, action)(window)
                if action in ('activate', 'close_window'):
                    self.minimized_tiles.forget(window)
                    self.minimized_bounds.pop((window['pid'], window['id']), None)
                    self.sheet_frames.pop((window['pid'], window['id']), None)
                    self.refresh_items()
                if action == 'activate' and not success:
                    self.integration_status = 'El sistema limitó la activación de esta ventana.'
            except Exception as error:
                self.integration_status = f'No se pudo completar la acción: {type(error).__name__}'

    def request_tile_capture(self, window):
        backend = self.watcher.backend
        if not hasattr(backend, 'u') or not backend.valid(window) or backend.u.IsIconic(window['id']):
            return False
        if any(tile_identity(entry) == tile_identity(window) for entry in self.tile_pending.values()):
            return True
        from window_capture import CaptureBroker
        if self.tile_capture_broker is None:
            self.tile_capture_broker = CaptureBroker(self)
            self.tile_capture_broker.ready.connect(self.tile_capture_ready)
        token = self.tile_capture_broker.request(window)
        if token is None:
            return False
        self.tile_pending[token] = dict(window)
        QTimer.singleShot(200, lambda: self.tile_capture_ready(token, None))
        return True

    def tile_capture_ready(self, token, image):
        window = self.tile_pending.pop(token, None)
        if window is None or getattr(self, 'closing', False):
            return
        backend = self.watcher.backend
        if backend and backend.valid(window):
            self.minimized_tiles.remember(window, image, detailed=self.minimize_into_app)
            self.window_action('minimize', window, skip_tile_capture=True)

    def start_minimize_effect(self, window, reverse=False):
        if any(effect.window['id'] == window['id'] for effect in self.effects):
            return True
        bounds = self.transition_target(window)
        if bounds is None or len(self.effects) >= 3:
            return False
        from minimize_effect import MinimizeEffect
        identity = (window['pid'], window['id'])
        effect = MinimizeEffect(self.watcher.backend, window, bounds, self.dock_screen, int(self.winId()),
                                reverse=reverse, origin=self.minimized_bounds.get(identity))
        effect.target_provider = lambda: self.transition_target(window)
        return self.track_effect(effect, identity)

    def transition_target(self, window):
        matching = [(index, item) for index, item in enumerate(self.items)
                    if any(tile_identity(candidate) == tile_identity(window) for candidate in item.get('windows', []))]
        if not matching:
            return None
        preferred = next(((index, item) for index, item in matching
                          if bool(item.get('minimized_window')) == (not self.minimize_into_app)), matching[0])
        index, _ = preferred
        centers, _ = self.layout_centers()
        rect = self.item_rect(centers[index], self.base_size*self.zoom[index])
        return QRect(self.mapToGlobal(rect.topLeft().toPoint()), rect.size().toSize())

    def track_effect(self, effect, identity):
        self.effects.append(effect)
        def finished():
            backend = self.watcher.backend
            minimized = backend and backend.valid(effect.window) and backend.u.IsIconic(effect.window['id'])
            if effect.started and not effect.reverse and minimized and not getattr(self, 'closing', False):
                if not self.minimize_into_app:
                    self.minimized_tiles.reserve(effect.window)
                self.minimized_bounds[identity] = effect.origin
                image = getattr(effect, 'image', None)
                if image is not None:
                    self.minimized_tiles.remember(effect.window, image, detailed=self.minimize_into_app)
                    self.sheet_frames.pop(identity, None)
                    self.sheet_frames[identity] = image
                    while sum(frame.sizeInBytes() for frame in self.sheet_frames.values()) > 32*1024*1024:
                        self.sheet_frames.popitem(last=False)
            else:
                self.minimized_bounds.pop(identity, None)
                self.sheet_frames.pop(identity, None)
                self.minimized_tiles.forget(effect.window)
            if effect in self.effects:
                self.effects.remove(effect)
            effect.deleteLater()
            if not getattr(self, 'closing', False):
                self.refresh_items()
                self.update()
        effect.finished.connect(finished)
        if effect.begin():
            return True
        if effect in self.effects:
            effect.close()
        return False

    def request_sheet_capture(self, window):
        if any(request[0]['id'] == window['id'] for request in self.sheet_pending.values()):
            return True
        if any(effect.window['id'] == window['id'] for effect in self.effects):
            return True
        if len(self.effects)+len(self.sheet_pending) >= 3:
            return False
        from window_capture import CaptureBroker, logical_bounds
        origin = logical_bounds(self.watcher.backend, window, self.dock_screen, int(self.winId()))
        if origin is None:
            return False
        if self.capture_broker is None:
            self.capture_broker = CaptureBroker(self)
            self.capture_broker.ready.connect(self.sheet_capture_ready)
        token = self.capture_broker.request(window)
        if token is None:
            return False
        self.sheet_pending[token] = (dict(window), origin)
        def expire():
            pending = self.sheet_pending.pop(token, None)
            if pending and not getattr(self, 'closing', False):
                self.sheet_fallback(pending[0])
        QTimer.singleShot(350, expire)
        return True

    def sheet_capture_ready(self, token, image):
        pending = self.sheet_pending.pop(token, None)
        if pending is None or getattr(self, 'closing', False):
            return
        window, origin = pending
        from window_capture import logical_bounds
        current = logical_bounds(self.watcher.backend, window, self.dock_screen, int(self.winId()))
        valid_image = image is not None and not image.isNull()
        if valid_image:
            ratio = self.dock_screen.devicePixelRatio()
            valid_image = abs(image.width()-origin.width()*ratio) <= 3 and abs(image.height()-origin.height()*ratio) <= 3
            # A failed/protected rendering often returns an all-black surface.
            if valid_image:
                sample = image.scaled(12, 12)
                valid_image = any(sample.pixelColor(x, y).lightness() > 2 for x in range(sample.width()) for y in range(sample.height()))
        if current != origin or not valid_image or not self.start_sheet_effect(window, image, origin):
            self.sheet_fallback(window)

    def start_sheet_effect(self, window, image, origin, reverse=False):
        if len(self.effects) >= 3 or any(effect.window['id'] == window['id'] for effect in self.effects):
            return False
        bounds = self.transition_target(window)
        if bounds is None:
            return False
        from sheet_effect import SheetEffect
        effect = SheetEffect(self.watcher.backend, window, bounds, self.dock_screen, origin, image,
                             edge=self.edge, reverse=reverse)
        effect.target_provider = lambda: self.transition_target(window)
        return self.track_effect(effect, (window['pid'], window['id']))

    def sheet_fallback(self, window):
        if self.watcher.backend.valid(window) and not self.watcher.backend.u.IsIconic(window['id']):
            if not self.start_minimize_effect(window):
                self.watcher.backend.minimize(window)

    def folder_stack(self, item):
        from panels import FolderPanel
        self.menu_open = True
        panel = FolderPanel(self, item)
        try:
            panel.exec()
        finally:
            self.menu_open = False
            self.last_inside = time.monotonic()
            panel.deleteLater()

    def window_overview(self, item):
        from panels import WindowPanel
        self.menu_open = True
        panel = WindowPanel(self, item)
        try:
            panel.exec()
        finally:
            self.menu_open = False
            self.last_inside = time.monotonic()
            panel.deleteLater()

    def add_path(self, path):
        if len(self.pins) >= 18:
            QMessageBox.information(self, APP, 'Este prototipo admite hasta 18 accesos personalizados.')
            return
        if Path(path).exists() and not any(p['path'] == path for p in self.pins):
            pin = dict(name=Path(path).stem, path=path)
            if Path(path).is_dir():
                pin['kind'] = 'folder'
            self.pins.append(pin)
            self.save()
            self.refresh_items()

    def add_pin(self, folder=False):
        self.menu_open = True
        try:
            path = QFileDialog.getExistingDirectory(self, 'Añadir carpeta') if folder else QFileDialog.getOpenFileName(self, 'Selecciona aplicación, acceso directo o archivo')[0]
            if path:
                self.add_path(path)
        finally:
            self.menu_open = False

    def dragEnterEvent(self, event):
        if accepts_mime(event.mimeData()) and drop_action(event.possibleActions()) != Qt.IgnoreAction:
            self.external_drag = True
            self.dragMoveEvent(event)
            if not event.isAccepted():
                self.reset_document_drag()

    def document_drop_target(self, position, files):
        # Launchers and folders are pinned, even if dropped over an application.
        # Documents retain the separate "open with" gesture.
        if not files or any(path.startswith('shell:AppsFolder\\') or Path(path).is_dir() or Path(path).suffix.lower() in
               ('.exe', '.lnk', '.com', '.bat', '.cmd', '.appref-ms') for path in files):
            return -1
        if os.name == 'nt':
            from document_drop import accepts_documents
            return next((index for index, (item, rect) in enumerate(zip(self.items, self.rects))
                         if rect.contains(position) and accepts_documents(item, self.targets)), -1)
        return -1

    def dragMoveEvent(self, event):
        if not accepts_mime(event.mimeData()) or drop_action(event.possibleActions()) == Qt.IgnoreAction:
            event.ignore()
            return
        self.drop_index = self.document_drop_target(event.position(),
                            local_paths(event.mimeData()))
        if self.document_opener and self.document_opener.busy:
            event.ignore()
            label = 'Espera a que termine la solicitud de apertura'
        else:
            event.setDropAction(drop_action(event.possibleActions()))
            event.accept()
            label = f"Abrir con {self.items[self.drop_index]['name']}" if self.drop_index >= 0 else 'Anclar en el dock'
        self.drop_label = label
        self.last_inside = time.monotonic()
        self.update()

    def reset_document_drag(self):
        self.external_drag = False
        self.drop_index = -1
        self.drop_label = ''
        self.last_inside = time.monotonic()
        QToolTip.hideText()
        self.update()

    def dragLeaveEvent(self, event):
        self.reset_document_drag()
        event.accept()

    def dropEvent(self, event):
        action = drop_action(event.possibleActions())
        entries = drop_entries(event.mimeData()) if action != Qt.IgnoreAction else []
        if not entries:
            self.reset_document_drag()
            event.ignore()
            return
        files = [entry['path'] for entry in entries]
        target_index = self.document_drop_target(event.position(), files)
        target = self.items[target_index] if target_index >= 0 else None
        self.reset_document_drag()
        if self.document_opener and self.document_opener.busy:
            event.ignore()
            return
        if target is not None:
            if self.request_documents(target, files):
                event.setDropAction(action)
                event.accept()
            else:
                event.ignore()
            return
        for entry in entries:
            if entry.get('kind') == 'app':
                self.pin_application(entry)
            else:
                self.add_path(entry['path'])
        event.setDropAction(action)
        event.accept()

    def request_documents(self, item, files):
        if self.document_opener is None:
            from document_drop import DocumentOpener
            self.document_opener = DocumentOpener(self)
            self.document_opener.finished.connect(self.document_drop_finished)
        return self.document_opener.request(dict(item), list(files))

    def choose_documents(self, item):
        previous = self.menu_open
        self.menu_open = True
        try:
            files = QFileDialog.getOpenFileNames(self, f"Abrir archivos con {item['name']}")[0]
            if files:
                self.request_documents(item, files)
        finally:
            self.menu_open = previous
            self.last_inside = time.monotonic()

    def document_drop_finished(self, success, message):
        if getattr(self, 'closing', False):
            return
        self.integration_status = message
        if not success:
            QMessageBox.warning(self, APP, message)

    def contextMenuEvent(self, event):
        self.last_context_action = None
        if self.press_pos is not None:
            self.drag_cancelled = True
            event.accept()
            return
        self.menu_open = True
        menu = QMenu(self)
        menu.addAction('Brisa Dock '+VERSION).setEnabled(False)
        menu.addSeparator()
        selected = next((item for item, rect in zip(self.items, self.rects) if rect.contains(event.pos())), {})
        if selected.get('path') and selected.get('symbol') != 'folder' and Path(selected['path']).is_dir():
            views = menu.addMenu('Ver contenido como')
            for key, label in (('fan', 'Abanico'), ('grid', 'Rejilla'), ('list', 'Lista'), ('auto', 'Automático')):
                action = views.addAction(label, lambda checked=False, value=key: self.set_folder_option('folder_mode', value, selected['path']))
                action.setCheckable(True)
                action.setChecked(self.folder_preferences(selected['path']).get('folder_mode', 'auto') == key)
            order = menu.addMenu('Ordenar por')
            for key, label in ORDERS:
                action = order.addAction(label, lambda checked=False, value=key: self.set_folder_option('folder_order', value, selected['path']))
                action.setCheckable(True)
                action.setChecked(self.folder_preferences(selected['path']).get('folder_order') == key)
            representation = menu.addMenu('Mostrar como')
            for key, label in (('folder', 'Carpeta'), ('stack', 'Pila')):
                action = representation.addAction(label, lambda checked=False, value=key: self.set_folder_option('folder_display', value, selected['path']))
                action.setCheckable(True)
                action.setChecked(self.folder_preferences(selected['path']).get('folder_display') == key)
            menu.addSeparator()
        windows = selected.get('windows', [])
        if os.name == 'nt':
            from document_drop import accepts_documents
            if accepts_documents(selected, self.targets):
                action = menu.addAction('Abrir archivos con esta aplicación…', lambda: self.choose_documents(selected))
                action.setEnabled(not self.document_opener or not self.document_opener.busy)
                menu.addSeparator()
        if windows:
            menu.addAction('Mostrar todas las ventanas', lambda: self.window_overview(selected))
            if not selected.get('minimized_window') and hasattr(self.watcher.backend, 'hidden'):
                if any(window.get('hidden') for window in windows):
                    menu.addAction('Mostrar aplicación', lambda: self.launch(self.items.index(selected)))
                else:
                    menu.addAction('Ocultar aplicación', lambda: self.hide_application(selected))
                menu.addAction('Ocultar otras aplicaciones', lambda: self.hide_application(selected, True))
            menu.addSeparator()
            for window in windows:
                label = ('✓ ' if window['active'] else '') + window['title'][:75]
                menu.addAction(label, lambda checked=False, w=window: self.window_action('activate', w))
            menu.addSeparator()
            if not selected.get('minimized_window'):
                menu.addAction('Abrir de nuevo', lambda: self.launch(self.items.index(selected), True))
                menu.addAction('Minimizar ventanas', lambda: [self.window_action('minimize', w) for w in windows])
            menu.addAction('Cerrar ventanas…', lambda: [self.window_action('close_window', w) for w in windows])
            if selected.get('temporary'):
                menu.addAction('Mantener en el dock', lambda: self.pin_application(selected))
            menu.addSeparator()
        if selected.get('recent'):
            menu.addAction('Mantener en el dock', lambda: self.pin_application(selected))
            menu.addAction('Quitar de recientes', lambda: self.remove_recent(selected['key']))
            menu.addSeparator()
        menu.addAction('Añadir app abierta…', self.running_applications_panel)
        menu.addAction('Añadir aplicación o archivo…', lambda: self.add_pin())
        menu.addAction('Añadir carpeta…', lambda: self.add_pin(True))
        if self.removed_pin is not None:
            menu.addAction('Deshacer quitar acceso', self.undo_remove_pin).setEnabled(len(self.pins) < 18)
        index = selected.get('pin_index', -1)
        if 0 <= index < len(self.pins):
            menu.addSeparator()
            menu.addAction('Quitar acceso', lambda: self.change_pin(index, 0))
            if neighbor(self.pins, index, -1, self.targets) is not None:
                menu.addAction('Mover antes', lambda: self.change_pin(index, -1))
            if neighbor(self.pins, index, 1, self.targets) is not None:
                menu.addAction('Mover después', lambda: self.change_pin(index, 1))
            placement = menu.addMenu('Zona del acceso')
            for key, label in (('auto', 'Automática'), ('apps', 'Aplicaciones'), ('files', 'Carpetas y documentos')):
                action = placement.addAction(label, lambda checked=False, value=key: self.set_pin_zone(index, value))
                action.setCheckable(True)
                action.setChecked(self.pins[index].get('zone', 'auto') == key)
        menu.addSeparator()
        menu.addAction('Navegar con teclado…', lambda: QTimer.singleShot(0, self.begin_keyboard_navigation))
        menu.addAction('Preferencias…', self.preferences)
        menu.addAction('Borrar aplicaciones recientes', self.clear_recent)
        if hasattr(self.watcher.backend, 'hidden'):
            menu.addAction('Mostrar aplicaciones ocultas', self.show_hidden_applications)
        hide = menu.addAction('Ocultar automáticamente')
        hide.setCheckable(True)
        hide.setChecked(self.auto_hide)
        hide.triggered.connect(self.toggle_hide)
        magnify = menu.addAction('Ampliar iconos al pasar el cursor')
        magnify.setCheckable(True)
        magnify.setChecked(self.magnify)
        magnify.triggered.connect(lambda checked: self.set_preference('magnify', checked))
        light = menu.addAction('Apariencia clara')
        light.setCheckable(True)
        light.setChecked(self.light)
        light.triggered.connect(lambda checked: self.set_preference('light', checked))
        sizes = menu.addMenu('Tamaño de iconos')
        for size in (16, 24, 32, 36, 48, 60, 72):
            sizes.addAction(f'{size} px', lambda checked=False, s=size: self.set_size(s))
        status = menu.addAction(self.integration_status)
        status.setEnabled(False)
        menu.addAction('Cerrar Brisa Dock', self.close)
        try:
            self.last_context_action = menu.exec(event.globalPos())
        finally:
            self.menu_open = False
            self.last_inside = time.monotonic()

    def change_pin(self, index, direction):
        if direction:
            other = neighbor(self.pins, index, direction, self.targets)
            if other is None:
                return
            self.pins[index], self.pins[other] = self.pins[other], self.pins[index]
        else:
            self.removed_pin = (index, dict(self.pins.pop(index)))
        self.save()
        self.refresh_items()

    def set_pin_zone(self, index, zone):
        if not 0 <= index < len(self.pins):
            return
        if zone in ('apps', 'files'):
            self.pins[index]['zone'] = zone
        else:
            self.pins[index].pop('zone', None)
        self.save()
        self.refresh_items()

    def folder_preferences(self, path):
        preferences = {key: self.config.get(key, default) for key, default in
                       (('folder_mode', 'auto'), ('folder_order', 'date'), ('folder_display', 'folder'))}
        stored = self.config.get('folder_preferences', {})
        if isinstance(stored, dict) and isinstance(stored.get(canonical(path)), dict):
            preferences.update(stored[canonical(path)])
        return preferences

    def set_folder_option(self, key, value, path=None):
        if path:
            if not isinstance(self.config.get('folder_preferences'), dict):
                self.config['folder_preferences'] = {}
            self.config['folder_preferences'].setdefault(canonical(path), {})[key] = value
        else:
            self.config[key] = value
        self.save()
        self.sync_folder_previews()
        self.update()

    def undo_remove_pin(self):
        if self.removed_pin is None or len(self.pins) >= 18:
            return
        index, pin = self.removed_pin
        if not any(existing['path'] == pin['path'] for existing in self.pins):
            self.pins.insert(min(index, len(self.pins)), pin)
            self.save()
            self.refresh_items()
        self.removed_pin = None

    def preferences(self):
        from panels import PreferencesPanel
        self.menu_open = True
        panel = PreferencesPanel(self)
        try:
            panel.exec()
        finally:
            self.menu_open = False
            self.last_inside = time.monotonic()
            panel.deleteLater()

    def set_appearance(self, key=None, value=None):
        self.appearance = normalize_appearance(dict(self.appearance, **{key: value}) if key else {})
        self.save()
        self.update()
        self.appearance_changed.emit()

    def toggle_hide(self, checked):
        self.auto_hide = checked
        self.sync_reservation()
        self.resize_to_fit()
        self.save()

    def set_preference(self, name, value):
        if name == 'screen_mode' and value not in MODES:
            return
        if name == 'screen_id' and not isinstance(value, str):
            return
        if name in ('edge', 'gap', 'reserve_space', 'screen_mode', 'screen_id'):
            for effect in self.effects[:]:
                effect.close()
        setattr(self, name, value)
        if name in ('screen_mode', 'screen_id'):
            self.recover_display()
        if name == 'light':
            self.appearance_changed.emit()
        if name == 'show_recent':
            if not value:
                self.recent.clear()
            self.refresh_items()
        if name == 'show_attention' and not value:
            self.attention_requests.clear()
        if name == 'keyboard_hotkey' and self.dock_hotkey:
            self.dock_hotkey.set_enabled(value)
        if name == 'minimize_into_app':
            for window in list(self.tile_pending.values()):
                if self.watcher.backend and self.watcher.backend.valid(window):
                    self.watcher.backend.minimize(window)
            self.tile_pending.clear()
            if value:
                self.minimized_tiles.reserved.clear()
            else:
                for effect in self.effects:
                    self.minimized_tiles.reserve(effect.window)
            self.refresh_items()
        if name in ('animate_minimize', 'animate_window_buttons'):
            self.sync_caption_minimizer()
        if name in ('animate_minimize', 'animation_style'):
            self.warm_sheet_capture()
        if name == 'animate_minimize' and not value:
            for effect in self.effects[:]:
                effect.close()
            self.sheet_frames.clear()
        if name in ('edge', 'gap', 'reserve_space', 'auto_hide', 'hide_fullscreen'):
            self.hidden_amount = 0
            self.last_inside = time.monotonic()
            self.sync_reservation()
            self.resize_to_fit()
        self.save()
        self.update()

    def set_size(self, size):
        self.size = size
        self.save()
        self.refresh_items()
        self.sync_reservation()
        self.resize_to_fit()

    def closeEvent(self, event):
        self.closing = True
        for broker in (self.capture_broker, self.tile_capture_broker):
            if broker is not None:
                broker.close()
        if self.caption_minimizer:
            self.caption_minimizer.close()
        self.reorder_timer.stop()
        self.restore_drag_order()
        if self.blur_shelf:
            self.blur_shelf.close()
        if self.application_icons:
            self.application_icons.close()
        if self.keyboard_controller:
            self.keyboard_controller.reject()
        if self.dock_hotkey:
            self.dock_hotkey.close()
        for window in list(self.tile_pending.values()):
            if self.watcher.backend and self.watcher.backend.valid(window):
                self.watcher.backend.minimize(window)
        self.tile_pending.clear()
        self.minimized_tiles.frames.clear()
        if self.recycle_monitor:
            self.recycle_monitor.close()
        if self.stack_previews:
            self.stack_previews.close()
        if self.target_opener:
            self.target_opener.close()
        if self.document_opener:
            self.document_opener.stop.set()
        for window, _ in list(self.sheet_pending.values()):
            if self.watcher.backend and self.watcher.backend.valid(window):
                self.watcher.backend.minimize(window)
        self.sheet_pending.clear()
        if self.shell_attention:
            self.shell_attention.close()
        if self.catalog_worker:
            self.catalog_worker.stop.set()
            self.catalog_worker.wait(2500)
        for effect in self.effects[:]:
            effect.close()
        self.sheet_frames.clear()
        self.show_hidden_applications()
        if self.appbar:
            self.appbar.release()
            self.appbar.close()
        self.watcher.stop_event.set()
        # Shortcut resolution has a bounded timeout; let it finish before disposal.
        self.watcher.wait(6000)
        super().closeEvent(event)
        self.closed.emit()

    def hideEvent(self, event):
        if self.blur_shelf:
            self.blur_shelf.hide()
        super().hideEvent(event)


def main():
    if sys.argv[1:] == ['--capture-server']:
        from window_capture import capture_server
        return capture_server()
    if len(sys.argv) == 3 and sys.argv[1] == '--shell-open':
        from target_opener import shell_open
        return shell_open(sys.argv[2])
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName('Brisa')
    profile = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
    diagnostic = '--smoke-test' in sys.argv or '--render' in sys.argv
    def startup_error(message):
        if diagnostic:
            if sys.stderr:
                print(message, file=sys.stderr)
        else:
            QMessageBox.critical(None, APP, 'No se pudo iniciar Brisa Dock.\n'+str(message))
    if '--profile-dir' in sys.argv:
        if sys.argv.index('--profile-dir')+1 >= len(sys.argv):
            startup_error('Falta la carpeta de perfil.')
            return 2
        profile = Path(sys.argv[sys.argv.index('--profile-dir')+1]).resolve()
    app.setProperty('brisaProfileDir', str(profile))
    from instance import InstanceGuard
    try:
        guard = InstanceGuard(profile, app)
    except OSError as error:
        startup_error(f'No se puede abrir el perfil: {error}')
        return 2
    if guard.error:
        startup_error(guard.error)
        return 2
    if not guard.owner:
        if not guard.notify():
            startup_error('Hay otra copia en marcha que no responde. Ciérrala antes de abrir esta versión.')
            return 2
        if guard.peer_version != VERSION:
            startup_error('Ya hay otra versión de Brisa Dock abierta. Ciérrala desde su menú y vuelve a abrir Brisa Dock '+VERSION+'.')
            return 2
        return 0
    dock = Dock()
    dock.closed.connect(app.quit)
    guard.reveal.connect(dock.reveal_instance)
    app.aboutToQuit.connect(guard.close)
    app.aboutToQuit.connect(lambda: dock.dock_hotkey.close() if dock.dock_hotkey else None)
    app.aboutToQuit.connect(lambda: dock.stack_previews.close() if dock.stack_previews else None)
    app.aboutToQuit.connect(lambda: dock.recycle_monitor.close() if dock.recycle_monitor else None)
    app.aboutToQuit.connect(lambda: dock.shell_attention.close() if dock.shell_attention else None)
    app.aboutToQuit.connect(lambda: dock.appbar.release() if dock.appbar else None)
    app.aboutToQuit.connect(lambda: [effect.close() for effect in dock.effects[:]])
    app.aboutToQuit.connect(lambda: (dock.catalog_worker.stop.set(), dock.catalog_worker.wait(2500)) if dock.catalog_worker else None)
    app.aboutToQuit.connect(lambda: (dock.watcher.stop_event.set(), dock.watcher.wait(6000)))
    if '--smoke-test' in sys.argv:
        QTimer.singleShot(800, app.quit)
    if '--render' in sys.argv:
        def render():
            dock.grab().save(sys.argv[sys.argv.index('--render') + 1])
            app.quit()
        QTimer.singleShot(500, render)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

