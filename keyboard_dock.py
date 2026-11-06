"""Focusable keyboard controller for the non-activating Dock, plus an optional hotkey."""
import ctypes as C
from ctypes import wintypes as W
import html
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QDialog, QToolTip
from applications import search_text


def item_identity(item):
    if item.get('window_id'):
        return 'window', item['window_id']
    if 'pin_index' in item:
        return 'pin', item['path']
    return item.get('symbol') or ('recent' if item.get('recent') else 'running'), item['path']


class DockHotkey:
    identifier = 0xBD18
    message = 0x0312
    modifiers = 0x4003  # MOD_NOREPEAT | MOD_CONTROL | MOD_ALT
    key = 0x72  # F3

    def __init__(self, hwnd, enabled=True):
        self.hwnd = hwnd
        self.available = False
        self.error = 0
        self.user = C.WinDLL('user32', use_last_error=True)
        self.user.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
        self.user.RegisterHotKey.restype = W.BOOL
        self.user.UnregisterHotKey.argtypes = [W.HWND, C.c_int]
        self.user.UnregisterHotKey.restype = W.BOOL
        self.user.GetForegroundWindow.restype = W.HWND
        self.user.IsWindow.argtypes = [W.HWND]
        self.user.IsWindowVisible.argtypes = [W.HWND]
        self.user.IsIconic.argtypes = [W.HWND]
        self.user.SetForegroundWindow.argtypes = [W.HWND]
        self.set_enabled(enabled)

    def set_enabled(self, enabled):
        self.close()
        if enabled:
            self.available = bool(self.user.RegisterHotKey(self.hwnd, self.identifier, self.modifiers, self.key))
            self.error = 0 if self.available else C.get_last_error()

    def matches(self, message):
        native = W.MSG.from_address(int(message))
        return self.available and native.message == self.message and native.wParam == self.identifier

    def close(self):
        if self.available:
            self.user.UnregisterHotKey(self.hwnd, self.identifier)
            self.available = False


class KeyboardDock(QDialog):
    def __init__(self, dock, preferred=None):
        super().__init__(dock, Qt.Popup | Qt.FramelessWindowHint)
        self.dock = dock
        self.setWindowTitle('Navegación de Brisa')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet('QDialog { background: transparent; border: none; }')
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleDescription('Flechas para seleccionar, Enter para abrir, Shift F10 para opciones y Escape para salir.')
        self.selection = preferred
        self.index = 0
        self.finished_navigation = False
        self.prefix = ''
        self.typed_at = 0
        self.return_focus = False
        self.previous_hwnd = dock.dock_hotkey.user.GetForegroundWindow() if dock.dock_hotkey else None
        self.reconcile()

    def reconcile(self):
        if self.finished_navigation or not self.dock.items:
            return
        self.setGeometry(self.dock.geometry())
        chosen = next((index for index, item in enumerate(self.dock.items) if item_identity(item) == self.selection),
                      min(self.index, len(self.dock.items)-1))
        self.select(chosen)

    def select(self, index):
        self.index = index % len(self.dock.items)
        item = self.dock.items[self.index]
        self.selection = item_identity(item)
        self.dock.keyboard_index = self.index
        self.dock.hover = self.index
        self.setAccessibleName(item['name'])
        self.dock.update()
        if self.dock.edge != 'bottom':
            centers, _ = self.dock.layout_centers()
            rect = self.dock.item_rect(centers[self.index], self.dock.base_size)
            QToolTip.showText(self.dock.mapToGlobal(rect.center().toPoint()), html.escape(item['name']), self)

    def keyPressEvent(self, event):
        if self.finished_navigation:
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self.return_focus = True
            self.reject()
        elif key in (Qt.Key_Left, Qt.Key_Up, Qt.Key_Backtab):
            self.select(self.index-1)
        elif key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Tab):
            self.select(self.index+1)
        elif key == Qt.Key_Home:
            self.select(0)
        elif key == Qt.Key_End:
            self.select(len(self.dock.items)-1)
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.invoke(bool(event.modifiers() & Qt.ControlModifier))
        elif key == Qt.Key_Menu or (key == Qt.Key_F10 and event.modifiers() & Qt.ShiftModifier):
            self.show_options()
        elif event.text().isprintable() and event.text().strip():
            now = time.monotonic()
            self.prefix = self.prefix+event.text() if now-self.typed_at < .8 else event.text()
            self.typed_at = now
            prefix = search_text(self.prefix)
            for offset in range(1, len(self.dock.items)+1):
                index = (self.index+offset) % len(self.dock.items)
                if search_text(self.dock.items[index]['name']).startswith(prefix):
                    self.select(index)
                    break
        else:
            super().keyPressEvent(event)

    def invoke(self, overview=False):
        if self.finished_navigation:
            return
        item = self.dock.items[self.index]
        index = self.index
        self.accept()
        self.dock.keyboard_launch = True
        try:
            if overview and item.get('windows'):
                self.dock.window_overview(item)
            else:
                self.dock.launch(index)
        finally:
            self.dock.keyboard_launch = False

    def show_options(self):
        selection = self.selection
        index = self.index
        previous_hwnd = self.previous_hwnd
        self.accept()
        self.dock.grab()  # Ensure menu hit targets match the selected item.
        point = self.dock.rects[index].center().toPoint()
        event = QContextMenuEvent(QContextMenuEvent.Keyboard, point, self.dock.mapToGlobal(point))
        self.dock.contextMenuEvent(event)
        if self.dock.last_context_action is None and not getattr(self.dock, 'closing', False):
            self.dock.begin_keyboard_navigation(selection)
            if self.dock.keyboard_controller:
                self.dock.keyboard_controller.previous_hwnd = previous_hwnd

    def mousePressEvent(self, event):
        point = self.dock.mapFromGlobal(event.globalPosition().toPoint())
        index = next((index for index, rect in enumerate(self.dock.rects) if rect.contains(point)), None)
        if index is not None:
            self.select(index)
            if event.button() == Qt.LeftButton:
                self.invoke()
            else:
                self.show_options()
        else:
            self.reject()

    def done(self, result):
        if self.finished_navigation:
            return
        self.finished_navigation = True
        self.dock.keyboard_index = -1
        if self.dock.keyboard_controller is self:
            self.dock.keyboard_controller = None
        self.dock.hover = -1
        self.dock.last_inside = time.monotonic()
        QToolTip.hideText()
        super().done(result)
        hotkey = self.dock.dock_hotkey
        if self.return_focus and hotkey and self.previous_hwnd:
            if (hotkey.user.IsWindow(self.previous_hwnd) and hotkey.user.IsWindowVisible(self.previous_hwnd)
                    and not hotkey.user.IsIconic(self.previous_hwnd)):
                hotkey.user.SetForegroundWindow(self.previous_hwnd)
        self.dock.update()
        self.deleteLater()
