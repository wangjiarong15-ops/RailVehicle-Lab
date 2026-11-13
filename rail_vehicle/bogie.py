"""Plotly renderer and illustrative, non-physical bogie motion."""

import plotly.graph_objects as go

from rail_vehicle.bogie_geometry import build_assembly, motion, motion_envelope


PARTS = ("构架", "轮对", "一系悬挂", "二系悬挂", "轴箱", "电机与传动", "制动装置")
VIEWS = ("整体视图", "侧视图", "正视图", "俯视图", "底视图")
COLORS = {"构架": "#69839a", "轮对": "#b0bec5", "一系悬挂": "#39c4ce",
          "二系悬挂": "#4f9fff", "轴箱": "#eaa851", "电机与传动": "#9a83c9", "制动装置": "#e07d62"}
_PALETTE = {
    "构架": (COLORS["构架"], "#91a9ba", "#49637a"),
    "轮对": (COLORS["轮对"], "#e0e9ef", "#64798a"),
    "一系悬挂": (COLORS["一系悬挂"], "#a5e5e5", "#377484"),
    "二系悬挂": (COLORS["二系悬挂"], "#7799b7", "#263e57"),
    "轴箱": (COLORS["轴箱"], "#ffcf89", "#8b6b50"),
    "电机与传动": (COLORS["电机与传动"], "#c1b0ed", "#625578"),
    "制动装置": (COLORS["制动装置"], "#a8b9c5", "#8a5551"),
    "轨道参照": ("#718194", "#45586c", "#37475b"),
}
PART_DESCRIPTIONS = {
    "构架": "两侧梁和横梁构成承载框架，托座为悬挂、电机和制动装置提供连接位置。",
    "轮对": "两根车轴各带两个车轮；可分辨踏面、内侧轮缘、轮辐板与轮毂。外侧短线用来观察转动。",
    "一系悬挂": "轴箱上方的螺旋弹簧和旁边的减振器，连接轮对侧与构架侧。",
    "二系悬挂": "构架两侧的空气弹簧示意，包含胶囊、上下支承板和车体侧安装位置；未绘制车体。",
    "轴箱": "车轮外侧的轴箱支承车轴端部；可见外侧轴承端盖和紧固点，内部滚动体未绘制。",
    "电机与传动": "每根车轴附近可看到构架侧电机、连接段和车轴侧齿轮箱。外形只用于展示布置关系。",
    "制动装置": "轮对内侧示意轴装制动盘；盘缘上方为卡钳及其支架。不代表特定制动系统。",
}


def build_bogie(mode="正常运行", phase=0.0, visible=PARTS, highlight="无", view="整体视图",
                *, labels=False, height=500, exploded=False, spread=1.0, rails=False, focus=None,
                camera_revision=0):
    lateral, vertical, yaw = (0, 0, 0) if exploded else motion(mode, phase)
    assembly = build_assembly(visible, phase=0 if exploded else phase, lateral=lateral,
                              vertical=vertical, yaw=yaw, spread=spread if exploded else 0,
                              rails=rails and not exploded and view != "底视图")
    highlighted = {highlight} if isinstance(highlight, str) else set(highlight)
    traces = []
    for (part, group), mesh in assembly.meshes.items():
        x, y, z = zip(*mesh.vertices)
        i, j, k = zip(*mesh.faces)
        if part in highlighted:
            palette = ("#ffca45", "#ffe19b", "#c88721")
        elif part == focus:
            palette = ("#36d9ed", "#a3f5fa", "#167a9c")
        else:
            palette = _PALETTE[part]
        traces.append(go.Mesh3d(x=x, y=y, z=z, i=i, j=j, k=k, name=part,
                               color=palette[0], facecolor=[palette[tone] for tone in mesh.tones],
                               uid=f"{part}-{group}", legendgroup=part, showlegend=False,
                               hovertemplate=part+"<extra></extra>",
                               lighting=dict(ambient=.70, diffuse=.77, specular=.34, roughness=.56),
                               lightposition=dict(x=2.0, y=3.1, z=4.2)))
    if assembly.markers:
        x, y, z = zip(*(point if point is not None else (None,None,None) for point in assembly.markers))
        marker_color = "#ffca45" if "轮对" in highlighted else "#4ce3de" if focus == "轮对" else "#6a9ea7"
        traces.append(go.Scatter3d(x=x, y=y, z=z, mode="lines", name="轮对", uid="wheel-marks",
                                  legendgroup="轮对", showlegend=False, hoverinfo="skip",
                                  line=dict(color=marker_color, width=5)))
    if labels:
        anchors = {
            "构架": assembly.frame((-.62, -1.18, 1.18)), "轮对": assembly.wheel((1.56, .99, .25)),
            "一系悬挂": (1.30, -1.29, .74+vertical*.5), "二系悬挂": assembly.frame((.04, 1.17, 1.50)),
            "轴箱": assembly.wheel((-1.30, 1.39, .54)), "电机与传动": assembly.frame((-.91, -.18, .82)),
            "制动装置": assembly.wheel((-1.30, -.65, .72)),
        }
        shown = [(part, assembly.point(part, anchors[part])) for part in PARTS if part in assembly.visible]
        if shown:
            names, points = zip(*shown)
            x, y, z = zip(*points)
            traces.append(go.Scatter3d(x=x, y=y, z=z, mode="markers+text", text=names,
                                      textposition="top center", marker=dict(size=3, color="#f5b65a"),
                                      hoverinfo="skip", showlegend=False, name="构件标注", uid="component-labels"))
    cameras = {"整体视图": dict(x=1.68, y=2.22, z=1.55), "侧视图": dict(x=0, y=2.7, z=0),
               "正视图": dict(x=2.7, y=0, z=0), "俯视图": dict(x=0, y=0, z=2.7),
               "底视图": dict(x=0, y=0, z=-2.7)}
    horizontal = view in ("俯视图", "底视图")
    fig = go.Figure(data=traces)
    fig.update_layout(scene=dict(aspectmode="data", camera=dict(eye=cameras[view],
        projection=dict(type="perspective" if view == "整体视图" else "orthographic"),
        up=dict(x=0, y=1, z=0) if horizontal else dict(x=0, y=0, z=1)),
        uirevision=f"{view}:{exploded}:{camera_revision}",
        xaxis=dict(visible=False, range=[-2.26,2.26] if exploded else [-2.17,2.17]),
        yaxis=dict(visible=False, range=[-2.10,2.10] if exploded else [-1.86,1.86]),
        zaxis=dict(visible=False, range=[-.23,2.85 if exploded else 1.66])),
        height=height, margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor="rgba(0,0,0,0)")
    return fig
