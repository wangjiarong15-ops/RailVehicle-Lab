"""Reference comparison with explicit origin, common units, and fixed sample clock."""

from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO, StringIO
from urllib.parse import urlsplit
import csv

import numpy as np

from rail_vehicle.flexible_track import CHANNELS, MODEL, TrackCase, response
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range

LEVELS = {"analytic":"解析验证", "numerical":"数值交叉验证", "external":"外部公开基准",
          "rig":"独立台架记录（来源声明）", "vehicle":"实车记录（来源声明）"}

CATALOG = (
    ("Manchester 整车基准", "https://doi.org/10.1080/00423119808969454",
     "蛇行、悬挂和通过曲线。待配套相同车辆、线路、初值及可授权数字响应。"),
    ("Huddersfield 轮轨接触基准 v3.0",
     "https://research-t4.hud.ac.uk/media/assets/document/research/railwayresearchinsituteofirr/downloads/Contact_Benchmark_ver3.0.pdf",
     "规定 S1002/UIC60 新轮和磨耗型面、横移、摇头和载荷。待核对配套坐标及单位，最高点搜索只覆盖零摇头截面。"),
    ("道岔车辆—轨道联合基准", "https://doi.org/10.1080/00423114.2021.1959038",
     "CC BY 开放论文，多求解器对照。当前独立垂向模型缺少道岔多接触条件。"),
    ("FRA 轨道几何研究", "https://railroads.fra.dot.gov/elibrary/characterization-track-geometry-various-operational-conditions",
     "轨道不平顺的统计参考，需配对独立轮重或车辆时程才能核验响应。"),
    ("Vtech CONTACT", "https://github.com/eve70a/CONTACT",
     "Apache-2.0 开源接触求解器，登记为外部复算候选。实验平台不嵌入该求解器。"),
)


@dataclass(frozen=True)
class Provenance:
    level: str
    title: str
    version: str
    license_note: str
    conditions: str
    url: str = ""

    def __post_init__(self):
        if self.level not in LEVELS or not all(isinstance(value,str) and 0 < len(value.strip()) <= 180
                for value in (self.title,self.version,self.license_note,self.conditions)):
            raise ValueError("请完整填写基准来源、版本、使用权和工况条件。")
        if self.level=="external" or self.url:
            url = urlsplit(self.url)
            if url.scheme!="https" or not url.hostname or url.username or url.password or len(self.url)>400:
                raise ValueError("公开基准须有可追溯的 HTTPS 来源地址。")


def reference_csv(times, values):
    target=StringIO(newline="")
    writer=csv.writer(target)
    writer.writerow(("time_s","value"))
    writer.writerows(zip(times,values,strict=True))
    return target.getvalue().encode()


def read_reference(blob):
    if not isinstance(blob,bytes) or len(blob)>500_000:
        raise ValueError("基准参考 CSV 最大 500 KB。")
    try:
        rows=csv.reader(StringIO(blob.decode("utf-8-sig")),strict=True)
        if next(rows)!=["time_s","value"]:
            raise ValueError
        values=np.asarray([[float(value) for value in row] for row in rows if row])
    except (ValueError, UnicodeError, csv.Error, StopIteration):
        raise ValueError("基准 CSV 需使用 time_s,value 两列。") from None
    if (values.ndim!=2 or values.shape[1]!=2 or not 10<=len(values)<=10000
            or not np.isfinite(values).all() or np.max(abs(values[:,1]))>1e8):
        raise ValueError("基准 CSV 需包含 10 至 10000 个有限、有效的样点。")
    if values[0,0]<0 or not np.all(np.diff(values[:,0])>=1e-6) or values[-1,0]-values[0,0]<.02:
        raise ValueError("基准须为统一起点后的严格递增秒数，覆盖至少 20 ms。")
    return values.T


def compare_reference(case, channel, unit, provenance, reference_blob, tolerance, *, min_coverage=.8):
    if channel not in CHANNELS or unit!=CHANNELS[channel]:
        raise ValueError("请使用所选通道的 SI 单位，参考值不会隐式转换。")
    if not in_range(tolerance,1e-12,1e7):
        raise ValueError("RMSE 门槛须为有效的正数。")
    reference_t, reference_v=read_reference(reference_blob)
    result=response(case)
    t=np.asarray(result["time_s"])
    overlap=(reference_t>=t[0]) & (reference_t<=t[-1])
    coverage=float((min(t[-1],reference_t[-1])-max(t[0],reference_t[0]))/(t[-1]-t[0]))
    if coverage<min_coverage or np.count_nonzero(overlap)<10:
        raise ValueError("共同时间轴覆盖不足 80%。请核对同一起点、时长和时钟单位。")
    stamps, actual=reference_t[overlap],reference_v[overlap]
    intervals=np.diff(stamps)
    if np.max(intervals)>5*np.median(intervals):
        raise ValueError("参考时间轴有大缺口，无法给出可信的整段误差。")
    computed=np.interp(stamps,t,result[channel])
    residual=computed-actual
    rmse=float(np.sqrt(np.mean(residual**2)))
    corr=float(np.clip(np.corrcoef(actual,computed)[0,1],-1,1)) if min(np.std(actual),np.std(computed))>1e-12 else None
    summary={"schema":"rail.validation.v1","model":MODEL,"parameters":asdict(case),"solver":result["solver"],
             "source":asdict(provenance),"reference_sha256":sha256(reference_blob).hexdigest(),
             "channel":channel,"unit":unit,"coverage":coverage,"sample_count":len(stamps),
             "rmse":rmse,"mae":float(np.mean(abs(residual))),"peak_error":float(max(abs(residual))),
             "correlation":corr,"tolerance":tolerance,"within_declared_gate":rmse<=tolerance,
             "scope":"fixed origin, timestamps and one stated component observable"}
    return {**summary,"evidence_id":"benchmark."+sha256(json_text(summary).encode()).hexdigest(),
            "time_s":stamps.tolist(),"reference":actual.tolist(),"response":computed.tolist(),"error":residual.tolist()}


def numerical_reference(case, channel):
    reference=response(case,solver="Radau")
    return (Provenance("numerical","独立积分器 Radau","flexible-track.v1 / Radau",
                       "RailVehicle-Lab 自有可复算合成基准","同一方程、静平衡及激励"),
            reference_csv(reference["time_s"],reference[channel]))


def validation_pdf(report):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    from rail_vehicle.analysis_report import _font_names

    stream=BytesIO()
    canvas=Canvas(stream,pagesize=A4)
    regular,bold=_font_names()
    canvas.setFont(bold,16)
    canvas.drawString(40,794,"RailVehicle-Lab 工程基准对照")
    canvas.setFont(regular,10)
    source=report["source"]
    lines=(f"验证等级  {LEVELS[source['level']]}",f"模型与求解器  {report['model']}  {report['solver']}",
           f"参考来源  {source['title']}",f"版本  {source['version']}",f"使用范围  {source['license_note']}",
           f"工况  {source['conditions']}",f"共同时间覆盖率  {report['coverage']:.1%}",
           f"RMSE  {report['rmse']:.6g} {report['unit']}  /  门槛  {report['tolerance']:.6g}",
           f"MAE  {report['mae']:.6g}  /  峰值绝对误差  {report['peak_error']:.6g} {report['unit']}",
           "证据摘要 "+report["evidence_id"],"参考文件 SHA-256 "+report["reference_sha256"],
           "范围  单一垂向组件、固定时钟及登记通道。现场独立测量状态以资料清单为准。")
    cursor=763
    for line in lines:
        while line:
            limit=len(line)
            while canvas.stringWidth(line[:limit],regular,10)>510:
                limit-=1
            canvas.drawString(40,cursor,line[:limit])
            cursor-=17
            line=line[limit:]
    canvas.save()
    return stream.getvalue()
