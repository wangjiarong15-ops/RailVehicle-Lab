/* Canvas running curves: speed / lateral / vertical on one shared time axis.
   Click or drag to seek the unified replay timeline; anomaly points and event
   bands come from the same detector results as the event list. */

const COLORS = {
  speed: "#82bbf6", lateral: "#50d3c3", vertical: "#fca549",
  anomaly: "#ff7a6b", cursor: "#e3edf9", grid: "#1d3048", axis: "#7d92a8",
  threshold: "rgba(252, 165, 73, .65)",
  bandLateral: "rgba(80, 211, 195, .10)", bandVertical: "rgba(252, 165, 73, .10)",
};
const CHANNELS = [
  {field: "speed_kmh", label: "速度", unit: "km/h", color: COLORS.speed},
  {field: "lateral_accel", label: "横向加速度", unit: "m/s²", color: COLORS.lateral},
  {field: "vertical_accel", label: "垂向加速度", unit: "m/s²", color: COLORS.vertical},
];
const GUTTER = 56, RIGHT = 16, TOP = 6, BOTTOM = 24, ROW_GAP = 14;

function niceStep(span, target) {
  const raw = span / target;
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const factor of [1, 2, 5, 10]) if (raw <= factor * power) return factor * power;
  return 10 * power;
}

export function createCharts(canvas, {onSeek, onHover}) {
  const ctx = canvas.getContext("2d");
  const tooltip = document.createElement("div");
  tooltip.className = "curve-tooltip";
  tooltip.hidden = true;
  canvas.parentElement.append(tooltip);
  let data = null, cursor = {position: 0, index: 0}, flashRow = null, hoverIndex = null;
  let width = 0, height = 0, dragging = false;

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    width = canvas.clientWidth; height = canvas.clientHeight;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  function layout() {
    const plotW = Math.max(10, width - GUTTER - RIGHT);
    const rowH = Math.max(24, (height - TOP - BOTTOM - ROW_GAP * 2) / 3);
    const duration = data ? Math.max(data.track.duration, 1e-6) : 1;
    const x = (time) => GUTTER + time / duration * plotW;
    const rows = CHANNELS.map((channel, index) => {
      const top = TOP + index * (rowH + ROW_GAP);
      let min = 0, max = 1;
      if (data) {
        const values = data.track.samples.map((row) => row[channel.field]);
        if (index === 0) {
          min = Math.min(0, ...values); max = Math.max(...values, min + 1);
          const pad = (max - min) * .1 || 1;
          min -= pad; max += pad;
        } else {
          const extent = Math.max(...values.map(Math.abs), data.threshold) * 1.18 || 1;
          min = -extent; max = extent;
        }
      }
      const y = (value) => top + rowH - (value - min) / (max - min) * rowH;
      return {...channel, top, rowH, min, max, x, y};
    });
    return {plotW, rowH, duration, x, rows};
  }

  function draw() {
    ctx.clearRect(0, 0, width, height);
    if (!data || !width || !height) return;
    const {track, events, threshold} = data;
    const {plotW, duration, x, rows} = layout();

    const step = niceStep(duration, Math.max(3, Math.floor(plotW / 110)));
    ctx.font = "10.5px 'Segoe UI', 'Microsoft YaHei', system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (let time = 0; time <= duration + 1e-9; time += step) {
      const px = x(time);
      ctx.strokeStyle = COLORS.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px, TOP); ctx.lineTo(px, height - BOTTOM); ctx.stroke();
      ctx.fillStyle = COLORS.axis; ctx.textAlign = "center";
      ctx.fillText(`${Math.round(time * 100) / 100}s`, px, height - BOTTOM / 2);
    }

    for (const row of rows) {
      ctx.fillStyle = "rgba(20, 35, 54, .5)";
      ctx.fillRect(GUTTER, row.top, plotW, row.rowH);
      if (row.field !== "speed_kmh") {
        for (const sign of [-1, 1]) {
          const py = row.y(sign * threshold);
          ctx.strokeStyle = COLORS.threshold; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(GUTTER, py); ctx.lineTo(GUTTER + plotW, py); ctx.stroke();
          ctx.setLineDash([]);
        }
        ctx.fillStyle = COLORS.threshold; ctx.textAlign = "left";
        ctx.fillText(`+阈值 ${threshold}`, GUTTER + 4, row.y(threshold) - 7);
        const zero = row.y(0);
        ctx.strokeStyle = COLORS.grid;
        ctx.beginPath(); ctx.moveTo(GUTTER, zero); ctx.lineTo(GUTTER + plotW, zero); ctx.stroke();
      }
      for (const event of events) {
        if (event.field !== row.field) continue;
        const x0 = x(track.offsets[event.start_row]);
        const x1 = x(track.offsets[event.start_row + event.sample_count - 1]);
        ctx.fillStyle = row.field === "lateral_accel" ? COLORS.bandLateral : COLORS.bandVertical;
        ctx.fillRect(x0, row.top, Math.max(x1 - x0, 2), row.rowH);
      }
      ctx.strokeStyle = COLORS.grid; ctx.lineWidth = 1;
      ctx.strokeRect(GUTTER + .5, row.top + .5, plotW - 1, row.rowH - 1);
      ctx.fillStyle = COLORS.axis; ctx.textAlign = "right";
      ctx.fillText(row.max.toFixed(row.field === "speed_kmh" ? 0 : 2), GUTTER - 6, row.top + 8);
      ctx.fillText(row.min.toFixed(row.field === "speed_kmh" ? 0 : 2), GUTTER - 6, row.top + row.rowH - 8);
      ctx.fillStyle = row.color; ctx.textAlign = "left";
      ctx.font = "600 11px 'Segoe UI', 'Microsoft YaHei', system-ui, sans-serif";
      ctx.fillText(`${row.label} · ${row.unit}`, GUTTER + 6, row.top + 10);
      ctx.font = "10.5px 'Segoe UI', 'Microsoft YaHei', system-ui, sans-serif";

      ctx.strokeStyle = row.color; ctx.lineWidth = 1.6; ctx.beginPath();
      track.samples.forEach((sample, index) => {
        const px = x(track.offsets[index]), py = row.y(sample[row.field]);
        index ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      });
      ctx.stroke();

      if (row.field !== "speed_kmh") {
        track.samples.forEach((sample, index) => {
          if (Math.abs(sample[row.field]) <= threshold) return;
          const px = x(track.offsets[index]), py = row.y(sample[row.field]);
          ctx.fillStyle = COLORS.anomaly;
          ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill();
          if (index === flashRow) {
            ctx.strokeStyle = COLORS.anomaly; ctx.lineWidth = 1.5;
            ctx.beginPath(); ctx.arc(px, py, 7, 0, Math.PI * 2); ctx.stroke();
          }
        });
      }
    }

    const cx = x(Math.min(cursor.position, duration));
    ctx.strokeStyle = COLORS.cursor; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(cx, TOP); ctx.lineTo(cx, height - BOTTOM); ctx.stroke();
    ctx.setLineDash([]);
    const sample = track.samples[cursor.index];
    if (sample) for (const row of rows) {
      const py = row.y(sample[row.field]);
      ctx.fillStyle = row.color;
      ctx.beginPath(); ctx.arc(cx, py, 3.5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#0b1521"; ctx.lineWidth = 1.5; ctx.stroke();
    }
    if (hoverIndex !== null && track.samples[hoverIndex]) {
      const hx = x(track.offsets[hoverIndex]);
      ctx.strokeStyle = "rgba(227, 237, 249, .35)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(hx, TOP); ctx.lineTo(hx, height - BOTTOM); ctx.stroke();
    }
  }

  function indexFromEvent(event) {
    const rect = canvas.getBoundingClientRect();
    const {duration} = layout();
    const plotW = Math.max(10, width - GUTTER - RIGHT);
    const time = Math.max(0, Math.min(duration, (event.clientX - rect.left - GUTTER) / plotW * duration));
    const track = data.track;
    let index = track.indexAt(time);
    if (index + 1 < track.samples.length &&
        Math.abs(track.offsets[index + 1] - time) < Math.abs(track.offsets[index] - time)) index += 1;
    return index;
  }

  canvas.addEventListener("pointerdown", (event) => {
    if (!data || event.button !== 0) return;
    dragging = true;
    canvas.setPointerCapture(event.pointerId);
    onSeek(indexFromEvent(event));
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!data) return;
    const index = indexFromEvent(event);
    if (dragging) { onSeek(index); return; }
    hoverIndex = index;
    const sample = data.track.samples[index];
    const rect = canvas.getBoundingClientRect();
    tooltip.hidden = false;
    tooltip.innerHTML =
      `<strong>${sample.timestamp.replace("+00:00", "Z")}</strong>` +
      `<span>样本 #${index + 1} · CSV 第 ${sample.csv_line ?? "—"} 行 · ${data.track.offsets[index].toFixed(2)}s</span>` +
      CHANNELS.map((channel) => {
        const value = sample[channel.field];
        const over = channel.field !== "speed_kmh" && Math.abs(value) > data.threshold;
        return `<span><i style="background:${channel.color}"></i>${channel.label} ${value.toFixed(3)} ${channel.unit}${over ? " · 超阈值" : ""}</span>`;
      }).join("");
    const left = Math.min(event.clientX - rect.left + 14, width - 230);
    tooltip.style.left = Math.max(4, left) + "px";
    tooltip.style.top = Math.max(4, event.clientY - rect.top - 10) + "px";
    draw();
  });
  const endDrag = () => { dragging = false; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("pointerleave", () => {
    hoverIndex = null; tooltip.hidden = true; draw();
  });

  new ResizeObserver(resize).observe(canvas);
  resize();

  return {
    setData(next) { data = next; flashRow = null; hoverIndex = null; tooltip.hidden = true; draw(); },
    setCursor(position, index) { cursor = {position, index}; draw(); },
    setFlash(row) { flashRow = row; draw(); },
    resize, draw,
    get cursorIndex() { return cursor.index; },
  };
}