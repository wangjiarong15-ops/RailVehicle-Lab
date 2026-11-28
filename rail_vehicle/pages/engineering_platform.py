"""Local project selection, provenance and engineering reference controls."""

import sqlite3

import streamlit as st

from rail_vehicle.engineering_project import capture, create_project, projects, read_revision, revisions, save_revision
from rail_vehicle.knowledge_library import documents, import_document, original, passages
from rail_vehicle.workbench_tools import serialize
from rail_vehicle.ui import COPILOT, SIMULATION


def context(page, data=None):
    state = st.session_state
    latest = state.get("wr_latest")
    choice = latest[0] if latest else None
    if page != SIMULATION:
        choice = state.get("cp_wr_case") or choice
    project = state.get("x_project")
    limits = state.get("x_constraints", {}).get(project) if project else None
    task = state.get("x_current_task") if state.get("x_task_project") == project else None
    result = capture(page, data, case=choice, inspection=state.get("cp_inspection_state"),
                     project=project, task=task, constraints=limits)
    if data:
        state["x_last_observation"] = (project, result["observation"])
    elif page != COPILOT:
        last = state.get("x_last_observation")
        if last and last[0] == project:
            result["recent_observation"] = last[1]
    return result


def _library(project):
    st.markdown("**工程知识库**")
    with st.form("x_library_import", clear_on_submit=True):
        content = st.file_uploader("授权资料", type=["md", "txt", "pdf"], max_upload_size=2)
        source = st.text_input("资料来源及授权说明", max_chars=250)
        submitted = st.form_submit_button("索引到当前项目")
    if submitted and content is not None:
        _, revision = import_document(project, content.name, source, content.getvalue())
        st.success(f"资料版本 v{revision} 已保存到本机。")
    query = st.text_input("检索原文、工程公式或结构术语", max_chars=160, key=f"x_query_{project}")
    if query:
        hits = passages(project, query)
        for hit in hits:
            st.caption(f"{hit.title} · {hit.source} · {hit.id}")
            st.text(hit.text)
        if not hits:
            st.caption("当前项目没有匹配段落。")
    archive = documents(project)
    if archive:
        selected = st.selectbox("已索引资料版本", range(len(archive)),
                                format_func=lambda i: f"{archive[i]['title']} · v{archive[i]['revision']}")
        row = archive[selected]
        name, content = original(project, row["document_id"], row["revision"])
        st.caption(f"SHA-256 {row['sha256']}")
        st.download_button("下载对应原文", content, name, on_click="ignore")


def project_bar():
    """Keep project selection available across pages without intercepting existing routes."""
    state = st.session_state
    try:
        archive = {item["id"]: item["name"] for item in projects()}
        if selected := state.pop("x_new_project", None):
            state["x_project"] = selected
    except (sqlite3.Error, OSError):
        return
    with st.container(horizontal=True, vertical_alignment="center", key="x_project_bar"):
        with st.popover("工程项目", icon=":material/folder_open:"):
            project = st.selectbox("当前工程项目", (None, *archive), key="x_project",
                                   format_func=lambda item: archive.get(item, "未选择"))
            try:
                with st.form("x_project_create", clear_on_submit=True):
                    name = st.text_input("新工程项目名称", max_chars=80)
                    submitted = st.form_submit_button("创建项目")
                if submitted:
                    state["x_new_project"] = create_project(name)
                    st.rerun()
                if project:
                    with st.expander("工况约束、版本与资料"):
                        ratio = st.number_input("减载率约束（%）", 2., 95., 40., key=f"x_unload_{project}")
                        travel = st.number_input("二系行程约束（mm）", 4., 100., 35., key=f"x_travel_{project}")
                        if ratio is not None and travel is not None:
                            state.setdefault("x_constraints", {})[project] = {
                                "unloading_ratio": ratio / 100, "travel_mm": travel,
                            }
                        previous_context = state.get("x_page_context")
                        has_context = previous_context and previous_context.get("project") == project
                        if st.button("保存当前上下文版本", disabled=not has_context):
                            revision = save_revision(project, previous_context)
                            st.success(f"已保存项目版本 v{revision}。")
                        saved = revisions(project)
                        if saved:
                            selected_revision = st.selectbox("项目版本", [row["revision"] for row in saved])
                            saved_context = read_revision(project, selected_revision)
                            st.download_button("导出项目上下文 JSON", serialize(saved_context), "project-context.json",
                                               "application/json", on_click="ignore")
                        _library(project)
            except (ValueError, sqlite3.Error, OSError) as error:
                st.warning(str(error) if isinstance(error, ValueError) else "无法读取或保存当前工程项目。")
        label = archive.get(project, "未选择项目")
        current = state.get("x_page_context", {})
        current_obs = current.get("observation") or current.get("recent_observation")
        if current_obs and current.get("project") == project:
            label += f" · 最近批次 #{current_obs['batch_id']}"
        st.caption(label)


def remember_page(page):
    state = st.session_state
    if page == COPILOT:
        return
    data = None
    if state.get("x_project"):
        from rail_vehicle.copilot.context import Dataset, MAX_ROWS
        from rail_vehicle.pages.data_workflow import BATCH_HANDOFF
        from rail_vehicle.ui import BATCH_VIEWS

        handoff = state.get(BATCH_HANDOFF) if page in BATCH_VIEWS else None
        if handoff:
            vehicle, batch, rows = handoff["copilot_data"]
            choice = handoff["evidence_selection"]
            if 0 < len(rows) <= MAX_ROWS:
                data = Dataset("demo" if handoff["target"]["fictional"] else "history",
                               vehicle, batch, rows, choice["threshold"], choice.get("row_span"),
                               preferred_event=handoff.get("evidence_id"))
    state["x_page_context"] = context(page, data)
