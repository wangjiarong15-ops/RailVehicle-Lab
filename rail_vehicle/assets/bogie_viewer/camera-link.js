// The two same-origin component frames exchange camera poses only. The group
// name is unique to this Streamlit session; nothing is persisted or sent to Python.
export function cameraLink(rig, report) {
  let channel, group, side, queued, lastPose;
  let receiving = false, paired = false;

  function publish(force = false) {
    if (!channel || (side === "reference" && !paired)) return;
    const camera = rig.capture();
    const fingerprint = JSON.stringify(camera);
    if (!force && fingerprint === lastPose) return;
    lastPose = fingerprint;
    channel.postMessage({type: "pose", side, camera});
  }

  function cancelQueued() {
    if (queued !== undefined) cancelAnimationFrame(queued);
    queued = undefined;
  }

  function changed() {
    if (!channel || receiving || queued !== undefined) return;
    queued = requestAnimationFrame(() => {
      queued = undefined;
      publish();
    });
  }

  function close() {
    cancelQueued();
    channel?.close();
    channel = undefined;
    paired = false;
    lastPose = undefined;
  }

  function connect(nextGroup, reference) {
    const nextSide = reference ? "reference" : "current";
    if (group === nextGroup && side === nextSide) return;
    close();
    group = nextGroup;
    side = nextSide;
    if (!group) return report("off");
    try {
      channel = new BroadcastChannel(`rail-bogie-camera:${group}`);
    } catch {
      return report("unsupported");
    }
    report("waiting");
    channel.onmessage = ({data}) => {
      const peer = side === "current" ? "reference" : "current";
      if (data?.side !== peer) return;
      if (data.type === "ready" && side === "current") {
        paired = true;
        report("linked");
        publish(true);
      } else if (data.type === "pose" && rig.accepts(data.camera)) {
        const first = !paired;
        paired = true;
        cancelQueued();
        receiving = true;
        try {
          rig.restore(data.camera);
          lastPose = JSON.stringify(rig.capture());
        } finally {
          receiving = false;
        }
        report("linked");
        if (first && side === "reference") channel.postMessage({type: "ready", side});
      }
    };
    if (side === "reference") channel.postMessage({type: "ready", side});
    else publish(true);
  }

  rig.controls.addEventListener("change", changed);
  return {connect, dispose() {
    rig.controls.removeEventListener("change", changed);
    close();
  }};
}
