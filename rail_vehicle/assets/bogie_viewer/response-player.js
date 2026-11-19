/** One sample index drives both the model pose and the response cursor. */
export function responsePlayer(invalidate) {
  const get = id => document.getElementById(id);
  const panel = get("response"), slider = get("sim-time"), play = get("sim-play");
  const svg = get("response-plot"), cursor = get("response-cursor");
  let result, index = 0, position = 0, last = null, running = false;
  function pause() {
    running = false;
    last = null;
    play.textContent = "播放";
  }
  function seek(sample) {
    index = Math.max(0, Math.min(Number(slider.max), Math.round(sample)));
    position = index;
    slider.value = index;
    const x = 1000*index/Number(slider.max);
    cursor.setAttribute("x1", x);
    cursor.setAttribute("x2", x);
    const c = result.channels;
    get("sim-values").textContent = (index/result.sample_hz).toFixed(2) + " s　行程 " +
      (1000*c.travel_m[index]).toFixed(2) + " mm　构架 " + c.accel_m_s2[index].toFixed(3) + " m/s²" +
      (Math.abs(c.travel_m[index]) > .04 ? "　显示行程限于 ±40 mm" : "");
    panel.dataset.sample = index;
    invalidate();
  }
  slider.oninput = () => { pause(); seek(Number(slider.value)); };
  get("sim-reset").onclick = () => { pause(); seek(0); };
  play.onclick = () => {
    if (running) { pause(); return; }
    if (index === Number(slider.max)) seek(0);
    running = true;
    last = performance.now();
    play.textContent = "暂停";
    invalidate();
  };
  function selectOnPlot(event) {
    const rect = svg.getBoundingClientRect();
    pause();
    seek((event.clientX-rect.left)/rect.width*Number(slider.max));
  }
  svg.onpointerdown = event => { svg.setPointerCapture(event.pointerId); selectOnPlot(event); };
  svg.onpointermove = event => { if (svg.hasPointerCapture(event.pointerId)) selectOnPlot(event); };
  return {
    load(next) {
      if (!next) { pause(); result = null; panel.hidden = true; return; }
      if (result && JSON.stringify(result.parameters) === JSON.stringify(next.parameters)) return;
      result = next;
      panel.hidden = false;
      pause();
      const series = result.channels.travel_m;
      slider.max = series.length-1;
      const nominal = result.nominal_travel_m ?? [];
      const scale = Math.max(.001, ...series.map(Math.abs), ...nominal.map(Math.abs));
      const path = values => values.map((v,i) =>
        (i ? "L" : "M") + (i*1000/(values.length-1)).toFixed(2) + " " + (32-v*32/scale).toFixed(2)).join("");
      get("response-path").setAttribute("d", path(series));
      get("nominal-path").setAttribute("d", path(nominal));
      get("response-scale").textContent = "悬挂行程 ±" + (scale*1000).toFixed(2) + " mm" +
        (nominal.length ? "　虚线：名义刚度" : "");
      seek(0);
    },
    tick(now) {
      if (!result) return null;
      if (running) {
        const next = Math.min(+slider.max, position + Math.max(0, now-last)/1000*result.sample_hz*Number(get("sim-rate").value));
        last = now;
        if (Math.round(next) !== index) seek(next);
        position = next;
        if (next === +slider.max) pause();
      }
      const c = result.channels;
      return {running, settling:false, angle:c.wheel_rad[index], vertical:Math.max(-.04, Math.min(.04,c.travel_m[index])),
        lateral:c.lateral_m[index], yaw:c.yaw_rad[index]};
    },
  };
}
