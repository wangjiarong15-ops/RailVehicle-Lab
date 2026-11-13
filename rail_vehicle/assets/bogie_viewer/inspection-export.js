const filename = "railvehicle-bogie-inspection.png";
const font = '"Segoe UI", "Microsoft YaHei", sans-serif';

function observationFields(metadata) {
  const source = metadata.observation;
  if (!source) return [];
  const rows = [["来源", [source.source, source.batch_label].filter(Boolean).join(" · ")]];
  if (metadata.reference) rows.push(["画面", "正常运动参照 · 观测与阈值状态来自当前场景"]);
  if (!source.sample_number) return [...rows, ["场景", source.scene_label]];
  return [
    ...rows,
    ["观测", `${source.sample_kind} · 第 ${source.sample_number}/${source.sample_count} 条 · ${source.timestamp}`],
    ["速度", `${source.speed_kmh.toFixed(1)} km/h`],
    ["加速度", `横向 ${source.lateral_accel.toFixed(3)} m/s² · 垂向 ${source.vertical_accel.toFixed(3)} m/s²`],
    ["阈值状态", `${source.scene_label} · 加速度绝对值阈值 ${source.threshold} m/s²`],
  ];
}

function wrapText(context, text, maxWidth) {
  const lines = [];
  let line = "";
  for (const character of text) {
    if (line && context.measureText(line + character).width > maxWidth) {
      lines.push(line.trimEnd());
      line = "";
    }
    if (line || character !== " ") line += character;
  }
  if (line) lines.push(line);
  return lines;
}

function savePng(output) {
  return new Promise((resolve, reject) => {
    output.toBlob((blob) => {
      if (!blob) {
        reject(new Error("Could not encode the inspection PNG"));
        return;
      }
      const link = document.createElement("a");
      let url;
      try {
        url = URL.createObjectURL(blob);
        link.href = url;
        link.download = filename;
        document.body.append(link);
        link.click();
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        link.remove();
        if (url) setTimeout(() => URL.revokeObjectURL(url), 30_000);
      }
    }, "image/png");
  });
}

export async function exportInspectionPng(canvas, metadata) {
  if (!canvas.width || !canvas.height) throw new Error("The inspection viewport is empty");
  const output = document.createElement("canvas");
  const width = Math.min(1920, Math.max(760, canvas.width));
  const scale = width / 1000;
  const padding = Math.round(24 * scale);
  const header = Math.round(86 * scale);
  const imageWidth = width - 2 * padding;
  const imageHeight = Math.round(canvas.height * imageWidth / canvas.width);
  const lineHeight = Math.ceil(24 * scale);
  const labelWidth = Math.round(95 * scale);
  output.width = width;
  const context = output.getContext("2d");
  if (!context) throw new Error("Could not create the inspection image");
  const fields = [
    ["模型", metadata.model],
    ["视角", metadata.view],
    ["零件", metadata.part],
    ["剖切", metadata.section],
    ["测距", metadata.measurement],
    ...observationFields(metadata),
  ].filter(([, value]) => value);
  const bodyFont = `${Math.round(17 * scale)}px ${font}`;
  context.font = bodyFont;
  const rows = fields.map(([label, value]) => [
    label, wrapText(context, value, imageWidth - labelWidth),
  ]);
  const imageBottom = header + imageHeight;
  const rowCount = rows.reduce((count, [, lines]) => count + lines.length, 0);
  output.height = imageBottom + padding + rowCount * lineHeight + (rows.length ? padding : 0);
  context.fillStyle = "#0b1521";
  context.fillRect(0, 0, width, output.height);
  context.textBaseline = "top";
  context.fillStyle = "#e3edf9";
  context.font = `700 ${Math.round(28 * scale)}px ${font}`;
  context.fillText("三维转向架检查记录", padding, padding);
  context.fillStyle = "#a8bbce";
  context.font = bodyFont;
  context.fillText("RailVehicle-Lab · 通用工程示意模型", padding, padding + Math.round(36 * scale));

  const backdrop = context.createRadialGradient(
    width / 2, header + imageHeight / 2, 0, width / 2, header + imageHeight / 2,
    Math.max(imageWidth, imageHeight) * .6,
  );
  backdrop.addColorStop(0, "#1d2c3c");
  backdrop.addColorStop(1, "#0b1521");
  context.fillStyle = backdrop;
  context.fillRect(padding, header, imageWidth, imageHeight);
  // Copy immediately after the caller renders, before the WebGL buffer can be cleared.
  context.drawImage(canvas, padding, header, imageWidth, imageHeight);
  context.strokeStyle = "#34485e";
  context.strokeRect(padding + .5, header + .5, imageWidth - 1, imageHeight - 1);
  let y = imageBottom + padding;
  for (const [label, lines] of rows) {
    context.fillStyle = "#a8bbce";
    context.fillText(label, padding, y);
    context.fillStyle = "#e3edf9";
    for (const line of lines) {
      context.fillText(line, padding + labelWidth, y);
      y += lineHeight;
    }
  }
  await savePng(output);
}
