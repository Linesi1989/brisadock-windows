# BrisaDock para Windows

BrisaDock 0.38 es un dock de escritorio gratuito para Windows 11 x64. Permite fijar y ordenar aplicaciones, archivos y carpetas, mostrar ventanas abiertas y minimizadas, y abrir pilas de carpetas en abanico, rejilla o lista. El código propio se distribuye bajo [MIT](LICENSE). Las bibliotecas que acompañan la compilación conservan sus [licencias](THIRD_PARTY/AVISOS.md).

## Compilar

Instala Python 3.12 x64 y ejecuta desde PowerShell en esta carpeta:

```powershell
.\construir-windows.ps1
```

Ejecuta `dist\BrisaDock\BrisaDock.exe` con la carpeta `_internal` junto a él. Cierra otra copia del Dock antes de probar. Para compilar el instalador tradicional, abre `BrisaDock.iss` con Inno Setup 7 después de compilar el ejecutable.

Las preferencias, los accesos fijados y la caché se almacenan en el perfil local. Las miniaturas se crean para mostrarlas en el Dock. Las acciones que abren un enlace en el navegador o entregan un archivo a otra aplicación quedan sujetas al comportamiento de esa aplicación.

## Distribuciones

La versión de Linux se mantiene en un [repositorio independiente](https://github.com/Linesi1989/brisadock--linux).

La compilación de este código genera un ejecutable sin firma propia. La [preparación para SignPath](SIGNPATH.md) describe los pasos que faltan para solicitar firma de distribución.

