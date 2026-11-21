"""Bounded local data snapshots. Local identifiers never come from model arguments."""

from dataclasses import dataclass, field
from hashlib import sha256
import csv
import io
import json
import math

from rail_vehicle.run_data import validate_run_csv


MAX_ROWS = 5000
SOURCE_LABELS = {"demo": "内置虚构数据", "history": "历史导入数据", "upload": "会话 CSV 附件"}


@dataclass
class Dataset:
    source: str
    vehicle: dict
    batch: dict
    rows: list
    threshold: float = 1.0
    row_span: tuple | None = None
    replay_scope: object = None
    batches: tuple = ()
    preferred_event: str | None = None
    sensors: dict = field(default_factory=dict)
    identity: str = field(init=False)

    def __post_init__(self):
        if self.source not in SOURCE_LABELS or not 0 < len(self.rows) <= MAX_ROWS:
            raise ValueError(f"请选择 1 至 {MAX_ROWS:,} 行数据；较大批次可先在运行分析中缩小时间范围。")
        if not math.isfinite(self.threshold) or self.threshold < 0:
            raise ValueError("阈值须为非负有限数。")
        fields = [self.source, self.vehicle, self.batch, self.threshold, self.row_span, self.rows]
        if self.sensors:
            fields.append(self.sensors)
        self.identity = sha256(
            json.dumps(fields, sort_keys=True, default=str, allow_nan=False).encode()
        ).hexdigest()


def csv_datasets(content, filename, threshold):
    if len(content) > 2_000_000:
        raise ValueError("会话 CSV 上限为 2 MB。")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        codes = set()
        for i, row in enumerate(reader, 1):
            if i > MAX_ROWS:
                raise ValueError(f"会话 CSV 最多包含 {MAX_ROWS:,} 行。")
            codes.add((row.get("vehicle_id") or "").strip())
    except (UnicodeError, csv.Error):
        raise ValueError("请上传 UTF-8 编码的运行数据 CSV。") from None
    vehicles = [
        dict(id=i, vehicle_code=code, vehicle_type="CSV 附件・车型未提供")
        for i, code in enumerate(sorted(codes), 1)
    ]
    validated = validate_run_csv(content, vehicles)
    if not validated.is_valid:
        raise ValueError("CSV 校验失败，请核对列名、车辆编号、时间、速度及加速度。")
    groups = {v["id"]: [] for v in vehicles}
    for row in validated.iter_rows():
        groups[row["vehicle_id"]].append(row)
    digest = sha256(content).hexdigest()
    return {
        v["vehicle_code"]: Dataset(
            "upload", v, dict(id=0, file_name=filename[:120], source_sha256=digest),
            groups[v["id"]], threshold
        )
        for v in vehicles
    }
