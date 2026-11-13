"""Bridge between the Streamlit replay and the local WebGL component."""

from pathlib import Path

from streamlit.components.v1 import declare_component

from rail_vehicle.bogie import motion_envelope


ASSETS = Path(__file__).with_name("assets") / "bogie_viewer"


def webgl_available():
    return all((ASSETS / name).is_file() for name in (
        "index.html", "viewer.js", "viewer.css", "assembly.json", "geometry.js",
        "lighting.js", "camera.js", "interaction.js", "motion.js", "model-loader.js",
        "playback-batches.js", "part-browser.js", "measurement.js", "section-plane.js",
        "inspection-export.js", "view-bookmarks.js", "bookmark-transfer.js",
        "vendor/three.module.min.js", "vendor/three.core.min.js",
        "vendor/OrbitControls.js", "vendor/RoomEnvironment.js",
    ))


def render_webgl(*, mode, phase, state, visible, highlight, view, focus, labels, rails,
                 exploded, spread, camera_revision, continuous, height, key, export_context=None, reference=False):
    # Keep the same widget identity on replay ticks so the orbit camera and scene stay mounted.
    component = declare_component("bogie_viewer", path=str(ASSETS))
    component(
        mode=mode, envelope=motion_envelope(mode), phase=phase, playing=state.playing and continuous,
        rate=state.rate, visible=visible, highlight=highlight, view=view, focus=focus,
        labels=labels, rails=rails, spread=spread if exploded else 0, revision=camera_revision,
        height=height, key=key, default=None,
        replayRunning=state.playing, exportContext=export_context, reference=reference,
    )
