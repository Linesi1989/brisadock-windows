"""Internal app-catalog drag gesture; no launch or external file operation."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QListWidget, QWidget


class DragIcon(QWidget):
    def __init__(self, panel, entry, icon=None):
        super().__init__(panel, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setWindowOpacity(.95)
        self.icon = icon if icon is not None else panel.dock.icon_for(entry)
        self.resize(72, 72)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(.9)
        self.icon.paint(painter, self.rect().adjusted(4, 4, -4, -4))


class CatalogList(QListWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.press_point = None
        self.press_entry = None
        self.press_icon = None
        self.consume_release = False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            self.press_point = event.position().toPoint()
            self.press_entry = dict(item.data(Qt.UserRole)) if item else None
            self.press_icon = item.icon() if item else None
            self.consume_release = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press_entry and event.buttons() & Qt.LeftButton:
            distance = (event.position().toPoint()-self.press_point).manhattanLength()
            if not self.panel.drag_entry and not self.consume_release and distance >= QApplication.startDragDistance():
                self.consume_release = True
                self.panel.start_catalog_drag(self.press_entry, self.press_icon)
            if self.panel.drag_entry:
                self.panel.move_catalog_drag(event.globalPosition().toPoint())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.consume_release:
            self.panel.finish_catalog_drag(event.globalPosition().toPoint())
            self.consume_release = False
            self.press_point, self.press_entry = None, None
            event.accept()
            return
        self.press_point, self.press_entry = None, None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.panel.drag_entry:
            self.panel.cancel_catalog_drag()
            event.accept()
            return
        super().keyPressEvent(event)


def over_dock(dock, global_position):
    local = dock.mapFromGlobal(global_position)
    rect = dock.shelf_rect()
    return rect.adjusted(-8, -8, 8, 8).contains(local)
