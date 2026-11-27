"""Versioned dispatch and a shared, simulated vertical test input for model comparison."""

from dataclasses import replace
from math import cos, tau

from rail_vehicle import coupled_vehicle as linear
from rail_vehicle import nonlinear_vehicle as wheelrail
from rail_vehicle.wheel_track import template


def model_version(case):
    if isinstance(case,wheelrail.WheelRailCase):
        return wheelrail.MODEL
    if isinstance(case,linear.CoupledCase):
        return linear.MODEL
    raise ValueError("无法识别该动力学工况版本。")


def solve(case,precision="standard"):
    model=model_version(case)
    if model==wheelrail.MODEL:
        return wheelrail.run_vehicle(case,accuracy=precision)
    if precision not in ("standard","refined"):
        raise ValueError("线性模型支持常规和精细积分。")
    return linear.simulate(case,substeps=5 if precision=="standard" else 10,include_modes=False)


def compare_common_vertical():
    """Compute both models on the same straight, symmetric, localized 2 mm rail pulse."""
    profile=tuple((i/4,(1-cos(tau*i/64))/2 if i not in (0,64) else 0.) for i in range(65))
    old=linear.CoupledCase(track="custom",vertical_m=.002,lateral_m=0.,duration_s=3,
                          profile=tuple((s,0.,height) for s,height in profile))
    track=replace(template("straight",left=0.,right=0.,align=0.),
                  profile=tuple((s,0.,.002*height,.002*height) for s,height in profile))
    new=wheelrail.WheelRailCase(route=track,disturbance_m=0.)
    a,b=solve(old),solve(new)
    return ({"model":model_version(case),"time_s":result["time_s"],
             "body_z_accel":[row[0 if isinstance(case,linear.CoupledCase) else 1] for row in result["ddq"]]}
            for case,result in ((old,a),(new,b)))
