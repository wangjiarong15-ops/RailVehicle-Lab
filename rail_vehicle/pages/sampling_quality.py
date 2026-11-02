"""Read-only sampling review shared with single-batch reporting."""

import streamlit as st

from rail_vehicle.data_quality import review_sampling_timestamps


def render_sampling_quality(samples: list[dict], *, key: str) -> dict:
    st.subheader("数据质量检查")
    with st.expander("采样检查规则 · 可调整", expanded=False):
        columns = st.columns(2)
        expected = columns[0].number_input(
            "预期采样间隔（秒，0 表示自动）", min_value=0., value=0., step=0.01,
            format="%.6f", key=f"{key}_expected_interval",
            help="已知采集频率时建议手动填写。例如 100 Hz 对应 0.01 秒。",
        )
        multiplier = columns[1].number_input(
            "长间隔判定倍数", min_value=1.1, value=3., step=0.5,
            key=f"{key}_gap_multiplier",
        )
    review = review_sampling_timestamps(
        (sample["timestamp"] for sample in samples),
        expected_interval_seconds=expected or None, gap_multiplier=multiplier,
    )
    columns = st.columns(3)
    columns[0].metric("重复时刻额外记录", str(review["duplicate_timestamp_rows"]))
    columns[1].metric("长采样间隔", str(review["long_gap_count"]))
    columns[2].metric("当前输入时间倒序", str(review["out_of_order_pairs"]))
    if review["status"] == "needs_review":
        st.warning("发现采样时间问题，请先回查采集记录。重复记录仍参与现有按行统计；长间隔不代表期间连续观测。")
    elif review["status"] == "insufficient":
        st.info("不足两个独立时刻，无法评估采样间隔。")
    else:
        st.info("按当前规则未发现重复时刻、倒序或长间隔；这不代表数据完整或质量合格。")
    baseline = review["baseline_seconds"]
    if baseline is not None:
        source = "手动指定" if expected else "当前范围去重后间隔中位数自动估计"
        st.caption(f"基准：{baseline:.6g} 秒（{source}）；严格大于 "
                   f"{review['gap_cutoff_seconds']:.6g} 秒标为长间隔。"
                   "自动估计可能漏掉持续低采样率或规则性缺测，请优先使用已知采集间隔。")
    st.caption("只检查当前车辆与筛选范围。历史数据已按 UTC 排序，当前输入倒序数不能用于判断原 CSV 行序；"
               "原 CSV 行序检查见导入预览。本检查不删除、补点或修改统计；检查结果与规则将写入单批次 PDF。")
    if review["long_gaps"]:
        with st.expander("长采样间隔明细（UTC）", expanded=False):
            st.dataframe([
                {"开始时刻（UTC）": gap["start_timestamp"], "结束时刻（UTC）": gap["end_timestamp"],
                 "相邻样本间隔（秒）": gap["interval_seconds"]}
                for gap in review["long_gaps"]
            ], hide_index=True, width="stretch")
            if review["details_truncated"]:
                st.caption(f"共 {review['long_gap_count']} 处长间隔，仅展示前 100 处；计数覆盖全部样本。")
    return review
