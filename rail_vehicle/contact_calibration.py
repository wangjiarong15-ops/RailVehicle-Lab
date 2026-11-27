"""Reproducible effective contact calibration, separate from vehicle defaults."""

from hashlib import sha256
from math import sqrt
import json
from pathlib import Path
import re

import numpy as np

from rail_vehicle.manchester_contact import LOADED_SHA, REFERENCE, REVISION
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range


SCHEMA = "rail.contact-compliance.v1"
NONHZ_SHA = "4da5b73d194fc47b3d9181346675a7674a532c353886bc56de89b328973750c6"
NONHZ_REFERENCE = REFERENCE.replace("contact_mbench", "contact_nonhz")
DATA = Path(__file__).resolve().parent / "assets" / "contact_validation" / "contact-reference.json"


def _pinned(blob, expected):
    if not isinstance(blob, bytes) or len(blob) > 250_000:
        raise ValueError("参考输出须在 250 KB 内。")
    normalized = blob.replace(b"\r\n", b"\n")
    if sha256(normalized).hexdigest() != expected:
        raise ValueError("参考输出摘要与登记版本不一致。")
    return normalized.decode("ascii")


def _line(text, headers):
    return list(map(float, re.search(headers + r"[^\n]*\n[ \t]+([^\n]+)", text)[1].split()))


def manchester_points(blob):
    """Keep CONTACT track, wheel and patch quantities in separate fields."""
    text = _pinned(blob, LOADED_SHA)
    cases = re.split(r"Case (\d+) for w/r contact on result element (\d+)", text)
    result, setups = [], {}
    for case, element, block in zip(cases[1::3], cases[2::3], cases[3::3]):
        nr = re.findall(r"\d+, NR,\s+z_ws, Fz:\s*(\S+)\s+(\S+), dFz/dz:\s*(\S+)", block)
        initial, _, _ = map(float, nr[0])
        solved, force, tangent = map(float, nr[-1])
        _, shift, _, roll, yaw, _ = _line(block, r"S_WS\s+Y_WS\s+Z_WS\s+ROLL_WS")
        _, tr_y, tr_z, angle, _, _ = _line(block, r"XCP\(TR\)\s+YCP\(TR\)")
        _, wheel_y, _ = _line(block, r"XCP\(W\)\s+YCP\(W\)")
        normal, *_ = _line(block, r"FN\s+FX\s+FS\s+MN")
        _, _, _, patch_approach, pressure = _line(block, r"FN/G\s+FX/FSTAT/FN")
        if re.search(r"DX\s+DS\s+DQREL", block):
            dx, _, *_ = _line(block, r"DX\s+DS\s+DQREL")
            setups[element] = (dx, _line(block, r"POISS\(R\)\s+POISS\(W\)"))
        dx, material = setups[element]  # Compact later cases retain element setup.
        _, ds, *_ = _line(block, r"CHI\s+DQ\s+VELOC")
        _, contact_cells, *_ = _line(block, r"NPOT\s+NCON\s+NADH")
        nu_r, nu_w, shear_r, shear_w = material
        effective = 1 / ((1 - nu_r) / (2 * shear_r) + (1 - nu_w) / (2 * shear_w))
        result.append({"case": int(case), "side": "left" if element == "1" else "right", "shift_mm": shift,
                       "roll_rad": roll, "yaw_rad": yaw, "vertical_force_n": force,
                       "vertical_approach_mm": round(solved - initial, 7), "tangent_n_mm": tangent,
                       "track_y_mm": tr_y, "track_z_mm": tr_z, "wheel_y_mm": wheel_y,
                       "contact_angle_rad": angle, "patch_normal_n": normal, "patch_approach_mm": patch_approach,
                       "peak_pressure_mpa": pressure, "grid_area_mm2": contact_cells * dx * ds,
                       "node_pitch_mm": [dx, ds], "poisson": [nu_r, nu_w], "shear_mpa": [shear_r, shear_w],
                       "effective_elastic_mpa": effective,
                       "patches": int(re.search(r",\s*(\d+) CONTACT PATCH", block)[1])})
    if len(result) != 42:
        raise ValueError("固定版本的 42 组轮侧输出未完整取得。")
    return result


def multi_load_points(blob):
    """CONTACT module 3, fixed gap grid 2.2 at two independently prescribed loads."""
    text = _pinned(blob, NONHZ_SHA)
    pattern = (r"cntc_getPenetration\(\s*2\.2\): penetration pen=\s*(\S+).*?"
               r"cntc_getContactForces\(\s*2\.2\): total forces fn,mz=\s*(\S+).*?"
               r"cntc_getContactPatchAreas\(\s*2\.2\): carea=\s*([^,\s]+).*?"
               r"cntc_getMaximumPressure\(\s*2\.2\): maximum pressure=\s*(\S+)")
    result = [{"normal_n": float(force), "approach_mm": float(approach)*1000,
               "area_mm2": float(area)*1e6, "peak_pressure_mpa": float(pressure)/1e6}
              for approach, force, area, pressure in re.findall(pattern, text, re.S)]
    if len(result) != 2:
        raise ValueError("固定网格的两个独立载荷输出未完整取得。")
    return sorted(result, key=lambda row: row["normal_n"])


def reference_bundle(manchester, module3):
    return {"revision": REVISION, "manchester_sha256": LOADED_SHA, "module3_sha256": NONHZ_SHA,
            "manchester": manchester_points(manchester), "module3": multi_load_points(module3)}


def load_reference():
    return json.loads(DATA.read_text(encoding="utf-8"))


def compare_fixed_grid(rows):
    """One-load fit and one held-out load. The reports round approach to 1 µm."""
    low, high = rows
    fitted = high["normal_n"]/(high["approach_mm"]/1000)**1.5
    return {"reference":NONHZ_REFERENCE, "reference_sha256":NONHZ_SHA, "fit":high, "held_out":low,
            "fit_n_m32":fitted,
            "held_out_nominal_mm":float(_predictions(np.array([low["normal_n"]]),8e10)[0,0]),
            "held_out_fitted_mm":float(_predictions(np.array([low["normal_n"]]),fitted)[0,0]),
            "reference_tangent_available":False}


def circular_hertz(normal_n, radius_m, young_a_pa, poisson_a, young_b_pa, poisson_b):
    """Axisymmetric, small, non-conformal patch. All arguments/returns in SI."""
    if not all(in_range(*args) for args in ((normal_n, 0., 1e7), (radius_m, .001, 5.),
                   (young_a_pa, 1e8, 1e12), (young_b_pa, 1e8, 1e12),
                   (poisson_a, 0., .49), (poisson_b, 0., .49))):
        raise ValueError("圆形 Hertz 算例的材料、曲率或载荷超出范围。")
    effective = 1 / ((1-poisson_a**2)/young_a_pa + (1-poisson_b**2)/young_b_pa)
    a = (3*normal_n*radius_m/(4*effective))**(1/3)
    return {"approach_m": a*a/radius_m, "radius_m": a, "area_m2": np.pi*a*a,
            "tangent_n_m": 2*effective*a, "peak_pressure_pa": 3*normal_n/(2*np.pi*a*a) if a else 0.}


def _predictions(force, coefficient):
    delta = (force/coefficient)**(2/3)
    return np.column_stack((delta*1000, 1.5*coefficient*np.sqrt(delta)/1000))


def fit_compliance(rows, *, initial=8e10, lower=2e9, upper=2e11, tangent_weight=1.):
    """Fit one effective coefficient, with both wheel sides held out together."""
    from scipy.optimize import least_squares

    if not (in_range(initial, lower, upper) and in_range(lower, 2e9, 2e11)
            and in_range(upper, 2e9, 2e11) and lower < upper
            and in_range(tangent_weight, .1, 10.)):
        raise ValueError("初值须位于物理搜索边界，切线权重须为 0.1–10。")
    fit, held = [], []
    for row in rows:
        if (row["patches"]==1 and abs(row["shift_mm"])<=5
                and min(row["vertical_force_n"], row["vertical_approach_mm"], row["tangent_n_mm"])>0):
            (held if row["case"]%3==1 else fit).append(row)
    if min(len({row["case"] for row in subset}) for subset in (fit, held)) < 2:
        raise ValueError("需要至少两组训练横移和两组留出横移。")

    weights = np.array([1., sqrt(tangent_weight)])

    def measure(rows, coefficient):
        force, approach, tangent = np.array([[r["vertical_force_n"],r["vertical_approach_mm"],r["tangent_n_mm"]]
                                              for r in rows]).T
        return _predictions(force, coefficient) / np.column_stack((approach, tangent)) - 1

    def estimate(rows):
        return least_squares(lambda logk: (measure(rows, float(np.exp(logk[0])))*weights).ravel(),
                [np.log(initial)], bounds=(np.log(lower), np.log(upper)), max_nfev=100)

    fitted = estimate(fit)
    if not fitted.success:
        raise ValueError("有效刚度参数搜索未收敛。")
    coefficient = float(np.exp(fitted.x[0]))
    errors = {}
    for label, subset in (("fit", fit), ("held_out", held)):
        errors[label] = {"count": len(subset), "cases": sorted({row["case"] for row in subset}),
                         "initial_rmse_relative": np.sqrt(np.mean(measure(subset, initial)**2, axis=0)).tolist(),
                         "fitted_rmse_relative": np.sqrt(np.mean(measure(subset, coefficient)**2, axis=0)).tolist()}
    sensitivity = [float(np.exp(estimate([r for r in fit if r["case"]!=group]).x[0]))
                   for group in errors["fit"]["cases"]]
    centred = next((r for r in held if r["case"]==1 and r["side"]=="left"), None)
    case_one = None
    if centred:
        case_one = {"initial_relative_error": measure([centred], initial)[0].tolist(),
                    "fitted_relative_error": measure([centred], coefficient)[0].tolist()}
    report = {"schema": SCHEMA, "source": REFERENCE, "revision": REVISION, "sha256": LOADED_SHA,
              "initial_n_m32": initial, "bounds_n_m32": [lower, upper], "tangent_weight": tangent_weight,
              "effective_n_m32": coefficient, "errors": errors, "centred_left": case_one,
              "jacobian_norm_log_coefficient": float(np.linalg.norm(fitted.jac)),
              "bound_limited": bool(fitted.active_mask[0]),
              "leave_one_fit_group_out_range_n_m32": [min(sensitivity), max(sensitivity)],
              "scope": "effective vertical compliance, fixed 10 kN Manchester test, no deployment to main model"}
    return {**report, "evidence_id": "contact-calibration."+sha256(json_text(report).encode()).hexdigest()}
