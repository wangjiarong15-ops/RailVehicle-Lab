"""Shared prompt instructions for traceable, request-local evidence citations."""

from __future__ import annotations

import json
from typing import Any

from rail_vehicle.ai.schemas import collect_evidence_metadata


def build_output_contract_instructions() -> str:
    """Return provider-neutral instructions matching the local output contract."""
    return (
        "【输出 JSON 契约】\n"
        "必须返回完整 JSON 对象，不要添加 Markdown 代码围栏或 JSON 之外的文字。"
        "根对象必须且只能包含 summary、observations、possible_causes、further_checks、limitations 五个字段；"
        "五个字段都必须存在；没有可写内容时 observations、possible_causes、further_checks、limitations 使用空数组。\n"
        "summary 必须是包含非空 text 和 evidence_ids 的对象；evidence_ids 至少包含一个本次清单中的合法 ID。"
        "observations 和 limitations 中的每一项也必须包含非空 text 和至少一个合法 evidence_id。"
        "不得输出空 evidence_ids，不得伪造引用。\n"
        "possible_causes 中的每一项必须包含非空 text、至少一个合法 evidence_id，"
        "以及固定字段 qualification，其值必须逐字为 \"hypothesis\"。\n"
        "further_checks 中的每一项必须同时包含非空 action、非空 reason 和至少一个合法 evidence_id。"
        "各项只能使用对应契约字段，不添加额外字段。\n"
        "每一个实际生成的 summary、observation、possible_cause、further_check 和 limitation 都必须包含非 null 的 claim_scope 对象；"
        "该对象必须包含 category、axis、batch_refs，并按当前证据元数据和允许值准确填写。"
        "不得省略 claim_scope，也不得将其设为 null。\n"
    )


def build_evidence_reference_instructions(payload: dict[str, Any]) -> str:
    """Build request-local citation guidance from the validator's evidence catalog."""
    evidence_metadata = collect_evidence_metadata(payload)
    allowed_ids = [item["evidence_id"] for item in evidence_metadata]
    allowed_ids_json = json.dumps(allowed_ids, ensure_ascii=False, separators=(",", ":"))
    metadata_json = json.dumps(evidence_metadata, ensure_ascii=False, separators=(",", ":"))
    event_duration_guidance = ""
    if payload.get("anomaly_events"):
        event_duration_guidance = (
            "【异常事件时间口径】\n"
            "异常事件的 duration_seconds 表示首个与最后一个异常样本之间的时间差，"
            "不代表该区间内持续观测到异常；存在采样空档时，不得声称空档期间持续异常。\n"
        )
    return (
        build_output_contract_instructions()
        + "【本次请求的证据引用规则】\n"
        f"本次唯一允许引用的 evidence_id 清单（直接取自当前结构化上下文）：{allowed_ids_json}\n"
        f"每个 ID 对应的校验元数据（category、axis、batch_refs，来自本地证据目录）：{metadata_json}\n"
        "每条结论的 evidence_ids 只能逐字引用此清单中的 ID；不得创造、猜测、改写，"
        "也不得沿用任何其他请求或历史分析中的 ID。引用前请逐项核对 ID，禁止为了满足格式而编造证据。\n"
        "claim_scope.category 必须按所引证据元数据填写：类别相同就填该类别，引用了不同类别就填 mixed。"
        "claim_scope.axis 按本地校验口径归并：忽略 not_applicable 后无适用轴向则填 not_applicable，"
        "只剩一个轴向就填该轴向，存在多个轴向就填 mixed。"
        "claim_scope.batch_refs 必须与所有被引用证据的 batch_refs 合并去重后的集合完全一致；"
        "没有批次范围时填空数组。不得把未引用的批次加入范围。\n"
        "没有合适证据时，不生成相应的可选观察、可能原因、检查建议或局限项。"
        "summary 必须由至少一个现有证据 ID 支持；若无法支持，不得编造 summary 或伪造 ID。\n"
        + event_duration_guidance
    )
