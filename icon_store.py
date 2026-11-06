"""Persistent per-profile icons. No entry-count eviction; atomic PNG/JSON files."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from PySide6.QtGui import QImageReader
from icon_visuals import visible_image

VERSION = 1


def canonical(path):
    if path.startswith(('package:', 'shell:')):
        return path.casefold()
    return os.path.normcase(os.path.abspath(path))


def fingerprint(path):
    if path.startswith('package:'):
        from package_paths import package_names
        return ['package', package_names(path.split(':', 1)[1])]
    try:
        value = os.stat(path)
        if stat.S_ISDIR(value.st_mode):
            return ['directory', value.st_ino]
        return ['file', value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino]
    except OSError:
        return None


def snapshot(paths):
    return {canonical(path): fingerprint(path) for path in paths if path}


def current(dependencies):
    return isinstance(dependencies, dict) and bool(dependencies) and all(
        fingerprint(path) == value for path, value in dependencies.items())


class IconStore:
    def __init__(self, root):
        self.root = Path(root)

    def files(self, path):
        key = hashlib.sha256(canonical(path).encode('utf-8')).hexdigest()
        return self.root/(key+'.json'), self.root/(key+'.png')

    def load(self, path):
        metadata, image_path = self.files(path)
        try:
            if metadata.stat().st_size > 65536:
                return None
            data = json.loads(metadata.read_text(encoding='utf-8'))
            if data.get('version') != VERSION or canonical(data['path']) != canonical(path) or not current(data['dependencies']):
                return None
            # Old versions classified every desktop Shell identifier as an Appx package.
            # Invalidate only those legacy entries, not the rest of the icon cache.
            if data['path'].startswith('shell:AppsFolder\\') and '!' not in data['path'] and any(key.startswith('package:') for key in data['dependencies']):
                return None
            if data.get('image'):
                reader = QImageReader(str(image_path))
                size = reader.size()
                if not size.isValid() or max(size.width(), size.height()) > 256:
                    return None
                image = reader.read()
                if image.isNull():
                    return None
                if not visible_image(image):
                    image = None
            else:
                image = None
            return dict(image=image, dependencies=data['dependencies'])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, path, image, dependencies):
        metadata, image_path = self.files(path)
        suffix = '.'+uuid.uuid4().hex+'.tmp'
        temporary_meta, temporary_image = Path(str(metadata)+suffix), Path(str(image_path)+suffix)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if image is not None:
                if not image.save(str(temporary_image), 'PNG'):
                    return
                os.replace(temporary_image, image_path)
            data = dict(version=VERSION, path=path, dependencies=dependencies, image=image is not None)
            temporary_meta.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary_meta, metadata)
        except OSError:
            pass  # A read-only/full cache must not prevent using the icon in RAM.
        finally:
            for temporary in (temporary_meta, temporary_image):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def entries(self):
        try:
            for metadata in self.root.glob('*.json'):
                try:
                    if metadata.stat().st_size > 65536:
                        continue
                    path = json.loads(metadata.read_text(encoding='utf-8'))['path']
                    result = self.load(path)
                    if result is not None:
                        yield path, result
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        except OSError:
            return
