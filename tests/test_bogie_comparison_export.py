import csv
from io import StringIO

import pytest

from rail_vehicle.replay import prepare_replay
from rail_vehicle.replay_evidence import comparison_evidence_csv


def test_comparison_export_distinguishes_simultaneous_observations_and_preserves_scope():
    stamp = "2026-11-13T07:00:01Z"
    track = prepare_replay([
        dict(timestamp=stamp, speed_kmh=speed, lateral_accel=lateral, vertical_accel=0.25)
        for speed, lateral in [(78.5, 0.0), (81.0, -1.5), (82.0, -1.0)]
    ])
    settings = dict(batch_id=-2, vehicle_id=None, fictional=True, threshold=1.0, cutoff=None,
                    speed_tolerance=1.0, first=1, last=2)
    data = comparison_evidence_csv(track, 1, 2, **settings)
    assert data.startswith(b"\xef\xbb\xbf")
    current, reference = csv.DictReader(StringIO(data.decode("utf-8-sig")))
    assert [(row["comparison_role"], row["batch_row"], row["lateral_exceeds"]) for row in (current, reference)] == [
        ("current_exceedance", "2", "1"), ("reference_not_exceeding", "3", "0"),
    ]
    assert current["timestamp_utc"] == reference["timestamp_utc"]
    assert current["lateral_accel_m_s2"] == "-1.5"
    assert reference["source"] == "fictional_demo" and reference["local_vehicle_id"] == ""
    assert (reference["window_first_row"], reference["window_last_row"]) == ("2", "3")
    assert float(reference["pair_speed_difference_kmh"]) == 1.0
    assert float(reference["pair_time_difference_s"]) == 0.0
    with pytest.raises(ValueError, match="不匹配"):
        comparison_evidence_csv(track, 1, 0, **settings)
