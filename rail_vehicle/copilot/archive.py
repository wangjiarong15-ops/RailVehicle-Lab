"""Portable user-owned transcripts. Imported text is inert and never creates tool calls."""

import json

from rail_vehicle.copilot.context import read_inspection_state
from rail_vehicle.coupled_vehicle import case_config as coupled_config, read_case as read_coupled_case
from rail_vehicle.lateral_dynamics import case_config, read_case


def export_chat(chat, *, lateral_case=None, inspection_state=None, coupled_case=None):
    turns = []
    for t in chat.turns:
        steps = [
            {"tool": r["tool"], "text": r["result"].text, "refs": list(r["result"].refs)} for r in t.results
        ]
        interpretation = []
        if t.verified:
            analysis = t.verified["analysis"]
            for section in ("summary", "observations", "possible_causes", "further_checks", "limitations"):
                claims = [analysis[section]] if section == "summary" else analysis[section]
                for claim in claims:
                    prose = (
                        claim["action"] + " " + claim["reason"]
                        if section == "further_checks"
                        else claim["text"]
                    )
                    if section == "possible_causes":
                        prose = "待核实假设 · " + prose
                    interpretation.append(prose + " [" + ", ".join(claim["evidence_ids"]) + "]")
        turns.append(
            dict(
                question=t.prompt, mode=t.mode, status=t.status, steps=steps, interpretation=interpretation
            )
        )
    result = dict(version=1, scope=chat.scope[0], turns=turns)
    if lateral_case is not None:
        result["engineering_case"] = case_config(lateral_case)
    if inspection_state is not None:
        result["inspection_state"] = inspection_state
    if coupled_case is not None:
        result["coupled_case"] = coupled_config(coupled_case)
    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def read_engineering_context(content):
    read_chat(content)
    archive = json.loads(content)
    context = {}
    if "engineering_case" in archive:
        context["cp_lateral_case"] = read_case(json.dumps(archive["engineering_case"]))
    if "coupled_case" in archive:
        context["cp_coupled_case"] = read_coupled_case(json.dumps(archive["coupled_case"]))
    if "inspection_state" in archive:
        state = read_inspection_state(archive["inspection_state"])
        if state is None:
            raise ValueError("会话中的三维检查状态不符合当前模型契约。")
        context["cp_inspection_state"] = state
    return context


def read_chat(content):
    if len(content) > 256_000:
        raise ValueError("会话文件过大。")
    try:
        obj = json.loads(content)
        if obj["version"] != 1 or not isinstance(obj["turns"], list) or not 1 <= len(obj["turns"]) <= 12:
            raise ValueError
        result = []
        for turn in obj["turns"]:
            prompt, steps = turn["question"], turn["steps"]
            if (
                not isinstance(prompt, str)
                or not 0 < len(prompt) <= 1200
                or not isinstance(steps, list)
                or len(steps) > 8
            ):
                raise ValueError
            lines = [item["text"] for item in steps]
            interpretation = turn.get("interpretation", [])
            if not isinstance(interpretation, list) or len(interpretation) > 8:
                raise ValueError
            lines.extend(interpretation)
            if not all(isinstance(x, str) and len(x) <= 5000 for x in lines):
                raise ValueError
            result.append((prompt, lines))
        return result
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("无法读取此会话记录。") from None
