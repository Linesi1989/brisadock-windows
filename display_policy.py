"""Selection and deliberate edge handoff for one shared Dock."""
MODES = ('follow_edge', 'primary', 'selected')


def screen_identity(screen):
    serial = screen.serialNumber().strip()
    if serial and serial.lower() not in ('0', 'unknown'):
        return 'serial:'+ '|'.join((screen.manufacturer(), screen.model(), serial))
    return 'name:'+screen.name()


def select_screen(mode, preferred, current, screens, primary):
    if not screens:
        return None
    fallback = primary if primary in screens else screens[0]
    if mode == 'primary':
        return fallback
    if mode == 'selected':
        return next((screen for screen in screens if screen_identity(screen) == preferred), fallback)
    return current if current in screens else fallback


def near_edge(point, area, edge):
    if not area.contains(point):
        return False
    if edge == 'bottom':
        return point.y() >= area.bottom()-3
    if edge == 'left':
        return point.x() <= area.left()+3
    return point.x() >= area.right()-3


class EdgeHandoff:
    def __init__(self, delay=.35):
        self.delay = delay
        self.reset()

    def reset(self):
        self.candidate = None
        self.since = 0.

    def poll(self, point, edge, screens, current, now, blocked=False):
        if blocked:
            self.reset()
            return None
        target = next((screen for screen in screens if screen is not current and
                       any(near_edge(point, area, edge) for area in (screen.geometry(), screen.availableGeometry()))), None)
        if target is None:
            self.reset()
            return None
        if target is not self.candidate:
            self.candidate, self.since = target, now
            return None
        if now-self.since >= self.delay:
            self.reset()
            return target
        return None
