/* Equivalent port of the verified RailVehicle-Lab Python rules:
   run_data.validate_run_csv, dynamics.analyze_run_data, anomaly.detect_anomalies,
   data_quality.summarize_timestamps, replay.prepare_replay / control_replay,
   bogie_replay scenes / event_positions / gap_cutoff, bogie_geometry.motion_envelope.
   All computation runs locally in the browser; nothing is uploaded. */

export const REQUIRED_COLUMNS = ["timestamp", "vehicle_id", "speed_kmh", "lateral_accel", "vertical_accel"];
export const MAX_SPEED_KMH = 600;
export const MAX_ABS_ACCELERATION = 100;
export const MAX_CSV_SIZE_BYTES = 50 * 1024 * 1024;
export const PREVIEW_LIMIT = 20;
export const ERROR_LIMIT = 50;
export const ILLUSTRATION_PERIOD = 2.4;
export const DEFAULT_THRESHOLD = 1.0;
export const ACCELERATION_FIELDS = [
  ["lateral_accel", "横向加速度统计异常"],
  ["vertical_accel", "垂向加速度统计异常"],
];
export const EVENT_DURATION_NOTE =
  "区段时长按首末异常样本的时间差计算；两者之间存在采样空档时，不代表空档内持续观测到异常。";

const TAU = Math.PI * 2;

/* ---------- timestamps ---------- */

const TS_PATTERN = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6})\d*)?)?(Z|z|[+-]\d{2}:\d{2}(?::\d{2})?)?$/;

function pad(value, width = 2) { return String(value).padStart(width, "0"); }

export function formatUtcUs(us, offsetMinutes = 0, naive = false) {
  const local = us + offsetMinutes * 60_000_000;
  const secondUs = Math.floor(local / 1_000_000) * 1_000_000;
  const micros = local - secondUs;
  const date = new Date(secondUs / 1000);
  let text = `${pad(date.getUTCFullYear(), 4)}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
  if (micros) text += "." + pad(micros, 6);
  if (!naive) {
    const sign = offsetMinutes < 0 ? "-" : "+";
    const abs = Math.abs(offsetMinutes);
    text += `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
  }
  return text;
}

/* ISO 8601 subset accepted by the CSV contract: date + time, optional seconds,
   optional fractional seconds (truncated to microseconds like datetime), optional
   Z/z or ±HH:MM offset. Naive timestamps are treated as UTC downstream. */
export function parseTimestamp(value) {
  if (typeof value !== "string") throw new Error("timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）");
  const text = value.endsWith("Z") || value.endsWith("z") ? value.slice(0, -1) + "+00:00" : value;
  const match = TS_PATTERN.exec(text);
  if (!match) throw new Error("timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）");
  const [, ys, ms, ds, hs, mins, ss, frac, zone] = match;
  const year = +ys, month = +ms, day = +ds, hour = +hs, minute = +mins;
  const second = ss ? +ss : 0;
  const micros = frac ? +frac.padEnd(6, "0") : 0;
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59)
    throw new Error("timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）");
  const probe = new Date(0);
  probe.setUTCFullYear(year, month - 1, day);
  probe.setUTCHours(hour, minute, second, 0);
  if (probe.getUTCFullYear() !== year || probe.getUTCMonth() !== month - 1 || probe.getUTCDate() !== day ||
      probe.getUTCHours() !== hour || probe.getUTCMinutes() !== minute || probe.getUTCSeconds() !== second)
    throw new Error("timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）");
  let offsetMinutes = 0, naive = true;
  if (zone) {
    naive = false;
    const sign = zone[0] === "-" ? -1 : 1;
    const oh = +zone.slice(1, 3), om = +zone.slice(4, 6);
    const os = zone.length > 6 ? +zone.slice(7, 9) : 0;
    if (oh > 23 || om > 59 || os > 59)
      throw new Error("timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）");
    offsetMinutes = sign * (oh * 60 + om + os / 60);
  }
  const localUs = probe.getTime() * 1000 + micros;
  const utcUs = localUs - offsetMinutes * 60_000_000;
  const utcYear = new Date(utcUs / 1000).getUTCFullYear();
  if (utcYear < 1 || utcYear > 9999) throw new Error("timestamp 超出可支持的 UTC 时间范围");
  const iso = formatUtcUs(utcUs, offsetMinutes, naive);
  return {utcUs, iso, naive, offsetMinutes};
}

export function utcOf(value) {
  return parseTimestamp(value).utcUs;
}

/* ---------- CSV reader (python csv.reader, excel dialect, strict) ---------- */

class CsvError extends Error {}

export function* csvRecords(text) {
  const source = text.replace(/^\uFEFF/, "");
  let line = 1, startLine = 1, index = 0, values = [], field = "", quoted = false, closed = false, any = false;
  const endField = () => { values.push(field); field = ""; quoted = false; closed = false; any = true; };
  const endRecord = () => {
    if (any || field !== "" || quoted) endField();
    const record = {line: startLine, values};
    values = []; any = false;
    return record;
  };
  const newline = (char) => {
    const record = endRecord();
    line += 1; if (char === "\r" && source[index + 1] === "\n") index += 1;
    index += 1; startLine = line;
    return record;
  };
  while (index < source.length) {
    const char = source[index];
    if (quoted) {
      if (char === '"') {
        if (source[index + 1] === '"') { field += '"'; index += 2; continue; }
        quoted = false; closed = true; index += 1; continue;
      }
      if (char === "\r") { line += 1; if (source[index + 1] === "\n") index += 1; field += "\r\n"; index += 1; continue; }
      if (char === "\n") { line += 1; field += "\n"; index += 1; continue; }
      field += char; index += 1; continue;
    }
    if (closed) {
      if (char === ",") { endField(); index += 1; continue; }
      if (char === "\r" || char === "\n") { yield newline(char); continue; }
      throw new CsvError("CSV 格式无效，请检查引号、列分隔符和字段长度。");
    }
    if (char === '"' && field === "") { quoted = true; any = true; index += 1; continue; }
    if (char === ",") { endField(); index += 1; continue; }
    if (char === "\r" || char === "\n") { yield newline(char); continue; }
    field += char; any = true; index += 1;
  }
  if (quoted) throw new CsvError("CSV 格式无效，请检查引号、列分隔符和字段长度。");
  if (any || field !== "") yield endRecord();
}

/* ---------- sampling summary (data_quality.summarize_timestamps) ---------- */

export function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function summarizeTimestamps(timestamps) {
  const instants = timestamps.map(utcOf);
  const unique = [...new Set(instants)].sort((a, b) => a - b);
  const intervals = unique.slice(1).map((value, index) => (value - unique[index]) / 1e6);
  return {
    sample_count: instants.length,
    unique_timestamp_count: unique.length,
    duplicate_timestamp_rows: instants.length - unique.length,
    interval_min_seconds: intervals.length ? Math.min(...intervals) : null,
    interval_median_seconds: median(intervals),
    interval_max_seconds: intervals.length ? Math.max(...intervals) : null,
  };
}

/* ---------- CSV validation (run_data.validate_run_csv) ----------
   Online adaptation: there is no vehicle registry database, so vehicle_id is
   only required to be non-empty unless a `vehicles` code list is supplied. */

export function validateCsv(content, {vehicles = null} = {}) {
  const result = {
    errors: [], rows: [], preview: [], source_row_count: 0, distinct_vehicle_count: 0,
    minimum_timestamp: null, maximum_timestamp: null, sampling_summary: null,
    missing_by_field: {}, naive_timestamp_rows: 0,
  };
  if (typeof content !== "string") {
    result.errors.push("CSV 文件必须使用 UTF-8 编码（可包含 BOM）。");
    return result;
  }
  const codes = vehicles ? new Set(vehicles) : null;
  let headers;
  try {
    const records = csvRecords(content);
    const first = records.next();
    if (first.done || !first.value.values.length) {
      result.errors.push("CSV 文件为空或缺少表头。");
      return result;
    }
    headers = first.value.values.map((header) => header.trim());
    const headerErrors = [];
    if (headers.some((header) => !header)) headerErrors.push("CSV 表头不能包含空列名。");
    const counts = new Map();
    for (const header of headers) counts.set(header, (counts.get(header) ?? 0) + 1);
    const duplicates = [...counts.entries()].filter(([, count]) => count > 1).map(([header]) => header).sort();
    if (duplicates.length) headerErrors.push(`CSV 存在重复列名：${duplicates.join(", ")}。`);
    const missing = REQUIRED_COLUMNS.filter((column) => !headers.includes(column));
    if (missing.length) headerErrors.push(`CSV 缺少必需字段：${missing.join(", ")}。`);
    if (headerErrors.length) {
      result.errors.push(...headerErrors);
      return result;
    }
    const requiredIndexes = Object.fromEntries(REQUIRED_COLUMNS.map((column) => [column, headers.indexOf(column)]));
    const distinct = new Set();
    let minUs = null, maxUs = null, truncated = false;
    const instants = [];
    for (const record of records) {
      const values = record.values;
      if (!values.length || !values.some((value) => value.trim())) continue;
      const rowNumber = record.line;
      result.source_row_count += 1;
      for (const [column, columnIndex] of Object.entries(requiredIndexes)) {
        if (columnIndex >= values.length || !values[columnIndex].trim())
          result.missing_by_field[column] = (result.missing_by_field[column] ?? 0) + 1;
      }
      const raw = Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
      if (result.preview.length < PREVIEW_LIMIT) result.preview.push(raw);
      if (truncated) continue;
      const rowErrors = [];
      if (values.length > headers.length) rowErrors.push("列数超过表头定义");
      const emptyFields = REQUIRED_COLUMNS.filter((column) => !(raw[column] ?? "").trim());
      if (emptyFields.length) rowErrors.push(`以下字段为空：${emptyFields.join(", ")}`);
      let parsed = null;
      const timestampText = (raw.timestamp ?? "").trim();
      if (timestampText) {
        if (!timestampText.includes("T") && !timestampText.includes(" "))
          rowErrors.push("timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）");
        else {
          try { parsed = parseTimestamp(timestampText); }
          catch (cause) { rowErrors.push(cause.message); }
        }
      }
      const vehicleCode = (raw.vehicle_id ?? "").trim();
      if (vehicleCode && codes && !codes.has(vehicleCode)) rowErrors.push(`车辆编号“${vehicleCode}”不存在`);
      const numbers = {};
      for (const column of ["speed_kmh", "lateral_accel", "vertical_accel"]) {
        const text = (raw[column] ?? "").trim();
        if (!text) continue;
        if (/^[+-]?(?:inf(?:inity)?|nan)$/i.test(text)) {
          rowErrors.push(`${column} 必须是有限数值`);
          continue;
        }
        if (!/^[+-]?(?:\d[\d_]*(?:\.[\d_]*)?|\.[\d_]+)(?:[eE][+-]?\d+)?$/.test(text)) {
          rowErrors.push(`${column} 必须是数字`);
          continue;
        }
        const value = Number(text.replace(/_/g, ""));
        if (!Number.isFinite(value)) {
          rowErrors.push(`${column} 必须是有限数值`);
          continue;
        }
        numbers[column] = value;
      }
      if (numbers.speed_kmh !== undefined && !(0 <= numbers.speed_kmh && numbers.speed_kmh <= MAX_SPEED_KMH))
        rowErrors.push(`speed_kmh 必须在 0 至 ${MAX_SPEED_KMH} 之间`);
      for (const column of ["lateral_accel", "vertical_accel"]) {
        if (numbers[column] !== undefined && Math.abs(numbers[column]) > MAX_ABS_ACCELERATION)
          rowErrors.push(`${column} 绝对值不能超过 ${MAX_ABS_ACCELERATION}`);
      }
      if (rowErrors.length || !parsed || !vehicleCode) {
        const messages = rowErrors.map((message) => `第 ${rowNumber} 行：${message}。`);
        const slots = Math.max(0, ERROR_LIMIT - result.errors.length);
        result.errors.push(...messages.slice(0, slots));
        if (messages.length > slots) {
          result.errors.push("错误数量较多，已停止显示后续错误。");
          truncated = true;
        }
        continue;
      }
      distinct.add(vehicleCode);
      instants.push(parsed.utcUs);
      if (parsed.naive) result.naive_timestamp_rows += 1;
      if (minUs === null || parsed.utcUs < minUs) { minUs = parsed.utcUs; result.minimum_timestamp = parsed.iso; }
      if (maxUs === null || parsed.utcUs > maxUs) { maxUs = parsed.utcUs; result.maximum_timestamp = parsed.iso; }
      result.rows.push({
        timestamp: parsed.iso, vehicle_id: vehicleCode, csv_line: rowNumber,
        speed_kmh: numbers.speed_kmh, lateral_accel: numbers.lateral_accel, vertical_accel: numbers.vertical_accel,
      });
    }
    result.distinct_vehicle_count = distinct.size;
    if (result.source_row_count === 0) result.errors.push("CSV 中没有可导入的数据行。");
    if (!result.errors.length) result.sampling_summary = summarizeTimestamps(result.rows.map((row) => row.timestamp));
  } catch (cause) {
    if (cause instanceof CsvError) {
      result.errors = [cause.message];
      result.rows = [];
      return result;
    }
    throw cause;
  }
  return result;
}

/* ---------- statistics (dynamics.analyze_run_data) ---------- */

function fsum(values) {
  let sum = 0, compensation = 0;
  for (const value of values) {
    const total = sum + value;
    compensation += Math.abs(sum) >= Math.abs(value) ? (sum - total) + value : (value - total) + sum;
    sum = total;
  }
  return sum + compensation;
}

function validatedValues(samples, field) {
  return samples.map((sample, index) => {
    const value = Number(sample[field]);
    if (!Number.isFinite(value)) throw new Error(`第 ${index + 1} 个样本的 ${field} 不是有限数值。`);
    return value;
  });
}

function accelerationStatistics(ordered, values) {
  let peakIndex = 0;
  for (let index = 1; index < values.length; index++)
    if (Math.abs(values[index]) > Math.abs(values[peakIndex])) peakIndex = index;
  return {
    maximum: Math.max(...values), minimum: Math.min(...values),
    mean: fsum(values) / values.length,
    rms: Math.sqrt(fsum(values.map((value) => value * value)) / values.length),
    peak_absolute: Math.abs(values[peakIndex]), peak_signed: values[peakIndex],
    peak_timestamp: ordered[peakIndex].timestamp,
  };
}

function exceedance(values, threshold) {
  const count = values.filter((value) => Math.abs(value) > threshold).length;
  return {count, percentage: count / values.length * 100};
}

export function validThreshold(threshold) {
  const value = Number(threshold);
  if (!Number.isFinite(value) || value < 0) throw new Error("加速度阈值必须是有限的非负数。");
  return value;
}

export function analyzeRunData(samples, accelerationThreshold = DEFAULT_THRESHOLD) {
  if (!samples.length) throw new Error("所选车辆和导入批次没有运行数据。");
  const threshold = validThreshold(accelerationThreshold);
  let ordered;
  try {
    ordered = [...samples].sort((a, b) => utcOf(a.timestamp) - utcOf(b.timestamp));
  } catch {
    throw new Error("运行数据中存在无效的时间戳。");
  }
  const speed = validatedValues(ordered, "speed_kmh");
  const lateral = validatedValues(ordered, "lateral_accel");
  const vertical = validatedValues(ordered, "vertical_accel");
  const eitherCount = lateral.filter((value, index) =>
    Math.abs(value) > threshold || Math.abs(vertical[index]) > threshold).length;
  return {
    sample_count: ordered.length, samples: ordered,
    speed: {maximum: Math.max(...speed), minimum: Math.min(...speed), mean: fsum(speed) / speed.length},
    lateral_accel: accelerationStatistics(ordered, lateral),
    vertical_accel: accelerationStatistics(ordered, vertical),
    threshold: {
      value: threshold,
      lateral: exceedance(lateral, threshold), vertical: exceedance(vertical, threshold),
      either: {count: eitherCount, percentage: eitherCount / ordered.length * 100},
    },
  };
}

/* ---------- anomaly detection (anomaly.detect_anomalies) ---------- */

export function detectAnomalies(samples, accelerationThreshold) {
  const threshold = validThreshold(accelerationThreshold);
  if (!samples.length) return {threshold, points: [], events: []};
  let ordered;
  try {
    ordered = [...samples].sort((a, b) => utcOf(a.timestamp) - utcOf(b.timestamp));
  } catch {
    throw new Error("运行数据中存在无效的时间戳。");
  }
  const points = [], indexedEvents = [];
  for (const [field, anomalyType] of ACCELERATION_FIELDS) {
    const values = validatedValues(ordered, field);
    const indices = values.map((value, index) => Math.abs(value) > threshold ? index : -1).filter((index) => index >= 0);
    for (const index of indices) {
      points.push({
        timestamp: ordered[index].timestamp, field, type: anomalyType,
        value: values[index], absolute_value: Math.abs(values[index]),
      });
    }
    const runs = [];
    for (const index of indices) {
      if (!runs.length || index !== runs[runs.length - 1][runs[runs.length - 1].length - 1] + 1) runs.push([index]);
      else runs[runs.length - 1].push(index);
    }
    for (const run of runs) {
      let peakIndex = run[0];
      for (const index of run) if (Math.abs(values[index]) > Math.abs(values[peakIndex])) peakIndex = index;
      const start = ordered[run[0]].timestamp, end = ordered[run[run.length - 1]].timestamp;
      const instant = utcOf(start);
      indexedEvents.push([instant, run[0], {
        type: anomalyType, field, start_timestamp: start, end_timestamp: end,
        duration_seconds: (utcOf(end) - instant) / 1e6,
        sample_count: run.length,
        maximum_absolute_value: Math.abs(values[peakIndex]),
        maximum_signed_value: values[peakIndex],
        maximum_timestamp: ordered[peakIndex].timestamp,
      }]);
    }
  }
  indexedEvents.sort((a, b) => a[0] - b[0] || (a[2].field < b[2].field ? -1 : a[2].field > b[2].field ? 1 : 0));
  points.sort((a, b) => utcOf(a.timestamp) - utcOf(b.timestamp) || (a.field < b.field ? -1 : a.field > b.field ? 1 : 0));
  return {threshold, points, events: indexedEvents.map(([, , event]) => event)};
}

/* ---------- replay track (replay.prepare_replay) ---------- */

function bisectRight(values, target) {
  let low = 0, high = values.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (target < values[mid]) high = mid; else low = mid + 1;
  }
  return low;
}

export function prepareReplay(samples) {
  const ordered = analyzeRunData(samples).samples;
  const normalized = ordered.map((sample) => {
    const parsed = parseTimestamp(sample.timestamp);
    const row = {timestamp: formatUtcUs(parsed.utcUs, 0, false)};
    if (sample.csv_line !== undefined) { row.csv_line = sample.csv_line; row.source_time = sample.timestamp; }
    for (const field of ["speed_kmh", "lateral_accel", "vertical_accel"]) row[field] = Number(sample[field]);
    return row;
  });
  const first = parseTimestamp(normalized[0].timestamp).utcUs;
  const offsets = normalized.map((row) => (parseTimestamp(row.timestamp).utcUs - first) / 1e6);
  return {
    samples: normalized, offsets,
    duration: offsets[offsets.length - 1],
    indexAt(position) { return Math.max(0, bisectRight(offsets, position) - 1); },
  };
}

export function gapCutoff(track) {
  const intervals = track.offsets.slice(1)
    .map((end, index) => end - track.offsets[index]).filter((value) => value > 0);
  return intervals.length ? 3 * median(intervals) : null;
}

export function longGapAt(track, position, cutoff) {
  if (cutoff === null) return null;
  const index = track.indexAt(position);
  if (index + 1 >= track.offsets.length) return null;
  const start = track.offsets[index], end = track.offsets[index + 1];
  return start < position && position < end && end - start > cutoff ? [start, end] : null;
}

/* ---------- scenes (bogie_replay) ---------- */

export const SCENES = {
  normal: {label: "未超阈值", mode: "正常运行", parts: [],
    impact: "当前样本的两个加速度通道均未超阈值。", check: "继续核查整段曲线。"},
  lateral: {label: "横向加速度超阈值", mode: "横向振动", parts: ["构架", "一系悬挂"],
    impact: "横向信号超过设定范围；高亮表示相关结构示意。", check: "检查传感器方向、固定和相邻样本。"},
  vertical: {label: "垂向加速度超阈值", mode: "垂向振动", parts: ["一系悬挂", "二系悬挂"],
    impact: "垂向信号超过设定范围；高亮表示相关结构示意。", check: "核对采样质量和运行工况。"},
  combined: {label: "综合振动异常", mode: "异常运行", parts: ["构架", "一系悬挂", "二系悬挂"],
    impact: "同一个样本的横向和垂向通道均超阈值。", check: "比对两个通道的原始波形与采样时刻。"},
  gap: {label: "长采样间隔 · 无当时观测", mode: "正常运行", parts: [],
    impact: "仪表保留间隔前的最后一次观测，模型暂停在中立示意。", check: "定位到间隔前后的观测点，核对采集记录。"},
};

export function sceneForSample(sample, threshold) {
  const lateral = Math.abs(sample.lateral_accel) > threshold;
  const vertical = Math.abs(sample.vertical_accel) > threshold;
  return lateral && vertical ? SCENES.combined : lateral ? SCENES.lateral : vertical ? SCENES.vertical : SCENES.normal;
}

export function replayScene(track, position, index, threshold, cutoff) {
  return longGapAt(track, position, cutoff) ? SCENES.gap : sceneForSample(track.samples[index], threshold);
}

export function eventPositions(track, threshold) {
  const events = detectAnomalies(track.samples, threshold).events;
  const byField = new Map();
  const preceding = {};
  track.samples.forEach((row, index) => {
    for (const field of ["lateral_accel", "vertical_accel"]) {
      const active = Math.abs(row[field]) > threshold;
      if (active && !preceding[field]) {
        const key = field + "|" + row.timestamp;
        if (!byField.has(key)) byField.set(key, []);
        byField.get(key).push(index);
      }
      preceding[field] = active;
    }
  });
  return events.map((event) => [event, byField.get(event.field + "|" + event.start_timestamp).shift()]);
}

/* ---------- illustrative motion (bogie_geometry.motion_envelope) ---------- */

export function motionEnvelope(mode) {
  return [
    ["横向振动", "蛇行运动示意", "异常运行"].includes(mode) ? .075 : 0,
    ["垂向振动", "异常运行"].includes(mode) ? .065 : 0,
    mode === "蛇行运动示意" ? .049 : 0,
  ];
}

export function illustrationPhase(position) {
  return position * TAU / ILLUSTRATION_PERIOD;
}

/* ---------- replay state machine (replay.advance_replay / control_replay) ---------- */

export function initialReplayState(rate = 1) {
  return {position: 0, playing: false, rate, lastTick: null};
}

export function advanceReplay(state, now, duration) {
  if (!state.playing || state.lastTick === null) return state;
  const position = Math.min(duration, state.position + Math.max(0, now - state.lastTick) * state.rate);
  const playing = position < duration;
  return {...state, position, playing, lastTick: playing ? now : null};
}

export function controlReplay(state, action, now, duration, {rate = null, position = null} = {}) {
  state = advanceReplay(state, now, duration);
  if (action === "play") {
    const next = state.position >= duration ? 0 : state.position;
    return {...state, position: next, playing: duration > 0, lastTick: duration > 0 ? now : null};
  }
  if (action === "pause") return {...state, playing: false, lastTick: null};
  if (action === "reset") return initialReplayState(state.rate);
  if (action === "rate") {
    if (rate === null || !Number.isFinite(rate) || rate <= 0) throw new Error("回放倍速必须是有限的正数。");
    return {...state, rate};
  }
  if (action === "seek") {
    if (position === null || !Number.isFinite(position)) throw new Error("定位时间必须是有限数值。");
    const next = Math.max(0, Math.min(duration, position));
    return {...state, position: next, lastTick: state.playing ? now : null};
  }
  throw new Error("未知回放操作。");
}