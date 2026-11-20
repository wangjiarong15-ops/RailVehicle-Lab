"""Portable user-owned transcripts. Imported text is inert and never creates tool calls."""

import json


def export_chat(chat):
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
    return json.dumps(
        dict(version=1, scope=chat.scope[0], turns=turns), ensure_ascii=False, indent=2
    ).encode("utf-8")


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
