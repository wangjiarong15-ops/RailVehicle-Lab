"""Verify secondary-text palette against the actual configured UI surfaces."""

import streamlit as st

from rail_vehicle.ui import THEME_TOKENS


def _luminance(color):
    rgb = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in rgb]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def contrast_ratio(foreground, background):
    values = sorted((_luminance(foreground), _luminance(background)))
    return (values[1] + .05) / (values[0] + .05)


def test_secondary_text_meets_aa_on_configured_main_card_and_sidebar_surfaces():
    for mode, tokens in THEME_TOKENS.items():
        backgrounds = [tokens["surface"]] + [st.get_option(f"theme.{mode}.{setting}") for setting in (
            "backgroundColor", "secondaryBackgroundColor", "sidebar.backgroundColor", "sidebar.secondaryBackgroundColor")]
        for background in backgrounds:
            assert contrast_ratio(tokens["muted"], background) >= 4.5, (mode, background)
