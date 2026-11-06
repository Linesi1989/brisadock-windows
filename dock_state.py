"""Small state models independent of desktop APIs."""
from pathlib import Path
from desktop_backend import canonical


class RecentApplications:
    def __init__(self, entries=()):
        self.entries = []
        for entry in entries or ():
            if isinstance(entry, dict) and isinstance(entry.get('path'), str) and isinstance(entry.get('name'), str):
                self.entries.append(dict(name=entry['name'], path=entry['path'], key=canonical(entry['path'])))
        self.entries = self.entries[:6]

    def observe(self, previous, current):
        before = {window['key']: window for window in previous}
        after = {window['key'] for window in current}
        changed = False
        for key in reversed(list(before)):
            window = before[key]
            if key not in after and Path(window['path']).is_file():
                self.entries = [entry for entry in self.entries if entry['key'] != key]
                self.entries.insert(0, dict(name=window['name'], path=window['path'], key=key))
                self.entries = self.entries[:6]
                changed = True
        return changed

    def visible(self, excluded):
        return [dict(entry, recent=True, windows=[]) for entry in self.entries
                if entry['key'] not in excluded and Path(entry['path']).is_file()][:3]

    def remove(self, key):
        self.entries = [entry for entry in self.entries if entry['key'] != key]

    def clear(self):
        self.entries.clear()

    def serialize(self):
        return [dict(name=entry['name'], path=entry['path']) for entry in self.entries]
