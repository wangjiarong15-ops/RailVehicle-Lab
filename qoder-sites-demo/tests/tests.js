/* Automated equivalence tests against the Python-verified baseline. */
import {
  DEFAULT_THRESHOLD, ERROR_LIMIT, ILLUSTRATION_PERIOD,
  advanceReplay, analyzeRunData, controlReplay, csvRecords, detectAnomalies,
  eventPositions, formatUtcUs, gapCutoff, illustrationPhase, initialReplayState,
  longGapAt, motionEnvelope, parseTimestamp, prepareReplay, replayScene,
  sceneForSample, summarizeTimestamps, validateCsv,
} from "../analysis.js";

const T = DEFAULT_THRESHOLD;
const results = [];
const test = (name, fn) => {
  try { fn(); results.push({name, ok: true}); }
  catch (cause) { results.push({name, ok: false, error: String(cause?.message ?? cause)}); }
};
const assert = (condition, message) => { if (!condition) throw new Error(message ?? "断言失败"); };

function close(actual, expected, path, out) {
  if (typeof expected === "number") {
    if (typeof actual !== "number" || !Number.isFinite(actual)) {
      out.push(`${path}: 期望数值 ${expected}，实际 ${JSON.stringify(actual)}`); return;
    }
    if (Math.abs(actual - expected) > Math.max(1e-9, Math.abs(expected) * 1e-9))
      out.push(`${path}: ${actual} ≠ ${expected}`);
    return;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      out.push(`${path}: 数组长度 ${actual?.length} ≠ ${expected.length}`); return;
    }
    expected.forEach((value, index) => close(actual[index], value, `${path}[${index}]`, out));
    return;
  }
  if (expected && typeof expected === "object") {
    if (!actual || typeof actual !== "object") { out.push(`${path}: 期望对象`); return; }
    for (const key of Object.keys(expected)) close(actual[key], expected[key], `${path}.${key}`, out);
    return;
  }
  if (actual !== expected) out.push(`${path}: ${JSON.stringify(actual)} ≠ ${JSON.stringify(expected)}`);
}
function eq(actual, expected, path = "value") {
  const out = [];
  close(actual, expected, path, out);
  if (out.length) throw new Error(out.slice(0, 5).join("\n"));
}

const HEADER = "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel";
const row = (ts, lat = 0, vert = 0, speed = 72, vehicle = "DEMO-001") =>
  `${ts},${vehicle},${speed},${lat},${vert}`;
const csv = (...rows) => [HEADER, ...rows].join("\n") + "\n";

async function sha256(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function batchEvents(track, cutoff) {
  return eventPositions(track, T).map(([event, startRow]) => {
    const endRow = startRow + event.sample_count - 1;
    const peakRow = track.samples.findIndex((sample) =>
      sample.timestamp === event.maximum_timestamp &&
      Math.abs(sample[event.field]) === event.maximum_absolute_value);
    const scene = replayScene(track, track.offsets[startRow], startRow, T, cutoff);
    return {
      ...event, start_row: startRow, peak_row: peakRow, end_row: endRow,
      start_csv_line: track.samples[startRow].csv_line,
      peak_csv_line: track.samples[peakRow].csv_line,
      end_csv_line: track.samples[endRow].csv_line,
      scene: {label: scene.label, mode: scene.mode, parts: [...scene.parts]},
    };
  });
}

async function main() {
  const expected = await (await fetch("./expected.json")).json();
  const batches = [];
  for (const batch of expected.batches) {
    const response = await fetch(`../assets/${batch.file_name}`);
    assert(response.ok, `${batch.file_name} 请求失败`);
    const buffer = await response.arrayBuffer();
    batches.push({batch, buffer, content: new TextDecoder("utf-8").decode(buffer)});
  }

  /* ---- fixture integrity ---- */
  for (const {batch, buffer} of batches) {
    const digest = await sha256(buffer);
    test(`基线完整性 ${batch.file_name}（SHA-256）`, () => eq(digest, batch.source_sha256, "sha256"));
  }

  /* ---- batches vs Python ---- */
  for (const {batch, content} of batches) {
    const label = batch.file_name;
    const validation = validateCsv(content, {vehicles: ["DEMO-001"]});
    test(`${label} 校验通过（原项目车辆口径）`, () => {
      eq(validation.errors, [], "errors");
      eq(validation.source_row_count, batch.source_row_count, "source_row_count");
      eq(validation.rows.length, batch.source_row_count, "rows");
    });
    test(`${label} 采样摘要`, () => eq(validation.sampling_summary, batch.sampling_summary, "sampling"));
    test(`${label} 原始 CSV 行号保留`, () =>
      eq(validation.rows.map((r) => r.csv_line), batch.csv_lines, "csv_lines"));
    const analysis = analyzeRunData(validation.rows, T);
    test(`${label} 统计与阈值`, () => eq({
      sample_count: analysis.sample_count, speed: analysis.speed,
      lateral_accel: analysis.lateral_accel, vertical_accel: analysis.vertical_accel,
      threshold: analysis.threshold,
    }, batch.analysis, "analysis"));
    test(`${label} 异常点`, () => eq(detectAnomalies(validation.rows, T).points, batch.points, "points"));
    const track = prepareReplay(validation.rows);
    test(`${label} 回放偏移与行号`, () => {
      eq(track.offsets, batch.offsets, "offsets");
      eq(track.samples.map((r) => r.csv_line), batch.csv_lines, "csv_lines");
      eq(track.duration, batch.offsets[batch.offsets.length - 1], "duration");
    });
    test(`${label} indexAt 边界`, () => {
      eq(track.indexAt(0), 0); eq(track.indexAt(.5), 0); eq(track.indexAt(1), 1);
      eq(track.indexAt(119), 119); eq(track.indexAt(1e6), 119); eq(track.indexAt(-3), 0);
    });
    const cutoff = gapCutoff(track);
    test(`${label} 长间隔阈值`, () => eq(cutoff, batch.gap_cutoff, "cutoff"));
    test(`${label} 事件/峰值/CSV 行/场景`, () =>
      eq(batchEvents(track, cutoff), batch.events.map(({anchor, ...rest}) => rest), "events"));
    test(`${label} 时间戳归一化为 UTC`, () => {
      for (const sample of track.samples) assert(sample.timestamp.endsWith("+00:00"), sample.timestamp);
    });
  }

  /* ---- edge fixture: unsorted / duplicates / strict boundary ---- */
  const edge = expected.edge;
  const edgeAnalysis = analyzeRunData(edge.input, T);
  const edgeTrack = prepareReplay(edge.input);
  test("乱序输入稳定排序（时间戳）", () =>
    eq(edgeAnalysis.samples.map((r) => r.timestamp), edge.ordered_timestamps, "ordered"));
  test("乱序/重复时间戳保留原始 CSV 行号", () =>
    eq(edgeTrack.samples.map((r) => r.csv_line), edge.ordered_csv_lines, "csv_lines"));
  test("边界样本统计", () => eq({
    sample_count: edgeAnalysis.sample_count, speed: edgeAnalysis.speed,
    lateral_accel: edgeAnalysis.lateral_accel, vertical_accel: edgeAnalysis.vertical_accel,
    threshold: edgeAnalysis.threshold,
  }, edge.analysis, "analysis"));
  test("边界异常点（|a|=1.0 不算异常）", () => {
    eq(detectAnomalies(edge.input, T).points, edge.points, "points");
    assert(!edge.points.some((p) => p.absolute_value === 1.0), "阈值边界必须严格大于");
  });
  test("边界事件与起始行", () =>
    eq(eventPositions(edgeTrack, T).map(([event, start]) => ({...event, start_row: start})),
      edge.events, "events"));
  test("边界回放偏移（重复时间戳）", () => eq(edgeTrack.offsets, edge.offsets, "offsets"));
  test("边界采样摘要（重复计数）", () =>
    eq(summarizeTimestamps(edge.input.map((r) => r.timestamp)), edge.sampling_summary, "sampling"));

  /* ---- timestamp parsing ---- */
  test("时间戳：微秒截断", () =>
    eq(parseTimestamp("2026-10-08T09:30:00.1234567Z").iso, "2026-10-08 09:30:00.123456+00:00"));
  test("时间戳：时区偏移换算", () => {
    const parsed = parseTimestamp("2026-10-08T17:30:00+08:00");
    eq(parsed.utcUs, parseTimestamp("2026-10-08T09:30:00Z").utcUs, "utcUs");
    eq(parsed.iso, "2026-10-08 17:30:00+08:00", "iso");
  });
  test("时间戳：小写 z / 空格分隔 / 无秒", () => {
    eq(parseTimestamp("2026-10-08t09:30:00z".replace("t", "T")).naive, false);
    eq(parseTimestamp("2026-10-08 09:30").iso, "2026-10-08 09:30:00");
    eq(parseTimestamp("2026-10-08 09:30").naive, true);
  });
  test("时间戳：非法日期拒绝", () => {
    for (const bad of ["2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z", "2026-10-08T24:00:00Z", "not-a-time"]) {
      let threw = false;
      try { parseTimestamp(bad); } catch { threw = true; }
      assert(threw, `应拒绝：${bad}`);
    }
  });
  test("时间戳：公元 99 年", () =>
    eq(parseTimestamp("0099-01-01T00:00:00Z").iso, "0099-01-01 00:00:00+00:00"));
  test("formatUtcUs 负微秒进位", () =>
    eq(formatUtcUs(-1, 0, false), "1969-12-31 23:59:59.999999+00:00"));

  /* ---- CSV reader ---- */
  test("CSV：多行引号字段记录物理起始行", () => {
    const records = [...csvRecords('h1,h2\n"a\nb",2\n3,4\n')];
    eq(records.map((r) => r.line), [1, 2, 4], "lines");
    eq(records[1].values, ["a\nb", "2"], "values");
  });
  test("CSV：引号内 CRLF 归一", () => {
    const records = [...csvRecords('h\n"a\r\nb"\n')];
    eq(records[1].values, ["a\r\nb"]);
  });
  test("CSV：BOM 去除", () => {
    const validation = validateCsv("\uFEFF" + csv(row("2026-10-08T09:30:00Z")));
    eq(validation.errors, [], "errors");
    eq(validation.rows.length, 1);
  });
  test("CSV：CRLF 与空行", () => {
    const validation = validateCsv([HEADER, row("2026-10-08T09:30:00Z"), "", row("2026-10-08T09:30:01Z")].join("\r\n"));
    eq(validation.errors, [], "errors");
    eq(validation.rows.map((r) => r.csv_line), [2, 4], "csv_lines");
  });
  test("CSV：引号语法错误", () => {
    for (const bad of [HEADER + '\n"x"y,DEMO-001,72,0,0\n', HEADER + '\n"2026-10-08T09:30:00Z,DEMO-001,72,0,0\n']) {
      const validation = validateCsv(bad);
      assert(validation.errors[0]?.includes("CSV 格式无效"), JSON.stringify(validation.errors));
      eq(validation.rows, [], "rows");
    }
  });

  /* ---- validation rules ---- */
  test("校验：空文件 / 仅表头", () => {
    assert(validateCsv("").errors[0].includes("为空或缺少表头"));
    assert(validateCsv(HEADER + "\n").errors.includes("CSV 中没有可导入的数据行。"));
  });
  test("校验：缺少必需字段 / 重复列名 / 空列名", () => {
    assert(validateCsv("timestamp,vehicle_id,speed_kmh,lateral_accel\n").errors[0].includes("缺少必需字段：vertical_accel"));
    assert(validateCsv("timestamp,timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n").errors[0].includes("重复列名：timestamp"));
    assert(validateCsv("timestamp,,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n").errors[0].includes("空列名"));
  });
  test("校验：行错误格式与行号", () => {
    const validation = validateCsv(csv(row("bad-time"), row("2026-10-08T09:30:01Z")));
    eq(validation.errors, ["第 2 行：timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）。"], "errors");
    eq(validation.rows.length, 1);
    eq(validation.rows[0].csv_line, 3);
  });
  test("校验：速度/加速度范围边界", () => {
    const ok = validateCsv(csv(
      row("2026-10-08T09:30:00Z", 100, -100, 600),
      row("2026-10-08T09:30:01Z", 0, 0, 0)));
    eq(ok.errors, [], "errors");
    const bad = validateCsv(csv(
      row("2026-10-08T09:30:00Z", 100.1, 0, 600.1),
      row("2026-10-08T09:30:01Z", -101, 0, -1)));
    assert(bad.errors.some((e) => e.includes("speed_kmh 必须在 0 至 600 之间")), JSON.stringify(bad.errors));
    assert(bad.errors.some((e) => e.includes("lateral_accel 绝对值不能超过 100")));
  });
  test("校验：数值语法（十六进制/inf/nan/下划线/科学计数）", () => {
    const validation = validateCsv(csv(
      row("2026-10-08T09:30:00Z", "0x10", 0),
      row("2026-10-08T09:30:01Z", "inf", 0),
      row("2026-10-08T09:30:02Z", "NaN", 0),
      row("2026-10-08T09:30:03Z", "1e-1", 0),
      row("2026-10-08T09:30:04Z", "1_0", 0)));
    assert(validation.errors.some((e) => e.includes("lateral_accel 必须是数字")), JSON.stringify(validation.errors));
    assert(validation.errors.some((e) => e.includes("必须是有限数值")));
    eq(validation.rows.length, 2, "rows");
    eq(validation.rows[0].lateral_accel, .1);
    eq(validation.rows[1].lateral_accel, 10);
  });
  test("校验：空字段与空车辆编号", () => {
    const validation = validateCsv(csv("2026-10-08T09:30:00Z,,72,0,0"));
    assert(validation.errors[0].includes("以下字段为空：vehicle_id"), JSON.stringify(validation.errors));
    eq(validation.missing_by_field.vehicle_id, 1);
  });
  test("校验：车辆台账口径（在线仅非空）", () => {
    const withRegistry = validateCsv(csv(row("2026-10-08T09:30:00Z", 0, 0, 72, "OTHER")), {vehicles: ["DEMO-001"]});
    assert(withRegistry.errors[0].includes("车辆编号“OTHER”不存在"));
    const online = validateCsv(csv(row("2026-10-08T09:30:00Z", 0, 0, 72, "OTHER")));
    eq(online.errors, [], "errors");
  });
  test("校验：列数超过表头", () => {
    const validation = validateCsv(csv(row("2026-10-08T09:30:00Z") + ",extra"));
    assert(validation.errors[0].includes("列数超过表头定义"));
  });
  test("校验：naive 时间戳计数", () => {
    const validation = validateCsv(csv(row("2026-10-08T09:30:00"), row("2026-10-08T09:30:01Z")));
    eq(validation.naive_timestamp_rows, 1);
    eq(validation.rows[0].timestamp, "2026-10-08 09:30:00");
  });
  test("校验：错误上限截断", () => {
    const validation = validateCsv(csv(...Array.from({length: 60}, (_, i) => row(`bad-${i}`))));
    eq(validation.errors.length, ERROR_LIMIT + 1, "errors.length");
    eq(validation.errors[ERROR_LIMIT], "错误数量较多，已停止显示后续错误。");
    eq(validation.source_row_count, 60);
  });

  /* ---- replay state machine ---- */
  test("回放：播放/推进/精确停止", () => {
    let state = controlReplay(initialReplayState(), "play", 0, 119);
    assert(state.playing);
    state = advanceReplay(state, 50, 119);
    eq(state.position, 50);
    state = advanceReplay(state, 500, 119);
    eq(state.position, 119);
    eq(state.playing, false);
    eq(state.lastTick, null);
  });
  test("回放：终点再播放回到起点", () => {
    let state = {...initialReplayState(), position: 119};
    state = controlReplay(state, "play", 10, 119);
    eq(state.position, 0);
    assert(state.playing);
  });
  test("回放：倍速/暂停/重置/定位", () => {
    let state = controlReplay(initialReplayState(), "rate", 0, 119, {rate: 2});
    state = controlReplay(state, "play", 0, 119);
    state = advanceReplay(state, 10, 119);
    eq(state.position, 20);
    state = controlReplay(state, "pause", 10, 119);
    eq(state.playing, false);
    state = controlReplay(state, "seek", 10, 119, {position: -5});
    eq(state.position, 0);
    state = controlReplay(state, "seek", 10, 119, {position: 1e9});
    eq(state.position, 119);
    state = controlReplay(state, "reset", 10, 119);
    eq(state.position, 0);
    eq(state.rate, 2);
    let threw = false;
    try { controlReplay(state, "rate", 0, 119, {rate: 0}); } catch { threw = true; }
    assert(threw, "倍速 0 应拒绝");
  });

  /* ---- scenes & illustrative motion ---- */
  test("场景判定（严格大于）", () => {
    eq(sceneForSample({lateral_accel: 1.0, vertical_accel: -1.0}, T).mode, "正常运行");
    eq(sceneForSample({lateral_accel: 1.2, vertical_accel: 0}, T).mode, "横向振动");
    eq(sceneForSample({lateral_accel: 0, vertical_accel: -1.2}, T).mode, "垂向振动");
    eq(sceneForSample({lateral_accel: 1.2, vertical_accel: -1.1}, T).mode, "异常运行");
  });
  test("长采样间隔场景", () => {
    const samples = [0, 1, 2, 12, 13].map((offset) => ({
      timestamp: formatUtcUs(parseTimestamp("2026-10-08T09:30:00Z").utcUs + offset * 1e6),
      vehicle_id: "DEMO-001", speed_kmh: 72, lateral_accel: 0, vertical_accel: 0,
    }));
    const track = prepareReplay(samples);
    const cutoff = gapCutoff(track);
    eq(cutoff, 3);
    eq(longGapAt(track, 5, cutoff), [2, 12]);
    eq(replayScene(track, 5, track.indexAt(5), T, cutoff).label, "长采样间隔 · 无当时观测");
    eq(replayScene(track, 1, track.indexAt(1), T, cutoff).mode, "正常运行");
    eq(longGapAt(track, 2, cutoff), null);
  });
  test("示意运动包络与相位", () => {
    eq(motionEnvelope("正常运行"), [0, 0, 0]);
    eq(motionEnvelope("横向振动"), [.075, 0, 0]);
    eq(motionEnvelope("垂向振动"), [0, .065, 0]);
    eq(motionEnvelope("异常运行"), [.075, .065, 0]);
    eq(motionEnvelope("蛇行运动示意"), [.075, 0, .049]);
    eq(ILLUSTRATION_PERIOD, expected.illustration_period);
    eq(illustrationPhase(2.4), Math.PI * 2);
    eq(illustrationPhase(0), 0);
  });

  render();
}

function render() {
  const list = document.querySelector("#results");
  const summary = document.querySelector("#summary");
  const failed = results.filter((r) => !r.ok);
  for (const result of results) {
    const item = document.createElement("li");
    item.className = result.ok ? "ok" : "bad";
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = result.ok ? "通过" : "失败";
    item.append(tag, document.createTextNode(result.name));
    if (!result.ok) {
      const err = document.createElement("span");
      err.className = "err";
      err.textContent = result.error;
      item.append(err);
    }
    list.append(item);
  }
  summary.textContent = `${results.length - failed.length} / ${results.length} 通过` +
    (failed.length ? ` · ${failed.length} 失败` : " · 全部通过");
  summary.className = failed.length ? "fail" : "pass";
  window.__tests = {done: true, total: results.length, passed: results.length - failed.length, failed};
}

main().catch((cause) => {
  const summary = document.querySelector("#summary");
  summary.textContent = "测试运行器崩溃：" + (cause?.message ?? cause);
  summary.className = "fail";
  window.__tests = {done: true, total: 0, passed: 0, failed: [{name: "runner", error: String(cause?.stack ?? cause)}]};
});