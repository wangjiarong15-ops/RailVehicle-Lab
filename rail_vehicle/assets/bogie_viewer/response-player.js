/** One sample index drives both the model pose and the response cursor. */
export function responsePlayer(invalidate) {
  const get = id => document.getElementById(id);
  const panel = get("response"), slider = get("sim-time"), play = get("sim-play");
  const svg = get("response-plot"), cursor = get("response-cursor");
  const axleSignal = get("axle-signal");
  const limitTravel = value => Math.max(-.04, Math.min(.04, value));
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
    updateReadout();
    panel.dataset.sample = index;
    invalidate();
  }
  function updateReadout() {
    const c = result.channels;
    const mm = v => (v*1000).toFixed(2);
    const dual = Boolean(c.front_travel_m);
    const front = dual ? c.front_travel_m[index] : c.travel_m[index];
    const rear = dual ? c.rear_travel_m[index] : front;
    const support = dual && axleSignal.value === "support";
    const readout = dual ? (support ? "轨面输入" : "行程") + "　B 端 " +
      mm(support ? c.front_support_m[index] : front) + " mm　A 端 " +
      mm(support ? c.rear_support_m[index] : rear) + " mm　构架点头 " +
      (c.pitch_rad[index]*1000).toFixed(2) + " mrad" : "行程 " + mm(front) + " mm";
    get("sim-values").textContent = (index/result.sample_hz).toFixed(2) + " s　" + readout +
      "　构架加速度 " + c.accel_m_s2[index].toFixed(3) + " m/s²" +
      (Math.max(Math.abs(front), Math.abs(rear)) > .04 ? "　显示行程限于 ±40 mm" : "");
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
  function drawSeries() {
    const c = result.channels;
    const dual = Boolean(c.front_travel_m);
    const key = axleSignal.value === "support" ? "support_m" : "travel_m";
    const series = dual ? c["front_" + key] : c.travel_m;
    const rear = dual ? c["rear_" + key] : [];
    const nominal = result.nominal_travel_m ?? [];
    const scale = Math.max(.001, ...series.map(Math.abs), ...nominal.map(Math.abs), ...rear.map(Math.abs));
    const path = values => values.map((v,i) =>
      (i ? "L" : "M") + (i*1000/(values.length-1)).toFixed(2) + " " + (32-v*32/scale).toFixed(2)).join("");
    get("response-path").setAttribute("d", path(series));
    get("nominal-path").setAttribute("d", path(nominal));
    get("rear-path").setAttribute("d", path(rear));
    const label = dual && key === "support_m" ? "轨面输入" : "悬挂行程";
    svg.setAttribute("aria-label", label + "曲线，点击定位");
    get("response-scale").textContent = label +
      " ±" + (scale*1000).toFixed(2) + " mm" + (dual ? "　实线 B 前轴 · 虚线 A 后轴" :
        nominal.length ? "　虚线：名义刚度" : "");
  }
  axleSignal.onchange = () => { drawSeries(); updateReadout(); };
  function seekCrest(rear) {
    const frontTime = result.bump_start_s + result.bump_length_m/(2*result.parameters.speed_m_s);
    pause();
    seek((frontTime + (rear ? result.axle_delay_s : 0))*result.sample_hz);
  }
  get("front-crest").onclick = () => seekCrest(false);
  get("rear-crest").onclick = () => seekCrest(true);
  return {
    command(action, seconds) {
      if (!result || !Number.isFinite(seconds)) return;
      pause();
      seek(seconds*result.sample_hz);
      if (action === "play") play.onclick();
    },
    load(next) {
      if (!next) { pause(); result = null; panel.hidden = true; return; }
      if (result && JSON.stringify(result.parameters) === JSON.stringify(next.parameters)) return;
      result = next;
      panel.hidden = false;
      pause();
      slider.max = result.channels.travel_m.length-1;
      get("axle-controls").hidden = !result.channels.front_travel_m;
      axleSignal.value = "travel";
      drawSeries();
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
      const front = limitTravel((c.front_travel_m ?? c.travel_m)[index]);
      const rear = limitTravel((c.rear_travel_m ?? c.travel_m)[index]);
      return {running, settling:false, angle:c.wheel_rad[index], vertical:(front+rear)/2,
        pitch:result.wheelbase_m ? Math.asin((front-rear)/result.wheelbase_m) : 0,
        lateral:c.lateral_m[index], yaw:c.yaw_rad[index]};
    },
  };
}
