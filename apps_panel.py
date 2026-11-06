"""Searchable application grid; discovery stays off the GUI thread."""
import os
import threading
import time

from PySide6.QtCore import Qt, QSize, QTimer, QThread, Signal, QUrl, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QShortcut, QKeySequence
from PySide6.QtWidgets import (QLineEdit, QListWidget, QListWidgetItem, QListView,
                               QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QMenu,
                               QWidget, QScrollArea, QStackedWidget, QInputDialog, QAbstractItemView)
from panels import Popup
from applications import scan_windows_shortcuts, scan_desktop_entries, store_apps, merge_catalog, search_text
from catalog_drag import CatalogList, DragIcon, over_dock


class CatalogWorker(QThread):
    ready = Signal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop = threading.Event()

    def run(self):
        try:
            if os.name == 'nt':
                entries = scan_windows_shortcuts(stop=self.stop)
                self.ready.emit(entries, False)
                if not self.stop.is_set():
                    entries = merge_catalog(entries, store_apps(self.stop))
            else:
                entries = scan_desktop_entries(stop=self.stop)
            if not self.stop.is_set():
                self.ready.emit(entries, True)
        except Exception:
            if not self.stop.is_set():
                self.ready.emit([], True)


class ApplicationsPanel(Popup):
    def __init__(self, dock):
        super().__init__(dock, 'Aplicaciones')
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self.setObjectName('BrisaApplicationsPopup')
        self._finished = False
        self.setAttribute(Qt.WA_TranslucentBackground)
        text = '#232328' if dock.light else '#f2f2f5'
        self.setStyleSheet(f'''
            QDialog#BrisaApplicationsPopup {{ background: transparent; border: none; }}
            QLabel {{ color: {text}; background: transparent; border: none; }}
            QLineEdit {{ color: {text}; background: transparent; border: none; padding: 8px; font-size: 25px; }}
            QPushButton {{ color: {text}; background: transparent; border: none; border-radius: 8px; padding: 7px; }}
            QPushButton:hover {{ background: rgba(128,128,140,45); }}
            QListWidget {{ color: {text}; background: transparent; border: none; outline: none; }}
            QListWidget::item {{ border-radius: 10px; padding: 4px; }}
            QListWidget::item:hover {{ background: rgba(128,128,140,35); }}
            QListWidget::item:selected {{ background: rgba(65,140,240,90); }}
            QScrollArea, QWidget#AppsSections {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: rgba(128,128,140,90); border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        ''')
        # The search field is the header, as in the modern application browser.
        self.title.hide()
        self.header.itemAt(1).widget().hide()
        self.entries = list(dock.catalog)
        self.page = 0
        self.filtered = []
        self.category = None
        self.all_apps = dock.config.get('apps_layout') == 'all'
        self._opening = False
        self.drag_entry = None
        self.drag_session = False
        self.drag_icon = None
        self.deferred_catalog = None
        self.section_lists = []
        self.connected_worker = None
        self.search = QLineEdit()
        self.search.setPlaceholderText('Aplicaciones')
        self.search.setAccessibleName('Buscar aplicaciones')
        self.search.setClearButtonEnabled(True)
        self.search.setFont(QFont('Segoe UI', 23))
        heading = QHBoxLayout()
        heading.addWidget(self.search, 1)
        self.running_button = QPushButton('Abiertas')
        self.running_button.setAccessibleName('Añadir una aplicación abierta al Dock')
        def show_running():
            self.reject()
            QTimer.singleShot(0, dock.running_applications_panel)
        self.running_button.clicked.connect(show_running)
        heading.addWidget(self.running_button)
        options = QPushButton('•••')
        options.setAccessibleName('Opciones del navegador de aplicaciones')
        options.clicked.connect(lambda: self.options_menu(options))
        heading.addWidget(options)
        self.body.addLayout(heading)
        layouts = QHBoxLayout()
        self.categories_button = QPushButton('Categorías')
        self.categories_button.clicked.connect(lambda: self.show_category(None))
        self.all_button = QPushButton('Todas')
        self.all_button.clicked.connect(self.show_all)
        layouts.addWidget(self.categories_button)
        layouts.addWidget(self.all_button)
        layouts.addStretch()
        self.body.addLayout(layouts)
        self.back = QPushButton('‹  Todas las categorías')
        self.back.clicked.connect(lambda: self.show_category(None))
        self.body.addWidget(self.back)
        self.views = QStackedWidget()
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().setAutoFillBackground(False)
        self.sections = QWidget()
        self.sections.setObjectName('AppsSections')
        self.sections_layout = QVBoxLayout(self.sections)
        self.sections_layout.setContentsMargins(2, 2, 2, 2)
        self.sections_layout.setSpacing(8)
        self.scroll.setWidget(self.sections)
        self.sections.setAutoFillBackground(False)
        self.views.addWidget(self.scroll)
        self.list = self.make_list()
        if dock.application_icons:
            dock.application_icons.changed.connect(self.refresh_icon)
        self.list.verticalScrollBar().valueChanged.connect(lambda value: self.request_visible_icons())
        self.scroll.verticalScrollBar().valueChanged.connect(lambda value: self.request_visible_icons())
        self.views.addWidget(self.list)
        self.body.addWidget(self.views, 1)
        controls = QHBoxLayout()
        self.status = QLabel('Leyendo aplicaciones…')
        controls.addWidget(self.status, 1)
        self.body.addLayout(controls)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.timeout.connect(self.filter_now)
        self.search.textChanged.connect(lambda: self.debounce.start(100))
        self.search.returnPressed.connect(self.open_selected)
        down = QShortcut(QKeySequence(Qt.Key_Down), self.search)
        down.setContext(Qt.WidgetShortcut)
        down.activated.connect(self.focus_results)
        area = dock.dock_screen.availableGeometry()
        self.resize(min(740, area.width()-32), min(680, area.height()-110))
        self.filter_now()
        # Re-polish after reparenting the scroll viewport and its children.
        self.setStyleSheet(self.styleSheet())
        QTimer.singleShot(0, self.start_scan)

    def showEvent(self, event):
        self._finished = False
        super().showEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(255, 255, 255, 110))
        painter.setBrush(QColor(237, 241, 248, 250) if self.dock.light else QColor(35, 36, 42, 250))
        painter.drawRoundedRect(QRectF(.5, .5, self.width()-1, self.height()-1), 24, 24)

    def make_list(self):
        view = CatalogList(self)
        view.setFrameShape(QListWidget.NoFrame)
        view.viewport().setAutoFillBackground(False)
        view.setMovement(QListView.Static)
        view.setResizeMode(QListView.Adjust)
        view.setWordWrap(True)
        view.setSelectionMode(QAbstractItemView.SingleSelection)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.configure_view(view)
        view.itemClicked.connect(lambda item: self.open_entry(item.data(Qt.UserRole)))
        view.setContextMenuPolicy(Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(lambda point, source=view: self.context_menu(point, source))
        enter = QShortcut(QKeySequence(Qt.Key_Return), view)
        enter.setContext(Qt.WidgetShortcut)
        enter.activated.connect(lambda source=view: self.open_from(source))
        return view

    def configure_view(self, view):
        listing = self.dock.config.get('apps_view') == 'list'
        view.setViewMode(QListView.ListMode if listing else QListView.IconMode)
        view.setIconSize(QSize(32, 32) if listing else QSize(64, 64))
        view.setGridSize(QSize(0, 48) if listing else QSize(132, 116))

    def category_for(self, entry):
        custom = self.dock.config.get('app_categories', {})
        value = custom.get(entry['path']) if isinstance(custom, dict) else None
        return value or entry.get('category') or 'Sin clasificar'

    def refresh_icon(self, path):
        if self._finished:
            return
        for view in [self.list]+self.section_lists:
            for row in range(view.count()):
                item = view.item(row)
                entry = item.data(Qt.UserRole)
                if entry['path'] == path:
                    item.setIcon(self.dock.icon_for(entry))

    def request_visible_icons(self):
        if self._finished:
            return
        views = [self.list] if self.views.currentWidget() is self.list else self.section_lists
        for view in views:
            if view.visibleRegion().isEmpty():
                continue
            for row in range(view.count()):
                item = view.item(row)
                if view.visualItemRect(item).intersects(view.viewport().rect()):
                    item.setIcon(self.dock.icon_for(item.data(Qt.UserRole)))

    def show_category(self, category):
        self.category = category
        self.all_apps = False
        self.dock.config['apps_layout'] = 'categories'
        self.dock.save()
        self.filter_now()

    def show_all(self):
        self.category = None
        self.all_apps = True
        self.dock.config['apps_layout'] = 'all'
        self.dock.save()
        self.filter_now()

    def focus_results(self):
        target = self.list if self.views.currentWidget() is self.list else next(iter(self.section_lists), self.list)
        target.setFocus()
        if target.count() and target.currentRow() < 0:
            target.setCurrentRow(0)

    def open_from(self, source):
        item = source.currentItem()
        if item:
            self.open_entry(item.data(Qt.UserRole))

    def options_menu(self, button):
        menu = QMenu(self)
        for key, label in (('grid', 'Rejilla'), ('list', 'Lista')):
            action = menu.addAction(label, lambda checked=False, mode=key: self.set_view(mode))
            action.setCheckable(True)
            action.setChecked(self.dock.config.get('apps_view', 'grid') == key)
        menu.addSeparator()
        menu.addAction('Actualizar catálogo', lambda: self.start_scan(True))
        menu.addAction('Cerrar', self.reject)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def set_view(self, mode):
        self.dock.config['apps_view'] = mode
        self.dock.save()
        self.configure_view(self.list)
        self.filter_now()

    def start_scan(self, force=False):
        if self._finished:
            return
        self.search.setFocus()
        if not force and self.dock.catalog and self.dock.catalog_time > 0 and time.monotonic()-self.dock.catalog_time < 60:
            self.entries = list(self.dock.catalog)
            self.filter_now()
            return
        worker = self.dock.catalog_worker
        if worker is None or not worker.isRunning():
            if worker is not None:
                worker.deleteLater()
            worker = CatalogWorker(self.dock)
            self.dock.catalog_worker = worker
            worker.ready.connect(self.dock.receive_catalog)
            worker.ready.connect(self.receive)
            self.connected_worker = worker
            worker.start()
        elif self.connected_worker is not worker:
            worker.ready.connect(self.receive)
            self.connected_worker = worker
        self.status.setText('Leyendo aplicaciones…')

    def receive(self, entries, complete):
        if self._finished:
            return
        if self.drag_session:
            self.deferred_catalog = (list(entries), complete)
            return
        scroll_position = self.scroll.verticalScrollBar().value()
        focused_path = None
        for view in self.section_lists:
            if view.hasFocus() and view.currentItem():
                focused_path = view.currentItem().data(Qt.UserRole)['path']
        self.entries = list(entries)
        self.filter_now()
        self.scroll.verticalScrollBar().setValue(scroll_position)
        if focused_path:
            for view in self.section_lists:
                for index in range(view.count()):
                    if view.item(index).data(Qt.UserRole)['path'] == focused_path:
                        view.setCurrentRow(index)
                        view.setFocus()
        if not complete:
            self.status.setText(self.status.text()+' · leyendo catálogo adicional…')

    def filter_now(self):
        if self.drag_session:
            return
        selected = self.list.currentItem()
        selected_path = selected.data(Qt.UserRole)['path'] if selected else None
        tokens = search_text(self.search.text()).split()
        self.filtered = [entry for entry in self.entries
                         if (self.category is None or self.category_for(entry) == self.category)
                         and all(token in search_text(entry['name']+' '+entry.get('keywords', '')) for token in tokens)]
        self.filtered.sort(key=lambda entry: (not search_text(entry['name']).startswith(search_text(self.search.text())), search_text(entry['name'])))
        self.page = 0
        self.render_page(selected_path)

    def fill_list(self, view, entries, selected_path=None):
        view.clear()
        selected_row = 0
        for entry in entries:
            if entry['path'] == selected_path:
                selected_row = view.count()
            view.addItem(self.make_item(entry))
        if view.count():
            view.setCurrentRow(selected_row)

    def make_item(self, entry):
        icon = self.dock.icon_for(entry, high_resolution=False)
        item = QListWidgetItem(icon, entry['name'])
        item.setToolTip(entry['name']+'\nArrastra al Dock para mantener esta aplicación.')
        item.setData(Qt.UserRole, entry)
        return item

    def render_page(self, selected_path=None):
        self.fill_list(self.list, self.filtered, selected_path)
        self.back.setVisible(self.category is not None)
        self.status.setText(f'{len(self.filtered)} aplicaciones' + (f' · {self.category}' if self.category else ''))
        grouped = not self.all_apps and not self.search.text().strip() and self.category is None
        self.all_button.setText(f'Todas ({len(self.entries)})')
        self.all_button.setEnabled(not self.all_apps or self.category is not None)
        if grouped:
            self.status.setText(f'{len(self.entries)} en el catálogo · resumen por categorías · pulsa Todas para verlas completas')
        self.views.setCurrentWidget(self.scroll if grouped else self.list)
        if not grouped:
            QTimer.singleShot(0, self.request_visible_icons)
            return
        self.section_lists = []
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        groups = {}
        pinned_paths = {entry['path'] for entry in self.dock.pins}
        pinned = [entry for entry in self.filtered if entry['path'] in pinned_paths]
        if pinned:
            self.add_section('En tu Dock', pinned, None)
        for entry in self.filtered:
            groups.setdefault(self.category_for(entry), []).append(entry)
        for category in sorted(groups, key=lambda value: (value == 'Sin clasificar', search_text(value))):
            self.add_section(category, groups[category], category)
        self.sections_layout.addStretch()
        QTimer.singleShot(0, self.request_visible_icons)

    def add_section(self, title, entries, category):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 10, 0, 10)
        heading = QHBoxLayout()
        label = QLabel(title)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        heading.addWidget(label, 1)
        if category:
            more = QPushButton(f'Ver todos ({len(entries)})')
            more.clicked.connect(lambda checked=False, value=category: self.show_category(value))
            heading.addWidget(more)
        layout.addLayout(heading)
        listing = self.dock.config.get('apps_view') == 'list'
        columns = max(1, (self.width()-44)//132)
        preview = entries[:(4 if listing else columns)]
        view = self.make_list()
        self.fill_list(view, preview)
        view.setCurrentRow(-1)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setFixedHeight(len(preview)*48+4 if listing else 122)
        layout.addWidget(view)
        self.section_lists.append(view)
        self.sections_layout.addWidget(block)

    def open_selected(self):
        self.debounce.stop()
        if self.search.hasFocus():
            self.filter_now()
        item = self.list.currentItem()
        if item:
            self.open_entry(item.data(Qt.UserRole))

    def open_entry(self, entry):
        if self._opening or self.drag_session:
            return
        self._opening = True
        if self.dock.open_path(entry['path']):
            self.accept()
        else:
            self._opening = False
            self.status.setText('El sistema no pudo abrir esta aplicación.')

    def start_catalog_drag(self, entry, icon=None):
        self.debounce.stop()
        self.drag_entry = dict(entry)
        self.drag_session = True
        self.dock.reveal_instance()
        self.setWindowOpacity(0)
        self.drag_icon = DragIcon(self, entry, icon)
        self.drag_icon.show()

    def move_catalog_drag(self, global_position):
        if not self.drag_entry:
            return
        self.drag_icon.move(global_position.x()-36, global_position.y()-36)
        self.dock.external_drag = True
        self.dock.drop_label = ('Mantener en el Dock: '+self.drag_entry['name'] if over_dock(self.dock, global_position)
                                else 'Lleva la aplicación a la barra · Escape para cancelar')
        self.dock.update()

    def clear_catalog_drag(self, keep_capture=False):
        self.drag_entry = None
        self.drag_session = keep_capture
        if self.drag_icon:
            self.drag_icon.close()
            self.drag_icon.deleteLater()
            self.drag_icon = None
        self.setWindowOpacity(1)
        self.dock.reset_document_drag()

    def finish_catalog_drag(self, global_position):
        entry = self.drag_entry
        valid = entry is not None and over_dock(self.dock, global_position)
        self.clear_catalog_drag()
        if valid and self.dock.pin_catalog_entry(entry, global_position):
            self.accept()
            return
        self.status.setText('No se añadió ninguna aplicación.' if not valid else self.dock.integration_status)
        self.restore_catalog_after_drag()

    def cancel_catalog_drag(self):
        self.clear_catalog_drag(keep_capture=True)
        self.status.setText('Arrastre cancelado.')

    def restore_catalog_after_drag(self):
        if self.deferred_catalog:
            entries, complete = self.deferred_catalog
            self.deferred_catalog = None
            self.receive(entries, complete)

    def done(self, result):
        self._finished = True
        self.clear_catalog_drag()
        super().done(result)

    def context_menu(self, point, source=None):
        source = source or self.list
        item = source.itemAt(point)
        if not item:
            return
        entry = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction('Abrir', lambda: self.open_entry(entry))
        menu.addAction('Mantener en el dock', lambda: self.dock.pin_application(entry))
        menu.addAction('Cambiar categoría…', lambda: self.assign_category(entry))
        menu.exec(source.viewport().mapToGlobal(point))

    def assign_category(self, entry):
        value, accepted = QInputDialog.getText(self, 'Categoría', 'Nombre de la categoría:', text=self.category_for(entry))
        if accepted and value.strip():
            if not isinstance(self.dock.config.get('app_categories'), dict):
                self.dock.config['app_categories'] = {}
            self.dock.config['app_categories'][entry['path']] = value.strip()[:80]
            self.dock.save()
            self.filter_now()
