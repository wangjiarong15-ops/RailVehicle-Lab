"""Run with Blender --background --factory-startup --python this_file -- --output PATH."""

import argparse
from collections import defaultdict
from math import cos, sin, tau
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rail_vehicle.bogie_geometry import AXLE_X, AXLE_Z, WHEEL_Y, Assembly, connection_anchors
from rail_vehicle.bogie_model_spec import DIMENSIONS, model_contract

DEFAULT_OUTPUT = ROOT / "rail_vehicle/assets/bogie_viewer/bogie.glb"

# Linear RGB for Blender's Principled BSDF. The existing workbench supplies the lights.
SURFACES = {
    "paint": ((.044, .077, .112), .34, .36),
    "paint_edge": ((.075, .125, .164), .38, .37),
    "steel": ((.18, .21, .23), .87, .29),
    "polished": ((.35, .40, .43), .91, .24),
    "cast": ((.102, .128, .141), .64, .43),
    "rubber": ((.023, .028, .032), .04, .62),
    "bronze": ((.23, .123, .066), .65, .39),
    "motor": ((.073, .137, .139), .48, .42),
    "sleeper": ((.071, .080, .087), .03, .84),
    "marker": ((.62, .46, .18), .36, .39),
}
NAMES = list(SURFACES)


class Part:
    """Reuse the profile and bevel mesh builders, then finish normals in Blender."""

    def __init__(self, node):
        self.node = node
        self.mesh = Assembly(["part"], None, None, 0)

    def box(self, center, size, material, bevel=.008):
        self.mesh.box("part", center, size, tone=NAMES.index(material), bevel=bevel)

    def outline(self, y, width, outline, material, bevel=.014):
        self.mesh.beam("part", y, width, outline, center=(0, y, 0),
                       tone=NAMES.index(material), bevel=bevel)

    def cylinder(self, a, b, radius, material, sides=24):
        self.mesh.cylinder("part", a, b, radius, tone=NAMES.index(material), detail=sides)

    def profile(self, center, axis, points, sides=48):
        profile = [(distance, radius, NAMES.index(material)) for distance, radius, material in points]
        self.mesh.revolve("part", center, axis, profile, detail=sides)

    def coil(self, length):
        steps, radial = 76, 8
        def centre(i):
            t = i / steps
            return Vector((.073*cos(4*tau*t), t*length, .073*sin(4*tau*t)))
        vertices, faces = [], []
        for i in range(steps+1):
            p = centre(i)
            tangent = (centre(min(steps, i+1))-centre(max(0, i-1))).normalized()
            normal = Vector((p.x, 0, p.z)).normalized()
            binormal = tangent.cross(normal).normalized()
            for j in range(radial):
                angle = j*tau/radial
                vertices.append(tuple(p + .0095*(normal*cos(angle) + binormal*sin(angle))))
                if i:
                    a, b = (i-1)*radial+j, (i-1)*radial+(j+1)%radial
                    faces.extend(((a, b, b+radial), (a, b+radial, a+radial)))
        self.mesh.mesh("part", None).add(vertices, faces, NAMES.index("polished"), lambda v: v)

    def finish(self, materials, collection):
        vertices, faces, tones = [], [], []
        for mesh in self.mesh.meshes.values():
            start = len(vertices)
            vertices.extend(mesh.vertices)
            faces.extend(tuple(start+i for i in face) for face in mesh.faces)
            tones.extend(mesh.tones)
        if not faces:
            raise ValueError(f"Empty assembly part: {self.node.name}")
        mesh = bpy.data.meshes.new(self.node.name + "_mesh")
        mesh.from_pydata(vertices, [], faces, shade_flat=False)
        mesh.update()
        used = {tone: index for index, tone in enumerate(sorted(set(tones)))}
        for tone in used:
            mesh.materials.append(materials[tone])
        for polygon, tone in zip(mesh.polygons, tones):
            polygon.material_index = used[tone]
        # Blender 4.5 stores crease decisions on mesh edges for the exporter.
        mesh.set_sharp_from_angle(angle=.66)
        obj = bpy.data.objects.new(self.node.name + "_surface", mesh)
        collection.objects.link(obj)
        obj.parent = self.node


def side_beam(p):
    w = DIMENSIONS["side_beam_width"]
    half = DIMENSIONS["frame_length"]/2
    p.outline(0, w, ((-half,-.033),(-1.57,-.122),(1.57,-.122),(half,-.033),
                     (half,.070),(1.54,.144),(-1.54,.144),(-half,.070)), "paint", .023)
    p.box((0,0,.134), (1.50,.32,.064), "paint_edge", .013)
    for side in (-1, 1):
        p.outline(side*.118, .026, ((-.56,-.060),(.56,-.060),(.65,-.021),(.56,.025),
                                   (-.56,.025),(-.65,-.021)), "cast", .005)
        for x in (-.49,.49):
            p.cylinder((x,side*.124,-.018),(x,side*.14,-.018),.017,"steel",6)
    for x in AXLE_X:
        p.box((x,0,-.138), (.35,.325,.095), "paint_edge", .016)
        for sign in (-1,1):
            a,b = sorted((x+sign*.14,x+sign*.34))
            p.outline(0,.15,((a,-.107),(b,-.075),(b,.012),(a,.012)), "paint_edge", .009)
    for x in (-.61,.61):
        for y in (-.12,.12):
            p.cylinder((x,y,.162),(x,y,.182),.020,"cast",6)


def cross_members(p):
    for x in (-.45,.45):
        p.box((x,0,0),(.285,2.14,.238),"paint",.025)
        p.box((x,0,.10),(.33,1.65,.045),"paint_edge",.010)
    for x in (-1.69,1.69):
        p.box((x,0,-.004),(.167,2.19,.148),"paint",.021)
    for y in (-.69,.69):
        p.box((0,y,-.032),(1.13,.17,.163),"paint_edge",.013)
    p.box((0,0,-.038),(.33,1.40,.14),"paint",.016)
    p.box((0,0,.054),(.29,.43,.058),"paint_edge",.012)
    p.cylinder((0,0,.077),(0,0,.148),.103,"cast",32)
    for x in (-.095,.095):
        for y in (-.165,.165):
            p.cylinder((x,y,.080),(x,y,.101),.019,"steel",6)


def wheel(p, side):
    tread, flange = DIMENSIONS["tread_radius"], DIMENSIONS["flange_radius"]
    p.profile((0,0,0),(0,side,0),[
        (-.122,0,"steel"),(-.122,.137,"cast"),(-.104,.220,"cast"),
        (-.103,.391,"steel"),(-.105,.423,"polished"),(-.097,.449,"polished"),
        (-.085,flange,"polished"),(-.071,.447,"polished"),(-.058,tread,"polished"),
        (.074,.413,"polished"),(.094,.402,"steel"),(.098,.381,"steel"),
        (.077,.361,"cast"),(.062,.231,"cast"),(.077,.180,"steel"),
        (.120,.133,"polished"),(.126,.113,"cast"),(.126,0,"cast"),
    ], sides=72)
    p.box((.263, side*.071,.010),(.066,.008,.012),"marker",.002)


def axle(p):
    points = [(-1.35,0),(-1.35,.067),(-1.18,.071),(-.97,.088),(-.76,.085),
              (-.56,.073),(-.50,.078),(.50,.078),(.56,.073),(.76,.085),
              (.97,.088),(1.18,.071),(1.35,.067),(1.35,0)]
    p.profile((0,0,0),(0,1,0),[(y,r,"steel" if abs(y)>.7 else "cast") for y,r in points],40)


def axlebox(p, side):
    p.box((0,0,.021),(.305,.241,.274),"cast",.033)
    p.box((0,0,.165),(.345,.265,.059),"paint_edge",.012)
    p.profile((0,0,0),(0,side,0),[(.108,0,"steel"),(.108,.131,"steel"),
              (.125,.146,"steel"),(.146,.144,"cast"),(.153,.118,"polished"),
              (.166,.094,"cast"),(.166,0,"cast")])
    for i in range(6):
        angle = i*tau/6
        x,z = .122*cos(angle),.122*sin(angle)
        p.cylinder((x,side*.142,z),(x,side*.164,z),.013,"steel",6)


def air_spring(p):
    p.profile((0,0,0),(0,0,1),[(0,0,"steel"),(0,.215,"steel"),(.029,.225,"cast"),
              (.043,.195,"rubber"),(.053,.209,"rubber"),(.075,.224,"rubber"),(.103,.224,"rubber"),
              (.126,.200,"rubber"),(.150,.192,"rubber"),(.175,.217,"rubber"),(.207,.222,"rubber"),
              (.229,.204,"rubber"),(.247,.186,"steel"),(.248,.224,"steel"),(.273,.230,"steel"),
              (.289,.226,"steel"),(.289,0,"steel")])
    p.box((0,0,.297),(.34,.33,.033),"paint_edge",.015)
    for x in (-.134,.134):
        for y in (-.119,.119):
            p.cylinder((x,y,.306),(x,y,.324),.018,"steel",6)


def motor(p):
    p.profile((0,0,0),(0,1,0),[(-.428,0,"cast"),(-.428,.138,"cast"),
              (-.395,.168,"steel"),(-.357,.190,"motor"),(.185,.191,"motor"),
              (.237,.172,"steel"),(.275,.122,"cast"),(.320,.082,"steel"),(.320,0,"steel")])
    for index in range(12):
        angle = tau*index/12
        x,z = .186*cos(angle),.186*sin(angle)
        p.cylinder((x,-.30,z),(x,.16,z),.016,"cast",8)
    p.box((0,0,.201),(.28,.34,.093),"paint_edge",.011)
    p.box((.12,-.19,.130),(.142,.18,.108),"motor",.012)


def gearbox(p):
    p.outline(0,.255,((-.174,-.102),(-.103,-.174),(.109,-.171),(.180,-.095),
                      (.166,.103),(.077,.159),(-.091,.148),(-.182,.061)),"motor",.026)
    p.cylinder((0,.109,AXLE_Z-.47),(0,.165,AXLE_Z-.47),.142,"cast",40)
    p.cylinder((0,.162,AXLE_Z-.47),(0,.179,AXLE_Z-.47),.093,"steel",32)
    for i in range(8):
        angle = i*tau/8
        x,z = .116*cos(angle),AXLE_Z-.47+.116*sin(angle)
        p.cylinder((x,.163,z),(x,.183,z),.013,"steel",6)


def disc(p):
    p.profile((0,0,0),(0,1,0),[(-.052,0,"cast"),(-.052,.115,"cast"),(-.028,.126,"bronze"),
              (-.028,.228,"steel"),(-.023,.241,"steel"),(.023,.241,"steel"),(.028,.229,"steel"),
              (.028,.127,"bronze"),(.052,.115,"cast"),(.052,0,"cast")])


def caliper(p):
    p.box((0,0,.014),(.245,.144,.087),"bronze",.015)
    for side in (-1,1):
        p.box((0,side*.068,-.038),(.172,.046,.105),"cast",.012)
    for x in (-.078,.078):
        p.cylinder((x,0,.052),(x,0,.076),.019,"steel",6)


def track(p):
    for y in (-WHEEL_Y,WHEEL_Y):
        p.box((0,y,-.043),(4.20,.106,.070),"steel",.006)
        p.box((0,y,-.094),(4.20,.052,.052),"cast",.005)
        p.box((0,y,-.128),(4.20,.135,.028),"cast",.004)
    for x in (-1.85,-1.25,-.65,0,.65,1.25,1.85):
        p.box((x,0,-.179),(.20,2.42,.077),"sleeper",.009)
        for y in (-WHEEL_Y,WHEEL_Y):
            p.box((x,y,-.132),(.158,.205,.030),"cast",.004)


def link(p, anchors, kind):
    _, _, start, end = anchors
    axis = Vector(end) - Vector(start)
    length = axis.length
    p.node.rotation_mode = "QUATERNION"
    p.node.rotation_quaternion = Vector((0,1,0)).rotation_difference(axis.normalized())
    if kind == "coil":
        p.coil(length)
    elif kind == "damper":
        p.cylinder((0,0,0),(0,.58*length,0),.034,"cast")
        p.cylinder((0,.56*length,0),(0,length,0),.019,"polished")
    else:
        radius = {"coupling": .062, "guide": .031, "stay": .026}[kind]
        p.cylinder((0,0,0),(0,length,0),radius,"cast")
        for y in (0,length):
            p.cylinder((-.046,y,0),(.046,y,0),radius*1.22,"steel",16)


def create_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    collection = bpy.data.collections.new("RailVehicleLab_Bogie")
    scene.collection.children.link(collection)
    contract = model_contract()
    material_objects = []
    for name, (rgb, metallic, roughness) in SURFACES.items():
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        material.diffuse_color = (*rgb,1)
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (*rgb,1)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        material_objects.append(material)

    def empty(name, parent=None, origin=(0,0,0)):
        obj = bpy.data.objects.new(name, None)
        collection.objects.link(obj)
        obj.parent = parent
        obj.location = origin
        obj.empty_display_size = .09
        return obj

    root = empty("rail_bogie")
    root["rail_contract"] = contract["version"]
    root["rail_units"] = "m"
    layers = {layer: empty(f"rail_{layer}",root) for layer in contract["layers"]}
    anchors = tuple(connection_anchors())
    factories = {"side_beam":side_beam,"cross_members":cross_members,"axle":axle,"air_spring":air_spring,
                 "motor":motor,"gearbox":gearbox,"disc":disc,"caliper":caliper,"reference_track":track}
    for name, spec in contract["parts"].items():
        node = empty(name, layers[spec["parent"]], spec["origin"])
        node["rail_part"] = name
        node["rail_group"] = spec["group"]
        p = Part(node)
        side = -1 if name.endswith("_l") else 1
        if "link" in spec:
            link(p, anchors[spec["link"]], name.split("_")[0])
        elif name.startswith("spring_seat"):
            upper = name.startswith("spring_seat_upper")
            z0,z1 = (0,.028) if upper else (-.026,0)
            p.cylinder((0,0,z0),(0,0,z1),.106,"steel",32)
        elif name.startswith(("wheel_", "axlebox_")):
            (wheel if name.startswith("wheel_") else axlebox)(p,side)
        else:
            key = next(key for key in factories if name == key or name.startswith(key+"_"))
            factories[key](p)
        p.finish(material_objects, collection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--blend", type=Path, help="Optional editable .blend project")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else [])
    create_scene()
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.blend.resolve()))
    result = bpy.ops.export_scene.gltf(filepath=str(args.output.resolve()), export_format="GLB",
                                       export_yup=True, export_extras=True, export_animations=False,
                                       export_texcoords=False, export_normals=True)
    if result != {"FINISHED"}:
        raise RuntimeError(f"GLB export failed: {result}")


if __name__ == "__main__":
    main()
