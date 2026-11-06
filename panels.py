"""Folder stacks and a per-application window overview. MIT license."""
from folder_content import scan_directory, ORDERS
from display_policy import MODES, screen_identity
from fan_view import FanView, STRIDE
from file_icons import stack_icon
from minimized_tiles import identity as preview_identity
from appearance import background as appearance_background, foreground, AppearancePreview
import os
from pathlib import Path

from PySide6.QtCore import (Qt, QSize, QPoint, QTimer, QUrl, QPropertyAnimation,
                            QEasingCurve, QObject, Signal, QRunnable, QThreadPool, QEvent, QRect, QRectF, QSignalBlocker)
from PySide6.QtGui import QIcon, QImageReader, QPixmap, QDesktopServices, QShortcut, QKeySequence, QPainter, QPainterPath, QColor, QPen, QPolygonF, QCursor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QListWidget, QListWidgetItem, QListView, QAbstractItemView,
                               QComboBox, QGridLayout, QMessageBox, QWidget, QStackedWidget, QApplication,
                               QFormLayout, QSlider, QDoubleSpinBox, QCheckBox, QMenu, QScrollArea, QColorDialog)
from PySide6.QtCore import QFileInfo


def style(light):
    background, card, text, border = ('#edf2f7', '#dde7f1', '#243346', '#bccbd9') if light else ('#202938', '#2c394c', '#eef4ff', '#43546a')
    return f'''
    QDialog {{ background: {background}; color: {text}; border: 1px solid {border}; border-radius: 16px; }}
    QLabel {{ color: {text}; background: transparent; border: none; }}
    QPushButton, QComboBox {{ background: {card}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 7px 10px; }}
    QPushButton:hover {{ border: 1px solid #66b9ff; background: #4175a0; }}
    QPushButton:disabled {{ color: #8395a8; }}
    QListWidget {{ background: transparent; color: {text}; border: none; outline: none; }}
    QListWidget::item {{ border-radius: 9px; padding: 4px; }}
    QListWidget::item:hover {{ background: {card}; }}
    QListWidget::item:selected {{ background: #386b98; color: white; }}
    QToolTip {{ background: {card}; color: {text}; border: 1px solid {border}; }}
    '''


class Popup(QDialog):
    def __init__(self, dock, title):
        super().__init__(dock, Qt.Popup | Qt.FramelessWindowHint)
        self.dock = dock
        self.setWindowTitle(title)
        self.setStyleSheet(style(dock.light))
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.header = QHBoxLayout()
        self.title = QLabel(title)
        font = self.title.font()
        font.setBold(True)
        font.setPointSize(12)
        self.title.setFont(font)
        self.header.addWidget(self.title, 1)
        close = QPushButton('×')
        close.setAccessibleName('Cerrar panel')
        close.clicked.connect(self.reject)
        self.header.addWidget(close)
        self.body.addLayout(self.header)

    def position_above_dock(self):
        screen = self.dock.dock_screen.availableGeometry()
        shelf = self.dock.shelf_rect()
        x = max(screen.left()+8, min(screen.right()-self.width()-8,
                                     self.dock.x()+self.dock.width()//2-self.width()//2))
        y = max(screen.top()+8, self.dock.y()+round(shelf.top())-self.height()-16)
        if self.dock.edge == 'left':
            x = self.dock.x()+round(shelf.right())+10
            y = self.dock.y()+self.dock.height()//2-self.height()//2
        elif self.dock.edge == 'right':
            x = self.dock.x()+round(shelf.left())-10-self.width()
            y = self.dock.y()+self.dock.height()//2-self.height()//2
        x = max(screen.left()+8, min(screen.right()-self.width()-8, x))
        y = max(screen.top()+8, min(screen.bottom()-self.height()-8, y))
        self.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        self.position_above_dock()
        # Animate position rather than window opacity: live DWM previews need
        # an opaque, non-layered destination window.
        end = self.pos()
        self.animation = QPropertyAnimation(self, b'pos', self)
        offset = QPoint(0, 14) if self.dock.edge == 'bottom' else QPoint(-14 if self.dock.edge == 'left' else 14, 0)
        self.animation.setStartValue(end+offset)
        self.animation.setEndValue(end)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.start()


class ImageSignals(QObject):
    ready = Signal(str, object)
    directory = Signal(int, object, int, str)


class DirectoryTask(QRunnable):
    def __init__(self, path, order, generation, signals):
        super().__init__()
        self.path, self.order, self.generation, self.signals = path, order, generation, signals

    def run(self):
        order = 'name' if self.order is True else 'date' if self.order is False else self.order
        entries, count, error = scan_directory(self.path, order)
        try:
            self.signals.directory.emit(self.generation, entries, count, error)
        except RuntimeError:
            pass


class ImageTask(QRunnable):
    def __init__(self, path, signals):
        super().__init__()
        self.path, self.signals = path, signals

    def run(self):
        try:
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(96, 96, Qt.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    self.signals.ready.emit(self.path, image)
        except RuntimeError:
            pass  # Dialog already disposed.


class FolderPanel(Popup):
    def __init__(self, dock, folder):
        super().__init__(dock, folder.get('name', 'Carpeta'))
        self.setObjectName('BrisaFolderPopup')
        # A native popup shadow outlines the entire transparent fan rectangle.
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._fan_mode = False
        dock.appearance_changed.connect(self.apply_mode)
        self.total_entries = 0
        anchor = next((rect for item, rect in zip(dock.items, dock.rects) if item['path'] == folder['path']), None)
        if anchor is None:
            anchor = next((rect for item, rect in zip(dock.items, dock.rects) if item.get('symbol') == 'download'), None)
        self.anchor = (QRect(dock.mapToGlobal(anchor.topLeft().toPoint()), anchor.size().toSize())
                       if anchor is not None else QRect(dock.x()+dock.width()//2, dock.y()+110, 48, 48))
        self.path = Path(folder['path'])
        self.initial_path = self.path
        preferences = dock.folder_preferences(str(self.initial_path))
        self.signals = ImageSignals(self)
        self.signals.ready.connect(self.image_ready)
        if dock.application_icons:
            dock.application_icons.changed.connect(self.native_icon_ready)
        self.signals.directory.connect(self.directory_ready)
        self.generation = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        QImageReader.setAllocationLimit(64)
        self.controls = QHBoxLayout()
        self.up = QPushButton('↑')
        self.up.setAccessibleName('Subir a la carpeta superior')
        self.up.clicked.connect(lambda: self.load(self.path.parent))
        self.controls.addWidget(self.up)
        self.mode = QComboBox()
        self.mode.addItems(['Rejilla', 'Lista', 'Abanico', 'Automático'])
        self.mode.setCurrentIndex({'grid':0, 'list':1, 'fan':2, 'auto':3}.get(preferences.get('folder_mode', 'auto'), 3))
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.controls.addWidget(self.mode)
        self.order = QComboBox()
        self.order.addItems([label for _, label in ORDERS])
        self.order.setCurrentIndex(next((index for index, (key, _) in enumerate(ORDERS) if key == preferences.get('folder_order')), 0))
        self.order.currentIndexChanged.connect(self.change_order)
        self.controls.addWidget(self.order)
        self.controls.addStretch()
        self.open_button = QPushButton('Abrir carpeta ↗')
        self.open_button.clicked.connect(self.open_folder)
        self.controls.addWidget(self.open_button)
        self.body.addLayout(self.controls)
        self.location = QLabel()
        self.body.addWidget(self.location)
        self.list = QListWidget()
        self.list.setMovement(QListView.Static)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setWordWrap(True)
        self.list.itemClicked.connect(self.open_entry)
        enter = QShortcut(QKeySequence(Qt.Key_Return), self.list)
        enter.setContext(Qt.WidgetShortcut)
        enter.activated.connect(lambda: self.open_entry(self.list.currentItem()) if self.list.currentItem() else None)
        self.views = QStackedWidget()
        self.views.addWidget(self.list)
        self.fan = FanView()
        self.fan_open_item = QListWidgetItem(stack_icon('folder', directory=True), 'Abrir en el Explorador')
        self.fan.opened.connect(self.open_entry)
        self.views.addWidget(self.fan)
        self.body.addWidget(self.views, 1)
        self.status = QLabel()
        self.body.addWidget(self.status)
        self.title.setAlignment(Qt.AlignCenter)
        options = QPushButton('···')
        options.setAccessibleName('Opciones de la carpeta')
        options.clicked.connect(self.fan_options)
        self.header.insertWidget(self.header.count()-1, options)
        self.fan_hint = QLabel()
        self.body.addWidget(self.fan_hint)
        self.grid_footer = QWidget()
        footer = QHBoxLayout(self.grid_footer)
        footer.setContentsMargins(0, 0, 0, 0)
        self.body.removeWidget(self.status)
        footer.addWidget(self.status, 1)
        open_grid = QPushButton('Abrir en el Explorador ↗')
        open_grid.clicked.connect(self.open_folder)
        footer.addWidget(open_grid)
        self.body.addWidget(self.grid_footer)
        area = dock.dock_screen.availableGeometry()
        self.resize(min(660, area.width()-32), min(470, area.height()-190))
        self.apply_mode()
        self.load(self.path)

    def apply_mode(self):
        previous = self._fan_mode
        grid = self.mode.currentIndex() == 0
        area = self.dock.dock_screen.availableGeometry()
        capacity = max(2, min(6, (self.anchor.top()-area.top()-24)//STRIDE))
        self.fan.capacity = capacity
        fan = self.dock.edge == 'bottom' and (self.mode.currentIndex() == 2 or (self.mode.currentIndex() == 3 and self.list.count() < capacity))
        self._fan_mode = fan
        if self.mode.currentIndex() == 3:
            grid = True
        if self.mode.currentIndex() == 2 and not fan:
            grid = True
        self.views.setCurrentWidget(self.fan if fan else self.list)
        self.list.setViewMode(QListView.IconMode if grid else QListView.ListMode)
        self.list.setIconSize(QSize(56, 56) if grid else QSize(26, 26))
        self.list.setGridSize(QSize(96, 104) if grid else QSize())
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.setSpacing(5 if grid else 2)
        if fan:
            self.fan.set_items([self.list.item(i) for i in range(min(self.list.count(), capacity-1))]+[self.fan_open_item])
        for layout in (self.header,):
            for index in range(layout.count()):
                widget = layout.itemAt(index).widget()
                if widget:
                    widget.setVisible(not fan)
        for index in range(self.controls.count()):
            widget = self.controls.itemAt(index).widget()
            if widget:
                widget.hide()
        self.header.itemAt(self.header.count()-1).widget().hide()
        self.location.hide()
        self.status.setVisible(not fan)
        self.grid_footer.setVisible(not fan)
        self.fan_hint.setText(self.status.text())
        self.fan_hint.setVisible(fan and self.list.count() == 0)
        self.body.setContentsMargins(6 if fan else 16, 6 if fan else 12, 6 if fan else 16, 6 if fan else 22)
        self.body.setSpacing(0 if fan else 6)
        visible_count = max(1, min(capacity, self.list.count()+1))
        self.views.setMinimumHeight(visible_count*STRIDE if fan else 0)
        if fan:
            self.setStyleSheet(style(False)+'''\nQDialog#BrisaFolderPopup { background: transparent; border: none; }
                QStackedWidget { background: transparent; border: none; }
                QPushButton { background: rgba(28,28,32,225); border: 1px solid rgba(255,255,255,65); color: white; padding: 6px 8px; }
                QLabel { background: rgba(28,28,32,215); border-radius: 7px; padding: 5px; }''')
            self.resize(min(360, area.width()-24), visible_count*STRIDE+12)
        else:
            color = appearance_background(self.dock.appearance, 'folder', self.dock.light)
            self.setStyleSheet(style(foreground(color) == '#1c1c1e')+'\nQDialog#BrisaFolderPopup { background: transparent; border: none; }')
            width = min(540, area.width()-24)
            columns = max(1, (width-32)//96)
            rows = max(1, (self.list.count()+columns-1)//columns)
            desired_height = rows*104+94 if grid else self.list.count()*34+94
            self.resize(width, min(430, area.height()-160, max(190, desired_height)))
        self.body.activate()
        if self.isVisible():
            if hasattr(self, 'animation'):
                self.animation.stop()
            self.position_above_dock()
            if fan and not previous:
                self.fan.reveal()

    def position_above_dock(self):
        if not self._fan_mode:
            if self.dock.edge != 'bottom':
                return super().position_above_dock()
            area = self.dock.dock_screen.availableGeometry()
            self.move(max(area.left()+8, min(area.right()-self.width()-8, self.anchor.center().x()-round(self.width()*.65))),
                      max(area.top()+8, self.anchor.top()-self.height()-8))
            return
        area = self.dock.dock_screen.availableGeometry()
        x = max(area.left()+8, min(area.right()-self.width()-8, self.anchor.center().x()-self.width()+112))
        y = max(area.top()+8, self.anchor.top()-self.height()-8)
        self.move(x, y)
        self.fan.spine_x = self.fan.mapFromGlobal(self.anchor.center()).x()
        self.fan.arrange()

    def showEvent(self, event):
        super().showEvent(event)
        if self._fan_mode:
            self.fan.reveal()

    def show_all_entries(self):
        if self.total_entries > self.list.count():
            self.open_folder()
            return
        # This is a temporary expanded view, not a change to the preferred mode.
        blocker = QSignalBlocker(self.mode)
        self.mode.setCurrentIndex(0)
        del blocker
        self.apply_mode()

    def mousePressEvent(self, event):
        if self._fan_mode and event.button() == Qt.LeftButton:
            self.reject()
            event.accept()
        else:
            super().mousePressEvent(event)

    def fan_options(self):
        menu = QMenu(self)
        for index, name in enumerate(('Rejilla', 'Lista', 'Abanico', 'Automático')):
            action = menu.addAction(name, lambda checked=False, value=index: self.mode.setCurrentIndex(value))
            action.setCheckable(True)
            action.setChecked(self.mode.currentIndex() == index)
        menu.addSeparator()
        order = menu.addMenu('Ordenar por')
        for index, (key, label) in enumerate(ORDERS):
            action = order.addAction(label, lambda checked=False, value=index: self.order.setCurrentIndex(value))
            action.setCheckable(True)
            action.setChecked(self.order.currentIndex() == index)
        representation = menu.addMenu('Mostrar en el Dock como')
        for key, label in (('folder', 'Carpeta'), ('stack', 'Pila')):
            action = representation.addAction(label, lambda checked=False, value=key: self.dock.set_folder_option('folder_display', value, str(self.initial_path)))
            action.setCheckable(True)
            action.setChecked(self.dock.folder_preferences(str(self.initial_path)).get('folder_display') == key)
        menu.addAction('Subir una carpeta', lambda: self.load(self.path.parent))
        menu.addSeparator()
        menu.addAction('Abrir en el Explorador', self.open_folder)
        menu.exec(QCursor.pos())

    def contextMenuEvent(self, event):
        self.fan_options()

    def paintEvent(self, event):
        if self._fan_mode:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        body = QRectF(.5, .5, self.width()-1, self.height()-11)
        arrow = QPainterPath()
        if self.dock.edge == 'bottom':
            x = max(24, min(self.width()-24, self.mapFromGlobal(self.anchor.center()).x()))
            arrow.moveTo(x-10, self.height()-11)
            arrow.lineTo(x+10, self.height()-11)
            arrow.lineTo(x, self.height()-1)
        elif self.dock.edge == 'left':
            body = QRectF(10.5, .5, self.width()-11, self.height()-1)
            y = max(24, min(self.height()-24, self.mapFromGlobal(self.anchor.center()).y()))
            arrow.moveTo(11, y-10)
            arrow.lineTo(11, y+10)
            arrow.lineTo(1, y)
        else:
            body = QRectF(.5, .5, self.width()-11, self.height()-1)
            y = max(24, min(self.height()-24, self.mapFromGlobal(self.anchor.center()).y()))
            arrow.moveTo(self.width()-11, y-10)
            arrow.lineTo(self.width()-11, y+10)
            arrow.lineTo(self.width()-1, y)
        arrow.closeSubpath()
        path.addRoundedRect(body, 17, 17)
        path = path.united(arrow)
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.setBrush(appearance_background(self.dock.appearance, 'folder', self.dock.light))
        painter.drawPath(path)

    def change_mode(self):
        self.apply_mode()
        self.dock.set_folder_option('folder_mode', ['grid', 'list', 'fan', 'auto'][self.mode.currentIndex()], str(self.initial_path))

    def change_order(self, index):
        self.dock.set_folder_option('folder_order', ORDERS[index][0], str(self.initial_path))
        self.load(self.path)

    def load(self, path):
        self.pool.clear()
        self.fan.set_items([])
        self.path = Path(path)
        self.location.setText(str(self.path))
        self.location.setToolTip(str(self.path))
        self.title.setText(self.path.name or str(self.path))
        self.list.clear()
        self.item_paths = {}
        self.up.setEnabled(self.path != self.path.parent)
        self.generation += 1
        self.status.setText('Cargando…')
        self.apply_mode()
        self.pool.start(DirectoryTask(self.path, ORDERS[self.order.currentIndex()][0], self.generation, self.signals))

    def directory_ready(self, generation, entries, count, error):
        if generation != self.generation:
            return
        if error:
            self.status.setText(f'No se puede leer esta carpeta: {error}')
            self.apply_mode()
            return
        for _, name, value, directory in entries:
            icon = stack_icon(value, directory=directory)
            item = QListWidgetItem(icon, name)
            item.setToolTip(name)
            item.setData(Qt.UserRole, value)
            item.setData(Qt.UserRole+1, directory)
            self.list.addItem(item)
            self.item_paths[value] = item
            if self.dock.application_icons and (directory or Path(value).suffix.lower() in ('.exe', '.lnk', '.ico', '.dll')):
                improved = self.dock.application_icons.request(value)
                if improved is not None:
                    item.setIcon(improved)
            if Path(value).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}:
                self.pool.start(ImageTask(value, self.signals))
        self.status.setText(f'{len(entries)} de {count} elementos' if entries else 'Carpeta vacía')
        self.total_entries = count
        self.apply_mode()
        if self._fan_mode:
            self.fan.reveal()
        if getattr(self.dock, 'keyboard_launch', False):
            if self._fan_mode and self.fan.buttons:
                self.fan.buttons[0].setFocus()
            elif self.list.count():
                self.list.setCurrentRow(0)
                self.list.setFocus()

    def image_ready(self, path, image):
        self.set_item_icon(path, QIcon(QPixmap.fromImage(image)))

    def native_icon_ready(self, path):
        item = self.item_paths.get(path)
        if item:
            icon = self.dock.application_icons.request(path)
            self.set_item_icon(path, icon or stack_icon(path, directory=bool(item.data(Qt.UserRole+1))))

    def set_item_icon(self, path, icon):
        item = self.item_paths.get(path)
        if item:
            item.setIcon(icon)
            for button, entry in zip(self.fan.buttons, self.fan.items):
                if entry is item:
                    button.setIcon(item.icon())

    def open_entry(self, item):
        if item is self.fan_open_item:
            self.open_folder()
            return
        path = Path(item.data(Qt.UserRole))
        if path.is_dir():
            self.load(path)
        elif self.dock.open_path(str(path)):
            self.accept()
        else:
            self.status.setText('El sistema no pudo abrir el archivo.')

    def open_folder(self):
        if self.dock.open_path(str(self.path)):
            self.accept()

    def done(self, result):
        self.pool.clear()
        self.fan.animation.stop()
        super().done(result)


class WindowPanel(Popup):
    def __init__(self, dock, item):
        super().__init__(dock, f"Ventanas · {item['name']}")
        self.windows = list(item.get('windows', []))
        self.page = 0
        self.thumbnails = None
        self.preview_results = []
        self.preview_sources = []
        self.cards = []
        area = dock.dock_screen.availableGeometry()
        self.columns = max(1, min(3, (area.width()-60)//254))
        self.page_size = self.columns * (2 if area.height() >= 700 else 1)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.body.addWidget(self.grid_widget)
        controls = QHBoxLayout()
        previous, following = QPushButton('←'), QPushButton('→')
        previous.clicked.connect(lambda: self.change_page(-1))
        following.clicked.connect(lambda: self.change_page(1))
        controls.addWidget(previous)
        self.page_label = QLabel()
        controls.addWidget(self.page_label, 1)
        controls.addWidget(following)
        self.previous, self.following = previous, following
        self.body.addLayout(controls)
        self.status = QLabel('Ventanas abiertas: vista en vivo. Minimizadas desde Brisa: última captura. Sin captura: título e icono.')
        if os.name != 'nt':
            self.status.setText('Selector de ventanas por título. Las vistas en vivo de KDE están pendientes.')
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self.populate()

    def populate(self):
        if self.thumbnails:
            self.thumbnails.clear()
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = []
        batch = self.windows[self.page*self.page_size:(self.page+1)*self.page_size]
        for index, window in enumerate(batch):
            card = QWidget()
            column = QVBoxLayout(card)
            button = QPushButton('Ventana minimizada' if window['minimized'] else 'Vista no disponible')
            button.setFixedSize(228, 128)
            button.setAccessibleName(f"Activar {window['title']}")
            image = self.dock.minimized_tiles.frames.get(preview_identity(window), (None, 0))[0]
            if image is not None:
                button.setIcon(QIcon(QPixmap.fromImage(image)))
                button.setIconSize(QSize(216, 116))
                button.setText('')
                button.setToolTip('Última vista antes de minimizar · '+window['title'])
            elif window.get('minimized'):
                button.setIcon(self.dock.icon_for(window))
                button.setIconSize(QSize(48, 48))
                button.setToolTip('Ventana minimizada sin captura previa · '+window['title'])
            button.clicked.connect(lambda checked=False, w=window: self.activate(w))
            column.addWidget(button)
            label = QLabel(window['title'][:56])
            label.setWordWrap(True)
            label.setFixedWidth(228)
            label.setToolTip(window['title'])
            column.addWidget(label)
            actions = QHBoxLayout()
            restore = QPushButton('Activar')
            restore.clicked.connect(lambda checked=False, w=window: self.activate(w))
            close = QPushButton('Cerrar')
            close.clicked.connect(lambda checked=False, w=window: self.request_close(w))
            actions.addWidget(restore)
            actions.addWidget(close)
            column.addLayout(actions)
            self.grid.addWidget(card, index//self.columns, index%self.columns)
            self.cards.append((button, window))
        total = max(1, (len(self.windows)+self.page_size-1)//self.page_size)
        self.page_label.setText(f'{len(self.windows)} ventanas · {self.page+1}/{total}')
        self.previous.setEnabled(self.page > 0)
        self.following.setEnabled(self.page+1 < total)
        self.adjustSize()
        if self.isVisible():
            self.position_above_dock()
            QTimer.singleShot(40, self.connect_previews)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self.dock, 'keyboard_launch', False) and self.cards:
            self.cards[0][0].setFocus()
        QTimer.singleShot(180, self.connect_previews)

    def connect_previews(self):
        if not self.isVisible() or os.name != 'nt' or self.property('testNoNative'):
            return
        if self.thumbnails:
            self.thumbnails.clear()
        self.preview_results, self.preview_sources = [], []
        try:
            from native_previews import DwmThumbnails
            self.thumbnails = DwmThumbnails(int(self.winId()))
            ratio = self.devicePixelRatioF()
            for button, window in self.cards:
                image = self.dock.minimized_tiles.frames.get(preview_identity(window), (None, 0))[0]
                cached = image is not None
                live = False
                # A successful registration can still yield a blank minimized source.
                if not window.get('minimized'):
                    point = button.mapTo(self, QPoint(4, 4))
                    rect = [round(value*ratio) for value in (point.x(), point.y(), button.width()-8, button.height()-8)]
                    valid = self.dock.watcher.backend and self.dock.watcher.backend.valid(window)
                    live = bool(valid and self.thumbnails.add(window['id'], rect))
                if live:
                    button.setText('')
                    button.setIcon(QIcon())
                    button.setToolTip('Vista en vivo · '+window['title'])
                self.preview_results.append(live or cached)
                self.preview_sources.append('live' if live else 'cache' if cached else 'title')
        except (OSError, AttributeError):
            self.status.setText('Vistas en vivo no disponibles; puedes elegir por última captura o título.')

    def change_page(self, direction):
        self.page += direction
        self.populate()

    def activate(self, window):
        self.dock.window_action('activate', window)
        self.accept()

    def request_close(self, window):
        self.dock.window_action('close_window', window)
        self.reject()  # Do not imply that an app accepted or finished closing.

    def done(self, result):
        if self.thumbnails:
            self.thumbnails.clear()
        super().done(result)


class PreferencesPanel(Popup):
    def __init__(self, dock):
        super().__init__(dock, 'Preferencias de Brisa Dock')
        # Settings own a modal color picker; a Qt.Popup would dismiss itself
        # when that child dialog takes focus.
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName('BrisaPreferencesContent')
        settings = QVBoxLayout(content)
        settings.setContentsMargins(0, 0, 4, 0)
        self.scroll.setWidget(content)
        content.setAutoFillBackground(False)
        self.body.addWidget(self.scroll, 1)
        form = QFormLayout()
        self.edge = QComboBox()
        self.edge.addItems(['Abajo', 'Izquierda', 'Derecha'])
        self.edge.setCurrentIndex(['bottom', 'left', 'right'].index(dock.edge))
        self.edge.currentIndexChanged.connect(self.change_edge)
        form.addRow('Posición', self.edge)
        self.screen_mode = QComboBox()
        self.screen_mode.addItems(['Seguir el borde del puntero', 'Solo pantalla principal', 'Pantalla elegida'])
        self.screen_mode.setCurrentIndex(MODES.index(dock.screen_mode))
        self.screen_choice = QComboBox()
        self.screen_mode.setAccessibleName('Comportamiento entre pantallas')
        self.screen_choice.setAccessibleName('Pantalla elegida para el Dock')
        form.addRow('Varias pantallas', self.screen_mode)
        form.addRow('Pantalla elegida', self.screen_choice)
        self.screen_notice = QLabel()
        self.screen_notice.setWordWrap(True)
        form.addRow(self.screen_notice)
        self.display_refresh = QTimer(self)
        self.display_refresh.setSingleShot(True)
        self.display_refresh.setInterval(100)
        self.display_refresh.timeout.connect(self.sync_display_controls)
        application = QApplication.instance()
        application.screenAdded.connect(self.queue_display_refresh)
        application.screenRemoved.connect(self.queue_display_refresh)
        application.primaryScreenChanged.connect(self.queue_display_refresh)
        dock.display_changed.connect(self.sync_display_controls)
        self.sync_display_controls()
        self.screen_mode.currentIndexChanged.connect(self.change_screen_mode)
        self.screen_choice.currentIndexChanged.connect(self.change_screen_selection)
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0, 32)
        self.gap.setDecimals(0)
        self.gap.setValue(dock.gap)
        self.gap.setSuffix(' px')
        self.gap.valueChanged.connect(lambda value: dock.set_preference('gap', int(value)))
        form.addRow('Separación del borde', self.gap)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(16, 72)
        self.size_slider.setValue(dock.size)
        self.size_slider.setAccessibleName('Tamaño de los iconos')
        self.size_label = QLabel(f'{dock.size} px')
        row = QHBoxLayout()
        row.addWidget(self.size_slider)
        row.addWidget(self.size_label)
        form.addRow('Tamaño', row)
        self.size_slider.valueChanged.connect(lambda v: self.size_label.setText(f'{v} px'))
        self.size_slider.sliderReleased.connect(lambda: dock.set_size(self.size_slider.value()))
        self.size_slider.valueChanged.connect(self.apply_keyboard_size)
        self.zoom = QDoubleSpinBox()
        self.zoom.setRange(1.1, 1.7)
        self.zoom.setSingleStep(.1)
        self.zoom.setValue(dock.magnification)
        self.zoom.setSuffix(' ×')
        self.zoom.valueChanged.connect(lambda value: dock.set_preference('magnification', value))
        form.addRow('Ampliación máxima', self.zoom)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(.2, 5)
        self.delay.setSingleStep(.1)
        self.delay.setValue(dock.hide_delay)
        self.delay.setSuffix(' s')
        self.delay.valueChanged.connect(lambda value: dock.set_preference('hide_delay', value))
        form.addRow('Espera para ocultarse', self.delay)
        self.animation = QComboBox()
        self.animation.addItems(['Reducir', 'Deformar hacia el icono'])
        self.animation.setCurrentIndex(1 if dock.animation_style == 'sheet' else 0)
        self.animation.setEnabled(os.name == 'nt')
        self.animation.currentIndexChanged.connect(lambda index: dock.set_preference('animation_style', 'sheet' if index else 'scale'))
        form.addRow('Animación de ventanas', self.animation)
        settings.addLayout(form)
        self.behavior_checks = {}
        for text, attribute in [('Ocultar automáticamente', 'auto_hide'),
                                ('Mostrar aplicaciones recientes', 'show_recent'),
                                ('Mostrar avisos visuales de atención', 'show_attention'),
                                ('Atajo de teclado Ctrl+Alt+F3 (Windows)', 'keyboard_hotkey'),
                                ('Ocultar en pantalla completa (Windows)', 'hide_fullscreen'),
                                ('Ampliar al pasar el cursor', 'magnify'), ('Apariencia clara', 'light'),
                                ('Animar minimización y restauración (Windows)', 'animate_minimize'),
                                ('Animar el botón Minimizar (ventanas compatibles, Windows)', 'animate_window_buttons'),
                                ('Minimizar dentro del icono de la aplicación (Windows)', 'minimize_into_app'),
                                ('Mostrar miniaturas al pulsar una app con varias ventanas', 'click_previews'),
                                ('Reservar espacio para ventanas maximizadas (Windows)', 'reserve_space')]:
            checkbox = QCheckBox(text)
            self.behavior_checks[attribute] = checkbox
            color = '#243346' if dock.light else '#eef4ff'
            checkbox.setStyleSheet(f'QCheckBox {{ color: {color}; padding: 7px; }}')
            checkbox.setChecked(getattr(dock, attribute))
            if attribute in ('reserve_space', 'animate_minimize', 'animate_window_buttons', 'hide_fullscreen', 'minimize_into_app', 'keyboard_hotkey') and os.name != 'nt':
                checkbox.setEnabled(False)
            checkbox.toggled.connect(lambda value, attr=attribute: dock.set_preference(attr, value))
            settings.addWidget(checkbox)
        settings.addWidget(QLabel('Colores y transparencia'))
        self.blur_checkbox = QCheckBox('Desenfoque real del Dock (experimental, Windows)')
        self.blur_checkbox.setChecked(dock.blur_enabled)
        self.blur_checkbox.setEnabled(dock.blur_shelf.supported)
        self.blur_checkbox.setStyleSheet(f'color: {"#243346" if dock.light else "#eef4ff"}; padding: 7px;')
        self.blur_checkbox.toggled.connect(lambda value: dock.set_preference('blur_enabled', value))
        settings.addWidget(self.blur_checkbox)
        self.appearance_preview = AppearancePreview(dock)
        settings.addWidget(self.appearance_preview)
        colors = QFormLayout()
        self.color_buttons, self.opacity_sliders, self.opacity_labels = {}, {}, {}
        for role, label in (('dock', 'Dock'), ('folder', 'Paneles de carpetas')):
            button = QPushButton()
            button.setAccessibleName('Elegir color de '+label)
            button.clicked.connect(lambda checked=False, role=role: self.choose_color(role))
            self.color_buttons[role] = button
            colors.addRow('Color de '+label, button)
            row = QHBoxLayout()
            slider = QSlider(Qt.Horizontal)
            slider.setRange(20 if role == 'dock' else 70, 100)
            slider.setAccessibleName('Opacidad de '+label)
            slider.valueChanged.connect(lambda value, role=role: dock.set_appearance(role+'_alpha', round(value*255/100)))
            self.opacity_sliders[role] = slider
            value_label = QLabel()
            self.opacity_labels[role] = value_label
            row.addWidget(slider)
            row.addWidget(value_label)
            colors.addRow('Opacidad de '+label, row)
        settings.addLayout(colors)
        self.reset_colors = QPushButton('Restablecer colores y opacidad')
        self.reset_colors.clicked.connect(lambda: dock.set_appearance())
        settings.addWidget(self.reset_colors)
        note = QLabel('El abanico conserva su fondo transparente. El desenfoque experimental solo afecta al Dock; si Windows no lo permite, se conserva el fondo con transparencia.')
        note.setWordWrap(True)
        settings.addWidget(note)
        dock.appearance_changed.connect(self.sync_appearance_controls)
        self.sync_appearance_controls()
        help_text = QLabel('Teclado: Ctrl+Alt+F3, flechas, Enter, Shift+F10 y Escape.\nCarpetas: opciones de vista y orden en su panel.')
        help_text.setWordWrap(True)
        settings.addWidget(help_text)
        if dock.dock_hotkey and dock.keyboard_hotkey and not dock.dock_hotkey.available:
            fallback = QLabel('Windows no permitió registrar el atajo. Puedes usar «Navegar con teclado» en el menú del Dock.')
            fallback.setWordWrap(True)
            settings.addWidget(fallback)
        settings.addStretch()
        self.setStyleSheet(self.styleSheet()+'\nQScrollArea, QWidget#BrisaPreferencesContent { background: transparent; border: none; }')
        area = dock.dock_screen.availableGeometry()
        self.resize(min(560, area.width()-24), min(620, area.height()-32))

    def queue_display_refresh(self, *args):
        self.display_refresh.start()

    def sync_display_controls(self):
        screens = QApplication.screens()
        blockers = [QSignalBlocker(self.screen_mode), QSignalBlocker(self.screen_choice)]
        self.screen_mode.setCurrentIndex(MODES.index(self.dock.screen_mode))
        self.screen_choice.clear()
        for index, screen in enumerate(screens):
            area, ratio = screen.geometry(), screen.devicePixelRatio()
            title = screen.model().strip() or screen.name()
            label = f'{index+1}. {title} · {round(area.width()*ratio)} × {round(area.height()*ratio)} · {round(ratio*100)} %'
            if screen == QApplication.primaryScreen():
                label += ' · Principal'
            self.screen_choice.addItem(label, screen_identity(screen))
        preferred = self.dock.screen_id or screen_identity(self.dock.dock_screen)
        chosen = self.screen_choice.findData(preferred)
        disconnected = chosen < 0
        if disconnected:
            self.screen_choice.addItem('Pantalla guardada (desconectada)', preferred)
            chosen = self.screen_choice.count()-1
        self.screen_choice.setCurrentIndex(chosen)
        self.screen_choice.setEnabled(self.dock.screen_mode == 'selected')
        if self.dock.screen_mode == 'selected' and disconnected:
            note = 'La pantalla elegida está desconectada. Se usa la principal hasta que vuelva.'
        elif self.dock.screen_mode == 'follow_edge':
            note = 'Un único Dock: mantén el puntero un instante en el borde de otra pantalla para llevarlo allí.'
        else:
            note = 'El Dock permanece en la pantalla indicada; mover el puntero a otra pantalla no lo traslada.'
        self.screen_notice.setText(note)
        del blockers
        if self.isVisible():
            area = self.dock.dock_screen.availableGeometry()
            self.resize(min(self.width(), area.width()-24), min(self.height(), area.height()-32))
            self.position_above_dock()

    def change_screen_mode(self, index):
        mode = MODES[index]
        if mode == 'selected' and not self.dock.screen_id:
            self.dock.set_preference('screen_id', self.screen_choice.currentData() or screen_identity(self.dock.dock_screen))
        self.dock.set_preference('screen_mode', mode)

    def change_screen_selection(self, index):
        identity = self.screen_choice.itemData(index)
        if self.dock.screen_mode == 'selected' and identity:
            self.dock.set_preference('screen_id', identity)

    def change_edge(self, index):
        self.dock.set_preference('edge', ['bottom', 'left', 'right'][index])
        if self.isVisible():
            self.position_above_dock()

    def sync_appearance_controls(self):
        for role, button in self.color_buttons.items():
            color = appearance_background(self.dock.appearance, role, self.dock.light)
            custom = self.dock.appearance[role+'_color']
            button.setText(color.name() if custom else 'Automático · '+color.name())
            button.setStyleSheet(f'background-color: {color.name()}; color: {foreground(color)};')
            value = round(self.dock.appearance[role+'_alpha']*100/255)
            slider = self.opacity_sliders[role]
            blocker = QSignalBlocker(slider)
            slider.setValue(value)
            del blocker
            self.opacity_labels[role].setText(str(value)+' %')

    def choose_color(self, role):
        current = appearance_background(self.dock.appearance, role, self.dock.light)
        color = QColorDialog.getColor(current, self, 'Color del '+('Dock' if role == 'dock' else 'panel de carpeta'),
                                      QColorDialog.DontUseNativeDialog)
        if color.isValid():
            self.dock.set_appearance(role+'_color', color.name())

    def apply_keyboard_size(self, value):
        if not self.size_slider.isSliderDown():
            self.dock.set_size(value)
