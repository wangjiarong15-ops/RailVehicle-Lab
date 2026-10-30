"""Presentation of already computed dashboard samples and rule events."""
from datetime import timezone
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from rail_vehicle.timestamp_utils import parse_iso_timestamp


def utc(value):
    parsed = parse_iso_timestamp(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def dashboard_chart(analysis, anomalies):
    rows = analysis["samples"]
    indices = sorted({round(i*(len(rows)-1)/min(1199,max(1,len(rows)-1))) for i in range(min(1200,len(rows)))})
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.09,
                           subplot_titles=("速度 · km/h", "横向加速度 · m/s²", "垂向加速度 · m/s²"))
    for number, field, name in ((1,"speed_kmh","速度"),(2,"lateral_accel","横向"),(3,"vertical_accel","垂向")):
        figure.add_trace(go.Scatter(x=[utc(rows[i]["timestamp"]) for i in indices], y=[rows[i][field] for i in indices],
                                   mode="markers", marker=dict(size=3), name=name), row=number,col=1)
        if number > 1:
            points = [p for p in anomalies["points"] if p["field"] == field]
            visible = points if len(points)<=1200 else [points[round(i*(len(points)-1)/1199)] for i in range(1200)]
            figure.add_trace(go.Scatter(x=[utc(p["timestamp"]) for p in visible],y=[p["value"] for p in visible],
                                       mode="markers",marker=dict(color="#F46D7A",size=6,symbol="diamond"),
                                       name=name+"统计异常",showlegend=False),row=number,col=1)
            for value in (-anomalies["threshold"],anomalies["threshold"]):
                figure.add_hline(y=value,line_dash="dot",line_color="#CF8C39",row=number,col=1)
    for event in anomalies["events"][-30:]:
        row = 2 if event["field"] == "lateral_accel" else 3
        start, end = utc(event["start_timestamp"]),utc(event["end_timestamp"])
        if start == end:
            figure.add_vline(x=start,line_color="#F46D7A",opacity=.5,row=row,col=1)
        else:
            figure.add_vrect(x0=start,x1=end,fillcolor="#F46D7A",opacity=.18,line_width=0,row=row,col=1)
    figure.update_layout(height=395,margin=dict(l=40,r=10,t=25,b=25),showlegend=False)
    figure.update_xaxes(title_text="时间（UTC）",row=3,col=1)
    return figure
