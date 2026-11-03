"""Presentation helper preserves default and caller-specified Plotly controls."""

from unittest.mock import patch

import plotly.graph_objects as go

from rail_vehicle.ui import plot_chart


def test_plot_chart_merges_config_without_mutating_caller():
    supplied = {"modeBarButtonsToRemove": ["zoomIn2d", "zoomOut2d"]}
    with patch("rail_vehicle.ui.st.plotly_chart") as render:
        plot_chart(go.Figure(), config=supplied, width="stretch")
    assert render.call_args.kwargs["config"] == {
        "displaylogo": False, "modeBarButtonsToRemove": ["zoomIn2d", "zoomOut2d"]
    }
    assert "displaylogo" not in supplied
