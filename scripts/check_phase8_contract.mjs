import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

const fromRoot = path => new URL(`../${path}`, import.meta.url);
const readJson = async path => JSON.parse(await readFile(fromRoot(path), "utf8"));
const hash = content => createHash("sha256").update(content).digest("hex");
const fixture = await readJson("tests/fixtures/phase8-v1.json");
const source = await readFile(fromRoot("qoder-sites-demo/analysis.js"));
const js = await import("data:text/javascript;base64," + source.toString("base64"));

function compare(actual, expected, field) {
  if (typeof expected === "number") {
    assert(Number.isFinite(actual) && Math.abs(actual - expected) <= Math.max(1e-10, Math.abs(expected) * 1e-10), field);
  } else if (Array.isArray(expected)) {
    assert.equal(actual.length, expected.length, field);
    expected.forEach((value, index) => compare(actual[index], value, `${field}[${index}]`));
  } else if (expected && typeof expected === "object") {
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), field);
    for (const [key, value] of Object.entries(expected)) compare(actual[key], value, `${field}.${key}`);
  } else {
    assert.equal(actual, expected, field);
  }
}

assert.equal(fixture.schema, "rail.phase8.cross-version.v1");
assert.deepEqual(js.REQUIRED_COLUMNS, fixture.csv_contract.fields);
assert.equal(js.MAX_SPEED_KMH, fixture.csv_contract.max_speed_kmh);
assert.equal(js.MAX_ABS_ACCELERATION, fixture.csv_contract.max_abs_acceleration_m_s2);
const threshold = fixture.csv_contract.threshold_m_s2;
assert.equal(js.DEFAULT_THRESHOLD, threshold);
for (const [name, {csv, expected}] of Object.entries(fixture.cases)) {
  const validation = js.validateCsv(csv, {vehicles: fixture.vehicle_codes});
  const result = {valid: !validation.errors.length, source_row_count: validation.source_row_count,
    missing_by_field: validation.missing_by_field, naive_timestamp_rows: validation.naive_timestamp_rows};
  if (result.valid) {
    const rows = validation.rows;
    const {samples, ...analysis} = js.analyzeRunData(rows, threshold);
    const track = js.prepareReplay(rows);
    const cutoff = js.gapCutoff(track);
    const index = track.offsets.length - 2;
    const middle = (track.offsets[index] + track.offsets[index + 1]) / 2;
    Object.assign(result, {sampling: validation.sampling_summary, sha256: hash(csv), analysis,
      anomalies: js.detectAnomalies(rows, threshold), csv_lines: track.samples.map(row => row.csv_line),
      offsets: track.offsets, gap_cutoff: cutoff, midpoint_scene: js.replayScene(track, middle, index, threshold, cutoff).mode});
  }
  compare(result, expected, name);
  // Use strict counts for the 1 ULP threshold case even when comparing statistics with tolerance.
  if (name === "physical_lines_and_clock") assert.equal(result.anomalies.points.length, expected.anomalies.points.length);
  console.log(`PASS ${name}`);
}

const staticData = await readJson("qoder-sites-demo/assets/viewer-data.json");
compare(staticData.contract, fixture.assembly, "GLB assembly contract");
for (const path of ["qoder-sites-demo/bogie.glb", "rail_vehicle/assets/bogie_viewer/bogie.glb"]) {
  assert.equal(hash(await readFile(fromRoot(path))), fixture.glb_sha256, path);
}
console.log(`PASS assembly version ${fixture.assembly.version}, ${Object.keys(fixture.assembly.parts).length} parts and matching GLB bytes`);
