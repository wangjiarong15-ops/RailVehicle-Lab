"""Pinned external numerical check of the centred Manchester rigid contact datum."""

from hashlib import sha256
from io import StringIO
import csv

import numpy as np

from rail_vehicle.profile_geometry import Pair, read_profile
from rail_vehicle.virtual_trials import json_text

REVISION = "5644061ade1b0214e7ce70d780066f630d05f782"
DIRECTORY = f"https://github.com/eve70a/CONTACT/tree/{REVISION}/testbank/profiles"
REFERENCE = f"https://github.com/eve70a/CONTACT/blob/{REVISION}/testbank/contact_mbench.ref_out"
WHEEL_SHA = "008c1aa6293ffc8a5141e78a663b21e39ba8daccd4774029f79cfde0c45e0d1d"
RAIL_SHA = "5a431b69bd7ae8418b641412da6fdf1d852f4d9865080875cf3f0f3e874e3588"


def _points(blob, expected):
    text=blob.replace(b"\r\n",b"\n")
    if sha256(text).hexdigest()!=expected:
        raise ValueError("型面摘要与登记的 CONTACT 版本不一致。")
    rows=text.decode("ascii").partition("point.begin")[2].partition("point.end")[0].splitlines()
    return np.asarray([[float(value) for value in line.split()[:2]] for line in rows
                       if line.strip() and not line.lstrip().startswith("!")])


def _ordered_csv(points):
    stream=StringIO(newline="")
    writer=csv.writer(stream)
    writer.writerow(("y_mm","z_mm"))
    writer.writerows(sorted(points))
    return stream.getvalue().encode()


def run_external_case(wheel_blob,rail_blob):
    wheel=_points(wheel_blob,WHEEL_SHA)
    rail=_points(rail_blob,RAIL_SHA)
    # Unmirrored supplied rail. The inboard gauge point is the negative-Y
    # crossing at 14 mm below the rail-head datum.
    gauge=[]
    for (y1,z1),(y2,z2) in zip(rail,rail[1:]):
        if z1!=z2 and min(z1,z2)<=14<=max(z1,z2):
            gauge.append(y1+(y2-y1)*(14-z1)/(z2-z1))
    centre_from_wheel_mm=1435/2-min(gauge)-(1360/2+70)
    wheel_csv=_ordered_csv([(-y,-z) for y,z in wheel if abs(y)<51])
    rail_csv=_ordered_csv([(y+centre_from_wheel_mm,-z) for y,z in rail if abs(y)<25])
    pair=Pair(read_profile(wheel_csv),read_profile(rail_csv))
    computed=pair.contact(0.)
    if computed["status"]!="tread":
        raise ValueError("刚性截面的首触没有落在内部踏面。")
    approach=-1000*computed["wheel_vertical_shift_m"]
    location=1000*computed["wheel_y_m"]
    # Case 1, result element 1 in the versioned reference file. NR iteration
    # zero is the rigid geometric datum. The loaded reference point is separate.
    rigid_contact_reference_mm=.1821
    loaded_reference_point_mm=1.8726
    result={"schema":"rail.external-contact.v1","level":"external","revision":REVISION,
            "reference_url":REFERENCE,"license":"Apache-2.0 upstream repository",
            "reference_case":"CONTACT case 1 element 1, centred, zero roll and yaw",
            "parameter_source":"Manchester Contact Benchmark v3 profile, 1435 mm/14 mm gauge, 1360 mm flange backs",
            "wheel_sha256":WHEEL_SHA,"rail_sha256":RAIL_SHA,
            "computed_rigid_touch_mm":approach,"reference_rigid_touch_mm":rigid_contact_reference_mm,
            "rigid_touch_error_mm":abs(approach-rigid_contact_reference_mm),
            "computed_rigid_lateral_mm":location,"loaded_contact_reference_lateral_mm":loaded_reference_point_mm,
            "lateral_difference_mm":abs(location-loaded_reference_point_mm),
            "covered":"rigid initial clearance datum; loaded contact location only displayed for comparison"}
    return {**result,"evidence_id":"external."+sha256(json_text(result).encode()).hexdigest()},wheel_csv,rail_csv
