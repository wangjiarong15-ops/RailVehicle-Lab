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
THEME_TOKENS = {
    "dark": {"surface": "#101F31", "accent": "#65ADFF", "muted": "#A8BAD0", "line": "#273B51"},
    "light": {"surface": "#FFFFFF", "accent": "#176BCE", "muted": "#455D78", "line": "#D7E2EF"},
}
pio.templates["rail"] = go.layout.Template(layout=dict(
    font=dict(family="Microsoft YaHei, sans-serif", size=12), hovermode="closest",
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    colorway=["#3B93EE", "#139F93", "#A280DB", "#D99431", "#E35F72"],
))


def plot_chart(figure, *, config=None, **kwargs):
    figure.update_layout(template="rail")
    chart_config = {"displaylogo": False, **(config or {})}
    return st.plotly_chart(figure, theme="streamlit", config=chart_config, **kwargs)


def apply_design():
    # Scope styling to our keyed components and a few stable native test IDs.
    st.html("""<style>
    /* Presentation-only visual refresh: native Streamlit behavior unchanged. */
    [data-testid="stMainBlockContainer"] {padding-top:4.15rem;padding-bottom:3rem;max-width:1680px;}
    [data-testid="stSidebarUserContent"] {padding-top:.35rem;}
    [data-testid="stHeading"] h1 {letter-spacing:-.035em;font-weight:700;padding-top:.2rem;padding-bottom:.6rem;}
    [data-testid="stHeading"] h2 {letter-spacing:-.02em;}
    .rv-eyebrow {font-size:.72rem;letter-spacing:.16em;font-weight:650;
      color:var(--rv-accent,#65ADFF);margin-bottom:.2rem;}
    .rv-legend {display:flex;flex-wrap:wrap;gap:.5rem 1.2rem;font-size:.76rem;
      color:var(--rv-muted,#A8BAD0);padding:.35rem 0;}
    .rv-legend span {display:inline-flex;align-items:center;gap:.4rem;}
    .rv-legend b {color:#E76F80;font-weight:600;}
    .rv-legend .rv-threshold {color:var(--rv-accent,#65ADFF);}
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
      color:var(--rv-muted,#B4C5DA);opacity:1;}
    .rv-brand {display:flex;align-items:center;gap:.7rem;padding:.75rem .6rem;
      margin-bottom:.4rem;}
    .rv-brand-icon {width:42px;height:42px;min-width:42px;display:flex;align-items:center;justify-content:center;
      border-radius:10px;background:#2463A5;
      color:#FFFFFF;font-size:1.05rem;font-weight:750;letter-spacing:-.08em;
      border-bottom:3px solid #54C4D4;}
    .rv-brand-name {font-size:1.04rem;font-weight:750;letter-spacing:.01em;line-height:1.25;}
    .rv-brand-subtitle {font-size:.73rem;color:var(--rv-muted,#B4C5DA);line-height:1.4;margin-top:.18rem;}
    .rv-status {display:inline-flex;align-items:center;padding:.24rem .65rem;
      border:1px solid var(--rv-accent,#4D9DFF);border-radius:999px;
      color:inherit;font-size:.76rem;font-weight:600;}
    .rv-themes {display:flex;gap:.5rem;flex-wrap:wrap;}
    .rv-themes a {padding:.38rem .75rem;border:1px solid var(--rv-accent,#4D9DFF);
      border-radius:10px;color:inherit;text-decoration:none;font-size:.83rem;
      transition:background-color .18s ease;}
    .rv-themes a:hover {background:rgba(80,146,220,.13);}
    .rv-themes a:focus-visible {outline:2px solid var(--rv-accent,#4D9DFF);outline-offset:3px;}
    [class*="st-key-nav_"] button {border-radius:8px;text-align:left;min-height:2.15rem;
      justify-content:flex-start;padding-inline:.9rem;gap:.65rem;
      transition:background-color .16s ease,transform .16s ease;}
    [data-testid="stSidebarUserContent"] [data-testid="stButton"] button > div {
      width:100%;justify-content:flex-start;}
    [class*="st-key-nav_"] button [data-testid="stIconMaterial"] {width:20px;min-width:20px;}
    [class*="st-key-nav_"] button[data-testid="stBaseButton-primary"] {
      background:rgba(72,129,196,.18);border:1px solid rgba(91,154,227,.28);
      border-left:3px solid var(--rv-accent,#4D9DFF);color:inherit;}
    [class*="st-key-nav_"] button[data-testid="stBaseButton-tertiary"] {color:var(--rv-muted,#B4C5DA);}
    .rv-nav-heading {padding:.65rem .9rem .1rem;color:var(--rv-muted,#B4C5DA);
      font-size:.78rem;font-weight:600;line-height:1.3;}
    [class*="st-key-rv-nav-group-"] [data-testid="stVerticalBlock"] {gap:.15rem;}
    [class*="st-key-nav_"] button:hover {transform:translateX(2px);}
    [class*="st-key-rv-kpi-"] {background:var(--rv-surface,#101F31);border-radius:10px;padding:.7rem 1rem;
      border:1px solid var(--rv-line,#273B51);border-top:2px solid var(--rv-accent,#65ADFF);
      transition:border-color .18s ease,box-shadow .18s ease;}
    [class*="st-key-rv-kpi-"] [data-testid="stMetricValue"] {font-variant-numeric:tabular-nums;font-size:2rem;letter-spacing:-.04em;}
    [class*="st-key-rv-kpi-"] [data-testid="stMetricLabel"] {color:var(--rv-muted,#A8BAD0);}
    [class*="st-key-rv-kpi-"]:hover {border-color:rgba(88,154,238,.42);
      box-shadow:0 6px 22px rgba(8,44,94,.12);}
    [class*="st-key-rv-panel-"] {background:var(--rv-surface,#101F31);border-radius:12px;
      border:1px solid var(--rv-line,#273B51);}
    [class*="st-key-rv-panel-"] h3 {font-size:1.05rem;font-weight:650;letter-spacing:-.015em;}
    [class*="st-key-rv-panel-"] [data-testid="stVerticalBlock"] {gap:.65rem;}
    .st-key-rv-panel-actions {border-top:2px solid var(--rv-accent,#65ADFF);}
    @media (max-width:960px) {.rv-brand {padding:.65rem;}}
    @media (prefers-reduced-motion:reduce) {
      .rv-themes a,[class*="st-key-nav_"] button,[class*="st-key-rv-kpi-"] {
        transition:none!important;transform:none!important;}}
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
    const tokens=__THEME_TOKENS__;
    for(const [name,value] of Object.entries(tokens[theme])){
      document.documentElement.style.setProperty('--rv-'+name,value);
    }
    try{localStorage.setItem('rail_vehicle_theme',theme);}catch(e){}
    if(!explicit){u.searchParams.append('embed_options',theme+'_theme');window.location.replace(u.href);}
    })();</script>""".replace("__THEME_TOKENS__", json.dumps(THEME_TOKENS)), unsafe_allow_javascript=True)


def theme_url(mode, page):
    if mode not in {"light", "dark"}:
        raise ValueError("Invalid presentation theme")
    return "?" + urlencode({"embed_options": mode+"_theme", "rail_page": page})


def page_title(title, subtitle="", *, primary=False):
    with st.container(gap=None):
        st.html('<div class="rv-eyebrow">RAILVEHICLE LAB / ENGINEERING WORKSPACE</div>')
        (st.title if primary else st.header)(title)
        if subtitle:
            st.caption(subtitle)


def source_caption(*, fictional=False):
    """Label data provenance without implying live monitoring or safety status."""
    st.caption("数据来源：内置虚构演示 · 不代表实际车辆" if fictional
               else "数据来源：本地导入的历史数据 · 非实时监测")


def workflow_caption(text):
    """Provide consistent, compact workflow guidance across pages."""
    st.caption(text)


def kpi(column, label, value, detail):
    with column.container(border=True, key="rv-kpi-"+label, gap=None):
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
    ("总览", (("平台首页", ":material/dashboard:"),)),
    ("数据工作台", (("车辆参数", ":material/train:"), ("CSV 数据导入", ":material/upload_file:"), ("运行分析", ":material/monitoring:"))),
    ("运行与智能分析", (("动态运行驾驶舱", ":material/speed:"), ("机器学习异常检测", ":material/model_training:"),
                        ("振动与平稳性分析", ":material/vibration:"), ("多车辆运行对比", ":material/compare_arrows:"),
                        ("AI 智能分析中心", ":material/smart_toy:"))),
    ("演示与实验", (("统计与异常演示", ":material/query_stats:"), ("演示中心", ":material/play_circle:"))),
)


def render_navigation(public):
    private = {"车辆参数", "CSV 数据导入", "运行分析"}
    allowed = [name for _, entries in GROUPS for name, _ in entries if not public or name not in private]
    if st.session_state.get("navigation_page") not in allowed:
        linked = st.query_params.get("rail_page")
        st.session_state["navigation_page"] = linked if linked in allowed else "演示中心" if public else "平台首页"
    selected = st.session_state["navigation_page"]
    with st.sidebar:
        st.html('<div class="rv-brand"><div class="rv-brand-icon" aria-hidden="true">RV</div>'
                '<div><div class="rv-brand-name">RailVehicle-Lab</div>'
                '<div class="rv-brand-subtitle">轨道车辆运行与动力学分析</div></div></div>')
        for index, (group, entries) in enumerate(GROUPS):
            visible = [(name, icon) for name, icon in entries if name in allowed]
            if not visible:
                continue
            with st.container(key=f"rv-nav-group-{index}"):
                st.html(f'<div class="rv-nav-heading">{escape(group)}</div>')
                for name, icon in visible:
                    st.button(name, icon=icon, key=f"nav_{name}", width="stretch",
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
