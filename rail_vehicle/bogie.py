"""Simplified bogie geometry and illustrative, non-physical motion."""

import math

import plotly.graph_objects as go


PARTS = ("构架", "轮对", "一系悬挂", "二系悬挂", "轴箱", "电机与传动", "制动装置")
COLORS = {"构架": "#69839a", "轮对": "#b0bec5", "一系悬挂": "#39c4ce",
          "二系悬挂": "#4f9fff", "轴箱": "#eaa851", "电机与传动": "#9a83c9", "制动装置": "#e07d62"}
PART_DESCRIPTIONS = {
    "构架": "连接两组轮对与悬挂，为电机、制动等装置提供安装位置。",
    "轮对": "每组轮对由一根车轴和两个车轮组成；轮面短线用于观察转动。",
    "一系悬挂": "位于轴箱和构架之间，示意图中用螺旋弹簧表示。",
    "二系悬挂": "位于构架与车体之间；本模型仅画出构架上的安装位置。",
    "轴箱": "支承车轴端部，内部轴承的位置由橙色轴箱示意。",
    "电机与传动": "示意电机、齿轮箱及其与轮对的布置关系。",
    "制动装置": "在轮对附近示意制动部件的布置，不表示特定制动系统。",
}


def motion(mode, phase):
    """Return prescribed illustration offsets; never infer displacement from acceleration."""
    s = math.sin(phase)
    return (0.16 * s if mode in ("横向振动", "蛇行运动示意", "异常运行") else 0,
            0.12 * s if mode in ("垂向振动", "异常运行") else 0,
            0.10 * math.cos(phase) if mode == "蛇行运动示意" else 0)


def build_bogie(mode="正常运行", phase=0.0, visible=PARTS, highlight="无", view="整体视图",
                *, labels=False, height=500):
    lateral, vertical, yaw = motion(mode, phase)
    traces = []

    def transform(x, y, z):
        return (x*math.cos(yaw) - y*math.sin(yaw),
                x*math.sin(yaw) + y*math.cos(yaw) + lateral, z + vertical)

    def color(part):
        return "#ffca45" if (part == highlight if isinstance(highlight, str) else part in highlight) else COLORS[part]

    def mesh(part, points, triangles, moving=False):
        if part not in visible:
            return
        x, y, z = zip(*(transform(*p) if moving else p for p in points))
        i, j, k = zip(*triangles)
        traces.append(go.Mesh3d(x=x, y=y, z=z, i=i, j=j, k=k, color=color(part), name=part,
                               hovertemplate=part+"<extra></extra>", showlegend=False,
                               lighting=dict(ambient=.64, diffuse=.8, specular=.25)))

    def box(part, center, size, moving=False):
        cx, cy, cz = center
        dx, dy, dz = (value/2 for value in size)
        points = [(cx+x*dx, cy+y*dy, cz+z*dz) for z in (-1, 1)
                  for x, y in ((-1,-1), (1,-1), (1,1), (-1,1))]
        faces = ((0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7))
        mesh(part, points, [(a,b,c) for a,b,c,d in faces]+[(a,c,d) for a,b,c,d in faces], moving)

    def cylinder(part, x, y, z, radius, length):
        n = 20
        points = [(x+radius*math.cos(i*math.tau/n), y+side*length/2,
                   z+radius*math.sin(i*math.tau/n)) for side in (-1, 1) for i in range(n)]
        triangles = []
        for i in range(n):
            j = (i+1) % n
            triangles.extend(((i,j,j+n), (i,j+n,i+n)))
            if 1 <= i < n-1:
                triangles.extend(((0,i+1,i), (n,i+n,i+1+n)))
        mesh(part, points, triangles)

    def line(part, points, width=10):
        if part not in visible:
            return
        transformed = points
        traces.append(go.Scatter3d(x=[p[0] for p in transformed], y=[p[1] for p in transformed],
            z=[p[2] for p in transformed], mode="lines", name=part, legendgroup=part,
            showlegend=False, hovertemplate=part+"<extra></extra>",
            line=dict(color=color(part), width=width)))

    for y in (-0.9, 0.9):
        box("构架", (0,y,1.05), (3.4,.22,.24), True)
    for x in (-1.4, 1.4):
        box("构架", (x,0,1.05), (.23,1.8,.22), True)
        cylinder("轮对", x, 0, .45, .08, 2.52)
        for y in (-1.05, 1.05):
            cylinder("轮对", x, y, .45, .45, .14)
            outer = y + math.copysign(.073, y)
            line("轮对", [(x,outer,.45),(x+.39*math.cos(phase),outer,.45+.39*math.sin(phase))], 5)
            box("轴箱", (x,y*1.23,.51), (.24,.18,.22))
            top = transform(x, y/1.17, .96)
            spring = [(x+(top[0]-x)*t+.08*math.cos(10*math.tau*t),
                       y*1.23+(top[1]-y*1.23)*t+.08*math.sin(10*math.tau*t),
                       .62+(top[2]-.62)*t) for t in (i/100 for i in range(101))]
            line("一系悬挂", spring, 5)
            box("制动装置", (x-.49,y,.48), (.10,.16,.28))
    for y in (-0.65,0.65):
        box("二系悬挂", (0,y,1.25), (.38,.36,.20), True)
    for x in (-1.0, 1.0):
        cylinder("电机与传动", x, 0, .57, .19, .53)
        box("电机与传动", (x*1.27,.19,.53), (.3,.22,.24))
    if labels:
        anchors = {
            "构架": transform(-.55, -.9, 1.25), "轮对": (1.54, 1.14, .30),
            "一系悬挂": (1.4, -1.29, .86), "二系悬挂": transform(.15, .65, 1.46),
            "轴箱": (-1.4, 1.4, .61), "电机与传动": (-1.0, 0, .85),
            "制动装置": (-1.89, -1.10, .70),
        }
        shown = [(part, anchors[part]) for part in PARTS if part in visible]
        if shown:
            names, points = zip(*shown)
            x, y, z = zip(*points)
            traces.append(go.Scatter3d(x=x, y=y, z=z, mode="markers+text", text=names,
                                      textposition="top center", marker=dict(size=3, color="#f5b65a"),
                                      hoverinfo="skip", showlegend=False, name="构件标注"))
    cameras = {"整体视图": dict(x=2.0,y=2.1,z=1.4), "侧视图": dict(x=0,y=2.7,z=0),
               "正视图": dict(x=2.7,y=0,z=0), "俯视图": dict(x=0,y=0,z=2.7)}
    fig = go.Figure(data=traces)
    fig.update_layout(scene=dict(aspectmode="data", camera=dict(eye=cameras[view],
        projection=dict(type="perspective" if view == "整体视图" else "orthographic"),
        up=dict(x=0,y=1,z=0) if view == "俯视图" else dict(x=0,y=0,z=1)), uirevision=view,
        xaxis=dict(visible=False,range=[-2,2]), yaxis=dict(visible=False,range=[-1.7,1.7]),
        zaxis=dict(visible=False,range=[0,1.8])), height=height, margin=dict(l=0,r=0,t=0,b=0),
        paper_bgcolor="rgba(0,0,0,0)")
    return fig
