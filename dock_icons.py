"""Original vector artwork for the built-in launchers. MIT license."""
import math
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QLinearGradient, QRadialGradient, QPainterPath, QPen, QPolygonF, QPainter


def gradient(top, bottom, y1=5, y2=95):
    value = QLinearGradient(0, y1, 0, y2)
    value.setColorAt(0, QColor(top))
    value.setColorAt(1, QColor(bottom))
    return value


def tile(painter, top, bottom):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 35))
    painter.drawRoundedRect(QRectF(5, 8, 90, 89), 20, 20)
    painter.setBrush(gradient(top, bottom))
    painter.setPen(QPen(QColor(255, 255, 255, 115), .8))
    painter.drawRoundedRect(QRectF(5, 5, 90, 89), 20, 20)


def draw_builtin(painter, rectangle, symbol):
    painter.save()
    painter.translate(rectangle.x(), rectangle.y())
    painter.scale(rectangle.width()/100, rectangle.height()/100)
    painter.setRenderHint(QPainter.Antialiasing)
    if symbol == 'folder':
        tile(painter, '#f5fbff', '#d1dce8')
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(5, 5, 90, 89), 20, 20)
        painter.save()
        painter.setClipPath(outline)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient('#58cbff', '#0878ee'))
        painter.drawRect(QRectF(5, 5, 45, 89))
        painter.restore()
        painter.setPen(QPen(QColor('#153f6b'), 2.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        nose = QPainterPath(QPointF(57, 7))
        nose.cubicTo(55, 26, 47, 43, 47, 54)
        nose.lineTo(59, 54)
        nose.cubicTo(57, 69, 57, 81, 60, 93)
        painter.drawPath(nose)
        painter.setPen(QPen(QColor('#123e6b'), 3.3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(28, 34), QPointF(28, 42))
        painter.drawLine(QPointF(73, 34), QPointF(73, 42))
        smile = QPainterPath(QPointF(24, 61))
        smile.cubicTo(37, 76, 63, 78, 78, 60)
        painter.setPen(QPen(QColor('#163e65'), 2.8, Qt.SolidLine, Qt.RoundCap))
        painter.drawPath(smile)
    elif symbol == 'web':
        tile(painter, '#ffffff', '#e0e8f1')
        # A compass inside the same rounded tile geometry as other built-ins.
        painter.translate(8, 8)
        painter.scale(.84, .84)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(QRectF(5, 8, 90, 90))
        painter.setBrush(gradient('#ffffff', '#a8b3c1'))
        painter.drawEllipse(QRectF(5, 4, 90, 90))
        face = QRadialGradient(36, 25, 78)
        face.setColorAt(0, QColor('#45dcff'))
        face.setColorAt(1, QColor('#0069df'))
        painter.setBrush(face)
        painter.drawEllipse(QRectF(10, 9, 80, 80))
        for tick in range(36):
            angle = tick*math.tau/36
            inner = 31 if tick % 3 == 0 else 34
            painter.setPen(QPen(QColor(255, 255, 255, 210), 1.3 if tick % 3 == 0 else .8))
            painter.drawLine(QPointF(50+inner*math.sin(angle), 49-inner*math.cos(angle)),
                             QPointF(50+37*math.sin(angle), 49-37*math.cos(angle)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#f9414c'))
        painter.drawPolygon(QPolygonF([QPointF(76, 22), QPointF(55, 55), QPointF(44, 44)]))
        painter.setBrush(gradient('#ffffff', '#d2e4f2'))
        painter.drawPolygon(QPolygonF([QPointF(24, 76), QPointF(44, 44), QPointF(55, 55)]))
        painter.setBrush(QColor('#eff8ff'))
        painter.drawEllipse(QPointF(50, 49), 2.2, 2.2)
    elif symbol == 'apps':
        tile(painter, '#eef2f7', '#9ca9b8')
        colors = ['#33baf1', '#ffbd36', '#ff6864', '#50c98c', '#8f77e5', '#44c4bb', '#f17fa9', '#3e93f1',
                  '#f5913e', '#699be4', '#70c54f', '#b67ade', '#45b5d9', '#ef7272', '#d4ab42', '#667ad7']
        painter.setPen(Qt.NoPen)
        for index, color in enumerate(colors):
            x, y = 17+(index % 4)*17, 17+(index//4)*17
            painter.setBrush(QColor(0, 0, 0, 28))
            painter.drawRoundedRect(QRectF(x, y+1, 14, 14), 3.5, 3.5)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(x, y, 14, 14), 3.5, 3.5)
            painter.setBrush(QColor(255, 255, 255, 125))
            painter.drawRoundedRect(QRectF(x+3, y+3, 8, 2), 1, 1)
    elif symbol == 'download':
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawRoundedRect(QRectF(7, 28, 87, 66), 7, 7)
        back = QPainterPath(QPointF(9, 84))
        back.lineTo(9, 23)
        back.quadTo(9, 17, 15, 17)
        back.lineTo(37, 17)
        back.lineTo(46, 25)
        back.lineTo(86, 25)
        back.quadTo(92, 25, 92, 31)
        back.lineTo(92, 84)
        back.closeSubpath()
        painter.setBrush(gradient('#69cdff', '#187ecb'))
        painter.drawPath(back)
        painter.setBrush(QColor('#d3f1ff'))
        painter.drawRoundedRect(QRectF(15, 32, 70, 42), 3, 3)
        front = QPainterPath(QPointF(6, 40))
        front.lineTo(94, 40)
        front.lineTo(90, 85)
        front.quadTo(90, 91, 84, 91)
        front.lineTo(16, 91)
        front.quadTo(10, 91, 10, 85)
        front.closeSubpath()
        painter.setBrush(gradient('#8addff', '#2c9eea', 40, 91))
        painter.setPen(QPen(QColor(255, 255, 255, 130), .8))
        painter.drawPath(front)
        painter.setPen(QPen(QColor('#f5fcff'), 5.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(QPointF(50, 51), QPointF(50, 78))
        painter.drawLine(QPointF(39, 68), QPointF(50, 79))
        painter.drawLine(QPointF(50, 79), QPointF(61, 68))
    elif symbol in ('trash', 'trash_full'):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 32))
        painter.drawEllipse(QRectF(24, 88, 52, 8))
        body = QPainterPath(QPointF(20, 23))
        body.lineTo(80, 23)
        body.lineTo(72, 87)
        body.quadTo(50, 97, 28, 87)
        body.closeSubpath()
        painter.setBrush(gradient('#f8fbff', '#a9b4c1'))
        painter.setPen(QPen(QColor('#7e8996'), 1.1))
        painter.drawPath(body)
        painter.save()
        painter.setClipPath(body)
        painter.setPen(QPen(QColor('#6c7d8f'), 1.4))
        for x in range(25, 80, 5):
            painter.drawLine(QPointF(x, 27), QPointF(50+(x-50)*.72, 90))
        painter.setPen(QPen(QColor(255, 255, 255, 195), 1))
        for y in range(31, 88, 6):
            painter.drawLine(QPointF(20, y), QPointF(80, y))
        painter.restore()
        painter.setBrush(gradient('#ffffff', '#9aa7b6', 14, 30))
        painter.setPen(QPen(QColor('#c6d2de'), 1.3))
        painter.drawEllipse(QRectF(18, 15, 64, 17))
        painter.setBrush(gradient('#667487', '#bdc9d6', 18, 28))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(23, 19, 54, 9))
        if symbol == 'trash_full':
            painter.setPen(QPen(QColor('#8e98a4'), 1))
            for color, points in (
                    ('#f5f5ef', [(27, 25), (24, 10), (42, 6), (47, 25)]),
                    ('#a7c5e7', [(41, 25), (43, 2), (63, 8), (59, 27)]),
                    ('#f0ebe3', [(56, 26), (62, 8), (77, 15), (72, 26)])):
                painter.setBrush(QColor(color))
                painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
            painter.setPen(QPen(QColor('#9ba8b7'), 1.3))
            painter.drawLine(QPointF(30, 14), QPointF(39, 17))
            painter.drawLine(QPointF(48, 10), QPointF(56, 12))
    else:
        tile(painter, '#a7b0bc', '#657182')
        painter.setPen(QPen(QColor('white'), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(28, 50), QPointF(72, 50))
        painter.drawLine(QPointF(50, 28), QPointF(50, 72))
    painter.restore()
