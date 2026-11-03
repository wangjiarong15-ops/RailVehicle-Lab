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
HOME_PLOT_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["zoomIn2d", "zoomOut2d", "select2d", "lasso2d"]}


def dashboard_events(anomalies, page=0):
    """Return stable, globally numbered events for the selected display page."""
    start = page * EVENTS_PER_PAGE
    return list(enumerate(anomalies["events"][start:start + EVENTS_PER_PAGE], start=start + 1))


def dashboard_chart(analysis, anomalies, event_page=0, *, timezone_name="UTC"):
    rows = analysis["samples"]
    clock = lambda value: display_timestamp(value, timezone_name)
    indices = sorted({round(i*(len(rows)-1)/min(1199,max(1,len(rows)-1))) for i in range(min(1200,len(rows)))})
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.12,
                           subplot_titles=("速度 · km/h（纵轴自动缩放，可能不从 0 起）", "横向加速度 · m/s²", "垂向加速度 · m/s²"))
    for number, field, name in ((1,"speed_kmh","速度"),(2,"lateral_accel","横向"),(3,"vertical_accel","垂向")):
        timestamps = [clock(rows[i]["timestamp"]) for i in indices]
        figure.add_trace(go.Scatter(x=timestamps, y=[rows[i][field] for i in indices],
                                   customdata=[stamp.isoformat() for stamp in timestamps],
                                   hovertemplate="%{customdata}<br>%{y:.3f}<extra>%{fullData.name}</extra>",
                                   mode="markers", marker=dict(size=3), name=name), row=number,col=1)
        if number > 1:
            points = [p for p in anomalies["points"] if p["field"] == field]
            visible = points if len(points)<=1200 else [points[round(i*(len(points)-1)/1199)] for i in range(1200)]
            figure.add_trace(go.Scatter(x=[clock(p["timestamp"]) for p in visible],y=[p["value"] for p in visible],
                                       hoverinfo="skip",  # use the aligned three base series in unified hover
                                       mode="markers",marker=dict(color="#F46D7A",size=6,symbol="diamond"),
                                       name=name+"统计异常",showlegend=False),row=number,col=1)
            for value in (-anomalies["threshold"],anomalies["threshold"]):
                figure.add_hline(y=value,line_dash="dot",line_color="#CF8C39",row=number,col=1)
                figure.add_annotation(x=clock(rows[-1]["timestamp"]), y=value, text=f"{value:+g} m/s²",
                                      showarrow=False, xanchor="right", yanchor="bottom", font=dict(color="#E9AC56", size=11),
                                      row=number, col=1)
    for index, event in dashboard_events(anomalies, event_page):
        row = 2 if event["field"] == "lateral_accel" else 3
        start, end = clock(event["start_timestamp"]),clock(event["end_timestamp"])
        if start == end:
            figure.add_vline(x=start,line_color="#F46D7A",opacity=.5,row=row,col=1)
        else:
            figure.add_vrect(x0=start,x1=end,fillcolor="#F46D7A",opacity=.18,line_width=0,row=row,col=1)
        figure.add_annotation(x=start, y=event["maximum_signed_value"], text=f"#{index}", showarrow=False,
                              yshift=13, font=dict(color="#F46D7A", size=11), row=row, col=1)
    figure.update_layout(height=620,margin=dict(l=45,r=20,t=40,b=35),showlegend=False,
                         hovermode="x unified", hoverlabel=dict(bgcolor="rgba(12,26,47,0.92)",
                         font=dict(color="white", size=12)))
    # Cross-panel unified hover was added after our minimum supported Plotly version.
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
    figure.update_layout(xaxis=dict(anchor="y3", matches=None, showticklabels=True,
                                    title_text=f"时间（{timezone_name}）"),
                         yaxis2=dict(anchor="x"), yaxis3=dict(anchor="x"))
    figure.layout.xaxis2 = None
    figure.layout.xaxis3 = None
    figure.layout.xaxis.update(showspikes=True, spikemode="across", spikesnap="cursor")
    return figure
