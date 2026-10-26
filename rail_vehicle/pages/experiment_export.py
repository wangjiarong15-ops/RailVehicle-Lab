"""Optional PDF export for a result already bound to current page inputs."""

import streamlit as st

from rail_vehicle.experiment_report import build_ml_report, build_vibration_report, generate_experiment_pdf


def render_export(saved, fingerprint, selection, kind):
    if not saved or saved.get("fingerprint") != fingerprint:
        return
    st.subheader("导出实验报告")
    st.caption("报告使用当前已计算结果，包含来源、参数、指标、曲线及局限；不调用 AI，不重新训练或计算频谱。")
    if st.button("生成实验 PDF", key=f"{kind}_report_generate"):
        saved.pop("pdf", None)
        try:
            builder = build_ml_report if kind == "ml" else build_vibration_report
            payload = builder(saved["result"], fingerprint, selection)
            saved["pdf"] = generate_experiment_pdf(payload)
        except Exception:
            st.error("实验报告生成失败，请稍后重试；当前分析结果仍可使用。")
    if saved.get("pdf"):
        st.download_button("下载实验 PDF", data=saved["pdf"], file_name=f"{kind}-experiment.pdf",
                           mime="application/pdf", key=f"{kind}_report_download", on_click="ignore")
