"""Local, allowlisted evidence details; no calculations or model requests."""

from rail_vehicle.ai.schemas import collect_evidence_metadata


def evidence_detail_catalog(payload):
    """Project current sanitized facts using the validator's exact scope catalog."""
    metadata = collect_evidence_metadata(payload)
    facts = {item["evidence_id"]: item for group in ("metrics", "threshold_statistics", "anomaly_events")
             for item in payload.get(group, [])}
    for batch in payload.get("batches", []):
        facts.update({item["evidence_id"]: item for item in batch["metrics"]})
    catalog = {}
    for meta in metadata:
        fact = facts[meta["evidence_id"]]
        base = {"证据 ID": meta["evidence_id"], "类别": meta["category"],
                "轴向": meta["axis"], "批次范围": "、".join(meta["batch_refs"]) or "不归属单一批次"}
        if meta["category"] == "event":
            fields = (("开始时间 UTC", fact["start_timestamp"], "ISO 8601"),
                      ("结束时间 UTC", fact["end_timestamp"], "ISO 8601"),
                      ("首末异常样本跨度", fact["duration_seconds"], "s"),
                      ("异常样本数量", fact["sample_count"], "点"),
                      ("最大绝对值", fact["maximum_absolute_value"], "m/s^2"))
            method = "首末样本跨度不表示空档期间持续异常"
        elif meta["category"] == "threshold_statistic":
            fields = ((fact["name"] + "数量", fact["count"], "点"),
                      (fact["name"] + "比例", fact["percentage"], "%"))
            method = payload.get("threshold", {}).get("rule", "当前输入的超阈值样本统计")
        else:
            fields = ((fact["name"], fact["value"], fact["unit"]),)
            method = fact["method"]
        catalog[meta["evidence_id"]] = [dict(base, 指标=label, 值=str(value), 单位=unit, 口径=method)
                                         for label, value, unit in fields]
    return catalog
