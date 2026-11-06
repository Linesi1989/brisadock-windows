# Componentes de terceros

El código de Brisa Dock se entrega en los módulos Python, bajo MIT. Los componentes
incluidos en el portable conservan sus licencias respectivas:

- Python 3.12.14: Python-LICENSE.txt.
- PyInstaller 6.12.0, bootloader: PyInstaller-COPYING.txt, con excepción para
  distribuir aplicaciones generadas. Fuente: https://github.com/pyinstaller/pyinstaller/tree/v6.12.0
- PySide6, Shiboken y Qt 6.8.3: se utilizan las opciones de licencia de código
  abierto correspondientes. Se adjuntan LGPL-3.0.txt y GPL-3.0.txt.
  Fuentes de la versión: https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.8.3
  y https://code.qt.io/cgit/qt/qtbase.git/?h=v6.8.3
  Avisos de Qt y sus dependencias: https://doc.qt.io/qt-6/licenses-used-in-qt.html
  Avisos de Qt for Python: https://doc.qt.io/qtforpython-6/licenses.html

Las DLL se distribuyen separadas en dist/BrisaDock/_internal. El código
editable y el script de compilación permiten generar otra copia usando versiones
modificadas de las bibliotecas. Brisa Dock no aplica restricciones para modificar
esas bibliotecas ni para depurarlas. La licencia MIT no sustituye sus licencias.

