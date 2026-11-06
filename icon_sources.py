"""Resolve native/package/custom-folder icon resources without launching apps."""
import configparser
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from PySide6.QtCore import QFileInfo, Qt
from PySide6.QtGui import QImage, QImageReader
from icon_store import snapshot


def icon_location(value, base):
    parts = value.rsplit(',', 1)
    try:
        index = int(parts[1]) if len(parts) == 2 else 0
        filename = parts[0]
    except ValueError:
        filename, index = value, 0
    path = Path(os.path.expandvars(filename.strip().strip('"')))
    return str(path if path.is_absolute() else base/path), index


def package_logo(path):
    for folder in list(Path(path).parents)[:3]:
        manifest = folder/'AppxManifest.xml'
        if not manifest.is_file():
            continue
        try:
            root = ET.parse(manifest).getroot()
            for app in root.iter():
                if app.tag.rsplit('}', 1)[-1] != 'Application':
                    continue
                executable = app.get('Executable', '').replace('\\', '/')
                if Path(executable).name.casefold() != Path(path).name.casefold():
                    continue
                candidates = []
                dependencies = [str(manifest), path]
                for visual in app.iter():
                    for key in ('Square44x44Logo', 'Square150x150Logo', 'Logo'):
                        resource = visual.get(key)
                        if not resource or resource.startswith('ms-resource:'):
                            continue
                        base = folder/resource.replace('\\', '/')
                        dependencies.append(str(base))
                        for candidate in base.parent.glob(base.stem+'*'+base.suffix):
                            size = QImageReader(str(candidate)).size()
                            if size.isValid():
                                # Prefer unplated artwork and sufficient source pixels.
                                score = (min(256, size.width()), 'unplated' in candidate.name,
                                         -abs(size.width()-256))
                                candidates.append((score, candidate))
                if candidates:
                    resource = max(candidates, key=lambda item: item[0])[1]
                    return str(resource), 0, dependencies+[str(resource)]
        except (OSError, ET.ParseError, ValueError):
            pass
    return None


def resolve_source(path):
    dependencies = [path]
    source, index = path, 0
    if path.startswith('shell:AppsFolder\\'):
        from package_paths import executable_for_aumid, known_folder_target
        identifier = path.split('\\', 1)[1]
        if '!' in identifier:
            dependencies.append('package:'+identifier.split('!', 1)[0])
            source = executable_for_aumid(path)
        else:
            source = known_folder_target(path, existing=False)
        if not source:
            return None, 0, dependencies
        dependencies.append(source)
    elif Path(path).is_dir():
        ini = Path(path)/'desktop.ini'
        dependencies.append(str(ini))
        try:
            raw = ini.read_bytes()
            encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
            try:
                text = raw.decode(encoding)
            except UnicodeError:
                text = raw.decode('mbcs')
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read_string(text)
            section_name = next((name for name in parser.sections() if name.casefold() == '.shellclassinfo'), '')
            section = parser[section_name]
            value = section.get('IconResource') or section.get('IconFile')
            if not value:
                return None, 0, dependencies
            source, index = icon_location(value, Path(path))
            if not section.get('IconResource'):
                index = int(section.get('IconIndex', '0'))
            dependencies.append(source)
        except (OSError, ValueError, KeyError, configparser.Error):
            return None, 0, dependencies
    elif Path(path).suffix.lower() == '.lnk':
        class Info(C.Structure):
            _fields_ = [('icon', W.HICON), ('index', C.c_int), ('attributes', W.DWORD),
                        ('display', W.WCHAR*260), ('kind', W.WCHAR*80)]
        shell = C.WinDLL('shell32')
        shell.SHGetFileInfoW.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(Info), W.UINT, W.UINT]
        shell.SHGetFileInfoW.restype = C.c_size_t
        info = Info()
        shell.SHGetFileInfoW(path, 0, C.byref(info), C.sizeof(info), 0x1000)
        source, index = info.display, info.index
        target = QFileInfo(path).symLinkTarget()
        dependencies += [source, target]
        if not source or not Path(source).is_file():
            source, index = target, 0
    if source and Path(source).suffix.lower() == '.msc':
        try:
            if Path(source).stat().st_size <= 2*1024*1024:
                icon=next((node for node in ET.parse(source).getroot().iter() if node.tag.rsplit('}',1)[-1]=='Icon' and node.get('File')),None)
                if icon is not None:
                    resource=Path(os.path.expandvars(icon.get('File')))
                    dependencies.append(str(resource))
                    if not resource.is_file() and resource.parent.name.casefold() in ('system32','syswow64') and resource.parent.parent.name.casefold()=='windows':
                        resource=Path(os.environ.get('WINDIR','C:/Windows'))/resource.parent.name/resource.name
                    if resource.is_file():
                        dependencies.append(str(resource));source,index=str(resource),int(icon.get('Index','0'))
        except (OSError,ET.ParseError,ValueError):
            pass
    if source and Path(source).suffix.lower() == '.exe':
        logo = package_logo(source)
        if logo:
            source, index, package_dependencies = logo
            dependencies += package_dependencies
    return source, index, dependencies


def extract_resource(path, index=0):
    if not path:
        return None
    suffix = Path(path).suffix.lower()
    if suffix in ('.ico', '.png', '.svg', '.jpg', '.jpeg', '.webp'):
        reader = QImageReader(path)
        if suffix == '.ico':
            largest = max(range(max(1, reader.imageCount())),
                          key=lambda number: icon_size(path, number))
            reader.jumpToImage(largest)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > 256:
            reader.setScaledSize(size.scaled(256, 256, Qt.KeepAspectRatio))
        image = reader.read()
        return None if image.isNull() else image
    if suffix not in ('.exe', '.dll'):
        return None
    shell, user = C.WinDLL('shell32'), C.WinDLL('user32')
    shell.SHDefExtractIconW.argtypes = [W.LPCWSTR, C.c_int, W.UINT, C.POINTER(W.HICON), C.c_void_p, W.UINT]
    shell.SHDefExtractIconW.restype = W.LONG
    user.DestroyIcon.argtypes = [W.HICON]
    handle = W.HICON()
    try:
        if shell.SHDefExtractIconW(path, index, 0, C.byref(handle), None, 256) != 0 or not handle.value:
            return None
        image = QImage.fromHICON(handle.value)
        return None if image.isNull() else image.copy()
    finally:
        if handle.value:
            user.DestroyIcon(handle)


def icon_size(path, number):
    reader = QImageReader(path)
    reader.jumpToImage(number)
    return reader.size().width()


def extract_with_dependencies(path):
    for attempt in range(2):
        resource, index, paths = resolve_source(path)
        before = snapshot(paths)
        image = extract_resource(resource, index)
        after = snapshot(paths)
        if before == after:
            return image, after
    raise OSError('Icon source changed during extraction')


def extract_image(path):
    return extract_with_dependencies(path)[0]
