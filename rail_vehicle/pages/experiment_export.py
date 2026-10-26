"""Optional PDF export for a result already bound to current page inputs."""

import streamlit as st
import hashlib
import json

from rail_vehicle.experiment_report import build_ml_report, build_vibration_report, generate_experiment_pdf, validated_experiment_ai
from rail_vehicle.fleet_report import build_fleet_report


def render_export(saved, fingerprint, selection, kind, *, ai_context=None, ai_result=None):
    if not saved or saved.get("fingerprint") != fingerprint:
        return
    st.subheader("导出多车辆对比报告" if kind == "fleet" else "导出实验报告")
    st.caption("报告使用当前已计算结果，包含来源、参数、指标、曲线及局限；不调用 AI，不重新训练或计算频谱。")
    section = validated_experiment_ai(ai_context, ai_result, fingerprint, selection, kind)
    ai_token = hashlib.sha256(json.dumps(section, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    if saved.get("pdf_ai_token") != ai_token:
        saved.pop("pdf", None)
        saved.pop("pdf_ai_token", None)
    st.caption("将附加当前完整验证的 AI 解读及证据目录。" if section else "当前无匹配且完整验证的 AI 解读，仍可导出确定性实验报告。")
    if st.button("生成多车辆 PDF" if kind == "fleet" else "生成实验 PDF", key=f"{kind}_report_generate"):
        saved.pop("pdf", None)
        try:
            builder = {"ml": build_ml_report, "vibration": build_vibration_report, "fleet": build_fleet_report}[kind]
            payload = builder(saved["result"], fingerprint, selection, ai_context=ai_context, ai_result=ai_result)
            saved["pdf"] = generate_experiment_pdf(payload)
            saved["pdf_ai_token"] = ai_token
        except Exception:
            st.error("实验报告生成失败，请稍后重试；当前分析结果仍可使用。")
    if saved.get("pdf"):
        st.download_button("下载多车辆 PDF" if kind == "fleet" else "下载实验 PDF", data=saved["pdf"], file_name=f"{kind}-experiment.pdf",
                           mime="application/pdf", key=f"{kind}_report_download", on_click="ignore")
