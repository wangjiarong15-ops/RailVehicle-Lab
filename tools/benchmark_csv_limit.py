"""Validate a near-limit fictional CSV in memory; never writes a database.

Run from the repository root: python -m tools.benchmark_csv_limit
"""
import argparse
import math
import time
from rail_vehicle.run_data import MAX_CSV_SIZE_BYTES, validate_run_csv
from tools.benchmark_replay import windows_memory_mb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mib", type=float, default=49.0,
                        help="目标大小，必须大于 0 且不超过项目 CSV 上限")
    args = parser.parse_args()
    if not math.isfinite(args.mib) or args.mib <= 0 or args.mib > MAX_CSV_SIZE_BYTES / 1048576:
        parser.error("目标大小必须大于 0 且不超过 CSV 上限")
    header = b"timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
    # Fixed timestamp intentionally stresses duplicate-time validation.  This
    # synthetic stress input is not representative of train measurements.
    line = b"2026-01-01T00:00:00Z,DEMO-001,60,0.1,0.2\n"
    target = int(args.mib * 1024 * 1024)
    if target < len(header) + len(line):
        parser.error("目标大小不足以包含表头和一条记录")
    count = (target - len(header)) // len(line)
    content = header + line * count
    start = time.perf_counter()
    result = validate_run_csv(content, [{"id": 1, "vehicle_code": "DEMO-001"}])
    elapsed = time.perf_counter() - start
    if (not result.is_valid or result.source_row_count != count or
            result.sampling_summary["duplicate_timestamp_rows"] != count - 1):
        raise RuntimeError("虚构 CSV 校验结果与预期不一致")
    print(f"内存中虚构 CSV: {len(content) / 1048576:.2f} MiB；样本: {count:,}")
    print(f"校验: {elapsed:.3f} s；重复时间额外记录: {result.sampling_summary['duplicate_timestamp_rows']:,}")
    # Check the 50 MiB boundary without writing any file or database.
    oversized = content + b" " * (MAX_CSV_SIZE_BYTES - len(content) + 1)
    rejected = validate_run_csv(oversized, [{"id": 1, "vehicle_code": "DEMO-001"}])
    if rejected.is_valid or rejected.source_row_count != 0:
        raise RuntimeError("超过大小上限的内容没有被拒绝")
    print("超过 50 MiB: 正确拒绝")
    memory = windows_memory_mb()
    if memory:
        print(f"本进程工作集 / 累计峰值: {memory[0]:.1f} / {memory[1]:.1f} MiB")


if __name__ == "__main__":
    main()
