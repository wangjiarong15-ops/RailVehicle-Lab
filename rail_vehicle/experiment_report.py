"""Deterministic experiment reports built only from already computed results.

Report fact IDs are local to these documents, not AI evidence/claim_scope IDs.
This module neither fits models nor computes signal statistics nor calls AI.
"""

from datetime import datetime, timezone
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import math
import re

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Group, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, SimpleDocTemplate, Spacer

from rail_vehicle.analysis_report import _font_names, _paragraph, _sampled_indices, _table
from rail_vehicle.ai.provider import input_fingerprint
from rail_vehicle.ai.schemas import collect_evidence_metadata, validate_output
from rail_vehicle.ai.ui import selection_fingerprint
from rail_vehicle.timestamp_utils import parse_iso_timestamp


SAFETY_NOTE = "本报告为离线数据探索性实验记录，不构成车辆故障诊断、事故、安全、法规或标准舒适等级结论。PDF 生成过程不调用 AI 服务。"
SOURCE_FIELDS = ("vehicle", "reference", "target", "batch")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("报告指标必须为有限数值。")
    return value


def _base(kind, fingerprint, selection, generated_at):
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("缺少有效分析指纹，不能导出实验报告。")
    source = selection.get("source")
    if source not in ("demo", "history"):
        raise ValueError("实验数据来源无效。")
    # Explicit allowlist: no filenames, remarks, credentials or arbitrary metadata.
    identifiers = {}
    if source == "history":
        for field in SOURCE_FIELDS:
            if field in selection:
                value = selection[field]
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("实验来源编号无效。")
                identifiers[field] = value
        required = {"vehicle", "reference", "target"} if kind == "ml" else {"vehicle", "batch"}
        if not required.issubset(identifiers):
            raise ValueError("实验来源信息不完整。")
    return dict(kind=kind, input_fingerprint=fingerprint,
                generated_at=(generated_at or datetime.now(timezone.utc)).isoformat(),
                source="内置虚构数据" if source == "demo" else "历史运行批次",
                identifiers=identifiers, facts=[], charts=[], notes=[])


def _fact(report, fact_id, label, value, unit=""):
    if value is not None:
        _number(value)
    report["facts"].append(dict(id=fact_id, label=label, value=value, unit=unit))


def _chart(title, x, y, x_label, y_label):
    if not x or len(x) != len(y):
        raise ValueError("报告曲线数据无效。")
    for value in (*x, *y):
        _number(value)
    # Bound PDF size, preserve endpoints and global y extrema.
    indices = sorted(set(_sampled_indices(len(x))) | {min(range(len(y)), key=y.__getitem__),
                                                    max(range(len(y)), key=y.__getitem__)})
    return dict(title=title, x_label=x_label, y_label=y_label,
                points=[(x[i], y[i]) for i in indices], source_points=len(x))


def validated_experiment_ai(context, result, fingerprint, selection, kind):
    """Fail closed for optional AI; no legacy scope path or context reconstruction."""
    try:
        expected = {"ml": "ml_experiment", "vibration": "vibration_experiment"}[kind]
        if not isinstance(context, dict) or not isinstance(result, dict):
            return None
        if context.get("analysis_type") != expected or result.get("status") != "available":
            return None
        if result.get("scope_validation") != "verified":
            return None
        if result.get("selection_fingerprint") != selection_fingerprint(
            dict(selection=selection, analysis_fingerprint=fingerprint)
        ):
            return None
        current = input_fingerprint(context)
        if current != result.get("input_fingerprint"):
            return None
        analysis = validate_output(result.get("analysis"), context)
        claims = [analysis["summary"]] + [claim for category in
                  ("observations", "possible_causes", "further_checks", "limitations") for claim in analysis[category]]
        cited = {eid for claim in claims for eid in claim["evidence_ids"]}
        facts = {item["evidence_id"]: item for item in context["metrics"]}
        for batch in context.get("batches", []):
            facts.update({item["evidence_id"]: item for item in batch["metrics"]})
        evidence = [{**item, **{field: facts[item["evidence_id"]][field] for field in ("name", "value", "unit")}}
                    for item in collect_evidence_metadata(context) if item["evidence_id"] in cited]
        metadata = result.get("ai_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        # Only known metadata fields; never propagate arbitrary provider fields.
        provider = metadata.get("provider")
        provider = provider if provider in ("deepseek", "openai") else "未记录"
        model = metadata.get("model")
        model = model if isinstance(model, str) and 0 < len(model) <= 160 else "未记录"
        generated = metadata.get("generated_at")
        if not isinstance(generated, str):
            generated = "未记录"
        else:
            try:
                generated = parse_iso_timestamp(generated).isoformat()
            except (ValueError, TypeError, OverflowError):
                generated = "未记录"
        section = deepcopy(dict(analysis=analysis, evidence=evidence, input_fingerprint=current,
                                provider=provider, model=model, generated_at=generated,
                                status="available", scope_validation="verified"))
        json.dumps(section, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return section
    except Exception:
        # Optional rejected AI must never block the deterministic PDF.
        return None


def _finish(report, ai_context=None, ai_result=None, selection=None):
    report["ai"] = validated_experiment_ai(ai_context, ai_result, report["input_fingerprint"], selection, report["kind"])
    encoded = json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    report["report_fingerprint"] = sha256(encoded).hexdigest()
    return report


def build_ml_report(result, fingerprint, selection, *, generated_at=None, ai_context=None, ai_result=None):
    report = _base("ml", fingerprint, selection, generated_at)
    report["title"] = "轨道车辆机器学习离群检测实验报告"
    report["ranges"] = dict(参考开始=result["reference_start"], 参考结束=result["reference_end"],
                            检测开始=result["target_start"], 检测结束=result["target_end"])
    for field, label, unit in (
        ("reference_count", "参考样本数", "条"), ("sample_count", "检测样本数", "条"),
        ("ml_count", "模型标记样本数", "条"), ("rule_count", "规则标记样本数", "条"),
        ("contamination", "参考离群比例参数", "比例"), ("rule_threshold", "加速度规则阈值", "m/s^2"),
        ("score_threshold", "参考分布学习的分数阈值", "分数"),
        ("seed", "随机种子", ""), ("tree_count", "树数量", "棵"),
    ):
        _fact(report, f"report.ml.{field}", label, result[field], unit)
    for key, label in (("both", "模型与规则均标记"), ("ml_only", "仅模型标记"),
                       ("rule_only", "仅规则标记"), ("neither", "两者均未标记")):
        _fact(report, f"report.ml.count.{key}", label, result["counts"][key], "条")
    records = result["records"]
    start = parse_iso_timestamp(records[0]["timestamp"])
    offsets = [(parse_iso_timestamp(row["timestamp"]) - start).total_seconds() for row in records]
    report["charts"] = [_chart("检测样本离群分数", offsets, [r["ml_score"] for r in records],
                               "相对检测开始时间（秒）", "离群分数"),
                        _chart("检测速度", offsets, [r["speed_kmh"] for r in records],
                               "相对检测开始时间（秒）", "速度（km/h）")]
    report["charts"][0]["horizontal_line"] = result["score_threshold"]
    report["notes"] += [
        "算法：scikit-learn IsolationForest；特征：速度、横向加速度、垂向加速度。仅在参考批次拟合，检测批次不参与训练。",
        "离群分数=-score_samples，严格大于参考分布学习的阈值时标记；分数不是故障概率。规则为任一加速度绝对值严格大于规则阈值。",
        "参考离群比例不是检测数据的真实异常率。没有真实故障标签，不能据此报告准确率；参考批次不等于经过认证的正常车辆。",
        "线路、载荷、传感器位置及运行工况差异可能导致分布偏移。报告未验证参考与检测数据是否独立或工况一致。",
    ]
    return _finish(report, ai_context, ai_result, selection)


def build_vibration_report(result, fingerprint, selection, *, generated_at=None, ai_context=None, ai_result=None):
    report = _base("vibration", fingerprint, selection, generated_at)
    report["title"] = "轨道车辆探索性振动分析实验报告"
    report["ranges"] = dict(分析开始=result["timestamps_utc"][0], 分析结束=result["timestamps_utc"][-1])
    for field, label, unit in (
        ("sample_count", "分析样本数", "条"), ("duration_seconds", "记录跨度", "s"),
        ("sampling_rate_hz", "估算采样率", "Hz"), ("interval_deviation", "采样间隔最大相对偏差", "比例"),
        ("band_low", "频带下限", "Hz"), ("band_high", "频带上限", "Hz"),
        ("window_seconds", "滑动 RMS 窗口", "s"),
    ):
        _fact(report, f"report.vibration.{field}", label, result[field], unit)
    _fact(report, "report.vibration.duplicate_rows", "重复 UTC 时间戳", result["sampling"]["duplicate_timestamp_rows"], "条")
    report["spectral_available"] = bool(result["spectral_available"])
    for field, axis in (("lateral_accel", "横向"), ("vertical_accel", "垂向")):
        channel = result["channels"][field]
        for key, label, unit in (("raw_mean", "原始均值", "m/s^2"), ("raw_rms", "原始 RMS", "m/s^2"),
                                 ("dynamic_rms", "去均值 RMS", "m/s^2"), ("dynamic_peak", "去均值绝对峰值", "m/s^2"),
                                 ("crest_factor", "峰值因子", "")):
            _fact(report, f"report.vibration.{field}.{key}", axis + label, channel[key], unit)
        if result["spectral_available"]:
            for key, label, unit in (("dominant_frequency_hz", "频带内主峰", "Hz"), ("band_rms", "未加权频带 RMS", "m/s^2")):
                _fact(report, f"report.vibration.{field}.{key}", axis + label, channel[key], unit)
            report["charts"].append(_chart(axis + "功率谱", channel["frequencies"], channel["psd"], "频率（Hz）", "PSD ((m/s^2)^2/Hz)"))
            if channel["rolling_rms"]:
                report["charts"].append(_chart(axis + "滑动振动强度", channel["rolling_end_seconds"], channel["rolling_rms"],
                                               "窗口结束时刻（相对秒）", "去均值 RMS（m/s^2）"))
    if result["spectral_available"]:
        for field, label, unit in (("segment_samples", "Welch 分段点数", "点"),
                                  ("segment_count", "Welch 段数", "段"),
                                  ("frequency_resolution_hz", "频率间隔", "Hz")):
            _fact(report, f"report.vibration.{field}", label, result[field], unit)
    else:
        report["notes"].append("频域条件不足：仅报告时域指标，未生成 PSD、频带 RMS 或滑动 RMS 曲线。")
    report["notes"] += list(result["issues"])
    report["notes"] += [
        "原始 RMS：加速度平方的均值再开平方；去均值 RMS：减去整段均值后，平方取均值再开平方。峰值因子=去均值绝对峰值/去均值 RMS，零振动时不定义。未做标准频率加权。",
        "采样率为 UTC 时间戳唯一间隔中位数的倒数。频域要求无重复、间隔最大偏差≤1%、至少64条、频带严格低于奈奎斯特频率、至少最低频率5个周期。未自动重采样。",
        "Welch：周期 Hann 窗、50% 重叠、每段去均值。频带 RMS：频带内 PSD 求和、乘以频率间隔后开平方，存在频率分辨率与谱泄漏误差；主峰不用于推断故障部件。",
        "滑动 RMS 使用整段去均值信号。未实施标准舒适性等级、Sperling、重力/姿态校正或传感器测量链路验证。",
    ]
    return _finish(report, ai_context, ai_result, selection)


def _append_experiment_ai(story, section, body, heading, small, header, width):
    story.append(_paragraph("五、AI 辅助解读（可选）", heading))
    if section is None:
        story.append(_paragraph("当前没有匹配且完整验证的 AI 解读。本报告仍包含完整的确定性实验结果。", body))
        return
    story.append(_paragraph("AI 生成内容仅解释当前聚合指标，不构成故障、事故、安全、法规或标准舒适等级结论。证据范围校验不证明自然语言推理或因果解释正确。", body))
    story.append(_table([["Provider / Model", "生成时间", "校验状态"],
                         [section["provider"]+" / "+section["model"], section["generated_at"], "available / verified"]],
                        [width*.38, width*.38, width*.24], small, header))
    fingerprint = section["input_fingerprint"]
    story.append(_paragraph(f"AI 输入指纹：{fingerprint[:32]} {fingerprint[32:]}", small))
    analysis = section["analysis"]
    groups = [("核心结论", [analysis["summary"]]), ("主要观察", analysis["observations"]),
              ("可能原因（待核实的假设）", analysis["possible_causes"]),
              ("建议进一步检查", analysis["further_checks"]), ("局限性", analysis["limitations"])]
    for label, claims in groups:
        story.append(_paragraph(label, heading))
        if not claims:
            story.append(_paragraph("未生成此类内容。", small))
        for claim in claims:
            text = (f"建议：{claim['action']}；依据：{claim['reason']}" if "action" in claim else claim["text"])
            if "qualification" in claim:
                text += "（待核实的假设，hypothesis）"
            # Individual paragraphs can split over pages; do not KeepTogether
            # an arbitrarily long model claim with its evidence references.
            story.append(_paragraph(text, body))
            story.append(_paragraph("证据：" + " · ".join(claim["evidence_ids"]), small))
    story.append(_paragraph("AI 引用证据目录", heading))
    rows = [["Evidence ID / 指标", "值 / 单位", "类别 / 轴向 / 批次"]]
    for item in section["evidence"]:
        rows.append([item["evidence_id"]+"\n"+item["name"], f"{item['value']:.8g} {item['unit']}",
                     item["category"]+" / "+item["axis"]+" / "+(", ".join(item["batch_refs"]) or "不适用")])
    story.append(_table(rows, [width*.48, width*.20, width*.32], small, header))


def _drawing(chart, font):
    drawing = Drawing(170 * mm, 72 * mm)
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 45, 25, 420, 150
    plot.data = [chart["points"]]
    plot.lines[0].strokeColor = colors.HexColor("#2563eb")
    if "horizontal_line" in chart:
        level = chart["horizontal_line"]
        plot.data.append([(chart["points"][0][0], level), (chart["points"][-1][0], level)])
        plot.lines[1].strokeColor = colors.HexColor("#ea580c")
        plot.lines[1].strokeDashArray = [4, 3]
    for axis in (plot.xValueAxis, plot.yValueAxis):
        axis.labels.fontName, axis.labels.fontSize = font, 7
        axis.labelTextFormat = "%.4g"
    drawing.add(plot)
    drawing.add(String(250, 3, chart["x_label"], textAnchor="middle", fontName=font, fontSize=8))
    label = Group()
    label.transform = (0, 1, -1, 0, 8, 100)
    label.add(String(0, 0, chart["y_label"], textAnchor="middle", fontName=font, fontSize=8))
    drawing.add(label)
    return drawing


def generate_experiment_pdf(report):
    """An optional AI layout failure falls back to the deterministic document."""
    try:
        return _render_experiment_pdf(report)
    except Exception:
        if isinstance(report, dict) and report.get("ai") is not None:
            fallback = deepcopy(report)
            fallback.pop("report_fingerprint", None)
            fallback["notes"].append("AI 附录无法写入，已省略；确定性实验结果保持完整。")
            try:
                return _render_experiment_pdf(_finish(fallback))
            except Exception:
                pass
        raise RuntimeError("实验报告生成失败，请重试；原有分析结果仍可使用。") from None


def _render_experiment_pdf(report):
    """Render an allowlisted report to bytes; no network, keys or model execution."""
    try:
        regular, bold = _font_names()
        stream = BytesIO()
        doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=18*mm, bottomMargin=20*mm,
                                title=report["title"], author="RailVehicle-Lab")
        body = ParagraphStyle("ExperimentBody", fontName=regular, fontSize=9, leading=14, wordWrap="CJK", spaceAfter=5)
        title = ParagraphStyle("ExperimentTitle", parent=body, fontName=bold, fontSize=18, leading=26, spaceAfter=12)
        heading = ParagraphStyle("ExperimentHeading", parent=body, fontName=bold, fontSize=12, leading=18, spaceBefore=10, keepWithNext=True)
        header = ParagraphStyle("ExperimentHeader", parent=body, textColor=colors.white, fontName=bold, fontSize=8)
        small = ParagraphStyle("ExperimentSmall", parent=body, fontSize=7, leading=10)
        story = [_paragraph(report["title"], title), _paragraph(SAFETY_NOTE, body),
                 _paragraph("一、数据来源与复现记录", heading),
                 _paragraph(f"生成时间：{report['generated_at']}；来源：{report['source']}", body)]
        for key, value in report["identifiers"].items():
            story.append(_paragraph(f"{key}（本地数据库编号）：{value}", body))
        for key, value in report["ranges"].items():
            story.append(_paragraph(f"{key}：{value}", body))
        for key, label in (("input_fingerprint", "输入指纹"), ("report_fingerprint", "报告内容指纹")):
            value = report[key]
            story.append(_paragraph(f"{label}（SHA-256）：{value[:32]} {value[32:]}", small))
        story.append(_paragraph("二、已计算指标与报告事实编号", heading))
        story.append(_paragraph("事实编号仅在本实验报告中追溯指标，不是 AI evidence_id，也不表示完成 AI claim_scope 校验。未定义或不可计算的数值以“不适用/未定义”表示。", small))
        rows = [["报告事实编号 / 指标", "值", "单位"]]
        for fact in report["facts"]:
            value = "不适用/未定义" if fact["value"] is None else format(fact["value"], ".8g")
            rows.append([fact["id"] + "\n" + fact["label"], value, fact["unit"]])
        story.append(_table(rows, [doc.width*.61, doc.width*.24, doc.width*.15], small, header))
        story.append(_paragraph("三、计算口径与局限", heading))
        for note in report["notes"]:
            story.append(_paragraph(note, body))
        story.append(_paragraph("四、实验曲线", heading))
        if not report["charts"]:
            story.append(_paragraph("当前数据未满足频域条件，未绘制频域曲线。", body))
        for chart in report["charts"]:
            threshold_note = "橙色虚线为参考分布学习的模型分数阈值。" if "horizontal_line" in chart else ""
            story.append(KeepTogether([
                _paragraph(chart["title"], heading),
                _drawing(chart, regular),
                _paragraph(f"{threshold_note}原始计算序列 {chart['source_points']} 点；PDF 绘制 {len(chart['points'])} 点，按等间隔抽点并保留端点与全局极值；曲线不代表所有局部变化，统计指标使用完整分析结果。", small),
            ]))
            story.append(Spacer(1, 3*mm))

        _append_experiment_ai(story, report.get("ai"), body, heading, small, header, doc.width)

        def footer(canvas, document):
            canvas.saveState()
            canvas.setFont(regular, 8)
            canvas.drawString(document.leftMargin, 10*mm, "RailVehicle-Lab · 可复现实验报告")
            canvas.drawRightString(A4[0]-document.rightMargin, 10*mm, f"第 {document.page} 页")
            canvas.restoreState()

        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        return stream.getvalue()
    except Exception:
        raise RuntimeError("实验报告生成失败，请重试；原有分析结果仍可使用。") from None
