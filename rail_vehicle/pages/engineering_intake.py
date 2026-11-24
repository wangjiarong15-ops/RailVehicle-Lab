"""Engineering CSV preflight and read-only provenance views."""

import streamlit as st

from rail_vehicle.engineering_intake import CONFIG_LABELS, SOURCE_LABELS, assess_intake, configuration_review, load_manifest


def _show_quality(intake):
    quality = intake["quality"]
    manifest = intake["manifest"]
    st.caption(f"{SOURCE_LABELS[manifest['source_kind']]} · 声明采样 {manifest['sample_rate_hz']:g} Hz · "
               f"时间基准 {manifest['time_basis']} · 时钟漂移证据不足 · 独立实测待完成")
    st.caption(f"全文件质量筛查 · 重复时刻 {quality['duplicate_timestamp_rows']} 条 · "
               f"长间隔 {quality['long_gap_count']} 处 · 短间隔 {quality['short_interval_count']} 处 · "
               f"倒序 {quality['out_of_order_pairs']} 处")
    st.caption("筛查以声明采样周期为基准，小于 0.5 倍记为短间隔，大于 3 倍记为长间隔。")
    if "duplicate_row_fraction" in quality:
        rate = quality.get("median_implied_rate_hz")
        estimate = f"{rate:.3g} Hz" if rate is not None else "未知（无正间隔）"
        fraction = quality["duplicate_row_fraction"]
        st.caption(f"正间隔中位数对应采样率 {estimate} · 重复时间戳额外行比例 {fraction:.1%}。")
    for channel, count in quality.get("full_scale_candidate_counts", {}).items():
        label = {"lateral_accel": "横向加速度", "vertical_accel": "垂向加速度"}[channel]
        status = "未声明量程" if count is None else f"{count} 条达到或超过声明量程的候选记录"
        st.caption(f"{label} · {status}")


def import_preflight(validation, *, revision):
    with st.expander("工程数据接入（可选）"):
        if not st.checkbox("本次导入附带工程元数据", key=f"intake_enable_{revision}"):
            return None, True
        st.caption("上传与当前单车 CSV 配套的元数据 JSON。声明用于核对来源、测点、单位和时间基准。")
        if validation.is_valid:
            st.code(validation.source_sha256, language=None)
        file = st.file_uploader("工程元数据 JSON", type=["json"], max_upload_size=1, key=f"intake_{revision}")
        if file is None:
            st.info("请上传工程元数据 JSON。")
            return None, False
        try:
            manifest = load_manifest(file.getvalue())
            assessment = assess_intake(validation, manifest)
        except ValueError as exc:
            st.error(str(exc))
            return None, False
        _show_quality(assessment)
        st.success("工程接入元数据已核对。现场资料与实测验收待核。")
        return manifest, True


def show_batch_intake(snapshot):
    if not (intake := snapshot.get("engineering")):
        return
    _show_quality(intake)
    st.dataframe([{"通道": row["channel"], "当前配置版本": row["version"],
                   "配置核对": CONFIG_LABELS[row["status"]], "差异字段": "、".join(row["differences"])}
                  for row in configuration_review(intake, snapshot["channels"])], hide_index=True)
