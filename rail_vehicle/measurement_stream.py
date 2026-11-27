"""Bounded, time-aligned acquisition of versioned SI engineering channels."""

from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import groupby
from math import isfinite
import csv
import io
import json
import re

from rail_vehicle.virtual_trials import json_text

STEP_US = 20_000
MAX_SAMPLES = 3000
CHANNEL_UNITS = {
    "force": {"N": 1., "kN": 1000.},
    "displacement": {"m": 1., "mm": .001},
    "acceleration": {"m/s2": 1., "g": 9.80665},
}
FIELDS = ("device", "seq", "timestamp_us", "value")
SCHEMA = "rail.acquisition.v1"


@dataclass(frozen=True)
class Device:
    device: str
    channel: str
    unit: str
    sign: int
    gain: float
    bias: float
    clock_offset_us: int
    calibration: str
    timing: str

    def __post_init__(self):
        if (not isinstance(self.device,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}",self.device)
                or self.channel not in CHANNEL_UNITS or self.unit not in CHANNEL_UNITS[self.channel]
                or type(self.sign) is not int or self.sign not in (-1,1)
                or type(self.gain) not in (int,float) or not 0 < self.gain <= 100
                or type(self.bias) not in (int,float) or not isfinite(self.bias) or abs(self.bias)>100000
                or type(self.clock_offset_us) is not int or abs(self.clock_offset_us)>2_000_000
                or self.calibration not in ("declared","reviewed","unknown")
                or self.timing not in ("common_trigger","unknown")):
            raise ValueError("设备编号、垂向通道、单位、标定或时基字段无效。")

    def convert(self, value):
        return self.sign*self.gain*(value-self.bias)*CHANNEL_UNITS[self.channel][self.unit]


@dataclass(frozen=True)
class Packet:
    device: str
    seq: int
    timestamp_us: int
    value: float

    def __post_init__(self):
        if (not isinstance(self.device,str) or type(self.seq) is not int or not 0<=self.seq<2**32
                or type(self.timestamp_us) is not int or not 0<=self.timestamp_us<2**53
                or type(self.value) not in (int,float) or not isfinite(self.value) or abs(self.value)>1e9):
            raise ValueError("采样号、微秒时间戳或传感器值无效。")


class Acquisition:
    """Single declared 50 Hz common trigger with chronological packet admission."""

    def __init__(self, devices, origin_us, source):
        self.devices = {item.device:item for item in devices}
        if (source not in ("simulated","declared_field","declared_test","unknown")
                or not 1<=len(devices)<=3 or len(self.devices)!=len(devices)
                or len({item.channel for item in devices})!=len(devices)
                or type(origin_us) is not int or not 0<=origin_us<2**53):
            raise ValueError("设备、数据来源或触发时刻无效。每个工程通道只绑定一个设备。")
        self.origin=origin_us
        self.source=source
        self.counters=dict(accepted=0,missing_sequence=0,repeated=0,late=0,unsynchronized=0,unregistered=0,invalid_range=0)
        self.packets=[]
        self.samples={}
        self.last={}

    def push(self, packet):
        if len(self.packets)>=MAX_SAMPLES*3:
            raise ValueError("当前记录已达到会话缓冲上限。请先导出再开启新会话。")
        self.packets.append(packet)
        device=self.devices.get(packet.device)
        if not device:
            self.counters["unregistered"]+=1
            return
        last=self.last.get(packet.device)
        if last and packet.seq<=last.seq:
            self.counters["repeated" if packet==last else "late"]+=1
            return
        if last:
            self.counters["missing_sequence"]+=packet.seq-last.seq-1
        self.last[packet.device]=packet
        delta=packet.timestamp_us-device.clock_offset_us-self.origin
        tick=(delta+STEP_US//2)//STEP_US
        if (device.timing!="common_trigger" or not 0<=tick<MAX_SAMPLES or abs(delta-tick*STEP_US)>2500
                or (last and packet.timestamp_us<=last.timestamp_us)):
            self.counters["unsynchronized"]+=1
            return
        value=device.convert(packet.value)
        bound={"force":2e6,"acceleration":250.,"displacement":.5}[device.channel]
        if abs(value)>bound or device.calibration=="unknown":
            self.counters["invalid_range"]+=1
            return
        frame=self.samples.setdefault(tick,{})
        if device.channel in frame:
            self.counters["repeated"]+=1
            return
        frame[device.channel]=value
        self.counters["accepted"]+=1

    def declaration(self):
        return {"schema":SCHEMA,"source":self.source,"origin_us":self.origin,
                "devices":[asdict(device) for device in self.devices.values()]}

    def recording(self):
        lines=[json_text(self.declaration()),*(json_text(asdict(packet)) for packet in self.packets)]
        return ("\n".join(lines)+"\n").encode()

    @property
    def evidence_id(self):
        return "stream."+sha256(self.recording()).hexdigest()


def read_recording(blob):
    if not isinstance(blob,bytes) or len(blob)>1_500_000:
        raise ValueError("原始记录须小于 1.5 MB。")
    try:
        lines=iter(blob.decode("utf-8-sig").splitlines())
        result=read_declaration(json.loads(next(lines)))
        for line in lines:
            result.push(Packet(**json.loads(line)))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, StopIteration, RecursionError):
        raise ValueError("无法读取有版本的 JSONL 设备记录。") from None
    return result


def read_declaration(header):
    if not isinstance(header,dict) or header.get("schema")!=SCHEMA or set(header)!={"schema","source","origin_us","devices"}:
        raise ValueError("设备声明的版本或字段无效。")
    return Acquisition(tuple(Device(**spec) for spec in header["devices"]),header["origin_us"],header["source"])


def read_packet_csv(blob, devices, origin, source):
    if not isinstance(blob,bytes) or len(blob)>700_000:
        raise ValueError("设备 CSV 须小于 700 KB。")
    try:
        result=Acquisition(tuple(devices),origin,source)
        rows=csv.reader(io.StringIO(blob.decode("utf-8-sig")),strict=True)
        if tuple(next(rows))!=FIELDS:
            raise ValueError
        for device,seq,stamp,value in rows:
            result.push(Packet(device,int(seq),int(stamp),float(value)))
    except (ValueError, StopIteration,csv.Error):
        raise ValueError("设备 CSV 需按 device,seq,timestamp_us,value 登记原始值。") from None
    return result


class Replay:
    """A reconnectable cursor over timestamped simulation packets."""

    def __init__(self, target, packets):
        self.target=target
        self.groups=tuple((tick,tuple(group)) for tick,group in groupby(packets,key=lambda item:item[0]))
        self.cursor=0
        self.connected=False

    def poll(self, budget=10):
        if self.connected:
            batch=self.groups[self.cursor:self.cursor+budget]
            for _, group in batch:
                for _, packet in group:
                    self.target.push(packet)
                self.cursor+=1
            if self.cursor==len(self.groups):
                self.connected=False
        return self.cursor
