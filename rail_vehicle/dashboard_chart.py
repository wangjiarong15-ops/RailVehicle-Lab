"""Presentation of already computed dashboard samples and rule events."""
from datetime import timezone
from zoneinfo import ZoneInfo
from plotly.subplots import make_subplots
import plotly.graph_objects as go
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


def dashboard_chart(analysis, anomalies, event_page=0, *, timezone_name="UTC", first_row=0, selected_event=None):
    rows = analysis["samples"]
    clock = lambda value: display_timestamp(value, timezone_name)
    events = dashboard_events(anomalies, event_page)
    indices = chart_sample_indices(rows, events)
    included = set(indices)
    timestamps = [clock(rows[i]["timestamp"]) for i in indices]
    labels = [sample_label(rows[i], i, first_row) for i in indices]
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.12,
                           subplot_titles=("速度 · km/h（纵轴自动缩放，可能不从 0 起）", "横向加速度 · m/s²", "垂向加速度 · m/s²"))
    for number, field, name in ((1,"speed_kmh","速度"),(2,"lateral_accel","横向"),(3,"vertical_accel","垂向")):
        figure.add_trace(go.Scatter(x=timestamps, y=[rows[i][field] for i in indices],
                                   customdata=[stamp.isoformat() for stamp in timestamps],
                                   text=labels, hovertemplate="%{customdata}<br>%{y:.3f}<br>%{text}<extra>%{fullData.name}</extra>",
                                   mode="markers", marker=dict(size=3, color=CHANNEL_COLORS[field]), name=name), row=number,col=1)
        if number > 1:
            points = [p for p in anomalies["points"] if p["field"] == field]
            visible = ([p for p in points if p["sample_index"] in included] if points and "sample_index" in points[0]
                       else points if len(points) <= DASHBOARD_POINT_LIMIT else
                       [points[round(i*(len(points)-1)/(DASHBOARD_POINT_LIMIT-1))] for i in range(DASHBOARD_POINT_LIMIT)])
            figure.add_trace(go.Scatter(x=[clock(p["timestamp"]) for p in visible],y=[p["value"] for p in visible],
                                       text=[sample_label(rows[p["sample_index"]], p["sample_index"], first_row)
                                             for p in visible if "sample_index" in p] or None,
                                       hovertemplate="%{y:.4f} m/s²<br>%{text}<extra>超阈值</extra>",
                                       mode="markers",marker=dict(color="#F46D7A",size=6,symbol="diamond"),
                                       name=name+"统计异常",showlegend=False),row=number,col=1)
            for value in (-anomalies["threshold"],anomalies["threshold"]):
                figure.add_hline(y=value,line_dash="dot",line_color="#CF8C39",row=number,col=1)
                figure.add_annotation(x=clock(rows[-1]["timestamp"]), y=value, text=f"{value:+g} m/s²",
                                      showarrow=False, xanchor="right", yanchor="bottom", font=dict(size=11),
                                      row=number, col=1)
    for index, event in events:
        row = 2 if event["field"] == "lateral_accel" else 3
        start, end = clock(event["start_timestamp"]),clock(event["end_timestamp"])
        if start == end:
            figure.add_vline(x=start,line_color="#F46D7A",opacity=.5,row=row,col=1)
        else:
            figure.add_vrect(x0=start,x1=end,fillcolor="#F46D7A",opacity=.18,line_width=0,row=row,col=1)
        if index == selected_event:
            if start == end:
                figure.add_vline(x=start, line_color="#CF8C39", line_width=2)
            else:
                figure.add_vrect(x0=start, x1=end, fillcolor="#CF8C39", opacity=.17, line_width=0)
        figure.add_annotation(x=clock(event["maximum_timestamp"]), y=event["maximum_signed_value"], text=f"#{index}", showarrow=False,
                              yshift=13, font=dict(color="#F46D7A", size=11), row=row, col=1)
    # Left-aligned channel labels read as instrument headers, not floating titles.
    for annotation in figure.layout.annotations[:3]:
        annotation.update(x=0, xanchor="left", font=dict(size=12), yshift=8)
    figure.update_yaxes(gridcolor="rgba(128,151,180,0.15)", zerolinecolor="rgba(128,151,180,0.3)",
                        tickfont=dict(size=10))
    figure.update_layout(height=500,margin=dict(l=38,r=16,t=24,b=30),showlegend=False,
                         hovermode="x unified", hoverlabel=dict(bgcolor="rgba(12,26,47,0.92)",
                         font=dict(color="white", size=12)))
    return share_time_hover(figure, "y3", f"时间（{timezone_name}）")


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
