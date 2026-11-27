export function partBrowser(parts, actions) {
  const toggle = document.querySelector("#browse-parts");
  const panel = document.querySelector("#part-panel");
  const query = document.querySelector("#part-query");
  const results = document.querySelector("#part-results");
  const count = document.querySelector("#part-count");
  const show = document.querySelector("#show-part");
  const isolate = document.querySelector("#isolate-part");
  const groups = new Map();
  const subgroups = new Map();
  const rows = [];
  let lastSelected = null;

  for (const [id, {spec}] of parts) {
    if (!groups.has(spec.group)) {
      const section = document.createElement("details");
      section.open = true;
      const heading = document.createElement("summary");
      heading.className = "part-group";
      heading.textContent = spec.group;
      section.append(heading);
      results.append(section);
      groups.set(spec.group, section);
    }
    const row = document.createElement("button");
    row.type = "button";
    row.className = "part-row";
    row.dataset.part = id;
    row.title = id;
    const name = document.createElement("span");
    name.textContent = spec.label || id;
    const status = document.createElement("span");
    status.className = "part-state";
    row.append(name, status);
    let parent = groups.get(spec.group);
    const axle = /_([ab])(?:_|$)/.exec(id)?.[1];
    if (axle) {
      const key = `${spec.group}:${axle}`;
      if (!subgroups.has(key)) {
        const subsection = document.createElement("details");
        const title = document.createElement("summary");
        title.className = "part-axle";
        title.textContent = axle.toUpperCase()+" 轴位置";
        subsection.open = true;
        subsection.append(title);
        parent.append(subsection);
        subgroups.set(key, subsection);
      }
      parent = subgroups.get(key);
    }
    parent.append(row);
    rows.push({id, spec, row, status, parent, search: normalize([name.textContent, spec.group, id].join(" "))});
  }

  function normalize(text) {
    return text.toLowerCase().replace(/\s+/g, "");
  }
  function filter() {
    const term = normalize(query.value);
    let matches = 0;
    for (const {row, search} of rows) {
      row.hidden = !search.includes(term);
      if (!row.hidden) matches++;
    }
    for (const section of [...subgroups.values(), ...groups.values()]) {
      section.hidden = !section.querySelector(".part-row:not([hidden])");
      if (term && !section.hidden) section.open = true;
    }
    count.textContent = matches ? matches + " / " + rows.length + " 件" : "没有匹配的零件";
  }
  function open(value) {
    panel.hidden = !value;
    toggle.setAttribute("aria-expanded", String(value));
    (value ? query : toggle).focus();
  }
  function clickToggle() { open(panel.hidden); }
  function clickRow(event) {
    const id = event.target.closest(".part-row")?.dataset.part;
    if (id) actions.select(id);
  }
  function key(event) {
    if (event.key === "Escape") open(false);
  }
  toggle.hidden = false;
  filter();
  toggle.addEventListener("click", clickToggle);
  query.addEventListener("input", filter);
  panel.addEventListener("keydown", key);
  results.addEventListener("click", clickRow);
  show.addEventListener("click", actions.show);
  isolate.addEventListener("click", actions.isolate);
  return {
    update(selection, hidden, isolated, visible, rails) {
      if (selection !== lastSelected && parts.has(selection)) {
        groups.get(parts.get(selection).spec.group).open = true;
        rows.find(row=>row.id===selection).parent.open = true;
      }
      lastSelected = selection;
      const groupVisible = (group) => group === "轨道参照" ? rails : visible.includes(group);
      for (const {id, spec, row, status} of rows) {
        row.setAttribute("aria-pressed", String(selection === id));
        status.textContent = hidden.has(id) ? "已隐藏"
          : !groupVisible(spec.group) ? "类别关闭" : isolated && isolated !== id ? "隔离隐藏" : "";
      }
      show.disabled = !hidden.has(selection);
      isolate.disabled = !isolated && (!parts.has(selection) || hidden.has(selection) ||
        !groupVisible(parts.get(selection).spec.group));
      isolate.setAttribute("aria-pressed", String(Boolean(isolated)));
      isolate.textContent = isolated ? "退出隔离" : "仅看所选";
    },
    dispose() {
      toggle.removeEventListener("click", clickToggle);
      query.removeEventListener("input", filter);
      panel.removeEventListener("keydown", key);
      results.removeEventListener("click", clickRow);
      show.removeEventListener("click", actions.show);
      isolate.removeEventListener("click", actions.isolate);
    },
  };
}
