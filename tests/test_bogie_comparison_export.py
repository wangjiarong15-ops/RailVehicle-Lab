import csv
from datetime import datetime, timezone
from io import StringIO

import pytest

from rail_vehicle.replay import prepare_replay
from rail_vehicle.replay_evidence import ReportComparison, comparison_evidence_csv


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


def test_report_handoff_revalidates_batch_identity_exact_rows_and_scope():
    instant = datetime(2026, 11, 13, 7, tzinfo=timezone.utc)
    track = prepare_replay([
        dict(timestamp=instant.isoformat(), speed_kmh=speed, lateral_accel=lateral, vertical_accel=.25)
        for speed, lateral in [(80, 0), (81, -2), (82, 0)]
    ])
    handoff = ReportComparison.capture(track, 1, 0, vehicle_id=6, batch_id=9, fictional=False,
                                       threshold=1, speed_tolerance=5)
    scope = dict(start=instant, end=instant, threshold=1, row_span=(1, 2))
    assert handoff.matches_batch(6, 9, False)
    assert not handoff.matches_batch(7, 9, False)
    assert handoff.matches_scope(track.samples, **scope)
    assert not handoff.matches_scope(track.samples, **dict(scope, row_span=(2, 3)))
    assert not handoff.matches_scope(track.samples, **dict(scope, threshold=2))
    changed = [track.samples[0], track.samples[2], track.samples[1]]
    assert not handoff.matches_scope(changed, **scope)
