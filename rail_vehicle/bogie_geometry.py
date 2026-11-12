"""Renderer-neutral teaching geometry. X is longitudinal, Y transverse, Z up."""

from dataclasses import dataclass, field
from functools import lru_cache
from math import cos, hypot, sin, tau


AXLE_X = (-1.30, 1.30)
AXLE_Z = .42
WHEEL_Y = .88
BOX_Y = 1.20
FRAME_Y = 1.16
WHEEL_POSE_GAIN = .035  # keep the wheelset close to the reference rails during schematic motion
_PARTS = ("构架", "轮对", "一系悬挂", "二系悬挂", "轴箱", "电机与传动", "制动装置", "轨道参照")


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _scale(a, factor):
    return tuple(x * factor for x in a)


def _cross(a, b):
    x, y, z = a
    p, q, r = b
    return y*r-z*q, z*p-x*r, x*q-y*p


def _basis(direction):
    w = _scale(direction, 1 / hypot(*direction))
    u = _cross(w, (0, 0, 1) if abs(w[2]) < .9 else (1, 0, 0))
    u = _scale(u, 1 / hypot(*u))
    return u, _cross(w, u), w


@lru_cache(maxsize=8)
def _circle(count):
    return tuple((cos(tau * i / count), sin(tau * i / count)) for i in range(count))


def _explosion_offset(part, point):
    x, y, _ = point
    side = 1 if y >= 0 else -1
    end = 1 if x >= 0 else -1
    return {
        "构架": (0, 0, .59), "轮对": (end*.23, 0, 0),
        "一系悬挂": (0, side*.34, .39), "二系悬挂": (0, 0, .86),
        "轴箱": (end*.23, side*.40, .03), "电机与传动": (-end*.30, 0, .42),
        "制动装置": (end*.48, 0, .25), "轨道参照": (0, 0, 0),
    }[part]


def _identity(point):
    return point


@lru_cache(maxsize=1)
def _spring_topology():
    helix = tuple((i/72, .073*cos(4*tau*i/72), .073*sin(4*tau*i/72)) for i in range(73))
    faces = []
    for i in range(72):
        for j in range(6):
            a, b = i*6+j, i*6+(j+1)%6
            faces.extend(((a, b, b+6), (a, b+6, a+6)))
    return helix, tuple(faces)


@dataclass
class Mesh:
    vertices: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    tones: list = field(default_factory=list)

    def add(self, vertices, faces, tone, place):
        offset = len(self.vertices)
        self.vertices.extend(map(place, vertices))
        for face in faces:
            self.faces.append(tuple(index + offset for index in face))
            self.tones.append(tone)


class Assembly:
    def __init__(self, visible, frame_pose, wheel_pose, spread):
        self.visible = frozenset(visible)
        self.frame = frame_pose
        self.wheel = wheel_pose
        self.spread = spread
        self.meshes = {}
        self.markers = []

    def point(self, part, point):
        return _add(point, _scale(_explosion_offset(part, point), self.spread)) if self.spread else point

    def placement(self, part, center, pose):
        # Separate each assembly as a unit in the exploded view; don't split parts at symmetry planes.
        transform = pose or _identity
        if not self.spread:
            return transform
        shift = _scale(_explosion_offset(part, center), self.spread)
        return lambda vertex: _add(transform(vertex), shift)

    def mesh(self, part, group, center=None, pose=None):
        return self.meshes.setdefault((part, group), Mesh())

    def revolve(self, part, center, axis, profile, *, tone=0, pose=None, group=None, detail=20):
        if part not in self.visible:
            return
        u, v, w = _basis(axis)
        place = self.placement(part, center, pose)
        mesh = self.mesh(part, group, center, pose)
        rings = []
        for offset, radius, _ in profile:
            origin = _add(center, _scale(w, offset))
            vertices = [origin] if radius == 0 else [
                _add(origin, _add(_scale(u, radius*c), _scale(v, radius*s))) for c, s in _circle(detail)]
            first = len(mesh.vertices)
            mesh.vertices.extend(map(place, vertices))
            rings.append(tuple(range(first, first + len(vertices))))
        for index, (lower, upper) in enumerate(zip(rings, rings[1:])):
            start = len(mesh.faces)
            for i in range(detail):
                j = (i + 1) % detail
                if len(lower) == 1:
                    mesh.faces.append((lower[0], upper[j], upper[i]))
                elif len(upper) == 1:
                    mesh.faces.append((lower[i], lower[j], upper[0]))
                else:
                    mesh.faces.extend(((lower[i], lower[j], upper[j]), (lower[i], upper[j], upper[i])))
            strip = profile[index][2]
            mesh.tones.extend([tone if strip is None else strip] * (len(mesh.faces) - start))

    def cylinder(self, part, start, end, radius, *, tone=0, pose=None, group=None, detail=14):
        axis = _sub(end, start)
        length = hypot(*axis)
        if length == 0:
            return
        center = _add(start, _scale(axis, .5))
        self.revolve(part, center, axis, ((-length/2, 0, None), (-length/2, radius, None),
                                       (length/2, radius, None), (length/2, 0, None)),
                     tone=tone, pose=pose, group=group, detail=detail)

    def box(self, part, center, size, *, tone=0, pose=None, bevel=0):
        if part not in self.visible:
            return
        x, y, z = center
        dx, dy, dz = _scale(size, .5)
        outline = ((x-dx, z-dz), (x+dx, z-dz), (x+dx, z+dz), (x-dx, z+dz))
        if bevel:
            b = min(bevel, dx*.4, dz*.4)
            outline = ((x-dx+b,z-dz), (x+dx-b,z-dz), (x+dx,z-dz+b), (x+dx,z+dz-b),
                       (x+dx-b,z+dz), (x-dx+b,z+dz), (x-dx,z+dz-b), (x-dx,z-dz+b))
        self.beam(part, y, dy*2, outline, center=center, tone=tone, pose=pose, bevel=bevel)

    def beam(self, part, y, width, outline, *, center, tone=0, pose=None, bevel=0):
        """Extrude a convex counterclockwise X/Z outline along the transverse axis."""
        if part not in self.visible:
            return
        n = len(outline)
        if bevel:
            b = min(bevel, width*.3)
            cx, cz = (sum(axis)/n for axis in zip(*outline))
            sx = 1 - 2*b/(max(p[0] for p in outline) - min(p[0] for p in outline))
            sz = 1 - 2*b/(max(p[1] for p in outline) - min(p[1] for p in outline))
            inset = [(cx+(x-cx)*sx, cz+(z-cz)*sz) for x, z in outline]
            rings = [(-width/2, inset), (-width/2+b, outline), (width/2-b, outline), (width/2, inset)]
        else:
            rings = [(-width/2, outline), (width/2, outline)]
        vertices = [(x, y+offset, z) for offset, points in rings for x, z in points]
        end = len(vertices)-n
        faces = [(0, i, i+1) for i in range(1, n-1)] + [(end, end+i+1, end+i) for i in range(1, n-1)]
        for first in range(0, end, n):
            for i in range(n):
                j = (i+1) % n
                faces.extend(((first+i, first+j+n, first+j), (first+i, first+i+n, first+j+n)))
        self.mesh(part, None, center, pose).add(vertices, faces, tone, self.placement(part, center, pose))

    def spring(self, bottom, top):
        part = "一系悬挂"
        if part not in self.visible:
            return
        direction = _sub(top, bottom)
        u, v, _ = _basis(direction)
        helix, faces = _spring_topology()
        points = [_add(_add(bottom, _scale(direction, t)), _add(_scale(u, x), _scale(v, y)))
                  for t, x, y in helix]
        vertices = []
        for i, point in enumerate(points):
            a, b, _ = _basis(_sub(points[min(i+1, 72)], points[max(i-1, 0)]))
            vertices.extend(_add(point, _add(_scale(a, .0095*c), _scale(b, .0095*s))) for c, s in _circle(6))
        self.mesh(part, None, bottom).add(vertices, faces, 0, self.placement(part, bottom, None))


class _Template(Assembly):
    """Build immutable geometry chunks grouped by the transform applied at playback."""

    def __init__(self):
        super().__init__(_PARTS, _identity, lambda point: point, 0)

    def mesh(self, part, group, center=None, pose=None):
        parent = "frame" if pose is self.frame else "wheel" if pose is self.wheel else "fixed"
        key = part, group, parent, _explosion_offset(part, center)
        return self.meshes.setdefault(key, Mesh())

    def freeze(self):
        return tuple((key, tuple(mesh.vertices), tuple(mesh.faces), tuple(mesh.tones))
                     for key, mesh in self.meshes.items())


def _frame(a):
    part = "构架"
    for y in (-FRAME_Y, FRAME_Y):
        outline = ((-1.86, .94), (-1.61, .83), (1.61, .83), (1.86, .94),
                   (1.86, 1.04), (1.57, 1.12), (-1.57, 1.12), (-1.86, 1.04))
        a.beam(part, y, .23, outline, center=(0,y,1), pose=a.frame, bevel=.026)
        a.box(part, (0,y,1.11), (1.44,.30,.078), tone=1, pose=a.frame, bevel=.014)
        for x in (-.57,.57):
            for dy in (-.105,.105):
                a.cylinder(part, (x,y+dy,1.146), (x,y+dy,1.165), .023, tone=2, pose=a.frame, detail=6)
        for x in AXLE_X:
            a.box(part, (x,y,.845), (.34,.33,.10), tone=2, pose=a.frame, bevel=.018)
            for direction in (-1, 1):
                gusset = sorted((x+direction*.14, x+direction*.30))
                a.beam(part, y, .14, ((gusset[0],.89), (gusset[1],.91),
                                    (gusset[1],1.015), (gusset[0],1.015)),
                       center=(x,y,.95), tone=1, pose=a.frame, bevel=.012)
        side = 1 if y > 0 else -1
        cover_y = y + side*.114
        a.beam(part, cover_y, .026, ((-.52,.929), (.52,.929), (.59,.965), (.52,1.017),
                                    (-.52,1.017), (-.59,.965)),
               center=(0,cover_y,.97), tone=2, pose=a.frame, bevel=.005)
        for x in (-.45,.45):
            a.cylinder(part, (x,cover_y,.974), (x,cover_y+side*.022,.974), .016,
                       tone=1, pose=a.frame, detail=6)
    for x in (-1.70, -.48, .48, 1.70):
        end = abs(x) > 1
        a.box(part, (x,0,1.01), (.17 if end else .26,2.28,.16 if end else .25),
              tone=2 if end else 0, pose=a.frame, bevel=.021)
    for direction in (-1, 1):
        a.box(part, (direction*.71,0,.915), (.61,.34,.094), tone=2, pose=a.frame)
        for y in (-.64,.64):
            a.box(part, (direction*1.09,y,.982), (1.20,.12,.12), tone=2, pose=a.frame)
    a.box(part, (0,0,1.01), (.30,1.46,.15), tone=2, pose=a.frame)
    # Central traction mounting pad; no car body or traction rod is assumed.
    a.box(part, (0,0,1.093), (.27,.42,.041), tone=1, pose=a.frame, bevel=.009)
    a.cylinder(part, (0,0,1.11), (0,0,1.176), .086, tone=2, pose=a.frame, detail=32)
    for x in (-.10,.10):
        for y in (-.16,.16):
            a.cylinder(part, (x,y,1.111), (x,y,1.13), .016, tone=2, pose=a.frame, detail=6)


def _running_gear(a):
    wheel = "轮对"
    bearing = "轴箱"
    spring = "一系悬挂"
    brake = "制动装置"
    # Profile from the inboard face to the outboard face: flange, tread, web, hub.
    profile = ((-.113,0,2), (-.113,.24,1), (-.112,.410,0), (-.101,.456,1),
               (-.076,.456,1), (-.054,.421,0), (.077,.413,1), (.088,.402,2),
               (.088,.245,2), (.106,.203,1), (.106,.125,0), (.124,.119,0), (.124,0,0))
    for x in AXLE_X:
        a.cylinder(wheel, (x,-1.31,AXLE_Z), (x,1.31,AXLE_Z), .071, tone=2, pose=a.wheel, group=(x,0))
        for side in (-1, 1):
            y = side*WHEEL_Y
            box_y = side*BOX_Y
            a.revolve(wheel, (x,y,AXLE_Z), (0,side,0), profile, pose=a.wheel, group=(x,side), detail=48)
            a.box(bearing, (x,box_y,.455), (.29,.23,.28), pose=a.wheel, bevel=.032)
            a.box(bearing, (x,box_y,.59), (.32,.27,.057), tone=1, pose=a.wheel, bevel=.012)
            a.revolve(bearing, (x,box_y,AXLE_Z), (0,side,0),
                      ((.102,0,2), (.102,.134,2), (.143,.134,1), (.143,.096,0), (.170,.096,0), (.170,0,0)),
                      pose=a.wheel)
            for dx in (-.079,.079):
                for dz in (-.076,.076):
                    a.cylinder(bearing, (x+dx,box_y+side*.139,AXLE_Z+dz),
                               (x+dx,box_y+side*.159,AXLE_Z+dz), .019, tone=2, pose=a.wheel, detail=6)
            a.cylinder(spring, (x,box_y,.603), (x,box_y,.625), .104, tone=1, pose=a.wheel)
            a.cylinder(spring, (x,side*FRAME_Y,.826), (x,side*FRAME_Y,.852), .102, tone=1, pose=a.frame)
            disc_y = side*.64
            a.revolve(brake, (x,disc_y,AXLE_Z), (0,side,0),
                      ((-.060,0,2), (-.060,.13,2), (-.030,.13,1), (-.030,.238,0),
                       (.030,.238,1), (.030,.13,2), (.059,.13,2), (.059,0,2)), pose=a.wheel,
                      group=(x,side,"disc"), detail=40)
            caliper = (x,disc_y,.658)
            a.box(brake, caliper, (.24,.152,.103), pose=a.wheel, bevel=.018)
            a.box(brake, (x,disc_y+side*.067,.599), (.14,.032,.078), tone=2, pose=a.wheel)
            a.box(brake, (x,disc_y-side*.067,.599), (.14,.032,.078), tone=2, pose=a.wheel)
            for dx in (-.074,.074):
                a.cylinder(brake, (x+dx,disc_y,.703), (x+dx,disc_y,.722), .018,
                           tone=1, pose=a.wheel, detail=6)


def _upper_suspension_and_drive(a):
    secondary = "二系悬挂"
    drive = "电机与传动"
    for y in (-FRAME_Y,FRAME_Y):
        a.revolve(secondary, (0,y,1.14), (0,0,1),
                  ((0,0,1), (0,.225,1), (.045,.225,1), (.045,.186,2), (.076,.217,2),
                   (.11,.221,2), (.145,.192,2), (.18,.216,2), (.216,.214,2), (.252,.187,1),
                   (.252,.234,1), (.292,.234,1), (.292,0,1)), pose=a.frame, detail=22)
        a.box(secondary, (0,y,1.449), (.34,.31,.035), tone=0, pose=a.frame, bevel=.009)
        for x in (-.13,.13):
            for dy in (-.116,.116):
                a.cylinder(secondary, (x,y+dy,1.465), (x,y+dy,1.485), .019, tone=1, pose=a.frame, detail=6)
    for x in AXLE_X:
        motor_x = x*.67
        a.revolve(drive, (motor_x,0,.63), (0,1,0),
                  ((-.42,0,2), (-.42,.15,1), (-.37,.187,0), (-.31,.193,0), (.20,.193,0),
                   (.26,.161,1), (.31,.117,1), (.33,.086,2), (.33,0,2)), pose=a.frame)
        for i in range(9):
            angle = i*tau/9
            xx, zz = motor_x+.187*cos(angle), .63+.187*sin(angle)
            a.cylinder(drive, (xx,-.30,zz), (xx,.18,zz), .015, tone=2, pose=a.frame, detail=6)
        a.box(drive, (motor_x,0,.824), (.28,.31,.10), tone=2, pose=a.frame)
        casing = ((x-.18,.36), (x-.12,.293), (x+.10,.293), (x+.18,.35),
                  (x+.18,.57), (x+.10,.643), (x-.12,.643), (x-.18,.58))
        a.beam(drive, .43, .28, casing, center=(x,.43,.468), pose=a.wheel, bevel=.023)
        a.cylinder(drive, (x,.55,AXLE_Z), (x,.608,AXLE_Z), .13, tone=1, pose=a.wheel)
        for dx in (-.097,.097):
            for dz in (-.066,.066):
                a.cylinder(drive, (x+dx,.604,AXLE_Z+dz), (x+dx,.622,AXLE_Z+dz),
                           .013, tone=2, pose=a.wheel, detail=6)


def connection_anchors():
    """Illustrative joints shared by the two renderers; endpoints are wheel, then frame."""
    spring, brake, drive = "一系悬挂", "制动装置", "电机与传动"
    for x in AXLE_X:
        yield drive, "coupling", (x*.90,.38,.55), (x*.67,.29,.63)
        for side in (-1, 1):
            box_y, frame_y, disc_y = side*BOX_Y, side*FRAME_Y, side*.64
            yield spring, "coil", (x,box_y,.625), (x,frame_y,.826)
            yield spring, "damper", (x+.17,box_y,.55), (x+.25,frame_y,.88)
            yield spring, "guide", (x-.13,box_y,.48), (x-.46,frame_y,.82)
            yield brake, "stay", (x,disc_y,.693), (x*.94,disc_y,.924)


def _connections(a):
    for part, kind, wheel, frame in connection_anchors():
        if part not in a.visible:
            continue
        lower, upper = a.wheel(wheel), a.frame(frame)
        if kind == "coil":
            a.spring(lower, upper)
        elif kind == "damper":
            middle = _add(lower, _scale(_sub(upper, lower), .59))
            a.cylinder(part, lower, middle, .032, tone=2)
            a.cylinder(part, middle, upper, .019, tone=1)
        else:
            a.cylinder(part, lower, upper, .063 if kind == "coupling" else .028,
                       tone=1 if kind == "coupling" else 2)


def _wheel_markers(a, phase):
    if "轮对" not in a.visible:
        return
    c, s = cos(phase), sin(phase)
    for x in AXLE_X:
        for side in (-1, 1):
            y = side*WHEEL_Y
            outer = y + side*.113
            mark = [(x+r*c, outer, AXLE_Z+r*s) for r in (.15,.36)]
            a.markers.extend([*map(a.placement("轮对", (x,y,AXLE_Z), a.wheel), mark), None])


@lru_cache(maxsize=1)
def _rigid_template():
    a = _Template()
    _frame(a)
    _running_gear(a)
    _upper_suspension_and_drive(a)
    for y in (-WHEEL_Y,WHEEL_Y):
        a.box("轨道参照", (0,y,-.04), (4.20,.105,.064), tone=0)
        a.box("轨道参照", (0,y,-.092), (4.20,.044,.045), tone=1)
        a.box("轨道参照", (0,y,-.126), (4.20,.132,.025), tone=1)
    for x in (-1.85,-1.25,-.65,0,.65,1.25,1.85):
        a.box("轨道参照", (x,0,-.177), (.20,2.42,.078), tone=2, bevel=.009)
        for y in (-WHEEL_Y,WHEEL_Y):
            a.box("轨道参照", (x,y,-.132), (.15,.22,.032), tone=1, bevel=.006)
    return a.freeze()


@lru_cache(maxsize=1)
def _neutral_links():
    a = _Template()
    _connections(a)
    return a.freeze()


def _apply_template(a, template):
    for (part, group, parent, offset), vertices, faces, tones in template:
        if part not in a.visible:
            continue
        pose = a.frame if parent == "frame" else a.wheel if parent == "wheel" else _identity
        destination = a.mesh(part, group)
        first = len(destination.vertices)
        if a.spread and any(offset):
            shift = _scale(offset, a.spread)
            destination.vertices.extend(_add(pose(point), shift) for point in vertices)
        elif pose is _identity:
            destination.vertices.extend(vertices)
        else:
            destination.vertices.extend(map(pose, vertices))
        destination.faces.extend(((i+first, j+first, k+first) for i, j, k in faces) if first else faces)
        destination.tones.extend(tones)


def build_assembly(visible, *, phase, lateral, vertical, yaw, spread=0, rails=False):
    c, s = cos(yaw), sin(yaw)
    wc, ws = cos(WHEEL_POSE_GAIN*yaw), sin(WHEEL_POSE_GAIN*yaw)

    def frame(p):
        x, y, z = p
        return x*c-y*s, x*s+y*c+lateral, z+vertical

    def wheel(p):
        x, y, z = p
        return x*wc-y*ws, x*ws+y*wc+WHEEL_POSE_GAIN*lateral, z

    neutral = not (lateral or vertical or yaw)
    a = Assembly((*visible, "轨道参照") if rails else visible,
                 _identity if neutral else frame, _identity if not (lateral or yaw) else wheel, spread)
    _apply_template(a, _rigid_template())
    if neutral:
        _apply_template(a, _neutral_links())
    else:
        _connections(a)
    _wheel_markers(a, phase)
    return a
