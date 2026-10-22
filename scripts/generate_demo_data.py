"""Create fictional CSV batches for a local RailVehicle-Lab demonstration."""

import argparse
from pathlib import Path

from rail_vehicle.demo_data import DEMO_SAMPLE_COUNT, DEMO_VEHICLE_CODE, generate_demo_csvs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/demo"),
        help="输出目录（默认：data/demo；不会覆盖已有演示文件）",
    )
    args = parser.parse_args()
    try:
        paths = generate_demo_csvs(args.output_dir)
    except (FileExistsError, OSError) as exc:
        parser.error(str(exc))

    print("已生成虚构演示数据，不代表真实车辆运行记录。")
    print(f"车辆编号：{DEMO_VEHICLE_CODE}；每个批次 {DEMO_SAMPLE_COUNT} 条记录。")
    for path in paths:
        print(path)
    print("请先在平台登记上述车辆编号，再导入两个 CSV 文件。")


if __name__ == "__main__":
    main()
