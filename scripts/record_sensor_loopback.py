"""Capture a declared local WebSocket stream to the workbench's JSONL format."""

from argparse import ArgumentParser
from pathlib import Path

from rail_vehicle.stream_loopback import LoopbackCapture


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("url",help="Local sensor bridge ws://127.0.0.1:port endpoint")
    parser.add_argument("output",type=Path,help="New JSONL recording")
    args=parser.parse_args()
    if args.output.exists():
        parser.error("请选择不存在的输出文件。")
    source=LoopbackCapture()
    try:
        for _ in range(12):
            source.receive(args.url)
            if source.complete:
                break
        if source.record is None or not source.record.packets:
            raise ValueError("没有收到具备声明的设备记录。")
        with args.output.open("xb") as stream:
            stream.write(source.record.recording())
    except (OSError,ValueError) as error:
        parser.error(str(error))
    if source.complete:
        print(f"已录制 {len(source.record.packets)} 条。")
    else:
        print(f"已保存 {len(source.record.packets)} 条部分记录。未收到完整结束确认。")
    return 0 if source.complete else 1


if __name__=="__main__":
    raise SystemExit(main())
