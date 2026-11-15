from rail_vehicle.curve_preview import CURVE_POINT_LIMIT, curve_preview


def test_preview_keeps_observed_extrema_and_endpoints_in_order():
    rows = [{"time": index, "speed": speed}
            for index, speed in enumerate([0, 6, 2, -9, 5, 14, 4, -8, 7, 0])]
    preview = curve_preview(rows, "speed", limit=6)
    assert [row["time"] for row in preview] == [0, 1, 3, 5, 7, 9]
    assert all(row is rows[row["time"]] for row in preview)


def test_preview_keeps_small_series_and_bounds_long_series_with_a_single_spike():
    small = [{"value": 8}, {"value": -2}]
    assert curve_preview(small, "value") == small
    rows = [{"value": 0} for _ in range(20_000)]
    rows[9947]["value"] = -0.7
    rows[9951]["value"] = 2.3
    preview = curve_preview(rows, "value")
    assert len(preview) <= CURVE_POINT_LIMIT
    assert rows[9947] in preview and rows[9951] in preview
    assert preview[0] is rows[0] and preview[-1] is rows[-1]
