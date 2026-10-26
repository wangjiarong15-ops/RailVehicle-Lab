"""Deployment policy, deliberately independent of credentials and session state."""

import os


def public_demo_enabled():
    """Host environment only: visitors cannot enable full mode through widgets."""
    return os.environ.get("RAIL_PUBLIC_DEMO", "false").strip().lower() in {"true", "1", "yes"}


def demo_source_options(history, demo):
    return [demo] if public_demo_enabled() else [history, demo]
