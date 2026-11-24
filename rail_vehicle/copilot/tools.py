"""Version 1 of the Copilot tool protocol. Handlers receive only the selected snapshots."""

from dataclasses import asdict, dataclass, field, replace
from functools import cached_property
import json
from time import monotonic

from jsonschema import Draft202012Validator

from rail_vehicle.ai.context import build_single_batch_payload
from rail_vehicle.ai.schemas import collect_evidence_ids
from rail_vehicle.analysis_report import build_analysis_report_data, generate_analysis_report_pdf
from rail_vehicle.anomaly import detect_anomalies_with_anchors
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.bogie import PARTS, PART_DESCRIPTIONS, VIEWS
from rail_vehicle.bogie_replay import TEACHING
from rail_vehicle.copilot.context import Dataset, SOURCE_LABELS
from rail_vehicle.copilot.knowledge import search
from rail_vehicle.data_quality import review_sampling_timestamps
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.event_anchor import anchors_by_evidence
from rail_vehicle.suspension import SCENARIOS, SuspensionCase, simulate
from rail_vehicle.sensor_mapping import registration
from rail_vehicle.sensor_store import active_channels, channel_inventory
from rail_vehicle.sensor_evidence import scope_evidence
from rail_vehicle.timestamp_utils import parse_iso_timestamp
from rail_vehicle.vibration import analyze_vibration, VibrationUnavailableError


@dataclass
class Output:
    text: str
    refs: tuple = ()
    rows: list = field(default_factory=list)
    plot: dict | None = None
    events: list = field(default_factory=list)
    scene: dict | None = None
    pdf: bytes | None = None
    citations: list = field(default_factory=list)
    provenance: dict | None = None


@dataclass
class Work:
    data: Dataset | None
    other: Dataset | None = None
    notes: tuple = ()
    previous_scene: dict | None = None
    verified: dict | None = None
    report_items: list = field(default_factory=list)

    @cached_property
    def anonymous_payload(self):
        return {**self.payload, "vehicle": {**self.payload["vehicle"], "vehicle_type": ""}}

    @cached_property
    def stats(self):
        return analyze_run_data(self.data.rows, self.data.threshold)

    @cached_property
    def detected(self):
        return detect_anomalies_with_anchors(self.stats["samples"], self.data.threshold)

    @cached_property
    def payload(self):
        rows = self.stats["samples"]
        return build_single_batch_payload(
            analysis=self.stats,
            anomalies=self.detected[0],
            vehicle=self.data.vehicle,
            batch_ref="B1",
            selected_start=rows[0]["timestamp"],
            selected_end=rows[-1]["timestamp"],
        )


def series(x, columns, xlabel, ylabel):
    return dict(x=x, columns=columns, xlabel=xlabel, ylabel=ylabel)


def vehicle(w):
    labels = {
        "vehicle_code": "车辆编号",
        "vehicle_type": "车型",
        "mass_kg": "质量（kg）",
        "axle_count": "车轴数",
        "bogie_count": "转向架数",
        "vehicle_length_m": "车长（m）",
    }
    return Output(
        f"{SOURCE_LABELS[w.data.source]} · {w.data.batch['file_name']} · {len(w.data.rows):,} 行",
        rows=[
            {"参数": label, "值": str(w.data.vehicle[k])}
            for k, label in labels.items()
            if w.data.vehicle.get(k) is not None
        ],
    )


def quality(w):
    q = review_sampling_timestamps(r["timestamp"] for r in w.data.rows)
    median = q["interval_median_seconds"]
    text = f"共 {q['sample_count']} 条，重复时间戳 {q['duplicate_timestamp_rows']} 条。"
    if median is not None:
        text += f"采样间隔中位数 {median:g} s。"
    if q["out_of_order_pairs"] or q["long_gap_count"]:
        text += f"按输入行序检查，倒序 {q['out_of_order_pairs']} 处，长间隔 {q['long_gap_count']} 处（超过中位间隔三倍）。"
    return Output(text)


def batch_catalog(w):
    items = w.data.batches or (w.data.batch,)
    return Output(
        "当前车辆在此来源中可见的批次。",
        rows=[
            {"批次": b["file_name"], "编号": b["id"], "记录数": b.get("sample_count", len(w.data.rows))}
            for b in items
        ],
    )


def sensors(w):
    rows = channel_inventory(w.data.sensors)
    for row in rows:
        record = active_channels(w.data.sensors)[row["通道"]]
        space = registration(record["metadata"])
        row["车辆系正方向"] = space["vehicle_direction"]
        row["模型系正方向"] = space["direction"]
        row["模型安装坐标 m"] = space["position"]
    if not rows:
        return Output("当前数据范围没有传感器登记记录。可按 CSV 通道核查异常，安装位置与测量正方向未知。")
    return Output("已读取当前车辆和批次的传感器版本。正方向为对应坐标系的单位向量。"
                  "三维位置用于核查安装记录，部件故障仍需独立证据。", rows=rows)


def sample_window(w, first, count):
    if first > len(w.data.rows):
        raise ValueError("起始样本超出当前数据范围。")
    chosen = w.stats["samples"][first - 1 : first - 1 + count]
    return Output(
        f"{SOURCE_LABELS[w.data.source]} · 当前范围内第 {first} 至 {first + len(chosen) - 1} 个样本，"
        "按 UTC 时刻排序，读数保留源通道值。CSV 原始行号缺失时留空。",
        rows=[
            {"序号": i, "CSV 原始行号": r.get("csv_line"),
             **{k: r[k] for k in ("timestamp", "speed_kmh", "lateral_accel", "vertical_accel")}}
            for i, r in enumerate(chosen, first)
        ],
    )


def statistics(w, axis):
    a = w.stats[axis + "_accel"]
    data = w.data
    origin = data.sensors or dict(file_name=data.batch["file_name"], source_sha256=data.batch.get("source_sha256"),
                                  vehicle_code=data.vehicle.get("vehicle_code"),
                                  batch_id=data.batch["id"] if data.source == "history" else None)
    return Output(
        f"{AXES[axis]}加速度 RMS {a['rms']:.4g} m/s²，绝对峰值 {a['peak_absolute']:.4g} m/s²。",
        (f"metric.accel.{axis}.rms", f"metric.accel.{axis}.peak_absolute"),
        provenance=scope_evidence(origin, axis+"_accel", w.stats["samples"], a, data.threshold, SOURCE_LABELS[data.source]),
    )


def events(w, axis):
    anchors = anchors_by_evidence(w.payload, w.detected[1])
    found = [event for event in w.payload["anomaly_events"] if event["type"] == axis]
    items = [dict(event, anchor=anchors[event["evidence_id"]]) for event in found]
    return Output(
        f"{AXES[axis]}加速度绝对值超过 {w.data.threshold:g} m/s² 的连续区段共 {len(found)} 个。",
        (f"metric.exceedance.{axis}",),
        events=items,
    )


def review(w, axis, ordinal):
    found = events(w, axis).events
    if ordinal > len(found):
        raise ValueError(f"当前范围只有 {len(found)} 个{AXES[axis]}区段。")
    item = found[ordinal - 1]
    return Output(
        f"第 {ordinal} 个{AXES[axis]}区段从 {item['start_timestamp']} 开始，"
        f"共 {item['sample_count']} 条，最大绝对加速度 {item['maximum_absolute_value']:g} m/s²。",
        (item["evidence_id"],),
        events=[item],
    )


def vibration(w, axis):
    try:
        result = analyze_vibration(w.data.rows)
    except VibrationUnavailableError:
        return Output("本机缺少可选振动分析依赖，频谱计算不可用。")
    c = result["channels"][axis + "_accel"]
    text = f"{AXES[axis]}信号"
    text += f"峭度 {c['kurtosis']:.4g}。" if c["kurtosis"] is not None else "在当前样本中未定义峭度。"
    plots = None
    if result["spectral_available"]:
        plots = series(c["frequencies"], {"Welch 功率谱密度": c["psd"]}, "频率（Hz）", "(m/s²)²/Hz")
        plots["fft"] = series(c["fft_hz"], {"FFT 幅值": c["fft_amplitude"]}, "频率（Hz）", "m/s²")
    else:
        text += " ".join(result["issues"])
    return Output(text, plot=plots)


def compare(w):
    if (
        w.other is None
        or w.data.batch["id"] == w.other.batch["id"]
        or w.data.vehicle["id"] != w.other.vehicle["id"]
        or w.data.source != w.other.source
    ):
        raise ValueError("请在数据来源中选择同车的另一批次。")
    rows = compare_run_batches(
        [
            dict(batch_id=d.batch["id"], label=d.batch["file_name"], samples=d.rows)
            for d in (w.data, w.other)
        ]
    )
    return Output(
        "按各自所选样本计算。两批次的线路、速度和传感器条件仍需核对。",
        rows=[
            {
                "批次": r["label"],
                "样本": r["sample_count"],
                "横向 RMS (m/s²)": r["lateral_accel_rms"],
                "垂向 RMS (m/s²)": r["vertical_accel_rms"],
            }
            for r in rows
        ],
    )


def lookup(w, query):
    citations = [asdict(p) for p in search(query, w.notes)]
    return Output(f"本地检索命中 {len(citations)} 条资料。", citations=citations)


def inspect(w, group, view, display, motion):
    known = PART_DESCRIPTIONS.get(group)
    if display in ("isolate", "hide") and not known:
        raise ValueError("隐藏或独立显示需要选择一个具体总成。")
    return Output(
        known or "通用双轴转向架装配示意。",
        scene=dict(group=group, view=view, display=display, motion=motion),
        rows=[{"总成": g, "装配说明": d} for g, d in PART_DESCRIPTIONS.items()]
        if display == "explode"
        else [],
    )


def cases(w):
    return Output("当前可执行的简化计算工况。", rows=[{"编号": k, "工况": v} for k, v in SCENARIOS.items()])


def transport(w, action, seconds):
    if not w.previous_scene:
        raise ValueError("请先在本对话中展示结构或执行悬挂仿真。")
    if action == "reset":
        seconds = 0.0
    return Output(
        f"三维时间轴定位到 {seconds:g} s。" if seconds is not None else
        ("已暂停在当前位置。" if action == "pause" else "已从当前位置播放。"),
        scene={**w.previous_scene, "transport": {"action": action, "seconds": seconds}},
    )


def suspension(w, scenario, stiffness_change, speed, amplitude):
    base = SuspensionCase(scenario, speed_m_s=speed, amplitude_m=amplitude / 1000)
    changed = replace(base, stiffness_n_m=base.stiffness_n_m * (1 + stiffness_change / 100))
    before, after = simulate(base), simulate(changed)
    traces = {
        "基准行程": [1000 * v for v in before["channels"]["travel_m"]],
        "调整后行程": [1000 * v for v in after["channels"]["travel_m"]],
    }
    text = (
        f"{SCENARIOS[scenario]} · 刚度 {base.stiffness / 1000:g} → {changed.stiffness / 1000:g} kN/m。"
        f"同一激励下，最大绝对行程 {max(map(abs, traces['基准行程'])):.3f} → "
        f"{max(map(abs, traces['调整后行程'])):.3f} mm。参数为教学假设。"
    )
    return Output(
        text,
        plot=series(
            [i / after["sample_hz"] for i in range(len(traces["调整后行程"]))],
            traces,
            "时间（s）",
            "行程（mm）",
        ),
        scene={"simulation": after},
    )


def report(w):
    from rail_vehicle.engineering_intake import report_provenance

    rows = w.stats["samples"]
    data = build_analysis_report_data(
        w.data.vehicle,
        w.data.batch,
        w.stats,
        w.detected[0],
        parse_iso_timestamp(rows[0]["timestamp"]),
        parse_iso_timestamp(rows[-1]["timestamp"]),
        replay_rows=w.data.row_span,
        ai_context=w.anonymous_payload if w.verified else None,
        ai_result=w.verified,
        ai_selection_fingerprint=w.data.identity,
        provenance=report_provenance(w.data.sensors, fallback=SOURCE_LABELS[w.data.source],
                                     source_sha256=w.data.batch.get("source_sha256")),
    )
    data["title"] = f"{SOURCE_LABELS[w.data.source]} · {data['title']}"
    notes = []
    for entry in w.report_items:
        tool, output = entry["tool"], entry["result"]
        if tool not in ("quality", "vibration", "review", "compare", "simulate", "knowledge"):
            continue
        lines = [output.text]
        if tool == "compare":
            lines.extend(" · ".join(f"{key} {value}" for key, value in row.items()) for row in output.rows)
        if tool == "knowledge":
            lines.extend(f"{c['title']} [{c['id']}] · {c['source']}" for c in output.citations)
        if output.refs:
            lines.append("证据 " + "、".join(output.refs))
        note = (TOOLS[tool].label, lines)
        if note not in notes:
            notes.append(note)
    data["copilot_notes"] = notes
    pdf = generate_analysis_report_pdf(data)
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("报告未能生成。")
    return Output(
        f"已生成包含 {len(rows)} 条样本的统计、曲线和 {len(w.detected[0]['events'])} 个规则异常区段的 PDF。",
        pdf=pdf,
    )


AXES = {"lateral": "横向", "vertical": "垂向"}


def enum(values):
    return {"type": "string", "enum": list(values)}


def number(low, high):
    return {"type": "number", "minimum": low, "maximum": high}


@dataclass(frozen=True)
class Tool:
    label: str
    handler: object
    properties: dict = field(default_factory=dict)

    @property
    def schema(self):
        return dict(
            type="object",
            properties=self.properties,
            required=list(self.properties),
            additionalProperties=False,
        )


TOOLS = {
    "sensors": Tool("查询传感器、坐标标定和安装来源", sensors),
    "vehicle": Tool("读取当前车辆与批次", vehicle),
    "quality": Tool("检查采样质量", quality),
    "batches": Tool("查询车辆批次目录", batch_catalog),
    "samples": Tool(
        "读取所选范围的样本",
        sample_window,
        {
            "first": {"type": "integer", "minimum": 1, "maximum": 5000},
            "count": {"type": "integer", "minimum": 1, "maximum": 200},
        },
    ),
    "statistics": Tool("计算加速度统计", statistics, {"axis": enum(AXES)}),
    "events": Tool("定位超阈值区段", events, {"axis": enum(AXES)}),
    "review": Tool(
        "复核指定的异常事件",
        review,
        {"axis": enum(AXES), "ordinal": {"type": "integer", "minimum": 1, "maximum": 5000}},
    ),
    "vibration": Tool("计算振动特征与频谱", vibration, {"axis": enum(AXES)}),
    "compare": Tool("对比两批运行数据", compare),
    "knowledge": Tool(
        "检索工程资料", lookup, {"query": {"type": "string", "minLength": 1, "maxLength": 160}}
    ),
    "inspect": Tool(
        "展示三维装配",
        inspect,
        {
            "group": enum(("整架", *PARTS)),
            "view": enum(VIEWS),
            "display": enum(("solid", "ghost", "wire", "isolate", "hide", "explode")),
            "motion": enum(TEACHING),
        },
    ),
    "cases": Tool("查询计算工况", cases),
    "transport": Tool(
        "控制上次三维演示", transport, {
            "action": enum(("play", "pause", "reset")),
            "seconds": {**number(0, 6), "type": ["number", "null"],
                        "description": "用户指定的时间（秒）。未指定时间时传 null 以保留当前播放位置。"},
        }
    ),
    "simulate": Tool(
        "计算悬挂响应对照",
        suspension,
        {
            "scenario": enum(SCENARIOS),
            "stiffness_change": number(-70, 100),
            "speed": number(1, 10),
            "amplitude": number(0, 8),
        },
    ),
    "report": Tool("生成当前范围 PDF", report),
}


def validate_plan(plan):
    if not isinstance(plan, list) or not 1 <= len(plan) <= 8:
        raise ValueError("计划须包含 1 至 8 次工具调用。")
    seen = set()
    for call in plan:
        if (
            not isinstance(call, dict)
            or set(call) != {"tool", "args"}
            or not isinstance(call["tool"], str)
            or call["tool"] not in TOOLS
        ):
            raise ValueError("计划包含未授权的工具。")
        if not Draft202012Validator(TOOLS[call["tool"]].schema).is_valid(call["args"]):
            raise ValueError("工具参数不在支持范围内。")
        identity = json.dumps(call, sort_keys=True, allow_nan=False)
        if identity in seen:
            raise ValueError("计划包含重复调用。")
        seen.add(identity)
    return plan


UNBOUND_TOOLS = frozenset({"knowledge", "inspect", "cases", "simulate", "transport"})


def execute(w, plan, *, cancelled=lambda: False, deadline=None):
    validate_plan(plan)
    if w.data is None and any(step["tool"] not in UNBOUND_TOOLS for step in plan):
        raise ValueError("这项操作需要运行数据。请从「数据来源与附件」选择批次，或使用内置虚构批次体验。")
    for call in plan:
        if cancelled() or (deadline is not None and monotonic() >= deadline):
            raise TimeoutError("任务已停止或超过时间预算。")
        start = monotonic()
        key = call["tool"]
        yield {"status": "running", "tool": key, "args": call["args"]}
        result = TOOLS[key].handler(w, **call["args"])
        if result.refs:
            ids = collect_evidence_ids(w.payload)
            if not set(result.refs) <= ids:
                raise ValueError("工具引用的证据不属于当前计算结果。")
        if cancelled() or (deadline is not None and monotonic() >= deadline):
            raise TimeoutError("任务已停止或超过时间预算。")
        yield {
            "status": "done",
            "tool": key,
            "args": call["args"],
            "ms": round((monotonic() - start) * 1000),
            "result": result,
        }
