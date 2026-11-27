"""Presentation of already computed dashboard samples and rule events."""
from datetime import timezone
from itertools import pairwise
from zoneinfo import ZoneInfo
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from rail_vehicle.data_quality import review_sampling_timestamps
from rail_vehicle.timestamp_utils import parse_iso_timestamp


def utc(value):
    parsed = parse_iso_timestamp(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


DISPLAY_TIMEZONES = ("UTC", "Asia/Shanghai")


def display_timestamp(value, timezone_name="UTC"):
    """Convert only the displayed clock; source timestamps and facts stay intact."""
    if timezone_name not in DISPLAY_TIMEZONES:
        raise ValueError("Unsupported display timezone")
    zone = timezone.utc if timezone_name == "UTC" else ZoneInfo(timezone_name)
    return utc(value).astimezone(zone)


EVENTS_PER_PAGE = 30
DASHBOARD_POINT_LIMIT = 1_200
HOME_PLOT_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["zoomIn2d", "zoomOut2d", "select2d", "lasso2d"]}
CHANNEL_COLORS = {"speed_kmh": "#4299E8", "lateral_accel": "#24B9B0", "vertical_accel": "#A38CF0"}
LIGHT_CHANNEL_COLORS = {"speed_kmh": "#176BCE", "lateral_accel": "#087E76", "vertical_accel": "#7145A8"}


def dashboard_events(anomalies, page=0):
    """Return stable, globally numbered events for the selected display page."""
    start = page * EVENTS_PER_PAGE
    return list(enumerate(anomalies["events"][start:start + EVENTS_PER_PAGE], start=start + 1))


def chart_sample_indices(rows, events):
    """Bound drawing cost while retaining visible event endpoints and channel extrema."""
    if len(rows) <= DASHBOARD_POINT_LIMIT:
        return range(len(rows))
    chosen = {0, len(rows)-1}
    for _, event in events:
        chosen.update(event[key] for key in ("first_index", "last_index", "peak_index") if key in event)
    buckets = max(1, (DASHBOARD_POINT_LIMIT-len(chosen))//6)
    for bucket in range(buckets):
        start, end = bucket*len(rows)//buckets, (bucket+1)*len(rows)//buckets
        for field in CHANNEL_COLORS:
            chosen.add(min(range(start, end), key=lambda i: rows[i][field]))
            chosen.add(max(range(start, end), key=lambda i: rows[i][field]))
    return sorted(chosen)


def sample_label(row, index, first_row=0):
    parts = [f"批次第 {first_row+index+1} 条"]
    if row.get("sample_id") is not None:
        parts.append(f"采样 ID {row['sample_id']}")
    if row.get("csv_line") is not None:
        parts.append(f"CSV 第 {row['csv_line']} 行")
    return " · ".join(parts)


def _line_indices(timestamps, cutoff):
    """Use empty slots for duplicate times and acquisition gaps, never fill them."""
    timestamps = [stamp.astimezone(timezone.utc) for stamp in timestamps]
    if cutoff is None:
        cutoff = review_sampling_timestamps(timestamps, detail_limit=0)["gap_cutoff_seconds"]
    indices = [0]
    for index, (previous, current) in enumerate(pairwise(timestamps), start=1):
        interval = (current - previous).total_seconds()
        if interval == 0 or (cutoff is not None and interval > cutoff):
            indices.append(None)
        indices.append(index)
    return indices


def dashboard_chart(analysis, anomalies, event_page=0, *, timezone_name="UTC", first_row=0, selected_event=None,
                    light=False, gap_cutoff=None):
    rows = analysis["samples"]
    clock = lambda value: display_timestamp(value, timezone_name)
    events = dashboard_events(anomalies, event_page)
    indices = chart_sample_indices(rows, events)
    included = set(indices)
    timestamps = [clock(rows[i]["timestamp"]) for i in indices]
    labels = [sample_label(rows[i], i, first_row) for i in indices]
    positions = _line_indices(timestamps, gap_cutoff) if len(rows) <= DASHBOARD_POINT_LIMIT else range(len(indices))
    connected = len(rows) <= DASHBOARD_POINT_LIMIT and len(positions) <= DASHBOARD_POINT_LIMIT
    positions = positions if connected else range(len(indices))
    x = [None if i is None else timestamps[i] for i in positions]
    hover_times = [stamp.isoformat() if stamp is not None else None for stamp in x]
    hover_rows = [None if i is None else labels[i] for i in positions]
    colors = LIGHT_CHANNEL_COLORS if light else CHANNEL_COLORS
    anomaly_color = "#B5274D" if light else "#F46D7A"
    threshold_color = "#925514" if light else "#CF8C39"
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.12,
                           subplot_titles=("速度 · km/h（纵轴自动缩放，可能不从 0 起）", "横向加速度 · m/s²", "垂向加速度 · m/s²"))
    shapes, labels_on_plot = [], list(figure.layout.annotations)
    described_markers = False
    for number, field, name in ((1,"speed_kmh","速度"),(2,"lateral_accel","横向"),(3,"vertical_accel","垂向")):
        values = [rows[i][field] for i in indices]
        y = [None if i is None else values[i] for i in positions]
        text = [label + " · 超阈值" if label and number > 1 and abs(value) > anomalies["threshold"] else label
                for label, value in zip(hover_rows, y)]
        unit = "km/h" if number == 1 else "m/s²"
        figure.add_trace(go.Scatter(x=x, y=y, customdata=hover_times, text=text,
                                   hovertemplate=f"%{{customdata}}<br>%{{y:.4f}} {unit}<br>%{{text}}<extra>{name}</extra>",
                                   mode="lines+markers" if connected else "markers", connectgaps=False,
                                   line=dict(width=1.2, color=colors[field]),
                                   marker=dict(size=3, color=colors[field]), name=name, showlegend=False), row=number,col=1)
        if number > 1:
            points = [p for p in anomalies["points"] if p["field"] == field]
            visible = ([p for p in points if p["sample_index"] in included] if points and "sample_index" in points[0]
                       else points if len(points) <= DASHBOARD_POINT_LIMIT else
                       [points[round(i*(len(points)-1)/(DASHBOARD_POINT_LIMIT-1))] for i in range(DASHBOARD_POINT_LIMIT)])
            figure.add_trace(go.Scatter(x=[clock(p["timestamp"]) for p in visible],y=[p["value"] for p in visible],
                                       text=[sample_label(rows[p["sample_index"]], p["sample_index"], first_row)
                                             for p in visible if "sample_index" in p] or None,
                                       hoverinfo="skip",
                                       mode="markers",marker=dict(color=anomaly_color,size=6,symbol="diamond"),
                                       name="超阈值样本",showlegend=bool(visible) and not described_markers),row=number,col=1)
            described_markers |= bool(visible)
            for line_index, value in enumerate(dict.fromkeys((-anomalies["threshold"], anomalies["threshold"]))):
                shapes.append(dict(type="line", xref="x domain", yref=f"y{number}", x0=0, x1=1, y0=value, y1=value,
                                   line=dict(dash="dot", color=threshold_color), name="阈值",
                                   showlegend=number == 2 and line_index == 0, legendrank=1100))
                labels_on_plot.append(dict(x=timestamps[-1], y=value, xref="x", yref=f"y{number}",
                                           text=f"{value:+g} m/s²", showarrow=False, xanchor="right",
                                           yanchor="bottom", font=dict(size=11)))
    named_shapes = set()
    for index, event in events:
        row = 2 if event["field"] == "lateral_accel" else 3
        start, end = clock(event["start_timestamp"]),clock(event["end_timestamp"])
        if start.astimezone(timezone.utc) == end.astimezone(timezone.utc):
            name = "同刻区段"
        else:
            name = "异常区段"
        legend = dict(name=name, showlegend=name not in named_shapes, legendrank=1050)
        named_shapes.add(name)
        same_time = name == "同刻区段"
        shapes.append(_interval_shape(start, end, f"y{row}", same_time, anomaly_color, **legend))
        if index == selected_event:
            shapes.extend(_interval_shape(start, end, axis, same_time, "#CF8C39", selected=True)
                          for axis in ("y", "y2", "y3"))
        labels_on_plot.append(dict(x=clock(event["maximum_timestamp"]), y=event["maximum_signed_value"],
                                   xref="x", yref=f"y{row}", text=f"#{index}", showarrow=False,
                                   yshift=13, font=dict(color=anomaly_color, size=11)))
    for annotation in labels_on_plot[:3]:
        annotation.update(x=0, xanchor="left", font=dict(size=12), yshift=8)
    figure.layout.shapes = shapes
    figure.layout.annotations = labels_on_plot
    figure.update_yaxes(gridcolor="rgba(128,151,180,0.15)", zerolinecolor="rgba(128,151,180,0.3)",
                        tickfont=dict(size=10))
    figure.update_layout(height=550,margin=dict(l=38,r=16,t=82,b=30),showlegend=True,
                         legend=dict(orientation="h", x=0, y=1.10, yanchor="bottom", font=dict(size=11),
                                     itemclick=False, itemdoubleclick=False),
                         hovermode="x unified", hoverlabel=dict(bgcolor="rgba(12,26,47,0.92)",
                         font=dict(color="white", size=12)))
    return share_time_hover(figure, "y3", f"时间（{timezone_name}）")


def _interval_shape(start, end, axis, same_time, color, *, selected=False, **legend):
    coordinates = dict(xref="x", yref=f"{axis} domain", x0=start, x1=end, y0=0, y1=1)
    if same_time:
        return dict(coordinates, type="line", line=dict(color=color, width=2 if selected else 1),
                    opacity=1 if selected else .55, **legend)
    return dict(coordinates, type="rect", fillcolor=color, opacity=.18, line=dict(width=0), **legend)


def share_time_hover(figure, bottom_axis, title):
    """Use the same Plotly x axis for every panel, including event shapes."""
    figure.update_layout(hovermode="x unified")
    if hasattr(go.Layout(), "hoversubplots"):
        figure.update_layout(hoversubplots="axis")
    # Matched x/x2/x3 ranges alone do not expand hover across subplots. Reuse
    # one actual x axis for all three panels and their event annotations.
    figure.update_traces(xaxis="x")
    for shape in figure.layout.shapes:
        if shape.xref in ("x2", "x3"):
            shape.xref = "x"
        elif shape.xref in ("x2 domain", "x3 domain"):
            shape.xref = "x domain"
    for annotation in figure.layout.annotations:
        if annotation.xref in ("x2", "x3"):
            annotation.xref = "x"
    figure.update_layout(xaxis=dict(anchor=bottom_axis, matches=None, showticklabels=True, title_text=title))
    for index in range(2, int(bottom_axis[1:])+1):
        figure.layout[f"yaxis{index}"].update(anchor="x")
        figure.layout[f"xaxis{index}"] = None
    figure.layout.xaxis.update(showspikes=True, spikemode="across", spikesnap="cursor")
    return figure
