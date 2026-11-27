"""Rigid, zero-yaw, cross-sectional wheel–rail geometry in signed SI coordinates."""

from dataclasses import dataclass
from hashlib import sha256
import csv
import io

import numpy as np


@dataclass(frozen=True)
class Profile:
    transverse_m: tuple
    height_m: tuple
    sha256: str

    def spline(self):
        from scipy.interpolate import PchipInterpolator

        return PchipInterpolator(self.transverse_m, self.height_m, extrapolate=False)


def read_profile(blob: bytes) -> Profile:
    if not isinstance(blob, bytes) or len(blob) > 256_000:
        raise ValueError("型面 CSV 最大 256 KB。")
    try:
        rows = csv.reader(io.StringIO(blob.decode("utf-8-sig")), strict=True)
        head = next(rows)
        scales = {("y_m", "z_m"): 1., ("y_mm", "z_mm"): .001}
        factor = scales[tuple(head)]
        points = np.asarray([[float(value)*factor for value in row] for row in rows if row])
    except (ValueError, UnicodeError, csv.Error, StopIteration, KeyError):
        raise ValueError("型面 CSV 表头应为 y_mm,z_mm 或 y_m,z_m，数据应为两列有限数值。") from None
    if (points.ndim != 2 or points.shape[1] != 2 or not 4 <= len(points) <= 3000
            or not np.isfinite(points).all() or np.max(abs(points)) > 1):
        raise ValueError("型面需要 4 至 3000 个位于 ±1 m 内的坐标点。")
    spacing = np.diff(points[:, 0])
    if np.any(spacing < 1e-6) or np.any(spacing > .04):
        raise ValueError("横坐标须由内向外递增。拒绝重复、倒序和大于 40 mm 的间隔。")
    if np.any(abs(np.diff(points[:, 1])/spacing) > 12):
        raise ValueError("局部斜率或跳变超出单值截面搜索范围。请检查坐标或拆分连续区段。")
    return Profile(tuple(points[:, 0]), tuple(points[:, 1]), sha256(blob).hexdigest())


class Pair:
    """Search the maximum rail minus wheel height with wheelset roll fixed to zero."""

    def __init__(self, wheel: Profile, rail: Profile, nominal_radius_m=.46, flange_boundary_m=-.024):
        if (not np.isfinite([nominal_radius_m, flange_boundary_m]).all()
                or not .25 <= nominal_radius_m <= .7 or not wheel.transverse_m[0] < flange_boundary_m < 0
                or not wheel.transverse_m[0] < 0 < wheel.transverse_m[-1]):
            raise ValueError("轮面须覆盖名义滚圆横坐标零点和所声明的轮缘界线。")
        self.wheel, self.rail = wheel, rail
        self.wheel_curve, self.rail_curve = wheel.spline(), rail.spline()
        self.wheel_derivatives = tuple(self.wheel_curve.derivative(i) for i in (1, 2, 3))
        self.rail_derivatives = tuple(self.rail_curve.derivative(i) for i in (1, 2, 3))
        self.nominal_radius = nominal_radius_m
        self.flange_boundary = flange_boundary_m

    def contact(self, shift):
        if not np.isfinite(shift) or abs(shift) > .03:
            raise ValueError("单轮横移须在 ±30 mm 内。")
        low = max(self.rail.transverse_m[0], self.wheel.transverse_m[0]+shift)
        high = min(self.rail.transverse_m[-1], self.wheel.transverse_m[-1]+shift)
        if high-low < .004:
            raise ValueError("轮轨型面有效重叠不足 4 mm。")
        segments = np.unique(np.clip(np.r_[self.rail.transverse_m, np.asarray(self.wheel.transverse_m)+shift], low, high))
        middles = (segments[:-1]+segments[1:])/2
        slopes = [rail(middles)-wheel(middles-shift)
                  for rail, wheel in zip(self.rail_derivatives, self.wheel_derivatives, strict=True)]
        points = [segments]
        for mid, half, (c, b, twice_a) in zip(middles, np.diff(segments)/2, np.asarray(slopes).T, strict=True):
            # On this interval the derivative is a quadratic. Normalize the variable to [-1, 1].
            coeff = np.trim_zeros([twice_a*half**2/2, b*half, c], "f")
            if len(coeff) > 1:
                roots = np.roots(coeff)
                points.append(np.asarray([mid+half*u.real for u in roots if abs(u.imag) < 1e-10 and abs(u.real) < 1]))
            elif len(coeff) == 0:
                points.append(np.asarray([mid]))
        candidates = np.unique(np.concatenate(points))
        wheel_positions = np.clip(candidates-shift, self.wheel.transverse_m[0], self.wheel.transverse_m[-1])
        height = self.rail_curve(candidates)-self.wheel_curve(wheel_positions)
        best = int(np.argmax(height))
        pos = float(candidates[best])
        wheel_u = float(wheel_positions[best])
        tied = np.any((height >= height[best]-1e-7) & (abs(candidates-pos) > .001))
        at_edge = min(pos-low, high-pos) <= 1e-7
        return {"rail_y_m": pos, "rail_z_m": float(self.rail_curve(pos)), "wheel_y_m": wheel_u,
                "wheel_vertical_shift_m": float(height[best]),
                "radius_m": self.nominal_radius-float(self.wheel_curve(wheel_u)-self.wheel_curve(0.)),
                "angle_deg": float(np.degrees(np.arctan(self.rail_derivatives[0](pos)))),
                "status": "edge" if at_edge else "ambiguous" if tied else
                          "flange" if wheel_u <= self.flange_boundary else "tread"}

    def scan(self, shifts):
        if not 2 <= len(shifts) <= 181 or not np.all(np.diff(shifts) > 0):
            raise ValueError("横移扫描需要 2 至 181 个递增位置。")
        rows = []
        for shift in shifts:
            left, right = self.contact(float(shift)), self.contact(-float(shift))
            delta = left["radius_m"]-right["radius_m"]
            rows.append({"shift_m": float(shift), "left": left, "right": right, "delta_radius_m": delta,
                         "secant_conicity": delta/(2*shift) if shift and left["status"]==right["status"]=="tread" else None})
        return rows

    def local_conicity(self, span=.002):
        rows = self.scan(np.linspace(-span, span, 9))
        if any(row[side]["status"] != "tread" for row in rows for side in ("left", "right")):
            raise ValueError("中心邻域含边界、轮缘或近等高候选，无法生成单点等效锥度。")
        shift = np.array([row["shift_m"] for row in rows])
        radius = np.array([row["delta_radius_m"] for row in rows])
        gain = (shift @ radius)/(2*(shift @ shift))
        residual = float(np.max(abs(radius-2*gain*shift)))
        if not 0 <= gain <= .35 or residual > max(5e-6, .08*np.max(abs(radius))):
            raise ValueError("中心邻域的滚动半径差不适合现有线性等效锥度。")
        return {"conicity": float(gain), "residual_m": residual, "span_m": span,
                "wheel_sha256": self.wheel.sha256, "rail_sha256": self.rail.sha256}


def sample_profiles(worn=False):
    """Synthetic monotone tread and convex rail head, with independent closed-form contact."""
    transverse = np.linspace(-.035, .035, 71)
    wheel = (.075 if worn else .10)*transverse
    rail = -(transverse**2)/(.40 if worn else .32)

    def encode(height):
        return ("y_mm,z_mm\n"+"\n".join(f"{s*1000:.6f},{z*1000:.9f}" for s,z in zip(transverse,height))).encode()

    return encode(wheel), encode(rail)
