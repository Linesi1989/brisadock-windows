"""A two-stage curved contraction, independent of desktop/window APIs."""


def smooth(value):
    value = max(0., min(1., value))
    return value*value*(3-2*value)


def canonical(rect, edge):
    x, y, width, height = rect
    if edge == 'right':
        return y, x, height, width
    if edge == 'left':
        return y, -x-width, height, width
    return x, y, width, height


def actual(rect, edge):
    x, y, width, height = rect
    if edge == 'right':
        return y, x, height, width
    if edge == 'left':
        return -y-height, x, height, width
    return rect


def sheet_bands(origin, target, progress, edge='bottom', count=32):
    """Return (fraction0, fraction1, destination rectangle) for each strip.

    First the near edge narrows and stretches toward the dock; then the far
    edge contracts. Reversing progress performs restoration along the same path.
    """
    ox, oy, ow, oh = canonical(origin, edge)
    tx, ty, tw, th = canonical(target, edge)
    progress = max(0., min(1., progress))
    pull = smooth(progress/.45)
    collapse = smooth((progress-.45)/.55)
    bottom = (oy+oh)+(ty+th-oy-oh)*pull
    top = oy+(ty-oy)*collapse
    result = []
    for index in range(count):
        v0, v1 = index/count, (index+1)/count
        middle = (v0+v1)/2
        bend = pull*middle*middle
        width = (ow+(tw-ow)*bend)*(1-collapse)+tw*collapse
        center = (ox+ow/2+(tx+tw/2-ox-ow/2)*bend)*(1-collapse)+(tx+tw/2)*collapse
        y0, y1 = top+(bottom-top)*v0, top+(bottom-top)*v1
        result.append((v0, v1, actual((center-width/2, y0, width, y1-y0), edge)))
    return result


def sheet_mesh(origin, target, progress, edge='bottom', count=64, overlap=0.):
    """Pinch first; feed the image through a fixed screen-space neck next."""
    ox, oy, ow, oh = canonical(origin, edge)
    tx, ty, tw, th = canonical(target, edge)
    progress = max(0., min(1., progress))
    pull = smooth(progress/.36)
    feed = smooth((progress-.36)/.64)
    bottom = oy+oh+(ty+th-oy-oh)*pull
    top = oy+(ty-oy)*feed
    neck_length = max(1., ty-oy)
    source_to_neck = max(1., ty+th-oy)/neck_length
    def point(v):
        y = top+(bottom-top)*v
        position = (y-oy)/neck_length if progress >= .36 else v*source_to_neck
        bend = pull*smooth(position)
        width = ow+(tw-ow)*bend
        center = ox+ow/2+(tx+tw/2-ox-ow/2)*bend
        return center-width/2, center+width/2, y
    def convert(x, y):
        return (y, x) if edge == 'right' else ((-y, x) if edge == 'left' else (x, y))
    result = []
    for index in range(count):
        v0, v1 = max(0., index/count-overlap), min(1., (index+1)/count+overlap)
        left0, right0, y0 = point(v0); left1, right1, y1 = point(v1)
        points = [convert(left0,y0),convert(right0,y0),convert(right1,y1),convert(left1,y1)]
        if edge == 'right': points = [points[i] for i in (0,3,2,1)]
        elif edge == 'left': points = [points[i] for i in (3,0,1,2)]
        result.append((v0,v1,points))
    return result
