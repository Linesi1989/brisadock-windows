"""Visible, deterministic application artwork when native resources are absent."""
from functools import lru_cache
import hashlib,re
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QIcon,QPixmap,QPainter,QColor,QFont,QImage,QLinearGradient

_visibility = {}

def visible_image(image):
    if image is None or image.isNull():return False
    if not image.hasAlphaChannel():return True
    sample=image.scaled(32,32,Qt.KeepAspectRatio,Qt.FastTransformation).convertToFormat(QImage.Format_RGBA8888)
    if sample.isNull():return False
    return any(memoryview(sample.constBits())[3::4])

def visible_icon(icon):
    if icon is None or icon.isNull():return False
    key=icon.cacheKey()
    if key not in _visibility:
        _visibility[key]=visible_image(icon.pixmap(32,32).toImage())
    return _visibility[key]

@lru_cache(maxsize=None)
def application_fallback(name,path):
    name=name.strip() or 'Aplicación'
    words=[word for word in re.findall(r'[^\W_]+',name,flags=re.UNICODE) if word.casefold() not in {'de','del','en','la','el','los','las','the','and','for'}]
    initials=''.join(word[0] for word in words[:2]).upper() or '?'
    hue=int.from_bytes(hashlib.sha256((path.casefold() or name.casefold()).encode('utf-8')).digest()[:2],'big')%360
    pixmap=QPixmap(256,256);pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.Antialiasing);painter.setPen(Qt.NoPen)
    gradient=QLinearGradient(0,0,256,256);gradient.setColorAt(0,QColor.fromHsv(hue,135,205));gradient.setColorAt(1,QColor.fromHsv(hue,185,140))
    painter.setBrush(gradient);painter.drawRoundedRect(QRectF(10,10,236,236),52,52)
    painter.setPen(Qt.white);font=QFont('Segoe UI');font.setPixelSize(94 if len(initials)>1 else 125);font.setBold(True);painter.setFont(font)
    painter.drawText(QRectF(10,4,236,236),Qt.AlignCenter,initials);painter.end()
    return QIcon(pixmap)
