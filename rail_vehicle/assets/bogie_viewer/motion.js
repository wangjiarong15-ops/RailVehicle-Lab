const radiansPerSecond = 2 * Math.PI / 2.4;

export function motionClock() {
  let latest, received = 0, phase = 0, last = 0, envelope = [0, 0, 0];
  return {
    receive(args, now) {
      if (!latest || !args.playing || !latest.playing || args.spread) phase = args.phase;
      if (!latest) envelope = args.envelope.slice();
      latest = args;
      received = now;
    },
    tick(now) {
      const dt = last ? Math.min((now - last) / 1000, .10) : 0;
      last = now;
      if (!latest) return null;
      const args = latest;
      const running = args.playing && now - received < 800;
      if (running) {
        // Bridge server snapshots without driving statistics or changing the chosen sample.
        const velocity = radiansPerSecond * args.rate;
        const expected = args.phase + (now - received) / 1000 * velocity;
        phase += velocity * dt;
        const correction = Math.atan2(Math.sin(expected - phase), Math.cos(expected - phase));
        phase += correction * (1 - Math.exp(-dt * 12));
      }
      let settling = false;
      for (let i = 0; i < 3; i++) {
        const target = args.spread ? 0 : args.envelope[i];
        const delta = target - envelope[i];
        if (Math.abs(delta) < .0002 || args.spread) envelope[i] = target;
        else {
          settling = true;
          envelope[i] += delta * (1 - Math.exp(-dt * 12));
        }
      }
      const angle = args.spread ? 0 : phase;
      return {running, settling, angle, lateral: envelope[0] * Math.sin(angle),
        vertical: envelope[1] * Math.sin(angle), yaw: envelope[2] * Math.cos(angle)};
    },
  };
}
