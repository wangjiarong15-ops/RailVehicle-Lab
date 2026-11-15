"""Bound browser chart size while keeping original observations and extrema."""

CURVE_POINT_LIMIT = 4_000


def curve_preview(rows, field, *, limit=CURVE_POINT_LIMIT):
    """Keep both endpoints and each contiguous bucket's extrema in input order."""
    if limit < 4:
        raise ValueError("A curve preview requires at least four points")
    if len(rows) <= limit:
        return rows

    bucket_count = (limit - 2) // 2
    interior = len(rows) - 2
    preview = [rows[0]]
    for bucket in range(bucket_count):
        start = 1 + bucket * interior // bucket_count
        stop = 1 + (bucket + 1) * interior // bucket_count
        low = high = start
        minimum = maximum = rows[start][field]
        for index in range(start + 1, stop):
            value = rows[index][field]
            if value < minimum:
                low, minimum = index, value
            if value > maximum:
                high, maximum = index, value
        preview.append(rows[min(low, high)])
        if low != high:
            preview.append(rows[max(low, high)])
    preview.append(rows[-1])
    return preview
