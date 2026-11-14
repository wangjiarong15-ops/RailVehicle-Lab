const turn = 2 * Math.PI;
const snapshotTimeout = 800;

export function motionClock() {
  let latest, received = 0, phase = 0, last = null, envelope = [0, 0, 0];
  return {
    receive(args, now) {
      // A teaching cycle wraps naturally. A historical event loop jumps to another batch time.
      const rewind = latest && !args.periodic && args.phase < latest.phase;
      if (!latest || !args.playing || !latest.playing || args.spread || args.hold || rewind ||
          args.periodic !== latest.periodic || now - received >= snapshotTimeout) {
        phase = args.hold ? 0 : args.phase;
        last = now;
      }
      if (args.hold) envelope.fill(0);
      else if (!latest) envelope = args.envelope.slice();
      latest = args;
      received = now;
    },
    tick(now) {
      const dt = last === null ? 0 : Math.min(Math.max(0, now - last) / 1000, .10);
      last = now;
      if (!latest) return null;
      const args = latest;
      const running = args.playing && !args.spread && !args.hold && now - received < snapshotTimeout;
      if (running) {
        // Bridge server snapshots without driving statistics or changing the chosen sample.
        const velocity = turn / args.period * args.rate;
        const expected = args.phase + Math.max(0, now - received) / 1000 * velocity;
        const step = velocity * dt;
        const difference = expected - (phase + step);
        const correction = Math.atan2(Math.sin(difference), Math.cos(difference)) * (1 - Math.exp(-dt * 12));
        // A delayed fragment can slow the wheel, but cannot make a running wheel spin backwards.
        phase += step + Math.max(-.65 * step, Math.min(.65 * step, correction));
      }
      let settling = false;
      for (let i = 0; i < 3; i++) {
        const target = args.spread || args.hold ? 0 : args.envelope[i];
        const delta = target - envelope[i];
        if (Math.abs(delta) < .0002 || args.spread) envelope[i] = target;
        else {
          settling = true;
          envelope[i] += delta * (1 - Math.exp(-dt * 12));
        }
      }
      const angle = args.spread || args.hold ? 0 : phase;
      return {running, settling, angle, lateral: envelope[0] * Math.sin(angle),
        vertical: envelope[1] * Math.sin(angle), yaw: envelope[2] * Math.cos(angle)};
    },
  };
}
