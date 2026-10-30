"""Native UI design system; presentation only, independent of analysis services."""

from datetime import datetime, timezone
import hashlib
import json
from html import escape
from urllib.parse import urlencode

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

COLORS = ["#176BCE", "#0D9488", "#8258B5", "#E39A24", "#D34444", "#526E8B"]
pio.templates["rail"] = go.layout.Template(layout=dict(
    font=dict(family="Microsoft YaHei, sans-serif", size=12), hovermode="closest",
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    colorway=["#3B93EE", "#139F93", "#A280DB", "#D99431", "#E35F72"],
))


def plot_chart(figure, **kwargs):
    figure.update_layout(template="rail")
    return st.plotly_chart(figure, theme="streamlit", config={"displaylogo": False}, **kwargs)


def apply_design():
    # Two stable layout test IDs for density; all other rules target our own classes.
    st.html("""<style>
    [data-testid="stMainBlockContainer"] {padding-top:3.8rem;padding-bottom:1.4rem;}
    [data-testid="stSidebarUserContent"] {padding-top:1.2rem;}
    .rv-status {display:inline-block;padding:.2rem .6rem;border:1px solid var(--rv-accent);
      border-radius:6px;color:inherit;font-size:.8rem;}
    .rv-themes {display:flex;gap:.5rem;}
    .rv-themes a {padding:.35rem .7rem;border:1px solid var(--rv-accent);border-radius:6px;
      color:inherit;text-decoration:none;}
    [class*="st-key-rv-kpi-"], [class*="st-key-rv-panel-"] {background:var(--rv-surface);}
    </style>""")


def initialize_theme():
    """Run after page rendering, avoiding navigation during lazy library imports."""
    # Use documented embed_options theme URLs, not Streamlit's private storage.
    # Only our presentation preference is read/written; no application data.
    st.html("""<script>(()=>{
    const u=new URL(window.location.href);
    const modes=u.searchParams.getAll('embed_options');
    const explicit=modes.includes('light_theme')?'light':modes.includes('dark_theme')?'dark':null;
    let saved=null;try{saved=localStorage.getItem('rail_vehicle_theme');}catch(e){}
    const theme=explicit||(saved==='light'?'light':'dark');
    document.documentElement.style.setProperty('--rv-surface',theme==='dark'?'#132338':'#FFFFFF');
    document.documentElement.style.setProperty('--rv-accent',theme==='dark'?'#4D9DFF':'#176BCE');
    try{localStorage.setItem('rail_vehicle_theme',theme);}catch(e){}
    if(!explicit){u.searchParams.append('embed_options',theme+'_theme');window.location.replace(u.href);}
    })();</script>""", unsafe_allow_javascript=True)


def theme_url(mode, page):
    if mode not in {"light", "dark"}:
        raise ValueError("Invalid presentation theme")
    return "?" + urlencode({"embed_options": mode+"_theme", "rail_page": page})


def page_title(title, subtitle="", *, primary=False):
    (st.title if primary else st.header)(title)
    if subtitle:
        st.caption(subtitle)


def kpi(column, label, value, detail):
    with column.container(border=True, key="rv-kpi-"+label):
        st.metric(label, value)
        st.caption(detail)


def status_label(text):
    st.html(f'<span class="rv-status">{escape(text)}</span>')


def empty_state(text):
    st.info(text)


def chart_container(key=None):
    return st.container(border=True, key=key)


def data_table(rows, **kwargs):
    st.dataframe(rows, hide_index=True, width="stretch", **kwargs)


def action_button(label, **kwargs):
    return st.button(label, **kwargs)


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
        linked = st.query_params.get("rail_page")
        st.session_state["navigation_page"] = linked if linked in allowed else "演示中心" if public else "平台首页"
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
        st.caption("工作空间外观")
        st.html('<div class="rv-themes">' + ''.join(
            f'<a target="_self" href="{escape(theme_url(mode, selected), quote=True)}">{label}</a>'
            for mode, label in (("dark", "◐ 深色"), ("light", "◑ 浅色"))) + '</div>')
        st.caption("偏好保存在当前浏览器。切换会重载当前页面，未持久化的实验/AI 结果需重新生成；数据库数据不受影响。")
    return selected
