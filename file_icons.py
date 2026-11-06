"""Original document-type icons for folder stacks; does not change system associations."""
from pathlib import Path
from functools import lru_cache
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QPainterPath, QFont, QLinearGradient


def stack_icon(path, directory=False):
    suffix = Path(path).suffix.lower()
    category = ('folder' if directory else 'audio' if suffix in {'.ogg', '.mp3', '.wav', '.m4a', '.flac', '.aac'}
                else 'archive' if suffix in {'.zip', '.7z', '.rar', '.tar', '.gz'}
                else 'package' if suffix in {'.exe', '.msi', '.msix', '.appx'} else 'document')
    return _draw_icon(category, '' if category in ('folder', 'audio', 'package') else suffix)


@lru_cache(maxsize=None)
def _draw_icon(category, suffix):
    image = QPixmap(256, 256)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(2.56, 2.56)
    p.setPen(Qt.NoPen)
    if category == 'folder':
        p.setBrush(QColor('#2d9ddb'))
        folder = QPainterPath(QPointF(7, 84))
        folder.lineTo(7, 22)
        folder.lineTo(37, 22)
        folder.lineTo(46, 30)
        folder.lineTo(93, 30)
        folder.lineTo(93, 84)
        folder.closeSubpath()
        p.drawPath(folder)
        fill = QLinearGradient(0, 35, 0, 88)
        fill.setColorAt(0, QColor('#87d9ff'))
        fill.setColorAt(1, QColor('#309ee7'))
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(7, 36, 86, 52), 5, 5)
    elif category == 'package':
        p.setBrush(QColor('#c79a63'))
        p.drawRoundedRect(QRectF(15, 21, 70, 67), 5, 5)
        p.setBrush(QColor('#e4c19a'))
        p.drawRect(QRectF(17, 23, 66, 20))
        p.setBrush(QColor('#9e7449'))
        p.drawRect(QRectF(44, 23, 13, 65))
        p.setPen(QPen(QColor('#f7e7cf'), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(65, 53), QPointF(65, 75))
        p.drawLine(QPointF(58, 68), QPointF(65, 75))
        p.drawLine(QPointF(65, 75), QPointF(72, 68))
    else:
        sheet = QPainterPath(QPointF(21, 7))
        sheet.lineTo(62, 7)
        sheet.lineTo(80, 26)
        sheet.lineTo(80, 93)
        sheet.lineTo(21, 93)
        sheet.closeSubpath()
        p.setBrush(QColor('#fafbfd'))
        p.setPen(QPen(QColor('#bec6d0'), 1))
        p.drawPath(sheet)
        p.setBrush(QColor('#dfe5ec'))
        fold = QPainterPath(QPointF(62, 7))
        fold.lineTo(62, 26)
        fold.lineTo(80, 26)
        fold.closeSubpath()
        p.drawPath(fold)
        color = '#d261a9' if category == 'audio' else '#8995a6' if category == 'archive' else '#dc5353' if suffix == '.pdf' else '#6096d1'
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(27, 43, 47, 35), 4, 4)
        if category == 'audio':
            p.setPen(QPen(QColor('white'), 4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(47, 51), QPointF(47, 68))
            p.drawLine(QPointF(47, 51), QPointF(59, 48))
            p.drawLine(QPointF(59, 48), QPointF(59, 64))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('white'))
            p.drawEllipse(QRectF(37, 64, 11, 8))
            p.drawEllipse(QRectF(49, 60, 11, 8))
        else:
            p.setFont(QFont('Segoe UI', 11, QFont.Bold))
            p.setPen(QColor('white'))
            label = (suffix[1:] or 'FILE').upper()[:4]
            p.drawText(QRectF(27, 43, 47, 35), Qt.AlignCenter, label)
    p.end()
    return QIcon(image)
