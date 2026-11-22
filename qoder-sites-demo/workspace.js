/* Running-analysis workspace: local CSV intake, statistics, threshold events,
   evidence panel and the unified timeline that drives curves and the 3D scene.
   The 3D motion is category C (preset teaching illustration synced to events);
   acceleration readings are never integrated into frame displacement. */

import {
  DEFAULT_THRESHOLD, EVENT_DURATION_NOTE, MAX_CSV_SIZE_BYTES,
  analyzeRunData, controlReplay, advanceReplay, eventPositions,
  gapCutoff, illustrationPhase, initialReplayState, motionEnvelope,
  prepareReplay, replayScene, validateCsv,
} from "./analysis.js";
import {createCharts} from "./charts.js";

const esc = (value) => String(value).replace(/[&<>"']/g, (char) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
const zulu = (iso) => iso.replace("+00:00", "Z");
const num = (value, digits = 3) => Number(value).toFixed(digits);
const CHANNEL_LABEL = {lateral_accel: "横向", vertical_accel: "垂向"};

const LIMITATIONS =
  "统计异常仅表示加速度绝对值超过用户设定阈值，不构成轴承、减振器或其他零件损坏的诊断结论。" +
  "三维高亮为相关结构示意，运动为预设教学动画（类别 C），并非由加速度积分重建的实车运动；" +
  "当前缺乏积分初始条件、模型参数与校准数据，未进行实车运动重建。";

export function createAnalysisWorkspace({elements, rig, inspector, partIdsForGroups, focusGroup, focusPart, describePart, openDrawer}) {
  const el = elements;
  const charts = createCharts(el.curveCanvas, {
    onSeek: (index) => seek(index),
  });
  let dataset = null, state = initialReplayState(), selectedEvent = null;
  let importErrors = [], sourceKind = "demo-1", lastNow = null, lastScene = null;
  let envelope = [0, 0, 0], scrubbing = false, loadedOnce = false;
  const readoutCache = {};

  /* ---------- dataset construction ---------- */

  function buildDataset(content, {name, fictional, kind}) {
    const validation = validateCsv(content);
    if (validation.errors.length || !validation.rows.length) {
      importErrors = validation.errors.length ? validation.errors : ["CSV 中没有可导入的数据行。"];
      renderSource();
      return false;
    }
    importErrors = [];
    const rows = validation.rows;
    const track = prepareReplay(rows);
    const threshold = DEFAULT_THRESHOLD;
    const positioned = eventPositions(track, threshold);
    dataset = {
      name, fictional, kind, content, rows, track, threshold,
      validation,
      cutoff: gapCutoff(track),
      analysis: analyzeRunData(rows, threshold),
      events: positioned.map(([event, startRow]) => {
        const endRow = startRow + event.sample_count - 1;
        let peakRow = startRow;
        for (let row = startRow; row <= endRow; row++) {
          if (track.samples[row].timestamp === event.maximum_timestamp &&
              Math.abs(track.samples[row][event.field]) === event.maximum_absolute_value) { peakRow = row; break; }
        }
        return {...event, start_row: startRow, end_row: endRow, peak_row: peakRow};
      }),
    };
    sourceKind = kind;
    state = initialReplayState(state.rate);
    selectedEvent = null;
    lastScene = null;
    envelope = [0, 0, 0];
    charts.setData({track, events: dataset.events, threshold});
    el.scrub.max = String(track.duration);
    el.scrub.step = String(Math.max(track.duration / 20000, .0005));
    el.scrub.value = "0";
    el.threshold.value = String(threshold);
    renderAll();
    syncUI(performance.now() / 1000, true);
    return true;
  }

  async function loadDemo(index) {
    const response = await fetch(`./assets/demo-run-${index + 1}.csv`);
    if (!response.ok) throw new Error(`演示数据请求失败：${response.status}`);
    const content = await response.text();
    return buildDataset(content, {name: `虚构演示批次 ${index + 1}（非实测）`, fictional: true, kind: `demo-${index + 1}`});
  }

  async function importFile(file) {
    if (file.size > MAX_CSV_SIZE_BYTES) {
      importErrors = ["CSV 文件超过 50 MB 大小上限。"];
      renderSource();
      return;
    }
    let content;
    try {
      content = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
    } catch {
      importErrors = ["CSV 文件必须使用 UTF-8 编码（可包含 BOM）。"];
      renderSource();
      return;
    }
    const ok = buildDataset(content, {name: file.name, fictional: false, kind: "import"});
    if (ok) {
      let option = el.source.querySelector('option[value="import"]');
      if (!option) {
        option = document.createElement("option");
        option.value = "import";
        el.source.append(option);
      }
      option.textContent = `已导入：${file.name}`;
      el.source.value = "import";
    }
  }

  function recompute() {
    if (!dataset) return;
    dataset.analysis = analyzeRunData(dataset.rows, dataset.threshold);
    const positioned = eventPositions(dataset.track, dataset.threshold);
    dataset.events = positioned.map(([event, startRow]) => {
      const endRow = startRow + event.sample_count - 1;
      let peakRow = startRow;
      for (let row = startRow; row <= endRow; row++) {
        if (dataset.track.samples[row].timestamp === event.maximum_timestamp &&
            Math.abs(dataset.track.samples[row][event.field]) === event.maximum_absolute_value) { peakRow = row; break; }
      }
      return {...event, start_row: startRow, end_row: endRow, peak_row: peakRow};
    });
    if (selectedEvent !== null && !dataset.events[selectedEvent]) selectedEvent = null;
    charts.setData({track: dataset.track, events: dataset.events, threshold: dataset.threshold});
    lastScene = null;
    renderAll();
  }

  /* ---------- timeline ---------- */

  function now() { return performance.now() / 1000; }

  function seek(index) {
    if (!dataset) return;
    const clamped = Math.max(0, Math.min(dataset.track.samples.length - 1, index));
    state = controlReplay(state, "seek", now(), dataset.track.duration,
      {position: dataset.track.offsets[clamped]});
    syncUI(now(), true);
  }

  function act(action, options) {
    if (!dataset) return;
    state = controlReplay(state, action, now(), dataset.track.duration, options);
    syncUI(now(), true);
  }

  function updateScene() {
    const track = dataset.track;
    const index = track.indexAt(state.position);
    const scene = replayScene(track, state.position, index, dataset.threshold, dataset.cutoff);
    if (scene !== lastScene) {
      lastScene = scene;
      inspector.setAlerts(partIdsForGroups(scene.parts));
      el.sceneChip.textContent = scene.label;
      el.sceneChip.dataset.scene = sceneKey(scene);
      el.sceneChip.classList.remove("pulse");
      void el.sceneChip.offsetWidth;
      el.sceneChip.classList.add("pulse");
    }
    return index;
  }

  function tick(current) {
    if (!dataset) { rig.pose(0, 0, 0, 0); return; }
    const dt = lastNow === null ? 0 : Math.min(Math.max(0, current - lastNow), .1);
    lastNow = current;
    if (state.lastTick !== null) state = {...state, lastTick: Math.max(state.lastTick, current - .1)};
    state = advanceReplay(state, current, dataset.track.duration);
    const track = dataset.track;
    const index = updateScene();
    const scene = replayScene(track, state.position, index, dataset.threshold, dataset.cutoff);
    const target = motionEnvelope(scene.mode);
    for (let channel = 0; channel < 3; channel++) {
      const delta = target[channel] - envelope[channel];
      envelope[channel] = Math.abs(delta) < .0002 ? target[channel]
        : envelope[channel] + delta * (1 - Math.exp(-dt * 12));
    }
    const phase = illustrationPhase(state.position);
    rig.pose(phase, envelope[0] * Math.sin(phase), envelope[1] * Math.sin(phase), envelope[2] * Math.cos(phase));
    charts.setCursor(state.position, index);
    syncUI(current, false);
  }

  function sceneKey(scene) {
    return scene.parts.length === 0 && scene.label.startsWith("长采样") ? "gap"
      : scene.mode === "正常运行" ? "normal" : scene.mode === "横向振动" ? "lateral"
      : scene.mode === "垂向振动" ? "vertical" : "combined";
  }

  function setText(node, text) {
    if (readoutCache[node.id] !== text) { readoutCache[node.id] = text; node.textContent = text; }
  }

  function syncUI(current, force) {
    if (!dataset) return;
    const track = dataset.track;
    const index = track.indexAt(state.position);
    const sample = track.samples[index];
    setText(el.tlTime, zulu(sample.timestamp));
    setText(el.tlElapsed, `${state.position.toFixed(2)} s / ${track.duration.toFixed(2)} s`);
    setText(el.tlSample, `样本 #${index + 1} / ${track.samples.length}`);
    setText(el.tlRow, `CSV 第 ${sample.csv_line} 行`);
    if (!scrubbing && (force || document.activeElement !== el.scrub))
      el.scrub.value = String(state.position);
    const playing = state.playing;
    if (force || el.play.getAttribute("aria-pressed") !== String(playing)) {
      el.play.setAttribute("aria-pressed", String(playing));
      el.play.querySelector(".label").textContent = playing ? "暂停" : "播放";
      el.play.querySelector(".icon-play").hidden = playing;
      el.play.querySelector(".icon-pause").hidden = !playing;
    }
    if (force) { updateScene(); charts.setCursor(state.position, index); }
  }

  /* ---------- panel rendering ---------- */

  function renderAll() { renderSource(); renderStats(); renderEvents(); renderDetail(); }

  function renderSource() {
    const badge = dataset
      ? (dataset.fictional
        ? '<span class="badge fiction">虚构演示 · 非实测</span>'
        : '<span class="badge local">本地导入 · 未上传</span>')
      : "";
    el.dataBadge.innerHTML = badge;
    let html = "";
    if (importErrors.length) {
      html += `<div class="import-errors" role="alert"><strong>CSV 校验未通过，未加载数据。</strong>
        <ul>${importErrors.slice(0, 12).map((error) => `<li>${esc(error)}</li>`).join("")}</ul>
        ${importErrors.length > 12 ? `<p>其余 ${importErrors.length - 12} 条错误已省略。</p>` : ""}</div>`;
    }
    if (!dataset) {
      el.secSourceBody.innerHTML = html + '<p class="note">载入虚构演示数据或导入本地 CSV 后，此处显示数据源与采样信息。</p>';
      return;
    }
    const summary = dataset.validation.sampling_summary;
    html += `<dl class="kv">
      <div><dt>数据源</dt><dd>${esc(dataset.name)}</dd></div>
      <div><dt>样本 / 车辆</dt><dd>${dataset.rows.length} 行 · ${esc([...new Set(dataset.rows.map((row) => row.vehicle_id))].join(", "))}</dd></div>
      <div><dt>UTC 范围</dt><dd class="mono">${zulu(dataset.validation.minimum_timestamp)}<br>→ ${zulu(dataset.validation.maximum_timestamp)}</dd></div>
      <div><dt>采样间隔（中位）</dt><dd>${summary.interval_median_seconds ?? "—"} s · 重复时间戳 ${summary.duplicate_timestamp_rows} 行</dd></div>
      <div><dt>长间隔判定</dt><dd>${dataset.cutoff === null ? "样本不足" : `> ${num(dataset.cutoff, 2)} s（3×中位间隔）`}</dd></div>
    </dl>
    <div class="categories">
      <div class="cat on"><b>A</b><span>原始测量数据${dataset.fictional ? "（此处为明确标记的虚构演示值）" : "（浏览器本地解析，未向云端上传）"}</span></div>
      <div class="cat off"><b>B</b><span>登记配置转换 · 未应用：在线版无传感器登记台账，未做增益/偏置/坐标转换</span></div>
      <div class="cat on"><b>C</b><span>三维示意运动 · 预设教学动画随事件同步，非加速度积分重建</span></div>
    </div>
    <p class="note">校验规则与原 RailVehicle-Lab 一致：必需字段、ISO 8601 时间戳、速度 0–600 km/h、加速度 |a| ≤ 100 m/s²。在线版无车辆登记台账，vehicle_id 仅要求非空。</p>`;
    el.secSourceBody.innerHTML = html;
  }

  function renderStats() {
    if (!dataset) { el.secStatsBody.innerHTML = '<p class="note">载入数据后显示统计。</p>'; return; }
    const result = dataset.analysis, threshold = dataset.threshold;
    const accelRow = (label, stats, exceed) => `
      <div><dt>${label}</dt><dd>均值 ${num(stats.mean)} · RMS ${num(stats.rms)} m/s²<br>
      峰值 ${num(stats.peak_signed)} m/s²（${zulu(stats.peak_timestamp)}）<br>
      超阈值 ${exceed.count} 点 · ${num(exceed.percentage, 1)}%</dd></div>`;
    el.secStatsBody.innerHTML = `<dl class="kv">
      <div><dt>速度</dt><dd>均值 ${num(result.speed.mean, 2)} · 范围 ${num(result.speed.minimum, 2)}–${num(result.speed.maximum, 2)} km/h</dd></div>
      ${accelRow("横向加速度", result.lateral_accel, result.threshold.lateral)}
      ${accelRow("垂向加速度", result.vertical_accel, result.threshold.vertical)}
      <div><dt>任一通道超阈值</dt><dd>${result.threshold.either.count} 点 · ${num(result.threshold.either.percentage, 1)}%</dd></div>
    </dl>
    <p class="note">以上由当前样本直接计算，不调用 AI。阈值 |a| > ${num(threshold)} m/s² 由用户设置；统计异常和未超阈值均不构成故障、运行安全或法规结论。</p>`;
  }

  function renderEvents() {
    if (!dataset) { el.secEventsBody.innerHTML = '<p class="note">载入数据后显示事件。</p>'; el.eventCount.textContent = "0"; return; }
    el.eventCount.textContent = String(dataset.events.length);
    if (!dataset.events.length) {
      el.secEventsBody.innerHTML = '<p class="note">当前阈值下未检测到异常事件。可降低阈值或更换数据批次。</p>';
      return;
    }
    el.secEventsBody.innerHTML = `<div class="event-list" role="listbox" aria-label="异常事件列表">` +
      dataset.events.map((event, index) => {
        const channel = event.field === "lateral_accel" ? "lateral" : "vertical";
        return `<button type="button" role="option" class="event-item ${channel}" data-event="${index}"
          aria-selected="${selectedEvent === index}">
          <span class="ev-head"><i class="ev-dot"></i>${CHANNEL_LABEL[event.field]}加速度统计异常
            <em>${event.sample_count} 点 · ${num(event.duration_seconds, 1)} s</em></span>
          <span class="ev-time mono">${zulu(event.start_timestamp)}</span>
          <span class="ev-meta">峰值 ${num(event.maximum_signed_value)} m/s² · CSV 第 ${dataset.track.samples[event.start_row].csv_line}–${dataset.track.samples[event.end_row].csv_line} 行</span>
        </button>`;
      }).join("") + `</div>
      <p class="note">${EVENT_DURATION_NOTE}</p>`;
  }

  function renderDetail() {
    if (selectedEvent === null || !dataset?.events[selectedEvent]) {
      el.secDetailBody.innerHTML = '<p class="note">点击异常事件查看证据与规则说明；点击三维零件查看部件信息。</p>';
      return;
    }
    const event = dataset.events[selectedEvent];
    const track = dataset.track;
    const start = track.samples[event.start_row], peak = track.samples[event.peak_row];
    const scene = replayScene(track, track.offsets[event.start_row], event.start_row, dataset.threshold, dataset.cutoff);
    const lines = [];
    for (let row = event.start_row; row <= event.end_row; row++) lines.push(track.samples[row].csv_line);
    const lineText = lines.length <= 10 ? lines.join(", ") : lines.slice(0, 10).join(", ") + ` 等 ${lines.length} 行`;
    const valuesRow = (label, sample) => `<tr><td>${label}</td><td class="mono">${zulu(sample.timestamp)}</td>
      <td>${num(sample.speed_kmh, 2)}</td><td>${num(sample.lateral_accel)}</td><td>${num(sample.vertical_accel)}</td></tr>`;
    el.secDetailBody.innerHTML = `
      <div class="evidence">
        <h3>${event.type}</h3>
        <p class="rule-chip mono">规则：|a| > ${num(dataset.threshold)} m/s²（严格大于，分轴统计）</p>
        <dl class="kv">
          <div><dt>异常时间</dt><dd class="mono">${zulu(event.start_timestamp)} → ${zulu(event.end_timestamp)}</dd></div>
          <div><dt>持续 / 样本</dt><dd>${num(event.duration_seconds, 1)} s · ${event.sample_count} 个异常样本</dd></div>
          <div><dt>原始 CSV 行号</dt><dd class="mono">第 ${lineText} 行</dd></div>
          <div><dt>峰值</dt><dd>${num(event.maximum_signed_value)} m/s² @ <span class="mono">${zulu(event.maximum_timestamp)}</span></dd></div>
        </dl>
        <table class="values"><thead><tr><th>观测</th><th>UTC 时间</th><th>速度 km/h</th><th>横向 m/s²</th><th>垂向 m/s²</th></tr></thead>
          <tbody>${valuesRow("起始", start)}${valuesRow("峰值", peak)}</tbody></table>
        <div class="part-links"><span>相关结构示意：</span>
          ${scene.parts.length
            ? scene.parts.map((group) => `<button type="button" class="part-chip" data-group="${esc(group)}">${esc(group)}</button>`).join("")
            : "<em>无（未超阈值或长采样间隔）</em>"}
        </div>
        <p class="impact">${esc(scene.impact)} ${esc(scene.check)}</p>
        <div class="panel-actions">
          <button type="button" data-seek="start">定位起始样本</button>
          <button type="button" data-seek="peak">定位峰值样本</button>
        </div>
        <p class="note limitation"><strong>证据与诊断局限性：</strong>${LIMITATIONS}</p>
      </div>`;
  }

  function showPart(id) {
    if (!id) { renderDetail(); return; }
    selectedEvent = null;
    renderEvents();
    el.secDetailBody.innerHTML = describePart(id);
    if (window.innerWidth <= 720) openDrawer();
  }

  /* ---------- wiring ---------- */

  el.secEventsBody.addEventListener("click", (event) => {
    const item = event.target.closest("[data-event]");
    if (!item || !dataset) return;
    selectedEvent = Number(item.dataset.event);
    const entry = dataset.events[selectedEvent];
    seek(entry.start_row);
    charts.setFlash(entry.peak_row);
    renderEvents();
    renderDetail();
    el.secDetail.open = true;
    if (window.innerWidth <= 720) openDrawer();
  });

  el.secDetailBody.addEventListener("click", (event) => {
    const seekButton = event.target.closest("[data-seek]");
    if (seekButton && dataset && selectedEvent !== null) {
      const entry = dataset.events[selectedEvent];
      seek(seekButton.dataset.seek === "peak" ? entry.peak_row : entry.start_row);
      charts.setFlash(entry.peak_row);
      return;
    }
    const chip = event.target.closest("[data-group]");
    if (chip) { focusGroup(chip.dataset.group); return; }
    const act = event.target.dataset?.act;
    if (!act) return;
    const id = inspector.selected;
    if (act === "focus" && id) focusPart(id);
    if (act === "isolate" && id) inspector.isolate(id);
    if (act === "showall") inspector.showAll();
    if (act === "ghost") {
      const on = event.target.getAttribute("aria-pressed") !== "true";
      event.target.setAttribute("aria-pressed", String(on));
      inspector.setGhost(on);
    }
  });

  el.play.addEventListener("click", () => act(state.playing ? "pause" : "play"));
  el.back.addEventListener("click", () => {
    if (!dataset) return;
    act("pause");
    seek(dataset.track.indexAt(state.position) - 1);
  });
  el.forward.addEventListener("click", () => {
    if (!dataset) return;
    act("pause");
    seek(dataset.track.indexAt(state.position) + 1);
  });
  el.tlReset.addEventListener("click", () => act("reset"));
  el.rate.addEventListener("change", () => act("rate", {rate: Number(el.rate.value)}));
  el.scrub.addEventListener("pointerdown", () => { scrubbing = true; });
  window.addEventListener("pointerup", () => { scrubbing = false; });
  el.scrub.addEventListener("input", () => {
    if (!dataset) return;
    state = controlReplay(state, "seek", now(), dataset.track.duration, {position: Number(el.scrub.value)});
    syncUI(now(), true);
  });
  el.threshold.addEventListener("change", () => {
    if (!dataset) return;
    const value = Number(el.threshold.value);
    if (!Number.isFinite(value) || value < 0) {
      el.threshold.value = String(dataset.threshold);
      return;
    }
    dataset.threshold = value;
    recompute();
    syncUI(now(), true);
  });
  el.source.addEventListener("change", async () => {
    const value = el.source.value;
    if (value === "import") {
      if (dataset?.kind === "import") return;
      el.csvFile.click();
      return;
    }
    await loadDemo(Number(value.slice(-1)) - 1);
  });
  el.importButton.addEventListener("click", () => el.csvFile.click());
  el.csvFile.addEventListener("change", async () => {
    const file = el.csvFile.files?.[0];
    el.csvFile.value = "";
    if (file) await importFile(file);
  });
  el.reset.addEventListener("click", async () => {
    if (!dataset) return;
    const {content, name, fictional, kind} = dataset;
    state = initialReplayState();
    el.rate.value = "1";
    buildDataset(content, {name, fictional, kind});
  });

  return {
    charts,
    get dataset() { return dataset; },
    get state() { return state; },
    get selectedEvent() { return selectedEvent; },
    async enter() {
      lastNow = null;
      charts.resize();
      if (!dataset && !loadedOnce) {
        loadedOnce = true;
        el.secSourceBody.innerHTML = '<p class="note">正在载入虚构演示数据…</p>';
        try { await loadDemo(0); }
        catch (cause) {
          importErrors = [cause.message];
          renderSource();
        }
      } else if (dataset) {
        lastScene = null;
        syncUI(now(), true);
      }
    },
    leave() { lastNow = null; },
    tick, seek, showPart, importFile, loadDemo,
    buildDatasetFromText: (content, meta) => buildDataset(content, meta),
    setThreshold(value) {
      if (!dataset) return;
      dataset.threshold = value;
      el.threshold.value = String(value);
      recompute();
    },
    resize: charts.resize,
  };
}