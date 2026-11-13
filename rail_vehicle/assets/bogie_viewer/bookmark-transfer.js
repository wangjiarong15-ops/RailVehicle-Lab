const format = "railvehicle-bogie-bookmarks";
const version = 1;
const filename = "railvehicle-bogie-bookmarks.json";
const maxBytes = 64 * 1024;
const maxEntries = 12;

function validEntries(entries) {
  return Array.isArray(entries) && entries.length >= 1 && entries.length <= maxEntries;
}

export async function readBookmarkFile(file) {
  if (!file?.size) throw new Error("请选择非空的书签 JSON 文件。");
  if (file.size > maxBytes) throw new Error("书签文件超过 64 KiB。");

  let raw;
  try {
    raw = await file.text();
  } catch {
    throw new Error("无法读取书签文件。");
  }
  let parsed;
  try {
    parsed = JSON.parse(raw.trimStart());
  } catch {
    throw new Error("书签文件不是有效的 JSON。");
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) ||
      Object.keys(parsed).length !== 3 || parsed.format !== format || parsed.version !== version ||
      !validEntries(parsed.entries)) {
    throw new Error("书签文件格式或版本不受支持，文件应包含 1 至 12 个检查视图。");
  }
  return parsed.entries;
}

export function downloadBookmarkFile(entries) {
  if (!validEntries(entries)) throw new Error("请选择包含 1 至 12 个检查视图的书签。");
  let contents;
  try {
    contents = JSON.stringify({format, version, entries}, null, 2);
  } catch {
    throw new Error("无法生成书签文件。");
  }
  const blob = new Blob([contents + "\n"], {type: "application/json"});
  if (blob.size > maxBytes) throw new Error("导出的书签文件超过 64 KiB。");

  const link = document.createElement("a");
  let url;
  try {
    url = URL.createObjectURL(blob);
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
  } catch {
    throw new Error("无法下载书签文件。");
  } finally {
    link.remove();
    if (url) setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
}
