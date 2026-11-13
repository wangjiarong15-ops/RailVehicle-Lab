const storageKey = "railvehicle.bogie.viewBookmarks.v1";
const maxBookmarks = 12;
const maxNameLength = 32;
const maxStoredLength = 65_536;

function isSnapshot(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readBookmarks() {
  let raw;
  try {
    raw = localStorage.getItem(storageKey);
  } catch {
    return {entries: [], unavailable: true};
  }
  if (!raw) return {entries: []};
  try {
    if (raw.length > maxStoredLength) throw new Error();
    const data = JSON.parse(raw);
    if (data?.version !== 1 || !Array.isArray(data.entries)) throw new Error();
    const entries = [];
    const names = new Set();
    for (const item of data.entries) {
      if (entries.length === maxBookmarks) break;
      const name = typeof item?.name === "string" ? item.name.trim() : "";
      if (!name || name.length > maxNameLength || names.has(name) || !isSnapshot(item.snapshot)) continue;
      names.add(name);
      entries.push({name, snapshot: item.snapshot});
    }
    return {entries, damaged: entries.length !== data.entries.length};
  } catch {
    return {entries: [], damaged: true};
  }
}

export function viewBookmarks({capture, restore}) {
  const toggle = document.querySelector("#bookmarks-toggle");
  const panel = document.querySelector("#bookmarks-controls");
  const nameInput = document.querySelector("#bookmark-name");
  const selection = document.querySelector("#bookmark-select");
  const save = document.querySelector("#bookmark-save");
  const load = document.querySelector("#bookmark-restore");
  const remove = document.querySelector("#bookmark-delete");
  const status = document.querySelector("#bookmark-status");
  const listeners = [];
  let entries = [];

  function listen(target, event, handler) {
    target.addEventListener(event, handler);
    listeners.push(() => target.removeEventListener(event, handler));
  }

  function show(message) {
    status.textContent = message;
    status.hidden = !message;
  }

  function refreshButtons() {
    load.disabled = remove.disabled = !selection.value;
  }

  function render(selected, result = readBookmarks()) {
    entries = result.entries;
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = entries.length ? "选择已保存视图" : "暂无已保存视图";
    selection.replaceChildren(prompt);
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.name;
      option.textContent = entry.name;
      selection.append(option);
    }
    selection.value = entries.some((entry) => entry.name === selected) ? selected : "";
    selection.disabled = !entries.length;
    save.disabled = Boolean(result.unavailable);
    refreshButtons();
    if (result.unavailable) show("当前浏览器无法访问本地书签。");
    else if (result.damaged) show("已忽略无法读取的书签记录。");
  }

  function write(next, selected) {
    try {
      const raw = JSON.stringify({version: 1, entries: next});
      if (raw.length > maxStoredLength) throw new Error();
      localStorage.setItem(storageKey, raw);
    } catch {
      show("无法写入浏览器书签，请检查存储权限或剩余空间。");
      return false;
    }
    render(selected, {entries: next});
    return true;
  }

  function storeCurrent() {
    const name = nameInput.value.trim();
    if (!name || name.length > maxNameLength) {
      show("请输入 1 至 32 个字符的视图名称。");
      nameInput.focus();
      return;
    }
    const latest = readBookmarks();
    render(selection.value, latest);
    if (latest.unavailable) return;
    if (entries.some((entry) => entry.name === name)) {
      show("已有同名视图，请使用其他名称。");
      return;
    }
    if (entries.length >= maxBookmarks) {
      show("最多保存 12 个检查视图，请先删除一项。");
      return;
    }
    let snapshot;
    try {
      snapshot = capture();
    } catch {
      snapshot = null;
    }
    if (!isSnapshot(snapshot)) {
      show("请暂停播放并退出分解示意后保存。");
      return;
    }
    if (write([...entries, {name, snapshot}], name)) {
      nameInput.value = "";
      show("已保存检查视图。");
    }
  }

  function selectedEntry() {
    const name = selection.value;
    render(name);
    return entries.find((entry) => entry.name === name);
  }

  toggle.disabled = false;
  render("");
  listen(toggle, "click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      render(selection.value);
      nameInput.focus();
    }
  });
  listen(nameInput, "keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing || save.disabled) return;
    event.preventDefault();
    storeCurrent();
  });
  listen(selection, "change", refreshButtons);
  listen(save, "click", storeCurrent);
  listen(load, "click", () => {
    const entry = selectedEntry();
    if (!entry) return;
    let restored = false;
    try {
      restored = restore(entry.snapshot);
    } catch {
      // A bookmark can outlive the viewer configuration that created it.
    }
    show(restored ? "已恢复检查视图。" : "无法恢复此书签。请暂停播放、退出分解并确认模型模式。");
  });
  listen(remove, "click", () => {
    const entry = selectedEntry();
    if (entry && write(entries.filter((item) => item !== entry), "")) show("已删除检查视图。");
  });
  listen(window, "storage", (event) => {
    if (event.key === storageKey || event.key === null) render(selection.value);
  });

  return {dispose() { for (const removeListener of listeners) removeListener(); }};
}
