from hashlib import sha256
from math import sqrt

import pytest

from rail_vehicle.verification_residuals import compare_projection


HEADER = b"model_time_s,reference_axis_m_s2\r\n"
RECORDS = {"reference_kind": "synthetic", "reference_record": "known signals",
           "alignment_record": "shared synthetic time zero"}


@pytest.fixture
def projection():
    return {"schema": "rail.frame-axis-projection.v1", "time_basis": "relative_seconds",
            "data_layer": "simulation", "sample_hz": 100, "axis_accel_m_s2": [0., 2., 4.],
            "batch_id": 7, "configuration": {"version": 2}, "channel": "vertical_accel",
            "parameters": {"scenario": "rough"}, "source_sha256": "b" * 64}


def test_paired_metrics_and_provenance(projection):
    reference = HEADER + b"0.02,3\r\n0,1\r\n0.01,1\r\n"
    report = compare_projection(projection, reference, **RECORDS)
    assert report["metrics"] == pytest.approx({"pairs": 3, "rmse_m_s2": 1., "mae_m_s2": 1.,
                                              "peak_abs_error_m_s2": 1., "pearson_r": sqrt(3) / 2})
    assert report["reference_sha256"] == sha256(reference).hexdigest()
    assert report["source_sha256"] == projection["source_sha256"]
    assert report["batch_id"] == 7 and report["configuration_version"] == 2
    assert report["independent_measurement"] == "pending"
    assert [row["reference_csv_line"] for row in report["pairs"]] == [3, 4, 2]
    assert [row["model_sample_index"] for row in report["pairs"]] == [0, 1, 2]
    changed = compare_projection({**projection, "axis_accel_m_s2": [1., 1., 3.]}, reference, **RECORDS)
    assert changed["metrics"]["rmse_m_s2"] == 0
    assert changed["projection_canonical_sha256"] != report["projection_canonical_sha256"]


@pytest.mark.parametrize(("content", "message"), [
    (b"0,1\n0.00,4\n0.01,1\n", "重复"),
    (b"0.010000000000000000000000001,1\n", "输出网格"),
    (b"1e9999999999,1\n", "输出网格"),
    (b"0,nan\n", "有限"),
    (b"0.03,1\n", "输出网格"),
    (b"0,1\n0.02,1\n", "三个"),
])
def test_invalid_reference_pairs(projection, content, message):
    with pytest.raises(ValueError, match=message):
        compare_projection(projection, HEADER + content, **RECORDS)


def test_constant_reference_and_missing_alignment(projection):
    reference = HEADER + b"0,4\n0.01,4\n0.02,4\n"
    assert compare_projection(projection, reference, **RECORDS)["metrics"]["pearson_r"] is None
    with pytest.raises(ValueError, match="时间零点"):
        compare_projection(projection, reference, **{**RECORDS, "alignment_record": " "})
