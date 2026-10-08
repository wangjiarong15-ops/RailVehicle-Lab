"""Report data assembly and PDF rendering for a single run analysis."""

from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.lineplots import LinePlot


REPORT_TITLE = "轨道车辆运行与动力学分析报告"
MAX_CHART_POINTS = 1200


def _timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value)


def build_analysis_report_data(
    vehicle: dict[str, Any],
    batch: dict[str, Any],
    analysis: dict[str, Any],
    anomalies: dict[str, Any],
    selected_start: datetime,
    selected_end: datetime,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble a stable report payload from the current filtered analysis."""
    samples = analysis["samples"]
    if not samples:
        raise ValueError("没有筛选后的运行数据，无法生成分析报告。")
    if generated_at is None:
        generated_at = datetime.now().astimezone()

    return {
        "title": REPORT_TITLE,
        "generated_at": _timestamp_text(generated_at),
        "vehicle": {
            "vehicle_code": vehicle["vehicle_code"],
            "vehicle_type": vehicle["vehicle_type"],
            "vehicle_length_m": vehicle.get("vehicle_length_m"),
            "mass_kg": vehicle.get("mass_kg"),
            "axle_count": vehicle.get("axle_count"),
            "bogie_count": vehicle.get("bogie_count"),
            "max_speed_kmh": vehicle.get("max_speed_kmh"),
        },
        "batch": {
            "id": batch["id"],
            "file_name": batch["file_name"],
            "imported_at": batch.get("imported_at", ""),
        },
        "filter_range": {
            "start": _timestamp_text(selected_start),
            "end": _timestamp_text(selected_end),
        },
        "data_range": {
            "start": str(samples[0]["timestamp"]),
            "end": str(samples[-1]["timestamp"]),
        },
        "sample_count": analysis["sample_count"],
        "speed": dict(analysis["speed"]),
        "lateral_accel": dict(analysis["lateral_accel"]),
        "vertical_accel": dict(analysis["vertical_accel"]),
        "threshold": dict(analysis["threshold"]),
        "anomaly_events": [dict(event) for event in anomalies["events"]],
        "samples": [dict(sample) for sample in samples],
    }


def _font_names() -> tuple[str, str]:
    """Register an installed Chinese font, embedding it in generated PDFs."""
    regular_name = "RailVehicleChinese"
    bold_name = "RailVehicleChineseBold"
    registered = set(pdfmetrics.getRegisteredFontNames())
    if regular_name in registered:
        return regular_name, bold_name if bold_name in registered else regular_name

    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    regular_candidates = (
        (windows_fonts / "simhei.ttf", 0),
        (windows_fonts / "msyh.ttc", 0),
        (windows_fonts / "simsun.ttc", 0),
    )
    bold_candidates = (
        (windows_fonts / "msyhbd.ttc", 0),
        (windows_fonts / "simsunb.ttc", 0),
    )

    def register_first(name: str, candidates: tuple[tuple[Path, int], ...]) -> bool:
        for path, subfont_index in candidates:
            if not path.is_file():
                continue
            try:
                pdfmetrics.registerFont(
                    TTFont(name, str(path), subfontIndex=subfont_index)
                )
                return True
            except Exception:
                continue
        return False

    if not register_first(regular_name, regular_candidates):
        # ReportLab ships this CJK CID font; readers provide the matching glyphs.
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        regular_name = "STSong-Light"
    if not register_first(bold_name, bold_candidates):
        bold_name = regular_name
    return regular_name, bold_name


def _paragraph(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _table(
    rows: list[list[Any]], widths: list[float], body_style: ParagraphStyle,
    header_style: ParagraphStyle,
) -> Table:
    cells = [
        [_paragraph(cell, header_style if row_index == 0 else body_style) for cell in row]
        for row_index, row in enumerate(rows)
    ]
    table = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9C7D3")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _sample_time_seconds(samples: list[dict[str, Any]]) -> list[float]:
    def parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    first = parse(samples[0]["timestamp"])
    return [(parse(sample["timestamp"]) - first).total_seconds() for sample in samples]


def _chart_drawing(
    samples: list[dict[str, Any]],
    field: str,
    y_label: str,
    color_list: list[Any],
    font_name: str,
) -> Drawing:
    indices = list(range(len(samples)))
    if len(indices) > MAX_CHART_POINTS:
        indices = [
            round(index * (len(samples) - 1) / (MAX_CHART_POINTS - 1))
            for index in range(MAX_CHART_POINTS)
        ]
    relative_seconds = _sample_time_seconds(samples)
    chart_points = [
        (relative_seconds[index], float(samples[index][field])) for index in indices
    ]

    drawing = Drawing(170 * mm, 72 * mm)
    plot = LinePlot()
    plot.x = 39
    plot.y = 22
    plot.width = 425
    plot.height = 154
    plot.data = [chart_points]
    plot.lines[0].strokeColor = color_list[0]
    plot.lines[0].strokeWidth = 1.5
    plot.xValueAxis.valueMin = 0
    plot.xValueAxis.valueMax = max(relative_seconds) or 1
    plot.xValueAxis.labelTextFormat = "%.1f"
    plot.yValueAxis.labelTextFormat = "%.4g"
    for axis in (plot.xValueAxis, plot.yValueAxis):
        axis.labels.fontName = font_name
        axis.labels.fontSize = 7
        axis.strokeColor = colors.HexColor("#718096")
        axis.gridStrokeColor = colors.HexColor("#DCE4EB")
        axis.gridStrokeWidth = 0.4
    drawing.add(plot)
    drawing.add(
        String(
            251,
            1,
            "相对运行时间（秒）",
            textAnchor="middle",
            fontName=font_name,
            fontSize=8,
        )
    )
    drawing.add(
        String(
            5,
            100,
            y_label,
            textAnchor="middle",
            angle=90,
            fontName=font_name,
            fontSize=8,
        )
    )
    return drawing


def generate_analysis_report_pdf(report: dict[str, Any]) -> bytes:
    """Render a complete single-batch report PDF into memory."""
    try:
        regular_font, bold_font = _font_names()
        stream = BytesIO()
        page_width, _ = A4
        document = SimpleDocTemplate(
            stream,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=19 * mm,
            bottomMargin=19 * mm,
            title=REPORT_TITLE,
            author="RailVehicle-Lab",
        )
        stylesheet = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "RailTitle", parent=stylesheet["Title"], fontName=bold_font,
            fontSize=20, leading=28, textColor=colors.HexColor("#17324D"),
            alignment=TA_CENTER, spaceAfter=8 * mm,
        )
        heading_style = ParagraphStyle(
            "RailHeading", parent=stylesheet["Heading2"], fontName=bold_font,
            fontSize=13, leading=18, textColor=colors.HexColor("#1F5A82"),
            spaceBefore=4 * mm, spaceAfter=2.5 * mm, keepWithNext=True,
        )
        body_style = ParagraphStyle(
            "RailBody", parent=stylesheet["BodyText"], fontName=regular_font,
            fontSize=8.5, leading=12, textColor=colors.HexColor("#273746"),
        )
        small_style = ParagraphStyle(
            "RailSmall", parent=body_style, fontSize=7.2, leading=9,
        )
        header_style = ParagraphStyle(
            "RailTableHeader", parent=body_style, fontName=bold_font,
            fontSize=7.7, leading=10, textColor=colors.white,
        )
        label_style = ParagraphStyle(
            "RailLabel", parent=body_style, fontName=bold_font,
        )
        content_width = page_width - document.leftMargin - document.rightMargin
        story: list[Any] = [
            _paragraph(REPORT_TITLE, title_style),
            _paragraph(f"报告生成时间：{report['generated_at']}", body_style),
            Spacer(1, 4 * mm),
        ]

        vehicle = report["vehicle"]
        batch = report["batch"]
        story.append(_paragraph("一、车辆与分析信息", heading_style))
        story.append(
            _table(
                [
                    ["项目", "内容", "项目", "内容"],
                    ["车辆编号", vehicle["vehicle_code"], "车辆型号", vehicle["vehicle_type"]],
                    ["车辆长度（m）", vehicle.get("vehicle_length_m", "未记录"), "车辆质量（kg）", vehicle.get("mass_kg", "未记录")],
                    ["轴数", vehicle.get("axle_count", "未记录"), "转向架数量", vehicle.get("bogie_count", "未记录")],
                    ["最高运行速度（km/h）", vehicle.get("max_speed_kmh", "未记录"), "分析批次", f"{batch['file_name']}（#{batch['id']}）"],
                    ["筛选开始时间", report["filter_range"]["start"], "筛选结束时间", report["filter_range"]["end"]],
                    ["实际数据开始时间", report["data_range"]["start"], "实际数据结束时间", report["data_range"]["end"]],
                    ["数据点数量", report["sample_count"], "批次导入时间", batch.get("imported_at", "未记录")],
                ],
                [content_width * 0.18, content_width * 0.32, content_width * 0.18, content_width * 0.32],
                small_style, header_style,
            )
        )

        story.append(_paragraph("二、运行速度统计", heading_style))
        speed = report["speed"]
        story.append(
            _table(
                [
                    ["指标", "结果"],
                    ["平均速度（km/h）", f"{speed['mean']:.4f}"],
                    ["最大速度（km/h）", f"{speed['maximum']:.4f}"],
                ],
                [content_width * 0.5, content_width * 0.5],
                body_style, header_style,
            )
        )

        story.append(_paragraph("三、加速度统计", heading_style))
        acceleration_rows = [["统计指标", "横向加速度（m/s^2）", "垂向加速度（m/s^2）"]]
        for key, label in (
            ("minimum", "最小值"),
            ("maximum", "最大值"),
            ("mean", "平均值"),
            ("rms", "RMS"),
            ("peak_absolute", "绝对峰值"),
        ):
            acceleration_rows.append(
                [
                    label,
                    f"{report['lateral_accel'][key]:.6f}",
                    f"{report['vertical_accel'][key]:.6f}",
                ]
            )
        story.append(
            _table(
                acceleration_rows,
                [content_width * 0.30, content_width * 0.35, content_width * 0.35],
                body_style, header_style,
            )
        )

        threshold = report["threshold"]
        story.append(_paragraph("四、阈值统计与异常事件", heading_style))
        story.append(
            _paragraph(
                f"加速度绝对值阈值：|a| > {threshold['value']:.4f} m/s^2。",
                body_style,
            )
        )
        story.append(
            _table(
                [
                    ["统计范围", "超阈值样本数", "样本占比"],
                    ["横向加速度", threshold["lateral"]["count"], f"{threshold['lateral']['percentage']:.2f}%"],
                    ["垂向加速度", threshold["vertical"]["count"], f"{threshold['vertical']['percentage']:.2f}%"],
                    ["任一方向（去重）", threshold["either"]["count"], f"{threshold['either']['percentage']:.2f}%"],
                ],
                [content_width * 0.48, content_width * 0.27, content_width * 0.25],
                body_style, header_style,
            )
        )
        events = report["anomaly_events"]
        story.append(Spacer(1, 2 * mm))
        story.append(_paragraph(f"异常事件数量：{len(events)}", body_style))
        event_rows = [["异常类型", "开始时间", "结束时间", "持续时间（秒）", "最大绝对值（m/s^2）", "样本数"]]
        for event in events:
            event_rows.append(
                [
                    event["type"], event["start_timestamp"], event["end_timestamp"],
                    f"{event['duration_seconds']:.3f}",
                    f"{event['maximum_absolute_value']:.4f}", event["sample_count"],
                ]
            )
        if not events:
            event_rows.append(["无统计异常事件", "-", "-", "-", "-", "0"])
        story.append(
            _table(
                event_rows,
                [content_width * ratio for ratio in (0.23, 0.20, 0.20, 0.13, 0.15, 0.09)],
                small_style, header_style,
            )
        )
        story.append(
            Spacer(1, 2 * mm)
        )
        story.append(
            _paragraph(
                "本报告为运行数据的基础动力学统计分析及规则异常检测结果，不构成法规合规、安全结论或车辆故障诊断。",
                small_style,
            )
        )

        story.append(_paragraph("五、运行曲线", heading_style))
        samples = report["samples"]
        if len(samples) > MAX_CHART_POINTS:
            story.append(
                _paragraph(
                    f"每张曲线最多等距显示 {MAX_CHART_POINTS} 个点以保持 PDF 清晰度；统计结果仍使用全部 {len(samples)} 条筛选后数据。",
                    small_style,
                )
            )
        chart_specs = (
            ("speed_kmh", "速度 - 时间曲线", "速度（km/h）"),
            ("lateral_accel", "横向加速度 - 时间曲线", "横向加速度（m/s^2）"),
            ("vertical_accel", "垂向加速度 - 时间曲线", "垂向加速度（m/s^2）"),
        )
        for field, title, y_label in chart_specs:
            chart = _chart_drawing(
                samples, field, y_label, [colors.HexColor("#1779A8")], regular_font
            )
            story.append(
                KeepTogether(
                    [
                        _paragraph(title, label_style),
                        chart,
                    ]
                )
            )
            story.append(Spacer(1, 2 * mm))

        def footer(canvas: Any, doc: Any) -> None:
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#B9C7D3"))
            canvas.setLineWidth(0.5)
            canvas.line(doc.leftMargin, 13 * mm, page_width - doc.rightMargin, 13 * mm)
            canvas.setFont(regular_font, 8)
            canvas.setFillColor(colors.HexColor("#607080"))
            canvas.drawString(doc.leftMargin, 8 * mm, "RailVehicle-Lab · 分析报告")
            canvas.drawRightString(page_width - doc.rightMargin, 8 * mm, f"第 {doc.page} 页")
            canvas.restoreState()

        document.build(story, onFirstPage=footer, onLaterPages=footer)
        result = stream.getvalue()
        if not result.startswith(b"%PDF-"):
            raise ValueError("PDF 文件头校验失败。")
        return result
    except Exception as exc:
        raise RuntimeError(f"分析报告 PDF 生成失败：{exc}") from exc
