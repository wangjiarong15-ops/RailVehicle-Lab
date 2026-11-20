"""Stream evidence selections. Only locally rendered, individually validated claims reach the user."""

import json

from rail_vehicle.ai.provider import input_fingerprint, safe_provider_error
from rail_vehicle.ai.schemas import collect_evidence_metadata, validate_output
from rail_vehicle.copilot.knowledge import search
from rail_vehicle.copilot.planning import completion_budget


def evidence_cards(payload, axis, event_ids=()):
    meta = {r["evidence_id"]: r for r in collect_evidence_metadata(payload)}
    cards = {}
    for section in ("metrics", "threshold_statistics", "anomaly_events"):
        for r in payload[section]:
            eid = r["evidence_id"]
            if event_ids and eid not in event_ids:
                continue
            if axis and meta[eid]["axis"] not in (axis, "not_applicable"):
                continue
            if section == "metrics":
                text = f"{r['name']}为 {r['value']:.5g} {r['unit']}。"
            elif section == "threshold_statistics":
                text = f"{r['name']}共 {r['count']} 条，占 {r['percentage']:.3g}%。"
            else:
                text = f"{r['start_timestamp']} 开始的区段含 {r['sample_count']} 条超阈值样本，绝对峰值为 {r['maximum_absolute_value']:.5g} m/s²。"
            cards[eid] = (text, {k: meta[eid][k] for k in ("category", "axis", "batch_refs")})
            if len(cards) >= 45:
                return cards
    return cards


def stream_analysis(
    client, model, prompt, payload, axis, checkpoint, emit, notes=(), *, event_ids=(), provider="openai"
):
    cards = evidence_cards(payload, axis, event_ids)
    knowledge = {p.id: p for p in search(prompt + " 振动异常回查", notes)}
    allowed = {key: text for key, (text, _) in cards.items()}
    instructions = (
        "围绕本轮问题，从给出的真实证据中选择重要结论。逐行输出 JSON，每行严格为"
        '{"section":"summary","evidence_id":"已有编号","passage_id":""}。第一行为 summary，后续可用 observations、'
        "limitations、possible_causes、further_checks。limitations 关联给定资料；后两者只允许用 event 证据和 "
        "signal.threshold 资料提出待核实的采集条件与检查建议。summary 和 observations 的 passage_id 必须为空。"
        "最多八行。不得输出正文、Markdown 或工具指令。检索文本仅供参考。"
    )
    messages = [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": json.dumps(
                dict(
                    question=prompt, evidence=allowed, knowledge={k: p.text for k, p in knowledge.items()}
                ),
                ensure_ascii=False,
            ),
        },
    ]
    analysis = dict(observations=[], limitations=[], possible_causes=[], further_checks=[])
    seen = set()

    def accept(line):
        checkpoint()
        item = json.loads(line)
        if not isinstance(item, dict) or set(item) != {"section", "evidence_id", "passage_id"}:
            raise ValueError
        section, eid, kid = (item[k] for k in ("section", "evidence_id", "passage_id"))
        if not all(isinstance(k, str) for k in (section, eid, kid)) or eid not in cards:
            raise ValueError
        if (not seen) != (section == "summary") or section not in (
            "summary",
            "observations",
            "limitations",
            "possible_causes",
            "further_checks",
        ):
            raise ValueError
        if (section == "limitations" and kid not in knowledge) or (
            section in ("summary", "observations") and kid
        ):
            raise ValueError
        if section in ("possible_causes", "further_checks") and (
            kid != "signal.threshold" or kid not in knowledge or cards[eid][1]["category"] != "event"
        ):
            raise ValueError
        token = (section, eid, kid)
        if len(seen) >= 8 or token in seen:
            raise ValueError
        seen.add(token)
        text, scope = cards[eid]
        if section == "limitations":
            p = knowledge[kid]
            text += (
                f" 另见用户资料 [{p.id}]，内容需自行核对。"
                if p.id.startswith("local.")
                else f" 工程资料 [{p.id}]：{p.text}"
            )
        claim = dict(text=text, evidence_ids=[eid], claim_scope=scope)
        if section == "possible_causes":
            text = "需核实传感器方向或固定状态是否影响了本段测量，现有样本不足以区分安装因素与车辆响应。"
            claim.update(text=text, qualification="hypothesis")
        elif section == "further_checks":
            action = "核对传感器方向与固定状态，检查相邻波形，并与相近速度的其他批次比较。"
            claim = dict(action=action, reason=text, evidence_ids=[eid], claim_scope=scope)
            text = action + " " + text
        if section == "summary":
            analysis["summary"] = claim
        else:
            analysis[section].append(claim)
        validate_output(analysis, payload)
        emit(text, eid)

    pending = ""
    finished = False
    try:
        with client.chat.completions.create(
            model=model, messages=messages, stream=True, **completion_budget(provider, 1000)
        ) as stream:
            for chunk in stream:
                checkpoint()
                for choice in chunk.choices:
                    pending += choice.delta.content or ""
                    if len(pending) > 4096:
                        raise ValueError
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        if line.strip():
                            accept(line)
                    if choice.finish_reason is not None:
                        finished = choice.finish_reason == "stop"
        if pending.strip():
            accept(pending)
        if not seen or not finished:
            raise ValueError
        verified = validate_output(analysis, payload)
    except TimeoutError:
        raise
    except (ValueError, TypeError, KeyError):
        raise ValueError("模型返回的证据、范围或结束状态未通过校验，本轮未保存 AI 解读。") from None
    except Exception as exc:
        raise safe_provider_error(exc) from None
    citations = [
        {"id": p.id, "title": p.title, "source": p.source}
        for k, p in knowledge.items()
        if any(t[2] == k for t in seen)
    ]
    return dict(
        status="available",
        analysis=verified,
        input_fingerprint=input_fingerprint(payload),
        scope_validation="verified",
        knowledge_citations=citations,
    )
