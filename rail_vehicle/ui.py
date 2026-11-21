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
    "dark": {"surface": "#1C293C", "accent": "#7CB4FF", "muted": "#ABC0D7", "line": "#304158"},
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
    [data-testid="stMainBlockContainer"] {padding:3.35rem clamp(1rem,2.1vw,2.6rem) 2.25rem;max-width:1840px}
    [data-testid="stSidebarUserContent"] {padding-top:0}
    [data-testid="stHeading"] :is(h1,h2,h3) {letter-spacing:-.025em;line-height:1.28}
    [data-testid="stHeading"] h3 {font-size:1.12rem}
    [data-testid="stCaptionContainer"] p,.rv-legend {color:var(--rv-muted);opacity:1}
    .rv-legend,.rv-themes {display:flex;align-items:center;gap:.9rem;flex-wrap:wrap;font-size:.83rem}
    .rv-legend b {color:#ce5c64}
    .rv-themes a,.rv-status {border:1px solid var(--rv-line);padding:.3rem .7rem;border-radius:5px;color:inherit}
    .rv-brand {display:flex;align-items:center;gap:.72rem;padding:.2rem .45rem .85rem;border-bottom:1px solid var(--rv-line)}
    .rv-brand-icon {display:flex;align-items:center;justify-content:center;gap:3px;background:#1c484c;
      width:34px;height:36px;border-radius:7px;color:#86e2d4}
    .rv-brand-icon i {width:3px;height:12px;border-radius:1px;background:currentColor;transform:skew(-13deg)}
    .rv-brand-icon i:nth-child(2){height:22px}.rv-brand-icon i:last-child{height:16px}
    .rv-brand-name {font-size:1.07rem;font-weight:730}.rv-brand-subtitle,.rv-nav-label {font-size:.75rem;color:var(--rv-muted)}
    .rv-nav-label {font-weight:650;padding:.48rem .6rem 0;letter-spacing:.04em}
    [data-testid="stSidebarCollapseButton"]{visibility:visible}
    [class*="st-key-nav_"] button {border-radius:5px;min-height:1.92rem;justify-content:flex-start;padding:.1rem .65rem}
    [data-testid="stSidebarUserContent"] [data-testid="stButton"] button > div{width:100%;justify-content:flex-start}
    [class*="st-key-nav_"] button[data-testid="stBaseButton-primary"]{background:var(--rv-surface);
      border:1px solid var(--rv-line);border-left:3px solid var(--rv-accent);color:inherit}
    [class*="st-key-nav_"] button:focus-visible,.rv-themes a:focus-visible{outline:2px solid var(--rv-accent)}
    [class*="st-key-rv-kpi-"] {border-left:2px solid var(--rv-line);padding:.2rem 1rem}
    [data-testid="stMetricValue"] {font-variant-numeric:tabular-nums;letter-spacing:-.035em}
    [class*="st-key-rv-panel-"] {border-top:1px solid var(--rv-line);padding-top:.65rem}
    .st-key-rv-panel-actions {background:var(--rv-surface);border-radius:6px;padding:.65rem 1rem}
    .st-key-cp_composer {padding:1.15rem clamp(.8rem,2vw,2rem);background:var(--rv-surface);
      border-left:3px solid var(--rv-accent);border-radius:6px}
    .st-key-cp_composer [data-testid="stChatInput"] {border:1px solid var(--rv-muted);
      border-radius:6px;padding:.15rem .35rem}
    .st-key-cp_composer [data-testid="stChatInput"]:focus-within {outline:2px solid var(--rv-accent);
      outline-offset:1px}
    [data-testid="stChatInput"] textarea {font-size:1.05rem}
    @media(max-height:800px){.rv-nav-label{padding-top:.22rem}[class*="st-key-nav_"] button{min-height:1.7rem}}
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


RESUME_PARAM = "rail_resume"


def theme_url(mode, page):
    if mode not in {"light", "dark"}:
        raise ValueError("Invalid presentation theme")
    return "?" + urlencode({"embed_options": mode+"_theme", "rail_page": page, RESUME_PARAM: "1"})


def page_title(title, subtitle="", *, primary=False):
    with st.container(gap=None):
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
    with column.container(border=False, key="rv-kpi-"+label, gap=None):
        st.metric(label, value)
        st.caption(detail)


def status_label(text):
    st.html(f'<span class="rv-status">{escape(text)}</span>')


def empty_state(text):
    st.info(text)


def chart_container(key=None):
    return st.container(border=False, key=key)


def data_table(rows, **kwargs):
    st.dataframe(rows, hide_index=True, width="stretch", **kwargs)


def action_button(label, **kwargs):
    return st.button(label, **kwargs)


def navigate(page, **settings):
    current = st.session_state.get("navigation_page")
    if current != page:
        if current in BATCH_VIEWS:
            st.session_state["rail_analysis_inputs"] = analysis_inputs()
        if page in BATCH_VIEWS:
            st.session_state.update(st.session_state.get("rail_analysis_inputs", {}))
        if current == COMPARISON:
            st.session_state["rail_comparison_inputs"] = {
                key: value for key, value in st.session_state.items()
                if key == "comparison_vehicle_id" or key.startswith("comparison_batch_ids_")
            }
        if page == COMPARISON:
            st.session_state.update(st.session_state.get("rail_comparison_inputs", {}))
    st.session_state.update(settings)
    st.session_state["navigation_page"] = page


def analysis_inputs():
    prefixes = ("dynamics_", "demo_dynamics_", "analysis_start_", "analysis_end_",
                "anomaly_review_event", "anomaly_review_seconds", "history_sampling_", "demo_sampling_")
    fields = {"workbench_analysis_source", "analysis_threshold", "demo_analysis_threshold", "analysis_replay_scope"}
    return {key: value for key, value in st.session_state.items()
            if key in fields or key.startswith(prefixes)}


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


HOME = "平台首页"
ANALYSIS = "运行分析"
ANOMALIES = "异常检测"
COMPARISON = "批次对比"
REPORT = "分析报告"
DATA_PAGES = ("车辆参数", "CSV 数据导入")
DEMO_DATA = "统计与异常演示"
BATCH_VIEWS = (ANALYSIS, ANOMALIES, REPORT, DEMO_DATA)
BOGIE = "三维转向架仿真"
AI = "AI 智能分析中心"
COPILOT = "RailVehicle Copilot"
ADVANCED_PAGES = ("演示中心", "动态运行驾驶舱", "多车辆运行对比", "机器学习异常检测")
ANALYSIS_MODE = "workbench_analysis_mode"


SIMULATION = "动力学仿真"
LINKED = "联动分析"
_WORKSPACE = ((HOME, "工作台首页", "space_dashboard"),
              (DATA_PAGES[0], "车辆管理", "train"), (DATA_PAGES[1], "数据导入", "upload_file"))
_ENGINEERING = ((BOGIE, "三维转向架", "view_in_ar"), (SIMULATION, SIMULATION, "experiment"),
                (COPILOT, "AI Copilot", "forum"), (REPORT, REPORT, "description"))
_REVIEW = ((ANOMALIES, ANOMALIES, "warning"), (LINKED, LINKED, "timeline"),
           (COMPARISON, COMPARISON, "compare_arrows"), ("振动与平稳性分析", "振动与频谱", "waves"),
           (AI, "AI 证据解读", "fact_check"))
_SPECIALTY = ((DEMO_DATA, "虚构数据分析", "science"), *zip(ADVANCED_PAGES, ADVANCED_PAGES,
              ("school", "speed", "compare", "model_training")))


def render_navigation(public, on_select=navigate):
    analysis = (DEMO_DATA, "虚构数据分析", "monitoring") if public else (ANALYSIS, ANALYSIS, "monitoring")
    groups = (("工作空间", _WORKSPACE[:1] if public else _WORKSPACE),
              ("工程分析", (analysis, *_ENGINEERING)),
              ("专项复核", tuple(row for row in _REVIEW if not public or row[0] != COMPARISON)))
    specialty = _SPECIALTY[1:] if public else _SPECIALTY
    allowed = {route for _, rows in groups for route, _, _ in rows} | {row[0] for row in specialty}
    aliases = {label: route for _, rows in groups for route, label, _ in rows}
    aliases.update({"分析工作台": DEMO_DATA if public else ANALYSIS, "高级工具": HOME,
                    "车辆与数据": DEMO_DATA if public else DATA_PAGES[0],
                    "综合工作台": HOME, "三维诊断工作台": BOGIE, "AI 分析中心": AI,
                    "同车多批次对比": COMPARISON, "工作台": HOME, "数据管理": DATA_PAGES[1],
                    "三维可视化": BOGIE, "工程 Copilot": COPILOT, "AI 智能分析": AI})
    requested = st.session_state.get("navigation_page")
    if requested is None:
        requested = st.query_params.get("rail_page")
    selected = aliases.get(requested, requested)
    if selected not in allowed:
        selected = DEMO_DATA if public else HOME
    if not public and selected == ANALYSIS and st.session_state.get(ANALYSIS_MODE) == "comparison":
        selected = COMPARISON
    st.session_state["navigation_page"] = selected

    def render_rows(rows):
        for route, label, icon in rows:
            st.button(label, icon=f":material/{icon}:", key=f"nav_{route}", width="stretch",
                      type="primary" if route == selected else "tertiary", on_click=on_select, args=(route,))

    with st.sidebar:
        st.html('<div class="rv-brand"><div class="rv-brand-icon" aria-hidden="true"><i></i><i></i><i></i></div>'
                '<div><div class="rv-brand-name">RailVehicle-Lab</div>'
                '<div class="rv-brand-subtitle">车辆工程工作空间</div></div></div>')
        with st.container(gap="xxsmall"):
            for label, rows in groups:
                st.html(f'<div class="rv-nav-label">{label}</div>')
                render_rows(rows)
            with st.expander("专项工具", icon=":material/tune:", expanded=selected in {r[0] for r in specialty}):
                with st.container(gap="xxsmall"):
                    render_rows(specialty)
            st.html('<div class="rv-nav-label">系统管理</div>')
            with st.container(horizontal=True, gap="xsmall"):
                with st.popover("系统设置", icon=":material/settings:"):
                    st.markdown("**系统设置 · 工作空间外观**")
                    st.html('<div class="rv-themes">' + ''.join(
                        f'<a target="_self" href="{escape(theme_url(mode, selected), quote=True)}">{label}</a>'
                        for mode, label in (("dark", "◐ 深色"), ("light", "◑ 浅色"))) + '</div>')
                    st.caption("切换外观会重载页面。本标签页可恢复单批次筛选；未保存的 AI 解读和未下载的报告需要重新生成。")
                with st.popover("关于", icon=":material/info:"):
                    st.markdown("**RailVehicle-Lab** · 轨道车辆异常分析与三维可视化")
                    st.caption("规则异常、三维结构示意和 AI 辅助解释不能作为故障确诊或运营安全结论。")
            st.caption("● 公开虚构数据演示" if public else "● 本地工作空间")
    return selected
