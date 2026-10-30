"""Native UI design system; presentation only, independent of analysis services."""

from datetime import datetime, timezone
import hashlib
import json

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

COLORS = ["#176BCE", "#0D9488", "#8258B5", "#E39A24", "#D34444", "#526E8B"]
pio.templates["rail"] = go.layout.Template(layout=dict(
    colorway=COLORS, paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
    font=dict(family="Microsoft YaHei, sans-serif", color="#142D4E", size=12),
    xaxis=dict(gridcolor="#E8EFF7", zerolinecolor="#CBD8E6"),
    yaxis=dict(gridcolor="#E8EFF7", zerolinecolor="#CBD8E6"),
    hovermode="closest", hoverlabel=dict(bgcolor="#FFFFFF"),
))


def plot_chart(figure, **kwargs):
    figure.update_layout(template="rail")
    return st.plotly_chart(figure, theme=None, config={"displaylogo": False}, **kwargs)


def navigate(page, **settings):
    st.session_state.update(settings)
    st.session_state["navigation_page"] = page


def record_analysis(kind, selection, count, *, fictional=False):
    """Session-only real execution record. Never write analysis history to SQLite."""
    identity = hashlib.sha256(json.dumps([kind, selection], sort_keys=True, default=str).encode()).hexdigest()
    records = st.session_state.get("recent_analysis_records", [])
    if any(row["fingerprint"] == identity for row in records):
        return
    row = dict(fingerprint=identity, 分析类型=kind, 数据点数=count,
               来源="虚构演示" if fictional else "历史数据",
               完成时间=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    st.session_state["recent_analysis_records"] = [row, *records][:8]


GROUPS = (
    ("总览", (("平台首页", "▦"),)),
    ("数据工作台", (("车辆参数", "▤"), ("CSV 数据导入", "↥"), ("运行分析", "∿"))),
    ("运行与智能分析", (("动态运行驾驶舱", "◉"), ("机器学习异常检测", "◇"),
                        ("振动与平稳性分析", "≋"), ("多车辆运行对比", "⇄"))),
    ("演示与实验", (("统计与异常演示", "⊞"), ("演示中心", "▷"))),
)


def render_navigation(public):
    private = {"车辆参数", "CSV 数据导入", "运行分析"}
    allowed = [name for _, entries in GROUPS for name, _ in entries if not public or name not in private]
    if st.session_state.get("navigation_page") not in allowed:
        st.session_state["navigation_page"] = "演示中心" if public else "平台首页"
    selected = st.session_state["navigation_page"]
    with st.sidebar:
        st.markdown("### 🚆 RailVehicle Lab")
        st.caption("运行与动力学分析工作台")
        st.divider()
        for group, entries in GROUPS:
            visible = [(name, icon) for name, icon in entries if name in allowed]
            if not visible:
                continue
            st.caption(group)
            for name, icon in visible:
                st.button(f"{icon}  {name}", key=f"nav_{name}", width="stretch",
                          type="primary" if selected == name else "tertiary",
                          on_click=navigate, args=(name,))
        st.divider()
        st.caption("● 公开虚构数据演示" if public else "● 本地 SQLite 工作空间")
        st.caption("统计异常 ≠ 故障或安全结论")
    return selected
