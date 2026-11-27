/** One sample index drives both the model pose and the response cursor. */
export function responsePlayer(invalidate) {
  const get = id => document.getElementById(id);
  const panel = get("response"), slider = get("sim-time"), play = get("sim-play");
  const svg = get("response-plot"), cursor = get("response-cursor");
  const axleSignal = get("axle-signal");
  const lateralSignal = get("lateral-signal");
  const limitTravel = value => Math.max(-.04, Math.min(.04, value));
  let result, index = 0, position = 0, last = null, running = false;
  const signalKey = () => result?.coupled && lateralSignal.value === "travel_m" ? "secondary_y_m" : lateralSignal.value;
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
    if (result.coupled) {
      const q = result.coupled.q[index];
      get("sim-values").textContent = (index/result.sample_hz).toFixed(3) + " s　车体 Y/Z " +
        mm(q[10]) + "/" + mm(q[0]) + " mm　侧滚/点头/摇头 " + [q[11], q[1], q[12]].map(mm).join("/") +
        " mrad　横向加速度 " + c.accel_m_s2[index].toFixed(3) + " m/s²　动画 " +
        result.coupled.display_gain.toFixed(2) + "×";
      const nearby = result.observed?.[signalKey()]?.[index];
      if (nearby) get("sim-values").textContent += "　观测 " + nearby.value.toPrecision(4) + "（SI）· CSV 第 " + nearby.csv_line + " 行";
      if (result.alternative) get("sim-values").textContent += "　方案横向加速度 " +
        result.alternative.channels.accel_m_s2[index].toFixed(3) + " m/s²";
      return;
    }
    if (result.lateral_model) {
      get("sim-values").textContent = (index/result.sample_hz).toFixed(3) + " s　车体横移 " + mm(c.body_y_m[index]) +
        " mm　侧滚 " + mm(c.roll_rad[index]) + " mrad　横向加速度 " + c.accel_m_s2[index].toFixed(3) +
        " m/s²　动画 " + displayGain().toFixed(2) + "×";
      return;
    }
    const dual = Boolean(c.front_travel_m) && !result.coupled;
    const front = dual ? c.front_travel_m[index] : c.travel_m[index];
    const rear = dual ? c.rear_travel_m[index] : front;
    const support = dual && axleSignal.value === "support";
    const readout = dual ? (support ? "轨面输入" : "行程") + "　B 端 " +
      mm(support ? c.front_support_m[index] : front) + " mm　A 端 " +
      mm(support ? c.rear_support_m[index] : rear) + " mm　构架点头 " +
      (c.pitch_rad[index]*1000).toFixed(2) + " mrad" : "行程 " + mm(front) + " mm";
    get("sim-values").textContent = (index/result.sample_hz).toFixed(2) + " s　" + readout +
      "　构架加速度 " + c.accel_m_s2[index].toFixed(3) + " m/s²" +
      (result.vehicle_model ? "　构架 " + mm(c.frame_m[index]) + " mm　轮对 B/A " +
        mm(c.front_wheel_m[index]) + "/" + mm(c.rear_wheel_m[index]) + " mm　二系 " +
        mm(c.secondary_m[index]) + " mm　动画 " + displayGain().toFixed(2) + "×" :
        Math.max(Math.abs(front), Math.abs(rear)) > .04 ? "　显示行程限于 ±40 mm" : "");
  }
  slider.oninput = () => { pause(); seek(Number(slider.value)); };
  get("sim-reset").onclick = () => { pause(); seek(0); };
  get("sim-prev").onclick = () => { pause(); seek(index-1); };
  get("sim-next").onclick = () => { pause(); seek(index+1); };
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
    const lateral = result.lateral_model || result.coupled;
    const dual = Boolean(c.front_travel_m) && !result.coupled;
    const key = axleSignal.value === "support" ? "support_m" : "travel_m";
    const series = lateral ? c[signalKey()] : dual ? c["front_" + key] : c.travel_m;
    const rear = result.alternative?.channels[signalKey()] ?? (dual ? c["rear_" + key] : []);
    const nominal = result.nominal_travel_m ?? [];
    const observations = result.observed?.[signalKey()] ?? [];
    const scale = Math.max(.001, ...series.map(Math.abs), ...nominal.map(Math.abs), ...rear.map(Math.abs),
      ...observations.map(point => point ? Math.abs(point.value) : 0));
    const path = values => values.map((v,i) =>
      (i ? "L" : "M") + (i*1000/(values.length-1)).toFixed(2) + " " + (32-v*32/scale).toFixed(2)).join("");
    get("response-path").setAttribute("d", path(series));
    get("nominal-path").setAttribute("d", path(nominal));
    get("rear-path").setAttribute("d", path(rear));
    get("observation-path").setAttribute("d", observations.map((point,i)=>{
      if (!point || point.csv_line===observations[i-1]?.csv_line) return "";
      const x=1000*point.observed_time_s/((series.length-1)/result.sample_hz);
      return "M"+x.toFixed(2)+" "+(32-point.value*32/scale).toFixed(2)+"h.15";
    }).join(""));
    const label = lateral ? lateralSignal.selectedOptions[0].textContent :
      dual && key === "support_m" ? "轨面输入" : "悬挂行程";
    const unit = lateral && lateralSignal.value === "roll_rad" ? "mrad" :
      lateral && lateralSignal.value.includes("accel") ? "m/s²" :
      lateral && lateralSignal.value.includes("velocity") ? "m/s" : "mm";
    svg.setAttribute("aria-label", label + "曲线，点击定位");
    get("response-scale").textContent = label +
      " ±" + (scale*(unit.startsWith("m/") ? 1 : 1000)).toFixed(2) + " " + unit + (dual ? "　实线 B 前轴 · 虚线 A 后轴" :
        result.alternative ? "　实线基准 · 虚线方案" : nominal.length ? "　虚线：名义刚度" : "") +
        (observations.length ? "　蓝点为对齐观测" : "");
  }
  axleSignal.onchange = () => { drawSeries(); updateReadout(); };
  lateralSignal.onchange = () => { drawSeries(); updateReadout(); };
  get("lateral-peak").onclick = () => {
    const values = result.channels[signalKey()];
    const peak = values.reduce((best, value, i) => Math.abs(value) > Math.abs(values[best]) ? i : best, 0);
    pause(); seek(peak);
  };
  function seekCrest(rear) {
    const frontTime = result.bump_start_s + result.bump_length_m/(2*result.parameters.speed_m_s);
    pause();
    seek((frontTime + (rear ? result.axle_delay_s : 0))*result.sample_hz);
  }
  get("front-crest").onclick = () => seekCrest(false);
  get("rear-crest").onclick = () => seekCrest(true);
  function displayGain() {
    const c = result.channels;
    if (result.lateral_model) return Math.min(result.parameters.gain, .04/Math.max(
      Math.abs(c.base_y_m[index]), Math.abs(c.travel_m[index]), ...c.spring_m[index].map(Math.abs), 1e-12));
    return Math.min(result.parameters.gain, .04/Math.max(Math.abs(c.front_wheel_m[index]),
      Math.abs(c.rear_wheel_m[index]), Math.abs(c.frame_m[index]),
      Math.abs(c.front_travel_m[index]), Math.abs(c.rear_travel_m[index]), Math.abs(c.secondary_m[index]), 1e-12));
  }
  return {
    partResponse(id) {
      if (result?.coupled && id) {
        const data = result.coupled, c = result.channels, q = data.q[index], roll = q[14+3*data.index];
        const sign = id.endsWith("_l") ? -1 : id.endsWith("_r") ? 1 : 0;
        if (id.startsWith("air_spring_")) {
          const delta = c.secondary_m[index] + sign*(q[11]-roll)*data.dimensions.side_beam_centres/2;
          return "二系垂向行程 " + (delta*1000).toFixed(2) + " mm · 线性计算";
        }
        const end = id.match(/_[ab](?:_|$)/)?.[0][1];
        if (end && /^(coil|damper|spring_seat)_/.test(id)) {
          const prefix = end === "b" ? "front" : "rear";
          const delta = c[prefix+"_travel_m"][index] + sign*roll*data.dimensions.axlebox_centres/2;
          return "一系行程 " + (delta*1000).toFixed(2) + " mm · 线性计算";
        }
      }
      if (result?.lateral_model && id?.startsWith("air_spring_")) {
        const side = id.endsWith("_l") ? 0 : 1;
        const spring = result.channels.spring_m[index][side];
        return "二系垂向相对变化 " + (1000*spring).toFixed(2) + " mm · 线性计算";
      }
      if (!result?.vehicle_model || !id) return "";
      const c = result.channels;
      const end = id.match(/_[ab](?:_|$)/)?.[0][1];
      const prefix = end === "b" ? "front" : "rear";
      if (id.startsWith("air_spring")) return "二系行程 " + (1000*c.secondary_m[index]).toFixed(2) + " mm";
      if (end && /^(coil|damper|spring_seat)_/.test(id))
        return "一系行程 " + (1000*c[prefix+"_travel_m"][index]).toFixed(2) + " mm";
      if (end && /^(wheel|axle|axlebox)_/.test(id))
        return "轮对垂向 " + (1000*c[prefix+"_wheel_m"][index]).toFixed(2) + " mm";
      return "";
    },
    command(action, seconds) {
      if (!result || !["play", "pause", "reset"].includes(action)) return;
      if (action !== "play" || Number.isFinite(seconds)) pause();
      if (action === "reset") seek(0);
      else if (Number.isFinite(seconds)) seek(seconds*result.sample_hz);
      if (action === "play" && !running) play.onclick();
    },
    load(next) {
      if (!next) { pause(); result = null; panel.hidden = true; return; }
      if (result && JSON.stringify(result.parameters) === JSON.stringify(next.parameters)) return;
      result = next;
      panel.hidden = false;
      pause();
      slider.max = result.channels.travel_m.length-1;
      get("axle-controls").hidden = !result.channels.front_travel_m || Boolean(result.coupled);
      get("sim-step").hidden = !result.vehicle_model && !result.lateral_model;
      get("lateral-controls").hidden = !(result.lateral_model || result.coupled);
      lateralSignal.querySelectorAll("[data-coupled]").forEach(option => {option.hidden = !result.coupled;});
      if (result.coupled) lateralSignal.value = "accel_m_s2";
      else if (lateralSignal.selectedOptions[0]?.hidden) lateralSignal.value = "body_y_m";
      get("front-crest").hidden = get("rear-crest").hidden = Boolean(result.vehicle_model);
      get("response-duration").textContent = "0 – " + Number(slider.max)/result.sample_hz + " s";
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
      if (result.coupled) {
        const data = result.coupled, q = data.q[index], y = data.rail_y[index], gain = data.display_gain;
        const i = data.index, f = 2+2*i, j = 13+3*i, axle = 2*i;
        panel.dataset.displayGain = gain.toFixed(4);
        return {running, settling:false, angle:c.wheel_rad[index], vertical:q[f]*gain, pitch:q[f+1]*gain,
          lateral:q[j]*gain, yaw:q[j+2]*gain, wheelOffsets:q.slice(6+axle, 8+axle).map(v=>v*gain),
          body:{frameRoll:q[j+1]*gain, wheelY:y.slice(axle, axle+2).map(v=>v*gain),
            y:c.secondary_y_m[index]*gain, heave:c.secondary_m[index]*gain, roll:(q[11]-q[j+1])*gain},
          vehicle:{q, y, gain, half:data.half_spacing, span:data.half_wheelbase, travel:data.secondary_y?.[index]},
          alternative:result.alternative ? {
            q:result.alternative.coupled.q[index],y:result.alternative.coupled.rail_y[index],
            half:data.half_spacing,span:data.half_wheelbase,gain,
            travel:result.alternative.coupled.secondary_y[index],
          } : null};
      }
      if (result.lateral_model) {
        const gain = displayGain(), roll = c.roll_rad[index]*gain;
        const y = (c.body_y_m[index]-c.base_y_m[index])*gain + result.parameters.com_height_m*Math.sin(roll);
        panel.dataset.displayGain = gain.toFixed(4);
        return {running, settling:false, angle:c.wheel_rad[index], vertical:0, yaw:0,
          lateral:c.base_y_m[index]*gain,
          body:{y, heave:result.parameters.com_height_m*(1-Math.cos(roll)), roll, rigidSupport:true}};
      }
      if (result.vehicle_model) {
        const front = c.front_wheel_m[index], rear = c.rear_wheel_m[index];
        // Preserve the relative geometry with one common display scale.
        const visibleGain = displayGain();
        panel.dataset.displayGain = visibleGain.toFixed(4);
        return {running, settling:false, angle:c.wheel_rad[index], vertical:c.frame_m[index]*visibleGain,
          pitch:c.pitch_rad[index]*visibleGain, lateral:0, yaw:0, wheelOffsets:[front*visibleGain, rear*visibleGain],
          body:{heave:c.secondary_m[index]*visibleGain}};
      }
      const front = limitTravel((c.front_travel_m ?? c.travel_m)[index]);
      const rear = limitTravel((c.rear_travel_m ?? c.travel_m)[index]);
      return {running, settling:false, angle:c.wheel_rad[index], vertical:(front+rear)/2,
        pitch:result.wheelbase_m ? Math.asin((front-rear)/result.wheelbase_m) : 0,
        lateral:c.lateral_m[index], yaw:c.yaw_rad[index],
        body:c.secondary_travel_m ? {heave:limitTravel(c.secondary_travel_m[index])} : undefined};
    },
  };
}
