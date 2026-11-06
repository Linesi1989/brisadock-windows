"""Pin an app from its open windows without browsing executable folders."""
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QLabel, QPushButton, QScrollArea, QWidget,
                               QVBoxLayout, QHBoxLayout)
from panels import Popup
from desktop_backend import canonical


class RunningAppsPanel(Popup):
    def __init__(self, dock):
        super().__init__(dock, 'Aplicaciones abiertas')
        hint = QLabel('Mantén una aplicación en el Dock sin buscar sus archivos.')
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidgetResizable(True)
        background = '#edf2f7' if dock.light else '#202938'
        self.scroll.viewport().setStyleSheet(f'background-color: {background};')
        self.content = QWidget()
        self.content.setObjectName('BrisaRunningAppsContent')
        self.content.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(self.styleSheet()+
                          f'\nQScrollArea, QWidget#BrisaRunningAppsContent {{ background-color: {background}; border: none; }}')
        self.rows = QVBoxLayout(self.content)
        self.scroll.setWidget(self.content)
        self.body.addWidget(self.scroll)
        self.signature = None
        self.buttons = []
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(750)
        area = dock.dock_screen.availableGeometry()
        self.resize(min(520, area.width()-24), min(440, area.height()-24))

    def refresh(self):
        entries = self.dock.running_applications()
        pinned = {canonical(self.dock.targets.get(pin['path'], pin['path']))
                  for pin in self.dock.pins}
        signature = tuple((entry['key'], len(entry['windows']), entry['key'] in pinned)
                          for entry in entries)
        if signature == self.signature:
            return
        self.signature = signature
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.buttons = []
        if not entries:
            self.rows.addWidget(QLabel('No hay aplicaciones con ventanas abiertas.'))
        for entry in entries:
            row = QWidget()
            layout = QHBoxLayout(row)
            icon = QLabel()
            icon.setPixmap(self.dock.icon_for(entry).pixmap(32, 32))
            layout.addWidget(icon)
            count = len(entry['windows'])
            label = QLabel(f"{entry['name']}\n{count} ventana"+('s' if count != 1 else ''))
            layout.addWidget(label, 1)
            fixed = entry['key'] in pinned
            button = QPushButton('Ya está en el Dock' if fixed else 'Mantener en el Dock')
            button.setAccessibleName(('Ya fijada: ' if fixed else 'Fijar ')+entry['name'])
            button.setEnabled(not fixed and Path(entry['path']).is_file())
            def pin(checked=False, entry=entry, button=button):
                self.dock.pin_application(entry)
                if any(canonical(self.dock.targets.get(pin['path'], pin['path'])) == entry['key']
                       for pin in self.dock.pins):
                    button.setText('Ya está en el Dock')
                    button.setEnabled(False)
                self.signature = None
            button.clicked.connect(pin)
            layout.addWidget(button)
            self.rows.addWidget(row)
            self.buttons.append((entry, button))
        self.rows.addStretch()
