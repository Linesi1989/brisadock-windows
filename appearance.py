"""Validated appearance settings; defaults preserve the existing Dock palette."""
import re
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

DEFAULTS = dict(dock_color=None, folder_color=None, dock_alpha=195, folder_alpha=250)


def normalize(value):
    value = value if isinstance(value, dict) else {}
    result = dict(DEFAULTS)
    for role in ('dock', 'folder'):
        color = value.get(role+'_color')
        if isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            result[role+'_color'] = color.lower()
        try:
            result[role+'_alpha'] = max(51 if role == 'dock' else 179,
                                       min(255, int(value.get(role+'_alpha', DEFAULTS[role+'_alpha']))))
        except (TypeError, ValueError, OverflowError):
            pass
    return result


def background(settings, role, light=False):
    settings = normalize(settings)
    fallback = '#f5f5f8' if light else ('#2e2e32' if role == 'dock' else '#252529')
    color = QColor(settings[role+'_color'] or fallback)
    color.setAlpha(settings[role+'_alpha'])
    return color


def foreground(color):
    channels = [value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4
                for value in (color.redF(), color.greenF(), color.blueF())]
    luminance = sum(value*weight for value, weight in zip(channels, (.2126, .7152, .0722)))
    return '#1c1c1e' if luminance > .179 else '#fafafd'


class AppearancePreview(QWidget):
    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.setMinimumHeight(150)
        self.setAccessibleName('Vista previa de los colores del Dock y las carpetas')
        dock.appearance_changed.connect(self.update)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for x in range(0, self.width(), 25):
            for y in range(0, self.height(), 25):
                painter.fillRect(x, y, 25, 25, QColor('#98bbc8' if (x//25+y//25)%2 else '#d9c6aa'))
        for role, rect, title in [('folder', QRectF(20, 12, self.width()-40, 62), 'Panel de carpeta'),
                                   ('dock', QRectF(35, 93, self.width()-70, 43), 'Dock')]:
            color = background(self.dock.appearance, role, self.dock.light)
            painter.setBrush(color)
            painter.setPen(QPen(QColor(255, 255, 255, 110), 1))
            painter.drawRoundedRect(rect, 14, 14)
            painter.setPen(QColor(foreground(color)))
            painter.drawText(rect, Qt.AlignCenter, title)
