"""Compare identical solver work with serial and two spawned process schedules."""

from argparse import ArgumentParser
from concurrent.futures import ProcessPoolExecutor
import ctypes
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import sys
from time import monotonic, process_time

import numpy as np

from rail_vehicle.nonlinear_vehicle import WheelRailCase, run_vehicle
from rail_vehicle.wheel_track import template


def peak_working_set():
    if sys.platform != "win32":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [("size",ctypes.c_ulong),("faults",ctypes.c_ulong)] + [
            (name,ctypes.c_size_t) for name in ("peak","current","quotaPeakPaged","quotaPaged","quotaPeakNonPaged",
                                                "quotaNonPaged","pagefile","peakPagefile")]

    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = (ctypes.c_void_p, ctypes.POINTER(Counters),ctypes.c_ulong)
    value = Counters()
    return value.peak if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(value),ctypes.sizeof(value)) else None


def one(case):
    started = process_time()
    answer = run_vehicle(case)
    return answer["q"], {"cpu_s":process_time()-started,"peak_working_set_bytes":peak_working_set(),
                         "evidence":answer["evidence_id"],"solver":answer["solver"]["method"]}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    base = WheelRailCase(duration_s=1.25)
    cases = [replace(base,route=template(mode)) for mode in ("straight","curve")]
    start = monotonic()
    serial = [one(case) for case in cases]
    serial_s = monotonic()-start
    start = monotonic()
    with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context("spawn")) as pool:
        parallel = list(pool.map(one,cases))
    parallel_s = monotonic()-start
    for (x,left),(y,right) in zip(serial,parallel):
        if left["evidence"]!=right["evidence"]:
            raise ValueError("Solver evidence mismatch")
        np.testing.assert_allclose(x,y,rtol=1e-11,atol=1e-12)
    result = {"serial_wall_s":serial_s,"two_worker_wall_s":parallel_s,
              "speedup":serial_s/parallel_s,"parallel_efficiency":serial_s/(2*parallel_s),
              "serial":[m for _,m in serial],"parallel":[m for _,m in parallel],
              "scope":"isolated benchmark, process creation included, same cases and integration precision"}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__=="__main__":
    main()
