"""Transparent, curved file stack with individually painted, keyboard-accessible rows."""
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import QWidget, QPushButton

STRIDE = 52


class FanButton(QPushButton):
    def __init__(self, entry, index, parent):
        super().__init__(parent)
        self.entry, self.index = entry, index
        self.icon_x = 300
        self.direction = 1
        self.progress = 1.
        self.setText(entry.text())
        self.setIcon(entry.icon())
        self.setToolTip(entry.text())
        self.setAccessibleName(entry.text())
        self.setAutoDefault(False)
        self.setFont(QFont('Segoe UI', 9))
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def shapes(self):
        icon = QRectF(self.icon_x-22, 3, 44, 44)
        left = self.icon_x >= self.width()/2
        available = self.icon_x-32 if left else self.width()-self.icon_x-32
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, max(30, min(205, int(available)-12)))
        width = self.fontMetrics().horizontalAdvance(text)+12
        x = self.icon_x-30-width if left else self.icon_x+30
        return icon, QRectF(x, 14, width, 23), text

    def hitButton(self, point):
        icon, label, _ = self.shapes()
        return icon.contains(point) or label.contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        icon, label, text = self.shapes()
        painter.setOpacity(self.progress)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 45))
        painter.drawEllipse(QRectF(self.icon_x-16, 46, 32, 4))
        painter.save()
        painter.translate(icon.center())
        painter.rotate(-self.direction*self.index*1.4*self.progress)
        pixmap = self.icon().pixmap(128, 128)
        painter.drawPixmap(QRectF(-22, -22, 44, 44), pixmap, QRectF(pixmap.rect()))
        painter.restore()
        if self.progress > .35:
            painter.setOpacity(min(1., (self.progress-.35)/.65))
            painter.setBrush(QColor(45, 95, 148, 235) if self.underMouse() or self.hasFocus() else QColor(28, 28, 32, 215))
            painter.setPen(QPen(QColor(255, 255, 255, 95), 1) if self.hasFocus() else Qt.NoPen)
            painter.drawRoundedRect(label, 5, 5)
            painter.setPen(QColor('#fafafd'))
            painter.drawText(label, Qt.AlignCenter, text)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class FanView(QWidget):
    opened = Signal(object)
    capacityChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items, self.buttons = [], []
        self.capacity = 6
        self.spine_x = None
        self.progress = 1.
        self.setAccessibleName('Abanico de archivos')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(240)
        self.animation.setStartValue(0.)
        self.animation.setEndValue(1.)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self.animate)

    def set_items(self, items):
        self.items = list(items)
        entries = self.items[:self.capacity]
        if len(entries) == len(self.buttons) and all(button.entry is entry for button, entry in zip(self.buttons, entries)):
            self.arrange()
            return
        self.animation.stop()
        self.progress = 1.
        for button in self.buttons:
            button.hide()
            button.deleteLater()
        self.buttons = []
        for index, entry in enumerate(entries):
            button = FanButton(entry, index, self)
            button.installEventFilter(self)
            button.clicked.connect(lambda checked=False, item=entry: self.opened.emit(item))
            button.show()
            self.buttons.append(button)
        self.arrange()

    def arrange(self):
        spine = self.width()-105 if self.spine_x is None else self.spine_x
        spine = max(38, min(self.width()-38, spine))
        direction = 1 if spine+100 <= self.width() else -1
        count = len(self.buttons)
        for index, button in enumerate(self.buttons):
            curve = (index/max(1, count-1))**1.5*70
            y = self.height()-STRIDE-index*STRIDE*self.progress
            button.setGeometry(0, round(y), self.width(), STRIDE-2)
            button.icon_x = spine+direction*curve*self.progress
            button.direction = direction
            button.progress = self.progress
            button.update()
            button.raise_()

    def reveal(self):
        self.animation.stop()
        if self.buttons:
            self.animate(0.)
            self.animation.start()

    def animate(self, value):
        self.progress = float(value)
        self.arrange()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()

    def eventFilter(self, source, event):
        if event.type() == QEvent.KeyPress and source in self.buttons and event.key() in (Qt.Key_Up, Qt.Key_Down):
            index = self.buttons.index(source)
            self.buttons[(index+(1 if event.key() == Qt.Key_Up else -1)) % len(self.buttons)].setFocus()
            return True
        return super().eventFilter(source, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window().reject()
        else:
            event.ignore()
